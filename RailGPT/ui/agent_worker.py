from PyQt5.QtCore import QThread, pyqtSignal
import time


class AgentWorker(QThread):

    token = pyqtSignal(str)
    thinking = pyqtSignal(str)   # ⭐ 新增
    pending = pyqtSignal(str)
    done = pyqtSignal(str)
    error = pyqtSignal(str)

    def __init__(self, backend, store, user_text, session):
        super().__init__()

        self.backend = backend
        self.store = store
        self.user_text = user_text
        self.session = session

    def run(self):

        try:

            if self.store.current_id is None:
                self.store.new_conversation("")

            if hasattr(self.session, "set_session_id"):
                self.session.set_session_id(self.store.current_id)

            self.store.append_user(self.user_text)

            full_answer = ""
            thinking_buffer = ""
            last_emit_time = time.time()

            for ev in self.backend.stream_events(self.user_text, self.session):

                etype = ev.get("type")
                text = ev.get("text", "")
                print("EVENT:", etype)
                # ==============================
                # 主回答流式
                # ==============================
                if etype == "token":
                    full_answer += text
                    self.token.emit(text)

                # ==============================
                # Thinking（🔥节流版）
                # ==============================
                elif etype == "thinking_token":

                    thinking_buffer += text

                    # 每 80ms 才发一次
                    now = time.time()
                    if now - last_emit_time > 0.08:
                        self.thinking.emit(thinking_buffer)
                        thinking_buffer = ""
                        last_emit_time = now

                # ==============================
                # pending
                # ==============================
                elif etype == "pending":
                    self.pending.emit(text)

                # ==============================
                # final
                # ==============================
                elif etype == "final":
                    full_answer = text
                    break

            # 最后残余 thinking
            if thinking_buffer:
                self.thinking.emit(thinking_buffer)

            self.store.append_ai(full_answer)
            self.store.save_session_memory(self.session)

            self.done.emit(full_answer)

        except Exception as e:
            self.error.emit(str(e))
