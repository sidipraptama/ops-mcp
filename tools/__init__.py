from .bitbucket import BITBUCKET_TOOLS, bitbucket_tool

# AWS is now served by the awslabs AWS API MCP server (see config.MCP_SERVERS),
# not local boto3 tools — only Bitbucket remains as a local (non-MCP) tool set.
ALL_TOOLS = BITBUCKET_TOOLS
BITBUCKET_TOOL_NAMES = {t["name"] for t in BITBUCKET_TOOLS}


def run_tool(name: str, inputs: dict) -> str:
    if name in BITBUCKET_TOOL_NAMES:
        return bitbucket_tool(name, inputs)
    return f"Unknown local tool: {name}"
