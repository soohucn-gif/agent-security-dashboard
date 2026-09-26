#!/usr/bin/env python3
"""把 data/ 下的 JSON 渲染成单文件 index.html（GitHub Pages 直接发布）。

纯标准库：SVG 在这里算好，页面只带一小段 JS 做悬停提示与折线十字准星。
配色与图表规范按 dataviz 参考色板：一条轴、细线、4px 圆角柱头、阈值线用虚线并直接标注、
状态一律"图标 + 文字"，深色模式单独取色。
"""
import csv
import datetime
import html
import json
import math
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import CONFIG, DATA, REPO_URL, ROOT, SITE_URL, load_json, now_bjt  # noqa: E402
from evaluate import STATUS_CN, fmt_item, fmt_pct, fmt_usd, mlabel  # noqa: E402
from sec_edgar import label_sort_key  # noqa: E402
import store  # noqa: E402

ICON = {"up": "↑", "down": "↓", "watch": "!", "hold": "•", "pending": "…"}
TYPE_CN = {"baseline": "基线", "earnings_review": "财报复盘", "season_review": "季度复盘", "note": "备注",
           "earnings": "财报入库"}
VERDICT_ST = {"上调": "up", "下调": "down", "观察": "watch", "维持": "hold", "—": "pending"}
CORE = ["AKAM", "PANW", "FTNT", "CHKP"]


def esc(s):
    return html.escape("" if s is None else str(s), quote=True)


def chip(status, label=None):
    return '<span class="chip st-%s"><span class="ic" aria-hidden="true">%s</span>%s</span>' % (
        status, ICON.get(status, "•"), esc(label or STATUS_CN.get(status, status)))


def nice_ticks(lo, hi, n=4):
    if hi == lo:
        hi = lo + 1
    raw = (hi - lo) / n
    mag = 10 ** math.floor(math.log10(raw))
    step = min((s * mag for s in (1, 2, 2.5, 5, 10) if s * mag >= raw), default=10 * mag)
    start = math.floor(lo / step) * step
    ticks = []
    v = start
    while v <= hi + step * 0.001:
        ticks.append(round(v, 10))
        v += step
    return ticks


def bar_path(x, y0, y1, w, r=4):
    """柱：数据端 4px 圆角，基线端直角。y0=基线像素，y1=数据端像素。"""
    h = abs(y1 - y0)
    r = min(r, w / 2.0, h)
    if h < 0.5:
        return ""
    if y1 < y0:   # 向上
        return ("M%.1f,%.1f L%.1f,%.1f Q%.1f,%.1f %.1f,%.1f L%.1f,%.1f Q%.1f,%.1f %.1f,%.1f L%.1f,%.1f Z"
                % (x, y0, x, y1 + r, x, y1, x + r, y1, x + w - r, y1, x + w, y1, x + w, y1 + r, x + w, y0))
    return ("M%.1f,%.1f L%.1f,%.1f Q%.1f,%.1f %.1f,%.1f L%.1f,%.1f Q%.1f,%.1f %.1f,%.1f L%.1f,%.1f Z"
            % (x, y0, x, y1 - r, x, y1, x + r, y1, x + w - r, y1, x + w, y1, x + w, y1 - r, x + w, y0))


# ---------------- 图表 ----------------

def column_chart(series, fmt, thresholds=(), dots=None, width=680, height=250, target=None,
                 unit_label="", mini=False, aria=""):
    """series=[(label, value, qualifier)]；thresholds=[(value, 文字)]；dots=[(label, value)] 第二序列（圆点）。"""
    if not series:
        return ""
    if mini:
        width, height = 300, 84
        ml, mr, mt, mb = 6, 34, 8, 16
    else:
        ml, mr, mt, mb = 46, 78, 18, 34
    labels = [s[0] for s in series] + ([target] if target else [])
    vals = [s[1] for s in series] + [t[0] for t in thresholds] + [d[1] for d in (dots or [])] + [0]
    lo, hi = min(vals), max(vals)
    pad = (hi - lo) * 0.08 or 1
    ticks = nice_ticks(lo - (pad if lo < 0 else 0), hi + pad)
    lo, hi = min(ticks[0], lo), max(ticks[-1], hi)
    pw, ph = width - ml - mr, height - mt - mb
    band = pw / max(1, len(labels))
    bw = min(24 if not mini else 14, band * 0.62)
    y = lambda v: mt + ph - (v - lo) / (hi - lo) * ph  # noqa: E731
    out = ['<svg class="chart%s" viewBox="0 0 %d %d" role="img" aria-label="%s">' % (
        " mini" if mini else "", width, height, esc(aria))]
    if not mini:
        for t in ticks:
            out.append('<line class="gl" x1="%d" x2="%d" y1="%.1f" y2="%.1f"/>' % (ml, width - mr, y(t), y(t)))
            out.append('<text class="tick" x="%d" y="%.1f" text-anchor="end">%s</text>' % (ml - 6, y(t) + 4, esc(fmt(t, tick=True))))
    out.append('<line class="axis" x1="%d" x2="%d" y1="%.1f" y2="%.1f"/>' % (ml, width - mr, y(0), y(0)))
    for tv, tlab in thresholds:
        out.append('<line class="thr" x1="%d" x2="%d" y1="%.1f" y2="%.1f"/>' % (ml, width - mr, y(tv), y(tv)))
        out.append('<text class="thr-l" x="%d" y="%.1f">%s</text>' % (width - mr + 4, y(tv) + 4, esc(tlab)))
    n = len(series)
    for i, (lab, v, ql) in enumerate(series):
        x = ml + band * i + (band - bw) / 2
        cls = "bar-last" if i == n - 1 else "bar-hist"
        d = bar_path(x, y(0), y(v), bw)
        tip = "%s：%s" % (lab, fmt(v, ql=ql))
        out.append('<path class="%s" d="%s"/>' % (cls, d))
        out.append('<rect class="hit" x="%.1f" y="%d" width="%.1f" height="%d" data-tip="%s" tabindex="0"/>' % (
            ml + band * i, mt, band, ph, esc(tip)))
        if not mini and (i == n - 1 or n <= 6):
            ty = y(v) - 6 if v >= 0 else y(v) + 14
            out.append('<text class="val" x="%.1f" y="%.1f" text-anchor="middle">%s</text>' % (x + bw / 2, ty, esc(fmt(v, ql=ql))))
        show = ((i == 0) or (i == n - 1 and not target)) if mini else (n <= 12 or i % 2 == (n - 1) % 2)
        if show:
            out.append('<text class="xl" x="%.1f" y="%d" text-anchor="middle">%s</text>' % (
                ml + band * i + band / 2, height - (3 if mini else 12), esc(lab)))
    if dots:
        idx = {s[0]: i for i, s in enumerate(series)}
        for lab, v in dots:
            if lab in idx:
                cx = ml + band * idx[lab] + band / 2
                out.append('<circle class="dot2" cx="%.1f" cy="%.1f" r="4.5"/>' % (cx, y(v)))
    if target:
        i = len(series)
        x = ml + band * i + (band - bw) / 2
        out.append('<rect class="target" x="%.1f" y="%.1f" width="%.1f" height="%.1f" rx="4"/>' % (
            x, mt + ph * 0.25, bw, ph * 0.5))
        out.append('<text class="xl" x="%.1f" y="%d" text-anchor="middle">%s</text>' % (
            ml + band * i + band / 2, height - (3 if mini else 12), esc(target)))
        if not mini:
            out.append('<text class="val" x="%.1f" y="%.1f" text-anchor="middle">判定季</text>' % (x + bw / 2, mt + ph * 0.25 - 6))
    if unit_label and not mini:
        out.append('<text class="tick" x="%d" y="%d">%s</text>' % (ml, 11, esc(unit_label)))
    out.append("</svg>")
    return "".join(out)


