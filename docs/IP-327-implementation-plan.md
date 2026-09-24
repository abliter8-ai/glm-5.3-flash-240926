---
id: IP-327
title: GLM-5.3-Flash EXL3 — adopt MiaAI upstream 0f49cfd and its TP=2 performance opt-ins on the spark pair
status: complete (CR-327) — v5 production per David 2026-09-24
created: 2026-09-24
---

> **David 2026-09-24:** "MIA-Ai has updated their GLM recipe which we closely track on the spark pair. read carefully
> and build a suitable recipe for our stack that delivers these claimed improvements. Once loaded perform our
> standard benchmarks."

## 1. What upstream changed (f906ee99 → 0f49cfd, 240 commits)

Production is recipe v3 = overlay f906ee99 (2026-09-12). IP-314 tested bc68f310 and adopted nothing (decode within 3%,
InstantTensor failed the vision memory floor, cooperative MoE refused on a native hash). New since then, TP=2:

| Option (default off) | Upstream claim | Cost |
|---|---|---|
| `GLM53_EXL3_MOE_FAST=1` thin-decode kernels (needs local build) | decode +7.71% structured, +8.88% code, +14.59% JSON at c1 (A/B/A2, 3 of 5 cells) | numerics study inconclusive; no memory cost stated |
| `GLM53_KDA_BF16_LARGE_M=1` | cold TTFT −11.4% @16k, −12.3% @100k, −14.1% mixed; decode unchanged | +3.29 GiB/rank, straight out of host headroom with a fixed KV pool |
| `GLM53_DRAFT_KV_COMPACT=1` | 28,672-token prefix + 64-token tail repeat −90%; reservation IDs −33% | prose −0.9…−3.3%; no VRAM saving |
| `GLM53_SPINWAIT_MS=16` | +0.95% decode, −85% EngineCore CPU | none stated |
| Always on: Mamba align state-free + chunk alignment fixes | long-prefill state leak and prefix-hit mis-resume fixed | +1 page/request (640k ≈ 12.0 GiB, our 13.5 GiB pool still fits) |
| Upstream defaults | vision caps 48 images / 2048 tokens / 1 GiB cache; omitted `max_tokens` → 65536; `tool_choice:"none"` enforced | lower image detail; `start.sh` now forces `ABLIT=0` after `.env` |

Not adopted: cooperative MoE (overlaps FAST, needs upstream's exact image digest — the IP-314 refusal — and the pair is
untested), InstantTensor (IP-314 memory floor), sparse APC retention 14336/0 (upstream: "UNQUALIFIED PAIR", 7-30×
slower edits), the TP=2 long-coding capacity (262k / 2 seqs / MNBT 1024 — a single-coder operating point; the fleet
needs 640k / 12 seqs), upstream's drafter dc77ff1c (lost our IP-310 A/B) and adaptive-k off (ours measured +17% prose).

## 2. Candidate (recipe v5)

Image `glm53-exl3-local:0f49cfd` built on <head-host> from the 0f49cfd tree (RUIN-DELTA §1 re-applied, §5 transplant
hardlinked), shipped to <worker-host>. `.env` = v3's plus `FAST=1`, `COMPACT=1`, `SPINWAIT=16`, pinned upstream defaults,
`LOAD_FORMAT=` auto. `ABLIT=1` exported by the boot script. KDA BF16 is a separate arm because of its memory cost.

## 3. Arms (all on :8001; :8000 closed so the gateway serves the 12 GLM routes from their fallbacks)

| Arm | Tree / image | KDA | Measures |
|---|---|---|---|
| A | v3 f906ee99 | — | same-day control: full standard battery |
| B | 0f49cfd | 0 | full standard battery + claim probes (repeat prefix, EngineCore CPU) |
| C | 0f49cfd | 1 | prefill ladder, 3×250k memory stress, vision ladder, decode check — **only if** B's measured head floor minus 3.29 GiB stays ≥ 3 GiB under the stress (amendment 2026-09-24, §6) |
| Final | winner on :8000 | — | 20/20 warmup, gateway vs direct parity, refusal probe, console parity |

