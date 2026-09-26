# Agent 时代安全 · 跟踪看板

页面：https://soohucn-gif.github.io/agent-security-dashboard/

跟踪《Akamai 的安全业务在哪一层？谁更受益于 agent 的几何级增长》研究底稿（2026-09-25）第七节建议的 8 项改判信号。全部跑在云端：GitHub Actions 定时抓数、建页、推飞书；Claude 云端 routine 在财报后补电话会口径并写跟踪反馈。本机关机不影响。

## 跟踪什么

| # | 信号 | 基线（2026-09-25） | 改判规则 | 数据从哪来 |
|---|---|---|---|---|
| S1 | Akamai 安全收入增速 | +10%（精确值 +9.5%），不变汇率 +9% | 连续两季超 12% → 量利转化上调；跌破 8% → 下调 | 新闻稿报表（自动） |
| S2 | Akamai 是否单列 bot / agent / API 安全收入 | 1Q26 起不再单列 | 首次单列 → 证据强度上调 | 新闻稿（自动扫描）+ 电话会（分析员） |
| S3 | Cloudflare 按请求收费的成交额 | 未披露 | 首次披露 → 边缘"量大难收钱"那格上移 | 电话会（分析员） |
| S4 | 身份厂商按 agent / 按用量计价 | Okta 未启用；SailPoint AI ARR 约占 5.7% | Okta 启用，或 SailPoint 占比超 10% | SailPoint 新闻稿（自动）+ Okta 电话会（分析员） |
| S5 | PANW Prisma AIRS ARR | 超 $1 亿 | 下季超 $2 亿 → 上调 | 电话会（分析员） |
| S6 | CrowdStrike AI DR ARR | 较 Q1 近三倍，无绝对值 | 披露绝对值超 $1 亿 → 上调 | 电话会（分析员） |
| S7 | Fortinet 产品收入增速 | +52% | 回落到 20% 以下 → AI 数据中心换机见顶 | 新闻稿报表（自动） |
| S8 | Check Point 产品收入 | −14%，公司称 Q4 回正 | Q4 未回正 → 维持最弱判断 | 6-K 新闻稿报表（自动） |

另有：9 家公司（AKAM、PANW、FTNT、CHKP + NET、CRWD、OKTA、SAIL、ZS）的季度指标台账、每日行情与市销率、财报日历、跟踪反馈日志。

## 怎么运转

```
SEC EDGAR（8-K 2.02 / 6-K）──┐
Nasdaq 日线 / 市值 / 财报日 ──┤→ GitHub Actions（北京 05:30、07:30、20:30，周一 08:30 周报）
                              │    watch_filings → extractors → data/quarterly.json
                              │    evaluate → data/signals_state.json（状态变化生成事件）
                              │    build_site → index.html（GitHub Pages）
                              │    notify_feishu → 飞书私聊（新财报、信号变化、分析员反馈、周报）
                              │
新财报进 data/review_queue.json → Claude 云端 routine（北京 周二至周六 09:15）
                                   读 raw/ 新闻稿全文 + WebSearch 电话会口径
                                   record.py 写指标与反馈 → push → 触发 Actions 重建与推送
```

- 状态：**维持** / **接近阈值** / **触发·上调** / **触发·下调**（S8 的触发分别显示"重估""维持最弱"）。
- 口径：同比优先用报表精确值现算，公司四舍五入口径并列；电话会数字标 †（第三方转录）；分析员或人工录入标 *。
- 数据写入优先级：人工 > 分析员 > 自动解析，低优先级不覆盖高优先级。

## 常用操作

改阈值或规则：编辑 `config/signals.json` 推送即可，页面与推送跟着变。

手动补一个数（本机或云端都行）：

```bash
python3 scripts/record.py metric --ticker PANW --period FY27Q1 --key prisma_airs_arr --value 210 --call --method manual --source "https://…" --quote "原句"
```

手动跑一轮 / 发测试推送 / 发周报：

```bash
gh workflow run update.yml -R soohucn-gif/agent-security-dashboard
gh workflow run update.yml -R soohucn-gif/agent-security-dashboard -f notify_test=true -f skip_fetch=true
gh workflow run update.yml -R soohucn-gif/agent-security-dashboard -f weekly=true -f skip_fetch=true
```

飞书 secrets（首次部署运行一次）：`bash scripts/setup_feishu_secrets.sh`

暂停：`gh workflow disable update.yml -R soohucn-gif/agent-security-dashboard`；分析员 routine 在 https://claude.ai/code/routines 里停。

## 目录

```
config/   companies.json 公司与 CIK｜signals.json 8 项信号与阈值｜metrics.json 指标目录｜calendar_seed.json 财报日兜底
data/     quarterly.json 季度指标｜signals_state.json 信号状态｜market.json 行情快照｜prices/*.csv 日线
          calendar.json 财报日｜feedback.json 反馈日志｜review_queue.json 分析员待办｜events.json 推送队列
raw/      最近几份新闻稿全文（给分析员读）
scripts/  fetch_market｜watch_filings｜extractors｜evaluate｜build_site｜notify_feishu｜record｜store｜sec_edgar
docs/     analyst_playbook.md 云端分析员手册
```

## 局限

- 新闻稿版式一变，正则可能取不到数：推送会提示"自动解析没取到指标"，分析员补录。
- 电话会口径只能靠分析员的网页检索（云端沙箱不能直连外网），来源是转录与报道，未经发行人核验。
- 财报日来自 Nasdaq/Zacks，多为预估；分析员每周二用公司公告改成"已确认"。

研究跟踪用途，不构成投资建议。
