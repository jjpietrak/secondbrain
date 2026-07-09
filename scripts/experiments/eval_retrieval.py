#!/usr/bin/env python3
"""Phase-4 in-loop tuning eval harness for the web-scrape crawl.

Runs ``web_crawl.crawl(dry_run=True)`` against the seeded throwaway experiment vault
``Disagg-Exp`` and measures whether the (tunable) crawl retrieves a known ground-truth
target set.  It is the measurement machinery for the human-in-loop tuning loop: each
iteration the orchestrator changes ONE knob in the experiment config, re-runs this
harness, and reads the recall + per-target ranks.

Isolation guarantees (so tuning never mutates shared production state):
  * The crawl reads an EXPERIMENT config copy (``scripts/experiments/exp-config.json``),
    NOT the shared ``.claude/web/web-config.json``.  This is enforced by monkeypatching
    ``web_crawl._load_config`` to return the experiment config dict.
  * ``dry_run=True`` -- nothing is written to the ingest index, nightly report, or the
    seen cache.
  * The chiplog reachability target is injected via ``--agent-candidates`` (a fixture),
    because ``scripts/*.py`` cannot call the real Claude WebSearch tool.  The injected
    candidate flows through the identical dedup -> rank -> lane-quota machinery.

Metrics (over the manifest ``scripts/experiments/targets.json``):
  * per-target: found? (present in the ranked pool), rank (1-indexed by score desc),
    selected? (present in the <=5 selected set)
  * recall@5 = fraction of targets in the selected set

$0: free arXiv/S2 APIs for harvest + local/offline rank.  Bounded via ``--limit``
(default 3 candidates per source/query).

CLI:
  python scripts/experiments/eval_retrieval.py \
      [--config scripts/experiments/exp-config.json] \
      [--targets scripts/experiments/targets.json] \
      [--agent-candidates scripts/experiments/chiplog-candidate.json] \
      [--vault <path-or-name>] [--limit N] [--json]
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

_THIS = Path(__file__).resolve()
_EXP_DIR = _THIS.parent                # scripts/experiments/
_SCRIPTS_DIR = _EXP_DIR.parent         # scripts/
_REPO_ROOT = _SCRIPTS_DIR.parent       # repo root (worktree)

# Make the crawl's sibling modules importable (web_crawl, web_harvest, ...).
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

# Default artefact locations (relative to this file, so the harness is CWD-independent).
DEFAULT_CONFIG = _EXP_DIR / "exp-config.json"
DEFAULT_TARGETS = _EXP_DIR / "targets.json"
DEFAULT_AGENT_CANDIDATES = _EXP_DIR / "chiplog-candidate.json"
DEFAULT_VAULT_NAME = "Disagg-Exp"


# ---------------------------------------------------------------------------
# Vault + config resolution
# ---------------------------------------------------------------------------


def _resolve_vault_root(vault_arg: str | None) -> str:
    """Resolve the Disagg-Exp vault root.

    Precedence: explicit --vault (path or registered name) -> vault_config for
    DEFAULT_VAULT_NAME (CODE_PATH pinned to the repo root so the worktree's
    config/vaults/ is used) -> direct read of config/vaults/<name>/vault.yaml.
    """
    if vault_arg:
        p = Path(vault_arg)
        if p.is_dir():
            return str(p)
    # Pin CODE_PATH to this repo so vault_config reads THIS checkout's config/vaults/.
    os.environ.setdefault("CODE_PATH", str(_REPO_ROOT))
    name = vault_arg or DEFAULT_VAULT_NAME
    try:
        from agents import vault_config as vc  # type: ignore[import]

        return str(vc.vault_path(name))
    except Exception:
        pass
    # Fallback: parse config/vaults/<name>/vault.yaml directly.
    vy = _REPO_ROOT / "config" / "vaults" / name / "vault.yaml"
    if vy.exists():
        try:
            import yaml  # type: ignore[import]

            data = yaml.safe_load(vy.read_text(encoding="utf-8")) or {}
            vp = data.get("vault_path")
            if vp:
                return str(vp)
        except Exception:
            pass
    raise SystemExit(f"Cannot resolve vault root for {name!r} (pass --vault <path>)")


def _load_json(path: Path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# Target <-> candidate matching
# ---------------------------------------------------------------------------


def _arxiv_core(ident_or_id: str) -> str:
    """Return the bare arXiv number (no 'arxiv:' prefix, no version suffix)."""
    s = ident_or_id.strip().lower()
    if s.startswith("arxiv:"):
        s = s[len("arxiv:"):]
    # strip a trailing version like v1 / v12
    s = re.sub(r"v\d+$", "", s)
    return s


def _norm_url(u: str) -> str:
    return u.strip().rstrip("/").lower()


def _match_ident(ident: str, target: dict) -> bool:
    """True if a candidate ident corresponds to this target.

    - present_in_vault targets carry an 'id' like 'arxiv:2504.02263': match if the
      bare arXiv number appears in the ident (ident may carry a version suffix).
    - reachability targets carry a 'url': match on normalised-URL equality/substring.
    """
    ident = (ident or "").strip()
    if not ident:
        return False
    tid = target.get("id", "")
    if tid:
        core = _arxiv_core(tid)
        return bool(core) and core in ident.lower()
    turl = target.get("url", "")
    if turl:
        ni, nt = _norm_url(ident), _norm_url(turl)
        return ni == nt or nt in ni or ni in nt
    return False


def _target_label(target: dict) -> str:
    return target.get("id") or target.get("url") or "(unknown)"


# ---------------------------------------------------------------------------
# Core evaluation
# ---------------------------------------------------------------------------


def evaluate(result: dict, targets: dict, backfill_ids: set[str] | None = None) -> dict:
    """Compute per-target found/rank/selected/backfill + recall@5 from a crawl result.

    ``result`` is the dry-run dict from web_crawl.crawl: {plan, selected, trace, ...}.
    The ranked pool is read from the trace's ('rank','scores') record; ranks are
    assigned by descending score (1 = best).  ``selected`` idents come from the
    <=5 selected candidate dicts via web_harvest.candidate_ident.

    ``backfill_ids`` (optional) is the deterministic SET of ids that
    ``web_backfill.build_backfill`` would surface (membership = backfill would stage it,
    independent of any per-run --limit batching).  A ``present_in_vault`` target counts as
    RETRIEVED if it is in the crawl-selected set OR in the backfill set.  Reachability
    targets (e.g. the chiplog URL) are measured via the crawl path only; backfill (arXiv/
    DOI ids) does not apply to them.  recall@5 is over the RETRIEVED count.
    """
    backfill_ids = backfill_ids or set()
    # 1. Build the ranked pool: [(ident, score)] sorted by score desc (stable).
    pool: list[dict] = []
    trace = result.get("trace")
    if trace is not None:
        recs = trace.find("rank", "scores")
        if recs:
            pool = list(recs[-1]["data"].get("candidates", []))
    ranked = sorted(pool, key=lambda c: float(c.get("score", 0.0)), reverse=True)

    # 2. Selected idents (the <=5 staged set).
    try:
        from web_harvest import candidate_ident as _cand_ident
    except Exception:  # pragma: no cover - defensive
        def _cand_ident(c: dict) -> str:  # type: ignore[misc]
            sid = c.get("source_id") or ""
            if sid.startswith(("arxiv:", "doi:")):
                return sid
            return (c.get("url") or "").rstrip("/") or sid

    selected = result.get("selected", []) or []
    selected_idents = [_cand_ident(c) for c in selected]

    # 3. Evaluate every target.
    all_targets: list[dict] = []
    for t in targets.get("present_in_vault", []):
        all_targets.append({**t, "kind": "present_in_vault"})
    for t in targets.get("reachability", []):
        all_targets.append({**t, "kind": "reachability"})

    rows: list[dict] = []
    n_selected = 0
    n_retrieved = 0
    for t in all_targets:
        rank = None
        found = False
        matched_ident = ""
        for i, c in enumerate(ranked, start=1):
            if _match_ident(c.get("ident", ""), t):
                found = True
                rank = i
                matched_ident = c.get("ident", "")
                break
        is_selected = any(_match_ident(sid, t) for sid in selected_idents)
        # Backfill only surfaces arXiv/DOI ids -> only meaningful for present_in_vault.
        in_backfill = t["kind"] == "present_in_vault" and any(
            _match_ident(bid, t) for bid in backfill_ids
        )
        is_retrieved = is_selected or in_backfill
        if is_selected:
            n_selected += 1
        if is_retrieved:
            n_retrieved += 1
        rows.append(
            {
                "name": t.get("name", ""),
                "key": _target_label(t),
                "kind": t["kind"],
                "found": found,
                "rank": rank,
                "selected": is_selected,
                "backfill": in_backfill,
                "retrieved": is_retrieved,
                "matched_ident": matched_ident,
            }
        )

    total = len(all_targets) or 1
    recall_at_5 = n_retrieved / total

    return {
        "recall_at_5": recall_at_5,
        "n_retrieved_targets": n_retrieved,
        "n_selected_targets": n_selected,
        "n_targets": len(all_targets),
        "pool_size": len(ranked),
        "n_selected_total": len(selected),
        "with_backfill": bool(backfill_ids),
        "rows": rows,
    }


def _compute_backfill_ids(vault_root: str) -> set[str]:
    """Return the SET of ids web_backfill would surface for this vault.

    Offline: ``build_backfill`` only scans the wiki + ingest index (no network; only
    ``fetch_arxiv_by_id`` hits the network, and we never call it).  Membership in this
    set means backfill would stage the id; the per-run ``--limit`` is just batching, so
    we use SET membership rather than the capped "would stage" list.
    """
    try:
        import web_backfill  # type: ignore[import]

        res = web_backfill.build_backfill(vault_root)
        return {b["id"] for b in res.get("backfill", [])}
    except Exception as exc:  # pragma: no cover - defensive
        print(f"[eval_retrieval] backfill set unavailable: {exc}", file=sys.stderr)
        return set()


def run_crawl(vault_root: str, config: dict, agent_candidates: str | None, limit: int) -> dict:
    """Run the dry-run crawl with the experiment config monkeypatched in.

    Never touches the shared .claude/web/web-config.json: web_crawl._load_config is
    replaced with a function returning ``config``.
    """
    import web_crawl  # type: ignore[import]

    orig_load_config = web_crawl._load_config
    web_crawl._load_config = lambda: config  # type: ignore[assignment]
    try:
        result = web_crawl.crawl(
            vault_root=vault_root,
            dry_run=True,
            per_source_limit=limit,
            agent_candidates_path=agent_candidates,
        )
    finally:
        web_crawl._load_config = orig_load_config  # type: ignore[assignment]
    return result


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


def render_scorecard(report: dict, *, vault_root: str, config_path: str) -> str:
    lines: list[str] = []
    lines.append("=" * 66)
    lines.append("web-scrape retrieval eval -- Disagg-Exp")
    lines.append("=" * 66)
    lines.append(f"vault      : {vault_root}")
    lines.append(f"config     : {config_path}")
    lines.append(f"pool size  : {report['pool_size']} ranked candidates")
    lines.append(f"selected   : {report['n_selected_total']} (crawl top-N)")
    lines.append(f"backfill   : {'ON' if report.get('with_backfill') else 'off'}")
    lines.append("")
    lines.append(
        f"RECALL@5   : {report['recall_at_5']:.2f}  "
        f"({report['n_retrieved_targets']}/{report['n_targets']} targets retrieved"
        f"; {report['n_selected_targets']} via crawl-select)"
    )
    lines.append("")
    # per-target table
    hdr = f"{'target':<26} {'kind':<17} {'found':<6} {'rank':<5} {'selected':<9} {'backfill'}"
    lines.append(hdr)
    lines.append("-" * len(hdr))
    for r in report["rows"]:
        rank = str(r["rank"]) if r["rank"] is not None else "-"
        bf = "yes" if r.get("backfill") else "no"
        if r["kind"] != "present_in_vault":
            bf = "n/a"
        lines.append(
            f"{(r['name'] or r['key'])[:25]:<26} "
            f"{r['kind']:<17} "
            f"{('yes' if r['found'] else 'no'):<6} "
            f"{rank:<5} "
            f"{('YES' if r['selected'] else 'no'):<9} "
            f"{bf}"
        )
    lines.append("")
    lines.append("keys:")
    for r in report["rows"]:
        lines.append(f"  {r['name']}: {r['key']}")
    lines.append("=" * 66)
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    if argv is None:
        argv = sys.argv[1:]
    ap = argparse.ArgumentParser(description="Phase-4 web-scrape retrieval eval harness.")
    ap.add_argument("--config", default=str(DEFAULT_CONFIG), help="Experiment config JSON.")
    ap.add_argument("--targets", default=str(DEFAULT_TARGETS), help="Target manifest JSON.")
    ap.add_argument(
        "--agent-candidates",
        default=str(DEFAULT_AGENT_CANDIDATES),
        dest="agent_candidates",
        help="Injected WebSearch candidates fixture (chiplog).",
    )
    ap.add_argument("--vault", default=None, help="Vault path or registered name (default: Disagg-Exp).")
    ap.add_argument("--limit", type=int, default=3, help="Max candidates per source/query (default: 3).")
    ap.add_argument(
        "--with-backfill",
        action="store_true",
        dest="with_backfill",
        help=(
            "Also count a present_in_vault target as retrieved if it is in the "
            "web_backfill.build_backfill SET (offline; no network -- membership only)."
        ),
    )
    ap.add_argument("--json", action="store_true", dest="as_json", help="Emit the report as JSON.")
    args = ap.parse_args(argv)

    vault_root = _resolve_vault_root(args.vault)
    config = _load_json(Path(args.config))
    targets = _load_json(Path(args.targets))
    agent_candidates = args.agent_candidates if args.agent_candidates and Path(args.agent_candidates).exists() else None

    backfill_ids: set[str] = set()
    if args.with_backfill:
        backfill_ids = _compute_backfill_ids(vault_root)

    result = run_crawl(vault_root, config, agent_candidates, args.limit)
    report = evaluate(result, targets, backfill_ids)

    if args.as_json:
        print(json.dumps({
            "vault_root": vault_root,
            "config": str(args.config),
            **report,
        }, indent=2, ensure_ascii=False))
    else:
        print(render_scorecard(report, vault_root=vault_root, config_path=str(args.config)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
