# agent/ag_prompt_builder.py

import json
from typing import Dict, Any, List, Optional
from datetime import datetime
from memory.session import SessionMemory


class AGPromptBuilder:
    """
    AGPromptBuilder v1.0

    ONLY responsible for building LLM messages.
    - No JSON parsing
    - No decision logic
    - No session state mutation
    """

    # =========================================================
    # 🟢 公共入口：普通回答模式
    # =========================================================
    def build_final_messages(
        self,
        user_text: str,
        facts: Dict[str, Any],
        session: Optional[SessionMemory],
        style: str = "detailed",
        length: str = "medium",
    ) -> List[Dict[str, str]]:

        messages: List[Dict[str, str]] = []

        messages.extend(self._build_base_system())
        messages.extend(self._inject_session_context(session))
        messages.append(self._build_user(user_text))
        messages.append(self._build_facts_block(facts))
        messages.append(self._build_style_block(style, length))

        return messages

    # =========================================================
    # 🟡 分析模式（结构化输出）
    # =========================================================
    def build_analysis_messages(
        self,
        user_text: str,
        facts: Dict[str, Any],
        session: Optional[SessionMemory],
    ) -> List[Dict[str, str]]:

        messages: List[Dict[str, str]] = []

        messages.extend(self._build_base_system())
        messages.extend(self._build_tool_map())
        messages.extend(self._inject_session_context(session))

        messages.append(self._build_user(user_text))
        messages.append(self._build_facts_block(facts))
        messages.extend(self._build_protocol_block())

        return messages

    # =========================================================
    # 🔵 强制终结模式
    # =========================================================
    def build_force_finalize_messages(
        self,
        user_text: str,
        facts: Dict[str, Any],
        session: Optional[SessionMemory],
    ) -> List[Dict[str, str]]:

        messages = self.build_final_messages(
            user_text=user_text,
            facts=facts,
            session=session,
            style="final",
            length="medium",
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

        return messages

    # =========================================================
    # ================= PRIVATE BLOCKS ========================
    # =========================================================

    def _build_base_system(self) -> List[Dict[str, str]]:

        blocks: List[Dict[str, str]] = []

        # 主身份
        blocks.append({
            "role": "system",
            "content": (
                "You are RailGo AnswerGenerator v4.0.\n"
                "You are a knowledgeable and friendly railway enthusiast assistant.\n\n"
                "Your job:\n"
                "- Convert tool-returned factual JSON into natural railway answers.\n"
                "- Summarize patterns (benchmark, stability, EMU usage).\n\n"
                "Critical rules:\n"
                "- NEVER fabricate facts not present in JSON.\n"
                "- City names are acceptable station identifiers.\n"
                "- Do NOT over-ask for station suffix.\n"
                "- Match user's language automatically."
            )
        })

        # 智能动车规则
        blocks.append({
            "role": "system",
            "content": (
                "Smart EMU Knowledge Patch:\n"
                "- CR400AF-Z / CR400BF-Z = Intelligent flagship (8-car)\n"
                "- CR400AF-BZ / CR400BF-BZ = Intelligent long (17-car)\n"
                "- CR400AF-BS / CR400BF-BS = Premium intelligent long\n"
                "- CR400AFS / CR400BFS = ALSO intelligent\n"
                "BFS/AFS MUST be treated as intelligent EMU."
            )
        })

        return blocks

    def _inject_session_context(
        self,
        session: Optional[SessionMemory]
    ) -> List[Dict[str, str]]:

        blocks: List[Dict[str, str]] = []

        if not session:
            return blocks

        # 最近历史 / mode-aware context budget
        if hasattr(session, "build_llm_history"):
            blocks.extend(session.build_llm_history(mode="deep", exclude_current_user=True))
        else:
            for m in session.get_recent_messages():
                blocks.append(m)

        # Follow-up 模式提示（只注入，不修改状态）
        if session.in_followup():
            blocks.append({
                "role": "system",
                "content": (
                    "FOLLOW-UP MODE ACTIVE.\n"
                    "User is replying to previous clarification.\n"
                    "Fill missing slots instead of treating as new query.\n\n"
                    f"{session.get_followup_context()}"
                )
            })

        return blocks

    def _build_user(self, user_text: str) -> Dict[str, str]:
        return {
            "role": "user",
            "content": user_text
        }

    def _build_facts_block(self, facts: Dict[str, Any]) -> Dict[str, str]:

        facts_text = json.dumps(facts, ensure_ascii=False, indent=2)

        return {
            "role": "system",
            "content": (
                "All records are from latest timetable + 30-day assignments.\n"
                f"Current Beijing Time: {datetime.now().strftime('%Y-%m-%d %H:%M')}\n\n"
                "Complete factual JSON:\n"
                + facts_text
            )
        }

    def _build_style_block(self, style: str, length: str) -> Dict[str, str]:
        return {
            "role": "system",
            "content": (
                f"Answer style: {style}. Length: {length}.\n"
                "Do NOT mention tool names or disclaimers."
            )
        }

    def _build_tool_map(self) -> List[Dict[str, str]]:

        return [{
            "role": "system",
            "content": (
                "You MUST respond in JSON ONLY.\n\n"
                "Output types:\n"
                "1) {type:'final', content:'...'}\n"
                "2) {type:'need_user_input', question:'...'}\n"
                "3) {type:'need_more_facts', extra_request:{missing:[...]}}\n\n"
                "No code blocks. No null."
            )
        }]

    def _build_protocol_block(self) -> List[Dict[str, str]]:

        return [{
            "role": "system",
            "content": (
                "If facts sufficient → return final.\n"
                "If slots missing → return need_user_input.\n"
                "If more tools required → return need_more_facts.\n"
            )
        }]
