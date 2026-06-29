#!/usr/bin/env python3
"""written_by_backfill.py -- backfill written_by frontmatter on existing wiki/ and research/ pages.

For every .md file under wiki/ and research/ (with specific skips):
  - Ensures a written_by frontmatter key is present with the correct value per area.
  - Removes any residual generated_by key.
  - Idempotent: only rewrites files that change.

Provenance value map:
  wiki/      ->  written_by: wiki
  research/  (deep/, query/, top-level reports)  ->  written_by: research
  research/notes/  ->  written_by: USER

Skipped files:
  - _template.md  (any path component is _template.md)
  - wiki/index.md, wiki/hot.md, wiki/log.md, wiki/gaps.md  (index/log/hot/gaps)
  - research/index.md, research/hot.md, research/log.md
  - .gitkeep

CLI:
  python scripts/written_by_backfill.py [--vault PATH] [--path PATH] [--dry-run] [--apply]

Options:
  --vault PATH   Override vault root (default: resolved via agents.vault_config)
  --path PATH    Override path to a specific subtree (must be under vault root)
  --dry-run      Default mode: print per-file summary, no writes
  --apply        Apply changes to disk
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

# ---------------------------------------------------------------------------
# Frontmatter regex
# ---------------------------------------------------------------------------

_FM_RE = re.compile(r"^---\r?\n(.*?)\r?\n---(?:\r?\n|$)", re.DOTALL)


# ---------------------------------------------------------------------------
# Provenance map
# ---------------------------------------------------------------------------

def _written_by_value(path: Path, vault_root: Path) -> str:
    """Return the correct written_by value for a file given its location.

    Relative path under vault_root determines the area:
      research/notes/  ->  USER
      research/*       ->  research
      wiki/*           ->  wiki
    """
    try:
        rel = path.relative_to(vault_root)
    except ValueError:
        # fallback if path is not under vault_root (should not happen in normal use)
        return "wiki"

    parts = rel.parts
    if not parts:
        return "wiki"

    area = parts[0]
    if area == "research":
        # research/notes/ -> USER; everything else -> research
        if len(parts) >= 2 and parts[1] == "notes":
            return "USER"
        return "research"
    if area == "wiki":
        return "wiki"
    # Unexpected area: return wiki as safe default
    return "wiki"


# ---------------------------------------------------------------------------
# Skip logic
# ---------------------------------------------------------------------------

_SKIP_NAMES: frozenset[str] = frozenset({
    "_template.md",
    ".gitkeep",
    "index.md",
    "hot.md",
    "log.md",
})


def _should_skip(path: Path) -> bool:
    """Return True if this file should be skipped by the backfill."""
    name = path.name

    # Skip .gitkeep (no extension variant)
    if name == ".gitkeep":
        return True

    # Skip any _template.md anywhere in the tree
    if name == "_template.md":
        return True

    # Skip index/log/hot at any depth (these are structural files, not content)
    if name in _SKIP_NAMES:
        return True

    return False


# ---------------------------------------------------------------------------
# Frontmatter rewrite
# ---------------------------------------------------------------------------

def _rewrite_frontmatter(text: str, written_by_value: str) -> tuple[str, bool, str]:
    """Ensure written_by is set and generated_by is absent.

    Returns (new_text, changed: bool, reason: str).
    Preserves all other frontmatter fields and body verbatim.
    Preserves frontmatter ordering as much as possible:
      - If written_by is already present with the correct value AND generated_by is absent
        -> no change.
      - If written_by is absent -> insert after the last existing FM line (before closing ---).
      - If written_by has wrong value -> correct in place.
      - If generated_by is present -> remove it.
    """
    m = _FM_RE.match(text)
    if not m:
        # No frontmatter block: prepend a minimal one
        fm_block = f"written_by: {written_by_value}\n"
        new_text = f"---\n{fm_block}---\n{text}"
        return new_text, True, "no frontmatter; minimal block prepended"

    fm_inner = m.group(1)
    after_fm = text[m.end():]

    lines = fm_inner.splitlines()
    new_lines: list[str] = []
    has_written_by = False
    written_by_correct = False
    had_generated_by = False
    written_by_was_wrong = False

    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            new_lines.append(line)
            continue

        if ":" not in line:
            new_lines.append(line)
            continue

        key = line.partition(":")[0].strip().lower()

        if key == "written_by":
            has_written_by = True
            current_val = line.partition(":")[2].strip()
            if current_val == written_by_value:
                written_by_correct = True
                new_lines.append(line)
            else:
                # Correct the value in place
                new_lines.append(f"written_by: {written_by_value}")
                written_by_was_wrong = True
            continue

        if key == "generated_by":
            had_generated_by = True
            # Drop this line (do not append)
            continue

        new_lines.append(line)

    # If written_by was absent, append it at the end of the frontmatter
    if not has_written_by:
        new_lines.append(f"written_by: {written_by_value}")

    changed = had_generated_by or not has_written_by or written_by_was_wrong

    if not changed:
        return text, False, "already correct"

    reasons: list[str] = []
    if not has_written_by:
        reasons.append(f"added written_by: {written_by_value}")
    if written_by_was_wrong:
        reasons.append(f"corrected written_by -> {written_by_value}")
    if had_generated_by:
        reasons.append("removed generated_by")

    new_fm_inner = "\n".join(new_lines)
    new_text = f"---\n{new_fm_inner}\n---" + ("\n" if not after_fm.startswith("\n") else "") + after_fm
    return new_text, True, "; ".join(reasons)


# ---------------------------------------------------------------------------
# Vault root resolution
# ---------------------------------------------------------------------------

def _resolve_vault_root(override: str | None = None) -> Path:
    if override:
        return Path(override).resolve()
    for ev in ("VAULT_ROOT", "VAULT_PATH"):
        val = os.environ.get(ev)
        if val:
            return Path(val)
    try:
        from agents import vault_config  # noqa: PLC0415
        return Path(vault_config.vault_path())
    except Exception as exc:
        sys.exit(f"written_by_backfill: cannot resolve vault root: {exc}")


# ---------------------------------------------------------------------------
# Main backfill logic
# ---------------------------------------------------------------------------

def run_backfill(
    vault_root: Path,
    scan_root: Path | None = None,
    dry_run: bool = True,
) -> dict:
    """Run the backfill over wiki/ and research/ under vault_root.

    scan_root: if provided, only scan this subtree (must be under vault_root).
    Returns a summary dict with keys: examined, changed, skipped, errors.
    """
    if scan_root is None:
        areas = [vault_root / "wiki", vault_root / "research"]
    else:
        areas = [scan_root]

    summary = {"examined": 0, "changed": 0, "skipped": 0, "errors": 0}

    for area_root in areas:
        if not area_root.is_dir():
            continue
        for path in sorted(area_root.rglob("*.md")):
            if not path.is_file():
                continue

            summary["examined"] += 1

            if _should_skip(path):
                summary["skipped"] += 1
                if dry_run:
                    rel = path.relative_to(vault_root)
                    print(f"  SKIP  {rel}")
                continue

            try:
                original = path.read_text(encoding="utf-8")
            except OSError as exc:
                summary["errors"] += 1
                print(f"  ERROR reading {path}: {exc}", file=sys.stderr)
                continue

            wanted = _written_by_value(path, vault_root)

            try:
                new_text, changed, reason = _rewrite_frontmatter(original, wanted)
            except Exception as exc:
                summary["errors"] += 1
                print(f"  ERROR processing {path}: {exc}", file=sys.stderr)
                continue

            rel = path.relative_to(vault_root)

            if changed:
                summary["changed"] += 1
                if dry_run:
                    print(f"  CHANGE {rel}  [{reason}]")
                else:
                    try:
                        path.write_text(new_text, encoding="utf-8")
                        print(f"  WROTE  {rel}  [{reason}]")
                    except OSError as exc:
                        summary["errors"] += 1
                        print(f"  ERROR writing {path}: {exc}", file=sys.stderr)
            else:
                if dry_run:
                    print(f"  OK     {rel}")

    return summary


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=(
            "Backfill written_by frontmatter on existing wiki/ and research/ pages. "
            "Removes residual generated_by. Default is --dry-run."
        )
    )
    ap.add_argument(
        "--vault",
        default=None,
        metavar="PATH",
        help="Override vault root path (default: resolved via agents.vault_config)",
    )
    ap.add_argument(
        "--path",
        default=None,
        metavar="PATH",
        help="Restrict scan to a specific subtree under the vault root",
    )
    ap.add_argument(
        "--dry-run",
        dest="dry_run",
        action="store_true",
        default=True,
        help="Print per-file summary without writing (default)",
    )
    ap.add_argument(
        "--apply",
        dest="dry_run",
        action="store_false",
        help="Apply changes to disk",
    )

    args = ap.parse_args(argv)

    vault_root = _resolve_vault_root(args.vault)
    scan_root = Path(args.path).resolve() if args.path else None

    mode = "dry-run" if args.dry_run else "apply"
    print(f"written_by_backfill [{mode}]  vault={vault_root}")
    if scan_root:
        print(f"  restricting scan to: {scan_root}")
    print()

    summary = run_backfill(vault_root, scan_root=scan_root, dry_run=args.dry_run)

    print()
    print(
        f"Summary: examined={summary['examined']} changed={summary['changed']} "
        f"skipped={summary['skipped']} errors={summary['errors']}"
    )
    if args.dry_run and summary["changed"] > 0:
        print("(dry-run: no files written; pass --apply to apply changes)")

    return 0 if summary["errors"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
