from __future__ import annotations

import json
import re
from datetime import datetime, timedelta
from typing import Any, Dict, Optional

from llm.llm_client import LLMClient
from llm.json_utils import loads_llm_json
from memory.session import SessionMemory


DATE_SOURCE_NONE = "none"


class DateNormalizerAgent:
    """
    Narrow date-resolution microagent.

    The LLM handles ambiguous/contextual language; code only validates the
    result and keeps a small deterministic guard for obvious dates so fast-go
    does not pay an extra LLM round for every normal query.
    """

    def __init__(self, llm: Optional[LLMClient] = None):
        self.llm = llm or LLMClient(mode="fast-go")
        self._cache: Dict[str, Dict[str, Any]] = {}

    def normalize(
        self,
        *,
        latest_user_text: str,
        rewritten_user_text: str = "",
        session: Optional[SessionMemory] = None,
        mode: str = "fast-go",
        current_date: str = "",
    ) -> Dict[str, Any]:
        current_date = current_date or datetime.now().strftime("%Y-%m-%d")
        latest_user_text = str(latest_user_text or "").strip()
        rewritten_user_text = str(rewritten_user_text or "").strip()

        cache_key = json.dumps(
            {
                "latest": latest_user_text,
                "rewritten": rewritten_user_text,
                "mode": mode,
                "current_date": current_date,
                "anchor_date": session.resolve_anchor("date") if session else "",
            },
            ensure_ascii=False,
            sort_keys=True,
        )
        cached = self._cache.get(cache_key)
        if cached:
            return dict(cached)

        llm_only = self._requires_llm_only(mode)
        fallback = self._empty(reason="high-mode date resolution is LLM-only")
        if not llm_only:
            fallback = self._fallback_resolution(
                latest_user_text=latest_user_text,
                rewritten_user_text=rewritten_user_text,
                session=session,
                current_date=current_date,
            )
            if fallback.get("has_date"):
                self._remember(cache_key, fallback)
                return dict(fallback)

            if not self._should_call_llm(latest_user_text, rewritten_user_text):
                self._remember(cache_key, fallback)
                return dict(fallback)
        elif not self._may_need_llm_date(latest_user_text, rewritten_user_text, session):
            self._remember(cache_key, fallback)
            return dict(fallback)

        try:
            raw = self.llm.generate(
                self._build_messages(
                    latest_user_text=latest_user_text,
                    rewritten_user_text=rewritten_user_text,
                    session=session,
                    mode=mode,
                    current_date=current_date,
                ),
                timeout=6.0 if mode == "fast-go" else 10.0,
                max_retries=0,
            )
            parsed = self._parse_result(raw)
        except Exception as exc:
            parsed = self._empty(reason=f"date normalizer unavailable: {exc}")

        accepted = self._validate_llm_result(parsed)
        if not accepted.get("has_date") and not llm_only:
            accepted = fallback

        self._remember(cache_key, accepted)
        return dict(accepted)

    @staticmethod
    def _requires_llm_only(mode: str) -> bool:
        normalized = str(mode or "").strip().lower()
        if normalized in {"fast", "fastgo"}:
            normalized = "fast-go"
        elif normalized == "fastplus":
            normalized = "fast-plus"
        return normalized in {"fast-plus", "deep"}

    def _remember(self, key: str, value: Dict[str, Any]):
        self._cache[key] = dict(value)
        if len(self._cache) > 128:
            self._cache.pop(next(iter(self._cache)), None)

    def _build_messages(
        self,
        *,
        latest_user_text: str,
        rewritten_user_text: str,
        session: Optional[SessionMemory],
        mode: str,
        current_date: str,
    ) -> list[dict]:
        messages = [
            {
                "role": "system",
                "content": (
                    "You are RailGPT Date Normalizer.\n"
                    "Only resolve the intended query date. Do not route the railway task.\n"
                    "Priority:\n"
                    "1. Latest user explicit date wins over everything.\n"
                    "2. Relative date words use CURRENT_BEIJING_DATE.\n"
                    "3. Context dates may be used only when the latest user clearly refers to a previous date.\n"
                    "4. Never replace explicit user date with today.\n"
                    "Return JSON only with this schema:\n"
                    "{"
                    '"has_date":true,'
                    '"normalized_date":"YYYY-MM-DD",'
                    '"date_source":"latest_user|relative_user|conversation_context|none",'
                    '"date_span":"原文片段",'
                    '"is_contextual_date":false,'
                    '"confidence":0,'
                    '"reason":"short"'
                    "}"
                ),
            }
        ]

        if session and hasattr(session, "build_agent_context_view"):
            package = session.build_agent_context_view(
                role="date",
                mode=mode,
                user_text=latest_user_text,
                include_dialogue=True,
            )
            messages.append(
                {
                    "role": "system",
                    "content": "AgentContextPackage:\n" + json.dumps(package, ensure_ascii=False, indent=2),
                }
            )
        elif session:
            history = session.get_recent_messages(8)
            messages.extend(history)

        messages.append(
            {
                "role": "user",
                "content": (
                    f"CURRENT_BEIJING_DATE: {current_date}\n"
                    f"LATEST_USER_TEXT: {latest_user_text}\n"
                    f"CONTEXT_AGENT_REWRITE: {rewritten_user_text or '(none)'}"
                ),
            }
        )
        return messages

    def _fallback_resolution(
        self,
        *,
        latest_user_text: str,
        rewritten_user_text: str,
        session: Optional[SessionMemory],
        current_date: str,
    ) -> Dict[str, Any]:
        current = self._parse_date(current_date) or datetime.now()
        explicit = self._extract_absolute_date(latest_user_text, current)
        if explicit:
            return self._result(
                normalized_date=explicit[0].strftime("%Y-%m-%d"),
                date_source="latest_user",
                date_span=explicit[1],
                is_contextual_date=False,
                confidence=98,
                reason="explicit date in latest user text",
            )

        relative = self._extract_relative_date(latest_user_text, current)
        if relative:
            return self._result(
                normalized_date=relative[0].strftime("%Y-%m-%d"),
                date_source="relative_user",
                date_span=relative[1],
                is_contextual_date=False,
                confidence=95,
                reason="relative date in latest user text",
            )

        contextual_tokens = ("那天", "这天", "当天", "刚才那天", "刚刚那天", "上面那天", "前面那天", "同一天")
        if session and any(token in latest_user_text for token in contextual_tokens):
            anchor_date = session.resolve_anchor("date")
            if self._parse_date(anchor_date):
                return self._result(
                    normalized_date=anchor_date,
                    date_source="conversation_context",
                    date_span="contextual reference",
                    is_contextual_date=True,
                    confidence=86,
                    reason="contextual date reference resolved from session anchor",
                )

        rewrite_explicit = self._extract_absolute_date(rewritten_user_text, current)
        if rewrite_explicit:
            return self._result(
                normalized_date=rewrite_explicit[0].strftime("%Y-%m-%d"),
                date_source="rewrite",
                date_span=rewrite_explicit[1],
                is_contextual_date=True,
                confidence=72,
                reason="date found only in context-agent rewrite",
            )

        return self._empty(reason="no date expression found")

    def _should_call_llm(self, latest_user_text: str, rewritten_user_text: str) -> bool:
        text = f"{latest_user_text}\n{rewritten_user_text}"
        return any(
            token in text
            for token in (
                "那天",
                "这天",
                "当天",
                "之后",
                "以前",
                "以后",
                "五一",
                "清明",
                "端午",
                "中秋",
                "国庆",
                "春节",
                "刚才",
                "上面",
                "前面",
            )
        )

    def _may_need_llm_date(
        self,
        latest_user_text: str,
        rewritten_user_text: str,
        session: Optional[SessionMemory],
    ) -> bool:
        text = f"{latest_user_text}\n{rewritten_user_text}"
        if self._contains_date_surface(text) or self._should_call_llm(latest_user_text, rewritten_user_text):
            return True
        if session and session.resolve_anchor("date"):
            compact = re.sub(r"\s+", "", str(text or ""))
            return any(
                token in compact
                for token in (
                    "这趟",
                    "这班",
                    "这辆",
                    "这列",
                    "这些",
                    "它",
                    "他们",
                    "刚刚",
                    "刚才",
                    "上面",
                    "前面",
                    "是的",
                    "好的",
                    "ok",
                    "OK",
                    "标杆",
                    "最快",
                    "推荐",
                    "余票",
                    "有票",
                    "直达",
                )
            )
        return False

    @staticmethod
    def _contains_date_surface(text: str) -> bool:
        value = str(text or "")
        if re.search(r"\d{4}[-/.年]\d{1,2}[-/.月]\d{1,2}", value):
            return True
        if re.search(r"(?<!\d)\d{8}(?!\d)", value):
            return True
        if re.search(r"(?<!\d)\d{1,2}[./月]\d{1,2}(?:日|号)?", value):
            return True
        if re.search(r"[零〇一二两三四五六七八九十]{1,3}月[零〇一二两三四五六七八九十]{1,3}(?:日|号)", value):
            return True
        return any(token in value for token in ("今天", "明天", "后天", "昨天", "周", "星期", "礼拜"))

    def _validate_llm_result(self, data: Dict[str, Any]) -> Dict[str, Any]:
        if not isinstance(data, dict):
            return self._empty(reason="invalid date normalizer output")

        normalized_date = str(data.get("normalized_date") or "").strip()
        if not normalized_date or not self._parse_date(normalized_date):
            return self._empty(reason="date normalizer returned no valid date")

        source = str(data.get("date_source") or DATE_SOURCE_NONE).strip()
        if source not in {"latest_user", "relative_user", "conversation_context", "rewrite"}:
            return self._empty(reason="date normalizer returned unsupported source")

        try:
            confidence = int(float(data.get("confidence") or 0))
        except Exception:
            confidence = 0
        if confidence < 65:
            return self._empty(reason="date normalizer confidence below threshold")

        return self._result(
            normalized_date=normalized_date,
            date_source=source,
            date_span=str(data.get("date_span") or "").strip(),
            is_contextual_date=bool(data.get("is_contextual_date")),
            confidence=confidence,
            reason=str(data.get("reason") or "date normalizer accepted").strip(),
        )

    def _parse_result(self, raw: str) -> Dict[str, Any]:
        return loads_llm_json(raw)

    @staticmethod
    def _empty(reason: str = "") -> Dict[str, Any]:
        return {
            "has_date": False,
            "normalized_date": "",
            "date_source": DATE_SOURCE_NONE,
            "date_span": "",
            "is_contextual_date": False,
            "confidence": 0,
            "reason": reason,
        }

    @staticmethod
    def _result(
        *,
        normalized_date: str,
        date_source: str,
        date_span: str,
        is_contextual_date: bool,
        confidence: int,
        reason: str,
    ) -> Dict[str, Any]:
        return {
            "has_date": True,
            "normalized_date": normalized_date,
            "date_source": date_source,
            "date_span": date_span,
            "is_contextual_date": is_contextual_date,
            "confidence": confidence,
            "reason": reason,
        }

    @staticmethod
    def _parse_date(value: Any) -> Optional[datetime]:
        try:
            return datetime.strptime(str(value or "").strip(), "%Y-%m-%d")
        except Exception:
            return None

    def _extract_absolute_date(self, text: str, now: datetime) -> Optional[tuple[datetime, str]]:
        value = str(text or "")
        patterns = (
            r"(\d{4})[-/年.](\d{1,2})[-/月.](\d{1,2})(?:日|号)?",
            r"(?<!\d)(\d{8})(?!\d)",
            r"(?<!\d)(\d{1,2})月(\d{1,2})(?:日|号)?",
            r"(?<!\d)(\d{1,2})[./](\d{1,2})(?:日|号)?(?!\d)",
        )
        for index, pattern in enumerate(patterns):
            match = re.search(pattern, value)
            if not match:
                continue
            if index == 1:
                raw = match.group(1)
                parsed = self._safe_date(int(raw[:4]), int(raw[4:6]), int(raw[6:]))
            elif index == 0:
                parsed = self._safe_date(*map(int, match.groups()))
            else:
                month, day = map(int, match.groups())
                parsed = self._safe_date(now.year, month, day)
            if parsed:
                return parsed, match.group(0)

        chinese_match = re.search(
            r"([零〇一二两三四五六七八九十]{1,3})月([零〇一二两三四五六七八九十]{1,3})(?:日|号)",
            value,
        )
        if chinese_match:
            month = self._parse_chinese_number(chinese_match.group(1))
            day = self._parse_chinese_number(chinese_match.group(2))
            parsed = self._safe_date(now.year, month, day) if month and day else None
            if parsed:
                return parsed, chinese_match.group(0)
        return None

    @staticmethod
    def _parse_chinese_number(value: str) -> int:
        digits = {"零": 0, "〇": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
        text = str(value or "").strip()
        if not text:
            return 0
        if text == "十":
            return 10
        if "十" in text:
            left, right = text.split("十", 1)
            tens = digits.get(left, 1) if left else 1
            ones = digits.get(right, 0) if right else 0
            return tens * 10 + ones
        result = 0
        for char in text:
            if char not in digits:
                return 0
            result = result * 10 + digits[char]
        return result

    def _extract_relative_date(self, text: str, now: datetime) -> Optional[tuple[datetime, str]]:
        value = str(text or "")
        for token, offset in (
            ("今天", 0),
            ("今日", 0),
            ("明天", 1),
            ("后天", 2),
            ("昨天", -1),
        ):
            if token in value:
                return now + timedelta(days=offset), token

        weekday = self._extract_weekday_date(value, now)
        if weekday:
            return weekday
        return None

    def _extract_weekday_date(self, text: str, now: datetime) -> Optional[tuple[datetime, str]]:
        weekday_map = {"一": 0, "二": 1, "三": 2, "四": 3, "五": 4, "六": 5, "日": 6, "天": 6}
        week_start = now - timedelta(days=now.weekday())

        weekend_match = re.search(
            r"(这周末|本周末|这星期末|本星期末|这礼拜末|本礼拜末|下周末|下星期末|下礼拜末|周末)",
            text,
        )
        if weekend_match:
            token = weekend_match.group(1)
            week_offset = 1 if token.startswith("下") else 0
            return week_start + timedelta(days=5 + week_offset * 7), token

        explicit_match = re.search(
            r"(这周|本周|下周|这星期|本星期|下星期|这礼拜|本礼拜|下礼拜)([一二三四五六日天])",
            text,
        )
        if explicit_match:
            prefix, day_token = explicit_match.groups()
            week_offset = 1 if prefix.startswith("下") else 0
            target_index = weekday_map[day_token]
            return week_start + timedelta(days=target_index + week_offset * 7), explicit_match.group(0)

        bare_match = re.search(r"(?:周|星期|礼拜)([一二三四五六日天])", text)
        if bare_match:
            target_index = weekday_map[bare_match.group(1)]
            delta = (target_index - now.weekday()) % 7
            return now + timedelta(days=delta), bare_match.group(0)

        return None

    @staticmethod
    def _safe_date(year: int, month: int, day: int) -> Optional[datetime]:
        try:
            return datetime(year, month, day)
        except Exception:
            return None
