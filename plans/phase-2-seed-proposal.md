---
type: proposal
status: awaiting-user-review
created: 2026-06-21
updated: 2026-06-21
---

# Phase 2 Checkpoint A -- objective/ seed proposal

**For the user's review and approval. Do NOT place these files in `objective/` until you
have reviewed, edited, and approved each one. All three node types -- purpose, topic,
research_question -- are USER-ONLY per the RBAC schema. Agents (including the backend
agent that produced this document) MUST NOT write into `objective/` for these types.**

**Instructions:**
1. Review each proposed block below.
2. Edit any field or body text you disagree with.
3. For approved nodes, copy the fenced block verbatim into
   `objective/<type>/<id-slug>.md` in the live vault (after running
   `python scripts/obj_init.py --apply` to create the folder structure).
4. Add or remove topic/question nodes freely -- these are YOUR research agenda.
5. Number the files consistently with the proposed ids (T-0001, Q-0001, ...).

**Source mapping:**
- Purpose: near-verbatim from `config/vaults/Inference-Disagg/vault.yaml` `purpose:` field.
- Topics: one per entry in `config/vaults/Inference-Disagg/topics.yaml` (8 topics total).
- Research questions: derived from `wiki/hot.md` TBD open-work-threads (9 TBDs) +
  obvious architectural gaps visible in `wiki/entities/iris-tetra.md` open questions.

---

## 1. Purpose node

**File to create:** `objective/purpose/PURPOSE.md`

The body text is taken near-verbatim from the `purpose:` field in `vault.yaml` and the
fuller statement in `wiki/entities/iris-tetra.md` ("## For future Claude"). Edit freely --
you own this file for the vault's lifetime.

```markdown
---
type: purpose
id: purpose
created: 2026-06-21
updated: 2026-06-21
status: active
vault: Inference-Disagg
---

## For future Claude
This is the vault PURPOSE file. It is the single most important context file
in the vault. It defines the research mission, scope, and what success looks
like. Read this first on every task. There is exactly ONE purpose per vault.
Edit only the body (the frontmatter status field is user-controlled).

# Vault purpose

## Mission
Stay current on disaggregated LLM inference -- prefill/decode separation and
finer FFN/attention-layer disaggregation across distinct resource pools,
KV-cache transfer, and the serving systems, scheduling, and heterogeneous-cluster
hardware that enable it -- in service of designing a disaggregated-inference
simulator and architecture for the Iris Tetra optical accelerator.

Iris Tetra is power-efficient, high-throughput, and strong at long-context
prefill, unlike decode-oriented chips such as Groq or Cerebras. The vault
supports both the simulator deliverable (fork/extend OptiSim + ASTRA-sim) and
the Iris Tetra chip and system architecture.

## Scope
IN SCOPE:
- Prefill/decode disaggregation (P/D and AFD variants): systems, papers, benchmarks.
- KV-cache transfer: protocols, bandwidth requirements, cost models.
- Serving frameworks and schedulers: vLLM, SGLang, TensorRT-LLM, Dynamo, llm-d.
- Layer and expert disaggregation: attention-FFN separation, MoE expert offloading.
- Heterogeneous clusters: hardware-aware scheduling, SLO-aware serving.
- Optical and photonic accelerators: optical compute invariants, silicon photonics,
  free-space optics, co-packaged optics, Lightmatter, Celestial AI.
- Specialised AI accelerators (as contrast / composition partners): Groq LPU,
  Cerebras, Tenstorrent, d-Matrix, NVIDIA hardware roadmap.
- Prefill-bound workload characteristics: arithmetic intensity, roofline modelling,
  long-context prefill, agentic multi-session workloads.
- Simulator design: OptiSim, ASTRA-sim, LLMServingSim and extensions.

OUT OF SCOPE:
- Training, RLHF, fine-tuning infrastructure (unless directly relevant to inference).
- Deployment topics not related to disaggregated inference (e.g. pure edge inference).
- General ML theory not grounded in the inference performance or architecture question.

## Success criteria
The vault supports confident answers to the open research questions under
`objective/research_question/`. Specifically:
- The Iris Tetra simulator is designed and validated against existing simulators
  (OptiSim, ASTRA-sim, LLMServingSim).
- The Iris Tetra architecture has a justified design point (substrate, memory
  architecture, attention pairing, interconnect) grounded in vault evidence.
- All open AFD / KV-cache / optical-interconnect questions are answered or
  explicitly deferred with a stated reason.
```

