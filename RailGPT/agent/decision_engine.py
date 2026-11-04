# agent/decision_engine.py

import json
from typing import Dict, Any, Optional
from memory.session import SessionMemory


class DecisionEngine:
    """
    DecisionEngine v1.0

    Responsibilities:
    - Parse LLM JSON safely
    - Validate protocol structure
    - Manage followup lifecycle
    - Return normalized decision dict

    NEVER:
    - Build prompts
    - Call LLM
    - Know about Planner or Executor
    """

    def decide(
        self,
        raw_output: str,
        session: Optional[SessionMemory] = None,
    ) -> Dict[str, Any]:

        # =====================================================
        # 🧯 第一层：JSON 解析防御
        # =====================================================
        try:
            result = json.loads(raw_output)
        except Exception:
            return self._final(raw_output, session)

        if not isinstance(result, dict):
            return self._final(str(result), session)

        rtype = result.get("type")

        # =====================================================
        # 🟢 FINAL
        # =====================================================
        if rtype == "final":
            content = result.get("content", "")
            return self._handle_final(content, session)

        # =====================================================
        # 🟡 NEED USER INPUT
        # =====================================================
        if rtype == "need_user_input":
            question = result.get("question")
            return self._handle_need_user_input(question, session)

        # =====================================================
        # 🔵 NEED MORE FACTS
        # =====================================================
        if rtype == "need_more_facts":
            extra = result.get("extra_request")
            return self._handle_need_more_facts(extra)

        # =====================================================
        # 🧯 未知类型 → 直接 final
        # =====================================================
        return self._final(raw_output, session)

    # =========================================================
    # ================= INTERNAL HANDLERS =====================
    # =========================================================

    def _handle_final(
        self,
        content: str,
        session: Optional[SessionMemory],
    ) -> Dict[str, Any]:

        if not content or not isinstance(content, str):
            content = "我可以继续帮你规划～请补充一下具体想咨询的信息😊"

        # 退出 followup
        if session and session.in_followup():
            session.exit_followup()

        return {
            "type": "final",
            "content": content
        }

    def _handle_need_user_input(
        self,
        question: Optional[str],
        session: Optional[SessionMemory],
    ) -> Dict[str, Any]:

        if not question or not isinstance(question, str):
            question = "我可以马上帮你查～请补充一下关键信息😊"

        # 🔥 统一 followup 生命周期入口
        if session:
            session.enter_followup(
                question=question,
                slot=[],
                context={}
            )

        return {
            "type": "need_user_input",
            "question": question
        }

    def _handle_need_more_facts(
        self,
        extra: Optional[Dict[str, Any]],
    ) -> Dict[str, Any]:

        if not extra or not isinstance(extra, dict):
            return {
                "type": "final",
                "content": "我还需要更多信息才能继续帮你分析～"
            }

        missing = extra.get("missing")

        if not missing or not isinstance(missing, list):
            return {
                "type": "final",
                "content": "我还需要补充一些查询数据才能继续～"
            }

        # 基础结构校验
        cleaned = []
        for item in missing:
            if not isinstance(item, dict):
                continue

            domain = item.get("domain")
            obj = item.get("object")
            obj_id = item.get("id")
            date = item.get("date")

            if not domain or not obj or not obj_id:
                continue

            entry = {
                "domain": domain,
                "object": obj,
                "id": obj_id
            }

            if date:
                entry["date"] = date

            cleaned.append(entry)

        if not cleaned:
            return {
                "type": "final",
                "content": "当前查询数据不足，请补充必要条件～"
            }

        return {
            "type": "need_more_facts",
            "extra_request": {
                "missing": cleaned
            }
        }

    def _final(
        self,
        content: str,
        session: Optional[SessionMemory],
    ) -> Dict[str, Any]:

        # 统一 final 处理
        if session and session.in_followup():
            session.exit_followup()

        return {
            "type": "final",
            "content": content
        }