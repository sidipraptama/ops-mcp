import json
from datetime import datetime, timezone

from config import AUDIT_LOG_FILE, AUDIT_CHAT_ID, AUDIT_THREAD_ID

# Tools that mutate state — these get a Telegram notification
WRITE_TOOLS: set[str] = {
    "approve_pr",
    "unapprove_pr",
    "request_changes_pr",
    "decline_pr",
    "post_pr_comment",
    "delete_pr_comment",
    "commit_file_to_new_branch",
    "create_pr",
}

_SENSITIVE_KEYS = {"content", "diff", "comment"}  # truncate in log


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _write(line: str) -> None:
    try:
        with open(AUDIT_LOG_FILE, "a") as f:
            f.write(line + "\n")
    except Exception as e:
        import sys
        print(f"[audit] failed to write log: {e}", file=sys.stderr)


def log_message(user_id: int, username: str, text: str) -> None:
    safe = text.replace("\n", " ")
    if len(safe) > 200:
        safe = safe[:200] + "..."
    _write(f'[{_now()}] MSG user={user_id} username={username} text="{safe}"')


def log_tool(user_id: int, username: str, tool: str, inputs: dict) -> None:
    slim = {}
    for k, v in inputs.items():
        slim[k] = (str(v)[:80] + "...") if k in _SENSITIVE_KEYS and len(str(v)) > 80 else v
    _write(f"[{_now()}] TOOL user={user_id} username={username} tool={tool} inputs={json.dumps(slim)}")


def log_tool_error(user_id: int, username: str, tool: str, error: str) -> None:
    _write(f"[{_now()}] ERROR user={user_id} username={username} tool={tool} error={error}")


async def notify_write(bot, user_id: int, username: str, tool: str, inputs: dict) -> None:
    if AUDIT_THREAD_ID is None or bot is None:
        return

    repo = inputs.get("repo", "?")
    pr_id = inputs.get("pr_id", "")
    pr_ref = f" PR #{pr_id}" if pr_id else ""

    descriptions = {
        "approve_pr":               f"✅ approved{pr_ref} in <b>{repo}</b>",
        "unapprove_pr":             f"↩️ unapproved{pr_ref} in <b>{repo}</b>",
        "request_changes_pr":       f"🔄 requested changes on{pr_ref} in <b>{repo}</b>",
        "decline_pr":               f"❌ declined{pr_ref} in <b>{repo}</b>",
        "post_pr_comment":          f"💬 posted comment on{pr_ref} in <b>{repo}</b>",
        "delete_pr_comment":        f"🗑️ deleted comment on{pr_ref} in <b>{repo}</b>",
        "commit_file_to_new_branch": f"📝 committed fix to <code>bot/fix-pr{pr_id}</code> in <b>{repo}</b>",
        "create_pr":                f"🔀 created PR <b>{inputs.get('title', '')}</b> in <b>{repo}</b>",
    }
    action = descriptions.get(tool, tool)

    text = (
        f"🔍 <b>Audit</b>\n"
        f"<b>{username}</b> (id: <code>{user_id}</code>)\n"
        f"{action}"
    )
    try:
        await bot.send_message(
            AUDIT_CHAT_ID, text,
            parse_mode="HTML",
            message_thread_id=AUDIT_THREAD_ID,
        )
    except Exception:
        pass