def hbar_chart(items, width=680):
    """items=[(ticker, name, value, is_core)]，年内涨跌；正值蓝、负值红（发散两极），零线居中。"""
    if not items:
        return ""
    rowh, ml, mr, mt = 26, 150, 60, 8
    height = mt + rowh * len(items) + 8
    vals = [v for _, _, v, _ in items if v is not None] + [0]
    lo, hi = min(vals), max(vals)
    span = max(hi, 0) - min(lo, 0) or 1
    pw = width - ml - mr
    x = lambda v: ml + (v - min(lo, 0)) / span * pw  # noqa: E731
    out = ['<svg class="chart" viewBox="0 0 %d %d" role="img" aria-label="年内涨跌幅">' % (width, height)]
    out.append('<line class="axis" x1="%.1f" x2="%.1f" y1="%d" y2="%d"/>' % (x(0), x(0), mt, height - 8))
    for i, (tk, name, v, core) in enumerate(items):
        yc = mt + rowh * i + rowh / 2
        out.append('<text class="yl%s" x="%d" y="%.1f" text-anchor="end">%s</text>' % (
            " core" if core else "", ml - 8, yc + 4, esc("%s %s" % (tk, name))))
        if v is None:
            continue
        x0, x1 = x(0), x(v)
        h = 14
        if v >= 0:
            d = "M%.1f,%.1f L%.1f,%.1f Q%.1f,%.1f %.1f,%.1f L%.1f,%.1f Q%.1f,%.1f %.1f,%.1f L%.1f,%.1f Z" % (
                x0, yc - h / 2, max(x0, x1 - 4), yc - h / 2, x1, yc - h / 2, x1, yc - h / 2 + 4,
                x1, yc + h / 2 - 4, x1, yc + h / 2, max(x0, x1 - 4), yc + h / 2, x0, yc + h / 2)
            cls, tx, anc = "bar-pos", x1 + 6, "start"
        else:
            d = "M%.1f,%.1f L%.1f,%.1f Q%.1f,%.1f %.1f,%.1f L%.1f,%.1f Q%.1f,%.1f %.1f,%.1f L%.1f,%.1f Z" % (
                x0, yc - h / 2, min(x0, x1 + 4), yc - h / 2, x1, yc - h / 2, x1, yc - h / 2 + 4,
                x1, yc + h / 2 - 4, x1, yc + h / 2, min(x0, x1 + 4), yc + h / 2, x0, yc + h / 2)
            cls, tx, anc = "bar-neg", x0 + 6, "start"
        out.append('<path class="%s" d="%s"/>' % (cls, d))
        out.append('<text class="val" x="%.1f" y="%.1f" text-anchor="%s">%s</text>' % (tx, yc + 4, anc, esc(fmt_pct(v))))
        out.append('<rect class="hit" x="0" y="%.1f" width="%d" height="%d" data-tip="%s" tabindex="0"/>' % (
            yc - rowh / 2, width, rowh, esc("%s 年内 %s" % (tk, fmt_pct(v)))))
    out.append("</svg>")
    return "".join(out)


def line_chart(lines, width=680, height=260, chart_id="px"):
    """lines=[(key, 名称, [(date, v)], css 类)]；同一基期=100，一条轴。十字准星由页面 JS 画。"""
    lines = [l for l in lines if l[2]]
    if not lines:
        return ""
    ml, mr, mt, mb = 40, 70, 14, 30
    dates = sorted({d for _, _, pts, _ in lines for d, _ in pts})
    di = {d: i for i, d in enumerate(dates)}
    vals = [v for _, _, pts, _ in lines for _, v in pts]
    ticks = nice_ticks(min(vals + [100]), max(vals + [100]))
    lo, hi = ticks[0], ticks[-1]
    pw, ph = width - ml - mr, height - mt - mb
    x = lambda i: ml + (i / max(1, len(dates) - 1)) * pw  # noqa: E731
    y = lambda v: mt + ph - (v - lo) / (hi - lo) * ph  # noqa: E731
    out = ['<svg class="chart" id="%s" viewBox="0 0 %d %d" role="img" aria-label="年初以来股价（年初 = 100）">' % (
        chart_id, width, height)]
    for t in ticks:
        out.append('<line class="gl" x1="%d" x2="%d" y1="%.1f" y2="%.1f"/>' % (ml, width - mr, y(t), y(t)))
        out.append('<text class="tick" x="%d" y="%.1f" text-anchor="end">%s</text>' % (ml - 6, y(t) + 4, esc("%g" % t)))
    out.append('<line class="axis" x1="%d" x2="%d" y1="%.1f" y2="%.1f"/>' % (ml, width - mr, y(100), y(100)))
    # 月份刻度
    seen = set()
    for d in dates:
        mth = d[:7]
        if mth not in seen and d[8:] <= "07":
            seen.add(mth)
            out.append('<text class="xl" x="%.1f" y="%d" text-anchor="middle">%s</text>' % (x(di[d]), height - 10, esc(str(int(d[5:7])) + "月")))
    ends = []
    payload = {"dates": dates, "series": []}
    for key, name, pts, cls in lines:
        path = "M" + " L".join("%.1f,%.1f" % (x(di[d]), y(v)) for d, v in pts)
        out.append('<path class="ln %s" d="%s"/>' % (cls, path))
        d_last, v_last = pts[-1]
        out.append('<circle class="end %s" cx="%.1f" cy="%.1f" r="4"/>' % (cls, x(di[d_last]), y(v_last)))
        ends.append([y(v_last), key, v_last])
        m = {d: round(v, 2) for d, v in pts}
        payload["series"].append({"key": key, "name": name, "cls": cls, "v": [m.get(d) for d in dates]})
    # 终点标签：按 y 排开，间距不够就交给图例与提示
    ends.sort()
    last_y = -99
    for yy, key, v in ends:
        if yy - last_y >= 13:
            out.append('<text class="endl" x="%d" y="%.1f">%s %s</text>' % (width - mr + 8, yy + 4, esc(key), esc("%.0f" % v)))
            last_y = yy
    out.append('<line class="xhair" x1="0" x2="0" y1="%d" y2="%d" visibility="hidden"/>' % (mt, mt + ph))
    out.append('<rect class="xhit" x="%d" y="%d" width="%d" height="%d" data-geo="%d,%d"/>' % (ml, mt, pw, ph, ml, pw))
    out.append("</svg>")
    out.append('<script type="application/json" id="%s-data">%s</script>' % (chart_id, json.dumps(payload, ensure_ascii=False)))
    return "".join(out)


