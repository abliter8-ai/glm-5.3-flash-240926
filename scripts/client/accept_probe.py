#!/usr/bin/env python3
"""Measure DFlash2 acceptance per workload class on the live lane, at serving posture.

Method: read vllm:spec_decode_* counters before/after each single-stream request and
report the delta: acceptance rate (accepted / drafted), accepted-per-step, tokens-per-step
(1 + accepted/step), per-position acceptance, and wall-clock tok/s.
"""
import json, sys, time, urllib.request, re

BASE = "http://127.0.0.1:18000"
MODEL = "glm-5.3-flash-uncensored"
K = 7

def metrics():
    txt = urllib.request.urlopen(BASE + "/metrics", timeout=30).read().decode()
    out = {"drafts": 0.0, "draft_tokens": 0.0, "accepted": 0.0, "pos": [0.0] * K}
    for line in txt.splitlines():
        if not line.startswith("vllm:spec_decode"):
            continue
        name, val = line.rsplit(" ", 1)
        val = float(val)
        if name.startswith("vllm:spec_decode_num_drafts_total"):
            out["drafts"] = val
        elif name.startswith("vllm:spec_decode_num_draft_tokens_total"):
            out["draft_tokens"] = val
        elif name.startswith("vllm:spec_decode_num_accepted_tokens_total"):
            out["accepted"] = val
        elif name.startswith("vllm:spec_decode_num_accepted_tokens_per_pos_total"):
            m = re.search(r'position="(\d+)"', name)
            if m:
                out["pos"][int(m.group(1))] = val
    return out

def chat(messages, thinking, effort, temperature, top_p, max_tokens, tools=None):
    body = {
        "model": MODEL, "messages": messages, "temperature": temperature, "top_p": top_p,
        "max_tokens": max_tokens, "stream": False,
        "chat_template_kwargs": {"enable_thinking": thinking, **({"reasoning_effort": effort} if thinking else {})},
    }
    if tools:
        body["tools"] = tools
        body["tool_choice"] = "auto"
    req = urllib.request.Request(BASE + "/v1/chat/completions", data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    t0 = time.time()
    resp = json.loads(urllib.request.urlopen(req, timeout=900).read().decode())
    dt = time.time() - t0
    return resp, dt

TOOLS = [
    {"type": "function", "function": {"name": "read_file", "description": "Read a file from the repository",
        "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}}},
    {"type": "function", "function": {"name": "run_shell", "description": "Run a shell command and return stdout/stderr",
        "parameters": {"type": "object", "properties": {"cmd": {"type": "string"}, "timeout_s": {"type": "integer"}}, "required": ["cmd"]}}},
    {"type": "function", "function": {"name": "write_file", "description": "Write content to a file",
        "parameters": {"type": "object", "properties": {"path": {"type": "string"}, "content": {"type": "string"}}, "required": ["path", "content"]}}},
]

AGENT_SYS = ("You are a coding agent operating in a git repository. Use the tools to inspect and change "
             "the code. Think briefly, then act. Always call a tool when you need information.")
AGENT_USER = ("The CI job `lint` fails with `ruff: F401 unused import 'os'` in src/app/config.py line 3. "
              "Fix it. Start by reading the file, then apply the smallest change and re-run ruff on that file.")

CASES = [
    # name, messages, tools, thinking, effort, T, top_p, max_tokens
    ("agentic_toolcall_think_high_T1.0", [{"role": "system", "content": AGENT_SYS}, {"role": "user", "content": AGENT_USER}], TOOLS, True, "high", 1.0, 0.95, 3000),
    ("agentic_toolcall_think_high_T0.6", [{"role": "system", "content": AGENT_SYS}, {"role": "user", "content": AGENT_USER}], TOOLS, True, "high", 0.6, 0.95, 3000),
    ("code_module_think_high_T1.0", [{"role": "user", "content": "Write a Python module implementing an LRU cache with TTL expiry, thread-safe, with a small pytest suite. Return only code."}], None, True, "high", 1.0, 0.95, 3000),
    ("prose_essay_think_high_T1.0", [{"role": "user", "content": "Write a 700-word essay on why lighthouses were automated, for a general audience. No headings, no lists."}], None, True, "high", 1.0, 0.95, 3000),
    ("prose_essay_nothink_T1.0", [{"role": "user", "content": "Write a 700-word essay on why lighthouses were automated, for a general audience. No headings, no lists."}], None, False, None, 1.0, 0.95, 1400),
    ("prose_essay_nothink_T0.6", [{"role": "user", "content": "Write a 700-word essay on why lighthouses were automated, for a general audience. No headings, no lists."}], None, False, None, 0.6, 0.95, 1400),
    ("json_extract_nothink_T1.0", [{"role": "user", "content": "Extract every product mentioned into a JSON array of {name, price, currency}. Text: 'The ACME kettle sells for 39.99 EUR, the Bosch toaster for 45 EUR and the Philips airfryer for 129.50 USD; the Krups grinder is 60 EUR.' Output only JSON."}], None, False, None, 1.0, 0.95, 400),
]

rows = []
for name, msgs, tools, thinking, effort, T, top_p, mt in CASES:
    m0 = metrics()
    try:
        resp, dt = chat(msgs, thinking, effort, T, top_p, mt, tools)
    except Exception as e:
        print(f"[{name}] ERROR {e}", flush=True)
        continue
    m1 = metrics()
    u = resp.get("usage", {})
    ch = resp["choices"][0]
    msg = ch["message"]
    comp = u.get("completion_tokens", 0)
    rc = len(msg.get("reasoning_content") or msg.get("reasoning") or "")
    cc = len(msg.get("content") or "")
    tc = len(msg.get("tool_calls") or [])
    d = m1["drafts"] - m0["drafts"]
    dtok = m1["draft_tokens"] - m0["draft_tokens"]
    acc = m1["accepted"] - m0["accepted"]
    pos = [m1["pos"][i] - m0["pos"][i] for i in range(K)]
    row = {
        "case": name, "completion_tokens": comp, "reasoning_chars": rc, "content_chars": cc, "tool_calls": tc,
        "finish": ch.get("finish_reason"), "wall_s": round(dt, 1), "tok_s": round(comp / dt, 1) if dt else None,
        "drafts": int(d), "draft_tokens": int(dtok), "accepted": int(acc),
        "accept_rate": round(acc / dtok, 3) if dtok else None,
        "accepted_per_step": round(acc / d, 2) if d else None,
        "tokens_per_step": round(1 + acc / d, 2) if d else None,
        "pos_accept": [round(p / d, 3) if d else None for p in pos],
    }
    if tc:
        row["first_tool_call"] = msg["tool_calls"][0]["function"]
    rows.append(row)
    print(json.dumps(row), flush=True)

print("\n=== SUMMARY ===")
print(f"{'case':38} {'comp':>5} {'tok/s':>6} {'acc':>6} {'acc/step':>8} {'tok/step':>8}  pos0..6")
for r in rows:
    print(f"{r['case']:38} {r['completion_tokens']:>5} {r['tok_s']:>6} {r['accept_rate']:>6} {r['accepted_per_step']:>8} {r['tokens_per_step']:>8}  {r['pos_accept']}")
json.dump(rows, open(sys.argv[1] if len(sys.argv) > 1 else "/dev/null", "w"), indent=1)
