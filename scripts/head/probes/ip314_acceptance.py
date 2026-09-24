#!/usr/bin/env python3
"""IP-314 synthetic acceptance cells; stdlib only, resumable JSON receipts.

Run against an isolated direct-engine endpoint. Aggregate engine counters are
attributed to one request only when both gauges are idle and all counts match.
"""
import argparse
import base64
import concurrent.futures
import hashlib
import json
import os
import re
import struct
import threading
import time
import urllib.error
import urllib.request
import uuid
import zlib


def _round(x):
    return round(x, 6) if x is not None else None


def consume_sse(chunks, submitted, clock=time.monotonic, first_event=None):
    if not callable(clock):
        clock = iter(clock).__next__
    buf, stamps, usage, finish, backend = b"", [], None, None, None
    assembled = {"reasoning": [], "content": [], "tool": []}
    first_kind, ended, done = None, submitted, False
    for chunk in chunks:
        buf += chunk
        while b"\n\n" in buf.replace(b"\r\n", b"\n"):
            buf = buf.replace(b"\r\n", b"\n")
            raw, buf = buf.split(b"\n\n", 1)
            payload = b"\n".join(line[5:].strip() for line in raw.splitlines() if line.startswith(b"data:"))
            if not payload:
                continue
            ended = clock()
            if payload == b"[DONE]":
                done = True
                continue
            event = json.loads(payload)
            if event.get("error"):
                raise RuntimeError(str(event["error"]))
            backend = event.get("model") or backend
            if event.get("usage") is not None:
                usage = event["usage"]
            choices = event.get("choices") or []
            if not choices:
                continue
            choice = choices[0]
            finish = choice.get("finish_reason") or finish
            delta = choice.get("delta") or {}
            values = {"reasoning": delta.get("reasoning_content") or delta.get("reasoning"),
                      "content": delta.get("content"), "tool": delta.get("tool_calls")}
            kinds = [k for k, v in values.items() if v]
            if kinds:
                stamps.append(ended)
                for kind in kinds:
                    assembled[kind].append(values[kind])
                if first_kind is None:
                    first_kind = kinds[0]
                    if first_event is not None:
                        first_event.set()
    details = (usage or {}).get("completion_tokens_details") or {}
    pdetails = (usage or {}).get("prompt_tokens_details") or {}
    decode_s = stamps[-1] - stamps[0] if len(stamps) > 1 else None
    ct = (usage or {}).get("completion_tokens")
    return {"ttft_s": _round(stamps[0] - submitted) if stamps else None,
            "first_generated_monotonic": stamps[0] if stamps else None,
            "last_generated_monotonic": stamps[-1] if stamps else None,
            "finished_monotonic": ended, "first_generated_kind": first_kind,
            "total_time_s": _round(ended - submitted),
            "longest_gap_s": _round(max((b-a for a,b in zip(stamps, stamps[1:])), default=0)),
            "stream_decode_s": _round(decode_s),
            "stream_decode_tok_s": _round((ct-1)/decode_s) if ct and decode_s else None,
            "decode_rate_basis": "completion tokens minus first token / first-to-last generated delta (SSE batching applies)",
            "usage": usage, "reasoning_tokens": details.get("reasoning_tokens"),
            "cached_tokens": pdetails.get("cached_tokens"), "backend_model": backend,
            "assembled": assembled, "finish_reason": finish, "sse_done": done,
            "generated_delta_times": stamps}


def error_record(exc, submitted, finished=None):
    rec = {"ttft_s": None, "total_time_s": _round((finished or time.monotonic())-submitted),
           "usage": None, "finish_reason": None, "error_type": type(exc).__name__, "error": str(exc)[:500]}
    if isinstance(exc, urllib.error.HTTPError):
        rec.update(http_status=exc.code, response=exc.read().decode(errors="replace")[:1000])
    return rec


