import asyncio

from plex_stack_mcp import server, tools


def test_build_mcp_registers_exactly_six_named_tools():
    reg = tools.Registry(family=None, private=None, qbit=None)
    mcp = server.build_mcp(reg)

    # FastMCP 3.x has no public `_tool_manager` (a private, version-specific
    # attribute the brief flagged as possibly stale). The public, documented
    # accessor in this installed version (fastmcp 3.4.4) is the async
    # `FastMCP.list_tools()`, which returns a list of Tool objects with a
    # `.name` attribute. Drive it synchronously via asyncio.run since no
    # pytest-asyncio plugin is installed in this project.
    registered = asyncio.run(mcp.list_tools())
    names = {t.name for t in registered}

    assert names == {
        "plex_status",
        "plex_sessions",
        "qbit_status",
        "plex_scan_library",
        "qbit_resume",
        "qbit_recheck",
    }
