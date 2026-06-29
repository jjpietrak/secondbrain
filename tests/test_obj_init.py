#!/usr/bin/env python3
"""Hermetic tests for scripts/obj_init.py.

No network, no live vault. Creates a fixture temp dir.

Run:  .venv/bin/python -m pytest tests/test_obj_init.py -v
 or:  .venv/bin/python tests/test_obj_init.py
"""

from __future__ import annotations

import importlib.util
import os
import sys
import tempfile
from pathlib import Path

REPO = Path(os.environ.get("CODE_PATH") or Path(__file__).resolve().parent.parent)
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))


def _load(modname: str, relpath: str):
    spec = importlib.util.spec_from_file_location(modname, REPO / relpath)
    mod = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(mod)
    return mod


obj_init = _load("obj_init", "scripts/obj_init.py")


# ---------------------------------------------------------------------------
# Expected structure
# ---------------------------------------------------------------------------

EXPECTED_FOLDERS = [
    "objective/purpose",
    "objective/topic",
    "objective/research_question",
    "objective/decision",
    "objective/research_question_proposal",
    "objective/direction",
    "objective/agent_todo",
]

EXPECTED_TEMPLATES = [
    "objective/purpose/_template.md",
    "objective/topic/_template.md",
    "objective/research_question/_template.md",
    "objective/decision/_template.md",
    "objective/research_question_proposal/_template.md",
    "objective/direction/_template.md",
    "objective/agent_todo/_template.md",
]


def _tree_snapshot(root: Path) -> dict[str, str]:
    """Map of relpath -> content for every file (for change detection)."""
    out: dict[str, str] = {}
    for p in sorted(root.rglob("*")):
        if p.is_file():
            out[str(p.relative_to(root)).replace("\\", "/")] = p.read_text(
                encoding="utf-8", errors="replace"
            )
    return out


# ---------------------------------------------------------------------------
# Test: apply creates all expected folders and templates
# ---------------------------------------------------------------------------

def test_apply_creates_structure() -> None:
    with tempfile.TemporaryDirectory() as td:
        vault = Path(td) / "vault"
        vault.mkdir()

        plan = obj_init.run_apply(vault)

        # All 7 subdirs must exist.
        for rel in EXPECTED_FOLDERS:
            assert (vault / rel).is_dir(), f"missing folder: {rel}"

        # All 7 templates must exist.
        for rel in EXPECTED_TEMPLATES:
            path = vault / rel
            assert path.exists(), f"missing template: {rel}"
            text = path.read_text(encoding="utf-8")
            assert "---" in text, f"template {rel} has no frontmatter"
            assert "type:" in text, f"template {rel} missing 'type:'"
            assert "For future Claude" in text, f"template {rel} missing 'For future Claude'"
            assert "ai-first" not in text or True  # not required in obj templates

        # objective/index.md must exist and have next_id block.
        index_path = vault / "objective" / "index.md"
        assert index_path.exists(), "objective/index.md not created"
        index_text = index_path.read_text(encoding="utf-8")
        assert "next_id:" in index_text, "index.md missing next_id block"
        assert "topic: 1" in index_text
        assert "research_question: 1" in index_text
        assert "direction: 1" in index_text

        # objective/hot.md must exist.
        hot_path = vault / "objective" / "hot.md"
        assert hot_path.exists(), "objective/hot.md not created"
        hot_text = hot_path.read_text(encoding="utf-8")
        assert "type: hot" in hot_text
        assert "written_by: research" in hot_text

        # Plan recorded what was created.
        assert len(plan.folders_created) == len(EXPECTED_FOLDERS), (
            f"plan.folders_created: {plan.folders_created}"
        )
        assert len(plan.templates_added) == len(EXPECTED_TEMPLATES), (
            f"plan.templates_added: {plan.templates_added}"
        )
        assert plan.index_created is True
        assert plan.hot_created is True

    print("PASS test_apply_creates_structure")


# ---------------------------------------------------------------------------
# Test: apply is idempotent (no overwrites on re-run)
# ---------------------------------------------------------------------------

