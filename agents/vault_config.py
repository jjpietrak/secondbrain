#!/usr/bin/env python3
"""Resolve the ACTIVE vault and load its encapsulated config.

Multi-vault model: each vault is fully encapsulated under config/vaults/<name>/
({vault,topics,budget}.yaml).  Agent rules and the vault PURPOSE live in the CODE repo:
CLAUDE.md, docs/, .claude/, and config/vaults/<name>/vault.yaml (purpose field).
The vault on-disk _CLAUDE.md is a v0.1 artefact; wiki_init reconcile removes it.
Shared infrastructure (the single LiteLLM proxy + API keys in .env) lives in
config/secondbrain.yaml and is NOT per-vault.

Active-vault resolution precedence:
  1. explicit `name` argument
  2. $VAULT environment variable
  3. `default_vault` in config/secondbrain.yaml

CLI (used by agents/commands to resolve paths without parsing YAML themselves):
  python -m agents.vault_config name        # active vault name
  python -m agents.vault_config path        # active vault absolute path  ($VAULT_ROOT)
  python -m agents.vault_config purpose      # active vault PURPOSE one-liner
  python -m agents.vault_config notebook     # active vault NotebookLM notebook id/alias
  python -m agents.vault_config engine       # default research engine (claude|perplexity|free)
  python -m agents.vault_config topics-due   # topic names due for research today
  python -m agents.vault_config list         # all registered vault names
  python -m agents.vault_config env          # export lines: VAULT, VAULT_ROOT, VAULT_PATH
  ... add --vault <name> to any of the above to target a specific vault.
"""

from __future__ import annotations

import os
import sys
from functools import lru_cache
from pathlib import Path

import yaml

CODE_PATH = Path(os.environ.get("CODE_PATH") or Path(__file__).resolve().parent.parent)
CONFIG_DIR = CODE_PATH / "config"
VAULTS_DIR = CONFIG_DIR / "vaults"
GLOBAL_CONFIG = CONFIG_DIR / "secondbrain.yaml"


@lru_cache(maxsize=1)
def global_config() -> dict:
    if GLOBAL_CONFIG.exists():
        return yaml.safe_load(GLOBAL_CONFIG.read_text()) or {}
    return {}


def default_vault() -> str:
    return global_config().get("default_vault", "example")


def registered_vaults() -> list[str]:
    """Names from the registry; fall back to whatever dirs exist under config/vaults/."""
    reg = [v["name"] for v in global_config().get("vaults", []) if v.get("name")]
    if reg:
        return reg
    return sorted(p.name for p in VAULTS_DIR.iterdir() if p.is_dir()) if VAULTS_DIR.exists() else []


def enabled_vaults() -> list[str]:
    vs = global_config().get("vaults", [])
    enabled = [v["name"] for v in vs if v.get("name") and v.get("enabled", True)]
    return enabled or registered_vaults()


def active_vault(name: str | None = None) -> str:
    return name or os.environ.get("VAULT") or default_vault()


def vault_config_dir(name: str | None = None) -> Path:
    return VAULTS_DIR / active_vault(name)


def _load_yaml(path: Path) -> dict:
    if not path.exists():
        raise SystemExit(f"missing config: {path}")
    return yaml.safe_load(path.read_text()) or {}


def load_vault(name: str | None = None) -> dict:
    return _load_yaml(vault_config_dir(name) / "vault.yaml")


def load_topics(name: str | None = None) -> dict:
    path = vault_config_dir(name) / "topics.yaml"
    return _load_yaml(path) if path.exists() else {"topics": []}


def load_budget(name: str | None = None) -> dict:
    path = vault_config_dir(name) / "budget.yaml"
    return _load_yaml(path) if path.exists() else {}


def vault_path(name: str | None = None) -> Path:
    """Absolute path of the active vault ($VAULT_ROOT).

    Precedence: when a vault is explicitly selected (name arg OR $VAULT env), its
    per-vault config always wins — this keeps multi-vault loops correct even if a
    stale $VAULT_PATH is exported. Only when NO vault is selected does a direct
    $VAULT_PATH env override apply (back-compat / single-vault convenience).
    """
    if name is None and not os.environ.get("VAULT") and os.environ.get("VAULT_PATH"):
        return Path(os.environ["VAULT_PATH"])
    vp = load_vault(name).get("vault_path")
    if not vp:
        raise SystemExit(f"vault_path not set in {vault_config_dir(name) / 'vault.yaml'}")
    return Path(vp)


def purpose(name: str | None = None) -> str:
    return " ".join((load_vault(name).get("purpose") or "").split()) or "(no purpose set)"


def notebooklm_notebook(name: str | None = None) -> str:
    """The active vault's NotebookLM notebook id/alias for sync.

    Per-vault config wins; the global NLM_SYNC_NOTEBOOK env is a fallback only when the
    vault sets none (keeps VAULT=all correct — each vault uses its own notebook)."""
    return (load_vault(name).get("notebooklm_notebook")
            or os.environ.get("NLM_SYNC_NOTEBOOK") or "").strip()


def research_engine(name: str | None = None) -> str:
    """Default research engine for /obsidian-research[-deep]: claude | perplexity | free."""
    val = (load_vault(name).get("research_engine") or "claude").strip().lower()
    return val if val in {"claude", "perplexity", "free"} else "claude"


def due_topics(name: str | None = None) -> list[str]:
    """Topic names due for research today: `daily` always, others only on Mondays."""
    from datetime import date
    is_monday = date.today().weekday() == 0
    out = []
    for t in load_topics(name).get("topics", []):
        if not isinstance(t, dict) or not t.get("name"):
            continue
        freq = (t.get("frequency") or "weekly").strip().lower()
        if freq == "daily" or is_monday:
            out.append(t["name"])
    return out


def _main(argv: list[str]) -> int:
    args = list(argv)
    name = None
    if "--vault" in args:
        i = args.index("--vault")
        name = args[i + 1]
        del args[i:i + 2]
    cmd = args[0] if args else "name"

    if cmd == "name":
        print(active_vault(name))
    elif cmd == "path":
        print(vault_path(name))
    elif cmd == "purpose":
        print(purpose(name))
    elif cmd == "notebook":
        print(notebooklm_notebook(name))
    elif cmd == "engine":
        print(research_engine(name))
    elif cmd == "topics-due":
        print("\n".join(due_topics(name)))
    elif cmd == "list":
        print("\n".join(registered_vaults()))
    elif cmd == "env":
        v = active_vault(name)
        p = vault_path(name)
        print(f"VAULT={v}")
        print(f"VAULT_ROOT={p}")
        print(f"VAULT_PATH={p}")
    else:
        print(f"unknown command: {cmd}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(_main(sys.argv[1:]))
