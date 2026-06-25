#!/usr/bin/env python3
"""Track token usage from interactive Claude Code CLI sessions.

Called by the Stop hook (fires after each turn). Reads the session transcript JSONL,
finds assistant messages not yet processed, aggregates token counts per skill, and
appends rows to logs/cost_ledger.jsonl.

Stop hook stdin JSON:
  { "session_id": "...", "transcript_path": "..." }

Watermark files (one per session) are stored under:
  logs/.interactive_watermarks/<session_id>
Each watermark file contains the UUID of the last processed assistant message.

Rows written to cost_ledger.jsonl use:
  source = "interactive-subscription"
  cost_usd = 0.0  (subscription, no marginal cost)
  estimated_cost_usd = computed from rate table
  action = attributionSkill from transcript (or "interactive" if absent)
  role = "interactive"
  provider = "anthropic"
  model = as reported in the transcript message

Gotchas:
  - The transcript file is written incrementally (live) during the session.
  - CLAUDE_CODE_SESSION_ID env var also carries the session_id (fallback).
  - transcript_path from stdin is preferred over constructing the path from session_id,
    because it handles non-default project directories.
  - We never block the Stop hook: all errors are caught and logged to stderr.
  - Cache tokens (cache_read_input_tokens, cache_creation_input_tokens) are recorded
    separately as they have different billing rates but zero marginal cost on subscription.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime
from pathlib import Path

CODE_PATH = Path(os.environ.get("CODE_PATH") or Path(__file__).resolve().parent.parent)

# Watermark directory: one small text file per session tracking last processed uuid
WATERMARK_DIR = CODE_PATH / "logs" / ".interactive_watermarks"

LEDGER = CODE_PATH / "logs" / "cost_ledger.jsonl"

# Rate table (model substring -> (input_usd_per_mtok, output_usd_per_mtok))
# Used for estimated_cost_usd only; cost_usd is always 0.0 (subscription).
_MTok = 1_000_000
_RATE_TABLE: list[tuple[str, tuple[float, float]]] = [
    ("haiku",   (0.80,   4.00)),
    ("sonnet",  (3.00,  15.00)),
    ("opus",    (15.00, 75.00)),
    ("flash",   (0.075,  0.30)),
    ("gemini",  (1.25,   5.00)),
]


def _estimate_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    m = (model or "").lower()
    for fragment, (in_rate, out_rate) in _RATE_TABLE:
        if fragment in m:
            return round((input_tokens * in_rate + output_tokens * out_rate) / _MTok, 8)
    return 0.0


def _active_vault() -> str:
    """Return the active vault name without importing agents (safe in hook context)."""
    try:
        sys.path.insert(0, str(CODE_PATH))
        from agents import vault_config as vc
        return vc.active_vault()
    except Exception:
        return "unknown"


def _read_watermark(session_id: str) -> str | None:
    """Return the last-processed message UUID for this session, or None."""
    path = WATERMARK_DIR / session_id
    if path.exists():
        content = path.read_text().strip()
        return content if content else None
    return None


def _write_watermark(session_id: str, last_uuid: str) -> None:
    WATERMARK_DIR.mkdir(parents=True, exist_ok=True)
    (WATERMARK_DIR / session_id).write_text(last_uuid + "\n")


def _append_ledger_row(row: dict) -> None:
    LEDGER.parent.mkdir(parents=True, exist_ok=True)
    with open(LEDGER, "a") as f:
        f.write(json.dumps(row) + "\n")


def _process_transcript(session_id: str, transcript_path: str) -> int:
    """Parse transcript, find new assistant messages, write ledger rows.

    Returns the count of rows written.
    """
    tp = Path(transcript_path)
    if not tp.exists():
        print(f"[token-tracker] transcript not found: {transcript_path}", file=sys.stderr)
        return 0

    watermark_uuid = _read_watermark(session_id)
    vault = _active_vault()
    now = datetime.now()
    ts = now.isoformat(timespec="seconds")
    date_str = now.date().isoformat()

    # Collect assistant messages after the watermark
    # Accumulate per-skill token totals so we emit one ledger row per skill per turn.
    # skill -> {"input": int, "output": int, "cache_read": int, "cache_create": int,
    #           "model": str, "last_uuid": str}
    skill_buckets: dict[str, dict] = {}
    last_uuid_overall: str | None = None
    found_watermark = watermark_uuid is None  # if no watermark, process all

    try:
        with open(tp) as f:
            lines = f.readlines()
    except Exception as e:
        print(f"[token-tracker] error reading transcript: {e}", file=sys.stderr)
        return 0

    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue

        # We only care about assistant messages with usage
        msg = obj.get("message", {})
        if msg.get("role") != "assistant":
            continue
        usage = msg.get("usage")
        if not usage:
            continue

        uuid = obj.get("uuid", "")

        # Watermark skip logic: advance past the watermark entry
        if not found_watermark:
            if uuid == watermark_uuid:
                found_watermark = True
            continue

        # This message is new (after watermark)
        skill = obj.get("attributionSkill") or "interactive"
        model = msg.get("model", "")

        bucket = skill_buckets.setdefault(skill, {
            "input": 0, "output": 0,
            "cache_read": 0, "cache_create": 0,
            "model": model,
            "last_uuid": "",
        })
        bucket["input"] += usage.get("input_tokens", 0)
        bucket["output"] += usage.get("output_tokens", 0)
        bucket["cache_read"] += usage.get("cache_read_input_tokens", 0)
        bucket["cache_create"] += usage.get("cache_creation_input_tokens", 0)
        if uuid:
            bucket["last_uuid"] = uuid
            last_uuid_overall = uuid

    if not skill_buckets:
        return 0

    rows_written = 0
    for skill, b in skill_buckets.items():
        in_tok = b["input"]
        out_tok = b["output"]
        model = b["model"]
        est_cost = _estimate_cost(model, in_tok, out_tok)

        row = {
            "ts": ts,
            "date": date_str,
            "vault": vault,
            "action": skill,
            "role": "interactive",
            "provider": "anthropic",
            "model": model,
            "input_tokens": in_tok,
            "output_tokens": out_tok,
            "cache_read_tokens": b["cache_read"],
            "cache_create_tokens": b["cache_create"],
            "cost_usd": 0.0,
            "estimated_cost_usd": est_cost,
            "source": "interactive-subscription",
            "session_id": session_id,
        }
        _append_ledger_row(row)
        rows_written += 1

    if last_uuid_overall:
        _write_watermark(session_id, last_uuid_overall)

    return rows_written


def main() -> int:
    try:
        raw = sys.stdin.read()
        event = json.loads(raw) if raw.strip() else {}
    except Exception as e:
        print(f"[token-tracker] failed to parse Stop hook event: {e}", file=sys.stderr)
        return 0  # never block the stop hook

    session_id = event.get("session_id") or os.environ.get("CLAUDE_CODE_SESSION_ID", "")
    transcript_path = event.get("transcript_path", "")

    if not session_id:
        print("[token-tracker] no session_id available", file=sys.stderr)
        return 0

    if not transcript_path:
        # Fallback: construct path from session_id using the project dir slug.
        # The slug is derived from the repo's absolute path (slashes become dashes).
        repo_slug = str(CODE_PATH.resolve()).replace("/", "-").replace("\\", "-")
        project_dir = Path.home() / ".claude" / "projects" / repo_slug
        transcript_path = str(project_dir / f"{session_id}.jsonl")

    try:
        rows = _process_transcript(session_id, transcript_path)
        if rows:
            print(f"[token-tracker] wrote {rows} ledger row(s) for session {session_id[:8]}",
                  file=sys.stderr)
    except Exception as e:
        print(f"[token-tracker] unexpected error: {e}", file=sys.stderr)

    return 0  # always exit 0 so Stop hook never blocks claude


if __name__ == "__main__":
    raise SystemExit(main())
