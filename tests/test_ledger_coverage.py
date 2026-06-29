#!/usr/bin/env python3
"""test_ledger_coverage.py - FU4: every Claude invocation path records to the ledger
with non-null tokens and an estimated_cost_usd even when cost_usd is 0 (credit-pool).

Covers:
  1. estimate_cost() helper in cost_tracker -- correctness for known models + unknown
  2. record() auto-populates estimated_cost_usd when cost_usd=0 (the credit-pool case)
  3. record() with explicit model="claude-sonnet-..." yields a non-zero estimate
  4. contextual-prefix.py tier-1 (Anthropic API): mocked urlopen; asserts a ledger row
     is written with action=contextual-prefix and estimated_cost_usd >= 0
  5. contextual-prefix.py tier-2 (claude CLI subprocess): mocked subprocess.run;
     asserts a ledger row is written with source=agent-sdk-credit

Hermetic: all tests run against a sandbox CODE_PATH (tmp dir) with agents/ + config/
symlinked in; no network, no real claude, no LLM calls, no vault writes.

Usage:
  python3 tests/test_ledger_coverage.py
  (or via tests/run_phase0.sh / run_phase1.sh)
"""
from __future__ import annotations

import importlib.util
import json
import os
import sys
import tempfile
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent

# ---------------------------------------------------------------------------
# Test framework (same pattern as other tests in this repo)
# ---------------------------------------------------------------------------

PASS = 0
FAIL = 0


def assert_eq(label: str, expected, actual) -> None:
    global PASS, FAIL
    if expected == actual:
        print(f"OK   {label}")
        PASS += 1
    else:
        print(f"FAIL {label}: expected {expected!r}, got {actual!r}")
        FAIL += 1


def assert_true(label: str, cond: bool) -> None:
    global PASS, FAIL
    if cond:
        print(f"OK   {label}")
        PASS += 1
    else:
        print(f"FAIL {label}: condition was False")
        FAIL += 1


def assert_ge(label: str, value, minimum) -> None:
    global PASS, FAIL
    if value >= minimum:
        print(f"OK   {label} ({value} >= {minimum})")
        PASS += 1
    else:
        print(f"FAIL {label}: {value} < {minimum}")
        FAIL += 1


# ---------------------------------------------------------------------------
# Sandbox setup: an isolated CODE_PATH so LEDGER writes don't pollute the live ledger
# ---------------------------------------------------------------------------

def make_sandbox() -> Path:
    """Create a tmp dir with agents/ + config/ symlinked so imports resolve."""
    d = Path(tempfile.mkdtemp(prefix="ledger-cov-"))
    (d / "logs").mkdir()
    (d / "agents").symlink_to(ROOT / "agents")
    (d / "config").symlink_to(ROOT / "config")
    return d


def load_cost_tracker(sandbox: Path):
    """Import cost_tracker with CODE_PATH pointing at the sandbox."""
    old_code = os.environ.get("CODE_PATH")
    old_vault = os.environ.get("VAULT")
    os.environ["CODE_PATH"] = str(sandbox)
    os.environ["VAULT"] = os.environ.get("VAULT", "example")
    # Force reimport so CODE_PATH is picked up fresh.
    import importlib
    if "agents.cost_tracker" in sys.modules:
        del sys.modules["agents.cost_tracker"]
    if "agents.vault_config" in sys.modules:
        del sys.modules["agents.vault_config"]
    sys.path.insert(0, str(sandbox))
    try:
        import agents.cost_tracker as ct
        # Patch the LEDGER path to land inside the sandbox.
        ct.LEDGER = sandbox / "logs" / "cost_ledger.jsonl"
        return ct
    finally:
        # Restore env but keep sys.path (needed for later imports).
        if old_code is None:
            os.environ.pop("CODE_PATH", None)
        else:
            os.environ["CODE_PATH"] = old_code
        if old_vault is None:
            os.environ.pop("VAULT", None)
        else:
            os.environ["VAULT"] = old_vault


def load_contextual_prefix(sandbox: Path):
    """Import contextual-prefix.py from the repo with CODE_PATH pointing at sandbox."""
    helper = ROOT / "scripts" / "contextual-prefix.py"
    os.environ["CODE_PATH"] = str(sandbox)
    os.environ["VAULT"] = os.environ.get("VAULT", "example")
    # Clean cached modules so the script picks up the fresh CODE_PATH.
    for key in list(sys.modules):
        if "cost_tracker" in key or "vault_config" in key:
            del sys.modules[key]
    spec = importlib.util.spec_from_file_location("contextual_prefix", helper)
    cp = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cp)
    # Patch the cost_tracker LEDGER inside the loaded module's import closure.
    return cp


def read_last_ledger_row(sandbox: Path) -> dict | None:
    ledger = sandbox / "logs" / "cost_ledger.jsonl"
    if not ledger.exists():
        return None
    lines = [l.strip() for l in ledger.read_text().splitlines() if l.strip()]
    if not lines:
        return None
    return json.loads(lines[-1])