def save_state(path, config, completed):
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    tmp = str(path) + ".tmp"
    with open(tmp, "w") as f:
        json.dump({"config": config, "completed": completed, "updated_at": time.time()}, f, indent=2)
        f.flush(); os.fsync(f.fileno())
    os.replace(tmp, path)


def load_state(path, config):
    if not os.path.exists(path):
        return {"config": config, "completed": []}
    with open(path) as f:
        state = json.load(f)
    if state["config"] != config:
        raise ValueError("configuration mismatch: use a new receipt path")
    return state


def pending_cell_ids(ids, state):
    done = {c["cell_id"] for c in state["completed"] if c.get("status", "passed") == "passed"}
    return [c for c in ids if c not in done]


def metrics(base_url):
    try:
        with urllib.request.urlopen(base_url.rstrip("/")+"/metrics", timeout=10) as r:
            txt = r.read().decode()
    except Exception as exc:
        return {"error": str(exc)}
    out = {}
    keys = {"num_requests_running": "running", "num_requests_waiting": "waiting",
            "request_success_total": "request_success_count", "num_preemptions_total": "preemptions"}
    for phase in ("queue", "prefill", "decode", "inference"):
        for suffix in ("count", "sum"):
            keys[f"request_{phase}_time_seconds_{suffix}"] = f"{phase}_{suffix}"
    for line in txt.splitlines():
        m = re.match(r'^vllm:([^\s{]+)(?:\{[^}]*\})?\s+([0-9.eE+-]+)$', line)
        if m and m[1] in keys:
            key = keys[m[1]]
            out[key] = out.get(key, 0) + float(m[2])
    return out


def attribute_metrics(before, after, sequential, unrelated_delta=0, expected=1):
    delta = {k: after[k]-v for k,v in before.items() if isinstance(v, (int,float)) and isinstance(after.get(k),(int,float))}
    idle = all(m.get(k) == 0 for m in (before, after) for k in ("running", "waiting"))
    counts = all(delta.get(k) == expected for k in ("request_success_count", "queue_count", "prefill_count", "decode_count"))
    valid = idle and counts and unrelated_delta == 0
    return {"valid_cell": valid, "attributed": valid and sequential, "scope": "per-request" if valid and sequential else "aggregate",
            "reason": "idle before/after and exact completed-request/histogram counts" if valid else "missing counters or contamination",
            "delta": delta}


def request_stream(base_url, body, first_event=None):
    req = urllib.request.Request(base_url.rstrip("/")+"/v1/chat/completions", data=json.dumps(body).encode(), headers={"Content-Type":"application/json"})
    submitted = time.monotonic()
    try:
        with urllib.request.urlopen(req, timeout=3600) as r:
            def chunks():
                while True:
                    x = r.read1(4096) if hasattr(r,"read1") else r.read(1)
                    if not x: break
                    yield x
            rec = consume_sse(chunks(), submitted, first_event=first_event)
            rec["http_status"] = r.status
    except Exception as exc:
        rec = error_record(exc, submitted)
    rec["submitted_monotonic"] = submitted
    return rec


def build_body(model, prompt, arm=None, mode="nothink", vision=False, max_tokens=400):
    return {"model":model,"messages":[{"role":"user","content":[{"type":"text","text":prompt}] if vision else prompt}],
            "max_tokens":max_tokens,"temperature":1.0,"top_p":0.95,"stream":True,"stream_options":{"include_usage":True},
            "chat_template_kwargs":{"enable_thinking":mode=="low", **({"reasoning_effort":"low"} if mode=="low" else {})}}


PARA = "Tensor parallelism divides a layer across devices and exchanges intermediate activations. Pipeline parallelism assigns complete layers to separate stages. Network latency, batch size and memory capacity affect both approaches. "
TAIL = "\nWrite a detailed 250-word explanation of the tradeoffs above for an engineer. Use complete prose and specific examples."


