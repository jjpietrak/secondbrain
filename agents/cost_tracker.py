#!/usr/bin/env python3
"""
Cost tracker for the Second Brain.

Lightweight, no-DB cost/usage ledger. One JSON line per model call in
logs/cost_ledger.jsonl:

  {ts, date, action, role, provider, input_tokens, output_tokens, cost_usd, source}

Writers:
  - claude -p runs -> orchestrator parses `--output-format json` and calls record(...)
  - direct provider scripts -> scripts call record() after metered API calls
    (e.g. scripts/wiki_cite_check.py for Gemini Flash,
     scripts/contextual-prefix.py for Anthropic API tier-1)

The dollar budget (per-vault config/vaults/<vault>/budget.yaml: daily_usd_cap) guards the pay-as-you-go pool.
Agent-SDK-credit calls are recorded with cost_usd=0 and source="agent-sdk-credit".

CLI:
  python agents/cost_tracker.py record --action ingest --role bulk --provider ollama \
      --in 1200 --out 300 --cost 0 --source agent-sdk-credit
  python agents/cost_tracker.py report        # writes meta/cost_report.md, prints summary
  python agents/cost_tracker.py check          # exit 1 if today's paid spend >= daily_usd_cap
"""
from __future__ import annotations
import os, sys, json, argparse
from pathlib import Path
from datetime import datetime, date, timedelta

CODE_PATH = Path(os.environ.get("CODE_PATH") or Path(__file__).resolve().parent.parent)
try:
    from agents import vault_config as vc
except ImportError:  # run as a script: agents/ is already on sys.path
    import vault_config as vc

VAULT_NAME = vc.active_vault()
VAULT_PATH = vc.vault_path()
LEDGER = CODE_PATH / "logs" / "cost_ledger.jsonl"   # shared file; rows tagged by vault
REPORT = VAULT_PATH / "meta" / "cost_report.md"      # per-vault report

METERED_PROVIDERS = {"anthropic", "gemini"}  # default; refined by budget.yaml

# ---------------------------------------------------------------------------
# Cost estimation (FU4): fills in estimated_cost_usd on rows where total_cost_usd
# is 0 / absent (e.g. Agent SDK credit-pool calls where billing is not metered).
# Rates are conservative public figures as of mid-2026 (USD per 1M tokens).
# The field is informational only; budget cap checks always use cost_usd (paid $).
# ---------------------------------------------------------------------------
_MTok = 1_000_000

# (model_substring -> (input_usd_per_mtok, output_usd_per_mtok))
_RATE_TABLE: list[tuple[str, tuple[float, float]]] = [
    # Anthropic - Haiku
    ("haiku",            (0.80,   4.00)),
    # Anthropic - Sonnet
    ("sonnet",           (3.00,  15.00)),
    # Anthropic - Opus
    ("opus",             (15.00, 75.00)),
    # Gemini Flash
    ("flash",            (0.075,  0.30)),
    # Gemini Pro / Ultra
    ("gemini",           (1.25,   5.00)),
    # Ollama / local: $0
    ("ollama",           (0.0,    0.0)),
]


def estimate_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    """Return an estimated cost in USD for a model call given token counts.

    Looks up the model name (case-insensitive substring match) against a static
    rate table and returns (input_tokens * input_rate + output_tokens * output_rate).
    Returns 0.0 for unknown models (treats them as free/local).

    This is used to populate ``estimated_cost_usd`` on rows where ``cost_usd`` is
    0.0 because the call went through the Agent SDK credit pool or a free route.
    The estimate is informational and does NOT affect budget-cap enforcement.
    """
    m = (model or "").lower()
    in_rate, out_rate = 0.0, 0.0
    for fragment, rates in _RATE_TABLE:
        if fragment in m:
            in_rate, out_rate = rates
            break
    return round(
        (int(input_tokens or 0) * in_rate + int(output_tokens or 0) * out_rate) / _MTok,
        8,
    )


