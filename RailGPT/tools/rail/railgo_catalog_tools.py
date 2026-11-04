"""Small, non-overlapping RailGo V1 catalogue/discovery tools.

Exact station lookups continue to use the local station dictionary before any
network call.  These endpoints are exposed only for explicit fuzzy discovery
or random-train requests, where no reliable local telecode exists yet.
"""

from __future__ import annotations

import json
from typing import Any, Dict

from agent.psw import AgentState
from tools.rail.railgo_client import (
    fetch_random_train_v1,
    fetch_station_preselect_v1,
    fetch_train_preselect_v1,
)


RAILGO_CATALOG_TOOL_OBJECTS = {
    "station_preselect",
    "train_preselect",
    "random_train",
}


def _source_line(payload: Dict[str, Any]) -> str:
    source = payload.get("_railgo") if isinstance(payload, dict) else {}
    return (
        f"SOURCE: RailGo {source.get('api_version', 'v1')} "
        f"{source.get('endpoint', '')} fetched_at={source.get('fetched_at', '?')} "
        f"url={source.get('url', 'https://railgo.dev')}"
    )


def query_railgo_catalog_tool(obj: str, query_id: str, *, psw: Any = None) -> Dict[str, Any]:
    """Execute one bounded V1 discovery request and return compact evidence."""

    obj = str(obj or "").strip()
    if obj not in RAILGO_CATALOG_TOOL_OBJECTS:
        raise ValueError(f"unsupported RailGo catalogue tool: {obj}")

    if psw:
        psw.set_state(AgentState.DISPATCH, f"RailGo v1 catalogue dispatch -> {obj} ({query_id})")
        psw.set_state(AgentState.QUERYING, f"RailGo v1 {obj}")

    if obj == "station_preselect":
        keyword = str(query_id or "").strip()
        payload = fetch_station_preselect_v1(keyword)
        data = list(payload["data"])[:20]
        title = f"STATION PRESELECT: {keyword}"
        result_id = keyword
    elif obj == "train_preselect":
        keyword = str(query_id or "").strip().upper()
        payload = fetch_train_preselect_v1(keyword)
        data = list(payload["data"])[:40]
        title = f"TRAIN PRESELECT: {keyword}"
        result_id = keyword
    else:
        payload = fetch_random_train_v1()
        data = dict(payload["data"])
        title = "RANDOM TRAIN"
        result_id = str(data.get("number") or "random").strip().upper()

    if psw:
        psw.set_state(AgentState.RENDERING, f"format RailGo v1 evidence: {obj}")

    pretty = "\n".join(
        [
            title,
            _source_line(payload),
            json.dumps(data, ensure_ascii=False, indent=2),
        ]
    )
    return {
        "domain": "railway",
        "object": obj,
        "id": result_id,
        "payload": None,
        "evidence": data,
        "source": payload.get("_railgo", {}),
        "pretty": pretty,
        "note": "bounded RailGo v1 catalogue evidence; exact station conversion remains local-first",
    }


__all__ = ["RAILGO_CATALOG_TOOL_OBJECTS", "query_railgo_catalog_tool"]