def all_ledger_rows(sandbox: Path) -> list[dict]:
    ledger = sandbox / "logs" / "cost_ledger.jsonl"
    if not ledger.exists():
        return []
    return [json.loads(l) for l in ledger.read_text().splitlines() if l.strip()]


# ===========================================================================
# Test suite
# ===========================================================================

def test_estimate_cost_haiku():
    """estimate_cost returns a positive number for Haiku model."""
    sandbox = make_sandbox()
    ct = load_cost_tracker(sandbox)
    est = ct.estimate_cost("claude-haiku-4-5-20251001", 1000, 200)
    assert_true("estimate_cost haiku > 0", est > 0.0)
    # Haiku: 0.80/1M in, 4.00/1M out -> (1000*0.80 + 200*4.00) / 1e6 = 0.0016
    assert_eq("estimate_cost haiku exact", round((1000 * 0.80 + 200 * 4.00) / 1_000_000, 8), est)


def test_estimate_cost_sonnet():
    """estimate_cost returns a positive number for Sonnet model."""
    sandbox = make_sandbox()
    ct = load_cost_tracker(sandbox)
    est = ct.estimate_cost("claude-sonnet-4-6", 2000, 500)
    assert_true("estimate_cost sonnet > 0", est > 0.0)
    expected = round((2000 * 3.00 + 500 * 15.00) / 1_000_000, 8)
    assert_eq("estimate_cost sonnet exact", expected, est)


def test_estimate_cost_opus():
    """estimate_cost returns a positive number for Opus model."""
    sandbox = make_sandbox()
    ct = load_cost_tracker(sandbox)
    est = ct.estimate_cost("claude-opus-4-8", 500, 100)
    assert_true("estimate_cost opus > 0", est > 0.0)


def test_estimate_cost_unknown_model():
    """estimate_cost returns 0.0 for unknown/local models."""
    sandbox = make_sandbox()
    ct = load_cost_tracker(sandbox)
    est = ct.estimate_cost("some-unknown-llm", 9999, 9999)
    assert_eq("estimate_cost unknown model -> 0.0", 0.0, est)


def test_estimate_cost_zero_tokens():
    """estimate_cost returns 0.0 when tokens are 0."""
    sandbox = make_sandbox()
    ct = load_cost_tracker(sandbox)
    est = ct.estimate_cost("claude-sonnet-4-6", 0, 0)
    assert_eq("estimate_cost zero tokens -> 0.0", 0.0, est)


def test_record_auto_estimated_cost_when_cost_zero():
    """record() populates estimated_cost_usd even when cost_usd=0 (credit-pool case).

    This is the core FU4 assertion: Agent SDK credit-pool rows must carry an estimate.
    """
    sandbox = make_sandbox()
    ct = load_cost_tracker(sandbox)
    ct.record(
        action="test-agent",
        role="",
        provider="anthropic",
        input_tokens=1000,
        output_tokens=200,
        cost_usd=0.0,
        source="agent-sdk-credit",
        model="claude-haiku-4-5-20251001",
    )
    row = read_last_ledger_row(sandbox)
    assert_true("ledger row written for credit-pool call", row is not None)
    if row:
        assert_eq("row source is agent-sdk-credit", "agent-sdk-credit", row.get("source"))
        assert_eq("row cost_usd is 0 (not metered)", 0.0, row.get("cost_usd"))
        assert_true("row estimated_cost_usd is present", "estimated_cost_usd" in row)
        assert_true("row estimated_cost_usd > 0 (Haiku has known rate)",
                    row.get("estimated_cost_usd", 0.0) > 0.0)
        assert_eq("row model is recorded", "claude-haiku-4-5-20251001", row.get("model"))


def test_record_preserves_paid_cost_and_adds_estimate():
    """record() preserves a non-zero cost_usd and also populates estimated_cost_usd."""
    sandbox = make_sandbox()
    ct = load_cost_tracker(sandbox)
    ct.record(
        action="test-paid",
        role="",
        provider="anthropic",
        input_tokens=500,
        output_tokens=100,
        cost_usd=0.0045,
        source="pay-as-you-go",
        model="claude-sonnet-4-6",
    )
    row = read_last_ledger_row(sandbox)
    assert_true("ledger row written for paid call", row is not None)
    if row:
        assert_eq("row cost_usd preserved", 0.0045, row.get("cost_usd"))
        assert_true("row estimated_cost_usd present", "estimated_cost_usd" in row)
        assert_ge("row estimated_cost_usd >= 0", row.get("estimated_cost_usd", -1), 0.0)


