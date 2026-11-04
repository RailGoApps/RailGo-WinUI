from PyQt5.QtWidgets import QTextEdit
from PyQt5.QtCore import QTimer


class ThinkingPanel(QTextEdit):
    """
    AI Thinking Panel

    - Smooth streaming
    - Natural language thinking
    - Soft visual style
    """

    def __init__(self):
        super().__init__()

        self.setReadOnly(True)
        self.setFrameStyle(QTextEdit.NoFrame)
        self.setLineWrapMode(QTextEdit.WidgetWidth)

        self.setStyleSheet("""
            QTextEdit {
                background: #0f172a;
                color: #dbeafe;
                border: 1px solid rgba(59, 130, 246, 0.12);
                border-radius: 16px;
                font-family: "Microsoft YaHei", "Segoe UI";
                font-size: 13px;
                line-height: 1.45;
                padding: 12px;
                selection-background-color: rgba(37, 99, 235, 0.34);
            }

            QScrollBar:vertical {
                width: 8px;
                background: transparent;
                margin: 8px 2px 8px 0;
            }

            QScrollBar::handle:vertical {
                background: rgba(148, 163, 184, 0.34);
                border-radius: 4px;
                min-height: 22px;
            }

            QScrollBar::handle:vertical:hover {
                background: rgba(191, 219, 254, 0.52);
            }

            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
                height: 0;
            }
        """)

        self.buffer = []

        self.timer = QTimer()
        self.timer.timeout.connect(self.flush)
        self.timer.start(60)

    # ======================================================
    # Push token
    # ======================================================
    def push(self, text: str):
        self.buffer.append(text)

    # ======================================================
    # Flush
    # ======================================================
    def flush(self):

        if not self.buffer:
            return

        text = "".join(self.buffer)
        self.buffer.clear()

        cursor = self.textCursor()
        cursor.movePosition(cursor.End)
        cursor.insertText(text)

        self.setTextCursor(cursor)
