import json
import os
import uuid
import urllib.request
import urllib.error
from base64 import b64encode

from config import BITBUCKET_WORKSPACE, BITBUCKET_USER, BITBUCKET_APP_PASSWORD

BITBUCKET_TOOLS = [
    {
        "name": "list_open_prs",
        "description": "List open pull requests in a Bitbucket repo",
        "input_schema": {
            "type": "object",
            "properties": {
                "repo": {"type": "string", "description": "Repo slug: dora-learner-3, maps-learner-3, boots-learner-3, procal-infra-3"},
            },
            "required": ["repo"],
        },
    },
    {
        "name": "get_pr_diff",
        "description": "Get the diff and description of a specific Bitbucket pull request",
        "input_schema": {
            "type": "object",
            "properties": {
                "repo": {"type": "string", "description": "Repo slug"},
                "pr_id": {"type": "integer", "description": "Pull request ID number"},
            },
            "required": ["repo", "pr_id"],
        },
    },
    {
        "name": "get_pr_comments",
        "description": "Get comments on a Bitbucket PR, including Atlantis plan output posted as comments",
        "input_schema": {
            "type": "object",
            "properties": {
                "repo": {"type": "string", "description": "Repo slug"},
                "pr_id": {"type": "integer", "description": "Pull request ID number"},
            },
            "required": ["repo", "pr_id"],
        },
    },
    {
        "name": "post_pr_comment",
        "description": "Post a comment on a Bitbucket PR. Use this to post review analysis, risk warnings, or trigger Atlantis commands (atlantis plan / atlantis apply).",
        "input_schema": {
            "type": "object",
            "properties": {
                "repo": {"type": "string", "description": "Repo slug"},
                "pr_id": {"type": "integer", "description": "Pull request ID number"},
                "comment": {"type": "string", "description": "Comment text. To trigger Atlantis: 'atlantis plan' or 'atlantis apply'"},
            },
            "required": ["repo", "pr_id", "comment"],
        },
    },
    {
        "name": "approve_pr",
        "description": "Formally approve a Bitbucket pull request",
        "input_schema": {
            "type": "object",
            "properties": {
                "repo": {"type": "string", "description": "Repo slug"},
                "pr_id": {"type": "integer", "description": "Pull request ID number"},
            },
            "required": ["repo", "pr_id"],
        },
    },
    {
        "name": "unapprove_pr",
        "description": "Remove a previous approval from a Bitbucket pull request",
        "input_schema": {
            "type": "object",
            "properties": {
                "repo": {"type": "string", "description": "Repo slug"},
                "pr_id": {"type": "integer", "description": "Pull request ID number"},
            },
            "required": ["repo", "pr_id"],
        },
    },
    {
        "name": "request_changes_pr",
        "description": "Formally request changes on a Bitbucket pull request (blocks merge until resolved)",
        "input_schema": {
            "type": "object",
            "properties": {
                "repo": {"type": "string", "description": "Repo slug"},
                "pr_id": {"type": "integer", "description": "Pull request ID number"},
            },
            "required": ["repo", "pr_id"],
        },
    },
    {
        "name": "decline_pr",
        "description": "Decline and close a Bitbucket pull request without merging",
        "input_schema": {
            "type": "object",
            "properties": {
                "repo": {"type": "string", "description": "Repo slug"},
                "pr_id": {"type": "integer", "description": "Pull request ID number"},
                "reason": {"type": "string", "description": "Optional reason for declining"},
            },
            "required": ["repo", "pr_id"],
        },
    },
    {
        "name": "delete_pr_comment",
        "description": "Delete a specific comment from a Bitbucket pull request",
        "input_schema": {
            "type": "object",
            "properties": {
                "repo": {"type": "string", "description": "Repo slug"},
                "pr_id": {"type": "integer", "description": "Pull request ID number"},
                "comment_id": {"type": "integer", "description": "Comment ID to delete"},
            },
            "required": ["repo", "pr_id", "comment_id"],
        },
    },
    {
        "name": "commit_file_to_new_branch",
        "description": "Commit a file fix to bot/fix-pr{pr_id} branch. Creates the branch if it doesn't exist, reuses it if it does.",
        "input_schema": {
            "type": "object",
            "properties": {
                "repo": {"type": "string", "description": "Repo slug"},
                "pr_id": {"type": "integer", "description": "The PR being fixed — used to name the fix branch bot/fix-pr{pr_id}"},
                "source_branch": {"type": "string", "description": "The PR source branch to fork from if branch doesn't exist yet"},
                "filepath": {"type": "string", "description": "Repo-relative path of the file to write"},
                "content": {"type": "string", "description": "Full new content of the file"},
                "commit_message": {"type": "string", "description": "Commit message"},
            },
            "required": ["repo", "pr_id", "source_branch", "filepath", "content", "commit_message"],
        },
    },
    {
        "name": "create_pr",
        "description": "Create a new Bitbucket pull request from one branch to another",
        "input_schema": {
            "type": "object",
            "properties": {
                "repo": {"type": "string", "description": "Repo slug"},
                "title": {"type": "string", "description": "PR title"},
                "source_branch": {"type": "string", "description": "Branch with the changes"},
                "destination_branch": {"type": "string", "description": "Branch to merge into"},
                "description": {"type": "string", "description": "PR description"},
            },
            "required": ["repo", "title", "source_branch", "destination_branch"],
        },
    },
]