# ---------------- 数据取用 ----------------

def read_prices(tk):
    path = os.path.join(DATA, "prices", "%s.csv" % tk)
    if not os.path.exists(path):
        return []
    with open(path, newline="", encoding="utf-8") as fh:
        return [(r["date"], float(r["close"])) for r in csv.DictReader(fh)]


def ytd_index(tk, year):
    px = read_prices(tk)
    base = None
    for d, c in px:
        if d <= "%d-12-31" % (year - 1):
            base = c
    if base is None:
        return []
    return [(d, c / base * 100.0) for d, c in px if d > "%d-12-31" % (year - 1)]


def ffmt_pct(v, tick=False, ql=""):
    return ("%g%%" % v) if tick else fmt_pct(v, ql)


def ffmt_usd_b(v, tick=False, ql=""):
    return ("%g" % (v / 100.0)) if tick else fmt_usd(v, ql)


def md_lite(text):
    """极简 markdown：段落、- 列表、**粗体**、[文字](链接)。先转义再替换，链接只放 http(s)。"""
    lines = esc(text).split("\n")
    out, in_ul = [], False
    for ln in lines:
        s = ln.strip()
        if s.startswith("- ") or s.startswith("• "):
            if not in_ul:
                out.append("<ul>")
                in_ul = True
            out.append("<li>%s</li>" % s[2:])
            continue
        if in_ul:
            out.append("</ul>")
            in_ul = False
        if s.startswith("### ") or s.startswith("## "):
            out.append("<h4>%s</h4>" % s.lstrip("# "))
        elif s:
            out.append("<p>%s</p>" % s)
    if in_ul:
        out.append("</ul>")
    h = "\n".join(out)
    h = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", h)
    h = re.sub(r"\[([^\]]+)\]\((https?://[^)\s]+)\)", r'<a href="\2" rel="noopener" target="_blank">\1</a>', h)
    return h


# ---------------- 页面各块 ----------------

def sec_header(state, market, cfg, now):
    counts = state.get("summary", {})
    chips = " ".join("%s <b>%d</b>" % (chip(k), v) for k, v in sorted(counts.items(), key=lambda kv: ["up", "down", "watch", "hold", "pending"].index(kv[0])))
    mdate = (next((v.get("date") for k, v in market.items() if not k.startswith("_") and v.get("date")), "") or "—")
    return """
<header class="top">
  <div class="kicker">AKAM · PANW · FTNT · CHKP · 云端跟踪</div>
  <h1>Agent 时代安全 · 跟踪看板</h1>
  <p class="sub">跟踪 {rdate} 研究底稿第七节的 8 项改判信号：财报一出就自动取数、对照阈值、推送飞书，云端分析员补电话会口径并写反馈。</p>
  <div class="meta"><span>页面生成 {now}（北京）</span><span>行情截至 {mdate}</span><span><a href="{repo}">数据与脚本</a></span></div>
  <div class="counts">{chips}</div>
</header>""".format(rdate=esc(cfg["source_report"]["date"]), now=esc(now), mdate=esc(mdate), repo=esc(REPO_URL), chips=chips)


def sec_thesis(state, cfg):
    th = cfg["thesis"]
    alerts = state.get("thesis", {}).get("alerts", [])
    if alerts:
        al = "；".join("%s（%s）" % (a["ticker"], "、".join(a["signals"])) for a in alerts)
        status = '<p class="alert">%s 有信号触发，排序需要复核：%s</p>' % (chip("watch", "复核"), esc(al))
    else:
        status = '<p class="muted">目前没有信号触发，排序维持 %s 的判断。</p>' % esc(th["asof"])
    return """
<section class="thesis card">
  <div class="thesis-rank"><span class="lbl">agent 受益弹性</span><span class="rank">{rank}</span></div>
  <p>{text}</p>
  {status}
</section>""".format(rank=esc(th["ranking"]), text=esc(th["text"]), status=status)


def sec_next(cal_rows):
    items = []
    for r in cal_rows[:4]:
        items.append('<li><span class="d">%s</span><span class="t">%s</span><span class="p">%s · %s%s</span><span class="s">%s</span></li>' % (
            esc(r["date"][5:].replace("-", "/")), esc(r["ticker"]), esc(r.get("period") or ""), esc(r["status"]),
            (" · " + esc(r["timing"])) if r.get("timing") else "", esc("、".join(r["signals"]) or "参照")))
    return '<section class="next"><h2>接下来</h2><ol class="nextlist">%s</ol></section>' % "".join(items)


def mini_for(sid, st, q):
    s = st
    if sid in ("S1", "S7", "S8"):
        ser = [(p, v, ql) for p, v, ql in s.get("series", [])]
        thr = {"S1": [(12, "12%"), (8, "8%")], "S7": [(20, "20%")], "S8": [(0, "0")]}[sid]
        return column_chart(ser[-10:], ffmt_pct, thr, mini=True, target="2026Q4" if sid == "S8" and "2026Q4" not in [x[0] for x in ser] else None,
                            aria="%s 走势" % sid)
    if sid == "S4":
        parts = s.get("parts", [])
        sail = next((p for p in parts if p.get("ticker") == "SAIL"), None)
        if sail and sail.get("series"):
            v = sail["series"][-1][1]
            pct = max(0, min(100, v / 10.0 * 100))
            return ('<div class="meter" role="img" aria-label="SailPoint AI 驱动 ARR 占比 %.1f%%，阈值 10%%">'
                    '<div class="meter-track"><div class="meter-fill" style="width:%.1f%%"></div></div>'
                    '<div class="meter-l"><span>SailPoint AI 占比 %s</span><span>阈值 10%%</span></div></div>' % (v, pct, esc(fmt_pct(v, level=True))))
    if sid == "S5":
        ser = s.get("series", [])
        if ser:
            v = ser[-1][1]
            pct = max(0, min(100, v / 200.0 * 100))
            return ('<div class="meter" role="img" aria-label="Prisma AIRS ARR 相对 $2 亿阈值">'
                    '<div class="meter-track"><div class="meter-fill" style="width:%.1f%%"></div></div>'
                    '<div class="meter-l"><span>%s</span><span>阈值 $2 亿</span></div></div>' % (pct, esc(s.get("reading", ""))))
    return ""


