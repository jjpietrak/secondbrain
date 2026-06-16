#!/usr/bin/env python3
"""
Cost tracker for the Second Brain.

Lightweight, no-DB cost/usage ledger. One JSON line per model call in
logs/cost_ledger.jsonl:

  {ts, date, action, role, provider, input_tokens, output_tokens, cost_usd, source}

Writers:
  - LiteLLM proxy  -> services/litellm/cost_callback.py calls record(...)
  - claude -p runs -> orchestrator parses `--output-format json` and calls record(...)

The dollar budget (config/budget.yaml: daily_usd_cap) guards the pay-as-you-go pool.
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

CODE_PATH = Path(os.environ.get("CODE_PATH", "/home/jpietrak/second_brain"))
VAULT_PATH = Path(os.environ.get("VAULT_PATH", "/mnt/c/Obsidian/Inference-Disagg"))
LEDGER = CODE_PATH / "logs" / "cost_ledger.jsonl"
BUDGET_FILE = CODE_PATH / "config" / "budget.yaml"
REPORT = VAULT_PATH / "meta" / "cost_report.md"

METERED_PROVIDERS = {"anthropic", "gemini"}  # default; refined by budget.yaml


def _load_budget() -> dict:
    try:
        import yaml
        with open(BUDGET_FILE) as f:
            return yaml.safe_load(f) or {}
    except Exception:
        return {}


def daily_cap() -> float:
    return float(_load_budget().get("daily_usd_cap", 2.0))


def record(action: str, role: str, provider: str,
           input_tokens: int = 0, output_tokens: int = 0,
           cost_usd: float = 0.0, source: str = "pay-as-you-go") -> None:
    LEDGER.parent.mkdir(parents=True, exist_ok=True)
    now = datetime.now()
    row = {
        "ts": now.isoformat(timespec="seconds"),
        "date": now.date().isoformat(),
        "action": action,
        "role": role,
        "provider": provider,
        "input_tokens": int(input_tokens or 0),
        "output_tokens": int(output_tokens or 0),
        "cost_usd": round(float(cost_usd or 0.0), 6),
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


def spent_today(provider: str | None = None) -> float:
    today = date.today().isoformat()
    total = 0.0
    for r in _rows():
        if r.get("date") != today:
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

    paid_today = sum(r["cost_usd"] for r in rows if r.get("date") == today_s)
    by_provider: dict[str, float] = {}
    by_action: dict[str, dict] = {}
    tok_in = tok_out = 0
    for r in rows:
        if r.get("date") != today_s:
            continue
        by_provider[r["provider"]] = by_provider.get(r["provider"], 0.0) + r["cost_usd"]
        a = by_action.setdefault(r["action"], {"calls": 0, "in": 0, "out": 0, "cost": 0.0})
        a["calls"] += 1; a["in"] += r["input_tokens"]; a["out"] += r["output_tokens"]; a["cost"] += r["cost_usd"]
        tok_in += r["input_tokens"]; tok_out += r["output_tokens"]

    rolling = sum(r["cost_usd"] for r in rows
                  if r.get("date", "") >= week_ago.isoformat())
    cap = daily_cap()
    remaining = max(0.0, cap - paid_today)

    L = [f"# Cost report\n", f"_Generated: {datetime.now().isoformat(timespec='seconds')}_\n",
         f"**Today ({today_s})** — paid spend: **${paid_today:.4f}** / cap ${cap:.2f} "
         f"(remaining ${remaining:.4f})",
         f"**Rolling 7-day paid spend:** ${rolling:.4f}",
         f"**Today tokens:** {tok_in:,} in / {tok_out:,} out\n",
         "## By provider (today)"]
    if by_provider:
        for p, c in sorted(by_provider.items(), key=lambda x: -x[1]):
            L.append(f"- {p}: ${c:.4f}")
    else:
        L.append("- (no calls recorded today)")
    L.append("\n## By action (today)")
    if by_action:
        L.append("| Action | Calls | Tokens in | Tokens out | Cost $ |")
        L.append("|--------|-------|-----------|------------|--------|")
        for act, d in sorted(by_action.items(), key=lambda x: -x[1]["cost"]):
            L.append(f"| {act} | {d['calls']} | {d['in']:,} | {d['out']:,} | {d['cost']:.4f} |")
    else:
        L.append("- (no calls recorded today)")

    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text("\n".join(L) + "\n")
    print(f"[cost] today=${paid_today:.4f}/{cap:.2f} | 7d=${rolling:.4f} | report -> {REPORT}")


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
    sub.add_parser("report")
    sub.add_parser("check")
    args = ap.parse_args()

    if args.cmd == "record":
        record(args.action, args.role, args.provider, args.inp, args.out, args.cost, args.source)
    elif args.cmd == "report":
        report()
    elif args.cmd == "check":
        sys.exit(check_budget())


if __name__ == "__main__":
    main()
