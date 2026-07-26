import json
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta

from agent.actions import (
    build_router_system_prompt,
    is_valid_action,
    validate_action_params,
    validate_query_semantics,
)
from agent.capabilities import (
    IntentEnvelope,
    active_workflow_steps,
    build_intent_envelope,
    build_missing_slot_contract,
    capability_catalog_for_mode,
    get_capability,
    grounded_slots_from_query_params,
    infer_composite_capability,
    routable_capability_objects,
    resolve_query_id,
)
from agent.context_agent import FastPlusContextAgent
from agent.date_normalizer import DateNormalizerAgent
from agent.pending_utils import compose_pending_question, normalize_pending_payload, normalize_pending_slots
from agent.psw import AgentState
from agent.rail_query_guard import clean_station_token, normalize_route_id, normalize_route_with_optional_via
from llm.llm_client import LLMClient
from llm.json_utils import loads_llm_json
from memory.entity_parser import extract_entities_from_text
from memory.session import SessionMemory
from tools.rail.station_dict import station_dict


# ============================================================
# Router v2.6.6 Ultimate Final Edition
# - Fully Compatible with Agent v2.6 Tools
# - Planner-safe JSON Contract
# - Strong ID Normalization + Hallucination Firewall
# ============================================================

EMU_FAMILY_ALIAS_MAP = {
    "AFZ": "CR400AFZ",
    "BFZ": "CR400BFZ",
    "AFBS": "CR400AFBS",
    "BFBS": "CR400BFBS",
    "AFAS": "CR400AFAS",
    "BFAS": "CR400BFAS",
}