def sec_signals(cfg, state, q):
    cards = []
    for s in cfg["signals"]:
        st = state["signals"].get(s["id"], {})
        ne = st.get("next_event") or {}
        nxt = ""
        if ne:
            nxt = '<div class="nx">下次读数：%s %s %s（%s，%d 天后）</div>' % (
                esc(ne["ticker"]), esc(ne.get("period") or ""), esc(ne["date"]), esc(ne["status"]), ne["days"])
        aux = '<div class="aux">%s</div>' % esc(st["aux"]) if st.get("aux") else ""
        cards.append("""
<article class="sig card st-b-{status}" id="{sid}">
  <div class="sig-h"><span class="sid">{sid}</span><span class="layer">{layer}</span>{chip}</div>
  <h3>{title}</h3>
  <div class="reading">{reading}</div>{aux}
  <div class="reason">{reason}</div>
  {mini}
  <div class="rule"><span class="lbl">改判规则</span>{trigger}</div>
  <div class="base"><span class="lbl">底稿基线</span>{base}</div>
  {nxt}
</article>""".format(status=esc(st.get("status", "pending")), sid=esc(s["id"]), layer=esc(s["layer"]),
                     chip=chip(st.get("status", "pending"), st.get("label")), title=esc(s["title"]),
                     reading=esc(st.get("reading", "—")), aux=aux, reason=esc(st.get("reason", "")),
                     mini=mini_for(s["id"], st, q), trigger=esc(s["trigger_text"]),
                     base=esc("%s：%s" % (s["baseline"]["period"], s["baseline"]["text"])), nxt=nxt))
    return '<section><h2>8 项改判信号</h2><div class="grid">%s</div></section>' % "".join(cards)


def series_of(q, tk, key):
    return [(p, it["value"], it.get("qualifier") or "") for p, it in store.metric_series(q, tk, key)]


def table_view(rows, head):
    tr = "".join("<tr>%s</tr>" % "".join("<td>%s</td>" % esc(c) for c in r) for r in rows)
    return '<details class="tv"><summary>数据表</summary><div class="tw"><table><thead><tr>%s</tr></thead><tbody>%s</tbody></table></div></details>' % (
        "".join("<th>%s</th>" % esc(h) for h in head), tr)


def sec_series(q):
    blocks = []
    # AKAM 安全同比：柱=报告币种精确值；圆点=不变汇率（公司口径）
    ak = series_of(q, "AKAM", "security_yoy")[-11:]
    cc = [(p, v) for p, v, _ in series_of(q, "AKAM", "security_yoy_cc")]
    blocks.append(("AKAM 安全收入同比", "柱：报告币种（报表精确值）；圆点：不变汇率（公司四舍五入）。虚线：12% 上调线、8% 下调线。",
                   '<div class="legend"><span class="k k-bar"></span>报告币种<span class="k k-dot"></span>不变汇率</div>' +
                   column_chart(ak, ffmt_pct, [(12, "上调 12%"), (8, "下调 8%")], dots=cc, aria="AKAM 安全收入同比"),
                   table_view([(p, fmt_pct(v), fmt_pct(dict(cc).get(p))) for p, v, _ in ak], ["财季", "报告币种", "不变汇率"])))
    ft = series_of(q, "FTNT", "product_yoy")[-11:]
    blocks.append(("FTNT 产品收入同比", "AI 数据中心换机周期的温度计。虚线：20% 见顶线。",
                   column_chart(ft, ffmt_pct, [(20, "见顶 20%")], aria="FTNT 产品收入同比"),
                   table_view([(p, fmt_pct(v)) for p, v, _ in ft], ["财季", "产品收入同比"])))
    ck = series_of(q, "CHKP", "product_yoy")[-11:]
    tgt = "2026Q4" if "2026Q4" not in [x[0] for x in ck] else None
    blocks.append(("CHKP 产品收入同比", "公司称 2026Q4 回正；判定季未到时留空位。",
                   column_chart(ck, ffmt_pct, [(0, "0")], target=tgt, aria="CHKP 产品收入同比"),
                   table_view([(p, fmt_pct(v)) for p, v, _ in ck], ["财季", "产品收入同比"])))
    pa = series_of(q, "PANW", "ngs_arr")[-9:]
    blocks.append(("PANW NGS ARR", "单位：亿美元。FY26Q3 起含 CyberArk、Chronosphere（约 $16 亿）。Prisma AIRS ARR 见信号 S5。",
                   column_chart(pa, ffmt_usd_b, [], unit_label="$ 亿", aria="PANW NGS ARR"),
                   table_view([(p, fmt_usd(v)) for p, v, _ in pa], ["财季", "NGS ARR"])))
    out = []
    for title, note, chart, tv in blocks:
        out.append('<figure class="card fig"><figcaption><h3>%s</h3><p class="muted">%s</p></figcaption>%s%s</figure>' % (
            esc(title), esc(note), chart, tv))
    return '<section><h2>核心序列</h2><div class="grid2">%s</div></section>' % "".join(out)


