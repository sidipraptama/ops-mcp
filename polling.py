import asyncio
import html
import json
from datetime import datetime, timezone

import bot_config
from claude_client import claude
from config import POLL_REPO, POLL_TARGET_BRANCH, POLL_INTERVAL
from tools.bitbucket import bitbucket_request, bitbucket_tool

import os

_PR_STATE_FILE = os.path.expanduser("~/.ops-bot-pr-state.json")
_pr_seen: dict[str, str] = {}  # {pr_id: commit_sha}
_bot_started_at = datetime.now(timezone.utc)

INFRA_REVIEW_PROMPT = """You are a Terraform infrastructure reviewer.
Analyze this pull request diff targeting the main branch and give a concise review.

Focus on:
- Dangerous changes: resource deletions, replacements, security group changes, IAM changes
- TFLint / Terraform warnings visible in Atlantis plan comments
- Missing variables or undeclared references
- Best practice violations

Format your review as:
🔍 **PR #{pr_id} Auto-Review** (commit {sha})

**Risk level**: 🟢 Low / 🟡 Medium / 🔴 High

**Changes summary**: (1-2 sentences)

**Issues found**: (bullet list, or "None" if clean)

**Recommendation**: Approve / Request changes / Needs Atlantis plan first
"""


def _load_pr_state() -> None:
    global _pr_seen
    try:
        with open(_PR_STATE_FILE) as f:
            _pr_seen = json.load(f)
    except Exception:
        _pr_seen = {}


def _save_pr_state() -> None:
    try:
        with open(_PR_STATE_FILE, "w") as f:
            json.dump(_pr_seen, f)
    except Exception:
        pass


async def _auto_review_infra_pr(bot, pr_id: int, pr_data: dict, commit_sha: str) -> None:
    try:
        diff_json = bitbucket_tool("get_pr_diff", {"repo": POLL_REPO, "pr_id": pr_id})
        diff = json.loads(diff_json)

        prompt = (
            f"PR #{pr_id}: {pr_data['title']}\n"
            f"Author: {pr_data['author']['display_name']}\n"
            f"Branch: {pr_data['source']['branch']['name']} → {POLL_TARGET_BRANCH}\n"
            f"Commit: {commit_sha[:8]}\n\n"
            f"Diff:\n{diff.get('diff', '')}"
        )
        system = INFRA_REVIEW_PROMPT.replace("{pr_id}", str(pr_id)).replace("{sha}", commit_sha[:8])
        response = claude.messages.create(
            model="claude-haiku",
            max_tokens=1024,
            system=system,
            messages=[{"role": "user", "content": prompt}],
        )
        review = response.content[0].text

        bitbucket_tool("post_pr_comment", {"repo": POLL_REPO, "pr_id": pr_id, "comment": review})

        if bot:
            pr_url = pr_data["links"]["html"]["href"]
            notif = (
                f"🤖 <b>Auto-reviewed PR #{pr_id}</b> in {POLL_REPO}\n"
                f"<b>{html.escape(pr_data['title'])}</b>\n"
                f"New commit: <code>{commit_sha[:8]}</code>\n"
                f"<a href='{pr_url}'>View PR</a>"
            )
            for cid, thread_id in bot_config.get_allowed_chats().items():
                await bot.send_message(cid, notif, parse_mode="HTML",
                                       message_thread_id=thread_id)

        print(f"Auto-reviewed PR #{pr_id} (commit {commit_sha[:8]})")
    except Exception as e:
        print(f"Auto-review failed for PR #{pr_id}: {e}")


async def poll_infra_prs(bot) -> None:
    _load_pr_state()
    print(f"PR polling started — watching {POLL_REPO} → {POLL_TARGET_BRANCH} every {POLL_INTERVAL}s")
    await asyncio.sleep(15)  # wait for MCP servers to finish connecting

    while True:
        try:
            data = bitbucket_request(f"{POLL_REPO}/pullrequests?state=OPEN&pagelen=50")
            for pr in data.get("values", []):
                if pr["destination"]["branch"]["name"] != POLL_TARGET_BRANCH:
                    continue

                pr_id = str(pr["id"])
                commit_sha = pr["source"]["commit"]["hash"]

                if pr_id not in _pr_seen:
                    _pr_seen[pr_id] = commit_sha
                    _save_pr_state()
                    pr_created = pr.get("created_on", "")
                    if datetime.fromisoformat(pr_created.replace("Z", "+00:00")) > _bot_started_at:
                        print(f"New PR #{pr_id} opened after bot start — reviewing")
                        await _auto_review_infra_pr(bot, int(pr_id), pr, commit_sha)
                    else:
                        print(f"Tracking existing PR #{pr_id} (sha {commit_sha[:8]})")
                    continue

                if _pr_seen[pr_id] == commit_sha:
                    continue

                _pr_seen[pr_id] = commit_sha
                _save_pr_state()
                await _auto_review_infra_pr(bot, int(pr_id), pr, commit_sha)

        except Exception as e:
            print(f"Poll cycle error: {e}")

        await asyncio.sleep(POLL_INTERVAL)
