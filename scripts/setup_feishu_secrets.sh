#!/bin/bash
# 给 agent-security-dashboard 仓库配飞书私聊推送的三个 secrets（与 macro5、AI Signal 同一个自建应用）。
# 凭据从本机 ~/.openclaw/credentials/feishu-app.json 读，直接交给 gh，不在终端回显。
set -euo pipefail
REPO="soohucn-gif/agent-security-dashboard"
CRED="$HOME/.openclaw/credentials/feishu-app.json"
OPEN_ID="${FEISHU_OPEN_ID:?先设置 FEISHU_OPEN_ID（Henry 在该自建应用下的 open_id，不入库）}"
python3 -c "import json,sys;print(json.load(open(sys.argv[1]))['app_id'],end='')" "$CRED" | gh secret set FEISHU_APP_ID -R "$REPO"
python3 -c "import json,sys;print(json.load(open(sys.argv[1]))['app_secret'],end='')" "$CRED" | gh secret set FEISHU_APP_SECRET -R "$REPO"
printf '%s' "$OPEN_ID" | gh secret set FEISHU_OPEN_ID -R "$REPO"
gh secret list -R "$REPO"
# 发一条测试推送，确认链路通
gh workflow run update.yml -R "$REPO" -f notify_test=true -f skip_fetch=true
echo "已触发测试推送；一两分钟后飞书私聊应收到“Agent 安全跟踪看板已上线”。"