def sec_market(market, cfg):
    rows, bars = [], []
    order = [k for k in ["AKAM", "PANW", "FTNT", "CHKP", "NET", "CRWD", "OKTA", "SAIL", "ZS", "CIBR", "QQQ"] if k in market]

    def cell(v):
        if v is None:
            return '<td class="num">—</td>'
        return '<td class="num %s">%s</td>' % ("pos" if v > 0 else ("neg" if v < 0 else ""), esc(fmt_pct(v)))
    for tk in order:
        m = market[tk]
        mc = m.get("mcap")
        mcs = "—" if not mc else ("$%s 亿" % "{:,.0f}".format(mc / 1e8))
        ps = "—" if not m.get("ps") else "%.1f×" % m["ps"]
        rows.append('<tr class="%s"><td><b>%s</b></td><td>%s</td><td class="num">%s</td>%s%s%s%s%s%s<td class="num">%s</td><td class="num" title="%s">%s</td></tr>' % (
            "core" if tk in CORE else ("bench" if m.get("group") == "bench" else ""), esc(tk), esc(m.get("name", "")),
            esc("%.2f" % m["close"]), cell(m.get("chg_1d")), cell(m.get("chg_1w")), cell(m.get("chg_1m")),
            cell(m.get("chg_ytd")), cell(m.get("chg_1y")), cell(m.get("from_hi")), esc(mcs), esc(m.get("ps_basis", "")), esc(ps)))
        bars.append((tk, m.get("name", ""), m.get("chg_ytd"), tk in CORE))
    bars.sort(key=lambda b: -(b[2] if b[2] is not None else -999))
    year = now_bjt().year
    lines = [(tk, market.get(tk, {}).get("name", tk), ytd_index(tk, year), "c%d" % (i + 1)) for i, tk in enumerate(CORE)]
    lines.append(("CIBR", "网络安全 ETF", ytd_index("CIBR", year), "cref"))
    legend = '<div class="legend">%s<span class="k k-ref"></span>CIBR 网络安全 ETF</div>' % "".join(
        '<span class="k k-c%d"></span>%s' % (i + 1, esc(tk)) for i, tk in enumerate(CORE))
    table = """<div class="tw"><table class="mkt"><thead><tr><th>代码</th><th>名称</th><th class="num">收盘</th><th class="num">1 日</th><th class="num">1 周</th><th class="num">1 月</th><th class="num">年内</th><th class="num">1 年</th><th class="num">距 52 周高点</th><th class="num">市值</th><th class="num">市销率</th></tr></thead><tbody>%s</tbody></table></div>""" % "".join(rows)
    return """
<section><h2>市场定价</h2>
<p class="muted">底稿第六节：被视为 agent 安全赢家的名字已先涨，AKAM 市销率几乎没给安全业务按“agent 边缘安全”估值——预期差在这里，风险也在这里。市销率 = 市值 ÷（最新季收入 × 4），与底稿同口径；悬停看口径。</p>
<div class="card">{table}</div>
<div class="grid2">
<figure class="card fig"><figcaption><h3>年内涨跌幅</h3><p class="muted">蓝为涨、红为跌；粗体为四家核心公司。</p></figcaption>{hbar}</figure>
<figure class="card fig"><figcaption><h3>年初以来走势（年初 = 100）</h3><p class="muted">四家核心公司对照网络安全 ETF。</p></figcaption>{legend}{line}<div class="xtip" id="px-tip" hidden></div></figure>
</div></section>""".format(table=table, hbar=hbar_chart(bars), legend=legend, line=line_chart(lines))


def calendar_rows(cfg, cal, seed, today):
    rows = []
    sig_by = {}
    for s in cfg["signals"]:
        for tk in s["tickers"]:
            sig_by.setdefault(tk, []).append(s["id"])
    for tk in sorted(set(list(seed.keys()) + list(cal.keys()))):
        e = cal.get(tk) or {}
        src = "Nasdaq/Zacks" if e else ""
        if not e.get("date") or e["date"] < today.isoformat():
            e, src = seed.get(tk) or {}, "按去年同期推算"
        if not e.get("date") or e["date"] < today.isoformat():
            continue
        d = datetime.date.fromisoformat(e["date"])
        rows.append({"ticker": tk, "date": e["date"], "days": (d - today).days, "status": e.get("status", "预估"),
                     "timing": e.get("timing") or (seed.get(tk) or {}).get("timing", ""),
                     "period": e.get("period") or (seed.get(tk) or {}).get("period", ""),
                     "signals": sig_by.get(tk, []), "src": e.get("source") or src})
    rows.sort(key=lambda r: r["date"])
    return rows


def sec_calendar(rows, later):
    tr = "".join('<tr><td class="num">%s</td><td class="num">%d 天</td><td><b>%s</b></td><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td class="muted">%s</td></tr>' % (
        esc(r["date"]), r["days"], esc(r["ticker"]), esc(r["period"]), esc(r["timing"]), esc(r["status"]),
        esc("、".join(r["signals"]) or "参照"), esc(r["src"])) for r in rows)
    lt = "".join("<li><b>%s</b>　%s</li>" % (esc(x["window"]), esc(x["what"])) for x in later)
    return """
<section><h2>财报日历</h2>
<div class="card"><div class="tw"><table><thead><tr><th>日期</th><th>距今</th><th>公司</th><th>财季</th><th>时段</th><th>状态</th><th>更新信号</th><th>来源</th></tr></thead><tbody>{tr}</tbody></table></div>
<ul class="later">{lt}</ul></div></section>""".format(tr=tr, lt=lt)


def sec_feedback(fb):
    items = []
    for e in reversed(fb[-40:]):
        st = VERDICT_ST.get(e.get("verdict", "—"), "pending")
        src = ""
        if e.get("sources"):
            src = '<div class="src">来源：%s</div>' % "；".join(
                '<a href="%s" rel="noopener" target="_blank">%s</a>' % (esc(u), esc(re.sub(r"^https?://(www\.)?", "", u)[:48]))
                for u in e["sources"] if u.startswith("http"))
        items.append("""
<article class="fb card">
  <div class="fb-h"><span class="date">{date}</span><span class="type">{type}</span>{chip}<span class="muted">{sig}</span></div>
  <h3>{title}</h3>
  <div class="body">{body}</div>
  {src}
  <div class="muted small">{author}{conf}</div>
</article>""".format(date=esc(e.get("date")), type=esc(TYPE_CN.get(e.get("type"), e.get("type"))),
                     chip=chip(st, e.get("verdict") if e.get("verdict") not in (None, "—") else "记录"),
                     sig=esc(" ".join(e.get("signals", []) + e.get("tickers", []))), title=esc(e.get("title")),
                     body=md_lite(e.get("body", "")), src=src, author=esc(e.get("author", "")),
                     conf=esc(" · 置信度 %s" % e["confidence"]) if e.get("confidence") else ""))
    if not items:
        items.append('<p class="muted">还没有反馈记录。</p>')
    return '<section><h2>跟踪反馈日志</h2><div class="fblist">%s</div></section>' % "".join(items)


