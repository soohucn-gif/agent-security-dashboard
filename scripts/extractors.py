#!/usr/bin/env python3
"""从季度业绩新闻稿（EX-99.1 / CHKP 6-K）文本里取指标。

原则：
- 只取新闻稿里有明确位置的数字（报表行、固定句式），取不到就留空，不猜。
- 同比优先用报表精确值现算（如 AKAM 安全收入按千美元算），公司四舍五入口径另存 *_rep。
- 电话会才有的数字（Prisma AIRS ARR、AI DR ARR、Okta 计价、Cloudflare 按请求收费成交额）
  不在这里取，交给云端分析员 routine，并标注"电话会口径"。
- keyword_hits() 扫信号相关关键词，给 routine 和飞书提醒用：命中≠触发，只是"要人看"。

每个指标返回 {"value": float, "unit": "USD_M"|"pct"|"count", "quote": "原文片段"}。
"""
import re
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import parse_num  # noqa: E402

NUM = r"\(?\$?\s?-?[\d,]+(?:\.\d+)?\)?%?"


def _lines(text):
    return text.split("\n")


def row_numbers(text, label, n=4, start=0, max_lines=12):
    """找到以 label（正则）开头的行，返回其后最多 n 个数。

    兼容两种版式：一行 "Security | $604,436 | $589,790 | ..."，
    或 CHKP 6-K 那种标签独占一行、数字逐行往下排。
    """
    lines = _lines(text)
    pat = re.compile(r"^\s*(?:" + label + r")\s*(?:\(\d\))?\s*(\||$)", re.I)
    for i in range(start, len(lines)):
        if not pat.search(lines[i]):
            continue
        cells = [c.strip() for c in lines[i].split("|")[1:]]
        vals = []
        for c in cells:
            v = parse_num(c)
            if v is None and c:
                break
            if v is not None:
                vals.append(v)
        j = i + 1
        while len(vals) < n and j < len(lines) and j <= i + max_lines:
            if lines[j].strip() in ("$", "(", "$(", "US$"):
                j += 1
                continue
            cs = [c.strip() for c in lines[j].split("|")]
            got = [parse_num(c) for c in cs]
            if not cs or any(g is None for g in got):
                break
            vals.extend(got)
            j += 1
        if vals:
            return vals[:n], i
    return None, None


def find_sentence(text, pattern, flags=re.I):
    m = re.search(pattern, text, flags)
    if not m:
        return None, None
    s = max(0, text.rfind("\n", 0, m.start()) + 1)
    e = text.find("\n", m.end())
    quote = text[s: e if e > 0 else None].strip()
    return m, quote[:300]


def money_to_musd(num, unit):
    v = parse_num(num)
    if v is None:
        return None
    unit = (unit or "").lower()
    if unit.startswith("b"):
        return v * 1000.0
    if unit.startswith("m"):
        return v
    if unit.startswith("t"):   # thousand
        return v / 1000.0
    return v


def _m(v, unit="USD_M", quote=""):
    if v is None:
        return None
    return {"value": round(v, 4), "unit": unit, "quote": quote}


def _yoy(cur, prev):
    if cur is None or prev in (None, 0):
        return None
    return (cur / prev - 1.0) * 100.0


def _put(out, key, v, unit="USD_M", quote="", qualifier=None):
    item = _m(v, unit, quote)
    if item is not None:
        if qualifier:
            item["qualifier"] = qualifier
        out[key] = item


def _qual(word):
    """'exceeded' / 'more than' / 'over' → '>'，用于"超过 $X"这类下限口径。"""
    return ">" if word and re.search(r"exceed|surpass|more than|over|above|top", word, re.I) else None


def _first(text, pats):
    for p in pats:
        m, q = find_sentence(text, p)
        if m:
            return m, q
    return None, None


# ---------------- 各公司 ----------------