---

## 2. Topic nodes

**Files to create:** `objective/topic/T-0001-<slug>.md` through `T-0008-<slug>.md`

Each node is derived directly from the corresponding entry in `topics.yaml`. The slug
is a short dash-separated version of the topic name. The "why it serves the purpose"
comment is in the body summary -- edit or expand as you see fit.

### T-0001

**File:** `objective/topic/T-0001-disaggregated-inference.md`

```markdown
---
type: topic
id: T-0001
created: 2026-06-21
updated: 2026-06-21
status: active
related_questions: []
---

## For future Claude
This is a TOPIC node. Topics are confirmed research directions or sub-problems
under the vault purpose. They are USER-ONLY (never created by agents).
status: active | paused | completed
related_questions: list of Q-NNNN ids that fall under this topic.

# Topic: Disaggregated inference

## Summary
Prefill/decode disaggregation (P/D) is the foundational layer of the vault's
architectural thesis. This topic covers the core P/D systems (DistServe,
Splitwise, Mooncake, TetriInfer, MegaScale-Infer, Step-3), their scheduling
primitives (goodput, SLO-aware serving, chunked prefill), and AFD (attention-FFN
disaggregation) as a finer-grained next layer. It is the primary context for
Iris Tetra's role as a prefill-phase accelerator.

## Open questions
-

## Key findings so far
- P/D disaggregation is now productised (Nvidia LPX rack, Step-3 deployment).
- AFD adds an expert/layer separation layer above P/D; dead zone on standard clusters.
- Splitwise + DistServe lineage is foundational; every system above assumes P/D.
```

### T-0002

**File:** `objective/topic/T-0002-kv-cache-systems.md`

```markdown
---
type: topic
id: T-0002
created: 2026-06-21
updated: 2026-06-21
status: active
related_questions: []
---

## For future Claude
This is a TOPIC node. Topics are confirmed research directions or sub-problems
under the vault purpose. They are USER-ONLY (never created by agents).
status: active | paused | completed
related_questions: list of Q-NNNN ids that fall under this topic.

# Topic: KV-cache systems

## Summary
KV-cache is the state that the prefill phase produces and the decode phase
consumes. In a disaggregated stack, transferring this state between pools (optical
prefill -> GPU decode) is a hard bandwidth constraint. This topic covers KV-cache
transfer protocols, offloading, compression, paged attention, prefix caching, and
cache-aware routing. It directly gates Iris Tetra's viability as a prefill engine.

## Open questions
-

## Key findings so far
- KV-cache transfer bandwidth is a first-order design constraint for disaggregated
  optical prefill; must pair with co-packaged HBM (per Photons-to-Tokens).
- Prefix caching and cache-aware routing reduce repeat-prefill cost significantly.
```

### T-0003

**File:** `objective/topic/T-0003-llm-serving-systems.md`

