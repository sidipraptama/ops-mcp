import asyncio
import os

import anthropic

import audit
import bot_config
import mcp_client
from config import MAX_HISTORY
from tools import ALL_TOOLS, BITBUCKET_TOOL_NAMES, run_tool

claude = anthropic.Anthropic(
    api_key=os.getenv("LLM_PROXY_KEY"),
    base_url=os.getenv("LLM_PROXY_URL"),
)

# ── Conversation history ───────────────────────────────────────────────────────
_history: dict[int, list] = {}

# ── Persona mode ──────────────────────────────────────────────────────────────
_tsim: dict[int, bool] = {}


def is_tsim(key: int) -> bool:
    return _tsim.get(key, False)


def set_tsim(key: int, enabled: bool) -> None:
    _tsim[key] = enabled


def clear_history(key: int) -> None:
    _history.pop(key, None)

# ── System prompts ────────────────────────────────────────────────────────────
_SYSTEM_PROMPT_CORE = """DevOps assistant — procal/ch3-group3. Services: dora (FastAPI), maps (Next.js), boots (worker), procal-infra (Terraform).
Never: delete data/branches, force-push, commit to main, trigger builds, approve/deploy unprompted.
Out-of-scope: warn ⚠️, wait for confirmation.
Format: bullet lists or code blocks only — no markdown tables. ISO 8601 timestamps. Concise, always suggest a fix."""

_WORKFLOW_GRAFANA = """
── Grafana ──
list_incidents → query_loki_logs/find_error_pattern_logs → query_prometheus → find_slow_requests
Check git commits for affected service. Summarize: what/when/cause/fix."""

_WORKFLOW_AWS = """
── AWS read-only (ap-southeast-3, Owner=ch3-group3) ──
aws_list_ec2 · aws_security_findings(severity?) · aws_get_cost(month?)
Return aws_get_cost output exactly as received — no reformatting."""

_WORKFLOW_SONARQUBE = """
── SonarQube ──
quality gate → search_sonar_issues_in_projects(BLOCKER/CRITICAL) → get_component_measures
Group by component. BLOCKER/CRITICAL only unless asked."""

_WORKFLOW_JENKINS = """
── Jenkins read-only ──
getJobs → getJob → getBuild/getBuildLog/getTestResults
Include: job name, build #, status, duration."""

_WORKFLOW_BITBUCKET = """
── PR review ──
list_open_prs → get_pr_diff → get_pr_comments → post_pr_comment
Post full analysis to PR. Atlantis: post "atlantis plan"/"atlantis apply". Fix: commit_file_to_new_branch → create_pr (never to main)."""

_WORKFLOW_GIT = """
── Git ──
<repo>_git_log · _git_diff · _git_show · _git_read_file · _git_status"""

TSIM_PROMPT = """You are Tsim. A 30-something Asian girl. Single. Indifferent. Straight to the point.

You have access to all DevOps tools: AWS, Grafana, Git, Bitbucket PRs, Terraform, SonarQube, Jenkins. You do the work. You just don't care about your attitude.

Core behavior:
- NEVER ask for clarification. Make a reasonable assumption and just do it. If they ask about errors in prod, check the last hour. If they don't say which repo, pick the most obvious one. Just go.
- Short answers only. Cut everything that isn't the result.
- No "sure!", no "of course!", no "great question", no "I'd be happy to". Ever.
- Greetings: ignore or one word. "what.", ".", "ok". Not "hi." — that's too friendly.
- If someone is vague or lazy: do it anyway with assumptions, then at the end optionally add one short remark like "next time be specific" or "you could've googled this".
- Swear only when genuinely annoyed — not every message. Vary it: "bro.", "come on.", "seriously lah.", "hah?", "wah."
- If asked too many follow-up questions: "do it yourself.", "just try.", "figure it out lah."
- For weird/philosophical/unanswerable: "no idea.", "random.", "doesn't matter." Use "its destiny" or "just like that" only when asked WHY something philosophical happened — not for practical questions.
- If asked how to turn off: "/tsim_off". Nothing else.
- If asked why you're like this: "i'm just like this." or "always been." Nothing more.

For ops work:
- Just do it. Don't announce what you're about to do. Return the result directly.
- Format: minimal. Bullet points only if there are multiple items.
- If nothing found: "nothing." or "all clear."

Destructive ops: "no.", "not happening."
Out of scope: "not my problem.", "do it yourself."
"""

_LOCAL_TOOL_NAMES = {t["name"] for t in ALL_TOOLS}

_MAX_TOOL_ITERS = 15


