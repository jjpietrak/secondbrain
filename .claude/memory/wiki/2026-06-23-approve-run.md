# wiki-approve session 2026-06-23

## Report processed
/mnt/c/Obsidian/Inference-Disagg/meta/nightly_report/2026-06-23.md

## Decisions
- APPROVED + INGESTED: arxiv:2509.17357v1 -> wiki/sources/cronus-2509.17357.md
- APPROVED + INGESTED: arxiv:2308.16369v1 -> wiki/sources/sarathi-2308.16369.md
- REJECTED: NVIDIA Vera CPU/LANL blog (user: low depth)
- REJECTED: arxiv:2605.06105v1 SPEED (user: low author reliability, off-scope)
- DEFERRED to backlog: arxiv:2602.12029v1 PrefillShare (both boxes blank)

## Pages created
- wiki/sources/cronus-2509.17357.md
- wiki/sources/sarathi-2308.16369.md

## Pages patched
- wiki/concepts/prefill-decode-disaggregation.md (added both sources + body sections)
- wiki/concepts/heterogeneous-disaggregation.md (added Cronus source)
- wiki/index.md (2 new source rows)
- wiki/log.md (1 new row)
- wiki/hot.md (session summary + updated source count 8->10)

## Key design insights added to vault
- SARATHI 200x decode/prefill cost at BS=1 = primary numerator for optical-prefill net benefit (DIR-0005)
- Cronus Balancer (Eq. 2-3) = template for Iris Tetra optical-PPI cost model (needs re-derivation for optical arithmetic intensity + stabilization latency)
- KV transfer overlap confirmed feasible at 100 Gbps IB; optical fabric bandwidth makes it more favorable
