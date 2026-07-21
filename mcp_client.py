import asyncio
from contextlib import AsyncExitStack

from mcp import ClientSession
from mcp.client.stdio import stdio_client
from mcp.client.streamable_http import streamablehttp_client

from config import (
    MCP_SERVERS,
    HTTP_MCP_SERVERS,
    GRAFANA_TOOLS_WHITELIST,
    GIT_TOOLS_WHITELIST,
    AWS_TOOLS_WHITELIST,
    SONARQUBE_TOOLS_WHITELIST,
    JENKINS_TOOLS_WHITELIST,
)
from tools import ALL_TOOLS

# Mutable state — populated by init_mcp(), reused across all requests
all_tools: list[dict] = list(ALL_TOOLS)
tool_to_session: dict[str, tuple] = {}   # {tool_key: (session, original_name)}
tool_group: dict[str, str] = {}          # {tool_key: group_name}
_mcp_stack: AsyncExitStack | None = None

# Tag local tools with their group at module load. Only Bitbucket is local now
# (AWS moved to the awslabs MCP server), so every local tool is a Bitbucket tool.
for _t in ALL_TOOLS:
    tool_group[_t["name"]] = "bitbucket"


def _server_meta(name: str) -> tuple[set[str], str, str]:
    """Resolve (whitelist, group, tool_key_prefix) for an MCP server name."""
    if name.startswith("git-"):
        prefix = name[len("git-"):] + "_"
        return GIT_TOOLS_WHITELIST, name, prefix
    if name == "grafana":
        return GRAFANA_TOOLS_WHITELIST, "grafana", ""
    if name == "aws":
        return AWS_TOOLS_WHITELIST, "aws", ""
    if name == "sonarqube":
        return SONARQUBE_TOOLS_WHITELIST, "sonarqube", ""
    if name == "jenkins":
        return JENKINS_TOOLS_WHITELIST, "jenkins", ""
    return set(), name, ""


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


def _register(name: str, session: ClientSession, result) -> None:
    """Register whitelisted tools from an initialized session."""
    whitelist, group, prefix = _server_meta(name)
    is_git = name.startswith("git-")
    count = 0
    for tool in result.tools:
        if tool.name in whitelist:
            tool_key = prefix + tool.name
            desc_prefix = f"[{prefix.rstrip('_')} repo] " if is_git else ""
            all_tools.append({
                "name": tool_key,
                "description": (desc_prefix + (tool.description or ""))[:120],
                "input_schema": _slim_schema(tool.inputSchema),
            })
            tool_to_session[tool_key] = (session, tool.name)
            tool_group[tool_key] = group
            count += 1
    print(f"Connected to {name} MCP — {count}/{len(result.tools)} tools loaded")


async def init_mcp() -> None:
    global _mcp_stack
    _mcp_stack = AsyncExitStack()
    await _mcp_stack.__aenter__()

    # ── stdio servers (child processes) ──────────────────────────────────────
    for name, params in MCP_SERVERS.items():
        try:
            async def _connect(p=params):
                read, write = await _mcp_stack.enter_async_context(stdio_client(p))
                session = await _mcp_stack.enter_async_context(ClientSession(read, write))
                await session.initialize()
                return session, await session.list_tools()

            session, result = await asyncio.wait_for(_connect(), timeout=30)
            _register(name, session, result)
        except asyncio.TimeoutError:
            print(f"Warning: {name} MCP timed out after 30s — skipping")
        except Exception as e:
            print(f"Warning: Could not connect to {name} MCP: {e}")

    # ── HTTP servers (Streamable HTTP, e.g. Jenkins plugin) ──────────────────
    for name, cfg in HTTP_MCP_SERVERS.items():
        try:
            async def _connect(c=cfg):
                read, write, _ = await _mcp_stack.enter_async_context(
                    streamablehttp_client(c["url"], headers=c.get("headers"))
                )
                session = await _mcp_stack.enter_async_context(ClientSession(read, write))
                await session.initialize()
                return session, await session.list_tools()

            session, result = await asyncio.wait_for(_connect(), timeout=30)
            _register(name, session, result)
        except asyncio.TimeoutError:
            print(f"Warning: {name} MCP timed out after 30s — skipping")
        except Exception as e:
            print(f"Warning: Could not connect to {name} MCP: {e}")

    print(f"Total tools available: {len(all_tools)}")


def filter_tools(enabled_groups: set[str]) -> list[dict]:
    """Return only tools whose group is in enabled_groups."""
    return [t for t in all_tools if tool_group.get(t["name"], "") in enabled_groups]