```markdown
---
type: topic
id: T-0003
created: 2026-06-21
updated: 2026-06-21
status: active
related_questions: []
---

## For future Claude
This is a TOPIC node. Topics are confirmed research directions or sub-problems
under the vault purpose. They are USER-ONLY (never created by agents).
status: active | paused | completed
related_questions: list of Q-NNNN ids that fall under this topic.

# Topic: LLM serving systems and schedulers

## Summary
The frameworks (vLLM, SGLang, TensorRT-LLM, Nvidia Dynamo, llm-d) and scheduling
primitives (continuous batching, chunked prefill, inference scheduling) that sit
above the hardware and orchestrate disaggregated deployments. Relevant to simulator
design (what workloads and SLOs to model) and to the question of where an optical
prefill pool plugs in to the existing software stack.

## Open questions
-

## Key findings so far
- ASTRA-sim 3.0 + LLMServingSim 2.0 are the direct simulator precedents.
- Dynamo and llm-d represent the productised disaggregated scheduling layer.
```

### T-0004

**File:** `objective/topic/T-0004-layer-expert-disaggregation.md`

```markdown
---
type: topic
id: T-0004
created: 2026-06-21
updated: 2026-06-21
status: active
related_questions: []
---

## For future Claude
This is a TOPIC node. Topics are confirmed research directions or sub-problems
under the vault purpose. They are USER-ONLY (never created by agents).
status: active | paused | completed
related_questions: list of Q-NNNN ids that fall under this topic.

# Topic: Layer and expert disaggregation

## Summary
Finer-grained disaggregation beyond P/D: splitting attention and FFN layers across
different resource pools, and offloading MoE experts across accelerator types. AFD
(Attention-FFN Disaggregation) is the current frontier (MegaScale-Infer, Step-3,
Baidu challenges paper). Directly relevant to the 3-tier Iris Tetra stack:
optical-prefill + GPU-attention-decode + LPU-FFN-decode.

## Open questions
-

## Key findings so far
- AFD requires 3-batch overlap (3BO) minimum for bubble-free operation.
- Dead zone on standard clusters (H800 50 GB/s scale-out); needs Superpod BW.
- Optical interconnect as a potential dead-zone eliminator is uncharted (vault TBD-9).
```

### T-0005

**File:** `objective/topic/T-0005-heterogeneous-cluster-inference.md`

```markdown
---
type: topic
id: T-0005
created: 2026-06-21
updated: 2026-06-21
status: active
related_questions: []
---

## For future Claude
This is a TOPIC node. Topics are confirmed research directions or sub-problems
under the vault purpose. They are USER-ONLY (never created by agents).
status: active | paused | completed
related_questions: list of Q-NNNN ids that fall under this topic.

# Topic: Heterogeneous-cluster inference

## Summary
Serving LLM inference across clusters mixing different accelerator types (GPU +
LPU + optical + network-attached storage). Covers hardware-aware scheduling,
accelerator placement, SLO-aware routing, and cross-accelerator cost models.
Dominant deployment reality per Gimlet Labs empirical data and Asgar et al
6-dimensional cost model. Directly models the cluster into which Iris Tetra fits.

## Open questions
-

## Key findings so far
- Gimlet Labs B200+Gaudi3 achieves 3-4x TCO over H100:H100 for disaggregated serving.
- InfraGraph (ASTRA-sim 3.0) enables heterogeneous cluster topology modelling.
- Asgar et al provide a 6-dim cost model usable for Iris Tetra placement optimisation.
```

### T-0006

**File:** `objective/topic/T-0006-optical-accelerators.md`

```markdown
---
type: topic
id: T-0006
created: 2026-06-21
updated: 2026-06-21
status: active
related_questions: []
---

## For future Claude
This is a TOPIC node. Topics are confirmed research directions or sub-problems
under the vault purpose. They are USER-ONLY (never created by agents).
status: active | paused | completed
related_questions: list of Q-NNNN ids that fall under this topic.

# Topic: Optical accelerators and inference efficiency

## Summary
The physics, architecture, and system design of photonic/optical compute for AI
inference. This is the most Iris-Tetra-proximate topic: it covers optical compute
invariants, programming/stabilization latency, free-space vs silicon photonics
tradeoffs, co-packaged optics, and the relevant industry actors (Lightmatter,
Celestial AI, Ayar Labs). The OptiSim simulator is the primary prior art for
Iris Tetra's simulation methodology.

## Open questions
-

## Key findings so far
- Free-space 3D optics beats silicon photonics for prefill (D=2048 vs D<=128).
- Co-packaged HBM is mandatory (PCIe attachment costs 12-27% performance).
- Optical accelerators are phase-selective by physics: prefill only.
- OptiSim 5-invariant abstraction is the right fork point for Iris Tetra simulator.
```