def test_contextual_prefix_tier1_records_ledger():
    """Tier-1 (Anthropic API direct call) writes a row to the cost ledger.

    Mock urlopen to return a realistic API response with usage fields.
    Assert a row lands in the ledger with action=contextual-prefix.
    """
    sandbox = make_sandbox()
    os.environ["CODE_PATH"] = str(sandbox)
    os.environ["VAULT"] = os.environ.get("VAULT", "example")

    # Patch cost_tracker.LEDGER before loading contextual-prefix so any record()
    # calls inside the loaded module write to the sandbox ledger.
    for key in list(sys.modules):
        if "cost_tracker" in key or "vault_config" in key:
            del sys.modules[key]

    cp = load_contextual_prefix(sandbox)

    # Patch cost_tracker.LEDGER in the module that got imported by _record_prefix_cost.
    import agents.cost_tracker as ct_live
    ct_live.LEDGER = sandbox / "logs" / "cost_ledger.jsonl"

    class _Resp:
        def __init__(self, d):
            self._d = json.dumps(d).encode()
        def read(self): return self._d
        def __enter__(self): return self
        def __exit__(self, *a): return False

    mock_response = {
        "content": [{"type": "text", "text": "This chunk discusses inference disaggregation."}],
        "usage": {
            "input_tokens": 800,
            "output_tokens": 30,
            "cache_creation_input_tokens": 50,
            "cache_read_input_tokens": 0,
        },
    }

    with mock.patch.object(cp.urllib.request, "urlopen",
                           lambda req, timeout=None: _Resp(mock_response)):
        result = cp.anthropic_api_prefix("FAKE_KEY", "Test Page",
                                         "x" * cp.HAIKU_CACHE_MIN_CHARS, "some chunk text")

    assert_true("tier-1 returns a prefix string", result is not None and len(result) > 0)

    rows = all_ledger_rows(sandbox)
    assert_true("tier-1 wrote at least one ledger row", len(rows) >= 1)
    prefix_rows = [r for r in rows if r.get("action") == "contextual-prefix"]
    assert_true("tier-1 row has action=contextual-prefix", len(prefix_rows) >= 1)
    if prefix_rows:
        r = prefix_rows[-1]
        assert_eq("tier-1 row source=pay-as-you-go", "pay-as-you-go", r.get("source"))
        assert_true("tier-1 row has estimated_cost_usd", "estimated_cost_usd" in r)
        assert_ge("tier-1 row estimated_cost_usd >= 0", r.get("estimated_cost_usd", -1), 0.0)


def test_contextual_prefix_tier2_records_ledger():
    """Tier-2 (claude CLI subprocess) writes a row to the cost ledger.

    Mock subprocess.run to return a successful result. Assert a row lands in the
    ledger with source=agent-sdk-credit.
    """
    sandbox = make_sandbox()
    os.environ["CODE_PATH"] = str(sandbox)
    os.environ["VAULT"] = os.environ.get("VAULT", "example")

    for key in list(sys.modules):
        if "cost_tracker" in key or "vault_config" in key:
            del sys.modules[key]

    cp = load_contextual_prefix(sandbox)

    import agents.cost_tracker as ct_live
    ct_live.LEDGER = sandbox / "logs" / "cost_ledger.jsonl"

    class _FakeResult:
        returncode = 0
        stdout = "This chunk covers prefill-decode separation.\n"
        stderr = ""

    with mock.patch.object(cp.subprocess, "run", return_value=_FakeResult()):
        result = cp.claude_cli_prefix("Test Page", "page body here", "some chunk text")

    assert_true("tier-2 returns a prefix string", result is not None and len(result) > 0)

    rows = all_ledger_rows(sandbox)
    assert_true("tier-2 wrote at least one ledger row", len(rows) >= 1)
    prefix_rows = [r for r in rows if r.get("action") == "contextual-prefix"]
    assert_true("tier-2 row has action=contextual-prefix", len(prefix_rows) >= 1)
    if prefix_rows:
        r = prefix_rows[-1]
        assert_eq("tier-2 row source=agent-sdk-credit", "agent-sdk-credit", r.get("source"))
        assert_true("tier-2 row has estimated_cost_usd", "estimated_cost_usd" in r)


def test_record_without_model_falls_back_to_provider():
    """record() with no model= uses provider as the model fragment for estimation.
    ollama provider -> 0.0 estimate; anthropic provider without model -> 0.0 (no fragment match).
    """
    sandbox = make_sandbox()
    ct = load_cost_tracker(sandbox)
    ct.record(
        action="test-ollama",
        role="bulk",
        provider="ollama",
        input_tokens=10000,
        output_tokens=2000,
        cost_usd=0.0,
        source="local-free",
        # no model= argument
    )
    row = read_last_ledger_row(sandbox)
    assert_true("ollama row written", row is not None)
    if row:
        assert_true("ollama estimated_cost_usd is 0 (free/local)", row.get("estimated_cost_usd", -1) == 0.0)


def main():
    print("=== test_ledger_coverage.py ===")
    print()
    test_estimate_cost_haiku()
    test_estimate_cost_sonnet()
    test_estimate_cost_opus()
    test_estimate_cost_unknown_model()
    test_estimate_cost_zero_tokens()
    print()
    test_record_auto_estimated_cost_when_cost_zero()
    test_record_preserves_paid_cost_and_adds_estimate()
    test_record_without_model_falls_back_to_provider()
    print()
    test_contextual_prefix_tier1_records_ledger()
    test_contextual_prefix_tier2_records_ledger()
    print()
    print(f"Pass: {PASS}  Fail: {FAIL}")
    if FAIL > 0:
        sys.exit(1)
    print("All ledger coverage tests passed.")


if __name__ == "__main__":
    main()
