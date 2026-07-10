from __future__ import annotations
import httpx
from fastmcp import FastMCP
from plex_stack_mcp.config import Settings
from plex_stack_mcp.plex_client import PlexClient
from plex_stack_mcp.qbit_client import QbitClient
from plex_stack_mcp import tools

TIMEOUT = httpx.Timeout(6.0)


def build_registry(
    settings: Settings,
    *,
    plex_client_factory=PlexClient,
    qbit_client_factory=QbitClient,
) -> tools.Registry:
    return tools.Registry(
        family=plex_client_factory(
            settings.plex_family_url, settings.plex_family_token,
            httpx.Client(timeout=TIMEOUT)),
        private=plex_client_factory(
            settings.plex_private_url, settings.plex_private_token,
            httpx.Client(timeout=TIMEOUT)),
        qbit=qbit_client_factory(
            settings.qbit_url, settings.qbit_username, settings.qbit_password,
            httpx.Client(timeout=TIMEOUT)),
    )


def build_mcp(reg: tools.Registry) -> FastMCP:
    mcp = FastMCP("plex-stack")

    @mcp.tool
    def plex_status(instance: str) -> dict:
        """Health of a Plex instance ('family' or 'private'): reachability,
        version, libraries, active/transcode session counts."""
        return tools.tool_plex_status(reg, instance)

    @mcp.tool
    def plex_sessions(instance: str) -> dict:
        """Current streams on a Plex instance ('family' or 'private')."""
        return tools.tool_plex_sessions(reg, instance)

    @mcp.tool
    def qbit_status() -> dict:
        """qBittorrent connection status, transfer rates, torrent list, and the
        set of stalled/errored torrent hashes."""
        return tools.tool_qbit_status(reg)

    @mcp.tool
    def plex_scan_library(instance: str, section_key: str) -> dict:
        """SAFE ACTION: trigger a library scan on a Plex instance."""
        return tools.tool_plex_scan_library(reg, instance, section_key)

    @mcp.tool
    def qbit_resume(hashes: list[str] | None = None, all_stalled: bool = False) -> dict:
        """SAFE ACTION: resume specific torrent hashes, or all stalled ones."""
        return tools.tool_qbit_resume(reg, hashes, all_stalled)

    @mcp.tool
    def qbit_recheck(hashes: list[str]) -> dict:
        """SAFE ACTION: force-recheck specific torrent hashes."""
        return tools.tool_qbit_recheck(reg, hashes)

    return mcp


def main() -> None:
    reg = build_registry(Settings.from_env())
    build_mcp(reg).run(transport="streamable-http", host="0.0.0.0", port=3000)


if __name__ == "__main__":
    main()