### T-0007

**File:** `objective/topic/T-0007-specialised-ai-accelerators.md`

```markdown
---
type: topic
id: T-0007
created: 2026-06-21
updated: 2026-06-21
status: active
related_questions: []
---

## For future Claude
This is a TOPIC node. Topics are confirmed research directions or sub-problems
under the vault purpose. They are USER-ONLY (never created by agents).
status: active | paused | completed
related_questions: list of Q-NNNN ids that fall under this topic.

# Topic: Specialised AI accelerators (heterogeneous hardware)

## Summary
Non-GPU inference accelerators: Groq LPU, Cerebras wafer-scale, SambaNova RDU,
Tenstorrent, d-Matrix Corsair, Etched Sohu, and others. Relevant as contrast
chips (Groq/Cerebras are decode-oriented, confirming Iris Tetra's prefill niche)
and as composition partners in the 3-tier disaggregated stack (LPU handles FFN
decode while Iris Tetra handles prefill). Also informs the competitive landscape.

## Open questions
-

## Key findings so far
- Groq LPU is decode-oriented: SRAM-rich but compute-limited at long-context prefill.
- The decode-oriented niche is crowded; prefill (Iris Tetra's target) is less contested.
```

### T-0008

**File:** `objective/topic/T-0008-prefill-bound-workloads.md`

```markdown
---
type: topic
id: T-0008
created: 2026-06-21
updated: 2026-06-21
status: active
related_questions: []
---

## For future Claude
This is a TOPIC node. Topics are confirmed research directions or sub-problems
under the vault purpose. They are USER-ONLY (never created by agents).
status: active | paused | completed
related_questions: list of Q-NNNN ids that fall under this topic.

# Topic: Prefill-bound workloads and compute characteristics

## Summary
The arithmetic intensity, roofline characteristics, and throughput profiles of
long-context prefill and agentic workloads. This topic underpins the Iris Tetra
design point: what batch sizes, sequence lengths, and model architectures make
optical compute the roofline-optimal choice? Covers HFU/OFU temporal-sparsity
metrics, the communication-level roofline extension, and the prefill/decode split
in agentic multi-session workloads.

## Open questions
-

## Key findings so far
- Hardware Flops Utilization (HFU) + OFU framework quantifies AFD/EP tradeoffs.
- Communication-level roofline extension (3 regimes: compute/memory/communication-bound)
  captures the AFD dead zone that standard roofline misses.
- Agentic workloads are prefill-heavy; long-context is the dominant growth trajectory.
```

---

## 3. Research question nodes

**Files to create:** `objective/research_question/Q-0001-<slug>.md` through
`Q-0008-<slug>.md`

Derived from: TBD-4, TBD-6, TBD-9, TBD-1 in `wiki/hot.md` (the most
architectural/answerable ones), plus the key architectural gaps in
`wiki/entities/iris-tetra.md`. TBD-2 (ingest paper tracking) and TBD-3
(authorship verify) are operational tasks, not research questions -- omitted.
TBD-5 (lint) and TBD-7 (synthesis update) are wiki maintenance -- omitted.
TBD-8 (EaaS papers) is an ingest task -- not proposed as a research question
(propose as a wiki-agent ingest task instead).

**Review note on priority:** I have assigned priority based on Iris Tetra
design-critical ordering. You should adjust to match your actual work focus.

---

### Q-0001

