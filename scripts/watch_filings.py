#!/usr/bin/env python3
"""盯 SEC：9 家公司有新的季度业绩新闻稿就下载、解析、入库、排进分析员队列、生成推送事件。

用法：
  python3 scripts/watch_filings.py                         常规（云端每天三次）：只处理没见过的申报
  python3 scripts/watch_filings.py --backfill 2024-01-15   回补历史：解析入库，不入队、不推送
        [--from-dir DIR]                                   用本机已下载的新闻稿文本（DIR/manifest_all.json）
  python3 scripts/watch_filings.py --mark-seen             把现有申报全部记为已见（首次部署）

已见清单 data/filings_seen.json：{ticker: {accession: {filed, form, status, label, url}}}
status：processed=已入库；not_earnings=6-K 但不是业绩稿；no_pr=没找到新闻稿；setup=部署时存量。
"""
import argparse
import datetime
import glob
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import DATA, RAW, ROOT, load_json, save_json, today_utc  # noqa: E402
from extractors import extract, keyword_hits  # noqa: E402
from sec_edgar import (companies, earnings_filings, fetch_press_release, fiscal_label,  # noqa: E402
                       quarter_end_before, submissions)
import store  # noqa: E402
from evaluate import fmt_item  # noqa: E402

SEEN_PATH = os.path.join(DATA, "filings_seen.json")
SIGNALS = load_json(os.path.join(ROOT, "config", "signals.json"))["signals"]
KEEP_RAW = 6   # 每家公司保留最近几份新闻稿全文，供云端分析员读取


def signals_for(ticker):
    return [s for s in SIGNALS if ticker in s["tickers"]]


HEADLINE = {  # 推送里每家公司先报哪几个数
    "AKAM": [("security_rev", "安全收入"), ("security_yoy", "安全同比"), ("security_yoy_cc", "不变汇率")],
    "FTNT": [("product_rev", "产品收入"), ("product_yoy", "产品同比"), ("billings_yoy", "账单同比")],
    "CHKP": [("product_rev", "产品收入"), ("product_yoy", "产品同比"), ("subscription_yoy", "订阅同比")],
    "PANW": [("revenue_yoy", "收入同比"), ("ngs_arr", "NGS ARR"), ("prisma_airs_arr", "Prisma AIRS ARR")],
    "CRWD": [("arr", "期末 ARR"), ("arr_yoy", "ARR 同比"), ("ai_dr_arr", "AI DR ARR")],
    "NET": [("revenue", "收入"), ("revenue_yoy", "收入同比"), ("agent_pay_value", "按请求收费成交额")],
    "OKTA": [("revenue_yoy", "收入同比"), ("crpo_yoy", "cRPO 同比")],
    "SAIL": [("arr", "总 ARR"), ("ai_arr", "AI 驱动 ARR"), ("ai_arr_share", "AI 占比")],
    "ZS": [("revenue_yoy", "收入同比"), ("arr", "ARR"), ("arr_yoy", "ARR 同比")],
}


def headline(ticker, metrics):
    parts = []
    for key, lab in HEADLINE.get(ticker, []):
        if key in metrics:
            parts.append("%s %s" % (lab, fmt_item(metrics[key])))
    return "；".join(parts)


def save_raw(ticker, label, filed, text):
    d = os.path.join(RAW, ticker)
    os.makedirs(d, exist_ok=True)
    path = os.path.join(d, "%s_%s.txt" % (label, filed))
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text[:400000])
    files = sorted(glob.glob(os.path.join(d, "*.txt")), key=lambda p: os.path.basename(p).split("_")[-1])
    for old in files[:-KEEP_RAW]:
        os.remove(old)
    return os.path.relpath(path, ROOT)


def ingest(q, comp, filing, url, text, label, period_end, method="auto"):
    tk = comp["ticker"]
    metrics = extract(tk, text)
    store.set_period_info(q, tk, label, period_end=period_end.isoformat(), filed=filing["filed"],
                          accession=filing["accession"], source_url=url, form=filing["form"])
    changed = []
    for key, item in metrics.items():
        if store.set_metric(q, tk, label, key, item, method, source=url):
            changed.append(key)
    return metrics, changed


