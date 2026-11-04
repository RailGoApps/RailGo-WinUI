from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtGui import QFontMetrics
from PyQt5.QtWidgets import QButtonGroup, QHBoxLayout, QPushButton, QSizePolicy, QWidget


class RailGPTDropdown(QWidget):
    selected = pyqtSignal(str)

    def __init__(self, items, parent=None):
        super().__init__(parent)

        self.options = [self._normalize_option(item) for item in items]
        self.values = [item["value"] for item in self.options]
        self.current = self.values[0] if self.values else ""
        self._buttons = []
        self._group = QButtonGroup(self)
        self._group.setExclusive(True)
        self._group.buttonClicked[int].connect(self._on_button_clicked)

        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setFixedHeight(40)
        self.setSizePolicy(QSizePolicy.MinimumExpanding, QSizePolicy.Fixed)
        self.setStyleSheet(
            """
            QWidget {
                background: rgba(255, 255, 255, 0.82);
                border-radius: 20px;
                border: 1px solid rgba(37, 99, 235, 0.16);
            }
            """
        )

        layout = QHBoxLayout(self)
        layout.setContentsMargins(5, 5, 5, 5)
        layout.setSpacing(4)

        for index, item in enumerate(self.options):
            button = QPushButton(item["label"])
            button.setCheckable(True)
            button.setCursor(Qt.PointingHandCursor)
            button.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
            button.setMinimumWidth(max(68, self.fontMetrics().horizontalAdvance(item["label"]) + 28))
            button.setToolTip(item["label"])
            self._group.addButton(button, index)
            self._buttons.append(button)
            layout.addWidget(button)

        self._sync_width_hint()
        self._apply_styles()

    def _normalize_option(self, item):
        if isinstance(item, dict):
            label = str(item.get("label") or item.get("value") or "")
            value = str(item.get("value") or label)
            return {"label": label, "value": value}

        if isinstance(item, (tuple, list)) and len(item) == 2:
            label, value = item
            return {"label": str(label), "value": str(value)}

        text = str(item)
        return {"label": text, "value": text}

    def _sync_width_hint(self):
        metrics = QFontMetrics(self.font())
        widths = [max(68, metrics.horizontalAdvance(item["label"]) + 28) for item in self.options]
        total = sum(widths) + 10 + max(0, len(widths) - 1) * 4
        self.setMinimumWidth(max(132, total))

    def _on_button_clicked(self, index: int):
        if index < 0 or index >= len(self.options):
            return

        self.current = self.options[index]["value"]
        self._apply_styles()
        self.selected.emit(self.current)

    def _apply_styles(self):
        current_index = self.values.index(self.current) if self.current in self.values else -1
        for index, button in enumerate(self._buttons):
            active = index == current_index
            button.setChecked(active)
            if active:
                button.setStyleSheet(
                    """
                    QPushButton {
                        background: #2563eb;
                        color: white;
                        border: none;
                        border-radius: 14px;
                        font-weight: 700;
                        padding: 0 12px;
                    }
                    """
                )
            else:
                button.setStyleSheet(
                    """
                    QPushButton {
                        background: transparent;
                        color: #1e293b;
                        border: none;
                        border-radius: 14px;
                        font-weight: 600;
                        padding: 0 12px;
                    }
                    QPushButton:hover {
                        background: rgba(37, 99, 235, 0.08);
                    }
                    """
                )

    def set_current(self, value: str):
        if value not in self.values:
            return
        self.current = value
        self._apply_styles()
