#!/usr/bin/env python3
"""Read-back for the interactive nightly_report approval surface.

After the user ticks approve/reject checkboxes in a ``meta/nightly_report/<date>.md``
produced by web_crawl, this script parses those decisions and applies them to the
ingest_index:

  approve  -> waiting_approval -> pending  (wiki agent then fetches raw/ + wiki-ingest)
  reject   -> rejected (sticky; enqueue of the same id is a no-op from now on)
  deferred -> appended to meta/nightly_report/backlog.md (report mode)
             OR kept in backlog.md (backlog mode)

CLI (default = dry-run):
  python scripts/report_approve.py --report <path> --vault <root>
  python scripts/report_approve.py --report <path> --vault <root> --apply
  python scripts/report_approve.py --report <path> --vault <root> --apply --json
  python scripts/report_approve.py --mode backlog --vault <root> [--apply] [--json]

Both the manual ``/wiki-approve`` command and the Phase-4 nightly auto-read reuse
``parse_report`` / ``parse_candidates`` so the same parsing logic handles interactive
and scheduled use.
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

# Matches any checkbox line (ticked or not) for the same action+id pattern.
_ANY_CHECKBOX_RE = re.compile(
    r"^\s*-\s+\[[x ]\]\s+(approve|reject)\s+[·.]\s+`([^`]+)`",
    re.IGNORECASE,
)

# Matches an unticked checkbox line (for completeness, used to distinguish ticked vs not)
_UNTICKED_RE = re.compile(r"^\s*-\s+\[ \]")

# Matches a reason line: "    - reason: <text>" (may have any leading indent)
_REASON_RE = re.compile(r"^\s*-\s+reason:\s*(.*)", re.IGNORECASE)

# Matches the url bullet: "- **url**: <url>"
_URL_BULLET_RE = re.compile(r"^\s*-\s+\*\*url\*\*:\s*(.+)", re.IGNORECASE)

# Section heading that ends the candidate blocks
_DECISION_TRACE_RE = re.compile(r"^##\s+Decision\s+trace", re.IGNORECASE)

# Candidate block heading: "### N. <title>"
_BLOCK_HEADING_RE = re.compile(r"^###\s+")

# Any ## heading (non-### section boundary)
_SECTION_HEADING_RE = re.compile(r"^##[^#]")

# Backlog frontmatter date key
_FM_DATE_RE = re.compile(r"^date:\s*(\S+)", re.IGNORECASE)

# Backlog search-date bullet already written
_SEARCH_DATE_BULLET_RE = re.compile(r"^\s*-\s+\*\*search-date\*\*:\s*(.+)")

# Id already in backlog: scan for any checkbox line with the id
_BACKLOG_ID_RE = re.compile(r"`([^`]+)`")


# ---------------------------------------------------------------------------
# parse_candidates  (NEW; returns ALL candidates including deferred)
# ---------------------------------------------------------------------------

def parse_candidates(md_text: str) -> list[dict]:
    """Parse a nightly-report markdown string and return ALL candidates.

    Returns one dict per candidate id (by its checkbox id):
        {"id": str, "action": "approve"|"reject"|"conflict"|"deferred",
         "reason": str, "block_text": str}

    Classification:
    - approve box ticked only  -> "approve"
    - reject box ticked only   -> "reject"
    - both ticked              -> "conflict"
    - neither ticked           -> "deferred"

    ``reason`` is the text after ``reason:`` in the block (for reject/deferred; "" if blank).
    ``block_text`` is the full markdown block from its ``### `` heading line through the line
    BEFORE the next ``### `` heading or the ``## Decision trace`` heading (whichever comes first).

    Parsing stops at ``## Decision trace``. Malformed lines are silently skipped.
    """
    lines = md_text.splitlines()
    n = len(lines)

    # First pass: locate block boundaries. Each block starts at a "### " line and ends
    # just before the next "### " or "## Decision trace" (or EOF).
    # We collect: block_start (inclusive), block_end (exclusive), all_lines for the block.
    # We also track which line indices belong to a ### block so bare checkbox lines
    # outside any block can be collected into a synthetic "orphan" block.
    blocks: list[tuple[int, int]] = []  # (start_line_idx, end_line_idx_exclusive)
    in_block: set[int] = set()
    i = 0
    while i < n:
        line = lines[i]
        if _DECISION_TRACE_RE.match(line):
            break
        if _BLOCK_HEADING_RE.match(line):
            start = i
            j = i + 1
            while j < n:
                if _DECISION_TRACE_RE.match(lines[j]):
                    break
                if _BLOCK_HEADING_RE.match(lines[j]):
                    break
                j += 1
            blocks.append((start, j))
            for k in range(start, j):
                in_block.add(k)
            i = j
        else:
            i += 1

    # Collect orphan lines (checkbox lines outside any ### block) as a synthetic block.
    orphan_lines: list[str] = []
    i = 0
    while i < n:
        if _DECISION_TRACE_RE.match(lines[i]):
            break
        if i not in in_block:
            # Include if it's a checkbox-related line (ticked, unticked, or reason).
            line = lines[i]
            if (_CHECKBOX_RE.match(line) or _ANY_CHECKBOX_RE.match(line)
                    or _REASON_RE.match(line)):
                orphan_lines.append(line)
            elif orphan_lines:
                # Also include trailing context lines (for reason scanning).
                orphan_lines.append(line)
        i += 1
    if orphan_lines:
        # Append a synthetic virtual block (just the orphan lines, no heading).
        # Use a special sentinel index that doesn't clash with real blocks.
        _ORPHAN_SENTINEL = -1
        blocks.append((_ORPHAN_SENTINEL, _ORPHAN_SENTINEL))
        # Store orphan lines in a side dict keyed by sentinel.
        _orphan_map = {_ORPHAN_SENTINEL: orphan_lines}
    else:
        _orphan_map: dict[int, list[str]] = {}

    # Second pass: parse each block for approve/reject/reason/block_text.
    results: list[dict] = []
    # Use dict keyed by id to handle edge case of duplicate id in one block.
    seen_ids: dict[str, dict] = {}

    for start, end in blocks:
        if start == -1:
            # Orphan block: use the collected orphan lines.
            block_lines = _orphan_map[-1]
        else:
            block_lines = lines[start:end]
        block_text = "\n".join(block_lines)

        # Collect per-id signals within this block.
        # approved_ids / rejected_ids: sets of ids seen in this block.
        approved_ids: set[str] = set()
        rejected_ids: set[str] = set()
        reasons: dict[str, str] = {}
        all_ids_in_block: list[str] = []

        li = 0
        while li < len(block_lines):
            bline = block_lines[li]

            # Ticked checkbox
            m = _CHECKBOX_RE.match(bline)
            if m:
                action_kw = m.group(1).lower()
                ident = m.group(2).strip()
                if ident not in all_ids_in_block:
                    all_ids_in_block.append(ident)
                if action_kw == "approve":
                    approved_ids.add(ident)
                elif action_kw == "reject":
                    rejected_ids.add(ident)
                    # Scan forward for the reason line.
                    reason = ""
                    j2 = li + 1
                    while j2 < len(block_lines):
                        nxt = block_lines[j2]
                        rm = _REASON_RE.match(nxt)
                        if rm:
                            reason = rm.group(1).strip()
                            break
                        if _CHECKBOX_RE.match(nxt) or _UNTICKED_RE.match(nxt):
                            break
                        if nxt.startswith("#"):
                            break
                        j2 += 1
                    reasons[ident] = reason
                li += 1
                continue

            # Unticked checkbox: extract the id so we know about deferred candidates.
            mu = _ANY_CHECKBOX_RE.match(bline)
            if mu:
                ident = mu.group(2).strip()
                if ident not in all_ids_in_block:
                    all_ids_in_block.append(ident)
            li += 1

        # Classify each id encountered in this block.
        # Also capture reason for deferred from the reason line (usually empty for deferred).
        all_ids_set = set(all_ids_in_block)
        # Include any ids only in approved/rejected sets (shouldn't happen, but safe).
        for ident in sorted(approved_ids | rejected_ids):
            if ident not in all_ids_set:
                all_ids_in_block.append(ident)

        for ident in all_ids_in_block:
            is_approved = ident in approved_ids
            is_rejected = ident in rejected_ids
            if is_approved and is_rejected:
                action = "conflict"
            elif is_approved:
                action = "approve"
            elif is_rejected:
                action = "reject"
            else:
                action = "deferred"

            entry = {
                "id": ident,
                "action": action,
                "reason": reasons.get(ident, ""),
                "block_text": block_text,
            }
            # Last-writer-wins for duplicate ids across blocks (shouldn't happen in practice).
            seen_ids[ident] = entry

    return list(seen_ids.values())


# ---------------------------------------------------------------------------
# parse_report  (kept for backward compat; filters deferred out)
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
    candidates = parse_candidates(md_text)
    # Filter: keep only ticked (approve/reject/conflict); drop deferred.
    return [
        {"id": c["id"], "action": c["action"], "reason": c["reason"]}
        for c in candidates
        if c["action"] != "deferred"
    ]


# ---------------------------------------------------------------------------
# Backlog helpers
# ---------------------------------------------------------------------------

_BACKLOG_FRONTMATTER = """\
---
type: nightly_backlog
written_by: web
updated: {date}
---

