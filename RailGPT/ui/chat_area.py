from PyQt5.QtCore import Qt, QTimer, pyqtSignal
from PyQt5.QtWidgets import QScrollArea, QStackedLayout, QVBoxLayout, QWidget

from ui.chat_bubble import ChatBubble
from ui.home_panel import HomePanel


class ChatArea(QWidget):
    suggestion_selected = pyqtSignal(str)

    def __init__(self):
        super().__init__()

        self._font_size = 15
        self.current_ai_bubble = None
        self._current_ai_received_token = False
        self._stick_to_bottom = True

        self._scroll_timer = QTimer(self)
        self._scroll_timer.setSingleShot(True)
        self._scroll_timer.setInterval(24)
        self._scroll_timer.timeout.connect(self.scroll_bottom)

        self._build()
        self.show_home()

    def _build(self):
        self.setStyleSheet(
            """
            QWidget#chatShell {
                background: white;
                border-radius: 18px;
                border: 1px solid rgba(15, 23, 42, 0.05);
            }

            QScrollArea {
                background: transparent;
                border: none;
            }

            QScrollBar:vertical {
                width: 8px;
                background: transparent;
            }

            QScrollBar::handle:vertical {
                background: rgba(0, 0, 0, 60);
                border-radius: 4px;
                min-height: 20px;
            }
            """
        )

        root = QVBoxLayout()
        root.setContentsMargins(0, 0, 0, 0)
        self.setLayout(root)

        self.shell = QWidget()
        self.shell.setObjectName("chatShell")
        shell_layout = QVBoxLayout()
        shell_layout.setContentsMargins(0, 0, 0, 0)
        self.shell.setLayout(shell_layout)

        self.stack = QStackedLayout()
        shell_layout.addLayout(self.stack)

        self.home = HomePanel()
        self.home.prompt_selected.connect(self.suggestion_selected.emit)
        self.stack.addWidget(self.home)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QScrollArea.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.scroll.setAttribute(Qt.WA_StyledBackground, True)

        self.container = QWidget()
        self.container.setStyleSheet("QWidget { background: transparent; }")

        self.message_layout = QVBoxLayout()
        self.message_layout.setAlignment(Qt.AlignTop)
        self.message_layout.setSpacing(16)
        self.message_layout.setContentsMargins(18, 16, 18, 20)
        self.container.setLayout(self.message_layout)
        self.scroll.setWidget(self.container)

        self.stack.addWidget(self.scroll)
        root.addWidget(self.shell)

    def show_home(self):
        self.stack.setCurrentWidget(self.home)
        self.current_ai_bubble = None
        self._current_ai_received_token = False

    def _show_chat(self):
        self.stack.setCurrentWidget(self.scroll)

    def add_user(self, text: str):
        self._show_chat()
        bubble = ChatBubble(text, role="user", font_size=self._font_size)
        self.message_layout.addWidget(bubble, 0, Qt.AlignRight)
        self._stick_to_bottom = True
        self.refresh_all_bubbles()
        self.scroll_bottom()

    def add_ai(self, text: str):
        self._show_chat()
        bubble = ChatBubble(text, role="ai", font_size=self._font_size)
        self.message_layout.addWidget(bubble)
        self.current_ai_bubble = bubble
        self._current_ai_received_token = bool(text)
        self._stick_to_bottom = True
        self.refresh_all_bubbles()
        self.scroll_bottom()

    def add_system(self, text: str):
        self._show_chat()
        bubble = ChatBubble(text, role="system", font_size=self._font_size)
        self.message_layout.addWidget(bubble, 0, Qt.AlignLeft)
        self._stick_to_bottom = True
        self.refresh_all_bubbles()
        self.scroll_bottom()

    def append_token(self, token: str):
        if not self.current_ai_bubble:
            return

        self._current_ai_received_token = True
        self._stick_to_bottom = self._is_near_bottom()
        self.current_ai_bubble.append_text(token)
        if self._stick_to_bottom and not self._scroll_timer.isActive():
            self._scroll_timer.start()

    def finalize_current_ai(self, text: str):
        final_text = text or ""
        if not self.current_ai_bubble:
            if final_text:
                self.add_ai(final_text)
            return

        self._stick_to_bottom = self._is_near_bottom()
        if not self._current_ai_received_token or not str(self.current_ai_bubble.text or "").strip():
            self.current_ai_bubble.set_text(final_text)
        self.refresh_all_bubbles()
        if self._stick_to_bottom:
            self.scroll_bottom()

    def scroll_bottom(self):
        bar = self.scroll.verticalScrollBar()
        bar.setValue(bar.maximum())

    def _is_near_bottom(self) -> bool:
        bar = self.scroll.verticalScrollBar()
        return (bar.maximum() - bar.value()) <= 48

    def clear_all(self):
        while self.message_layout.count():
            item = self.message_layout.takeAt(0)
            widget = item.widget()
            if widget:
                widget.deleteLater()

        self.current_ai_bubble = None
        self._current_ai_received_token = False
        self._scroll_timer.stop()
        self._stick_to_bottom = True
        self.show_home()

    def refresh_all_bubbles(self):
        if self.stack.currentWidget() is not self.scroll:
            return

        available_width = (
            self.scroll.viewport().width()
            - self.message_layout.contentsMargins().left()
            - self.message_layout.contentsMargins().right()
        )

        for index in range(self.message_layout.count()):
            widget = self.message_layout.itemAt(index).widget()
            if widget and hasattr(widget, "update_width"):
                widget.update_width(available_width)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.refresh_all_bubbles()

    def set_chat_font_size(self, size: int):
        if self._font_size == size:
            return

        self._font_size = size
        if hasattr(self.home, "set_font_size"):
            self.home.set_font_size(size)
        for index in range(self.message_layout.count()):
            widget = self.message_layout.itemAt(index).widget()
            if widget and hasattr(widget, "set_font_size"):
                widget.set_font_size(size)

        self.container.adjustSize()
        self.container.updateGeometry()
        self.refresh_all_bubbles()
        self.update()
