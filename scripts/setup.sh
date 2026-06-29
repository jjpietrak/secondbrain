#!/usr/bin/env bash
# setup.sh -- one-command team setup for Second Brain v0.2
#
# Usage:
#   scripts/setup.sh --vault-path <abs-path> --name <vault-name> [--purpose "<one-line>"]
#
# What it does:
#   1. Creates config/vaults/<name>/ from the example template (vault/topics/budget.yaml).
#   2. Registers the vault in config/secondbrain.yaml and sets it as default_vault.
#   3. Scaffolds the vault at <vault-path>: wiki structure, objective/ graph, PURPOSE.md.
#   4. Prints next steps.
#
# Idempotent: re-running updates config; does NOT clobber existing vault content.
# Exit 0 = success.  Non-zero = bad args or a step failed.

set -euo pipefail

# ---------------------------------------------------------------------------
# Resolve the repo root from the script location (portable: no $PWD assumption)
# ---------------------------------------------------------------------------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "${SCRIPT_DIR}/.." && pwd)"

# ---------------------------------------------------------------------------
# Python: prefer the repo venv (Linux .venv), fall back to system python3
# ---------------------------------------------------------------------------
if [ -x "${REPO}/.venv/bin/python" ]; then
    PYTHON="${REPO}/.venv/bin/python"
else
    PYTHON="python3"
fi

# ---------------------------------------------------------------------------
# Argument parsing (flags; prompt interactively for any missing required arg)
# ---------------------------------------------------------------------------
VAULT_PATH=""
VAULT_NAME=""
PURPOSE=""

while [[ $# -gt 0 ]]; do
    case "$1" in
        --vault-path)
            VAULT_PATH="$2"; shift 2 ;;
        --name)
            VAULT_NAME="$2"; shift 2 ;;
        --purpose)
            PURPOSE="$2"; shift 2 ;;
        -h|--help)
            echo "Usage: scripts/setup.sh --vault-path <abs-path> --name <vault-name> [--purpose \"<one-line>\"]"
            exit 0 ;;
        *)
            echo "ERROR: unknown argument: $1" >&2
            echo "Usage: scripts/setup.sh --vault-path <abs-path> --name <vault-name> [--purpose \"<one-line>\"]" >&2
            exit 1 ;;
    esac
done

# Prompt interactively for missing required args
if [ -z "${VAULT_PATH}" ]; then
    read -rp "Vault path (absolute path to your Obsidian vault folder): " VAULT_PATH
fi
if [ -z "${VAULT_NAME}" ]; then
    read -rp "Vault name (short identifier, e.g. my-research): " VAULT_NAME
fi

# Validate
if [ -z "${VAULT_PATH}" ]; then
    echo "ERROR: --vault-path is required." >&2; exit 1
fi
if [ -z "${VAULT_NAME}" ]; then
    echo "ERROR: --vault-name is required." >&2; exit 1
fi
# Vault name: alphanumeric, hyphens, underscores only
if ! echo "${VAULT_NAME}" | grep -qE '^[A-Za-z0-9_-]+$'; then
    echo "ERROR: vault name must contain only letters, digits, hyphens, or underscores." >&2; exit 1
fi

# ---------------------------------------------------------------------------
# Step 1: Create config/vaults/<name>/ from the example template
# ---------------------------------------------------------------------------
VAULT_CFG_SRC="${REPO}/config/vaults/example"
VAULT_CFG_DST="${REPO}/config/vaults/${VAULT_NAME}"

echo ""
echo "=== Step 1: vault config ==="
mkdir -p "${VAULT_CFG_DST}"

# Use inline Python for safe YAML substitution (no brittle sed).
# Copy and patch vault.yaml, topics.yaml, budget.yaml.
"${PYTHON}" - <<PYEOF
import sys, shutil, pathlib, re

src = pathlib.Path("${VAULT_CFG_SRC}")
dst = pathlib.Path("${VAULT_CFG_DST}")
vault_name = "${VAULT_NAME}"
vault_path = "${VAULT_PATH}"
purpose = """${PURPOSE}"""

