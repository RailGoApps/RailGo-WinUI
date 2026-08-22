import threading
import queue
from typing import Dict, List
from llm.llm_client import LLMClient
from thinking.thinking_prompt import SYSTEM_PROMPT


class ThinkingEngine:

    def __init__(self, llm: LLMClient):
        self.llm = llm

        self._trace: List[Dict] = []
        self._lock = threading.Lock()

        self._listener = None
        self._event_queue = queue.Queue()

        self._last_state = None
        self._running = True

        # ⭐ 单一后台 worker
        threading.Thread(
            target=self._worker_loop,
            daemon=True
        ).start()

    # =====================================================
    # UI listener
    # =====================================================
    def set_listener(self, fn):
        self._listener = fn

    # =====================================================
    # 用户初始思考
    # =====================================================
    def start_user_thinking(self, user_text: str):

        self._event_queue.put({
            "type": "user",
            "content": user_text
        })

    # =====================================================
    # PSW 推送事件
    # =====================================================
    def push_event(self, event: Dict):

        state = event.get("state")

        if state == self._last_state:
            return

        self._last_state = state

        self._event_queue.put({
            "type": "psw",
            "content": event
        })

    # =====================================================
    # 单线程 Worker
    # =====================================================
    def _worker_loop(self):

        while self._running:

            event = self._event_queue.get()

            try:

                if event["type"] == "user":
                    self._handle_user(event["content"])

                elif event["type"] == "psw":
                    self._handle_psw(event["content"])

            except Exception as e:
                print("ThinkingEngine error:", e)

    # =====================================================
    # 用户思考逻辑
    # =====================================================
    def _handle_user(self, user_text):

        messages = [
            {
                "role": "system",
                "content": """
You are an AI reasoning visualizer.

The user just asked a question.
Think about how you might solve it.

Be concise.
Do NOT mention APIs.
Do NOT produce final answer.
Only show reasoning direction.
"""
            },
            {
                "role": "user",
                "content": user_text
            }
        ]

        for token in self.llm.stream_generate(messages):
            self._emit_token(token)

    # =====================================================
    # PSW 思考逻辑
    # =====================================================
    def _handle_psw(self, event):

        with self._lock:
            self._trace.append(event)
            self._trace = self._trace[-5:]

            recent = list(self._trace)

        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "user",
                "content": f"Execution events:\n{recent}"
            }
        ]

        for token in self.llm.stream_generate(messages):
            self._emit_token(token)

    # =====================================================
    # Token 输出
    # =====================================================
    def _emit_token(self, token):

        if not token:
            return

        if self._listener:
            self._listener({
                "type": "thinking_token",
                "text": token
            })