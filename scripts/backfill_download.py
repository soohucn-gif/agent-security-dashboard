#!/usr/bin/env python3
"""一次性回补：把各公司 since 以来的季度业绩新闻稿文本下载到 <out>/<TICKER>/<财季>_<申报日>.txt。"""
import os, sys, json
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from sec_edgar import companies, earnings_filings, fetch_press_release, quarter_end_before, fiscal_label

out = sys.argv[1]
since = sys.argv[2] if len(sys.argv) > 2 else "2024-01-15"
only = set(sys.argv[3].split(",")) if len(sys.argv) > 3 else None
manifest = {}
for c in companies():
    if only and c["ticker"] not in only:
        continue
    d = os.path.join(out, c["ticker"]); os.makedirs(d, exist_ok=True)
    got = {}
    for f in earnings_filings(c, since=since):
        pe = quarter_end_before(f["filed"], c["fy_end_month"])
        lab = fiscal_label(pe, c["fy_end_month"])
        if lab in got:      # 同一财季只取第一份（最新的那份是业绩稿）
            continue
        try:
            url, text = fetch_press_release(c, f)
        except Exception as e:
            print("ERR", c["ticker"], f["filed"], e); continue
        if not url:
            continue
        got[lab] = {"filed": f["filed"], "accession": f["accession"], "url": url, "period_end": pe.isoformat()}
        open(os.path.join(d, "%s_%s.txt" % (lab, f["filed"])), "w").write(text)
        print(c["ticker"], lab, f["filed"], len(text), url.rsplit("/",1)[-1])
    manifest[c["ticker"]] = got
json.dump(manifest, open(os.path.join(out, "manifest_%s.json" % ("all" if not only else "_".join(sorted(only)))), "w"), indent=1)
