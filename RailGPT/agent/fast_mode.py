import copy
import hashlib
import json
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Dict, List

from agent.psw import AgentState
from llm.llm_client import LLMClient
from utils.simple_lru import SimpleLRUCache


class FastFactCompressor:
    """
    Fast-mode evidence compressor.

    The tool layer still returns full facts. Fast mode changes how those facts
    are consumed: split into chunks, pack into up to 6 balanced lanes, compress
    each lane in parallel, then merge the extracted candidates.
    """

    def __init__(
        self,
        llm_factory=None,
        max_workers: int = 6,
        max_chunk_chars: int = 2200,
        max_candidates_per_lane: int = 6,
        max_retained_units: int = 16,
        max_retained_chars: int = 6000,
        cache_size: int = 64,
    ):
        self.max_workers = max_workers
        self.max_chunk_chars = max_chunk_chars
        self.max_candidates_per_lane = max_candidates_per_lane
        self.max_retained_units = max_retained_units
        self.max_retained_chars = max_retained_chars
        self._context_cache = SimpleLRUCache(cache_size)
        self._llm_factory = llm_factory or (lambda: LLMClient(mode="fast-go"))
        self._thread_local = threading.local()

    def compress(
        self,
        user_text: str,
        facts: Dict[str, Any],
        psw=None,
    ) -> Dict[str, Any]:
        units = self._build_units(facts)
        seed_candidates = self._build_seed_candidates(facts)

        if not units:
            return self._empty_context(facts, seed_candidates=seed_candidates, cache_hit=False)

        cache_key = self._build_cache_key(user_text, units, seed_candidates)
        cached = self._context_cache.get(cache_key)
        if cached is not None:
            if psw:
                psw.set_state(
                    AgentState.FAST_CACHE_HIT,
                    f"fast mode cache hit for {len(units)} chunks",
                )
            cached_result = copy.deepcopy(cached)
            cached_result["source_stats"]["cache_hit"] = True
            return cached_result

        if psw:
            psw.set_state(
                AgentState.FAST_CACHE_MISS,
                f"fast mode cache miss for {len(units)} chunks",
            )

        if self._should_skip_lane_llm(units, seed_candidates, facts):
            if psw:
                psw.set_state(
                    AgentState.FAST_REDUCING,
                    f"fast mode deterministic reducer kept {len(units)} fact chunks local",
                )

            results = [
                {
                    "lane_id": "deterministic_local",
                    "is_relevant": bool(seed_candidates),
                    "candidates": [],
                    "observations": self._build_local_observations(units),
                    "raw_fallback": None,
                }
            ]

            if psw:
                psw.set_state(
                    AgentState.FAST_MERGING,
                    "fast mode merging deterministic local candidates",
                )

            merged = self._merge_results(
                user_text=user_text,
                facts=facts,
                units=units,
                lanes=[],
                results=results,
                seed_candidates=seed_candidates,
            )
            merged["strategy"] = "deterministic_fast_views_seed_merge"
            merged["source_stats"]["lane_count"] = 0
            merged["source_stats"]["deterministic_only"] = True
            self._context_cache.put(cache_key, copy.deepcopy(merged))
            return merged

        lanes = self._pack_units_into_lanes(units)

        if psw:
            psw.set_state(
                AgentState.FAST_REDUCING,
                f"fast mode split {len(units)} fact chunks into {len(lanes)} lanes",
            )

        results: List[Dict[str, Any]] = []

        with ThreadPoolExecutor(max_workers=len(lanes)) as pool:
            future_map = {
                pool.submit(self._extract_from_lane, user_text, lane): lane
                for lane in lanes
            }

            for future in as_completed(future_map):
                lane = future_map[future]
                try:
                    results.append(future.result())
                except Exception as exc:
                    results.append(self._fallback_result(lane, f"lane_failed: {exc}"))

        if psw:
            psw.set_state(
                AgentState.FAST_MERGING,
                "fast mode merging local candidates",
            )

        merged = self._merge_results(user_text, facts, units, lanes, results, seed_candidates)
        merged["source_stats"]["deterministic_only"] = False
        self._context_cache.put(cache_key, copy.deepcopy(merged))
        return merged

    def _build_units(self, facts: Dict[str, Any]) -> List[Dict[str, Any]]:
        units: List[Dict[str, Any]] = []

        for idx, item in enumerate(facts.get("queries", [])):
            units.extend(self._entry_to_units("query", idx, item))

        for idx, item in enumerate(facts.get("analysis", [])):
            units.extend(self._entry_to_units("analysis", idx, item))

        for idx, item in enumerate(facts.get("comparisons", [])):
            units.extend(self._entry_to_units("comparison", idx, item))

        meta = facts.get("meta", {})

        for idx, item in enumerate(meta.get("warnings", [])):
            units.extend(self._entry_to_units("warning", idx, item))

        for idx, item in enumerate(meta.get("errors", [])):
            units.extend(self._entry_to_units("error", idx, item))

        return units

    def _build_seed_candidates(self, facts: Dict[str, Any]) -> List[Dict[str, Any]]:
        seed_candidates: List[Dict[str, Any]] = []

        for idx, item in enumerate(facts.get("queries", [])):
            seed_candidates.extend(self._entry_to_seed_candidates("query", idx, item))

        for idx, item in enumerate(facts.get("analysis", [])):
            seed_candidates.extend(self._entry_to_seed_candidates("analysis", idx, item))

        for idx, item in enumerate(facts.get("comparisons", [])):
            seed_candidates.extend(self._entry_to_seed_candidates("comparison", idx, item))

        return seed_candidates

    def _should_skip_lane_llm(
        self,
        units: List[Dict[str, Any]],
        seed_candidates: List[Dict[str, Any]],
        facts: Dict[str, Any],
    ) -> bool:
        if not units or not seed_candidates:
            return False

        if facts.get("analysis") or facts.get("comparisons"):
            return False

        queries = facts.get("queries", [])
        if len(queries) > 4:
            return False

        if any(unit.get("source_kind") != "fast_view" for unit in units):
            return False

        return all(self._supports_deterministic_query(item) for item in queries if isinstance(item, dict))

    def _supports_deterministic_query(self, item: Dict[str, Any]) -> bool:
        obj = str(item.get("object") or "").strip()
        if not obj:
            return False

        if obj in {
            "train",
            "emu",
            "path_detail",
            "path_future",
            "path_past",
            "path_stopcheck",
            "left_ticket_12306",
            "transfer_12306",
        }:
            return True

        return obj.startswith("station_to_station") or obj.startswith("s2s_")

    def _build_local_observations(self, units: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        observations: List[Dict[str, Any]] = []
        selected_units = sorted(
            [unit for unit in units if unit.get("source_kind") == "fast_view"],
            key=lambda item: (
                -int(item.get("priority", 0)),
                item.get("text_len", 0),
                item.get("fact_id", ""),
            ),
        )[:4]

        for unit in selected_units:
            content = self._extract_local_observation_text(unit)
            if not content:
                continue

            observations.append(
                {
                    "kind": "meta",
                    "content": content,
                    "fact_ids": [unit["fact_id"]],
                }
            )

        return observations

    def _extract_local_observation_text(self, unit: Dict[str, Any]) -> str:
        lines = []
        in_view_text = False
        for raw_line in str(unit.get("text") or "").splitlines():
            line = raw_line.strip()
            if not line:
                continue
            if line == "VIEW_TEXT:":
                in_view_text = True
                continue
            if not in_view_text:
                continue
            if line.startswith("OBJECT:") or line.startswith("QUERY_") or line.startswith("VIEW_"):
                continue
            if line.startswith("- "):
                line = line[2:].strip()
            lines.append(line)
            if len(lines) >= 2:
                break

        if not lines:
            return ""

        view_type = str(unit.get("view_type") or "fast_view").strip()
        summary = " / ".join(lines)
        if len(summary) > 220:
            summary = summary[:217] + "..."
        return f"{view_type}: {summary}"

    def _entry_to_seed_candidates(
        self,
        category: str,
        index: int,
        item: Any,
    ) -> List[Dict[str, Any]]:
        if not isinstance(item, dict):
            return []

        raw_candidates = item.get("fast_candidates")
        if not isinstance(raw_candidates, list):
            return []

        root_fact_id = f"{category}_{index:03d}"
        normalized: List[Dict[str, Any]] = []

        for idx, candidate in enumerate(raw_candidates):
            if not isinstance(candidate, dict):
                continue

            candidate_key = str(candidate.get("candidate_key") or f"seed:{root_fact_id}:{idx}").strip()
            if not candidate_key:
                candidate_key = f"seed:{root_fact_id}:{idx}"

            attributes = candidate.get("attributes", {})
            if not isinstance(attributes, dict):
                attributes = {}

            normalized.append(
                {
                    "candidate_key": candidate_key,
                    "candidate_type": str(candidate.get("candidate_type") or "unknown").strip() or "unknown",
                    "label": str(candidate.get("label") or candidate_key).strip() or candidate_key,
                    "score": self._normalize_score(candidate.get("score")),
                    "why": str(candidate.get("why") or "").strip(),
                    "supporting_points": self._clean_string_list(candidate.get("supporting_points"), limit=6),
                    "fact_ids": [root_fact_id],
                    "attributes": attributes,
                    "source_kind": "deterministic_seed",
                }
            )

        return normalized

    def _entry_to_units(
        self,
        category: str,
        index: int,
        item: Any,
    ) -> List[Dict[str, Any]]:
        root_fact_id = f"{category}_{index:03d}"
        fast_views = []

        if isinstance(item, dict):
            maybe_views = item.get("fast_views")
            if isinstance(maybe_views, list):
                fast_views = [view for view in maybe_views if isinstance(view, dict)]

        if fast_views:
            return self._fast_views_to_units(category, item, root_fact_id, fast_views)

        serialized = self._serialize_entry(category, item)
        chunks = self._split_text(serialized, self.max_chunk_chars)

        if not chunks:
            chunks = [serialized]

        units: List[Dict[str, Any]] = []
        total = len(chunks)

        for chunk_idx, chunk_text in enumerate(chunks, start=1):
            fact_id = root_fact_id if total == 1 else f"{root_fact_id}.part{chunk_idx:02d}"
            units.append(
                {
                    "fact_id": fact_id,
                    "root_fact_id": root_fact_id,
                    "category": category,
                    "chunk_index": chunk_idx,
                    "chunk_count": total,
                    "preview": chunk_text[:240],
                    "text": chunk_text,
                    "text_len": len(chunk_text),
                    "priority": 0,
                    "source_kind": "serialized_fact",
                    "view_type": "",
                }
            )

        return units

    def _fast_views_to_units(
        self,
        category: str,
        item: Dict[str, Any],
        root_fact_id: str,
        fast_views: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        units: List[Dict[str, Any]] = []

        for view_index, view in enumerate(fast_views, start=1):
            view_fact_root = f"{root_fact_id}.view{view_index:02d}"
            serialized = self._serialize_fast_view(category, item, view)
            chunks = self._split_text(serialized, self.max_chunk_chars)

            if not chunks:
                chunks = [serialized]

            for chunk_index, chunk_text in enumerate(chunks, start=1):
                fact_id = (
                    view_fact_root
                    if len(chunks) == 1
                    else f"{view_fact_root}.part{chunk_index:02d}"
                )
                units.append(
                    {
                        "fact_id": fact_id,
                        "root_fact_id": root_fact_id,
                        "category": category,
                        "chunk_index": chunk_index,
                        "chunk_count": len(chunks),
                        "preview": chunk_text[:240],
                        "text": chunk_text,
                        "text_len": len(chunk_text),
                        "priority": self._normalize_priority(view.get("priority")),
                        "source_kind": "fast_view",
                        "view_type": str(view.get("view_type") or "").strip(),
                    }
                )

        return units

    def _serialize_entry(self, category: str, item: Any) -> str:
        if isinstance(item, dict):
            data = item
        else:
            data = {"value": item}

        rendered = json.dumps(data, ensure_ascii=False, indent=2, default=str)
        return f"CATEGORY: {category}\n{rendered}"

    def _serialize_fast_view(
        self,
        category: str,
        item: Dict[str, Any],
        view: Dict[str, Any],
    ) -> str:
        header = [
            f"CATEGORY: {category}",
            f"QUERY_OBJECT: {item.get('object', '?')}",
            f"QUERY_ID: {item.get('id', '?')}",
            f"VIEW_TYPE: {view.get('view_type', '?')}",
            f"VIEW_ID: {view.get('view_id', '?')}",
        ]

        if item.get("date"):
            header.append(f"QUERY_DATE: {item.get('date')}")

        metadata = view.get("metadata")
        if isinstance(metadata, dict) and metadata:
            header.append(
                "VIEW_METADATA: "
                + json.dumps(metadata, ensure_ascii=False, sort_keys=True, default=str)
            )

        text = str(view.get("text") or "").strip()
        if not text:
            payload = {key: value for key, value in view.items() if key != "priority"}
            text = json.dumps(payload, ensure_ascii=False, indent=2, default=str)

        return "\n".join(header + ["VIEW_TEXT:", text])

    def _split_text(self, text: str, max_chars: int) -> List[str]:
        if len(text) <= max_chars:
            return [text]

        lines = text.splitlines() or [text]
        segments: List[str] = []
        current: List[str] = []
        current_len = 0

        for line in lines:
            line_len = len(line) + 1

            if len(line) > max_chars:
                if current:
                    segments.append("\n".join(current))
                    current = []
                    current_len = 0

                start = 0
                while start < len(line):
                    segments.append(line[start:start + max_chars])
                    start += max_chars
                continue

            if current and current_len + line_len > max_chars:
                segments.append("\n".join(current))
                current = [line]
                current_len = line_len
            else:
                current.append(line)
                current_len += line_len

        if current:
            segments.append("\n".join(current))

        return segments

    def _pack_units_into_lanes(self, units: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        lane_count = max(1, min(self.max_workers, len(units)))
        lanes = [
            {
                "lane_id": f"lane_{idx + 1:02d}",
                "units": [],
                "total_chars": 0,
            }
            for idx in range(lane_count)
        ]

        for unit in sorted(units, key=lambda item: item["text_len"], reverse=True):
            lane = min(lanes, key=lambda item: item["total_chars"])
            lane["units"].append(unit)
            lane["total_chars"] += unit["text_len"]

        packed_lanes: List[Dict[str, Any]] = []
        for lane in lanes:
            if not lane["units"]:
                continue

            parts = []
            fact_ids = []

            for unit in lane["units"]:
                fact_ids.append(unit["fact_id"])
                parts.append(
                    "\n".join(
                        [
                            f"[FACT_ID] {unit['fact_id']}",
                            f"[FACT_CATEGORY] {unit['category']}",
                            f"[FACT_CHUNK] {unit['chunk_index']}/{unit['chunk_count']}",
                            unit["text"],
                        ]
                    )
                )

            packed_lanes.append(
                {
                    "lane_id": lane["lane_id"],
                    "fact_ids": fact_ids,
                    "units": lane["units"],
                    "text": "\n\n====================\n\n".join(parts),
                    "total_chars": lane["total_chars"],
                }
            )

        return packed_lanes

    def _extract_from_lane(self, user_text: str, lane: Dict[str, Any]) -> Dict[str, Any]:
        messages = [
            {
                "role": "system",
                "content": (
                    "You are RailGPT Fast Lane Extractor.\n"
                    "You are compressing one packed lane of fact chunks into structured candidates for a later global merge.\n"
                    "Do not answer the user directly.\n"
                    "Do not invent facts.\n"
                    "Prefer keeping potentially optimal candidates instead of dropping them.\n"
                    "The lane may contain multiple fact chunks. Use fact_ids precisely.\n"
                    "Return JSON only with this schema:\n"
                    "{\n"
                    '  "is_relevant": true,\n'
                    '  "candidates": [\n'
                    "    {\n"
                    '      "candidate_key": "train:G87 or route:dep-arr or transfer:label or fact:<lane_id>:<n>",\n'
                    '      "candidate_type": "train|route|ticket|transfer|station|meta|unknown",\n'
                    '      "label": "short readable label",\n'
                    '      "score": 0,\n'
                    '      "why": "why this lane matters",\n'
                    '      "supporting_points": ["bullet"],\n'
                    '      "fact_ids": ["fact_id"],\n'
                    '      "attributes": {"train_no": "", "route": "", "date": "", "station": "", "emu": ""}\n'
                    "    }\n"
                    "  ],\n"
                    '  "observations": [\n'
                    "    {\n"
                    '      "kind": "constraint|warning|error|answer_part|meta",\n'
                    '      "content": "important note from this lane",\n'
                    '      "fact_ids": ["fact_id"]\n'
                    "    }\n"
                    "  ]\n"
                    "}\n"
                    f"Keep at most {self.max_candidates_per_lane} candidates and at most 6 observations."
                ),
            },
            {
                "role": "user",
                "content": (
                    f"USER_QUERY:\n{user_text}\n\n"
                    f"LANE_ID: {lane['lane_id']}\n"
                    f"LANE_FACT_IDS: {', '.join(lane['fact_ids'])}\n"
                    "LANE_TEXT:\n"
                    f"{lane['text']}"
                ),
            },
        ]

        raw = self._get_llm().generate(messages)
        return self._normalize_result(raw, lane)

    def _get_llm(self):
        llm = getattr(self._thread_local, "llm", None)
        if llm is None:
            llm = self._llm_factory()
            self._thread_local.llm = llm
        return llm

    def _normalize_result(self, raw: str, lane: Dict[str, Any]) -> Dict[str, Any]:
        try:
            parsed = self._loads_json(raw)
        except Exception as exc:
            return self._fallback_result(lane, f"json_parse_failed: {exc}")

        if not isinstance(parsed, dict):
            return self._fallback_result(lane, "non_dict_response")

        candidates: List[Dict[str, Any]] = []
        for idx, item in enumerate(parsed.get("candidates", [])):
            if not isinstance(item, dict):
                continue

            candidate_key = str(item.get("candidate_key") or f"fact:{lane['lane_id']}:{idx}").strip()
            if not candidate_key:
                candidate_key = f"fact:{lane['lane_id']}:{idx}"

            attributes = item.get("attributes", {})
            if not isinstance(attributes, dict):
                attributes = {}

            candidates.append(
                {
                    "candidate_key": candidate_key,
                    "candidate_type": str(item.get("candidate_type") or "unknown").strip() or "unknown",
                    "label": str(item.get("label") or candidate_key).strip() or candidate_key,
                    "score": self._normalize_score(item.get("score")),
                    "why": str(item.get("why") or "").strip(),
                    "supporting_points": self._clean_string_list(item.get("supporting_points"), limit=6),
                    "fact_ids": self._normalize_fact_ids(item.get("fact_ids"), lane["fact_ids"]),
                    "attributes": attributes,
                }
            )

        observations: List[Dict[str, Any]] = []
        for item in parsed.get("observations", []):
            if not isinstance(item, dict):
                continue

            content = str(item.get("content") or "").strip()
            if not content:
                continue

            observations.append(
                {
                    "kind": str(item.get("kind") or "meta").strip() or "meta",
                    "content": content,
                    "fact_ids": self._normalize_fact_ids(item.get("fact_ids"), lane["fact_ids"]),
                }
            )

        return {
            "lane_id": lane["lane_id"],
            "is_relevant": bool(parsed.get("is_relevant", candidates or observations)),
            "candidates": candidates[: self.max_candidates_per_lane],
            "observations": observations[:6],
            "raw_fallback": None,
        }

    def _loads_json(self, raw: str) -> Any:
        return loads_llm_json(raw)

    def _fallback_result(self, lane: Dict[str, Any], reason: str) -> Dict[str, Any]:
        return {
            "lane_id": lane["lane_id"],
            "is_relevant": True,
            "candidates": [],
            "observations": [
                {
                    "kind": "raw_fallback",
                    "content": f"Lane kept as raw fallback because {reason}.",
                    "fact_ids": lane["fact_ids"],
                }
            ],
            "raw_fallback": {
                "lane_id": lane["lane_id"],
                "fact_ids": lane["fact_ids"],
                "text": lane["text"],
                "reason": reason,
            },
        }

    def _merge_results(
        self,
        user_text: str,
        facts: Dict[str, Any],
        units: List[Dict[str, Any]],
        lanes: List[Dict[str, Any]],
        results: List[Dict[str, Any]],
        seed_candidates: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        candidate_map: Dict[str, Dict[str, Any]] = {}
        observation_map: Dict[tuple, Dict[str, Any]] = {}
        raw_fallbacks: List[Dict[str, Any]] = []

        for candidate in seed_candidates:
            key = candidate["candidate_key"]
            candidate_map[key] = {
                "candidate_key": key,
                "candidate_type": candidate["candidate_type"],
                "label": candidate["label"],
                "score": candidate["score"],
                "why_list": [candidate["why"]] if candidate["why"] else [],
                "supporting_points": list(candidate.get("supporting_points", [])),
                "fact_ids": list(candidate.get("fact_ids", [])),
                "attributes": dict(candidate.get("attributes", {})),
            }

        for result in results:
            raw_fallback = result.get("raw_fallback")
            if raw_fallback:
                raw_fallbacks.append(raw_fallback)

            for candidate in result.get("candidates", []):
                key = candidate["candidate_key"]

                if key not in candidate_map:
                    candidate_map[key] = {
                        "candidate_key": key,
                        "candidate_type": candidate["candidate_type"],
                        "label": candidate["label"],
                        "score": candidate["score"],
                        "why_list": [],
                        "supporting_points": [],
                        "fact_ids": [],
                        "attributes": {},
                    }

                merged = candidate_map[key]
                merged["score"] = max(merged["score"], candidate["score"])
                merged["candidate_type"] = merged["candidate_type"] or candidate["candidate_type"]
                merged["label"] = merged["label"] or candidate["label"]

                if candidate["why"] and candidate["why"] not in merged["why_list"]:
                    merged["why_list"].append(candidate["why"])

                for point in candidate.get("supporting_points", []):
                    if point not in merged["supporting_points"]:
                        merged["supporting_points"].append(point)

                for fact_id in candidate.get("fact_ids", []):
                    if fact_id not in merged["fact_ids"]:
                        merged["fact_ids"].append(fact_id)

                for attr_key, attr_value in candidate.get("attributes", {}).items():
                    if attr_value and attr_key not in merged["attributes"]:
                        merged["attributes"][attr_key] = attr_value

            for obs in result.get("observations", []):
                dedupe_key = (obs["kind"], obs["content"])
                if dedupe_key not in observation_map:
                    observation_map[dedupe_key] = {
                        "kind": obs["kind"],
                        "content": obs["content"],
                        "fact_ids": [],
                    }

                merged_obs = observation_map[dedupe_key]
                for fact_id in obs.get("fact_ids", []):
                    if fact_id not in merged_obs["fact_ids"]:
                        merged_obs["fact_ids"].append(fact_id)

        candidates = []
        for item in candidate_map.values():
            candidates.append(
                {
                    "candidate_key": item["candidate_key"],
                    "candidate_type": item["candidate_type"],
                    "label": item["label"],
                    "score": item["score"],
                    "why": item["why_list"][0] if item["why_list"] else "",
                    "supporting_points": item["supporting_points"][:8],
                    "fact_ids": item["fact_ids"],
                    "attributes": item["attributes"],
                }
            )

        candidates.sort(
            key=lambda item: (
                -item["score"],
                -len(item.get("fact_ids", [])),
                item.get("label", ""),
            )
        )

        observations = list(observation_map.values())
        observations.sort(
            key=lambda item: (
                0 if item["kind"] in ("warning", "error", "constraint") else 1,
                item["content"],
            )
        )

        retained_evidence = self._select_retained_evidence(units)

        return {
            "format": "fast_fact_context_v1",
            "strategy": "fast_views_pack_to_6_lanes_parallel_extract_then_merge",
            "user_query": user_text,
            "source_stats": {
                "query_count": len(facts.get("queries", [])),
                "analysis_count": len(facts.get("analysis", [])),
                "comparison_count": len(facts.get("comparisons", [])),
                "fact_chunk_count": len(units),
                "lane_count": len(lanes),
                "seed_candidate_count": len(seed_candidates),
                "merged_candidate_count": len(candidates),
                "raw_fallback_count": len(raw_fallbacks),
                "retained_evidence_count": len(retained_evidence),
                "cache_hit": False,
                "deterministic_only": False,
            },
            "candidates": candidates,
            "observations": observations,
            "raw_fallbacks": raw_fallbacks,
            "retained_evidence": retained_evidence,
        }

    def _empty_context(
        self,
        facts: Dict[str, Any],
        seed_candidates: List[Dict[str, Any]] | None = None,
        cache_hit: bool = False,
    ) -> Dict[str, Any]:
        if seed_candidates is None:
            seed_candidates = []
        return {
            "format": "fast_fact_context_v1",
            "strategy": "fast_views_pack_to_6_lanes_parallel_extract_then_merge",
            "user_query": "",
            "source_stats": {
                "query_count": len(facts.get("queries", [])),
                "analysis_count": len(facts.get("analysis", [])),
                "comparison_count": len(facts.get("comparisons", [])),
                "fact_chunk_count": 0,
                "lane_count": 0,
                "seed_candidate_count": len(seed_candidates),
                "merged_candidate_count": 0,
                "raw_fallback_count": 0,
                "retained_evidence_count": 0,
                "cache_hit": cache_hit,
                "deterministic_only": False,
            },
            "candidates": seed_candidates,
            "observations": [],
            "raw_fallbacks": [],
            "retained_evidence": [],
        }

    def _normalize_score(self, value: Any) -> int:
        try:
            score = int(float(value))
        except Exception:
            return 50
        return max(0, min(100, score))

    def _clean_string_list(self, value: Any, limit: int) -> List[str]:
        if not isinstance(value, list):
            return []

        result: List[str] = []
        for item in value:
            text = str(item).strip()
            if text and text not in result:
                result.append(text)
            if len(result) >= limit:
                break
        return result

    def _normalize_fact_ids(self, value: Any, fallback_fact_ids: Any) -> List[str]:
        if isinstance(fallback_fact_ids, list):
            fallback_ids = list(fallback_fact_ids)
        else:
            fallback_ids = [str(fallback_fact_ids)]

        if not isinstance(value, list):
            return fallback_ids

        result: List[str] = []
        for item in value:
            text = str(item).strip()
            if text and text not in result:
                result.append(text)

        if not result:
            result = fallback_ids

        return result

    def _normalize_priority(self, value: Any) -> int:
        try:
            return int(value)
        except Exception:
            return 0

    def _select_retained_evidence(self, units: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        selected: List[Dict[str, Any]] = []
        total_chars = 0

        fast_view_units = [
            unit for unit in units
            if unit.get("source_kind") == "fast_view"
        ]

        fast_view_units.sort(
            key=lambda item: (
                -int(item.get("priority", 0)),
                item.get("text_len", 0),
                item.get("fact_id", ""),
            )
        )

        for unit in fast_view_units:
            text_len = int(unit.get("text_len", 0))

            if selected and total_chars + text_len > self.max_retained_chars:
                continue

            selected.append(
                {
                    "fact_id": unit["fact_id"],
                    "view_type": unit.get("view_type", ""),
                    "priority": int(unit.get("priority", 0)),
                    "text": unit["text"],
                }
            )
            total_chars += text_len

            if len(selected) >= self.max_retained_units or total_chars >= self.max_retained_chars:
                break

        return selected

    def _build_cache_key(
        self,
        user_text: str,
        units: List[Dict[str, Any]],
        seed_candidates: List[Dict[str, Any]],
    ) -> str:
        payload = {
            "user_text": user_text,
            "units": [
                {
                    "fact_id": unit.get("fact_id"),
                    "text": unit.get("text"),
                    "priority": unit.get("priority", 0),
                }
                for unit in units
            ],
            "seed_candidates": [
                {
                    "candidate_key": item.get("candidate_key"),
                    "score": item.get("score"),
                    "why": item.get("why"),
                }
                for item in seed_candidates
            ],
        }
        digest = hashlib.sha1(
            json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
        ).hexdigest()
        return f"fastctx:{digest}"
from llm.json_utils import loads_llm_json