def run_normal(args):
    seen = load_json(SEEN_PATH, {}) or {}
    q = store.load_quarterly()
    cutoff = (today_utc() - datetime.timedelta(days=args.window)).isoformat()
    report = {"new": [], "errors": []}
    for comp in companies():
        tk = comp["ticker"]
        seen.setdefault(tk, {})
        try:
            fl = earnings_filings(comp, since=cutoff)
        except Exception as e:  # noqa: BLE001
            report["errors"].append("%s submissions: %s" % (tk, str(e)[:120]))
            continue
        for f in fl:
            if f["accession"] in seen[tk]:
                continue
            try:
                url, text = fetch_press_release(comp, f)
            except Exception as e:  # noqa: BLE001
                report["errors"].append("%s %s: %s" % (tk, f["accession"], str(e)[:120]))
                continue  # 下次再试
            if not url:
                seen[tk][f["accession"]] = {"filed": f["filed"], "form": f["form"],
                                            "status": "not_earnings" if f["form"] == "6-K" else "no_pr"}
                continue
            pe = quarter_end_before(f["filed"], comp["fy_end_month"])
            label = fiscal_label(pe, comp["fy_end_month"])
            metrics, changed = ingest(q, comp, f, url, text, label, pe)
            raw_path = save_raw(tk, label, f["filed"], text)
            hits = keyword_hits(tk, text)
            sigs = signals_for(tk)
            item_id = "%s-%s" % (tk, label)
            store.queue_add({
                "id": item_id, "ticker": tk, "period": label, "filed": f["filed"], "source_url": url,
                "raw_path": raw_path, "signals": [s["id"] for s in sigs],
                "auto_metrics": {k: {"value": v["value"], "unit": v["unit"], "qualifier": v.get("qualifier")}
                                 for k, v in metrics.items()},
                "keyword_hits": hits,
                "todo": [("%s %s：%s" % (s["id"], s["short"], s.get("todo", ""))) for s in sigs] or
                        ["参照公司：记录收入 / ARR 与 AI 相关表述"],
            })
            warn = "" if metrics else "⚠ 自动解析没取到指标（版式可能变了），已交给分析员"
            store.add_event({
                "id": "earnings-%s" % item_id, "type": "earnings", "ticker": tk, "period": label,
                "title": "%s %s 财报入库" % (tk, label),
                "lines": [x for x in [headline(tk, metrics), warn] if x],
                "signals": [s["id"] for s in sigs], "url": url,
            })
            seen[tk][f["accession"]] = {"filed": f["filed"], "form": f["form"], "status": "processed",
                                        "label": label, "url": url}
            report["new"].append("%s %s（%s）" % (tk, label, f["filed"]))
            print("📥 新财报 %s %s %s 解析 %d 项" % (tk, label, f["filed"], len(metrics)))
    store.save_quarterly(q)
    save_json(SEEN_PATH, seen)
    rep_path = os.path.join(DATA, "fetch_report.json")
    rep = load_json(rep_path, {}) or {}
    rep["filings"] = dict(report, run_at=store.stamp())
    save_json(rep_path, rep)
    print("新财报：%s；错误：%d" % ("、".join(report["new"]) or "无", len(report["errors"])))
    for e in report["errors"]:
        print("  ❌", e)
    return 0


def run_backfill(args):
    q = store.load_quarterly()
    seen = load_json(SEEN_PATH, {}) or {}
    manifest = None
    if args.from_dir:
        manifest = json.load(open(os.path.join(args.from_dir, "manifest_all.json"), encoding="utf-8"))
    for comp in companies():
        tk = comp["ticker"]
        seen.setdefault(tk, {})
        if manifest is not None:
            items = []
            for label, m in (manifest.get(tk) or {}).items():
                path = os.path.join(args.from_dir, tk, "%s_%s.txt" % (label, m["filed"]))
                if os.path.exists(path):
                    items.append((label, m, open(path, encoding="utf-8").read()))
        else:
            items = []
            done = set()
            for f in earnings_filings(comp, since=args.backfill):
                pe = quarter_end_before(f["filed"], comp["fy_end_month"])
                label = fiscal_label(pe, comp["fy_end_month"])
                if label in done:
                    continue
                url, text = fetch_press_release(comp, f)
                if not url:
                    continue
                done.add(label)
                items.append((label, {"filed": f["filed"], "accession": f["accession"], "url": url,
                                      "period_end": pe.isoformat()}, text))
        for label, m, text in items:
            if m["filed"] < args.backfill:
                continue
            f = {"filed": m["filed"], "accession": m["accession"], "form": comp["form"]}
            pe = datetime.date.fromisoformat(m["period_end"])
            metrics, changed = ingest(q, comp, f, m["url"], text, label, pe)
            seen[tk][m["accession"]] = {"filed": m["filed"], "form": comp["form"], "status": "processed",
                                        "label": label, "url": m["url"]}
            print("回补 %s %s：%d 项" % (tk, label, len(metrics)))
    store.save_quarterly(q)
    save_json(SEEN_PATH, seen)
    return 0


def run_mark_seen(args):
    """把近 400 天内的全部候选申报记为已见（含 CHKP 非业绩 6-K），避免云端首跑重复处理。"""
    seen = load_json(SEEN_PATH, {}) or {}
    cutoff = (today_utc() - datetime.timedelta(days=400)).isoformat()
    for comp in companies():
        tk = comp["ticker"]
        seen.setdefault(tk, {})
        sub = submissions(comp["cik"])
        n = 0
        for f in earnings_filings(comp, since=cutoff, sub=sub):
            if f["accession"] not in seen[tk]:
                seen[tk][f["accession"]] = {"filed": f["filed"], "form": f["form"], "status": "setup"}
                n += 1
        print("%s 标记已见 %d 份" % (tk, n))
    save_json(SEEN_PATH, seen)
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--backfill", metavar="SINCE")
    ap.add_argument("--from-dir")
    ap.add_argument("--mark-seen", action="store_true")
    ap.add_argument("--window", type=int, default=45, help="常规模式回看天数")
    args = ap.parse_args()
    if args.mark_seen:
        return run_mark_seen(args)
    if args.backfill:
        return run_backfill(args)
    return run_normal(args)


if __name__ == "__main__":
    sys.exit(main())