_AUTH_TOKEN = b64encode(f"{BITBUCKET_USER}:{BITBUCKET_APP_PASSWORD}".encode()).decode()


def _build_multipart(fields: dict) -> tuple[bytes, str]:
    """Build multipart/form-data body for the Bitbucket source API."""
    boundary = f"----BotBoundary{uuid.uuid4().hex}"
    lines = []
    for name, value in fields.items():
        lines.append(f"--{boundary}".encode())
        lines.append(f'Content-Disposition: form-data; name="{name}"'.encode())
        lines.append(b"")
        lines.append(value.encode() if isinstance(value, str) else value)
    lines.append(f"--{boundary}--".encode())
    body = b"\r\n".join(lines)
    return body, f"multipart/form-data; boundary={boundary}"


def bitbucket_request(path: str, method: str = "GET", body: bytes = None,
                      content_type: str = "application/json") -> dict:
    url = f"https://api.bitbucket.org/2.0/repositories/{BITBUCKET_WORKSPACE}/{path}"
    headers = {"Authorization": f"Basic {_AUTH_TOKEN}", "Accept": "application/json"}
    if body is not None:
        headers["Content-Type"] = content_type
    req = urllib.request.Request(url, data=body, method=method, headers=headers)
    with urllib.request.urlopen(req, timeout=10) as resp:
        raw = resp.read()
        return json.loads(raw) if raw else {}


