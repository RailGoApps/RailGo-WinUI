# ui/psw_panel.py

from PyQt5.QtWidgets import QTextEdit
from PyQt5.QtCore import QTimer


class PSWPanel(QTextEdit):
    """
    Right Debug Panel (Buffered)

    Features:
    ✅ smooth log streaming
    ✅ no UI freeze
    ✅ auto flush every 80ms
    """

    def __init__(self):
        super().__init__()

        self.setReadOnly(True)
        self.setFrameStyle(QTextEdit.NoFrame)
        self.setLineWrapMode(QTextEdit.NoWrap)

        self.setStyleSheet("""
            QTextEdit {
                background: #0f172a;
                color: #bfdbfe;
                border: 1px solid rgba(59, 130, 246, 0.14);
                border-radius: 16px;
                font-family: "Cascadia Mono", "Consolas";
                font-size: 12px;
                line-height: 1.35;
                padding: 12px 12px 12px 12px;
                selection-background-color: rgba(37, 99, 235, 0.34);
            }

            QScrollBar:vertical {
                width: 8px;
                background: transparent;
                margin: 8px 2px 8px 0;
            }

            QScrollBar::handle:vertical {
                background: rgba(148, 163, 184, 0.38);
                border-radius: 4px;
                min-height: 22px;
            }

            QScrollBar::handle:vertical:hover {
                background: rgba(191, 219, 254, 0.55);
            }

            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
                height: 0;
            }
        """)

        # ===============================
        # Buffer System (关键)
        # ===============================
        self.buffer = []

        self.timer = QTimer()
        self.timer.timeout.connect(self.flush)
        self.timer.start(80)  # ⭐80ms 刷新一次

    # ======================================================
    # Push line (fast, safe)
    # ======================================================
    def push(self, line: str):
        """
        Called from UI thread signal.
        Only buffer here.
        """
        self.buffer.append(line)

    # ======================================================
    # Flush buffered logs
    # ======================================================
    def flush(self):
        """
        Periodically flush logs into QTextEdit.
        Prevents heavy repaint.
        """
        if not self.buffer:
            return

        text = "\n".join(self.buffer)
        self.buffer.clear()

        self.append(text)

        # auto scroll bottom
        bar = self.verticalScrollBar()
        bar.setValue(bar.maximum())
