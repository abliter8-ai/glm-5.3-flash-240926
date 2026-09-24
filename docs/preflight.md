# Pre-flight — Mia-AiLab/GLM-5.3-Flash-EXL3-TR3-4bpw (IP-327, 2026-09-24)

Model: `Mia-AiLab/GLM-5.3-Flash-EXL3-TR3-4bpw` @ `25a44fdbf16862a46b7cc9921142c6c81350af2f` (unchanged since IP-285).
Served as `glm-5.3-flash-uncensored`, TP=2 on <head-host> (head) + <worker-host> (worker), vLLM via the MiaAI-Lab
GLM-5.3-Flash-EXL3-2x-DGX-Sparks overlay. Change: overlay f906ee99 → 0f49cfd (2026-09-23) with TP=2 opt-ins.
Previous pre-flight: `preflight-20260901.md` (IP-285).

## Sources consulted (this session)

- `artifacts-20260924.md` — model-artifact-scout: every pinned artifact re-fetched and hash-verified unchanged
  (chat_template.jinja, tokenizer_config.json, config.json, generation_config.json, README); base
  zai-org/GLM-5.3-Flash checked. EOS ids 154820/154827/154829, pad 154820.
- `serving-path-research-20260924.md` — model-docs-researcher: LiteLLM 1.102.1 source (BerriAI/litellm@main) and the
  overlay at 0f49cfd (Context7 unavailable in that agent; source read instead).
- Upstream clone at 0f49cfd: CHANGELOG, README, start.sh, docs/sm121-perf-paths.md, docs/kda-bf16-large-m.md,
  examples/tp2-long-coding.env (extraction in the IP-327 working notes, cited file:line).

## Step 4 — recommended configuration (recorded)

| Item | Value | Source |
|---|---|---|
| Chat template | overlay `files/chat_template.jinja`, sha256 `7a5a0dda1331a7c4…` — **byte-identical f906ee99 → 0f49cfd** (`git diff` empty). Served via `--chat-template`. The checkpoint's own template is NOT used (it ignores enable_thinking and blocks vision). | upstream clone; artifact scout |
| System prompt | none required; one leading system message (template does not merge duplicates) | template; serving-path research |
| Thinking | `chat_template_kwargs.enable_thinking` (bool) is the only reliable switch. `reasoning_effort` in chat_template_kwargs: `low`/`high`; any other value (incl. `none`) renders **Max**. Never send top-level OpenAI `reasoning_effort`. | template line 8; serving-path research |
| Stops / EOS | 154820 / 154827 / 154829 (generation_config); stop strings suppressed inside reasoning (overlay patch) | artifact scout |
| Output budget | `DEFAULT_MAX_NEW_TOKENS=65536` applies only when the request omits max_tokens; thinking tiers keep explicit gateway budgets | start.sh:223-235 |
| Parsers | `--reasoning-parser glm45`, `--tool-call-parser glm47 --enable-auto-tool-choice`; `tool_choice:"none"` now enforced at decode (bad_words `<tool_call>`) | start.sh; overlay/patch_tool_choice_none.py |
| Vision | `LIMIT_MM` image 48 / video 1, `MM_IMAGE_TOKENS=2048`, `MM_PROCESSOR_CACHE_GB=1` (upstream OOM caps) | start.sh:273-294 |

## Step 5 — rendered prompt (gate passed)

`rendered-prompt-overlay-20260924.txt` = the overlay template at 0f49cfd applied with the pinned tokenizer, 6 cases
(multi-turn; + system; thinking off; thinking on low; thinking on high; tool call + tool result, thinking off).
All 6 are **identical** to the same renders with the f906ee99 template (the production template). Thinking off
renders `<|assistant|><think></think>` with no effort line; thinking on renders `<|system|>Reasoning Effort: Low|High`
and an open `<think>`; tools render the XML `<tool_call>…<arg_key>/<arg_value>` form and `<|observation|><tool_response>`.
The checkpoint-template render (`model-render-prompt.py`) is identical to `rendered-prompt.txt` of 2026-09-01.

## Step 6 — serving path

client (OpenWebUI, Hermes, Honcho, Cognee BAML, scripts) → **LiteLLM 1.102.1** on <gateway-host> :4000 (routes set
`chat_template_kwargs` per tier; forwards them unchanged; renames streaming `reasoning` → `reasoning_content`; no
system-prompt injection — verified 2026-09-23 by equal prompt_tokens gateway vs direct; fallbacks to zAI / Qwen3.8)
→ **vLLM overlay** on <head-host> :8000 (owns template, parsers, stop suppression, omitted-max_tokens default,
tool_choice none, sampling defaults = none server-side) → model. A gateway is in the path, so the final verification
compares a direct and a gateway call (prompt_tokens, reasoning presence, finish reason) before any success claim.

## Step 7 — launch

`~/ip327/boot_arm.sh <arm> <tree> <port> <kda>` on <head-host> (never the overlay's stop/restart): drains, takes down with
`~/safe-takedown.sh --peer <worker-host>`, then `./start.sh start` from the tree with `SKIP_PULL/BUILD/SHIP/DOWNLOAD/SYNC=1`,
exporting `PORT`, `ABLIT=1 ABLIT_METHOD=transplant` (start.sh @0f49cfd resets ABLIT after `.env`) and
`GLM53_KDA_BF16_LARGE_M`. The tree's `.env` carries the v3 posture (640000 ctx, 12 seqs, MNBT 7168, KV pinned 13.5 GiB,
dual-rail NCCL, DFlash2 k=7 @ 7d74cdd8, adaptive-k ema 2,4,7 margin 2.0, dense FP8 dense+kda, E3, mixed prefill 1024)
plus the IP-327 opt-ins (`GLM53_EXL3_MOE_FAST=1`, `GLM53_DRAFT_KV_COMPACT=1`, `GLM53_SPINWAIT_MS=16`) and pinned
upstream defaults (`LOAD_FORMAT=` auto, vision caps, `DEFAULT_MAX_NEW_TOKENS=65536`, `GLM53_EXPOSE_CACHE_RESET=0`).
The script fails the arm on an incomplete boot-shape warmup or a missing transplant line.

## Serving-path parity (2026-09-24, final boot on :8000 — IP-327 / CR-327)

Paths: direct `<head-host>:8000/v1/chat/completions` with the tier's `chat_template_kwargs` vs the LiteLLM 1.102.1 route on
<gateway-host> :4000 (virtual key), identical messages, temperature 0.

| Tier (route) | Direct kwargs | prompt tokens | completion tokens | finish | reasoning chars |
|---|---|---|---|---|---|
| NoThink (`Abliter8/GLM-5.3-Flash-NoThink`) | `enable_thinking: false` | 23 / 23 | 50 / 50 | stop / stop | 0 / 0 |
| Low (`Abliter8/GLM-5.3-Flash-Low`) | `enable_thinking: true, reasoning_effort: low` | 29 / 29 | 61 / 61 | stop / stop | 0 / 0 (trivial prompt) |
| High (`Abliter8/GLM-5.3-Flash-High`) | `enable_thinking: true, reasoning_effort: high` | 53 / 53 | 48 / 48 | stop / stop | 63 / 63 |

Verdict: parity — the gateway renders and forwards the same prompt and returns the same reasoning split as the
engine. Answers identical (High: "16:10", correct).
