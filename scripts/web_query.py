#!/usr/bin/env python3
"""web_query.py -- Crawl-time QUERY REFORMULATION for Second Brain v0.2 Phase 4A.

Reformulates crawl-plan targets' queries at crawl time, using:
  - Design B (LLM path): one batched LLM call via claude_agent.sh, prompt in
    pipeline_prompts.WEB_QUERY_REFORMULATION_PROMPT.  Returns per-engine query
    variants shaped for each engine's strengths.
  - Design A (deterministic fallback): term-extraction from expected_evidence +
    gap title/missing + PURPOSE.  Always available, $0, offline.

The crawl always gets queries (the fallback guarantees that even when LLM is
disabled or fails).
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# sys.path shim: scripts/ and repo root must be importable
# ---------------------------------------------------------------------------
_SCRIPTS_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _SCRIPTS_DIR.parent
for _p in (str(_SCRIPTS_DIR), str(_REPO_ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

# ---------------------------------------------------------------------------
# TODO (Design C, selective escalation):
# At the top of reformulate(), eventually reformulate ONLY targets flagged with
# a LOW retrieval/learning score (origin gap/topic with low
# learned["calibration"], repeated rejects, or thin prior yield), instead of
# ALL targets every crawl, to save LLM calls and focus effort.  This requires
# the learning signal to warm up (several crawl cycles with accept/reject
# decisions) before the calibration scores are meaningful.  For now reformulate
# ALL enabled targets unconditionally.  When implementing Design C:
#   - Add a _needs_reformulation(target, learned) -> bool helper.
#   - Gate the LLM sub-call (and the wiki-context read) on that flag.
#   - Targets that pass the gate keep their existing queries untouched
#     (set reformulation.method = "unchanged").
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Engine list
# ---------------------------------------------------------------------------

_ALL_ENGINES = ("arxiv", "semantic_scholar", "web", "forum")

# ---------------------------------------------------------------------------
# Deterministic stopwords (shared with web_decision + agent_learn patterns)
# ---------------------------------------------------------------------------

_EN_STOPWORDS: frozenset[str] = frozenset(
    {
        "that", "this", "with", "from", "have", "been", "will",
        "were", "they", "them", "than", "then", "also", "into",
        "only", "some", "such", "each", "both", "very", "more",
        "most", "over", "under", "does", "what", "when", "where",
        "which", "their", "there", "these", "those", "would", "could",
        "should", "about", "after", "before", "between", "across",
        "through", "other", "within", "system", "based", "using",
        "used", "need", "page", "pages", "paper", "papers",
        "source", "sources", "how", "why", "who", "and", "the",
        "for", "not", "but", "all", "any", "new", "via", "can",
        "its", "has", "had", "was", "are", "per", "see",
    }
)

# Domain tokens that are too ubiquitous to be discriminative query terms.
_DOMAIN_STOPLIST: frozenset[str] = frozenset(
    {
        "optical", "iris", "tetra", "vault", "model", "cache",
        "inference", "disaggregation", "gpu", "llm", "scale",
        "design", "cost", "data", "system", "method", "work",
        "result", "results", "approach", "analysis", "study",
        "show", "shows", "propose", "proposed", "present", "presented",
        "novel", "deep", "large", "high",
    }
)


def _tokenize_terms(text: str) -> list[str]:
    """Lowercase, split on non-alphanumerics, drop short/stop/domain tokens.

    Returns tokens in first-occurrence order (no dedup) so phrase proximity
    is preserved for multi-word extraction.
    """
    tokens = re.split(r"[^a-z0-9]+", text.lower())
    out = []
    for t in tokens:
        if (
            len(t) >= 4
            and t not in _EN_STOPWORDS
            and t not in _DOMAIN_STOPLIST
        ):
            out.append(t)
    return out


def _bigrams(tokens: list[str]) -> list[str]:
    """Emit consecutive bigrams from a token list."""
    return [f"{tokens[i]} {tokens[i+1]}" for i in range(len(tokens) - 1)]


# ---------------------------------------------------------------------------
# Wiki-context delta helper
# ---------------------------------------------------------------------------

_WIKILINK_RE = re.compile(r"\[\[([^\]|#]+?)(?:\|[^\]]+)?\]\]")


def _resolve_wikilink(link_target: str, vault_root: str) -> "Path | None":
    """Turn a wikilink target like wiki/concepts/foo into an absolute path.

    Tries:
      1. <vault_root>/<link_target>.md  (exact path-qualified wikilink)
      2. <vault_root>/<link_target>     (no extension)
    Returns None when the file is missing.
    """
    vp = Path(vault_root)
    link_target = link_target.strip().strip("[]").strip()
    stem = link_target.removesuffix(".md")
    candidate = vp / f"{stem}.md"
    if candidate.exists():
        return candidate
    candidate2 = vp / stem
    if candidate2.exists():
        return candidate2
    return None


def _wiki_context(target: dict, vault_root: str) -> str:
    """Build a short already-known / still-missing snippet for the target.

    Reads:
      - The gap missing field (concise statement of what is absent).
      - Each wiki page listed in shows_up_in (the origin gap frontmatter
        field), reading the first 150 chars of body text as a context sample.

    Returns a string capped at ~400 characters.  Returns "" gracefully when
    any file is missing or the target has no gap info.
    """
    gap = target.get("_gap") or {}
    if not gap:
        return ""

    parts: list[str] = []

    # 1. Gap missing text (concise statement of what is absent)
    missing = gap.get("missing", "").strip()
    if missing:
        parts.append(f"Missing: {missing[:200]}")

    # 2. shows_up_in wiki pages -- read a snippet from each
    shows_up_in_raw = gap.get("shows_up_in", "")
    if isinstance(shows_up_in_raw, str) and shows_up_in_raw:
        # Parse comma-separated wikilinks like "[[wiki/x]], [[wiki/y]]"
        links = _WIKILINK_RE.findall(shows_up_in_raw)
        for link in links[:3]:  # cap at 3 pages
            resolved = _resolve_wikilink(link, vault_root)
            if resolved and resolved.exists():
                try:
                    text = resolved.read_text(encoding="utf-8", errors="ignore")
                    # Strip frontmatter, take first 150 chars of body
                    body = re.sub(r"^---.*?---\n", "", text, flags=re.DOTALL).strip()
                    snippet = body[:150].replace("\n", " ").strip()
                    if snippet:
                        parts.append(f"Known ({link}): {snippet}")
                except OSError:
                    pass

    result = " | ".join(parts)
    return result[:400]


# ---------------------------------------------------------------------------
# Deterministic fallback (Design A)
# ---------------------------------------------------------------------------

def _deterministic_queries(
    target: dict,
    purpose: str,
) -> "dict[str, list[str]]":
    """Build per-engine query variants deterministically from the target.

    Term extraction:
      - expected_evidence field
      - gap missing + title fields (or dir targets_gap)
      - PURPOSE (first 200 chars, as context anchor)
    Emits bigrams first (more distinctive), then top unigrams.

    Per-engine variants:
      arxiv            -- top 4 terms joined with spaces (precise / title-like)
      semantic_scholar -- natural-language question from gap title + top terms
      web              -- natural-language question (broader phrasing)
      forum            -- top 3-4 keyword tokens (short, forum-friendly)
    """
    evidence = target.get("expected_evidence", "")
    gap = target.get("_gap") or {}
    direction = target.get("_dir") or {}

    gap_title = gap.get("title", "") or direction.get("targets_gap", "")
    gap_missing = gap.get("missing", "")

    raw_text = f"{gap_title} {gap_missing} {evidence}"
    tokens = _tokenize_terms(raw_text)

    # Bigrams from the combined text (distinctive multi-word phrases)
    bigrams = _bigrams(tokens)

    # Deduplicate while preserving order
    seen: set[str] = set()
    ranked: list[str] = []
    for phrase in bigrams + tokens:
        if phrase not in seen:
            seen.add(phrase)
            ranked.append(phrase)

    # Also add a couple of terms from PURPOSE as a relevance anchor
    purpose_tokens = _tokenize_terms(purpose[:200])
    for t in purpose_tokens[:3]:
        if t not in seen:
            seen.add(t)
            ranked.append(t)

    # Build per-engine query lists
    top_terms = ranked[:8]  # working set
    unigrams = [t for t in top_terms if " " not in t]
    phrases = [t for t in top_terms if " " in t]

    # arxiv: precise term join (title-like, bigrams first then unigrams)
    arxiv_parts = (phrases[:2] + unigrams)[:4]
    arxiv_q = " ".join(arxiv_parts) if arxiv_parts else (gap_title or "")
    arxiv_queries = [arxiv_q] if arxiv_q else []

    # semantic_scholar + web: natural-language question
    nat_lang_base = gap_title.strip() if gap_title.strip() else evidence[:80].strip()
    if nat_lang_base:
        support = " ".join(unigrams[:3])
        if support:
            nat_lang_q = f"{nat_lang_base} {support}"
        else:
            nat_lang_q = nat_lang_base
        ss_queries = [nat_lang_q[:120]]
        if nat_lang_base.lower().startswith("what"):
            web_q = nat_lang_base
        else:
            web_q = f"what is {nat_lang_base.lower()}"
        web_queries = [web_q[:120]]
    else:
        ss_queries = [" ".join(unigrams[:4])] if unigrams else []
        web_queries = list(ss_queries)

    # forum: short keyword string (no question framing)
    forum_parts = unigrams[:4]
    forum_queries = [" ".join(forum_parts)] if forum_parts else (
        [gap_title[:80]] if gap_title else []
    )

    result: dict[str, list[str]] = {}
    if arxiv_queries:
        result["arxiv"] = arxiv_queries
    if ss_queries:
        result["semantic_scholar"] = ss_queries
    if web_queries:
        result["web"] = web_queries
    if forum_queries:
        result["forum"] = forum_queries

    # Only include engines in fillable_by (or all if fillable_by is empty)
    fillable = target.get("fillable_by") or list(_ALL_ENGINES)
    if fillable:
        result = {k: v for k, v in result.items() if k in fillable}

    # Ensure at least one engine has queries (last-resort fallback)
    if not result and gap_title:
        result = {"web": [gap_title[:120]]}

    return result


# ---------------------------------------------------------------------------
# LLM response parser
# ---------------------------------------------------------------------------

_FENCE_RE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL)
_BARE_JSON_RE = re.compile(r"\{.*\}", re.DOTALL)


def _parse_llm_queries(text: str) -> "dict[str, dict] | None":
    """Parse LLM output for per-target query dicts.

    Expected format (fenced JSON block):
      The JSON object maps target_id -> {queries_by_engine: {...}, rationale: str}

    Returns a dict mapping target_id -> {queries_by_engine, rationale}
    or None on ANY parse/shape error (caller falls back per target).
    """
    if not text:
        return None

    # Try fenced JSON block first, then bare object
    m = _FENCE_RE.search(text)
    if m:
        json_str = m.group(1)
    else:
        m2 = _BARE_JSON_RE.search(text)
        if not m2:
            return None
        json_str = m2.group(0)

    try:
        data = json.loads(json_str)
    except json.JSONDecodeError:
        return None

    if not isinstance(data, dict):
        return None

    # Validate shape
    result: dict[str, dict] = {}
    for tid, val in data.items():
        if not isinstance(val, dict):
            return None  # shape error -> full fallback
        qbe = val.get("queries_by_engine")
        if not isinstance(qbe, dict):
            return None
        # Each engine value must be a list of strings
        for eng_val in qbe.values():
            if not isinstance(eng_val, list):
                return None
        result[tid] = {
            "queries_by_engine": {
                k: [str(q) for q in v]
                for k, v in qbe.items()
            },
            "rationale": str(val.get("rationale", "")),
        }

    return result if result else None


# ---------------------------------------------------------------------------
# LLM invocation via claude_agent.sh
# ---------------------------------------------------------------------------

def _invoke_llm(prompt: str, agent: str) -> str:
    """Call claude_agent.sh --agent <agent> with the prompt text.

    Invocation: scripts/claude_agent.sh --agent <agent> <prompt>

    The script returns only the .result text on stdout (no JSON wrapper).
    Returns the stdout string, or "" on any failure.
    """
    script = _REPO_ROOT / "scripts" / "claude_agent.sh"
    if not script.exists():
        return ""
    try:
        result = subprocess.run(
            ["bash", str(script), "--agent", agent, prompt],
            capture_output=True,
            text=True,
            timeout=120,
        )
        return result.stdout.strip()
    except Exception:
        return ""


# ---------------------------------------------------------------------------
# Public API: reformulate
# ---------------------------------------------------------------------------

def reformulate(
    targets: list[dict],
    *,
    purpose: str,
    vault_root: str,
    learned: "dict | None" = None,
    use_llm: bool = True,
    agent: str = "web",
    max_per_engine: int = 3,
) -> list[dict]:
    """Reformulate crawl-plan targets queries at crawl time.

    For each target:
      1. Build wiki-context delta (_wiki_context).
      2. LLM path (use_llm=True, claude_agent.sh available): batch all targets
         into one prompt; parse the fenced-JSON response.  Falls back per target
         on ANY failure.
      3. Deterministic fallback: term-extract from expected_evidence + gap
         metadata + PURPOSE; emit per-engine query variants.

    Returns new target dicts (copies, inputs not mutated) with:
      - queries           flat list (back-compat, union of all engines)
      - queries_by_engine {engine: [q, ...]} (new; capped at max_per_engine)
      - reformulation     {method: llm|fallback, old_queries: [...], rationale: str}

    Args:
      targets:        list of target dicts from web_decision.build_plan.
      purpose:        vault PURPOSE text (short statement of vault mission).
      vault_root:     absolute path to the vault root directory.
      learned:        optional learned dict from agent_learn.learn(); used to
                      surface reject keywords in the prompt.
      use_llm:        when False, skip LLM entirely (deterministic fallback only).
      agent:          --agent tag for claude_agent.sh cost attribution.
      max_per_engine: cap on queries per engine.
    """
    # TODO (Design C): reformulate ONLY targets with low learning/retrieval
    # scores -- see module-level TODO block at top of file.
    if not targets:
        return []

    # --- collect wiki-context deltas up front (cheap, offline) ---
    wiki_contexts: dict[str, str] = {}
    for t in targets:
        tid = t.get("target_id", "")
        wiki_contexts[tid] = _wiki_context(t, vault_root)

    # --- LLM path ---
    llm_results: "dict[str, dict] | None" = None
    if use_llm:
        llm_results = _try_llm_reformulate(
            targets,
            purpose=purpose,
            wiki_contexts=wiki_contexts,
            learned=learned,
            agent=agent,
            max_per_engine=max_per_engine,
        )
        # llm_results is None or partial (missing some target_ids) on failure

    # --- build output targets ---
    out: list[dict] = []
    for t in targets:
        tid = t.get("target_id", "")
        old_queries = list(t.get("queries", []))

        llm_entry = (llm_results or {}).get(tid)
        if llm_entry is not None:
            qbe = _cap_engines(llm_entry["queries_by_engine"], max_per_engine)
            rationale = llm_entry.get("rationale", "")
            method = "llm"
        else:
            qbe = _cap_engines(
                _deterministic_queries(t, purpose), max_per_engine
            )
            rationale = "deterministic fallback: term-extraction from gap metadata + PURPOSE"
            method = "fallback"

        # Flat queries list: union of all engine lists (order: arxiv, ss, web, forum)
        flat: list[str] = []
        seen_flat: set[str] = set()
        for eng in _ALL_ENGINES:
            for q in qbe.get(eng, []):
                if q and q not in seen_flat:
                    seen_flat.add(q)
                    flat.append(q)

        new_t = {k: v for k, v in t.items()}
        new_t["queries"] = flat
        new_t["queries_by_engine"] = qbe
        new_t["reformulation"] = {
            "method": method,
            "old_queries": old_queries,
            "rationale": rationale,
        }
        out.append(new_t)

    return out


# ---------------------------------------------------------------------------
# LLM reformulation helpers
# ---------------------------------------------------------------------------

def _try_llm_reformulate(
    targets: list[dict],
    *,
    purpose: str,
    wiki_contexts: "dict[str, str]",
    learned: "dict | None",
    agent: str,
    max_per_engine: int,
) -> "dict[str, dict] | None":
    """Build + send one batched LLM prompt; return parsed results or None."""
    from scripts.prompts.pipeline_prompts import WEB_QUERY_REFORMULATION_PROMPT

    # Build learned reject-keyword blurb
    learned_terms_blurb = ""
    if learned:
        keywords = (learned.get("reject_patterns") or {}).get("keywords", [])
        if keywords:
            learned_terms_blurb = (
                "AVOID these terms (learned reject keywords, appear in rejected results): "
                + ", ".join(keywords[:15])
            )

    # Build per-target blocks
    target_blocks = []
    for t in targets:
        tid = t.get("target_id", "")
        origin_ids = ", ".join(t.get("origin_ids", []))
        expected = t.get("expected_evidence", "")[:200]
        old_queries = t.get("queries", [])[:4]
        fillable = t.get("fillable_by") or list(_ALL_ENGINES)
        wiki_ctx = wiki_contexts.get(tid, "")

        block = (
            f"TARGET: {tid}\n"
            f"  origin_ids: {origin_ids}\n"
            f"  fillable_by (active engines): {', '.join(fillable)}\n"
            f"  expected_evidence: {expected}\n"
            f"  wiki_context_delta: {wiki_ctx or '(not available)'}\n"
            f"  old_seed_queries: {json.dumps(old_queries)}\n"
        )
        target_blocks.append(block)

    target_section = "\n".join(target_blocks)
    engine_rules = (
        "Engine phrasing rules:\n"
        "  arxiv:            precise author/title/term phrase "
        "(e.g. disaggregated KV cache prefill decode separation)\n"
        "  semantic_scholar: natural-language research question "
        "(e.g. how does KV cache disaggregation reduce decode latency?)\n"
        "  web:              broad natural-language question or keyword phrase\n"
        "  forum:            2-4 keyword tokens (no question framing)\n"
    )

    prompt = WEB_QUERY_REFORMULATION_PROMPT.format(
        purpose=purpose[:300],
        max_per_engine=max_per_engine,
        engine_rules=engine_rules,
        target_section=target_section,
        learned_terms_blurb=learned_terms_blurb or "(none)",
    )

    raw_output = _invoke_llm(prompt, agent)
    if not raw_output:
        return None

    return _parse_llm_queries(raw_output)


def _cap_engines(
    qbe: "dict[str, list[str]]", max_per_engine: int
) -> "dict[str, list[str]]":
    """Cap each engine list to max_per_engine entries."""
    return {k: v[:max_per_engine] for k, v in qbe.items() if v}


# ---------------------------------------------------------------------------
# Vault root resolution (mirrors web_decision pattern)
# ---------------------------------------------------------------------------

def _resolve_vault_root(vault_arg: "str | None") -> str:
    """Resolve vault root from CLI arg, env, or vault_config."""
    import os
    if vault_arg:
        p = Path(vault_arg)
        if p.is_dir():
            return str(p)
        try:
            from agents import vault_config as vc
            return str(vc.vault_path(vault_arg))
        except Exception:
            return vault_arg

    env = os.environ.get("VAULT_ROOT") or os.environ.get("VAULT_PATH")
    if env:
        return env

    try:
        from agents import vault_config as vc
        return str(vc.vault_path())
    except Exception:
        pass

    raise SystemExit("Cannot resolve vault root -- pass --vault <path>")


def _web_config_dir() -> Path:
    """Find .claude/web/ relative to the code repo root."""
    candidate = _REPO_ROOT / ".claude" / "web"
    if candidate.is_dir():
        return candidate
    raise SystemExit(f"Cannot find .claude/web/ (tried {candidate})")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(
        description="Second Brain query reformulator -- reformulate crawl plan queries."
    )
    parser.add_argument("--vault", default=None, help="Vault root path or name")
    parser.add_argument(
        "--no-llm",
        action="store_true",
        default=False,
        help="Deterministic fallback only (no LLM call).",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        dest="as_json",
        help="Output results as JSON.",
    )
    args = parser.parse_args()

    try:
        vault_root = _resolve_vault_root(args.vault)
    except SystemExit as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2

    web_dir = _web_config_dir()
    config_path = web_dir / "web-config.json"
    if not config_path.exists():
        print(f"ERROR: {config_path} not found", file=sys.stderr)
        return 2

    try:
        config = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        print(f"ERROR reading web-config.json: {e}", file=sys.stderr)
        return 2

    # Load registry
    from web_decision import load_registry, build_plan
    registry = load_registry(web_dir)
    plan = build_plan(vault_root, config, registry)
    targets = plan.get("targets", [])

    # Load PURPOSE
    purpose = ""
    try:
        purpose_path = Path(vault_root) / "objective" / "purpose" / "PURPOSE.md"
        if purpose_path.exists():
            purpose = purpose_path.read_text(encoding="utf-8", errors="ignore")[:500]
        else:
            yaml_path = Path(vault_root) / "vault.yaml"
            if yaml_path.exists():
                import yaml
                vault_yaml = yaml.safe_load(yaml_path.read_text())
                purpose = (vault_yaml or {}).get("purpose", "")
    except Exception:
        pass

    query_cfg = config.get("query", {})
    use_llm = (not args.no_llm) and query_cfg.get("use_llm", True)
    max_per_engine = query_cfg.get("max_queries_per_engine", 3)

    reformed = reformulate(
        targets,
        purpose=purpose,
        vault_root=vault_root,
        use_llm=use_llm,
        max_per_engine=max_per_engine,
    )

    if args.as_json:
        # Strip internal _gap/_dir keys for clean output
        clean = []
        for t in reformed:
            clean.append({k: v for k, v in t.items() if not k.startswith("_")})
        print(json.dumps(clean, indent=2))
    else:
        print(f"Reformulated {len(reformed)} targets")
        for t in reformed:
            tid = t.get("target_id", "?")
            method = t.get("reformulation", {}).get("method", "?")
            qbe = t.get("queries_by_engine", {})
            n_queries = sum(len(v) for v in qbe.values())
            print(f"  {tid}  method={method}  engines={list(qbe.keys())}  n={n_queries}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