def sec_ledger(q, metrics):
    out = []
    names = {c["ticker"]: c for c in load_json(os.path.join(CONFIG, "companies.json"))["companies"]}
    for tk in ["AKAM", "PANW", "FTNT", "CHKP", "NET", "CRWD", "OKTA", "SAIL", "ZS"]:
        recs = q.get(tk) or {}
        pers = sorted([p for p in recs if not p.startswith("_")], key=label_sort_key)[-8:]
        if not pers:
            continue
        head = "".join('<th class="num">%s</th>' % esc(p) for p in pers)
        body = []
        for m in metrics.get(tk, []):
            cells, has = [], False
            for p in pers:
                it = (recs[p].get("metrics") or {}).get(m["key"])
                if it:
                    has = True
                    mark = ""
                    if it.get("call"):
                        mark += '<sup title="电话会口径（第三方转录）">†</sup>'
                    if it.get("method") in ("routine", "manual"):
                        mark += '<sup title="%s">*</sup>' % ("云端分析员录入" if it["method"] == "routine" else "人工核对录入")
                    src = it.get("source") or recs[p].get("source_url") or ""
                    cells.append('<td class="num"><span title="%s">%s</span>%s</td>' % (
                        esc((it.get("quote") or "")[:200]), esc(fmt_item(it)), mark))
                else:
                    cells.append('<td class="num muted">—</td>')
            if has:
                body.append('<tr><th scope="row">%s</th>%s</tr>' % (esc(m["label"]), "".join(cells)))
        notes = []
        for p in pers[-3:]:
            for n in recs[p].get("notes", []):
                link = (' <a href="%s" rel="noopener" target="_blank">出处</a>' % esc(n["source"])) if n.get("source") else ""
                notes.append("<li><b>%s</b>　%s%s%s</li>" % (esc(p), esc(n["text"]), " †" if n.get("call") else "", link))
        srcs = " ".join('<a href="%s" rel="noopener" target="_blank">%s</a>' % (esc(recs[p]["source_url"]), esc(p))
                        for p in pers if recs[p].get("source_url"))
        out.append("""
<details class="card led"><summary><b>{tk}</b> {name}<span class="muted">　{role}</span></summary>
<div class="tw"><table><thead><tr><th>指标</th>{head}</tr></thead><tbody>{body}</tbody></table></div>
{notes}<div class="muted small">新闻稿原文：{srcs}</div></details>""".format(
            tk=esc(tk), name=esc(names.get(tk, {}).get("name", "")), role=esc(names.get(tk, {}).get("role", "")),
            head=head, body="".join(body), notes=("<ul class='notes'>%s</ul>" % "".join(notes)) if notes else "", srcs=srcs))
    return '<section><h2>季度指标台账</h2><p class="muted">† 电话会口径（第三方转录）；* 云端分析员或人工核对录入；其余为新闻稿自动解析。悬停数字看原文。</p>%s</section>' % "".join(out)


def sec_method(report, state):
    fr = report.get("filings", {})
    mk = report.get("market", {})
    return """
<section><h2>方法与数据源</h2><div class="card method">
<ul>
<li><b>自动取数（GitHub Actions，云端）</b>：北京时间 05:30、07:30（周二至周六，接美股盘后）与 20:30（周一至周五，接盘前）查 SEC EDGAR（8-K Item 2.02；CHKP 为 6-K），发现新的季度业绩新闻稿就解析、入库、对照阈值、推送飞书；每次顺带更新 Nasdaq 日线、市值和下一次财报日。周一 08:30 另发一条周报。</li>
<li><b>云端分析员（Claude routine）</b>：北京时间周二至周六 09:15 查待办队列；有新财报就读新闻稿全文、用网页检索补电话会口径（Prisma AIRS ARR、AI DR ARR、Okta 计价、Cloudflare 按请求收费），写入台账和一条反馈；每季 15 日后写季度复盘。没有待办就直接退出。</li>
<li><b>口径</b>：同比优先用报表精确值现算（AKAM 安全收入按千美元），公司四舍五入口径并列；电话会数字一律标 †；阈值全部来自底稿第七节，改动只需编辑仓库里的 config/signals.json。</li>
<li><b>最近运行</b>：SEC 检查 {fr_at}；行情 {mk_at}；信号评估 {ev_at}。</li>
</ul>
<p class="muted small">研究跟踪用途，不构成投资建议。行情为公开延迟数据；电话会数字来自第三方转录，未经发行人核验。</p>
</div></section>""".format(fr_at=esc(fr.get("run_at", "—")), mk_at=esc((mk.get("run_at_utc") or "—").replace("T", " ").replace("Z", " UTC")),
                           ev_at=esc(state.get("updated_bjt", "—")))


