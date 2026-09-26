#!/usr/bin/env python3
"""按 config/signals.json 的规则给 8 项信号打状态，写 data/signals_state.json；状态有变化就生成推送事件。

状态：pending 待数据 / hold 维持 / watch 接近阈值 / up 触发·上调 / down 触发·下调
（S8 的 down 显示为"触发·维持最弱"，up 显示为"触发·重估"。）
"""
import datetime
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import CONFIG, DATA, load_json, now_bjt, save_json  # noqa: E402
from sec_edgar import label_sort_key  # noqa: E402
import store  # noqa: E402

STATUS_CN = {"pending": "待数据", "hold": "维持", "watch": "接近阈值", "up": "触发·上调", "down": "触发·下调"}
METRICS = load_json(os.path.join(CONFIG, "metrics.json"))


def mlabel(tk, key):
    for m in METRICS.get(tk, []):
        if m["key"] == key:
            return m["label"]
    return key


def fmt_pct(v, ql="", level=False):
    """同比用带符号的 +9.5%（整数去掉 .0，负号用 −）；占比等水平值 level=True 不带符号。"""
    if v is None:
        return "—"
    q = "超 " if ql == ">" else ("约 " if ql == "≈" else "")
    body = ("%.1f" % abs(v)).rstrip("0").rstrip(".") if abs(v - round(v)) < 1e-9 else "%.1f" % abs(v)
    if level:
        return "%s%s%%" % (q, body)
    sign = "+" if v > 0 else ("−" if v < 0 else "")
    return "%s%s%s%%" % (q, sign, body)


def fmt_usd(v, ql=""):
    """百万美元 → 中文：≥1 亿用"$X 亿"，否则"$X 万"。"""
    if v is None:
        return "—"
    q = "超 " if ql == ">" else ("约 " if ql == "≈" else "")
    if v >= 100:
        s = ("%.2f" % (v / 100.0)).rstrip("0").rstrip(".")
        return "%s$%s 亿" % (q, s)
    return "%s$%s 万" % (q, "{:,.0f}".format(v * 100))


def fmt_item(it):
    if not it:
        return "—"
    u, ql = it.get("unit"), it.get("qualifier") or ""
    if u == "pct":
        return fmt_pct(it["value"], ql, level=it.get("level", False))
    if u == "USD_M":
        return fmt_usd(it["value"], ql)
    if u == "flag":
        return "已启用" if it["value"] else "未启用"
    return str(it["value"])


def latest_period(q, tk):
    pers = [p for p in (q.get(tk) or {}) if not p.startswith("_")]
    return max(pers, key=label_sort_key) if pers else None


def after(p, base):
    return label_sort_key(p) > label_sort_key(base)


def series_out(ser):
    return [[p, round(it["value"], 2), it.get("qualifier") or ""] for p, it in ser]


# ---------------- 规则 ----------------

def rule_threshold_streak(s, r, q):
    tk, key = r["ticker"], r["metric"]
    ser = store.metric_series(q, tk, key)
    if not ser:
        return {"status": "pending", "reading": "—", "reason": "还没有数据"}
    per, it = ser[-1]
    v = it["value"]
    aux = []
    for k in r.get("aux", []):
        rec = ((q.get(tk) or {}).get(per) or {}).get("metrics", {}).get(k)
        if rec:
            aux.append("%s %s" % (mlabel(tk, k).replace("安全同比", "").strip("（）") or k, fmt_item(rec)))
    status, reason = "hold", ""
    up, down = r.get("up"), r.get("down")
    last = lambda n: [x["value"] for _, x in ser[-n:]] if len(ser) >= n else None  # noqa: E731
    if up and last(up["n"]) and all(x > up["gt"] for x in last(up["n"])):
        status = "up"
        reason = "最近 %d 季 %s 均超 %g%%" % (up["n"], "、".join(fmt_pct(x) for x in last(up["n"])), up["gt"])
    elif down and last(down["n"]) and all(x < down["lt"] for x in last(down["n"])):
        status = "down"
        reason = "%s 跌破 %g%%" % (fmt_pct(v), down["lt"])
    else:
        gaps = []
        if up:
            gaps.append("距上调线 %g%% 还差 %.1f 个百分点" % (up["gt"], up["gt"] - v))
            if up["n"] > 1 and v > up["gt"]:
                gaps[-1] = "本季已超 %g%%，再有一季即触发上调" % up["gt"]
        if down:
            gaps.append("距下调线 %g%% 还有 %.1f 个百分点" % (down["lt"], v - down["lt"]))
        reason = "；".join(gaps)
        if (r.get("watch_hi") is not None and v >= r["watch_hi"]) or \
           (r.get("watch_lo") is not None and v <= r["watch_lo"]):
            status = "watch"
    reading = "%s（%s）" % (fmt_pct(v), per)
    return {"status": status, "reading": reading, "value": v, "period": per, "aux": "；".join(aux),
            "reason": reason, "series": series_out(ser[-12:])}


