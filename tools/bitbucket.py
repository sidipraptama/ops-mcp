import json
import os
import uuid
import urllib.request
from base64 import b64encode

from config import BITBUCKET_WORKSPACE, BITBUCKET_USER, BITBUCKET_APP_PASSWORD


def _auth_token() -> str:
    return b64encode(f"{BITBUCKET_USER}:{BITBUCKET_APP_PASSWORD}".encode()).decode()


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
    token = _auth_token()
    url = f"https://api.bitbucket.org/2.0/repositories/{BITBUCKET_WORKSPACE}/{path}"
    headers = {"Authorization": f"Basic {token}", "Accept": "application/json"}
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
        token = _auth_token()
        diff_url = (
            f"https://api.bitbucket.org/2.0/repositories/{BITBUCKET_WORKSPACE}"
            f"/{repo}/pullrequests/{pr_id}/diff"
        )
        req = urllib.request.Request(diff_url, headers={"Authorization": f"Basic {token}"})
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
        except Exception:
            branch_data = bitbucket_request(f"{repo}/refs/branches/{source_branch}")
            fields["parents"] = branch_data["target"]["hash"]
            is_new = True

        body, content_type = _build_multipart(fields)
        token = _auth_token()
        url = (
            f"https://api.bitbucket.org/2.0/repositories/{BITBUCKET_WORKSPACE}/{repo}/src"
        )
        req = urllib.request.Request(
            url, data=body, method="POST",
            headers={"Authorization": f"Basic {token}", "Content-Type": content_type},
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
