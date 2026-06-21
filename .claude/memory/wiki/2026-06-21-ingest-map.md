# Ingest map: arXiv 2602.09721 (2026-06-21)

Source: raw/papers/2602.09721v1.pdf
Title: Revealing the Challenges of Attention-FFN Disaggregation for Modern MoE Models and Hardware Systems
Authors: Baidu Baige AI Team (Liu, Li, Guo, Lyu, Zhou, Liu, Li, Wang)
Date: Feb 2026

## Source -> page map
- raw/papers/2602.09721v1.pdf -> wiki/sources/baidu-afd-challenges-2602.09721.md (NEW)
- -> wiki/concepts/hardware-flops-utilization.md (NEW)
- -> wiki/concepts/attention-ffn-disaggregation.md (UPDATED: dead zone section added)
- -> wiki/concepts/expert-parallelism.md (UPDATED: EP vs AFD imbalance comparison)
- -> wiki/concepts/roofline-model.md (UPDATED: communication-level extension)

## Key findings to remember
- AFD dead zone: standard clusters (50 GB/s scale-out) cap B_rank; adding FFN nodes worsens HFU.
- 3BO mandatory for AFD (not 2BO); lower tB fluctuation tolerance than EP.
- EP wins on imbalance: DP imbalance alpha_EP > sigma; AFD alpha_AFD = sigma. EP imbalance: AFD discrete NA scaling loses to EP continuous batch tuning.
- Favorable conditions: Superpod (GB200/GB300, 720 GB/s scale-up) + coarse experts (Step-3 M=5120) + low sparsity.
- Optical niche: bandwidth wall = the dead zone cause. Optical fabric with higher B_ScaleOut would eliminate dead zone without Superpod silicon. Paper does NOT mention optical; this is vault inference.