# ---- vault.yaml ----
vault_src = src / "vault.yaml"
vault_dst = dst / "vault.yaml"
if vault_dst.exists():
    print(f"  vault.yaml already exists at {vault_dst} -- updating name/vault_path/purpose fields.")
text = vault_src.read_text(encoding="utf-8")

# Replace name: example -> name: <name>
text = re.sub(r'^name:\s*.*$', f'name: {vault_name}', text, flags=re.MULTILINE)

# Replace vault_path: line with the real path (preserve ~ expansion note comment)
text = re.sub(
    r'^vault_path:\s*.*$',
    f'vault_path: "{vault_path}"',
    text, flags=re.MULTILINE,
)

# Replace purpose: if a real purpose was supplied
if purpose.strip():
    escaped = purpose.strip().replace('"', '\\"')
    text = re.sub(
        r'^purpose:\s*".*".*$',
        f'purpose: "{escaped}"',
        text, flags=re.MULTILINE,
    )

vault_dst.write_text(text, encoding="utf-8")
print(f"  wrote {vault_dst}")

# ---- topics.yaml ---- (copy as-is if target does not exist)
t_dst = dst / "topics.yaml"
if not t_dst.exists():
    shutil.copy2(src / "topics.yaml", t_dst)
    print(f"  wrote {t_dst}")
else:
    print(f"  topics.yaml already exists -- skipping.")

# ---- budget.yaml ---- (copy as-is if target does not exist)
b_dst = dst / "budget.yaml"
if not b_dst.exists():
    shutil.copy2(src / "budget.yaml", b_dst)
    print(f"  wrote {b_dst}")
else:
    print(f"  budget.yaml already exists -- skipping.")

print("  Step 1 done.")
PYEOF

# ---------------------------------------------------------------------------
# Step 2: Register vault in config/secondbrain.yaml + set as default_vault
# ---------------------------------------------------------------------------
SECONDBRAIN_YAML="${REPO}/config/secondbrain.yaml"

echo ""
echo "=== Step 2: vault registry ==="
"${PYTHON}" - <<PYEOF
import pathlib, re

yaml_path = pathlib.Path("${SECONDBRAIN_YAML}")
vault_name = "${VAULT_NAME}"
text = yaml_path.read_text(encoding="utf-8")

# Update default_vault
text = re.sub(r'^default_vault:\s*.*$', f'default_vault: {vault_name}', text, flags=re.MULTILINE)

# Add vault to registry if absent
entry_marker = f'  - name: {vault_name}'
if entry_marker not in text:
    # Insert after the last '  - name:' entry in the vaults: block
    # Find the vaults: block and append after the last entry.
    new_entry = f'  - name: {vault_name}\n    enabled: true\n'
    # Simple approach: find 'vaults:' and insert just before 'shared:' or end of file
    if '  - name:' in text:
        # append after the last registry entry
        last_name_match = None
        for m in re.finditer(r'^  - name:.*$', text, flags=re.MULTILINE):
            last_name_match = m
        if last_name_match:
            # Find end of that entry block (next blank line or next entry or end)
            insert_pos = last_name_match.end()
            # Advance past the enabled: line if present
            rest = text[insert_pos:]
            enabled_m = re.match(r'\n    enabled:.*', rest)
            if enabled_m:
                insert_pos += enabled_m.end()
            text = text[:insert_pos] + '\n' + new_entry + text[insert_pos:]
    else:
        # No entries yet; insert after 'vaults:' line
        text = re.sub(r'^(vaults:\s*)$', r'\1\n' + new_entry, text, flags=re.MULTILINE)
    print(f"  registered '{vault_name}' in vaults registry.")
else:
    print(f"  '{vault_name}' already in registry -- skipping add.")

yaml_path.write_text(text, encoding="utf-8")
print(f"  set default_vault: {vault_name}")
print("  Step 2 done.")
PYEOF