During the window the lane is declared on :8001 (`cluster-services.yaml` + the fleet-probe REGISTRY primary marker,
deployed to the console host's checkout) so the parity gate and the planner stay true, as in IP-310. Both revert at
close-out.

Standard battery = the IP-310 per-boot set: `l0_spark.sh` (boot facts, bench_decode structured ×5, c12 ×3, needles,
ablit probe, host floor), `l0_client.sh` (acceptance by 7 workload classes, thinking-off battery, warm-poll fairness,
staggered admission), LiveCodeBench trio, cold prefill ladder, 3×250k host-floor stress.

## 4. Gates (from v3's results record)

- Memory: KV pool ≥ 640,000 tokens; worst-case MemAvailable ≥ 3 GB under 3×250k and under the largest image
  batch the recipe allows (vision ladder 1, 8×3, 16 … 48 in steps of 8, then 49 must return HTTP 400). Before each
  rung the minimum is projected as MemAvailable − 1.25 × previous drop × n/previous n; the ladder stops before a
  rung that projects under 3 GiB. The recipe's `LIMIT_MM` image cap is the highest rung that passed.
- Speed: no workload class below −3% of arm A; c12 aggregate within −3%.
- Correctness: needles exact; ablit probe complies; thinking-off battery (LCB runaways 0/3, stories ≥ 3/4, 0 reasoning
  chars); warm session answers ≤ 30 s during a long prefill; warm-up complete; transplant fingerprint present.
- Claims are reported as measured against arm A, not assumed.

## 5. Rollback

`~/safe-takedown.sh --peer <worker-host>`, then boot v3 from `~/glm53-exl3-overlay-f906ee99` on :8000 (image
`glm53-exl3-local:f906ee99` untouched on both ranks). Downtime per boot ≈ 10 min; GLM routes fall back to zAI and
NoThink to Qwen3.8 Extract meanwhile. Known risk: <worker-host>'s in-place GPU reset (L1) is blocked by the
gnome-remote-desktop user unit — a wedge there needs David's call before L1.

## 6. Amendments during execution (2026-09-24)

- **Arm C is conditional.** `GLM53_KDA_BF16_LARGE_M=1` keeps a BF16 copy of the KDA `in_proj` weight: +3.29 GiB per
  rank (upstream measured), taken from host headroom because the KV pool is pinned. Arm A (v3) measured a head
  minimum of 4.07 GiB under the 3×250k stress, so C projects to ≈ 0.8 GiB, under the 3 GiB gate and inside earlyoom's
  `-m 2` range (2% of 121.63 GiB = 2.43 GiB). The head is the tight rank because it also runs the vLLM API server
  (3.36 GiB RSS measured). C boots only if B's measured floor leaves room.
- **Vision gate = the declared cap.** v5 adopts upstream's cap of 48 images per request (v3: 100, uncapped tokens),
  which upstream added after a real OOM (≈33 high-resolution images, 236,544 vision tokens, node 0 at 44 MB free,
  `VLLM::Worker_TP` killed). IP-314 stopped at 8 images, so the 48-image rung has never been measured on this pair.
  The ladder in §4 measures it, and the cap follows the measurement.
- **Arm C′ replaces C (funded KDA).** B boots with the same 13.5 GiB pin at **1,421,333 tokens (2.22× at 640k)**
  against v3's 721,183 (1.13×): with `GLM53_DRAFT_KV_COMPACT=1` the DFlash2 drafter group is a 2,048-token sliding
  window in 896-token blocks (20 block ids per 640k request) instead of reserving pages for the whole context
  (240 ids per request in total). v3-parity capacity therefore needs only ≈ 6.9 GiB, so lowering the pin frees
  host memory on both ranks. C′ = B + `GLM53_KDA_BF16_LARGE_M=1` + a smaller pin (`boot_arm.sh … 1 <bytes>`,
  exported as an `EXTRA_ARGS` override; the `.env` is unchanged). The pin is the largest that keeps the pool
  ≥ v3's 721,183 tokens, the 3×250k floor ≥ 3 GiB after the KDA copy, and the vision cap measured on B. C′ runs the
  full standard battery, because it becomes the final configuration if it passes.
