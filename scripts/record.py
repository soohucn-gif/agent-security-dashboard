#!/usr/bin/env python3
"""给云端分析员 routine（和人工）用的写入工具：带校验地改数据，不要手改 JSON。

  python3 scripts/record.py queue-list
  python3 scripts/record.py metric   --ticker PANW --period FY27Q1 --key prisma_airs_arr --value 210 \
          [--qualifier '>'] --source URL --quote "原文" [--call] [--method routine|manual] [--force]
  python3 scripts/record.py flag     --ticker OKTA --period FY27Q3 --key usage_pricing --value 0|1 \
          --source URL --quote "原文" [--call]
  python3 scripts/record.py note     --ticker CRWD --period FY27Q3 --text "AI DR ARR 较上季…" --source URL [--call]
  python3 scripts/record.py feedback --type earnings_review|season_review|note --title "…" \
          --verdict 维持|上调|下调|观察 --signals S5,S6 --tickers PANW --body-file body.md \
          [--sources URL1,URL2] [--confidence 高|中|低] [--author 云端分析员]
  python3 scripts/record.py queue-done --id PANW-FY27Q1 [--note "…"]
  python3 scripts/record.py calendar --ticker AKAM --date 2026-11-05 --status 已确认 [--timing 盘后] --source URL

所有写入都会过白名单（config/metrics.json）和格式校验；出错直接非零退出，不写半截数据。
"""
import argparse
import datetime
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import CONFIG, DATA, load_json, save_json  # noqa: E402
import store  # noqa: E402

METRICS = load_json(os.path.join(CONFIG, "metrics.json"))
SIGNAL_IDS = {s["id"] for s in load_json(os.path.join(CONFIG, "signals.json"))["signals"]}
PERIOD_RE = re.compile(r"^(20\d\dQ[1-4]|FY\d\dQ[1-4])$")


def die(msg):
    print("❌ " + msg, file=sys.stderr)
    sys.exit(2)


def check_period(tk, per):
    if not PERIOD_RE.match(per):
        die("财季格式应为 2026Q3 或 FY27Q1：%s" % per)
    fy = {c: m for c, m in (("AKAM", 12), ("FTNT", 12), ("CHKP", 12), ("NET", 12))}
    if tk in fy and per.startswith("FY"):
        die("%s 是自然年财年，用 2026Q3 这种写法" % tk)
    if tk not in fy and not per.startswith("FY"):
        die("%s 是非自然年财年，用 FY27Q1 这种写法" % tk)


def meta(tk, key):
    if tk not in METRICS:
        die("未知公司：%s" % tk)
    for m in METRICS[tk]:
        if m["key"] == key:
            return m
    die("%s 没有指标 %s；可用：%s" % (tk, key, ", ".join(m["key"] for m in METRICS[tk])))


def cmd_queue_list(a):
    qu = store.load_queue()
    print(json.dumps(qu["pending"], ensure_ascii=False, indent=1))
    print("待处理 %d 条" % len(qu["pending"]))


def cmd_metric(a, flag=False):
    tk = a.ticker.upper()
    check_period(tk, a.period)
    m = meta(tk, a.key)
    try:
        v = float(a.value)
    except ValueError:
        die("value 必须是数字：%s" % a.value)
    if flag and v not in (0.0, 1.0):
        die("flag 只能是 0 或 1")
    if m["unit"] == "pct" and not (-100 <= v <= 1000):
        die("百分比越界：%s" % v)
    if m["unit"] == "USD_M" and not (0 <= v <= 500000):
        die("金额越界（单位百万美元）：%s" % v)
    if not a.source or not a.source.startswith("http"):
        die("必须给出处 URL（--source）")
    item = {"value": v, "unit": m["unit"], "quote": (a.quote or "")[:300]}
    if a.qualifier:
        if a.qualifier not in (">", "<", "≈"):
            die("qualifier 只能是 > < ≈")
        item["qualifier"] = a.qualifier
    if a.call or m.get("call"):
        item["call"] = True
    q = store.load_quarterly()
    changed = store.set_metric(q, tk, a.period, a.key, item, a.method, source=a.source, force=a.force)
    store.save_quarterly(q)
    print(("✅ 已写入" if changed else "＝ 未变化（或被更高优先级保护，需 --force）"),
          tk, a.period, a.key, item.get("qualifier", ""), v)


def cmd_note(a):
    tk = a.ticker.upper()
    check_period(tk, a.period)
    q = store.load_quarterly()
    store.add_note(q, tk, a.period, a.text[:500], source=a.source, method=a.method, call=a.call)
    store.save_quarterly(q)
    print("✅ 备注已写入", tk, a.period)


