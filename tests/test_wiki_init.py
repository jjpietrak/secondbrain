#!/usr/bin/env python3
"""Hermetic tests for wiki-init (dry-run-on-copy + apply idempotency) and the RBAC guard.

No network, no live vault. Builds a fixture vault under a tmp dir.

Run:  .venv/bin/python -m pytest tests/test_wiki_init.py -q
 or:  .venv/bin/python tests/test_wiki_init.py   (self-running fallback)
"""

from __future__ import annotations

import importlib.util
import io
import json
import os
import shutil
import sys
import tempfile
from contextlib import redirect_stdout
from pathlib import Path

REPO = Path(os.environ.get("CODE_PATH", "/home/jpietrak/second_brain"))
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))


def _load(modname: str, relpath: str):
    spec = importlib.util.spec_from_file_location(modname, REPO / relpath)
    mod = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(mod)
    return mod


wiki_init = _load("wiki_init", "scripts/wiki_init.py")
rbac_guard = _load("rbac_guard", "scripts/rbac_guard.py")


# ---------------------------------------------------------------------------
# Fixture
# ---------------------------------------------------------------------------

def make_fixture(root: Path) -> None:
    """Recreate the live-vault divergences in miniature."""
    (root / "wiki/concepts").mkdir(parents=True)
    (root / "wiki/entities").mkdir(parents=True)
    (root / "wiki/sources").mkdir(parents=True)
    (root / "wiki/synthesis").mkdir(parents=True)
    (root / "wiki/hot").mkdir(parents=True)  # folder form (divergence)
    (root / "raw/papers").mkdir(parents=True)
    (root / "raw/articles").mkdir(parents=True)
    (root / "meta").mkdir(parents=True)
    (root / "daily").mkdir(parents=True)     # to be dropped
    (root / "output").mkdir(parents=True)    # to be dropped
    # templates/ = old Obsidian Templater dir; Change 1 drops it.
    (root / "templates").mkdir(parents=True)
    (root / "templates/Daily Note.md").write_text("# Daily Note\n", encoding="utf-8")

    # An existing custom template (so reconcile UPDATES it to the superset).
    (root / "wiki/entities/_template.md").write_text(
        "---\ntype: entity\ntitle: \"{{title}}\"\nstatus: seed\n---\n# {{title}}\n",
        encoding="utf-8",
    )
    # hot.md inside the folder
    (root / "wiki/hot/hot.md").write_text("# Hot\nrecent\n", encoding="utf-8")
    # stray root page
    (root / "stepfun-mfa.md").write_text("# StepFun MFA\nstray page\n", encoding="utf-8")
    # vault _CLAUDE.md = v0.1 artefact; Change 2 removes it.
    (root / "_CLAUDE.md").write_text("# Vault rules\npurpose: disaggregated inference\n",
                                     encoding="utf-8")
    # a real source page (must NOT be touched)
    (root / "wiki/sources/photons.md").write_text("# Photons\nkeep me\n", encoding="utf-8")
    # meta single-artifact files (must stay files)
    (root / "meta/ingest_index.json").write_text("{}\n", encoding="utf-8")
    (root / "meta/cost_report.md").write_text("# cost\n", encoding="utf-8")


def tree_snapshot(root: Path) -> dict[str, str]:
    """Map of relpath -> content for every file (for change detection)."""
    out: dict[str, str] = {}
    for p in sorted(root.rglob("*")):
        if p.is_file():
            out[str(p.relative_to(root))] = p.read_text(encoding="utf-8", errors="replace")
    return out


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_dry_run_makes_zero_source_changes() -> None:
    with tempfile.TemporaryDirectory() as td:
        base = Path(td)
        vault = base / "vault"
        make_fixture(vault)
        before = tree_snapshot(vault)

        out = io.StringIO()
        with redirect_stdout(out):
            plan, report = wiki_init.run_reconcile(vault, apply=False, out_dir=base / "tmp")

        after = tree_snapshot(vault)

        # The ONLY new file on the live vault is the dry-run report under meta/health_report/.
        new_keys = set(after) - set(before)
        assert all(k.startswith("meta/health_report/wiki-init-dryrun-") for k in new_keys), \
            f"unexpected new files: {new_keys}"
        # No EXISTING file was modified or removed.
        for k, v in before.items():
            assert k in after, f"file disappeared from live vault: {k}"
            assert after[k] == v, f"live file mutated: {k}"

        # Report exists and names the key changes.
        assert report is not None and report.exists()
        txt = report.read_text(encoding="utf-8")
        for needle in ("raw/opinions", "research/query", "daily", "output",
                       "wiki/hot.md", "stepfun-mfa.md", "_template.md", "STOP",
                       "templates", "_CLAUDE.md"):
            assert needle in txt, f"report missing mention of {needle!r}"

        # Plan reflects the expected actions.
        assert "daily" in plan.folders_dropped and "output" in plan.folders_dropped
        assert "templates" in plan.folders_dropped
        assert plan.hot_collapsed is True
        assert plan.vault_claude_deleted is True
        assert any("raw/opinions" in f for f in plan.folders_created)
        assert any(s == "stepfun-mfa.md" for s, _ in plan.files_moved)
        # entity template UPDATED (existed but differs), others ADDED.
        assert plan.template_actions.get("wiki/entities/_template.md") == "updated"
        assert plan.template_actions.get("wiki/concepts/_template.md") == "added"
        # producer-folder templates added.
        assert plan.template_actions.get("meta/health_report/_template.md") == "added"
        assert plan.template_actions.get("meta/nightly_report/_template.md") == "added"
        assert plan.template_actions.get("research/deep/_template.md") == "added"
        assert plan.template_actions.get("research/query/_template.md") == "added"
    print("PASS test_dry_run_makes_zero_source_changes")