CSS = """
:root{color-scheme:light;--page:#f9f9f7;--surface:#fcfcfb;--ink:#0b0b0b;--ink2:#52514e;--muted:#6f6d68;--grid:#e1e0d9;--axis:#c3c2b7;--border:rgba(11,11,11,.10);
--s1:#2a78d6;--s1h:#9ec5f4;--s2:#eb6834;--s3:#1baf7a;--s4:#eda100;--ref:#898781;--pos:#006300;--neg:#b42323;--bpos:#2a78d6;--bneg:#e34948;
--good:#0ca30c;--warn:#fab219;--crit:#d03b3b;--chipbg:rgba(11,11,11,.05)}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){color-scheme:dark;--page:#0d0d0d;--surface:#1a1a19;--ink:#fff;--ink2:#c3c2b7;--muted:#9a988f;--grid:#2c2c2a;--axis:#383835;--border:rgba(255,255,255,.10);
--s1:#3987e5;--s1h:#184f95;--s2:#d95926;--s3:#199e70;--s4:#c98500;--ref:#898781;--pos:#0ca30c;--neg:#e66767;--bpos:#3987e5;--bneg:#e66767;--chipbg:rgba(255,255,255,.07)}}
:root[data-theme="dark"]{color-scheme:dark;--page:#0d0d0d;--surface:#1a1a19;--ink:#fff;--ink2:#c3c2b7;--muted:#9a988f;--grid:#2c2c2a;--axis:#383835;--border:rgba(255,255,255,.10);
--s1:#3987e5;--s1h:#184f95;--s2:#d95926;--s3:#199e70;--s4:#c98500;--ref:#898781;--pos:#0ca30c;--neg:#e66767;--bpos:#3987e5;--bneg:#e66767;--chipbg:rgba(255,255,255,.07)}
*{box-sizing:border-box}html{-webkit-text-size-adjust:100%}
body{margin:0;background:var(--page);color:var(--ink);font:15px/1.6 system-ui,-apple-system,"PingFang SC","Hiragino Sans GB","Microsoft YaHei","Segoe UI",sans-serif}
main{max-width:1120px;margin:0 auto;padding:20px 16px 64px}
a{color:var(--s1)}h1{font-size:26px;line-height:1.25;margin:4px 0 6px;font-weight:650}h2{font-size:18px;margin:34px 0 12px;font-weight:650}
h3{font-size:15px;margin:2px 0 6px;font-weight:600}h4{font-size:14px;margin:10px 0 4px}
.kicker{font-size:12px;letter-spacing:.06em;color:var(--muted)}.sub{color:var(--ink2);margin:0 0 8px;max-width:760px}
.meta{display:flex;flex-wrap:wrap;gap:6px 16px;font-size:13px;color:var(--muted)}.counts{margin-top:12px;display:flex;flex-wrap:wrap;gap:8px 14px;font-size:13px;color:var(--ink2)}
.card{background:var(--surface);border:1px solid var(--border);border-radius:10px;padding:14px 16px}
.muted{color:var(--muted)}.small{font-size:12px}
.chip{display:inline-flex;align-items:center;gap:4px;font-size:12px;line-height:1;padding:4px 8px;border-radius:999px;background:var(--chipbg);color:var(--ink);white-space:nowrap}
.chip .ic{display:inline-grid;place-items:center;width:15px;height:15px;border-radius:50%;font-size:11px;font-weight:700;color:#fff;background:var(--ref)}
.st-up .ic{background:var(--good)}.st-down .ic{background:var(--crit)}.st-watch .ic{background:var(--warn);color:#0b0b0b}.st-pending .ic{background:var(--axis);color:var(--ink)}
.thesis{margin-top:18px}.thesis p{margin:6px 0 0;color:var(--ink2)}.thesis-rank{display:flex;flex-wrap:wrap;align-items:baseline;gap:6px 14px}
.lbl{font-size:12px;color:var(--muted);margin-right:8px}.rank{font-size:20px;font-weight:650;letter-spacing:.02em}
.alert{color:var(--ink)}
.next h2{margin-top:22px}.nextlist{list-style:none;margin:0;padding:0;display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:10px}
.nextlist li{background:var(--surface);border:1px solid var(--border);border-radius:10px;padding:10px 12px;display:grid;grid-template-columns:auto 1fr;gap:0 10px}
.nextlist .d{grid-row:span 2;font-size:22px;font-weight:650;align-self:center}.nextlist .t{font-weight:650}.nextlist .p{font-size:12px;color:var(--muted)}.nextlist .s{grid-column:1/-1;font-size:12px;color:var(--ink2)}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(330px,1fr));gap:12px}.grid2{display:grid;grid-template-columns:repeat(auto-fill,minmax(440px,1fr));gap:12px;margin-top:12px}
.sig{display:flex;flex-direction:column;gap:4px;border-top:3px solid var(--axis)}
.st-b-up{border-top-color:var(--good)}.st-b-down{border-top-color:var(--crit)}.st-b-watch{border-top-color:var(--warn)}
.sig-h{display:flex;align-items:center;gap:8px}.sid{font-weight:700;font-size:13px}.layer{font-size:12px;color:var(--muted);flex:1}
.reading{font-size:20px;font-weight:650;line-height:1.3}.aux{font-size:13px;color:var(--ink2)}.reason{font-size:13px;color:var(--ink2)}
.rule,.base{font-size:12.5px;color:var(--ink2)}.rule .lbl,.base .lbl{display:inline-block;min-width:4.5em}.nx{font-size:12.5px;margin-top:2px}
.fig{margin:0}.fig figcaption p{margin:0 0 6px;font-size:12.5px}
svg.chart{display:block;width:100%;height:auto;overflow:visible}svg.mini{max-width:320px;margin:4px 0 2px}
line.gl{stroke:var(--grid);stroke-width:1}.axis{stroke:var(--axis);stroke-width:1}
.thr{stroke:var(--ink2);stroke-width:1;stroke-dasharray:4 3;opacity:.8}.thr-l{fill:var(--ink2);font-size:11px}
.tick,.xl{fill:var(--muted);font-size:11px;font-variant-numeric:tabular-nums}.val{fill:var(--ink);font-size:11.5px;font-weight:600}
.yl{fill:var(--ink2);font-size:12px}.yl.core{fill:var(--ink);font-weight:700}
.bar-hist{fill:var(--s1h)}.bar-last{fill:var(--s1)}.bar-pos{fill:var(--bpos)}.bar-neg{fill:var(--bneg)}
.dot2{fill:var(--s2);stroke:var(--surface);stroke-width:2}.target{fill:none;stroke:var(--axis);stroke-width:1.5;stroke-dasharray:4 3}
.hit{fill:transparent;cursor:default;outline:none}.hit:hover,.hit:focus{fill:var(--ink);fill-opacity:.05}
.ln{fill:none;stroke-width:2;stroke-linejoin:round;stroke-linecap:round}.end{stroke:var(--surface);stroke-width:2}
.c1{stroke:var(--s1)}circle.c1{fill:var(--s1)}.c2{stroke:var(--s2)}circle.c2{fill:var(--s2)}.c3{stroke:var(--s3)}circle.c3{fill:var(--s3)}.c4{stroke:var(--s4)}circle.c4{fill:var(--s4)}
.cref{stroke:var(--ref);stroke-width:1.5}circle.cref{fill:var(--ref)}.endl{fill:var(--ink2);font-size:11px}
.xhair{stroke:var(--axis);stroke-width:1}.xhit{fill:transparent}
.legend{display:flex;flex-wrap:wrap;align-items:center;gap:4px 12px;font-size:12px;color:var(--ink2);margin:2px 0 6px}
.k{display:inline-block;margin-right:4px;vertical-align:middle}.k-bar{width:10px;height:10px;border-radius:2px;background:var(--s1)}.k-dot{width:9px;height:9px;border-radius:50%;background:var(--s2)}
.k-c1,.k-c2,.k-c3,.k-c4,.k-ref{width:14px;height:2px;border-radius:1px}.k-c1{background:var(--s1)}.k-c2{background:var(--s2)}.k-c3{background:var(--s3)}.k-c4{background:var(--s4)}.k-ref{background:var(--ref)}
.meter{margin:6px 0 2px;max-width:320px}.meter-track{height:8px;border-radius:4px;background:var(--s1h)}.meter-fill{height:8px;border-radius:4px;background:var(--s1)}
.meter-l{display:flex;justify-content:space-between;font-size:12px;color:var(--ink2);margin-top:4px}
.tw{overflow-x:auto;-webkit-overflow-scrolling:touch}table{border-collapse:collapse;width:100%;font-size:13px}
th,td{padding:6px 8px;border-bottom:1px solid var(--grid);text-align:left;white-space:nowrap}th{font-weight:600;color:var(--ink2)}
td.num,th.num{text-align:right;font-variant-numeric:tabular-nums}td.pos{color:var(--pos)}td.neg{color:var(--neg)}
tr.core td{font-weight:600}tr.bench td{color:var(--ink2)}
details.tv{margin-top:6px;font-size:12.5px}details.tv summary{cursor:pointer;color:var(--muted)}
.later{margin:10px 0 0;padding-left:18px;font-size:13px;color:var(--ink2)}
.fblist{display:flex;flex-direction:column;gap:10px}.fb-h{display:flex;flex-wrap:wrap;align-items:center;gap:6px 10px;font-size:12.5px}
.fb .date{font-weight:650}.fb .type{color:var(--muted)}.fb .body{font-size:14px;color:var(--ink)}.fb .body p{margin:4px 0}.fb .body ul{margin:4px 0;padding-left:20px}
.src{font-size:12px;color:var(--muted);word-break:break-all}
details.led{margin-bottom:8px}details.led summary{cursor:pointer}details.led table{margin-top:8px}details.led th[scope=row]{font-weight:500;color:var(--ink)}
sup{color:var(--muted);font-size:10px;margin-left:1px}.notes{font-size:12.5px;color:var(--ink2);padding-left:18px}
.method ul{margin:0;padding-left:18px}.method li{margin:4px 0}
#tip,.xtip{position:fixed;z-index:9;pointer-events:none;background:var(--surface);color:var(--ink);border:1px solid var(--border);border-radius:8px;padding:6px 9px;font-size:12.5px;box-shadow:0 4px 14px rgba(0,0,0,.12);max-width:260px}
.xtip .row{display:flex;align-items:center;gap:6px}.xtip .row b{min-width:48px;text-align:right;font-variant-numeric:tabular-nums}.xtip .key{width:12px;height:2px}
@media (max-width:560px){h1{font-size:22px}.grid,.grid2{grid-template-columns:1fr}.reading{font-size:18px}th,td{padding:5px 6px}}
"""