def _load_budget() -> dict:
    try:
        return vc.load_budget(VAULT_NAME)
    except Exception:
        return {}


def daily_cap() -> float:
    return float(_load_budget().get("daily_usd_cap", 2.0))


def record(action: str, role: str, provider: str,
           input_tokens: int = 0, output_tokens: int = 0,
           cost_usd: float = 0.0, source: str = "pay-as-you-go",
           model: str = "", estimated_cost_usd: float | None = None) -> None:
    """Append one row to the JSONL ledger.

    If ``estimated_cost_usd`` is not supplied explicitly, it is computed via
    ``estimate_cost(model, input_tokens, output_tokens)`` so every row carries an
    informational cost figure even when ``cost_usd`` is 0 (credit-pool / free routes).
    """
    LEDGER.parent.mkdir(parents=True, exist_ok=True)
    now = datetime.now()
    in_tok = int(input_tokens or 0)
    out_tok = int(output_tokens or 0)
    if estimated_cost_usd is None:
        estimated_cost_usd = estimate_cost(model or provider, in_tok, out_tok)
    row = {
        "ts": now.isoformat(timespec="seconds"),
        "date": now.date().isoformat(),
        "vault": VAULT_NAME,
        "action": action,
        "role": role,
        "provider": provider,
        "model": model,
        "input_tokens": in_tok,
        "output_tokens": out_tok,
        "cost_usd": round(float(cost_usd or 0.0), 6),
        "estimated_cost_usd": round(float(estimated_cost_usd), 8),
        "source": source,
    }
    with open(LEDGER, "a") as f:
        f.write(json.dumps(row) + "\n")


