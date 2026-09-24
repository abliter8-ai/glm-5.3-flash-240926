#!/usr/bin/env python3
"""IP-327: is MM_IMAGE_TOKENS=1024 enough for web-page screenshots? Renders a 1920x1080 page-like image with
24 labelled rows of random 6-character codes at 11/12/13/14/16/20 px (Arial, 4 rows per size, two columns), asks
the lane to transcribe every row at several per-request image-token budgets (vLLM mm_processor_kwargs
max_image_tokens), and scores exact matches per text size. Thinking off, temperature 0.
Usage: python3 image_tokens_readability.py <out.json> [base_url] [seeds]
"""
import base64, io, json, random, sys, time, urllib.request
from PIL import Image, ImageDraw, ImageFont

OUT = sys.argv[1]
BASE = sys.argv[2] if len(sys.argv) > 2 else "http://__HEAD_HOST__:8000"
SEEDS = [int(x) for x in sys.argv[3].split(",")] if len(sys.argv) > 3 else [11, 12, 13]
SIZES = [11, 12, 13, 14, 16, 20]
BUDGETS = [2048, 1024, 768]
ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"   # no O/0, I/1/L ambiguity
FONT = "/System/Library/Fonts/Supplemental/Arial.ttf"


def page(seed):
    rng = random.Random(seed)
    img = Image.new("RGB", (1920, 1080), "white")
    d = ImageDraw.Draw(img)
    d.rectangle([0, 0, 1920, 64], fill=(36, 41, 47))
    d.text((32, 18), "Fleet console - release checks", font=ImageFont.truetype(FONT, 24), fill="white")
    rows, n = {}, 0
    for col, x in ((0, 80), (1, 1000)):
        y = 120
        for size in SIZES[col * 3:(col + 1) * 3]:
            f = ImageFont.truetype(FONT, size)
            d.text((x, y), f"{size}px section", font=ImageFont.truetype(FONT, 18), fill=(90, 90, 90))
            y += 34
            for _ in range(4):
                n += 1
                code = "".join(rng.choice(ALPHABET) for _ in range(6))
                rows[n] = (size, code)
                d.text((x, y), f"Row {n:02d}   status: pending   code {code}   owner: build-bot", font=f, fill=(20, 20, 20))
                y += size + 18
            y += 40
    buf = io.BytesIO(); img.save(buf, "PNG")
    return base64.b64encode(buf.getvalue()).decode(), rows


def ask(b64, budget):
    body = {"model": "glm-5.3-flash-uncensored", "temperature": 0, "max_tokens": 1024,
            "chat_template_kwargs": {"enable_thinking": False},
            "mm_processor_kwargs": {"max_image_tokens": budget},
            "messages": [{"role": "user", "content": [
                {"type": "text", "text": "List every row in this screenshot as 'NN: CODE' (the row number and the 6-character "
                                         "value after the word 'code'), one per line, nothing else."},
                {"type": "image_url", "image_url": {"url": "data:image/png;base64," + b64}}]}]}
    t = time.time()
    r = json.load(urllib.request.urlopen(urllib.request.Request(BASE + "/v1/chat/completions", data=json.dumps(body).encode(),
                                                                headers={"Content-Type": "application/json"}), timeout=300))
    return r["choices"][0]["message"].get("content") or "", r["usage"]["prompt_tokens"], round(time.time() - t, 1)


def score(text, rows):
    got = {}
    for line in text.splitlines():
        line = line.strip().replace("*", "")
        if ":" in line:
            k, v = line.split(":", 1)
            k = "".join(ch for ch in k if ch.isdigit())
            if k:
                got[int(k)] = v.strip().split()[0] if v.strip() else ""
    per = {s: [0, 0] for s in SIZES}
    for n, (size, code) in rows.items():
        per[size][1] += 1
        per[size][0] += int(got.get(n, "").upper() == code)
    return per


results = []
for seed in SEEDS:
    b64, rows = page(seed)
    for budget in BUDGETS:
        text, ptok, secs = ask(b64, budget)
        per = score(text, rows)
        results.append({"seed": seed, "budget": budget, "prompt_tokens": ptok, "seconds": secs,
                        "exact_by_px": {s: f"{a}/{b}" for s, (a, b) in per.items()}, "text": text})
        print(f"seed {seed} budget {budget:4d} prompt_tokens {ptok:5d} {secs:5.1f}s  " +
              "  ".join(f"{s}px {a}/{b}" for s, (a, b) in per.items()), flush=True)
tot = {}
for r in results:
    for s, ab in r["exact_by_px"].items():
        a, b = map(int, ab.split("/")); t = tot.setdefault(r["budget"], {}).setdefault(s, [0, 0]); t[0] += a; t[1] += b
print("TOTAL " + " | ".join(f"{b}: " + " ".join(f"{s}px {a}/{n}" for s, (a, n) in tot[b].items()) for b in BUDGETS))
json.dump({"results": results, "totals": {b: {s: f"{a}/{n}" for s, (a, n) in v.items()} for b, v in tot.items()}}, open(OUT, "w"), indent=1)