**File:** `objective/research_question/Q-0001-optical-fabric-bw-target.md`
**Source:** wiki/hot.md TBD-9 + iris-tetra.md open question on KV-cache transfer.

```markdown
---
type: research_question
id: Q-0001
created: 2026-06-21
updated: 2026-06-21
solved: "no"
topic: T-0006
priority: high
answer_ref: ""
---

## For future Claude
This is a RESEARCH QUESTION node. It is USER-ONLY.
solved: "yes" | "no" -- only the USER sets this; agents NEVER flip it autonomously.
priority: high | medium | low
answer_ref: filled by question-solve skill pointing to research/<question_id>.md

# Research question

What scale-out bandwidth (B_ScaleOut) does Iris Tetra need to eliminate the AFD
dead zone for DeepSeek-V3-class and Step-3-class model configurations, and is
this target achievable with a free-space optical fabric?

## Background
Baidu 2026 (arXiv 2602.09721) identified the AFD dead zone: on standard clusters
with 50 GB/s scale-out bandwidth (H800), scaling the number of ranks dilutes the
token pool and collapses HFU. The vault's central new implication (hot.md 2026-06-21)
is that an optical fabric with 5-10x higher effective scale-out bandwidth could
eliminate this dead zone without Superpod silicon. The exact bandwidth target is
unquantified. Derived from Baidu Eq. 7 + Iris Tetra specs.

## Acceptance criteria
A quantitative derivation of the minimum B_ScaleOut needed to keep HFU above a
useful threshold (say, >0.7) for DeepSeek-V3 (M=2048) and Step-3 (M=5120)
configurations, expressed in GB/s per rank, compared against optical fabric
achievable BW from the Photons-to-Tokens / free-space optics literature.
```

---

### Q-0002

**File:** `objective/research_question/Q-0002-iris-tetra-simulator-design.md`
**Source:** wiki/hot.md TBD-4 + iris-tetra.md deliverable "disaggregated-inference simulator".

```markdown
---
type: research_question
id: Q-0002
created: 2026-06-21
updated: 2026-06-21
solved: "no"
topic: T-0006
priority: high
answer_ref: ""
---

## For future Claude
This is a RESEARCH QUESTION node. It is USER-ONLY.
solved: "yes" | "no" -- only the USER sets this; agents NEVER flip it autonomously.
priority: high | medium | low
answer_ref: filled by question-solve skill pointing to research/<question_id>.md

# Research question

What is the minimal extension to OptiSim + ASTRA-sim 3.0 needed to simulate a
heterogeneous disaggregated inference cluster with an Iris Tetra optical prefill
pool, and what workload scenarios should the simulator validate against?

## Background
wiki/hot.md TBD-4 flags this as the next design step. OptiSim (Photons-to-Tokens)
provides the 5-invariant optical arithmetic abstraction. ASTRA-sim 3.0 provides
the InfraGraph heterogeneous collective communication framework. LLMServingSim 2.0
(TBD-6, arXiv 2602.23036) extends ASTRA-sim for disaggregated LLM serving and is
a direct precedent. The gap is an Iris Tetra-specific optical node model integrated
into this stack.

## Acceptance criteria
A concrete simulator design: (1) which parts of OptiSim, ASTRA-sim 3.0, and
LLMServingSim 2.0 are reused vs extended; (2) the interface between the optical
node model and the InfraGraph collective layer; (3) three representative validation
scenarios (long-context prefill, agentic multi-session, MoE-heavy decode).
```

---

### Q-0003

**File:** `objective/research_question/Q-0003-kv-cache-transfer-cost.md`
**Source:** iris-tetra.md "Key architectural questions" + TBD-9 bandwidth work.

