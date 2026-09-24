---
id: CR-327
title: GLM-5.3-Flash EXL3 — MiaAI upstream 0f49cfd and its TP=2 opt-ins, measured against a same-day v3 control
status: complete — v5 PRODUCTION per David 2026-09-24; scrubbed GitHub backup follows David's file-list review
date: 2026-09-24
linked_ip: IP-327
author: claude-opus-5-5
---

# CR-327 — Upstream 0f49cfd on the spark pair: what the claimed improvements are worth here

[Plan](../b-implementation_plans/IP-327-glm53-exl3-upstream-0f49cfd-tp2-optins.md) ·
[Recipe v5 (production)](../../models/registry/recipes/glm53-flash-exl3-tr3-4bpw__dual-gb10-tp2__vllm-miaai-overlay__v5.yaml) ·
[Results](../../models/registry/results/glm53-flash-exl3-tr3-4bpw__dual-gb10-tp2__vllm-miaai-overlay__v5__20260924__ip327-upstream-optins.yaml) ·
Pre-flight `.model-preflight/mia-ailab--glm-5.3-flash-exl3-tr3-4bpw/preflight.md` · Ledger
`~/.local/share/bench/ip327-20260924/LEDGER.md` (<client-host>), raw `~/ip327/raw/` (<head-host>).

## Verdict

Adopted: upstream 0f49cfd with `GLM53_EXL3_MOE_FAST=1`, `GLM53_DRAFT_KV_COMPACT=1`, `GLM53_SPINWAIT_MS=16`, the
always-on Mamba align fixes and upstream's safety defaults, at v3's 13.5 GiB KV pin, **with the image cap at 8 per
request**. Against a same-day v3 control: decode +7.7% single-stream (+8.8% code, +6–7% prose), +7.4% at 12 streams,
cold prefill +2–6%, a repeated 28.7k-token prefix answers in 0.43 s instead of 3.9 s, EngineCore CPU −82%, twice the
KV token capacity, a higher worst-case host floor, and no correctness regression. Not adopted: `GLM53_KDA_BF16_LARGE_M`
(real +9–10% prefill, numerics-neutral, but its 3.29 GiB/rank cannot be funded without cutting 12-session concurrency
or vision).

## Claims vs measurement

| Upstream claim (TP=2) | Measured here | Verdict |
|---|---|---|
| `GLM53_EXL3_MOE_FAST` decode +7.7% structured / +8.9% code / +14.6% JSON | structured c1 **79.14 vs 73.51 tok/s (+7.7%)**, same acceptance (0.956), 96.95 vs 104.39 ms per draft step; code think-high **49.5 vs 45.5 (+8.8%)**; prose +6.2…+6.8%; JSON 53.5 vs 54.2 (−1.3%, n=1 at T 1.0, that sample accepted 0.867 vs 0.99); c2 aggregate +10.4%; c12 357.0 vs 332.4 (+7.4%) | reproduced (structured, code); JSON not shown at n=1 |
| `GLM53_DRAFT_KV_COMPACT` repeat of a 28,672-token prefix −90% (2.62 → 0.30 s); reservation ids −33% | repeat **0.43 vs 3.93 s (−89%)**, 28,672 vs 25,088 tokens reused; the same 13.5 GiB pin holds **1,421,333 vs 721,183 tokens (2.22× vs 1.13× at 640k)** — the drafter group is a 2,048-token window in 896-token blocks | reproduced; token capacity doubles (not a concurrency gain — see below) |
| `GLM53_SPINWAIT_MS=16` +0.95% decode, −85% EngineCore CPU | EngineCore **17.7% vs 99.7%** of one core (−82%) | reproduced |
| Mamba align fixes (always on) | the page a repeat used to lose is recovered; cold prefill +6.1 / +3.1 / +2.4% at 12k / 154k / 487k; 3×250k 11/11 exact | reproduced |
| `GLM53_KDA_BF16_LARGE_M` cold TTFT −11.4 / −12.3 / −14.1% | prefill **+8.8 / +9.6 / +9.9%** at 12k / 159k / 470k (TTFT −8…−9%); 28.7k cold TTFT −8.5%; KL vs BF16 dense 0.0062 nats (v3 0.0061); decode unchanged | real, smaller than claimed; not adopted (memory) |
| Vision caps 48 × 2048 tokens, 1 GiB processor cache | each 1080p image costs ~180–210 MiB of transient head memory; 8 images → head min 3.29 GiB; 12 projected < 3 GiB | cap 48 unsafe on our head; **8 adopted** |

