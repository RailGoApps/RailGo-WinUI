"""
station_dict.py

长期车站字典数据库（Station Telecode Dictionary）

数据来源：
    https://kyfw.12306.cn/otn/resources/js/framework/station_name.js

存储目标：
    - 中文站名
    - 电报码 telecode
    - 拼音
    - 缩写
    - 城市信息
    - 原始字段 raw

更新策略：
    - 每季度更新一次（1/4/7/10 月 26 日触发）
    - 本地优先，过期才联网刷新
"""

import os
import json
import requests
from datetime import datetime
from app_runtime import writable_path


# =========================
# ✅核心 StationDict 类
# =========================

class StationDict:
    """
    长期车站字典：
        telecode -> station profile
    """

    STATION_JS_URL = (
        "https://kyfw.12306.cn/otn/resources/js/framework/station_name.js"
    )

    def __init__(self, filepath: str = None):

        if filepath is None:
            filepath = writable_path("tools", "rail", "store", "station_dict.json")

        self.filepath = filepath
        self.meta_path = filepath.replace(".json", "_meta.json")

        self.data = {}
        self.meta = {}

        self._ensure_dir()
        self._load_local()

        # 🔥 自动初始化
        if not self.data:
            print("[StationDict] Empty dict, auto bootstrap...")
            self.bootstrap(force=True)

    # -------------------------
    # 文件系统保障
    # -------------------------

    def _ensure_dir(self):
        folder = os.path.dirname(self.filepath)
        os.makedirs(folder, exist_ok=True)

    # -------------------------
    # 本地加载与保存
    # -------------------------

    def _load_local(self):
        """
        加载本地 station_dict.json
        """
        if os.path.exists(self.filepath):
            try:
                with open(self.filepath, "r", encoding="utf-8") as f:
                    self.data = json.load(f)
            except Exception:
                self.data = {}

        if os.path.exists(self.meta_path):
            try:
                with open(self.meta_path, "r", encoding="utf-8") as f:
                    self.meta = json.load(f)
            except Exception:
                self.meta = {}

    def save(self):
        """
        保存 station_dict.json
        """
        with open(self.filepath, "w", encoding="utf-8") as f:
            json.dump(self.data, f, ensure_ascii=False, indent=2)

        with open(self.meta_path, "w", encoding="utf-8") as f:
            json.dump(self.meta, f, ensure_ascii=False, indent=2)

    # -------------------------
    # 更新策略：季度刷新
    # -------------------------

    def _quarter_tag(self, dt: datetime):
        """
        返回季度标签：
            2026-Q1, 2026-Q2 ...
        """
        q = (dt.month - 1) // 3 + 1
        return f"{dt.year}-Q{q}"

    def _should_refresh(self):
        """
        判断是否需要刷新：
        - meta 中没有 last_update
        - 当前季度已变化
        - 且当前日期 >= 26 号
        """
        now = datetime.now()

        last_q = self.meta.get("quarter")
        now_q = self._quarter_tag(now)

        # 从未更新过
        if not last_q:
            return True

        # 季度变化了
        if now_q != last_q and now.day >= 26:
            return True

        return False

    # -------------------------
    # Bootstrap：从12306抓取
    # -------------------------

    def bootstrap(self, force=False):
        """
        从 12306 下载 station_name.js 并解析。

        force=True 可强制刷新。
        """
        if not force and not self._should_refresh():
            return

        print("[StationDict] Bootstrapping from 12306 station_name.js ...")

        try:
            resp = requests.get(self.STATION_JS_URL, timeout=15)
            resp.raise_for_status()
            text = resp.text
        except Exception as e:
            print("[StationDict] Download failed:", e)
            return

        # station_names='@bjb|北京北|VAP|beijingbei|bjb|0|0357|北京|||...'
        try:
            raw = text.split("='")[1].split("';")[0]
        except Exception:
            print("[StationDict] Parse error: cannot extract station_names")
            return

        items = raw.split("@")

        new_data = {}

        for item in items:
            if not item.strip():
                continue

            parts = item.split("|")

            # 标准格式至少 8 个字段
            if len(parts) < 8:
                continue

            key = parts[0]
            name = parts[1]
            telecode = parts[2]
            pinyin = parts[3]
            abbr = parts[4]
            idx = parts[5]
            admin_code = parts[6]
            city = parts[7]

            # ⭐忠实存储所有字段 raw
            new_data[telecode] = {
                "name": name,
                "telecode": telecode,
                "pinyin": pinyin,
                "abbr": abbr,
                "city": city,
                "admin_code": admin_code,
                "idx": idx,
                "key": key,
                "raw": parts
            }

        # 更新写入
        self.data = new_data

        now = datetime.now()
        self.meta = {
            "last_update": now.strftime("%Y-%m-%d %H:%M:%S"),
            "quarter": self._quarter_tag(now),
            "count": len(new_data),
            "source": self.STATION_JS_URL
        }

        self.save()

        print(f"[StationDict] Loaded {len(new_data)} stations ✅")

    # -------------------------
    # 查询接口（上层调用）
    # -------------------------

    def lookup(self, telecode: str):
        """
        telecode -> station info dict
        """
        return self.data.get(telecode)

    def name_of(self, telecode: str):
        """
        telecode -> 中文站名
        """
        info = self.lookup(telecode)
        return info["name"] if info else None

    def telecode_of(self, name: str):
        """
        中文站名 -> telecode
        """
        for code, info in self.data.items():
            if info["name"] == name:
                return code
        return None

    def __len__(self):
        return len(self.data)


# =========================
# ✅单例：station_dict
# =========================

station_dict = StationDict()


# =========================
# ✅测试入口
# =========================

"""if __name__ == "__main__":
    print("[StationDict] Local loaded:", len(station_dict))

    # 第一次运行需要 bootstrap
    #station_dict.bootstrap()

    # 测试查询
    print("\n--- Lookup NKH ---")
    print(station_dict.lookup("NKH"))

    print("\n--- Name of XKS ---")
    print(station_dict.name_of("XKS"))

    print("\n--- Name of UUH ---")
    print(station_dict.name_of("UUH"))

    print("\n--- Telecode of 厦门北 ---")
    print(station_dict.telecode_of("厦门北"))

    print("\n--- Telecode of 徐州东 ---")
    print(station_dict.telecode_of("徐州东"))
"""
