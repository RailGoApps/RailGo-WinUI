# memory/conversation_store.py

import os
import json
import re
from datetime import datetime
from typing import Any, Optional, List, Dict, Callable
import threading


DEFAULT_NEW_TITLE = "New Chat"
PLACEHOLDER_TITLES = {"", DEFAULT_NEW_TITLE, "新对话"}

class ConversationStore:
    """
    Conversation Archive Module (RailGPT v2.7)

    ✅ Supports:
    - multi-session storage (001.json, 002.json...)
    - index.json sidebar list
    - auto title generation via LLM
    - /new /load /list /rename /export
    - restore SessionMemory
    """

    def __init__(self, root_dir="conversations", llm=None):
        self.root_dir = root_dir
        os.makedirs(self.root_dir, exist_ok=True)

        self.index_path = os.path.join(self.root_dir, "index.json")

        # Optional LLM (for title summarization)
        self.llm = llm

        # Current session state
        self.current_id: Optional[int] = None
        self.title: Optional[str] = None
        self.messages: List[Dict] = []
        self.memory_state: Dict = {}
        self._lock = threading.RLock()
        self._title_jobs_inflight: set[int] = set()
        self._change_callback: Optional[Callable[[], None]] = None

        # Load index on startup
        self._load_index()

    # ============================================================
    # Index Management
    # ============================================================

    def _load_index(self):
        """
        Load index.json if exists, else init empty.
        """
        if not os.path.exists(self.index_path):
            self.index = []
            self._save_index()
            return

        try:
            with open(self.index_path, "r", encoding="utf-8") as f:
                self.index = json.load(f)
        except:
            self.index = []

    def _save_index(self):
        """
        Persist index.json
        """
        with open(self.index_path, "w", encoding="utf-8") as f:
            json.dump(self.index, f, ensure_ascii=False, indent=2)

    def set_on_change(self, callback: Optional[Callable[[], None]]):
        self._change_callback = callback

    def _notify_changed(self):
        callback = self._change_callback
        if not callable(callback):
            return

        try:
            callback()
        except Exception:
            pass

    def _update_index_entry(self, cid: int, title: str):
        """
        Update or insert index entry.
        """
        now = datetime.now().strftime("%Y-%m-%d %H:%M")

        for item in self.index:
            if item["id"] == cid:
                item["title"] = title
                item["updated"] = now
                self._save_index()
                return

        # New entry
        self.index.append({
            "id": cid,
            "title": title,
            "updated": now
        })

        self.index.sort(key=lambda x: x["updated"],reverse=True)
        self._save_index()

    # ============================================================
    # Path Helpers
    # ============================================================

    def _id_to_path(self, cid: int) -> str:
        return os.path.join(self.root_dir, f"{cid:03d}.json")

    def _next_id(self) -> int:
        """
        Auto-increment ID.
        """
        existing = []

        for fn in os.listdir(self.root_dir):
            if fn.endswith(".json") and fn[:3].isdigit():
                existing.append(int(fn[:3]))

        return max(existing, default=0) + 1

    # ============================================================
    # Title Generator (LLM Optional)
    # ============================================================

    def _auto_title(self, first_user_text: str) -> str:
        """
        Generate a short ChatGPT-like conversation title.

        If llm exists → summarize title
        Else → fallback to truncated user text
        """

        raw = first_user_text.strip()

        if not raw:
            return DEFAULT_NEW_TITLE

        # Fallback if no LLM
        if not self.llm:
            return raw[:18] + ("..." if len(raw) > 18 else "")
        if hasattr(self.llm, "is_configured") and not self.llm.is_configured():
            return raw[:18] + ("..." if len(raw) > 18 else "")

        # LLM summarization prompt
        prompt = [
            {
                "role": "system",
                "content": (
                    "你是RailGPT的会话标题生成器。\n"
                    "请根据用户第一句话生成一个简短标题（不超过10字）。\n"
                    "不要标点，不要解释，只输出标题。"
                )
            },
            {
                "role": "user",
                "content": raw
            }
        ]

        try:
            title = self.llm.generate(prompt).strip()
            title = title.replace("标题：", "").strip()
            return title[:10]
        except:
            return raw[:10] + "..."

    # ============================================================
    # Save / Load Conversation
    # ============================================================

    def _save(self):
        """
        Save current conversation JSON.
        """
        with self._lock:
            if self.current_id is None:
                return

            cid = self.current_id
            title = self.title or ""
            messages = list(self.messages)
            memory_state = dict(self.memory_state)

        path = self._id_to_path(cid)

        data = {
            "id": cid,
            "title": title,
            "updated": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "messages": messages,
            "memory_state": memory_state,
        }

        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

        # Sync index
        self._update_index_entry(cid, title)
        self._notify_changed()

    def new_conversation(self, first_user_text: str = "") -> int:
        """
        Start a new conversation.
        Auto-generate title.
        """
        with self._lock:
            cid = self._next_id()
            self.current_id = cid
            self.messages = []
            self.title = DEFAULT_NEW_TITLE
            self.memory_state = {}
        self._save()

        if first_user_text.strip():
            self._schedule_title_update(first_user_text, cid=cid)

        return cid

    def load_conversation(self, cid: int) -> bool:
        """
        Load conversation into memory.
        """
        path = self._id_to_path(cid)

        if not os.path.exists(path):
            return False

        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)

        with self._lock:
            self.current_id = cid
            self.title = data.get("title", "")
            self.messages = data.get("messages", [])
            self.memory_state = data.get("memory_state", {}) if isinstance(data.get("memory_state"), dict) else {}

        return True

    # ============================================================
    # Append Messages
    # ============================================================

    def append_user(self, text: str):
        with self._lock:
            self.messages.append({"role": "user", "content": text})
            should_schedule = (
                self.current_id is not None
                and (self.title or "") in PLACEHOLDER_TITLES
                and len(self.messages) == 1
            )
            cid = self.current_id

        if should_schedule and cid is not None:
            self._schedule_title_update(text, cid=cid)

    def _schedule_title_update(self, first_user_text: str, cid: Optional[int] = None):
        text = first_user_text.strip()
        if not text:
            return

        with self._lock:
            target_cid = cid if cid is not None else self.current_id
            if target_cid is None:
                return
            if target_cid in self._title_jobs_inflight:
                return
            self._title_jobs_inflight.add(target_cid)

        threading.Thread(
            target=self._auto_title_async,
            args=(target_cid, text),
            daemon=True,
        ).start()

    def _auto_title_async(self, cid: int, first_user_text: str):
        """Generate title in background and write it back to the target conversation only."""
        try:
            title = self._auto_title(first_user_text)
        except Exception:
            title = (first_user_text.strip()[:10] + "...") if first_user_text.strip() else DEFAULT_NEW_TITLE

        path = self._id_to_path(cid)
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
            except Exception:
                data = {"id": cid, "messages": []}

            data["title"] = title
            data["updated"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)

            self._update_index_entry(cid, title)

        with self._lock:
            self._title_jobs_inflight.discard(cid)
            if self.current_id == cid:
                self.title = title

        self._notify_changed()

    def append_ai(self, text: str, attachments: Optional[List[Dict[str, Any]]] = None):
        safe_attachments = []
        for item in attachments or []:
            if not isinstance(item, dict):
                continue
            asset_id = str(item.get("asset_id") or "").strip().lower()
            kind = str(item.get("type") or "").strip()
            if kind not in {"coach_image", "route_map"} or not re.fullmatch(r"[0-9a-f]{64}", asset_id):
                continue
            safe_attachments.append(
                {
                    "type": kind,
                    "asset_id": asset_id,
                    "caption": str(item.get("caption") or "")[:200],
                    "source": str(item.get("source") or "")[:100],
                    "cache_status": str(item.get("cache_status") or "")[:30],
                    "summary": item.get("summary") if isinstance(item.get("summary"), dict) else {},
                }
            )
        with self._lock:
            message = {"role": "assistant", "content": text}
            if safe_attachments:
                message["attachments"] = safe_attachments
            self.messages.append(message)
        self._save()

    def save_session_memory(self, session) -> bool:
        if session is None or not hasattr(session, "export_state"):
            return False

        with self._lock:
            if self.current_id is None:
                return False
            try:
                self.memory_state = dict(session.export_state())
            except Exception:
                return False

        self._save()
        return True

    # ============================================================
    # List Conversations (Sidebar)
    # ============================================================

    def list_conversations(self) -> List[str]:
        """
        Return sidebar-friendly list.
        """
        rows = []
        for item in self.index:
            rows.append(f"[{item['id']:03d}] {item['title']}")

        return rows

    # ============================================================
    # Rename Conversation
    # ============================================================

    def rename_conversation(self, cid: int, new_title: str) -> bool:
        """
        Rename manually.
        """
        if not new_title.strip():
            return False

        path = self._id_to_path(cid)
        if not os.path.exists(path):
            return False

        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)

        data["title"] = new_title.strip()

        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

        # Update index
        self._update_index_entry(cid, new_title.strip())

        with self._lock:
            if self.current_id == cid:
                self.title = new_title.strip()

        self._notify_changed()

        return True

    # ============================================================
    # Export Markdown
    # ============================================================

    def suggest_export_filename(self, cid: int) -> str:
        path = self._id_to_path(cid)
        if not os.path.exists(path):
            return f"conversation_{cid:03d}.md"

        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)

        safe_title = data.get("title", f"conversation_{cid}")
        safe_title = re.sub(r'[\\/:*?"<>|]', "_", safe_title).strip() or f"conversation_{cid}"
        return f"{safe_title}.md"

    def export_markdown(self, cid: int, save_path: Optional[str] = None) -> Optional[str]:
        """
        Export conversation to Markdown file.
        If save_path provided → export there.
        Else → export to conversations folder.
        """

        path = self._id_to_path(cid)

        if not os.path.exists(path):
            return None

        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)

        # 默认文件名
        safe_title = os.path.splitext(self.suggest_export_filename(cid))[0]

        if save_path:
            md_path = save_path
        else:
            md_path = os.path.join(self.root_dir, f"{cid:03d}.md")

        lines = []
        lines.append(f"# {safe_title}")
        lines.append("")

        for m in data.get("messages", []):
            if m["role"] == "user":
                lines.append("### 👤 User")
            else:
                lines.append("### 🤖 RailGPT")

            lines.append(m["content"])
            for attachment in m.get("attachments", []):
                if not isinstance(attachment, dict):
                    continue
                caption = str(attachment.get("caption") or attachment.get("type") or "附件")
                asset_id = str(attachment.get("asset_id") or "")
                if attachment.get("type") == "coach_image":
                    lines.append(f"\n![{caption}](/api/assets/coach/{asset_id})")
                    lines.append("\n> 图片来源：RailGo；该引用在 RailGPT 本地资产库中可用。")
                elif attachment.get("type") == "route_map":
                    summary = attachment.get("summary") if isinstance(attachment.get("summary"), dict) else {}
                    lines.append(f"\n> 交互线路图：{caption}（资产 `{asset_id}`，来源 RailGo + OpenStreetMap）")
                    if summary:
                        lines.append(f"> 坐标估算折线长度：{summary.get('estimated_polyline_km', '?')} km；非营业里程。")
            lines.append("")

        with open(md_path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))

        return md_path

    # ============================================================
    # Restore SessionMemory
    # ============================================================

    def build_session_memory(self, session):
        """
        Restore SessionMemory from stored messages.
        """
        session.restore_from_messages(
            self.messages[-120:],
            session_id=self.current_id,
            memory_state=self.memory_state,
        )

    # ============================================================
    # Delete Conversation
    # ============================================================

    def delete_conversation(self, cid: int) -> bool:
        """
        Delete conversation file and update index.
        """

        path = self._id_to_path(cid)

        if not os.path.exists(path):
            return False

        try:
            os.remove(path)
        except:
            return False

        # 从 index 删除
        self.index = [x for x in self.index if x["id"] != cid]
        self._save_index()

        # 如果删的是当前会话
        with self._lock:
            self._title_jobs_inflight.discard(cid)
            if self.current_id == cid:
                self.current_id = None
                self.title = None
                self.messages = []
                self.memory_state = {}

        self._notify_changed()

        return True
