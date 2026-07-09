#!/usr/bin/env python3
"""web_backfill.py -- on-demand KNOWN-GAP backfill for Second Brain v0.2.

Fills KNOWN gaps first: papers already CITED in the wiki (arXiv / DOI links) but not
yet truly ingested. This is a SEPARATE, on-demand command -- it is NOT folded into the
nightly `web-scrape` crawl. Run it before exploratory crawling to close the papers the
vault already knows it wants.

Logic
-----
1. HARVEST cited ids. Scan ``wiki/**/*.md`` for ``arxiv.org/(abs|pdf)/<id>`` links
   (version suffix stripped, consistent with web_harvest.candidate_ident) and DOIs
   (``doi.org/<doi>`` or a bare ``10.NNNN/...``). Each open gap's ``## Shows up in``
   body section names wiki pages; ids cited on those gap-referenced pages are the
   HIGHEST signal. Record which wiki page(s) cite each id.
2. TRUTH-CHECK what is ACTUALLY ingested. An id counts as ingested ONLY if the
   ingest_index has it with status ``ingested`` AND its ``filename`` exists on disk
   (status alone can lie -- the Splitwise case). ``pending``/``waiting_approval`` are
   already queued (skip). ``rejected``/``deleted`` are decided (skip). Everything
   cited-but-not-truly-ingested-and-not-already-decided = the BACKFILL SET.
3. FETCH + STAGE. For each backfill id fetch metadata by id (arXiv ``?id_list=<id>``),
   tag lane ``backfill``, BYPASS the seen.json cache, and stage as ``waiting_approval``
   via ingest_index.enqueue with a rationale naming the citing page(s). Highest-signal
   first (gap ``## Shows up in`` cites > plain wiki cites). Respect ``--limit`` (10).
4. REPORT. Write ``meta/nightly_report/backfill-<date>.md`` in the SAME interactive
   approve/reject checkbox block format web_crawl uses (so report_approve.py parses it).
   ``--dry-run`` writes nothing and prints what WOULD stage. ``--explain`` prints a trace.

Free APIs only; $0 marginal.

CLI:
  python scripts/web_backfill.py --vault <root> [--dry-run] [--json] [--limit N] [--explain]

Exit codes:
  0  -- success
  2  -- usage error
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import date
from pathlib import Path

# ---------------------------------------------------------------------------
# sys.path shim: ensure scripts/ and repo root are importable (mirror web_crawl)
# ---------------------------------------------------------------------------
_SCRIPTS_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _SCRIPTS_DIR.parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


# ---------------------------------------------------------------------------
# Citation extraction
# ---------------------------------------------------------------------------

# arXiv links: arxiv.org/abs/<id> or arxiv.org/pdf/<id>, version suffix stripped
# (capture group is the bare id 2401.09670 -- consistent with candidate_ident).
_ARXIV_LINK_RE = re.compile(
    r"arxiv\.org/(?:abs|pdf)/(\d{4}\.\d{4,5})(?:v\d+)?", re.IGNORECASE
)

# arXiv TEXTUAL citation: "arXiv 2504.02263", "arXiv: 2311.18677v4", "**arXiv**: <id>",
# or an `arxiv:` frontmatter field. Requires the "arxiv" token within a few non-word
# chars of an arxiv-shaped id (\d{4}\.\d{4,5}) so it is high-precision (no bare-number
# false positives). Most wiki pages cite papers this way, NOT as arxiv.org/abs URLs
# (which typically only exist on an already-ingested paper's sources page).
_ARXIV_TEXT_RE = re.compile(
    r"arxiv\W{0,4}(\d{4}\.\d{4,5})(?:v\d+)?", re.IGNORECASE
)

# DOI links: doi.org/<doi>, doi: <doi>, or a bare 10.NNNN/... token.
_DOI_LINK_RE = re.compile(
    r"(?:doi\.org/|\bdoi:\s*)?(10\.\d{4,9}/[^\s)\]\}\"'<>]+)", re.IGNORECASE
)

# Wikilinks inside a gap "## Shows up in" section: [[wiki/sources/foo]] (strip alias/anchor)
_WIKILINK_RE = re.compile(r"\[\[(wiki/[^\]|#]+)")

# Trailing punctuation to strip off a raw DOI token.
_DOI_TRAILING = ".,;:)]}"


def _norm_arxiv_id(raw: str) -> str:
    """Return arxiv:<id> with any version suffix (vN) stripped."""
    raw = raw.strip()
    m = re.search(r"(\d{4}\.\d{4,5})", raw)
    return f"arxiv:{m.group(1)}" if m else raw


def _norm_ingest_id(rid: str) -> str:
    """Normalize an ingest_index row id to the canonical comparison form.

    arXiv ids drop any version suffix; DOIs are lowercased. Everything else is
    returned unchanged (stripped).
    """
    rid = (rid or "").strip()
    low = rid.lower()
    if low.startswith("arxiv:"):
        return _norm_arxiv_id(rid[len("arxiv:"):])
    if low.startswith("doi:"):
        return "doi:" + rid[len("doi:"):].strip().lower()
    return rid


def _iter_wiki_files(vault_root: str):
    """Yield every wiki/**/*.md path under the vault (sorted, deterministic)."""
    wiki = Path(vault_root) / "wiki"
    if not wiki.is_dir():
        return
    for p in sorted(wiki.rglob("*.md")):
        yield p


def harvest_cited_ids(vault_root: str) -> dict:
    """Scan wiki/**/*.md for arXiv + DOI citations.

    Returns a dict mapping canonical id -> {"id_type": "arxiv"|"doi", "pages": set[str]}
    where ``pages`` are vault-relative posix paths of the citing wiki pages.
    """
    vroot = Path(vault_root)
    cited: dict[str, dict] = {}
    for f in _iter_wiki_files(vault_root):
        try:
            text = f.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        rel = f.relative_to(vroot).as_posix()
        for m in _ARXIV_LINK_RE.finditer(text):
            cid = f"arxiv:{m.group(1)}"
            cited.setdefault(cid, {"id_type": "arxiv", "pages": set()})["pages"].add(rel)
        for m in _ARXIV_TEXT_RE.finditer(text):
            cid = f"arxiv:{m.group(1)}"
            cited.setdefault(cid, {"id_type": "arxiv", "pages": set()})["pages"].add(rel)
        for m in _DOI_LINK_RE.finditer(text):
            doi = m.group(1).rstrip(_DOI_TRAILING).lower()
            cid = f"doi:{doi}"
            cited.setdefault(cid, {"id_type": "doi", "pages": set()})["pages"].add(rel)
    return cited


def gap_referenced_pages(vault_root: str) -> dict:
    """Map each wiki page named in an OPEN gap's ``## Shows up in`` body to its gap id(s).

    Returns dict page (vault-relative, no .md, e.g. "wiki/sources/photons-to-tokens")
    -> set of GAP ids referencing it.
    """
    from web_decision import _parse_frontmatter, _parse_section

    gap_dir = Path(vault_root) / "wiki" / "gap"
    result: dict[str, set] = {}
    if not gap_dir.is_dir():
        return result
    for f in sorted(gap_dir.glob("GAP-*.md")):
        if f.name.startswith("_") or f.name == "index.md":
            continue
        try:
            text = f.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        fm = _parse_frontmatter(text)
        status = fm.get("status", "open").strip().lower()
        if status and status != "open":
            continue
        gid = (fm.get("id") or "").strip()
        if not gid:
            m = re.match(r"^(GAP-\d+)", f.stem, re.IGNORECASE)
            gid = m.group(1).upper() if m else f.stem
        body = _parse_section(text, "Shows up in")
        for wl in _WIKILINK_RE.finditer(body):
            page = wl.group(1).split("#")[0].strip().rstrip("/")
            result.setdefault(page, set()).add(gid)
    return result


def _ingest_rows(vault_root: str) -> list[dict]:
    """Load ingest_index rows via web_crawl._load_ingest_rows, with a direct-file
    fallback for vaults not registered in vault_config (that helper raises SystemExit,
    which it does not catch, when it cannot resolve the vault name)."""
    from web_crawl import _load_ingest_rows

    try:
        return _load_ingest_rows(vault_root)
    except BaseException:  # SystemExit from unresolved vault name, etc.
        p = Path(vault_root) / "meta" / "ingest_index.json"
        if p.exists():
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
                return list(data.get("sources", {}).values())
            except (OSError, json.JSONDecodeError):
                pass
        return []


def ingest_status_map(vault_root: str) -> dict:
    """Map canonical id -> (status, filename, file_exists) from the ingest index."""
    vroot = Path(vault_root)
    out: dict[str, tuple] = {}
    for row in _ingest_rows(vault_root):
        cid = _norm_ingest_id(row.get("id", ""))
        if not cid:
            continue
        fn = (row.get("filename") or "").strip()
        exists = bool(fn) and (vroot / fn).exists()
        out[cid] = (row.get("status", "") or "", fn, exists)
    return out


# ---------------------------------------------------------------------------
# Backfill set construction
# ---------------------------------------------------------------------------


def build_backfill(vault_root: str) -> dict:
    """Compute the backfill set + the excluded categories (for the trace).

    Returns:
      {
        "cited": {id: {...}},                      # every cited id
        "truly_ingested": [(id, pages, status)],   # excluded (real file on disk)
        "already_queued":  [(id, pages, status)],  # excluded (pending/waiting_approval)
        "skipped_rejected":[(id, pages, status)],  # excluded (sticky reject)
        "backfill": [ {id, id_type, pages, gap_refs, signal, prior_status} ],
      }
    ``backfill`` is ordered highest-signal first: gap ``## Shows up in`` cites before
    plain wiki cites, then by id.
    """
    cited = harvest_cited_ids(vault_root)
    gap_pages = gap_referenced_pages(vault_root)
    status = ingest_status_map(vault_root)

    truly_ingested: list[tuple] = []
    already_queued: list[tuple] = []
    skipped_rejected: list[tuple] = []
    backfill: list[dict] = []

    for cid, info in cited.items():
        pages = sorted(info["pages"])
        st = status.get(cid)
        stat = st[0] if st else ""
        exists = st[2] if st else False

        if stat == "ingested" and exists:
            truly_ingested.append((cid, pages, stat))
            continue
        if stat in ("pending", "waiting_approval"):
            already_queued.append((cid, pages, stat))
            continue
        if stat in ("rejected", "deleted"):
            # Decided by the user: `rejected` is a sticky reject; `deleted` was
            # removed from the index (typically with the PDF still on disk). Do
            # not resurrect either -- re-staging a decided item would nag the user.
            skipped_rejected.append((cid, pages, stat))
            continue

        # Everything else (not-in-index / unknown status) = backfill.
        gap_refs: set = set()
        for p in pages:
            stem = p[:-3] if p.endswith(".md") else p
            if stem in gap_pages:
                gap_refs |= gap_pages[stem]
        backfill.append(
            {
                "id": cid,
                "id_type": info["id_type"],
                "pages": pages,
                "gap_refs": sorted(gap_refs),
                "signal": "gap" if gap_refs else "wiki",
                "prior_status": stat or "not-in-index",
            }
        )

    # Highest-signal first: gap-cited before wiki-only, then stable by id.
    backfill.sort(key=lambda b: (0 if b["signal"] == "gap" else 1, b["id"]))

    return {
        "cited": cited,
        "truly_ingested": truly_ingested,
        "already_queued": already_queued,
        "skipped_rejected": skipped_rejected,
        "backfill": backfill,
    }


# ---------------------------------------------------------------------------
# Metadata fetch (by-id; bounded; bypasses seen.json entirely)
# ---------------------------------------------------------------------------


def fetch_arxiv_by_id(arxiv_id: str, *, timeout: int = 20) -> dict | None:
    """Fetch a single arXiv paper's metadata by id via the Atom API ``id_list``.

    Returns a candidate dict (web_harvest shape) or None on any failure. Bounded by
    ``timeout`` seconds; NEVER touches the seen.json dedup cache.
    """
    import web_harvest as wh  # also puts scripts/research on sys.path

    try:
        import requests  # type: ignore[import]
        from lib.sources.arxiv import ENDPOINT, _parse
    except Exception as exc:  # pragma: no cover - import guard
        print(f"[web_backfill] arxiv by-id fetch unavailable: {exc}", file=sys.stderr)
        return None

    try:
        resp = requests.get(
            ENDPOINT,
            params={"id_list": arxiv_id, "max_results": 1},
            timeout=timeout,
        )
    except Exception as exc:
        print(f"[web_backfill] arxiv fetch failed for {arxiv_id}: {exc}", file=sys.stderr)
        return None
    if resp.status_code != 200:
        print(
            f"[web_backfill] arxiv fetch non-200 ({resp.status_code}) for {arxiv_id}",
            file=sys.stderr,
        )
        return None
    results = _parse(resp.text)
    if not results:
        return None
    return wh._result_to_paper_candidate(results[0], "arxiv")


def _rationale(item: dict) -> str:
    """One-line rationale naming the citing page(s) and prior index status."""
    pages = ", ".join(f"[[{p[:-3] if p.endswith('.md') else p}]]" for p in item["pages"][:3])
    gap = f" ({', '.join(item['gap_refs'])})" if item["gap_refs"] else ""
    txt = (
        f"Cited in {pages}{gap} but not truly ingested "
        f"(index status: {item['prior_status']}). Known-gap backfill."
    )
    return txt[:200].rstrip()


def _to_candidate(vault_root: str, item: dict) -> dict:
    """Build a report/enqueue candidate dict for one backfill item (fetches metadata)."""
    cid = item["id"]
    score = 1.0 if item["signal"] == "gap" else 0.5
    if item["id_type"] == "arxiv":
        arxiv_id = cid[len("arxiv:"):]
        meta = fetch_arxiv_by_id(arxiv_id) or {}
        url = meta.get("url") or f"https://arxiv.org/abs/{arxiv_id}"
        title = meta.get("title") or f"arXiv {arxiv_id}"
        snippet = meta.get("snippet") or ""
        source_id = meta.get("source_id") or cid
        published = meta.get("published") or ""
        engine = "arxiv"
    else:
        doi = cid[len("doi:"):]
        url = f"https://doi.org/{doi}"
        title = f"DOI {doi}"
        snippet = ""
        source_id = cid
        published = ""
        engine = "backfill"
    return {
        "id": cid,
        "title": title,
        "url": url,
        "source_id": source_id,
        "snippet": snippet,
        "published": published,
        "engine": engine,
        "lane": "backfill",
        "score": score,
        "origin_ids": item["gap_refs"],
        "expected_evidence": _rationale(item),
        "rationale": _rationale(item),
    }


# ---------------------------------------------------------------------------
# Report writer (reuses web_crawl's checkbox-block format so report_approve parses it)
# ---------------------------------------------------------------------------

_BACKFILL_FRONTMATTER = """\
---
type: nightly_report
date: {today}
written_by: web
phase: backfill
---

