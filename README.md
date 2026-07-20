# Procal Ops Bot

A Telegram bot powered by Claude AI for DevOps incident triage, PR review, and infrastructure management. Lives in a Telegram group topic and responds only when mentioned.

---

## Architecture

```
Telegram Group (topic thread #4)
    ↓  @mention or reply
EC2 (procal-ops) — private subnet + NAT Gateway
    ├── bot.py               ← entry point: Telegram app + startup
    ├── admin_panel.py       ← FastAPI admin UI (port 8080)
    ├── config.py            ← static env vars and constants
    ├── bot_config.py        ← runtime chat/tool config (~/.ops-bot-config.json)
    ├── mcp_client.py        ← MCP session management (Grafana + Git)
    ├── claude_client.py     ← Claude API, conversation history, prompts
    ├── audit.py             ← audit trail (log file + Telegram notifications)
    ├── polling.py           ← auto PR review loop (infra repo → main)
    ├── tools/
    │   ├── __init__.py      ← unified tool registry (ALL_TOOLS, run_tool)
    │   ├── aws.py           ← EC2 + Inspector boto3 tools
    │   └── bitbucket.py     ← Bitbucket REST API + tool schemas (PRs, comments, branches)
    ├── tg/
    │   ├── handlers.py      ← message routing and command handlers
    │   └── formatting.py    ← markdown→HTML, message splitting, rate-limit parsing
    ├── templates/
    │   └── admin.html       ← admin panel SPA (HTML/CSS/JS)
    ├── deploy/
    │   ├── ops-bot.service          ← systemd unit for the bot
    │   └── ops-bot-admin.service    ← systemd unit for the admin panel
    └── scripts/
        ├── setup-server.sh          ← one-time EC2 setup script
        └── list_tools.py            ← dev utility: inspect MCP tool list
```

### Dependency flow

```
bot.py
 ├── mcp_client.py  ←  config, tools/
 ├── polling.py     ←  config, claude_client, tools/bitbucket
 └── tg/
     └── handlers.py  ←  config, claude_client, tg/formatting
         claude_client.py  ←  config, mcp_client, tools/
config.py  ← no internal imports (stdlib + dotenv only)
```

### How tools work

Claude receives a unified tool list built from three sources:

| Source | Tools | Count |
|--------|-------|-------|
| `tools/aws.py` | EC2 (`list_ec2_instances`), Inspector (`list_inspector_findings`) | 2 |
| `tools/bitbucket.py` | PR CRUD, comments, approvals, branch commits | 11 |
| Grafana MCP server | Loki logs, Prometheus, incidents, traces | 7 |
| Git MCP servers (×4) | `git_log`, `git_diff`, `git_show`, `git_read_file`, `git_status` per repo | 20 |

Git tools are prefixed by repo: `dora_git_log`, `maps_git_diff`, `infra_git_read_file`, etc.

### Tool groups (admin panel toggles)

| Toggle | Controls |
|--------|----------|
| `aws` | EC2 instance listing, Inspector vulnerability findings |
| `bitbucket` | All Bitbucket operations across all repos (PRs, comments, approvals) |
| `grafana` | Loki logs, Prometheus metrics, Tempo traces, incidents |
| `git-dora` | Read git history from local `~/dora` clone |
| `git-maps` | Read git history from local `~/maps` clone |
| `git-boots` | Read git history from local `~/boots` clone |
| `git-infra` | Read git history from local `~/procal-infra` clone |

Note: `bitbucket` is all-or-nothing — there is no per-repo Bitbucket toggle.

---

## Features

### 1. Incident triage (RCA)

Ask the bot about any service error and it will:
1. Check active incidents in Grafana
2. Query Loki error logs for the affected service
3. Query Prometheus metrics (latency, error rate, saturation)
4. Check slow traces in Tempo
5. Cross-reference recent git commits
6. Return: what happened, when, probable cause, recommended fix

```
@sidi3_bot dora backend is returning 500s since 10 minutes ago
@sidi3_bot check if there are any critical alerts
@sidi3_bot show me the last 5 commits to the maps repo
```

### 2. PR review

The bot can read, review, and act on Bitbucket PRs:

```
@sidi3_bot review all open PRs in procal-infra-3
@sidi3_bot check the diff on dora-learner-3 PR #14
@sidi3_bot request changes on procal-infra-3 PR #137
@sidi3_bot approve dora PR #14
@sidi3_bot trigger atlantis plan on infra PR #137
```

Available PR actions: `list`, `get diff`, `get comments`, `post comment`, `approve`, `unapprove`, `request changes`, `decline`, `delete comment`, `create PR`, `commit fix to new branch`.

### 3. Auto PR review (polling)

