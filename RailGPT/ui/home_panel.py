from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from app_runtime import APP_NAME, APP_VERSION


class SuggestionChip(QPushButton):
    def __init__(self, text: str, parent=None):
        super().__init__(text, parent)
        self.setCursor(Qt.PointingHandCursor)
        self.setStyleSheet(
            """
            QPushButton {
                background: rgba(255, 255, 255, 0.9);
                border: 1px solid rgba(29, 78, 216, 0.16);
                border-radius: 18px;
                padding: 10px 14px;
                text-align: left;
                color: #16324f;
                font-size: 13px;
            }

            QPushButton:hover {
                background: #ffffff;
                border: 1px solid rgba(37, 99, 235, 0.34);
            }
            """
        )


class HomePanel(QWidget):
    prompt_selected = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._title = None
        self._subtitle = None
        self._guide = None
        self._chip_grid = None
        self._chips = []
        self._build()

    def _build(self):
        root = QVBoxLayout()
        root.setContentsMargins(28, 28, 28, 28)
        root.setSpacing(18)
        root.addStretch(1)

        hero = QFrame()
        hero.setStyleSheet(
            """
            QFrame {
                background: qlineargradient(
                    x1: 0, y1: 0, x2: 1, y2: 1,
                    stop: 0 #eff6ff,
                    stop: 0.45 #f8fbff,
                    stop: 1 #eef2ff
                );
                border: 1px solid rgba(59, 130, 246, 0.12);
                border-radius: 26px;
            }
            """
        )

        hero_layout = QVBoxLayout()
        hero_layout.setContentsMargins(26, 24, 26, 24)
        hero_layout.setSpacing(14)

        eyebrow = QLabel("Railway Knowledge + Fast Coordination")
        eyebrow.setStyleSheet("color: #2563eb; font-size: 12px; font-weight: 700; letter-spacing: 0.5px;")

        title = QLabel(f"{APP_NAME} v{APP_VERSION}")
        title.setStyleSheet("color: #0f172a; font-size: 32px; font-weight: 700;")
        self._title = title

        subtitle = QLabel(
            "把铁路工具结果、知识检索和 Fast 模式候选压缩协同起来，尽快给出更稳的铁路咨询答案。"
        )
        subtitle.setWordWrap(True)
        subtitle.setStyleSheet("color: #334155; font-size: 15px; line-height: 1.5;")
        self._subtitle = subtitle

        stats_row = QHBoxLayout()
        stats_row.setSpacing(10)
        for label in ("Fast default", "RAG grounded", "Railway-focused"):
            badge = QLabel(label)
            badge.setStyleSheet(
                """
                QLabel {
                    background: rgba(37, 99, 235, 0.08);
                    color: #1e3a8a;
                    border-radius: 14px;
                    padding: 6px 10px;
                    font-size: 12px;
                    font-weight: 600;
                }
                """
            )
            stats_row.addWidget(badge)
        stats_row.addStretch(1)

        hero_layout.addWidget(eyebrow)
        hero_layout.addWidget(title)
        hero_layout.addWidget(subtitle)
        hero_layout.addLayout(stats_row)
        hero.setLayout(hero_layout)

        guide = QLabel("可以直接点一个示例，或者输入你自己的铁路问题。")
        guide.setStyleSheet("color: #475569; font-size: 14px;")
        self._guide = guide

        chip_grid = QGridLayout()
        chip_grid.setHorizontalSpacing(12)
        chip_grid.setVerticalSpacing(12)
        self._chip_grid = chip_grid

        suggestions = [
            "南京南到徐州东最快的标杆车",
            "G87 今天停不停南京南",
            "福州到北京南有哪些直达",
            "南京南电报码是什么",
            "北京南到上海虹桥余票怎么样",
            "福州到深圳北怎么中转更快",
        ]
        for index, text in enumerate(suggestions):
            chip = SuggestionChip(text)
            chip.clicked.connect(lambda _checked=False, value=text: self.prompt_selected.emit(value))
            chip_grid.addWidget(chip, index // 2, index % 2)
            self._chips.append(chip)

        root.addWidget(hero)
        root.addWidget(guide)
        root.addLayout(chip_grid)
        root.addStretch(2)

        self.setLayout(root)
        self._reflow_chips()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._reflow_chips()

    def _reflow_chips(self):
        if self._chip_grid is None:
            return

        columns = 1 if self.width() < 760 else 2

        while self._chip_grid.count():
            item = self._chip_grid.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.setParent(self)

        for index, chip in enumerate(self._chips):
            row = index // columns
            column = index % columns
            self._chip_grid.addWidget(chip, row, column)

        for column in range(max(1, columns)):
            self._chip_grid.setColumnStretch(column, 1)

    def set_font_size(self, size: int):
        title_size = max(24, size + 16)
        subtitle_size = max(14, size)
        guide_size = max(13, size - 1)
        chip_size = max(12, size - 1)

        if self._title is not None:
            self._title.setStyleSheet(
                f"color: #0f172a; font-size: {title_size}px; font-weight: 700;"
            )
        if self._subtitle is not None:
            self._subtitle.setStyleSheet(
                f"color: #334155; font-size: {subtitle_size}px; line-height: 1.55;"
            )
        if self._guide is not None:
            self._guide.setStyleSheet(
                f"color: #475569; font-size: {guide_size}px;"
            )
        for chip in self._chips:
            chip.setStyleSheet(
                f"""
                QPushButton {{
                    background: rgba(255, 255, 255, 0.9);
                    border: 1px solid rgba(29, 78, 216, 0.16);
                    border-radius: 18px;
                    padding: 10px 14px;
                    text-align: left;
                    color: #16324f;
                    font-size: {chip_size}px;
                }}

                QPushButton:hover {{
                    background: #ffffff;
                    border: 1px solid rgba(37, 99, 235, 0.34);
                }}
                """
            )