## Findings that change how this lane is sized

1. **KV capacity is per request, not per token.** Every running request holds ≥ 35 of the pool's blocks (≈ 0.9 GiB)
   whatever its length: engine stats on C′ read 11.1% of 315 blocks for one request, 22.2% for two, 33.3% for three,
   and 100% with 9 running and 3 waiting at c12. Twelve sessions need ≈ 420 blocks (≈ 10.7 GiB); the 13.5 GiB pin
   (532 usable blocks) is right for `MAX_NUM_SEQS=12`. The 2.22× figure is how many 640k-token requests fit, not how
   many sessions. The IP-327 amendment that funded KDA from the pin was based on the token figure and was wrong;
   arm C′ measured the cost (c12 −31%).
2. **The head rank is the tight one** because it also runs the vLLM API server (3.36 GiB RSS). Vision memory lands
   there: the per-image cost is host-side copies of the processed pixels while the request is in flight, so a request
   with many NEW images is the OOM path (upstream's 2026-09-14 crash; v3's cap of 100 with uncapped tokens is exposed
   to it). The cap counts every image in the conversation, history included.
3. **KDA BF16 is numerics-neutral.** Prompt-logprob KL on the IP-310 corpus (2,627 positions) is 0.0062 nats vs the
   BF16 dense reference (v3: 0.0061) and 0.0041 vs v3 itself.
4. **Why B beats a KDA profile for this lane.** On production traffic ~90% of prompt tokens come from the prefix cache
   (v3 lifetime hit ratio 0.897), so KDA's gain touches ~10% of prompt work, while the KV blocks it would cost set how
   many long sessions run at once. Decode (+7.7%) and cache reuse (0.43 s repeats, 2× tokens) are where agent latency
   is spent.

## What was done

1. **Research.** Upstream f906ee99 → 0f49cfd (240 commits: 153 files added, 36 modified, none deleted) read in a
   local clone with file:line citations. Pre-flight re-run: every pinned model artifact re-fetched and hash-verified
   unchanged; the overlay chat template is byte-identical to v3's and six rendered cases match v3's renders exactly.
2. **Build.** `glm53-exl3-local:0f49cfd` (sha256:bd4d789050da, stamp e7b9e9a866c217eb) built on <head-host> at 06:15Z
   with a 4 GiB build-memory cap while v3 served; shipped to <worker-host> by `docker save | load` before any takedown. Same
   vLLM base (905c0293) and ExLlamaV3 pin (c5d9c657); the thin-decode kernels are compiled in.
3. **Window.** v3 drained and taken down with `~/safe-takedown.sh --peer <worker-host>`; arms booted on :8001 so the
   gateway's GLM routes fell back (zAI; NoThink → Qwen3.8 Extract). The lane was declared on :8001 for the window
   (cluster-services.yaml + the fleet-probe REGISTRY marker, deployed to the console host's checkout).
4. **Arms.** A = v3 re-booted as the control (full battery). B = candidate at v3's pin (full battery + claim probes;
   PASS). C′ = B + KDA + 8 GiB pin (decode, claim probes, KL, needles, ablit, prefill ladder; stopped on the c12
   failure). Every boot verified by effect: the knobs read back from BOTH containers, image id on both ranks,
   transplant fingerprint, warm-up, a greedy completion checked for content.