def extract_akam(text):
    out = {}
    # 报表：SUPPLEMENTAL REVENUE DATA – REVENUE BY SOLUTION（千美元）：本季 | 上季 | 去年同季 [| 半年 | 半年]
    k = text.find("REVENUE BY SOLUTION")
    start = 0
    if k >= 0:
        start = text.count("\n", 0, k)
    vals, i = row_numbers(text, r"Security", n=3, start=start)
    if vals and len(vals) >= 3 and vals[0] > 1000:
        cur, prv, ly = vals[0] / 1000.0, vals[1] / 1000.0, vals[2] / 1000.0
        q = "Security | %s | %s | %s（千美元：本季/上季/去年同季）" % tuple("{:,.0f}".format(v) for v in vals[:3])
        _put(out, "security_rev", cur, "USD_M", q)
        _put(out, "security_rev_ly", ly, "USD_M", q)
        _put(out, "security_yoy", _yoy(cur, ly), "pct", q)
        # 其后两行是公司四舍五入的同比：报告币种、不变汇率
        vals2, i2 = row_numbers(text, r"Security", n=1, start=i + 1)
        if vals2:
            _put(out, "security_yoy_rep", vals2[0], "pct", "报表同比行（公司四舍五入）")
            vals3, _ = row_numbers(text, r"Security", n=1, start=i2 + 1)
            if vals3:
                _put(out, "security_yoy_cc", vals3[0], "pct", "报表不变汇率同比行（公司四舍五入）")
    for key, lab in (("cis_rev", r"Cloud infrastructure services"),
                     ("delivery_rev", r"Delivery and other cloud applications|Delivery"),
                     ("compute_rev", r"Compute")):
        v, _ = row_numbers(text, lab, n=3, start=start)
        if v and len(v) >= 3 and v[0] > 1000:
            _put(out, key, v[0] / 1000.0, "USD_M", "报表 %s 行" % lab.split("|")[0])
            _put(out, key.replace("_rev", "_yoy"), _yoy(v[0], v[2]), "pct", "报表现算")
    v, _ = row_numbers(text, r"Revenue", n=3)
    if v and len(v) >= 3 and v[0] > 100000:
        _put(out, "revenue", v[0] / 1000.0, "USD_M", "利润表 Revenue 行（千美元）")
        _put(out, "revenue_yoy", _yoy(v[0], v[2]), "pct", "利润表 Revenue 行现算")
    else:
        m, q = find_sentence(text, r"Revenue (?:was|of) \$([\d.,]+) (billion|million)")
        if m:
            _put(out, "revenue", money_to_musd(m.group(1), m.group(2)), "USD_M", q)
    # 信号 2：Guardicore 微分段 + API 安全（4Q25 新闻稿单列过，1Q26 起未再出现）
    m, q = find_sentence(text, r"(Guardicore Segmentation and API Security|API Security)[^.\n]{0,40}?revenue[^.\n]{0,40}?\$([\d.,]+) (million|billion)[^.\n]{0,40}?up (\d+)%")
    if m:
        _put(out, "gs_api_rev", money_to_musd(m.group(2), m.group(3)), "USD_M", q)
        _put(out, "gs_api_yoy", float(m.group(4)), "pct", q)
    return out


def extract_ftnt(text):
    out = {}
    # 利润表：Product | 本季 | 去年同季 | 累计 | 累计（百万美元）——文中第一处 Product 行是收入
    v, _ = row_numbers(text, r"Product", n=2)
    if v and len(v) == 2:
        q = "Product | %.1f | %.1f（百万美元：本季/去年同季）" % (v[0], v[1])
        _put(out, "product_rev", v[0], "USD_M", q)
        _put(out, "product_rev_ly", v[1], "USD_M", q)
        _put(out, "product_yoy", _yoy(v[0], v[1]), "pct", q)
    v, _ = row_numbers(text, r"Service", n=2)
    if v and len(v) == 2:
        _put(out, "service_rev", v[0], "USD_M", "Service 行")
        _put(out, "service_yoy", _yoy(v[0], v[1]), "pct", "Service 行现算")
    v, _ = row_numbers(text, r"Total revenue", n=2)
    if v and len(v) == 2:
        _put(out, "revenue", v[0], "USD_M", "Total revenue 行")
        _put(out, "revenue_yoy", _yoy(v[0], v[1]), "pct", "Total revenue 行现算")
    m, q = find_sentence(text, r"Billings grew (\d+)%(?: year over year)? to \$([\d.,]+) (billion|million)")
    if m:
        _put(out, "billings", money_to_musd(m.group(2), m.group(3)), "USD_M", q)
        _put(out, "billings_yoy", float(m.group(1)), "pct", q)
    return out