def test_apply_is_idempotent() -> None:
    with tempfile.TemporaryDirectory() as td:
        vault = Path(td) / "vault"
        vault.mkdir()

        # First apply: creates everything.
        plan1 = obj_init.run_apply(vault)
        assert not plan1.is_noop(), "first apply should create files"

        snapshot_after_first = _tree_snapshot(vault)

        # Second apply: no-op.
        plan2 = obj_init.run_apply(vault)
        assert plan2.is_noop(), f"second apply should be no-op; plan: {vars(plan2)}"

        snapshot_after_second = _tree_snapshot(vault)
        assert snapshot_after_first == snapshot_after_second, (
            "idempotency violated: tree changed on re-apply"
        )

    print("PASS test_apply_is_idempotent")


# ---------------------------------------------------------------------------
# Test: apply does NOT overwrite an existing _template.md
# ---------------------------------------------------------------------------

def test_apply_does_not_overwrite_existing_template() -> None:
    with tempfile.TemporaryDirectory() as td:
        vault = Path(td) / "vault"
        (vault / "objective" / "topic").mkdir(parents=True)
        existing_content = "# custom topic template\n"
        (vault / "objective" / "topic" / "_template.md").write_text(
            existing_content, encoding="utf-8"
        )

        obj_init.run_apply(vault)

        kept = (vault / "objective" / "topic" / "_template.md").read_text(encoding="utf-8")
        assert kept == existing_content, (
            "apply overwrote an existing _template.md"
        )

    print("PASS test_apply_does_not_overwrite_existing_template")


# ---------------------------------------------------------------------------
# Test: dry-run makes ZERO live vault writes
# ---------------------------------------------------------------------------

def test_dry_run_makes_zero_live_writes() -> None:
    with tempfile.TemporaryDirectory() as td:
        vault = Path(td) / "vault"
        vault.mkdir()
        # put a canary file so we can track the snapshot
        (vault / "wiki").mkdir()
        (vault / "wiki" / "existing.md").write_text("# existing\n", encoding="utf-8")

        before = _tree_snapshot(vault)
        obj_init.run_dry_run(vault)
        after = _tree_snapshot(vault)

        assert before == after, (
            f"dry-run modified live vault; diff keys: "
            f"{set(after) - set(before)}"
        )

    print("PASS test_dry_run_makes_zero_live_writes")


# ---------------------------------------------------------------------------
# Test: template frontmatter fields are correct per type
# ---------------------------------------------------------------------------

def test_template_frontmatter_per_type() -> None:
    with tempfile.TemporaryDirectory() as td:
        vault = Path(td) / "vault"
        vault.mkdir()
        obj_init.run_apply(vault)

        cases = {
            "objective/purpose/_template.md": {
                "must_contain": ["type: purpose", "id: purpose", "status: active", "vault:"],
            },
            "objective/topic/_template.md": {
                "must_contain": ["type: topic", "id: T-NNNN", "status: active",
                                 "related_questions:"],
            },
            "objective/research_question/_template.md": {
                "must_contain": ["type: research_question", "id: Q-NNNN", 'solved: "no"',
                                 "priority:", "answer_ref:"],
            },
            "objective/decision/_template.md": {
                "must_contain": ["type: decision", "id: D-NNNN", "status: active", "scope:"],
            },
            "objective/research_question_proposal/_template.md": {
                "must_contain": ["type: research_question_proposal", "id: QP-NNNN",
                                 "written_by: research", "from_gap:", "status: pending"],
            },
            "objective/direction/_template.md": {
                "must_contain": ["type: direction", "id: DIR-NNNN",
                                 "written_by: research", "serves_question:",
                                 "priority:", "status: open",
                                 "reasoning_pattern", "expected_evidence",
                                 "seed_queries", "solves_when"],
            },
            "objective/agent_todo/_template.md": {
                "must_contain": ["type: agent_todo", "id: TODO-NNNN",
                                 "written_by: research", "status: open"],
            },
        }

        for rel, spec in cases.items():
            text = (vault / rel).read_text(encoding="utf-8")
            for needle in spec["must_contain"]:
                assert needle in text, (
                    f"template {rel} missing {needle!r}"
                )

    print("PASS test_template_frontmatter_per_type")


# ---------------------------------------------------------------------------
# Self-running fallback
# ---------------------------------------------------------------------------

def _run_all() -> int:
    test_apply_creates_structure()
    test_apply_is_idempotent()
    test_apply_does_not_overwrite_existing_template()
    test_dry_run_makes_zero_live_writes()
    test_template_frontmatter_per_type()
    print("\nAll obj_init tests PASSED.")
    return 0


if __name__ == "__main__":
    raise SystemExit(_run_all())