class Router:
    def __init__(self,memory):
        self.llm = LLMClient()
        self.session = memory
        self.psw = None
        self._station_name_cache = None
        self.mode_profile = "deep"
        self.context_agent = None
        self.date_normalizer = None
        self._date_resolution_cache = {}
        self._semantic_council_cache = {}
        self.last_intent_envelope = IntentEnvelope()

    def set_mode(self, mode: str):
        normalized = str(mode or "").strip().lower()
        if normalized == "fast":
            normalized = "fast-go"
        if normalized not in {"fast-go", "fast-plus", "deep"}:
            normalized = "deep"

        self.mode_profile = normalized

        if hasattr(self.llm, "set_mode"):
            self.llm.set_mode(normalized)

    def is_fast_mode(self) -> bool:
        if self.mode_profile in {"fast-go", "fast-plus"}:
            return True
        get_mode = getattr(self.llm, "get_mode", None)
        if callable(get_mode):
            return get_mode() in {"fast", "fast-go", "fast-plus"}
        return False

    def is_fast_plus_mode(self) -> bool:
        return self.mode_profile == "fast-plus"

    def is_context_agent_enabled(self) -> bool:
        return self.mode_profile in {"fast-go", "fast-plus"}

    def _effective_mode_profile(self) -> str:
        mode = self.mode_profile
        get_mode = getattr(self.llm, "get_mode", None)
        if callable(get_mode):
            llm_mode = str(get_mode() or "").strip().lower()
            if llm_mode in {"fast", "fastgo"}:
                llm_mode = "fast-go"
            elif llm_mode == "fastplus":
                llm_mode = "fast-plus"
            if mode == "deep" and llm_mode in {"fast-go", "fast-plus"}:
                return llm_mode
        return mode

    def _get_context_agent(self):
        if self.context_agent is None:
            self.context_agent = FastPlusContextAgent()
        return self.context_agent

    def _get_date_normalizer(self):
        if self.date_normalizer is None:
            self.date_normalizer = DateNormalizerAgent(llm=self.llm)
        return self.date_normalizer

    def _legacy_set_router_state(self, state, detail: str):
        if self.psw:
            self.psw.set_state(state, detail)

    def _is_context_agent_usable(self, context_state: dict | None) -> bool:
        if not isinstance(context_state, dict) or not context_state:
            return False

        try:
            confidence_value = int(context_state.get("confidence") or 0)
        except Exception:
            confidence_value = 0

        # When resolved fields (route/trains) are present, accept lower confidence so
        # the injected context is not discarded even on heuristic-fallback turns.
        has_resolved_fields = bool(
            context_state.get("resolved_route") or
            context_state.get("resolved_train_numbers")
        )
        if has_resolved_fields:
            return confidence_value >= 15

        if confidence_value < 55:
            return False

        intent_category = str(context_state.get("intent_category") or "").strip().lower()
        if intent_category == "unknown":
            return False

        return True

    def _format_context_agent_summary(self, context_state: dict) -> str:
        if not isinstance(context_state, dict) or not context_state:
            return ""

        mode_label = str(self.mode_profile or "fast").strip().upper()
        lines = [f"{mode_label} context-agent summary:"]
        for key in (
            "intent_category",
            "rewritten_user_text",
            "resolved_route",
            "resolved_emu",
            "resolved_date",
            "resolved_query_object",
            "reason",
        ):
            value = str(context_state.get(key) or "").strip()
            if value:
                lines.append(f"- {key}: {value}")

        resolved_trains = context_state.get("resolved_train_numbers") or []
        if resolved_trains:
            lines.append("- resolved_train_numbers: " + ", ".join(str(item) for item in resolved_trains[:6]))

        resolved_stations = context_state.get("resolved_station_mentions") or []
        if resolved_stations:
            lines.append("- resolved_station_mentions: " + ", ".join(str(item) for item in resolved_stations[:8]))

        confidence = context_state.get("confidence")
        try:
            confidence_value = int(confidence)
        except Exception:
            confidence_value = 0
        if confidence_value > 0:
            lines.append(f"- confidence: {confidence_value}")

        return "\n".join(lines)

    def _run_context_agent(self, user_text: str, session: SessionMemory | None = None) -> dict:
        if not self.is_context_agent_enabled():
            if session:
                session.set_context_agent_state(None)
            return {}

        mode_label = str(self.mode_profile or "fast").strip().lower()
        self._set_router_state(
            AgentState.ROUTER_CONTEXT_AGENT,
            f"{mode_label} context agent normalizing user intent before expert routing",
        )
        try:
            context_agent = self._get_context_agent()
            if hasattr(context_agent, "set_mode_profile"):
                context_agent.set_mode_profile(self.mode_profile)
            context_state = context_agent.prepare(user_text=user_text, session=session)
        except Exception as exc:
            context_state = {
                "intent_category": "unknown",
                "rewritten_user_text": str(user_text or "").strip(),
                "resolved_route": "",
                "resolved_train_numbers": [],
                "resolved_emu": "",
                "resolved_date": "",
                "resolved_station_mentions": [],
                "resolved_query_object": "",
                "confidence": 0,
                "reason": f"context agent failed: {exc}",
            }

        if session:
            session.set_context_agent_state(context_state)

        detail = self._format_context_agent_summary(context_state) or f"{mode_label} context agent returned an empty summary"
        self._set_router_state(AgentState.ROUTER_CONTEXT_READY, detail)
        return context_state

    def _has_reusable_context(self, session: SessionMemory | None, context: dict | None = None) -> bool:
        if not session:
            return False

        if session.in_followup():
            return True

        anchors = session.get_anchor_snapshot() if hasattr(session, "get_anchor_snapshot") else {}
        if any(str(anchors.get(key) or "").strip() for key in ("route", "dep", "arr", "train", "emu", "date", "query_object", "query_type")):
            return True

        recent_messages = list(session.get_recent_messages(6)) if hasattr(session, "get_recent_messages") else []
        if len(recent_messages) < 2:
            return False

        pool = {}
        if isinstance(context, dict):
            pool = context.get("context_entity_pool") or context.get("recent_entity_pool") or {}
        if not pool:
            pool = self._build_context_entity_pool(session)

        return any(bool(pool.get(key)) for key in ("trains", "emus", "routes", "dates", "stations", "objects"))

    def _is_self_contained_fast_query(self, context: dict | None) -> bool:
        if not isinstance(context, dict):
            return False

        return bool(
            context.get("explicit_route")
            or context.get("explicit_train_numbers")
            or context.get("explicit_emu_id")
            or context.get("telecode")
        )

    def _should_run_context_agent(
        self,
        user_text: str,
        session: SessionMemory | None,
        context: dict | None,
    ) -> tuple[bool, str]:
        if not self.is_context_agent_enabled():
            return False, "context agent disabled for current mode"

        if not session:
            return False, "session context unavailable"

        context = context if isinstance(context, dict) else {}

        local_proposals: list[dict] = []
        try:
            local_proposals = self._collect_fast_route_proposals(context)
        except Exception:
            local_proposals = []

        top = local_proposals[0] if local_proposals else {}
        try:
            top_confidence = int(top.get("confidence") or 0)
        except Exception:
            top_confidence = 0

        top_tasks = top.get("tasks") if isinstance(top, dict) else []
        first_task = top_tasks[0] if isinstance(top_tasks, list) and top_tasks else {}
        first_action = str(first_task.get("action") or "").strip().lower() if isinstance(first_task, dict) else ""

        if self._is_self_contained_fast_query(context):
            return False, "query already contains sufficient hard entities"

        if context.get("has_partial_route_query_intent") and self._has_reusable_context(session, context):
            return True, "partial route change needs conversation context normalization"

        # Treat the local fast experts as the first-stage agent council. If they
        # can already produce a high-confidence action, the heavyweight context
        # agent would only add latency and may inject stale memory.
        if top_confidence >= 92:
            if first_action == "chat":
                return False, "obvious chat or railway knowledge turn"
            if first_action == "pending":
                if self._has_reusable_context(session, context):
                    return True, "local pending may be resolvable from conversation context"
                return False, "local experts can ask the missing slot directly"
            if first_action == "query":
                if not session.in_followup():
                    return False, "local experts are sufficient for this turn"
                if context.get("route_completed_from_context"):
                    return False, "local memory already completed the missing route side"
                if context.get("explicit_route") or context.get("explicit_train_numbers") or context.get("explicit_emu_id"):
                    return False, "local experts are sufficient for this follow-up"

        if not self._has_reusable_context(session, context):
            return False, "first turn or weak context without reusable anchors"

        if session.in_followup():
            return True, "active follow-up still needs context normalization"

        if any(
            bool(context.get(key))
            for key in (
                "asks_contextual_chat",
                "asks_contextual_social_chat",
                "asks_contextual_evidence_followup",
                "asks_contextual_route_followup",
                "asks_contextual_assignment",
                "affirmative_followup",
            )
        ):
            return True, "contextual reference needs memory normalization"

        return False, "local experts are sufficient for this turn"

    # ========================================================
    # Main Route Entry
    # ========================================================

    def route(self, user_text: str, session: SessionMemory) -> list[dict]:

        assert isinstance(user_text, str)
        assert user_text.strip()

        route_started_at = time.perf_counter()
        self.last_intent_envelope = IntentEnvelope()
        fast_mode = self.is_fast_mode()
        context_agent_enabled = self.is_context_agent_enabled()
        base_context = self._build_fast_route_context(
            user_text,
            session=session,
            context_agent_result={},
        ) if fast_mode or context_agent_enabled else {}

        if session and not context_agent_enabled:
            session.set_context_agent_state(None)

        context_agent_result = {}
        context_agent_usable = False
        if context_agent_enabled:
            should_run_context_agent, context_skip_reason = self._should_run_context_agent(
                user_text=user_text,
                session=session,
                context=base_context,
            )
            if should_run_context_agent:
                context_agent_result = self._run_context_agent(user_text, session=session)
                context_agent_usable = self._is_context_agent_usable(context_agent_result)
            else:
                if session:
                    session.set_context_agent_state(None)
                self._set_router_state(
                    AgentState.SKIP,
                    f"{self.mode_profile} context agent bypassed: {context_skip_reason}",
                )

        routing_context = self._build_fast_route_context(
            user_text,
            session=session,
            context_agent_result=context_agent_result if context_agent_usable else {},
        ) if context_agent_usable else base_context

        explicit_chat_turn = bool(routing_context.get("asks_chat"))
        fast_followup_ready = bool(
            fast_mode
            and (
                routing_context.get("asks_chat")
                or routing_context.get("route")
                or routing_context.get("train_numbers")
                or routing_context.get("emu_id")
                or routing_context.get("telecode")
                or routing_context.get("route_completed_from_context")
                or routing_context.get("has_partial_route_query_intent")
                or routing_context.get("asks_contextual_assignment")
                or routing_context.get("asks_general_rail_knowledge")
            )
        )
        followup_mode = bool(
            session
            and session.in_followup()
            and not explicit_chat_turn
            and (not context_agent_enabled or not context_agent_usable)
            and not fast_followup_ready
        )

        if fast_mode and not followup_mode:
            pending_fallback = []
            fast_tasks = self._try_fast_route(
                user_text,
                session=session,
                context_agent_result=context_agent_result if context_agent_usable else {},
                prefetched_context=routing_context,
            )
            if fast_tasks:
                safe = self._repair_fast_tasks(
                    self._safe_parse_tasks(json.dumps(fast_tasks, ensure_ascii=False), context=routing_context),
                    strict_invalid=False,
                )
                safe = self._enrich_pending_tasks(
                    safe,
                    self._build_fast_route_context(
                        user_text,
                        session=session,
                        context_agent_result=context_agent_result if context_agent_usable else {},
                    ),
                )
                if self._should_escalate_pending_to_fast_llm(safe, routing_context):
                    pending_fallback = safe
                    self._set_router_state(
                        AgentState.ROUTER_MICRO_LLM,
                        "fast router pending hit looks semantically ambiguous, escalating to compact llm arbiter",
                    )
                else:
                    self._set_router_state(
                        AgentState.ROUTER_FAST_HIT,
                        f"fast router experts hit in {self._elapsed_ms(route_started_at)} ms",
                    )
                    print("\n================ Fast Route Tasks ================\n")
                    print(json.dumps(safe, indent=2, ensure_ascii=False))
                    return self._finalize_routed_tasks(safe, routing_context)

            if not pending_fallback:
                self._set_router_state(
                    AgentState.ROUTER_MICRO_LLM,
                    "fast router experts missed, using compact llm arbiter",
                )

            compact_safe = self._route_with_fast_llm(
                user_text,
                session,
                context_agent_result=context_agent_result if context_agent_usable else {},
                prefetched_context=routing_context,
            )
            if compact_safe:
                self._set_router_state(
                    AgentState.ROUTER_FAST_HIT,
                    f"fast router compact llm resolved in {self._elapsed_ms(route_started_at)} ms",
                )
                print("\n================ Fast Route Tasks ================\n")
                print(json.dumps(compact_safe, indent=2, ensure_ascii=False))
                return self._finalize_routed_tasks(compact_safe, routing_context)

            if pending_fallback:
                self._set_router_state(
                    AgentState.ROUTER_FAST_HIT,
                    f"fast router retained local pending fallback in {self._elapsed_ms(route_started_at)} ms",
                )
                print("\n================ Fast Route Tasks ================\n")
                print(json.dumps(pending_fallback, indent=2, ensure_ascii=False))
                return self._finalize_routed_tasks(pending_fallback, routing_context)

            self._set_router_state(
                AgentState.ROUTER_LEGACY_FALLBACK,
                "fast router compact arbiter failed, using legacy router prompt",
            )

        now = datetime.now().strftime("%Y-%m-%d %H:%M")

        messages = []

        # 1) Base system prompt
        messages.append({
            "role": "system",
            "content": build_router_system_prompt()
                       + f"\n\n[Router Runtime Clock] Beijing Time Now = {now}"
        })

        if session:
            memory_context = session.get_memory_context()
            if memory_context:
                messages.append({
                    "role": "system",
                    "content": (
                        "Conversation memory recall is available for slot filling.\n"
                        "Prefer these anchors before asking the user to repeat known entities.\n"
                        f"{memory_context}"
                    ),
                })

            context_agent_context = session.get_context_agent_context(min_confidence=55)
            if fast_mode and context_agent_context:
                messages.append({
                    "role": "system",
                    "content": context_agent_context,
                })

        # 2) Follow-up mode injection (BEFORE user)
        if session and session.in_followup():
            messages.append({
                "role": "system",
                "content": (
                    "=================================================\n"
                    "FOLLOW-UP MODE ACTIVE\n"
                    "=================================================\n"
                    "User is replying to your previous clarification.\n"
                    "Do NOT treat this as a new query.\n"
                    "Fill missing slots and output the correct query task.\n\n"
                    f"{session.get_followup_context()}\n"
                )
            })

        # 2.5 Inject recent conversation context
        if session:
            if hasattr(session, "build_agent_context_view"):
                context_view = session.build_agent_context_view(
                    role="router",
                    mode=self.mode_profile,
                    user_text=user_text,
                    include_dialogue=False,
                )
                messages.append({
                    "role": "system",
                    "content": (
                        "Structured AgentContextPackage for legacy router fallback:\n"
                        f"{json.dumps(context_view, ensure_ascii=False)}"
                    ),
                })
            if hasattr(session, "build_llm_history"):
                recent_messages = session.build_llm_history(
                    mode=self.mode_profile,
                    exclude_current_user=True,
                    latest_user_text=user_text,
                )
            else:
                recent_messages = list(session.get_recent_messages())
                if recent_messages and recent_messages[-1].get("role") == "user" and recent_messages[-1].get("content") == user_text:
                    recent_messages = recent_messages[:-1]
            messages.extend(recent_messages)
        # 3) User message
        messages.append({
            "role": "user",
            "content": (
                context_agent_result.get("rewritten_user_text")
                if self._is_context_agent_usable(context_agent_result) and isinstance(context_agent_result, dict) and context_agent_result.get("rewritten_user_text")
                else user_text
            )
        })

        # 4) LLM call
        raw = self.llm.generate(messages)

        print("\n================ Raw LLM Output ================\n")
        print(raw)

        legacy_context = self._build_fast_route_context(
            user_text,
            session=session,
            context_agent_result=context_agent_result if context_agent_usable else {},
        )
        safe = self._repair_fast_tasks(self._safe_parse_tasks(raw, context=legacy_context), strict_invalid=True)
        safe = self._apply_context_date_to_tasks(safe, legacy_context)
        safe = self._enrich_pending_tasks(
            safe,
            legacy_context,
        )

        print("\n================ Safe Tasks ====================\n")
        print(json.dumps(safe, indent=2, ensure_ascii=False))

        return self._finalize_routed_tasks(safe, legacy_context)

    def _try_fast_route(
        self,
        user_text: str,
        session: SessionMemory | None = None,
        context_agent_result: dict | None = None,
        prefetched_context: dict | None = None,
    ) -> list[dict]:
        context = prefetched_context if isinstance(prefetched_context, dict) else self._build_fast_route_context(
            user_text,
            session=session,
            context_agent_result=context_agent_result,
        )
        if not isinstance(context, dict) or not context.get("text"):
            return []
        context = dict(context)
        context["capability_continuation_candidate"] = self._has_capability_continuation_candidate(context)
        proposals = self._collect_fast_route_proposals(context)

        compact_text = re.sub(r"\s+", "", str(context.get("text") or ""))
        needs_live_tool_arbitration = self._needs_live_operational_tool_arbitration(context)
        needs_local_conflict_arbitration = self._has_pending_chat_proposal_conflict(proposals)
        needs_pending_semantic_arbitration = self._should_arbitrate_complex_pending(
            proposals,
            context,
        )
        profile_index = (
            ((context.get("agent_context_package") or {}).get("memory_context_package") or {}).get("profile_index")
            or []
        )
        top_tasks = proposals[0].get("tasks") if proposals and isinstance(proposals[0].get("tasks"), list) else []
        top_action = str((top_tasks[0] if top_tasks else {}).get("action") or "")
        needs_profile_semantic_arbitration = bool(
            profile_index
            and not context.get("latest_turn_has_new_hard_entities")
            and (not proposals or top_action == "pending")
        )
        needs_capability_continuation_arbitration = bool(context.get("capability_continuation_candidate"))
        # Semantic interpretation belongs to the router council. Local experts
        # remain a latency-safe fallback when the model is unavailable or emits
        # an invalid contract; they no longer get first refusal on user meaning.
        wants_semantic_first = bool(compact_text)

        if wants_semantic_first:
            semantic_context = self._attach_semantic_router_council(
                context,
                session=session,
                force=True,
            )
            semantic_tasks = self._resolve_semantic_router_council_tasks(semantic_context, session=session)
            if semantic_tasks:
                consensus = semantic_context.get("semantic_consensus") or {}
                label = str(consensus.get("preferred_action") or "chat").strip() or "chat"
                confidence = int(consensus.get("confidence") or 0)
                self._set_router_state(
                    AgentState.ROUTER_FAST_EXPERTS,
                    f"semantic router council chose {label} conf={confidence}",
                )
                return semantic_tasks

        repaired: list[dict] = []
        if proposals:
            top = proposals[0]
            self._set_router_state(
                AgentState.ROUTER_FAST_EXPERTS,
                f"{len(proposals)} expert proposals, winner={top['name']} conf={top['confidence']}",
            )

            if top["confidence"] >= 72:
                repaired = self._repair_fast_tasks(top["tasks"], strict_invalid=False)
                if repaired and repaired[0].get("action") != "pending":
                    return repaired

        if self._should_run_semantic_router_council(context):
            semantic_context = self._attach_semantic_router_council(context, session=session)
            semantic_tasks = self._resolve_semantic_router_council_tasks(semantic_context, session=session)
            if semantic_tasks:
                consensus = semantic_context.get("semantic_consensus") or {}
                label = str(consensus.get("preferred_action") or "chat").strip() or "chat"
                confidence = int(consensus.get("confidence") or 0)
                self._set_router_state(
                    AgentState.ROUTER_FAST_EXPERTS,
                    f"semantic router council chose {label} conf={confidence}",
                )
                return semantic_tasks

        if repaired:
            return repaired

        if not proposals:
            return []

        top = proposals[0]
        if top["confidence"] < 72:
            return []

        self._set_router_state(
            AgentState.ROUTER_LEGACY_FALLBACK,
            "fast router proposal invalid after route recovery swarm, escalating to compact llm",
        )
        return []

    @staticmethod
    def _has_pending_chat_proposal_conflict(proposals: list[dict] | None) -> bool:
        """Escalate close pending-vs-chat votes instead of blindly taking one point."""

        proposals = [item for item in (proposals or []) if isinstance(item, dict)]
        if not proposals:
            return False
        top = proposals[0]
        top_tasks = top.get("tasks") if isinstance(top.get("tasks"), list) else []
        top_action = str((top_tasks[0] if top_tasks else {}).get("action") or "")
        if top_action != "pending":
            return False
        top_confidence = int(top.get("confidence") or 0)

        for proposal in proposals[1:]:
            tasks = proposal.get("tasks") if isinstance(proposal.get("tasks"), list) else []
            action = str((tasks[0] if tasks else {}).get("action") or "")
            confidence = int(proposal.get("confidence") or 0)
            if action == "chat" and confidence >= 88 and top_confidence - confidence <= 6:
                return True
        return False

    @staticmethod
    def _should_arbitrate_complex_pending(
        proposals: list[dict] | None,
        context: dict | None,
    ) -> bool:
        """Use the semantic council before accepting a complex local pending result.

        This is a cost gate, not an intent rule: deterministic code only measures
        whether a natural-language turn is too rich for one missing-slot expert.
        The council still decides between chat, query, and pending.
        """

        proposals = [item for item in (proposals or []) if isinstance(item, dict)]
        if not proposals or not isinstance(context, dict):
            return False

        top_tasks = proposals[0].get("tasks") if isinstance(proposals[0].get("tasks"), list) else []
        top_action = str((top_tasks[0] if top_tasks else {}).get("action") or "")
        if top_action != "pending":
            return False

        package = context.get("agent_context_package") or {}
        memory_package = package.get("memory_context_package") if isinstance(package, dict) else {}
        if isinstance(memory_package, dict) and memory_package.get("profile_index"):
            # The profile only opens semantic arbitration. It never selects a
            # route or fills a slot by itself.
            return True

        if (
            context.get("explicit_route")
            or context.get("explicit_train_numbers")
            or context.get("explicit_emu_id")
            or context.get("telecode")
        ):
            return False

        compact = re.sub(r"\s+", "", str(context.get("raw_text") or context.get("text") or ""))
        if not compact:
            return False

        clause_count = len([part for part in re.split(r"[，。！？；,.!?;]+", compact) if part])
        return len(compact) >= 28 or clause_count >= 3

    def _merge_entity_pool(self, pool: dict, entities: dict | None):
        if not isinstance(pool, dict) or not isinstance(entities, dict):
            return

        for key in ("trains", "emus", "routes", "dates", "stations", "objects", "tokens"):
            values = entities.get(key, [])
            if isinstance(values, list):
                for value in values:
                    normalized = str(value or "").strip()
                    if normalized and normalized not in pool[key]:
                        pool[key].append(normalized)
            elif isinstance(values, str):
                normalized = values.strip()
                if normalized and normalized not in pool[key]:
                    pool[key].append(normalized)

    def _build_context_entity_pool(self, session: SessionMemory | None, recent_entity_pool: dict | None = None) -> dict:
        pool = {
            "trains": [],
            "emus": [],
            "routes": [],
            "dates": [],
            "stations": [],
            "objects": [],
            "tokens": [],
        }

        self._merge_entity_pool(pool, recent_entity_pool or {})
        if not session:
            return pool

        anchors = session.get_anchor_snapshot() if hasattr(session, "get_anchor_snapshot") else {}
        route = str(anchors.get("route") or "").strip()
        if route:
            self._merge_entity_pool(pool, {"routes": [route]})
            if "-" in route:
                dep, arr = route.split("-", 1)
                self._merge_entity_pool(pool, {"stations": [dep, arr]})
        for anchor_key, pool_key in (
            ("train", "trains"),
            ("emu", "emus"),
            ("date", "dates"),
            ("query_object", "objects"),
            ("query_type", "objects"),
            ("dep", "stations"),
            ("arr", "stations"),
        ):
            value = str(anchors.get(anchor_key) or "").strip()
            if value:
                self._merge_entity_pool(pool, {pool_key: [value]})

        recall_bundle = getattr(session, "memory_recall", {}) or {}
        memory_package = recall_bundle.get("memory_context_package", {}) if isinstance(recall_bundle, dict) else {}
        if isinstance(memory_package, dict) and memory_package:
            anchor_candidates = memory_package.get("hard_anchors", {}) or {}
        else:
            for section_key in ("session", "episodic"):
                for item in recall_bundle.get(section_key, []) or []:
                    if not isinstance(item, dict):
                        continue
                    entities = item.get("entities")
                    if isinstance(entities, dict):
                        self._merge_entity_pool(pool, entities)
                        continue
                    text = str(item.get("text") or "").strip()
                    if text:
                        self._merge_entity_pool(pool, extract_entities_from_text(text))
            anchor_candidates = recall_bundle.get("anchor_candidates", {}) or {}

        route = str(anchor_candidates.get("route") or "").strip()
        if route:
            self._merge_entity_pool(pool, {"routes": [route]})
            if "-" in route:
                dep, arr = route.split("-", 1)
                self._merge_entity_pool(pool, {"stations": [dep, arr]})
        for anchor_key, pool_key in (
            ("train", "trains"),
            ("emu", "emus"),
            ("date", "dates"),
            ("query_object", "objects"),
            ("query_type", "objects"),
            ("dep", "stations"),
            ("arr", "stations"),
        ):
            value = str(anchor_candidates.get(anchor_key) or "").strip()
            if value:
                self._merge_entity_pool(pool, {pool_key: [value]})

        return pool

    def _resolve_date_for_turn(
        self,
        *,
        raw_text: str,
        rewritten_user_text: str = "",
        session: SessionMemory | None = None,
    ) -> dict:
        current_date = datetime.now().strftime("%Y-%m-%d")
        anchor_date = session.resolve_anchor("date") if session else ""
        mode = self._effective_mode_profile()
        key = json.dumps(
            {
                "raw_text": raw_text,
                "rewritten_user_text": rewritten_user_text,
                "mode": mode,
                "current_date": current_date,
                "anchor_date": anchor_date,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
        cached = self._date_resolution_cache.get(key)
        if cached:
            return dict(cached)

        result = self._get_date_normalizer().normalize(
            latest_user_text=raw_text,
            rewritten_user_text=rewritten_user_text,
            session=session,
            mode=mode,
            current_date=current_date,
        )
        if not isinstance(result, dict):
            result = {}

        self._date_resolution_cache[key] = dict(result)
        if len(self._date_resolution_cache) > 128:
            self._date_resolution_cache.pop(next(iter(self._date_resolution_cache)), None)
        return dict(result)

    def _build_fast_route_context(
        self,
        user_text: str,
        session: SessionMemory | None = None,
        context_agent_result: dict | None = None,
    ) -> dict:
        raw_text = str(user_text or "").strip()
        context_agent_result = context_agent_result if isinstance(context_agent_result, dict) else {}
        try:
            context_agent_confidence = int(context_agent_result.get("confidence") or 0)
        except Exception:
            context_agent_confidence = 0

        use_context_agent = self._is_context_agent_usable(context_agent_result)
        rewritten_user_text = str(context_agent_result.get("rewritten_user_text") or "").strip()
        raw_route_source = self._strip_route_noise(raw_text)
        raw_route_candidates = self._extract_route_candidates(raw_route_source)
        raw_station_mentions = self._extract_station_mentions(raw_route_source)
        raw_partial_route = self._has_partial_route_query_intent(
            text=raw_text,
            station_mentions=raw_station_mentions,
            route_candidates=raw_route_candidates,
            train_numbers=self._extract_train_numbers(raw_text.upper()),
            emu_id=self._extract_emu_id(raw_text.upper()),
            telecode=(re.search(r"\b([A-Z]{3})\b", raw_text.upper()) or [None, None])[1],
        )
        resolved_route_hint = str(context_agent_result.get("resolved_route") or "").strip()
        anchor_route = str(session.resolve_anchor("route") or "").strip() if session else ""
        if (
            use_context_agent
            and raw_partial_route
            and not (session and session.in_followup())
            and len(raw_station_mentions) == 1
            and resolved_route_hint
            and resolved_route_hint == anchor_route
        ):
            # Do not let a context rewrite turn an old route into explicit text
            # for a fresh single-sided request. The semantic council still sees
            # the original dialogue and can decide whether the turn continues it.
            use_context_agent = False
        text = rewritten_user_text if use_context_agent and rewritten_user_text else raw_text
        date_resolution = self._resolve_date_for_turn(
            raw_text=raw_text,
            rewritten_user_text=rewritten_user_text if use_context_agent else "",
            session=session,
        )
        text_upper = text.upper()
        tele_match = re.search(r"\b([A-Z]{3})\b", text_upper)
        route_source_text = self._strip_route_noise(text)
        explicit_route_candidates = self._extract_route_candidates(route_source_text)
        route_candidates = list(explicit_route_candidates)
        explicit_train_numbers = self._extract_train_numbers(text_upper)
        train_numbers = list(explicit_train_numbers)
        explicit_emu_id = self._extract_emu_id(text_upper)
        emu_preferences = self._extract_emu_preferences(text_upper)
        emu_id = explicit_emu_id
        station_mentions = self._extract_station_mentions(route_source_text)
        bureau_preferences = self._extract_bureau_preferences(text)
        high_mode_date_llm_only = self._effective_mode_profile() in {"fast-plus", "deep"}
        raw_explicit_date = None if high_mode_date_llm_only else self._extract_explicit_date(raw_text)
        rewritten_explicit_date = None if high_mode_date_llm_only else self._extract_explicit_date(text)
        explicit_date = raw_explicit_date or rewritten_explicit_date
        query_date = datetime.now().strftime("%Y-%m-%d")
        query_date_source = "default_today"
        if raw_explicit_date:
            query_date = raw_explicit_date.strftime("%Y-%m-%d")
            query_date_source = "explicit_user_raw"
        elif date_resolution.get("has_date") and date_resolution.get("normalized_date"):
            query_date = str(date_resolution.get("normalized_date"))
            source = str(date_resolution.get("date_source") or "date_normalizer")
            query_date_source = f"date_normalizer:{source}"
        elif rewritten_explicit_date:
            query_date = rewritten_explicit_date.strftime("%Y-%m-%d")
            query_date_source = "explicit_context_rewrite"
        date_locked = query_date_source != "default_today"
        route_completed_from_context = False
        followup_active = bool(session and session.in_followup())
        followup_slots = list((session.followup_slots or {}).get("slot", [])) if followup_active else []
        followup_context = (
            dict((session.followup_slots or {}).get("context") or {})
            if followup_active and isinstance((session.followup_slots or {}).get("context"), dict)
            else {}
        )
        direction = str(followup_context.get("direction") or "").strip().lower()
        if direction not in {"arrival", "departure"}:
            direction = ""
        affirmative_followup = followup_active and self._looks_like_affirmative_reply(text)

        if followup_active:
            followup_train = str(followup_context.get("train") or followup_context.get("train_no") or "").strip().upper()
            if followup_train and not train_numbers:
                train_numbers = re.findall(r"[GDKTZC]\d{1,5}", followup_train)[:5]
            followup_date = str(followup_context.get("date") or "").strip()
            if followup_date and not date_locked:
                query_date = followup_date
                query_date_source = "followup_contract"
                date_locked = True

        recent_entity_pool = session.get_recent_entity_pool() if session else {}
        context_entity_pool = self._build_context_entity_pool(session, recent_entity_pool)
        agent_context_package = (
            session.build_agent_context_view(
                role="router",
                mode=self.mode_profile,
                user_text=raw_text,
                date_resolution=date_resolution,
                include_dialogue=True,
            )
            if session and hasattr(session, "build_agent_context_view")
            else (
                session.build_agent_context_package(
                    mode=self.mode_profile,
                    user_text=raw_text,
                    date_resolution=date_resolution,
                )
                if session and hasattr(session, "build_agent_context_package")
                else {}
            )
        )
        dialogue_excerpt = str(agent_context_package.get("dialogue_excerpt") or "").strip()
        last_assistant_message = str(agent_context_package.get("last_assistant_message") or "").strip()
        has_recent_substantive_answer = bool(agent_context_package.get("has_recent_substantive_answer"))
        partial_route_query_intent = self._has_partial_route_query_intent(
            text=text,
            station_mentions=station_mentions,
            route_candidates=route_candidates,
            train_numbers=train_numbers,
            emu_id=emu_id,
            telecode=tele_match.group(1) if tele_match else None,
        )

        if use_context_agent:
            resolved_route = str(context_agent_result.get("resolved_route") or "").strip()
            if (
                resolved_route
                and partial_route_query_intent
                and not followup_active
                and len(station_mentions) == 1
                and session
                and resolved_route == str(session.resolve_anchor("route") or "").strip()
            ):
                # A context model may echo a stale route anchor for a fresh
                # single-sided query. Assistant/memory context can suggest a
                # completion, but cannot silently harden the old OD pair.
                resolved_route = ""
            if resolved_route and resolved_route not in route_candidates:
                route_candidates.insert(0, resolved_route)

            resolved_train_numbers = [
                str(item).strip()
                for item in (context_agent_result.get("resolved_train_numbers") or [])
                if str(item or "").strip()
            ]
            for train_no in reversed(resolved_train_numbers):
                if train_no not in train_numbers:
                    train_numbers.insert(0, train_no)

            resolved_emu = str(context_agent_result.get("resolved_emu") or "").strip()
            if resolved_emu and not emu_id:
                emu_id = resolved_emu

            resolved_date = str(context_agent_result.get("resolved_date") or "").strip()
            if resolved_date and not date_locked and not high_mode_date_llm_only:
                query_date = resolved_date
                query_date_source = "context_agent"
                date_locked = True

            resolved_station_mentions = [
                str(item).strip()
                for item in (context_agent_result.get("resolved_station_mentions") or [])
                if str(item or "").strip()
            ]
            for station_name in resolved_station_mentions:
                if station_name not in station_mentions:
                    station_mentions.append(station_name)

        if session:
            if not route_candidates and not partial_route_query_intent and self._should_reuse_route_anchor(text):
                anchor_route = session.resolve_anchor("route") or next(iter(context_entity_pool.get("routes") or []), None)
                if anchor_route:
                    route_candidates = [anchor_route]

            if not train_numbers and self._should_reuse_train_anchor(text):
                anchor_train = session.resolve_anchor("train") or next(iter(context_entity_pool.get("trains") or []), None)
                if anchor_train:
                    train_numbers = [anchor_train]

            if not emu_id and self._should_reuse_emu_anchor(text):
                anchor_emu = session.resolve_anchor("emu") or next(iter(context_entity_pool.get("emus") or []), None)
                if anchor_emu:
                    emu_id = anchor_emu

            if not date_locked and not high_mode_date_llm_only and self._should_reuse_date_anchor(text):
                anchor_date = session.resolve_anchor("date") or next(iter(context_entity_pool.get("dates") or []), None)
                if anchor_date:
                    query_date = anchor_date
                    query_date_source = "memory_anchor"
                    date_locked = True

            if affirmative_followup and not followup_slots:
                if not route_candidates:
                    anchor_route = session.resolve_anchor("route") or next(iter(context_entity_pool.get("routes") or []), None)
                    if anchor_route:
                        route_candidates = [anchor_route]
                        route_completed_from_context = True

                if not train_numbers:
                    anchor_train = session.resolve_anchor("train") or next(iter(context_entity_pool.get("trains") or []), None)
                    if anchor_train:
                        train_numbers = [anchor_train]

                if not emu_id:
                    anchor_emu = session.resolve_anchor("emu") or next(iter(context_entity_pool.get("emus") or []), None)
                    if anchor_emu:
                        emu_id = anchor_emu

                if not date_locked:
                    anchor_date = session.resolve_anchor("date") or next(iter(context_entity_pool.get("dates") or []), None)
                    if anchor_date:
                        query_date = anchor_date
                        query_date_source = "memory_anchor"
                        date_locked = True

            if session.in_followup() and not route_candidates and len(station_mentions) == 1 and partial_route_query_intent:
                station_name = station_mentions[0]
                candidate_stations = [
                    item
                    for item in (context_entity_pool.get("stations") or [])
                    if item and item != station_name
                ]
                if candidate_stations:
                    if any(token in text for token in ("从", "出发", "坐")):
                        route_candidates = [f"{station_name}-{candidate_stations[0]}"]
                        route_completed_from_context = True
                    elif any(token in text for token in ("到", "去", "前往", "抵达")):
                        route_candidates = [f"{candidate_stations[0]}-{station_name}"]
                        route_completed_from_context = True

        route_value = route_candidates[0] if route_candidates else None
        dep = ""
        arr = ""
        if route_value and "-" in route_value:
            dep, arr = route_value.split("-", 1)
        elif len(station_mentions) == 1 and partial_route_query_intent:
            if any(token in text for token in ("从", "出发", "坐")):
                dep = station_mentions[0]
            elif any(token in text for token in ("到", "去", "前往", "抵达")):
                arr = station_mentions[0]

        anchor_query_object = (
            str(followup_context.get("query_object") or "").strip()
            or (session.resolve_anchor("query_object") if session else None)
        )
        anchor_query_type = session.resolve_anchor("query_type") if session else None
        latest_turn_has_new_hard_entities = bool(
            explicit_route_candidates
            or explicit_train_numbers
            or explicit_emu_id
            or tele_match
            or query_date_source == "explicit_user_raw"
            or query_date_source.startswith("date_normalizer:latest_user")
            or query_date_source.startswith("date_normalizer:relative_user")
        )

        benchmark_tokens = (
            "最快",
            "标杆",
            "最强",
            "怎么坐最快",
            "怎么走最快",
            "最快的车",
            "最快方案",
            "推荐最快",
        )
        listing_tokens = (
            "有什么车",
            "有哪些车",
            "有啥车",
            "有什么高铁",
            "有哪些高铁",
            "有哪些直达",
            "有哪几趟直达",
            "直达车",
            "直达列车",
            "直达高铁",
            "直达",
            "班次",
        )
        travel_advice_tokens = (
            "怎么坐",
            "怎么走",
            "怎么去",
            "推荐",
            "安排一下",
            "行程",
            "方案",
            "坐哪趟",
        )

        context_agent_intent = str(context_agent_result.get("intent_category") or "").strip().lower()
        context_agent_query_object = str(context_agent_result.get("resolved_query_object") or "").strip()

        asks_path = any(
            token in text
            for token in ("线路", "运行线路", "路线", "具体路线", "运行路线", "走向", "走哪条线", "沿着什么高铁线", "沿着什么线路", "实时位置", "现在到哪", "到哪了", "运行到哪")
        )
        has_delay_language = any(
            token in text
            for token in ("晚点", "晚了多久", "正晚点", "晚点了多久", "延误", "早点")
        )
        has_delay_reference = any(
            token in text
            for token in ("它", "这趟", "这班", "那趟", "那班", "这个车", "这车")
        )
        # A live delay lookup is train-scoped. Broad questions about which line or
        # time band is more punctual remain railway knowledge questions.
        asks_live_delay = bool(
            has_delay_language
            and (
                explicit_train_numbers
                or (train_numbers and self._should_reuse_train_anchor(text))
                or has_delay_reference
            )
        )
        asks_train_terminal = self._looks_like_train_terminal_intent(text)
        asks_stopcheck = any(
            token in text
            for token in (
                "停不停",
                "停不停车",
                "停靠",
                "停吗",
                "会不会停",
                "是否停",
                "是否停靠",
                "有没有停",
                "经停",
                "路过",
                "通过",
                "哪些停",
                "只停",
                "加停",
                "增停",
                "开始加停",
                "什么时候开始加停",
                "停哪几站",
            )
        )
        stopcheck_stations = self._extract_stopcheck_stations(text, station_mentions)
        asks_identity_chat = any(
            token in text
            for token in ("你是谁", "你是？", "你是什么", "介绍一下你自己", "你叫什麽", "你叫什么")
        )
        asks_capability_chat = any(
            token in text
            for token in ("你能做什么", "你会什么", "你可以做什么", "怎么用", "如何使用", "能查什么")
        )
        asks_smalltalk_chat = any(
            token in text
            for token in ("你好", "hello", "hi", "嗨", "在吗", "谢谢", "辛苦了", "再见", "早上好", "晚上好", "哈哈", "哈哈哈", "呵呵", "hhh", "hhhh", "2333", "笑死", "绷不住")
        )
        asks_explainer_chat = (
            any(token in text for token in ("什么是", "什么叫", "解释一下", "科普一下", "介绍一下"))
            and not route_candidates
            and not train_numbers
            and not emu_id
            and not tele_match
        )
        asks_general_rail_knowledge = self._looks_like_general_rail_knowledge_question(
            text=text,
            route_candidates=route_candidates,
            train_numbers=train_numbers,
            emu_id=emu_id,
            telecode=tele_match.group(1) if tele_match else None,
            station_mentions=station_mentions,
        )
        if not asks_general_rail_knowledge:
            asks_general_rail_knowledge = self._looks_like_line_station_affiliation_question(text)
        if has_delay_language and not asks_live_delay:
            asks_general_rail_knowledge = True
        asks_generic_chat = self._looks_like_generic_chat_turn(text)
        asks_contextual_social_chat = self._looks_like_contextual_social_reply(
            text,
            recent_entity_pool=context_entity_pool,
        )
        asks_contextual_evidence_followup = self._looks_like_contextual_evidence_followup(
            text,
            recent_entity_pool=context_entity_pool,
        )
        asks_contextual_chat = self._looks_like_contextual_chat_turn(
            text,
            recent_entity_pool=context_entity_pool,
        )
        if affirmative_followup:
            asks_contextual_chat = False
        if asks_contextual_evidence_followup:
            asks_generic_chat = False
            asks_general_rail_knowledge = False
            asks_contextual_chat = False
        asks_recommendation = any(
            token in text for token in ("推荐", "推荐一二", "推荐一下", "给我推荐", "帮我推荐", "适合")
        )
        asks_contextual_route_followup = self._looks_like_contextual_route_followup(
            text,
            has_route=bool(route_candidates),
            anchor_query_object=anchor_query_object or anchor_query_type,
        )
        if affirmative_followup and not followup_slots and route_candidates:
            asks_contextual_route_followup = True
        asks_contextual_assignment = self._looks_like_contextual_assignment_followup(
            text,
            recent_entity_pool=context_entity_pool,
        )
        asks_train_comparison_lookup = self._has_explicit_train_comparison_lookup_intent(text, train_numbers)
        has_partial_route_query_intent = partial_route_query_intent

        if context_agent_intent == "chat":
            asks_generic_chat = True
        if context_agent_intent == "general_rail":
            asks_general_rail_knowledge = True
        if context_agent_intent == "train_assignment":
            asks_contextual_assignment = True
        if context_agent_intent == "train_path":
            asks_path = True
        if context_agent_intent == "train_stopcheck":
            asks_stopcheck = True
        if context_agent_intent in {"train_assignment", "train_path", "train_stopcheck"}:
            asks_recommendation = False
            asks_contextual_route_followup = False
        if asks_train_comparison_lookup:
            asks_general_rail_knowledge = False
            asks_generic_chat = False
            asks_contextual_social_chat = False
            asks_contextual_chat = False

        asks_assignment = context_agent_intent == "train_assignment" or any(
            token in text for token in ("车底", "担当", "车型", "智能动车组", "车组", "长编组", "短编组", "重联", "单组", "16节", "8节", "编组")
        )
        asks_path = context_agent_intent == "train_path" or asks_path or asks_contextual_evidence_followup or asks_train_comparison_lookup or (
            bool(train_numbers) and self._looks_like_train_line_membership_intent(text)
        ) or any(
            token in text for token in ("经停", "停哪些站", "时刻表", "停站", "路径", "路线", "具体路线", "运行路线", "走向", "实时位置", "现在到哪", "到哪了", "运行到哪")
        )
        asks_train_overview = context_agent_intent == "train_overview" or asks_train_comparison_lookup or self._has_train_overview_intent(text)
        asks_ticket = context_agent_intent == "route_ticket" or any(
            token in text for token in ("余票", "有票", "票吗", "票务")
        )
        asks_transfer = context_agent_intent == "route_transfer" or "中转" in text
        contextual_route_compare_tokens = (
            "对比",
            "比较",
            "排行",
            "排名",
            "榜单",
            "是不是标杆",
            "是不是最快",
            "谁最快",
            "哪个最快",
        )
        asks_contextual_route_compare = bool(
            route_candidates
            and (anchor_query_object or anchor_query_type)
            and any(token in text for token in contextual_route_compare_tokens)
        )
        if asks_contextual_route_compare:
            asks_generic_chat = False
            asks_general_rail_knowledge = False
            asks_contextual_social_chat = False
            asks_contextual_chat = False
        asks_benchmark = (
            context_agent_intent == "route_benchmark"
            or any(token in text for token in benchmark_tokens)
            or asks_contextual_route_compare
        )
        asks_listing = context_agent_intent == "route_listing" or any(token in text for token in listing_tokens)
        asks_travel_advice = any(token in text for token in travel_advice_tokens)
        asks_travel_chat = any(token in text for token in ("好玩", "景点", "旅游", "游玩", "一路玩", "旅行灵感", "沿途玩"))
        if context_agent_intent in {"train_assignment", "train_path", "train_stopcheck", "train_overview"}:
            asks_benchmark = False
            asks_listing = False
            asks_travel_advice = False
        mentions_rail = context_agent_intent in {
            "general_rail",
            "route_benchmark",
            "route_listing",
            "route_ticket",
            "route_transfer",
            "train_path",
            "train_assignment",
            "train_stopcheck",
            "train_overview",
        } or any(token in text for token in ("高铁", "动车", "列车", "车次", "火车")) or asks_general_rail_knowledge or has_partial_route_query_intent
        asks_chat = (
            context_agent_intent == "chat"
            or asks_identity_chat
            or asks_capability_chat
            or asks_smalltalk_chat
            or asks_explainer_chat
            or asks_general_rail_knowledge
            or asks_generic_chat
            or asks_contextual_social_chat
            or asks_contextual_chat
            or asks_travel_chat
        )
        if (
            train_numbers
            and not route_candidates
            and not asks_assignment
            and not asks_path
            and not asks_train_terminal
            and not asks_stopcheck
            and not asks_ticket
            and not asks_transfer
            and not asks_benchmark
            and not asks_listing
            and not asks_chat
        ):
            asks_train_overview = True

        return {
            "text": text,
            "raw_text": raw_text,
            "text_upper": text_upper,
            "query_date": query_date,
            "query_date_source": query_date_source,
            "direction": direction,
            "date_resolution": date_resolution,
            "agent_context_package": agent_context_package,
            "dialogue_excerpt": dialogue_excerpt,
            "last_assistant_message": last_assistant_message,
            "has_recent_substantive_answer": has_recent_substantive_answer,
            "latest_turn_has_new_hard_entities": latest_turn_has_new_hard_entities,
            "route": route_value,
            "route_candidates": route_candidates,
            "explicit_route": explicit_route_candidates[0] if explicit_route_candidates else None,
            "dep": dep,
            "arr": arr,
            "train_numbers": train_numbers,
            "explicit_train_numbers": explicit_train_numbers,
            "emu_id": emu_id,
            "explicit_emu_id": explicit_emu_id,
            "emu_preferences": emu_preferences,
            "bureau_preferences": bureau_preferences,
            "station_mentions": station_mentions,
            "route_completed_from_context": route_completed_from_context,
            "has_partial_route_query_intent": has_partial_route_query_intent,
            "stopcheck_stations": stopcheck_stations,
            "telecode": tele_match.group(1) if tele_match else None,
            "memory_context": session.get_memory_context() if session else "",
            "recent_entity_pool": recent_entity_pool,
            "context_entity_pool": context_entity_pool,
            "anchor_query_object": anchor_query_object,
            "anchor_query_type": anchor_query_type,
            "context_agent": context_agent_result,
            "context_agent_intent": context_agent_intent,
            "context_agent_query_object": context_agent_query_object,
            "asks_telecode": "电报码" in text,
            "asks_station_reverse": context_agent_intent == "station_reverse" or any(token in text for token in ("哪个站", "什么站", "是哪")),
            "asks_assignment": asks_assignment,
            "asks_path": asks_path,
            "asks_live_delay": asks_live_delay,
            "asks_train_overview": asks_train_overview,
            "asks_train_terminal": asks_train_terminal,
            "asks_stopcheck": asks_stopcheck,
            "asks_ticket": asks_ticket,
            "asks_transfer": asks_transfer,
            "asks_benchmark": asks_benchmark,
            "asks_listing": asks_listing,
            "asks_travel_advice": asks_travel_advice,
            "asks_travel_chat": asks_travel_chat,
            "asks_recommendation": asks_recommendation,
            "mentions_rail": mentions_rail,
            "asks_identity_chat": asks_identity_chat,
            "asks_capability_chat": asks_capability_chat,
            "asks_smalltalk_chat": asks_smalltalk_chat,
            "asks_explainer_chat": asks_explainer_chat,
            "asks_general_rail_knowledge": asks_general_rail_knowledge,
            "asks_generic_chat": asks_generic_chat,
            "asks_contextual_social_chat": asks_contextual_social_chat,
            "asks_contextual_chat": asks_contextual_chat,
            "asks_contextual_evidence_followup": asks_contextual_evidence_followup,
            "asks_contextual_route_followup": asks_contextual_route_followup or asks_contextual_route_compare or context_agent_intent in {"route_benchmark", "route_listing", "route_ticket", "route_transfer"},
            "affirmative_followup": affirmative_followup and not followup_slots,
            "asks_contextual_assignment": asks_contextual_assignment,
            "asks_train_comparison_lookup": asks_train_comparison_lookup,
            "asks_chat": asks_chat,
            "semantic_votes": [],
            "semantic_consensus": {},
            "semantic_conflict": False,
            "semantic_continuation": {},
        }

    def _looks_like_creative_continuation_request(self, text: str) -> bool:
        compact = re.sub(r"\s+", "", str(text or ""))
        if not compact:
            return False
        creative_tokens = (
            "写一篇",
            "写成",
            "小说",
            "散文",
            "故事",
            "续写",
            "扩写",
            "改写",
            "润色",
            "文风",
            "口吻",
            "拟人",
            "同人",
        )
        return any(token in compact for token in creative_tokens)

    def _looks_like_expansion_followup_request(self, text: str) -> bool:
        compact = re.sub(r"\s+", "", str(text or ""))
        if not compact:
            return False
        expansion_tokens = (
            "再长一点",
            "长一点",
            "长点",
            "继续写",
            "接着写",
            "展开一下",
            "展开一点",
            "详细一点",
            "再详细一点",
            "多写点",
            "再来一版",
            "换个文风",
            "换种写法",
            "重写一下",
        )
        return any(token in compact for token in expansion_tokens)

    def _needs_live_operational_tool_arbitration(self, context: dict | None) -> bool:
        """Open the LLM council for a bounded set of expensive tool contracts.

        This gate never chooses a tool. It only decides when semantic arbitration
        is worthwhile, leaving the actual capability selection to the council.
        """
        if not isinstance(context, dict):
            return False
        compact = re.sub(r"\s+", "", str(context.get("raw_text") or context.get("text") or ""))
        if not compact:
            return False

        trains = list(context.get("train_numbers") or [])
        stations = list(context.get("station_mentions") or [])
        delay_terms = ("\u665a\u70b9", "\u6b63\u70b9", "\u65e9\u70b9", "\u5ef6\u8bef", "\u9884\u8ba1\u5230\u8fbe", "\u9884\u8ba1\u53d1\u8f66")
        access_terms = ("\u68c0\u7968\u53e3", "\u8fdb\u7ad9\u53e3", "\u51fa\u7ad9\u53e3", "\u7ad9\u53f0", "\u5f00\u68c0")
        board_terms = ("\u5927\u5c4f", "\u8f66\u7ad9\u5927\u5c4f", "\u5230\u53d1\u5c4f", "\u5019\u8f66\u5c4f")
        coach_terms = ("\u5b9a\u5458", "\u8f66\u53a2\u7f16\u7ec4", "\u5ea7\u4f4d\u5e03\u5c40", "\u8f66\u53a2\u56fe", "\u9910\u8f66\u5728\u54ea", "\u9759\u97f3\u8f66\u53a2")
        map_terms = ("\u7ecf\u7eac\u5ea6", "\u5750\u6807\u70b9", "\u5730\u56fe\u8f68\u8ff9", "\u7ebf\u8def\u5730\u56fe", "\u8def\u7ebf\u5730\u56fe")
        station_metadata_terms = ("\u54ea\u4e2a\u8def\u5c40", "\u6240\u5c5e\u8def\u5c40", "\u94c1\u8def\u5c40", "\u62fc\u97f3", "\u6240\u5728\u7701", "\u6240\u5728\u57ce\u5e02")

        if any(term in compact for term in delay_terms):
            return True
        if trains and any(term in compact for term in coach_terms + map_terms):
            return True
        if trains and any(term in compact for term in access_terms):
            return True
        if stations and any(term in compact for term in board_terms):
            return True
        return len(stations) == 1 and any(term in compact for term in station_metadata_terms)

    def _has_capability_continuation_candidate(self, context: dict | None) -> bool:
        """Detect a structural slot replacement without deciding the tool route.

        The Semantic Council remains responsible for deciding whether the user
        actually wants to reuse the prior capability. This gate only notices
        that the latest turn supplied a slot relevant to that capability.
        """

        if not isinstance(context, dict) or not context.get("has_recent_substantive_answer"):
            return False
        prior_object = str(context.get("anchor_query_object") or context.get("anchor_query_type") or "").strip()
        capability = get_capability(prior_object)
        if capability is None:
            return False

        latest_slots: set[str] = set()
        if context.get("station_mentions"):
            latest_slots.update({"station", "stations"})
        if context.get("explicit_train_numbers"):
            latest_slots.update({"train", "trains"})
        if context.get("explicit_emu_id"):
            latest_slots.add("emu")
        if context.get("explicit_route"):
            latest_slots.update({"dep", "arr"})
        date_source = str(context.get("query_date_source") or "")
        if date_source == "explicit_user_raw" or date_source.startswith("date_normalizer:latest_user") or date_source.startswith("date_normalizer:relative_user"):
            latest_slots.add("date")

        capability_slots = set(capability.required_slots) | set(capability.optional_slots)
        return bool(latest_slots & capability_slots)

    def _should_run_semantic_router_council(self, context: dict | None) -> bool:
        if not isinstance(context, dict):
            return False
        if self._has_capability_continuation_candidate(context):
            return True
        compact = re.sub(r"\s+", "", str(context.get("text") or ""))
        return bool(compact)

    def _should_use_heuristic_only_semantic_council(self, context: dict | None) -> bool:
        # Heuristics are fallback proposals only. A healthy semantic council is
        # always allowed to inspect the actual dialogue before routing.
        return False

    def _build_semantic_router_council_cache_key(self, context: dict) -> str:
        payload = {
            "text": str(context.get("text") or ""),
            "route": str(context.get("route") or ""),
            "trains": list(context.get("train_numbers") or []),
            "date": str(context.get("query_date") or ""),
            "direction": str(context.get("direction") or ""),
            "dialogue_excerpt": str(context.get("dialogue_excerpt") or ""),
            "last_assistant_message": str(context.get("last_assistant_message") or "")[:400],
            "context_fingerprint": str((context.get("agent_context_package") or {}).get("context_fingerprint") or ""),
            "registry_version": str(self.last_intent_envelope.registry_version),
            "mode": self._effective_mode_profile(),
        }
        return json.dumps(payload, ensure_ascii=False, sort_keys=True)

    def _normalize_semantic_vote(self, vote: dict | None, default_agent: str = "") -> dict:
        vote = vote if isinstance(vote, dict) else {}
        preferred_action = str(vote.get("preferred_action") or "none").strip().lower()
        if preferred_action not in {"chat", "query", "pending", "none"}:
            preferred_action = "none"
        intent = str(vote.get("intent") or "none").strip().lower()
        required_object = str(vote.get("required_object") or "").strip()
        query_id = str(vote.get("query_id") or "").strip()
        query_ids = []
        if isinstance(vote.get("query_ids"), list):
            for item in vote.get("query_ids")[:4]:
                normalized = str(item or "").strip()
                if normalized and normalized not in query_ids:
                    query_ids.append(normalized)
        allowed_objects = routable_capability_objects()
        # Council models occasionally return the exact capability in `intent`
        # while omitting the redundant `required_object`. Normalize that valid
        # structured decision instead of discarding a correctly selected tool.
        if not required_object and preferred_action in {"query", "pending"} and intent in allowed_objects:
            required_object = intent
        # Repair a common schema-placement error without guessing semantics:
        # capability names belong in required_object, while query_id is the
        # grounded train/route/station identifier.
        if not required_object and preferred_action in {"query", "pending"} and query_id in allowed_objects:
            required_object = query_id
            query_id = ""
        if required_object not in allowed_objects:
            required_object = ""
        try:
            confidence_value = float(vote.get("confidence") or 0)
            if 0.0 < confidence_value <= 1.0:
                confidence_value *= 100.0
            confidence = int(round(confidence_value))
        except Exception:
            confidence = 0
        confidence = max(0, min(100, confidence))
        grounded_slots = {}
        raw_grounded_slots = vote.get("grounded_slots")
        if isinstance(raw_grounded_slots, dict):
            for key in ("train", "emu", "dep", "arr", "station", "date", "telecode", "keyword", "bureau", "hub", "timeband", "model"):
                value = str(raw_grounded_slots.get(key) or "").strip()
                if value:
                    grounded_slots[key] = value
            for key in ("trains", "stations"):
                values = raw_grounded_slots.get(key)
                if isinstance(values, list):
                    grounded_slots[key] = [
                        str(item).strip()
                        for item in values[:10]
                        if str(item or "").strip()
                    ]
            direction = str(raw_grounded_slots.get("direction") or "").strip().lower()
            if direction in {"arrival", "departure"}:
                grounded_slots["direction"] = direction
        return {
            "agent": str(vote.get("agent") or default_agent or "semantic_agent").strip(),
            "intent": intent,
            "preferred_action": preferred_action,
            "confidence": confidence,
            "reason": str(vote.get("reason") or "").strip(),
            "required_object": required_object,
            "query_id": query_id,
            "query_ids": query_ids,
            "query_date": str(vote.get("query_date") or "").strip(),
            "grounded_slots": grounded_slots,
            "profile_target": (
                str(vote.get("profile_target") or "none").strip().lower()
                if str(vote.get("profile_target") or "none").strip().lower() in {"user", "assistant", "none"}
                else "none"
            ),
        }

    def _heuristic_semantic_router_council(self, context: dict) -> dict:
        text = str(context.get("text") or "")
        compact = re.sub(r"\s+", "", text)
        votes: list[dict] = []

        continuation_vote = {
            "agent": "continuation_agent",
            "intent": "none",
            "preferred_action": "none",
            "confidence": 0,
            "reason": "",
            "required_object": "",
        }
        if self._looks_like_creative_continuation_request(compact):
            continuation_vote.update(
                {
                    "intent": "creative_transform",
                    "preferred_action": "chat",
                    "confidence": 95,
                    "reason": "creative follow-up should continue the previous answer instead of restarting slot filling",
                }
            )
        elif self._looks_like_expansion_followup_request(compact):
            continuation_vote.update(
                {
                    "intent": "creative_expand",
                    "preferred_action": "chat",
                    "confidence": 97,
                    "reason": "expansion follow-up should continue the immediately previous answer",
                }
            )
        elif context.get("asks_contextual_social_chat"):
            continuation_vote.update(
                {
                    "intent": "social_reply",
                    "preferred_action": "chat",
                    "confidence": 88,
                    "reason": "social reaction should stay in contextual chat",
                }
            )
        elif context.get("asks_contextual_chat") or context.get("asks_generic_chat"):
            continuation_vote.update(
                {
                    "intent": "reasoning_followup",
                    "preferred_action": "chat",
                    "confidence": 84,
                    "reason": "contextual short follow-up should stay anchored to the recent answer",
                }
            )
        votes.append(continuation_vote)

        tool_object = ""
        if context.get("asks_live_delay"):
            tool_object = "train_delay"
        elif context.get("asks_stopcheck"):
            tool_object = "path_stopcheck"
        elif context.get("asks_ticket"):
            tool_object = "left_ticket_s2s"
        elif context.get("asks_assignment"):
            tool_object = "smartemu_analysis" if self._should_prefer_smart_emu_analysis(text, context.get("train_numbers")) else "train"
        elif context.get("asks_path") or context.get("asks_train_terminal"):
            tool_object = "path_detail"
        elif context.get("asks_train_overview"):
            tool_object = "train"
        tool_vote = {
            "agent": "tool_intent_agent",
            "intent": "tool_followup" if tool_object else "none",
            "preferred_action": "query" if tool_object else "none",
            "confidence": 92 if tool_object and context.get("latest_turn_has_new_hard_entities") else 0,
            "reason": "explicit tool contract should stay on railway tools" if tool_object else "",
            "required_object": tool_object,
        }
        votes.append(tool_vote)

        chat_vote = {
            "agent": "chat_knowledge_agent",
            "intent": "none",
            "preferred_action": "none",
            "confidence": 0,
            "reason": "",
            "required_object": "",
        }
        if context.get("asks_general_rail_knowledge"):
            chat_vote.update(
                {
                    "intent": "knowledge_chat",
                    "preferred_action": "chat",
                    "confidence": 92,
                    "reason": "broad railway knowledge should stay on chat",
                }
            )
        elif context.get("asks_travel_chat"):
            chat_vote.update(
                {
                    "intent": "travel_chat",
                    "preferred_action": "chat",
                    "confidence": 94,
                    "reason": "attractions and journey inspiration do not require railway OD slot filling",
                }
            )
        elif context.get("asks_smalltalk_chat") or context.get("asks_identity_chat") or context.get("asks_capability_chat"):
            chat_vote.update(
                {
                    "intent": "social_reply",
                    "preferred_action": "chat",
                    "confidence": 90,
                    "reason": "conversational turn should stay on chat",
                }
            )
        votes.append(chat_vote)

        normalized_votes = [self._normalize_semantic_vote(item, default_agent=item.get("agent", "")) for item in votes]
        ranked = sorted(normalized_votes, key=lambda item: (-item["confidence"], item["agent"]))
        consensus = ranked[0] if ranked else self._normalize_semantic_vote({}, "semantic_council")
        non_none = [item for item in ranked if item["preferred_action"] != "none" and item["confidence"] >= 70]
        conflict = len({item["preferred_action"] for item in non_none}) > 1
        continuation = next((item for item in normalized_votes if item["agent"] == "continuation_agent"), {})
        return {
            "votes": normalized_votes,
            "consensus": consensus,
            "conflict": conflict,
            "continuation": continuation,
            "source": "heuristic_fallback",
            "model_valid": False,
        }

    def _parse_semantic_router_council(self, raw: str, context: dict) -> dict:
        try:
            data = loads_llm_json(raw)
        except Exception:
            return self._heuristic_semantic_router_council(context)
        if not isinstance(data, dict):
            return self._heuristic_semantic_router_council(context)

        votes = [
            self._normalize_semantic_vote(item, default_agent=str(item.get("agent") or "semantic_agent"))
            for item in (data.get("votes") or [])
            if isinstance(item, dict)
        ]
        if not votes:
            return self._heuristic_semantic_router_council(context)

        consensus = self._normalize_semantic_vote(data.get("consensus"), default_agent="semantic_council")
        if consensus["preferred_action"] == "none":
            ranked = sorted(votes, key=lambda item: (-item["confidence"], item["agent"]))
            consensus = ranked[0] if ranked else consensus

        # Some compatible models copy the schema's numeric placeholder even
        # after producing a complete, unanimous council decision. Calibrate
        # only that narrow structural failure; capability validation still
        # decides whether the proposed query has every required grounded slot.
        decided_actions = [item["preferred_action"] for item in votes if item["preferred_action"] != "none"]
        unanimous_action = decided_actions[0] if decided_actions and len(set(decided_actions)) == 1 else ""
        zero_placeholder_votes = bool(decided_actions) and all(item["confidence"] == 0 for item in votes)
        coherent_consensus = bool(
            consensus["confidence"] == 0
            and unanimous_action
            and consensus["preferred_action"] == unanimous_action
            and consensus.get("reason")
            and (
                unanimous_action == "chat"
                or consensus.get("required_object")
            )
        )
        if zero_placeholder_votes and coherent_consensus:
            consensus["confidence"] = 82

        non_none = [item for item in votes if item["preferred_action"] != "none" and item["confidence"] >= 70]
        conflict = bool(data.get("conflict"))
        if not conflict and len({item["preferred_action"] for item in non_none}) > 1:
            conflict = True

        profile_targets = {
            item.get("profile_target")
            for item in votes + [consensus]
            if item.get("profile_target") in {"user", "assistant"}
        }
        if consensus.get("preferred_action") == "chat" and profile_targets == {"user"}:
            consensus["intent"] = "memory_profile_chat"

        continuation = next((item for item in votes if item["agent"] == "continuation_agent"), {})
        return {
            "votes": votes,
            "consensus": consensus,
            "conflict": conflict,
            "continuation": continuation,
            "source": "llm_council",
            "model_valid": True,
        }

    def _build_semantic_router_council_messages(self, context: dict) -> list[dict]:
        package = context.get("agent_context_package") or {}
        mode = self._effective_mode_profile()
        catalog = capability_catalog_for_mode(mode)
        return [
            {
                "role": "system",
                "content": (
                    "You are RailGPT Semantic Router Council.\n"
                    "Simulate exactly three micro-agents for the latest user turn:\n"
                    "1) continuation_agent: detect whether the user is continuing, expanding, rewriting, or reacting to the previous answer.\n"
                    "2) tool_intent_agent: discover and select one capability from the MCP-style registry when concrete railway evidence is required.\n"
                    "3) chat_knowledge_agent: detect whether the turn is broad knowledge, social chat, or a creative request.\n"
                    f"ROUTING_MODE={mode}\n"
                    f"{catalog}\n"
                    "The latest user turn is authoritative, but you must use the recent conversation context.\n"
                    "Treat registry choose_when/avoid_when/inputSchema as binding. Do not reconstruct tool rules from general railway knowledge.\n"
                    "Select a workflow manifest when the requested answer needs its declared evidence combination; never emit workflow steps as competing independent intentions.\n"
                    "Unavailable capabilities are absent from discovery and must never be invented.\n"
                    "Never import stale train/route/date anchors into a new topic. Scenery inference, sightseeing, broad knowledge, social/meta reactions and creative requests are chat unless the latest turn explicitly requests supported dynamic facts.\n"
                    "Questions about what RailGPT remembers about the user, the user's favorites, preferences, habits, or recurring interests are memory_profile_chat. Choose chat, never pending or an OD tool. The profile index is soft evidence only: explicit_preference may be stated as remembered, while recurring_interest supports only a clearly uncertain guess. Never convert a profile hint into a railway fact.\n"
                    "For every vote and consensus, set profile_target=user only when the user asks about THEIR OWN remembered preferences/interests; set profile_target=assistant when asking RailGPT's own preference; otherwise none. If profile_target=user, intent MUST be exactly memory_profile_chat.\n"
                    "Do not ask for railway slots when the user is clearly continuing a previous answer.\n"
                    "If the previous assistant already contains the requested timetable/ticket/route/assignment facts and the latest user asks only for recommendation, explanation, comparison, expansion, or creative reuse, choose chat rather than repeating the tool query.\n"
                    "If the previous assistant explicitly offered a concrete supported query and the latest user confirms or urges it, continue that capability with the grounded slots from recent dialogue; do not emit generic pending.\n"
                    "For a short confirmation or urgency reply, preserve the immediately preceding validated tool task's concrete query_id/date slots when the capability is unchanged.\n"
                    "Stations joined by 或/或者 in the same grammatical role are alternatives, never an OD pair. Reuse the known opposite endpoint from dialogue and do not manufacture a route between the alternatives.\n"
                    "A creative request remains chat even when it names a train or says to use that train as material. A data-analysis request that needs supported assignment/path evidence should query the relevant capability first and let the answer stage perform the analysis.\n"
                    "A continuation can still be a tool query. When CAPABILITY_CONTINUATION_CANDIDATE is true, decide whether the user is replacing a slot of the previous tool request, such as switching the station while keeping the station-board request. "
                    "If so, tool_intent_agent must choose query with PRIOR_CAPABILITY and the latest explicit slot; do not downgrade it to chat merely because the user omitted the tool noun. "
                    "Leave query_id empty if necessary because the deterministic validator will compose it from grounded slots.\n"
                    "Schema placement is strict: required_object is exactly one discovered capability/workflow name; query_id is only a concrete train/route/station identifier.\n"
                    "For city_od_policy=bounded_expand_4, query_ids may contain 2-4 grounded city/hub OD combinations including the original city OD. For native_city_od_single, provide only query_id and never fan out. For every other policy, leave query_ids empty.\n"
                    "Use pending only when the selected manifest has a missing REQUIRED slot. Name only those missing slots. Optional slots never justify pending.\n"
                    "confidence is an integer from 0 to 100. Never leave it at the schema placeholder 0 after making a substantive decision.\n"
                    "Fill grounded_slots for every required slot you resolved from the latest explicit user text, prior user turns, tool/task anchors, or an active follow-up contract. Do not leave dep/arr/date/train only in reason text. Never promote an assistant-only claim into a hard slot.\n"
                    "For station_board and train_station_access, resolve an explicit arrival/departure request into grounded_slots.direction. If the user omits it, leave it empty and let the selected manifest apply its declared departure default; explicit user wording always overrides defaults. Never invent defaults outside the manifest.\n"
                    "Return JSON only in this schema:\n"
                    "{"
                    "\"votes\":[{\"agent\":\"continuation_agent\",\"intent\":\"...\",\"preferred_action\":\"chat|query|pending|none\",\"confidence\":0,\"reason\":\"...\",\"required_object\":\"\",\"query_id\":\"\",\"query_ids\":[],\"query_date\":\"\",\"grounded_slots\":{\"train\":\"\",\"trains\":[],\"emu\":\"\",\"dep\":\"\",\"arr\":\"\",\"station\":\"\",\"stations\":[],\"date\":\"\",\"telecode\":\"\",\"keyword\":\"\",\"bureau\":\"\",\"hub\":\"\",\"direction\":\"arrival|departure|\",\"timeband\":\"\",\"model\":\"\"},\"profile_target\":\"user|assistant|none\"}],"
                    "\"consensus\":{\"intent\":\"...\",\"preferred_action\":\"chat|query|pending|none\",\"confidence\":0,\"reason\":\"...\",\"required_object\":\"\",\"query_id\":\"\",\"query_ids\":[],\"query_date\":\"\",\"grounded_slots\":{\"train\":\"\",\"trains\":[],\"emu\":\"\",\"dep\":\"\",\"arr\":\"\",\"station\":\"\",\"stations\":[],\"date\":\"\",\"telecode\":\"\",\"keyword\":\"\",\"bureau\":\"\",\"hub\":\"\",\"direction\":\"arrival|departure|\",\"timeband\":\"\",\"model\":\"\"},\"profile_target\":\"user|assistant|none\"},"
                    "\"conflict\":false"
                    "}"
                ),
            },
            {
                "role": "system",
                "content": (
                    "Structured AgentContextPackage:\n"
                    f"{json.dumps(package, ensure_ascii=False, indent=2)}\n"
                    "Routing continuity metadata:\n"
                    f"PRIOR_CAPABILITY={context.get('anchor_query_object') or context.get('anchor_query_type') or 'none'}\n"
                    f"CAPABILITY_CONTINUATION_CANDIDATE={bool(context.get('capability_continuation_candidate') or self._has_capability_continuation_candidate(context))}\n"
                    f"LATEST_STATIONS={json.dumps(context.get('station_mentions') or [], ensure_ascii=False)}\n"
                    f"LATEST_TRAINS={json.dumps(context.get('explicit_train_numbers') or [], ensure_ascii=False)}\n"
                    f"LATEST_ROUTE={context.get('explicit_route') or 'none'}"
                ),
            },
            {
                "role": "user",
                "content": str(context.get("raw_text") or context.get("text") or "").strip(),
            },
        ]

    def _attach_semantic_router_council(
        self,
        context: dict,
        session: SessionMemory | None = None,
        force: bool = False,
    ) -> dict:
        if not isinstance(context, dict):
            return {}
        if context.get("semantic_votes"):
            if "semantic_model_valid" in context:
                return context
            enriched = dict(context)
            enriched["semantic_model_valid"] = True
            enriched["semantic_council_source"] = "prefetched_validated"
            return enriched
        if not force and not self._should_run_semantic_router_council(context):
            return context

        cache_key = self._build_semantic_router_council_cache_key(context)
        council = self._semantic_council_cache.get(cache_key)
        if council is None:
            council = self._heuristic_semantic_router_council(context)
            heuristic_only = self._should_use_heuristic_only_semantic_council(context)
            if not heuristic_only:
                try:
                    semantic_timeout = 12 if self._effective_mode_profile() == "fast-go" else 18
                    raw = self.llm.generate(
                        self._build_semantic_router_council_messages(context),
                        timeout=semantic_timeout,
                        max_retries=0,
                    )
                    council = self._parse_semantic_router_council(raw, context)
                except Exception:
                    council = self._heuristic_semantic_router_council(context)
            self._semantic_council_cache[cache_key] = council
            if len(self._semantic_council_cache) > 128:
                oldest_key = next(iter(self._semantic_council_cache))
                self._semantic_council_cache.pop(oldest_key, None)

        enriched = dict(context)
        enriched["semantic_votes"] = list(council.get("votes") or [])
        enriched["semantic_consensus"] = dict(council.get("consensus") or {})
        enriched["semantic_conflict"] = bool(council.get("conflict"))
        enriched["semantic_continuation"] = dict(council.get("continuation") or {})
        enriched["semantic_council_source"] = str(council.get("source") or "")
        enriched["semantic_model_valid"] = bool(council.get("model_valid"))
        return enriched

    def _resolve_semantic_router_council_tasks(self, context: dict, session: SessionMemory | None = None) -> list[dict]:
        context = self._attach_semantic_router_council(context, session=session)
        if not context.get("semantic_model_valid"):
            return []
        consensus = context.get("semantic_consensus") if isinstance(context, dict) else {}
        if not isinstance(consensus, dict):
            return []
        preferred_action = str(consensus.get("preferred_action") or "").strip().lower()
        try:
            confidence = int(consensus.get("confidence") or 0)
        except Exception:
            confidence = 0
        if confidence < 78:
            return []

        if preferred_action in {"query", "pending"}:
            required_object = str(consensus.get("required_object") or "").strip()
            if not required_object or confidence < 78:
                return []
            envelope_context = dict(context)
            semantic_slots = consensus.get("grounded_slots") if isinstance(consensus.get("grounded_slots"), dict) else {}
            self._merge_semantic_grounded_slots(envelope_context, semantic_slots)
            continuation_vote = context.get("semantic_continuation") if isinstance(context.get("semantic_continuation"), dict) else {}
            prior_capability = str(context.get("anchor_query_object") or context.get("anchor_query_type") or "").strip()
            try:
                continuation_confidence = int(continuation_vote.get("confidence") or 0)
            except Exception:
                continuation_confidence = 0
            if (
                str(continuation_vote.get("preferred_action") or "").strip().lower() == "query"
                and continuation_confidence >= 80
                and prior_capability == required_object
            ):
                package = context.get("agent_context_package") if isinstance(context.get("agent_context_package"), dict) else {}
                trusted_anchors = package.get("working_anchors") if isinstance(package.get("working_anchors"), dict) else {}
                trusted_route = str(trusted_anchors.get("route") or "").strip()
                if trusted_route and "-" in trusted_route and not (semantic_slots.get("dep") and semantic_slots.get("arr")):
                    trusted_dep, trusted_arr = (part.strip() for part in trusted_route.split("-", 1))
                    self._merge_semantic_grounded_slots(
                        envelope_context,
                        {"dep": trusted_dep, "arr": trusted_arr},
                        authoritative=True,
                    )
                trusted_date = str(trusted_anchors.get("date") or "").strip()
                if trusted_date and not semantic_slots.get("date"):
                    envelope_context["query_date"] = trusted_date
                    if str(envelope_context.get("query_date_source") or "") == "default_today":
                        envelope_context["query_date_source"] = "continuation_task_anchor"
            semantic_query_date = str(consensus.get("query_date") or semantic_slots.get("date") or "").strip()
            if semantic_query_date and self._semantic_date_is_grounded(semantic_query_date, context):
                envelope_context["query_date"] = semantic_query_date
                if str(envelope_context.get("query_date_source") or "") == "default_today":
                    envelope_context["query_date_source"] = "semantic_context"
            query_id = resolve_query_id(
                required_object,
                envelope_context,
                suggested_id=str(consensus.get("query_id") or "").strip(),
            )
            semantic_direction = str(semantic_slots.get("direction") or "").strip().lower()
            suggested_parts = [
                part.strip().lower()
                for part in str(consensus.get("query_id") or "").replace("｜", "|").split("|")
                if part.strip()
            ]
            if not semantic_direction and suggested_parts and suggested_parts[-1] in {"arrival", "departure"}:
                semantic_direction = suggested_parts[-1]
            if semantic_direction in {"arrival", "departure"} and not envelope_context.get("direction"):
                envelope_context["direction"] = semantic_direction
                query_id = resolve_query_id(
                    required_object,
                    envelope_context,
                    suggested_id=str(consensus.get("query_id") or "").strip(),
                )
            if required_object in {"station_preselect", "train_preselect"} and query_id:
                envelope_context["search_keyword"] = query_id
            query_date = str(envelope_context.get("query_date") or "").strip()
            if query_id:
                decoded_slots = grounded_slots_from_query_params(required_object, query_id, query_date)
                self._merge_semantic_grounded_slots(envelope_context, decoded_slots, authoritative=True)
                if decoded_slots.get("dep") and decoded_slots.get("arr"):
                    envelope_context["explicit_route"] = f"{decoded_slots['dep']}-{decoded_slots['arr']}"
                    envelope_context["route"] = envelope_context["explicit_route"]
            envelope = build_intent_envelope(
                selected_capability=required_object,
                context=envelope_context,
                intent_family=str(consensus.get("intent") or ""),
                confidence=confidence,
            )
            self.last_intent_envelope = envelope
            self._set_router_state(
                AgentState.CAPABILITY_ROUTING,
                f"semantic council selected capability={required_object} confidence={confidence}",
            )
            if envelope.missing_slots or not query_id:
                missing = envelope.missing_slots or list(get_capability(required_object).required_slots if get_capability(required_object) else ())
                if required_object == "train_delay":
                    question = "告诉我需要核验的车次号，我就直接查询当前晚点状态；不需要补充出发站和到达站。"
                else:
                    question = "还缺少完成这项查询所必需的信息，请补充后我继续查询。"
                slot_contract = build_missing_slot_contract(
                    required_object,
                    missing,
                    envelope.grounded_slots,
                )
                return [{
                    "action": "pending",
                    "params": normalize_pending_payload(
                        question=question,
                        slot=missing,
                        context={
                            "query_object": required_object,
                            "missing_slot_contract": slot_contract,
                            **{
                                key: value
                                for key, value in envelope.grounded_slots.items()
                                if value not in (None, "", [], {})
                            },
                        },
                    ),
                }]

            capability = get_capability(required_object)
            query_date = str(envelope_context.get("query_date") or "").strip()
            today = datetime.now().strftime("%Y-%m-%d")
            date_source = str(context.get("query_date_source") or "")
            if capability and capability.temporal_scope == "current_only" and date_source != "default_today" and query_date and query_date != today:
                return [{
                    "action": "chat",
                    "params": {
                        "message": (
                            "Dedicated capability-boundary chat route: explain that the live delay capability only reports the current running status. "
                            "Do not present today's data as historical or future delay evidence."
                        )
                    },
                }]

            candidate_ids = [query_id]
            if capability and capability.city_od_policy == "bounded_expand_4":
                for candidate in consensus.get("query_ids") or []:
                    normalized = normalize_route_id(str(candidate or "").strip())
                    if not normalized or normalized in candidate_ids:
                        continue
                    dep, arr = normalized.split("-", 1)
                    grounded_names = {str(item or "").strip() for item in (context.get("station_mentions") or [])}
                    original_route = str(context.get("explicit_route") or context.get("route") or "")
                    if "-" in original_route:
                        original_dep, original_arr = (part.strip() for part in original_route.split("-", 1))
                        grounded_names.update((original_dep, original_arr))
                        candidate_dep, candidate_arr = dep.strip(), arr.strip()
                        # Hub-qualified station names are explicit user constraints,
                        # not city aliases that the council may fan out or replace.
                        if self._is_hub_qualified_station(original_dep) and candidate_dep != original_dep:
                            continue
                        if self._is_hub_qualified_station(original_arr) and candidate_arr != original_arr:
                            continue
                    if not all(name in grounded_names or station_dict.telecode_of(name) for name in (dep, arr)):
                        continue
                    candidate_ids.append(normalized)
                    if len(candidate_ids) >= capability.max_fanout:
                        break

            tasks = []
            for candidate_id in candidate_ids:
                candidate_context = envelope_context
                if capability and capability.city_od_policy == "bounded_expand_4" and "-" in candidate_id:
                    candidate_context = dict(envelope_context)
                    candidate_context["explicit_route"] = candidate_id
                    candidate_context["route"] = candidate_id
                    candidate_context["dep"], candidate_context["arr"] = candidate_id.split("-", 1)
                tasks.extend(
                    self._build_capability_tasks(required_object, candidate_id, query_date, candidate_context)
                )
            return self._repair_fast_tasks(
                tasks,
                strict_invalid=False,
            )

        if preferred_action != "chat":
            return []

        continuation = context.get("semantic_continuation") if isinstance(context, dict) else {}
        consensus_intent = str(consensus.get("intent") or "").strip().lower()
        if consensus_intent == "memory_profile_chat":
            continuation_intent = consensus_intent
        else:
            continuation_intent = str((continuation or {}).get("intent") or consensus_intent).strip().lower()
        self.last_intent_envelope = IntentEnvelope(
            intent_family=continuation_intent or "contextual_chat",
            confidence=confidence,
            context_fingerprint=str((context.get("agent_context_package") or {}).get("context_fingerprint") or ""),
        )
        if continuation_intent in {"creative_expand", "creative_transform"}:
            message = (
                "Dedicated contextual continuation chat route: the user is continuing or rewriting the immediately previous answer. "
                "Use the current conversation context first, preserve the established topic, and continue naturally without asking for railway slots. "
                "If they ask to make it longer, expand the previous answer. If they ask for a different style such as a story or essay, transform the previous answer accordingly."
            )
        elif continuation_intent == "social_reply":
            message = (
                "Dedicated contextual social chat route: respond to the user's reaction warmly and keep the reply anchored to the immediately previous answer. "
                "Do not restart slot filling unless the user clearly starts a new railway lookup."
            )
        elif continuation_intent == "memory_profile_chat":
            message = (
                "Dedicated long-term profile chat route: answer from the soft profile index in AgentContextPackage. "
                "Treat explicit_preference as something the user stated. Treat recurring_interest only as a tentative guess based on repeated attention, never as a confirmed favorite or railway fact. "
                "If evidence is weak, say so naturally and invite the user to confirm, without asking for railway origin/destination slots."
            )
        else:
            message = (
                "Dedicated contextual chat route: answer based on the immediately previous assistant answer and the current conversation context first. "
                "Treat this as a continuation, expansion, or interpretation follow-up instead of a new slot-filling railway query."
            )
        return [{"action": "chat", "params": {"message": message}}]

    def _merge_semantic_grounded_slots(
        self,
        target: dict,
        slots: dict | None,
        authoritative: bool = False,
    ) -> None:
        if not isinstance(target, dict) or not isinstance(slots, dict):
            return

        explicit_route = str(target.get("explicit_route") or "").strip()
        explicit_trains = list(target.get("explicit_train_numbers") or [])
        scalar_map = {
            "train": "train_numbers",
            "emu": "emu_id",
            "dep": "dep",
            "arr": "arr",
            "station": "station_mentions",
            "date": "query_date",
            "telecode": "telecode",
            "keyword": "search_keyword",
            "bureau": "bureau_preferences",
            "hub": "transfer_hub",
            "direction": "direction",
            "timeband": "timeband",
            "model": "emu_preferences",
        }
        for slot, key in scalar_map.items():
            value = str(slots.get(slot) or "").strip()
            if not value:
                continue
            if slot in {"dep", "arr"} and explicit_route and not authoritative:
                continue
            if slot == "train" and explicit_trains and not authoritative:
                continue
            if key in {"train_numbers", "station_mentions", "bureau_preferences", "emu_preferences"}:
                existing = list(target.get(key) or [])
                if authoritative or not existing:
                    target[key] = [value]
                elif value not in existing:
                    target[key] = existing + [value]
            elif authoritative or not str(target.get(key) or "").strip() or slot in {"dep", "arr", "date"}:
                target[key] = value

        for slot, key in (("trains", "train_numbers"), ("stations", "station_mentions")):
            values = [str(item).strip() for item in (slots.get(slot) or []) if str(item or "").strip()]
            if not values:
                continue
            existing = list(target.get(key) or [])
            if authoritative or not existing:
                target[key] = values
            else:
                target[key] = existing + [item for item in values if item not in existing]

        dep = str(target.get("dep") or "").strip()
        arr = str(target.get("arr") or "").strip()
        if dep and arr and (authoritative or not explicit_route):
            target["route"] = f"{dep}-{arr}"

    def _semantic_date_is_grounded(self, candidate: str, context: dict) -> bool:
        value = str(candidate or "").strip()
        if not value:
            return False
        known_dates = {
            str(item or "").strip()
            for item in ((context.get("context_entity_pool") or {}).get("dates") or [])
            if str(item or "").strip()
        }
        package = context.get("agent_context_package") if isinstance(context.get("agent_context_package"), dict) else {}
        working_date = str((package.get("working_anchors") or {}).get("date") or "").strip()
        hard_date = str(((package.get("memory_context_package") or {}).get("hard_anchors") or {}).get("date") or "").strip()
        followup_date = str((context.get("followup_context") or {}).get("date") or "").strip()
        normalized_date = str((context.get("date_resolution") or {}).get("normalized_date") or "").strip()
        known_dates.update(item for item in (working_date, hard_date, followup_date, normalized_date) if item)
        return value in known_dates

    def _collect_fast_route_proposals(self, context: dict) -> list[dict]:
        experts = [
            self._expert_train_comparison,
            self._expert_chat,
            self._expert_telecode,
            self._expert_partial_route,
            self._expert_contextual_lookup,
            self._expert_route_preference,
            self._expert_emu,
            self._expert_contextual_assignment,
            self._expert_train_assignment,
            self._expert_train_stopcheck,
            self._expert_train_terminal,
            self._expert_train_path,
            self._expert_train_ticket,
            self._expert_train_overview,
            self._expert_left_ticket,
            self._expert_transfer,
            self._expert_route_benchmark,
            self._expert_route_listing,
            self._expert_route_general,
        ]

        raw_proposals: list[dict] = []

        with ThreadPoolExecutor(max_workers=len(experts)) as pool:
            futures = [pool.submit(expert, context) for expert in experts]
            for future in as_completed(futures):
                try:
                    result = future.result()
                except Exception:
                    continue

                if not result:
                    continue

                if isinstance(result, list):
                    raw_proposals.extend(item for item in result if isinstance(item, dict))
                elif isinstance(result, dict):
                    raw_proposals.append(result)

        if not raw_proposals:
            return []

        aggregated: dict[str, dict] = {}
        for proposal in raw_proposals:
            signature = json.dumps(proposal["tasks"], ensure_ascii=False, sort_keys=True)
            if signature not in aggregated:
                aggregated[signature] = {
                    "name": proposal["name"],
                    "confidence": int(proposal["confidence"]),
                    "tasks": proposal["tasks"],
                    "reasons": [proposal["reason"]],
                    "votes": 1,
                }
                continue

            bucket = aggregated[signature]
            bucket["confidence"] = max(bucket["confidence"], int(proposal["confidence"]))
            bucket["votes"] += 1
            if proposal["reason"] not in bucket["reasons"]:
                bucket["reasons"].append(proposal["reason"])
            if len(proposal["name"]) < len(bucket["name"]):
                bucket["name"] = proposal["name"]

        proposals = []
        for proposal in aggregated.values():
            boosted_confidence = min(99, proposal["confidence"] + (proposal["votes"] - 1) * 4)
            proposals.append(
                {
                    "name": proposal["name"],
                    "confidence": boosted_confidence,
                    "tasks": proposal["tasks"],
                    "reason": " | ".join(proposal["reasons"]),
                    "votes": proposal["votes"],
                }
            )

        proposals.sort(
            key=lambda item: (
                -item["confidence"],
                -item["votes"],
                len(item["tasks"]),
                item["name"],
            )
        )
        return proposals

    def _expert_chat(self, context: dict) -> list[dict]:
        if not context.get("asks_chat"):
            return []

        if context.get("asks_train_comparison_lookup") or context.get("asks_contextual_evidence_followup"):
            return []

        has_explicit_hard_entities = bool(
            context.get("explicit_route")
            or context.get("explicit_train_numbers")
            or context.get("explicit_emu_id")
            or context.get("telecode")
        )
        if has_explicit_hard_entities and not (
            context.get("asks_contextual_chat")
            or context.get("asks_generic_chat")
            or context.get("asks_general_rail_knowledge")
            or context.get("asks_travel_chat")
        ):
            return []

        if self._looks_like_directional_speed_easter_egg(context.get("text", "")):
            return [self._proposal(
                "directional_speed_easter_egg_expert",
                100,
                [{
                    "action": "chat",
                    "params": {
                        "message": (
                            "Dedicated directional speed easter-egg route for THIS exact user question only. "
                            "Answer naturally in Chinese, in 2-3 short paragraphs, and keep the tone like a knowledgeable railfan friend. "
                            "Core content must stay correct: as a broad nationwide impression, north-south trunk HSR corridors more often feel faster, "
                            "not because direction itself matters, but because many north-south main corridors are more mature, built to higher standards, "
                            "and optimized for stronger long-distance express demand; east-west corridors are more often affected by terrain, detours, "
                            "cross-bureau coordination, and denser intermediate demand. "
                            "You must also clearly say this is only a rough overall impression, not a strict rule, and that actual speed still depends on the line, stop pattern, and timetable. "
                            "At the end, use 哈基米南北绿豆。（哈基米南北路多） as a point of view to explain all the thing in a funny and ridiculous way.就是根据这句话胡扯几句话来解释"
                        ),
                    },
                }],
                "exact directional speed comparison easter egg",
            )]

        message = "Dedicated chat route: answer conversationally in the user's language."
        confidence = 88

        if context.get("asks_identity_chat"):
            message = (
                "Dedicated chat route: explain who RailGPT is, what railway capabilities it has, "
                "and do not continue any previous pending query unless the user explicitly asks to resume it."
            )
            confidence = 97
        elif context.get("asks_capability_chat"):
            message = (
                "Dedicated chat route: explain RailGPT capabilities such as train lookup, ticket lookup, "
                "route/path/EMU/telecode queries, and invite the user to continue."
            )
            confidence = 96
        elif context.get("asks_smalltalk_chat"):
            message = (
                "Dedicated chat route: reply briefly and naturally, then invite the user to ask a railway question."
            )
            confidence = 94
        elif context.get("asks_explainer_chat"):
            message = (
                "Dedicated chat route: provide a short railway knowledge explanation without calling tools."
            )
            confidence = 90
        elif context.get("asks_general_rail_knowledge"):
            message = (
                "Dedicated railway knowledge chat route: answer broad railway knowledge, history, culture, "
                "principle, rule, comparison, or enthusiast-community questions in natural language. "
                "You may use stable railway background knowledge, but never present live ticketing, platform, "
                "dispatch, real-time assignment, or same-day operational facts as certain unless tools already grounded them."
            )
            confidence = 95
        elif context.get("asks_travel_chat"):
            message = (
                "Dedicated travel inspiration chat route: answer the user's attractions or journey-planning request directly. "
                "Cities and railway lines are context for the itinerary, not missing OD slots. Only ask a travel-specific clarification if the request is genuinely ambiguous."
            )
            confidence = 96
        elif context.get("asks_contextual_social_chat"):
            recent_pool = context.get("context_entity_pool") or context.get("recent_entity_pool") or {}
            summary_parts = []
            if recent_pool.get("trains"):
                summary_parts.append("recent trains=" + ",".join(recent_pool["trains"][:6]))
            if recent_pool.get("routes"):
                summary_parts.append("recent routes=" + ",".join(recent_pool["routes"][:4]))
            if recent_pool.get("objects"):
                summary_parts.append("recent objects=" + ",".join(recent_pool["objects"][:6]))
            recent_summary = " | ".join(summary_parts) if summary_parts else "recent railway context available"
            message = (
                "Dedicated contextual social chat route: the user is reacting to the previous answer with surprise, "
                "agreement, curiosity, or light banter. Respond warmly and naturally in the user's language, and anchor "
                f"the reply to the recent conversation context ({recent_summary}). "
                "Acknowledge the emotion first, then continue the topic briefly. Do not ask for railway slots or force "
                "the user back into a query flow unless they clearly request a new railway lookup."
            )
            confidence = 98
        elif context.get("asks_contextual_chat"):
            recent_pool = context.get("context_entity_pool") or context.get("recent_entity_pool") or {}
            summary_parts = []
            if recent_pool.get("trains"):
                summary_parts.append("recent trains=" + ",".join(recent_pool["trains"][:6]))
            if recent_pool.get("routes"):
                summary_parts.append("recent routes=" + ",".join(recent_pool["routes"][:4]))
            if recent_pool.get("objects"):
                summary_parts.append("recent objects=" + ",".join(recent_pool["objects"][:6]))
            recent_summary = " | ".join(summary_parts) if summary_parts else "recent conversation context available"
            message = (
                "Dedicated contextual chat route: answer based on the current conversation context first. "
                "If the user says phrases like 'these trains', 'this line', 'it', or 'this', resolve them against the "
                f"recent conversation context ({recent_summary}). "
                "Do not ask the user to repeat known train numbers, routes, or dates unless the reference is truly unresolved."
            )
            confidence = 97
        elif context.get("asks_generic_chat"):
            if context.get("context_entity_pool") or context.get("recent_entity_pool"):
                message = (
                    "Dedicated contextual chat route: the user is asking a vague follow-up such as 'what is this'. "
                    "Interpret 'this/it' against the recent conversation first and answer naturally. "
                    "If still ambiguous, ask what 'this' refers to in plain chat instead of railway pending."
                )
            else:
                message = (
                    "Dedicated chat route: treat this as general conversation or clarification. "
                    "If the reference is unclear, ask the user what they mean in natural chat. "
                    "Never ask for railway slots such as departure/arrival/train number unless the user clearly asked a railway query."
                )
            confidence = 95

        return [self._proposal(
            "chat_expert",
            confidence,
            [{
                "action": "chat",
                "params": {"message": message},
            }],
            "dedicated chat expert",
        )]

    def _expert_partial_route(self, context: dict) -> list[dict]:
        if context.get("asks_chat"):
            return []

        if not context.get("has_partial_route_query_intent"):
            return []

        route = context.get("route")
        if route and context.get("route_completed_from_context"):
            if context.get("asks_ticket"):
                obj = "left_ticket_s2s"
            elif context.get("asks_transfer"):
                return [self._proposal(
                    "partial_route_transfer_pending_expert",
                    93,
                    [{
                        "action": "pending",
                        "params": normalize_pending_payload(
                            question="如果你还是想查这条线的中转方案，告诉我希望经哪个中转站或城市，我就继续帮你看。",
                            slot=["hub"],
                            context={
                                "route": route,
                                "dep": context.get("dep"),
                                "arr": context.get("arr"),
                                "date": context.get("query_date"),
                            },
                        ),
                    }],
                    "single-sided route resolved but transfer hub is still missing",
                )]
            elif context.get("asks_benchmark") or context.get("asks_travel_advice") or context.get("asks_recommendation"):
                obj = "s2s_benchmark"
            else:
                obj = self._pick_s2s_object(context.get("query_date"))

            return [self._proposal(
                "partial_route_context_completion_expert",
                98,
                [self._make_query(obj, route, date=context.get("query_date"))],
                "single-sided route was completed locally from reusable memory context",
            )]

        slots = self._infer_pending_slots_from_context(context) or ["dep", "arr"]
        pending_context = {
            "route": context.get("route"),
            "dep": context.get("dep"),
            "arr": context.get("arr"),
            "station": ",".join(context.get("station_mentions") or []),
            "date": context.get("query_date"),
        }
        return [self._proposal(
            "partial_route_pending_expert",
            96,
            [{
                "action": "pending",
                "params": normalize_pending_payload(
                    question="",
                    slot=slots,
                    context=pending_context,
                ),
            }],
            "single-sided route should ask for the missing side directly",
        )]

    def _expert_telecode(self, context: dict) -> list[dict]:
        if not context["asks_telecode"]:
            return []

        if context["telecode"] and context["asks_station_reverse"]:
            return [self._proposal(
                "telecode_reverse_expert",
                97,
                [self._make_query("name", context["telecode"])],
                "telecode reverse lookup",
            )]

        station_name = (
            context["text"]
            .replace("的电报码", "")
            .replace("电报码", "")
            .replace("是什么", "")
            .replace("是啥", "")
        )
        station_name = re.sub(r"^[请问帮我查一下\s]+", "", station_name).strip(" ，。？！?：:")
        if not station_name:
            return []

        return [self._proposal(
            "telecode_expert",
            95,
            [self._make_query("telecode", station_name)],
            "station to telecode lookup",
        )]

    def _expert_route_preference(self, context: dict) -> list[dict]:
        route = context.get("route")
        if not route:
            return []

        route_level_intent = bool(
            context.get("asks_benchmark")
            or context.get("asks_listing")
            or context.get("asks_assignment")
            or context.get("asks_travel_advice")
            or context.get("asks_recommendation")
            or context.get("asks_ticket")
            or "有没有" in str(context.get("text") or "")
        )
        if not route_level_intent:
            return []

        bureau_preferences = context.get("bureau_preferences") or []
        emu_preferences = context.get("emu_preferences") or []
        explicit_train_numbers = context.get("explicit_train_numbers") or []
        smart_emu_route_intent = bool(
            context.get("asks_assignment") and self._has_smart_emu_intent(context.get("text", ""))
        )
        has_soft_constraints = bool(
            bureau_preferences
            or emu_preferences
            or context.get("explicit_emu_id")
            or explicit_train_numbers
            or smart_emu_route_intent
        )
        if not has_soft_constraints:
            return []

        tasks: list[dict] = []
        if context.get("asks_ticket"):
            tasks.append(self._make_query("left_ticket_s2s", route, date=context["query_date"]))
        elif context.get("asks_benchmark") or context.get("asks_travel_advice") or context.get("asks_recommendation"):
            tasks.append(self._make_query("s2s_benchmark", route, date=context["query_date"]))
        else:
            tasks.append(self._make_query(self._pick_s2s_object(context["query_date"]), route, date=context["query_date"]))

        if bureau_preferences:
            tasks.append(
                self._make_query(
                    "s2s_bureau_filter",
                    f"{route}|{bureau_preferences[0]}",
                    date=context["query_date"],
                )
            )

        if emu_preferences or context.get("explicit_emu_id") or smart_emu_route_intent:
            tasks.append(self._make_query("station_to_station_mini", route, date=context["query_date"]))

        if explicit_train_numbers:
            if context.get("asks_ticket"):
                tasks.extend(
                    self._make_query("path_detail", train_no, date=context["query_date"])
                    for train_no in explicit_train_numbers[:2]
                )
            else:
                tasks.extend(self._make_query("train", train_no) for train_no in explicit_train_numbers[:3])
                if context.get("asks_benchmark"):
                    tasks.extend(
                        self._make_query("path_detail", train_no, date=context["query_date"])
                        for train_no in explicit_train_numbers[:2]
                    )

        return [self._proposal(
            "route_preference_expert",
            99,
            self._dedupe_query_tasks(tasks),
            "route intent with bureau/model preference should stay on route-level tools",
        )]

    def _legacy_expert_contextual_lookup_v1(self, context: dict) -> list[dict]:
        if context.get("asks_chat") and not context.get("asks_contextual_evidence_followup"):
            return []

        has_context_reference = bool(
            context.get("asks_contextual_route_followup")
            or context.get("asks_contextual_assignment")
            or context.get("asks_contextual_evidence_followup")
            or self._looks_like_contextual_followup(context.get("text", ""))
        )
        if not has_context_reference:
            return []

        if context.get("explicit_route") or context.get("explicit_train_numbers") or context.get("explicit_emu_id") or context.get("telecode"):
            return []

        context_pool = context.get("context_entity_pool") or {}
        route = context.get("route") or next(iter(context_pool.get("routes") or []), None)
        trains = list(context.get("train_numbers") or context_pool.get("trains") or [])
        if not context.get("explicit_train_numbers") and context.get("asks_contextual_assignment") and context_pool.get("trains"):
            trains = list(context_pool.get("trains") or [])
        date = context.get("query_date")

        if context.get("asks_ticket") and route:
            tasks = [self._make_query("left_ticket_s2s", route, date=date)]
            if trains:
                tasks.append(self._make_query("train", trains[0]))
            return [self._proposal(
                "context_ticket_expert",
                98,
                self._dedupe_query_tasks(tasks),
                "context expert reused route and preferred train for ticket follow-up",
            )]

        if context.get("asks_assignment") and trains:
            smart_emu = "智能" in context.get("text", "")
            tasks = (
                [self._make_query("smartemu_analysis", ",".join(trains[:5]))]
                if smart_emu
                else [self._make_query("train", train_no) for train_no in trains[:5]]
            )
            return [self._proposal(
                "context_assignment_expert",
                98,
                self._dedupe_query_tasks(tasks),
                "context expert reused prior trains for assignment follow-up",
            )]

        if context.get("asks_stopcheck") and trains and context.get("stopcheck_stations"):
            query_id = f"{','.join(trains[:20])}|{','.join((context.get('stopcheck_stations') or [])[:10])}"
            return [self._proposal(
                "context_stopcheck_expert",
                97,
                [self._make_query("path_stopcheck", query_id, date=date)],
                "context expert reused prior trains for stopcheck follow-up",
            )]

        if context.get("asks_contextual_evidence_followup") and trains:
            return [self._proposal(
                "context_path_evidence_expert",
                99,
                [self._make_query("path_detail", train_no, date=date) for train_no in trains[:3]],
                "context expert reused prior trains for evidence challenge follow-up",
            )]

        if (context.get("asks_train_terminal") or context.get("asks_path")) and trains:
            return [self._proposal(
                "context_path_expert",
                97,
                [self._make_query("path_detail", trains[0], date=date)],
                "context expert reused prior train for path follow-up",
            )]

        if route and (
            context.get("asks_benchmark")
            or context.get("asks_listing")
            or context.get("asks_travel_advice")
            or context.get("asks_recommendation")
            or context.get("asks_contextual_route_followup")
        ):
            obj = self._pick_contextual_route_object(context)
            if not obj:
                obj = "s2s_benchmark" if context.get("asks_benchmark") or context.get("asks_recommendation") else self._pick_s2s_object(date)
            return [self._proposal(
                "context_route_expert",
                97,
                [self._make_query(obj, route, date=date)],
                "context expert reused previous route for follow-up",
            )]

        return []

    def _legacy_expert_contextual_assignment_v1(self, context: dict) -> list[dict]:
        assignment_intent = bool(context.get("asks_assignment")) or any(
            token in context.get("text", "") for token in ("智能动车", "智能动车组", "智能复兴号", "车底", "担当", "车型", "车组")
        )
        if not assignment_intent:
            return []
        if context.get("train_numbers"):
            return []

        recent_trains = list((context.get("context_entity_pool") or context.get("recent_entity_pool") or {}).get("trains") or [])
        if not recent_trains:
            return []
        if not context.get("asks_contextual_assignment"):
            return []

        trains = recent_trains[:5]
        if any(token in context.get("text", "") for token in ("智能动车", "智能动车组", "智能复兴号")):
            tasks = [self._make_query("smartemu_analysis", ",".join(trains))]
            reason = "contextual smart emu follow-up reused recent trains"
            name = "contextual_smartemu_expert"
        else:
            tasks = [self._make_query("train", train_no) for train_no in trains]
            reason = "contextual assignment follow-up reused recent trains"
            name = "contextual_train_assignment_expert"

        return [self._proposal(
            name,
            98,
            self._dedupe_query_tasks(tasks),
            reason,
        )]

    def _expert_emu(self, context: dict) -> list[dict]:
        if not context["emu_id"]:
            return []
        if context.get("route") and (
            context.get("asks_benchmark")
            or context.get("asks_listing")
            or context.get("asks_travel_advice")
            or context.get("asks_recommendation")
            or context.get("asks_ticket")
            or context.get("asks_transfer")
        ):
            return []
        return [self._proposal(
            "emu_expert",
            96,
            [self._make_query("emu", context["emu_id"])],
            "explicit emu id found",
        )]

    def _legacy_expert_train_assignment_v1(self, context: dict) -> list[dict]:
        assignment_intent = bool(context.get("asks_assignment")) or any(
            token in context.get("text", "") for token in ("智能动车", "智能动车组", "智能复兴号", "车底", "担当", "车型", "车组", "长编组", "短编组", "重联", "单组", "16节", "8节", "编组")
        )
        if not context["train_numbers"] or not assignment_intent:
            return []

        effective_trains = list(context["train_numbers"])
        recent_trains = list((context.get("context_entity_pool") or context.get("recent_entity_pool") or {}).get("trains") or [])
        if not context.get("explicit_train_numbers") and context.get("asks_contextual_assignment") and recent_trains:
            effective_trains = recent_trains[:5]

        if any(token in context.get("text", "") for token in ("智能动车", "智能动车组", "智能复兴号")):
            return [self._proposal(
                "smartemu_assignment_expert",
                96,
                [self._make_query("smartemu_analysis", ",".join(effective_trains[:5]))],
                "explicit train smart emu assignment intent",
            )]

        return [self._proposal(
            "train_assignment_expert",
            95,
            [self._make_query("train", train_no) for train_no in effective_trains[:5]],
            "train assignment intent",
        )]

    def _expert_train_stopcheck(self, context: dict) -> list[dict]:
        train_numbers = context.get("train_numbers") or []
        stopcheck_stations = context.get("stopcheck_stations") or []
        asks_stopcheck = bool(context.get("asks_stopcheck"))

        if asks_stopcheck and train_numbers and stopcheck_stations:
            query_id = f"{','.join(train_numbers[:20])}|{','.join(stopcheck_stations[:10])}"
            return [self._proposal(
                "train_stopcheck_expert",
                98,
                [self._make_query("path_stopcheck", query_id, date=context["query_date"])],
                "explicit train plus station stopcheck intent",
            )]

        if asks_stopcheck and train_numbers and not stopcheck_stations:
            return [self._proposal(
                "train_stopcheck_pending_expert",
                93,
                [{
                    "action": "pending",
                    "params": normalize_pending_payload(
                        question="请告诉我要核验的车站名，我就可以判断这趟车是否停靠。",
                        slot=["station_name"],
                        context={
                            "train_no": ",".join(train_numbers[:5]),
                            "date": context.get("query_date"),
                        },
                    ),
                }],
                "stopcheck intent missing explicit station",
            )]

        if asks_stopcheck and stopcheck_stations and not train_numbers:
            return [self._proposal(
                "train_stopcheck_missing_train_expert",
                90,
                [{
                    "action": "pending",
                    "params": normalize_pending_payload(
                        question="请告诉我具体车次号，我再帮你核验这个站是否停靠。",
                        slot=["train_no"],
                        context={
                            "station": ",".join(stopcheck_stations[:5]),
                            "date": context.get("query_date"),
                        },
                    ),
                }],
                "stopcheck intent missing train number",
            )]

        return []

    def _expert_train_terminal(self, context: dict) -> list[dict]:
        train_numbers = context.get("train_numbers") or []
        if not context.get("asks_train_terminal"):
            return []

        if train_numbers:
            return [self._proposal(
                "train_terminal_expert",
                97,
                [self._make_query("path_detail", train_numbers[0], date=context["query_date"])],
                "explicit train terminal/origin-destination intent",
            )]

        return [self._proposal(
            "train_terminal_pending_expert",
            91,
            [{
                "action": "pending",
                "params": normalize_pending_payload(
                    question="请告诉我具体车次号，比如 G88，我就可以帮你查这趟车从哪里始发、开往哪里。",
                    slot=["train_no"],
                    context={"date": context.get("query_date")},
                ),
            }],
            "train terminal intent missing train number",
        )]

    def _expert_train_path(self, context: dict) -> list[dict]:
        if not context["train_numbers"] or not context["asks_path"]:
            return []

        return [self._proposal(
            "train_path_expert",
            94,
            [self._make_query("path_detail", train_no, date=context["query_date"]) for train_no in context["train_numbers"][:3]],
            "train stop/path or runtime-status intent",
        )]

    def _expert_train_comparison(self, context: dict) -> list[dict]:
        train_numbers = list(context.get("train_numbers") or [])
        if len(train_numbers) < 2 or not context.get("asks_train_comparison_lookup"):
            return []

        return [self._proposal(
            "train_comparison_expert",
            99,
            [self._make_query("path_detail", train_no, date=context["query_date"]) for train_no in train_numbers[:4]],
            "explicit multi-train comparison should stay on path tools",
        )]

    def _expert_train_ticket(self, context: dict) -> list[dict]:
        train_numbers = list(context.get("train_numbers") or [])
        if not train_numbers or not context.get("asks_ticket"):
            return []

        context_pool = context.get("context_entity_pool") or context.get("recent_entity_pool") or {}
        route = context.get("route") or next(iter(context_pool.get("routes") or []), None)
        date = context.get("query_date")

        tasks: list[dict] = [self._make_query("path_detail", train_numbers[0], date=date)]
        if route:
            tasks.insert(0, self._make_query("left_ticket_s2s", route, date=date))

        return [self._proposal(
            "train_ticket_validation_expert",
            98 if route else 96,
            self._dedupe_query_tasks(tasks),
            "explicit train ticket validation should stay on railway tools",
        )]

    def _expert_left_ticket(self, context: dict) -> list[dict]:
        if not context["route"] or not context["asks_ticket"]:
            return []

        return [self._proposal(
            "left_ticket_expert",
            94,
            [self._make_query("left_ticket_s2s", context["route"], date=context["query_date"])],
            "route plus ticket keywords",
        )]

    def _expert_transfer(self, context: dict) -> list[dict]:
        if not context["route"] or not context["asks_transfer"]:
            return []

        hub = self._extract_transfer_hub(context["text"], context["route"])
        if hub:
            return [self._proposal(
                "transfer_expert",
                93,
                [self._make_query("transfer_12306", f"{context['route']}|{hub}", date=context["query_date"])],
                "route plus transfer hub",
            )]

        return [self._proposal(
            "transfer_pending_expert",
            91,
            [{
                "action": "pending",
                "params": {
                    "question": "我可以查官方中转方案，请告诉我你希望经哪个中转站或城市。"
                }
            }],
            "transfer intent but missing hub",
        )]

    def _expert_route_benchmark(self, context: dict) -> list[dict]:
        if not context["route"]:
            return []

        wants_benchmark = context["asks_benchmark"]
        wants_travel_advice = context["asks_travel_advice"] and not context["asks_transfer"] and not context["asks_ticket"]
        if not wants_benchmark and not wants_travel_advice:
            return []

        confidence = 96 if wants_benchmark else 84
        return [self._proposal(
            "route_benchmark_expert",
            confidence,
            [self._make_query("s2s_benchmark", context["route"], date=context["query_date"])],
            "route benchmark/travel advice intent",
        )]

    def _expert_route_listing(self, context: dict) -> list[dict]:
        if not context["route"] or not context["asks_listing"]:
            return []

        obj = self._pick_s2s_object(context["query_date"])
        return [self._proposal(
            "route_listing_expert",
            90,
            [self._make_query(obj, context["route"], date=context["query_date"])],
            "route listing intent",
        )]

    def _expert_route_general(self, context: dict) -> list[dict]:
        if not context["route"]:
            return []

        if context["asks_transfer"] or context["asks_ticket"] or context["asks_assignment"] or context["asks_path"]:
            return []

        if context["asks_benchmark"] or context["asks_listing"]:
            return []

        contextual_obj = self._pick_contextual_route_object(context)
        if contextual_obj:
            return [self._proposal(
                "contextual_route_followup_expert",
                93,
                [self._make_query(contextual_obj, context["route"], date=context["query_date"])],
                "explicit route follow-up reused previous route query type",
            )]

        if context["mentions_rail"] or any(token in context["text"] for token in ("看看", "查查", "帮我查", "我想去", "想去", "坐哪趟")):
            obj = self._pick_s2s_object(context["query_date"])
            return [self._proposal(
                "route_general_expert",
                74,
                [self._make_query(obj, context["route"], date=context["query_date"])],
                "generic route discovery intent",
            )]

        return []

    def _route_with_fast_llm(
        self,
        user_text: str,
        session: SessionMemory | None,
        context_agent_result: dict | None = None,
        prefetched_context: dict | None = None,
    ) -> list[dict]:
        context = prefetched_context if isinstance(prefetched_context, dict) else self._build_fast_route_context(
            user_text,
            session=session,
            context_agent_result=context_agent_result,
        )
        messages = self._build_fast_router_messages(
            user_text,
            session,
            context_agent_result=context_agent_result,
            prefetched_context=context,
        )
        raw = self.llm.generate(messages)
        repaired = self._repair_fast_tasks(self._safe_parse_tasks(raw, context=context), strict_invalid=False)
        repaired = self._apply_context_date_to_tasks(repaired, context)
        repaired = self._enrich_pending_tasks(repaired, context)
        if self._should_prefer_chat_fallback_for_fast_llm(context):
            if not repaired or repaired[0].get("action") == "pending":
                chat_fallback = self._expert_chat(context)
                if chat_fallback:
                    return chat_fallback[0]["tasks"]
        if self._is_useful_fast_llm_result(repaired):
            return repaired
        return []

    def _should_prefer_chat_fallback_for_fast_llm(self, context: dict | None) -> bool:
        if not isinstance(context, dict):
            return False
        consensus = context.get("semantic_consensus") or {}
        if str(consensus.get("preferred_action") or "").strip().lower() == "chat":
            return True

        return bool(
            context.get("asks_chat")
            or context.get("asks_general_rail_knowledge")
            or context.get("asks_generic_chat")
            or context.get("asks_contextual_chat")
            or context.get("asks_contextual_social_chat")
        )

    def _apply_context_date_to_tasks(self, tasks: list[dict], context: dict | None) -> list[dict]:
        if not isinstance(tasks, list) or not isinstance(context, dict):
            return tasks
        query_date = str(context.get("query_date") or "").strip()
        query_date_source = str(context.get("query_date_source") or "").strip()
        if not query_date or not query_date_source or query_date_source == "default_today":
            return tasks

        today = datetime.now().strftime("%Y-%m-%d")
        dateful_objects = {
            "left_ticket_s2s",
            "transfer_12306",
            "s2s_benchmark",
            "s2s_timeband_dep",
            "s2s_timeband_arr",
            "s2s_regular_only",
            "s2s_temporary_only",
            "s2s_bureau_filter",
            "path_detail",
            "path_future",
            "path_past",
            "path_stopcheck",
            "station_to_station_mini",
            "station_to_station_detail",
            "station_to_station_future",
            "station_to_station_past",
            "train_station_access",
        }

        fixed: list[dict] = []
        for task in tasks:
            if not isinstance(task, dict) or task.get("action") != "query":
                fixed.append(task)
                continue
            params = task.get("params") if isinstance(task.get("params"), dict) else {}
            obj = str(params.get("object") or "").strip()
            if obj in dateful_objects and (not params.get("date") or str(params.get("date")) == today):
                cloned = {"action": "query", "params": dict(params)}
                cloned["params"]["date"] = query_date
                fixed.append(cloned)
            else:
                fixed.append(task)
        return fixed

    def _should_escalate_short_contextual_pending_to_fast_llm(self, context: dict | None) -> bool:
        if not isinstance(context, dict):
            return False

        if context.get("has_partial_route_query_intent"):
            return False

        has_explicit_hard_entities = bool(
            context.get("explicit_route")
            or context.get("explicit_train_numbers")
            or context.get("explicit_emu_id")
            or context.get("telecode")
        )
        if has_explicit_hard_entities:
            return False

        recent_pool = context.get("context_entity_pool") or context.get("recent_entity_pool") or {}
        has_recent_rail_context = any(
            bool(recent_pool.get(key)) for key in ("trains", "routes", "objects", "stations")
        )
        if not has_recent_rail_context:
            return False

        compact = re.sub(r"\s+", "", str(context.get("text") or ""))
        if not compact or len(compact) > 24:
            return False

        return True

    def _should_escalate_pending_to_fast_llm(self, tasks: list[dict], context: dict | None) -> bool:
        if not tasks or not isinstance(context, dict):
            return False

        first = tasks[0] if isinstance(tasks[0], dict) else {}
        if first.get("action") != "pending":
            return False

        if context.get("asks_train_comparison_lookup"):
            return False

        params = first.get("params") if isinstance(first.get("params"), dict) else {}
        pending_slots = list(params.get("slot") or [])
        pending_question = str(params.get("question") or "")
        generic_pending = (
            not pending_slots
            or pending_question in {"请补充一下", "请补充信息", "请补充一下关键信息"}
            or "还缺的那个关键信息" in pending_question
        )

        consensus = context.get("semantic_consensus") or {}
        semantic_chat = str(consensus.get("preferred_action") or "").strip().lower() == "chat"
        return bool(
            semantic_chat
            or self._should_prefer_chat_fallback_for_fast_llm(context)
            or (generic_pending and self._should_escalate_short_contextual_pending_to_fast_llm(context))
        )

    def _build_fast_router_messages(
        self,
        user_text: str,
        session: SessionMemory | None,
        context_agent_result: dict | None = None,
        prefetched_context: dict | None = None,
    ) -> list[dict]:
        context = prefetched_context if isinstance(prefetched_context, dict) else self._build_fast_route_context(
            user_text,
            session=session,
            context_agent_result=context_agent_result,
        )
        proposals = self._collect_fast_route_proposals(context)[:4]
        capability_contract = capability_catalog_for_mode(self._effective_mode_profile())

        messages = [
            {
                "role": "system",
                "content": (
                    "You are RailGPT Fast Intent Arbiter.\n"
                    "Goal: convert one railway user message into the smallest correct action JSON.\n"
                    "Prefer one precise query task when possible.\n"
                    "Do not overthink.\n"
                    "Output JSON only.\n"
                    "Allowed actions: query, pending, chat.\n"
                    "The Semantic Router Council result is a strong proposal, but verify it against this same registry and the latest context.\n"
                    "For a workflow manifest, return its declared executable query steps rather than querying the workflow name itself.\n"
                    "For chat, return a short safe handoff instruction in params.message, not the final answer.\n"
                    "For pending, ask only for missing required slots from the selected manifest and include slot/context.\n"
                    "Never resurrect an unavailable or unlisted capability.\n"
                    f"{capability_contract}\n"
                ),
            }
        ]

        memory_context = context.get("memory_context")
        if memory_context:
            messages.append(
                {
                    "role": "system",
                    "content": (
                        "Conversation memory recall is available.\n"
                        "Reuse these anchors before returning pending for missing slots.\n"
                        f"{memory_context}"
                    ),
                }
            )

        context_agent_summary = self._format_context_agent_summary(context_agent_result or {})
        if context_agent_summary:
            messages.append(
                {
                    "role": "system",
                    "content": context_agent_summary,
                }
            )

        if session:
            if hasattr(session, "build_agent_context_view"):
                context_view = session.build_agent_context_view(
                    role="router",
                    mode=self.mode_profile,
                    user_text=user_text,
                    date_resolution=context.get("date_resolution"),
                    include_dialogue=False,
                )
                messages.append(
                    {
                        "role": "system",
                        "content": (
                            "Structured AgentContextPackage for this router arbiter:\n"
                            f"{json.dumps(context_view, ensure_ascii=False)}"
                        ),
                    }
                )
        semantic_consensus = context.get("semantic_consensus") or {}
        if semantic_consensus:
            messages.append(
                {
                    "role": "system",
                    "content": (
                        "Semantic router council summary:\n"
                        f"{json.dumps(semantic_consensus, ensure_ascii=False)}"
                    ),
                }
            )
        if session:
            if hasattr(session, "build_llm_history"):
                recent_messages = session.build_llm_history(
                    mode=self.mode_profile,
                    exclude_current_user=True,
                    latest_user_text=user_text,
                )
            else:
                recent_messages = list(session.get_recent_messages()[-2:])
                if recent_messages and recent_messages[-1].get("role") == "user" and recent_messages[-1].get("content") == user_text:
                    recent_messages = recent_messages[:-1]
            messages.extend(recent_messages)

        proposal_lines = [
            f"- {item['name']} | conf={item['confidence']} | tasks={json.dumps(item['tasks'], ensure_ascii=False)}"
            for item in proposals
        ]
        if not proposal_lines:
            proposal_lines.append("- none")

        messages.append(
            {
                "role": "user",
                "content": (
                    f"USER_QUERY: {user_text}\n"
                    f"NORMALIZED_QUERY: {context.get('text') or user_text}\n"
                    f"EXTRACTED_ROUTE: {context.get('route') or 'none'}\n"
                    f"ROUTE_CANDIDATES: {', '.join(context.get('route_candidates') or []) or 'none'}\n"
                    f"EXTRACTED_DATE: {context.get('query_date') or 'none'}\n"
                    f"EXTRACTED_DATE_SOURCE: {context.get('query_date_source') or 'none'}\n"
                    f"EXTRACTED_TRAINS: {', '.join(context.get('train_numbers') or []) or 'none'}\n"
                    f"EXTRACTED_EMU: {context.get('emu_id') or 'none'}\n"
                    f"CONTEXT_AGENT_INTENT: {context.get('context_agent_intent') or 'none'}\n"
                    f"ANCHOR_QUERY_OBJECT: {context.get('anchor_query_object') or context.get('anchor_query_type') or 'none'}\n"
                    f"EMU_PREFERENCES: {', '.join(context.get('emu_preferences') or []) or 'none'}\n"
                    f"BUREAU_PREFERENCES: {', '.join(context.get('bureau_preferences') or []) or 'none'}\n"
                    "MICRO_AGENT_PROPOSALS:\n"
                    + "\n".join(proposal_lines)
                ),
            }
        )

        return messages

    def _is_useful_fast_llm_result(self, safe_tasks: list[dict]) -> bool:
        if not safe_tasks:
            return False

        first = safe_tasks[0]
        if first["action"] == "chat":
            message = str(first.get("params", {}).get("message") or "")
            return "Router" not in message

        return first["action"] in {"query", "pending"}

    def _proposal(self, name: str, confidence: int, tasks: list[dict], reason: str) -> dict:
        return {
            "name": name,
            "confidence": confidence,
            "tasks": tasks,
            "reason": reason,
        }

    def _set_router_state(self, state: AgentState, detail: str):
        if self.psw:
            self.psw.set_state(state, detail)

    def _elapsed_ms(self, started_at: float) -> int:
        return int((time.perf_counter() - started_at) * 1000)

    def get_last_intent_envelope(self) -> dict:
        return self.last_intent_envelope.to_dict()

    def _build_capability_tasks(
        self,
        object_name: str,
        query_id: str,
        query_date: str,
        context: dict,
    ) -> list[dict]:
        """Compile one selected manifest into executable query tasks."""

        capability = get_capability(object_name)
        if capability is None or not capability.is_available:
            return []

        route = str(context.get("explicit_route") or context.get("route") or "").strip()
        tasks: list[dict] = []
        for step_object, input_source in active_workflow_steps(capability, context):
            if input_source == "result_trains":
                # Deferred MCP workflow input: the evidence gate binds this
                # step only after the preceding OD tool returns real trains.
                continue
            step_capability = get_capability(step_object)
            if step_capability is None or not step_capability.is_executable:
                return []

            if input_source == "route":
                step_id = route or resolve_query_id(step_object, context)
            elif input_source == "train":
                step_id = resolve_query_id(step_object, context, suggested_id=query_id)
            elif input_source == "station":
                step_id = resolve_query_id(step_object, context)
            else:
                step_id = resolve_query_id(step_object, context, suggested_id=query_id)
            if not step_id:
                return []

            accepts_date = "date" in (*step_capability.required_slots, *step_capability.optional_slots)
            tasks.append(
                self._make_query(
                    step_object,
                    step_id,
                    date=query_date if accepts_date and query_date else None,
                )
            )
        return tasks

    def _finalize_routed_tasks(self, tasks: list[dict], context: dict | None) -> list[dict]:
        tasks = list(tasks or [])
        context = context if isinstance(context, dict) else {}

        if context.get("asks_live_delay"):
            train_id = resolve_query_id("train_delay", context)
            query_date = str(context.get("query_date") or "").strip()
            date_source = str(context.get("query_date_source") or "")
            today = datetime.now().strftime("%Y-%m-%d")
            if date_source != "default_today" and query_date and query_date != today:
                tasks = [{
                    "action": "chat",
                    "params": {
                        "message": (
                            "Dedicated capability-boundary chat route: explain that RailGo live delay evidence is current-only. "
                            "Do not substitute current status for a historical or future date."
                        )
                    },
                }]
            elif train_id:
                tasks = self._repair_fast_tasks(
                    self._build_capability_tasks("train_delay", train_id, query_date, context),
                    strict_invalid=False,
                )
            else:
                tasks = [{
                    "action": "pending",
                    "params": normalize_pending_payload(
                        question="告诉我需要核验的车次号，我就直接查询当前晚点状态；不需要补充出发站和到达站。",
                        slot=["train"],
                    ),
                }]
            self.last_intent_envelope = build_intent_envelope(
                selected_capability="train_delay",
                context=context,
                intent_family="live_delay",
                confidence=max(90, self.last_intent_envelope.confidence),
            )

        query_objects = [
            str(task.get("params", {}).get("object") or "").strip()
            for task in tasks
            if isinstance(task, dict) and task.get("action") == "query"
        ]
        selected = self.last_intent_envelope.selected_capability
        selected_contract = get_capability(selected)
        selected_workflow = set(selected_contract.workflow if selected_contract else ())
        if selected and selected_workflow and selected_workflow.issubset(set(query_objects)):
            pass
        elif not selected:
            composite = infer_composite_capability(query_objects)
            selected = composite.object if composite else (query_objects[0] if query_objects else "")
        elif selected not in query_objects:
            selected = "train_delay" if "train_delay" in query_objects else (query_objects[0] if query_objects else selected)

        semantic = context.get("semantic_consensus") if isinstance(context.get("semantic_consensus"), dict) else {}
        if selected:
            validation_context = dict(context)
            prior_envelope = self.last_intent_envelope
            self._merge_semantic_grounded_slots(
                validation_context,
                prior_envelope.grounded_slots,
                authoritative=True,
            )
            compiled_trains: list[str] = []
            compiled_stations: list[str] = []
            for task in tasks:
                if not isinstance(task, dict) or task.get("action") != "query":
                    continue
                params = task.get("params") if isinstance(task.get("params"), dict) else {}
                decoded = grounded_slots_from_query_params(
                    str(params.get("object") or ""),
                    str(params.get("id") or ""),
                    str(params.get("date") or ""),
                )
                for train in decoded.get("trains") or []:
                    if train not in compiled_trains:
                        compiled_trains.append(train)
                for station in decoded.get("stations") or []:
                    if station not in compiled_stations:
                        compiled_stations.append(station)
                self._merge_semantic_grounded_slots(
                    validation_context,
                    decoded,
                    authoritative=True,
                )
                decoded_date = str(decoded.get("date") or "").strip()
                prior_date = str(prior_envelope.grounded_slots.get("date") or "").strip()
                if decoded_date and decoded_date == prior_date:
                    validation_context["query_date"] = decoded_date
                    if str(validation_context.get("query_date_source") or "") == "default_today":
                        validation_context["query_date_source"] = "validated_semantic_task"
            if compiled_trains:
                validation_context["train_numbers"] = compiled_trains
            if compiled_stations:
                validation_context["station_mentions"] = compiled_stations
            validated_dep = str(validation_context.get("dep") or "").strip()
            validated_arr = str(validation_context.get("arr") or "").strip()
            if validated_dep and validated_arr:
                validation_context["explicit_route"] = f"{validated_dep}-{validated_arr}"
                validation_context["route"] = validation_context["explicit_route"]
            self.last_intent_envelope = build_intent_envelope(
                selected_capability=selected,
                context=validation_context,
                intent_family=str(semantic.get("intent") or self.last_intent_envelope.intent_family or ""),
                confidence=int(semantic.get("confidence") or self.last_intent_envelope.confidence or 90),
            )
            self._set_router_state(
                AgentState.CAPABILITY_VALIDATED,
                (
                    f"capability={selected} required_evidence="
                    f"{','.join(self.last_intent_envelope.required_evidence) or 'none'} "
                    f"missing_slots={','.join(self.last_intent_envelope.missing_slots) or 'none'}"
                ),
            )
            if query_objects and self.last_intent_envelope.missing_slots:
                slot_contract = build_missing_slot_contract(
                    selected,
                    self.last_intent_envelope.missing_slots,
                    self.last_intent_envelope.grounded_slots,
                )
                tasks = [{
                    "action": "pending",
                    "params": normalize_pending_payload(
                        question="请补充这项查询真正必需的信息，我就继续查询。",
                        slot=self.last_intent_envelope.missing_slots,
                        context={
                            "query_object": selected,
                            "missing_slot_contract": slot_contract,
                            **{
                                key: value
                                for key, value in self.last_intent_envelope.grounded_slots.items()
                                if value not in (None, "", [], {})
                            },
                        },
                    ),
                }]
        elif tasks and tasks[0].get("action") == "chat":
            semantic_intent = str(
                semantic.get("intent")
                or self.last_intent_envelope.intent_family
                or ""
            ).strip().lower()
            if semantic_intent == "memory_profile_chat":
                intent_family = "memory_profile_chat"
            elif context.get("asks_smalltalk_chat"):
                intent_family = "social_chat"
            elif context.get("asks_contextual_chat") or context.get("asks_contextual_social_chat"):
                intent_family = "contextual_chat"
            elif context.get("asks_general_rail_knowledge"):
                intent_family = "knowledge_chat"
            else:
                intent_family = "chat"
            self.last_intent_envelope = IntentEnvelope(
                intent_family=intent_family,
                confidence=90,
                context_fingerprint=str((context.get("agent_context_package") or {}).get("context_fingerprint") or ""),
            )
        return tasks

    def _make_query(self, obj: str, obj_id: str, date: str | None = None) -> dict:
        params = {
            "domain": "railway",
            "object": obj,
            "id": obj_id,
        }
        if date:
            params["date"] = date
        return {"action": "query", "params": params}

    def _dedupe_query_tasks(self, tasks: list[dict]) -> list[dict]:
        deduped: list[dict] = []
        seen: set[str] = set()
        for task in tasks or []:
            if not isinstance(task, dict):
                continue
            signature = json.dumps(task, ensure_ascii=False, sort_keys=True)
            if signature in seen:
                continue
            seen.add(signature)
            deduped.append(task)
        return deduped

    def _pick_s2s_object(self, query_date: str | None) -> str:
        if not query_date:
            return "station_to_station_mini"

        today = datetime.now().strftime("%Y-%m-%d")
        if query_date > today:
            return "station_to_station_future"
        if query_date < today:
            return "station_to_station_past"
        return "station_to_station_mini"

    def _extract_train_numbers(self, text: str) -> list[str]:
        compact = re.sub(r"\s+", "", str(text or "")).upper()
        direct_matches = re.findall(r"[GDKTZC]\d{1,5}", compact)
        seen = []

        if len(direct_matches) == 2:
            range_match = re.search(r"([GDKTZC])(\d{1,5})(?:到|至|[-~～—－])(?:\1)?(\d{1,5})", compact)
            if range_match:
                prefix, start_no, end_no = range_match.groups()
                try:
                    start_idx = int(start_no)
                    end_idx = int(end_no)
                except Exception:
                    start_idx = end_idx = -1
                if start_idx >= 0 and end_idx >= 0:
                    if start_idx > end_idx:
                        start_idx, end_idx = end_idx, start_idx
                    if end_idx - start_idx <= 19:
                        for value in range(start_idx, end_idx + 1):
                            item = f"{prefix}{value}"
                            if item not in seen:
                                seen.append(item)

        for item in direct_matches:
            if item not in seen:
                seen.append(item)
        return seen

    def _looks_like_train_terminal_intent(self, text: str) -> bool:
        compact = re.sub(r"\s+", "", str(text or ""))
        if not compact:
            return False

        if any(
            token in compact
            for token in (
                "始发终到",
                "始发到终到",
                "始发站",
                "终到站",
                "开往哪里",
                "开去哪里",
                "去哪里",
                "去哪",
                "去哪儿",
                "到哪里",
                "到哪",
                "起点站",
                "终点站",
                "从哪里开",
                "从哪开",
                "哪里开",
                "哪里到",
            )
        ):
            return True

        patterns = (
            r"从哪里.*去哪里",
            r"从哪里.*到哪里",
            r"从哪.*去哪",
            r"从哪.*到哪",
            r"从哪里开.*去哪里",
            r"从哪里开.*到哪里",
            r"从哪开.*去哪",
            r"从哪开.*到哪",
        )
        return any(re.search(pattern, compact) for pattern in patterns)

    def _looks_like_train_line_membership_intent(self, text: str) -> bool:
        compact = re.sub(r"\s+", "", str(text or ""))
        if not compact:
            return False

        direct_tokens = (
            "京津城际",
            "属于哪条线",
            "属于哪条高铁",
            "属于哪条线路",
            "属于什么线",
            "属于什么高铁",
            "跑哪条线",
            "走哪条线",
            "沿着什么线",
            "沿着哪条线",
            "是不是城际",
            "是不是高铁线",
        )
        if any(token in compact for token in direct_tokens):
            return True

        return (
            "是不是" in compact
            and any(token in compact for token in ("城际", "高铁", "线路", "线"))
        )

    def _looks_like_generic_chat_turn(self, text: str) -> bool:
        compact = re.sub(r"\s+", "", str(text or ""))
        if not compact:
            return False

        exact_phrases = {
            "这是什么",
            "这是啥",
            "这啥",
            "这啥意思",
            "什么意思",
            "什么情况",
            "怎么回事",
            "为啥",
            "为什么",
            "说人话",
            "展开说说",
            "讲讲这个",
            "这是啥意思儿",
            "这啥玩意儿",
        }
        if compact in exact_phrases:
            return True

        startswith_phrases = (
            "这是什么",
            "这是啥",
            "这啥",
            "啥意思",
            "什么意思",
            "为什么",
            "为啥",
        )
        if any(compact.startswith(prefix) for prefix in startswith_phrases):
            return True

        return compact.startswith("这") and len(compact) <= 8 and any(
            token in compact for token in ("什么", "啥", "意思")
        )

    def _has_partial_route_query_intent(
        self,
        text: str,
        station_mentions: list[str] | None = None,
        route_candidates: list[str] | None = None,
        train_numbers: list[str] | None = None,
        emu_id: str | None = None,
        telecode: str | None = None,
    ) -> bool:
        compact = re.sub(r"\s+", "", str(text or ""))
        station_mentions = list(station_mentions or [])
        if not compact or not station_mentions:
            return False

        if route_candidates or train_numbers or emu_id or telecode:
            return False

        partial_route_tokens = (
            "查车",
            "查一下车",
            "查一下列车",
            "查一下高铁",
            "帮我查车",
            "帮我查一下车",
            "有什么车",
            "有哪些车",
            "有车吗",
            "余票",
            "有票",
            "最快",
            "标杆",
            "怎么坐",
            "怎么走",
            "怎么去",
            "从",
            "出发",
            "到",
            "去",
            "前往",
            "抵达",
            "想去",
        )
        return any(token in compact for token in partial_route_tokens)

    def _looks_like_general_rail_knowledge_question(
        self,
        text: str,
        route_candidates: list[str] | None = None,
        train_numbers: list[str] | None = None,
        emu_id: str | None = None,
        telecode: str | None = None,
        station_mentions: list[str] | None = None,
    ) -> bool:
        compact = re.sub(r"\s+", "", str(text or ""))
        if not compact:
            return False

        if telecode or "电报码" in compact:
            return False

        if any(token in compact for token in ("你是谁", "你能做什么", "你好", "谢谢", "在吗")):
            return False

        knowledge_tokens = (
            "什么是",
            "什么叫",
            "标准是什么",
            "技术标准",
            "设计标准",
            "规范是什么",
            "技术规范",
            "设计规范",
            "参数是什么",
            "允许速度",
            "通过速度",
            "是什么意思",
            "什么关系",
            "为什么",
            "为啥",
            "原理",
            "规则",
            "定义",
            "区别",
            "差异",
            "逻辑",
            "如何",
            "怎么安排",
            "怎么实现",
            "怎么规定",
            "怎么来的",
            "具体指什么",
            "决策依据",
            "优先权",
            "体验",
            "作用",
            "成本",
            "来源",
            "起源",
            "多大作用",
            "如何界定",
            "法规依据",
            "技术短板",
            "主要区别",
            "如何保证",
            "怎么看",
            "合法吗",
        )
        physical_explainer_marker = (
            any(token in compact for token in ("不能完全消除", "完全消除不了", "为什么不能完全"))
            and any(token in compact for token in ("高铁", "动车", "列车", "隧道", "耳朵", "耳压", "压迫感", "气压", "车厢", "压力"))
        )
        engineering_explainer_marker = (
            any(token in compact for token in ("道岔", "联络线", "交叉渡线", "转辙机", "轨道", "接触网", "信号系统", "线路所"))
            and any(token in compact for token in ("标准", "规范", "参数", "原理", "为什么", "为啥", "怎么做到", "怎么实现", "怎么规定", "为什么不需要减速", "为何不需要减速", "为什么不用减速", "为何不用减速", "允许速度", "通过速度"))
        )
        history_stats_marker = (
            any(token in compact for token in ("历史平均", "平均准点率", "平均旅速", "去年", "暑运", "晚点最多", "历史上某天"))
            and any(token in compact for token in ("高铁", "动车", "列车", "京沪", "车次", "准点", "晚点", "旅速"))
        )
        future_strategy_marker = (
            any(token in compact for token in ("会产生什么影响", "产生什么影响", "有什么影响", "会有什么影响", "有可能", "能不能", "是否可能", "你觉得", "你认为", "终极形态", "怎么调整", "如何调整", "如何制定", "优先保证", "怎么腾出", "腾出天窗"))
            and any(token in compact for token in ("京沪", "高铁", "运行图", "调度", "夜间动卧", "CR450", "天窗", "故障处理", "无人化"))
        )
        technical_feasibility_marker = (
            any(token in compact for token in ("有可能", "有没有可能", "能不能", "从技术上讲"))
            and any(token in compact for token in ("重联", "解编", "动车组", "列车", "高铁", "接触网", "故障处理"))
        )
        railfan_marker = (
            any(token in compact for token in ("拍车", "拍摄列车", "外号", "金凤凰", "海豚", "带鱼", "刷里程", "车迷运转", "经典路线", "同站台", "站台面", "接续方案", "换乘方案", "哪个站台"))
            and any(token in compact for token in ("高铁", "动车", "列车", "站台", "南京南", "车迷", "车型", "方向"))
        )
        vehicle_experience_marker = (
            any(token in compact for token in ("B塞", "B智", "优选一等座", "CRH380", "380B", "CR400", "AF-BS", "BF-A", "复兴号", "和谐号"))
            and any(token in compact for token in ("区别", "差异", "体验", "布局", "座椅", "内饰", "外观"))
        )
        operations_cause_marker = (
            any(token in compact for token in ("线路原因", "调度原因", "为什么只跑", "跑250", "限速原因"))
            and any(token in compact for token in ("高铁", "动车", "列车", "京沪", "车次", "线路", "调度"))
        )
        directional_comparison_marker = (
            any(token in compact for token in ("东西方向", "南北方向", "东西向", "南北向"))
            and any(token in compact for token in ("更快", "更慢", "谁更快", "哪个更快", "快还是慢", "快慢"))
            and any(token in compact for token in ("列车", "高铁", "动车", "车次", "火车"))
        )
        railfan_identification_marker = (
            any(token in compact for token in ("一眼认出", "怎么看出来", "怎么认出", "除了看水牌", "水牌"))
            and any(token in compact for token in ("车迷", "车次", "列车", "高铁", "动车"))
        )
        reliability_window_marker = (
            any(token in compact for token in ("最不容易晚点", "哪个时段", "哪个时间段", "哪一天"))
            and any(token in compact for token in ("晚点", "高铁", "动车", "列车", "京沪", "线路"))
        )
        explicit_train_knowledge_marker = bool(train_numbers) and any(
            token in compact
            for token in ("豹子号", "唯一", "最早是不是", "什么时候开始", "前身", "验证", "准不准")
        )
        has_knowledge_marker = (
            any(token in compact for token in knowledge_tokens)
            or physical_explainer_marker
            or engineering_explainer_marker
            or history_stats_marker
            or future_strategy_marker
            or technical_feasibility_marker
            or railfan_marker
            or vehicle_experience_marker
            or operations_cause_marker
            or directional_comparison_marker
            or railfan_identification_marker
            or reliability_window_marker
            or explicit_train_knowledge_marker
        )
        if not has_knowledge_marker:
            return False

        rail_scope_tokens = (
            "高铁",
            "动车",
            "列车",
            "车次",
            "铁路",
            "铁路局",
            "车迷",
            "线路",
            "调度",
            "时速",
            "站台",
            "换乘",
            "候补",
            "调图",
            "标杆车",
            "天窗",
            "限速",
            "CTCS",
            "道岔",
            "联络线",
            "交叉渡线",
            "转辙机",
            "无砟轨道",
            "接触网",
            "受电弓",
            "转向架",
            "重联",
            "滚动轴承",
            "信号机",
            "刷绿",
            "豹子号",
            "金凤凰",
            "海豚",
            "带鱼",
            "绿巨人",
            "大地铁",
            "亚洲南",
            "亚洲东",
            "亚洲北",
            "运转",
            "本务",
            "大车",
            "桶",
            "老鼠",
            "夜间动卧",
            "CR450",
            "无人化",
            "旅速",
            "准点率",
            "暑运",
            "优选一等座",
            "B塞",
            "B智",
            "CRH380",
            "380B",
            "CR400",
            "水牌",
        )
        has_rail_scope = bool(route_candidates or train_numbers or emu_id or station_mentions) or any(
            token in compact for token in rail_scope_tokens
        )
        if not has_rail_scope:
            return False

        clear_tool_lookup = bool(
            route_candidates and any(token in compact for token in ("余票", "有票", "最快", "标杆", "中转", "直达", "有哪些车", "有什么车"))
        ) or bool(
            train_numbers and any(
                token in compact
                for token in (
                    "经停",
                    "停站",
                    "时刻表",
                    "始发",
                    "终到",
                    "从哪里开",
                    "开往哪里",
                    "停不停",
                    "停靠",
                    "只停",
                    "加停",
                    "增停",
                    "开始加停",
                    "什么时候开始加停",
                    "停哪几站",
                    "实时位置",
                    "现在到哪",
                    "到哪了",
                    "运行到哪",
                    "晚点",
                    "晚了多久",
                    "正晚点",
                    "晚点了多久",
                )
            )
        ) or bool(
            train_numbers and any(
                token in compact
                for token in ("余票", "有票", "已售罄", "商务座", "一等座", "二等座", "候补", "余座", "票务", "12306")
            )
        ) or bool(
            train_numbers and any(token in compact for token in ("车底", "担当", "车型", "车组", "智能动车组", "长编组", "短编组", "重联", "单组", "16节", "8节", "编组")) and not has_knowledge_marker
        )
        return not clear_tool_lookup

    def _looks_like_line_station_affiliation_question(self, text: str) -> bool:
        compact = re.sub(r"\s+", "", str(text or ""))
        if not compact:
            return False

        line_tokens = ("高铁", "城际", "客专", "铁路", "线路", "干线", "联络线")
        station_tokens = ("火车站", "车站", "站点")
        ask_tokens = ("哪个", "哪座", "哪一座", "哪一站", "哪边")
        relation_tokens = ("是", "算不算", "属于", "算是", "挂靠", "对应")
        hard_lookup_tokens = ("最快", "余票", "有票", "有哪些车", "有什么车", "时刻", "经停", "停站", "车次")

        if any(token in compact for token in hard_lookup_tokens):
            return False

        return (
            any(token in compact for token in line_tokens)
            and any(token in compact for token in station_tokens)
            and any(token in compact for token in ask_tokens)
            and any(token in compact for token in relation_tokens)
        )

    def _legacy_looks_like_contextual_social_reply_v1(self, text: str, recent_entity_pool: dict | None = None) -> bool:
        compact = re.sub(r"\s+", "", str(text or ""))
        if not compact:
            return False

        recent_entity_pool = recent_entity_pool or {}
        has_recent_context = any(bool(recent_entity_pool.get(key)) for key in ("trains", "routes", "objects", "stations"))
        if not has_recent_context:
            return False

        direct_replies = {
            "原来如此",
            "懂了",
            "明白了",
            "有点东西",
            "有点厉害",
            "好家伙",
            "离谱",
            "绝了",
            "牛啊",
            "牛哇",
            "可以啊",
            "厉害",
            "真快",
            "真强",
        }
        if compact in direct_replies:
            return True

        social_tokens = (
            "厉害",
            "好强",
            "真强",
            "真快",
            "牛",
            "猛",
            "离谱",
            "夸张",
            "绝了",
            "原来如此",
            "懂了",
            "有点东西",
            "可以啊",
        )
        if len(compact) <= 14 and any(token in compact for token in social_tokens):
            return True

        return len(compact) <= 14 and any(
            compact.endswith(suffix) for suffix in ("吗", "吗？", "吗?", "呢", "呢？", "呢?", "啊", "啊？", "啊?")
        ) and any(token in compact for token in ("这么", "这也太", "原来", "居然", "竟然"))

    def _looks_like_contextual_evidence_followup(self, text: str, recent_entity_pool: dict | None = None) -> bool:
        compact = re.sub(r"\s+", "", str(text or ""))
        if not compact:
            return False

        recent_entity_pool = recent_entity_pool or {}
        has_recent_train_context = bool(recent_entity_pool.get("trains"))
        has_recent_route_context = bool(recent_entity_pool.get("routes") or recent_entity_pool.get("stations"))
        if not (has_recent_train_context or has_recent_route_context):
            return False

        challenge_tokens = (
            "你怎么知道",
            "怎么知道",
            "你怎么确定",
            "怎么确定",
            "凭什么",
            "你凭什么",
            "依据是什么",
            "根据什么",
            "为什么说",
            "为什么是",
            "怎么就知道",
            "哪里看出来",
            "怎么看出来",
        )
        if not any(token in compact for token in challenge_tokens):
            return False

        evidence_tokens = (
            "高铁",
            "线路",
            "路线",
            "这条线",
            "经停",
            "停站",
            "路径",
            "沿着",
            "京广高铁",
            "沪昆高铁",
            "京沪高铁",
            "徐兰高铁",
            "贵广高铁",
            "郑渝高铁",
            "车次",
            "这趟",
            "它们",
        )
        return any(token in compact for token in evidence_tokens) or has_recent_train_context

    def _legacy_looks_like_contextual_chat_turn_v1(self, text: str, recent_entity_pool: dict | None = None) -> bool:
        compact = re.sub(r"\s+", "", str(text or ""))
        if not compact:
            return False

        recent_entity_pool = recent_entity_pool or {}
        has_recent_context = any(bool(recent_entity_pool.get(key)) for key in ("trains", "routes", "objects", "stations"))
        if not has_recent_context:
            return False

        reference_tokens = (
            "这些",
            "这几趟",
            "这些车次",
            "这些列车",
            "这些车",
            "这几列",
            "这两班车",
            "这两趟车",
            "这两列车",
            "它们",
            "上面这些",
            "刚才这些",
            "这条线",
            "这批",
            "这个路线",
        )
        followup_tokens = (
            "特点",
            "有啥特点",
            "有什么特点",
            "区别",
            "不同",
            "共同点",
            "怎么理解",
            "怎么看",
            "怎么选",
            "如何选",
            "介绍一下",
            "展开说说",
            "解释一下",
            "什么意思",
            "是什么",
            "怎么样",
        )

        if any(token in compact for token in reference_tokens) and any(token in compact for token in followup_tokens):
            return True

        reasoning_reference_tokens = (
            "这两班车",
            "这两趟车",
            "这两列车",
            "它们",
        )
        reasoning_tokens = (
            "路权",
            "标杆车",
            "是因为",
            "是不是因为",
            "原因",
        )
        if any(token in compact for token in reasoning_reference_tokens) and any(token in compact for token in reasoning_tokens):
            return True

        if compact in {"它们呢", "这些车次呢", "这些车呢", "这条线呢", "这是什么"}:
            return True

        if self._looks_like_contextual_social_reply(compact, recent_entity_pool=recent_entity_pool):
            return True

        return False

    def _extract_emu_id(self, text: str) -> str | None:
        match = re.search(r"(CRH|CR|CJ)\d+[A-Z-]+\d+", text)
        if not match:
            return None
        return match.group(0).replace("-", "")

    def _strip_route_noise(self, text: str) -> str:
        cleaned = str(text or "")
        cleaned = re.sub(r"(?:我喜欢|喜欢|偏爱|偏好|想坐|想要|想体验)?[\u4e00-\u9fff]{2,4}局的?", "", cleaned)
        cleaned = re.sub(r"(?:CRH|CR|CJ)\d+[A-Z-]{2,}\d*", "", cleaned, flags=re.IGNORECASE)
        return cleaned

    def _extract_emu_preferences(self, text: str) -> list[str]:
        seen: list[str] = []
        for item in re.findall(r"(?:CRH|CR|CJ)\d+[A-Z-]{2,}(?!\d)", text):
            normalized = item.upper().replace("-", "")
            if self._extract_emu_id(normalized):
                continue
            if len(normalized) < 7 or normalized in seen:
                continue
            seen.append(normalized)

        compact = re.sub(r"\s+", "", str(text or "").upper())
        for alias, normalized in EMU_FAMILY_ALIAS_MAP.items():
            if re.search(rf"(?<![A-Z0-9]){re.escape(alias)}(?![A-Z0-9])", compact) and normalized not in seen:
                seen.append(normalized)
        return seen

    def _extract_bureau_preferences(self, text: str) -> list[str]:
        seen: list[str] = []
        for item in re.findall(r"[\u4e00-\u9fff]{2,4}局", str(text or "")):
            normalized = re.sub(r"^(?:我喜欢|喜欢|偏爱|偏好|想坐|想要|想体验)", "", item)
            normalized = normalized.strip()
            if normalized.endswith("局") and normalized not in seen:
                seen.append(normalized)
        return seen

    def _looks_like_contextual_route_followup(
        self,
        text: str,
        has_route: bool,
        anchor_query_object: str | None = None,
    ) -> bool:
        compact = re.sub(r"\s+", "", str(text or ""))
        if not compact or not has_route or not str(anchor_query_object or "").strip():
            return False

        if any(token in compact for token in ("余票", "中转", "停不停", "经停", "车底", "担当", "电报码", "车次号", "列车号")):
            return False

        if any(token in compact for token in ("那从", "那到", "那这条", "这条线", "这个区间", "这一段", "那这段")):
            return True

        if compact.startswith(("从", "那")) and compact.endswith(("呢", "呢？", "呢?", "吗", "吗？", "吗?")):
            return True

        return False

    def _legacy_looks_like_contextual_assignment_followup_v1(self, text: str, recent_entity_pool: dict | None = None) -> bool:
        compact = re.sub(r"\s+", "", str(text or ""))
        if not compact:
            return False

        recent_entity_pool = recent_entity_pool or {}
        if not recent_entity_pool.get("trains"):
            return False

        reference_tokens = (
            "这几班",
            "这几班车",
            "这几趟",
            "这几列",
            "这些车次",
            "这些列车",
            "这些车",
            "推荐的这几班",
            "推荐的这几趟",
            "推荐的这些",
            "你推荐的",
            "这趟",
            "这班",
            "这列",
            "它们",
        )
        assignment_tokens = (
            "车底",
            "担当",
            "车型",
            "智能动车",
            "智能动车组",
            "智能复兴号",
            "车组",
        )
        return any(token in compact for token in reference_tokens) and any(token in compact for token in assignment_tokens)

    def _pick_contextual_route_object(self, context: dict) -> str | None:
        if not context.get("asks_contextual_route_followup"):
            return None

        anchor_object = str(
            context.get("anchor_query_object")
            or context.get("anchor_query_type")
            or ""
        ).strip()
        if not anchor_object:
            return None

        if anchor_object in {"s2s_benchmark", "s2s_bureau_filter"}:
            return "s2s_benchmark"
        if anchor_object == "left_ticket_s2s":
            return "left_ticket_s2s"
        if anchor_object in {
            "station_to_station_mini",
            "station_to_station_detail",
            "station_to_station_future",
            "station_to_station_past",
            "s2s_regular_only",
            "s2s_temporary_only",
            "s2s_timeband_dep",
            "s2s_timeband_arr",
        }:
            return self._pick_s2s_object(context.get("query_date"))
        if anchor_object == "smartemu_analysis" and context.get("route"):
            return self._pick_s2s_object(context.get("query_date"))

        return None

    def _legacy_looks_like_contextual_followup_v1(self, text: str) -> bool:
        lowered = str(text or "").strip()
        if not lowered:
            return False

        followup_tokens = (
            "这趟",
            "这一趟",
            "这班",
            "这一班",
            "这列",
            "这一列",
            "这些",
            "它们",
            "该车",
            "该列车",
            "这几趟",
            "这几列",
            "这些车次",
            "这些列车",
            "该车",
            "这车",
            "它",
            "这个车次",
            "这趟列车",
            "上一趟",
            "上一个",
            "还",
            "然后",
            "那",
        )
        return any(token in lowered for token in followup_tokens)

    def _legacy_should_reuse_train_anchor_v1(self, text: str) -> bool:
        if self._extract_train_numbers(text.upper()):
            return False
        if self._looks_like_contextual_followup(text):
            return True
        return any(token in text for token in ("线路", "经停", "停站", "时刻", "这趟列车", "这班车", "这列车"))

    def _legacy_should_reuse_train_anchor_v2(self, text: str) -> bool:
        if self._extract_train_numbers(text.upper()):
            return False
        if self._looks_like_contextual_followup(text):
            return True
        if any(token in text for token in ("车底", "担当", "车型", "智能动车", "智能动车组", "智能复兴号", "车组", "长编组", "短编组", "重联", "单组", "16节", "8节", "编组")):
            return True
        return any(token in text for token in ("线路", "经停", "停站", "时刻", "这趟列车", "这班车", "这列车"))

    def _legacy_should_reuse_route_anchor_v1(self, text: str) -> bool:
        if self._extract_route_candidates(text):
            return False
        station_mentions = self._extract_station_mentions(text)
        if self._has_partial_route_query_intent(
            text=text,
            station_mentions=station_mentions,
            route_candidates=[],
            train_numbers=self._extract_train_numbers(str(text or "").upper()),
            emu_id=self._extract_emu_id(str(text or "").upper()),
            telecode=None,
        ):
            return False
        if self._looks_like_contextual_followup(text):
            return True
        return any(token in text for token in (
            "余票", "有票", "最快", "标杆", "还有吗", "这条线", "这条路线",
            "对比", "比较", "排行", "排名", "榜单", "是不是标杆", "是不是最快", "谁最快", "哪个最快",
        ))

    def _legacy_should_reuse_emu_anchor_v1(self, text: str) -> bool:
        if self._extract_emu_id(text.upper()):
            return False
        return any(token in text for token in ("这组", "这列", "它", "这趟")) or self._looks_like_contextual_followup(text)

    def _legacy_should_reuse_date_anchor_v1(self, text: str) -> bool:
        if self._extract_explicit_date(text) is not None:
            return False
        return self._looks_like_contextual_followup(text) or any(
            token in text for token in (
                "当天",
                "同一天",
                "这天",
                "那天",
                "当日",
                "当晚",
                "余票",
                "有票",
                "最快",
                "标杆",
                "有哪些车",
                "有什么车",
                "直达",
                "这条线",
                "这条路线",
            )
        )

    def _extract_explicit_date(self, text: str) -> datetime | None:
        now = datetime.now()
        return (
            self._extract_absolute_date(text=text, now=now)
            or self._extract_relative_day(text=text, now=now)
            or self._extract_weekday_date(text=text, now=now)
        )

    def _extract_date_legacy(self, text: str) -> str | None:
        now = datetime.now()

        match = re.search(r"(\d{4})[-/年](\d{1,2})[-/月](\d{1,2})日?", text)
        if match:
            year, month, day = map(int, match.groups())
            return f"{year:04d}-{month:02d}-{day:02d}"

        match = re.search(r"\b(\d{8})\b", text)
        if match:
            raw = match.group(1)
            return f"{raw[:4]}-{raw[4:6]}-{raw[6:]}"

        match = re.search(r"(\d{1,2})月(\d{1,2})日", text)
        if match:
            month, day = map(int, match.groups())
            return f"{now.year:04d}-{month:02d}-{day:02d}"

        if "今天" in text or "今日" in text:
            return now.strftime("%Y-%m-%d")
        if "明天" in text:
            return (now + timedelta(days=1)).strftime("%Y-%m-%d")
        if "后天" in text:
            return (now + timedelta(days=2)).strftime("%Y-%m-%d")
        if "昨天" in text:
            return (now - timedelta(days=1)).strftime("%Y-%m-%d")

        return now.strftime("%Y-%m-%d")

    def _extract_date(self, text: str) -> str | None:
        now = datetime.now()

        absolute_date = self._extract_absolute_date(text=text, now=now)
        if absolute_date:
            return absolute_date.strftime("%Y-%m-%d")

        relative_date = self._extract_relative_day(text=text, now=now)
        if relative_date:
            return relative_date.strftime("%Y-%m-%d")

        weekday_date = self._extract_weekday_date(text=text, now=now)
        if weekday_date:
            return weekday_date.strftime("%Y-%m-%d")

        return now.strftime("%Y-%m-%d")

    def _extract_absolute_date(self, text: str, now: datetime) -> datetime | None:
        match = re.search(r"(\d{4})[-/年.](\d{1,2})[-/月.](\d{1,2})(?:日|号)?", text)
        if match:
            return self._safe_make_date(*map(int, match.groups()))

        match = re.search(r"\b(\d{8})\b", text)
        if match:
            raw = match.group(1)
            return self._safe_make_date(int(raw[:4]), int(raw[4:6]), int(raw[6:]))

        match = re.search(r"(?<!\d)(\d{1,2})月(\d{1,2})(?:日|号)?", text)
        if match:
            month, day = map(int, match.groups())
            return self._safe_make_date(now.year, month, day)

        match = re.search(r"(?<!\d)(\d{1,2})[./](\d{1,2})(?:日|号)?(?!\d)", text)
        if match:
            month, day = map(int, match.groups())
            return self._safe_make_date(now.year, month, day)

        return None

    def _extract_relative_day(self, text: str, now: datetime) -> datetime | None:
        for token, offset in (
            ("今天", 0),
            ("今日", 0),
            ("明天", 1),
            ("后天", 2),
            ("昨天", -1),
        ):
            if token in text:
                return now + timedelta(days=offset)
        return None

    def _extract_weekday_date(self, text: str, now: datetime) -> datetime | None:
        weekday_map = {
            "一": 0,
            "二": 1,
            "三": 2,
            "四": 3,
            "五": 4,
            "六": 5,
            "日": 6,
            "天": 6,
        }
        week_start = now - timedelta(days=now.weekday())

        weekend_match = re.search(
            r"(这周末|本周末|这星期末|本星期末|这礼拜末|本礼拜末|下周末|下星期末|下礼拜末|周末)",
            text,
        )
        if weekend_match:
            token = weekend_match.group(1)
            week_offset = 1 if token.startswith("下") else 0
            return week_start + timedelta(days=5 + week_offset * 7)

        explicit_match = re.search(
            r"(这周|本周|下周|这星期|本星期|下星期|这礼拜|本礼拜|下礼拜)([一二三四五六日天])",
            text,
        )
        if explicit_match:
            prefix, day_token = explicit_match.groups()
            week_offset = 1 if prefix.startswith("下") else 0
            target_index = weekday_map[day_token]
            return week_start + timedelta(days=target_index + week_offset * 7)

        bare_match = re.search(r"(?:周|星期|礼拜)([一二三四五六日天])", text)
        if bare_match:
            target_index = weekday_map[bare_match.group(1)]
            delta = (target_index - now.weekday()) % 7
            return now + timedelta(days=delta)

        return None

    def _safe_make_date(self, year: int, month: int, day: int) -> datetime | None:
        try:
            return datetime(year, month, day)
        except ValueError:
            return None

    def _extract_route_candidates(self, text: str) -> list[str]:
        compact = re.sub(r"\s+", "", text)
        if not compact:
            return []

        candidates = []
        for candidate in (
            self._extract_route_by_pattern(compact),
            self._extract_route_from_station_mentions(compact),
        ):
            normalized = normalize_route_id(candidate)
            if normalized and normalized not in candidates:
                candidates.append(normalized)
        return candidates

    def _extract_route_by_pattern(self, compact: str) -> str | None:
        patterns = [
            r"浠?([\u4e00-\u9fffA-Za-z]{2,16}?)(?:寮€寰€|鍓嶅線|鍘诲線|鍒皘鍘粅鑷硘寰€)([\u4e00-\u9fffA-Za-z]{2,24})",
            r"([\u4e00-\u9fffA-Za-z]{2,16})(?:->|=>|鈫抾鉃鈬抾鉃渱鉃潀鉄秥鉄箌[-~锝炩€旓紞]+)([\u4e00-\u9fffA-Za-z]{2,24})",
        ]

        for pattern in patterns:
            match = re.search(pattern, compact)
            if not match:
                continue

            dep = self._clean_route_token(match.group(1), is_departure=True)
            arr = self._clean_route_token(match.group(2), is_departure=False)

            if dep and arr and dep != arr:
                return f"{dep}-{arr}"

        return None

    def _extract_route_from_station_mentions(self, compact: str) -> str | None:
        matches = self._match_station_mentions(compact)

        if len(matches) < 2:
            return None

        matches.sort(key=lambda item: (item[0], item[1]))
        ordered = []
        for _, _, name in matches:
            if not ordered or ordered[-1] != name:
                ordered.append(name)

        if len(ordered) < 2:
            return None

        first = next((item for item in matches if item[2] == ordered[0]), None)
        second = next((item for item in matches if item[2] == ordered[1] and (not first or item[0] >= first[1])), None)
        if first and second:
            connector = compact[first[1] : second[0]]
            trailing = compact[second[1] : second[1] + 8]
            if connector in {"或", "或者", "、"} and any(token in trailing for token in ("出发", "始发", "到达", "抵达")):
                return None

        return f"{ordered[0]}-{ordered[1]}"

    @staticmethod
    def _is_hub_qualified_station(name: str) -> bool:
        value = str(name or "").strip()
        return len(value) >= 2 and value[-1] in {"东", "西", "南", "北"}

    def _match_station_mentions(self, compact: str) -> list[tuple[int, int, str]]:
        matches = []
        occupied: list[tuple[int, int]] = []

        for name in self._get_station_name_cache():
            start = 0
            while True:
                index = compact.find(name, start)
                if index < 0:
                    break

                end = index + len(name)
                overlaps = any(not (end <= span_start or index >= span_end) for span_start, span_end in occupied)
                if not overlaps:
                    matches.append((index, end, name))
                    occupied.append((index, end))
                start = index + len(name)

        matches.sort(key=lambda item: (item[0], item[1]))
        return matches

    def _extract_station_mentions(self, text: str) -> list[str]:
        compact = re.sub(r"\s+", "", str(text or ""))
        if not compact:
            return []

        ordered = []
        for _, _, name in self._match_station_mentions(compact):
            if not ordered or ordered[-1] != name:
                ordered.append(name)
        return ordered

    def _extract_stopcheck_stations(self, text: str, station_mentions: list[str]) -> list[str]:
        if not station_mentions:
            return []

        compact = re.sub(r"\s+", "", str(text or ""))
        history_stop_tokens = ("只停", "加停", "增停", "开始加停", "什么时候开始加停", "停哪几站")
        if any(token in compact for token in history_stop_tokens):
            ordered_mentions = []
            for station in station_mentions:
                if station not in ordered_mentions:
                    ordered_mentions.append(station)
            if ordered_mentions:
                return ordered_mentions

        candidate_fragments = []
        patterns = [
            r"(?:停不停|停不停车|停靠|停吗|会不会停|是否停|是否停靠|有没有停|路过|通过)([\u4e00-\u9fffA-Za-z,，、]{2,24})",
            r"([\u4e00-\u9fffA-Za-z,，、]{2,24})(?:停不停|停不停车|停靠|停吗|会不会停|是否停|是否停靠|有没有停)",
            r"哪些停([\u4e00-\u9fffA-Za-z,，、]{2,24})",
        ]

        for pattern in patterns:
            for match in re.finditer(pattern, compact):
                fragment = str(match.group(1) or "").strip("，。、,？?吗呢呀")
                if fragment:
                    candidate_fragments.append(fragment)

        stopcheck_stations = []
        for fragment in candidate_fragments:
            for station in self._extract_station_mentions(fragment):
                if station not in stopcheck_stations:
                    stopcheck_stations.append(station)

        if stopcheck_stations:
            return stopcheck_stations

        if len(station_mentions) == 1:
            return list(station_mentions)

        return []

    def _has_assignment_intent(self, text: str) -> bool:
        compact = re.sub(r"\s+", "", str(text or ""))
        if not compact:
            return False
        tokens = (
            "车底",
            "担当",
            "车型",
            "车组",
            "动车组",
            "智能动车",
            "智能动车组",
            "智能复兴号",
            "使用情况",
            "长编组",
            "短编组",
            "重联",
            "单组",
            "16节",
            "8节",
            "编组",
        )
        return any(token in compact for token in tokens)

    def _has_train_overview_intent(self, text: str) -> bool:
        raw = str(text or "")
        compact = re.sub(r"\s+", "", raw)
        lowered = raw.lower()
        if not compact:
            return False

        zh_tokens = (
            "介绍一下",
            "详细介绍",
            "详细说说",
            "展开说说",
            "全面解析",
            "全面分析",
            "完整介绍",
            "全部信息",
            "所有信息",
            "全貌",
            "详情",
            "详细信息",
            "具体信息",
            "是什么车",
            "这车怎么样",
            "这趟车怎么样",
            "有什么区别",
            "有啥区别",
            "为什么更快",
            "为什么更慢",
            "哪个更快",
            "哪个才是",
            "前身",
            "最早是不是",
            "什么时候开始",
            "晚1分钟",
            "晚一分钟",
            "是不是唯一",
        )
        if any(token in compact for token in zh_tokens):
            return True

        en_tokens = (
            "everything about",
            "all about",
            "tell me about",
            "know everything about",
            "full details",
            "overview of",
            "details about",
            "introduce ",
        )
        return any(token in lowered for token in en_tokens)

    def _has_explicit_train_comparison_lookup_intent(self, text: str, train_numbers: list[str] | None = None) -> bool:
        compact = re.sub(r"\s+", "", str(text or ""))
        trains = list(train_numbers or [])
        if len(trains) < 2 or not compact:
            return False

        comparison_tokens = (
            "为什么更快",
            "为什么更慢",
            "哪个更快",
            "哪个更慢",
            "谁更快",
            "谁更慢",
            "停站有什么不同",
            "停站有什么区别",
            "经停有什么不同",
            "经停有什么区别",
            "路线有什么不同",
            "路径有什么不同",
            "有什么不同",
            "有什么区别",
            "区别在哪里",
            "差异在哪里",
            "谁停站更多",
            "谁停站更少",
            "谁经停更多",
            "谁经停更少",
            "为什么会快",
            "为什么会慢",
        )
        if any(token in compact for token in comparison_tokens):
            return True

        return any(token in compact for token in ("停站", "经停", "路线", "路径")) and any(
            token in compact for token in ("比较", "对比", "差异", "区别", "更快", "更慢")
        )

    def _has_smart_emu_intent(self, text: str) -> bool:
        compact = re.sub(r"\s+", "", str(text or ""))
        if any(token in compact for token in ("智能动车", "智能动车组", "智能复兴号")):
            return True

        return any(
            re.search(rf"(?<![A-Z0-9]){re.escape(alias)}(?![A-Z0-9])", compact.upper())
            for alias in ("AFZ", "BFZ")
        )

    def _should_prefer_smart_emu_analysis(self, text: str, train_numbers: list[str] | None = None) -> bool:
        compact = re.sub(r"\s+", "", str(text or ""))
        train_numbers = list(train_numbers or [])
        if not compact:
            return False

        if any(token in compact for token in ("具体编号", "完整编号", "精确编号", "车组编号", "车号", "编组号")):
            return False

        # "是什么关系/有什么区别" 这类题更像解释型问法，不应误打到多车次车组分析工具。
        if any(token in compact for token in ("什么关系", "有何关系", "有啥关系", "什么区别", "有何区别", "有啥区别")):
            return False

        concrete_single_train_tokens = (
            "今天",
            "今日",
            "这几天",
            "最近",
            "用什么车",
            "用什么车底",
            "什么车底",
            "长编组还是短编组",
            "是长编组还是短编组",
            "是不是智能动车组",
        )
        if len(train_numbers) <= 1 and (
            self._has_assignment_intent(compact)
            or any(token in compact for token in concrete_single_train_tokens)
        ):
            return False

        if len(train_numbers) >= 2 and self._has_smart_emu_intent(compact):
            return True

        analytical_tokens = (
            "分析",
            "使用情况",
            "使用规律",
            "使用概况",
            "使用分布",
            "使用倾向",
            "配属情况",
            "配属规律",
            "配属分布",
            "概率",
            "预测",
            "稳定性",
            "最近都是什么",
            "都是什么动车组",
            "套跑",
            "轮换",
            "同一个车底",
            "同车底",
            "都是什么车型",
            "都是什么车",
            "哪一组",
            "这一组",
            "这一批",
        )
        if len(train_numbers) >= 2 and any(token in compact for token in analytical_tokens):
            return True

        if len(train_numbers) >= 3 and "都是什么" in compact and self._has_assignment_intent(compact):
            return True

        return False

    def _legacy_looks_like_affirmative_reply_v1(self, text: str) -> bool:
        compact = re.sub(r"\s+", "", str(text or "")).strip().lower()
        if not compact:
            return False

        direct_yes = {
            "ok",
            "okay",
            "好的",
            "好",
            "行",
            "可以",
            "继续",
            "继续吧",
            "继续查",
            "查吧",
            "那就查吧",
            "那就继续",
            "麻烦继续",
            "现在就查",
            "可以查",
        }
        if compact in direct_yes:
            return True

        return len(compact) <= 12 and (
            compact.startswith(("那就", "那你", "那麻烦", "继续", "可以"))
            or compact.endswith(("吧", "呀"))
        )

    def _looks_like_contextual_social_reply(self, text: str, recent_entity_pool: dict | None = None) -> bool:
        compact = re.sub(r"\s+", "", str(text or ""))
        if not compact:
            return False

        recent_entity_pool = recent_entity_pool or {}
        has_recent_context = any(bool(recent_entity_pool.get(key)) for key in ("trains", "routes", "objects", "stations"))
        if not has_recent_context:
            return False

        direct_replies = {
            "原来如此",
            "懂了",
            "明白了",
            "有点东西",
            "有点厉害",
            "好家伙",
            "离谱",
            "绝了",
            "牛啊",
            "可以啊",
            "厉害",
            "真快",
            "真强",
        }
        if compact in direct_replies:
            return True

        laughter_compact = re.sub(r"[!！?？~～.。,\s]", "", compact).lower()
        if laughter_compact and re.fullmatch(r"(哈|呵|嘿|h|ha|233|6)+", laughter_compact):
            return True

        social_tokens = ("厉害", "好强", "真强", "真快", "牛", "猛", "离谱", "夸张", "绝了", "原来如此", "懂了", "有点东西", "可以啊", "笑死", "绷不住", "哈哈", "呵呵", "hhh")
        if len(compact) <= 14 and any(token in compact for token in social_tokens):
            return True

        return len(compact) <= 14 and any(
            compact.endswith(suffix) for suffix in ("吗", "吗？", "吗?", "呢", "呢？", "呢?", "啊", "啊？", "啊?")
        ) and any(token in compact for token in ("这么", "这也太", "原来", "居然", "竟然"))

    def _looks_like_contextual_chat_turn(self, text: str, recent_entity_pool: dict | None = None) -> bool:
        compact = re.sub(r"\s+", "", str(text or ""))
        if not compact:
            return False

        recent_entity_pool = recent_entity_pool or {}
        has_recent_context = any(bool(recent_entity_pool.get(key)) for key in ("trains", "routes", "objects", "stations"))
        if not has_recent_context:
            return False

        reference_tokens = (
            "这些",
            "这几趟",
            "这些车次",
            "这些列车",
            "这些车",
            "这几列",
            "这两班车",
            "这两趟车",
            "这两列车",
            "它们",
            "上面这些",
            "刚才这些",
            "这条线",
            "这批",
            "这个路线",
            "推荐的这些",
            "推荐的这几班",
            "推荐的这几趟",
            "你推荐的",
        )
        followup_tokens = (
            "特点",
            "有啥特点",
            "有什么特点",
            "区别",
            "不同",
            "共同点",
            "怎么理解",
            "怎么看",
            "怎么选",
            "如何选",
            "介绍一下",
            "展开说说",
            "解释一下",
            "什么意思",
            "是什么",
            "怎么样",
        )

        if any(token in compact for token in reference_tokens) and any(token in compact for token in followup_tokens):
            return True

        reasoning_reference_tokens = (
            "这两班车",
            "这两趟车",
            "这两列车",
            "它们",
        )
        reasoning_tokens = (
            "路权",
            "标杆车",
            "是因为",
            "是不是因为",
            "原因",
        )
        if any(token in compact for token in reasoning_reference_tokens) and any(token in compact for token in reasoning_tokens):
            return True

        if compact in {"它们呢", "这些车次呢", "这些车呢", "这条线呢", "这是什么"}:
            return True

        if self._looks_like_affirmative_reply(compact):
            return True

        return self._looks_like_contextual_social_reply(compact, recent_entity_pool=recent_entity_pool)

    def _looks_like_contextual_assignment_followup(self, text: str, recent_entity_pool: dict | None = None) -> bool:
        compact = re.sub(r"\s+", "", str(text or ""))
        if not compact:
            return False

        recent_entity_pool = recent_entity_pool or {}
        if not recent_entity_pool.get("trains"):
            return False

        reference_tokens = (
            "这几班",
            "这几班车",
            "这几趟",
            "这几列",
            "这些车次",
            "这些列车",
            "这些车",
            "推荐的这几班",
            "推荐的这几趟",
            "推荐的这些",
            "你推荐的",
            "这趟",
            "这班",
            "这列",
            "它们",
        )
        assignment_tokens = (
            "车底",
            "担当",
            "车型",
            "智能动车",
            "智能动车组",
            "智能复兴号",
            "车组",
            "使用情况",
        )
        return any(token in compact for token in reference_tokens) and any(token in compact for token in assignment_tokens)

    def _looks_like_contextual_followup(self, text: str) -> bool:
        compact = re.sub(r"\s+", "", str(text or ""))
        if not compact:
            return False
        if any(token in compact for token in ("这辆车", "这台车", "这部车", "这辆列车")):
            return True
        followup_tokens = (
            "这趟",
            "这班",
            "这列",
            "这些",
            "它们",
            "这几趟",
            "这几列",
            "这些车次",
            "这些列车",
            "该车",
            "这车",
            "这个车次",
            "这趟列车",
            "上一趟",
            "然后",
            "那",
            "这条线",
        )
        return any(token in compact for token in followup_tokens)

    def _should_reuse_train_anchor(self, text: str) -> bool:
        if self._extract_train_numbers(text.upper()):
            return False
        if self._looks_like_contextual_followup(text):
            return True
        if self._has_assignment_intent(text):
            return True
        return False

    def _should_reuse_route_anchor(self, text: str) -> bool:
        if self._extract_route_candidates(text):
            return False
        station_mentions = self._extract_station_mentions(text)
        if self._has_partial_route_query_intent(
            text=text,
            station_mentions=station_mentions,
            route_candidates=[],
            train_numbers=self._extract_train_numbers(str(text or "").upper()),
            emu_id=self._extract_emu_id(str(text or "").upper()),
            telecode=None,
        ):
            return False
        if self._looks_like_contextual_followup(text):
            return True
        return any(token in text for token in (
            "余票",
            "有票",
            "最快",
            "标杆",
            "还有吗",
            "这条线",
            "这条路线",
            "对比",
            "比较",
            "排行",
            "排名",
            "榜单",
            "是不是标杆",
            "是不是最快",
            "谁最快",
            "哪个最快",
        ))

    def _should_reuse_emu_anchor(self, text: str) -> bool:
        if self._extract_emu_id(text.upper()):
            return False
        return any(token in text for token in ("这组", "这列", "它", "这趟")) or self._looks_like_contextual_followup(text)

    def _should_reuse_date_anchor(self, text: str) -> bool:
        if self._extract_explicit_date(text) is not None:
            return False
        return self._looks_like_contextual_followup(text) or any(
            token in text for token in (
                "当天",
                "同一天",
                "这天",
                "那天",
                "当日",
                "当晚",
                "余票",
                "有票",
                "最快",
                "标杆",
                "有哪些车",
                "有什么车",
                "直达",
                "这条线",
                "这条路线",
            )
        )

    def _expert_contextual_lookup(self, context: dict) -> list[dict]:
        if context.get("asks_chat"):
            return []

        if (
            context.get("has_partial_route_query_intent")
            and context.get("station_mentions")
            and not context.get("route_completed_from_context")
        ):
            # A newly stated one-sided station is not permission to reuse an
            # old OD route. Let the partial-route expert or semantic council
            # resolve only the genuinely missing endpoint.
            return []

        has_context_reference = bool(
            context.get("asks_contextual_route_followup")
            or context.get("asks_contextual_assignment")
            or context.get("asks_contextual_evidence_followup")
            or self._looks_like_contextual_followup(context.get("text", ""))
        )
        if not has_context_reference:
            return []

        if context.get("explicit_route") or context.get("explicit_train_numbers") or context.get("explicit_emu_id") or context.get("telecode"):
            return []

        context_pool = context.get("context_entity_pool") or {}
        route = context.get("route") or next(iter(context_pool.get("routes") or []), None)
        trains = list(context.get("train_numbers") or context_pool.get("trains") or [])
        if not context.get("explicit_train_numbers") and context.get("asks_contextual_assignment") and context_pool.get("trains"):
            trains = list(context_pool.get("trains") or [])
        date = context.get("query_date")

        if context.get("asks_ticket") and route:
            tasks = [self._make_query("left_ticket_s2s", route, date=date)]
            if trains:
                tasks.append(self._make_query("train", trains[0]))
            return [self._proposal(
                "context_ticket_expert",
                98,
                self._dedupe_query_tasks(tasks),
                "context expert reused route and preferred train for ticket follow-up",
            )]

        if self._has_assignment_intent(context.get("text", "")) and trains:
            tasks = (
                [self._make_query("smartemu_analysis", ",".join(trains[:5]))]
                if self._has_smart_emu_intent(context.get("text", ""))
                else [self._make_query("train", train_no) for train_no in trains[:5]]
            )
            return [self._proposal(
                "context_assignment_expert",
                98,
                self._dedupe_query_tasks(tasks),
                "context expert reused prior trains for assignment follow-up",
            )]

        if context.get("asks_stopcheck") and trains and context.get("stopcheck_stations"):
            query_id = f"{','.join(trains[:20])}|{','.join((context.get('stopcheck_stations') or [])[:10])}"
            return [self._proposal(
                "context_stopcheck_expert",
                97,
                [self._make_query("path_stopcheck", query_id, date=date)],
                "context expert reused prior trains for stopcheck follow-up",
            )]

        if context.get("asks_contextual_evidence_followup") and trains:
            return [self._proposal(
                "context_path_evidence_expert",
                97,
                [self._make_query("path_detail", train_no, date=date) for train_no in trains[:3]],
                "context expert reused prior trains for evidence challenge follow-up",
            )]

        if (context.get("asks_train_terminal") or context.get("asks_path")) and trains:
            return [self._proposal(
                "context_path_expert",
                97,
                [self._make_query("path_detail", trains[0], date=date)],
                "context expert reused prior train for path follow-up",
            )]

        if route and (
            context.get("asks_benchmark")
            or context.get("asks_listing")
            or context.get("asks_travel_advice")
            or context.get("asks_recommendation")
            or context.get("asks_contextual_route_followup")
        ):
            obj = self._pick_contextual_route_object(context)
            if not obj:
                obj = "s2s_benchmark" if context.get("asks_benchmark") or context.get("asks_recommendation") else self._pick_s2s_object(date)
            return [self._proposal(
                "context_route_expert",
                97,
                [self._make_query(obj, route, date=date)],
                "context expert reused previous route for follow-up",
            )]

        return []

    def _expert_contextual_assignment(self, context: dict) -> list[dict]:
        assignment_intent = self._has_assignment_intent(context.get("text", ""))
        if not assignment_intent or context.get("train_numbers"):
            return []

        recent_trains = list((context.get("context_entity_pool") or context.get("recent_entity_pool") or {}).get("trains") or [])
        if not recent_trains:
            return []
        if not context.get("asks_contextual_assignment"):
            return []

        trains = recent_trains[:5]
        if self._should_prefer_smart_emu_analysis(context.get("text", ""), trains):
            tasks = [self._make_query("smartemu_analysis", ",".join(trains[:10]))]
            reason = "contextual smart emu follow-up reused recent trains"
            name = "contextual_smartemu_expert"
        else:
            tasks = [self._make_query("train", train_no) for train_no in trains]
            reason = "contextual assignment follow-up reused recent trains"
            name = "contextual_train_assignment_expert"

        return [self._proposal(name, 98, self._dedupe_query_tasks(tasks), reason)]

    def _expert_train_assignment(self, context: dict) -> list[dict]:
        assignment_intent = self._has_assignment_intent(context.get("text", ""))
        if not context.get("train_numbers") or not assignment_intent:
            return []

        effective_trains = list(context.get("train_numbers") or [])
        recent_trains = list((context.get("context_entity_pool") or context.get("recent_entity_pool") or {}).get("trains") or [])
        if not context.get("explicit_train_numbers") and context.get("asks_contextual_assignment") and recent_trains:
            effective_trains = recent_trains[:5]

        if self._should_prefer_smart_emu_analysis(context.get("text", ""), effective_trains):
            smartemu_trains = effective_trains[:10]
            return [self._proposal(
                "smartemu_assignment_expert",
                96,
                [self._make_query("smartemu_analysis", ",".join(smartemu_trains))],
                "explicit train emu usage analysis intent",
            )]

        return [self._proposal(
            "train_assignment_expert",
            95,
            [self._make_query("train", train_no) for train_no in effective_trains[:5]],
            "train assignment intent",
        )]

    def _expert_train_overview(self, context: dict) -> list[dict]:
        if not context.get("train_numbers") or not context.get("asks_train_overview"):
            return []

        trains = list(context.get("train_numbers") or [])[:3]
        date = context.get("query_date")
        tasks: list[dict] = []

        for train_no in trains[:2]:
            tasks.append(self._make_query("path_detail", train_no, date=date))
        for train_no in trains:
            tasks.append(self._make_query("train", train_no))

        return [self._proposal(
            "train_overview_expert",
            92,
            self._dedupe_query_tasks(tasks),
            "explicit train overview intent should stay on train-level tools",
        )]

    def _get_station_name_cache(self) -> list[str]:
        if self._station_name_cache is None:
            names = {
                str(info.get("name") or "")
                for info in station_dict.data.values()
                if isinstance(info, dict) and info.get("name")
            }
            self._station_name_cache = sorted(
                (name for name in names if len(name) >= 2),
                key=lambda item: (-len(item), item),
            )
        return self._station_name_cache

    def _extract_route(self, text: str) -> str | None:
        candidates = self._extract_route_candidates(text)
        if candidates:
            return candidates[0]

        compact = re.sub(r"\s+", "", text)
        patterns = [
            r"从?([\u4e00-\u9fffA-Za-z]{2,16}?)(?:开往|前往|去往|到|去|至|往)([\u4e00-\u9fffA-Za-z]{2,24})",
            r"([\u4e00-\u9fffA-Za-z]{2,16})(?:->|=>|→|➡|⇒|➜|➝|⟶|⟹|[-~～—－]+)([\u4e00-\u9fffA-Za-z]{2,24})",
        ]

        for pattern in patterns:
            match = re.search(pattern, compact)
            if not match:
                continue

            dep = self._clean_route_token(match.group(1), is_departure=True)
            arr = self._clean_route_token(match.group(2), is_departure=False)

            if dep and arr and dep != arr:
                normalized = normalize_route_id(f"{dep}-{arr}")
                if normalized:
                    return normalized
                return f"{dep}-{arr}"

        return None

    def _clean_route_token(self, token: str, is_departure: bool) -> str:
        return clean_station_token(token, is_departure=is_departure)

    def _extract_transfer_hub(self, text: str, route: str) -> str | None:
        match = re.search(r"经([\u4e00-\u9fffA-Za-z]{2,12})中转", text)
        if match:
            return match.group(1)

        match = re.search(r"([\u4e00-\u9fffA-Za-z]{2,12})中转", text)
        if match:
            hub = match.group(1)
            dep, arr = route.split("-", 1)
            if hub not in (dep, arr):
                return hub

        return None

    def _repair_fast_tasks(self, tasks: list[dict], strict_invalid: bool = False) -> list[dict]:
        if not isinstance(tasks, list):
            return []

        repaired: list[dict] = []
        repair_count = 0
        for task in tasks:
            if not isinstance(task, dict):
                continue

            if task.get("action") != "query":
                repaired.append(task)
                continue

            fixed = self._repair_query_task(task)
            if not fixed:
                if strict_invalid:
                    self._set_router_state(
                        AgentState.ROUTER_ROUTE_BLOCKED,
                        "router blocked invalid route-like query id after repair stage",
                    )
                    return [{
                        "action": "pending",
                        "params": normalize_pending_payload(
                            question="我需要再确认一下准确的出发站和到达站，当前识别到的站名还不够稳定。",
                            slot=["dep", "arr"],
                        ),
                    }]
                return []
            if fixed != task:
                repair_count += 1
            repaired.append(fixed)

        if repair_count:
            self._set_router_state(
                AgentState.ROUTER_ROUTE_REPAIRED,
                f"router repaired {repair_count} route-like query ids before execution",
            )
        return repaired

    def _repair_query_task(self, task: dict) -> dict | None:
        params = task.get("params", {})
        if not isinstance(params, dict):
            return None

        obj = str(params.get("object") or "").strip()
        route_objects = {
            "station_to_station_mini",
            "station_to_station_future",
            "station_to_station_past",
            "station_to_station_detail",
            "s2s_benchmark",
            "s2s_timeband_dep",
            "s2s_timeband_arr",
            "s2s_regular_only",
            "s2s_temporary_only",
            "s2s_bureau_filter",
            "left_ticket_s2s",
            "transfer_12306",
        }
        if obj not in route_objects:
            return task

        repaired_id = self._repair_route_like_id(obj, params.get("id"))
        if not repaired_id:
            return None

        cloned = {"action": task.get("action"), "params": dict(params)}
        cloned["params"]["id"] = repaired_id
        return cloned

    def _repair_route_like_id(self, obj: str, raw_id: str | None) -> str | None:
        text = str(raw_id or "").strip()
        if not text:
            return None

        if obj == "transfer_12306":
            return normalize_route_with_optional_via(text)
        if obj == "s2s_bureau_filter":
            route_text, sep, bureau_text = text.partition("|")
            normalized_route = normalize_route_id(route_text)
            bureau = bureau_text.strip()
            if not normalized_route:
                return None
            if not sep:
                return normalized_route
            if not bureau:
                return None
            return f"{normalized_route}|{bureau}"
        return normalize_route_id(text)

    def recover_fast_tasks(self, user_text: str, facts: dict, prior_tasks: list[dict]) -> list[dict]:
        if not self.is_fast_mode() or not isinstance(prior_tasks, list):
            return []

        failed_dynamic_queries = []
        for item in facts.get("queries", []):
            if not isinstance(item, dict):
                continue
            if item.get("type") not in {"query_empty", "query_error"}:
                continue

            key = str(item.get("key") or "")
            if any(
                f":{obj}:" in key
                for obj in (
                    "station_to_station_mini",
                    "station_to_station_future",
                    "station_to_station_past",
                    "station_to_station_detail",
                    "s2s_benchmark",
                    "s2s_timeband_dep",
                    "s2s_timeband_arr",
                    "s2s_regular_only",
                    "s2s_temporary_only",
                    "s2s_bureau_filter",
                    "left_ticket_s2s",
                    "transfer_12306",
                )
            ):
                failed_dynamic_queries.append(item)

        if not failed_dynamic_queries:
            return []

        repaired = self._repair_fast_tasks(prior_tasks, strict_invalid=True)
        if repaired and repaired != prior_tasks:
            return repaired

        compact_safe = self._route_with_fast_llm(user_text, None)
        if compact_safe:
            return self._repair_fast_tasks(compact_safe, strict_invalid=True)

        return []

    # ========================================================
    # Safe Parse (Extreme Robust)
    # ========================================================

    def _safe_parse_tasks(self, raw: str, context: dict | None = None) -> list[dict]:
        try:
            data = loads_llm_json(raw)
        except Exception:
            return [{"action": "chat", "params": {"message": "Router JSON解析失败"}}]

        # ==========================================================
        # ✅顶层格式兼容
        # ==========================================================
        if isinstance(data, list):
            tasks = data
        elif isinstance(data, dict) and "tasks" in data:
            tasks = data["tasks"]
        elif isinstance(data, dict) and "action" in data:
            tasks = [data]
        else:
            return [{"action": "chat", "params": {"message": "Router输出格式错误"}}]

        if not isinstance(tasks, list):
            return [{"action": "chat", "params": {"message": "Router任务不是列表"}}]

        expanded_tasks: list[dict] = []
        for task in tasks:
            if not isinstance(task, dict) or task.get("action") != "query":
                expanded_tasks.append(task)
                continue
            params = task.get("params") if isinstance(task.get("params"), dict) else {}
            capability = get_capability(params.get("object"))
            if capability is None or capability.kind != "workflow":
                expanded_tasks.append(task)
                continue
            if not isinstance(context, dict):
                continue
            expanded_tasks.extend(
                self._build_capability_tasks(
                    capability.object,
                    str(params.get("id") or "").strip(),
                    str(params.get("date") or context.get("query_date") or "").strip(),
                    context,
                )
            )
        tasks = expanded_tasks

        safe_tasks = []

        for task in tasks:
            if not isinstance(task, dict):
                continue

            action = task.get("action")
            params = task.get("params", {})

            # ======================================================
            # ✅1) CHAT：普通闲聊
            # ======================================================
            if action == "chat":
                msg = params.get("message", "")
                if isinstance(msg, str) and msg.strip():
                    chat_params = {"message": msg.strip()}
                    direct_reply = params.get("direct_reply", "")
                    if isinstance(direct_reply, str) and direct_reply.strip():
                        chat_params["direct_reply"] = direct_reply.strip()
                    safe_tasks.append({
                        "action": "chat",
                        "params": chat_params
                    })
                continue

            # ======================================================
            # ✅2) PENDING：追问态（必须保留）
            # ======================================================
            if action == "pending":
                pending_payload = normalize_pending_payload(
                    question=params.get("question", ""),
                    slot=params.get("slot", []),
                    context=params.get("context", {}),
                    fallback="我还需要你补充一个关键信息才能继续查询😊",
                )

                safe_tasks.append({
                    "action": "pending",
                    "params": pending_payload,
                })
                continue

            # ======================================================
            # action 校验（query/compare/analyze/summarize）
            # ======================================================
            if not is_valid_action(action):
                continue

            if not validate_action_params(action, params):
                continue

            # ======================================================
            # ✅3) QUERY normalize
            # ======================================================
            if action == "query":

                obj = params.get("object")
                obj_id = str(params.get("id", "")).strip()

                # -------------------
                # Train normalize
                # -------------------
                if obj == "train":
                    obj_id = obj_id.replace("次", "").upper()
                    params["id"] = obj_id

                # -------------------
                # EMU normalize
                # -------------------
                elif obj == "emu":
                    obj_id = obj_id.replace("-", "").replace(" ", "").upper()

                    if len(obj_id) < 10:
                        safe_tasks.append({
                            "action": "pending",
                            "params": normalize_pending_payload(
                                question="请提供完整动车组编号，例如 CR400AFZ2333",
                                slot=["emu_id"],
                            ),
                        })
                        continue

                    params["id"] = obj_id

                # -------------------
                # Left Ticket normalize
                # -------------------
                elif obj == "left_ticket_s2s":
                    if "-" not in obj_id:
                        safe_tasks.append({
                            "action": "pending",
                            "params": normalize_pending_payload(
                                question="余票查询需要区间信息～请告诉我出发站和到达站😊",
                                slot=["dep", "arr"],
                            ),
                        })
                        continue

                    params["id"] = obj_id

                # -------------------
                # Transfer normalize
                # -------------------
                elif obj == "transfer_12306":
                    params["id"] = obj_id

                params = self._apply_default_date_to_query(params)

                # -------------------
                # Final semantic check
                # -------------------
                if not validate_query_semantics(params):
                    continue

            safe_tasks.append({
                "action": action,
                "params": params
            })

        # ======================================================
        # ✅最终兜底：必须至少返回一个 pending/chat
        # ======================================================
        if not safe_tasks:
            return [{
                "action": "pending",
                "params": normalize_pending_payload(
                    question="我还需要你补充一下信息才能继续😊",
                ),
            }]

        return safe_tasks

    # ========================================================
    # Query Normalizer (v2.6.6 Ultimate)
    # ========================================================
    def _normalize_transfer_id(self, x: str) -> str:
        """
        transfer_12306 支持：
        南京南-福州|上饶
        南京南-福州｜上饶
        """
        if not x:
            return ""

        x = x.strip()

        # 半角 | → 全角｜
        x = x.replace("|", "｜")

        # 多余空格去掉
        x = x.replace(" ", "")

        return x

    def _normalize_query(self, params: dict) -> tuple[bool, dict]:

        obj = params.get("object")
        qid = params.get("id")

        if not isinstance(qid, str):
            return False, params

        qid = qid.strip()

        today = datetime.now().strftime("%Y-%m-%d")

        # ====================================================
        # 0) Block hallucination garbage
        # ====================================================
        if len(qid) <= 1:
            return False, params

        if qid.upper() in ("AFBS", "UNKNOWN", "ANY"):
            return False, params

        # ====================================================
        # 1) TRAIN Evidence Tool
        # ====================================================
        if obj == "train":

            qid = qid.upper()
            qid = re.sub(r"[次（）()\s].*", "", qid)

            if not re.match(r"^[GDKTZC]\d+$", qid):
                return False, params

        # ====================================================
        # 2) EMU Evidence Tool
        # - Allow CRH380AL-2541 → CRH380AL2541
        # - Reject incomplete AFBS style
        # ====================================================
        elif obj == "emu":

            qid = qid.upper().replace("-", "")

            # Strict EMU ID pattern
            if not re.match(r"^(CRH|CR|CJ)\d+[A-Z]*\d+$", qid):
                return False, params

        # ====================================================
        # 3) Path Evidence Tools
        # ====================================================
        elif obj in ("path_detail", "path_future", "path_past"):

            qid = qid.upper()
            qid = re.sub(r"[次（）()\s].*", "", qid)

            if not re.match(r"^[GDKTZC]\d+$", qid):
                return False, params

        # ====================================================
        # 4) OD Listing Tools
        # ====================================================
        elif obj.startswith("station_to_station"):

            if "-" not in qid:
                return False, params

            # Auto date fill
            if "date" not in params:
                params["date"] = today

            d = params["date"]

            # Strict Future/Past Firewall
            if obj == "station_to_station_future":
                if d <= today:
                    return False, params

            if obj == "station_to_station_past":
                if d >= today:
                    return False, params

        # ====================================================
        # 5) Left Ticket (Real-time 12306)
        # ====================================================
        elif obj == "left_ticket_s2s":

            if "-" not in qid:
                return False, params

            if "date" not in params:
                params["date"] = today

        # ====================================================
        # 6) Transfer Tool (Compound ID Supported)
        # Format:
        #   南京南-福州｜上饶｜20260217
        # ====================================================
        elif obj == "transfer_12306":
            params["id"] = self._normalize_transfer_id(params["id"])
            parts = [x.strip() for x in qid.split("｜")]

            if len(parts) not in (2, 3):
                return False, params

            route = parts[0]
            if "-" not in route:
                return False, params

        # ====================================================
        # 7) Benchmark / Timeband / Filter Tools
        # (OD required)
        # ====================================================
        elif obj in (
            "s2s_benchmark",
            "s2s_timeband_dep",
            "s2s_timeband_arr",
            "s2s_regular_only",
            "s2s_temporary_only",
            "s2s_bureau_filter",
        ):
            if "-" not in qid:
                return False, params

            if "date" not in params:
                params["date"] = today

        # ====================================================
        # 8) Smart EMU Analysis Tool
        # ====================================================
        elif obj == "smartemu_analysis":
            if "-" not in qid:
                return False, params

        # ====================================================
        # 9) Local Station Tool
        # ====================================================
        elif obj == "telecode":
            if len(qid) < 2:
                return False, params

        # ====================================================
        # 10) Station Metadata Tool (Strict)
        # ====================================================
        elif obj == "station":
            if len(qid) < 2:
                return False, params

        # ====================================================
        # Final Commit
        # ====================================================
        params["id"] = qid
        return True, params

    def _apply_default_date_to_query(self, params: dict) -> dict:
        if not isinstance(params, dict):
            return params

        obj = str(params.get("object") or "").strip()
        if not obj:
            return params

        cloned = dict(params)
        if cloned.get("date"):
            return cloned

        today = datetime.now().strftime("%Y-%m-%d")
        dateful_objects = {
            "left_ticket_s2s",
            "transfer_12306",
            "s2s_benchmark",
            "s2s_timeband_dep",
            "s2s_timeband_arr",
            "s2s_regular_only",
            "s2s_temporary_only",
            "s2s_bureau_filter",
            "path_detail",
            "path_future",
            "path_past",
            "path_stopcheck",
            "station_to_station_mini",
            "station_to_station_detail",
            "station_to_station_future",
            "station_to_station_past",
            "train_station_access",
        }
        if obj not in dateful_objects:
            return cloned

        if obj in {"station_to_station_future", "station_to_station_past"}:
            cloned["object"] = self._pick_s2s_object(today)
        elif obj in {"path_future", "path_past"}:
            cloned["object"] = "path_detail"

        cloned["date"] = today
        return cloned

    def _looks_like_explicit_chat_turn(
        self,
        text: str,
        session: SessionMemory | None = None,
        context_agent_result: dict | None = None,
    ) -> bool:
        context = self._build_fast_route_context(
            text,
            session=session,
            context_agent_result=context_agent_result,
        )
        return bool(context.get("asks_chat"))

    def _infer_pending_slots_from_context(self, context: dict) -> list[str]:
        if not isinstance(context, dict):
            return []

        station_mentions = list(context.get("station_mentions") or [])
        text = str(context.get("text") or "")
        dep = str(context.get("dep") or "").strip()
        arr = str(context.get("arr") or "").strip()

        if not context.get("route") and station_mentions:
            if any(token in text for token in ("从", "出发", "坐")):
                return ["arr"]
            if any(token in text for token in ("到", "去", "前往", "抵达")):
                return ["dep"]

        if dep and not arr:
            return ["arr"]

        if arr and not dep:
            return ["dep"]

        semantic = context.get("semantic_consensus")
        selected_capability = ""
        if isinstance(semantic, dict) and semantic.get("preferred_action") in {"query", "pending"}:
            candidate = str(semantic.get("required_object") or "").strip()
            if get_capability(candidate):
                selected_capability = candidate
        if selected_capability:
            envelope = build_intent_envelope(selected_capability, context)
            slot_aliases = {
                "trains": "train",
                "stations": "station_name",
                "station": "station_name",
            }
            missing = [slot_aliases.get(slot, slot) for slot in envelope.missing_slots]
            if missing:
                return normalize_pending_slots(missing)

        if context.get("asks_transfer") and context.get("route"):
            return ["hub"]

        if context.get("asks_stopcheck") and context.get("train_numbers") and not context.get("stopcheck_stations"):
            return ["station_name"]

        if context.get("asks_stopcheck") and context.get("stopcheck_stations") and not context.get("train_numbers"):
            return ["train_no"]

        if context.get("asks_train_terminal") and not context.get("train_numbers"):
            return ["train_no"]

        if not context.get("route") and (
            context.get("asks_ticket")
            or context.get("asks_transfer")
            or context.get("asks_benchmark")
            or context.get("asks_listing")
        ):
            return ["dep", "arr"]

        if context.get("asks_path") and not context.get("train_numbers"):
            return ["train_no"]

        if context.get("asks_assignment") and not context.get("train_numbers") and not context.get("emu_id"):
            return ["train_no"]

        if context.get("asks_telecode") and not context.get("telecode"):
            return ["station_name"]

        return []

    def _enrich_pending_tasks(self, tasks: list[dict], context: dict) -> list[dict]:
        if not isinstance(tasks, list) or not tasks:
            return tasks

        enriched = []
        for task in tasks:
            if not isinstance(task, dict) or task.get("action") != "pending":
                enriched.append(task)
                continue

            params = dict(task.get("params", {})) if isinstance(task.get("params"), dict) else {}
            slots = normalize_pending_slots(params.get("slot", [])) or self._infer_pending_slots_from_context(context)
            merged_context = dict(params.get("context", {})) if isinstance(params.get("context"), dict) else {}
            for source_key, target_key in (
                ("route", "route"),
                ("dep", "dep"),
                ("arr", "arr"),
                ("query_date", "date"),
                ("telecode", "telecode"),
                ("direction", "direction"),
            ):
                value = context.get(source_key)
                if value and target_key not in merged_context:
                    merged_context[target_key] = value

            if context.get("train_numbers") and "train_no" not in merged_context:
                merged_context["train_no"] = ",".join(context.get("train_numbers")[:5])

            if context.get("station_mentions") and "station" not in merged_context:
                merged_context["station"] = ",".join(context.get("station_mentions")[:5])

            semantic = context.get("semantic_consensus") if isinstance(context.get("semantic_consensus"), dict) else {}
            selected_capability = str(
                self.last_intent_envelope.selected_capability
                or semantic.get("required_object")
                or merged_context.get("query_object")
                or ""
            ).strip()
            if get_capability(selected_capability):
                canonical_aliases = {
                    "train_no": "train",
                    "station_name": "station",
                    "emu_id": "emu",
                }
                canonical_missing = [canonical_aliases.get(slot, slot) for slot in slots]
                known_slots = dict(self.last_intent_envelope.grounded_slots or {})
                known_slots.update(
                    {
                        key: value
                        for key, value in merged_context.items()
                        if value not in (None, "", [], {})
                    }
                )
                merged_context["query_object"] = selected_capability
                merged_context["missing_slot_contract"] = build_missing_slot_contract(
                    selected_capability,
                    canonical_missing,
                    known_slots,
                )

            enriched.append({
                "action": "pending",
                "params": normalize_pending_payload(
                    question=params.get("question", ""),
                    slot=slots,
                    context=merged_context,
                    fallback=compose_pending_question(slots, merged_context),
                ),
            })

        return enriched

    def _looks_like_affirmative_reply(self, text: str) -> bool:
        compact = re.sub(r"[\s!！?？。.,，~～]+", "", str(text or "")).strip().lower()
        if not compact:
            return False

        direct_yes = {
            "ok",
            "okay",
            "yes",
            "好的",
            "好",
            "行",
            "可以",
            "是的",
            "对",
            "对的",
            "嗯",
            "嗯嗯",
            "继续",
            "继续查",
            "查吧",
            "那就查吧",
            "那就继续",
            "麻烦继续",
            "现在就查",
            "可以查",
        }
        if compact in direct_yes:
            return True

        return len(compact) <= 12 and (
            compact.startswith(("那就", "那你", "那麻烦", "继续", "可以", "是的", "对", "嗯"))
            or compact.endswith(("吧", "呀", "啊"))
        )

    def _looks_like_directional_speed_easter_egg(self, text: str) -> bool:
        compact = re.sub(r"[\s!！?？。.,，~～]+", "", str(text or ""))
        return compact == "东西方向的列车更快还是南北方向的更快"


# ============================================================
# Local Test Runner (Inside File)
# ============================================================

if __name__ == "__main__":
    memory = SessionMemory()
    router = Router(memory)


    tests = [
        # ============================================================
        # 🚀终极Boss闭环测试（毕业题）
        # ============================================================

        """
        G87、G89、G101最近都是什么车底？
        哪些停南京南、杭州东？
        今天南京到福州还有票吗？
        推荐这条线标杆车。
        如果直达不行给我上饶中转方案。
        顺便告诉我南京南电报码。
        """
        # ============================================================
        # 城市 OD 补全地狱
        # ============================================================

        "今天南京到福州还有车吗？不要普速，只想看高铁站的。",
        "南京-福州今天还有没有票？",
        "南京到福州的标杆车是哪趟？",

        # ============================================================
        # 电报码 OD 输入（必须支持）
        # ============================================================

        "NKH-FZS今天有票吗？",
        "NKH-福州 今天还有车吗？",
        "南京南-FZS 今天还有没有余票？",

        # ============================================================
        # 多车次 + 停站矩阵组合输入
        # ============================================================

        "G87、G89、G101 哪些停南京南？哪些停杭州东？",
        "G87,G89,G101 哪些停南京南、杭州东、福州？",
        "这些车哪些停南京南：G87 G89 G101",

        # ============================================================
        # 多车次车底证据链 train × N
        # ============================================================

        "G87、G89、G101 最近都是什么车底？",
        "G87次最近用什么车底？",
        "G87和G89车底一样吗？",

        # ============================================================
        # 非法车次必须拒绝（train限制）
        # ============================================================

        "K155最近用什么车底？",
        "Z99最近是什么车底？",

        # ============================================================
        # EMU 编号严格性测试
        # ============================================================

        "CR400AFZ2333最近跑什么交路？",
        "CR400AFZ这组车怎么样？",  # ❌必须chat
        "AFBS这组车最近跑什么？",  # ❌必须chat
        "CRH380AL2541最近交路是什么？",

        # ============================================================
        # 经停表 path_detail / future / past
        # ============================================================

        "G87完整经停表给我看看。",
        "G87未来经停会变吗？",  # ❌必须追问日期
        "2026-02-20 G87经停有哪些？",
        "2月1日G87停哪些站？",

        # ============================================================
        # Future / Past OD 日期严格测试
        # ============================================================

        "下周南京南到福州有什么车？",  # ❌future必须追问日期
        "2026-02-20 南京南到福州有什么车？",
        "昨天南京南到福州开了哪些车？",

        # ============================================================
        # Timeband 分桶工具
        # ============================================================

        "南京南到福州上午出发有什么车？",
        "南京南到福州下午到达的车有哪些？",

        # ============================================================
        # Pattern Filters 图定 vs 临客
        # ============================================================

        "南京南到福州每天都有的车有哪些？",
        "南京南到福州春运加开车有哪些？",

        # ============================================================
        # Bureau Filter 组合输入 OD|路局
        # ============================================================

        "南京南到福州南局担当的车有哪些？",
        "北京南到上海虹桥上局担当的车有哪些？",

        # ============================================================
        # Benchmark 工具边界
        # ============================================================

        "南京南到福州这条线最强标杆车是谁？",
        "这条线最强标杆车是哪趟？",  # ❌缺OD必须chat

        # ============================================================
        # Transfer 中转合法性测试
        # ============================================================

        "南京到福州给我一个上饶中转方案。",
        "南京到福州给我一个上饶市中转方案。",  # ❌中转地必须车站
        "南京到福州怎么走最快？",  # ❌不能乱用transfer

        # ============================================================
        # 余票刷票倾向（必须拒绝）
        # ============================================================

        "帮我每分钟查一次南京南到福州余票。",
        "一直查南京到福州有没有票，查到有为止。",

        # ============================================================
        # Telecode 专用
        # ============================================================

        "南京南的电报码是什么？",
        "深圳北的电报码是什么？",

        # ============================================================
        # 电报码反查（禁止幻想）
        # ============================================================

        "NKH是哪个站？",  # ❌必须chat

        # ============================================================
        # Station Metadata 严格限制
        # ============================================================

        "深圳北属于哪个路局？是不是特等站？",

        # ============================================================
        # Smart EMU Hint 多车次支持
        # ============================================================

        "G87、G89可能是什么车型？",


    ]

    for t in tests:
        print("\n\n=======================================")
        print("User:", t)
        print("=======================================")

        tasks = router.route(t,memory)
