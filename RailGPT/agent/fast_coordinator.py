import hashlib
import json
import time
from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

from agent.psw import AgentState
from utils.simple_lru import SimpleLRUCache


@dataclass
class FastCoordinatorResult:
    context_bundle: Optional[Dict[str, Any]] = None
    rag_result: Optional[Dict[str, Any]] = None
    rag_context: str = ""
    presentation_plan: Dict[str, Any] = field(default_factory=dict)
    stats: Dict[str, Any] = field(default_factory=dict)


class FastCoordinator:
    def __init__(self, answer_generator, max_workers: int = 3, cache_size: int = 64):
        self.answer_generator = answer_generator
        self.max_workers = max_workers
        self._cache = SimpleLRUCache(cache_size)

    def prepare(
        self,
        user_text: str,
        facts: Dict[str, Any],
        psw=None,
    ) -> FastCoordinatorResult:
        if not self.answer_generator.is_fast_mode():
            return FastCoordinatorResult()

        cache_key = self._build_cache_key(user_text, facts)
        cached = self._cache.get(cache_key)
        if cached is not None:
            if psw is not None:
                psw.set_state(AgentState.FAST_COORD_READY, "fast coordinator cache hit")
            cached_copy = deepcopy(cached)
            cached_copy.stats["cache_hit"] = True
            return cached_copy

        started_at = time.perf_counter()
        if psw is not None:
            psw.set_state(AgentState.FAST_COORDINATING, "fast coordinator preparing context, rag and presentation plan")

        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            future_context = executor.submit(self._build_context_bundle, user_text, facts)
            future_rag = executor.submit(self._build_rag_payload, user_text, facts, psw)
            future_plan = executor.submit(self._plan_presentation, user_text, facts, psw)

            context_bundle = future_context.result()
            rag_result, rag_context = future_rag.result()
            presentation_plan = future_plan.result()

        result = FastCoordinatorResult(
            context_bundle=context_bundle,
            rag_result=rag_result,
            rag_context=rag_context,
            presentation_plan=presentation_plan,
            stats={
                "cache_hit": False,
                "elapsed_ms": int((time.perf_counter() - started_at) * 1000),
            },
        )
        self._cache.put(cache_key, result)

        if psw is not None:
            psw.set_state(
                AgentState.FAST_COORD_READY,
                f"fast coordinator ready in {result.stats['elapsed_ms']} ms",
            )

        return result

    def _build_cache_key(self, user_text: str, facts: Dict[str, Any]) -> str:
        payload = {
            "user_text": user_text,
            "facts": self.answer_generator._strip_fast_only_fields(facts),
            "mode": self.answer_generator.llm.get_mode() if hasattr(self.answer_generator.llm, "get_mode") else "unknown",
        }
        return hashlib.sha1(
            json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
        ).hexdigest()

    def _build_context_bundle(self, user_text: str, facts: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        return self.answer_generator.build_context_bundle(user_text=user_text, facts=facts)

    def _build_rag_payload(self, user_text: str, facts: Dict[str, Any], psw=None):
        if self._should_skip_rag(facts):
            if psw is not None:
                psw.set_state(AgentState.FAST_RAG_SKIPPED, "fast coordinator skipped rag for dynamic tool-grounded query")
            return None, ""

        if psw is not None:
            psw.set_state(AgentState.FAST_RAG_RETRIEVING, "fast coordinator retrieving railway knowledge")

        rag = getattr(self.answer_generator, "knowledge_rag", None)
        if rag is None:
            return None, ""

        result = rag.retrieve(
            user_text=user_text,
            facts=facts,
            context_bundle=None,
            top_k=4,
        )
        context = rag.build_prompt_context_from_result(result)
        return result, context

    def _should_skip_rag(self, facts: Dict[str, Any]) -> bool:
        dynamic_objects = {
            "left_ticket_12306",
            "transfer_12306",
            "path_detail",
            "path_future",
            "path_past",
            "path_stopcheck",
            "train_delay",
            "train_station_access",
            "station_board",
            "coach_layout",
            "train_route_map",
            "station_preselect",
            "train_preselect",
            "random_train",
        }
        for item in facts.get("queries", []):
            if not isinstance(item, dict):
                continue
            obj = str(item.get("object") or "").strip()
            if not obj:
                continue
            if obj in dynamic_objects or obj.startswith("station_to_station") or obj.startswith("s2s_"):
                return True
        return False

    def _plan_presentation(self, user_text: str, facts: Dict[str, Any], psw=None) -> Dict[str, Any]:
        if psw is not None:
            psw.set_state(AgentState.FAST_TEMPLATE_PLANNING, "fast coordinator planning response structure")

        queries = [item for item in facts.get("queries", []) if isinstance(item, dict)]
        objects = {str(item.get("object") or "") for item in queries}
        text = str(user_text or "")

        plan = {
            "answer_shape": "structured_brief",
            "sections": ["summary", "details"],
            "highlight_fields": ["key_fact"],
            "prefer_table": False,
            "tone": "clear and grounded",
        }

        if objects & {"s2s_benchmark", "station_to_station_mini", "station_to_station_future", "station_to_station_past"}:
            plan.update(
                {
                    "answer_shape": "ranked_trains",
                    "sections": ["summary", "ranking", "notes"],
                    "highlight_fields": ["train_no", "dep_time", "arr_time", "duration", "bureau", "emu"],
                    "prefer_table": False,
                    "tone": "decisive and travel-oriented",
                }
            )
        elif "left_ticket_12306" in objects:
            plan.update(
                {
                    "answer_shape": "ticket_snapshot",
                    "sections": ["summary", "availability", "notes"],
                    "highlight_fields": ["train_no", "seat_type", "availability", "dep_time", "arr_time"],
                    "prefer_table": True,
                    "tone": "direct and operational",
                }
            )
        elif "transfer_12306" in objects:
            plan.update(
                {
                    "answer_shape": "transfer_options",
                    "sections": ["summary", "options", "notes"],
                    "highlight_fields": ["leg_1", "leg_2", "hub", "layover", "total_duration"],
                    "prefer_table": False,
                    "tone": "comparative and concise",
                }
            )
        elif objects & {"path_detail", "path_future", "path_past", "path_stopcheck"}:
            plan.update(
                {
                    "answer_shape": "timetable_or_stop_matrix",
                    "sections": ["summary", "key_stops", "notes"],
                    "highlight_fields": ["train_no", "station", "arrive", "depart", "dwell"],
                    "prefer_table": True,
                    "tone": "operational and precise",
                }
            )
        elif objects & {"telecode", "name", "station", "station_preselect", "train_preselect", "random_train"}:
            plan.update(
                {
                    "answer_shape": "lookup_card",
                    "sections": ["answer", "details"],
                    "highlight_fields": ["station_name", "telecode", "city", "province"],
                    "prefer_table": False,
                    "tone": "compact and exact",
                }
            )
        elif objects & {"train_delay", "train_station_access", "station_board"}:
            plan.update(
                {
                    "answer_shape": "live_operation_status",
                    "sections": ["current_status", "details", "freshness"],
                    "highlight_fields": ["train_no", "station", "status", "delay", "platform", "gate"],
                    "prefer_table": True,
                    "tone": "live, precise, and source-aware",
                }
            )
        elif "coach_layout" in objects:
            plan.update(
                {
                    "answer_shape": "coach_layout_card",
                    "sections": ["summary", "composition", "capacity", "notes"],
                    "highlight_fields": ["carCode", "trainStyle", "formation", "capacity"],
                    "prefer_table": True,
                    "tone": "technical and concise",
                }
            )
        elif "train_route_map" in objects:
            plan.update(
                {
                    "answer_shape": "route_coordinate_summary",
                    "sections": ["summary", "station_coordinates", "line_segments"],
                    "highlight_fields": ["station", "longitude", "latitude", "coordinate_system"],
                    "prefer_table": True,
                    "tone": "technical and precise",
                }
            )

        if any(token in text for token in ("最快", "标杆", "benchmark", "fastest", "top")):
            plan["answer_shape"] = "ranked_trains"
            if "ranking" not in plan["sections"]:
                plan["sections"] = ["summary", "ranking", "notes"]

        return plan