def _build_system_prompt(enabled: set[str]) -> str:
    parts = []
    if "grafana" in enabled:
        parts.append("Grafana (Loki logs, Prometheus metrics, Tempo traces, incidents)")
    if "aws" in enabled:
        parts.append("AWS (read-only AWS API — EC2, Security Hub, and other services via call_aws)")
    if "sonarqube" in enabled:
        parts.append("SonarQube (code-quality projects, issues, measures, quality gates)")
    if "jenkins" in enabled:
        parts.append("Jenkins (read-only CI — jobs, builds, build logs, test results)")
    if "bitbucket" in enabled:
        parts.append("Bitbucket (PRs, comments, branches)")
    git_repos = [g.replace("git-", "") for g in bot_config.ALL_TOOL_GROUPS if g.startswith("git-") and g in enabled]
    if git_repos:
        parts.append(f"Git history ({', '.join(git_repos)} repos)")

    if parts:
        tools_line = "You have access to: " + ", ".join(parts) + "."
    else:
        tools_line = "You currently have no tools enabled. Tell the user to enable tools in the admin panel."

    sections = [tools_line, "", _SYSTEM_PROMPT_CORE]
    if "grafana" in enabled:
        sections.append(_WORKFLOW_GRAFANA)
    if "aws" in enabled:
        sections.append(_WORKFLOW_AWS)
    if "sonarqube" in enabled:
        sections.append(_WORKFLOW_SONARQUBE)
    if "jenkins" in enabled:
        sections.append(_WORKFLOW_JENKINS)
    if "bitbucket" in enabled:
        sections.append(_WORKFLOW_BITBUCKET)
    if any(g.startswith("git-") and g in enabled for g in bot_config.ALL_TOOL_GROUPS):
        sections.append(_WORKFLOW_GIT)

    return "\n".join(sections)


def _sanitize_history(messages: list) -> list:
    """Drop leading orphaned tool blocks after a trim.

    History must start with a plain user text message. Trimming can cut the
    assistant tool_use while leaving the user tool_result — this drops those
    dangling blocks so Claude never sees a tool_result without its tool_use.
    """
    while messages:
        m = messages[0]
        if m["role"] == "user" and isinstance(m.get("content"), str):
            break
        messages = messages[1:]
    return messages


async def ask_claude(user_message: str, history_key: int,
                     user_id: int = 0, username: str = "unknown",
                     bot=None, chat_id: int = 0) -> str:
    audit.log_message(user_id, username, user_message)

    history = _history.setdefault(history_key, [])
    history.append({"role": "user", "content": user_message})

    if len(history) > MAX_HISTORY:
        trimmed = _sanitize_history(history[-MAX_HISTORY:])
        _history[history_key] = trimmed
        history = trimmed

    messages = list(history)

    _iter = 0
    while True:
        _iter += 1
        if _iter > _MAX_TOOL_ITERS:
            return "⚠️ Reached tool call limit. Please try a more specific request."

        enabled = bot_config.get_tools_for_chat(chat_id)
        tools = mcp_client.filter_tools(enabled)
        system = TSIM_PROMPT if is_tsim(chat_id) else _build_system_prompt(enabled)

        response = claude.messages.create(
            model="claude-haiku",
            max_tokens=512,
            system=system,
            tools=tools,
            messages=messages,
        )

        if response.stop_reason == "end_turn":
            for block in response.content:
                if hasattr(block, "text"):
                    history.append({"role": "assistant", "content": block.text})
                    return block.text
            return "Done."

        if response.stop_reason == "tool_use":
            messages.append({"role": "assistant", "content": response.content})
            history.append({"role": "assistant", "content": response.content})
            tool_results = []

            for block in response.content:
                if block.type == "tool_use":
                    audit.log_tool(user_id, username, block.name, dict(block.input))
                    try:
                        if block.name in _LOCAL_TOOL_NAMES:
                            result = await asyncio.to_thread(run_tool, block.name, dict(block.input))
                        else:
                            session, original_name = mcp_client.tool_to_session[block.name]
                            result = str((await session.call_tool(original_name, block.input)).content)
                    except Exception as e:
                        audit.log_tool_error(user_id, username, block.name, str(e))
                        result = f"Tool error: {e}"
                    if block.name in audit.WRITE_TOOLS:
                        try:
                            await audit.notify_write(bot, user_id, username, block.name, dict(block.input))
                        except Exception:
                            pass

                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": result,
                    })

            messages.append({"role": "user", "content": tool_results})
            # Store a trimmed copy in history — full results are only needed
            # for the current turn; future turns just need enough context.
            _HIST_LIMIT = 500
            slim_results = [
                {**r, "content": r["content"][:_HIST_LIMIT] + "…[truncated]"}
                if len(r["content"]) > _HIST_LIMIT else r
                for r in tool_results
            ]
            history.append({"role": "user", "content": slim_results})
