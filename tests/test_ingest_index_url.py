#!/usr/bin/env python3
"""Hermetic tests for the URL field and _valid_url gate in agents/ingest_index.py.

Tests:
  1. _valid_url  -- true for http/https-with-netloc; false for empty, bare ids, ftp, relative.
  2. has_working_url -- delegates to _valid_url via row["url"].
  3. enqueue(url=...) -- explicit url= is stored; without url= leaves "" (or unchanged).
  4. enqueue with a url-form ident still sets url from ident derivation.
  5. enqueue re-enqueue: non-empty url= updates empty url; does NOT clobber existing url.
  6. "url": "" is present in _V3_DEFAULTS (additive migration field).
  7. _migrate() backfills "url": "" on old rows that lack it.

No network, no LLM, $0.
Run: .venv/bin/python -m pytest tests/test_ingest_index_url.py -q
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

import agents.ingest_index as ii


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_vault(tmp_path: Path) -> Path:
    """Minimal vault skeleton (meta/ dir + empty ingest_index.json)."""
    (tmp_path / "meta").mkdir(parents=True, exist_ok=True)
    return tmp_path


# ---------------------------------------------------------------------------
# 1. _valid_url
# ---------------------------------------------------------------------------

class TestValidUrl:

    def test_http_url_is_valid(self):
        assert ii._valid_url("http://example.com/page") is True

    def test_https_url_is_valid(self):
        assert ii._valid_url("https://arxiv.org/abs/2509.17357") is True

    def test_https_url_with_path_is_valid(self):
        assert ii._valid_url("https://example.com/path/to/resource?q=1") is True

    def test_empty_string_is_invalid(self):
        assert ii._valid_url("") is False

    def test_bare_arxiv_id_is_invalid(self):
        assert ii._valid_url("arxiv:2509.17357") is False

    def test_not_a_url_string_is_invalid(self):
        assert ii._valid_url("not a url") is False

    def test_ftp_scheme_is_invalid(self):
        assert ii._valid_url("ftp://files.example.com/data.csv") is False

    def test_relative_path_is_invalid(self):
        assert ii._valid_url("//example.com/relative") is False

    def test_scheme_only_no_netloc_is_invalid(self):
        assert ii._valid_url("https://") is False

    def test_mailto_is_invalid(self):
        assert ii._valid_url("mailto:user@example.com") is False

    def test_http_no_netloc_is_invalid(self):
        # A URL with scheme but empty netloc after parsing
        assert ii._valid_url("http:///path/only") is False


# ---------------------------------------------------------------------------
# 2. has_working_url
# ---------------------------------------------------------------------------

class TestHasWorkingUrl:

    def test_row_with_valid_url_returns_true(self):
        row = {"url": "https://arxiv.org/abs/2509.17357"}
        assert ii.has_working_url(row) is True

    def test_row_with_empty_url_returns_false(self):
        row = {"url": ""}
        assert ii.has_working_url(row) is False

    def test_row_without_url_key_returns_false(self):
        row = {}
        assert ii.has_working_url(row) is False

    def test_row_with_none_url_returns_false(self):
        row = {"url": None}
        assert ii.has_working_url(row) is False

    def test_row_with_http_url_returns_true(self):
        row = {"url": "http://blog.example.com/post"}
        assert ii.has_working_url(row) is True


# ---------------------------------------------------------------------------
# 3. enqueue -- url= parameter stores the URL
# ---------------------------------------------------------------------------

class TestEnqueueUrl:

    def test_enqueue_with_explicit_url_stores_it(self, tmp_path, monkeypatch):
        """enqueue(url='https://...') stores the URL in the row."""
        _make_vault(tmp_path)
        monkeypatch.setenv("VAULT_PATH", str(tmp_path))

        row = ii.enqueue(
            "arxiv:2509.17357",
            url="https://arxiv.org/abs/2509.17357",
            title="Test paper",
        )
        assert row["url"] == "https://arxiv.org/abs/2509.17357"

        # Also confirmed on disk.
        data = json.loads((tmp_path / "meta" / "ingest_index.json").read_text())
        disk_row = data["sources"]["arxiv:2509.17357"]
        assert disk_row["url"] == "https://arxiv.org/abs/2509.17357"

    def test_enqueue_without_url_leaves_empty(self, tmp_path, monkeypatch):
        """enqueue() without url= on a bare explicit id leaves url as ''."""
        _make_vault(tmp_path)
        monkeypatch.setenv("VAULT_PATH", str(tmp_path))

        # Use an explicit id that has no URL derivable from it.
        row = ii.enqueue("id:some-paper-id", title="No URL Paper")
        assert row["url"] == ""

    def test_enqueue_with_url_ident_derives_url(self, tmp_path, monkeypatch):
        """An https:// ident is normalized to url:<canonical> and the URL is stored."""
        _make_vault(tmp_path)
        monkeypatch.setenv("VAULT_PATH", str(tmp_path))

        row = ii.enqueue("https://blog.example.com/post", title="Blog Post")
        # The url should be the canonical form of the ident.
        assert row["url"].startswith("https://blog.example.com")

    def test_enqueue_explicit_url_wins_over_derived(self, tmp_path, monkeypatch):
        """Explicit url= overrides the URL derived from the ident."""
        _make_vault(tmp_path)
        monkeypatch.setenv("VAULT_PATH", str(tmp_path))

        row = ii.enqueue(
            "arxiv:2509.17357",
            url="https://arxiv.org/abs/2509.17357",
        )
        assert row["url"] == "https://arxiv.org/abs/2509.17357"

    def test_enqueue_none_url_leaves_empty(self, tmp_path, monkeypatch):
        """enqueue(url=None) on a new row leaves url empty (None is treated as missing)."""
        _make_vault(tmp_path)
        monkeypatch.setenv("VAULT_PATH", str(tmp_path))

        row = ii.enqueue("id:no-url-none", url=None, title="None URL")
        assert row["url"] == ""


