#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""小熊老师：静态页 + DeepSeek 分析代理。密钥只放本机 .env，不要提交。

本机试：
  1. 复制 .env.example 为 .env，填入 DEEPSEEK_API_KEY
  2. python server.py
  3. 浏览器打开 http://127.0.0.1:8090/

服务器若继续用 Nginx 8088 提供网页，让 Python 只监听本机，并加反向代理：
  location /api/ {
      proxy_pass http://127.0.0.1:8090/api/;
      proxy_http_version 1.1;
      proxy_set_header Host $host;
      proxy_read_timeout 60s;
  }
然后：cd /home/guoqiang/QiaoSuanKide && python3 server.py
"""
from __future__ import annotations

import json
import os
import re
import ssl
import sys
import urllib.request
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

ROOT = os.path.dirname(os.path.abspath(__file__))
ALLOWED_KINDS = (
    "onlyAdd",
    "onlySub",
    "mix10",
    "noCarryAdd",
    "noBorrowSub",
    "carryAdd",
    "borrowSub",
    "mix20",
)
FACT_RE = re.compile(r"^(\d{1,2})([+\-])(\d{1,2})$")


def load_env(path: str) -> None:
    if not os.path.isfile(path):
        return
    with open(path, encoding="utf-8") as f:
        for raw in f:
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            key, val = key.strip(), val.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = val


load_env(os.path.join(ROOT, ".env"))
API_KEY = os.environ.get("DEEPSEEK_API_KEY", "").strip()
if API_KEY.lower() in ("sk-replace-me", "your-key-here", "changeme"):
    API_KEY = ""
MODEL = os.environ.get("DEEPSEEK_MODEL", "deepseek-v4-flash").strip() or "deepseek-v4-flash"
HOST = os.environ.get("HOST", "0.0.0.0").strip() or "0.0.0.0"
PORT = int(os.environ.get("PORT", "8090"))
API_URL = "https://api.deepseek.com/chat/completions"

SYSTEM_PROMPT = """你是「小熊老师」，帮家长看6岁孩子的20以内加减练习记录。
程序已经判过对错，你不要重算、不要改答案。
只做归纳：卡在凑十、破十，还是某几道口算不熟。
给孩子的话要短、亲切、鼓励，不要批评。
只输出一个 JSON 对象，不要 markdown，字段如下：
{"kid_line":"给孩子的一句话，最多28字","parent_note":"给家长2到3句，说明问题和怎么练","weak_facts":["8+6"],"tomorrow_kinds":["carryAdd"],"focus":"凑十"}
tomorrow_kinds 只能从这些里选1到2个：onlyAdd, onlySub, mix10, noCarryAdd, noBorrowSub, carryAdd, borrowSub, mix20
weak_facts 最多5个，格式必须是 8+6 或 13-5 这种。"""


def clip(text, n: int) -> str:
    s = re.sub(r"\s+", " ", str(text or "")).strip()
    s = re.sub(r"[<>]", "", s)
    return s[:n]


def valid_fact(text: str):
    m = FACT_RE.match(str(text).replace(" ", ""))
    if not m:
        return None
    a, op, b = int(m.group(1)), m.group(2), int(m.group(3))
    ans = a + b if op == "+" else a - b
    if a > 20 or b > 20 or ans < 0 or ans > 20:
        return None
    return f"{a}{op}{b}"


def classify_item(item: dict) -> str:
    try:
        a = int(item.get("a", 0))
        b = int(item.get("b", 0))
        op = str(item.get("opt") or "")
    except (TypeError, ValueError):
        return "mix20"
    if op == "+":
        big, small = max(a, b), min(a, b)
        need = 10 - big
        if big < 10 and small > need > 0:
            return "carryAdd"
        if a + b <= 10:
            return "onlyAdd"
        return "noCarryAdd"
    ones = a % 10
    if b > ones:
        return "borrowSub"
    if a <= 10:
        return "onlySub"
    return "noBorrowSub"


def local_plan(payload: dict) -> dict:
    log = payload.get("log") or []
    wrong = [x for x in log if isinstance(x, dict) and not x.get("correct")]
    facts = []
    seen = set()
    for x in wrong:
        fact = valid_fact(x.get("q") or f"{x.get('a','')}{x.get('opt','')}{x.get('b','')}")
        if fact and fact not in seen:
            seen.add(fact)
            facts.append(fact)
    for x in payload.get("errorBook") or []:
        if not isinstance(x, dict):
            continue
        fact = valid_fact(x.get("question") or f"{x.get('a','')}{x.get('opt','')}{x.get('b','')}")
        if fact and fact not in seen:
            seen.add(fact)
            facts.append(fact)
    kinds = []
    for x in wrong:
        k = classify_item(x)
        if k not in kinds:
            kinds.append(k)
    if not kinds:
        lv = int(payload.get("currentLv") or 0)
        fallback = ["onlyAdd", "onlySub", "mix10", "noCarryAdd", "noBorrowSub", "carryAdd", "borrowSub", "mix20"]
        kinds = [fallback[min(max(lv, 0), len(fallback) - 1)]]
    kinds = [k for k in kinds if k in ALLOWED_KINDS][:2]
    focus = "凑十" if "carryAdd" in kinds else "破十" if "borrowSub" in kinds else "口算"
    kid = "今天很认真，明天先听思路再做。"
    if facts:
        kid = f"明天先练{facts[0]}，听完思路再填。"
    parent = "先按错题和薄弱类型练10道。听思路后再自己算，不要只求快。"
    if facts:
        parent = f"容易错：{'、'.join(facts[:4])}。建议先听凑十/破十，再做针对性练习。"
    return {
        "kid_line": kid[:28],
        "parent_note": parent,
        "weak_facts": facts[:5],
        "tomorrow_kinds": kinds,
        "focus": focus,
        "source": "local",
    }


def extract_json(text: str) -> dict:
    raw = (text or "").strip()
    raw = re.sub(r"^```(?:json)?", "", raw)
    raw = re.sub(r"```$", "", raw).strip()
    start, end = raw.find("{"), raw.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("no json")
    return json.loads(raw[start : end + 1])


def sanitize_plan(data: dict, source: str) -> dict:
    facts = []
    for item in data.get("weak_facts") or []:
        fact = valid_fact(item)
        if fact and fact not in facts:
            facts.append(fact)
        if len(facts) >= 5:
            break
    kinds = []
    for item in data.get("tomorrow_kinds") or []:
        if item in ALLOWED_KINDS and item not in kinds:
            kinds.append(item)
        if len(kinds) >= 2:
            break
    if not kinds:
        kinds = ["carryAdd"]
    return {
        "kid_line": clip(data.get("kid_line"), 28) or "今天很棒，明天再听一遍思路。",
        "parent_note": clip(data.get("parent_note"), 180) or "按薄弱点练10道，先听思路再填。",
        "weak_facts": facts,
        "tomorrow_kinds": kinds,
        "focus": clip(data.get("focus"), 12) or "口算",
        "source": source,
    }


def compact_payload(body: dict) -> dict:
    log = []
    for item in (body.get("log") or [])[-80:]:
        if not isinstance(item, dict):
            continue
        log.append(
            {
                "q": clip(item.get("q"), 12),
                "a": int(item.get("a") or 0),
                "b": int(item.get("b") or 0),
                "opt": str(item.get("opt") or "")[:1],
                "correct": bool(item.get("correct")),
                "hint": bool(item.get("hint")),
                "lv": int(item.get("lv") or 0),
            }
        )
    errors = []
    for item in (body.get("errorBook") or [])[:20]:
        if not isinstance(item, dict):
            continue
        errors.append(
            {
                "question": clip(item.get("question"), 12),
                "a": int(item.get("a") or 0),
                "b": int(item.get("b") or 0),
                "opt": str(item.get("opt") or "")[:1],
            }
        )
    return {
        "todayCount": int(body.get("todayCount") or 0),
        "todayRight": int(body.get("todayRight") or 0),
        "todayWrong": int(body.get("todayWrong") or 0),
        "currentLv": int(body.get("currentLv") or 0),
        "levelName": clip(body.get("levelName"), 40),
        "log": log,
        "errorBook": errors,
    }


def call_deepseek(payload: dict) -> dict:
    body = json.dumps(
        {
            "model": MODEL,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
            ],
            "temperature": 0.3,
            "max_tokens": 700,
            "thinking": {"type": "disabled"},
            "response_format": {"type": "json_object"},
        },
        ensure_ascii=False,
    ).encode("utf-8")
    req = urllib.request.Request(
        API_URL,
        data=body,
        headers={
            "Content-Type": "application/json",
            "Authorization": "Bearer " + API_KEY,
        },
        method="POST",
    )
    ctx = ssl.create_default_context()
    with urllib.request.urlopen(req, timeout=45, context=ctx) as resp:
        raw = json.loads(resp.read().decode("utf-8"))
    content = (((raw.get("choices") or [{}])[0].get("message") or {}).get("content")) or ""
    return sanitize_plan(extract_json(content), "ai")


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=ROOT, **kwargs)

    def log_message(self, fmt: str, *args) -> None:
        sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))

    def _cors(self) -> None:
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def _json(self, code: int, data: dict) -> None:
        blob = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self._cors()
        self.send_header("Content-Length", str(len(blob)))
        self.end_headers()
        self.wfile.write(blob)

    def do_OPTIONS(self) -> None:
        if self.path.split("?", 1)[0].startswith("/api/"):
            self.send_response(204)
            self._cors()
            self.end_headers()
            return
        self.send_error(404)

    def do_GET(self) -> None:
        path = self.path.split("?", 1)[0]
        if path == "/api/health":
            self._json(200, {"ok": True, "has_key": bool(API_KEY), "model": MODEL})
            return
        super().do_GET()

    def do_POST(self) -> None:
        path = self.path.split("?", 1)[0]
        if path != "/api/analyze":
            self.send_error(404)
            return
        length = int(self.headers.get("Content-Length") or 0)
        if length > 100_000:
            self._json(413, {"error": "内容太长"})
            return
        try:
            body = json.loads(self.rfile.read(length) or b"{}")
            if not isinstance(body, dict):
                raise ValueError("bad body")
            payload = compact_payload(body)
        except Exception:
            self._json(400, {"error": "数据格式不对"})
            return
        if payload["todayCount"] <= 0 and not payload["log"] and not payload["errorBook"]:
            self._json(400, {"error": "今天还没有练习记录"})
            return
        if API_KEY:
            try:
                self._json(200, call_deepseek(payload))
                return
            except Exception as exc:
                sys.stderr.write("deepseek failed: %s\n" % type(exc).__name__)
        plan = local_plan(payload)
        if not API_KEY:
            plan["parent_note"] = "还没配置密钥，先按错题安排。" + plan["parent_note"]
        else:
            plan["parent_note"] = "小熊暂时连不上，先按错题安排。" + plan["parent_note"]
        self._json(200, plan)


def main() -> None:
    os.chdir(ROOT)
    httpd = ThreadingHTTPServer((HOST, PORT), Handler)
    print("QiaoSuanKid  http://127.0.0.1:%s/" % PORT, flush=True)
    print("API key:", "已配置" if API_KEY else "未配置（先复制 .env.example 为 .env）", flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nbye")


if __name__ == "__main__":
    main()
