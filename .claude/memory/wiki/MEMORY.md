# Wiki agent memory index

Role-scoped durable facts for the Wiki Agent (librarian). Read this first. Add one fact per
file and link it here with a one-line pointer. Do not duplicate what is derivable from the
vault contents or the code.

## Index
- [2026-06-21-ingest-map.md](2026-06-21-ingest-map.md) -- source->page map for arXiv 2602.09721 (Baidu AFD challenges); key AFD dead zone finding; optical bandwidth niche for Iris Tetra.
- [2026-06-22-gaps-run.md](2026-06-22-gaps-run.md) -- wiki-gaps run results: 68 pages scanned, 32 open-question items harvested (parenthetical header bug), 10 gaps, 5 self-contained tasks.
- [2026-06-23-approve-run.md](2026-06-23-approve-run.md) -- wiki-approve 2026-06-23: 2 approved/ingested (Cronus 2509.17357, SARATHI 2308.16369), 2 rejected, 1 deferred. 10 sources now in vault.
- 2026-06-23 session 2: ingested raw/notes/Iris Tetra Roofline BW.md -> wiki/sources/iris-tetra-roofline-bw-note.md. Iris Tetra design targets: int4 33K TFLOPs, PCIe BW min 256 GB/s, memory TBD. Fixed stale ingest_index pending entries for SARATHI + Cronus. 11 sources in vault.
- 2026-06-24: ingested raw/papers/2602.23036v2.pdf (LLMServingSim 2.0, KAIST) + 2 code stubs. New: wiki/sources/llmservingsim-2-2602.23036.md, wiki/entities/llmservingsim.md, wiki/sources/astra-sim-repo.md, wiki/sources/llmservingsim-repo.md. Patched: astra-sim entity, prefill-decode-disaggregation + heterogeneous-disaggregation concepts, GAP-04 (partially-closed). Fixed all stale hash entries; ingest_index pending=0. TBD-6 closed. 14 sources in vault (17 ingested including code stubs + note).
- 2026-06-24 health-fix: (1) gap/index.md GAP table links changed from [[wiki/gap/GAP-N-...]] to bare [[GAP-N-...]] slugs -- all 10 gap pages now reachable. (2) astra-sim-repo + llmservingsim-repo source stubs linked from their entity pages. (3) entities/step-3.md + sources/step-3.md confirmed as correctly-typed distinct pages (model entity vs paper). (4) 176 dead links confirmed as intentional forward-refs (distserve, mooncake, cerebras, tenstorrent, etc.).
- 2026-06-25 wiki-approve + batch ingest: 3 papers approved and ingested from nightly report 2026-06-25. New source pages: spad-2510.08544, mist-2504.09775, zte-multivendor-pd-2509.17542. Key finding: SPAD 40% BW headroom bound for Iris Tetra PCIe latency; MIST Etched+TPUv6e case (49.3% tokens/$) is best analogy for optical-prefill+digital-decode architecture; ZTE TP alignment module is engineering template for optical-to-digital KV handoff. 17 sources now in vault.
- 2026-06-25 wiki-approve (report mode) nightly report 2026-06-23 (second run / perplexity-RSS crawl): 2 web sources approved and ingested (techtarget-disagg-storage-opinion, emergentmind-disaggregated-architectures), 3 rejected (radiodata.biz broken link, YouTube memory disagg out-of-scope, NVIDIA BioNeMo off-purpose). Key finding: EmergentMind survey independently names silicon-photonics Tb/s cross-rack links as leading interconnect (Guo et al. Nov 2025); GAP-02 remains open (no published 3-tier inference system found). 19 sources now in vault.
- 2026-06-28: ingested raw/papers/2602.21548v2.pdf (DualPath, PKU+Tsinghua+DeepSeek-AI). New: wiki/sources/dualpath-2602.21548.md. Updated: kv-cache-transfer (dual-path mode 3 + CNIC analysis), prefill-decode-disaggregation (DualPath section), agentic-ai-workloads (I/O characterization + cache-compute ratios), inference-memory-hierarchy (working set sizing). Key: prefill SNIC saturation in agentic PD systems; layerwise streaming = Iris Tetra pattern; 1.87x throughput gain; SSD tier mandatory for agentic serving. 20 sources now in vault. pending=0.

## Standing reminders
- No web. Ingest only what is in `raw/` and approved; new-source discovery is the Research
  Agent's job.
- Write allowlist: `wiki/**`, `raw/<type>/` (approved only, raw is immutable), `meta/ingest_index*`.
  NEVER `objective/`, `research/`, `meta/{health,cost}_report/`, `meta/nightly_report/`, `docs/`.
  A PreToolUse RBAC hook enforces this.
- Frontmatter = merged superset (live home-grown fields + bi-temporal `timeline:` + frozen
  type tags), carried in each `wiki/<type>/_template.md`.
- Bi-temporal rule: never overwrite role/status/company/fact; append to `timeline:`.
- Shared append targets (`wiki/index.md`, `wiki/log.md`, `wiki/hot.md`, `meta/ingest_index*`)
  require a Layer-2 lock (`scripts/wiki-lock.sh`) before writing.
- Citations are `[[wikilinks]]` only. Every note follows ai-first-rules + write-rules. ASCII only.
