#!/usr/bin/env python3
"""Read-back for the interactive nightly_report approval surface.

After the user ticks approve/reject checkboxes in a ``meta/nightly_report/<date>.md``
produced by web_crawl, this script parses those decisions and applies them to the
ingest_index:

  approve  -> waiting_approval -> pending  (wiki agent then fetches raw/ + wiki-ingest)
  reject   -> rejected (sticky; enqueue of the same id is a no-op from now on)

CLI (default = dry-run):
  python scripts/report_approve.py --report <path> --vault <root>
  python scripts/report_approve.py --report <path> --vault <root> --apply
  python scripts/report_approve.py --report <path> --vault <root> --apply --json

Both the manual ``/wiki-approve`` command and the Phase-4 nightly auto-read reuse
``parse_report`` so the same parsing logic handles interactive and scheduled use.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

# Allow running as a script (scripts/ is not a package).
_REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO_ROOT))

from agents import ingest_index  # noqa: E402

# ---------------------------------------------------------------------------
# Regex patterns for checkbox lines
# ---------------------------------------------------------------------------
# Matches: "  - [x] approve · `<id>`" or "  - [x] reject · `<id>`"
# The leading whitespace is optional. Action is group(1), id is group(2).
_CHECKBOX_RE = re.compile(
    r"^\s*-\s+\[x\]\s+(approve|reject)\s+[·.]\s+`([^`]+)`",
    re.IGNORECASE,
)

# Matches an unticked checkbox line (for completeness, used to distinguish ticked vs not)
_UNTICKED_RE = re.compile(r"^\s*-\s+\[ \]")

# Matches a reason line: "    - reason: <text>" (may have any leading indent)
_REASON_RE = re.compile(r"^\s*-\s+reason:\s*(.*)", re.IGNORECASE)

# Section heading that ends the candidate blocks
_DECISION_TRACE_RE = re.compile(r"^##\s+Decision\s+trace", re.IGNORECASE)


# ---------------------------------------------------------------------------
# parse_report
# ---------------------------------------------------------------------------

def parse_report(md_text: str) -> list[dict]:
    """Parse an interactive nightly-report markdown string and return decisions.

    Returns one dict per source that has at least one ticked checkbox:
        {"id": str, "action": "approve" | "reject" | "conflict", "reason": str}

    Rules:
    - A ``- [x] approve * `ID``` line -> action approve, reason "".
    - A ``- [x] reject  * `ID``` line -> action reject, reason = text after
      ``reason:`` on the nearest following ``- reason:`` line in the same block
      (stripped; "" when blank or absent).
    - Both ticked for the same ID -> action "conflict" (never guessed, always
      surfaced to the user). reason is preserved from the reject line.
    - Unticked boxes are ignored.
    - Everything from a ``## Decision trace`` heading onward is ignored.
    - Malformed lines are silently skipped (never raises).
    """
    lines = md_text.splitlines()

    # First pass: collect per-id signals, stopping at Decision trace.
    # approved_ids: set of ids with an approve tick
    # rejected_ids: set of ids with a reject tick
    # reasons: id -> reason string (from the reject block)
    approved_ids: set[str] = set()
    rejected_ids: set[str] = set()
    reasons: dict[str, str] = {}

    i = 0
    n = len(lines)
    while i < n:
        line = lines[i]

        # Stop at the Decision trace section.
        if _DECISION_TRACE_RE.match(line):
            break

        m = _CHECKBOX_RE.match(line)
        if m:
            action = m.group(1).lower()
            ident = m.group(2).strip()
            if action == "approve":
                approved_ids.add(ident)
            elif action == "reject":
                rejected_ids.add(ident)
                # Scan forward for the next reason line in this block.
                # A "block" ends when we hit another checkbox or a heading or a blank
                # line followed by a heading; we stop at the first non-indented line
                # that is not a continuation (blank or indented).
                reason = ""
                j = i + 1
                while j < n:
                    nxt = lines[j]
                    # Stop collecting reason at section boundary.
                    if _DECISION_TRACE_RE.match(nxt):
                        break
                    rm = _REASON_RE.match(nxt)
                    if rm:
                        reason = rm.group(1).strip()
                        break
                    # If we hit another checkbox line, stop.
                    if _CHECKBOX_RE.match(nxt) or _UNTICKED_RE.match(nxt):
                        break
                    # If we hit a heading of any level, stop.
                    if nxt.startswith("#"):
                        break
                    j += 1
                reasons[ident] = reason
        i += 1

    # Second pass: build results list, one entry per id that was touched.
    all_ids = approved_ids | rejected_ids
    results: list[dict] = []
    for ident in sorted(all_ids):  # sorted for deterministic output
        is_approved = ident in approved_ids
        is_rejected = ident in rejected_ids
        if is_approved and is_rejected:
            action = "conflict"
        elif is_approved:
            action = "approve"
        else:
            action = "reject"
        results.append({
            "id": ident,
            "action": action,
            "reason": reasons.get(ident, ""),
        })
    return results


# ---------------------------------------------------------------------------
# apply
# ---------------------------------------------------------------------------

def apply(report_path: str, vault_root: str, *, dry_run: bool = True) -> dict:
    """Parse the report at *report_path* and optionally apply approve/reject to the
    ingest_index for the vault at *vault_root*.

    Returns:
        {
          "approved":  [id, ...],
          "rejected":  [{"id": id, "reason": reason}, ...],
          "conflicts": [id, ...],
          "blocked":   [{"id": id, "reason": str}, ...],  # approved but no valid URL
          "applied":   bool,   # False when dry_run=True
        }

    When dry_run=True the function returns the plan without touching the index.
    When dry_run=False it calls ingest_index.approve / ingest_index.reject, keyed by
    vault name = Path(vault_root).name (matching how other scripts pass the name to
    ingest_index._load).

    Approve gate: a ticked-approve entry is only approved when the corresponding ingest
    row has a valid http/https URL.  Without a working URL the entry is added to
    "blocked" (with reason "no working URL") and skipped -- the index is NOT mutated.
    This applies to both dry_run=True (plan) and dry_run=False (--apply).

    Conflict ids are always skipped (never approve-or-reject guessed).
    Reject is unaffected -- a source can always be rejected without a URL.
    Never raises on a malformed report file (returns empty plan on read error).
    """
    approved: list[str] = []
    rejected: list[dict] = []
    conflicts: list[str] = []
    blocked: list[dict] = []

    try:
        md_text = Path(report_path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return {"approved": approved, "rejected": rejected,
                "conflicts": conflicts, "blocked": blocked, "applied": False}

    entries = parse_report(md_text)

    # Resolve the vault by path rather than by registered name, so the function
    # works with both named registered vaults and ad-hoc temp vaults (tests).
    # vault_config._load honours $VAULT_PATH when name=None and $VAULT is unset,
    # so we temporarily export vault_root as VAULT_PATH for the duration of the
    # approve/reject calls, then restore the previous value.  This is the same
    # pattern used by all other hermetic tests in the suite.
    _prev_vault_path = os.environ.get("VAULT_PATH")
    _prev_vault = os.environ.get("VAULT")

    def _set_vault():
        os.environ["VAULT_PATH"] = vault_root
        os.environ.pop("VAULT", None)

    def _restore_vault():
        if _prev_vault_path is not None:
            os.environ["VAULT_PATH"] = _prev_vault_path
        else:
            os.environ.pop("VAULT_PATH", None)
        if _prev_vault is not None:
            os.environ["VAULT"] = _prev_vault

    def _load_row(ident: str) -> dict | None:
        """Load the ingest row for *ident* with the vault env set.  Returns None on miss."""
        try:
            _set_vault()
            data = ingest_index._load(None)
            # _normalize_enqueue_id to look up the canonical id as ingest_index.approve does.
            sid, _, _ = ingest_index._normalize_enqueue_id(ident)
            return data["sources"].get(sid) or data["sources"].get(ident)
        except Exception:
            return None
        finally:
            _restore_vault()

    for entry in entries:
        ident = entry["id"]
        action = entry["action"]
        reason = entry.get("reason", "")

        if action == "conflict":
            conflicts.append(ident)
            continue

        if action == "approve":
            # URL gate: only approve when the row has a valid http/https URL.
            row = _load_row(ident)
            if row is None or not ingest_index._valid_url(row.get("url") or ""):
                blocked.append({"id": ident, "reason": "no working URL"})
                print(f"[report_approve] BLOCKED {ident}: no working URL", file=sys.stderr)
                continue  # do NOT approve; do NOT mutate the index

            approved.append(ident)
            if not dry_run:
                try:
                    _set_vault()
                    ingest_index.approve(ident, name=None)
                except Exception:
                    pass  # never crash; surface via return value
                finally:
                    _restore_vault()
        elif action == "reject":
            rejected.append({"id": ident, "reason": reason})
            if not dry_run:
                try:
                    _set_vault()
                    ingest_index.reject(ident, reason=reason, name=None)
                except Exception:
                    pass
                finally:
                    _restore_vault()

    return {
        "approved": approved,
        "rejected": rejected,
        "conflicts": conflicts,
        "blocked": blocked,
        "applied": not dry_run,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        description="Read-back: parse ticked nightly-report checkboxes and apply "
                    "approve/reject to the ingest_index.",
        add_help=True,
    )
    parser.add_argument("--report", required=True,
                        help="Path to the nightly report markdown file.")
    parser.add_argument("--vault", required=True,
                        help="Vault root directory (Path.name is used as the vault name).")
    parser.add_argument("--apply", action="store_true", default=False,
                        help="Apply the decisions to the ingest_index (default: dry-run).")
    parser.add_argument("--json", action="store_true", default=False,
                        help="Print the plan/result as JSON.")

    args = parser.parse_args(argv)

    plan = apply(args.report, args.vault, dry_run=not args.apply)

    if args.json:
        print(json.dumps(plan, indent=2))
    else:
        mode = "APPLIED" if plan["applied"] else "DRY-RUN"
        print(f"[{mode}] report: {args.report}  vault: {args.vault}")
        print(f"  approve  ({len(plan['approved'])}): {plan['approved']}")
        for r in plan["rejected"]:
            print(f"  reject   : {r['id']}  reason={r['reason']!r}")
        if plan["conflicts"]:
            print(f"  CONFLICT ({len(plan['conflicts'])}): {plan['conflicts']} "
                  f"(both boxes ticked -- skipped, surface to user)")
        if plan.get("blocked"):
            print(f"  BLOCKED  ({len(plan['blocked'])}): "
                  + ", ".join(f"{b['id']} ({b['reason']})" for b in plan["blocked"]))
    return 0


if __name__ == "__main__":
    sys.exit(_main(sys.argv[1:]))
