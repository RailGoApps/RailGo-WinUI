# s2s_leftTicket_12306.py
"""
🎫 12306 LeftTicket Tool (Ultimate Working Version)

API:
    https://kyfw.12306.cn/otn/leftTicket/queryG

Supports:
- 成人票 purpose_codes=ADULT
- 学生票 purpose_codes=0X00
- StationDict 自动站名→电报码
- RailGo Store 缓存
- Session + Cookies + Headers 防止 HTML Block
- Pretty 输出完整余票信息

Author:
- Student Project / AIagent Railway Tool
"""

from __future__ import annotations
import requests
import json
import time
from typing import Any, Dict, List, Optional

from tools.rail.rail_12306_store import Rail12306Store
from tools.rail.station_dict import station_dict
from tools.rail.http_12306 import query_leftticket
# ============================================================
# 12306 LeftTicket Seat Index (Full Version, 2026 Stable)
#
# queryG result string: f = line.split("|")
#
# After control fields: ...|1|0|
# Seat block starts.
#
# PreferredFirst (优选一等座) is officially enabled in 2026+
# Stable position index = 21 (京沪全线验证)
# ============================================================

SEAT_INDEX_FULL = {
    # ======= High Speed Seats =======
    "商务座": 32,
    "优选一等座": 20,   # ⭐PreferredFirst 官方启用字段
    "一等座": 31,
    "二等座": 30,
    "无座": 26,

    # ======= Sleeper (EMU) =======
    "动卧": 33,

    # ======= Classic Train Seats =======
    "高级软卧": 22,
    "软卧": 23,
    "硬卧": 28,
    "硬座": 29,

    # ======= Others =======
    "特等座": 25,
    "高级动卧": 24,
}

def norm_seat(x: str) -> str:
    """
    Normalize seat availability:

    "" / None / "--" → "—"   (该席别不存在)
    "无"            → "无票"
    "有"            → "有票"
    "数字"          → 剩余张数
    """

    if x is None:
        return "—"

    x = str(x).strip()

    if x in ("", "--", "—"):
        return "—"

    if x == "无":
        return "无票"

    if x == "有":
        return "有票"

    return x

def parse_train_line(line: str) -> Dict[str, Any]:
    """
    Parse one train result line from 12306 queryG.

    Key upgrade:
    - keep from_code/to_code for correct station rendering
    - full seat parsing already supported
    """

    f = line.split("|")

    train_no = f[3]

    from_code = f[6]   # 出发站电报码
    to_code   = f[7]   # 到达站电报码

    dep_time = f[8]
    arr_time = f[9]
    duration = f[10]

    seat_info = {}
    for seat_name, idx in SEAT_INDEX_FULL.items():
        seat_info[seat_name] = norm_seat(f[idx]) if idx < len(f) else "—"

    return {
        "train": train_no,
        "from_code": from_code,
        "to_code": to_code,
        "dep": dep_time,
        "arr": arr_time,
        "dur": duration,
        "seats": seat_info,
        "secretStr": f[0],
    }
# ============================================================
# 3) Real Fetch Function (Session Safe)
# ============================================================
def fetch_leftticket(date: str, dep: str, arr: str, purpose: str, psw=None) -> Dict[str, Any]:
    """
    Real request to 12306 queryG.

    ✅ Uses global shared session (TCP reuse)
    ✅ Uses retry/backoff + HTML block detection
    ✅ JSON safe return
    """

    resp = query_leftticket(
        date=date,
        dep=dep,
        arr=arr,
        purpose=purpose,
        psw=psw
    )

    # resp is:
    # {
    #   "ok": True,
    #   "status": 200,
    #   "json": {...}
    # }

    if resp["ok"]:
        return resp["json"]

    # Otherwise return same error payload format
    return {
        "error": resp["error"],
        "http": resp.get("status"),
        "preview": resp.get("preview", "")
    }
# ============================================================
# 4) Tool Entry
# ============================================================

store = Rail12306Store("rail12306.db")