def bitbucket_tool(name: str, inputs: dict) -> str:
    repo = inputs.get("repo")

    if name == "list_open_prs":
        data = bitbucket_request(f"{repo}/pullrequests?state=OPEN&pagelen=20")
        prs = [
            {
                "id": pr["id"],
                "title": pr["title"],
                "author": pr["author"]["display_name"],
                "source": pr["source"]["branch"]["name"],
                "destination": pr["destination"]["branch"]["name"],
                "created_on": pr["created_on"],
                "url": pr["links"]["html"]["href"],
            }
            for pr in data.get("values", [])
        ]
        return json.dumps(prs, indent=2)

    if name == "get_pr_diff":
        pr_id = inputs["pr_id"]
        pr = bitbucket_request(f"{repo}/pullrequests/{pr_id}")
        diff_url = (
            f"https://api.bitbucket.org/2.0/repositories/{BITBUCKET_WORKSPACE}"
            f"/{repo}/pullrequests/{pr_id}/diff"
        )
        req = urllib.request.Request(diff_url, headers={"Authorization": f"Basic {_AUTH_TOKEN}"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            diff = resp.read().decode(errors="replace")[:3000]
        return json.dumps({
            "id": pr["id"],
            "title": pr["title"],
            "description": pr.get("description", "")[:500],
            "author": pr["author"]["display_name"],
            "source": pr["source"]["branch"]["name"],
            "destination": pr["destination"]["branch"]["name"],
            "diff": diff,
        }, indent=2)

    if name == "get_pr_comments":
        pr_id = inputs["pr_id"]
        data = bitbucket_request(f"{repo}/pullrequests/{pr_id}/comments?pagelen=50")
        comments = []
        for c in data.get("values", []):
            raw = c.get("content", {}).get("raw", "")
            is_atlantis = any(kw in raw for kw in (
                "Ran Plan", "atlantis plan", "Plan Error", "No changes",
                "terraform plan", "atlantis apply",
            ))
            comments.append({
                "id": c["id"],
                "author": c.get("author", {}).get("display_name", "unknown"),
                "is_atlantis": is_atlantis,
                "created_on": c["created_on"],
                "content": raw[:3000],
            })
        return json.dumps(comments, indent=2)

    if name == "post_pr_comment":
        pr_id = inputs["pr_id"]
        comment = inputs.get("comment", "")
        body = json.dumps({"content": {"raw": comment}}).encode()
        result = bitbucket_request(
            f"{repo}/pullrequests/{pr_id}/comments",
            method="POST", body=body,
        )
        return json.dumps({"status": "posted", "comment_id": result["id"], "comment": comment})

    if name == "approve_pr":
        pr_id = inputs["pr_id"]
        bitbucket_request(f"{repo}/pullrequests/{pr_id}/approve", method="POST", body=b"{}")
        return json.dumps({"status": "approved", "pr_id": pr_id})

    if name == "unapprove_pr":
        pr_id = inputs["pr_id"]
        bitbucket_request(f"{repo}/pullrequests/{pr_id}/approve", method="DELETE")
        return json.dumps({"status": "unapproved", "pr_id": pr_id})

    if name == "request_changes_pr":
        pr_id = inputs["pr_id"]
        bitbucket_request(f"{repo}/pullrequests/{pr_id}/request-changes", method="POST", body=b"{}")
        return json.dumps({"status": "changes_requested", "pr_id": pr_id})

    if name == "decline_pr":
        pr_id = inputs["pr_id"]
        reason = inputs.get("reason", "")
        body = json.dumps({"message": reason}).encode() if reason else b"{}"
        bitbucket_request(f"{repo}/pullrequests/{pr_id}/decline", method="POST", body=body)
        return json.dumps({"status": "declined", "pr_id": pr_id})

    if name == "delete_pr_comment":
        pr_id = inputs["pr_id"]
        comment_id = inputs["comment_id"]
        bitbucket_request(f"{repo}/pullrequests/{pr_id}/comments/{comment_id}", method="DELETE")
        return json.dumps({"status": "deleted", "comment_id": comment_id})

    if name == "commit_file_to_new_branch":
        pr_id = inputs["pr_id"]
        source_branch = inputs["source_branch"]
        filepath = inputs["filepath"]
        content = inputs["content"]
        commit_message = inputs["commit_message"]
        fix_branch = f"bot/fix-pr{pr_id}"

        fields = {"message": commit_message, "branch": fix_branch, filepath: content}
        try:
            bitbucket_request(f"{repo}/refs/branches/{fix_branch}")
            is_new = False
        except urllib.error.HTTPError as e:
            if e.code != 404:
                raise
            branch_data = bitbucket_request(f"{repo}/refs/branches/{source_branch}")
            fields["parents"] = branch_data["target"]["hash"]
            is_new = True

        body, content_type = _build_multipart(fields)
        url = (
            f"https://api.bitbucket.org/2.0/repositories/{BITBUCKET_WORKSPACE}/{repo}/src"
        )
        req = urllib.request.Request(
            url, data=body, method="POST",
            headers={"Authorization": f"Basic {_AUTH_TOKEN}", "Content-Type": content_type},
        )
        with urllib.request.urlopen(req, timeout=15) as r:
            r.read()
        return json.dumps({
            "status": "committed", "fix_branch": fix_branch,
            "is_new_branch": is_new, "file": filepath,
        })

    if name == "create_pr":
        body = json.dumps({
            "title": inputs["title"],
            "description": inputs.get("description", ""),
            "source": {"branch": {"name": inputs["source_branch"]}},
            "destination": {"branch": {"name": inputs["destination_branch"]}},
            "close_source_branch": True,
        }).encode()
        result = bitbucket_request(f"{repo}/pullrequests", method="POST", body=body)
        return json.dumps({
            "status": "created",
            "pr_id": result["id"],
            "title": result["title"],
            "url": result["links"]["html"]["href"],
        })

    return "Unknown Bitbucket tool"
