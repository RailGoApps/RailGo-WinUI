# agent/answer_engine.py

from typing import Dict, Any, Optional
from llm.llm_client import LLMClient
from memory.session import SessionMemory
from .ag_prompt_builder import AGPromptBuilder


class AnswerEngine:
    """
    AnswerEngine v1.0

    Responsibilities:
    - Build final answer prompts
    - Call LLM
    - Return natural language response

    NEVER:
    - Parse JSON
    - Decide need_more_facts
    - Manage followup lifecycle
    """

    def __init__(self, llm: LLMClient):
        self.llm = llm
        self.prompt_builder = AGPromptBuilder()

    # =========================================================
    # 🟢 普通自然语言回答
    # =========================================================
    def generate(
        self,
        user_text: str,
        facts: Dict[str, Any],
        session: Optional[SessionMemory] = None,
        style: str = "detailed",
        length: str = "medium",
    ) -> str:

        messages = self.prompt_builder.build_final_messages(
            user_text=user_text,
            facts=facts,
            session=session,
            style=style,
            length=length,
        )

        return self.llm.generate(messages)

    # =========================================================
    # 🔵 强制终结回答（用于多轮失败兜底）
    # =========================================================
    def force_finalize(
        self,
        user_text: str,
        facts: Dict[str, Any],
        session: Optional[SessionMemory] = None,
    ) -> str:

        messages = self.prompt_builder.build_force_finalize_messages(
            user_text=user_text,
            facts=facts,
            session=session,
        )

        return self.llm.generate(messages)

    # =========================================================
    # 🟡 流式回答（可选）
    # =========================================================
    def stream_generate(
        self,
        user_text: str,
        facts: Dict[str, Any],
        session: Optional[SessionMemory] = None,
        style: str = "detailed",
        length: str = "medium",
    ):

        messages = self.prompt_builder.build_final_messages(
            user_text=user_text,
            facts=facts,
            session=session,
            style=style,
            length=length,
        )

        return self.llm.stream_generate(messages)