from __future__ import annotations
from dataclasses import dataclass
from plex_stack_mcp.plex_client import PlexClient
from plex_stack_mcp.qbit_client import QbitClient

DOWN_NOTE = "down — needs a host-level container restart (Option B, not yet available)"
STALLED_STATES = {"stalledDL", "stalledUP", "error", "missingFiles"}


@dataclass
class Registry:
    family: PlexClient | None
    private: PlexClient | None
    qbit: QbitClient | None


def resolve_plex(reg: Registry, instance: str) -> PlexClient:
    if instance == "family":
        return reg.family
    if instance == "private":
        return reg.private
    raise ValueError(f"unknown plex instance: {instance!r} (use 'family' or 'private')")


def tool_plex_status(reg: Registry, instance: str) -> dict:
    plex = resolve_plex(reg, instance)
    ident = plex.identity()
    if not ident["reachable"]:
        return {"instance": instance, "reachable": False, "version": None,
                "libraries": [], "session_count": 0, "transcode_count": 0,
                "error": ident["error"], "note": DOWN_NOTE}
    sessions = plex.sessions()
    return {"instance": instance, "reachable": True, "version": ident["version"],
            "libraries": plex.libraries(),
            "session_count": len(sessions),
            "transcode_count": sum(1 for s in sessions if s["transcode"])}


def tool_plex_sessions(reg: Registry, instance: str) -> dict:
    plex = resolve_plex(reg, instance)
    ident = plex.identity()
    if not ident["reachable"]:
        return {"instance": instance, "reachable": False, "sessions": [],
                "error": ident["error"], "note": DOWN_NOTE}
    return {"instance": instance, "reachable": True, "sessions": plex.sessions()}


def tool_qbit_status(reg: Registry) -> dict:
    try:
        reg.qbit.login()
        info = reg.qbit.transfer_info()
        torrents = reg.qbit.torrents()
    except Exception as e:  # noqa: BLE001
        return {"reachable": False, "error": str(e), "note": DOWN_NOTE}
    stalled = [t["hash"] for t in torrents if t["state"] in STALLED_STATES]
    return {"reachable": True, "connection_status": info["connection_status"],
            "dl_info_speed": info["dl_info_speed"], "up_info_speed": info["up_info_speed"],
            "torrents": torrents, "stalled": stalled}


def tool_qbit_resume(reg: Registry, hashes: list[str] | None,
                     all_stalled: bool = False) -> dict:
    if all_stalled:
        status = tool_qbit_status(reg)
        if status.get("reachable") is False:
            return {"reachable": False, "error": status.get("error"), "note": DOWN_NOTE}
        target = status.get("stalled", [])
    elif hashes:
        reg.qbit.login()
        target = hashes
    else:
        raise ValueError("provide hashes=[...] or all_stalled=True")
    if target:
        reg.qbit.resume(target)
    return {"resumed": target, "count": len(target)}


def tool_qbit_recheck(reg: Registry, hashes: list[str]) -> dict:
    if not hashes:
        raise ValueError("hashes must be a non-empty list")
    reg.qbit.login()
    reg.qbit.recheck(hashes)
    return {"rechecked": hashes, "count": len(hashes)}


def tool_plex_scan_library(reg: Registry, instance: str, section_key: str) -> dict:
    plex = resolve_plex(reg, instance)
    ident = plex.identity()
    if not ident["reachable"]:
        return {"instance": instance, "reachable": False,
                "error": ident["error"], "note": DOWN_NOTE}
    plex.scan(section_key)
    return {"instance": instance, "section_key": section_key,
            "scan_triggered": True, "reachable": True}
