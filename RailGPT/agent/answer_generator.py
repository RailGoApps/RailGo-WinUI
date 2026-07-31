# agent/answer_generator.py

import json
import re
from datetime import datetime
from zoneinfo import ZoneInfo
import sys
import time
from typing import Any, Dict, Iterable, List, Optional

from agent.capabilities import IntentEnvelope, capability_catalog, get_capability
from agent.fast_mode import FastFactCompressor
from knowledge.railway_knowledge import RailwayKnowledgeRAG
from llm.llm_client import LLMClient
from llm.json_utils import loads_llm_json
from memory.session import SessionMemory


def stream_print(text: str, delay: float = 0.015):
    """
    ✅终端逐字流式输出（适配 PyCharm 控制台）

    delay:
        0.01~0.02 最像 ChatGPT
        0.0     直接一次性输出
    """
    for ch in text:
        sys.stdout.write(ch)
        sys.stdout.flush()
        time.sleep(delay)

    sys.stdout.write("\n")
    sys.stdout.flush()

class AnswerGenerator:
    """
    AnswerGenerator v2 (Analysis-oriented, No Truncation, No Disclaimer)

    Responsibilities:
    - Convert structured facts into natural language answers
    - Be human-friendly and railway-enthusiast oriented
    - NEVER claim facts are truncated or insufficient unless truly empty
    - Do NOT include disclaimers (handled by frontend)
    """

    @staticmethod
    def _validated_extra_request(extra_request: Any) -> Dict[str, Any]:
        """Keep late tool requests inside the active capability contract."""
        if not isinstance(extra_request, dict):
            return {}

        missing = []
        for item in extra_request.get("missing") or []:
            if not isinstance(item, dict):
                continue
            capability = get_capability(item.get("object"))
            if (
                item.get("domain") == "railway"
                and capability is not None
                and capability.is_executable
                and str(item.get("id") or "").strip()
            ):
                missing.append(dict(item))
        return {"missing": missing} if missing else {}

    def __init__(
        self,
        llm_client: LLMClient,
        knowledge_rag: Optional[RailwayKnowledgeRAG] = None,
        final_llm: Optional[LLMClient] = None,
    ):
        self.llm = llm_client
        self.final_llm = final_llm or llm_client
        self.fast_compressor = FastFactCompressor()
        self.knowledge_rag = knowledge_rag or RailwayKnowledgeRAG()
        self.psw = None
        self.mode_profile = "deep"
        self.set_mode_profile(getattr(self.llm, "get_mode", lambda: "deep")())

    def _normalize_mode_profile(self, mode: str) -> str:
        normalized = str(mode or "").strip().lower()
        if normalized == "fast":
            return "fast-go"
        if normalized in {"fast-go", "fast-plus", "deep"}:
            return normalized
        return "deep"

    def set_mode_profile(self, mode: str):
        self.mode_profile = self._normalize_mode_profile(mode)
        pipeline_mode = self.mode_profile
        final_mode = self.mode_profile

        if self.final_llm is self.llm and pipeline_mode != final_mode and isinstance(self.llm, LLMClient):
            self.final_llm = LLMClient(mode=final_mode)

        if hasattr(self.llm, "set_mode"):
            self.llm.set_mode(pipeline_mode)

        if hasattr(self.final_llm, "set_mode"):
            self.final_llm.set_mode(final_mode)

    def get_mode_profile(self) -> str:
        return self.mode_profile

    def get_final_mode(self) -> str:
        return self.mode_profile

    def is_fast_mode(self) -> bool:
        return self.mode_profile in {"fast-go", "fast-plus"}

    def _is_fast_compact_mode(self, context_bundle: Optional[Dict[str, Any]]) -> bool:
        return self.is_fast_mode() and context_bundle is not None

    def _iter_query_records(self, facts: Dict[str, Any]) -> List[Dict[str, Any]]:
        if not isinstance(facts, dict):
            return []
        return [item for item in facts.get("queries", []) if isinstance(item, dict)]

    def _is_placeholder_query(self, item: Dict[str, Any]) -> bool:
        qtype = str(item.get("type") or "").strip().lower()
        return qtype in {"query_empty", "query_error"}

    def _has_positive_query_evidence(self, facts: Dict[str, Any]) -> bool:
        for item in self._iter_query_records(facts):
            if not self._is_placeholder_query(item):
                return True
        return False

    def should_skip_fast_context_bundle(self, facts: Dict[str, Any]) -> bool:
        if not self.is_fast_mode():
            return False

        queries = self._iter_query_records(facts)
        if not queries:
            return False

        if facts.get("analysis") or facts.get("comparisons"):
            return False

        return not self._has_positive_query_evidence(facts)

    def _empty_query_scope_label(self, obj: str) -> str:
        obj = str(obj or "").strip()
        if not obj:
            return "匹配结果"

        if obj in {"left_ticket_s2s", "left_ticket_12306"}:
            return "余票结果"
        if obj == "transfer_12306":
            return "中转方案"
        if obj in {"path_detail", "path_future", "path_past", "path_stopcheck"}:
            return "运行路径或经停信息"
        if obj == "train":
            return "车底或担当结果"
        if obj == "emu":
            return "动车组运用结果"
        if obj in {"telecode", "name", "station"}:
            return "站点匹配结果"
        if obj in {"train_delay", "train_station_access", "station_board"}:
            return "实时运行状态"
        if obj == "coach_layout":
            return "列车编组与定员信息"
        if obj == "train_route_map":
            return "线路地图坐标"
        if obj.startswith("station_to_station") or obj.startswith("s2s_"):
            return "车次或标杆车结果"
        return "匹配结果"

    def _collect_empty_queries(self, facts: Dict[str, Any]) -> List[Dict[str, Any]]:
        return [
            item
            for item in self._iter_query_records(facts)
            if str(item.get("type") or "").strip().lower() == "query_empty"
        ]

    def should_force_finalize_on_empty_queries(
        self,
        facts: Dict[str, Any],
        extra_request: Optional[Dict[str, Any]] = None,
    ) -> bool:
        if not self.is_fast_mode():
            return False

        if facts.get("analysis") or facts.get("comparisons"):
            return False

        if self._has_positive_query_evidence(facts):
            return False

        empty_queries = self._collect_empty_queries(facts)
        if not empty_queries:
            return False

        missing = (extra_request or {}).get("missing") if isinstance(extra_request, dict) else None
        if not missing:
            return True

        # If we already have only empty placeholders and the model still asks for more
        # tool calls, that follow-up is almost certainly a loop in fast mode.
        return True

    def _build_empty_query_answer(self, user_text: str, facts: Dict[str, Any]) -> str:
        if not self.should_force_finalize_on_empty_queries(facts):
            return ""

        empty_queries = self._collect_empty_queries(facts)
        if not empty_queries:
            return ""

        first = empty_queries[0]
        obj = str(first.get("object") or "").strip()
        scope_label = self._empty_query_scope_label(obj)
        query_id = str(first.get("id") or "").strip()
        query_date = str(first.get("date") or "").strip()

        anchors = []
        if query_id:
            anchors.append(query_id)
        if query_date:
            anchors.append(query_date)

        if anchors:
            intro = f"我已经按 {' / '.join(anchors)} 查询过{scope_label}，"
        else:
            intro = f"我已经查询过{scope_label}，"

        return (
            intro
            + "但当前没有查到可用的匹配结果。"
            + "这轮我先不继续往下空查了，避免系统在没有车次结果时反复补查。"
            + "如果你愿意，我可以继续帮你改查相邻日期、附近车站，或者放宽筛选条件。"
        )

    def _looks_like_train_ticket_request(self, user_text: str) -> bool:
        compact = re.sub(r"\s+", "", str(user_text or ""))
        if not compact:
            return False
        ticket_tokens = ("余票", "有票", "票务", "已售罄", "候补", "商务座", "一等座", "二等座", "优选一等座", "12306")
        train_tokens = tuple(f"{prefix}{digits}" for prefix, digits in re.findall(r"([GDKTZC])(\d{1,5})", compact.upper()))
        return any(token in compact for token in ticket_tokens) and bool(train_tokens)

    def _facts_have_object(self, facts: Dict[str, Any], object_name: str) -> bool:
        return any(str(item.get("object") or "").strip() == object_name for item in self._iter_query_records(facts))

    def _extract_route_from_path_pretty(self, pretty: str) -> Optional[str]:
        lines = [str(line).strip() for line in str(pretty or "").splitlines() if str(line or "").strip()]
        station_lines = []
        pattern = re.compile(r"^\d+\s*\|.*\|\s*([^|()]+?)\s*(?:\([A-Z]+\))?\s*$")
        for line in lines:
            match = pattern.match(line)
            if match:
                station_name = str(match.group(1) or "").strip()
                if station_name:
                    station_lines.append(station_name)
        if len(station_lines) >= 2:
            return f"{station_lines[0]}-{station_lines[-1]}"
        return None

    def _resolve_route_for_ticket_followup(self, facts: Dict[str, Any], session: Optional[SessionMemory]) -> Optional[str]:
        for item in self._iter_query_records(facts):
            obj = str(item.get("object") or "").strip()
            query_id = str(item.get("id") or "").strip()
            if obj in {"left_ticket_s2s", "station_to_station_mini", "station_to_station_future", "station_to_station_past", "s2s_benchmark"} and "-" in query_id:
                return query_id

        for item in self._iter_query_records(facts):
            obj = str(item.get("object") or "").strip()
            if obj not in {"path_detail", "path_future", "path_past"}:
                continue
            pretty = item.get("pretty")
            route = self._extract_route_from_path_pretty(str(pretty or ""))
            if route:
                return route

        if session:
            anchor_route = str(session.resolve_anchor("route") or "").strip()
            if anchor_route:
                return anchor_route

        return None

    def _should_finalize_grounded_path_comparison(self, user_text: str, facts: Dict[str, Any]) -> bool:
        if not self.is_fast_mode():
            return False

        if facts.get("analysis") or facts.get("comparisons"):
            return False

        meta = facts.get("meta", {})
        if meta.get("errors") or meta.get("warnings"):
            return False

        queries = self._iter_query_records(facts)
        if len(queries) < 2:
            return False

        allowed_objects = {"path_detail", "path_future", "path_past", "path_stopcheck"}
        if not all(str(item.get("object") or "").strip() in allowed_objects for item in queries):
            return False

        train_ids = {
            str(item.get("id") or "").strip().upper()
            for item in queries
            if re.match(r"^[GDKTZC]\d{1,5}$", str(item.get("id") or "").strip().upper())
        }
        if len(train_ids) < 2:
            return False

        compact = re.sub(r"\s+", "", str(user_text or ""))
        compare_markers = (
            "更快",
            "更慢",
            "停站",
            "有什么不同",
            "有何不同",
            "区别",
            "差异",
            "对比",
            "谁快",
            "快在哪",
            "为什么",
        )
        return any(marker in compact for marker in compare_markers)

    def _build_rag_context(
        self,
        user_text: str,
        facts: Dict[str, Any],
        context_bundle: Optional[Dict[str, Any]],
    ) -> str:
        try:
            return self.knowledge_rag.build_prompt_context(
                user_text=user_text,
                facts=facts,
                context_bundle=context_bundle,
                top_k=4 if self.is_fast_mode() else 6,
            )
        except Exception:
            return ""

    def _format_presentation_plan(self, plan: Optional[Dict[str, Any]]) -> str:
        if not isinstance(plan, dict) or not plan:
            return ""

        lines = [
            "Fast presentation plan:",
            f"- answer_shape: {plan.get('answer_shape', 'structured_brief')}",
        ]

        sections = plan.get("sections") or []
        if sections:
            lines.append("- sections: " + ", ".join(str(item) for item in sections))

        highlight_fields = plan.get("highlight_fields") or []
        if highlight_fields:
            lines.append("- highlight_fields: " + ", ".join(str(item) for item in highlight_fields))

        if plan.get("prefer_table"):
            lines.append("- prefer_table: true when the evidence is stable and compact.")

        tone = plan.get("tone")
        if tone:
            lines.append(f"- tone: {tone}")

        return "\n".join(lines)

    def _build_calendar_grounding(self, facts: Dict[str, Any]) -> str:
        if not isinstance(facts, dict):
            return ""

        dates: List[str] = []
        for item in facts.get("queries", []):
            if not isinstance(item, dict):
                continue
            query_date = str(item.get("date") or "").strip()
            if re.fullmatch(r"\d{4}-\d{2}-\d{2}", query_date) and query_date not in dates:
                dates.append(query_date)

        if not dates:
            return ""

        weekday_labels = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
        lines = ["Calendar grounding:"]
        for query_date in dates[:4]:
            try:
                weekday = weekday_labels[datetime.strptime(query_date, "%Y-%m-%d").weekday()]
            except ValueError:
                continue
            lines.append(f"- {query_date} = {weekday}")

        if len(lines) == 1:
            return ""

        lines.append("- When the user refers to weekdays, use these exact mappings.")
        return "\n".join(lines)

    def build_context_bundle(
        self,
        user_text: str,
        facts: Dict[str, Any],
    ) -> Optional[Dict[str, Any]]:
        if not self.is_fast_mode():
            return None

        return self.fast_compressor.compress(
            user_text=user_text,
            facts=facts,
            psw=self.psw,
        )

    def build_messages(
            self,
            user_text: str,
            facts: Dict[str, Any],
            session: Optional[SessionMemory] = None,
            style: str = "detailed",
            length: str = "medium",
            context_bundle: Optional[Dict[str, Any]] = None,
            include_rag: bool = True,
            rag_context: Optional[str] = None,
            presentation_plan: Optional[Dict[str, Any]] = None,
    ) -> List[Dict[str, str]]:

        messages: List[Dict[str, str]] = []

        if (
            context_bundle is None
            and self.is_fast_mode()
            and not self.should_skip_fast_context_bundle(facts)
        ):
            context_bundle = self.build_context_bundle(
                user_text=user_text,
                facts=facts,
            )

        compact_fast = self._is_fast_compact_mode(context_bundle)
        fact_meta = facts.get("meta", {}) if isinstance(facts.get("meta"), dict) else {}
        intent_envelope = IntentEnvelope.from_dict(fact_meta.get("intent_envelope"))
        is_memory_profile_chat = intent_envelope.intent_family == "memory_profile_chat"

        # =====================================================
        # 1️⃣ System Prompt：铁路专家 + 多工具适配
        # =====================================================

        if is_memory_profile_chat:
            system_lines = [
                "You are RailGPT's closed-book user memory writer.",
                "Answer naturally in the user's language using only the supplied profile_index.",
                "Every train number, EMU/model, route, station, preference, and other proper noun in the answer must exactly appear as a value in profile_index.",
                "Do not use general railway knowledge and do not infer relationships between profile entries.",
                "explicit_preference is a remembered user statement; recurring_interest is only repeated attention and supports a tentative guess.",
                "If a requested category has no profile entry, say there is not enough reliable memory for that category.",
            ]
        elif compact_fast:
            system_lines = [
                "You are RailGPT Fast Answer Writer.",
                "Use the merged fast context to answer accurately and quickly.",
                "Prefer explicit ranking and direct recommendations over long exposition.",
                "Never invent train facts or ticket facts not grounded in the provided evidence.",
                "City names are acceptable station identifiers; do not over-ask for station suffixes.",
                "Always match the user's language.",
            ]
        else:
            system_lines = [
                "You are RailGPT AnswerGenerator v1.0.",
                "You are a knowledgeable and friendly railway enthusiast assistant.",
                "",
                "Your job:",
                "- Convert tool-returned factual JSON into a natural railway answer.",
                "- Summarize patterns (benchmark, stability, typical EMU usage).",
                "- Be human-friendly and railway-enthusiast oriented.",
                "",
                "Critical rules:",
                "- NEVER fabricate specific train facts not present in facts.",
                "- City names are acceptable station identifiers (tool layer resolves them).",
                "- Do NOT over-ask user for station suffix like '站/南/北'.",
                "- Ask only for slots required by the selected capability. Never apply OD requirements globally.",
                "- train_delay requires a train number only; OD is optional answer scope.",
                "- 你接入了实时铁路工具，不能在已有对应能力时声称无法核验。",
                "- 如果用户想核验某车次的余票，而 memory / facts / path_detail 已经能确定这趟车的发到区间，就应优先转成 left_ticket_s2s 所需的 DEP-ARR，而不是再追问用户。",
                "- 只有当某车次的余票问题既没有 route / OD，也无法从现有 facts / memory / path_detail 推出时，才说明'还缺这趟车对应的出发地-目的地，补齐后即可继续做12306余票核验'。"
                "",
                "Language:",
                "- Always match the user's language (Chinese/English)."
            ]

        messages.append({
            "role": "system",
            "content": "\n".join(system_lines)
        })

        if intent_envelope.selected_capability and not is_memory_profile_chat:
            answer_capabilities = [intent_envelope.selected_capability]
            if intent_envelope.selected_capability == "train_delay":
                answer_capabilities.append("path_detail")
            selected_contract = get_capability(intent_envelope.selected_capability)
            if selected_contract:
                answer_capabilities.extend(selected_contract.workflow)
            messages.append({
                "role": "system",
                "content": capability_catalog(level="l0", objects=answer_capabilities),
            })

        if intent_envelope.selected_capability == "route_train_benchmark":
            messages.append({
                "role": "system",
                "content": (
                    "Benchmark verification safety contract:\n"
                    "- The s2s_benchmark score and tier are the sole authority for whether the named train is a benchmark candidate on the requested OD segment.\n"
                    "- Timetable stops and duration may explain the rating but must not replace or override it.\n"
                    "- A train can be a benchmark for an intermediate OD segment even when its full service continues beyond the requested destination.\n"
                    "- Never redefine benchmark as zero stops, few stops, or matching the train's full origin and terminal."
                ),
            })

        candidate_guard = self._format_candidate_whitelist(facts, context_bundle)
        if candidate_guard:
            messages.append({
                "role": "system",
                "content": candidate_guard,
            })

        if not is_memory_profile_chat:
            messages.append({
                "role": "system",
                "content": (
                    "Railway knowledge answering rules:\n"
                    "- You may use stable general railway knowledge for explanations about technology, operations, history, culture, enthusiast slang, and comparison questions.\n"
                    "- Never present live ticket inventory, same-day platform allocation, current dispatch order, real-time assignment, or current running status as certain unless tools already grounded them.\n"
                    "- Never say that a lookup ran, returned no records, or failed unless the current-turn facts contain that query result. A prior turn's tool evidence does not prove the result for a newly requested station/train/date.\n"
                    "- Use the standard Chinese term 快照 for a time-stamped result; never write 快相.\n"
                    "- Tool provenance is displayed by the application UI. Do not repeat provider names or provider URLs unless the user explicitly asks about data sources.\n"
                    "- If both tool-grounded facts and general railway knowledge appear, make tool-grounded facts primary and keep the explanatory knowledge clearly secondary."
                ),
            })

        # =====================================================
        # ⭐ Smart EMU Knowledge Patch
        # =====================================================
        if is_memory_profile_chat:
            pass
        elif compact_fast:
            messages.append({
                "role": "system",
                "content": (
                    "Fast-mode answering note:\n"
                    "- Prefer the top merged candidates.\n"
                    "- If multiple options matter, rank them clearly.\n"
                    "- Keep the answer compact but specific.\n"
                    "- Return polished Markdown with short headings in the user's language.\n"
                    "- For comparable train records, prefer a compact Markdown table over repetitive numbered prose.\n"
                    "- Put caveats or notes in a separate bullet list."
                )
            })
        else:
            knowledge_lines = [
                "=================================================",
                "Smart EMU Knowledge Patch (VERY IMPORTANT)",
                "=================================================",
                "CR400 intelligent naming rules:",
                "- CR400AF-Z / CR400BF-Z = Intelligent flagship (8-car)",
                "- CR400AF-BZ / CR400BF-BZ = Intelligent upgraded long (17-car)",
                "- CR400AF-BS / CR400BF-BS = Premium intelligent long (17-car)",
                "- CR400AF-AZ / CR400BF-AZ = Intelligent upgraded long (16-car)",
                "- CR400AF-AS / CR400BF-AS = Premium intelligent long (16-car)",
                "- CR400AFS / CR400BFS = ALSO treated as intelligent-config batches",
                "",
                "Therefore:",
                "BFS/AFS MUST be treated as 智能动车组, NOT standard."
            ]

            messages.append({
                "role": "system",
                "content": "\n".join(knowledge_lines)
            })

            # =====================================================
            # ⭐ Basic Formation Notes
            # =====================================================

            emu_basic_lines = [
                "=================================================",
                "Basic EMU Formation Notes",
                "=================================================",
                "CR400AF/BF = standard Fuxing (8-car)",
                "CR300AF/BF = newer 300km/h sets",
                "",
                "CRH380A/B/C/D = usually 8-car",
                "CRH380AL/BL = 16-car long sets (L = Long)",
                "",
                "Operational hint:",
                "- Stop duration 5–15min may be waiting/dispatch.",
                "- Stop >25min often indicates technical stop or reversal."
            ]

            messages.append({
                "role": "system",
                "content": "\n".join(emu_basic_lines)
            })

        # =====================================================
        # 2️⃣ Recent Conversation (for reference only)
        # =====================================================

        if session:
            if hasattr(session, "build_agent_context_view"):
                context_view = session.build_agent_context_view(
                    role="answer",
                    mode=self.get_final_mode(),
                    user_text=user_text,
                    include_dialogue=False,
                )
                if (
                    intent_envelope.intent_family.endswith("chat")
                    and intent_envelope.intent_family != "memory_profile_chat"
                ):
                    context_view["memory_context_package"] = {}
                if intent_envelope.intent_family in {"social_chat", "knowledge_chat", "travel_chat", "chat", "memory_profile_chat"}:
                    context_view["working_anchors"] = {}
                if intent_envelope.intent_family == "memory_profile_chat":
                    memory_package = context_view.get("memory_context_package") or {}
                    context_view["memory_context_package"] = {
                        "schema_version": memory_package.get("schema_version", 2),
                        "hard_anchors": {},
                        "profile_index": list(memory_package.get("profile_index") or [])[:6],
                    }
                elif isinstance(context_view.get("memory_context_package"), dict):
                    context_view["memory_context_package"].pop("profile_index", None)
                messages.append({
                    "role": "system",
                    "content": (
                        "Role-scoped AgentContextPackage for final answer continuity:\n"
                        f"{json.dumps(context_view, ensure_ascii=False)}"
                    ),
                })
                if intent_envelope.intent_family == "memory_profile_chat":
                    messages.append({
                        "role": "system",
                        "content": (
                            "Long-term memory safety contract:\n"
                            "- This profile is user-preference context only, never railway operational evidence.\n"
                            "- explicit_preference means the user directly stated it.\n"
                            "- recurring_interest means only that the user asked about it repeatedly; phrase any conclusion as a tentative guess.\n"
                            "- Treat profile categories as a closed evidence set. Mention only exact values present in profile_index.\n"
                            "- Answer each requested category separately. If there is no entry for a requested category, say that there is not enough reliable memory for it.\n"
                            "- Never infer an EMU/model from a train number, a train from a route, or any other unstored relationship.\n"
                            "- Never turn profile entries into routes, dates, assignments, ticket status, delay status, or other facts.\n"
                            "- If the profile does not support an answer, say that you do not reliably remember it yet."
                        ),
                    })
            if is_memory_profile_chat:
                recent_messages = []
            elif hasattr(session, "build_llm_history"):
                recent_messages = session.build_llm_history(
                    mode=self.get_final_mode(),
                    exclude_current_user=True,
                    latest_user_text=user_text,
                )
            else:
                recent_messages = list(session.get_recent_messages())
                if recent_messages and recent_messages[-1].get("role") == "user" and recent_messages[-1].get("content") == user_text:
                    recent_messages = recent_messages[:-1]
            messages.extend(recent_messages)

        if session and session.in_followup():
            messages.append({
                "role": "system",
                "content": (
                    "=================================================\n"
                    "FOLLOW-UP MODE ACTIVE\n"
                    "=================================================\n"
                    "The user is replying to your previous clarification.\n"
                    "Do NOT treat this as a new unrelated query.\n"
                    "You MUST fill missing slots based on the previous question.\n\n"
                    f"{session.get_followup_context()}\n"
                )
            })

        if session:
            context_agent_context = session.get_context_agent_context(min_confidence=55)
            if (
                self.is_fast_mode()
                and context_agent_context
                and intent_envelope.intent_family not in {"social_chat", "knowledge_chat", "travel_chat", "chat", "memory_profile_chat"}
            ):
                messages.append({
                    "role": "system",
                    "content": (
                        "Use this FAST context-agent normalization to preserve dialogue continuity in fast modes.\n"
                        f"{context_agent_context}"
                    ),
                })

        # =====================================================
        # 3️⃣ User Query
        # =====================================================

        messages.append({
            "role": "user",
            "content": user_text
        })

        if intent_envelope.selected_capability or intent_envelope.intent_family != "unknown":
            messages.append({
                "role": "system",
                "content": (
                    "Validated intent envelope:\n"
                    f"{json.dumps(intent_envelope.to_dict(), ensure_ascii=False)}\n"
                    "Use only the selected capability's required slots. "
                    "If scope contains dep/arr, present only that segment."
                ),
            })

        missing_evidence = list(fact_meta.get("missing_required_evidence") or [])
        if missing_evidence:
            messages.append({
                "role": "system",
                "content": (
                    "Required live evidence is unavailable: " + ", ".join(missing_evidence) + ".\n"
                    "State that the live lookup did not return reliable evidence. "
                    "Never infer live delay, ticket, platform, or operating status from a timetable/path record. "
                    "Scheduled path facts may be shown only as clearly labelled timetable background."
                ),
            })

        completed_queries = [
            item
            for item in facts.get("queries", [])
            if isinstance(item, dict)
            and str(item.get("type") or "query") not in {"query_error", "query_empty"}
            and (item.get("evidence") is not None or item.get("pretty") or item.get("fast_views"))
        ]
        if completed_queries:
            current_objects = sorted(
                {str(item.get("object") or "").strip() for item in completed_queries if str(item.get("object") or "").strip()}
            )
            messages.append({
                "role": "system",
                "content": (
                    "Current-turn tool execution contract:\n"
                    "- The requested lookup has already completed and its evidence is included below.\n"
                    "- Answer with the available result now. Never say you are about to query, ask the user to wait, or promise a later result.\n"
                    "- Do not describe a completed tool call as still running.\n"
                    "- If the result is a time-stamped snapshot, quote its exact observed time; never invent a nearby time or estimate elapsed time.\n"
                    "Evidence isolation contract:\n"
                    f"- The current evidence objects are: {', '.join(current_objects)}.\n"
                    "- Prior user/assistant messages and ActiveTopicFrame are continuity context, not factual evidence for this answer.\n"
                    "- Do not carry over a prior board snapshot, delay result, time, train status, station state, or inference unless it appears in the current-turn evidence.\n"
                    "- In particular, path_detail establishes scheduled route/profile facts only. It cannot establish real-time delay, punctuality, or the contents of an earlier snapshot.\n"
                    "- Use 快照, never 快相."
                ),
            })

        station_board_queries = [
            item for item in completed_queries
            if str(item.get("object") or "").strip() == "station_board"
        ]
        if station_board_queries:
            board = station_board_queries[-1]
            grounded_slots = board.get("grounded_slots") if isinstance(board.get("grounded_slots"), dict) else {}
            freshness = board.get("freshness") if isinstance(board.get("freshness"), dict) else {}
            row_count = len(board.get("evidence") or []) if isinstance(board.get("evidence"), list) else 0
            messages.append({
                "role": "system",
                "content": (
                    "Station-board presentation contract:\n"
                    f"- Queried station: {grounded_slots.get('station') or 'unknown'}.\n"
                    f"- Board direction: {grounded_slots.get('direction') or 'unknown'}.\n"
                    f"- Exact snapshot time: {freshness.get('fetched_at') or 'not supplied'}.\n"
                    f"- Returned board rows: {row_count}.\n"
                    "- The station above remains the conversation subject. Origins, destinations, and trains inside rows are entries only.\n"
                    "- Call this a board snapshot, not a continuously updating feed.\n"
                    "- Never replace the exact snapshot time with phrases such as 'around noon' or claim how long ago it was unless exact arithmetic is required and grounded."
                ),
            })

        # =====================================================
        # 4️⃣ Facts JSON (Full, No Truncation)
        # =====================================================
        if context_bundle:
            facts_text = json.dumps(
                self._strip_fast_only_fields(context_bundle),
                ensure_ascii=False,
                indent=2,
            )
            facts_intro = (
                "Fast mode compressed evidence context.\n"
                "The original full facts were split into chunks, processed in parallel, merged into a candidate set, and paired with retained micro-evidence.\n"
                "Treat this merged context as the working evidence set for answering.\n"
                "Tool-grounded query-date evidence always outranks any background knowledge.\n"
            )
        else:
            facts_text = json.dumps(self._strip_fast_only_fields(facts), ensure_ascii=False, indent=2)
            facts_intro = "Complete factual tool output JSON:\nTool-grounded evidence always outranks any background knowledge.\n"

        messages.append({
            "role": "system",
            "content": (
                    "Evidence below is current-turn tool output; honor each query's own scope and timestamp.\n"
                    f"Current Beijing Time (UTC+8): {datetime.now(ZoneInfo('Asia/Shanghai')).strftime('%Y-%m-%d %H:%M:%S')}\n\n"
                    + facts_intro
                    + facts_text
            )
        })

        empty_query_answer = self._build_empty_query_answer(user_text, facts)
        if empty_query_answer:
            messages.append({
                "role": "system",
                "content": (
                    "Empty-result grounding:\n"
                    "- query_empty means the tool completed a valid lookup but found no matching result for the current scope.\n"
                    "- If all current railway queries are empty placeholders and there is no positive evidence, answer directly instead of requesting more tool rounds.\n"
                    "- Prefer a calm, user-facing explanation over internal protocol language.\n"
                    f"- Recommended answer direction: {empty_query_answer}"
                ),
            })

        calendar_grounding = self._build_calendar_grounding(facts)
        if calendar_grounding:
            messages.append({
                "role": "system",
                "content": calendar_grounding,
            })

        if include_rag and rag_context is None and not is_memory_profile_chat:
            rag_context = self._build_rag_context(
                user_text=user_text,
                facts=facts,
                context_bundle=context_bundle,
            )
            if rag_context:
                messages.append({
                    "role": "system",
                    "content": rag_context,
                })
        elif rag_context and not is_memory_profile_chat:
            messages.append({
                "role": "system",
                "content": rag_context,
            })

        presentation_context = self._format_presentation_plan(presentation_plan)
        if presentation_context:
            messages.append({
                "role": "system",
                "content": presentation_context,
            })

        chat_tags, chat_guidance = self._chat_guidance_from_meta(facts.get("meta", {}))
        is_dedicated_chat_turn = bool(chat_tags or chat_guidance)
        if is_dedicated_chat_turn:
            messages.append({
                "role": "system",
                "content": (
                    "Chat continuation turn detected.\n"
                    "Answer the current user message conversationally.\n"
                    "Do not continue any previous pending clarification or unfinished railway query unless the user explicitly asks to resume it.\n"
                    "If the user is reacting to the previous answer, acknowledge that reaction first.\n"
                    "Use the recent conversation context before claiming you need more information.\n"
                    "Do not switch into slot-filling or railway pending mode unless the user clearly starts a new railway query.\n"
                ),
            })
            if chat_guidance:
                messages.append({
                    "role": "system",
                    "content": "Chat route hints:\n" + "\n".join(f"- {line}" for line in chat_guidance),
                })

        # =====================================================
        # 5️⃣ Output Style Hint
        # =====================================================

        if is_dedicated_chat_turn:
            instruct_lines = [
                f"Answer style: {style}. Preferred length: short-to-medium.",
                "Reply like a warm, knowledgeable railway companion rather than a form.",
                "When the user says things like '这么厉害的吗' or '原来如此', first respond to the feeling or reaction, then continue naturally.",
                "Keep the tone friendly, grounded, and lightly conversational; one or two short paragraphs is usually enough.",
                "Use the conversation memory and the immediately previous topic so the reply feels connected.",
                "Do not end every reply by forcing a new railway query. Only invite follow-up naturally when it helps.",
                "Use at most one natural, context-appropriate emoji when it adds warmth; do not decorate every sentence.",
                "Do not force a table into casual chat, emotional reactions, stories, or prose."
            ]
        else:
            instruct_lines = [
                f"Answer style: {style}. Preferred length: {length}.",
                "If multiple trains appear, separate sections clearly.",
                "If user asked benchmark/fastest, rank only candidates explicitly present in current tool evidence.",
                "If current tool evidence contains no rated candidate, state that directly and do not supply a plausible-looking ranking.",
                "Use polished Markdown with concise headings and strong visual hierarchy.",
                "Use 1-3 meaningful, context-appropriate emoji in the whole answer, normally in headings such as '🚄 推荐车次', '📊 对比', or '⚠️ 提醒'. Do not place emoji in every row, bullet, or sentence.",
                "When there are at least two comparable records with shared fields, use a compact Markdown table. This especially applies to train lists, rankings, timetables, stop comparisons, ticket inventory, EMU assignments, and model comparisons.",
                "Keep table columns selective and readable: include only fields that help answer the question. Never create a value merely to fill a table cell; use '—' for a genuinely unavailable field.",
                "After a table, add only the short conclusion or caveat that materially helps the user.",
                "Do not use a table for a single-value lookup, casual chat, prose, or content with long free-form cells.",
                "Do not expose internal tool names. Clearly distinguish tool-grounded facts from general explanation."
            ]

        messages.append({
            "role": "system",
            "content": "\n".join(instruct_lines)
        })

        return messages

    def should_use_fast_direct_final(
        self,
        user_text: str,
        facts: Dict[str, Any],
        context_bundle: Optional[Dict[str, Any]],
    ) -> bool:
        if not self._is_fast_compact_mode(context_bundle):
            return False

        if not isinstance(context_bundle, dict):
            return False

        if facts.get("analysis") or facts.get("comparisons"):
            return False

        meta = facts.get("meta", {})
        if meta.get("errors") or meta.get("warnings") or meta.get("chat_messages"):
            return False

        queries = [item for item in facts.get("queries", []) if isinstance(item, dict)]
        if len(queries) != 1:
            return False

        source_stats = context_bundle.get("source_stats", {})
        merged_candidate_count = int(source_stats.get("merged_candidate_count", 0))
        retained_evidence_count = int(source_stats.get("retained_evidence_count", 0))
        if merged_candidate_count <= 0 and retained_evidence_count <= 0:
            return False

        if int(source_stats.get("raw_fallback_count", 0)) > 0:
            return False

        route_expansion_tokens = (
            "智能动车",
            "车底",
            "担当",
            "是不是",
            "停不停",
            "是否停",
            "哪个更好",
            "对比",
            "比较",
        )
        if any(token in user_text for token in route_expansion_tokens):
            return False

        if re.search(r"\d{4}-\d{2}-\d{2}", user_text):
            return False

        query_object = str(queries[0].get("object") or "")
        simple_lookup_objects = {
            "telecode",
            "name",
            "station",
        }
        return query_object in simple_lookup_objects

    def _chat_guidance_from_meta(self, meta: Any) -> tuple[list[str], list[str]]:
        if not isinstance(meta, dict):
            return [], []

        tags: list[str] = []
        for tag in meta.get("chat_route_tags") or []:
            cleaned = str(tag or "").strip().lower()
            if cleaned and cleaned not in tags:
                tags.append(cleaned)

        # Backward compatibility for older routes/tests. Classify legacy router
        # prose, but never pass that prose through to the final answer model.
        for message in meta.get("chat_messages") or []:
            tag = self._chat_route_tag_from_legacy_message(message)
            if tag and tag not in tags:
                tags.append(tag)

        guidance_by_tag = {
            "contextual_social": "Acknowledge the user's reaction first, then continue from the previous answer.",
            "contextual_followup": "Resolve vague references against the immediately previous answer before asking for more information.",
            "creative_followup": "Continue or transform the previous answer in the requested style.",
            "memory_profile": "Use profile memory only as soft preference evidence; say when confidence is weak.",
            "railway_knowledge": "Answer as stable railway knowledge and avoid presenting live operational facts without tool evidence.",
            "travel_chat": "Treat cities and rail context as itinerary context, not missing ticket-query slots.",
            "capability_boundary": "Explain the capability boundary plainly without exposing internal routing.",
            "directional_speed": "Answer the directional-speed easter-egg naturally and keep the broad-rule caveat.",
            "identity_or_capability": "Explain RailGPT's identity or capabilities briefly.",
            "general_chat": "Reply naturally in the user's language.",
        }
        return tags, [guidance_by_tag[tag] for tag in tags if tag in guidance_by_tag]

    def _chat_route_tag_from_legacy_message(self, message: Any) -> str:
        text = str(message or "").strip().lower()
        if not text:
            return "general_chat"
        if "memory" in text and "profile" in text:
            return "memory_profile"
        if "directional speed" in text or "easter" in text:
            return "directional_speed"
        if "capability-boundary" in text or "current-only" in text or "current running status" in text:
            return "capability_boundary"
        if "travel inspiration" in text or "attractions" in text:
            return "travel_chat"
        if "railway knowledge" in text or "stable railway background" in text:
            return "railway_knowledge"
        if "contextual social" in text or "reacting" in text:
            return "contextual_social"
        if "creative" in text or "rewriting" in text or "different style" in text:
            return "creative_followup"
        if "contextual" in text or "previous answer" in text or "recent conversation" in text:
            return "contextual_followup"
        if "capabilities" in text or "who railgpt is" in text:
            return "identity_or_capability"
        return "general_chat"

    def _format_candidate_whitelist(self, facts: Dict, context_bundle: Optional[Dict[str, Any]] = None) -> str:
        if not isinstance(facts, dict):
            return ""

        queries = [item for item in facts.get("queries", []) if isinstance(item, dict)]
        route_objects = {
            "s2s_benchmark",
            "station_to_station_mini",
            "station_to_station_detail",
            "station_to_station_future",
            "station_to_station_past",
            "s2s_timeband_dep",
            "s2s_timeband_arr",
            "s2s_regular_only",
            "s2s_temporary_only",
            "s2s_bureau_filter",
        }
        if not any(str(query.get("object") or "") in route_objects for query in queries):
            return ""

        labels: list[str] = []

        def add_label(value: Any) -> None:
            label = str(value or "").strip().upper()
            if re.fullmatch(r"[GDCZTKSY]\d{1,5}[A-Z]?", label) and label not in labels:
                labels.append(label)

        for query in queries:
            for candidate in query.get("fast_candidates") or []:
                if not isinstance(candidate, dict):
                    continue
                add_label(candidate.get("label"))
                attrs = candidate.get("attributes") if isinstance(candidate.get("attributes"), dict) else {}
                add_label(attrs.get("train_no"))

        if isinstance(context_bundle, dict):
            for candidate in context_bundle.get("candidates") or []:
                if not isinstance(candidate, dict):
                    continue
                add_label(candidate.get("label"))
                attrs = candidate.get("attributes") if isinstance(candidate.get("attributes"), dict) else {}
                add_label(attrs.get("train_no"))

        if not labels:
            return (
                "Route candidate whitelist:\n"
                "- No train candidate whitelist is available from tool evidence.\n"
                "- For benchmark, fastest, ranking, or recommendation answers, do not name specific train numbers.\n"
                "- Say that the available evidence is insufficient and ask to rerun or verify the route query."
            )

        return (
            "Route candidate whitelist:\n"
            f"- Allowed train numbers for benchmark/ranking/recommendation claims: {', '.join(labels)}.\n"
            "- Every train number recommended, ranked, or compared as an OD candidate must appear in this whitelist.\n"
            "- If a train number is not in this whitelist, do not mention it as a candidate, even if it seems plausible from background knowledge.\n"
            "- If the whitelist is too small for the user's request, say so and answer only from the listed evidence."
        )

    def _strip_fast_only_fields(self, value: Any) -> Any:
        if isinstance(value, dict):
            cleaned = {}
            for key, item in value.items():
                lowered = str(key).lower()
                if key in {"fast_views", "fast_candidates", "artifacts", "attachments", "mediaCatalog", "_media_catalog"}:
                    continue
                if lowered in {
                    "url",
                    "pictureurl",
                    "picurl",
                    "remote_url",
                    "local_path",
                    "geojson",
                    "fallback_svg",
                    "source",
                    "source_json",
                    "_railgo",
                    "provider",
                    "api_version",
                    "endpoint",
                }:
                    continue
                cleaned[key] = self._strip_fast_only_fields(item)
            return cleaned

        if isinstance(value, list):
            return [self._strip_fast_only_fields(item) for item in value]

        if isinstance(value, str) and "\n" in value:
            provenance_prefixes = (
                "source:",
                "🔎 source",
                "📌 data source:",
                "✅ plans are generated by official",
            )
            lines = [
                line
                for line in value.splitlines()
                if not line.strip().lower().startswith(provenance_prefixes)
            ]
            return "\n".join(lines)

        return value

    def generate(
            self,
            user_text: str,
            facts: Dict[str, Any],
            session: Optional[SessionMemory] = None,
            style: str = "detailed",
            length: str = "medium",
            context_bundle: Optional[Dict[str, Any]] = None,
    ) -> str:

        messages = self.build_messages(
            user_text=user_text,
            facts=facts,
            session=session,
            style=style,
            length=length,
            context_bundle=context_bundle,
        )

        return self._normalize_display_text(self.final_llm.generate(messages))

    def build_pending_messages(
        self,
        user_text: str,
        pending_payload: Dict[str, Any],
        session: Optional[SessionMemory] = None,
        tasks: Optional[List[Dict[str, Any]]] = None,
        facts: Optional[Dict[str, Any]] = None,
    ) -> List[Dict[str, str]]:
        payload = pending_payload if isinstance(pending_payload, dict) else {}
        missing_slots = payload.get("slot", [])
        context = payload.get("context", {}) if isinstance(payload.get("context"), dict) else {}
        slot_contract = context.get("missing_slot_contract") if isinstance(context.get("missing_slot_contract"), dict) else {}
        fallback_question = str(payload.get("question") or "").strip()

        history: List[Dict[str, str]] = []
        if session and hasattr(session, "build_llm_history"):
            history = session.build_llm_history(
                mode=self.get_final_mode(),
                max_messages=6,
                max_chars=4000,
                exclude_current_user=True,
                latest_user_text=user_text,
            )

        compact_facts = {}
        if isinstance(facts, dict):
            meta = facts.get("meta", {}) if isinstance(facts.get("meta"), dict) else {}
            compact_facts = {
                "query_objects": [
                    str(item.get("object") or "").strip()
                    for item in facts.get("queries", [])
                    if isinstance(item, dict) and str(item.get("object") or "").strip()
                ][:6],
                "warnings": [str(item or "").strip() for item in meta.get("warnings", []) if str(item or "").strip()][:3],
                "errors": [str(item or "").strip() for item in meta.get("errors", []) if str(item or "").strip()][:3],
            }

        task_hints: List[str] = []
        for item in tasks or []:
            if not isinstance(item, dict):
                continue
            action = str(item.get("action") or "").strip()
            if action:
                task_hints.append(action)

        system_prompt = (
            "You are RailGPT Clarification Writer.\n"
            "Write a natural Chinese follow-up question for a railway assistant when some query slots are missing.\n"
            "Your job is only to ask for the minimum missing information so the lookup can continue.\n"
            "\n"
            "Rules:\n"
            "1. Sound natural, warm, and concise.\n"
            "2. Ask only for the missing information; never ask again for known details.\n"
            "3. Reuse known context like route, train number, station, or date when helpful.\n"
            "3a. The capability-specific missing-slot contract is authoritative. Respect why each field is required and never ask for optional or unrelated fields.\n"
            "3b. Apply only defaults declared by the selected capability contract; explicit user values always override defaults.\n"
            "4. Do not mention internal slot names such as dep, arr, train_no, route.\n"
            "5. Do not use bullet lists, JSON, Markdown headings, or rigid form-style wording.\n"
            "6. Do not say you cannot help. Do not explain the whole system.\n"
            "7. Return only the final user-facing clarification sentence or short paragraph in Chinese."
        )

        user_prompt = (
            f"Latest user message:\n{str(user_text or '').strip()}\n\n"
            f"Missing slots:\n{json.dumps(missing_slots, ensure_ascii=False)}\n\n"
            f"Known context:\n{json.dumps(context, ensure_ascii=False, indent=2)}\n\n"
            f"Capability-specific missing-slot contract:\n{json.dumps(slot_contract, ensure_ascii=False, indent=2)}\n\n"
            f"Recent dialogue:\n{json.dumps(history, ensure_ascii=False, indent=2)}\n\n"
            f"Planner/task hints:\n{json.dumps(task_hints[:6], ensure_ascii=False)}\n\n"
            f"Fact hints:\n{json.dumps(compact_facts, ensure_ascii=False, indent=2)}\n\n"
            f"Current deterministic fallback wording:\n{fallback_question or '请补充关键信息。'}\n\n"
            "Now write the next clarification message in Chinese."
        )

        return [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]

    def generate_pending_question(
        self,
        user_text: str,
        pending_payload: Dict[str, Any],
        session: Optional[SessionMemory] = None,
        tasks: Optional[List[Dict[str, Any]]] = None,
        facts: Optional[Dict[str, Any]] = None,
    ) -> str:
        messages = self.build_pending_messages(
            user_text=user_text,
            pending_payload=pending_payload,
            session=session,
            tasks=tasks,
            facts=facts,
        )
        return self.final_llm.generate(messages)

    @staticmethod
    def _normalize_display_text(text: str) -> str:
        """Correct known display-only lexical slips without changing railway facts."""
        return str(text or "").replace("快相", "快照")

    def _normalized_stream(self, tokens: Iterable[str]):
        # Keep one trailing character so a two-character correction still works
        # when the provider splits it across separate SSE chunks.
        pending = ""
        for token in tokens:
            pending = self._normalize_display_text(pending + str(token or ""))
            if len(pending) > 1:
                yield pending[:-1]
                pending = pending[-1:]
        if pending:
            yield self._normalize_display_text(pending)

    def stream_pending_question(
        self,
        user_text: str,
        pending_payload: Dict[str, Any],
        session: Optional[SessionMemory] = None,
        tasks: Optional[List[Dict[str, Any]]] = None,
        facts: Optional[Dict[str, Any]] = None,
    ):
        messages = self.build_pending_messages(
            user_text=user_text,
            pending_payload=pending_payload,
            session=session,
            tasks=tasks,
            facts=facts,
        )
        return self._normalized_stream(self.final_llm.stream_generate(messages))

    def stream_final(self, messages: List[Dict[str, str]]):
        return self._normalized_stream(self.final_llm.stream_generate(messages))

    def produce_and_record(
            self,
            user_text: str,
            facts: Dict[str, Any],
            session: Optional[SessionMemory] = None,
            style: str = "detailed",
            length: str = "medium",
            record_session: bool = True,
            context_bundle: Optional[Dict[str, Any]] = None,
    ) -> str:

        answer = self.generate(
            user_text=user_text,
            facts=facts,
            session=session,
            style=style,
            length=length,
            context_bundle=context_bundle,
        )

        if record_session and session:
            session.add_ai_message(answer)

        return answer

    def generate_structured(
        self,
        user_text: str,
        facts: Dict[str, Any],
        session=None,
        context_bundle: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        严格结构化输出：
        - final
        - need_more_facts
        """

        fact_meta = facts.get("meta", {}) if isinstance(facts.get("meta"), dict) else {}
        if fact_meta.get("missing_required_evidence"):
            return {
                "type": "final",
                "content": "Required capability evidence is unavailable; finalize honestly without substituting other evidence.",
            }

        if self.should_force_finalize_on_empty_queries(facts):
            return {
                "type": "final",
                "content": self._build_empty_query_answer(user_text, facts),
            }

        if self._looks_like_train_ticket_request(user_text) and not self._facts_have_object(facts, "left_ticket_s2s"):
            route_hint = self._resolve_route_for_ticket_followup(facts, session=session)
            query_date = ""
            for item in self._iter_query_records(facts):
                date_value = str(item.get("date") or "").strip()
                if date_value:
                    query_date = date_value
                    break
            if not query_date and session:
                query_date = str(session.resolve_anchor("date") or "").strip()

            if route_hint:
                missing = [{"domain": "railway", "object": "left_ticket_s2s", "id": route_hint}]
                if query_date:
                    missing[0]["date"] = query_date
                return {"type": "need_more_facts", "extra_request": {"missing": missing}}

            train_hint = ""
            train_match = re.search(r"([GDKTZC]\d{1,5})", str(user_text or "").upper())
            if train_match:
                train_hint = train_match.group(1)
            followup = "我可以继续做 12306 余票核验，不过还缺这趟车对应的出发地-目的地。"
            if train_hint:
                followup += f"比如告诉我 {train_hint} 是从哪里到哪里，我就继续查。"
            else:
                followup += "把这趟车的发到区间告诉我，我就继续查。"
            return {"type": "need_user_input", "question": followup}

        if self._should_finalize_grounded_path_comparison(user_text, facts):
            return {
                "type": "final",
                "content": "我已经拿到相关车次的路径和停站信息，可以直接基于这些结果给出对比结论。",
            }

        messages = self.build_messages(
            user_text=user_text,
            facts=facts,
            session=session,
            style="analysis",
            length="medium",
            context_bundle=context_bundle,
            include_rag=False,
        )

        compact_fast = self._is_fast_compact_mode(context_bundle)

        if compact_fast:
            messages.append({
                "role": "system",
                "content": (
                    "Fast-mode decision protocol:\n"
                    "- Output JSON only.\n"
                    "- Prefer {\"type\":\"final\"} when the merged candidates and retained evidence are already enough.\n"
                    "- Use {\"type\":\"need_more_facts\"} only if the user's request clearly requires another railway object not present.\n"
                    "- Reuse conversation memory anchors before deciding that a key slot is missing.\n"
                    "- If route/date evidence already exists and the user only adds soft preferences such as a liked train, model, bureau, or comfort bias, prefer final instead of another tool round.\n"
                    "- If pronouns like 这几班/这几趟/这条线/推荐的这些 appear and memory already identifies the trains or route, treat the reference as resolved.\n"
                    "- If the user asks to verify ticket availability for a specific train and memory / facts / path_detail already reveal its route, prefer need_more_facts with left_ticket_s2s instead of final or another user question.\n"
                    "- If the user is asking broad railway knowledge that is not a concrete tool lookup, prefer final and answer with stable background knowledge instead of requesting more facts.\n"
                    "- Use {\"type\":\"need_user_input\"} only for truly missing key slots.\n"
                )
            })
            messages.append({
                "role": "system",
                "content": (
                    "Return exactly one JSON object with one of these schemas:\n"
                    "{\"type\":\"final\",\"content\":\"...\"}\n"
                    "{\"type\":\"need_user_input\",\"question\":\"...\"}\n"
                    "{\"type\":\"need_more_facts\",\"extra_request\":{\"missing\":[{\"domain\":\"railway\",\"object\":\"...\",\"id\":\"...\",\"date\":\"YYYY-MM-DD\"}]}}"
                )
            })
            raw = self.llm.generate(messages)
            try:
                result = loads_llm_json(raw)
            except Exception:
                return {
                    "type": "final",
                    "content": raw
                }

            if not isinstance(result, dict):
                return {
                    "type": "final",
                    "content": str(result)
                }

            rtype = result.get("type")
            if rtype == "need_user_input":
                question = result.get("question") or "我可以继续帮你查，不过还差一个关键条件。"
                if session:
                    session.enter_followup(
                        question=question,
                        slots={"reason": "missing_key_slots"}
                    )
                return {"type": "need_user_input", "question": question}

            if rtype == "need_more_facts":
                extra = self._validated_extra_request(result.get("extra_request"))
                if extra and extra.get("missing"):
                    return {"type": "need_more_facts", "extra_request": extra}
                return {
                    "type": "final",
                    "content": "我先基于现有结果给你最接近的结论。"
                }

            return {
                "type": "final",
                "content": str(result.get("content") or raw)
            }

        tool_lines = [
            "=================================================",
            "Tool Capability Map (railway.query.object)",
            "=================================================",

            "[object=train]",
            "- Input: train number (e.g. G87)",
            "- Output: recent EMU assignment history (30 days)",
            "- Use for: 车底/担当/是否智能动车",

            "[object=emu]",
            "- Input: EMU ID (e.g. CR400AFZ2333)",
            "- Output: services this EMU operated recently",

            "[object=path_detail]",
            "- Input: train number + date",
            "- Output: full timetable + stops + dwell time",
            "- Use for: 经停表/是否停某站/标杆分析",

            "[object=path_stopcheck]",
            "- Input: 'G87,G89|南京南,杭州东'",
            "- Output: stop yes/no matrix",
            "- Use for: 批量判断哪些车停某站",

            "[object=station_to_station_mini]",
            "- Input: DEP-ARR",
            "- Output: trains running today",
            "- Use for: 出行规划第一步",

            "[object=station_to_station_future]",
            "- Input: DEP-ARR + date",
            "- Output: future timetable",

            "[object=station_to_station_past]",
            "- Input: DEP-ARR + date",
            "- Output: historical trains",

            "[object=left_ticket_s2s]",
            "- Input: DEP-ARR",
            "- Output: real-time ticket availability",
            "- 请严格遵循Input格式，对于用户咨询某车次的余票需求，也必须通过 DEP-ARR咨询，不允许输入车次",
            "- 如果 path_detail / memory 已经揭示了某车次的发到区间，应先把它改写成 DEP-ARR 再请求本工具",

            "[object=s2s_benchmark]",
            "- Input: DEP-ARR",
            "- Output: fastest flagship trains",

            "[object=transfer_12306]",
            "- Input: DEP-ARR|HUB",
            "- Output: transfer方案（两段组合）",
            "- NOTE: HUB may be city name only (e.g. 上饶)",

            "[object=s2s_timeband_dep]",
            "- Use for: morning/afternoon departure filter",

            "[object=s2s_timeband_arr]",
            "- Use for: arrival time filter",

            "[object=s2s_regular_only]",
            "- Use for: 每天开行车次",

            "[object=s2s_temporary_only]",
            "- Use for: 加开/临客",

            "[object=s2s_bureau_filter]",
            "- Input: DEP-ARR|路局",
            "- Use for: 担当路局筛选",

            "[object=telecode]",
            "- Input: station name",
            "- Output: 电报码",

            "[object=name]",
            "- Input: telecode",
            "- Output: station name reverse lookup",

            "[object=station]",
            "- Use only for an explicit single-station bureau/city/province/pinyin metadata request.",

            "[object=train_delay]",
            "- Input: train number; output: live punctuality/delay status by station",

            "[object=train_station_access]",
            "- Input: TRAIN|STATION|arrival/departure + date",
            "- Output: currently published check gate/exit/platform",

            "[object=station_board]",
            "- Input: STATION|arrival/departure; output: live station board rows",

            "- Coach composition/images and train route-map coordinates are temporarily unavailable.",
            "- Never substitute train assignment or path timetable evidence for those withdrawn services."
        ]

        messages.append({
            "role": "system",
            "content": "\n".join(tool_lines)
        })
        messages.append({
            "role": "system",
            "content": (
                "=================================================\n"
                "Mandatory Tool Rule: train vs car field\n"
                "=================================================\n"
                "The 'car' field in station_to_station and path results is ONLY a rough model type.\n"
                "It is NOT the exact EMU set assignment.You MUST prioritize the fastest trains first.\n\n"

                "If the user asks ANY of the following examples:\n"
                "- 车底/车组号\n"
                "- 担当动车组是否稳定\n"
                "- 最近用什么CR400编号\n"
                "- 我想坐南昌局的智能动车\n"
                "- 是否是智能动车组\n\n"

                "Then you MUST request:\n"
                "{domain:'railway', object:'train', id:'<train_no>'}\n\n"

                "You are NOT allowed to answer EMU assignment questions without train tool.\n"
            )
        })
        messages.append({
            "role": "system",
            "content": (
                "-------------------------------------------------\n"
                "Top Benchmark Candidate Expansion Rule\n"
                "-------------------------------------------------\n"
                "If the user has BOTH:\n"
                "- benchmark/fastest requirement\n"
                "- AND an extra constraint (bureau / intelligent EMU / specific model)\n\n"

                "Then you MUST do this workflow:\n"
                "1) Select Top 3 fastest benchmark trains from station_to_station.\n"
                "2) Request train tool for ALL Top 3 candidates.\n"
                "3) Compare which one satisfies the constraint best.\n\n"

                "Do NOT stop after checking only one train.\n"
                "If none satisfies perfectly, recommend the closest benchmark alternative.\n"
            )
        })

        # =====================================================
        # ⭐ Implicit OD Expansion Patch (京沪干线特殊问法)
        # =====================================================

        implicit_lines = [
            "=================================================",
            "Implicit Route Expansion Rules (VERY IMPORTANT)",
            "=================================================",
            "Sometimes the user asks about a corridor without giving explicit stations.",
            "Example: '京沪高铁有没有不停南京南的车？'",
            "",
            "In such cases, you MUST infer the main OD pairs and request station_to_station.",
            "",
            "京沪 corridor default OD candidates (MAX 6 queries):",
            "1) 北京南-上海虹桥",
            "2) 上海虹桥-北京南",
            "3) 北京南-上海",
            "4) 上海-北京南",
            "5) 北京-上海虹桥",
            "6) 上海虹桥-北京",
            "",
            "Rules:",
            "- ONLY trigger this when user mentions 京沪高铁/京沪线/沪宁段.",
            "- Do NOT exceed 6 station_to_station queries.",

            "If user asks skip-stop questions (不停靠某站):\n",
            "- You MUST NOT answer from station_to_station alone.\n",
            "- You MUST request path evidence.There is NO path query number limitation in this sort of question:try to cover ALL fasteast trains.\n",
            "- Only path can confirm a station is skipped.",
            "Workflow:",
            "Step1: request station_to_station for OD candidates.",
            "Step2: from returned trains, request path for fastest trains.",
            "Step3: answer whether 南京南 is skipped.",
        ]

        messages.append({
            "role": "system",
            "content": "\n".join(implicit_lines)
        })

        messages.append({
            "role": "system",
            "content": (
                "IMPORTANT:\n"
                "For questions asking whether a train type exists (e.g. '有没有不停南京南的标杆车'),\n"
                "you MUST NOT answer 'none exists' based on checking only a few examples.\n"
                "You must either:\n"
                "- verify ALL benchmark candidates via path, OR\n"
                "- return need_more_facts to continue verification.\n"
            )
        })

        dispatch_lines = [
            "=================================================",
            "Multi-round Scheduling Rules",
            "=================================================",

            "If user asks about timetable/stops → MUST request path.",
            "If user asks about EMU usage → request train first.",
            "If user asks about transfer strategy:",
            "- Decompose into two legs:",
            "  origin→hub and hub→destination",
            "- Request station_to_station twice.",

            "If hub not specified → return need_user_input asking preferred hub.",
            "Do NOT fabricate estimated arrival times if path is missing."
        ]

        messages.append({
            "role": "system",
            "content": "\n".join(dispatch_lines)
        })

        # 🔒 协议铁律（非常重要）
        messages.append({
            "role": "system",
            "content": (
                "You MUST respond in JSON ONLY.\n\n"

                "====================================\n"
                "Railway Answer Protocol (v2.6 Stable)\n"
                "====================================\n\n"

                "You are the FINAL Answer Generator.\n"
                "You must output EXACTLY one JSON object.\n\n"

                "------------------------------------\n"
                "Output Types (ONLY THREE)\n"
                "------------------------------------\n\n"

                "1) If facts are sufficient:\n"
                "Return:\n"
                "{\n"
                "  \"type\": \"final\",\n"
                "  \"content\": \"<natural language answer>\"\n"
                "}\n\n"

                "2) If user is missing key slots (departure/date/etc):\n"
                "Return:\n"
                "{\n"
                "  \"type\": \"need_user_input\",\n"
                "  \"question\": \"<ask user for missing info>\"\n"
                "}\n\n"

                "3) If facts are not sufficient AND tools can solve it:\n"
                "Return:\n"
                "{\n"
                "  \"type\": \"need_more_facts\",\n"
                "  \"extra_request\": {\n"
                "    \"missing\": [\n"
                "      {\n"
                "        \"domain\": \"railway\",\n"
                "        \"object\": \"train | emu | station_to_station_mini | station_to_station_future | station_to_station_past | "
                                        "path_detail | path_stopcheck | left_ticket_s2s | s2s_benchmark | transfer_12306 | telecode | name | "
                                        "train_delay | train_station_access | station_board | "
                                        "station_preselect | train_preselect | random_train\",\n"
                "        \"id\": \"<string>\",\n"
                "        \"date\": \"<optional YYYY-MM-DD>\"\n"
                "      }\n"
                "    ]\n"
                "  }\n"
                "}\n\n"

                "====================================\n"
                "Critical Rules\n"
                "====================================\n\n"

                "- Output JSON ONLY.\n"
                "- No code blocks.\n"
                "- No null.\n"
                "- If type is need_more_facts, missing MUST be non-empty.\n"
                "- If type is need_user_input, question MUST be non-empty.\n\n"

                "------------------------------------\n"
                "Slot Filling Priority (MOST IMPORTANT)\n"
                "------------------------------------\n\n"

                "Before returning need_user_input, you MUST check conversation memory anchors and recalled context.\n"
                "If the train / route / date is already available in memory, reuse it instead of asking again.\n\n"

                "Missing slots are capability-local. Only OD capabilities such as left_ticket_s2s, transfer_12306, and station-to-station queries require departure and arrival.\n"
                "train_delay requires only a train number; path_detail requires only a train number.\n\n"

                "If user provides city names (南京/福州/上饶):\n"
                "→ treat them as valid station identifiers.\n"
                "→ tool layer will resolve 南京→南京南/南京站 automatically.\n"
                "→ DO NOT ask user to clarify '南京南还是南京站' unless ambiguity breaks the query.\n"
                "→ 如果用户希望核验某车次的余票，先检查 memory / facts / path_detail 是否已经给出了 route。\n"
                "→ 若 route 已可确定：返回 need_more_facts，请求 left_ticket_s2s 对应的 DEP-ARR。\n"
                "→ 若 route 仍无法确定：才返回 need_user_input，并明确说明还缺'这趟车对应的出发地-目的地'；绝不能说系统无法调用12306。\n"

                "Forbidden examples:\n"
                "❌ 任意出发地-北京\n"
                "❌ 您的出发车站-北京\n"
                "❌ to_beijing\n\n"

                "Correct example:\n"
                "{\n"
                "  \"type\": \"need_user_input\",\n"
                "  \"question\": \"我可以推荐去北京的好车次～请问你从哪个城市或车站出发？\"\n"
                "}\n"
                "另外，如果用户明显是开玩笑，那就直接need_user_input，并且question用滑稽话语构造"
            )
        })

        raw = self.llm.generate(messages)

        # 🧯 第一层防御：JSON 解析
        try:
            result = loads_llm_json(raw)
        except Exception:
            # JSON 崩了 → 直接 final（不重试，防死循环）
            return {
                "type": "final",
                "content": raw
            }

        # 🧯 第二层防御：必须是 dict
        if not isinstance(result, dict):
            return {
                "type": "final",
                "content": str(result)
            }

        rtype = result.get("type")

        # ======================================================
        # 🆕 第三态补丁：need_user_input（缺槽位专用）
        # ======================================================
        if rtype == "need_user_input":
            question = result.get("question")

            if not question or not isinstance(question, str):
                question = "我可以马上帮你查～请先补充一下出发站和到达站😊"

            if session:
                session.enter_followup(
                    question=question,
                    slots={"reason": "missing_key_slots"}
                )

            return {
                "type": "need_user_input",
                "question": question
            }

        # ======================================================
        # 🧯 第三层防御：final 输出安全
        # ======================================================
        if rtype == "final":
            content = result.get("content", "")
            if session and session.in_followup():
                session.exit_followup()

            # 防止模型在 final 里夹 extra_request
            if "need_more_facts" in content or "extra_request" in content:
                return {
                    "type": "final",
                    "content": "我还需要你补充一个关键信息，比如出发城市～"
                }

            # 防止丧气话（软兜底）
            if "无法" in content or "不能" in content:
                content = (
                    "我可以帮你推荐最合适的车次～"
                    "请先告诉我你从哪个城市或车站出发？😊"
                )

            return {
                "type": "final",
                "content": content
            }

        # ======================================================
        # 🧯 第四层防御：need_more_facts 必须完整
        # ======================================================
        if rtype == "need_more_facts":
            extra = self._validated_extra_request(result.get("extra_request"))

            # missing 必须非空
            if not extra or not extra.get("missing"):
                return {
                    "type": "final",
                    "content": "我还需要你补充一下出发地或日期，才能继续帮你查车次～"
                }

            # 🚫 防漂移：禁止 placeholder id
            bad_ids = {"任意出发地-北京", "您的出发车站-北京", "to_beijing", "出发地-北京"}

            for item in extra["missing"]:
                if item.get("id") in bad_ids:
                    return {
                        "type": "need_user_input",
                        "question": "我可以帮你查去北京的车次～请先告诉我你从哪个城市出发？"
                    }

            return {
                "type": "need_more_facts",
                "extra_request": extra
            }

        # ======================================================
        # 🧯 最终兜底：未知 type
        # ======================================================
        return {
            "type": "final",
            "content": "我可以继续帮你规划～请补充一下具体想咨询的信息😊"+result.get("content", "")
        }

    def force_finalize(
        self,
        user_text: str,
        facts: Dict[str, Any],
        session=None,
        context_bundle: Optional[Dict[str, Any]] = None,
    ) -> str:
        """
        在多轮失败 / 达到最大轮次后，强制生成一个回答
        """

        empty_query_answer = self._build_empty_query_answer(user_text, facts)
        if empty_query_answer:
            return empty_query_answer

        messages = self.build_messages(
            user_text=user_text,
            facts=facts,
            session=session,
            style="final",
            length="medium",
            context_bundle=context_bundle,
        )

        messages.append({
            "role": "system",
            "content": (
                "You MUST provide a helpful answer based on available facts.\n"
                "If facts are incomplete, state assumptions clearly.\n"
                "DO NOT ask follow-up questions.\n"
                "DO NOT return JSON.\n"
            )
        })
        answer = str(self.final_llm.generate(messages) or "").strip()
        if answer and not answer.startswith("{"):
            return answer

        retry_messages = list(messages)
        retry_messages.append({
            "role": "system",
            "content": (
                "Retry once more.\n"
                "Return a concise natural-language railway answer only.\n"
                "Do not return blank output.\n"
                "Do not return JSON.\n"
            ),
        })
        retry_answer = str(self.final_llm.generate(retry_messages) or "").strip()
        if retry_answer:
            return retry_answer
        return "我先基于当前已经拿到的事实，给你一个尽量可靠的结论。"

    def stream_final_answer(self, text: str):
        """
        final答案专用：流式输出old，fake
        """
        stream_print(text, delay=0.01)

    def stream_answer(self, user_text, facts, session=None, context_bundle: Optional[Dict[str, Any]] = None):
        messages = self.build_messages(user_text, facts, session, context_bundle=context_bundle)
        return self._normalized_stream(self.final_llm.stream_generate(messages))
