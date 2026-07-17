import asyncio
import os
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from dotenv import load_dotenv

load_dotenv()

async def main():
    params = StdioServerParameters(
        command=os.path.expanduser("~/.local/bin/uvx"),
        args=["mcp-grafana"],
        env={
            "GRAFANA_URL": os.getenv("GRAFANA_URL"),
            "GRAFANA_SERVICE_ACCOUNT_TOKEN": os.getenv("GRAFANA_SERVICE_ACCOUNT_TOKEN"),
            "PATH": os.environ.get("PATH", "")
        }
    )

    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.list_tools()
            print(f"\nTotal tools: {len(result.tools)}\n")
            for tool in result.tools:
                print(f"- {tool.name}")
                print(f"  {tool.description[:100]}")
                print()

asyncio.run(main())
