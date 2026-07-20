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
        "name": "list_inspector_findings",
        "description": "List vulnerability findings from Amazon Inspector (max 20)",
        "input_schema": {
            "type": "object",
            "properties": {
                "severity": {"type": "string", "description": "Filter by severity: CRITICAL, HIGH, MEDIUM, LOW"},
            },
        },
    },
]


def _run_aws(name: str, inputs: dict) -> str:
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
        criteria = {}
        if inputs.get("severity"):
            criteria["severity"] = [{"comparison": "EQUALS", "value": inputs["severity"]}]
        resp = inspector.list_findings(filterCriteria=criteria, maxResults=20)
        findings = [
            {
                "title": finding.get("title"),
                "severity": finding.get("severity"),
                "description": finding.get("description", "")[:200],
                "resource": finding.get("resources", [{}])[0].get("id", "-"),
            }
            for finding in resp.get("findings", [])
        ]
        return json.dumps(findings, indent=2)

    return f"Unknown AWS tool: {name}"
