"""Loads research-toolkit credentials from the project .env at CODE_PATH/.env."""

from pathlib import Path
from dotenv import load_dotenv
import os

# Prefer the project .env; fall back to the legacy osb config dir.
_code_path = Path(os.environ.get("CODE_PATH", "/home/jpietrak/second_brain"))
_project_env = _code_path / ".env"
_legacy_env = Path.home() / ".config" / "obsidian-second-brain" / ".env"
ENV_PATH = _project_env if _project_env.exists() else _legacy_env

load_dotenv(ENV_PATH)


def get_required(name: str) -> str:
    val = os.environ.get(name, "").strip()
    if not val:
        raise SystemExit(
            f"\n{name} not configured.\n"
            f"Add it to {ENV_PATH}\n"
        )
    return val


def get_optional(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip() or default


XAI_API_KEY = lambda: get_required("XAI_API_KEY")
PERPLEXITY_API_KEY = lambda: get_required("PERPLEXITY_API_KEY")
GEMINI_API_KEY = lambda: get_required("GEMINI_API_KEY")
YOUTUBE_API_KEY = lambda: get_optional("YOUTUBE_API_KEY", "")

GROK_MODEL = get_optional("GROK_MODEL", "grok-4")
PERPLEXITY_RESEARCH_MODEL = get_optional("PERPLEXITY_RESEARCH_MODEL", "sonar-pro")
PERPLEXITY_DEEP_MODEL = get_optional("PERPLEXITY_DEEP_MODEL", "sonar-deep-research")
NOTEBOOKLM_MODEL = get_optional("NOTEBOOKLM_MODEL", "gemini-2.5-flash")

def _resolve_vault_path() -> Path:
    """Active vault path. Precedence: VAULT_PATH env > multi-vault resolver
    (VAULT / default_vault) > legacy OBSIDIAN_VAULT_PATH."""
    vp = os.environ.get("VAULT_PATH")
    if vp:
        return Path(vp).expanduser()
    try:
        from agents import vault_config as vc  # resolves VAULT / default_vault
        return vc.vault_path()
    except Exception:
        return Path(get_required("OBSIDIAN_VAULT_PATH")).expanduser()


VAULT_PATH = _resolve_vault_path()
USAGE_LOG = Path.home() / ".research-toolkit" / "usage.log"