def _rows() -> list[dict]:
    if not LEDGER.exists():
        return []
    out = []
    for line in LEDGER.read_text(errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def _in_active_vault(r: dict) -> bool:
    """Row belongs to the active vault. Legacy untagged rows count toward it."""
    v = r.get("vault")
    return v is None or v == VAULT_NAME


def spent_today(provider: str | None = None) -> float:
    today = date.today().isoformat()
    total = 0.0
    for r in _rows():
        if r.get("date") != today or not _in_active_vault(r):
            continue
        if provider and r.get("provider") != provider:
            continue
        total += float(r.get("cost_usd", 0.0))
    return round(total, 6)


def check_budget() -> int:
    cap = daily_cap()
    spent = spent_today()
    remaining = max(0.0, cap - spent)
    over = spent >= cap
    print(f"[budget] today paid spend=${spent:.4f} / cap=${cap:.2f} | remaining=${remaining:.4f}"
          + (" | OVER CAP" if over else ""))
    return 1 if over else 0


def report() -> None:
    rows = _rows()
    today = date.today()
    today_s = today.isoformat()
    week_ago = today - timedelta(days=7)

    rows = [r for r in rows if _in_active_vault(r)]   # per-vault encapsulation
    paid_today = sum(r["cost_usd"] for r in rows if r.get("date") == today_s)
    est_today = sum(r.get("estimated_cost_usd", 0.0) for r in rows if r.get("date") == today_s)
    by_provider: dict[str, float] = {}
    by_source: dict[str, dict] = {}
    by_action: dict[str, dict] = {}
    tok_in = tok_out = 0
    for r in rows:
        if r.get("date") != today_s:
            continue
        by_provider[r["provider"]] = by_provider.get(r["provider"], 0.0) + r["cost_usd"]
        src = r.get("source", "unknown")
        s = by_source.setdefault(src, {"in": 0, "out": 0, "cost": 0.0, "est": 0.0})
        s["in"] += r["input_tokens"]
        s["out"] += r["output_tokens"]
        s["cost"] += r["cost_usd"]
        s["est"] += r.get("estimated_cost_usd", 0.0)
        a = by_action.setdefault(r["action"], {"calls": 0, "in": 0, "out": 0, "cost": 0.0, "est": 0.0, "sources": set()})
        a["calls"] += 1
        a["in"] += r["input_tokens"]
        a["out"] += r["output_tokens"]
        a["cost"] += r["cost_usd"]
        a["est"] += r.get("estimated_cost_usd", 0.0)
        a["sources"].add(src)
        tok_in += r["input_tokens"]
        tok_out += r["output_tokens"]

    rolling = sum(r["cost_usd"] for r in rows
                  if r.get("date", "") >= week_ago.isoformat())
    rolling_est = sum(r.get("estimated_cost_usd", 0.0) for r in rows
                      if r.get("date", "") >= week_ago.isoformat())
    cap = daily_cap()
    remaining = max(0.0, cap - paid_today)

    L = [f"# Cost report — {VAULT_NAME}\n", f"_Generated: {datetime.now().isoformat(timespec='seconds')}_\n",
         f"**Today ({today_s})** — paid spend: **${paid_today:.4f}** / cap ${cap:.2f} "
         f"(remaining ${remaining:.4f}) | est. equivalent: **${est_today:.4f}**",
         f"**Rolling 7-day paid spend:** ${rolling:.4f} | est. equivalent: ${rolling_est:.4f}",
         f"**Today tokens:** {tok_in:,} in / {tok_out:,} out\n",
         "## By source (today)"]
    if by_source:
        L.append("| Source | Tokens in | Tokens out | Paid $ | Est. $ |")
        L.append("|--------|-----------|------------|--------|--------|")
        for src, d in sorted(by_source.items(), key=lambda x: -(x[1]["cost"] + x[1]["est"])):
            L.append(f"| {src} | {d['in']:,} | {d['out']:,} | {d['cost']:.4f} | {d['est']:.4f} |")
    else:
        L.append("- (no calls recorded today)")
    L.append("\n## By provider (today)")
    if by_provider:
        for p, c in sorted(by_provider.items(), key=lambda x: -x[1]):
            L.append(f"- {p}: ${c:.4f}")
    else:
        L.append("- (no calls recorded today)")
    L.append("\n## By action (today)")
    if by_action:
        L.append("| Action | Source | Calls | Tokens in | Tokens out | Paid $ | Est. $ |")
        L.append("|--------|--------|-------|-----------|------------|--------|--------|")
        for act, d in sorted(by_action.items(), key=lambda x: -(x[1]["cost"] + x[1]["est"])):
            srcs = ", ".join(sorted(d["sources"]))
            L.append(f"| {act} | {srcs} | {d['calls']} | {d['in']:,} | {d['out']:,} | {d['cost']:.4f} | {d['est']:.4f} |")
    else:
        L.append("- (no calls recorded today)")

    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text("\n".join(L) + "\n")
    print(f"[cost] today=${paid_today:.4f}/{cap:.2f} (est ${est_today:.4f}) | 7d=${rolling:.4f} (est ${rolling_est:.4f}) | report -> {REPORT}")


def main() -> None:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("record")
    r.add_argument("--action", required=True)
    r.add_argument("--role", default="")
    r.add_argument("--provider", required=True)
    r.add_argument("--in", dest="inp", type=int, default=0)
    r.add_argument("--out", dest="out", type=int, default=0)
    r.add_argument("--cost", type=float, default=0.0)
    r.add_argument("--source", default="pay-as-you-go")
    r.add_argument("--model", default="", help="Model name for cost estimation")
    sub.add_parser("report")
    sub.add_parser("check")
    e = sub.add_parser("estimate-cost", help="Print estimated cost for given model+tokens")
    e.add_argument("--model", required=True)
    e.add_argument("--in", dest="inp", type=int, default=0)
    e.add_argument("--out", dest="out", type=int, default=0)
    args = ap.parse_args()

    if args.cmd == "record":
        record(args.action, args.role, args.provider, args.inp, args.out, args.cost, args.source,
               model=args.model)
    elif args.cmd == "estimate-cost":
        print(estimate_cost(args.model, args.inp, args.out))
    elif args.cmd == "report":
        report()
    elif args.cmd == "check":
        sys.exit(check_budget())


if __name__ == "__main__":
    main()
