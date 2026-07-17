# Procal Ops Bot

A Telegram bot powered by Claude AI for DevOps incident triage, PR review, and infrastructure management. Lives in a Telegram group topic and responds only when mentioned.

---

## Architecture

```
Telegram Group (topic thread #4)
    ↓  @mention or reply
EC2 (procal-ops) — private subnet + NAT Gateway
    ├── bot.py               ← entry point: Telegram app + startup
    ├── config.py            ← all env vars and constants
    ├── mcp_client.py        ← MCP session management (Grafana + Git)
    ├── claude_client.py     ← Claude API, conversation history, prompts
    ├── polling.py           ← auto PR review loop (infra repo → main)
    ├── tools/
    │   ├── aws.py           ← EC2, Inspector (boto3) + tool schema definitions
    │   └── bitbucket.py     ← Bitbucket REST API (PRs, comments, branches)
    └── tg/
        ├── handlers.py      ← message routing and command handlers
        └── formatting.py    ← markdown→HTML, message splitting, rate-limit parsing
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
| `tools/aws.py` | EC2, Inspector, all Bitbucket actions | ~14 |
| Grafana MCP server | Loki logs, Prometheus, incidents, traces | 7 |
| Git MCP servers (×4) | `git_log`, `git_diff`, `git_show`, `git_read_file`, `git_status` per repo | 20 |

Git tools are prefixed by repo: `dora_git_log`, `maps_git_diff`, `infra_git_read_file`, etc.

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

- The bot only responds in chat `-1004269056589`, topic thread `4`
- In groups, it responds only when **@mentioned** or when someone **replies** to one of its messages
- When replying, the bot includes the original message as context for Claude
- Each user in the group has their own conversation history (keyed by user ID)

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
source venv/bin/activate
pip install anthropic boto3 mcp python-telegram-bot python-dotenv
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

### 6. Run as systemd service

```bash
sudo nano /etc/systemd/system/ops-bot.service
```

```ini
[Unit]
Description=Procal Ops Bot
After=network.target

[Service]
User=ubuntu
WorkingDirectory=/home/ubuntu/ops-bot
EnvironmentFile=/home/ubuntu/ops-bot/.env
ExecStart=/home/ubuntu/ops-bot/venv/bin/python bot.py
Restart=always
RestartSec=5
TimeoutStopSec=5

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable ops-bot
sudo systemctl start ops-bot
sudo systemctl status ops-bot
```

### 7. Restart after updates

```bash
# fast kill + restart
pkill -f bot.py; sudo systemctl start ops-bot

# or if TimeoutStopSec=5 is set in the service file
sudo systemctl restart ops-bot
```

---

## File reference

| File | Purpose |
|------|---------|
| `bot.py` | Entry point. Builds the Telegram app, registers handlers, calls `post_init` on startup |
| `config.py` | All constants and env vars. Edit here to change allowed chat IDs, poll interval, MCP server paths, etc. |
| `mcp_client.py` | Connects to Grafana and Git MCP servers at startup. Populates `all_tools` and `tool_to_session` globals used by `claude_client` |
| `claude_client.py` | `ask_claude()` — the main Claude API call loop with tool dispatch. Manages per-user conversation history and holds both system prompts |
| `polling.py` | Background asyncio task that watches `procal-infra-3` PRs and auto-reviews new commits |
| `tools/aws.py` | boto3 EC2 + Inspector queries. Also defines `BOTO3_TOOLS` — the schema list that Claude sees for all local tools |
| `tools/bitbucket.py` | All Bitbucket REST API calls: PR CRUD, comments, branch creation, file commits |
| `tg/handlers.py` | `handle_message` routing (allowlist, mention/reply detection, inline command dispatch). All slash command handlers |
| `tg/formatting.py` | Markdown→Telegram HTML converter, message splitter for 4096-char limit, rate-limit reset-time parser |

---

## Troubleshooting

| Problem | Cause | Fix |
|---------|-------|-----|
| `systemctl restart` hangs for 90s | Old process holds Telegram polling socket | `pkill -f bot.py; sudo systemctl start ops-bot` |
| `401 AuthenticationError` | Wrong or expired LLM proxy key | Update `LLM_PROXY_KEY` in `.env` |
| `400 Bitbucket auth error` | Using username instead of email | Set `BITBUCKET_USER` to your account **email** |
| MCP server times out | `uvx` subprocess hangs on init | Skipped after 30s. Check `~/.local/bin/uvx` exists |
| Bot not responding in group | Wrong chat/thread ID, or not @mentioned | Use `/chatid` to verify; update `ALLOWED_CHAT_IDS` / `ALLOWED_THREAD_ID` in `config.py` |
| `NoCredentialsError` from boto3 | No IAM role attached to EC2 | AWS Console → EC2 → Actions → Security → Modify IAM role |
| `429 Rate limited` | Shared 100k tokens/min class quota exhausted | Bot retries automatically (3×). Wait for reset or get a personal API key |
| PR polling not triggering review | Bot restarted within 5-min poll window | Wait 5 min, or push a new commit to the PR |
