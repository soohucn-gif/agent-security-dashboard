#!/usr/bin/env python3
"""把待推送事件压成一条飞书私聊发给 Henry（自建应用 + open_id，与 macro5、AI Signal 同一套）。

凭据从环境变量读（GitHub Actions Secrets）：FEISHU_APP_ID / FEISHU_APP_SECRET / FEISHU_OPEN_ID。

推什么（一次运行最多一条消息，事件合并）：
  - earnings        新财报入库：关键读数 + 相关信号当前状态
  - signal_change   信号状态变化（维持 → 接近 / 触发）
  - feedback        云端分析员写了新反馈（标题 + 结论 + 摘要）
  - weekly          周一 08:30 周报：8 项信号一行一个、未来 3 周财报、核心四家一周涨跌
  - --test          手动测试：发一条"看板已上线"
没配 secrets 时只打印、不标已发，等配好后下一次运行补发。
"""
import datetime
import json
import os
import sys
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import CONFIG, DATA, SITE_URL, load_json, now_bjt, save_json  # noqa: E402
from evaluate import fmt_pct  # noqa: E402
import store  # noqa: E402

STATE_PATH = os.path.join(DATA, "notify_state.json")
ICON = {"up": "↑", "down": "↓", "watch": "!", "hold": "·", "pending": "…"}
MAX_CHARS = 3000


def load_state():
    return load_json(STATE_PATH, {}) or {}


def sig_line(sid, st, cfg_by_id):
    s = st["signals"].get(sid) or {}
    short = cfg_by_id[sid]["short"]
    return "%s %s %s｜%s｜%s" % (ICON.get(s.get("status"), "·"), sid, short, s.get("label", "—"), s.get("reading", "—"))


def compose_events(evs, st, cfg_by_id):
    lines = []
    for e in evs:
        t = e.get("type")
        if t == "earnings":
            lines.append("📥 %s" % e["title"])
            for x in e.get("lines", []):
                lines.append("   " + x)
            for sid in e.get("signals", []):
                lines.append("   " + sig_line(sid, st, cfg_by_id))
        elif t == "signal_change":
            lines.append("🔔 %s" % e["title"])
            for x in e.get("lines", []):
                lines.append("   " + x)
        elif t == "feedback":
            lines.append("🧠 分析员反馈｜%s（%s）" % (e["title"], e.get("verdict", "—")))
            summ = (e.get("summary") or "").replace("\n", " ").replace("**", "")
            if summ:
                lines.append("   " + summ[:260] + ("…" if len(summ) > 260 else ""))
    return lines


def compose_weekly(st, cfg, market, cal_rows):
    cfg_by_id = {s["id"]: s for s in cfg["signals"]}
    lines = ["📊 周报｜8 项信号"]
    for s in cfg["signals"]:
        lines.append("  " + sig_line(s["id"], st, cfg_by_id))
    soon = [r for r in cal_rows if r["days"] <= 21]
    if soon:
        lines.append("🗓 未来 3 周财报：" + "；".join("%s %s（%s）" % (r["date"][5:], r["ticker"], r["status"]) for r in soon))
    mv = []
    for tk in ("AKAM", "PANW", "FTNT", "CHKP", "NET", "CRWD", "OKTA", "ZS"):
        m = market.get(tk) or {}
        if m.get("chg_1w") is not None:
            mv.append("%s %s" % (tk, fmt_pct(m["chg_1w"])))
    if mv:
        lines.append("📈 一周：" + " · ".join(mv))
    ak = market.get("AKAM") or {}
    if ak.get("ps"):
        lines.append("   AKAM 市销率 %.1f×，年内 %s" % (ak["ps"], fmt_pct(ak.get("chg_ytd"))))
    return lines


def send(text):
    app_id = os.environ.get("FEISHU_APP_ID", "")
    app_secret = os.environ.get("FEISHU_APP_SECRET", "")
    open_id = os.environ.get("FEISHU_OPEN_ID", "")
    if not (app_id and app_secret and open_id):
        raise LookupError("FEISHU_APP_ID / FEISHU_APP_SECRET / FEISHU_OPEN_ID 未配置")

    def post(url, body, headers):
        req = urllib.request.Request(url, data=json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json", **headers})
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.load(r)

    tok = post("https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
               {"app_id": app_id, "app_secret": app_secret}, {})
    if tok.get("code") != 0:
        raise RuntimeError("tenant_access_token: %s" % tok.get("msg"))
    r = post("https://open.feishu.cn/open-apis/im/v1/messages?receive_id_type=open_id",
             {"receive_id": open_id, "msg_type": "text", "content": json.dumps({"text": text})},
             {"Authorization": "Bearer " + tok["tenant_access_token"]})
    if r.get("code") != 0:
        raise RuntimeError("send message: %s %s" % (r.get("code"), r.get("msg")))


def main():
    args = sys.argv[1:]
    cfg = load_json(os.path.join(CONFIG, "signals.json"))
    cfg_by_id = {s["id"]: s for s in cfg["signals"]}
    st = load_json(os.path.join(DATA, "signals_state.json"), {}) or {"signals": {}}
    market = {k: v for k, v in (load_json(os.path.join(DATA, "market.json"), {}) or {}).items() if not k.startswith("_")}
    state = load_state()
    now = now_bjt()
    evs_all = store.load_events()
    pending = [e for e in evs_all if not e.get("sent")]
    body = compose_events(pending, st, cfg_by_id)

    weekly_due = False
    wk = now.strftime("%G-W%V")
    if "--weekly" in args or (now.weekday() == 0 and now.hour >= 8 and state.get("last_weekly") != wk):
        weekly_due = True
        from build_site import calendar_rows
        seed = (load_json(os.path.join(CONFIG, "calendar_seed.json"), {}) or {}).get("dates", {})
        cal = load_json(os.path.join(DATA, "calendar.json"), {}) or {}
        body += compose_weekly(st, cfg, market, calendar_rows(cfg, cal, seed, now.date()))

    if "--test" in args:
        body = ["✅ Agent 安全跟踪看板已上线（测试推送）",
                "以后这里会收到：新财报入库与读数、信号状态变化、云端分析员反馈、周一周报。"] + \
               ["  " + sig_line(s["id"], st, cfg_by_id) for s in cfg["signals"]]

    if not body:
        print("feishu: 没有待推送内容")
        return 0
    text = "Agent 安全看板 %s\n%s\n%s" % (now.strftime("%m-%d %H:%M"), "\n".join(body), SITE_URL)
    if len(text) > MAX_CHARS:
        text = text[:MAX_CHARS - len(SITE_URL) - 20] + "\n…（更多见看板）\n" + SITE_URL
    print(text)
    if "--dry-run" in args:
        return 0
    try:
        send(text)
    except LookupError as e:
        print("::warning::%s——消息保留在队列，配好 secrets 后下次运行补发" % e)
        return 0
    except (urllib.error.URLError, RuntimeError) as e:
        print("::error::飞书推送失败：%s" % e)
        return 1
    ids = {e["id"] for e in pending}
    for e in evs_all:
        if e["id"] in ids:
            e["sent"] = True
            e["sent_at"] = now.strftime("%Y-%m-%d %H:%M")
    save_json(store.EVENTS_PATH, evs_all)
    if weekly_due:
        state["last_weekly"] = wk
    state["last_sent"] = now.strftime("%Y-%m-%d %H:%M")
    save_json(STATE_PATH, state)
    print("feishu: sent")
    return 0


if __name__ == "__main__":
    sys.exit(main())