# Web Backfill Report -- {today}

Known cited-but-missing papers, highest-signal first (gap `## Shows up in` cites, then
plain wiki cites). {n} candidate(s) staged.

"""


def _write_backfill_report(vault_root: str, today: str, candidates: list[dict]) -> Path:
    """Write meta/nightly_report/backfill-<today>.md in the shared checkbox format."""
    from web_crawl import (
        _CANDIDATE_BLOCK,
        _REPORT_INSTRUCTION,
        _REPORT_FOOTER,
        _content_summary,
        _relevance_paragraph,
        _relevance_links,
    )

    report_dir = Path(vault_root) / "meta" / "nightly_report"
    report_dir.mkdir(parents=True, exist_ok=True)
    report_path = report_dir / f"backfill-{today}.md"

    blocks: list[str] = [_REPORT_INSTRUCTION + "\n\n"]
    for n, c in enumerate(candidates, start=1):
        blocks.append(
            _CANDIDATE_BLOCK.format(
                n=n,
                title=c.get("title", "(no title)"),
                ident=c["id"],
                engine=(c.get("engine") or "").strip(),
                source_id=(c.get("source_id") or "").strip(),
                published=(c.get("published") or "").strip() or "—",
                score=float(c.get("score", 0.0)),
                lane=c.get("lane", "backfill"),
                relevance_links=_relevance_links(c.get("origin_ids") or [], vault_root),
                content_summary=_content_summary(c),
                relevance_paragraph=_relevance_paragraph(c),
                url=c.get("url", ""),
            )
        )
    body = "\n".join(blocks) if candidates else "_No cited-but-missing papers found._"
    content = _BACKFILL_FRONTMATTER.format(today=today, n=len(candidates)) + body + _REPORT_FOOTER
    report_path.write_text(content, encoding="utf-8")
    return report_path


# ---------------------------------------------------------------------------
# Explain trace
# ---------------------------------------------------------------------------


def _render_explain(res: dict) -> str:
    """Render a human-readable trace of the backfill decision to a string."""
    lines: list[str] = []
    lines.append(f"cited ids found: {len(res['cited'])}")
    for cid in sorted(res["cited"]):
        pages = sorted(res["cited"][cid]["pages"])
        lines.append(f"  cited {cid} <- {', '.join(pages)}")
    lines.append("")
    lines.append(f"truly ingested (EXCLUDED): {len(res['truly_ingested'])}")
    for cid, _pages, stat in sorted(res["truly_ingested"]):
        lines.append(f"  excluded {cid} (status={stat}, file present)")
    lines.append(f"already queued (EXCLUDED): {len(res['already_queued'])}")
    for cid, _pages, stat in sorted(res["already_queued"]):
        lines.append(f"  excluded {cid} (status={stat})")
    if res["skipped_rejected"]:
        lines.append(f"rejected/deleted decided (EXCLUDED): {len(res['skipped_rejected'])}")
        for cid, _pages, stat in sorted(res["skipped_rejected"]):
            lines.append(f"  excluded {cid} (status={stat})")
    lines.append("")
    lines.append(f"BACKFILL SET: {len(res['backfill'])}")
    for item in res["backfill"]:
        refs = f" gap={','.join(item['gap_refs'])}" if item["gap_refs"] else ""
        lines.append(
            f"  [{item['signal']}] {item['id']} (prior={item['prior_status']}){refs}"
            f" <- {', '.join(item['pages'])}"
        )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------


def run(
    vault_root: str,
    *,
    dry_run: bool = False,
    limit: int = 10,
    explain: bool = False,
    today: str | None = None,
) -> dict:
    """Compute the backfill set and (unless dry_run) stage it + write the report.

    Returns a result dict:
      {"backfill_count", "would_stage"|"staged", "report_path"|None,
       "enqueued": [...], "trace": <str|None>}
    """
    today_s = today or date.today().isoformat()
    res = build_backfill(vault_root)
    backfill = res["backfill"]
    to_process = backfill[:limit]

    trace_str = _render_explain(res) if explain else None
    if explain and trace_str:
        print(trace_str, file=sys.stderr)

    if dry_run:
        # Write NOTHING. Report what WOULD stage.
        would = [
            {
                "id": item["id"],
                "signal": item["signal"],
                "prior_status": item["prior_status"],
                "gap_refs": item["gap_refs"],
                "citing_pages": item["pages"],
            }
            for item in to_process
        ]
        return {
            "backfill_count": len(backfill),
            "would_stage": would,
            "report_path": None,
            "enqueued": [],
            "trace": trace_str,
        }

    # Live run: fetch metadata, enqueue, write report.
    from web_crawl import _import_ingest_index

    ii = _import_ingest_index()
    vault_name = Path(vault_root).name
    candidates: list[dict] = []
    enqueued: list[str] = []
    for item in to_process:
        cand = _to_candidate(vault_root, item)
        candidates.append(cand)
        try:
            row = ii.enqueue(
                cand["id"],
                url=cand["url"],
                title=cand["title"],
                rationale=cand["rationale"],
                score=cand["score"],
                discovered_by="web",
                objective_ids=list(cand["origin_ids"]),
                published=cand["published"] or None,
                source_id=cand["source_id"],
                engine=cand["engine"],
                name=vault_name,
            )
            enqueued.append(row.get("id", cand["id"]))
        except Exception as exc:
            print(f"[web_backfill] enqueue({cand['id']!r}) failed: {exc}", file=sys.stderr)

    report_path = _write_backfill_report(vault_root, today_s, candidates)
    return {
        "backfill_count": len(backfill),
        "staged": [c["id"] for c in candidates],
        "report_path": str(report_path),
        "enqueued": enqueued,
        "trace": trace_str,
    }


# ---------------------------------------------------------------------------
# Vault root resolution (reuse web_crawl's resolver)
# ---------------------------------------------------------------------------


def _resolve_vault_root(vault_arg: str | None) -> str:
    from web_crawl import _resolve_vault_root as _rv

    return _rv(vault_arg)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    if argv is None:
        argv = sys.argv[1:]

    parser = argparse.ArgumentParser(
        prog="web_backfill",
        description=(
            "Backfill KNOWN gaps: stage papers already cited in the wiki (arXiv/DOI) "
            "but not truly ingested. Free APIs only, $0. Run on demand."
        ),
    )
    parser.add_argument("--vault", default=None, help="Vault root path or name")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        dest="dry_run",
        help="Compute the backfill set and print what WOULD stage; write nothing.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        dest="as_json",
        help="Print the result dict as JSON to stdout.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=10,
        metavar="N",
        help="Max candidates to stage (default: 10).",
    )
    parser.add_argument(
        "--explain",
        action="store_true",
        dest="explain",
        help=(
            "Print a decision trace to STDERR: cited ids found, truly-ingested "
            "(excluded), already-queued (excluded), and the backfill set with citing pages."
        ),
    )

    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return exc.code if exc.code is not None else 2

    vault_root = _resolve_vault_root(args.vault)
    result = run(
        vault_root,
        dry_run=args.dry_run,
        limit=args.limit,
        explain=args.explain,
    )

    if args.as_json:
        printable = {k: v for k, v in result.items() if k != "trace"}
        print(json.dumps(printable, indent=2, ensure_ascii=False))
    else:
        n = result["backfill_count"]
        if args.dry_run:
            would = result.get("would_stage", [])
            print(f"[web_backfill] DRY-RUN: {n} cited-but-missing paper(s); would stage {len(would)}:")
            for w in would:
                refs = f" ({', '.join(w['gap_refs'])})" if w["gap_refs"] else ""
                print(f"  [{w['signal']}] {w['id']}{refs} <- {', '.join(w['citing_pages'])}")
        else:
            staged = result.get("staged", [])
            print(f"[web_backfill] staged {len(staged)} of {n} cited-but-missing paper(s).")
            print(f"[web_backfill] report: {result.get('report_path')}")
            for sid in staged:
                print(f"  staged {sid}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