def rule_threshold_latest(s, r, q):
    tk, key = r["ticker"], r["metric"]
    ser = store.metric_series(q, tk, key)
    if not ser:
        return {"status": "pending", "reading": "—", "reason": "还没有数据"}
    per, it = ser[-1]
    v, ql = it["value"], it.get("qualifier") or ""
    reading = "%s（%s）" % (fmt_item(it), per)
    base = s["baseline"]["period"]
    if not after(per, base):
        status = "hold"
        reason = "基线读数；等下一季（阈值 %s）" % fmt_usd(r["gt"])
    elif v > r["gt"] or (ql == ">" and v >= r["gt"]):
        status, reason = "up", "%s 超过 %s" % (fmt_item(it), fmt_usd(r["gt"]))
    elif v >= r.get("watch", r["gt"]):
        status, reason = "watch", "%s，接近 %s" % (fmt_item(it), fmt_usd(r["gt"]))
    else:
        status, reason = "hold", "%s，未达 %s" % (fmt_item(it), fmt_usd(r["gt"]))
    return {"status": status, "reading": reading, "value": v, "period": per, "reason": reason,
            "series": series_out(ser[-8:])}


def rule_disclosure(s, r, q):
    tk = r["ticker"]
    lp = latest_period(q, tk)
    if not lp:
        return {"status": "pending", "reading": "—", "reason": "还没有数据"}
    for per in sorted((p for p in q.get(tk, {}) if not p.startswith("_") and after(p, r["after"])),
                      key=label_sort_key, reverse=True):
        mets = q[tk][per].get("metrics", {})
        for k in r.get("up_metrics", []):
            if k in mets:
                return {"status": "up", "period": per,
                        "reading": "%s %s（%s）" % (mlabel(tk, k), fmt_item(mets[k]), per),
                        "reason": "首次单列披露"}
    for per in sorted((p for p in q.get(tk, {}) if not p.startswith("_") and after(p, r["after"])),
                      key=label_sort_key, reverse=True):
        mets = q[tk][per].get("metrics", {})
        for k in r.get("watch_metrics", []):
            if k in mets:
                return {"status": "watch", "period": per,
                        "reading": "%s %s（%s）" % (mlabel(tk, k), fmt_item(mets[k]), per),
                        "reason": s.get("impact", {}).get("watch", "出现部分披露")}
    hist = []
    for k in r.get("watch_metrics", []):
        hist += store.metric_series(q, tk, k)
    return {"status": "hold", "period": lp, "reading": "未披露（最新一期 %s）" % lp,
            "reason": r.get("hold_text", "最新一期新闻稿与电话会都没有披露"), "series": series_out(hist[-8:])}


def rule_disclosure_threshold(s, r, q):
    tk, key = r["ticker"], r["metric"]
    lp = latest_period(q, tk)
    ser = [(p, it) for p, it in store.metric_series(q, tk, key) if not after(r["from"], p)]
    if not ser:
        return {"status": "hold", "period": lp, "reading": "未披露绝对值（最新一期 %s）" % (lp or "—"),
                "reason": "只给相对增速"}
    per, it = ser[-1]
    v = it["value"]
    if v > r["gt"]:
        return {"status": "up", "period": per, "value": v, "reading": "%s（%s）" % (fmt_item(it), per),
                "reason": "披露绝对值且超过 %s" % fmt_usd(r["gt"]), "series": series_out(ser)}
    return {"status": "watch", "period": per, "value": v, "reading": "%s（%s）" % (fmt_item(it), per),
            "reason": "已披露绝对值，未超 %s" % fmt_usd(r["gt"]), "series": series_out(ser)}


def rule_composite(s, r, q):
    parts, statuses = [], []
    for pt in r["parts"]:
        ser = store.metric_series(q, pt["ticker"], pt["metric"])
        if not ser:
            parts.append({"label": pt["label"], "reading": "—", "status": "pending"})
            continue
        per, it = ser[-1]
        v = it["value"]
        if pt["kind"] == "flag":
            st = "up" if v else "hold"
        else:
            st = "up" if v > pt["gt"] else ("watch" if v >= pt.get("watch", pt["gt"]) else "hold")
        statuses.append(st)
        parts.append({"label": pt["label"], "ticker": pt["ticker"], "reading": "%s（%s）" % (fmt_item(it), per),
                      "status": st, "series": series_out(ser[-8:])})
    if "up" in statuses:
        status = "up"
    elif "watch" in statuses:
        status = "watch"
    elif statuses:
        status = "hold"
    else:
        status = "pending"
    reading = "；".join("%s %s" % (p.get("ticker", ""), p["reading"]) for p in parts)
    reason = "；".join("%s：%s" % (p["label"], STATUS_CN[p["status"]]) for p in parts)
    return {"status": status, "reading": reading, "reason": reason, "parts": parts}


