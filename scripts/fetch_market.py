#!/usr/bin/env python3
"""行情与财报日：9 家公司 + 两个基准的日线、市值、市销率，以及下一次财报日。

源：Nasdaq 公开接口为主（本机与云端都通），Yahoo chart 接口兜底（本机常 429）。
产出：
  data/prices/<T>.csv       日线收盘（date,close,volume），按日期合并，只增不删
  data/market.json          最新快照：收盘、1日/1周/1月/3月/年内/1年涨跌、52 周高低、市值、市销率
  data/calendar.json        下一次财报日（Nasdaq/Zacks，标注"预估/已确认"）
市销率口径与底稿一致：市值 ÷（最新一季收入 × 4），收入取 data/quarterly.json。
"""
import csv
import datetime
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import DATA, http_get, load_json, now_bjt, save_json  # noqa: E402
from sec_edgar import companies, label_sort_key  # noqa: E402

BENCH = [{"ticker": "CIBR", "name": "网络安全 ETF（CIBR）", "asset": "etf"},
         {"ticker": "QQQ", "name": "纳指 100 ETF（QQQ）", "asset": "etf"}]
NASDAQ_H = {"Origin": "https://www.nasdaq.com", "Referer": "https://www.nasdaq.com/",
            "Accept": "application/json, text/plain, */*"}


def _num(s):
    if s in (None, "", "N/A"):
        return None
    try:
        return float(str(s).replace("$", "").replace(",", "").strip())
    except ValueError:
        return None


def nasdaq_history(tk, asset, days=800):
    to = datetime.date.today()
    fr = to - datetime.timedelta(days=days)
    url = ("https://api.nasdaq.com/api/quote/%s/historical?assetclass=%s&fromdate=%s&todate=%s&limit=9999"
           % (tk, "etf" if asset == "etf" else "stocks", fr.isoformat(), to.isoformat()))
    d = json.loads(http_get(url, tries=4, timeout=40, headers=NASDAQ_H))
    rows = ((d.get("data") or {}).get("tradesTable") or {}).get("rows") or []
    out = []
    for r in rows:
        dt = datetime.datetime.strptime(r["date"], "%m/%d/%Y").date().isoformat()
        c = _num(r.get("close"))
        if c:
            out.append((dt, c, _num(r.get("volume"))))
    if not out:
        raise RuntimeError("nasdaq history empty for %s" % tk)
    return out


def yahoo_history(tk, days=800):
    rng = "2y" if days > 400 else "1y"
    last = None
    for host in ("query1", "query2"):
        try:
            d = json.loads(http_get("https://%s.finance.yahoo.com/v8/finance/chart/%s?range=%s&interval=1d"
                                    % (host, tk, rng), tries=2, timeout=30))
            res = d["chart"]["result"][0]
            ts, q = res["timestamp"], res["indicators"]["quote"][0]
            out = []
            for t, c, v in zip(ts, q["close"], q["volume"]):
                if c:
                    out.append((datetime.datetime.utcfromtimestamp(t).date().isoformat(), float(c), v))
            return out
        except Exception as e:  # noqa: BLE001
            last = e
    raise RuntimeError("yahoo failed for %s: %s" % (tk, last))


def nasdaq_mcap(tk):
    d = json.loads(http_get("https://api.nasdaq.com/api/quote/%s/summary?assetclass=stocks" % tk,
                            tries=3, timeout=30, headers=NASDAQ_H))
    sd = (d.get("data") or {}).get("summaryData") or {}
    return _num((sd.get("MarketCap") or {}).get("value"))


def nasdaq_earnings_date(tk):
    d = json.loads(http_get("https://api.nasdaq.com/api/analyst/%s/earnings-date" % tk,
                            tries=3, timeout=30, headers=NASDAQ_H))
    data = d.get("data") or {}
    text = (data.get("reportText") or "") + " " + (data.get("announcement") or "")
    m = re.search(r"(\d{2})/(\d{2})/(\d{4})", text)
    if not m:
        m2 = re.search(r"([A-Z][a-z]{2}) (\d{1,2}), (\d{4})", data.get("announcement") or "")
        if not m2:
            return None
        dt = datetime.datetime.strptime(" ".join(m2.groups()), "%b %d %Y").date()
    else:
        dt = datetime.date(int(m.group(3)), int(m.group(1)), int(m.group(2)))
    estimated = ("estimated" in text.lower()) or ("algorithm" in text.lower())
    timing = ""
    low = text.lower()
    if "after market close" in low or "after the close" in low:
        timing = "盘后"
    elif "before market open" in low or "prior to market open" in low:
        timing = "盘前"
    return {"date": dt.isoformat(), "status": "预估" if estimated else "已确认", "timing": timing,
            "source": "Nasdaq/Zacks", "raw": (data.get("announcement") or "")[:160]}


def merge_prices(tk, rows):
    path = os.path.join(DATA, "prices", "%s.csv" % tk)
    old = {}
    if os.path.exists(path):
        with open(path, newline="", encoding="utf-8") as fh:
            for r in csv.DictReader(fh):
                old[r["date"]] = r
    added = 0
    for dt, c, v in rows:
        if dt not in old:
            added += 1
        old[dt] = {"date": dt, "close": "%.4f" % c, "volume": "" if v is None else "%d" % v}
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=["date", "close", "volume"])
        w.writeheader()
        for k in sorted(old):
            w.writerow(old[k])
    series = [(k, float(old[k]["close"])) for k in sorted(old)]
    return series, added


