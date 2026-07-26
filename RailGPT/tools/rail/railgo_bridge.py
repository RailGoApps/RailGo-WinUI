"""Client for the local RailGo.Core JSON-lines named-pipe bridge."""

from __future__ import annotations

import json
import os
import uuid


def bridge_enabled() -> bool:
    return bool(os.environ.get("RAILGO_BRIDGE_PIPE"))


def call_railgo(method: str, params: dict | None = None) -> object:
    pipe_name = os.environ.get("RAILGO_BRIDGE_PIPE", "").strip()
    if not pipe_name:
        raise RuntimeError("RailGo bridge is not available")

    # Windows named pipes are exposed as files. The server accepts one
    # request per connection, which keeps the protocol resilient to a
    # restarted WinUI host.
    pipe_path = "\\\\.\\pipe\\" + pipe_name
    request = {
        "id": uuid.uuid4().hex,
        "method": method,
        "params": params or {},
    }
    with open(pipe_path, "r+b", buffering=0) as pipe:
        pipe.write((json.dumps(request, ensure_ascii=False) + "\n").encode("utf-8"))
        chunks = bytearray()
        while True:
            chunk = pipe.read(1)
            if not chunk or chunk == b"\n":
                break
            chunks.extend(chunk)

    response = json.loads(chunks.decode("utf-8"))
    if not response.get("ok"):
        raise RuntimeError(response.get("error") or "RailGo bridge request failed")
    return response.get("result")


def query_railgo_host(method: str, **params: object) -> object:
    """Public adapter used by RailGPT railway tools when hosted by RailGo."""

    return call_railgo(method, params)


__all__ = ["bridge_enabled", "call_railgo", "query_railgo_host"]