def tool_s2s_leftticket(query: str) -> Dict[str, Any]:
    """
    Query format:
      北京-上海｜2026-02-15｜学生
      福州-徐州东｜2026-02-16｜成人
    """

    parts = [x.strip() for x in query.split("｜")]
    if len(parts) < 2:
        raise ValueError("Format must be A-B｜date｜(成人/学生)")

    od = parts[0]
    date = parts[1]
    tag = parts[2] if len(parts) >= 3 else "成人"

    if "-" not in od:
        raise ValueError("OD must be A-B")

    dep_name, arr_name = od.split("-")

    # StationDict bootstrap if empty
    if len(station_dict) < 100:
        station_dict.bootstrap()

    dep = station_dict.telecode_of(dep_name)
    arr = station_dict.telecode_of(arr_name)

    if not dep or not arr:
        raise ValueError(f"Station resolve failed: {dep_name},{arr_name}")

    purpose = "0X00" if "学生" in tag else "ADULT"

    # Cached query
    res = store.cached_query(
        api="left_ticket",
        date=date.replace("-", ""),
        dep=dep,
        arr=arr,

        # ✅必须兼容 store 的 fetch_func(**kw)
        fetch_func=lambda date, dep, arr, **kw: fetch_leftticket(
            date=date[:4] + "-" + date[4:6] + "-" + date[6:],
            dep=dep,
            arr=arr,
            purpose=purpose,
            psw=store.psw
        ),

        cache_ttl=2*3600,
    )

    return {
        "query": query,
        "source": res["source"],
        "payload": res["payload"],
    }


# ============================================================
# 5) Pretty Output
# ============================================================

# ============================================================
# 5) Pretty Output (Ultimate Full Seat Adaptive Version)
# ============================================================

from typing import Dict, Any, List
from collections import defaultdict
import random


# ============================================================
# Seat Display Order (UI Priority)
# ============================================================

SEAT_DISPLAY_ORDER = [
    # High-speed EMU priority
    "商务座",
    "特等座",
    "优选一等座",
    "一等座",
    "二等座",
    "动卧",
    "无座",

    # Sleeper system
    "高级软卧",
    "软卧",
    "硬卧",

    # Seat system
    "软座",
    "硬座",

    # Rare
    "一人软包",
    "二人软包",
    "包厢座",
    "混编硬卧",
]


# ============================================================
# 5) Ultimate Pretty Output Module (Full Working)
# ============================================================

from typing import Dict, Any, List
from collections import defaultdict
import random


# ============================================================
# Utils
# ============================================================

def dur_to_min(dur: str):
    """HH:MM → minutes"""
    if not dur or ":" not in dur:
        return None
    h, m = dur.split(":")
    return int(h) * 60 + int(m)


def seat_num(x):
    """
    Seat value normalization:
    - "有票" → 20
    - number → int
    - "无"/"0" → 0
    - "—"/None → None
    """
    if x in ("--", "—", None):
        return None
    if x in ("无", "0", "无票"):
        return 0
    if x == "有票":
        return 20
    try:
        return int(x)
    except:
        return 0


def has_ticket(seats: dict):
    """Any seat type available → True"""
    for _, v in seats.items():
        n = seat_num(v)
        if n and n > 0:
            return True
    return False


# ============================================================
# Dynamic Seat Rendering (核心)
# ============================================================
def render_seats(seats: Dict[str, str]) -> str:
    parts = []

    for name, val in seats.items():
        if val in ("—", "", None):
            continue

        # 无票也可以显示（否则用户不知道有没有该席别）
        if val in ("无", "无票", "0"):
            parts.append(f"{name}:无票")
        else:
            parts.append(f"{name}:{val}")

    return "  ".join(parts)


# ============================================================
# Mini Score System (0~100)
# ============================================================