JS = """
(function(){
var tip=document.getElementById('tip');
function show(t,x,y){tip.textContent=t;tip.hidden=false;var w=tip.offsetWidth,h=tip.offsetHeight;
 tip.style.left=Math.min(window.innerWidth-w-8,x+12)+'px';tip.style.top=Math.max(8,y-h-10)+'px';}
function hide(){tip.hidden=true;}
document.querySelectorAll('[data-tip]').forEach(function(el){
 el.addEventListener('pointermove',function(e){show(el.getAttribute('data-tip'),e.clientX,e.clientY);});
 el.addEventListener('pointerleave',hide);
 el.addEventListener('focus',function(){var r=el.getBoundingClientRect();show(el.getAttribute('data-tip'),r.left+r.width/2,r.top);});
 el.addEventListener('blur',hide);});
var svg=document.getElementById('px'),data=document.getElementById('px-data'),xt=document.getElementById('px-tip');
if(svg&&data&&xt){var d=JSON.parse(data.textContent),hit=svg.querySelector('.xhit'),hair=svg.querySelector('.xhair');
 var g=hit.getAttribute('data-geo').split(','),ml=+g[0],pw=+g[1],n=d.dates.length;
 hit.addEventListener('pointermove',function(e){var pt=svg.createSVGPoint();pt.x=e.clientX;pt.y=e.clientY;
  var p=pt.matrixTransform(svg.getScreenCTM().inverse());var i=Math.round((p.x-ml)/pw*(n-1));i=Math.max(0,Math.min(n-1,i));
  var x=ml+i/(n-1)*pw;hair.setAttribute('x1',x);hair.setAttribute('x2',x);hair.setAttribute('visibility','visible');
  xt.textContent='';var h=document.createElement('div');h.className='muted';h.textContent=d.dates[i];xt.appendChild(h);
  d.series.forEach(function(s){var v=s.v[i];if(v==null)return;var r=document.createElement('div');r.className='row';
   var k=document.createElement('span');k.className='key';k.style.background='var(--'+(s.cls=='cref'?'ref':'s'+s.cls.slice(1))+')';
   var b=document.createElement('b');b.textContent=v.toFixed(1);var t=document.createElement('span');t.textContent=s.key;
   r.appendChild(k);r.appendChild(b);r.appendChild(t);xt.appendChild(r);});
  xt.hidden=false;var w=xt.offsetWidth;xt.style.left=Math.min(window.innerWidth-w-8,e.clientX+14)+'px';xt.style.top=(e.clientY-20)+'px';});
 hit.addEventListener('pointerleave',function(){xt.hidden=true;hair.setAttribute('visibility','hidden');});}
})();
"""


def main():
    cfg = load_json(os.path.join(CONFIG, "signals.json"))
    metrics = load_json(os.path.join(CONFIG, "metrics.json"))
    q = store.load_quarterly()
    state = load_json(os.path.join(DATA, "signals_state.json"), {}) or {"signals": {}}
    market = {k: v for k, v in (load_json(os.path.join(DATA, "market.json"), {}) or {}).items() if not k.startswith("_")}
    cal = load_json(os.path.join(DATA, "calendar.json"), {}) or {}
    seed_all = load_json(os.path.join(CONFIG, "calendar_seed.json"), {}) or {}
    seed = seed_all.get("dates", {})
    fb = store.load_feedback()
    report = load_json(os.path.join(DATA, "fetch_report.json"), {}) or {}
    now = now_bjt()
    rows = calendar_rows(cfg, cal, seed, now.date())
    body = "".join([
        sec_header(state, market, cfg, now.strftime("%Y-%m-%d %H:%M")),
        sec_thesis(state, cfg),
        sec_next(rows),
        sec_signals(cfg, state, q),
        sec_series(q),
        sec_market(market, cfg),
        sec_calendar(rows, seed_all.get("later", [])),
        sec_feedback(fb),
        sec_ledger(q, metrics),
        sec_method(report, state),
    ])
    page = """<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Agent 安全跟踪看板</title>
<meta name="description" content="跟踪 AKAM、PANW、FTNT、CHKP 等 9 家安全公司的 8 项 agent 时代改判信号：财报自动取数、阈值对照、云端分析员反馈。">
<link rel="icon" href="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 16 16'%3E%3Crect x='2' y='8' width='3' height='6' rx='1' fill='%232a78d6'/%3E%3Crect x='6.5' y='5' width='3' height='9' rx='1' fill='%232a78d6'/%3E%3Crect x='11' y='2' width='3' height='12' rx='1' fill='%232a78d6'/%3E%3C/svg%3E">
<style>{css}</style></head>
<body><main>{body}</main><div id="tip" hidden></div><script>{js}</script></body></html>
""".format(css=CSS, body=body, js=JS)
    with open(os.path.join(ROOT, "index.html"), "w", encoding="utf-8") as fh:
        fh.write(page)
    print("index.html %d KB" % (len(page.encode("utf-8")) // 1024))
    return 0


if __name__ == "__main__":
    sys.exit(main())