```markdown
---
type: research_question
id: Q-0003
created: 2026-06-21
updated: 2026-06-21
solved: "no"
topic: T-0002
priority: high
answer_ref: ""
---

## For future Claude
This is a RESEARCH QUESTION node. It is USER-ONLY.
solved: "yes" | "no" -- only the USER sets this; agents NEVER flip it autonomously.
priority: high | medium | low
answer_ref: filled by question-solve skill pointing to research/<question_id>.md

# Research question

What is the KV-cache transfer bandwidth requirement and latency budget for an
Iris Tetra optical prefill pool feeding a GPU attention-decode pool, and which
interconnect topology meets that budget?

## Background
KV-cache transfer from prefill to decode is a fundamental constraint in any P/D
disaggregated system. For Iris Tetra specifically, the transfer must cross an
optical-to-digital boundary, which adds topology and protocol constraints beyond
standard GPU-to-GPU NVLink or InfiniBand transfers. The vault covers KV-cache
transfer in wiki/concepts/kv-cache-transfer.md but has not yet quantified the
Iris Tetra-specific budget (sequence lengths, batch sizes, BW/latency targets).

## Acceptance criteria
A model or table: for representative (context-length, batch-size) pairs, what
peak KV-cache transfer BW is required? Which interconnect options (CPO, optical
I/O, NVLink-like) fall within the budget? What is the tolerable per-token latency
overhead before the prefill advantage is eroded?
```

---

### Q-0004

**File:** `objective/research_question/Q-0004-llmservingsim-extension.md`
**Source:** wiki/hot.md TBD-6 (LLMServingSim 2.0).

```markdown
---
type: research_question
id: Q-0004
created: 2026-06-21
updated: 2026-06-21
solved: "no"
topic: T-0003
priority: medium
answer_ref: ""
---

## For future Claude
This is a RESEARCH QUESTION node. It is USER-ONLY.
solved: "yes" | "no" -- only the USER sets this; agents NEVER flip it autonomously.
priority: high | medium | low
answer_ref: filled by question-solve skill pointing to research/<question_id>.md

# Research question

How does LLMServingSim 2.0 (Cho et al 2026, arXiv 2602.23036) extend ASTRA-sim
for disaggregated LLM serving, and what specifically must be further extended to
model an optical prefill node?

## Background
wiki/hot.md TBD-6 flags LLMServingSim 2.0 as a direct precedent for the Iris Tetra
simulator. It is not yet ingested into the vault. Understanding its architecture
(what it models, what it abstracts away, how it represents prefill vs decode phases)
is a prerequisite for designing the Iris Tetra extension to the simulator stack.

## Acceptance criteria
A summary of LLMServingSim 2.0's architecture, its inputs/outputs, and a gap analysis:
what does it NOT model that an optical prefill node requires? Minimum: (1) does it
model inter-pool KV-cache transfer BW as a bottleneck? (2) does it support
heterogeneous node types? (3) can it model phase-selective compute (optical matmul
only on prefill, pass-through on decode)?
```

---

### Q-0005

**File:** `objective/research_question/Q-0005-optical-prior-art.md`
**Source:** wiki/hot.md TBD-1 (ingest optical AI prior art).

```markdown
---
type: research_question
id: Q-0005
created: 2026-06-21
updated: 2026-06-21
solved: "no"
topic: T-0006
priority: medium
answer_ref: ""
---

## For future Claude
This is a RESEARCH QUESTION node. It is USER-ONLY.
solved: "yes" | "no" -- only the USER sets this; agents NEVER flip it autonomously.
priority: high | medium | low
answer_ref: filled by question-solve skill pointing to research/<question_id>.md

# Research question

What do the optical-AI prior-art papers cited by Photons-to-Tokens (LightML,
Demirkiran et al, Ahmed et al Nature 2025 / Lightmatter, Lightning-Transformer)
say about programming latency, device scaling, and system-level integration, and
how do their design points compare with the Iris Tetra OptiSim baseline?

## Background
wiki/hot.md TBD-1 lists four specific optical-AI papers not yet ingested. They are
cited by Photons-to-Tokens (the vault's primary optical reference) as prior art,
which means they either challenge or refine the 5-invariant model OptiSim uses.
Understanding their design points is needed before finalising the Iris Tetra
architectural choices (substrate, D scaling, latency targets).

## Acceptance criteria
After ingestion of the four papers: a comparative table of Lprog, D, throughput/W,
and memory architecture across LightML, Demirkiran, Ahmed/Lightmatter,
Lightning-Transformer, and OptiSim. A verdict: do any of them invalidate or
substantially revise the free-space + co-packaged-HBM design direction?
```