The bot watches `procal-infra-3` → `main` PRs every 5 minutes. When a new PR is opened or a new commit is pushed:
- Fetches the diff
- Runs a Terraform-focused review (risk level, dangerous changes, best practice violations)
- Posts the review as a Bitbucket comment
- Sends a notification to the Telegram group topic

### 4. Code fixes

The bot can fix code issues directly in Bitbucket without cloning locally:
1. Creates a `bot/fix-pr{id}` branch forked from the PR source branch
2. Commits the fixed file via the Bitbucket source API
3. Opens a PR from `bot/fix-pr{id}` back to the author's branch

```
@sidi3_bot the token validation in dora PR #14 is wrong, fix it
```

### 5. Tsim mode

A no-nonsense persona. Short answers, no pleasantries, does the ops work without enthusiasm.

---

## Telegram commands

| Command | Description |
|---------|-------------|
| `@bot /tsim` | Enable Tsim mode |
| `@bot /tsim_off` | Back to normal mode |
| `@bot /clear` | Clear your conversation history |
| `@bot /chatid` | Show the current chat ID and your user ID |

Commands work via `@mention /cmd` in groups, or as `/cmd@botname`, or as plain `/cmd` in private chat.

---

## Group chat behavior

- The bot responds only in chats configured via the admin panel (default: `-1004269056589`, topic thread `4`)
- In groups, it responds only when **@mentioned** or when someone **replies** to one of its messages
- When replying, the bot includes the original message as context for Claude
- Each user in the group has their own conversation history (keyed by user ID)
- Chat config is read from `~/.ops-bot-config.json` — changes take effect on the next message (no restart needed)

---

## Admin panel

A web UI for managing which chats the bot operates in and which tool groups are enabled per chat.

### Access

Connect via VPN, then open `http://<EC2-private-ip>:8080`.

Sign in with the credentials from your `.env`:
```
ADMIN_USERNAME=admin
ADMIN_PASSWORD=<your-password>
```

### What you can do

| Action | Description |
|--------|-------------|
| **Add chat** | Register a new group/supergroup with its topic thread ID |
| **Remove chat** | Stop the bot from responding in a chat |
| **Toggle tools** | Enable or disable tool groups (AWS, Bitbucket, Grafana, Git repos) per chat |
| **Edit chat** | Update display name or change the topic thread |
| **Logs tab** | View the audit log (messages, tool calls, errors) with type and username filters. Auto-refreshes every 10s. |

Changes are saved immediately to `~/.ops-bot-config.json` and picked up by the bot on the next message.

---

## Setup

### 1. EC2 prerequisites

```bash
sudo apt update && sudo apt install -y python3 python3-pip python3-venv git
curl -LsSf https://astral.sh/uv/install.sh | sh
source $HOME/.local/bin/env
```

### 2. Clone repos for Git MCP context

The bot reads git history from local clones. Clone each repo into `~/`:

```bash
cd ~
git clone git@bitbucket.org:academytools/dora-learner-3.git dora
git clone git@bitbucket.org:academytools/maps-learner-3.git maps
git clone git@bitbucket.org:academytools/boots-learner-3.git boots
git clone git@bitbucket.org:academytools/procal-infra-3.git procal-infra
```

These are read-only — the MCP git server reads the local `.git` directory regardless of the remote origin.

### 3. Install Python dependencies

```bash
cd ~/ops-bot
python3 -m venv venv
venv/bin/pip install -r requirements.txt
```

### 4. Create `.env`

```env
# LLM proxy
LLM_PROXY_KEY=sk-...
LLM_PROXY_URL=https://llm.devopsinstitute.id

# Telegram
TELEGRAM_BOT_TOKEN=...

# Grafana
GRAFANA_URL=https://grafana.ch3-group3.devopsinstitute.id
GRAFANA_SERVICE_ACCOUNT_TOKEN=glsa_...

# Bitbucket (use your account email, not username)
BITBUCKET_USER=your@email.com
BITBUCKET_APP_PASSWORD=...
BITBUCKET_WORKSPACE=academytools

# Admin panel
ADMIN_USERNAME=admin
ADMIN_PASSWORD=<strong-password>   # required — bot refuses to start without this
ADMIN_PORT=8080
```

#### Getting each value

