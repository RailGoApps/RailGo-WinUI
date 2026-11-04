from __future__ import annotations

import json
import re
from typing import Any


_FENCED_JSON_RE = re.compile(
    r"```(?:json)?\s*(.*?)\s*```",
    flags=re.IGNORECASE | re.DOTALL,
)


def loads_llm_json(raw: Any) -> Any:
    """Decode structured LLM output without relaxing the JSON contract.

    Models occasionally wrap an otherwise valid response in a Markdown JSON
    fence or add a short sentence around it. This helper unwraps those common
    presentation layers, then still delegates all syntax validation to the
    standard JSON decoder.
    """

    if not isinstance(raw, str):
        return raw

    text = raw.strip()
    if not text:
        raise json.JSONDecodeError("empty LLM response", text, 0)

    fenced = _FENCED_JSON_RE.search(text)
    if fenced:
        text = fenced.group(1).strip()

    try:
        return json.loads(text)
    except json.JSONDecodeError as direct_error:
        decoder = json.JSONDecoder()
        starts = [position for position in (text.find("{"), text.find("[")) if position >= 0]
        for position in sorted(starts):
            try:
                value, _ = decoder.raw_decode(text[position:])
                return value
            except json.JSONDecodeError:
                continue
        raise direct_error