def mini_score(t):
    """
    Score based on:
    - Train type priority
    - Duration shorter is better
    - Seat availability bonus
    """

    code = t["train"]
    dur = dur_to_min(t["dur"]) or 999

    score = 0

    # --- Train Type Base ---
    if code.startswith("G"):
        score += 75
    elif code.startswith("D"):
        score += 65
    elif code.startswith("C"):
        score += 60
    elif code.startswith("Z"):
        score += 45
    elif code.startswith("T"):
        score += 35
    else:
        score += 25

    # --- Duration Bonus ---
    score += max(0, 30 - dur * 0.25)

    # --- Seat Bonus (商务 > 优选一等 > 一等 > 二等 > 无座) ---
    s = t["seats"]
    score += (seat_num(s.get("商务座")) or 0) * 0.12
    score += (seat_num(s.get("优选一等座")) or 0) * 0.08
    score += (seat_num(s.get("一等座")) or 0) * 0.05
    score += (seat_num(s.get("二等座")) or 0) * 0.03
    score += (seat_num(s.get("无座")) or 0) * 0.01

    return int(min(score, 100))


def score10(score100):
    """UI style 9.9/10"""
    return round(score100 / 10, 1)


# ============================================================
# Tier Classification (标杆等级)
# ============================================================

PEAK_DELTA_MIN = 6


def is_super_g(train_no: str) -> bool:
    """G<400 = 巅峰车"""
    if not train_no.startswith("G"):
        return False
    try:
        return int(train_no[1:]) < 400
    except:
        return False


def tier(score: int, train_no: str, dur_min: int,
         top10_trains: set,
         best_dur: int):

    # --- 京沪巅峰车 ---
    if is_super_g(train_no):
        return "🥇⭐ 巅峰标杆"

    # --- Top10 fastest + high score ---
    if (
        train_no in top10_trains
        and score >= 90
        and dur_min is not None
        and dur_min <= best_dur + PEAK_DELTA_MIN
    ):
        return "🥇⭐ 巅峰标杆"

    # --- General tiers ---
    if score >= 85:
        return "🏆 顶级标杆"
    elif score >= 60:
        return "⚡ 优质快车"
    elif score >= 35:
        return "🚄 普通较快"
    else:
        return "🐢 慢车慎选"


# ============================================================
# Tag System (最多4个)
# ============================================================

def make_tags(x, best_dur):

    tags = []

    def add(t):
        if t not in tags:
            tags.append(t)

    train = x["train"]
    dur = x["dur_min"]
    available = x["available"]
    tier_name = x["tier"]

    # --- Identity ---
    if tier_name == "🥇⭐ 巅峰标杆":
        add("标准答案")
    elif tier_name == "🏆 顶级标杆":
        add("顶级推荐")
    elif tier_name == "⚡ 优质快车":
        add("速度优选")
    else:
        add("备选方案")

    # --- Speed Tag (巅峰保护) ---
    if dur is not None:

        if dur <= best_dur + 2:
            add("极速最快")

        elif dur <= best_dur + 15:
            add("快速旗舰")

        else:
            # ⭐巅峰车不允许负面语义
            if tier_name == "🥇⭐ 巅峰标杆":
                add("非最快王者")
            else:
                add("耗时较长")

    # --- Ticket ---
    if available:
        add("余票尚可")
    else:
        add("候补优先")

    return tags[:4]


# ============================================================
# Time Bucket (场景分段)
# ============================================================

def bucket(dep: str):
    h = int(dep.split(":")[0])
    if 0 <= h < 6:
        return "🌙 深夜"
    if 6 <= h < 10:
        return "🌅 早高峰"
    if 10 <= h < 16:
        return "☀️ 白天"
    if 16 <= h < 20:
        return "🌇 傍晚"
    return "🌙 晚间"


# ============================================================
# Stratified Sample (最多5条)
# ============================================================

def stratified_speed_sample(group, k=5):

    if len(group) <= k:
        return group

    group = sorted(group, key=lambda x: x["dur_min"])

    fastest = group[:1]
    slowest = group[-1:]

    middle = group[1:-1]
    remain = random.sample(middle, min(k - 2, len(middle)))

    return fastest + remain + slowest