| Variable | Where to get it |
|----------|----------------|
| `LLM_PROXY_KEY` | From your mentor |
| `TELEGRAM_BOT_TOKEN` | [@BotFather](https://t.me/BotFather) → `/newbot` |
| `GRAFANA_URL` | Your Grafana instance URL |
| `GRAFANA_SERVICE_ACCOUNT_TOKEN` | Grafana → Administration → Service accounts → Add token (Viewer role) |
| `BITBUCKET_USER` | Your Bitbucket account **email** (not username) |
| `BITBUCKET_APP_PASSWORD` | Bitbucket → Personal settings → App passwords → Create (needs: Repositories read/write, Pull requests read/write) |

### 5. IAM role

Attach an IAM role to the EC2 instance with:
- `ec2:Describe*`
- `inspector2:ListFindings`, `inspector2:GetFindingsStatistics`
- `cloudwatch:GetMetricData`, `cloudwatch:DescribeAlarms`
- `logs:DescribeLogGroups`, `logs:GetLogEvents`, `logs:FilterLogEvents`

### 6. Run as systemd services

Service unit files are in `deploy/`. The setup script installs both:

```bash
bash ~/ops-bot/scripts/setup-server.sh
```

This installs `deploy/ops-bot.service` and `deploy/ops-bot-admin.service`, enables them, and starts both.

To do it manually:

```bash
sudo cp ~/ops-bot/deploy/ops-bot.service       /etc/systemd/system/
sudo cp ~/ops-bot/deploy/ops-bot-admin.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now ops-bot ops-bot-admin
```

### 7. Restart after updates

```bash
# bot only
sudo systemctl restart ops-bot

# admin panel only
sudo systemctl restart ops-bot-admin

# if restart hangs (old process holds socket)
pkill -f bot.py && sudo systemctl start ops-bot
```

---

## File reference

**Application**

| File | Purpose |
|------|---------|
| `bot.py` | Entry point. Builds the Telegram app, registers handlers, seeds config on first run |
| `bot_config.py` | Loads/saves `~/.ops-bot-config.json`. Source of truth for allowed chats, thread IDs, and per-chat tool groups |
| `admin_panel.py` | FastAPI web UI (port 8080). Login, chat management, per-chat tool toggles |
| `config.py` | Static constants and env vars: MCP server paths, poll interval, audit settings |
| `mcp_client.py` | Connects to Grafana and Git MCP servers at startup. Populates `all_tools`, `tool_to_session`, `tool_group` |
| `claude_client.py` | `ask_claude()` — Claude API loop with tool dispatch, per-user history, both system prompts |
| `audit.py` | Appends to `~/.ops-bot-audit.log`. Sends Telegram notifications for write-action tool calls |
| `polling.py` | Background asyncio task watching `procal-infra-3` → `main` PRs every 5 min |
| `tools/__init__.py` | Unified tool registry: `ALL_TOOLS`, `BITBUCKET_TOOL_NAMES`, `run_tool()` dispatcher |
| `tools/aws.py` | boto3 EC2 + Inspector: `AWS_TOOLS` schemas and executor |
| `tools/bitbucket.py` | Bitbucket REST API + `BITBUCKET_TOOLS` schemas: PR CRUD, comments, branch creation, file commits |
| `templates/admin.html` | Admin panel SPA — all HTML, CSS, and JavaScript |
| `tg/handlers.py` | `handle_message` routing: allowlist, mention/reply detection, inline command dispatch |
| `tg/formatting.py` | Markdown→Telegram HTML, message splitter (4096-char limit), rate-limit parser |

**Deployment**

| File | Purpose |
|------|---------|
| `deploy/ops-bot.service` | systemd unit for the Telegram bot |
| `deploy/ops-bot-admin.service` | systemd unit for the admin panel |
| `scripts/setup-server.sh` | One-time EC2 setup: installs deps, registers both systemd units, configures logrotate |
| `scripts/list_tools.py` | Dev utility: lists all tools available from the Grafana MCP server |

---

## Troubleshooting

| Problem | Cause | Fix |
|---------|-------|-----|
| `systemctl restart` hangs for 90s | Old process holds Telegram polling socket | `pkill -f bot.py; sudo systemctl start ops-bot` |
| `401 AuthenticationError` | Wrong or expired LLM proxy key | Update `LLM_PROXY_KEY` in `.env` |
| `400 Bitbucket auth error` | Using username instead of email | Set `BITBUCKET_USER` to your account **email** |
| MCP server times out | `uvx` subprocess hangs on init | Skipped after 30s. Check `~/.local/bin/uvx` exists |
| Bot not responding in group | Wrong chat/thread ID, or not @mentioned | Use `/chatid` to verify; add the chat via the admin panel at `:8080` |
| `NoCredentialsError` from boto3 | No IAM role attached to EC2 | AWS Console → EC2 → Actions → Security → Modify IAM role |
| `429 Rate limited` | Shared 100k tokens/min class quota exhausted | Bot retries automatically (3×). Wait for reset or get a personal API key |
| PR polling not triggering review | Bot restarted within 5-min poll window | Wait 5 min, or push a new commit to the PR |