---

### Q-0006

**File:** `objective/research_question/Q-0006-afd-dead-zone-conditions.md`
**Source:** wiki/hot.md 2026-06-21 key finding on AFD dead zone + iris-tetra.md implication.

```markdown
---
type: research_question
id: Q-0006
created: 2026-06-21
updated: 2026-06-21
solved: "no"
topic: T-0004
priority: medium
answer_ref: ""
---

## For future Claude
This is a RESEARCH QUESTION node. It is USER-ONLY.
solved: "yes" | "no" -- only the USER sets this; agents NEVER flip it autonomously.
priority: high | medium | low
answer_ref: filled by question-solve skill pointing to research/<question_id>.md

# Research question

Under what exact cluster conditions (scale-out BW, model granularity M, number of
ranks NF) does AFD escape the dead zone, and does an optical-fabric scale-out
interconnect (as a substitute for Superpod NVSwitch) enable standard-cluster AFD?

## Background
Baidu 2026 (arXiv 2602.09721) defines the AFD dead zone and the necessary conditions
(GB200/GB300 720 GB/s, or large M). The vault has not yet formally derived the phase
boundary in (B_ScaleOut, M, NF) space. The optical-fabric hypothesis (hot.md
2026-06-21) is stated qualitatively but not derived. This question makes it
falsifiable.

## Acceptance criteria
A phase diagram or inequality in (B_ScaleOut, M, NF) space derived from Baidu Eq. 7
showing where HFU > useful threshold. The optical-fabric hypothesis confirmed or
refuted: at the BW achievable by a free-space optical fabric (from Q-0001), does
standard-cluster AFD become viable for DeepSeek-V3 / Step-3 models?
```

---

### Q-0007

**File:** `objective/research_question/Q-0007-3tier-all-to-all-cost.md`
**Source:** iris-tetra.md "All-to-All dispatch/combine cost in a 3-tier stack".

```markdown
---
type: research_question
id: Q-0007
created: 2026-06-21
updated: 2026-06-21
solved: "no"
topic: T-0001
priority: medium
answer_ref: ""
---

## For future Claude
This is a RESEARCH QUESTION node. It is USER-ONLY.
solved: "yes" | "no" -- only the USER sets this; agents NEVER flip it autonomously.
priority: high | medium | low
answer_ref: filled by question-solve skill pointing to research/<question_id>.md

# Research question

What is the latency and bandwidth cost of All-to-All dispatch/combine in a
3-tier disaggregated stack (optical-prefill + GPU-attention-decode + LPU-FFN-decode),
and is this overhead small enough to preserve the prefill advantage of optical compute?

## Background
wiki/entities/iris-tetra.md lists this as an open architectural question.
wiki/concepts/all-to-all-dispatch-combine.md covers the 2-tier (prefill/decode)
case. The 3-tier case adds a second All-to-All boundary (GPU-attention <-> LPU-FFN)
and the optical-to-digital transition cost. ASTRA-sim 3.0 InfraGraph can model this
topology but the Iris Tetra-specific cost model has not been run.

## Acceptance criteria
An analytical or simulated cost model: for a representative batch size and model
config, the end-to-end latency breakdown across the 3 tiers including both
All-to-All collectives and KV-cache transfer. A threshold: at what dispatch/combine
overhead does the optical prefill speedup (4x over A100 per Photons-to-Tokens)
become net-negative for total time-to-first-token?
```

