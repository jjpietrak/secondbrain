"""Hermetic tests for the web role extension in scripts/rbac_guard.py.

Tests verify:
  1. web write to meta/nightly_report/ -> ALLOW
  2. web write to wiki/concepts/ -> DENY
  3. web write to wiki/entities/ -> DENY
  4. web write to raw/papers/ -> DENY
  5. web write to objective/direction/ -> DENY
  6. web write to objective/research_question/ -> DENY
  7. web write to meta/ingest_index/ -> DENY (enqueue is a Bash call, not Write)
  8. web Edit to meta/nightly_report/ -> guard defers (path is allowed; web has no Edit in
     practice but the guard checks path allowance, not tool availability)
  9. web Edit to wiki/concepts/ -> DENY
  10. web Edit to objective/direction/ -> DENY
  11. non-write tool (Read) -> None (never blocked)
  12. non-write tool (Bash) -> None (never blocked)
  13. non-write tool (WebFetch) -> None (never blocked)
  14. wiki role: still ALLOW wiki/, still DENY research/ (unchanged behavior)
  15. research role: still ALLOW meta/nightly_report/, still DENY wiki/ (unchanged)
  16. backend role (unknown to guard): not blocked -> None returned
  17. unknown role: not blocked -> None returned
"""

from __future__ import annotations

import os
import sys
from unittest.mock import patch

import pytest

# Make scripts/ importable without installing the package.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import rbac_guard  # noqa: E402  (import after sys.path patch)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

FAKE_VAULT = "/tmp/fake-vault/LLM-Inference"


def _make_event(
    role: str,
    path: str,
    tool: str = "Write",
) -> dict:
    """Build a minimal PreToolUse event dict."""
    return {
        "tool_name": tool,
        "subagent_type": role,
        "tool_input": {"file_path": path},
    }


def _decide_with_vault(
    event: dict,
    env_overrides: dict[str, str] | None = None,
) -> dict | None:
    """Call decide() with a patched vault root and optional env overrides."""
    env = {k: v for k, v in os.environ.items()}
    # Remove any lingering test env vars from the parent environment.
    env.pop("SB_AGENT_ROLE", None)
    env.pop("SB_SANCTIONED_SKILL", None)
    env.pop("VAULT_ROOT", None)
    if env_overrides:
        env.update(env_overrides)
    with patch.dict(os.environ, env, clear=True):
        with patch.object(rbac_guard, "_vault_root", return_value=FAKE_VAULT):
            return rbac_guard.decide(event)


def _is_deny(result: dict | None) -> bool:
    if result is None:
        return False
    return (
        result.get("hookSpecificOutput", {}).get("permissionDecision") == "deny"
    )


def _is_allow(result: dict | None) -> bool:
    """Guard returns None for allow (defer to normal flow)."""
    return result is None


# ---------------------------------------------------------------------------
# web role tests
# ---------------------------------------------------------------------------


