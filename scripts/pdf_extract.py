#!/usr/bin/env python3
"""Extract a PDF to clean Markdown for LLM ingestion, via pymupdf4llm (PyMuPDF).

Fast, no ML download, handles multi-column academic layout. Use this in /obsidian-ingest
instead of vision-reading PDF pages — it is far cheaper and more reliable on text PDFs.
(Scanned/image PDFs are NOT OCR'd; for those use ocrmypdf+tesseract first.)

Usage:
  uv run -m scripts.pdf_extract <file.pdf>                 # Markdown to stdout
  uv run -m scripts.pdf_extract <file.pdf> --out notes.md  # …to a file
  uv run -m scripts.pdf_extract <file.pdf> --pages 0-4     # only these pages (0-indexed)
  uv run -m scripts.pdf_extract <file.pdf> --max-chars 60000
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


def _parse_pages(spec: str) -> list[int]:
    out: list[int] = []
    for part in spec.split(","):
        part = part.strip()
        if "-" in part:
            a, b = part.split("-", 1)
            out.extend(range(int(a), int(b) + 1))
        elif part:
            out.append(int(part))
    return out


def extract(pdf: Path, pages: list[int] | None = None) -> str:
    import pymupdf4llm
    kwargs = {}
    if pages:
        kwargs["pages"] = pages
    return pymupdf4llm.to_markdown(str(pdf), **kwargs)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("pdf", type=Path)
    ap.add_argument("--out", type=Path, help="write Markdown here instead of stdout")
    ap.add_argument("--pages", help="0-indexed page selection, e.g. '0-4' or '0,2,5'")
    ap.add_argument("--max-chars", type=int, default=0, help="truncate output to N chars (0 = no limit)")
    a = ap.parse_args()

    if not a.pdf.exists():
        print(f"no such file: {a.pdf}", file=sys.stderr)
        return 1
    try:
        md = extract(a.pdf, _parse_pages(a.pages) if a.pages else None)
    except Exception as e:  # noqa: BLE001 - surface any extraction error to the caller
        print(f"PDF extraction failed for {a.pdf}: {e}", file=sys.stderr)
        return 2

    if a.max_chars and len(md) > a.max_chars:
        md = md[: a.max_chars] + f"\n\n<!-- truncated at {a.max_chars} chars -->\n"
    if a.out:
        a.out.write_text(md)
        print(f"wrote {len(md)} chars -> {a.out}", file=sys.stderr)
    else:
        sys.stdout.write(md)
    return 0


if __name__ == "__main__":
    sys.exit(main())