def extract_chkp(text):
    out = {}
    k = text.find("CONSOLIDATED STATEMENT OF INCOME")
    start = text.count("\n", 0, k) if k >= 0 else 0
    v, _ = row_numbers(text, r"Products and licenses", n=2, start=start)
    if v and len(v) == 2:
        q = "Products and licenses %.1f vs %.1f（百万美元：本季/去年同季）" % (v[0], v[1])
        _put(out, "product_rev", v[0], "USD_M", q)
        _put(out, "product_rev_ly", v[1], "USD_M", q)
        _put(out, "product_yoy", _yoy(v[0], v[1]), "pct", q)
    v, _ = row_numbers(text, r"Security subscriptions", n=2, start=start)
    if v and len(v) == 2:
        _put(out, "subscription_rev", v[0], "USD_M", "Security subscriptions 行")
        _put(out, "subscription_yoy", _yoy(v[0], v[1]), "pct", "Security subscriptions 行现算")
    v, _ = row_numbers(text, r"Total revenues", n=2, start=start)
    if v and len(v) == 2:
        _put(out, "revenue", v[0], "USD_M", "Total revenues 行")
        _put(out, "revenue_yoy", _yoy(v[0], v[1]), "pct", "Total revenues 行现算")
    return out


def extract_panw(text):
    out = {}
    v, _ = row_numbers(text, r"Total revenue", n=2)
    if v and len(v) == 2 and v[0] > 500:
        _put(out, "revenue", v[0], "USD_M", "Total revenue | %.1f | %.1f（百万美元：本季/去年同季）" % (v[0], v[1]))
        _put(out, "revenue_yoy", _yoy(v[0], v[1]), "pct", "利润表现算")
    else:
        m, q = find_sentence(text, r"Total revenue for the fiscal \w+ quarter \d{4} grew (\d+)% year over year to \$([\d.,]+) (billion|million)")
        if m:
            _put(out, "revenue", money_to_musd(m.group(2), m.group(3)), "USD_M", q)
            _put(out, "revenue_yoy", float(m.group(1)), "pct", q)
    m, q = find_sentence(text, r"Next-Generation Security ARR (?:for the fiscal \w+ quarter \d{4} )?grew (\d+)% year over year to \$([\d.,]+) (billion|million)")
    if m:
        _put(out, "ngs_arr", money_to_musd(m.group(2), m.group(3)), "USD_M", q)
        _put(out, "ngs_arr_yoy", float(m.group(1)), "pct", q)
    m, q = find_sentence(text, r"Remaining performance obligations? grew (\d+)% year over year to \$([\d.,]+) (billion|million)")
    if m:
        _put(out, "rpo", money_to_musd(m.group(2), m.group(3)), "USD_M", q)
    m, q = find_sentence(text, r"Prisma AIRS[^.\n]{0,80}?\$([\d.,]+) (million|billion)")
    if m:
        _put(out, "prisma_airs_arr", money_to_musd(m.group(1), m.group(2)), "USD_M", q)
    return out


def extract_crwd(text):
    out = {}
    m, q = _first(text, [r"Total revenue was \$([\d.,]+) (billion|million), a (\d+)% increase"])
    if m:
        _put(out, "revenue", money_to_musd(m.group(1), m.group(2)), "USD_M", q)
        _put(out, "revenue_yoy", float(m.group(3)), "pct", q)
    m, q = _first(text, [r"Annual Recurring Revenue \(ARR\) grew (\d+)% year-over-year to \$([\d.,]+) (billion|million)",
                         r"Ending ARR grows (\d+)% year-over-year to reach \$([\d.,]+) (billion|million)"])
    if m:
        _put(out, "arr", money_to_musd(m.group(2), m.group(3)), "USD_M", q)
        _put(out, "arr_yoy", float(m.group(1)), "pct", q)
    m, q = _first(text, [r"of which \$([\d.,]+) (million|billion) was net new ARR"])
    if m:
        _put(out, "net_new_arr", money_to_musd(m.group(1), m.group(2)), "USD_M", q)
    m, q = _first(text, [r"(Exceeds|Surpasses|Reaches|Over|More than)?\s*\$([\d.,]+) (billion|million) in ending ARR from (?:accounts that have adopted (?:the )?Falcon Flex|Falcon Flex accounts)[^\n]{0,80}?(\d+)%"])
    if m:
        _put(out, "flex_arr", money_to_musd(m.group(2), m.group(3)), "USD_M", q, _qual(m.group(1)))
        _put(out, "flex_arr_yoy", float(m.group(4)), "pct", q)
    m, q = _first(text, [r"(?:AI ?DR|AI Detection and Response)[^.\n]{0,80}?ARR[^.\n]{0,40}?(exceed\w*|surpass\w*|more than|over|of|to)?\s*\$([\d.,]+) (million|billion)"])
    if m:
        _put(out, "ai_dr_arr", money_to_musd(m.group(2), m.group(3)), "USD_M", q, _qual(m.group(1)))
    return out