---

### Q-0008

**File:** `objective/research_question/Q-0008-iris-tetra-arch-design-point.md`
**Source:** iris-tetra.md "Deliverables: the Iris Tetra architecture itself".

```markdown
---
type: research_question
id: Q-0008
created: 2026-06-21
updated: 2026-06-21
solved: "no"
topic: T-0006
priority: low
answer_ref: ""
---

## For future Claude
This is a RESEARCH QUESTION node. It is USER-ONLY.
solved: "yes" | "no" -- only the USER sets this; agents NEVER flip it autonomously.
priority: high | medium | low
answer_ref: filled by question-solve skill pointing to research/<question_id>.md

# Research question

What is the justified Iris Tetra chip design point: substrate choice, device scaling D,
memory architecture, and attention-design pairing, given the accumulated vault evidence?

## Background
wiki/entities/iris-tetra.md summarises preliminary design implications from
Photons-to-Tokens: free-space optics, D=2048, co-packaged HBM, MFA-style attention.
These are grounded in a single source. Answering this question requires synthesising
all vault optical, KV-cache, and prefill-workload evidence into a coherent design
recommendation. It is the final deliverable of the vault's mission.

## Acceptance criteria
A synthesis document covering: (1) substrate (free-space vs silicon photonics
justified against full prior-art survey from Q-0005); (2) device scaling D target;
(3) memory architecture (HBM tier, SRAM, co-packaging constraints); (4) attention
design pairing (MFA, standard MHA, GQA) matched to the optical roofline; (5) open
risks and what evidence would revise the design point.
```

---

## 4. Notes for user review

**Topics -- especially review:**
- T-0007 (Specialised AI accelerators) has `status: active`. If you only track this as
  background context and do not actively research it, consider `status: paused`.
- The `related_questions: []` fields are intentionally empty -- fill them in after you
  have placed the question files (add the Q-NNNN ids that belong to each topic).

**Questions -- especially review:**
- Q-0001 and Q-0006 overlap (both are about the AFD dead zone and optical bandwidth).
  Q-0001 is the quantitative bandwidth target; Q-0006 is the phase-space condition.
  You may want to merge them into one question or keep them separate as ordered subtasks
  (Q-0006 produces the phase diagram that Q-0001 uses as input -- so Q-0006 first).
- Q-0008 (architecture design point) is marked `priority: low` because it depends on
  answering Q-0001, Q-0003, Q-0005, Q-0006 first. Adjust if you want it as the explicit
  north star.
- Q-0004 (LLMServingSim extension) depends on ingesting arXiv 2602.23036 first (TBD-6).
  Consider whether to open it now or only after ingest.
- All questions have `topic:` pointing to the most directly relevant topic. A question
  may span multiple topics (e.g. Q-0003 KV-cache is T-0002 but also T-0006); pick the
  primary one and note the secondary in the body.

**What would answer each question (one-liner):**
| Id    | What would answer it |
|-------|----------------------|
| Q-0001 | Derivation from Baidu Eq. 7 + optical BW specs |
| Q-0002 | Simulator design document with interface spec |
| Q-0003 | BW/latency model for representative workloads |
| Q-0004 | LLMServingSim 2.0 ingest + gap analysis |
| Q-0005 | Ingest 4 optical papers + comparative table |
| Q-0006 | Phase diagram from Baidu dead-zone analysis |
| Q-0007 | 3-tier cost model via ASTRA-sim InfraGraph |
| Q-0008 | Full synthesis of all optical/KV/prefill evidence |

**Decision nodes (not in scope of this proposal, but suggested):**
The plan calls for at least one seed decision node. Suggested D-0001:
"Do not fetch web content from the research agent (no WebSearch/WebFetch in Phase 2)."
You may create this directly in `objective/decision/D-0001-no-web-research.md`
following the `_template.md` frontmatter.
