# IP-327 kit — build, boot, verify and the standard battery for the GLM-5.3-Flash EXL3 TP=2 lane (recipe v5)

The exact scripts that produced the IP-327 results record (`models/registry/results/…__v5__20260924__ip327-upstream-optins.yaml`).

| Where | Install | Scripts |
|---|---|---|
| Head (<head-host>) | `head/` → `~/ip327/`, `head/probes/` → `~/ip327/probes/` | `build.sh` (local image build, 4 GiB build cap, transplant moved aside, recipe stamp as start.sh), `boot_arm.sh <label> <tree> <8000\|8001> <kda 0\|1> [kv_bytes]` (drain → `~/safe-takedown.sh --peer <worker-host>` → `start.sh start` with `ABLIT=1 ABLIT_METHOD=transplant` exported; fails on an incomplete warm-up or a missing transplant), `verify_boot.sh <label> <port>` (knobs read back from BOTH containers, image ids, KV pool, host floor, greedy content check), `spark_battery.sh <label> <tree> <full\|control\|kda>`, `vision_check.sh <label> "<rungs>"` (stepped image ladder with a projection stop at 3 GiB) |
| Client (<client-host>, via a tunnel to the lane) | anywhere; `BASE=http://127.0.0.1:18000` | `arm_rest.sh <label> "<rungs>" <c12rerun>` (waits for the spark battery, then `l0_client.sh`, `lcb_trio.sh`, the vision ladder), `accept_probe.py`, `l0_thinkoff_battery.py`, `warmpoll_probe.py`, `stagger_probe.py`, `kl_probe.py capture\|compare`, `image_tokens_readability.py` |

Order used: build → boot (arm on :8001, :8000 closed so the gateway falls back) → verify → spark battery → arm_rest →
KL (when numerics change) → final boot on :8000 → verify → refusal probe → gateway parity.
`probes/ip314_acceptance.py` here differs from `../ip314_acceptance.py` only in accepting any `--vision-max-images`
rung. Host addresses and the SSH user in these scripts are this fleet's; paths assume the trees under `~/`.
