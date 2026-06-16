---
description: Show today's token/cost spend per action and provider, and remaining daily budget. Renders meta/cost_report.md.
category: maintenance
triggers_en: ["cost report", "how much have I spent", "token usage", "budget status"]
---

Report cost/usage from the ledger. Run from the code repo:

```bash
cd /home/jpietrak/second_brain
uv run python agents/cost_tracker.py report
```

This reads `logs/cost_ledger.jsonl` and writes `$VAULT_ROOT/meta/cost_report.md`
(today's paid spend vs the daily cap, rolling 7-day spend, per-provider $, per-action
token/cost breakdown).

Then summarise for the user: today's paid spend, remaining budget, and the biggest
cost drivers. Note that Tier-1 Routines and Tier-2 `claude -p` work draws Agent SDK
credit (recorded with cost $0) — the dollar figures reflect the pay-as-you-go pool
(LiteLLM → Anthropic/Gemini) only.

To check the budget gate (exit 1 if over cap): `uv run python agents/cost_tracker.py check`.