def cmd_feedback(a):
    if a.type not in ("earnings_review", "season_review", "note", "baseline"):
        die("type 只能是 earnings_review / season_review / note / baseline")
    if a.verdict not in ("维持", "上调", "下调", "观察", "—"):
        die("verdict 只能是 维持 / 上调 / 下调 / 观察 / —")
    sigs = [s.strip() for s in (a.signals or "").split(",") if s.strip()]
    for s in sigs:
        if s not in SIGNAL_IDS:
            die("未知信号：%s" % s)
    body = open(a.body_file, encoding="utf-8").read().strip() if a.body_file else (a.body or "")
    if len(body) < 20:
        die("正文太短")
    date = a.date or store.stamp()[:10]
    fid = "%s-%s-%s" % (date, a.type, re.sub(r"[^A-Za-z0-9]+", "", (a.tickers or "") + "".join(sigs)) or "x")
    entry = {
        "id": fid, "date": date, "type": a.type, "title": a.title.strip(), "verdict": a.verdict,
        "signals": sigs, "tickers": [t.strip().upper() for t in (a.tickers or "").split(",") if t.strip()],
        "body": body[:6000], "sources": [s.strip() for s in (a.sources or "").split(",") if s.strip()],
        "confidence": a.confidence, "author": a.author,
    }
    if not store.feedback_add(entry):
        die("同 id 的反馈已存在：%s（同一天同类型同公司只写一条；要补充就换 type=note）" % fid)
    store.add_event({"id": "feedback-" + fid, "type": "feedback", "title": entry["title"],
                     "verdict": a.verdict, "signals": sigs, "summary": body[:500]})
    print("✅ 反馈已写入", fid)


def cmd_queue_done(a):
    if not store.queue_done(a.id, a.note or ""):
        die("队列里没有这个 id：%s" % a.id)
    print("✅ 已完成", a.id)


def cmd_calendar(a):
    tk = a.ticker.upper()
    if tk not in METRICS:
        die("未知公司：%s" % tk)
    try:
        datetime.date.fromisoformat(a.date)
    except ValueError:
        die("日期格式 YYYY-MM-DD")
    if a.status not in ("已确认", "预估"):
        die("status 只能是 已确认 / 预估")
    path = os.path.join(DATA, "calendar.json")
    cal = load_json(path, {}) or {}
    cal[tk] = {"date": a.date, "status": a.status, "timing": a.timing or "", "source": a.source or "",
               "fetched": store.stamp()[:10], "by": a.method}
    save_json(path, cal)
    print("✅ 财报日已更新", tk, a.date, a.status)


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("queue-list")
    for name in ("metric", "flag"):
        p = sub.add_parser(name)
        p.add_argument("--ticker", required=True)
        p.add_argument("--period", required=True)
        p.add_argument("--key", required=True)
        p.add_argument("--value", required=True)
        p.add_argument("--qualifier")
        p.add_argument("--source", required=True)
        p.add_argument("--quote", default="")
        p.add_argument("--call", action="store_true")
        p.add_argument("--method", default="routine", choices=["routine", "manual"])
        p.add_argument("--force", action="store_true")
    p = sub.add_parser("note")
    p.add_argument("--ticker", required=True)
    p.add_argument("--period", required=True)
    p.add_argument("--text", required=True)
    p.add_argument("--source")
    p.add_argument("--call", action="store_true")
    p.add_argument("--method", default="routine", choices=["routine", "manual"])
    p = sub.add_parser("feedback")
    p.add_argument("--type", required=True)
    p.add_argument("--title", required=True)
    p.add_argument("--verdict", default="—")
    p.add_argument("--signals", default="")
    p.add_argument("--tickers", default="")
    p.add_argument("--body-file")
    p.add_argument("--body")
    p.add_argument("--sources", default="")
    p.add_argument("--confidence", default="中")
    p.add_argument("--author", default="云端分析员")
    p.add_argument("--date")
    p = sub.add_parser("queue-done")
    p.add_argument("--id", required=True)
    p.add_argument("--note")
    p = sub.add_parser("calendar")
    p.add_argument("--ticker", required=True)
    p.add_argument("--date", required=True)
    p.add_argument("--status", required=True)
    p.add_argument("--timing")
    p.add_argument("--source")
    p.add_argument("--method", default="routine")
    a = ap.parse_args()
    {"queue-list": cmd_queue_list, "metric": cmd_metric, "flag": lambda x: cmd_metric(x, flag=True),
     "note": cmd_note, "feedback": cmd_feedback, "queue-done": cmd_queue_done,
     "calendar": cmd_calendar}[a.cmd](a)


if __name__ == "__main__":
    main()
