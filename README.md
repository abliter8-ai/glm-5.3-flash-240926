# glm-5.3-flash-240926

GLM-5.3-Flash (EXL3/TR3 4-bit routed experts, abliterated) served on two NVIDIA DGX Spark (GB10) nodes with
tensor parallel 2 over ConnectX-7 — the serving recipe promoted to production on 2026-09-24, with the benchmark
evidence behind it.

| | |
|---|---|
| Weights | [`Mia-AiLab/GLM-5.3-Flash-EXL3-TR3-4bpw`](https://huggingface.co/Mia-AiLab/GLM-5.3-Flash-EXL3-TR3-4bpw) @ `25a44fdb` |
| Engine | vLLM via [`MiaAI-Lab/GLM-5.3-Flash-EXL3-2x-DGX-Sparks`](https://github.com/MiaAI-Lab/GLM-5.3-Flash-EXL3-2x-DGX-Sparks) @ `0f49cfd`, image built locally, plus the deltas in `overlay/` |
| Speculative decoding | DFlash2 k=7 ([`incoai/GLM-5.3-Flash-DFlash2`](https://huggingface.co/incoai/GLM-5.3-Flash-DFlash2) @ `7d74cdd8`), adaptive verification ema {2,4,7}, margin 2.0 |
| Serving shape | 640k context, 12 sequences, fp8 KV pinned at 13.5 GiB, FP8 weight-only dense/KDA projections, runtime o_proj abliteration (layers 15–45) |
| New in this recipe | `GLM53_EXL3_MOE_FAST=1` (thin-decode MoE kernels), `GLM53_DRAFT_KV_COMPACT=1`, `GLM53_SPINWAIT_MS=16`, Mamba align fixes, 8 images per request |

## Results — same day, same hardware, against the previous production recipe

| Measure | Previous (overlay f906ee99) | This recipe |
|---|---|---|
| Decode, single stream (structured) | 73.5 tok/s | **79.1 tok/s** (+7.7%) |
| Code, thinking high | 45.5 tok/s | **49.5 tok/s** (+8.8%) |
| Prose | 22.7–23.5 tok/s | **24.1–25.1 tok/s** (+6–7%) |
| 12 concurrent streams | 332 tok/s | **357 tok/s** (+7.4%) |
| Cold prefill at 12k / 154k / 487k tokens | 968 / 1035 / 991 tok/s | **1027 / 1067 / 1015 tok/s** |
| Repeat of a 28.7k-token prompt with a new tail | 3.93 s | **0.43 s** |
| Engine-loop CPU | 100% of a core | **18%** |
| KV tokens at the same 13.5 GiB | 721k | **1.42M** |

Correctness held: long-context needles exact (including 11/11 under three concurrent 250k-token prefills),
refusal probe 8/8, thinking-off quality unchanged, LiveCodeBench (thinking off, 16k) 2/3 vs 0/3, gateway and
direct calls identical. `GLM53_KDA_BF16_LARGE_M` was measured (+9–10% prefill, numerics-neutral) but not adopted:
its 3.29 GiB per node cannot be funded at 12 concurrent sequences. Details: `docs/CR-327-completion-report.md`
and `results/`.

## Contents

| Path | What |
|---|---|
| `recipe/` | The serving recipe (every value and why) |
| `results/` | Per-arm measurements: control, candidate, KDA variant, final boot |
| `docs/` | Completion report, implementation plan, model pre-flight (template render check, serving-path parity) |
| `config/launch.env` | The launch environment (`.env` for the overlay tree); `config/glm53_adaptive_k.json` is the adaptive-k runtime file |
| `overlay/` | Our deltas to upstream: the one-line `start.sh` patch (dual-rail NCCL), the bench-harness constants, the delta log and vendoring record |
| `scripts/` | Build, boot, verify and the standard benchmark battery (`scripts/README.md`) |

## Reproduce

1. Clone the upstream overlay at `0f49cfd` and apply `overlay/start.sh-merge-nics.patch`.
2. Build the image on the head node (`scripts/head/build.sh`) and load it on the worker **before** any boot.
3. Copy `config/launch.env` to the tree's `.env` and fill the placeholders; copy `config/glm53_adaptive_k.json`
   to `~/.cache/vllm-glm53-flash/glm53_adaptive_k.json`; fetch the abliteration transplant set
   (upstream `ablit/fetch_transplant.py`).
4. Boot: `scripts/head/boot_arm.sh <label> <tree> 8000 0` — it takes the previous engine down gracefully, exports
   `ABLIT=1 ABLIT_METHOD=transplant` (upstream's `start.sh` resets `ABLIT` after `.env`), and fails the boot on an
   incomplete warm-up or a missing transplant. Never force-remove a live GPU container on GB10.
5. Verify: `scripts/head/verify_boot.sh <label> 8000`, then the battery (`scripts/README.md`).

## Placeholders

Host names, addresses and user names are replaced: `<head-host>`, `<worker-host>`, `<head-ip>`, `<worker-ip>`,
`<user>`, `<gateway-host>`, `<client-host>`, `<console-host>` in documents, and `__HEAD_HOST__`, `__WORKER_IP__`,
`__USER__` and so on in scripts and `config/launch.env`. Paths in the documents refer to the fleet repository's
layout. Not included: upstream code (use the pinned commit), model weights, and the LiveCodeBench harness that
`scripts/client/lcb_trio.sh` calls.
