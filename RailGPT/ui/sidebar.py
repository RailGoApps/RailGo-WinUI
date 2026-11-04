# ui/sidebar.py

from PyQt5.QtWidgets import (
    QListWidget,
    QListWidgetItem,
    QMenu,
    QInputDialog,
    QMessageBox
)
from PyQt5.QtCore import pyqtSignal, Qt


class Sidebar(QListWidget):
    """
    Left Sidebar Conversation List (RailGPT Final)

    Features:
    ✅ Safe ID parsing
    ✅ Apple rounded highlight
    ✅ Keeps selection after refresh
    ✅ Right-click rename
    ✅ Right-click delete
    """

    session_selected = pyqtSignal(int)

    def __init__(self, store):
        super().__init__()

        self.store = store
        self.current_cid = None
        self.setSpacing(2)
        self.setUniformItemSizes(False)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setVerticalScrollMode(QListWidget.ScrollPerPixel)

        # Apple-like style
        self.setStyleSheet("""
        QListWidget {
            background: transparent;
            border: none;
            outline: none;   /* 🔥 去掉灰色虚线框 */
        }

        QListWidget::item {
            padding: 11px 12px;
            margin: 4px;
            border-radius: 12px;
        }

        QListWidget::item:selected {
            background: #e8f0ff;
            color: #000;
        }

        QListWidget::item:hover {
            background: #f2f2f2;
        }
        """)

        # Enable right-click menu
        self.setContextMenuPolicy(Qt.CustomContextMenu)
        self.customContextMenuRequested.connect(self.show_context_menu)

        self.refresh()
        self.itemClicked.connect(self.on_click)

    # ======================================================
    # Refresh sidebar list
    # ======================================================
    def refresh(self):
        """
        Reload conversation titles from ConversationStore.
        Keep current selection.
        """

        old = self.current_cid

        self.clear()

        for row in self.store.list_conversations():
            item = QListWidgetItem(row)
            self.addItem(item)

            cid = self._safe_parse_id(row)

            if cid == old:
                item.setSelected(True)

    # ======================================================
    # Safe ID parsing
    # ======================================================
    def _safe_parse_id(self, text: str):
        """
        Parse "[001] title" safely.
        """
        try:
            if text.startswith("[") and "]" in text:
                head = text.split("]")[0]
                return int(head[1:])
        except:
            return None

        return None

    # ======================================================
    # Click handler
    # ======================================================
    def on_click(self, item):
        """
        User clicked one conversation.
        Emit cid.
        """

        text = item.text()
        cid = self._safe_parse_id(text)

        if cid is None:
            return

        self.current_cid = cid
        self.session_selected.emit(cid)

    # ======================================================
    # Right Click Menu
    # ======================================================
    def show_context_menu(self, position):

        item = self.itemAt(position)
        if not item:
            return

        text = item.text()
        cid = self._safe_parse_id(text)

        if cid is None:
            return

        from PyQt5.QtWidgets import QMenu, QFileDialog, QMessageBox
        from PyQt5.QtCore import Qt

        menu = QMenu(self)

        # 关键：去系统边框
        menu.setWindowFlags(
            menu.windowFlags() | Qt.FramelessWindowHint | Qt.NoDropShadowWindowHint
        )

        # 关键：允许透明背景
        menu.setAttribute(Qt.WA_TranslucentBackground)

        menu.setStyleSheet("""
            QMenu {
                background-color: rgba(255, 255, 255, 240);
                border-radius: 16px;
                padding: 8px;
                border: none;
            }

            QMenu::item {
                padding: 8px 26px;
                border-radius: 10px;
            }

            QMenu::item:selected {
                background-color: #e8f0ff;
            }

            QMenu::item[danger="true"]:selected {
                background-color: #ff3b30;
                color: white;
            }
        """)

        # ======================
        # 菜单项
        # ======================

        rename_action = menu.addAction("✏️ 重命名")
        delete_action = menu.addAction("🗑 删除")
        delete_action.setProperty("danger", True)

        menu.addSeparator()

        export_action = menu.addAction("📤 导出为 Markdown")

        # ======================
        # 执行
        # ======================

        action = menu.exec_(self.mapToGlobal(position))

        if action == rename_action:
            self._rename(cid)

        elif action == delete_action:
            self._delete(cid)

        elif action == export_action:

            default_name = f"{cid:03d}.md"

            file_path, _ = QFileDialog.getSaveFileName(
                self,
                "导出对话",
                default_name,
                "Markdown Files (*.md)"
            )

            if file_path:
                saved = self.store.export_markdown(cid, save_path=file_path)

                if saved:
                    QMessageBox.information(
                        self,
                        "导出成功",
                        f"已保存到：\n{saved}"
                    )
                else:
                    QMessageBox.warning(
                        self,
                        "导出失败",
                        "未能导出该对话。"
                    )

    # ======================================================
    # Rename
    # ======================================================
    def _rename(self, cid):

        text, ok = QInputDialog.getText(
            self,
            "重命名对话",
            "请输入新标题："
        )

        if ok and text.strip():
            self.store.rename_conversation(cid, text.strip())
            self.refresh()

    # ======================================================
    # Delete
    # ======================================================
    def _delete(self, cid):

        reply = QMessageBox.question(
            self,
            "确认删除",
            f"确定删除对话 {cid:03d} 吗？",
            QMessageBox.Yes | QMessageBox.No
        )

        if reply == QMessageBox.Yes:
            self.store.delete_conversation(cid)

            if self.current_cid == cid:
                self.current_cid = None

            self.refresh()
