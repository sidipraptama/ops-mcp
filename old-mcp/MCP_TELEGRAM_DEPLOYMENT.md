# Deployment — Telegram MCP Bot (SonarQube + AWS + Jenkins + Bitbucket + LLM Proxy) on EC2

Deploy a **Telegram bot** to an Ubuntu 24.04 EC2 instance that answers code-quality, infrastructure, CI, and repository questions by driving an LLM — the learner **Claude API proxy** from `LLM_PROXY_GUIDE.md` (`https://llm.devopsinstitute.id`, OpenAI-compatible, LiteLLM) — which calls **SonarQube MCP**, **read-only AWS MCP**, **Jenkins MCP**, and **read-only Bitbucket MCP** tools. The bot is the MCP _client_ and Telegram frontend; the LLM decides the tool calls.

Four MCP servers, two transports:

- **SonarQube** — Docker container the bot spawns, **stdio**
- **AWS API** — `uvx` process the bot spawns, **stdio**
- **Bitbucket** — `npx` process the bot spawns (community `bitbucket-mcp`, security-reviewed §6), **stdio**
- **Jenkins** — the `mcp-server` **plugin inside Jenkins itself**, reached over **Streamable HTTP**; nothing to spawn

```
Telegram user
   │ (long polling — no inbound ports needed)
   ▼
Bot service (Python, systemd)  ──────► LLM proxy  https://llm.devopsinstitute.id
   │     │      │      │                    │ (Bearer key, $10/day budget)
   │     │      │      │                    ▼ decides tool calls
   │     │      │      └──► Jenkins MCP plugin (HTTP: <jenkins>/mcp-server/mcp, Basic auth)
   │     │      └──► Bitbucket MCP (npx, stdio, read-only app password) → api.bitbucket.org
   │     └──► AWS API MCP (uvx, stdio, READ-ONLY, creds via instance role)
   ▼
SonarQube MCP server (Docker, stdio)
   │
   ▼
SonarQube Community  →  https://sonarqube.ch3-group3.devopsinstitute.id
```

## 1. Prerequisites

- **AWS** — account + key pair for SSH.
- **Telegram bot token** — from **@BotFather** (`/newbot`).
- **Your Telegram numeric user ID** — from **@userinfobot**.
- **LLM proxy key** — your personal `sk-…` key from your mentor; see `LLM_PROXY_GUIDE.md` for models, budget, rate limits. All three `claude-*` models support function calling; use `claude-haiku` to iterate, `claude-sonnet` when tool-call quality matters (AWS/Jenkins tools especially).
- **SonarQube user token** — Server: _My Account → Security → Generate Token_. Prefer a read-only token scoped to the projects you need. See `SONARQUBE_MCP_DEPLOYMENT.md`.
- **For AWS tools** — the bot EC2's **instance role** with `AmazonEC2ReadOnlyAccess` (or broader `ReadOnlyAccess`) attached. IAM permissions alone don't give the LLM tools — the AWS MCP server (§4) does; the role is just its credentials. AWS _Inspector_ policies are unrelated.
- **For Jenkins tools** — Jenkins **≥ 2.533** with the `mcp-server` plugin (§5), plus a Jenkins user + **API token** for the bot.
- **For Bitbucket tools** — a Bitbucket **app password** with read-only scopes (§6.2), workspace `academytools`.

### EC2 instance

- **Ubuntu Server 24.04 LTS**, `t3.small` or larger — the Java-based SonarQube MCP container wants ~2 GB RAM; `t3.micro` will OOM.
- Storage: 20 GB gp3.

### Security groups

- **Inbound** — `22` from your IP only. Nothing else — long polling means Telegram never connects inbound.
- **Outbound** — `443` for Telegram API, LLM proxy, SonarQube, AWS APIs, Jenkins, and image/package pulls.
- **Jenkins side** — the Jenkins SG must accept HTTP(S) from the bot EC2 (VPC-internal or via VPN DNS).

> Do **not** open `80`/`443` on the bot instance unless you later switch to webhook mode. Polling mode keeps zero public attack surface.

## 2. Instance setup