def tokenize(base_url, body):
    payload = {k:body[k] for k in ("model","messages","chat_template_kwargs")}
    req = urllib.request.Request(base_url.rstrip("/")+"/tokenize",data=json.dumps(payload).encode(),headers={"Content-Type":"application/json"})
    with urllib.request.urlopen(req, timeout=60) as r:
        data=json.load(r)
    count=data.get("count") or len(data.get("tokens",[]))
    if not count: raise RuntimeError("tokenize returned no count")
    return count


def calibrated_body(base, model, target, mode, nonce):
    prefix = "Synthetic test session " + nonce + ".\n"
    chars=target*4
    for _ in range(10):
        filler=(PARA*((chars//len(PARA))+1))[:chars]
        body=build_body(model,prefix+filler+TAIL,mode=mode)
        count=tokenize(base,body)
        if abs(count-target)<=8:
            body["min_tokens"]=200
            return body,count
        chars=max(1,chars+int((target-count)*len(PARA)/39))
    raise RuntimeError(f"prompt calibration failed: target {target}, actual {count}")


def run_two(base_url, bodies):
    barrier=threading.Barrier(len(bodies))
    def call(body):
        barrier.wait()
        return request_stream(base_url,body)
    t=time.monotonic()
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(bodies)) as pool:
        rec=list(pool.map(call,bodies))
    return {"threads":rec,"joint_duration_s":_round(time.monotonic()-t)}


def run_overlap(base_url, model, newcomer_body, delay_s=5.0, incumbent_body=None):
    event=threading.Event(); results={}; samples=[]; stop=threading.Event()
    long_body=incumbent_body or build_body(model,"Test "+uuid.uuid4().hex+"\n"+PARA*30+"\nWrite a continuous detailed essay of at least 1800 words about MoE inference.",max_tokens=1200)
    long_body["min_tokens"]=1200
    def incumbent(): results["long"]=request_stream(base_url,long_body,event)
    def poll():
        while not stop.is_set():
            samples.append({"t":time.monotonic(),**metrics(base_url)});stop.wait(1)
    monitor=threading.Thread(target=poll);monitor.start()
    thread=threading.Thread(target=incumbent);thread.start()
    started=time.monotonic()
    while not event.wait(.05) and thread.is_alive():
        if time.monotonic()-started>600: break
    if event.is_set():
        time.sleep(delay_s)
        if thread.is_alive():
            results["newcomer"]=request_stream(base_url,newcomer_body)
    thread.join();stop.set();monitor.join()
    a=results.get("long",{});b=results.get("newcomer",{})
    submitted=min(r['submitted_monotonic'] for r in (a,b) if r.get('submitted_monotonic'))
    finished=max(r.get('finished_monotonic',submitted+r.get('total_time_s',0)) for r in (a,b))
    results.update(joint_duration_s=_round(finished-submitted),stagger_s=delay_s,gauges=samples,
                   overlap_valid=bool(b and a.get("first_generated_monotonic",float("inf")) < b["submitted_monotonic"] < a.get("last_generated_monotonic",0)))
    return results


# A small built-in bitmap alphabet keeps the labelled vision fixture reproducible.
FONT={
 'A':['01110','10001','10001','11111','10001','10001','10001'],
 'B':['11110','10001','10001','11110','10001','10001','11110'],
 'C':['01111','10000','10000','10000','10000','10000','01111'],
 'D':['11110','10001','10001','10001','10001','10001','11110'],
 'E':['11111','10000','10000','11110','10000','10000','11111'],
 'I':['11111','00100','00100','00100','00100','00100','11111'],
 'O':['01110','10001','10001','10001','10001','10001','01110'],
 'P':['11110','10001','10001','11110','10000','10000','10000'],
 'R':['11110','10001','10001','11110','10100','10010','10001'],
 'T':['11111','00100','00100','00100','00100','00100','00100'],
 'V':['10001','10001','10001','10001','10001','01010','00100'],
 '0':['01110','10001','10011','10101','11001','10001','01110'],
 '1':['00100','01100','00100','00100','00100','00100','01110'],
 '2':['01110','10001','00001','00010','00100','01000','11111'],
 '3':['11110','00001','00001','01110','00001','00001','11110'],
 '4':['00010','00110','01010','10010','11111','00010','00010'],
 '5':['11111','10000','10000','11110','00001','00001','11110'],
 '6':['01110','10000','10000','11110','10001','10001','01110'],
 '7':['11111','00001','00010','00100','01000','01000','01000'],
 '8':['01110','10001','10001','01110','10001','10001','01110'],
 '9':['01110','10001','10001','01111','00001','00001','01110'],
 '-':['00000','00000','00000','11111','00000','00000','00000'],
 ' ':['00000']*7}


def synthetic_png(label="IP314", width=1920,height=1080, nonce=""):
    pixels=bytearray(b'\xff\xff\xff'*(width*height))
    def rect(x,y,w,h,color):
        row=bytes(color)*w
        for yy in range(y,min(y+h,height)):
            pos=(yy*width+x)*3;pixels[pos:pos+w*3]=row
    def text(s,x,y,scale):
        for char in s:
            for yy,row in enumerate(FONT[char]):
                for xx,bit in enumerate(row):
                    if bit=='1':rect(x+xx*scale,y+yy*scale,scale,scale,(0,0,0))
            x+=6*scale
    text(label,100,100,12)
    rect(150,400,300,220,(230,40,40));rect(850,400,300,220,(40,80,230))
    rect(450,500,400,10,(0,0,0));text("A",250,680,8);text("B",950,680,8)
    text("CODE 7314",100,860,3)
    if nonce:
        digest=hashlib.sha256(nonce.encode()).digest()
        for i,value in enumerate(digest):rect(width-80+i%8*8,height-40+i//8*8,8,8,(value,255-value,128))
    raw=b''.join(b'\0'+pixels[y*width*3:(y+1)*width*3] for y in range(height))
    def chunk(kind,data):return struct.pack('>I',len(data))+kind+data+struct.pack('>I',zlib.crc32(kind+data)&0xffffffff)
    return b'\x89PNG\r\n\x1a\n'+chunk(b'IHDR',struct.pack('>IIBBBBB',width,height,8,2,0,0,0))+chunk(b'IDAT',zlib.compress(raw))+chunk(b'IEND',b'')


def final_check(kind,rec):
    assembled=rec.get("assembled",{})
    if kind=="json":
        try:return json.loads(''.join(assembled.get('content',[])))=={"code":"ORCHID-7314","count":3}
        except (ValueError,TypeError):return False
    calls={}
    for batch in assembled.get('tool',[]):
        for part in batch:
            idx=part.get('index',0);call=calls.setdefault(idx,{"name":"","arguments":""})
            fn=part.get('function',{})
            call['name']+=fn.get('name','');call['arguments']+=fn.get('arguments','')
    try:
        # Named tool choice ends with stop; auto/required choice uses tool_calls.
        return rec.get('finish_reason') in ('stop','tool_calls') and len(calls)==1 and calls[0]['name']=='ip314_probe' and json.loads(calls[0]['arguments'])=={"code":"ORCHID-7314","count":3}
    except (KeyError,ValueError,TypeError):return False


def cells(case,arm,runs,vision_max_images=1):
    if case=='short':
        return [dict(cell_id=f'{case}-{arm}-{size}-{mode}-c{c}-{cache}-{run}',size=size,mode=mode,concurrency=c,cache=cache,run=run) for size in (6000,8000) for mode in ('nothink','low') for c in (1,2) for cache in ('cold','warm') for run in range(runs)]
    if case=='overlap':
        return [dict(cell_id=f'{case}-{arm}-{size}-{kind}-{run}',size=size,kind=kind,run=run) for size in (8000,30000) for run in range(runs) for kind in ('solo','joint')]
    if case=='vision':
        return [dict(cell_id=f'{case}-{arm}-{n}-{run}',images=n,run=run) for n in (vision_max_images,) for run in range(runs)]
    return [dict(cell_id=f'{case}-{arm}-{kind}-{run}',kind=kind,run=run) for kind in ('json','tool') for run in range(runs)]


def valid_response(rec,model):
    return (not rec.get('error') and rec.get('sse_done') and rec.get('finish_reason') in ('stop','length','tool_calls') and rec.get('backend_model')==model and rec.get('usage') is not None)


def main(argv=None):
    ap=argparse.ArgumentParser()
    ap.add_argument('--base-url',default='http://127.0.0.1:8000');ap.add_argument('--model',default='glm-5.3-flash-uncensored')
    ap.add_argument('--arm',required=True);ap.add_argument('--case',required=True,choices=('short','overlap','vision','final'))
    ap.add_argument('--runs',type=int,default=1);ap.add_argument('--out',required=True)
    ap.add_argument('--vision-max-images',type=int,default=1,help='one vision rung; inspect both-rank memory before advancing');ap.add_argument('--video-path')
    args=ap.parse_args(argv);cfg=vars(args).copy()
    with open(__file__,'rb') as f:cfg['harness_sha256']=hashlib.sha256(f.read()).hexdigest()
    state=load_state(args.out,cfg);completed=state['completed']
    pending=set(pending_cell_ids([c['cell_id'] for c in cells(args.case,args.arm,args.runs,args.vision_max_images)],state))
    for cell in cells(args.case,args.arm,args.runs,args.vision_max_images):
        if cell['cell_id'] not in pending:continue
        bodies=[];measured=[];prime=[];expected=1
        if args.case=='short':
            for caller in range(cell['concurrency']):
                nonce=hashlib.sha256(f"short:{cell['size']}:{cell['mode']}:{cell['concurrency']}:{cell['cache']}:{cell['run']}:{caller}".encode()).hexdigest()[:32]
                body,count=calibrated_body(args.base_url,args.model,cell['size'],cell['mode'],nonce)
                body['seed']=314000+cell['run']*2+caller
                bodies.append(body);measured.append(count)
                if cell['cache']=='warm':
                    p=dict(body,max_tokens=1,min_tokens=0);prime.append(request_stream(args.base_url,p))
            expected=len(bodies)
        elif args.case=='overlap':
            nonce=hashlib.sha256(f"overlap:{cell['size']}:{cell['kind']}:{cell['run']}".encode()).hexdigest()[:32]
            body,count=calibrated_body(args.base_url,args.model,cell['size'],'nothink',nonce+'-arrival')
            body.update(max_tokens=32,min_tokens=0,seed=314000+cell['run']);measured=[count]
            incumbent_body=build_body(args.model,'Test '+nonce+'-incumbent\n'+PARA*30+'\nWrite a continuous detailed essay of at least 1800 words about MoE inference.',max_tokens=1200)
            incumbent_body.update(min_tokens=1200,seed=314000+cell['run'])
            if cell['kind']=='solo':
                body=incumbent_body
            else:expected=2
        elif args.case=='vision':
            prompt='Answer every item using these four fields: Large label: <exact text at the top>; Small CODE: <digits after CODE>; Rectangle A: <colour>; Rectangle B: <colour>. Transcribe the text from the image. Do not omit any field.' if cell['images']==1 else 'How many labelled diagrams are in this input? Reply with the count and the large label on the last image.'
            if args.video_path:prompt='Answer every item using these four fields: First label: <exact large label at the start>; Last label: <exact large label at the end>; Rectangle A: <colour>; Rectangle B: <colour>. Do not omit any field.'
            body=build_body(args.model,prompt,vision=True,max_tokens=160)
            body['temperature']=0
            for i in range(0 if args.video_path else cell['images']):
                encoded=base64.b64encode(synthetic_png(f'IP314-{i+1:02d}',nonce=cell['cell_id']+f':{i}')).decode()
                body['messages'][0]['content'].append({'type':'image_url','image_url':{'url':'data:image/png;base64,'+encoded}})
            if args.video_path:
                with open(args.video_path,'rb') as f:encoded=base64.b64encode(f.read()).decode()
                body['messages'][0]['content'].append({'type':'video_url','video_url':{'url':'data:video/mp4;base64,'+encoded}})
        else:
            body=build_body(args.model,'Return code ORCHID-7314 and count 3. Use the required JSON format or tool. No other text.',max_tokens=160)
            body['temperature']=0
            if cell['kind']=='json':body['response_format']={'type':'json_object'}
            else:
                body['tools']=[{'type':'function','function':{'name':'ip314_probe','parameters':{'type':'object','properties':{'code':{'type':'string'},'count':{'type':'integer'}},'required':['code','count'],'additionalProperties':False}}}]
                body['tool_choice']={'type':'function','function':{'name':'ip314_probe'}}
        time.sleep(.2)
        for _ in range(100):
            before=metrics(args.base_url)
            if before.get('running')==0 and before.get('waiting')==0:break
            time.sleep(.1)
        if before.get('running')!=0 or before.get('waiting')!=0:raise RuntimeError('engine is not isolated/idle before cell')
        if args.case=='short':
            rec=run_two(args.base_url,bodies) if len(bodies)>1 else request_stream(args.base_url,bodies[0])
        elif args.case=='overlap' and cell['kind']=='joint':rec=run_overlap(args.base_url,args.model,body,incumbent_body=incumbent_body)
        else:rec=request_stream(args.base_url,body)
        for _ in range(100):
            after=metrics(args.base_url)
            if after.get('request_success_count',0)-before.get('request_success_count',0)>=expected and after.get('running')==0 and after.get('waiting')==0:break
            if rec.get('error'):break
            time.sleep(.1)
        attribution=attribute_metrics(before,after,expected==1,expected=expected)
        responses=rec.get('threads') or ([rec['long'],rec.get('newcomer',{})] if 'long' in rec else [rec])
        valid=all(valid_response(r,args.model) for r in responses) and attribution['valid_cell']
        if args.case=='short':
            for r in responses:
                content=''.join(r.get('assembled',{}).get('content',[]))
                r['no_markup_leak']=not any(x in content for x in ('<think>','</think>','[gMASK]','<|assistant|>'))
                r['visible_output_present']=bool(content.strip())
                valid=valid and r['no_markup_leak'] and r['visible_output_present'] and 200 <= (r.get('usage') or {}).get('completion_tokens',0) <= 600
            valid=valid and all(valid_response(r,args.model) for r in prime)
        if args.case=='overlap' and cell['kind']=='joint':valid=valid and rec['overlap_valid']
        if args.case=='final':rec['final_check']=final_check(cell['kind'],rec);valid=valid and rec['final_check']
        if args.case=='vision' and cell['images']==1:
            answer=''.join(rec.get('assembled',{}).get('content',[])).upper()
            compact=re.sub('[^A-Z0-9]','',answer)
            labels=['IP31401','IP31403'] if args.video_path else ['IP31401','7314']
            rec['vision_content_check']=all(label in compact for label in labels) and 'RED' in answer and 'BLUE' in answer
            valid=valid and rec['vision_content_check']
        if args.case=='vision' and cell['images']==49:
            valid=rec.get('http_status')==400 and after.get('running')==0 and '48' in rec.get('response','') and 'image' in rec.get('response','').lower()
            rec['expected_rejection']=True
        rec.update(cell,arm=args.arm,case=args.case,metrics_before=before,metrics_after=after,metric_attribution=attribution,
                   prompt_tokens_measured=measured,prime_requests=prime,status='passed' if valid else 'failed')
        completed.append(rec);save_state(args.out,cfg,completed)
        print(json.dumps({k:rec.get(k) for k in ('cell_id','status','ttft_s','total_time_s','joint_duration_s','usage','error','metric_attribution')}),flush=True)
        if not valid:raise RuntimeError(f"cell failed: {cell['cell_id']}; receipt saved")
    print(json.dumps({'out':args.out,'completed':len(completed)}),flush=True)


if __name__=='__main__':main()
