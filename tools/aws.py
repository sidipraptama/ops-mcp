import json

import boto3

from .bitbucket import bitbucket_tool

BOTO3_TOOLS = [
    {
        "name": "list_ec2_instances",
        "description": "List EC2 instances with their IPs, state, and tags",
        "input_schema": {
            "type": "object",
            "properties": {
                "env": {"type": "string", "description": "Filter by env tag: dev, staging, production"},
            },
        },
    },
    {
        "name": "list_inspector_findings",
        "description": "List vulnerability findings from Amazon Inspector",
        "input_schema": {
            "type": "object",
            "properties": {
                "severity": {"type": "string", "description": "Filter by severity: CRITICAL, HIGH, MEDIUM, LOW"},
            },
        },
    },
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

_BITBUCKET_TOOL_NAMES = {t["name"] for t in BOTO3_TOOLS if t["name"] not in ("list_ec2_instances", "list_inspector_findings")}


def run_boto3_tool(name: str, inputs: dict) -> str:
    if name == "list_ec2_instances":
        ec2 = boto3.client("ec2", region_name="ap-southeast-3")
        filters = [{"Name": "instance-state-name", "Values": ["running"]}]
        if inputs.get("env"):
            filters.append({"Name": "tag:Env", "Values": [inputs["env"]]})
        resp = ec2.describe_instances(Filters=filters)
        instances = []
        for r in resp["Reservations"]:
            for i in r["Instances"]:
                tags = {t["Key"]: t["Value"] for t in i.get("Tags", [])}
                instances.append({
                    "id": i["InstanceId"],
                    "name": tags.get("Name", "-"),
                    "private_ip": i.get("PrivateIpAddress", "-"),
                    "state": i["State"]["Name"],
                    "type": i["InstanceType"],
                    "env": tags.get("Env", "-"),
                    "app": tags.get("App", "-"),
                })
        return json.dumps(instances, indent=2)

    if name == "list_inspector_findings":
        inspector = boto3.client("inspector2", region_name="ap-southeast-3")
        f = {}
        if inputs.get("severity"):
            f["severity"] = [{"comparison": "EQUALS", "value": inputs["severity"]}]
        resp = inspector.list_findings(filterCriteria=f, maxResults=20)
        findings = [
            {
                "title": f.get("title"),
                "severity": f.get("severity"),
                "description": f.get("description", "")[:200],
                "resource": f.get("resources", [{}])[0].get("id", "-"),
            }
            for f in resp.get("findings", [])
        ]
        return json.dumps(findings, indent=2)

    if name in _BITBUCKET_TOOL_NAMES:
        return bitbucket_tool(name, inputs)

    return "Unknown tool"