def rule_target_period(s, r, q):
    tk, key, target = r["ticker"], r["metric"], r["target"]
    ser = store.metric_series(q, tk, key)
    if not ser:
        return {"status": "pending", "reading": "—", "reason": "还没有数据"}
    per, it = ser[-1]
    v = it["value"]
    reading = "%s（%s）" % (fmt_pct(v), per)
    base_per = "%dQ%s" % (int(target[:4]) - 1, target[-1]) if not target.startswith("FY") else None
    base_rev = None
    if base_per:
        b = ((q.get(tk) or {}).get(base_per) or {}).get("metrics", {}).get("product_rev")
        base_rev = b["value"] if b else None
    tgt = dict(ser).get(target)
    if tgt is not None:
        if tgt["value"] > r["gt"]:
            return {"status": "up", "label": "触发·重估", "reading": "%s（%s）" % (fmt_pct(tgt["value"]), target),
                    "value": tgt["value"], "period": target, "reason": "%s 回正" % target, "series": series_out(ser[-12:])}
        return {"status": "down", "label": "触发·维持最弱", "reading": "%s（%s）" % (fmt_pct(tgt["value"]), target),
                "value": tgt["value"], "period": target, "reason": "%s 未回正" % target, "series": series_out(ser[-12:])}
    need = "（需产品收入超过 2025Q4 的 %s）" % fmt_usd(base_rev) if base_rev else ""
    status = "watch" if v > r["gt"] else "hold"
    reason = ("%s 已提前回正；" % per if v > r["gt"] else "") + "判定季是 %s%s" % (target, need)
    return {"status": status, "reading": reading, "value": v, "period": per, "reason": reason,
            "series": series_out(ser[-12:])}


RULES = {"threshold_streak": rule_threshold_streak, "threshold_latest": rule_threshold_latest,
         "disclosure": rule_disclosure, "disclosure_threshold": rule_disclosure_threshold,
         "composite": rule_composite, "target_period": rule_target_period}


# ---------------- 财报日 ----------------

def next_event(tickers, cal, seed, today):
    best = None
    for tk in tickers:
        e = cal.get(tk) or {}
        src = "cal"
        if not e.get("date") or e["date"] < today.isoformat():
            e = seed.get(tk) or {}
            src = "seed"
        if not e.get("date") or e["date"] < today.isoformat():
            continue
        d = datetime.date.fromisoformat(e["date"])
        item = {"ticker": tk, "date": e["date"], "days": (d - today).days, "status": e.get("status", "预估"),
                "timing": e.get("timing", ""), "period": e.get("period", ""), "src": src}
        if best is None or item["date"] < best["date"]:
            best = item
    return best


def main():
    cfg = load_json(os.path.join(CONFIG, "signals.json"))
    q = store.load_quarterly()
    cal = load_json(os.path.join(DATA, "calendar.json"), {}) or {}
    seed = (load_json(os.path.join(CONFIG, "calendar_seed.json"), {}) or {}).get("dates", {})
    prev = (load_json(os.path.join(DATA, "signals_state.json"), {}) or {}).get("signals", {})
    today = now_bjt().date()
    out = {"updated_bjt": now_bjt().strftime("%Y-%m-%d %H:%M"), "signals": {}}
    counts = {}
    for s in cfg["signals"]:
        res = RULES[s["rule"]["type"]](s, s["rule"], q)
        res["label"] = res.get("label") or STATUS_CN[res["status"]]
        res["next_event"] = next_event(s["tickers"], cal, seed, today)
        old = prev.get(s["id"]) or {}
        res["since"] = old.get("since") if old.get("status") == res["status"] and old.get("since") else today.isoformat()
        out["signals"][s["id"]] = res
        counts[res["status"]] = counts.get(res["status"], 0) + 1
        if old and old.get("status") != res["status"]:
            impact = s.get("impact", {}).get(res["status"], "")
            store.add_event({
                "id": "signal-%s-%s-%s" % (s["id"], today.isoformat(), res["status"]),
                "type": "signal_change", "signal": s["id"],
                "title": "%s %s：%s → %s" % (s["id"], s["short"], old.get("label", STATUS_CN.get(old.get("status"), "?")),
                                           res["label"]),
                "lines": [x for x in [res.get("reading"), res.get("reason"), impact] if x],
            })
            print("🔔 %s %s → %s" % (s["id"], old.get("status"), res["status"]))
    th = cfg["thesis"]
    alerts = []
    for tk, sigs in th["links"].items():
        moved = [sid for sid in sigs if out["signals"][sid]["status"] in ("up", "down")]
        if moved:
            alerts.append({"ticker": tk, "signals": moved})
    out["summary"] = counts
    out["thesis"] = {"ranking": th["ranking"], "asof": th["asof"], "alerts": alerts}
    save_json(os.path.join(DATA, "signals_state.json"), out)
    print("信号状态：" + "，".join("%s %d" % (STATUS_CN[k], v) for k, v in counts.items()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