def extract_net(text):
    out = {}
    m, q = _first(text, [r"revenue totaled \$([\d.,]+) (million|billion), representing an increase of (\d+)%",
                         r"Total revenue of \$([\d.,]+) (million|billion),? representing an increase of (\d+)%"])
    if m:
        _put(out, "revenue", money_to_musd(m.group(1), m.group(2)), "USD_M", q)
        _put(out, "revenue_yoy", float(m.group(3)), "pct", q)
    m, q = _first(text, [r"(?:pay[- ]per[- ]crawl|Cloudflare\.?pay|agent payments?)[^.\n]{0,120}?\$([\d.,]+) (million|billion)"])
    if m:
        _put(out, "agent_pay_value", money_to_musd(m.group(1), m.group(2)), "USD_M", q)
    return out


def extract_okta(text):
    out = {}
    m, q = _first(text, [r"Total revenue was \$([\d.,]+) (billion|million), an increase of (\d+)% year-over-year"])
    if m:
        _put(out, "revenue", money_to_musd(m.group(1), m.group(2)), "USD_M", q)
        _put(out, "revenue_yoy", float(m.group(3)), "pct", q)
    m, q = _first(text, [r"Subscription revenue was \$([\d.,]+) (billion|million), an increase of (\d+)%"])
    if m:
        _put(out, "subscription_rev", money_to_musd(m.group(1), m.group(2)), "USD_M", q)
        _put(out, "subscription_yoy", float(m.group(3)), "pct", q)
    m, q = _first(text, [r"cRPO[^\n]{0,160}?was \$([\d.,]+) (billion|million), up (\d+)%"])
    if m:
        _put(out, "crpo", money_to_musd(m.group(1), m.group(2)), "USD_M", q)
        _put(out, "crpo_yoy", float(m.group(3)), "pct", q)
    return out


def extract_sail(text):
    out = {}
    m, q = _first(text, [r"Total ARR was \$([\d.,]+) (billion|million), an increase of (\d+)%"])
    if m:
        _put(out, "arr", money_to_musd(m.group(1), m.group(2)), "USD_M", q)
        _put(out, "arr_yoy", float(m.group(3)), "pct", q)
    m, q = _first(text, [r"SaaS ARR was \$([\d.,]+) (billion|million), an increase of (\d+)%"])
    if m:
        _put(out, "saas_arr", money_to_musd(m.group(1), m.group(2)), "USD_M", q)
        _put(out, "saas_arr_yoy", float(m.group(3)), "pct", q)
    m, q = _first(text, [r"Total revenue was \$([\d.,]+) (billion|million), an increase of (\d+)%"])
    if m:
        _put(out, "revenue", money_to_musd(m.group(1), m.group(2)), "USD_M", q)
        _put(out, "revenue_yoy", float(m.group(3)), "pct", q)
    m, q = _first(text, [r"AI[- ]driven ARR (exceeded|surpassed|reached|was|of|grew to|topped)?\s*(more than |over |approximately )?\$([\d.,]+) (million|billion)"])
    if m:
        qual = _qual(m.group(1)) or _qual(m.group(2))
        _put(out, "ai_arr", money_to_musd(m.group(3), m.group(4)), "USD_M", q, qual)
        if "arr" in out:
            _put(out, "ai_arr_share", out["ai_arr"]["value"] / out["arr"]["value"] * 100.0, "pct",
                 "AI 驱动 ARR ÷ 总 ARR（现算）", qual)
            out["ai_arr_share"]["level"] = True
    m, q = _first(text, [r"AI[- ]driven solutions accounted for (more than |over )?(\d+)% of net new ARR"])
    if m:
        _put(out, "ai_net_new_share", float(m.group(2)), "pct", q, _qual(m.group(1)))
        out["ai_net_new_share"]["level"] = True
    return out


