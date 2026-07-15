"""tests/test_web_query.py -- hermetic, offline tests for scripts/web_query.py.

NO real LLM call / network call in any test.

Coverage:
  1. LLM path (monkeypatched): reformulate() -> method=="llm", per-engine queries,
     queries (flat list) updated, old_queries recorded, rationale present.
  2. use_llm=False: deterministic fallback, method=="fallback", per-engine variants
     present, NO subprocess call attempted.
  3. LLM returns malformed/garbage: graceful fallback per target, method=="fallback".
  4. LLM returns partial JSON (only some target_ids): targets without LLM result
     fall back; those with LLM result use method=="llm".
  5. _wiki_context: tmp vault with shows_up_in wiki page + gap file -> non-empty snippet.
  6. _wiki_context: missing files -> "".
  7. _wiki_context: target without _gap -> "".
  8. _deterministic_queries: returns per-engine dict, all values non-empty lists.
  9. _deterministic_queries: fillable_by filter respected.
  10. _parse_llm_queries: valid fenced JSON -> parsed dict.
  11. _parse_llm_queries: garbage input -> None.
  12. _parse_llm_queries: missing queries_by_engine key -> None.
  13. Multiple targets, all LLM: each gets its own queries_by_engine.
  14. Empty targets list: returns [].
  15. max_per_engine cap: engine lists capped at max_per_engine.
  16. WEB_QUERY_REFORMULATION_PROMPT importable and contains required placeholders.
  17. Confirm NO real claude_agent.sh or network call in any test via assertion.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

# ---------------------------------------------------------------------------
# sys.path: scripts/ and repo root must be importable
# ---------------------------------------------------------------------------
_ROOT = Path(__file__).resolve().parent.parent
_SCRIPTS = _ROOT / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import web_query
from web_query import (
    reformulate,
    _wiki_context,
    _deterministic_queries,
    _parse_llm_queries,
    _cap_engines,
)


# ---------------------------------------------------------------------------
# Test fixtures / helpers
# ---------------------------------------------------------------------------

def _make_gap_target(
    target_id: str = "GAP-01",
    fillable_by: list | None = None,
    old_queries: list | None = None,
    expected_evidence: str = "survey of P/D disaggregation scheduling algorithms",
    missing: str = "survey of P/D disaggregation scheduling algorithms",
    title: str = "Disaggregated inference scheduling gaps",
    shows_up_in: str = "[[wiki/concepts/inference]]",
) -> dict:
    """Build a minimal gap-lane target dict (mirrors web_decision output shape)."""
    fb = fillable_by if fillable_by is not None else ["arxiv", "web", "forum"]
    return {
        "target_id": target_id,
        "lane": "gap",
        "origin_ids": [target_id],
        "priority": "high",
        "queries": old_queries if old_queries is not None else ["Disaggregated inference scheduling"],
        "expected_evidence": expected_evidence,
        "fillable_by": fb,
        "routed_sources": [],
        "_gap": {
            "id": target_id,
            "title": title,
            "shows_up_in": shows_up_in,
            "missing": missing,
            "fillable_by": fb,
            "topics": ["T-0001"],
            "priority": "high",
        },
        "_dir": None,
    }


_CANNED_LLM_JSON = {
    "GAP-01": {
        "queries_by_engine": {
            "arxiv": ["disaggregated KV cache prefill decode separation"],
            "web": ["how does KV cache disaggregation reduce decode latency?"],
            "forum": ["KV cache disaggregation serving"],
        },
        "rationale": "Targets the missing scheduling survey via arxiv and practitioner forums",
    }
}

_CANNED_LLM_RESPONSE = "```json\n" + json.dumps(_CANNED_LLM_JSON) + "\n```"


def _make_vault(tmp_path: Path) -> Path:
    """Create a minimal vault skeleton."""
    (tmp_path / "wiki" / "concepts").mkdir(parents=True, exist_ok=True)
    (tmp_path / "wiki" / "gap").mkdir(parents=True, exist_ok=True)
    (tmp_path / "objective" / "purpose").mkdir(parents=True, exist_ok=True)
    return tmp_path


# ---------------------------------------------------------------------------
# 1. LLM path (monkeypatched)
# ---------------------------------------------------------------------------

def test_llm_path_reformulate(tmp_path):
    """reformulate() with monkeypatched LLM -> method==llm, per-engine queries."""
    vault = _make_vault(tmp_path)
    target = _make_gap_target()

    with patch.object(web_query, "_invoke_llm", return_value=_CANNED_LLM_RESPONSE) as mock_llm:
        results = reformulate(
            [target],
            purpose="Understand disaggregated LLM inference systems",
            vault_root=str(vault),
            use_llm=True,
        )

    assert len(results) == 1
    r = results[0]

    # method == llm
    ref = r["reformulation"]
    assert ref["method"] == "llm"
    assert ref["rationale"] != ""
    assert isinstance(ref["old_queries"], list)
    assert ref["old_queries"] == target["queries"]

    # queries_by_engine populated
    qbe = r["queries_by_engine"]
    assert "arxiv" in qbe
    assert isinstance(qbe["arxiv"], list)
    assert len(qbe["arxiv"]) >= 1

    # flat queries non-empty
    assert len(r["queries"]) >= 1

    # LLM was called
    mock_llm.assert_called_once()


# ---------------------------------------------------------------------------
# 2. use_llm=False -> deterministic fallback
# ---------------------------------------------------------------------------

def test_no_llm_deterministic_fallback(tmp_path):
    """use_llm=False -> method==fallback, per-engine variants, no subprocess."""
    vault = _make_vault(tmp_path)
    target = _make_gap_target()

    subprocess_calls = []

    with patch.object(web_query, "_invoke_llm", side_effect=lambda *a, **kw: subprocess_calls.append(a) or "") as mock_llm:
        results = reformulate(
            [target],
            purpose="Understand disaggregated LLM inference systems",
            vault_root=str(vault),
            use_llm=False,
        )

    assert len(results) == 1
    r = results[0]

    ref = r["reformulation"]
    assert ref["method"] == "fallback"

    # Per-engine variants present (at least one engine)
    qbe = r["queries_by_engine"]
    assert len(qbe) >= 1
    for v in qbe.values():
        assert isinstance(v, list)
        assert len(v) >= 1

    # NO LLM invocation
    mock_llm.assert_not_called()
    assert subprocess_calls == []


# ---------------------------------------------------------------------------
# 3. LLM returns malformed/garbage -> graceful fallback
# ---------------------------------------------------------------------------

def test_llm_malformed_falls_back(tmp_path):
    """Garbage LLM output -> graceful fallback per target (method==fallback)."""
    vault = _make_vault(tmp_path)
    target = _make_gap_target()

    with patch.object(web_query, "_invoke_llm", return_value="not json at all !!"):
        results = reformulate(
            [target],
            purpose="Understand disaggregated LLM inference",
            vault_root=str(vault),
            use_llm=True,
        )

    assert len(results) == 1
    r = results[0]
    assert r["reformulation"]["method"] == "fallback"
    # Still has queries
    assert len(r["queries"]) >= 1


# ---------------------------------------------------------------------------
# 4. LLM returns partial JSON (only some target_ids)
# ---------------------------------------------------------------------------

def test_llm_partial_json_mixed_fallback(tmp_path):
    """LLM returns JSON for target A but not B -> A=llm, B=fallback."""
    vault = _make_vault(tmp_path)
    t_a = _make_gap_target("GAP-01")
    t_b = _make_gap_target("GAP-02", title="Optical interconnect prior art")

    partial_json = json.dumps({
        "GAP-01": {
            "queries_by_engine": {
                "arxiv": ["disaggregated KV prefill separation"],
            },
            "rationale": "Focused arxiv query",
        }
    })

    with patch.object(web_query, "_invoke_llm", return_value=f"```json\n{partial_json}\n```"):
        results = reformulate(
            [t_a, t_b],
            purpose="Understand LLM inference",
            vault_root=str(vault),
            use_llm=True,
        )

    assert len(results) == 2
    methods = {r["target_id"]: r["reformulation"]["method"] for r in results}
    assert methods["GAP-01"] == "llm"
    assert methods["GAP-02"] == "fallback"


# ---------------------------------------------------------------------------
# 5. _wiki_context: wiki page + gap file -> non-empty snippet
# ---------------------------------------------------------------------------

def test_wiki_context_with_files(tmp_path):
    """_wiki_context reads shows_up_in wiki page and returns non-empty snippet."""
    vault = _make_vault(tmp_path)

    # Create the wiki page referenced in shows_up_in
    wiki_page = vault / "wiki" / "concepts" / "inference.md"
    wiki_page.write_text(
        "---\ntype: concept\ntitle: Inference\n---\n"
        "Disaggregated inference separates prefill and decode phases. "
        "This allows scaling them independently.",
        encoding="utf-8",
    )

    target = _make_gap_target(
        shows_up_in="[[wiki/concepts/inference]]",
        missing="survey of scheduling algorithms for P/D disaggregation",
    )

    ctx = _wiki_context(target, str(vault))

    assert ctx != ""
    # Should mention the missing content
    assert "scheduling" in ctx.lower() or "survey" in ctx.lower() or "disaggregat" in ctx.lower()
    # Should be capped to ~400 chars
    assert len(ctx) <= 420


# ---------------------------------------------------------------------------
# 6. _wiki_context: missing files -> ""
# ---------------------------------------------------------------------------

def test_wiki_context_missing_files(tmp_path):
    """_wiki_context with non-existent shows_up_in page -> empty string."""
    vault = _make_vault(tmp_path)
    # wiki page does NOT exist
    target = _make_gap_target(
        shows_up_in="[[wiki/concepts/nonexistent-page]]",
        missing="some missing knowledge",
    )
    ctx = _wiki_context(target, str(vault))
    # Should still return the 'missing' field text (not crash)
    # and gracefully handle the missing file
    # missing is short enough to always be included
    assert isinstance(ctx, str)
    # The missing text should at least contribute something
    assert "missing knowledge" in ctx.lower() or ctx == "" or "Missing:" in ctx


# ---------------------------------------------------------------------------
# 7. _wiki_context: target without _gap -> ""
# ---------------------------------------------------------------------------

def test_wiki_context_no_gap(tmp_path):
    """_wiki_context on a target with no _gap returns ''."""
    vault = _make_vault(tmp_path)
    target = {
        "target_id": "news",
        "lane": "news",
        "origin_ids": ["news"],
        "queries": [],
        "expected_evidence": "recent vendor announcements",
        "fillable_by": ["web"],
        "_gap": None,
        "_dir": None,
    }
    ctx = _wiki_context(target, str(vault))
    assert ctx == ""


# ---------------------------------------------------------------------------
# 8. _deterministic_queries: non-empty per-engine dict
# ---------------------------------------------------------------------------

def test_deterministic_queries_basic():
    """_deterministic_queries returns per-engine dict with non-empty lists."""
    target = _make_gap_target()
    purpose = "Understand disaggregated LLM inference systems for optical interconnects"
    result = _deterministic_queries(target, purpose)

    assert isinstance(result, dict)
    assert len(result) >= 1
    for engine, queries in result.items():
        assert isinstance(queries, list)
        assert len(queries) >= 1
        for q in queries:
            assert isinstance(q, str)
            assert len(q) > 0


# ---------------------------------------------------------------------------
# 9. _deterministic_queries: fillable_by filter respected
# ---------------------------------------------------------------------------

def test_deterministic_queries_fillable_by_filter():
    """_deterministic_queries only includes engines in fillable_by."""
    target = _make_gap_target(fillable_by=["arxiv"])
    purpose = "Understand disaggregated inference"
    result = _deterministic_queries(target, purpose)

    # Only arxiv should be present (fillable_by=["arxiv"])
    for engine in result:
        assert engine == "arxiv", f"Unexpected engine: {engine}"


# ---------------------------------------------------------------------------
# 10. _parse_llm_queries: valid fenced JSON
# ---------------------------------------------------------------------------

def test_parse_llm_queries_valid():
    """_parse_llm_queries correctly parses fenced JSON."""
    text = _CANNED_LLM_RESPONSE
    result = _parse_llm_queries(text)

    assert result is not None
    assert "GAP-01" in result
    entry = result["GAP-01"]
    assert "queries_by_engine" in entry
    assert "rationale" in entry
    qbe = entry["queries_by_engine"]
    assert "arxiv" in qbe
    assert isinstance(qbe["arxiv"], list)


# ---------------------------------------------------------------------------
# 11. _parse_llm_queries: garbage input -> None
# ---------------------------------------------------------------------------

def test_parse_llm_queries_garbage():
    """_parse_llm_queries returns None on garbage input."""
    assert _parse_llm_queries("") is None
    assert _parse_llm_queries("not json at all") is None
    assert _parse_llm_queries("```\nnot json\n```") is None


# ---------------------------------------------------------------------------
# 12. _parse_llm_queries: missing queries_by_engine -> None
# ---------------------------------------------------------------------------

def test_parse_llm_queries_missing_qbe():
    """_parse_llm_queries returns None when queries_by_engine is absent."""
    bad = json.dumps({
        "GAP-01": {
            "rationale": "some rationale",
            # missing "queries_by_engine"
        }
    })
    result = _parse_llm_queries(f"```json\n{bad}\n```")
    assert result is None


# ---------------------------------------------------------------------------
# 13. Multiple targets, all LLM
# ---------------------------------------------------------------------------

def test_multiple_targets_llm(tmp_path):
    """Multiple targets all get LLM results when LLM returns complete JSON."""
    vault = _make_vault(tmp_path)
    t1 = _make_gap_target("GAP-01")
    t2 = _make_gap_target("GAP-02", title="Optical prior art")

    canned = json.dumps({
        "GAP-01": {
            "queries_by_engine": {
                "arxiv": ["disaggregated KV cache serving"],
            },
            "rationale": "rationale A",
        },
        "GAP-02": {
            "queries_by_engine": {
                "arxiv": ["optical interconnect AI accelerators"],
                "web": ["optical AI chip photonic interconnect survey"],
            },
            "rationale": "rationale B",
        },
    })

    with patch.object(web_query, "_invoke_llm", return_value=f"```json\n{canned}\n```"):
        results = reformulate(
            [t1, t2],
            purpose="Understand LLM serving hardware",
            vault_root=str(vault),
            use_llm=True,
        )

    assert len(results) == 2
    methods = {r["target_id"]: r["reformulation"]["method"] for r in results}
    assert methods["GAP-01"] == "llm"
    assert methods["GAP-02"] == "llm"

    r2 = next(r for r in results if r["target_id"] == "GAP-02")
    assert "arxiv" in r2["queries_by_engine"]
    assert "web" in r2["queries_by_engine"]


# ---------------------------------------------------------------------------
# 14. Empty targets list
# ---------------------------------------------------------------------------

def test_empty_targets():
    """reformulate([]) returns []."""
    results = reformulate(
        [],
        purpose="anything",
        vault_root="/tmp",
        use_llm=False,
    )
    assert results == []


# ---------------------------------------------------------------------------
# 15. max_per_engine cap
# ---------------------------------------------------------------------------

def test_max_per_engine_cap(tmp_path):
    """Queries per engine are capped at max_per_engine."""
    vault = _make_vault(tmp_path)
    target = _make_gap_target()

    # LLM returns 5 queries per engine (well over the cap)
    big_llm = json.dumps({
        "GAP-01": {
            "queries_by_engine": {
                "arxiv": ["q1", "q2", "q3", "q4", "q5"],
                "web": ["w1", "w2", "w3", "w4", "w5"],
            },
            "rationale": "test",
        }
    })

    with patch.object(web_query, "_invoke_llm", return_value=f"```json\n{big_llm}\n```"):
        results = reformulate(
            [target],
            purpose="LLM inference",
            vault_root=str(vault),
            use_llm=True,
            max_per_engine=2,
        )

    qbe = results[0]["queries_by_engine"]
    for engine, queries in qbe.items():
        assert len(queries) <= 2, f"Engine {engine} exceeded max_per_engine=2: {queries}"


# ---------------------------------------------------------------------------
# 16. WEB_QUERY_REFORMULATION_PROMPT importable + required placeholders
# ---------------------------------------------------------------------------

def test_prompt_template_importable():
    """WEB_QUERY_REFORMULATION_PROMPT is importable and has required placeholders."""
    from scripts.prompts.pipeline_prompts import WEB_QUERY_REFORMULATION_PROMPT

    assert isinstance(WEB_QUERY_REFORMULATION_PROMPT, str)
    assert len(WEB_QUERY_REFORMULATION_PROMPT) > 100

    # Required format placeholders
    for placeholder in ("{purpose}", "{max_per_engine}", "{engine_rules}",
                        "{target_section}", "{learned_terms_blurb}"):
        assert placeholder in WEB_QUERY_REFORMULATION_PROMPT, (
            f"Missing placeholder {placeholder!r} in WEB_QUERY_REFORMULATION_PROMPT"
        )

    # Should reference key output fields
    assert "queries_by_engine" in WEB_QUERY_REFORMULATION_PROMPT
    assert "rationale" in WEB_QUERY_REFORMULATION_PROMPT


# ---------------------------------------------------------------------------
# 17. No real claude_agent.sh / network call in any test
# ---------------------------------------------------------------------------

def test_no_real_llm_call_in_fallback_mode(tmp_path):
    """Prove use_llm=False triggers zero subprocess calls (belt-and-suspenders)."""
    vault = _make_vault(tmp_path)
    target = _make_gap_target()

    call_count = {"n": 0}
    original_invoke = web_query._invoke_llm

    def guarded_invoke(*args, **kwargs):
        call_count["n"] += 1
        raise AssertionError(
            "REAL LLM CALL ATTEMPTED in test_no_real_llm_call_in_fallback_mode"
        )

    web_query._invoke_llm = guarded_invoke
    try:
        results = reformulate(
            [target],
            purpose="test",
            vault_root=str(vault),
            use_llm=False,
        )
    finally:
        web_query._invoke_llm = original_invoke

    assert call_count["n"] == 0
    assert results[0]["reformulation"]["method"] == "fallback"


# ---------------------------------------------------------------------------
# 18. reformulate preserves non-mutated input (new dicts returned)
# ---------------------------------------------------------------------------

def test_reformulate_does_not_mutate_input(tmp_path):
    """reformulate returns new dicts; original targets are not mutated."""
    vault = _make_vault(tmp_path)
    target = _make_gap_target()
    original_queries = list(target["queries"])

    with patch.object(web_query, "_invoke_llm", return_value=_CANNED_LLM_RESPONSE):
        results = reformulate(
            [target],
            purpose="LLM inference",
            vault_root=str(vault),
            use_llm=True,
        )

    # Original target's queries unchanged
    assert target["queries"] == original_queries
    # Result is a different object
    assert results[0] is not target


# ---------------------------------------------------------------------------
# 19. learned reject keywords surface in prompt (blurb present)
# ---------------------------------------------------------------------------

def test_learned_reject_keywords_in_prompt(tmp_path):
    """When learned has reject keywords, the LLM prompt contains them."""
    vault = _make_vault(tmp_path)
    target = _make_gap_target()

    learned = {
        "reject_patterns": {
            "keywords": ["unrelated", "offtrack"],
            "keyword_counts": {"unrelated": 3, "offtrack": 2},
        }
    }

    captured_prompts = []

    def capture_invoke(prompt: str, agent: str) -> str:
        captured_prompts.append(prompt)
        return _CANNED_LLM_RESPONSE

    with patch.object(web_query, "_invoke_llm", side_effect=capture_invoke):
        reformulate(
            [target],
            purpose="LLM inference",
            vault_root=str(vault),
            learned=learned,
            use_llm=True,
        )

    assert len(captured_prompts) == 1
    prompt = captured_prompts[0]
    # The reject keywords should appear in the prompt somewhere
    assert "unrelated" in prompt or "offtrack" in prompt
