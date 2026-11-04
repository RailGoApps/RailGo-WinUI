import math

from PyQt5.QtCore import Qt, QTimer
from PyQt5.QtGui import QFont
from PyQt5.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from ui.markdown_renderer import MarkdownRenderer


class ChatBubble(QWidget):
    def __init__(self, text: str, role: str = "ai", font_size: int = 15):
        super().__init__()

        self.role = role
        self.text = text
        self.font_size = font_size
        self.renderer = MarkdownRenderer(font_size)
        self._current_available_width = None

        self._render_timer = QTimer(self)
        self._render_timer.setSingleShot(True)
        self._render_timer.setInterval(28)
        self._render_timer.timeout.connect(self._flush_pending_render)

        self._build()

    def _build(self):
        outer = QHBoxLayout()
        outer.setContentsMargins(12, 6, 12, 6)

        self.bubble = QWidget()
        bubble_layout = QVBoxLayout()
        bubble_layout.setContentsMargins(16, 12, 16, 12)
        bubble_layout.setSpacing(6)

        self.bubble.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Preferred)
        self.header = QLabel(self._header_text())
        self.header.setStyleSheet(self._header_style())
        bubble_layout.addWidget(self.header)

        if self.role == "user":
            self.content = QLabel(self.text)
            self.content.setWordWrap(True)
            self.content.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Preferred)
            self._apply_user_style()
        else:
            self.content = QTextBrowser()
            self.content.setOpenExternalLinks(True)
            self.content.setFrameShape(QTextBrowser.NoFrame)
            self.content.setReadOnly(True)
            self.content.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
            self.content.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
            self.content.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Preferred)
            self.content.document().setDocumentMargin(0)
            self._apply_ai_font()
            self._render_markdown()

        bubble_layout.addWidget(self.content)
        self.bubble.setLayout(bubble_layout)

        if self.role == "user":
            self.bubble.setStyleSheet(
                """
                QWidget {
                    background-color: #2f6fed;
                    border-radius: 18px;
                    border: 1px solid rgba(255, 255, 255, 0.08);
                }
                """
            )
            outer.addStretch(1)
            outer.addWidget(self.bubble, 0, Qt.AlignRight)
        elif self.role == "system":
            self.bubble.setStyleSheet(
                """
                QWidget {
                    background-color: #fff7e5;
                    border-radius: 18px;
                    border: 1px solid rgba(251, 191, 36, 0.24);
                }
                """
            )
            outer.addWidget(self.bubble)
        else:
            self.bubble.setStyleSheet(
                """
                QWidget {
                    background-color: #f8fafc;
                    border-radius: 18px;
                    border: 1px solid rgba(148, 163, 184, 0.18);
                }
                """
            )
            outer.addWidget(self.bubble)

        self.setLayout(outer)

    def _render_markdown(self):
        if self.role == "user":
            return

        self.content.setHtml(self.renderer.render(self.text, self.role))

        if self._current_available_width:
            self.update_width(self._current_available_width)

    def _flush_pending_render(self):
        if self.role == "user":
            return
        self._render_markdown()

    def update_width(self, available_width: int):
        self._current_available_width = available_width
        outer_margin = 32
        safe_width = max(220, available_width - outer_margin)

        if self.role == "user":
            bubble_width = max(180, min(int(safe_width * 0.68), 560, safe_width))
            self.bubble.setMaximumWidth(bubble_width)
            self.content.setMaximumWidth(max(140, bubble_width - 32))
            self.updateGeometry()
            return

        bubble_width = max(220, min(int(safe_width * 0.86), 860, safe_width))
        content_width = max(180, bubble_width - 32)

        self.bubble.setMaximumWidth(bubble_width)
        self.content.setMaximumWidth(content_width)

        doc = self.content.document()
        doc.setTextWidth(content_width)
        doc_height = doc.documentLayout().documentSize().height()

        final_height = math.ceil(doc_height) + 12
        self.content.setMinimumHeight(final_height)
        self.content.setMaximumHeight(16777215)

        self.updateGeometry()

    def append_text(self, token: str):
        self.text += token

        if self.role == "user":
            self.content.setText(self.text)
            return

        if "\n" in token or len(self.text) < 120:
            self._flush_pending_render()
            return

        if not self._render_timer.isActive():
            self._render_timer.start()

    def set_text(self, text: str):
        self.text = text or ""
        self._render_timer.stop()

        if self.role == "user":
            self.content.setText(self.text)
            return

        self._render_markdown()

    def set_font_size(self, size: int):
        if self.font_size == size:
            return

        self.font_size = size
        self.renderer.set_font_size(size)
        self.header.setStyleSheet(self._header_style())

        if self.role == "user":
            self._apply_user_style()
        else:
            self._apply_ai_font()
            self._render_markdown()

        if self._current_available_width:
            self.update_width(self._current_available_width)

    def _header_text(self) -> str:
        if self.role == "user":
            return "You"
        if self.role == "system":
            return "Guide"
        return "RailGPT"

    def _header_style(self) -> str:
        badge_size = max(11, min(14, self.font_size - 3))
        if self.role == "user":
            color = "rgba(255,255,255,0.86)"
            bg = "rgba(255,255,255,0.14)"
        elif self.role == "system":
            color = "#92400e"
            bg = "rgba(245, 158, 11, 0.12)"
        else:
            color = "#2563eb"
            bg = "rgba(37, 99, 235, 0.08)"

        return (
            "QLabel {"
            f"color: {color};"
            f"background: {bg};"
            "border-radius: 10px;"
            "padding: 4px 8px;"
            f"font-size: {badge_size}px;"
            "font-weight: 700;"
            "max-width: 80px;"
            "}"
        )

    def _apply_user_style(self):
        self.content.setStyleSheet(
            f"""
            QLabel {{
                font-size: {self.font_size}px;
                line-height: 1.55;
                color: white;
            }}
            """
        )

    def _apply_ai_font(self):
        font = QFont("Microsoft YaHei UI")
        font.setPixelSize(self.font_size)
        self.content.setFont(font)
        self.content.document().setDefaultFont(font)
        self.content.setStyleSheet(
            f"""
            QTextBrowser {{
                background: transparent;
                border: none;
                font-size: {self.font_size}px;
                color: #111827;
            }}
            """
        )
