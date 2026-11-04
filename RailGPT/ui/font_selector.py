import sys
from PyQt5.QtWidgets import (
    QWidget, QHBoxLayout, QPushButton,
    QGraphicsDropShadowEffect, QApplication,
    QVBoxLayout, QLabel, QSizePolicy
)
from PyQt5.QtCore import (
    Qt, QPropertyAnimation, QRect,
    QEasingCurve, QTimer
)
from PyQt5.QtGui import QColor, QFont


class FontSelector(QWidget):
    """
    Ultimate Stable Apple Segmented Control
    - 按钮等宽
    - 高亮绝对定位
    - 动画稳定
    - 结构干净
    """

    def __init__(self, callback=None):
        super().__init__()

        self.callback = callback
        self.current_index = 1
        self.button_count = 4
        self.setMinimumWidth(248)
        self.setMaximumWidth(320)
        self.setFixedHeight(38)
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        self.setAttribute(Qt.WA_StyledBackground, True)

        # ===== 胶囊背景 =====
        self.setStyleSheet("""
            QWidget {
                background: rgba(255,255,255,0.6);
                border-radius: 19px;
                border: 1px solid rgba(255,255,255,0.85);
            }
        """)

        self.layout = QHBoxLayout(self)
        self.layout.setContentsMargins(6, 6, 6, 6)
        self.layout.setSpacing(0)

        self.buttons = []
        labels = ["小", "标准", "大", "超大"]

        for i, text in enumerate(labels):
            btn = QPushButton(text)
            btn.setCursor(Qt.PointingHandCursor)
            btn.setFlat(True)

            # 等宽关键
            btn.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

            # 固定字体
            font = QFont()
            font.setPointSize(14)
            btn.setFont(font)

            btn.setStyleSheet("""
                QPushButton {
                    border: none;
                    background: transparent;
                    color: #333;
                }
                QPushButton:hover {
                    color: #007aff;
                }
            """)

            btn.clicked.connect(lambda _, idx=i: self.select(idx))
            self.layout.addWidget(btn)
            btn.raise_()
            self.buttons.append(btn)

        # ===== 高亮块 =====
        self.highlight = QWidget(self)
        self.highlight.setAttribute(Qt.WA_TransparentForMouseEvents, True)

        self.highlight.setStyleSheet("""
            QWidget {
                background: qlineargradient(
                    x1:0, y1:0, x2:0, y2:1,
                    stop:0 rgba(255,255,255,0.95),
                    stop:1 rgba(255,255,255,0.8)
                );
                border-radius: 999px;
                border: 1px solid rgba(255,255,255,0.95);
            }
        """)

        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(25)
        shadow.setOffset(0, 2)
        shadow.setColor(QColor(0, 0, 0, 40))
        self.highlight.setGraphicsEffect(shadow)

        self.highlight.lower()

        # 动画
        self.animation = QPropertyAnimation(self.highlight, b"geometry")
        self.animation.setDuration(220)
        self.animation.setEasingCurve(QEasingCurve.OutCubic)

        self._update_text_color()
        QTimer.singleShot(0,self._apply_outer_style)
        QTimer.singleShot(0, self._sync_position)

    def _apply_outer_style(self):
        radius = self.height() / 2
        self.setStyleSheet(f"""
            QWidget {{
                background: rgba(255,255,255,0.6);
                border-radius: {radius}px;
                border: 1px solid rgba(255,255,255,0.85);
            }}
        """)
    # ===============================
    # 精准计算位置（绝对稳定）
    # ===============================
    def _calculate_rect(self, index):

        left = self.layout.contentsMargins().left()
        top = self.layout.contentsMargins().top()
        height = self.height() - top * 2

        total_width = self.width() - left * 2
        segment_width = total_width / self.button_count

        x = left + segment_width * index

        return QRect(int(x), int(top),
                     int(segment_width),
                     int(height))

    def _sync_position(self):
        rect = self._calculate_rect(self.current_index)
        self.highlight.setGeometry(rect)

        # 动态圆角（真正胶囊）
        radius = rect.height() / 2
        self.highlight.setStyleSheet(f"""
            QWidget {{
                background: qlineargradient(
                    x1:0, y1:0, x2:0, y2:1,
                    stop:0 rgba(255,255,255,0.95),
                    stop:1 rgba(255,255,255,0.8)
                );
                border-radius: {radius}px;
                border: 1px solid rgba(255,255,255,0.95);
            }}
        """)

    # ===============================
    # 点击选择
    # ===============================
    def select(self, index):

        if index == self.current_index:
            return

        self.current_index = index
        self._update_text_color()

        target = self._calculate_rect(index)

        self.animation.stop()
        self.animation.setStartValue(self.highlight.geometry())
        self.animation.setEndValue(target)
        self.animation.start()

        if self.callback:
            self.callback(self.current_size())

    # ===============================
    # 更新文字颜色
    # ===============================
    def _update_text_color(self):
        for i, btn in enumerate(self.buttons):
            if i == self.current_index:
                btn.setStyleSheet("""
                    QPushButton {
                        border: none;
                        background: transparent;
                        color: #007aff;
                        font-weight: 500;
                    }
                """)
            else:
                btn.setStyleSheet("""
                    QPushButton {
                        border: none;
                        background: transparent;
                        color: #333;
                    }
                    QPushButton:hover {
                        color: #007aff;
                    }
                """)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._apply_outer_style()
        self._sync_position()

    def current_size(self):
        return [13, 15, 18, 22][self.current_index]


# =========================================================
# 单文件测试
# =========================================================
if __name__ == "__main__":

    QApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)
    app = QApplication(sys.argv)

    window = QWidget()
    window.setWindowTitle("Ultimate Apple Segmented Control")
    window.resize(666, 366)

    layout = QVBoxLayout(window)
    layout.setAlignment(Qt.AlignCenter)
    layout.setSpacing(38)

    preview = QLabel("这是字体预览  Text Preview 123")
    preview.setAlignment(Qt.AlignCenter)

    font = preview.font()
    font.setPointSize(15)
    preview.setFont(font)

    # 只改变 preview 字体
    def change_font(size):
        f = preview.font()
        f.setPointSize(size)
        preview.setFont(f)

    selector = FontSelector(callback=change_font)

    layout.addWidget(preview)
    layout.addWidget(selector)

    window.show()
    sys.exit(app.exec_())
