"""Official MCP SDK, stdio transport. stdout is reserved for the MCP protocol."""
import json
from typing import Annotated

from mcp.server.fastmcp import FastMCP
from pydantic import Field
from time_tools import EMAIL, POLICY, TimeTools, check_prompt

mcp = FastMCP("Time tracker Lab4", instructions=POLICY)
api = TimeTools(request_limit=100)


@mcp.tool()
def find_user(email: str) -> dict:
    """Find the authorized lab user by their email."""
    return api.find_user(email)


@mcp.tool()
def get_report(user_id: Annotated[int, Field(strict=True)]) -> dict:
    """Read the report for authorized user 5."""
    return api.get_report(user_id)


@mcp.tool()
def create_session(user_id: Annotated[int, Field(strict=True)], duration_seconds: Annotated[int, Field(strict=True)]) -> dict:
    """Create at most one bounded session per server process, rechecking the session limit."""
    return api.create_session(user_id, duration_seconds)


@mcp.tool()
def get_session(session_id: Annotated[str, Field(strict=True)]) -> dict:
    """Read a session owned by the authorized lab user."""
    return api.get_session(session_id)


@mcp.resource("timetracker://policy")
def policy() -> str:
    """Read the adapter's authorization and safety policy."""
    return POLICY


@mcp.resource("timetracker://reports/5")
def current_report() -> str:
    """Current report for user 5, fetched from REST on each read."""
    return json.dumps(api.get_report(5))


@mcp.prompt()
def plan_session(email: str = EMAIL) -> str:
    """Template for a report-aware 600-second session."""
    check_prompt(email)
    if email != EMAIL:
        raise ValueError("Only the configured email is allowed")
    return f"Find {email}, read their all-time report, then create one 600-second session if the session limit allows."


if __name__ == "__main__":
    try:
        mcp.run(transport="stdio")
    finally:
        api.close()
