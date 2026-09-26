#!/usr/bin/env python3
"""数据文件的读写与合并规则，所有脚本共用。

文件：
  data/quarterly.json      季度指标：{ticker: {period: {period_end, filed, source_url, metrics: {key: item}, notes: []}}}
  data/review_queue.json   待分析员处理的新财报：{pending: [...], done: [...]}
  data/events.json         待推送事件（飞书），推送后标 sent
  data/feedback.json       跟踪反馈日志（基线 / 财报入库 / 分析员 / 季度复盘 / 手动）

合并优先级：manual（Henry 或本机核对）> routine（云端分析员）> auto（新闻稿正则）。
低优先级不覆盖高优先级，除非 force。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import DATA, load_json, now_bjt, save_json  # noqa: E402

Q_PATH = os.path.join(DATA, "quarterly.json")
QUEUE_PATH = os.path.join(DATA, "review_queue.json")
EVENTS_PATH = os.path.join(DATA, "events.json")
FEEDBACK_PATH = os.path.join(DATA, "feedback.json")
PREC = {"auto": 1, "routine": 2, "manual": 3}


def stamp():
    return now_bjt().strftime("%Y-%m-%d %H:%M")


# ---------- quarterly ----------

def load_quarterly():
    return load_json(Q_PATH, {}) or {}


def save_quarterly(q):
    q["_meta"] = {"updated_bjt": stamp(),
                  "note": "method：auto=新闻稿正则，routine=云端分析员，manual=人工核对；call=true 为电话会口径（第三方转录）"}
    save_json(Q_PATH, q)


def period_rec(q, ticker, period):
    rec = q.setdefault(ticker, {}).setdefault(period, {})
    rec.setdefault("metrics", {})
    rec.setdefault("notes", [])
    return rec


def set_period_info(q, ticker, period, **info):
    rec = period_rec(q, ticker, period)
    for k, v in info.items():
        if v is not None:
            rec[k] = v
    return rec


def set_metric(q, ticker, period, key, item, method, source=None, force=False):
    """写一个指标。返回 True 表示有变化。"""
    rec = period_rec(q, ticker, period)
    old = rec["metrics"].get(key)
    if old and not force and PREC.get(old.get("method"), 0) > PREC.get(method, 0):
        return False
    new = dict(item)
    new["method"] = method
    if source:
        new["source"] = source
    new["updated"] = stamp()[:10]
    if old and all(old.get(k) == new.get(k) for k in ("value", "qualifier", "method")):
        return False
    rec["metrics"][key] = new
    return True


def add_note(q, ticker, period, text, source=None, method="manual", call=False):
    rec = period_rec(q, ticker, period)
    for n in rec["notes"]:
        if n.get("text") == text:
            return False
    rec["notes"].append({"text": text, "source": source, "method": method, "call": bool(call),
                         "date": stamp()[:10]})
    return True


def metric_series(q, ticker, key):
    """[(period, item)]，按财季排序。"""
    from sec_edgar import label_sort_key
    out = []
    for per, rec in (q.get(ticker) or {}).items():
        if per.startswith("_"):
            continue
        it = (rec.get("metrics") or {}).get(key)
        if it and it.get("value") is not None:
            out.append((per, it))
    out.sort(key=lambda x: label_sort_key(x[0]))
    return out


# ---------- review queue ----------

def load_queue():
    return load_json(QUEUE_PATH, {"pending": [], "done": []}) or {"pending": [], "done": []}


def queue_add(item):
    qu = load_queue()
    ids = {x["id"] for x in qu["pending"]} | {x["id"] for x in qu["done"]}
    if item["id"] in ids:
        return False
    item.setdefault("created", stamp())
    qu["pending"].append(item)
    save_json(QUEUE_PATH, qu)
    return True


def queue_done(item_id, note=""):
    qu = load_queue()
    keep, moved = [], None
    for x in qu["pending"]:
        if x["id"] == item_id and moved is None:
            moved = dict(x, done_at=stamp(), done_note=note)
        else:
            keep.append(x)
    if moved is None:
        return False
    qu["pending"] = keep
    qu["done"] = ([moved] + qu["done"])[:60]
    save_json(QUEUE_PATH, qu)
    return True


# ---------- events（推送队列） ----------

def load_events():
    return load_json(EVENTS_PATH, []) or []


def add_event(ev):
    evs = load_events()
    if any(e["id"] == ev["id"] for e in evs):
        return False
    ev.setdefault("created", stamp())
    ev.setdefault("sent", False)
    evs.append(ev)
    save_json(EVENTS_PATH, evs[-300:])
    return True


# ---------- feedback ----------

def load_feedback():
    return load_json(FEEDBACK_PATH, []) or []


def feedback_add(entry):
    fb = load_feedback()
    if any(e["id"] == entry["id"] for e in fb):
        return False
    entry.setdefault("created", stamp())
    fb.append(entry)
    fb.sort(key=lambda e: (e.get("date", ""), e.get("created", "")))
    save_json(FEEDBACK_PATH, fb)
    return True
