#!/usr/bin/env python3
"""wiki_cite_check.py - light citation fact-check for the Second Brain wiki.

Given a CLAIM made on a wiki page and a CITED SOURCE (the `[[sources/X]]` summary
page and/or the immutable `raw/<type>/...` file it points at), decide whether the
source SUPPORTS the claim. This is the LIGHT fact-check that backs the `wiki-cite`
skill - NOT the heavy grounding benchmark (that is Phase 6).

Two stages:
  1. EXCERPT match (local, $0): pull the most relevant excerpt(s) from the source
     text by lexical overlap with the claim. Keeps the judge prompt small and gives
     a deterministic fallback if the judge route is unreachable.
  2. JUDGE (Gemini Flash via the LiteLLM `validation` role): ask "does this excerpt
     support this claim?" and parse a structured supported/unsupported/unclear
     verdict. The proxy auto-logs cost via config/cost_callback.py.

Routing decision (consumed by the wiki-cite SKILL):
  - supported   -> keep the `[[sources/X]]` link as-is.
  - unsupported -> emit a `> [!gap]` callout and route the claim to wiki-reconcile.
  - unclear     -> treat like unsupported for routing (flag), but mark the lower
                   confidence so a human/wiki-reconcile can adjudicate.

Egress note: the ONLY network call is to the LOCAL LiteLLM proxy (localhost:4000),
which in turn calls Gemini Flash. No direct provider egress from this script. If the
proxy is unreachable, the script degrades to the excerpt-overlap heuristic and marks
the verdict `route=unreachable` so the caller can decide (it never silently passes).

Usage:
  wiki_cite_check.py --claim "<text>" --source-file <path> [--page <wiki page>]
  wiki_cite_check.py --claim "<text>" --source-text "<raw text>"
  echo '{"claim": "...", "source_text": "..."}' | wiki_cite_check.py --stdin

Output (JSON to stdout):
  {
    "claim": "...",
    "verdict": "supported" | "unsupported" | "unclear",
    "route": "keep" | "gap" | "unreachable",
    "confidence": "high" | "medium" | "low",
    "excerpt": "the matched source excerpt the judge saw",
    "reason": "one-line judge rationale",
    "judge_model_role": "validation"
  }

Exit codes:
  0 - ran (verdict in payload; check `route`)
  2 - usage error
"""

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

EXIT_OK = 0
EXIT_USAGE = 2

VALIDATION_ROLE = "validation"  # LiteLLM role -> Gemini Flash (config/litellm.yaml)
DEFAULT_PORT = "4000"
EXCERPT_WINDOW = 6      # sentences of context around the best-matching sentence
MAX_EXCERPT_CHARS = 2000


def log(msg):
    print(msg, file=sys.stderr)


_WORD = re.compile(r"[a-z0-9]+")


def _tokens(text):
    return set(_WORD.findall((text or "").lower()))


def _split_sentences(text):
    # Cheap, dependency-free sentence split WITHIN a paragraph.
    parts = re.split(r"(?<=[.!?])\s+", text or "")
    return [p.strip() for p in parts if p.strip()]