class TestWebRole:
    # ------ Allowed paths ------

    def test_nightly_report_write_allowed(self):
        """web MAY write meta/nightly_report/."""
        ev = _make_event("web", f"{FAKE_VAULT}/meta/nightly_report/2026-06-22.md")
        assert _is_allow(_decide_with_vault(ev))

    def test_nightly_report_subdir_write_allowed(self):
        """web MAY write any file under meta/nightly_report/."""
        ev = _make_event("web", f"{FAKE_VAULT}/meta/nightly_report/2026-06-21.md")
        assert _is_allow(_decide_with_vault(ev))

    # ------ Denied vault paths ------

    def test_wiki_concepts_write_denied(self):
        """web must NOT write wiki/concepts/."""
        ev = _make_event("web", f"{FAKE_VAULT}/wiki/concepts/x.md")
        assert _is_deny(_decide_with_vault(ev))

    def test_wiki_entities_write_denied(self):
        """web must NOT write wiki/entities/."""
        ev = _make_event("web", f"{FAKE_VAULT}/wiki/entities/x.md")
        assert _is_deny(_decide_with_vault(ev))

    def test_raw_papers_write_denied(self):
        """web must NOT write raw/papers/."""
        ev = _make_event("web", f"{FAKE_VAULT}/raw/papers/x.pdf")
        assert _is_deny(_decide_with_vault(ev))

    def test_objective_direction_write_denied(self):
        """web must NOT write objective/direction/."""
        ev = _make_event("web", f"{FAKE_VAULT}/objective/direction/x.md")
        assert _is_deny(_decide_with_vault(ev))

    def test_objective_research_question_write_denied(self):
        """web must NOT write objective/research_question/."""
        ev = _make_event("web", f"{FAKE_VAULT}/objective/research_question/x.md")
        assert _is_deny(_decide_with_vault(ev))

    def test_meta_ingest_index_write_denied(self):
        """web must NOT write meta/ingest_index/ via Write tool (enqueue is Bash only)."""
        ev = _make_event("web", f"{FAKE_VAULT}/meta/ingest_index/index.json")
        assert _is_deny(_decide_with_vault(ev))

    # ------ Edit tool (web has no Edit in practice, but guard checks path allowance) ------

    def test_nightly_report_edit_allowed_by_path(self):
        """Guard defers for Edit to allowed path (meta/nightly_report/); web cannot issue
        Edit in practice since it lacks the Edit tool, but the guard path logic should match
        the Write behavior for the allowlisted prefix."""
        ev = _make_event("web", f"{FAKE_VAULT}/meta/nightly_report/2026-06-22.md", tool="Edit")
        assert _is_allow(_decide_with_vault(ev))

    def test_wiki_concepts_edit_denied(self):
        """web Edit to wiki/ must be denied."""
        ev = _make_event("web", f"{FAKE_VAULT}/wiki/concepts/x.md", tool="Edit")
        assert _is_deny(_decide_with_vault(ev))

    def test_objective_direction_edit_denied(self):
        """web Edit to objective/direction/ must be denied."""
        ev = _make_event("web", f"{FAKE_VAULT}/objective/direction/x.md", tool="Edit")
        assert _is_deny(_decide_with_vault(ev))

    # ------ Non-write tools are never blocked ------

    def test_read_tool_not_blocked(self):
        """Read is never blocked, even for restricted paths."""
        ev = _make_event("web", f"{FAKE_VAULT}/wiki/concepts/foo.md", tool="Read")
        assert _is_allow(_decide_with_vault(ev))

    def test_bash_tool_not_blocked(self):
        """Bash is never blocked by this guard."""
        ev = _make_event("web", f"{FAKE_VAULT}/meta/ingest_index/index.json", tool="Bash")
        assert _is_allow(_decide_with_vault(ev))

    def test_webfetch_tool_not_blocked(self):
        """WebFetch is never blocked by this guard."""
        ev = _make_event("web", "https://arxiv.org/abs/2301.00001", tool="WebFetch")
        assert _is_allow(_decide_with_vault(ev))

    def test_websearch_tool_not_blocked(self):
        """WebSearch is never blocked by this guard."""
        ev = _make_event("web", "memory bandwidth disaggregation LLM", tool="WebSearch")
        assert _is_allow(_decide_with_vault(ev))

    # ------ SB_AGENT_ROLE env var overrides event ------

    def test_sb_agent_role_env_overrides_event(self):
        """SB_AGENT_ROLE=web overrides a different subagent_type in the event."""
        # subagent_type says research but SB_AGENT_ROLE says web;
        # the write to meta/nightly_report/ should ALLOW (web role).
        ev = _make_event("research", f"{FAKE_VAULT}/meta/nightly_report/2026-06-22.md")
        assert _is_allow(_decide_with_vault(ev, {"SB_AGENT_ROLE": "web"}))

    def test_sb_agent_role_env_web_blocks_wiki_write(self):
        """SB_AGENT_ROLE=web enforces web allowlist even when event says a different role."""
        ev = _make_event("wiki", f"{FAKE_VAULT}/wiki/concepts/foo.md")
        assert _is_deny(_decide_with_vault(ev, {"SB_AGENT_ROLE": "web"}))


# ---------------------------------------------------------------------------
# wiki role - preserve existing behavior (spot checks)
# ---------------------------------------------------------------------------


class TestWikiRoleUnchangedByWebAddition:
    def test_wiki_write_still_allowed(self):
        """Adding web role must not break wiki ALLOW for wiki/."""
        ev = _make_event("wiki", f"{FAKE_VAULT}/wiki/concepts/foo.md")
        assert _is_allow(_decide_with_vault(ev))

    def test_wiki_research_dir_still_denied(self):
        """wiki must NOT write research/ (unchanged)."""
        ev = _make_event("wiki", f"{FAKE_VAULT}/research/deep/2026-06-21-test.md")
        assert _is_deny(_decide_with_vault(ev))


# ---------------------------------------------------------------------------
# research role - preserve existing behavior (spot checks)
# ---------------------------------------------------------------------------


class TestResearchRoleUnchangedByWebAddition:
    def test_research_nightly_report_still_allowed(self):
        """research MAY write meta/nightly_report/ (unchanged)."""
        ev = _make_event("research", f"{FAKE_VAULT}/meta/nightly_report/2026-06-21.md")
        assert _is_allow(_decide_with_vault(ev))

    def test_research_wiki_still_denied(self):
        """research must NOT write wiki/ (unchanged)."""
        ev = _make_event("research", f"{FAKE_VAULT}/wiki/concepts/foo.md")
        assert _is_deny(_decide_with_vault(ev))


# ---------------------------------------------------------------------------
# unknown / backend role - not blocked (unchanged)
# ---------------------------------------------------------------------------


class TestUnknownRoleDeferredUnchanged:
    def test_backend_role_not_blocked(self):
        """backend is not in ENFORCED_ROLES; guard defers (returns None)."""
        ev = _make_event("backend", f"{FAKE_VAULT}/meta/health_report/2026-06-21.md")
        assert _is_allow(_decide_with_vault(ev))

    def test_unknown_role_not_blocked(self):
        """Completely unknown role is not blocked by the guard."""
        ev = _make_event("unknown-agent", f"{FAKE_VAULT}/wiki/concepts/foo.md")
        assert _is_allow(_decide_with_vault(ev))

    def test_no_role_not_blocked(self):
        """No role in event and no SB_AGENT_ROLE: guard defers."""
        ev = {
            "tool_name": "Write",
            "tool_input": {"file_path": f"{FAKE_VAULT}/wiki/concepts/foo.md"},
        }
        assert _is_allow(_decide_with_vault(ev))
