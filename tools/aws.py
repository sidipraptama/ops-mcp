import json

import boto3


AWS_TOOLS = [
    {
        "name": "list_ec2_instances",
        "description": "List running EC2 instances with IPs, state, and tags",
        "input_schema": {
            "type": "object",
            "properties": {
                "env": {"type": "string", "description": "Filter by env tag: dev, staging, production"},
            },
        },
    },
    {
        "name": "list_security_findings",
        "description": "List active security findings from AWS Security Hub (max 20)",
        "input_schema": {
            "type": "object",
            "properties": {
                "severity": {"type": "string", "description": "Filter by severity: CRITICAL, HIGH, MEDIUM, LOW, INFORMATIONAL"},
            },
        },
    },
]


_OWNER_TAG = "ch3-group3"


def _run_aws(name: str, inputs: dict) -> str:
    if name == "list_ec2_instances":
        ec2 = boto3.client("ec2", region_name="ap-southeast-3")
        filters = [
            {"Name": "instance-state-name", "Values": ["running"]},
            {"Name": "tag:Owner", "Values": [_OWNER_TAG]},
        ]
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

    if name == "list_security_findings":
        hub = boto3.client("securityhub", region_name="ap-southeast-3")
        filters = {
            "ResourceTags": [{"Key": "Owner", "Value": _OWNER_TAG, "Comparison": "EQUALS"}],
            "RecordState":  [{"Value": "ACTIVE",   "Comparison": "EQUALS"}],
            "WorkflowStatus": [
                {"Value": "NEW",      "Comparison": "EQUALS"},
                {"Value": "NOTIFIED", "Comparison": "EQUALS"},
            ],
        }
        if inputs.get("severity"):
            filters["SeverityLabel"] = [{"Value": inputs["severity"].upper(), "Comparison": "EQUALS"}]
        resp = hub.get_findings(Filters=filters, MaxResults=20)
        findings = [
            {
                "title":    f.get("Title"),
                "severity": f.get("Severity", {}).get("Label"),
                "source":   f.get("ProductName", "-"),
                "description": f.get("Description", "")[:200],
                "resource": f.get("Resources", [{}])[0].get("Id", "-"),
            }
            for f in resp.get("Findings", [])
        ]
        return json.dumps(findings, indent=2)

    return f"Unknown AWS tool: {name}"
