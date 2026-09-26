#!/usr/bin/env python3
"""共用工具：HTTP、JSON 读写、HTML 转文本、路径。只用标准库，GitHub Actions 无需 pip install。"""
import datetime
import gzip
import html
import html.parser
import json
import os
import re
import time
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")
CONFIG = os.path.join(ROOT, "config")
RAW = os.path.join(ROOT, "raw")

BJT = datetime.timezone(datetime.timedelta(hours=8))
SITE_URL = "https://soohucn-gif.github.io/agent-security-dashboard/"
REPO_URL = "https://github.com/soohucn-gif/agent-security-dashboard"

BROWSER_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")
# SEC 要求 UA 带联系方式；沿用 fetch-quarterly-financials 技能的口径，不放个人邮箱。
SEC_UA = "ResearchAgent contact@example.com"


def http_get(url, tries=5, timeout=60, headers=None, ua=BROWSER_UA):
    """带重试的 GET，返回 bytes。SEC 连接常被 reset，默认重试 5 次、指数退避。"""
    hdr = {"Accept-Encoding": "gzip", "User-Agent": ua}
    if headers:
        hdr.update(headers)
    last = None
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers=hdr)
            with urllib.request.urlopen(req, timeout=timeout) as r:
                raw = r.read()
                if r.headers.get("Content-Encoding") == "gzip":
                    raw = gzip.decompress(raw)
                return raw
        except Exception as e:  # noqa: BLE001 — 上层统一处理
            last = e
            if getattr(e, "code", None) in (400, 401, 403, 404):
                break
            if i < tries - 1:
                time.sleep(1.5 * (2 ** i))
    raise RuntimeError("GET failed: %s (%s)" % (url, last))


def sec_get(url, tries=5):
    time.sleep(0.15)  # SEC 限 10 req/s，留足余量
    return http_get(url, tries=tries, ua=SEC_UA)


def load_json(path, default=None):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except FileNotFoundError:
        return default


def save_json(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, ensure_ascii=False, indent=1)
        fh.write("\n")
    os.replace(tmp, path)


def now_bjt():
    return datetime.datetime.now(BJT)


def today_utc():
    return datetime.datetime.now(datetime.timezone.utc).date()


class _Text(html.parser.HTMLParser):
    """HTML → 文本：表格单元格用 " | " 连接、每行一条，便于正则按行取数。"""
    BLOCK = {"p", "div", "br", "tr", "li", "h1", "h2", "h3", "h4", "h5", "h6", "table"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.out = []
        self.skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self.skip += 1
        elif tag in ("td", "th"):
            self.out.append(" | ")
        elif tag in self.BLOCK:
            self.out.append("\n")

    def handle_endtag(self, tag):
        if tag in ("script", "style"):
            self.skip = max(0, self.skip - 1)
        elif tag in self.BLOCK:
            self.out.append("\n")

    def handle_data(self, data):
        if not self.skip:
            self.out.append(data)


def html_to_text(raw):
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8", errors="replace")
    p = _Text()
    p.feed(raw)
    txt = html.unescape("".join(p.out))
    txt = txt.replace("\xa0", " ").replace("​", "").replace(" ", " ")
    lines = []
    for ln in txt.split("\n"):
        ln = re.sub(r"[ \t]+", " ", ln).strip()
        # 空单元格与 "$ | 604" 这类碎片合并，一行只留有内容的格
        cells = [c.strip() for c in ln.split("|")]
        cells = [c for c in cells if c]
        merged = []
        for c in cells:
            if merged and merged[-1] in ("$", "(", "$(", "€"):
                merged[-1] = merged[-1] + c
            elif merged and c in (")", "%", ")%", "%)", "pts", "bps"):
                merged[-1] = merged[-1] + c
            else:
                merged.append(c)
        ln = " | ".join(merged)
        if ln:
            lines.append(ln)
    return "\n".join(lines)


def pct(a, b):
    """同比 %：a 相对 b。"""
    if a is None or b in (None, 0):
        return None
    return (a / b - 1.0) * 100.0


def parse_num(s):
    """'1,234.5' / '(12.3)' / '$604' / '—' → float 或 None。括号为负。"""
    if s is None:
        return None
    s = s.strip().replace("$", "").replace(",", "").replace("%", "").strip()
    if s in ("", "—", "–", "-", "N/A", "n/a", "NM", "nm", "*"):
        return None
    neg = s.startswith("(") and s.endswith(")")
    s = s.strip("()").strip()
    try:
        v = float(s)
    except ValueError:
        return None
    return -v if neg else v
