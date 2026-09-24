# Our deltas to the vendored MiaAI overlay (@ 0f49cfd, re-vendored 2026-09-24 under IP-327; was f906ee99)

Kept deliberately minimal so this tree stays honest against upstream.

## 1. `start.sh` — NCCL_IB_MERGE_NICS made env-overridable (IP-285; re-applied IP-289, IP-310 and IP-327 at `start.sh:1982`)

```diff
-        -e NCCL_IB_MERGE_NICS=0
+        -e "NCCL_IB_MERGE_NICS=${NCCL_IB_MERGE_NICS:-0}"
```

Upstream still hardcodes single-rail (`start.sh:1381` at f906ee99). On the GB10 the two "rails" are two
PCIe x4 functions on the SAME physical ConnectX-7 port, so dual-rail is about reaching link line rate,
not adding a second cable: we measure 196 Gb/s merged vs 109 Gb/s on one function.

**Default is unchanged (0)** — this only makes the value settable. Set `NCCL_IB_MERGE_NICS=1`
plus comma-listed HCAs in `HEAD_CX7_IB` / `WORKER_CX7_IB` to enable.

Expected to matter for PREFILL and large-batch all-reduce, not single-stream decode:
at `MAX_NUM_BATCHED_TOKENS=8192` a per-layer all-reduce is 8192x4096x2B = 64 MiB
(~4.9 ms/layer single-rail vs ~2.7 ms dual-rail, ~100 ms per chunk across 45 layers),
whereas single-stream decode moves only ~8 KB per layer and is latency-bound.
Measured 2026-09-01: +4-6% prefill, nothing reliable on decode.

## 2. RETIRED 2026-09-12 — GID probe on a comma-separated dual-rail HCA list

Upstream PR #172 (merged 2026-09-12, `9aebd2a1`) validates the RoCE GID on every listed CX7 HCA, so
the six-site `${HEAD_CX7_IB%%,*}` patch we carried since IP-285 is no longer needed. Verify on the
first IP-310 launch that the GID probe accepts `HEAD_CX7_IB=rocep1s0f1,roceP2p1s0f1`; if it dies at
the probe, the retired patch is in git history (`RUIN-DELTAS.md` @ eb0469fb).

## 3. `tests/bench_decode.py` on the LIVE tree only — port and served name (IP-289, re-applied on every sync)

Upstream's decode harness hardcodes `BASE = http://127.0.0.1:8888` and `MODEL = "GLM-5.3-Flash-EXL3"`.
Our head serves on :8000 as `glm-5.3-flash-uncensored`, so the harness 404s as shipped. On the
<head-host> working copy both constants are patched (comment `# RUIN-DELTA`); the repo copy stays
upstream-pristine. Upstream also changed this file at f906ee99 (bearer auth for keyed runs, PR #136);
re-apply the two constants after syncing, or the per-boot battery silently loses its single-stream
and c12 rows (it did on the first IP-289 B0 pass).

## 4. Template ahead of the vendored commit during IP-310 L0 (transitional)

IP-310 L0 puts `files/chat_template.jinja` @ f906ee99 (sha256 `7a5a0dda…`) on the live tree while the
rest of the live tree is still eb0469fb. Once L1 syncs the whole tree this section is moot; it exists
so the intermediate state is recorded, not inferred.

## 5. `ablit/transplant/` is NOT in this tree — every new live tree must carry it (learned IP-310 L1 attempt 1)

The abliteration transplant set (32 `.bin`, L15–L45 o_proj tensors from the dealign donor, 2.6 GB) is
fetched per node by `ablit/fetch_transplant.py` (IP-285) and is gitignored upstream and here. A tree
synced from this repo therefore boots with an empty `ablit/transplant/`, and with `ABLIT_METHOD=transplant`
both ranks fail closed at weight load (`AblitError … run: python3 ablit/fetch_transplant.py`). On the
Sparks the set lives in `~/glm53-exl3-overlay/ablit/transplant/`; hardlink it into any new tree
(`cp -al`, same filesystem) rather than re-fetching 2.6 GB. Keep `ABLIT_METHOD=transplant` explicit:
`auto` silently falls back to the projection edit when the directory is missing, which garbles output.

## 6. `start.sh` @ 0f49cfd resets `ABLIT=0` after sourcing `.env` (IP-327) — export it, do not patch it

Upstream now forces stock `o_proj` unless the CALLER exported `ABLIT` (start.sh:89-92): the `.env` value is
cleared, then caller exports are re-applied (start.sh:76-109). Our `.env` still says `ABLIT=1` for readability,
but it is the boot command that must export `ABLIT=1 ABLIT_METHOD=transplant` (IP-327 `boot_arm.sh` does). A boot
that forgets the export serves the stock, censored checkpoint under the uncensored name; the transplant lines in
the head log (32 × `ablit: transplanted layers.N.self_attn.o_proj`) are the check. No patch: the reset is upstream's
safety default and caller exports are the supported opt-in.

## Things we deliberately did NOT change

- `docker rm -f` in their teardown. We simply never call their `stop`/`restart`; we use
  `~/safe-takedown.sh --peer <worker-host>`. Patching their script here would diverge us further for no
  benefit, since we don't use that path.
- Upstream defaults we override only through `.env` / `launch.env`, never in `start.sh`:
  `MAX_NUM_SEQS` 4 → 12, `MAX_MODEL_LEN` 850000 → 640000, `GPU_MEM_UTIL` 0.85 → 0.845,
  `EXL3_TEMP_ROWS_FUSED` 32 → 128 (must stay ≥ seqs × (k+1) = 96 with E3), `GLM53_MIXED_PREFILL_CHUNK`
  fair (upstream default since 2026-09-15) → 1024 (IP-327: the FAIR_* knobs are then inert), the KV pool pin
  (`--kv-cache-memory-bytes`), `LIMIT_MM`, and `DFLASH_REVISION` (7d74cdd8, not upstream's dc77ff1c).
  `MAX_NUM_BATCHED_TOKENS` matches upstream (7168) since IP-310 L1b.
