#!/usr/bin/env python3
"""Hermetic test for scripts/wiki_ingest_defuddle.py.

Mocks `defuddle` on PATH with a fake script, runs the helper against a mktemp out-dir, and
asserts the cleaned markdown is routed to raw/articles/ with a provenance header. No network,
no real defuddle-cli, no live vault. Run: .venv/bin/python -m tests.test_wiki_ingest_defuddle
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from scripts import wiki_ingest_defuddle as wd  # noqa: E402


def _make_fake_defuddle(bindir: Path, body: str) -> None:
    """Drop a fake `defuddle` executable that echoes a fixed body for any arg."""
    fake = bindir / "defuddle"
    fake.write_text("#!/usr/bin/env bash\nprintf '%s\\n' " + repr(body) + "\n")
    fake.chmod(0o755)


def test_slugify() -> None:
    assert wd.slugify("https://example.com/blog/My_Great-Post?utm=1#frag") == "my-great-post"
    assert wd.slugify("https://example.com/") == "example-com"
    assert wd.slugify("./page.html") == "page"
    # no traversal / leading dots survive
    s = wd.slugify("https://x.com/../../etc/passwd")
    assert "/" not in s and not s.startswith(".")
    print("ok test_slugify")


def test_clean_to_raw_routes_and_headers() -> None:
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        bindir = td / "bin"
        bindir.mkdir()
        outdir = td / "raw" / "articles"
        _make_fake_defuddle(bindir, "# Clean Title\n\nClean body text.")
        old_path = os.environ.get("PATH", "")
        os.environ["PATH"] = f"{bindir}{os.pathsep}{old_path}"
        try:
            out = wd.clean_to_raw("https://example.com/article", out_dir=outdir)
        finally:
            os.environ["PATH"] = old_path
        assert out.exists(), "output file not written"
        assert out.parent == outdir, f"routed to wrong dir: {out.parent}"
        assert out.name.startswith("article-") and out.suffix == ".md"
        text = out.read_text()
        assert text.startswith("---\n"), "missing frontmatter header"
        assert "source_url: https://example.com/article" in text
        assert "source_type: article" in text
        assert "fetched:" in text
        assert "# Clean Title" in text and "Clean body text." in text
        print("ok test_clean_to_raw_routes_and_headers")


def test_immutable_no_overwrite() -> None:
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        bindir = td / "bin"
        bindir.mkdir()
        outdir = td / "raw" / "articles"
        _make_fake_defuddle(bindir, "body one")
        old_path = os.environ.get("PATH", "")
        os.environ["PATH"] = f"{bindir}{os.pathsep}{old_path}"
        try:
            a = wd.clean_to_raw("https://example.com/dup", out_dir=outdir)
            b = wd.clean_to_raw("https://example.com/dup", out_dir=outdir)
        finally:
            os.environ["PATH"] = old_path
        assert a != b, "second run overwrote the first (raw must be immutable)"
        assert a.exists() and b.exists()
        print("ok test_immutable_no_overwrite")


def test_missing_defuddle_exit3() -> None:
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        outdir = td / "raw" / "articles"
        # PATH with no defuddle
        old_path = os.environ.get("PATH", "")
        os.environ["PATH"] = str(td / "empty")
        try:
            rc = wd.main(["https://example.com/x", "--out-dir", str(outdir)])
        finally:
            os.environ["PATH"] = old_path
        assert rc == 3, f"expected exit 3 when defuddle absent, got {rc}"
        print("ok test_missing_defuddle_exit3")


if __name__ == "__main__":
    test_slugify()
    test_clean_to_raw_routes_and_headers()
    test_immutable_no_overwrite()
    test_missing_defuddle_exit3()
    print("All wiki_ingest_defuddle tests passed")