5. **Final.** B with `LIMIT_MM` image 8 booted on :8000 (launch 09:39:25Z → ready 09:50:10Z, warm-up 24/24), verified by
   effect (knobs on both containers, 532 usable blocks, 32 transplant lines, host floor head 7.27 / worker 9.37 GiB,
   greedy content check). Refusal probe 8/8. Gateway parity against the <gateway-host> :4000 routes at T=0: NoThink,
   Low and High match direct on prompt tokens, completion tokens, finish reason and reasoning (High: 63 reasoning
   chars both, correct answer both). Declaration back on :8000 / recipe v5, fleet-probe at HEAD, both deployed to
   the console host's checkout, `console-parity-gate.sh check` exit 0. Tunnel closed; the live harness constant is back
   on :8000.
6. **Repo.** Overlay re-vendored to 0f49cfd in `models/runtimes/dual-spark-cluster/glm53-flash-exl3/miaai-overlay/`
   (+ RUIN-DELTAS §1), byte-identical to the live tree (257 files, path + sha256) apart from its `.env`, the
   transplant set and the §3 harness constants; `VENDORED.txt` and `RUIN-DELTAS.md` (new §6: the ABLIT reset)
   updated. Recipe v5 (draft), the results record, and this report.

## Gates (IP-327 §4) — final configuration = B + image cap 8

| Gate | Result |
|---|---|
| KV pool ≥ 640,000 tokens | 1,421,333 |
| Worst-case MemAvailable ≥ 3 GiB | 3×250k: head 4.66 / worker 6.15 GiB; 8 images: head 3.29 GiB |
| No class below −3% of A; c12 within −3% | lowest −1.3% (JSON, n=1); c12 +7.4% |
| Needles exact | 8k, 128k, and 11/11 in the stress |
| Ablit probe complies | 8/8 on the same-seed rerun (7/8 first draw: a borderline redirect, the IP-310 pattern) |
| Thinking-off: stories ≥ 3/4, JSON valid, code parses, 0 reasoning chars | 3/4, valid, parses, 0 |
| Warm session ≤ 30 s during a long prefill | ≤ 8.5 s |
| Warm-up complete; transplant present | 24/24; 32 lines, fingerprint identical to v3 |
| LCB thinking-off (no regression) | 2/3 (v3 0/3) |

## Deviations from the plan

- Arm C was replaced by C′ (KDA funded by a smaller pin) after B showed the doubled token capacity; C′ disproved the
  funding model and was stopped after its prefill ladder (stress, client-side battery and vision ladder not run).
- The vision gate was widened from "8 images" to a stepped ladder up to the declared cap; it stopped at 8 → the cap.
- Needles ran at 8k and 128k@10% (not 128k@90%); the stress adds 11 needles per arm.

## Open items for David

- **Image cap 8** (was 100). Accepted in principle 2026-09-24 ("the image thing is fine … I can always push image
  understanding elsewhere"). Screenshot-heavy agents should keep the last 3–5 screenshots or use a vision lane.
  `MM_IMAGE_TOKENS=1024` was measured and rejected for web pages: on a 1920×1080 page it read 2/12, 3/12, 4/12 and
  8/12 six-character codes at 11, 12, 13 and 14 px (2048: 10/12, 10/12, 12/12, 12/12); 16 px and up read 12/12 at
  both. A client may still request 1024 per call (`mm_processor_kwargs`) for large-text images.
- **Video** keeps upstream's default (1 video, 32 frames) — unmeasured here; the same per-frame memory cost applies.
- **Promoted** 2026-09-24 ("That's great. I'll sign off on that."); v3 stays on both ranks as the rollback. Scrubbed backup to
  `abliter8-ai/glm-5.3-flash-240926` after David reviews the file list.
- **Gateway fallbacks** on the 12 GLM routes are unchanged (they fire only when :8000 refuses).