def ret(series, back):
    """series 为 (date, close) 升序；back 个交易日前的涨跌 %。"""
    if len(series) <= back:
        return None
    return (series[-1][1] / series[-1 - back][1] - 1.0) * 100.0


def ret_since(series, date_iso):
    """相对 date_iso 当天或之前最后一个收盘的涨跌 %。"""
    base = None
    for d, c in series:
        if d <= date_iso:
            base = c
        else:
            break
    if base is None:
        return None
    return (series[-1][1] / base - 1.0) * 100.0


def latest_revenue(quarterly, tk):
    q = (quarterly or {}).get(tk) or {}
    best = None
    for lab, rec in q.items():
        rev = ((rec.get("metrics") or {}).get("revenue") or {}).get("value")
        if rev is None:
            continue
        if best is None or label_sort_key(lab) > label_sort_key(best[0]):
            best = (lab, rev)
    return best


def main():
    quarterly = load_json(os.path.join(DATA, "quarterly.json"), {})
    market = load_json(os.path.join(DATA, "market.json"), {}) or {}
    cal = load_json(os.path.join(DATA, "calendar.json"), {}) or {}
    report = {"run_at_utc": datetime.datetime.utcnow().isoformat(timespec="seconds") + "Z", "jobs": []}
    rows_out = {}
    universe = [{"ticker": c["ticker"], "name": c["name"], "asset": "stock", "group": c["group"]} for c in companies()]
    universe += [dict(b, group="bench") for b in BENCH]
    for u in universe:
        tk = u["ticker"]
        job = {"label": "行情 %s" % tk, "ok": False}
        src = None
        try:
            try:
                rows = nasdaq_history(tk, u["asset"])
                src = "Nasdaq"
            except Exception as e1:  # noqa: BLE001
                job["nasdaq_error"] = str(e1)[:160]
                rows = yahoo_history(tk)
                src = "Yahoo"
            series, added = merge_prices(tk, rows)
            last_d, last_c = series[-1]
            prev_year_end = "%d-12-31" % (int(last_d[:4]) - 1)
            one_year_ago = (datetime.date.fromisoformat(last_d) - datetime.timedelta(days=365)).isoformat()
            win = [c for d, c in series if d > one_year_ago]
            rec = {
                "ticker": tk, "name": u["name"], "group": u["group"], "date": last_d, "close": last_c,
                "chg_1d": ret(series, 1), "chg_1w": ret(series, 5), "chg_1m": ret(series, 21),
                "chg_3m": ret(series, 63), "chg_ytd": ret_since(series, prev_year_end),
                "chg_1y": ret_since(series, one_year_ago),
                "hi_52w": max(win) if win else None, "lo_52w": min(win) if win else None,
                "source": src,
            }
            if rec["hi_52w"]:
                rec["from_hi"] = (last_c / rec["hi_52w"] - 1.0) * 100.0
            if u["asset"] == "stock":
                mcap = None
                try:
                    mcap = nasdaq_mcap(tk)
                except Exception as e:  # noqa: BLE001
                    job["mcap_error"] = str(e)[:160]
                if mcap is None and tk in market and market[tk].get("mcap") and market[tk].get("close"):
                    # 接口没给市值时，用上次市值按股价变动外推（股本季内变化小）
                    mcap = market[tk]["mcap"] * last_c / market[tk]["close"]
                    rec["mcap_note"] = "按上次市值 × 股价变动外推"
                rec["mcap"] = mcap
                lr = latest_revenue(quarterly, tk)
                if mcap and lr:
                    rec["ps"] = mcap / (lr[1] * 1e6 * 4)
                    rec["ps_basis"] = "市值 ÷（%s 收入 $%.0f 百万 × 4）" % (lr[0], lr[1])
                try:
                    ed = nasdaq_earnings_date(tk)
                    if ed:
                        old = cal.get(tk) or {}
                        # 已确认的日期不被"预估"覆盖（除非那一天已经过去）
                        if not (old.get("status") == "已确认" and ed["status"] == "预估"
                                and old.get("date", "") >= last_d):
                            cal[tk] = dict(ed, fetched=now_bjt().strftime("%Y-%m-%d"))
                except Exception as e:  # noqa: BLE001
                    job["earn_error"] = str(e)[:160]
            rows_out[tk] = rec
            job.update(ok=True, rows=len(series), added=added, last_date=last_d, source=src)
        except Exception as e:  # noqa: BLE001
            job["error"] = str(e)[:200]
            if tk in market:
                rows_out[tk] = dict(market[tk], stale=True)
        report["jobs"].append(job)
        print(("✅" if job["ok"] else "❌"), job["label"], job.get("last_date", ""), job.get("source", ""),
              job.get("error", ""))
    rows_out["_meta"] = {"updated_bjt": now_bjt().strftime("%Y-%m-%d %H:%M"),
                         "note": "Nasdaq 公开日线为主，Yahoo 兜底；市销率 = 市值 ÷（最新季收入 × 4）"}
    save_json(os.path.join(DATA, "market.json"), rows_out)
    save_json(os.path.join(DATA, "calendar.json"), cal)
    rep_path = os.path.join(DATA, "fetch_report.json")
    rep = load_json(rep_path, {}) or {}
    rep["market"] = report
    save_json(rep_path, rep)
    bad = [j["label"] for j in report["jobs"] if not j["ok"]]
    return 1 if len(bad) > len(universe) // 2 else 0


if __name__ == "__main__":
    sys.exit(main())