# ---------------------------------------------------------------------------
# Step 3: Scaffold vault directory
# ---------------------------------------------------------------------------
echo ""
echo "=== Step 3: scaffold vault at ${VAULT_PATH} ==="
mkdir -p "${VAULT_PATH}"

# wiki_init scaffold: creates wiki/, raw/, research/, meta/ structure + _template.md files
VAULT="${VAULT_NAME}" \
    "${PYTHON}" "${REPO}/scripts/wiki_init.py" scaffold \
    --vault-root "${VAULT_PATH}"
echo "  wiki structure scaffolded."

# obj_init apply: creates objective/ folder tree + _template.md files
VAULT="${VAULT_NAME}" \
    "${PYTHON}" "${REPO}/scripts/obj_init.py" \
    --vault-root "${VAULT_PATH}" \
    --apply
echo "  objective/ structure scaffolded."

# ---------------------------------------------------------------------------
# Step 4: Write PURPOSE.md into objective/purpose/
# ---------------------------------------------------------------------------
echo ""
echo "=== Step 4: PURPOSE.md ==="
PURPOSE_DIR="${VAULT_PATH}/objective/purpose"
PURPOSE_FILE="${PURPOSE_DIR}/PURPOSE.md"
mkdir -p "${PURPOSE_DIR}"

if [ -f "${PURPOSE_FILE}" ]; then
    echo "  PURPOSE.md already exists -- skipping."
else
    TEMPLATE="${REPO}/scripts/templates/purpose_template.md"
    TODAY="$(date +%Y-%m-%d)"
    if [ -n "${PURPOSE}" ]; then
        ESCAPED_PURPOSE="${PURPOSE}"
    else
        ESCAPED_PURPOSE="TODO: one sentence stating this vault's single research purpose (the high-priority relevance filter)."
    fi
    "${PYTHON}" - <<PYEOF
import pathlib, datetime

template = pathlib.Path("${TEMPLATE}").read_text(encoding="utf-8")
today = "${TODAY}"
purpose = """${PURPOSE}"""
vault_name = "${VAULT_NAME}"

out = template.replace("{{date}}", today)
out = out.replace("{{vault}}", vault_name)

if purpose.strip():
    out = out.replace(
        "TODO: one sentence stating this vault's single research purpose (the high-priority relevance filter).",
        purpose.strip()
    )

pathlib.Path("${PURPOSE_FILE}").write_text(out, encoding="utf-8")
print(f"  wrote ${PURPOSE_FILE}")
PYEOF
fi

# ---------------------------------------------------------------------------
# Step 5: Next steps
# ---------------------------------------------------------------------------
echo ""
echo "============================================================"
echo "  Second Brain setup complete."
echo "============================================================"
echo ""
echo "  Vault name   : ${VAULT_NAME}"
echo "  Vault path   : ${VAULT_PATH}"
echo "  Config       : ${REPO}/config/vaults/${VAULT_NAME}/"
echo ""
echo "  Next steps:"
echo ""
echo "  1. Edit config/vaults/${VAULT_NAME}/vault.yaml to confirm the vault"
echo "     path and PURPOSE one-liner."
echo ""
echo "  2. Copy .env.example to .env (if you have not already) and fill in"
echo "     your API keys (CLAUDE_CODE_OAUTH_TOKEN is required for automation)."
echo ""
echo "  3. Restart Claude Code to register skills and agents."
echo "     (The skill registry is loaded at startup.)"
echo ""
echo "  4. Drop a PDF into ${VAULT_PATH}/raw/papers/"
echo "     and run: /wiki-ingest"
echo "     Then try: /wiki-query <your question>"
echo ""
echo "  5. Seed your research objectives:"
echo "     Edit ${VAULT_PATH}/objective/purpose/PURPOSE.md"
echo "     Then run: /obj-synth, /deep-synthesis"
echo ""
echo "  Tip: VAULT=${VAULT_NAME} claude   -- targets this vault explicitly."
echo ""
exit 0
