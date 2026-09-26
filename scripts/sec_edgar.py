#!/usr/bin/env python3
"""SEC EDGAR：列财报申报、定位新闻稿附件（EX-99.1）、换算财季标签。

- 美国公司的季度新闻稿在 8-K Item 2.02 的 EX-99.1；CHKP 是外国发行人，走 6-K，
  6-K 没有 item 字段，要靠正文判断是不是业绩新闻稿。
- 财季标签：自然年公司用 2026Q2；非自然年用 FY27Q2（CRWD/OKTA/SAIL 一月财年、PANW/ZS 七月财年）。
"""
import calendar
import datetime
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import CONFIG, html_to_text, load_json, sec_get  # noqa: E402


def companies():
    return load_json(os.path.join(CONFIG, "companies.json"))["companies"]


def company(ticker):
    for c in companies():
        if c["ticker"] == ticker:
            return c
    raise KeyError(ticker)


def submissions(cik):
    return json.loads(sec_get("https://data.sec.gov/submissions/CIK%010d.json" % cik))


def _rows(block):
    n = len(block["form"])
    for i in range(n):
        yield {
            "filed": block["filingDate"][i],
            "form": block["form"][i],
            "items": block.get("items", [""] * n)[i] or "",
            "accession": block["accessionNumber"][i],
            "primary": block["primaryDocument"][i],
            "report_date": block.get("reportDate", [""] * n)[i] or "",
        }


def earnings_filings(comp, since="2023-12-01", sub=None):
    """公司 since 之后的候选财报申报（新到旧）。8-K 只要含 Item 2.02 的；6-K 全部返回，由正文判定。"""
    sub = sub or submissions(comp["cik"])
    out = []
    for r in _rows(sub["filings"]["recent"]):
        if r["filed"] < since:
            continue
        if r["form"] != comp["form"]:
            continue
        if r["form"] == "8-K" and "2.02" not in r["items"]:
            continue
        out.append(r)
    return out


def filing_base(cik, accession):
    return "https://www.sec.gov/Archives/edgar/data/%d/%s/" % (cik, accession.replace("-", ""))


def filing_files(cik, accession):
    idx = json.loads(sec_get(filing_base(cik, accession) + "index.json"))
    return [it for it in idx["directory"]["item"]]


_PR_PAT = re.compile(r"(ex|exh|exhibit)[-_]?99[-_.]?0?1(?!\d)|ex991|99-?1\.htm|991\.htm|pr\.htm$|press", re.I)


def _candidates(files, primary):
    htm = [f for f in files if f["name"].lower().endswith((".htm", ".html", ".txt"))
           and not f["name"].lower().startswith(("index", "r"))
           and "-index" not in f["name"].lower()
           and not re.match(r"^\d{10}-\d{2}-\d{6}\.txt$", f["name"])]
    first = [f["name"] for f in htm if _PR_PAT.search(f["name"])]
    ex99 = [f["name"] for f in htm if re.search(r"ex[-_]?99|exhibit[-_]?99|ex_?99", f["name"], re.I) and f["name"] not in first]
    rest = sorted([f for f in htm if f["name"] not in first and f["name"] not in ex99],
                  key=lambda f: -int(f.get("size") or 0) if str(f.get("size") or "").isdigit() else 0)
    order = first + ex99 + [f["name"] for f in rest]
    # 主文档（8-K 封面）排最后；6-K 的主文档本身可能就是新闻稿，所以不剔除
    if primary in order:
        order.remove(primary)
        order.append(primary)
    return order


_EARN_PAT = re.compile(
    r"(reports?|announces?|delivers?)\b[^\n]{0,120}\b(first|second|third|fourth|q[1-4])\b[^\n]{0,60}\b(quarter|results)|"
    r"\b(first|second|third|fourth)\s+quarter\b[^\n]{0,60}\b(fiscal\s+)?(20\d\d\s+)?(financial\s+)?results",
    re.I)


def looks_like_earnings(text):
    head = text[:4000]
    return bool(_EARN_PAT.search(head)) and ("revenue" in text.lower())


def fetch_press_release(comp, filing):
    """返回 (url, text)。找不到返回 (None, None)。"""
    files = filing_files(comp["cik"], filing["accession"])
    base = filing_base(comp["cik"], filing["accession"])
    for name in _candidates(files, filing["primary"])[:6]:
        try:
            text = html_to_text(sec_get(base + name))
        except RuntimeError:
            continue
        if len(text) < 1500:
            continue
        if looks_like_earnings(text):
            return base + name, text
    return None, None


# ---------- 财季换算 ----------

def _month_end(y, m):
    return datetime.date(y, m, calendar.monthrange(y, m)[1])


def quarter_months(fy_end_month):
    return sorted({((fy_end_month - 1 + 3 * k) % 12) + 1 for k in range(4)})


def quarter_end_before(filed, fy_end_month):
    """申报日之前最近的一个财季末。"""
    if isinstance(filed, str):
        filed = datetime.date.fromisoformat(filed)
    qm = quarter_months(fy_end_month)
    y, m = filed.year, filed.month
    for _ in range(15):
        if m in qm:
            d = _month_end(y, m)
            if d < filed:
                return d
        m -= 1
        if m == 0:
            m, y = 12, y - 1
    raise ValueError("no quarter end before %s" % filed)


def fiscal_label(period_end, fy_end_month):
    """period_end(date) → '2026Q2' 或 'FY27Q2'。"""
    if isinstance(period_end, str):
        period_end = datetime.date.fromisoformat(period_end)
    m, y = period_end.month, period_end.year
    if fy_end_month == 12:
        return "%dQ%d" % (y, (m - 1) // 3 + 1)
    # 该季度所属财年 = 财年结束月所在年份
    fy = y if m <= fy_end_month else y + 1
    months_into = (m - fy_end_month) % 12  # 3,6,9,0
    q = {3: 1, 6: 2, 9: 3, 0: 4}[months_into]
    return "FY%02dQ%d" % (fy % 100, q)


def prior_year_label(label):
    """'2026Q2' → '2025Q2'；'FY27Q2' → 'FY26Q2'。"""
    if label.startswith("FY"):
        return "FY%02dQ%s" % (int(label[2:4]) - 1, label[-1])
    return "%dQ%s" % (int(label[:4]) - 1, label[-1])


def prior_quarter_label(label):
    q = int(label[-1])
    if label.startswith("FY"):
        fy = int(label[2:4])
        return "FY%02dQ%d" % (fy - 1, 4) if q == 1 else "FY%02dQ%d" % (fy, q - 1)
    y = int(label[:4])
    return "%dQ4" % (y - 1) if q == 1 else "%dQ%d" % (y, q - 1)


def label_sort_key(label):
    """统一排序：按日历上的财季末近似排序用。"""
    if label.startswith("FY"):
        return (2000 + int(label[2:4]), int(label[-1]))
    return (int(label[:4]), int(label[-1]))


if __name__ == "__main__":
    # 自测：python3 scripts/sec_edgar.py AKAM
    tk = sys.argv[1] if len(sys.argv) > 1 else "AKAM"
    c = company(tk)
    fl = earnings_filings(c, since=sys.argv[2] if len(sys.argv) > 2 else "2026-01-01")
    for f in fl[:4]:
        pe = quarter_end_before(f["filed"], c["fy_end_month"])
        url, text = fetch_press_release(c, f)
        print(f["filed"], f["form"], f["accession"], fiscal_label(pe, c["fy_end_month"]), url, len(text or ""))
