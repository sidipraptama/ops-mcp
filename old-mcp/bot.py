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
        # version pinned = version security-reviewed (deployment doc §6.1)
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
