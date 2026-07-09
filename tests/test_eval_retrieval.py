"""tests/test_eval_retrieval.py -- hermetic, OFFLINE tests for the Phase-4 eval harness
(scripts/experiments/eval_retrieval.py).

No network, no live crawl:
  * evaluate() is exercised directly on a canned crawl result (plan/selected/trace).
  * run_crawl() is exercised with web_crawl.crawl monkeypatched to return a canned
    result, verifying the harness threads the experiment config in and restores
    web_crawl._load_config afterwards.

Verifies recall@5 + per-target found/rank/selected are computed correctly, including
arXiv version-suffix tolerance and URL (reachability) matching.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
_SCRIPTS = _ROOT / "scripts"
_EXP = _SCRIPTS / "experiments"
for p in (_EXP, _SCRIPTS, _ROOT):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import eval_retrieval as er  # noqa: E402
from web_decision import DecisionTrace  # noqa: E402


TARGETS = {
    "present_in_vault": [
        {"id": "arxiv:2504.02263", "name": "MegaScale-Infer"},
        {"id": "arxiv:2507.19635", "name": "Gimlet/Asgar"},
        {"id": "arxiv:2311.18677", "name": "Splitwise"},
    ],
    "reachability": [
        {
            "url": "https://www.chiplog.io/p/inside-attention-ffn-disaggregation",
            "name": "Chiplog",
            "via": "websearch",
        }
    ],
}

CHIPLOG_URL = "https://www.chiplog.io/p/inside-attention-ffn-disaggregation"


def _canned_result():
    """A crawl-style dry-run result: {plan, selected, trace}.

    Ranked pool (deliberately out of score order to prove the harness sorts it):
      arxiv:2504.02263      score 0.70  -> MegaScale, rank 2
      arxiv:2311.18677v1    score 0.90  -> Splitwise (versioned), rank 1
      <chiplog url>         score 0.60  -> Chiplog,   rank 3
      arxiv:9999.99999      score 0.50  -> noise,     rank 4
    Gimlet/Asgar (2507.19635) is NOT in the pool -> found=False.
    Selected (top set) contains Splitwise + MegaScale only; Chiplog NOT selected.
    """
    trace = DecisionTrace()
    trace.add(
        "rank",
        "scores",
        path="fallback",
        candidates=[
            {"ident": "arxiv:2504.02263", "score": 0.70},
            {"ident": "arxiv:2311.18677v1", "score": 0.90},
            {"ident": CHIPLOG_URL, "score": 0.60},
            {"ident": "arxiv:9999.99999", "score": 0.50},
        ],
        n=4,
    )
    selected = [
        {"source_id": "arxiv:2311.18677v1", "title": "Splitwise"},
        {"source_id": "arxiv:2504.02263", "title": "MegaScale-Infer"},
    ]
    return {"plan": {"targets": []}, "selected": selected, "trace": trace}


def test_evaluate_recall_and_per_target():
    report = er.evaluate(_canned_result(), TARGETS)

    assert report["pool_size"] == 4
    assert report["n_targets"] == 4
    # 2 of 4 targets selected -> recall@5 == 0.5
    assert report["n_selected_targets"] == 2
    assert report["recall_at_5"] == pytest.approx(0.5)

    rows = {r["name"]: r for r in report["rows"]}

    # Splitwise: versioned ident in pool, top score -> rank 1, selected
    assert rows["Splitwise"]["found"] is True
    assert rows["Splitwise"]["rank"] == 1
    assert rows["Splitwise"]["selected"] is True

    # MegaScale: rank 2, selected
    assert rows["MegaScale-Infer"]["found"] is True
    assert rows["MegaScale-Infer"]["rank"] == 2
    assert rows["MegaScale-Infer"]["selected"] is True

    # Chiplog: reachability, found via URL match at rank 3, NOT selected
    assert rows["Chiplog"]["found"] is True
    assert rows["Chiplog"]["rank"] == 3
    assert rows["Chiplog"]["selected"] is False

    # Gimlet/Asgar: absent from pool
    assert rows["Gimlet/Asgar"]["found"] is False
    assert rows["Gimlet/Asgar"]["rank"] is None
    assert rows["Gimlet/Asgar"]["selected"] is False


def test_evaluate_with_backfill_lifts_present_in_vault():
    """A present_in_vault target not crawl-selected but in the backfill SET counts as
    retrieved; reachability targets are never counted via backfill."""
    backfill_ids = {"arxiv:2507.19635", "arxiv:2311.18677", "arxiv:2504.02263"}
    report = er.evaluate(_canned_result(), TARGETS, backfill_ids)

    # crawl-select still only sees Splitwise + MegaScale
    assert report["n_selected_targets"] == 2
    # backfill lifts Gimlet (absent from the crawl pool) -> 3 of 4 retrieved
    assert report["n_retrieved_targets"] == 3
    assert report["recall_at_5"] == pytest.approx(0.75)
    assert report["with_backfill"] is True

    rows = {r["name"]: r for r in report["rows"]}
    # Gimlet: not found/selected by crawl, but surfaced by backfill -> retrieved
    assert rows["Gimlet/Asgar"]["selected"] is False
    assert rows["Gimlet/Asgar"]["backfill"] is True
    assert rows["Gimlet/Asgar"]["retrieved"] is True
    # Splitwise: selected AND in backfill set
    assert rows["Splitwise"]["backfill"] is True
    assert rows["Splitwise"]["retrieved"] is True
    # Chiplog: reachability -> backfill never applies, still not retrieved
    assert rows["Chiplog"]["backfill"] is False
    assert rows["Chiplog"]["retrieved"] is False


def test_evaluate_empty_pool_zero_recall():
    trace = DecisionTrace()  # no rank record
    report = er.evaluate({"plan": {}, "selected": [], "trace": trace}, TARGETS)
    assert report["pool_size"] == 0
    assert report["recall_at_5"] == pytest.approx(0.0)
    assert all(r["found"] is False and r["selected"] is False for r in report["rows"])


def test_arxiv_core_and_url_matchers():
    assert er._arxiv_core("arxiv:2311.18677v3") == "2311.18677"
    assert er._arxiv_core("2504.02263") == "2504.02263"
    assert er._match_ident("arxiv:2311.18677v1", {"id": "arxiv:2311.18677"}) is True
    assert er._match_ident("arxiv:2401.09670", {"id": "arxiv:2311.18677"}) is False
    assert er._match_ident(CHIPLOG_URL + "/", {"url": CHIPLOG_URL}) is True
    assert er._match_ident("https://example.com/x", {"url": CHIPLOG_URL}) is False


def test_run_crawl_monkeypatched_restores_load_config():
    import web_crawl

    canned = _canned_result()
    sentinel_config = {"marker": "experiment-config"}
    captured = {}

    orig_load_config = web_crawl._load_config
    orig_crawl = web_crawl.crawl

    def fake_crawl(*, vault_root, dry_run, per_source_limit, agent_candidates_path):
        # Prove the experiment config is monkeypatched in during the call.
        captured["config"] = web_crawl._load_config()
        captured["dry_run"] = dry_run
        captured["limit"] = per_source_limit
        captured["agent_candidates_path"] = agent_candidates_path
        return canned

    web_crawl.crawl = fake_crawl  # type: ignore[assignment]
    try:
        result = er.run_crawl(
            vault_root="/tmp/does-not-matter",
            config=sentinel_config,
            agent_candidates="/tmp/chiplog.json",
            limit=3,
        )
    finally:
        web_crawl.crawl = orig_crawl  # type: ignore[assignment]

    assert result is canned
    assert captured["config"] == sentinel_config
    assert captured["dry_run"] is True
    assert captured["limit"] == 3
    assert captured["agent_candidates_path"] == "/tmp/chiplog.json"
    # _load_config restored to the original after run_crawl returns
    assert web_crawl._load_config is orig_load_config

    # And the harness computes the same recall from the canned result.
    report = er.evaluate(result, TARGETS)
    assert report["recall_at_5"] == pytest.approx(0.5)
