---
type: gaps_report
created: 2026-06-01
vault: test-vault
---

## Knowledge Gaps

### GAP-08: Optical prior art -- citations [3]-[6] from "Photons to Tokens" not ingested
- shows_up_in: [[wiki/sources/photons-to-tokens]] Open Questions; [[wiki/concepts/free-space-optics]] references LightML
- missing: entity/source pages for these four cited papers; they may contain Iris Tetra-relevant device parameters
- fillable_by: arxiv (all four are likely arXiv papers based on citation style)
- topic: T-0006
- priority: medium -- may provide the optical roofline range needed to close GAP-01

### GAP-02: 3-tier disaggregation -- no published system or model
- shows_up_in: [[wiki/synthesis/disaggregation-thesis]] Open Questions; [[wiki/concepts/attention-ffn-disaggregation]] Open Questions
- missing: system architecture, scheduling algorithm, communication topology (M2N2K), and performance model
- fillable_by: arxiv (MegaScale-Infer follow-up); forum (Nvidia developer blog, MLSys community); web (conference proceedings)
- topic: T-0001, T-0004
- priority: high -- appears in Open-Question Harvest; the simulator deliverable cannot be specified without this