# ============================================================
# ✅ Ultimate Pretty LeftTicket Output
# ============================================================
def pretty_leftticket(result: Dict[str, Any]) -> str:
    payload = result["payload"]

    if "error" in payload:
        return f"⚠️ ERROR: {payload['error']}"

    data = payload.get("data", {})
    trains_raw = data.get("result", [])
    station_map = data.get("map", {})

    trains = [parse_train_line(x) for x in trains_raw]

    if not trains:
        return "⚠️ No trains found."

    # ============================================================
    # Baseline (best duration)
    # ============================================================

    durations = [dur_to_min(t["dur"]) for t in trains if dur_to_min(t["dur"])]
    best_dur = min(durations) if durations else 999

    # ============================================================
    # Enrich trains (NO raw split anymore)
    # ============================================================

    enriched = []
    for t in trains:

        # ✅ station name resolve (important for same-city stations)
        from_name = station_map.get(t["from_code"], t["from_code"])
        to_name = station_map.get(t["to_code"], t["to_code"])

        dur_min = dur_to_min(t["dur"])
        score = mini_score(t)

        enriched.append({
            **t,
            "from_name": from_name,
            "to_name": to_name,
            "dur_min": dur_min,
            "score": score,
            "available": has_ticket(t["seats"]),
        })

    # ============================================================
    # Top10
    # ============================================================

    top10 = set(
        [x["train"] for x in sorted(enriched, key=lambda z: z["score"], reverse=True)[:10]]
    )

    # ============================================================
    # Tier + Tags
    # ============================================================

    for x in enriched:
        x["tier"] = tier(
            x["score"],
            x["train"],
            x["dur_min"],
            top10,
            best_dur
        )
        x["tags"] = make_tags(x, best_dur)

    # ============================================================
    # Render Output
    # ============================================================

    lines = []
    lines.append("=" * 70)
    lines.append("🎫 12306 LeftTicket Directory (Ultimate Full Seat)")
    lines.append("=" * 70)
    lines.append(f"Query : {result['query']}")
    lines.append(f"Source: {result['source']}")
    lines.append("-" * 70)

    # ============================================================
    # Peaks First (must show)
    # ============================================================

    peaks = [x for x in enriched if x["tier"] == "🥇⭐ 巅峰标杆"]

    peak_ids = set()
    if peaks:
        lines.append("\n🏔 巅峰标杆列车（置顶展示）")
        lines.append("-" * 70)

        for x in sorted(peaks, key=lambda z: z["score"], reverse=True):
            peak_ids.add(x["train"])

            lines.append(f"⭐ {x['train']} {x['dep']}→{x['arr']} ⏱{x['dur']}")
            lines.append(f"   🚉 {x['from_name']} → {x['to_name']}")
            lines.append(f"   💺 {render_seats(x['seats'])}")
            lines.append(
                f"   🏅评分:{score10(x['score'])}/10 "
                f"标签:{'/'.join(x['tags'])}"
            )
            lines.append("")


    # ============================================================
    # Buckets (exclude peaks)
    # ============================================================

    buckets = defaultdict(list)
    for x in enriched:
        if x["train"] in peak_ids:
            continue
        buckets[bucket(x["dep"])].append(x)

    for name, group in buckets.items():

        avail = [x for x in group if x["available"]]
        if not avail:
            continue

        sampled = stratified_speed_sample(avail, k=5)

        lines.append(f"\n{name} 推荐车次")
        lines.append("-" * 70)

        for x in sampled:
            lines.append(f"🚆 {x['train']} {x['dep']}→{x['arr']} ⏱{x['dur']}")
            lines.append(f"   🚉 {x['from_name']} → {x['to_name']}")
            lines.append(f"   💺 {render_seats(x['seats'])}")
            lines.append(f"   标签:{'/'.join(x['tags'])}")
            lines.append("")

    # ============================================================
    # Footer
    # ============================================================

    lines.append("=" * 70)
    lines.append("📌 Data Source: Official 12306.cn queryG API (Real-time)")
    lines.append("=" * 70)

    return "\n".join(lines)
# ============================================================
# 6) Local Real Test
# ============================================================

if __name__ == "__main__":

    q1 = "北京南-上海虹桥｜2026-02-19｜成人"
    res1 = tool_s2s_leftticket(q1)
    print(pretty_leftticket(res1))

