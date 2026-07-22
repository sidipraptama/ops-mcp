from .bitbucket import BITBUCKET_TOOLS, bitbucket_tool
from .aws_tools import AWS_TOOLS, AWS_TOOL_NAMES, aws_tool

ALL_TOOLS = BITBUCKET_TOOLS + AWS_TOOLS
BITBUCKET_TOOL_NAMES = {t["name"] for t in BITBUCKET_TOOLS}


def run_tool(name: str, inputs: dict) -> str:
    if name in BITBUCKET_TOOL_NAMES:
        return bitbucket_tool(name, inputs)
    if name in AWS_TOOL_NAMES:
        return aws_tool(name, inputs)
    return f"Unknown local tool: {name}"