# ---------------------------------------------------------------------------
# 4. enqueue re-enqueue: url update logic
# ---------------------------------------------------------------------------

class TestEnqueueUrlUpdate:

    def test_reenqueue_with_url_fills_empty(self, tmp_path, monkeypatch):
        """Re-enqueue with url= updates the row when url was previously empty."""
        _make_vault(tmp_path)
        monkeypatch.setenv("VAULT_PATH", str(tmp_path))

        # First enqueue without URL.
        ii.enqueue("id:fill-me", title="Fill Me")
        # Second enqueue with URL fills it in.
        row = ii.enqueue("id:fill-me", url="https://example.com/fill-me")
        assert row["url"] == "https://example.com/fill-me"

    def test_reenqueue_without_url_does_not_clobber_existing(self, tmp_path, monkeypatch):
        """Re-enqueue without url= does NOT overwrite an existing URL."""
        _make_vault(tmp_path)
        monkeypatch.setenv("VAULT_PATH", str(tmp_path))

        ii.enqueue("id:keep-url", url="https://example.com/original", title="Keep URL")
        # Re-enqueue without url= -- original must survive.
        row = ii.enqueue("id:keep-url", title="Updated Title")
        assert row["url"] == "https://example.com/original", (
            f"URL was clobbered: {row['url']!r}"
        )

    def test_reenqueue_with_none_url_does_not_clobber_existing(self, tmp_path, monkeypatch):
        """Re-enqueue with url=None does NOT overwrite an existing URL."""
        _make_vault(tmp_path)
        monkeypatch.setenv("VAULT_PATH", str(tmp_path))

        ii.enqueue("id:keep-url2", url="https://example.com/keep", title="Keep URL 2")
        row = ii.enqueue("id:keep-url2", url=None)
        assert row["url"] == "https://example.com/keep", (
            f"URL was clobbered by None: {row['url']!r}"
        )

    def test_reenqueue_with_new_url_updates_when_empty(self, tmp_path, monkeypatch):
        """Re-enqueue with a different url= on a row that has no URL fills it."""
        _make_vault(tmp_path)
        monkeypatch.setenv("VAULT_PATH", str(tmp_path))

        ii.enqueue("id:was-empty", title="Was Empty")
        row = ii.enqueue("id:was-empty", url="https://example.com/now-has-url")
        assert row["url"] == "https://example.com/now-has-url"


# ---------------------------------------------------------------------------
# 5. _V3_DEFAULTS includes "url": ""
# ---------------------------------------------------------------------------

class TestV3Defaults:

    def test_url_in_v3_defaults(self):
        """'url' must be present in _V3_DEFAULTS (additive migration)."""
        assert "url" in ii._V3_DEFAULTS
        assert ii._V3_DEFAULTS["url"] == ""

    def test_migrate_backfills_url_on_old_rows(self):
        """_migrate() adds url='' to rows that lack it (v2->v3 migration)."""
        old_data = {
            "version": 2,
            "vault": "TestVault",
            "sources": {
                "arxiv:1234.56789": {
                    "id": "arxiv:1234.56789",
                    "status": "pending",
                    "title": "Old Row",
                    # NOTE: no "url" key (simulates a pre-url row)
                },
            },
        }
        # _migrate is called by _load; call it directly here.
        result = ii._migrate(old_data)
        row = result["sources"]["arxiv:1234.56789"]
        assert "url" in row, "_migrate() must add 'url' key"
        assert row["url"] == "", f"Migrated url must be empty string, got: {row['url']!r}"
