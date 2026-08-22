from __future__ import annotations

import json
import os
import threading
from typing import Any, Dict, Iterable, List

from memory.packets import MemoryPacket


class JsonlMemoryPacketStore:
    def __init__(self, root_dir: str):
        self.root_dir = root_dir
        os.makedirs(self.root_dir, exist_ok=True)
        self.path = os.path.join(self.root_dir, "packets.jsonl")
        self._lock = threading.Lock()

    def append_packets(self, packets: Iterable[MemoryPacket]) -> int:
        rows = [packet.to_dict() for packet in packets or [] if isinstance(packet, MemoryPacket)]
        if not rows:
            return 0
        with self._lock:
            with open(self.path, "a", encoding="utf-8") as handle:
                for row in rows:
                    handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
        return len(rows)

    def load_recent(self, limit: int = 200) -> List[Dict[str, Any]]:
        if not os.path.exists(self.path):
            return []
        try:
            with open(self.path, "r", encoding="utf-8") as handle:
                lines = handle.readlines()[-max(1, int(limit)):]
        except Exception:
            return []

        items: List[Dict[str, Any]] = []
        for line in lines:
            try:
                payload = json.loads(line)
            except Exception:
                continue
            if isinstance(payload, dict):
                items.append(payload)
        return items
