#!/usr/bin/env bash
# Hermetic ($0) test for ingest_index v2 -> v3.
# Uses the existing VAULT_PATH override (vault_config.vault_path honors $VAULT_PATH when
# neither a --vault arg nor $VAULT is set), so no live vault is touched. A temp vault dir
# under mktemp holds meta/ingest_index.json + a raw/ tree. No network, no paid API.
set -euo pipefail

cd "$(dirname "$0")/.."
PY=.venv/bin/python
[ -x "$PY" ] || PY=python3

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

# Isolate: clear any inherited vault selection so VAULT_PATH wins.
unset VAULT VAULT_PATH 2>/dev/null || true
export VAULT_PATH="$TMP"

mkdir -p "$TMP/meta" "$TMP/raw/papers"

pass=0
fail=0
check() {  # check <label> <expected-substring> -- runs the rest as a command
  local label="$1" expect="$2"; shift 2
  local out
  out="$("$@" 2>&1)" || true
  if printf '%s' "$out" | grep -qF -- "$expect"; then
    echo "  PASS: $label"
    pass=$((pass + 1))
  else
    echo "  FAIL: $label"
    echo "    expected substring: $expect"
    echo "    got: $out"
    fail=$((fail + 1))
  fi
}
ii() { "$PY" -m agents.ingest_index "$@"; }

# --- Seed a v2 store: one ingested row (on disk) + one deleted row (no file). ----------
printf '# Real paper\n' > "$TMP/raw/papers/real.md"
cat > "$TMP/meta/ingest_index.json" <<'JSON'
{
  "version": 2,
  "vault": "TestVault",
  "sources": {
    "arxiv:2401.00001": {
      "id": "arxiv:2401.00001", "id_type": "arxiv", "status": "ingested",
      "title": "Old ingested paper", "filename": "raw/papers/real.md", "url": "",
      "source_type": "papers", "content_hash": "sha256:deadbeef",
      "first_seen": "2026-01-01T00:00:00+00:00", "ingested_at": "2026-01-01T00:00:00+00:00",
      "source_page": "wiki/sources/old.md"
    },
    "url:https://example.com/gone": {
      "id": "url:https://example.com/gone", "id_type": "url", "status": "deleted",
      "title": "Gone source", "filename": "raw/papers/gone.md", "url": "https://example.com/gone",
      "source_type": "papers", "content_hash": "", "first_seen": "2026-01-01T00:00:00+00:00",
      "ingested_at": null, "source_page": "", "deleted_at": "2026-02-01T00:00:00+00:00"
    }
  }
}
JSON

echo "== v2 store loads + migrates with no row loss =="
# `list` triggers _load -> _migrate; both ids must survive and gain v3 fields.
check "v2 migrate keeps arxiv row"      'arxiv:2401.00001'        ii list
check "v2 migrate keeps deleted row"    'url:https://example.com/gone' ii list
check "v3 field added (rationale)"      '"rationale"'             ii list
check "v3 field added (proposed_at)"    '"proposed_at"'           ii list
# Trigger a persisting verb (scan calls _save, which bumps version + writes migrated rows).
ii scan >/dev/null
check "version bumped to 3 on save"     '"version": 3' cat "$TMP/meta/ingest_index.json"
check "migrated v2 row persisted with v3 field" '"rationale"' cat "$TMP/meta/ingest_index.json"

echo "== enqueue -> queue lists the candidate =="
ii enqueue "arxiv:2501.12345" --title "Candidate paper" --rationale "relevant to objective" \
   --score 0.91 --discovered-by research --objective-ids OBJ-1 >/dev/null
check "queue lists enqueued candidate"  'arxiv:2501.12345'        ii queue
check "queue shows score"               '0.91'                    ii queue
check "enqueued row is waiting_approval" '"status": "waiting_approval"' ii get --id arxiv:2501.12345

echo "== scan does NOT flip waiting_approval/rejected to deleted =="
# real.md still on disk; the deleted row + the waiting_approval candidate have NO file.
ii scan >/dev/null
check "scan keeps waiting_approval"     '"status": "waiting_approval"' ii get --id arxiv:2501.12345
# the candidate must NOT appear in the deleted list
del_out="$(ii deleted 2>&1 || true)"
if printf '%s' "$del_out" | grep -qF 'arxiv:2501.12345'; then
  echo "  FAIL: scan wrongly deleted the waiting_approval candidate"; fail=$((fail + 1))
else
  echo "  PASS: scan did not delete the waiting_approval candidate"; pass=$((pass + 1))
fi

echo "== reject makes re-enqueue a no-op (sticky) =="
ii reject "arxiv:2501.12345" --reason "off-topic" >/dev/null
check "rejected status set"              '"status": "rejected"'    ii get --id arxiv:2501.12345
check "rejection_reason recorded"        'off-topic'               ii get --id arxiv:2501.12345
# re-enqueue: must remain rejected (no regress to waiting_approval)
ii enqueue "arxiv:2501.12345" --title "Try again" >/dev/null
check "re-enqueue stays rejected"        '"status": "rejected"'    ii get --id arxiv:2501.12345
check "re-enqueue did not overwrite title" '"title": "Candidate paper"' ii get --id arxiv:2501.12345

echo "== scan also skips rejected rows =="
ii scan >/dev/null
check "scan keeps rejected"              '"status": "rejected"'    ii get --id arxiv:2501.12345

echo "== approve -> pending shows in pending list =="
ii enqueue "arxiv:2502.99999" --title "Approve me" >/dev/null
ii approve "arxiv:2502.99999" >/dev/null
check "approved row is pending"          '"status": "pending"'     ii get --id arxiv:2502.99999

echo
echo "ingest_index v3: $pass passed, $fail failed"
[ "$fail" -eq 0 ]
