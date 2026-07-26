import json
import re
from datetime import datetime
from typing import Any, Dict, List, Optional

from llm.llm_client import LLMClient
from llm.json_utils import loads_llm_json
from agent.capabilities import REGISTRY_VERSION, capability_catalog_for_mode, routable_capability_objects
from memory.session import SessionMemory


class FastPlusContextAgent:
    """
    A heavy but stable pre-routing context shaper.

    It is used in fast modes to turn vague follow-ups into
    a more self-contained intent payload before expert routing runs.
    """

    def __init__(self, llm: Optional[LLMClient] = None):
        self.llm = llm or LLMClient(mode="fast-plus")
        self.mode_profile = "fast-plus"
        self.request_timeout_s = 18.0
        self._cache: Dict[str, Dict[str, Any]] = {}

    def set_mode_profile(self, mode: str):
        normalized = str(mode or "").strip().lower()
        if normalized in {"fast", "fastgo"}:
            normalized = "fast-go"
        elif normalized == "fastplus":
            normalized = "fast-plus"
        if normalized in {"fast-go", "fast-plus", "deep"}:
            self.mode_profile = normalized
            if hasattr(self.llm, "set_mode"):
                self.llm.set_mode(normalized)

    def prepare(self, user_text: str, session: Optional[SessionMemory] = None) -> Dict[str, Any]:
        cache_key = self._build_cache_key(user_text=user_text, session=session)
        cached = self._cache.get(cache_key)
        if cached:
            return dict(cached)

        heuristic = self._heuristic_result(user_text=user_text, session=session)
        messages = self._build_messages(user_text=user_text, session=session)

        try:
            raw = self.llm.generate(
                messages,
                timeout=self.request_timeout_s,
                max_retries=0,
            )
            parsed = self._parse_result(raw=raw, user_text=user_text)
        except Exception as exc:
            parsed = {
                "intent_category": "unknown",
                "rewritten_user_text": str(user_text or "").strip(),
                "resolved_route": "",
                "resolved_train_numbers": [],
                "resolved_emu": "",
                "resolved_date": "",
                "resolved_station_mentions": [],
                "resolved_query_object": "",
                "confidence": 0,
                "reason": f"context agent timeout/failure: {exc}",
            }

        final_result = parsed
        if not self._is_usable_llm_result(parsed) and int(parsed.get("confidence") or 0) < int(heuristic.get("confidence") or 0):
            final_result = heuristic

        self._cache[cache_key] = dict(final_result)
        if len(self._cache) > 128:
            oldest_key = next(iter(self._cache))
            self._cache.pop(oldest_key, None)
        return dict(final_result)

    def _is_usable_llm_result(self, result: Dict[str, Any]) -> bool:
        if not isinstance(result, dict):
            return False

        intent = str(result.get("intent_category") or "").strip().lower()
        if not intent or intent == "unknown":
            return False

        try:
            confidence = int(result.get("confidence") or 0)
        except Exception:
            confidence = 0

        return confidence >= 50

    def _build_messages(self, user_text: str, session: Optional[SessionMemory]) -> List[Dict[str, str]]:
        capability_hints = capability_catalog_for_mode(self.mode_profile)
        messages: List[Dict[str, str]] = [
            {
                "role": "system",
                "content": (
                    "You are RailGPT FAST Context Agent.\n"
                    "Resolve omitted references in the latest user turn for downstream agents.\n"
                    "Use conversation context only to resolve omitted references such as this route, these trains, that date, or recommended options.\n"
                    "Do not invent trains, stations, routes, or dates that are not grounded in the context snapshot.\n"
                    "The latest user turn is authoritative. If it explicitly names a date, preserve that date in rewritten_user_text and resolved_date; context dates are only fallback when the latest turn omits a date.\n"
                    "Never replace an explicit user date with today, current date, or any date from memory.\n"
                    "Prefer concise normalization over long reasoning.\n"
                    "If the user is making a social or conversational follow-up, set intent_category=chat.\n"
                    "If the user is continuing a railway question, rewrite it into a standalone railway request while preserving the user's meaning.\n"
                    "When the latest turn gives alternative origins or destinations with words such as 或/或者, they are alternatives, not an origin-destination pair. Preserve the known opposite endpoint from the recent dialogue and never rewrite 厦门北或者厦门出发 as 厦门北到厦门.\n"
                    "When the previous assistant explicitly offered to run a concrete query and the latest user confirms or urges it (for example 是的、需要、快点查), rewrite the confirmation as that concrete query using only the OD/train/date stated in the recent dialogue.\n"
                    "When the previous assistant already supplied a concrete timetable, ticket list, route, or assignment result and the latest user only asks for recommendation, explanation, expansion, comparison, or creative writing from those results, classify it as chat unless fresh dynamic evidence is explicitly requested.\n"
                    "resolved_query_object is only a non-authoritative hint. The Semantic Router Council selects the actual capability.\n"
                    "When you provide resolved_query_object, it must be a discovered capability name from the registry below; otherwise leave it empty.\n"
                    "Do not turn a social, meta, knowledge, travel, or creative follow-up into an OD lookup.\n"
                    f"{capability_hints}\n"
                    "Return JSON only with this schema:\n"
                    "{"
                    "\"intent_category\":\"concise semantic intent label or unknown\","
                    "\"rewritten_user_text\":\"...\","
                    "\"resolved_route\":\"\","
                    "\"resolved_train_numbers\":[],"
                    "\"resolved_emu\":\"\","
                    "\"resolved_date\":\"\","
                    "\"resolved_station_mentions\":[],"
                    "\"resolved_query_object\":\"\","
                    "\"confidence\":0,"
                    "\"reason\":\"...\""
                    "}"
                ),
            }
        ]

        if session:
            if session.in_followup():
                messages.append(
                    {
                        "role": "system",
                        "content": (
                            "A previous clarification is still active.\n"
                            "If the new user message clearly continues that railway task, preserve that continuity.\n"
                            f"{session.get_followup_context()}"
                        ),
                    }
                )

            if hasattr(session, "build_agent_context_view"):
                context_view = session.build_agent_context_view(
                    role="context_resolver",
                    mode=self.mode_profile,
                    user_text=user_text,
                    include_dialogue=False,
                )
                messages.append(
                    {
                        "role": "system",
                        "content": (
                            "Role-scoped AgentContextPackage for reference resolution:\n"
                            f"{json.dumps(context_view, ensure_ascii=False)}"
                        ),
                    }
                )

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

        messages.append(
            {
                "role": "system",
                "content": (
                    "Latest user turn to normalize, verbatim:\n"
                    f"{str(user_text or '').strip()}\n"
                    "When this text contains an explicit date, that date has priority over all context snapshot dates."
                ),
            }
        )

        messages.append({"role": "user", "content": user_text})
        return messages

    def _build_cache_key(self, user_text: str, session: Optional[SessionMemory]) -> str:
        anchors = session.get_anchor_snapshot() if session else {}
        recent_pool = session.get_recent_entity_pool(max_items=4) if session else {}
        context_fingerprint = ""
        if session and hasattr(session, "build_agent_context_package"):
            context_fingerprint = str(
                session.build_agent_context_package(
                    mode=self.mode_profile,
                    user_text=user_text,
                ).get("context_fingerprint")
                or ""
            )
        payload = {
            "user_text": str(user_text or "").strip(),
            "anchors": anchors,
            "recent_pool": recent_pool,
            "followup": session.get_followup_context() if session and session.in_followup() else "",
            "context_fingerprint": context_fingerprint,
            "mode": self.mode_profile,
            "registry_version": REGISTRY_VERSION,
        }
        return json.dumps(payload, ensure_ascii=False, sort_keys=True)

    def _build_session_snapshot(self, session: SessionMemory, user_text: str) -> str:
        lines: List[str] = []
        recall = getattr(session, "memory_recall", {}) or {}
        package = recall.get("memory_context_package", {}) if isinstance(recall, dict) else {}
        if isinstance(package, dict) and package:
            hard_anchors = package.get("hard_anchors", {})
            if isinstance(hard_anchors, dict) and hard_anchors:
                lines.append(
                    "memory_hard_anchors="
                    + ", ".join(f"{key}={value}" for key, value in hard_anchors.items() if value)
                )
            soft_context = package.get("soft_context", [])
            if isinstance(soft_context, list) and soft_context:
                summaries = []
                for item in soft_context[:3]:
                    if not isinstance(item, dict):
                        continue
                    source = str(item.get("source") or item.get("kind") or "memory")
                    text = str(item.get("summary_l0") or "").strip()
                    if text:
                        summaries.append(f"{source}:{text[:120]}")
                if summaries:
                    lines.append("memory_soft_context=" + " | ".join(summaries))

        anchors = session.get_anchor_snapshot()
        if anchors:
            lines.append("anchors=" + ", ".join(f"{k}={v}" for k, v in anchors.items()))

        recent_pool = session.get_recent_entity_pool(max_items=4)
        for key in ("trains", "routes", "dates", "stations", "objects"):
            values = recent_pool.get(key) or []
            if values:
                lines.append(f"recent_{key}=" + ", ".join(values[:4]))

        if session.in_followup():
            lines.append("followup_active=true")
            followup_context = session.get_followup_context()
            if followup_context:
                compact_followup = " ".join(followup_context.split())
                if len(compact_followup) > 220:
                    compact_followup = compact_followup[:220] + "..."
                lines.append("followup_context=" + compact_followup)

        recent_messages = list(session.get_recent_messages(2))
        if recent_messages and recent_messages[-1].get("role") == "user" and recent_messages[-1].get("content") == user_text:
            recent_messages = recent_messages[:-1]
        for idx, message in enumerate(recent_messages[-2:], start=1):
            role = str(message.get("role") or "")
            content = " ".join(str(message.get("content") or "").split())
            if len(content) > 140:
                content = content[:140] + "..."
            if role and content:
                lines.append(f"recent_message_{idx}={role}:{content}")

        return "\n".join(lines[:10])

    def _heuristic_result(self, user_text: str, session: Optional[SessionMemory]) -> Dict[str, Any]:
        text = str(user_text or "").strip()
        compact = re.sub(r"\s+", "", text)
        upper = text.upper()
        lower = text.lower()
        trains = self._extract_train_numbers(upper)
        anchors = session.get_anchor_snapshot() if session else {}
        recent_pool = session.get_recent_entity_pool(max_items=4) if session else {}
        route = str(anchors.get("route") or "")
        allow_heuristic_date = self.mode_profile == "fast-go"
        explicit_date = self._extract_explicit_date_string(text) if allow_heuristic_date else ""
        date = (explicit_date or str(anchors.get("date") or "")) if allow_heuristic_date else ""

        def result(
            intent_category: str,
            rewritten_user_text: str,
            resolved_query_object: str = "",
            confidence: int = 78,
            reason: str = "",
            resolved_route: str = "",
            resolved_train_numbers: Optional[List[str]] = None,
            resolved_date: str = "",
        ) -> Dict[str, Any]:
            return {
                "intent_category": intent_category,
                "rewritten_user_text": rewritten_user_text,
                "resolved_route": resolved_route,
                "resolved_train_numbers": resolved_train_numbers or [],
                "resolved_emu": "",
                "resolved_date": resolved_date,
                "resolved_station_mentions": [],
                "resolved_query_object": resolved_query_object,
                "confidence": confidence,
                "reason": reason or "heuristic context-agent fallback",
            }

        if any(token in compact for token in ("你是谁", "你能做什么", "你好", "谢谢", "这是什么", "这么厉害")):
            return result(
                "chat",
                text,
                confidence=88,
                reason="heuristic social/chat follow-up",
            )

        rail_knowledge_tokens = (
            "什么是",
            "什么叫",
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
            "技术短板",
            "主要区别",
            "如何保证",
        )
        rail_scope_tokens = (
            "高铁",
            "动车",
            "列车",
            "车次",
            "铁路",
            "铁路局",
            "车迷",
            "站台",
            "候补",
            "调图",
            "标杆车",
            "天窗",
            "限速",
            "CTCS",
            "无砟轨道",
            "接触网",
            "受电弓",
            "转向架",
            "重联",
            "滚动轴承",
            "信号机",
            "刷绿",
            "豹子号",
            "绿巨人",
            "大地铁",
            "亚洲东",
            "亚洲北",
            "运转",
            "本务",
            "大车",
            "桶",
            "老鼠",
        )
        if (
            any(token in compact for token in rail_knowledge_tokens)
            and (
                trains
                or route
                or any(token in compact for token in rail_scope_tokens)
            )
        ):
            return result(
                "general_rail",
                text,
                resolved_query_object="general_rail",
                confidence=86,
                reason="heuristic general railway knowledge intent",
                resolved_route=route,
                resolved_train_numbers=trains[:5],
                resolved_date=date,
            )

        if trains and any(token in compact for token in ("车底", "担当", "车型", "车组", "动车组", "使用情况", "智能动车")):
            train_id = ",".join(trains[:5])
            rewritten = text
            return result(
                "train_assignment",
                rewritten,
                resolved_query_object="train",
                confidence=90,
                reason="heuristic explicit-train assignment intent",
                resolved_train_numbers=trains[:5],
                resolved_date=date,
            )

        if trains and any(token in compact for token in ("经停", "停站", "时刻", "路线", "走哪条线", "从哪里开", "开往哪里")):
            rewritten = text
            return result(
                "train_path",
                rewritten,
                resolved_query_object="path_detail",
                confidence=90,
                reason="heuristic explicit-train path intent",
                resolved_train_numbers=trains[:5],
                resolved_date=date,
            )

        if trains and (
            any(token in compact for token in ("介绍一下", "详细介绍", "全面解析", "全面分析", "展开说说", "详情", "全部信息", "所有信息"))
            or any(token in lower for token in ("everything about", "all about", "tell me about", "overview of", "details about"))
        ):
            rewritten = f"请全面介绍{trains[0]}次列车，包括其运行路径、时刻、经停和基本车型信息。"
            return result(
                "train_overview",
                rewritten,
                resolved_query_object="train",
                confidence=92,
                reason="heuristic explicit-train overview intent",
                resolved_train_numbers=trains[:5],
                resolved_date=date,
            )

        if route and any(token in compact for token in ("余票", "有票", "票吗", "票务")):
            return result(
                "route_ticket",
                text,
                resolved_query_object="left_ticket_s2s",
                confidence=84,
                reason="heuristic route ticket follow-up",
                resolved_route=route,
                resolved_date=date,
            )

        if route and any(token in compact for token in ("最便宜", "便宜", "最低价", "最省钱", "价格最低", "最低票价", "低价", "省钱")):
            return result(
                "route_ticket",
                text,
                resolved_query_object="left_ticket_s2s",
                confidence=82,
                reason="heuristic cheapest ticket follow-up",
                resolved_route=route,
                resolved_date=date,
            )

        if route and any(token in compact for token in ("最快", "标杆", "直达", "推荐")):
            return result(
                "route_benchmark",
                text,
                resolved_query_object="s2s_benchmark",
                confidence=82,
                reason="heuristic route benchmark follow-up",
                resolved_route=route,
                resolved_date=date,
            )

        if any(token in compact for token in ("这趟车", "这班车", "这列车", "它")) and recent_pool.get("trains"):
            train_id = recent_pool["trains"][0]
            rewritten = f"请继续查询{train_id}次列车的相关信息。"
            return result(
                "train_overview",
                rewritten,
                resolved_query_object="train",
                confidence=76,
                reason="heuristic contextual train follow-up",
                resolved_train_numbers=[train_id],
                resolved_date=date,
            )

        return {
            "intent_category": "unknown",
            "rewritten_user_text": text,
            "resolved_route": route,
            "resolved_train_numbers": trains[:5],
            "resolved_emu": "",
            "resolved_date": date,
            "resolved_station_mentions": [],
            "resolved_query_object": "",
            "confidence": 20 if trains or route else 0,
            "reason": "heuristic fallback could not confidently normalize the turn",
        }

    def _extract_train_numbers(self, text: str) -> List[str]:
        seen: List[str] = []
        for item in re.findall(r"[GDKTZC]\d{1,5}", str(text or "")):
            if item not in seen:
                seen.append(item)
        return seen

    def _extract_explicit_date_string(self, text: str) -> str:
        value = str(text or "")
        for pattern in (
            r"(\d{4})[-/年.](\d{1,2})[-/月.](\d{1,2})(?:日|号)?",
            r"(?<!\d)(\d{8})(?!\d)",
            r"(?<!\d)(\d{1,2})月(\d{1,2})(?:日|号)?",
            r"(?<!\d)(\d{1,2})[./](\d{1,2})(?:日|号)?(?!\d)",
        ):
            match = re.search(pattern, value)
            if not match:
                continue
            try:
                if len(match.groups()) == 1:
                    raw = match.group(1)
                    return f"{raw[:4]}-{raw[4:6]}-{raw[6:]}"
                if len(match.groups()) == 3:
                    year, month, day = map(int, match.groups())
                else:
                    now_year = datetime.now().year
                    month, day = map(int, match.groups())
                    year = now_year
                return f"{year:04d}-{month:02d}-{day:02d}"
            except Exception:
                continue
        return ""

    def _parse_result(self, raw: str, user_text: str) -> Dict[str, Any]:
        fallback = {
            "intent_category": "unknown",
            "rewritten_user_text": str(user_text or "").strip(),
            "resolved_route": "",
            "resolved_train_numbers": [],
            "resolved_emu": "",
            "resolved_date": "",
            "resolved_station_mentions": [],
            "resolved_query_object": "",
            "confidence": 0,
            "reason": "context agent unavailable",
        }

        try:
            data = loads_llm_json(raw)
        except Exception:
            return fallback

        if not isinstance(data, dict):
            return fallback

        def _normalize_string(value: Any) -> str:
            return str(value or "").strip()

        def _normalize_list(value: Any) -> List[str]:
            if not isinstance(value, list):
                return []
            result: List[str] = []
            for item in value:
                normalized = _normalize_string(item)
                if normalized and normalized not in result:
                    result.append(normalized)
            return result

        confidence = data.get("confidence", 0)
        try:
            confidence = int(confidence)
        except Exception:
            confidence = 0
        confidence = max(0, min(100, confidence))

        parsed = {
            "intent_category": _normalize_string(data.get("intent_category")) or "unknown",
            "rewritten_user_text": _normalize_string(data.get("rewritten_user_text")) or str(user_text or "").strip(),
            "resolved_route": _normalize_string(data.get("resolved_route")),
            "resolved_train_numbers": _normalize_list(data.get("resolved_train_numbers")),
            "resolved_emu": _normalize_string(data.get("resolved_emu")),
            "resolved_date": _normalize_string(data.get("resolved_date")),
            "resolved_station_mentions": _normalize_list(data.get("resolved_station_mentions")),
            "resolved_query_object": _normalize_string(data.get("resolved_query_object")),
            "confidence": confidence,
            "reason": _normalize_string(data.get("reason")),
        }

        if parsed["resolved_query_object"] not in routable_capability_objects():
            parsed["resolved_query_object"] = ""

        return parsed
