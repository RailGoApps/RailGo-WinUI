"""LLM-first coach media target resolver.

The model sees labels and selectors only. Remote media URLs stay exclusively in
the deterministic coach asset service.
"""

from __future__ import annotations

import json
import re
from typing import Any, Dict, List

from llm.json_utils import loads_llm_json
from llm.llm_client import LLMClient


class CoachMediaResolverAgent:
    def __init__(self, llm: Any = None):
        self.llm = llm or LLMClient(mode="fast-go", credential_slot="thinking")

    def set_mode(self, mode: str) -> None:
        # Resolution is a compact classification task even in heavier modes.
        if hasattr(self.llm, "set_mode"):
            self.llm.set_mode("fast-go" if mode == "fast-go" else "fast-plus")

    def resolve(self, user_text: str, catalog: List[Dict[str, str]]) -> Dict[str, Any]:
        safe_catalog = [
            {
                "kind": str(item.get("kind") or ""),
                "selector": str(item.get("selector") or ""),
                "label": str(item.get("label") or ""),
            }
            for item in catalog[:64]
            if isinstance(item, dict)
        ]
        messages = [
            {
                "role": "system",
                "content": (
                    "You are RailGPT Coach Media Resolver. Select only from AVAILABLE_TARGETS. "
                    "Never invent a selector and never request or output URLs. "
                    "A generic request for a train/coach layout image means whole_train/default. "
                    "If the user asks for an interior picture but gives neither coach number nor seat type, "
                    "return clarify. A data/specification question returns summary. Return JSON only: "
                    '{"presentation_mode":"summary|whole_train|coach|interior|clarify",'
                    '"selector":"","coach_number":"","seat_type":"","confidence":0,"reason":""}'
                ),
            },
            {
                "role": "user",
                "content": json.dumps({"user_text": user_text, "available_targets": safe_catalog}, ensure_ascii=False),
            },
        ]
        try:
            result = loads_llm_json(self.llm.generate(messages, timeout=10, max_retries=0))
            if isinstance(result, dict):
                normalized = self._validate(result, safe_catalog)
                if normalized:
                    return normalized
        except Exception as exc:
            print(f"[coach-media-resolver] semantic resolver fallback: {exc}")
        return self._fallback(user_text, safe_catalog)

    @staticmethod
    def _validate(result: Dict[str, Any], catalog: List[Dict[str, str]]) -> Dict[str, Any] | None:
        mode = str(result.get("presentation_mode") or "summary")
        if mode not in {"summary", "whole_train", "coach", "interior", "clarify"}:
            return None
        selector = str(result.get("selector") or ("default" if mode == "whole_train" else ""))
        if mode in {"whole_train", "coach", "interior"}:
            allowed = {(item["kind"], item["selector"]) for item in catalog}
            if (mode, selector) not in allowed:
                return None
        return {
            "presentation_mode": mode,
            "selector": selector,
            "coach_number": str(result.get("coach_number") or ""),
            "seat_type": str(result.get("seat_type") or ""),
            "confidence": max(0, min(100, int(result.get("confidence") or 0))),
            "reason": str(result.get("reason") or "")[:160],
        }

    @staticmethod
    def _fallback(text: str, catalog: List[Dict[str, str]]) -> Dict[str, Any]:
        lowered = str(text or "").lower()
        if not any(token in lowered for token in ("图", "图片", "照片", "内部", "内饰")):
            return {"presentation_mode": "summary", "selector": "", "confidence": 70, "reason": "no image request"}
        coach_match = re.search(r"(?<!\d)(\d{1,2})(?:号)?车(?:厢)?", lowered)
        if coach_match:
            selector = f"{int(coach_match.group(1)):02d}"
            if any(item["kind"] == "coach" and item["selector"] == selector for item in catalog):
                return {"presentation_mode": "coach", "selector": selector, "coach_number": selector, "confidence": 75, "reason": "explicit coach"}
        for item in catalog:
            if item["kind"] == "interior" and any(token in lowered for token in (item["label"].lower(), item["selector"].lower())):
                return {"presentation_mode": "interior", "selector": item["selector"], "seat_type": item["label"], "confidence": 72, "reason": "explicit interior label"}
        if any(token in lowered for token in ("内部", "内饰", "座椅", "商务座", "一等座", "二等座")):
            return {"presentation_mode": "clarify", "selector": "", "confidence": 60, "reason": "ambiguous interior target"}
        if any(item["kind"] == "whole_train" and item["selector"] == "default" for item in catalog):
            return {"presentation_mode": "whole_train", "selector": "default", "confidence": 75, "reason": "generic coach image"}
        return {"presentation_mode": "summary", "selector": "", "confidence": 50, "reason": "no available image target"}


__all__ = ["CoachMediaResolverAgent"]