```bash
ssh -i key.pem ubuntu@<EC2_IP>

sudo apt update && sudo apt upgrade -y
sudo apt install -y python3-venv python3-pip

# Docker
curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker ubuntu
newgrp docker   # or re-login

# uv (provides uvx — runs the AWS MCP server)
# NOTE: installs per-user into ~/.local/bin — run as the SAME user the systemd unit uses
curl -LsSf https://astral.sh/uv/install.sh | sh

# Node.js + npm (provides npx — runs the Bitbucket MCP server)
sudo apt install -y nodejs npm
```

## 3. SonarQube MCP server image

Official image:

```bash
docker pull mcp/sonarqube
```

Quick sanity test (stdio server — starts, waits on stdin, `Ctrl+C` to exit):

```bash
docker run -i --rm \
  -v sonarqube-mcp-storage:/app/storage \
  -e SONARQUBE_TOKEN=<token> \
  -e SONARQUBE_URL=https://sonarqube.ch3-group3.devopsinstitute.id \
  mcp/sonarqube
```

> **The `-v sonarqube-mcp-storage:/app/storage` volume matters.** The server downloads ~12 analyzer plugins at startup; with `--rm` and no volume they're re-downloaded on every bot restart, and tools that depend on analyzers register _after_ the bot has already snapshotted the tool list — so the bot sees fewer tools than the server's "All tools loaded: N" log line. The volume caches the analyzers so subsequent starts are fast and complete.

> The bot spawns this container itself over stdio at startup — no need to keep it running manually, and no port is published.

## 4. AWS API MCP server

Gives the LLM read-only AWS tools (describe EC2 instances, etc.) via the official `awslabs` server.

1. **IAM** — attach `AmazonEC2ReadOnlyAccess` (or `ReadOnlyAccess`) to the bot EC2's instance role. Credentials then flow via IMDS — no keys in `.env`.
2. **Smoke test** (Ctrl+C after it starts):

```bash
uvx awslabs.aws-api-mcp-server@latest
```

> **Security:** this hands AWS API access to an LLM driven by chat messages. Keep the IAM policy read-only, keep `READ_OPERATIONS_ONLY=true` (set in `bot.py`), and keep the Telegram user allowlist tight. Never attach write permissions to this role.

## 5. Jenkins MCP server (plugin)

Unlike the other two, this is **not a process on the bot host** — it's the official [`mcp-server` plugin](https://github.com/jenkinsci/mcp-server-plugin) running _inside Jenkins_, exposing MCP over **Streamable HTTP** at `<jenkins-url>/mcp-server/mcp`. The bot connects as an HTTP client with Basic auth.

Tools exposed: `getJobs`/`getJob`, `triggerBuild`, `getBuild`/`getBuildLog`/`searchBuildLog`, `getTestResults`, `rebuildBuild`, `replayBuild`, SCM lookups (`getBuildChangeSets`, `findJobsWithScmUrl`), `whoAmI`, `getStatus`.

### 5.1 Install the plugin (Jenkins side)

Requires Jenkins **≥ 2.533**.

**Manage Jenkins → Plugins → Available → search `MCP Server` (`mcp-server`) → Install** (plugin page: plugins.jenkins.io/mcp-server). Restart if prompted.

### 5.2 Create the bot's Jenkins account + API token

1. Create a dedicated Jenkins user for the bot (e.g. `mcp-bot`) — don't reuse a human account.
2. Grant it via matrix/role security: **Overall/Read + Job/Read** (add **Job/Build** only if you _want_ chat-triggered builds — see security note).
3. Log in as that user → _account → Security → API Token → Generate_. Save the token — shown once.

> **Security — read this before granting Build/Replay.** The plugin includes `triggerBuild` and `replayBuild`. `replayBuild` re-runs a pipeline **with a modified Groovy script** — that is arbitrary code execution on your Jenkins agents, initiated by whatever the LLM decides from a chat message. With a read-only Jenkins account these tools fail with 403, which is the safe default. Grant Job/Build only deliberately, and Replay ideally never.

### 5.3 Smoke test from the bot EC2

