"""Offline tests for scripts/web_backfill.py (Phase 3 known-gap backfill).

All fixtures are tiny, on-disk, and hermetic -- NO live network. The arXiv by-id
fetch is never exercised here (only build_backfill + dry-run, which do no fetching).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
_SCRIPTS = _REPO_ROOT / "scripts"
for _p in (str(_REPO_ROOT), str(_SCRIPTS)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import web_backfill as wb  # noqa: E402


# ---------------------------------------------------------------------------
# Fixture vault builder
# ---------------------------------------------------------------------------


def _build_vault(tmp_path: Path) -> Path:
    """A tiny vault with:
      - Splitwise 2311.18677: cited + status=ingested + raw file EXISTS -> excluded.
      - 2401.00001 (arxiv abs): cited, NOT in index -> in backfill set.
      - 2402.00002 (arxiv pdf): cited, status=ingested but raw file MISSING -> backfill.
      - DOI 10.1145/1234567.8901234: cited, not in index -> in backfill set.
      - GAP-01 open, "## Shows up in" -> wiki/concepts/gapped-page (which cites 2401.00001)
        so 2401.00001 gets the 'gap' signal.
    """
    vault = tmp_path / "TestVault"
    (vault / "wiki" / "sources").mkdir(parents=True)
    (vault / "wiki" / "concepts").mkdir(parents=True)
    (vault / "wiki" / "gap").mkdir(parents=True)
    (vault / "raw" / "papers").mkdir(parents=True)
    (vault / "meta").mkdir(parents=True)

    # Splitwise source page (truly ingested)
    (vault / "wiki" / "sources" / "splitwise.md").write_text(
        "See https://arxiv.org/abs/2311.18677 for the split.\n", encoding="utf-8"
    )
    # A gap-referenced concept page that cites 2401.00001 (pdf link) + a DOI
    (vault / "wiki" / "concepts" / "gapped-page.md").write_text(
        "Related work: https://arxiv.org/pdf/2401.00001v2 and "
        "https://doi.org/10.1145/1234567.8901234 discuss this.\n",
        encoding="utf-8",
    )
    # A plain page citing 2402.00002 (status=ingested but file missing -> backfill)
    (vault / "wiki" / "sources" / "ghost.md").write_text(
        "Ghost paper at arxiv.org/pdf/2402.00002 -- file was deleted.\n",
        encoding="utf-8",
    )

    # Open gap whose "## Shows up in" names the concept page
    (vault / "wiki" / "gap" / "GAP-01-example.md").write_text(
        "---\n"
        "type: gap\n"
        "id: GAP-01\n"
        "title: Example gap\n"
        "status: open\n"
        "---\n\n"
        "## Missing\nsomething\n\n"
        "## Shows up in\n"
        "- [[wiki/concepts/gapped-page]] -- cites 2401.00001\n",
        encoding="utf-8",
    )

    # Splitwise raw file EXISTS on disk (the truth-check must trust the file, not status)
    (vault / "raw" / "papers" / "2311.18677v2.pdf").write_text("%PDF-1.4\n", encoding="utf-8")

    # ingest index: splitwise ingested+file present; 2402.00002 ingested but file ABSENT
    index = {
        "version": 3,
        "vault": "TestVault",
        "sources": {
            "arxiv:2311.18677": {
                "id": "arxiv:2311.18677",
                "status": "ingested",
                "filename": "raw/papers/2311.18677v2.pdf",
                "title": "Splitwise",
            },
            "arxiv:2402.00002": {
                "id": "arxiv:2402.00002",
                "status": "ingested",
                "filename": "raw/papers/2402.00002.pdf",  # NOT created -> status lies
                "title": "Ghost",
            },
        },
    }
    (vault / "meta" / "ingest_index.json").write_text(
        json.dumps(index, indent=2), encoding="utf-8"
    )
    return vault


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_harvest_cited_ids_arxiv_abs_pdf_and_doi(tmp_path):
    vault = _build_vault(tmp_path)
    cited = wb.harvest_cited_ids(str(vault))
    # arXiv abs + pdf links (version stripped) and a DOI are all captured.
    assert "arxiv:2311.18677" in cited
    assert "arxiv:2401.00001" in cited  # from .../pdf/2401.00001v2 (v stripped)
    assert "arxiv:2402.00002" in cited
    assert "doi:10.1145/1234567.8901234" in cited
    # citing page is recorded
    assert any(
        p.endswith("gapped-page.md") for p in cited["arxiv:2401.00001"]["pages"]
    )


def test_truth_check_excludes_ingested_with_file_includes_missing(tmp_path):
    vault = _build_vault(tmp_path)
    res = wb.build_backfill(str(vault))
    backfill_ids = {b["id"] for b in res["backfill"]}

    # Splitwise: status=ingested AND raw file exists -> EXCLUDED (must NOT be backfilled).
    assert "arxiv:2311.18677" not in backfill_ids
    assert any(cid == "arxiv:2311.18677" for cid, _p, _s in res["truly_ingested"])

    # 2402.00002: status=ingested but file MISSING -> status lies -> INCLUDED.
    assert "arxiv:2402.00002" in backfill_ids

    # 2401.00001: cited, not in index -> INCLUDED.
    assert "arxiv:2401.00001" in backfill_ids
    # It is cited on a gap-referenced page -> highest 'gap' signal.
    item = next(b for b in res["backfill"] if b["id"] == "arxiv:2401.00001")
    assert item["signal"] == "gap"
    assert "GAP-01" in item["gap_refs"]


def test_gap_signal_orders_before_wiki(tmp_path):
    vault = _build_vault(tmp_path)
    res = wb.build_backfill(str(vault))
    # The first backfill item must be a gap-signal one (highest priority).
    assert res["backfill"][0]["signal"] == "gap"


def test_seen_cache_bypassed(tmp_path, monkeypatch):
    """build_backfill must NEVER consult the seen.json dedup cache."""
    import web_harvest

    def _boom(*a, **k):  # pragma: no cover - only fires on regression
        raise AssertionError("dedup_seen must not be called by web_backfill")

    monkeypatch.setattr(web_harvest, "dedup_seen", _boom)
    vault = _build_vault(tmp_path)
    res = wb.build_backfill(str(vault))
    # A cited-but-missing id survives regardless of any seen-cache state.
    assert "arxiv:2401.00001" in {b["id"] for b in res["backfill"]}


def test_dry_run_writes_nothing(tmp_path):
    vault = _build_vault(tmp_path)
    index_before = (vault / "meta" / "ingest_index.json").read_text(encoding="utf-8")

    result = wb.run(str(vault), dry_run=True, limit=10, today="2026-07-09")

    # No report file created.
    report_dir = vault / "meta" / "nightly_report"
    assert not report_dir.exists() or not list(report_dir.glob("backfill-*.md"))
    # Ingest index untouched.
    assert (vault / "meta" / "ingest_index.json").read_text(encoding="utf-8") == index_before
    # Reports what WOULD stage.
    assert result["report_path"] is None
    ids = {w["id"] for w in result["would_stage"]}
    assert "arxiv:2401.00001" in ids
    assert "arxiv:2311.18677" not in ids  # excluded (truly ingested)


def test_textual_arxiv_citation_harvested(tmp_path):
    """Papers cited as 'arXiv NNNN.NNNNN' (textual, not a URL) are harvested.

    This is the common wiki citation form -- a not-yet-ingested paper has no
    sources page with an arxiv.org/abs URL, so it is referenced textually in
    concept/entity/gap pages. The URL-only pattern would miss exactly these.
    """
    vault = tmp_path / "TextVault"
    (vault / "wiki" / "concepts").mkdir(parents=True)
    (vault / "meta").mkdir(parents=True)
    (vault / "wiki" / "concepts" / "afd.md").write_text(
        "AFD was introduced in MegaScale-Infer (arXiv 2504.02263) and "
        "Splitwise (arXiv: 2311.18677v4); see also **arXiv**: 2507.19635.\n",
        encoding="utf-8",
    )
    (vault / "meta" / "ingest_index.json").write_text(
        json.dumps({"version": 3, "vault": "TextVault", "sources": {}}, indent=2),
        encoding="utf-8",
    )
    cited = wb.harvest_cited_ids(str(vault))
    assert "arxiv:2504.02263" in cited  # "arXiv 2504.02263"      (space)
    assert "arxiv:2311.18677" in cited  # "arXiv: 2311.18677v4"   (colon + version)
    assert "arxiv:2507.19635" in cited  # "**arXiv**: 2507.19635" (markdown bold)


def test_deleted_status_skipped_as_decided(tmp_path):
    """A cited id marked status=deleted is a user decision -> excluded, not re-staged."""
    vault = tmp_path / "DelVault"
    (vault / "wiki" / "concepts").mkdir(parents=True)
    (vault / "meta").mkdir(parents=True)
    (vault / "wiki" / "concepts" / "p.md").write_text(
        "Cited paper arXiv 2308.16369 here.\n", encoding="utf-8"
    )
    (vault / "meta" / "ingest_index.json").write_text(
        json.dumps(
            {
                "version": 3,
                "vault": "DelVault",
                "sources": {
                    "arxiv:2308.16369": {
                        "id": "arxiv:2308.16369",
                        "status": "deleted",
                        "filename": "",
                    }
                },
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    res = wb.build_backfill(str(vault))
    backfill_ids = {b["id"] for b in res["backfill"]}
    assert "arxiv:2308.16369" not in backfill_ids  # deleted = decided -> skip
    assert any(cid == "arxiv:2308.16369" for cid, _p, _s in res["skipped_rejected"])


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
