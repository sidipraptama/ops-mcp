import asyncio
from contextlib import AsyncExitStack

from mcp import ClientSession
from mcp.client.stdio import stdio_client

from config import MCP_SERVERS, GRAFANA_TOOLS_WHITELIST, GIT_TOOLS_WHITELIST
from tools import ALL_TOOLS, BITBUCKET_TOOL_NAMES

# Mutable state — populated by init_mcp(), reused across all requests
all_tools: list[dict] = list(ALL_TOOLS)
tool_to_session: dict[str, tuple] = {}   # {tool_key: (session, original_name)}
tool_group: dict[str, str] = {}          # {tool_key: group_name}
_mcp_stack: AsyncExitStack | None = None

# Tag local tools with their group at module load
for _t in ALL_TOOLS:
    tool_group[_t["name"]] = "bitbucket" if _t["name"] in BITBUCKET_TOOL_NAMES else "aws"


def _slim_schema(input_schema: dict) -> dict:
    """Truncate param descriptions to reduce token overhead per tool call."""
    props = input_schema.get("properties") or {}
    slim_props = {}
    for prop_name, prop_val in props.items():
        slim = {"type": prop_val.get("type", "string")}
        if "enum" in prop_val:
            slim["enum"] = prop_val["enum"]
        if "description" in prop_val:
            slim["description"] = prop_val["description"][:60]
        slim_props[prop_name] = slim
    return {
        "type": "object",
        "properties": slim_props,
        **({"required": input_schema["required"]} if "required" in input_schema else {}),
    }


async def init_mcp() -> None:
    global _mcp_stack
    _mcp_stack = AsyncExitStack()
    await _mcp_stack.__aenter__()

    for name, params in MCP_SERVERS.items():
        is_git = name.startswith("git-")
        repo_prefix = name[len("git-"):] + "_" if is_git else ""
        whitelist = GIT_TOOLS_WHITELIST if is_git else GRAFANA_TOOLS_WHITELIST
        group = name if is_git else "grafana"
        try:
            async def _connect(p=params):
                read, write = await _mcp_stack.enter_async_context(stdio_client(p))
                session = await _mcp_stack.enter_async_context(ClientSession(read, write))
                await session.initialize()
                return session, await session.list_tools()

            session, result = await asyncio.wait_for(_connect(), timeout=30)
            count = 0
            for tool in result.tools:
                if tool.name in whitelist:
                    tool_key = repo_prefix + tool.name
                    desc_prefix = f"[{repo_prefix.rstrip('_')} repo] " if is_git else ""
                    all_tools.append({
                        "name": tool_key,
                        "description": (desc_prefix + (tool.description or ""))[:120],
                        "input_schema": _slim_schema(tool.inputSchema),
                    })
                    tool_to_session[tool_key] = (session, tool.name)
                    tool_group[tool_key] = group
                    count += 1
            print(f"Connected to {name} MCP — {count}/{len(result.tools)} tools loaded")
        except asyncio.TimeoutError:
            print(f"Warning: {name} MCP timed out after 30s — skipping")
        except Exception as e:
            print(f"Warning: Could not connect to {name} MCP: {e}")

    print(f"Total tools available: {len(all_tools)}")


def filter_tools(enabled_groups: set[str]) -> list[dict]:
    """Return only tools whose group is in enabled_groups."""
    return [t for t in all_tools if tool_group.get(t["name"], "") in enabled_groups]
