"""MCP handshake/discovery/read/prompt/tool checks against a real stdio subprocess."""
import argparse
import asyncio
import json
import os
from pathlib import Path
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


async def check(write=False):
    parameters = StdioServerParameters(command=sys.executable,
        args=[str(Path(__file__).with_name("mcp_server.py"))], env=dict(os.environ))
    async with stdio_client(parameters) as (read, write_stream):
        async with ClientSession(read, write_stream) as session:
            initialized = await session.initialize()
            print("NEGOTIATED MCP", initialized.protocolVersion)
            tools = await session.list_tools()
            assert {t.name for t in tools.tools} == {"find_user", "get_report", "create_session", "get_session"}
            print("TOOLS", [t.name for t in tools.tools])
            resources = await session.list_resources()
            assert len(resources.resources) == 2
            print("RESOURCES", [str(r.uri) for r in resources.resources])
            prompts = await session.list_prompts()
            assert [p.name for p in prompts.prompts] == ["plan_session"]
            policy = await session.read_resource("timetracker://policy")
            assert policy.contents
            current = await session.read_resource("timetracker://reports/5")
            assert json.loads(current.contents[0].text)["user_id"] == 5
            prompt = await session.get_prompt("plan_session", {"email": "student5@example.com"})
            assert prompt.messages
            print("PROMPT", prompt.model_dump_json())
            user = await session.call_tool("find_user", {"email": "student5@example.com"})
            assert not user.isError, user
            report = await session.call_tool("get_report", {"user_id": 5})
            assert not report.isError, report
            print("REPORT", report.model_dump_json())
            denied = await session.call_tool("create_session", {"user_id": 6, "duration_seconds": 600})
            assert denied.isError
            print("GUARDRAIL", denied.model_dump_json())
            boolean = await session.call_tool("create_session", {"user_id": 5, "duration_seconds": True})
            assert boolean.isError, "Boolean duration must not be coerced to an integer"
            if write:
                created = await session.call_tool("create_session", {"user_id": 5, "duration_seconds": 600})
                assert not created.isError, created
                data = json.loads(created.content[0].text)
                status = await session.call_tool("get_session", {"session_id": data["id"]})
                assert not status.isError, status
                print("CREATED", created.model_dump_json())
            print("MCP CHECK PASSED")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write", action="store_true", help="Also create a real session")
    asyncio.run(check(parser.parse_args().write))