<!-- Items here are undecided (deferred) candidates from nightly reports.
     Review with: python scripts/report_approve.py --mode backlog --vault <V> [--apply]
     Tick approve or reject for each item, then re-run with --apply. -->

"""

_BACKLOG_HEADER_END_MARKER = "<!-- Items here are undecided"


def _read_backlog_ids(backlog_text: str) -> set[str]:
    """Return the set of source ids already present in backlog.md."""
    ids: set[str] = set()
    for m in _BACKLOG_ID_RE.finditer(backlog_text):
        ids.add(m.group(1))
    return ids


def _extract_date_from_report(report_path: str) -> str:
    """Return the crawl date for a report: frontmatter date: > filename stem YYYY-MM-DD > today."""
    try:
        text = Path(report_path).read_text(encoding="utf-8", errors="replace")
        # Scan only the frontmatter (between the first two --- lines).
        in_fm = False
        for line in text.splitlines():
            if line.strip() == "---":
                if not in_fm:
                    in_fm = True
                    continue
                else:
                    break
            if in_fm:
                m = _FM_DATE_RE.match(line)
                if m:
                    return m.group(1).strip()
    except OSError:
        pass
    # Fallback: filename stem if it looks like YYYY-MM-DD.
    stem = Path(report_path).stem
    if re.match(r"^\d{4}-\d{2}-\d{2}$", stem):
        return stem
    # Last resort: today.
    from datetime import date
    return date.today().isoformat()


def _ensure_backlog(backlog_path: Path, date_str: str) -> str:
    """Read backlog.md or create it with frontmatter+header. Return current text."""
    if backlog_path.exists():
        return backlog_path.read_text(encoding="utf-8", errors="replace")
    backlog_path.parent.mkdir(parents=True, exist_ok=True)
    content = _BACKLOG_FRONTMATTER.format(date=date_str)
    backlog_path.write_text(content, encoding="utf-8")
    return content


def _parse_backlog_blocks(backlog_text: str) -> list[dict]:
    """Parse backlog.md and return a list of block dicts.

    Each dict: {"ids": [str], "text": str, "search_date": str}
    where "ids" are all source ids in that block, "text" is the raw block text,
    "search_date" is the value of the - **search-date**: bullet (or "").

    Ignores YAML frontmatter and the header comment.
    """
    lines = backlog_text.splitlines()
    n = len(lines)

    # Skip past frontmatter and header comment.
    # Frontmatter: between first pair of ---; header comment: through first blank line
    # after the closing ---.
    # We just find "### " blocks anywhere after the header.
    blocks: list[dict] = []
    i = 0
    while i < n:
        line = lines[i]
        if _BLOCK_HEADING_RE.match(line):
            start = i
            j = i + 1
            while j < n:
                if _BLOCK_HEADING_RE.match(lines[j]):
                    break
                j += 1
            block_lines = lines[start:j]
            block_text = "\n".join(block_lines)

            # Extract ids from checkbox lines.
            ids: list[str] = []
            search_date = ""
            for bl in block_lines:
                mu = _ANY_CHECKBOX_RE.match(bl)
                if mu:
                    cid = mu.group(2).strip()
                    if cid not in ids:
                        ids.append(cid)
                sd_m = _SEARCH_DATE_BULLET_RE.match(bl)
                if sd_m:
                    search_date = sd_m.group(1).strip()

            if ids:
                blocks.append({"ids": ids, "text": block_text, "search_date": search_date})
            i = j
        else:
            i += 1
    return blocks


def _rewrite_backlog(backlog_path: Path, kept_blocks: list[dict], date_str: str) -> None:
    """Rewrite backlog.md with only the kept_blocks, bumping the updated date."""
    # Preserve frontmatter + header comment block.
    if backlog_path.exists():
        old_text = backlog_path.read_text(encoding="utf-8", errors="replace")
        # Replace the updated: line in frontmatter.
        new_text = re.sub(r"^updated:\s*\S+", f"updated: {date_str}", old_text,
                          count=1, flags=re.MULTILINE)
        # Find the end of the header (the end of the comment block -->).
        header_end = new_text.find("-->\n")
        if header_end != -1:
            header = new_text[:header_end + 4]
        else:
            # Fallback: keep up to the first ### block.
            first_block_pos = new_text.find("\n### ")
            if first_block_pos != -1:
                header = new_text[:first_block_pos + 1]
            else:
                header = new_text
    else:
        header = _BACKLOG_FRONTMATTER.format(date=date_str)

    body_parts = [block["text"] for block in kept_blocks]
    content = header + "\n".join(body_parts)
    if body_parts:
        content += "\n"
    backlog_path.write_text(content, encoding="utf-8")


# ---------------------------------------------------------------------------
# apply
# ---------------------------------------------------------------------------

def apply(report_path: str, vault_root: str, *, dry_run: bool = True,
          mode: str = "report") -> dict:
    """Parse the report at *report_path* and optionally apply approve/reject to the
    ingest_index for the vault at *vault_root*.

    mode="report" (default):
        Returns:
            {"approved", "rejected", "conflicts", "blocked", "deferred", "backlogged",
             "applied", "mode"}
        - approve (url-gated)  -> ingest_index.approve
        - reject               -> ingest_index.reject(reason)
        - conflict             -> skip
        - blocked              -> blocked[]
        - deferred             -> (NOT dry_run) appended to backlog.md with search-date

    mode="backlog":
        report_path must point to backlog.md.
        Returns:
            {"approved", "rejected", "conflicts", "blocked", "remaining",
             "applied", "mode"}
        - approve (url-gated)  -> ingest_index.approve; block dropped from backlog
        - reject               -> ingest_index.reject(reason); block dropped from backlog
        - deferred             -> kept in backlog
        - conflict/blocked     -> kept in backlog
        (NOT dry_run) rewrites backlog.md to only the kept blocks.

    dry_run=True (default) performs NO ingest_index mutation and NO file write.
    dry_run=False (--apply) performs mutations + file writes.
    Never raises on a malformed report file (returns empty plan on read error).
    """
    approved: list[str] = []
    rejected: list[dict] = []
    conflicts: list[str] = []
    blocked: list[dict] = []
    deferred: list[str] = []
    backlogged: list[str] = []
    remaining: list[str] = []

    try:
        md_text = Path(report_path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        base: dict = {"approved": approved, "rejected": rejected,
                      "conflicts": conflicts, "blocked": blocked, "applied": False,
                      "mode": mode}
        if mode == "report":
            base["deferred"] = deferred
            base["backlogged"] = backlogged
        else:
            base["remaining"] = remaining
        return base

    candidates = parse_candidates(md_text)

    # Resolve the vault by path rather than by registered name, so the function
    # works with both named registered vaults and ad-hoc temp vaults (tests).
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
        """Load the ingest row for *ident* with the vault env set. Returns None on miss."""
        try:
            _set_vault()
            data = ingest_index._load(None)
            sid, _, _ = ingest_index._normalize_enqueue_id(ident)
            return data["sources"].get(sid) or data["sources"].get(ident)
        except Exception:
            return None
        finally:
            _restore_vault()

    if mode == "report":
        # --- report mode ---
        search_date = _extract_date_from_report(report_path)
        backlog_path = Path(vault_root) / "meta" / "nightly_report" / "backlog.md"

        # Load existing backlog text (if any) for dedup check.
        if backlog_path.exists():
            existing_backlog_text = backlog_path.read_text(encoding="utf-8", errors="replace")
        else:
            existing_backlog_text = ""
        existing_ids = _read_backlog_ids(existing_backlog_text)

        # Blocks to append to backlog (deferred candidates not already present).
        new_backlog_blocks: list[dict] = []

        for candidate in candidates:
            ident = candidate["id"]
            action = candidate["action"]
            reason = candidate.get("reason", "")

            if action == "conflict":
                conflicts.append(ident)
                continue

            if action == "deferred":
                deferred.append(ident)
                if ident not in existing_ids:
                    new_backlog_blocks.append(candidate)
                    backlogged.append(ident)
                # deferred rows stay waiting_approval; do not touch the index.
                continue

            if action == "approve":
                row = _load_row(ident)
                if row is None or not ingest_index._valid_url(row.get("url") or ""):
                    blocked.append({"id": ident, "reason": "no working URL"})
                    print(f"[report_approve] BLOCKED {ident}: no working URL", file=sys.stderr)
                    continue

                approved.append(ident)
                if not dry_run:
                    try:
                        _set_vault()
                        ingest_index.approve(ident, name=None)
                    except Exception:
                        pass
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

        # Write deferred blocks to backlog.md (only if NOT dry_run and there are new blocks).
        if not dry_run and new_backlog_blocks:
            backlog_text = _ensure_backlog(backlog_path, search_date)
            # Re-check existing ids in case file was just created.
            existing_ids = _read_backlog_ids(backlog_text)
            append_parts: list[str] = []
            for bc in new_backlog_blocks:
                if bc["id"] in existing_ids:
                    backlogged.remove(bc["id"])
                    continue
                # Insert the - **search-date**: <D> bullet right after the **url**: line.
                block_lines = bc["block_text"].splitlines()
                new_lines: list[str] = []
                for bl in block_lines:
                    new_lines.append(bl)
                    if _URL_BULLET_RE.match(bl):
                        # Insert search-date right after the url line.
                        indent = "- "
                        new_lines.append(f"{indent}**search-date**: {search_date}")
                append_parts.append("\n".join(new_lines))

            if append_parts:
                addition = "\n" + "\n\n".join(append_parts) + "\n"
                backlog_path.write_text(backlog_text.rstrip("\n") + addition,
                                        encoding="utf-8")

        return {
            "approved": approved,
            "rejected": rejected,
            "conflicts": conflicts,
            "blocked": blocked,
            "deferred": deferred,
            "backlogged": backlogged,
            "applied": not dry_run,
            "mode": mode,
        }

    else:
        # --- backlog mode ---
        # Parse the backlog file as a set of candidate blocks and process each.
        backlog_path = Path(report_path)
        backlog_blocks = _parse_backlog_blocks(md_text)

        # Build a flat candidate list from backlog blocks. Each block has a primary id
        # (the first id found in its checkbox lines). We use parse_candidates on the
        # block text to get the action.
        # But first, let's understand: the backlog stores candidate blocks that were
        # previously deferred. They may now have been ticked by the user.

        kept_blocks: list[dict] = []  # backlog blocks to keep

        for bb in backlog_blocks:
            # Parse this block's candidates to determine actions.
            block_candidates = parse_candidates(bb["text"])
            # A backlog block typically has exactly one candidate id (the source).
            # Use the primary id (first in block's ids list).
            primary_id = bb["ids"][0] if bb["ids"] else None
            if primary_id is None:
                # Malformed block; keep it.
                kept_blocks.append(bb)
                continue

            # Find the candidate for the primary id.
            cand = next((c for c in block_candidates if c["id"] == primary_id), None)
            if cand is None:
                # No candidate found; keep block.
                kept_blocks.append(bb)
                continue

            action = cand["action"]
            reason = cand.get("reason", "")

            if action == "conflict":
                conflicts.append(primary_id)
                kept_blocks.append(bb)  # keep in backlog with note
                continue

            if action == "deferred":
                remaining.append(primary_id)
                kept_blocks.append(bb)
                continue

            if action == "approve":
                row = _load_row(primary_id)
                if row is None or not ingest_index._valid_url(row.get("url") or ""):
                    blocked.append({"id": primary_id, "reason": "no working URL"})
                    print(f"[report_approve] BLOCKED {primary_id}: no working URL",
                          file=sys.stderr)
                    kept_blocks.append(bb)  # keep in backlog
                    continue

                approved.append(primary_id)
                if not dry_run:
                    try:
                        _set_vault()
                        ingest_index.approve(primary_id, name=None)
                    except Exception:
                        pass
                    finally:
                        _restore_vault()
                # Block is dropped from backlog (not added to kept_blocks).

            elif action == "reject":
                rejected.append({"id": primary_id, "reason": reason})
                if not dry_run:
                    try:
                        _set_vault()
                        ingest_index.reject(primary_id, reason=reason, name=None)
                    except Exception:
                        pass
                    finally:
                        _restore_vault()
                # Block is dropped from backlog (not added to kept_blocks).

        # Rewrite backlog.md (only the kept blocks).
        if not dry_run:
            from datetime import date as _date
            today = _date.today().isoformat()
            _rewrite_backlog(backlog_path, kept_blocks, today)

        return {
            "approved": approved,
            "rejected": rejected,
            "conflicts": conflicts,
            "blocked": blocked,
            "remaining": remaining,
            "applied": not dry_run,
            "mode": mode,
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
    parser.add_argument("--report",
                        help="Path to the nightly report or backlog markdown file. "
                             "Defaults to <vault>/meta/nightly_report/backlog.md "
                             "when --mode backlog is given with no --report.")
    parser.add_argument("--vault", required=True,
                        help="Vault root directory (Path.name is used as the vault name).")
    parser.add_argument("--apply", action="store_true", default=False,
                        help="Apply the decisions to the ingest_index (default: dry-run).")
    parser.add_argument("--json", action="store_true", default=False,
                        help="Print the plan/result as JSON.")
    parser.add_argument("--mode", choices=["report", "backlog"], default="report",
                        help="Processing mode: 'report' (default) for a dated nightly_report "
                             "file, 'backlog' for meta/nightly_report/backlog.md.")

    args = parser.parse_args(argv)

    # Default --report for backlog mode.
    report_path = args.report
    if report_path is None:
        if args.mode == "backlog":
            report_path = str(
                Path(args.vault) / "meta" / "nightly_report" / "backlog.md"
            )
        else:
            parser.error("--report is required when --mode is 'report'")

    plan = apply(report_path, args.vault, dry_run=not args.apply, mode=args.mode)

    if args.json:
        print(json.dumps(plan, indent=2))
    else:
        mode_label = "APPLIED" if plan["applied"] else "DRY-RUN"
        print(f"[{mode_label}] mode={plan['mode']}  report: {report_path}  vault: {args.vault}")
        print(f"  approve  ({len(plan['approved'])}): {plan['approved']}")
        for r in plan["rejected"]:
            print(f"  reject   : {r['id']}  reason={r['reason']!r}")
        if plan["conflicts"]:
            print(f"  CONFLICT ({len(plan['conflicts'])}): {plan['conflicts']} "
                  f"(both boxes ticked -- skipped, surface to user)")
        if plan.get("blocked"):
            print(f"  BLOCKED  ({len(plan['blocked'])}): "
                  + ", ".join(f"{b['id']} ({b['reason']})" for b in plan["blocked"]))
        if plan.get("deferred"):
            print(f"  deferred ({len(plan['deferred'])}): {plan['deferred']}")
        if plan.get("backlogged"):
            print(f"  backlogged ({len(plan['backlogged'])}): {plan['backlogged']}")
        if plan.get("remaining"):
            print(f"  remaining in backlog ({len(plan['remaining'])}): {plan['remaining']}")
    return 0


if __name__ == "__main__":
    sys.exit(_main(sys.argv[1:]))
