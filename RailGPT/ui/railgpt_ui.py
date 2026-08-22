# ui/railgpt_ui.py

import math
import sys

from PyQt5.QtCore import QEvent, Qt, pyqtSignal
from PyQt5.QtGui import QIcon
from PyQt5.QtWidgets import (
    QApplication,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QSplitter,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from app_runtime import APP_NAME, APP_VERSION, WINDOW_TITLE, resource_path
from memory.session import SessionMemory
from ui.agent_worker import AgentWorker
from ui.chat_area import ChatArea
from ui.dropdown import RailGPTDropdown
from ui.font_selector import FontSelector
from ui.psw_panel import PSWPanel
from ui.sidebar import Sidebar
from ui.thinking_panel import ThinkingPanel


def after(callback):
    def decorator(fn):
        def fun(*args, **kwargs):
            result = fn(*args, **kwargs)
            callback()
            return result

        return fun

    return decorator


class RailGPTWindow(QWidget):
    thinking_signal = pyqtSignal(str)
    psw_signal = pyqtSignal(str)
    sidebar_refresh_signal = pyqtSignal()

    def __init__(self, backend, store, llm):
        super().__init__()

        self.backend = backend
        self.store = store
        self.llm = llm
        self.session = SessionMemory()
        self.worker = None
        self.mode_profile = "fast-go"

        self.init_ui()
        self.on_model_selected(self.model_box.current)

        self.psw_signal.connect(self.psw.push)
        self.sidebar_refresh_signal.connect(self.sidebar.refresh)
        self.backend.psw.add_listener(self.on_psw_line)
        self.backend.thinking_engine.set_listener(self.on_thinking_event)
        self.thinking_signal.connect(self._handle_thinking)

        if hasattr(self.store, "set_on_change"):
            self.store.set_on_change(self._request_sidebar_refresh)
        else:
            self.store._save = after(self._request_sidebar_refresh)(self.store._save)

    def init_ui(self):
        self.setWindowTitle(WINDOW_TITLE)
        self.resize(1320, 840)
        self.setStyleSheet(
            """
            QWidget {
                font-family: "Segoe UI";
                font-size: 14px;
                background: #f5f7fb;
            }

            #primaryButton {
                border-radius: 16px;
                padding: 10px 18px;
                background: #2563eb;
                color: white;
                border: none;
                font-weight: 600;
            }

            #primaryButton:hover {
                background: #1d4ed8;
            }

            #chatInput {
                border-radius: 16px;
                border: 1px solid rgba(148, 163, 184, 0.24);
                padding: 10px;
                background: #ffffff;
            }

            #chatInput:focus {
                border: 1px solid #2563eb;
            }

            #toolbarCard {
                background: rgba(255, 255, 255, 0.88);
                border-radius: 22px;
                border: 1px solid rgba(148, 163, 184, 0.12);
            }

            #inputCard {
                background: rgba(255, 255, 255, 0.96);
                border-radius: 22px;
                border: 1px solid rgba(148, 163, 184, 0.14);
            }

            #inputHint {
                color: #64748b;
                font-size: 12px;
            }

            #panelTitle {
                color: #0f172a;
                font-size: 15px;
                font-weight: 700;
            }

            #panelCaption {
                color: #64748b;
                font-size: 12px;
                line-height: 1.35;
            }

            QWidget#sidebarCard, QWidget#rightCard {
                background: rgba(255, 255, 255, 0.96);
                border-radius: 24px;
                border: 1px solid rgba(148, 163, 184, 0.12);
            }

            QWidget#observerShell {
                background: rgba(248, 250, 252, 0.96);
                border-radius: 20px;
                border: 1px solid rgba(148, 163, 184, 0.16);
            }

            QComboBox#observerToggle {
                background: #ffffff;
                border: 1px solid rgba(148, 163, 184, 0.22);
                border-radius: 14px;
                padding: 0 12px;
                color: #0f172a;
                font-weight: 600;
            }

            QComboBox#observerToggle:hover {
                border-color: rgba(59, 130, 246, 0.34);
            }

            QComboBox#observerToggle::drop-down {
                width: 28px;
                border: none;
                background: transparent;
            }

            #toolbarCaption {
                color: #64748b;
                font-size: 12px;
                font-weight: 600;
            }

            QSplitter::handle {
                background: transparent;
            }

            QSplitter::handle:hover {
                background: rgba(148, 163, 184, 0.12);
                border-radius: 4px;
            }
            """
        )

        root = QVBoxLayout()
        root.setContentsMargins(18, 18, 18, 18)
        root.setSpacing(0)
        self.setLayout(root)

        self.main_splitter = QSplitter(Qt.Horizontal)
        self.main_splitter.setChildrenCollapsible(False)
        self.main_splitter.setHandleWidth(10)
        self.main_splitter.splitterMoved.connect(self._update_layout_state)
        root.addWidget(self.main_splitter)

        self.sidebar_wrapper = QWidget()
        self.sidebar_wrapper.setObjectName("sidebarCard")
        self.sidebar_wrapper.setMinimumWidth(220)
        self.sidebar_wrapper.setMaximumWidth(328)
        sidebar_layout = QVBoxLayout()
        sidebar_layout.setContentsMargins(10, 10, 10, 10)
        sidebar_layout.setSpacing(8)
        self.sidebar = Sidebar(self.store)
        self.sidebar.session_selected.connect(self.load_chat)
        sidebar_layout.addWidget(self.sidebar)
        self.sidebar_wrapper.setLayout(sidebar_layout)

        self.center_wrapper = QWidget()
        center = QVBoxLayout()
        center.setContentsMargins(0, 0, 0, 0)
        center.setSpacing(12)
        self.center_wrapper.setLayout(center)

        toolbar_card = QFrame()
        toolbar_card.setObjectName("toolbarCard")
        top = QHBoxLayout()
        top.setContentsMargins(18, 14, 18, 14)
        top.setSpacing(10)

        title_box = QVBoxLayout()
        title_box.setSpacing(2)
        title = QLabel(f"{APP_NAME} v{APP_VERSION}")
        title.setStyleSheet("font-size: 24px; font-weight: 700; color: #0f172a;")
        self.subtitle = QLabel("")
        self.subtitle.setObjectName("toolbarCaption")
        title_box.addWidget(title)
        title_box.addWidget(self.subtitle)
        top.addLayout(title_box)

        top.addSpacing(10)
        mode_label = QLabel("模式：")
        mode_label.setStyleSheet("color: #334155; font-weight: 600;")
        top.addWidget(mode_label)

        self.model_box = RailGPTDropdown(
            [
                ("FAST-GO", "fast-go"),
                ("FAST-PLUS", "fast-plus"),
                ("DEEP", "deep"),
            ],
            self,
        )
        self.model_box.selected.connect(self.on_model_selected)
        top.addWidget(self.model_box)

        self.font_selector = FontSelector(callback=self._change_chat_font)
        top.addWidget(self.font_selector)

        top.addStretch()

        self.new_btn = QPushButton("+ 新建对话")
        self.new_btn.setObjectName("primaryButton")
        self.new_btn.setFixedHeight(40)
        self.new_btn.clicked.connect(self.new_chat)
        top.addWidget(self.new_btn)

        toolbar_card.setLayout(top)
        center.addWidget(toolbar_card, 0)

        self.chat = ChatArea()
        self.chat.suggestion_selected.connect(self.submit_text)
        self.chat.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        center.addWidget(self.chat, 1)

        input_card = QWidget()
        input_card.setObjectName("inputCard")
        input_card.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        input_layout = QVBoxLayout()
        input_layout.setContentsMargins(16, 14, 16, 12)
        input_layout.setSpacing(8)

        input_row = QHBoxLayout()
        input_row.setContentsMargins(0, 0, 0, 0)
        input_row.setSpacing(12)

        self.input = QTextEdit()
        self.input.setObjectName("chatInput")
        self.input.setAcceptRichText(False)
        self.input.setMinimumHeight(64)
        self.input.setMaximumHeight(168)
        self.input.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.input.setPlaceholderText("输入铁路问题。Enter 换行，Ctrl+Enter 发送。")
        self.input.installEventFilter(self)
        self.input.textChanged.connect(self._sync_input_height)

        self.send_btn = QPushButton("发送")
        self.send_btn.setObjectName("primaryButton")
        self.send_btn.setFixedWidth(90)
        self.send_btn.setFixedHeight(44)
        self.send_btn.clicked.connect(self.send)

        input_row.addWidget(self.input, 1)
        input_row.addWidget(self.send_btn, 0, Qt.AlignBottom)

        self.input_hint = QLabel("Ctrl+Enter 发送 · Enter 换行")
        self.input_hint.setObjectName("inputHint")

        input_layout.addLayout(input_row)
        input_layout.addWidget(self.input_hint, 0, Qt.AlignLeft)
        input_card.setLayout(input_layout)
        center.addWidget(input_card, 0)

        self.right_wrapper = QWidget()
        self.right_wrapper.setObjectName("rightCard")
        self.right_wrapper.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Expanding)
        self.right_wrapper.setMinimumWidth(308)
        self.right_wrapper.setMaximumWidth(400)
        right_layout = QVBoxLayout()
        right_layout.setContentsMargins(16, 16, 16, 16)
        right_layout.setSpacing(12)

        panel_heading = QVBoxLayout()
        panel_heading.setSpacing(2)
        panel_title = QLabel("Observer Panel")
        panel_title.setObjectName("panelTitle")
        panel_caption = QLabel("思考流与调试日志会在这里展示，方便观察 Fast 协调过程。")
        panel_caption.setWordWrap(True)
        panel_caption.setObjectName("panelCaption")
        panel_heading.addWidget(panel_title)
        panel_heading.addWidget(panel_caption)

        self.debug_toggle = QComboBox()
        self.debug_toggle.setObjectName("observerToggle")
        self.debug_toggle.addItems(["思考面板", "调试日志"])
        self.debug_toggle.setFixedHeight(38)
        self.debug_toggle.currentIndexChanged.connect(self.switch_mode)

        self.thinking = ThinkingPanel()
        self.psw = PSWPanel()
        self.psw.hide()

        observer_shell = QWidget()
        observer_shell.setObjectName("observerShell")
        observer_shell.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Expanding)
        observer_layout = QVBoxLayout()
        observer_layout.setContentsMargins(12, 12, 12, 12)
        observer_layout.setSpacing(10)
        observer_layout.addWidget(self.debug_toggle, 0)
        observer_layout.addWidget(self.thinking, 1)
        observer_layout.addWidget(self.psw, 1)
        observer_shell.setLayout(observer_layout)

        right_layout.addLayout(panel_heading, 0)
        right_layout.addWidget(observer_shell, 1)
        self.right_wrapper.setLayout(right_layout)

        self.main_splitter.addWidget(self.sidebar_wrapper)
        self.main_splitter.addWidget(self.center_wrapper)
        self.main_splitter.addWidget(self.right_wrapper)
        self.main_splitter.setStretchFactor(0, 0)
        self.main_splitter.setStretchFactor(1, 1)
        self.main_splitter.setStretchFactor(2, 0)
        self.main_splitter.setSizes([246, 878, 332])

        self._change_chat_font(self.font_selector.current_size())
        self._sync_input_height()
        self._refresh_mode_caption(self.model_box.current)
        self._update_layout_state()

    def eventFilter(self, obj, event):
        if obj is self.input and event.type() == QEvent.KeyPress:
            if event.key() in (Qt.Key_Return, Qt.Key_Enter) and event.modifiers() & Qt.ControlModifier:
                self.send()
                return True
        return super().eventFilter(obj, event)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._update_layout_state()

    def _sync_input_height(self):
        doc_height = self.input.document().size().height()
        target = max(64, min(168, int(math.ceil(doc_height)) + 22))
        self.input.setFixedHeight(target)

    def _update_layout_state(self):
        width = self.width()
        compact = width < 1240
        very_compact = width < 1090

        if compact:
            self.sidebar_wrapper.setMinimumWidth(208)
            self.sidebar_wrapper.setMaximumWidth(272)
            self.right_wrapper.setMinimumWidth(284)
            self.right_wrapper.setMaximumWidth(344)
        else:
            self.sidebar_wrapper.setMinimumWidth(220)
            self.sidebar_wrapper.setMaximumWidth(328)
            self.right_wrapper.setMinimumWidth(308)
            self.right_wrapper.setMaximumWidth(400)

        if very_compact:
            self.main_splitter.setSizes([214, max(560, width - 540), 290])

        if hasattr(self.chat, "refresh_all_bubbles"):
            self.chat.refresh_all_bubbles()

    def _refresh_mode_caption(self, mode: str):
        self.mode_profile = str(mode or "fast-go").strip().lower()
        caption_map = {
            "fast-go": "Railway Knowledge + Fast Coordination · Final: Chat",
            "fast-plus": "Railway Knowledge + Fast Coordination · Final: Reasoner",
            "deep": "Railway Knowledge + Deep Reasoning",
        }
        self.subtitle.setText(caption_map.get(self.mode_profile, caption_map["fast-go"]))

    def on_thinking_event(self, event):
        if isinstance(event, dict) and event.get("type") == "thinking_token":
            self.thinking_signal.emit(event["text"])

    def switch_mode(self, idx):
        if idx == 0:
            self.psw.hide()
            self.thinking.show()
        else:
            self.thinking.hide()
            self.psw.show()

    def on_model_selected(self, mode: str):
        profile = str(mode or "fast-go").strip().lower()
        if profile == "fast":
            profile = "fast-go"
        if profile not in {"fast-go", "fast-plus", "deep"}:
            profile = "fast-go"
        self.llm.set_mode(profile)
        if hasattr(self.backend, "set_mode"):
            self.backend.set_mode(profile)
        self._refresh_mode_caption(profile)

    def _request_sidebar_refresh(self):
        self.sidebar_refresh_signal.emit()

    def _change_chat_font(self, size: int):
        if hasattr(self, "chat"):
            self.chat.set_chat_font_size(size)

    def _set_busy_state(self, busy: bool):
        self.send_btn.setEnabled(not busy)
        self.input.setEnabled(not busy)
        self.new_btn.setEnabled(not busy)

    def send(self):
        text = self.input.toPlainText().strip()
        if not text:
            return

        self.input.clear()
        self._sync_input_height()
        self.submit_text(text)

    def submit_text(self, text: str):
        if self.worker and self.worker.isRunning():
            return

        text = str(text or "").strip()
        if not text:
            return

        if self.debug_toggle.currentIndex() == 0:
            self.thinking.clear()

        self.chat.add_user(text)
        self.chat.add_ai("")

        if self.store.current_id is None:
            cid = self.store.new_conversation("")
            self.sidebar.current_cid = cid
            self._request_sidebar_refresh()

        self.worker = AgentWorker(
            backend=self.backend,
            store=self.store,
            user_text=text,
            session=self.session,
        )
        self.worker.token.connect(self.chat.append_token)
        self.worker.pending.connect(self.on_pending)
        self.worker.done.connect(self.on_done)
        self.worker.error.connect(self.on_error)
        self._set_busy_state(True)
        self.worker.start()

    def _handle_thinking(self, text):
        if self.debug_toggle.currentIndex() == 0:
            self.thinking.push(text)

    def on_pending(self, q):
        self.chat.append_token(q)

    def on_done(self, full):
        if hasattr(self.chat, "finalize_current_ai"):
            self.chat.finalize_current_ai(full)
        self._request_sidebar_refresh()
        self._set_busy_state(False)
        self.input.setFocus()
        self.worker = None

    def on_error(self, err):
        self.chat.append_token("\n⚠️ " + err)
        self._set_busy_state(False)
        self.input.setFocus()
        self.worker = None

    def load_chat(self, cid):
        if not self.store.load_conversation(cid):
            return

        self.store.build_session_memory(self.session)
        self.chat.clear_all()

        for msg in self.store.messages:
            if msg["role"] == "user":
                self.chat.add_user(msg["content"])
            else:
                self.chat.add_ai(msg["content"])

        self.chat.refresh_all_bubbles()

    def new_chat(self):
        cid = self.store.new_conversation("")
        self.sidebar.current_cid = cid
        self.session = SessionMemory()
        self.chat.clear_all()
        self._request_sidebar_refresh()
        self.psw.push(f"[NEW CHAT] id={cid}")

    def on_psw_line(self, event):
        line = f"[{event['timestamp']}] [round {event['round']}] [{event['state']}] {event['detail']}"
        self.psw_signal.emit(line)


def launch_ui(backend, store, llm):
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    app.setWindowIcon(QIcon(resource_path("RailGPT.ico")))
    win = RailGPTWindow(backend, store, llm)
    win.show()
    sys.exit(app.exec_())
