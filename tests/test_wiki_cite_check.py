#!/usr/bin/env python3
"""Hermetic tests for scripts/wiki_cite_check.py.

Mocks the Gemini Flash `validation` judge call entirely (via the injectable
judge_fn) and uses a fixture source string. No network, no API key, no proxy.
Asserts supported -> keep and unsupported/unclear -> gap routing, plus the
unreachable-judge degradation path and the judge-reply parser.

Run: .venv/bin/python tests/test_wiki_cite_check.py   (exit 0 = all passed)
"""

import importlib.util
import json
import sys
from pathlib import Path

CODE = Path(__file__).resolve().parent.parent
SCRIPT = CODE / "scripts" / "wiki_cite_check.py"

spec = importlib.util.spec_from_file_location("wiki_cite_check", SCRIPT)
wcc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(wcc)

PASSED = 0
FAILED = 0


def check(name, cond):
    global PASSED, FAILED
    if cond:
        PASSED += 1
        print(f"  ok   - {name}")
    else:
        FAILED += 1
        print(f"  FAIL - {name}")


# --- Fixture source -----------------------------------------------------------
FIXTURE_SOURCE = """
Co-packaged optics (CPO) integrates the optical engine into the same package as
the switch ASIC. By moving the optics next to the compute die, CPO cuts the
electrical reach to near zero, which lowers SerDes power and raises usable
bandwidth density. The 2024 measurements reported a 30 percent reduction in
interconnect energy per bit versus pluggable transceivers.

Separately, the cafeteria menu on Tuesdays features a vegetarian lasagna.
""".strip()


# --- Mock judges (stand in for the Gemini Flash validation route) -------------
def judge_supported(claim, excerpt):
    # The real route would return this for a claim the excerpt supports.
    assert excerpt, "excerpt must be non-empty when handed to the judge"
    return {"verdict": "supported", "reason": "excerpt states the 30 percent figure"}


def judge_unsupported(claim, excerpt):
    return {"verdict": "unsupported", "reason": "excerpt does not mention this"}


def judge_unclear(claim, excerpt):
    return {"verdict": "unclear", "reason": "on topic but does not settle the claim"}


# --- Tests --------------------------------------------------------------------
def test_supported_routes_keep():
    claim = "Co-packaged optics reduced interconnect energy per bit by 30 percent."
    res = wcc.check_claim(claim, FIXTURE_SOURCE, judge_fn=judge_supported)
    check("supported -> verdict supported", res["verdict"] == "supported")
    check("supported -> route keep", res["route"] == "keep")
    check("supported -> confidence high", res["confidence"] == "high")
    check("supported -> judge role recorded", res["judge_model_role"] == "validation")
    check("supported -> excerpt is the CPO paragraph, not the lasagna",
          "energy per bit" in res["excerpt"] and "lasagna" not in res["excerpt"])


def test_unsupported_routes_gap():
    claim = "Co-packaged optics tripled the per-port latency."
    res = wcc.check_claim(claim, FIXTURE_SOURCE, judge_fn=judge_unsupported)
    check("unsupported -> verdict unsupported", res["verdict"] == "unsupported")
    check("unsupported -> route gap", res["route"] == "gap")
    check("unsupported -> confidence high", res["confidence"] == "high")


def test_unclear_routes_gap():
    claim = "Co-packaged optics is the cheapest option for hyperscalers."
    res = wcc.check_claim(claim, FIXTURE_SOURCE, judge_fn=judge_unclear)
    check("unclear -> verdict unclear", res["verdict"] == "unclear")
    check("unclear -> route gap (flag, do not silently pass)", res["route"] == "gap")
    check("unclear -> confidence medium", res["confidence"] == "medium")


def test_excerpt_selection_anchors_on_claim():
    claim = "vegetarian lasagna on Tuesdays"
    exc = wcc.best_excerpt(claim, FIXTURE_SOURCE)
    check("excerpt anchors on the matching sentence", "lasagna" in exc)


def test_unreachable_judge_degrades():
    # judge_fn that raises simulates the real route being down -> check_claim only
    # degrades for the real (judge_fn=None) path, so we exercise the heuristic +
    # route directly via the documented contract.
    claim = "Co-packaged optics cuts the electrical reach to near zero."
    # Heuristic should find strong overlap -> supported, but with judge unreachable
    # the route must be 'unreachable' and confidence 'low' (never a clean 'keep').
    heur = wcc._heuristic_verdict(claim, wcc.best_excerpt(claim, FIXTURE_SOURCE))
    route = wcc._route_for(heur["verdict"], judge_reachable=False)
    conf = wcc._confidence_for(heur["verdict"], judge_reachable=False)
    check("unreachable -> route unreachable", route == "unreachable")
    check("unreachable -> confidence low", conf == "low")


def test_parse_judge_content_variants():
    # bare JSON
    r = wcc._parse_judge_content('{"verdict": "supported", "reason": "ok"}')
    check("parse bare json", r["verdict"] == "supported")
    # fenced JSON with prose around it
    r = wcc._parse_judge_content('Sure!\n```json\n{"verdict":"unsupported","reason":"no"}\n```')
    check("parse fenced json", r["verdict"] == "unsupported")
    # keyword-only fallback
    r = wcc._parse_judge_content("This claim is clearly unsupported by the text.")
    check("parse keyword fallback", r["verdict"] == "unsupported")
    # garbage -> unclear (never crash)
    r = wcc._parse_judge_content("???")
    check("parse garbage -> unclear", r["verdict"] == "unclear")


def test_main_json_output():
    # Exercise the CLI path with --source-text and a monkeypatched judge.
    orig = wcc.call_validation_judge
    wcc.call_validation_judge = lambda claim, excerpt, timeout=60: {"verdict": "supported", "reason": "m"}
    try:
        import io, contextlib
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = wcc.main([
                "--claim", "energy per bit dropped 30 percent",
                "--source-text", FIXTURE_SOURCE,
                "--page", "wiki/concepts/CPO.md",
            ])
        out = json.loads(buf.getvalue())
        check("main returns 0", rc == 0)
        check("main echoes page", out.get("page") == "wiki/concepts/CPO.md")
        check("main route keep", out["route"] == "keep")
    finally:
        wcc.call_validation_judge = orig


def main():
    print("test_wiki_cite_check:")
    test_supported_routes_keep()
    test_unsupported_routes_gap()
    test_unclear_routes_gap()
    test_excerpt_selection_anchors_on_claim()
    test_unreachable_judge_degrades()
    test_parse_judge_content_variants()
    test_main_json_output()
    print(f"\n{PASSED} passed, {FAILED} failed")
    if FAILED:
        print("FAILED")
        return 1
    print("All wiki-cite-check tests passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
