import datetime
import boto3
from botocore.exceptions import BotoCoreError, ClientError

_REGION = "ap-southeast-3"
_OWNER_TAG = "ch3-group3"

AWS_TOOLS = [
    {
        "name": "aws_list_ec2",
        "description": "List running EC2 instances owned by ch3-group3 (tag Owner=ch3-group3). Returns name, instance ID, private IP, type, and state.",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "aws_get_cost",
        "description": "Get AWS cost and usage for ch3-group3 this month (or a specific month). Returns total cost per service in USD.",
        "input_schema": {
            "type": "object",
            "properties": {
                "month": {
                    "type": "string",
                    "description": "Month in YYYY-MM format (e.g. 2026-07). Defaults to current month.",
                }
            },
            "required": [],
        },
    },
    {
        "name": "aws_security_findings",
        "description": "Get active Security Hub findings for ch3-group3 resources (tag Owner=ch3-group3). Returns title, severity, resource ARN, and description.",
        "input_schema": {
            "type": "object",
            "properties": {
                "severity": {
                    "type": "string",
                    "description": "Filter by severity: CRITICAL, HIGH, MEDIUM, LOW, INFORMATIONAL. Omit for all.",
                    "enum": ["CRITICAL", "HIGH", "MEDIUM", "LOW", "INFORMATIONAL"],
                }
            },
            "required": [],
        },
    },
]

AWS_TOOL_NAMES = {t["name"] for t in AWS_TOOLS}


def _ec2_client():
    return boto3.client("ec2", region_name=_REGION)


def _sh_client():
    return boto3.client("securityhub", region_name=_REGION)


def _tag_name(instance) -> str:
    for tag in instance.get("Tags", []):
        if tag["Key"] == "Name":
            return tag["Value"]
    return "(no name)"


def aws_tool(name: str, inputs: dict) -> str:
    try:
        if name == "aws_list_ec2":
            return _list_ec2()
        if name == "aws_security_findings":
            return _security_findings(inputs.get("severity"))
        if name == "aws_get_cost":
            return _get_cost(inputs.get("month"))
        return f"Unknown AWS tool: {name}"
    except (BotoCoreError, ClientError) as e:
        return f"AWS error: {e}"
    except Exception as e:
        return f"Error: {e}"


def _get_cost(month: str | None) -> str:
    # Cost Explorer is always us-east-1 regardless of resource region
    ce = boto3.client("ce", region_name="us-east-1")
    today = datetime.date.today()
    if month:
        try:
            start = datetime.datetime.strptime(month, "%Y-%m").date().replace(day=1)
        except ValueError:
            return "Invalid month format. Use YYYY-MM (e.g. 2026-07)."
    else:
        start = today.replace(day=1)
    # end = first day of next month, capped at today
    if start.month == 12:
        end = start.replace(year=start.year + 1, month=1)
    else:
        end = start.replace(month=start.month + 1)
    end = min(end, today)
    resp = ce.get_cost_and_usage(
        TimePeriod={"Start": start.isoformat(), "End": end.isoformat()},
        Granularity="MONTHLY",
        Metrics=["BlendedCost"],
        Filter={"Tags": {"Key": "Owner", "Values": [_OWNER_TAG]}},
        GroupBy=[{"Type": "DIMENSION", "Key": "SERVICE"}],
    )
    results = resp.get("ResultsByTime", [])
    if not results:
        return "No cost data found."
    total = 0.0
    lines = []
    entries = []
    for group in results[0].get("Groups", []):
        svc = group["Keys"][0]
        amount = float(group["Metrics"]["BlendedCost"]["Amount"])
        if amount > 0.001:
            entries.append((svc, amount))
            total += amount
    max_len = max((len(s) for s, _ in entries), default=20)
    lines = [f"  {svc:<{max_len}}  ${amt:>8.2f}" for svc, amt in entries]
    if not lines:
        return f"No costs found for Owner={_OWNER_TAG} in {start.strftime('%B %Y')}. Check that cost allocation tags are enabled in Billing settings."
    lines.sort(key=lambda x: float(x.split("$")[1]), reverse=True)
    # Return as preformatted block so the bot wraps it in ``` for Telegram
    header = f"AWS Cost — {start.strftime('%B %Y')} (Owner={_OWNER_TAG})"
    rows = "\n".join(lines)
    return f"```\n{header}\n{'-'*len(header)}\n{rows}\nTotal: ${total:.2f} USD\n```"


def _list_ec2() -> str:
    ec2 = _ec2_client()
    resp = ec2.describe_instances(
        Filters=[
            {"Name": "tag:Owner", "Values": [_OWNER_TAG]},
            {"Name": "instance-state-name", "Values": ["running"]},
        ]
    )
    instances = [i for r in resp["Reservations"] for i in r["Instances"]]
    if not instances:
        return "No running EC2 instances found for Owner=ch3-group3."
    lines = []
    for i in instances:
        lines.append(
            f"- {_tag_name(i)} | {i['InstanceId']} | {i.get('PrivateIpAddress','?')} | {i['InstanceType']} | {i['State']['Name']}"
        )
    return "\n".join(lines)


def _security_findings(severity: str | None) -> str:
    sh = _sh_client()
    # AWS-generated findings don't carry custom resource tags, so tag filtering
    # returns nothing. This account is ch3-group3 only, so no tag filter needed.
    # Exclude ARCHIVED + PASSED to surface only actionable findings.
    filters: dict = {
        "WorkflowStatus": [
            {"Value": "NEW", "Comparison": "EQUALS"},
            {"Value": "NOTIFIED", "Comparison": "EQUALS"},
        ],
        "ComplianceStatus": [{"Value": "FAILED", "Comparison": "EQUALS"}],
    }
    if severity:
        filters["SeverityLabel"] = [{"Value": severity, "Comparison": "EQUALS"}]
    resp = sh.get_findings(Filters=filters, MaxResults=20)
    findings = resp.get("Findings", [])
    if not findings:
        return "No active Security Hub findings for Owner=ch3-group3."
    lines = []
    for f in findings:
        sev = f.get("Severity", {}).get("Label", "?")
        title = f.get("Title", "?")
        resource = f.get("Resources", [{}])[0].get("Id", "?")
        lines.append(f"[{sev}] {title}\n  Resource: {resource}")
    return "\n\n".join(lines)