def extract_zs(text):
    out = {}
    m, q = _first(text, [r"Revenue:? (?:[Gg]rew|[Gg]rows|increased) (\d+)% year[- ]over[- ]year to \$([\d.,]+) (million|billion)",
                         r"[Rr]evenue (?:of|was) \$([\d.,]+) (million|billion)[^.\n]{0,40}?(\d+)%"])
    if m:
        g = m.groups()
        if m.re.pattern.startswith("Revenue:?"):
            _put(out, "revenue", money_to_musd(g[1], g[2]), "USD_M", q)
            _put(out, "revenue_yoy", float(g[0]), "pct", q)
        else:
            _put(out, "revenue", money_to_musd(g[0], g[1]), "USD_M", q)
            _put(out, "revenue_yoy", float(g[2]), "pct", q)
    m, q = _first(text, [r"(?:ARR:? ARR grew|ARR: Grew|Annual Recurring Revenue \(\"?ARR\"?\) (?:grew|grows)) (\d+)% year[- ]over[- ]year to \$([\d.,]+) (million|billion)"])
    if m:
        _put(out, "arr", money_to_musd(m.group(2), m.group(3)), "USD_M", q)
        _put(out, "arr_yoy", float(m.group(1)), "pct", q)
    return out


EXTRACTORS = {
    "AKAM": extract_akam, "FTNT": extract_ftnt, "CHKP": extract_chkp, "PANW": extract_panw,
    "CRWD": extract_crwd, "NET": extract_net, "OKTA": extract_okta, "SAIL": extract_sail, "ZS": extract_zs,
}

# 信号相关关键词：命中只代表"新闻稿里提到了，需要人/routine 判断"，不是触发。
KEYWORDS = {
    "AKAM": {"S2": [r"API Security revenue", r"Guardicore Segmentation and API Security",
                    r"\bbot\b[^.\n]{0,60}revenue", r"\bagent[^.\n]{0,60}revenue", r"AI security revenue",
                    r"Bot (?:&|and) Agent"]},
    "NET": {"S3": [r"pay[- ]per[- ]crawl", r"Cloudflare\.?pay", r"per[- ]request", r"monetiz", r"x402",
                   r"AI Crawl Control", r"crawl"]},
    "OKTA": {"S4": [r"per[- ]agent", r"usage[- ]based", r"consumption", r"Okta for AI Agents", r"Auth0 for AI Agents",
                    r"pricing"]},
    "SAIL": {"S4": [r"AI[- ]driven", r"non-human", r"machine identit", r"agent", r"\bAI\b[^.\n]{0,40}ARR"]},
    "PANW": {"S5": [r"Prisma AIRS", r"AI security", r"AIRS"]},
    "CRWD": {"S6": [r"AI ?DR", r"AI Detection and Response", r"Falcon AI"]},
    "FTNT": {"S7": [r"AI data ?cent", r"GPU", r"hyperscal"]},
    "CHKP": {"S8": [r"product[s]? (?:and licenses )?revenue", r"return to (?:product )?growth", r"AI Network Firewall",
                    r"Lakera"]},
}


def keyword_hits(ticker, text, limit=6):
    hits = []
    for sig, pats in KEYWORDS.get(ticker, {}).items():
        for p in pats:
            for m in re.finditer(p, text, re.I):
                s = max(0, m.start() - 110)
                e = min(len(text), m.end() + 110)
                snip = re.sub(r"\s+", " ", text[s:e]).strip()
                hits.append({"signal": sig, "pattern": p, "snippet": snip})
                break
    # 去重、截断
    seen, out = set(), []
    for h in hits:
        if h["snippet"] in seen:
            continue
        seen.add(h["snippet"])
        out.append(h)
    return out[:limit]


def extract(ticker, text):
    fn = EXTRACTORS.get(ticker)
    return fn(text) if fn else {}


if __name__ == "__main__":
    import json
    tk, path = sys.argv[1], sys.argv[2]
    t = open(path, encoding="utf-8").read()
    print(json.dumps(extract(tk, t), ensure_ascii=False, indent=1))
    print(json.dumps(keyword_hits(tk, t), ensure_ascii=False, indent=1))