def test_apply_is_idempotent() -> None:
    with tempfile.TemporaryDirectory() as td:
        base = Path(td)
        vault = base / "vault"
        make_fixture(vault)

        # First apply: mutates.
        plan1, _ = wiki_init.run_reconcile(vault, apply=True)
        assert not plan1.is_noop(), "first apply should change the tree"

        # Schema invariants after apply.
        assert (vault / "wiki/hot.md").is_file()
        assert not (vault / "wiki/hot").exists()
        assert not (vault / "daily").exists()
        assert not (vault / "output").exists()
        # Change 1: templates/ dropped.
        assert not (vault / "templates").exists()
        assert (vault / "wiki/concepts/stepfun-mfa.md").is_file()
        assert not (vault / "stepfun-mfa.md").exists()
        # Change 2: vault _CLAUDE.md deleted.
        assert not (vault / "_CLAUDE.md").exists()
        assert (vault / "raw/opinions").is_dir()
        assert (vault / "raw/code").is_dir()
        assert (vault / "raw/notebooklm").is_dir()
        assert (vault / "research/query").is_dir()
        assert (vault / "meta/health_report").is_dir()
        assert (vault / "meta/nightly_report").is_dir()
        # meta single-artifact stores stayed FILES.
        assert (vault / "meta/ingest_index.json").is_file()
        assert (vault / "meta/cost_report.md").is_file()
        # untouched real page preserved.
        assert (vault / "wiki/sources/photons.md").read_text() == "# Photons\nkeep me\n"
        # templates carry the merged superset (timeline + frozen tags).
        ent = (vault / "wiki/entities/_template.md").read_text()
        assert "timeline:" in ent and "company" in ent and "last_interaction" in ent
        assert "ai-first: true" in ent and "For future Claude" in ent
        # Change 1: producer-folder templates exist and have For future Claude preamble.
        for prod in ("meta/health_report/_template.md", "meta/nightly_report/_template.md",
                     "research/deep/_template.md", "research/query/_template.md"):
            t = (vault / prod).read_text()
            assert "For future Claude" in t, f"missing preamble in {prod}"
            assert "ai-first: true" in t, f"missing ai-first in {prod}"

        after_first = tree_snapshot(vault)

        # Second apply: no-op.
        plan2, _ = wiki_init.run_reconcile(vault, apply=True)
        assert plan2.is_noop(), f"second apply not idempotent: {vars(plan2)}"
        after_second = tree_snapshot(vault)
        assert after_first == after_second, "idempotency violated: tree changed on re-apply"
    print("PASS test_apply_is_idempotent")


def test_rbac_hook_denies_wiki_write_to_objective() -> None:
    vault_root = "/mnt/c/Obsidian/Inference-Disagg"
    os.environ["VAULT_ROOT"] = vault_root
    try:
        # wiki agent writing to objective/ -> DENY
        event = {
            "tool_name": "Write",
            "tool_input": {"file_path": f"{vault_root}/objective/topic/foo.md"},
            "agent": "wiki",
        }
        decision = rbac_guard.decide(event)
        assert decision is not None, "expected a deny decision"
        hso = decision["hookSpecificOutput"]
        assert hso["permissionDecision"] == "deny"
        assert "objective" in hso["permissionDecisionReason"]

        # wiki writing to wiki/ -> allowed (no opinion)
        ok = rbac_guard.decide({
            "tool_name": "Write",
            "tool_input": {"file_path": f"{vault_root}/wiki/concepts/x.md"},
            "agent": "wiki",
        })
        assert ok is None, "wiki write to wiki/ should be permitted"

        # wiki writing to raw/papers/ -> allowed
        ok2 = rbac_guard.decide({
            "tool_name": "Write",
            "tool_input": {"file_path": f"{vault_root}/raw/papers/x.pdf"},
            "agent": "wiki",
        })
        assert ok2 is None

        # wiki writing to meta/health_report/ (backend-owned) -> DENY
        deny2 = rbac_guard.decide({
            "tool_name": "Edit",
            "tool_input": {"file_path": f"{vault_root}/meta/health_report/r.md"},
            "agent": "wiki",
        })
        assert deny2 is not None and deny2["hookSpecificOutput"]["permissionDecision"] == "deny"

        # wiki writing to research/ -> DENY
        deny3 = rbac_guard.decide({
            "tool_name": "Write",
            "tool_input": {"file_path": f"{vault_root}/research/deep/r.md"},
            "agent": "wiki",
        })
        assert deny3 is not None and deny3["hookSpecificOutput"]["permissionDecision"] == "deny"

        # meta/ingest_index.json -> allowed (wiki owns it)
        ok3 = rbac_guard.decide({
            "tool_name": "Edit",
            "tool_input": {"file_path": f"{vault_root}/meta/ingest_index.json"},
            "agent": "wiki",
        })
        assert ok3 is None

        # A Read tool is never governed.
        assert rbac_guard.decide({
            "tool_name": "Read",
            "tool_input": {"file_path": f"{vault_root}/objective/topic/foo.md"},
            "agent": "wiki",
        }) is None

        # Unknown role -> not enforced (defers).
        assert rbac_guard.decide({
            "tool_name": "Write",
            "tool_input": {"file_path": f"{vault_root}/objective/topic/foo.md"},
            "agent": "backend",
        }) is None
    finally:
        os.environ.pop("VAULT_ROOT", None)
    print("PASS test_rbac_hook_denies_wiki_write_to_objective")


def _run_all() -> int:
    test_dry_run_makes_zero_source_changes()
    test_apply_is_idempotent()
    test_rbac_hook_denies_wiki_write_to_objective()
    print("\nAll wiki-init tests passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(_run_all())
