from .aws import AWS_TOOLS, _run_aws
from .bitbucket import BITBUCKET_TOOLS, bitbucket_tool

ALL_TOOLS = AWS_TOOLS + BITBUCKET_TOOLS
BITBUCKET_TOOL_NAMES = {t["name"] for t in BITBUCKET_TOOLS}


def run_tool(name: str, inputs: dict) -> str:
    if name in BITBUCKET_TOOL_NAMES:
        return bitbucket_tool(name, inputs)
    return _run_aws(name, inputs)