def best_excerpt(claim, source_text, window=EXCERPT_WINDOW, max_chars=MAX_EXCERPT_CHARS):
    """Return the highest lexical-overlap excerpt of the source for this claim.

    Deterministic and local ($0). Keeps the judge prompt small. The excerpt is
    windowed WITHIN the best-matching paragraph (blank-line delimited) so it never
    bleeds an unrelated paragraph into the judge prompt. If the source is short,
    returns the whole thing (trimmed to max_chars).
    """
    source_text = (source_text or "").strip()
    if not source_text:
        return ""
    claim_toks = _tokens(claim)
    if not claim_toks:
        return source_text[:max_chars]

    # 1. Pick the best paragraph by lexical overlap (paragraph = blank-line block).
    paragraphs = [p.strip() for p in re.split(r"\n{2,}", source_text) if p.strip()]
    if not paragraphs:
        return source_text[:max_chars]
    para_scored = sorted(
        ((len(claim_toks & _tokens(p)), idx) for idx, p in enumerate(paragraphs)),
        reverse=True,
    )
    best_para_overlap, best_para_i = para_scored[0]
    if best_para_overlap == 0:
        # No lexical anchor anywhere - hand the judge the head of the source so it
        # can still rule (and likely return unsupported).
        return source_text[:max_chars]
    para = paragraphs[best_para_i]

    # 2. Window sentences WITHIN that paragraph around the best-matching sentence.
    sentences = _split_sentences(para)
    if len(sentences) <= window:
        return para[:max_chars]
    sent_scored = sorted(
        ((len(claim_toks & _tokens(s)), idx) for idx, s in enumerate(sentences)),
        reverse=True,
    )
    best_i = sent_scored[0][1]
    lo = max(0, best_i - window // 2)
    hi = min(len(sentences), lo + window)
    excerpt = " ".join(sentences[lo:hi]).strip()
    return excerpt[:max_chars]


def _proxy_base():
    return f"http://localhost:{os.environ.get('LITELLM_PORT', DEFAULT_PORT)}"


def _master_key():
    # Prefer the env var; fall back to reading the project .env (parity with
    # tests/test_routing.sh). Never hard-code the key.
    key = os.environ.get("LITELLM_MASTER_KEY", "").strip()
    if key:
        return key
    code = Path(os.environ.get("CODE_PATH", "/home/jpietrak/second_brain"))
    env = code / ".env"
    if env.is_file():
        for line in env.read_text(encoding="utf-8", errors="ignore").splitlines():
            if line.startswith("LITELLM_MASTER_KEY="):
                return line.split("=", 1)[1].strip()
    return ""


JUDGE_SYSTEM = (
    "You are a strict citation checker. You are given a CLAIM from a knowledge wiki "
    "and an EXCERPT from the source the claim cites. Decide ONLY whether the excerpt "
    "supports the claim. Do not use outside knowledge. Answer with a compact JSON "
    "object: {\"verdict\": \"supported|unsupported|unclear\", \"reason\": \"<one short "
    "sentence>\"}. Use \"supported\" only if the excerpt states or directly entails the "
    "claim; \"unsupported\" if it contradicts or is unrelated; \"unclear\" if the "
    "excerpt is on-topic but does not settle the claim."
)


def call_validation_judge(claim, excerpt, timeout=60):
    """Call the LiteLLM `validation` role (Gemini Flash). Returns a dict
    {verdict, reason} or raises on transport/parse failure so the caller can fall
    back to the heuristic. Overridable in tests via WIKI_CITE_JUDGE_CMD (see main)."""
    base = _proxy_base()
    mk = _master_key()
    payload = {
        "model": VALIDATION_ROLE,
        "messages": [
            {"role": "system", "content": JUDGE_SYSTEM},
            {
                "role": "user",
                "content": f"CLAIM:\n{claim}\n\nEXCERPT:\n{excerpt}\n\nRespond with the JSON object only.",
            },
        ],
        "max_tokens": 200,
        "temperature": 0,
    }
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        f"{base}/v1/chat/completions",
        data=data,
        headers={
            "Authorization": f"Bearer {mk}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        body = json.loads(resp.read().decode("utf-8"))
    content = body["choices"][0]["message"]["content"]
    return _parse_judge_content(content)


def _parse_judge_content(content):
    """Parse the judge's reply into {verdict, reason}. Tolerant of code fences and
    surrounding prose: extract the first JSON object, else keyword-scan."""
    content = (content or "").strip()
    # Strip a leading ```json fence if present.
    fence = re.search(r"\{.*\}", content, re.DOTALL)
    if fence:
        try:
            obj = json.loads(fence.group(0))
            verdict = str(obj.get("verdict", "")).lower().strip()
            if verdict in ("supported", "unsupported", "unclear"):
                return {"verdict": verdict, "reason": str(obj.get("reason", "")).strip()}
        except (json.JSONDecodeError, AttributeError):
            pass
    low = content.lower()
    if "unsupported" in low:
        return {"verdict": "unsupported", "reason": content[:200]}
    if "unclear" in low:
        return {"verdict": "unclear", "reason": content[:200]}
    if "supported" in low:
        return {"verdict": "supported", "reason": content[:200]}
    return {"verdict": "unclear", "reason": "could not parse judge reply"}


def _heuristic_verdict(claim, excerpt):
    """Local fallback when the judge route is unreachable. Conservative: only calls
    a claim `supported` on strong lexical overlap; otherwise `unclear` (which routes
    to a gap flag, never a silent pass)."""
    claim_toks = _tokens(claim)
    exc_toks = _tokens(excerpt)
    if not claim_toks:
        return {"verdict": "unclear", "reason": "empty claim"}
    overlap = len(claim_toks & exc_toks) / max(1, len(claim_toks))
    if overlap >= 0.6:
        return {"verdict": "supported", "reason": f"lexical overlap {overlap:.2f} (heuristic, judge unreachable)"}
    return {"verdict": "unclear", "reason": f"lexical overlap {overlap:.2f} (heuristic, judge unreachable)"}


def _route_for(verdict, judge_reachable):
    if not judge_reachable:
        return "unreachable"
    return "keep" if verdict == "supported" else "gap"


def _confidence_for(verdict, judge_reachable):
    if not judge_reachable:
        return "low"
    if verdict == "supported":
        return "high"
    if verdict == "unsupported":
        return "high"
    return "medium"  # unclear


def check_claim(claim, source_text, judge_fn=None, timeout=60):
    """Pure function: run excerpt-match + judge, return the result dict.

    `judge_fn(claim, excerpt)` is injectable for hermetic tests. When None, the real
    LiteLLM `validation` route is used; on any transport error it degrades to the
    local heuristic and marks `route=unreachable`.
    """
    excerpt = best_excerpt(claim, source_text)
    judge_reachable = True
    if judge_fn is not None:
        result = judge_fn(claim, excerpt)
    else:
        try:
            result = call_validation_judge(claim, excerpt, timeout=timeout)
        except (urllib.error.URLError, OSError, KeyError, ValueError, json.JSONDecodeError) as e:
            log(f"WARN: validation route unreachable ({type(e).__name__}: {e}); using local heuristic")
            judge_reachable = False
            result = _heuristic_verdict(claim, excerpt)

    verdict = result.get("verdict", "unclear")
    return {
        "claim": claim,
        "verdict": verdict,
        "route": _route_for(verdict, judge_reachable),
        "confidence": _confidence_for(verdict, judge_reachable),
        "excerpt": excerpt,
        "reason": result.get("reason", ""),
        "judge_model_role": VALIDATION_ROLE,
    }


def _read_source(args):
    if args.source_text is not None:
        return args.source_text
    if args.source_file:
        p = Path(args.source_file)
        if not p.is_file():
            log(f"ERR: source file not found: {p}")
            sys.exit(EXIT_USAGE)
        return p.read_text(encoding="utf-8", errors="ignore")
    return ""


def main(argv=None):
    parser = argparse.ArgumentParser(description="Light citation fact-check via the validation role.")
    parser.add_argument("--claim", help="The wiki claim to check.")
    parser.add_argument("--source-file", help="Path to the cited source (raw/... or wiki/sources/...).")
    parser.add_argument("--source-text", help="Inline source text (alternative to --source-file).")
    parser.add_argument("--page", help="Optional: the wiki page the claim lives on (echoed in output).")
    parser.add_argument("--stdin", action="store_true",
                        help="Read a JSON object {claim, source_text|source_file} from stdin.")
    parser.add_argument("--timeout", type=int, default=60, help="Judge call timeout (seconds).")
    args = parser.parse_args(argv)

    if args.stdin:
        payload = json.loads(sys.stdin.read())
        claim = payload.get("claim", "")
        if "source_text" in payload:
            source_text = payload["source_text"]
        elif "source_file" in payload:
            source_text = Path(payload["source_file"]).read_text(encoding="utf-8", errors="ignore")
        else:
            source_text = ""
        page = payload.get("page")
    else:
        if not args.claim:
            parser.error("--claim is required (unless --stdin)")
        claim = args.claim
        source_text = _read_source(args)
        page = args.page

    # WIKI_CITE_JUDGE_CMD is a hermetic-test hook: a python expression is not allowed,
    # but tests import check_claim() directly with a judge_fn, so no shell hook needed.
    out = check_claim(claim, source_text, timeout=args.timeout)
    if page:
        out["page"] = page
    print(json.dumps(out, indent=2, ensure_ascii=False))
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