```bash
JENKINS_URL=https://jenkins.ch3-group3.devopsinstitute.id   # adjust to your Jenkins

curl -s $JENKINS_URL/mcp-health          # no auth — plugin alive check

curl -s -u mcp-bot:<api-token> \
  -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"smoke","version":"0"}}}' \
  $JENKINS_URL/mcp-server/mcp            # expect a JSON-RPC result
```

`404` on `/mcp-server/mcp` → plugin not installed/loaded. `401` → bad user/token.

## 6. Bitbucket MCP server

Community server [`MatanYemini/bitbucket-mcp`](https://github.com/MatanYemini/bitbucket-mcp) (npm package `bitbucket-mcp`) — gives the LLM read access to repositories, pull requests, and pipelines in the `academytools` workspace on Bitbucket Cloud. Runs via `npx` as a stdio child of the bot, same pattern as the AWS server.

### 6.1 Security review (v5.0.6, reviewed 2026-07-16)

Third-party (not Atlassian-official), so the GitHub source **and** the published npm tarball were reviewed before adoption:

- **No install hooks** (`preinstall`/`postinstall` absent in repo and tarball) — nothing runs at `npm`/`npx` install time.
- **No telemetry / exfiltration** — zero hardcoded endpoints; the only outbound target is `BITBUCKET_URL` (default `https://api.bitbucket.org/2.0`).
- **No `eval` / `child_process` / obfuscation** in source or published `dist/`.
- **Dependencies**: `@modelcontextprotocol/sdk`, `axios`, `dotenv`, `winston` — all mainstream.
- **Credentials** go only into the `Authorization` header / basic auth; config logging omits token and password. (It does log tool-call arguments to a local winston logfile; `BITBUCKET_LOG_DISABLE=true` turns that off.)
- **Caveat — not read-only by design**: ~40 tools including write ops **enabled by default** (`createPullRequest`, `mergePullRequest`, `approvePullRequest`, `declinePullRequest`, comments…). Only `delete*` tools sit behind `BITBUCKET_ENABLE_DANGEROUS` (off by default). Read-only enforcement therefore comes **entirely from the credential scope** — §6.2 is mandatory, not optional.

> **Pin the reviewed version.** `bot.py` runs `bitbucket-mcp@5.0.6` — the exact tarball reviewed above. Never switch to `@latest`: that would hand your repo credentials to whatever gets published next, unreviewed. Re-run this review before bumping the pin.

### 6.2 Read-only app password (mandatory)

Bitbucket → **Personal settings → App passwords → Create**, label `mcp-bot`, scopes:

- **Repositories: Read**
- **Pull requests: Read**
- **Pipelines: Read**

Nothing else. The Bitbucket API then rejects every write tool with 401/403 no matter what the MCP server exposes.

### 6.3 Smoke test

```bash
BITBUCKET_USERNAME=<your-bitbucket-username> \
BITBUCKET_PASSWORD=<app-password> \
BITBUCKET_WORKSPACE=academytools \
npx -y bitbucket-mcp@5.0.6
# first run downloads the package, then starts and waits on stdin — Ctrl+C to exit
```

> `BITBUCKET_USERNAME` = your Bitbucket _username_ (Personal settings → Account settings), **not** your email — email fails auth with app passwords.

## 7. Bot project

```bash
mkdir -p ~/mcp-bot && cd ~/mcp-bot
python3 -m venv .venv && source .venv/bin/activate
pip install "python-telegram-bot>=21" "openai>=1.40" "mcp>=1.0" python-dotenv
```

`~/mcp-bot/.env` — secrets live here, never in code:

```bash
TELEGRAM_BOT_TOKEN=123456:ABC...
ALLOWED_USER_IDS=11111111,22222222

LLM_BASE_URL=https://llm.devopsinstitute.id/v1
LLM_API_KEY=sk-your-personal-key      # from your mentor — see LLM_PROXY_GUIDE.md
LLM_MODEL=claude-haiku                # or claude-sonnet for better tool use

SONARQUBE_TOKEN=squ_xxx
SONARQUBE_URL=https://sonarqube.ch3-group3.devopsinstitute.id
# SonarQube Cloud would use SONARQUBE_ORG instead — self-hosted uses URL

AWS_REGION=ap-southeast-3

JENKINS_URL=https://jenkins.ch3-group3.devopsinstitute.id   # adjust to your Jenkins
JENKINS_USER=mcp-bot
JENKINS_TOKEN=11xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx          # API token from §5.2

BITBUCKET_USERNAME=your-bitbucket-username    # username, NOT email
BITBUCKET_PASSWORD=ATBBxxxxxxxxxxxxxxxx       # read-only app password from §6.2
BITBUCKET_WORKSPACE=academytools
```

```bash
chmod 600 .env
```

## 8. Bot code

`~/mcp-bot/bot.py`:

```python
import asyncio
import base64
import json
import os
from collections import deque
from contextlib import AsyncExitStack

from dotenv import load_dotenv
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.client.streamable_http import streamablehttp_client
from openai import AsyncOpenAI
from telegram import Update
from telegram.constants import ChatAction, ParseMode
from telegram.error import BadRequest
from telegram.ext import Application, ContextTypes, MessageHandler, filters

load_dotenv()

ALLOWED_IDS = {int(x) for x in os.environ["ALLOWED_USER_IDS"].split(",")}
MODEL = os.environ["LLM_MODEL"]

llm = AsyncOpenAI(
    base_url=os.environ["LLM_BASE_URL"],
    api_key=os.environ["LLM_API_KEY"],
)

docker_env = ["-e", f"SONARQUBE_TOKEN={os.environ['SONARQUBE_TOKEN']}"]
if os.getenv("SONARQUBE_URL"):
    docker_env += ["-e", f"SONARQUBE_URL={os.environ['SONARQUBE_URL']}"]
if os.getenv("SONARQUBE_ORG"):
    docker_env += ["-e", f"SONARQUBE_ORG={os.environ['SONARQUBE_ORG']}"]

STDIO_SERVERS = {
    "sonarqube": StdioServerParameters(
        command="docker",
        args=["run", "-i", "--rm",
              "-v", "sonarqube-mcp-storage:/app/storage",
              *docker_env, "mcp/sonarqube"],
    ),
    "aws": StdioServerParameters(
        command=os.path.expanduser("~/.local/bin/uvx"),
        args=["awslabs.aws-api-mcp-server@latest"],
        env={**os.environ,
             "AWS_REGION": os.getenv("AWS_REGION", "ap-southeast-3"),
             "READ_OPERATIONS_ONLY": "true"},
    ),
    "bitbucket": StdioServerParameters(
        # version pinned = version security-reviewed (§6.1); never @latest
        command="npx",
        args=["-y", "bitbucket-mcp@5.0.6"],
        env={**os.environ,
             "BITBUCKET_WORKSPACE": os.getenv("BITBUCKET_WORKSPACE",
                                              "academytools"),
             "BITBUCKET_LOG_DISABLE": "true"},
    ),
}

JENKINS_AUTH = base64.b64encode(
    f"{os.environ['JENKINS_USER']}:{os.environ['JENKINS_TOKEN']}".encode()
).decode()

HTTP_SERVERS = {
    "jenkins": {
        "url": os.environ["JENKINS_URL"].rstrip("/") + "/mcp-server/mcp",
        "headers": {"Authorization": f"Basic {JENKINS_AUTH}"},
    },
}

tool_to_session: dict[str, ClientSession] = {}
openai_tools: list[dict] = []
mcp_lock = asyncio.Lock()

HISTORY_BUBBLES = 3  # user+assistant bubbles kept as context, per chat
chat_history: dict[int, deque] = {}

SYSTEM_PROMPT = (
    "You are a DevOps assistant with SonarQube code-quality tools, "
    "read-only AWS tools, Jenkins CI tools (jobs, builds, logs, test "
    "results), and read-only Bitbucket tools (repos, pull requests, "
    "pipelines). The Bitbucket workspace is always 'academytools' — "
    "pass it as the workspace argument on every Bitbucket tool call; "
    "never guess another workspace. "
    "When adding a PR comment, the comment text goes in the 'content' "
    "argument; only use 'inline' for line-anchored comments. "
    "Use the tools to answer questions. Keep answers short. "
    "Formatting rules by destination: "
    "(1) Your chat replies use Telegram HTML — <b>bold</b>, <i>italic</i>, "
    "<code>inline code</code>, <pre>code block</pre> — never markdown. "
    "(2) Any text you pass INTO a tool argument (PR comments, "
    "descriptions, etc.) uses plain Markdown — never HTML tags."
)


async def run_agent(chat_id: int, user_text: str) -> str:
    history = chat_history.setdefault(chat_id, deque(maxlen=HISTORY_BUBBLES))
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        *history,  # only final text bubbles — tool traffic never stored
        {"role": "user", "content": user_text},
    ]
    reply = "Stopped: too many tool-call rounds."
    for _ in range(10):  # max tool-call rounds — budget guard, keep it
        resp = await llm.chat.completions.create(
            model=MODEL, messages=messages,
            tools=openai_tools or None,
            max_tokens=1000,  # caps output spend per call (LLM_PROXY_GUIDE.md)
        )
        msg = resp.choices[0].message
        if not msg.tool_calls:
            reply = msg.content or "(empty response)"
            break
        messages.append(msg.model_dump(exclude_none=True))
        for tc in msg.tool_calls:
            args = json.loads(tc.function.arguments or "{}")
            session = tool_to_session.get(tc.function.name)
            if session is None:
                content = f"Unknown tool: {tc.function.name}"
            else:
                async with mcp_lock:
                    result = await session.call_tool(tc.function.name, args)
                content = "\n".join(
                    c.text for c in result.content if c.type == "text"
                )
            messages.append({
                "role": "tool",
                "tool_call_id": tc.id,
                "content": content[:20000],
            })
    history.append({"role": "user", "content": user_text})
    history.append({"role": "assistant", "content": reply})
    return reply


async def on_message(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ALLOWED_IDS:
        return  # silently ignore strangers
    chat = update.effective_chat
    stop_typing = asyncio.Event()

    async def keep_typing():
        # Telegram shows "typing…" ~5s per action — refresh until done
        while not stop_typing.is_set():
            try:
                await ctx.bot.send_chat_action(
                    chat_id=chat.id, action=ChatAction.TYPING
                )
            except Exception as e:
                print(f"typing indicator failed: {e}")  # shows in journal
            try:
                await asyncio.wait_for(stop_typing.wait(), timeout=4)
            except asyncio.TimeoutError:
                pass

    typing_task = asyncio.create_task(keep_typing())
    try:
        reply = await run_agent(chat.id, update.message.text)
    except Exception as e:
        reply = f"Error: {e}"
    finally:
        stop_typing.set()
        await typing_task
    try:
        await update.message.reply_text(
            reply[:4096], parse_mode=ParseMode.HTML
        )
    except BadRequest:
        # LLM emitted broken/unsupported markup — deliver as plain text
        await update.message.reply_text(reply[:4096])


async def register(name: str, session: ClientSession):
    await session.initialize()
    tools = (await session.list_tools()).tools
    for t in tools:
        tool_to_session[t.name] = session
        openai_tools.append({
            "type": "function",
            "function": {
                "name": t.name,
                "description": t.description or "",
                "parameters": t.inputSchema,
            },
        })
    print(f"{name}: {len(tools)} tools")


async def main():
    async with AsyncExitStack() as stack:
        for name, params in STDIO_SERVERS.items():
            read, write = await stack.enter_async_context(stdio_client(params))
            session = await stack.enter_async_context(ClientSession(read, write))
            await register(name, session)
        for name, cfg in HTTP_SERVERS.items():
            read, write, _ = await stack.enter_async_context(
                streamablehttp_client(cfg["url"], headers=cfg["headers"])
            )
            session = await stack.enter_async_context(ClientSession(read, write))
            await register(name, session)
        print(f"MCP ready: {len(openai_tools)} tools total")

        app = Application.builder().token(
            os.environ["TELEGRAM_BOT_TOKEN"]
        ).build()
        app.add_handler(
            MessageHandler(filters.TEXT & ~filters.COMMAND, on_message)
        )
        async with app:
            await app.start()
            await app.updater.start_polling()
            await asyncio.Event().wait()  # run forever


if __name__ == "__main__":
    asyncio.run(main())
```

Test run:

```bash
cd ~/mcp-bot && source .venv/bin/activate
python bot.py
# expect: "sonarqube: N tools", "aws: M tools", "bitbucket: J tools",
#         "jenkins: K tools", "MCP ready: … tools total"
# then in Telegram:
#   "list my sonarqube projects"
#   "describe the EC2 instance named ch3-group3-sonarqube"
#   "what was the result of the last boots-procal dev build?"
#   "show me the failing stage log of the last staging build"
#   "list open pull requests on boots-learner-3"
```

## 9. systemd service

`/etc/systemd/system/mcp-bot.service`:

```ini
[Unit]
Description=Telegram MCP bot (SonarQube + AWS + Jenkins + Bitbucket)
After=network-online.target docker.service
Requires=docker.service

[Service]
User=ubuntu
WorkingDirectory=/home/ubuntu/mcp-bot
ExecStart=/home/ubuntu/mcp-bot/.venv/bin/python bot.py
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
```

> **`User`, `WorkingDirectory`, `ExecStart` must match where the project actually lives.** Installed as root in `/root/mcp-bot`? Then either fix all three paths (`User=root`, `/root/mcp-bot/...`) or move the project to `/home/ubuntu/mcp-bot` — and after moving, **rebuild the venv** (`rm -rf .venv && python3 -m venv .venv && pip install ...`): venvs pin the absolute path they were created at and break silently when moved. Mismatch symptom: `status=203/EXEC`, "Unable to locate executable", infinite restart loop. Same rule for `uvx` — §2 installs it per-user, so it must exist for the systemd `User` (`/home/ubuntu/.local/bin/uvx`).

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now mcp-bot
journalctl -u mcp-bot -f    # watch logs
```

## 10. Hardening checklist

- **`ALLOWED_USER_IDS` is mandatory.** Without it, anyone who finds the bot can drain your **$10/day proxy budget**, read SonarQube data, enumerate AWS resources, and poke Jenkins. The proxy key is personal (`LLM_PROXY_GUIDE.md` ground rules) — a public bot is effectively a shared key.
- **The 10-round cap in `run_agent` is a budget guard** — an uncapped agent loop burns the daily budget in minutes, then just collects 429s. Keep it, and keep `max_tokens` set.
- **AWS access stays read-only** — read-only IAM policy on the instance role _and_ `READ_OPERATIONS_ONLY=true` on the MCP server. Two layers, keep both.
- **Jenkins account stays least-privilege** — Overall/Read + Job/Read only, unless you deliberately want chat-triggered builds. `replayBuild` = pipeline-script execution; never grant Replay to the bot account (§5.2).
- **Bitbucket stays read-only via credential scope** — the community server exposes write tools (merge/approve/decline/comment) by default; the read-only app password is the _only_ guard (§6.1). Never widen its scopes, keep `BITBUCKET_ENABLE_DANGEROUS` unset, keep the version pin at the reviewed release.
- Use a **read-only SonarQube token** scoped to needed projects.
- Better than `.env`: store secrets in **AWS SSM Parameter Store** (SecureString) and fetch at boot via the instance role — no plaintext on disk.
- Enforce **IMDSv2** on the instance; SSH from your IP only.
- `sudo apt install unattended-upgrades` for security patches.

## 11. Troubleshooting

| Symptom                                          | Cause / fix                                                                                                                                                                         |
| ------------------------------------------------ | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Bot silent                                       | User ID not in `ALLOWED_USER_IDS`; check `journalctl -u mcp-bot`                                                                                                                    |
| `McpError: Connection closed` at startup         | A stdio MCP child died at boot — read its stderr above the Python trace. Common: DNS typo in `SONARQUBE_URL` (e.g. `.net` vs `.id`) → Java `UnknownHostException` → container exits |
| `FileNotFoundError: .../uvx`                     | uv not installed **for the service user** — `sudo -u ubuntu bash -c 'curl -LsSf https://astral.sh/uv/install.sh \| sh'`, verify `/home/ubuntu/.local/bin/uvx`                       |
| `status=203/EXEC` + restart loop                 | systemd unit paths don't match install location — §9 note; rebuild venv if the project was moved                                                                                    |
| Fewer SonarQube tools than the server logs claim | Tool list snapshotted before background analyzer download finished — the storage volume (§3) fixes it on the next start                                                             |
| Jenkins: HTTP 404 on `/mcp-server/mcp`           | Plugin not installed / Jenkins < 2.533 — §5.1; check `/mcp-health` first                                                                                                            |
| Jenkins: 401 at startup                          | Wrong `JENKINS_USER`/`JENKINS_TOKEN` — regenerate API token (§5.2)                                                                                                                  |
| Jenkins tools return 403                         | Bot account lacks that permission (e.g. `triggerBuild` without Job/Build) — intentional safe default                                                                                |
| 401 from LLM                                     | Missing/invalid proxy key — check `LLM_API_KEY`                                                                                                                                     |
| 403 from LLM                                     | Model name typo or not allowed for your key — `GET /v1/models` lists what the key can call                                                                                          |
| `429 budget_exceeded`                            | $10/day spent — resets 00:00 UTC (07:00 WIB); switch to `claude-haiku`                                                                                                              |
| 429 (rate limit)                                 | >20 req/min or >100K tokens/min — each bot message can be several LLM calls (tool rounds); slow down                                                                                |
| Model never calls tools                          | Weak model or vague prompt — bump `LLM_MODEL` to `claude-sonnet`                                                                                                                    |
| AWS tools return `AccessDenied`                  | Instance role lacks the read-only policy (§4), or the call is a write op blocked by `READ_OPERATIONS_ONLY` — the block is intentional                                               |
| `bitbucket: 0 tools` / spawn fail                | `npx` missing (§2 Node install) — or first-run package download slow/blocked; test §6.3 manually                                                                                    |
| Bitbucket 401 on every tool                      | `BITBUCKET_USERNAME` is the email (must be username), or app password revoked — §6.2/§6.3                                                                                           |
| Bitbucket write tool fails 401/403               | App password has read-only scopes — intentional (§6.2)                                                                                                                              |
| `docker: permission denied`                      | `usermod -aG docker <user>` then re-login                                                                                                                                           |
| Container OOM-killed                             | Instance too small — bump to `t3.small`/`t3.medium`                                                                                                                                 |

## Design notes / assumptions

- **Polling over webhook** — webhook mode needs public HTTPS (Nginx + certbot or ALB) and an open inbound port; polling needs neither. Switch only if latency at scale matters.
- **LLM proxy** — the learner Claude API from `LLM_PROXY_GUIDE.md` (LiteLLM, OpenAI-compatible). One Telegram message = up to 11 LLM calls (10 tool rounds + final answer), and each round resends the full message history — budget accordingly; check spend via the `x-litellm-key-spend` response header. AWS describe-\* JSON and Jenkins build logs are large — they inflate history fast (the bot truncates tool results at 20K chars; `getBuildLog` supports its own limits too).
- **Mixed transports** — stdio servers (SonarQube, AWS) are child processes of the bot; the Jenkins server lives inside Jenkins and the bot is just an HTTP client (`streamablehttp_client`). Same `ClientSession` API on top of both, so `register()` and routing don't care.
- **Chat memory** — last `HISTORY_BUBBLES` (3) user/assistant bubbles per chat, in-RAM only: lost on bot restart, fine for a helper bot. Only final text is stored — tool calls/results are _not_ replayed into later prompts, which keeps history token-cheap and avoids dangling `tool_call_id` references. Bump the constant for longer memory; every bubble rides along on every LLM call, so it costs budget.
- **Multi-server routing** — tools from all MCP servers merge into one flat list; `tool_to_session` routes each call to its owning server. Name collisions across servers would clobber — fine for SonarQube+AWS+Jenkins+Bitbucket (disjoint names); prefix tool names if adding servers that overlap.
- **Tool-definition overhead** — every registered tool's schema is sent on _every_ LLM call; Bitbucket alone adds ~40. With all four servers the definitions cost real input tokens per message. If budget bites, trim: filter `openai_tools` in `register()` to an allowlist of tools you actually use.
- **One shared lock across MCP sessions** guarded by `mcp_lock` — fine for a small allowlisted user set; split per-session locks if usage grows.
