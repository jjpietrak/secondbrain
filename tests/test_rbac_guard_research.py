"""Hermetic tests for the research role extension in scripts/rbac_guard.py.

Tests verify:
  1. research write to wiki/ -> DENY
  2. research write to objective/direction/ -> ALLOW
  3. research write to objective/research_question/ WITHOUT sanction -> DENY
  4. research write to objective/research_question/ WITH SB_SANCTIONED_SKILL=question-promote -> ALLOW
  5. research write to objective/research_question/ WITH SB_SANCTIONED_SKILL=question-solve -> ALLOW
  6. research write to objective/purpose/ -> DENY always (not in sanctioned paths)
  7. research write to objective/topic/ -> DENY always
  8. research write to objective/decision/ -> DENY always
  9. research write to research/ -> ALLOW
  10. research write to meta/nightly_report/ -> ALLOW
  11. research write to objective/hot.md -> ALLOW
  12. research write to objective/index.md -> ALLOW
  13. research write to objective/research_question_proposal/ -> ALLOW
  14. SB_SANCTIONED_SKILL=wrong-value does NOT grant access to research_question/
  15. wiki role: still ALLOW wiki/, still DENY research/ (unchanged behavior)
  16. backend role (unknown to guard): not blocked -> None returned
  17. unknown role: not blocked -> None returned
  18. non-write tool (Read): never blocked -> None
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

FAKE_VAULT = "/tmp/fake-vault-example"


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
# research role tests
# ---------------------------------------------------------------------------


class TestResearchRole:
    def test_wiki_write_denied(self):
        """research must NOT write wiki/."""
        ev = _make_event("research", f"{FAKE_VAULT}/wiki/concepts/foo.md")
        assert _is_deny(_decide_with_vault(ev))

    def test_wiki_subdirectory_denied(self):
        """research must NOT write wiki/entities/ either."""
        ev = _make_event("research", f"{FAKE_VAULT}/wiki/entities/Bar.md")
        assert _is_deny(_decide_with_vault(ev))

    def test_direction_write_allowed(self):
        """research MAY write objective/direction/."""
        ev = _make_event("research", f"{FAKE_VAULT}/objective/direction/DIR-0001-test.md")
        assert _is_allow(_decide_with_vault(ev))

    def test_research_question_proposal_allowed(self):
        """research MAY write objective/research_question_proposal/."""
        ev = _make_event(
            "research",
            f"{FAKE_VAULT}/objective/research_question_proposal/QP-0001-test.md",
        )
        assert _is_allow(_decide_with_vault(ev))

    def test_agent_todo_allowed(self):
        """research MAY write objective/agent_todo/."""
        ev = _make_event("research", f"{FAKE_VAULT}/objective/agent_todo/TODO-0001.md")
        assert _is_allow(_decide_with_vault(ev))

    def test_hot_md_allowed(self):
        """research MAY write objective/hot.md."""
        ev = _make_event("research", f"{FAKE_VAULT}/objective/hot.md")
        assert _is_allow(_decide_with_vault(ev))

    def test_index_md_allowed(self):
        """research MAY write objective/index.md."""
        ev = _make_event("research", f"{FAKE_VAULT}/objective/index.md")
        assert _is_allow(_decide_with_vault(ev))

    def test_research_dir_allowed(self):
        """research MAY write research/."""
        ev = _make_event("research", f"{FAKE_VAULT}/research/deep/2026-06-21-test.md")
        assert _is_allow(_decide_with_vault(ev))

    def test_nightly_report_allowed(self):
        """research MAY write meta/nightly_report/."""
        ev = _make_event("research", f"{FAKE_VAULT}/meta/nightly_report/2026-06-21.md")
        assert _is_allow(_decide_with_vault(ev))

    # ------ R4 sanction tests ------

    def test_research_question_no_sanction_denied(self):
        """research must NOT write objective/research_question/ without sanction."""
        ev = _make_event(
            "research",
            f"{FAKE_VAULT}/objective/research_question/Q-0001-test.md",
        )
        assert _is_deny(_decide_with_vault(ev))

    def test_research_question_question_promote_allowed(self):
        """research MAY write objective/research_question/ with SB_SANCTIONED_SKILL=question-promote."""
        ev = _make_event(
            "research",
            f"{FAKE_VAULT}/objective/research_question/Q-0001-test.md",
        )
        assert _is_allow(
            _decide_with_vault(ev, {"SB_SANCTIONED_SKILL": "question-promote"})
        )

    def test_research_question_question_solve_allowed(self):
        """research MAY write objective/research_question/ with SB_SANCTIONED_SKILL=question-solve."""
        ev = _make_event(
            "research",
            f"{FAKE_VAULT}/objective/research_question/Q-0002-test.md",
        )
        assert _is_allow(
            _decide_with_vault(ev, {"SB_SANCTIONED_SKILL": "question-solve"})
        )

    def test_research_question_wrong_sanction_denied(self):
        """Wrong SB_SANCTIONED_SKILL value must NOT grant access."""
        ev = _make_event(
            "research",
            f"{FAKE_VAULT}/objective/research_question/Q-0003-test.md",
        )
        assert _is_deny(
            _decide_with_vault(ev, {"SB_SANCTIONED_SKILL": "some-other-skill"})
        )

    def test_research_question_empty_sanction_denied(self):
        """Empty SB_SANCTIONED_SKILL must NOT grant access."""
        ev = _make_event(
            "research",
            f"{FAKE_VAULT}/objective/research_question/Q-0004-test.md",
        )
        assert _is_deny(_decide_with_vault(ev, {"SB_SANCTIONED_SKILL": ""}))

    # ------ User-only paths always denied (no sanction exemption) ------

    def test_purpose_denied_always(self):
        """research must NEVER write objective/purpose/ (no R4 exemption)."""
        ev = _make_event("research", f"{FAKE_VAULT}/objective/purpose/PURPOSE.md")
        assert _is_deny(_decide_with_vault(ev))

    def test_purpose_denied_even_with_sanction(self):
        """SB_SANCTIONED_SKILL must NOT grant access to objective/purpose/."""
        ev = _make_event("research", f"{FAKE_VAULT}/objective/purpose/PURPOSE.md")
        assert _is_deny(
            _decide_with_vault(ev, {"SB_SANCTIONED_SKILL": "question-promote"})
        )

    def test_topic_denied(self):
        """research must NOT write objective/topic/."""
        ev = _make_event("research", f"{FAKE_VAULT}/objective/topic/T-0001-test.md")
        assert _is_deny(_decide_with_vault(ev))

    def test_decision_denied(self):
        """research must NOT write objective/decision/."""
        ev = _make_event("research", f"{FAKE_VAULT}/objective/decision/D-0001-test.md")
        assert _is_deny(_decide_with_vault(ev))

    def test_decision_denied_even_with_sanction(self):
        """SB_SANCTIONED_SKILL must NOT grant access to objective/decision/."""
        ev = _make_event("research", f"{FAKE_VAULT}/objective/decision/D-0001-test.md")
        assert _is_deny(
            _decide_with_vault(ev, {"SB_SANCTIONED_SKILL": "question-solve"})
        )

    def test_non_write_tool_not_blocked(self):
        """Read tool is never blocked, even for research."""
        ev = _make_event("research", f"{FAKE_VAULT}/wiki/concepts/foo.md", tool="Read")
        assert _is_allow(_decide_with_vault(ev))

    def test_sb_agent_role_env_overrides_event(self):
        """SB_AGENT_ROLE env var takes priority over event subagent_type."""
        # subagent_type says wiki but SB_AGENT_ROLE says research;
        # the write to objective/direction/ should ALLOW (research role).
        ev = _make_event("wiki", f"{FAKE_VAULT}/objective/direction/DIR-0001.md")
        assert _is_allow(_decide_with_vault(ev, {"SB_AGENT_ROLE": "research"}))


# ---------------------------------------------------------------------------
# wiki role - preserve existing behavior
# ---------------------------------------------------------------------------


class TestWikiRoleUnchanged:
    def test_wiki_write_allowed(self):
        """wiki MAY still write wiki/."""
        ev = _make_event("wiki", f"{FAKE_VAULT}/wiki/concepts/foo.md")
        assert _is_allow(_decide_with_vault(ev))

    def test_raw_papers_allowed(self):
        """wiki MAY still write raw/papers/."""
        ev = _make_event("wiki", f"{FAKE_VAULT}/raw/papers/2026-01-01-paper.pdf")
        assert _is_allow(_decide_with_vault(ev))

    def test_meta_ingest_index_allowed(self):
        """wiki MAY still write meta/ingest_index."""
        ev = _make_event("wiki", f"{FAKE_VAULT}/meta/ingest_index/index.json")
        assert _is_allow(_decide_with_vault(ev))

    def test_research_dir_denied_for_wiki(self):
        """wiki must NOT write research/ (that is the research agent's area)."""
        ev = _make_event("wiki", f"{FAKE_VAULT}/research/deep/2026-06-21-test.md")
        assert _is_deny(_decide_with_vault(ev))

    def test_objective_denied_for_wiki(self):
        """wiki must NOT write objective/."""
        ev = _make_event("wiki", f"{FAKE_VAULT}/objective/direction/DIR-0001.md")
        assert _is_deny(_decide_with_vault(ev))

    def test_wiki_sanction_ignored(self):
        """SB_SANCTIONED_SKILL has no effect on the wiki role."""
        ev = _make_event("wiki", f"{FAKE_VAULT}/objective/research_question/Q-0001.md")
        assert _is_deny(
            _decide_with_vault(ev, {"SB_SANCTIONED_SKILL": "question-promote"})
        )


# ---------------------------------------------------------------------------
# unknown / backend role - not blocked
# ---------------------------------------------------------------------------


class TestUnknownRoleDeferred:
    def test_backend_role_not_blocked(self):
        """backend is not in ENFORCED_ROLES; guard defers (returns None)."""
        ev = _make_event("backend", f"{FAKE_VAULT}/meta/health_report/2026-06-21.md")
        assert _is_allow(_decide_with_vault(ev))

    def test_unknown_role_not_blocked(self):
        """Completely unknown role is not blocked by the guard."""
        ev = _make_event("webagent", f"{FAKE_VAULT}/wiki/concepts/foo.md")
        assert _is_allow(_decide_with_vault(ev))

    def test_no_role_not_blocked(self):
        """No role in event and no SB_AGENT_ROLE: guard defers."""
        ev = {
            "tool_name": "Write",
            "tool_input": {"file_path": f"{FAKE_VAULT}/wiki/concepts/foo.md"},
        }
        assert _is_allow(_decide_with_vault(ev))
