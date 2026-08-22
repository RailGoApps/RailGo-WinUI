"""
train_query.py

铁路车次 / 动车组查询与分析模块（基于 rail.re）

核心思想：
- rail.re 本质返回同一类时间序列记录：
  (date, train_no, emu_no)
- /train/{train_no} 与 /emu/{emu_no} 只是索引不同、语义不同
- 本模块统一建模后，对外提供清晰的查询与分析接口

⚠️ 数据来自第三方 rail.re，非官方，仅供铁路爱好者参考
"""

import requests
from datetime import datetime
from collections import Counter
from typing import List, Dict, Optional
from tools.rail.http_client import RAIL_RE_HEADERS,http_get
from agent.psw import AgentState,PSW
from utils.simple_lru import SimpleLRUCache
from utils.net_retry import truncated_binary_backoff

BASE_URL = "https://api.rail.re"
TIME_FMT = "%Y-%m-%d %H:%M"
_train_cache=SimpleLRUCache(512)
_emu_cache=SimpleLRUCache(512)
_psw:PSW|None=None
def set_psw(psw: PSW):
    global _psw
    _psw = psw
# =========================================================
# 1️⃣ 原始数据获取层（HTTP，仅负责“拿数据”）
# =========================================================

def fetch_train_history(train_no: str) -> List[Dict]:
    """
    按车次查询历史担当记录
    - 接口与返回结构保持不变
    - 内部增加：LRU cache + 截断二进制退避
    """

    cache_key = f"train:{train_no}"
    cached = _train_cache.get(cache_key)
    if cached is not None:
        if _psw:
            _psw.set_state(
                AgentState.CACHE_HIT,
                f"train {train_no}"
            )
        return cached

    if _psw:
        _psw.set_state(
            AgentState.CACHE_MISS,
            f"train {train_no}"
        )

    def _do_request():
        url = f"{BASE_URL}/train/{train_no}"
        r = http_get(url, timeout=10,headers=RAIL_RE_HEADERS)
        r.raise_for_status()
        return r.json()

    data = truncated_binary_backoff(_do_request)

    # ⚠️ 只有成功拿到数据，才写 cache
    _train_cache.put(cache_key, data)
    return data


def fetch_emu_history(emu_no: str) -> List[Dict]:
    """
    按动车组查询历史担当记录
    """

    cache_key = f"emu:{emu_no}"
    cached = _emu_cache.get(cache_key)
    if cached is not None:
        if _psw:
            _psw.set_state(
                AgentState.CACHE_HIT,
                f"emu {emu_no}"
            )
        return cached

    if _psw:
        _psw.set_state(
            AgentState.CACHE_MISS,
            f"emu {emu_no}"
        )

    def _do_request():
        url = f"{BASE_URL}/emu/{emu_no}"
        r = http_get(url, timeout=10,headers=RAIL_RE_HEADERS)
        r.raise_for_status()
        return r.json()

    data = truncated_binary_backoff(_do_request)

    _emu_cache.put(cache_key, data)
    return data


# =========================================================
# 2️⃣ 通用时间序列算法（核心公共能力）
# =========================================================

def _parse_time(record: Dict) -> datetime:
    return datetime.strptime(record["date"], TIME_FMT)


def get_latest_record(records: List[Dict]) -> Optional[Dict]:
    """
    获取最新一条记录（工程意义上的“今天 / 现在”）
    """
    if not records:
        return None
    return max(records, key=_parse_time)


# =========================================================
# 3️⃣ 车次视角 API（train → emu）
# =========================================================

def query_train_today(train_no: str) -> Optional[Dict]:
    """
    查询某一车次最近一次使用的动车组
    例：G1 今天用什么车？
    """
    records = fetch_train_history(train_no)
    return get_latest_record(records)


def summarize_train_history(records: List[Dict]) -> Dict:
    """
    统计车次使用动车组情况（稳定性分析）
    """
    emus = [r["emu_no"] for r in records if r.get("emu_no")]
    counter = Counter(emus)

    return {
        "total_records": len(records),
        "unique_emu_count": len(counter),
        "most_common_emus": counter.most_common(5),
    }


# =========================================================
# 4️⃣ 动车组视角 API（emu → train）
# =========================================================

def query_emu_today(emu_no: str) -> Optional[Dict]:
    """
    查询某一动车组最近一次担当的车次
    例：CR400AFZ2322 今天开什么车？
    """
    records = fetch_emu_history(emu_no)
    return get_latest_record(records)


def summarize_emu_history(records: List[Dict]) -> Dict:
    """
    统计动车组近期担当的车次情况
    """
    trains = [r["train_no"] for r in records if r.get("train_no")]
    counter = Counter(trains)

    return {
        "total_records": len(records),
        "unique_train_count": len(counter),
        "most_common_trains": counter.most_common(5),
    }

