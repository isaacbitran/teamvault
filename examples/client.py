"""Minimal MCP client for a teamvault server: lists the tools and calls one.

  uv run python examples/client.py <server-url> <token> [tool] [json-args]
  uv run python examples/client.py http://127.0.0.1:8790 tv_... team_search '{"query": "flour"}'
"""
import asyncio
import json
import sys

import httpx2
from mcp.client.session import ClientSession
from mcp.client.streamable_http import streamable_http_client


async def call(url: str, token: str, tool: str | None = None, args: dict | None = None) -> tuple[list[str], str]:
    async with httpx2.AsyncClient(headers={"Authorization": f"Bearer {token}"}, timeout=30) as http:
        async with streamable_http_client(url.rstrip("/") + "/mcp", http_client=http) as (read, write, *_):
            async with ClientSession(read, write) as session:
                await session.initialize()
                tools = [t.name for t in (await session.list_tools()).tools]
                text = ""
                if tool:
                    res = await session.call_tool(tool, args or {})
                    text = "\n".join(getattr(c, "text", "") for c in res.content)
                    if res.is_error:
                        text = "ERROR: " + text
                return tools, text


if __name__ == "__main__":
    url, token = sys.argv[1], sys.argv[2]
    tool = sys.argv[3] if len(sys.argv) > 3 else None
    args = json.loads(sys.argv[4]) if len(sys.argv) > 4 else {}
    tools, text = asyncio.run(call(url, token, tool, args))
    print("tools:", ", ".join(tools))
    if text:
        print(text)
