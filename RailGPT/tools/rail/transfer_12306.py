# transfer_12306.py
"""
🚆 12306 Transfer Tool (Ultimate Version)

Supports:
- A-B｜date
- A-B｜VIA｜date

Features:
- Railgo-style store (cert 6h + query cache)
- Station name → telecode via station_dict
- Full transfer plan output (2 segments + seats + wait + total)

Official API:
https://kyfw.12306.cn/lcquery/queryG
"""

from __future__ import annotations
import requests
from typing import Any, Dict, List, Optional
import datetime

from tools.rail.rail_12306_store import Rail12306Store
from tools.rail.station_dict import station_dict
from tools.rail.http_12306 import http12306_get

# ============================================================
# Global Store
# ============================================================

store = Rail12306Store("rail12306.db")


# ============================================================
# Station Resolver (Robust)
# ============================================================

def resolve_station(x: str) -> str:
    """
    Accept:
    - Chinese station name: 南京南
    - Telecode: VNP
    """

    x = x.strip()

    # Already telecode
    if len(x) == 3 and x.isupper():
        return x

    # Lookup by Chinese name
    code = station_dict.telecode_of(x)
    if code:
        return code

    raise ValueError(f"Unknown station: {x}")


# ============================================================
# Cert Fetch Stub (12306 token placeholder)
# ============================================================

def fetch_cert_token() -> str:
    """
    12306 transfer query currently does not require a real token,
    but railgo-store expects cert refresh logic.
    """
    return f"TOKEN_{int(datetime.datetime.now().timestamp())}"


# ============================================================
# Core Fetcher: Transfer API
# ============================================================
TRANSFER_API = "https://kyfw.12306.cn/lcquery/queryG"


def fetch_transfer(date: str, dep: str, arr: str, middle: str = "", psw=None) -> Dict[str, Any]:
    """
    Real query to 12306 transfer API
    ✅ uses shared session + retry + block detect
    """

    params = {
        "train_date": date,
        "from_station_telecode": dep,
        "to_station_telecode": arr,
        "middle_station": middle,
    }

    resp = http12306_get(
        url=TRANSFER_API,
        params=params,
        timeout=15,
        retry=1,
        psw=psw
    )

    # ✅保持 tool/store 兼容：只返回 JSON payload
    if resp["ok"]:
        return resp["json"]

    # 否则返回 error payload（和 leftticket 一致）
    return {
        "error": resp["error"],
        "preview": resp.get("preview", "")
    }

def tool_transfer_query(query: str) -> Dict[str, Any]:
    """
    Query formats:
      A-B｜date
      A-B｜VIA｜date
    """

    parts = [p.strip() for p in query.split("｜")]

    if len(parts) == 2:
        route, date = parts
        via = ""
    elif len(parts) == 3:
        route, via, date = parts
    else:
        raise ValueError("Format must be A-B｜date or A-B｜VIA｜date")

    if "-" not in route:
        raise ValueError("Route must be A-B")

    from_station, to_station = route.split("-")

    dep = resolve_station(from_station)
    arr = resolve_station(to_station)

    middle_code = ""
    if via:
        middle_code = resolve_station(via)

    # ======================================================
    # ✅ Store handles:
    # - cert validity
    # - retry/backoff
    # - rate limit
    # - cache TTL
    # ======================================================

    result = store.cached_query(
        api="transfer",
        date=date.replace("-", ""),
        dep=dep,
        arr=arr,

        fetch_func=lambda **kw: fetch_transfer(
            date=date,
            dep=dep,
            arr=arr,
            middle=middle_code,
            psw=store.psw
        ),

        cache_ttl=6*3600,
        middle=middle_code
    )

    return {
        "query": query,
        "source": result["source"],
        "payload": result["payload"],
    }
# ============================================================
# Pretty Output Helpers
# ============================================================

def seat_str(x: str) -> str:
    if not x:
        return "--"
    x = str(x).strip()
    if x == "有":
        return "有票"
    if x == "无":
        return "无票"
    if x == "--":
        return "--"
    return x


def format_segment(seg: Dict[str, Any]) -> str:
    code = seg.get("station_train_code", "?")

    a = seg.get("from_station_name", "?")
    b = seg.get("to_station_name", "?")

    dep = seg.get("start_time", "?")
    arr = seg.get("arrive_time", "?")
    dur = seg.get("lishi", "?")

    swz = seat_str(seg.get("swz_num"))
    zy = seat_str(seg.get("zy_num"))
    ze = seat_str(seg.get("ze_num"))

    return (
        f"🚆 {code}\n"
        f"   {a} {dep} → {b} {arr}   ⏱{dur}\n"
        f"   💺商务:{swz}  一等:{zy}  二等:{ze}"
    )

def pretty_transfer(result: Dict[str, Any]) -> str:
    payload = result.get("payload", {})
    data = payload.get("data", {})

    if not isinstance(data, dict):
        error_msg = str(payload.get("errorMsg") or "").strip()
        if error_msg:
            return f"⚠️ {error_msg}"
        return "⚠️ No transfer plans found."

    middle_list = data.get("middleList", [])
    if not middle_list:
        error_msg = str(payload.get("errorMsg") or "").strip()
        if error_msg:
            return f"⚠️ {error_msg}"
        return "⚠️ No transfer plans found."

    lines = []
    lines.append("==================================================")
    lines.append("🚆 12306 Transfer Directory (Ultimate)")
    lines.append("==================================================")
    lines.append(f"Query : {result['query']}")
    lines.append(f"Source: {result['source']}")
    lines.append("--------------------------------------------------")

    for i, plan in enumerate(middle_list[:8], 1):

        total = plan.get("all_lishi", "?")
        wait = plan.get("wait_time", "?")

        full = plan.get("fullList", [])
        if len(full) < 2:
            continue

        seg1, seg2 = full[0], full[1]

        train1 = seg1.get("station_train_code", "?")
        train2 = seg2.get("station_train_code", "?")

        mid_station = seg1.get("to_station_name", "?")

        # ===============================
        # ⭐ Same Train → Seat Change
        # ===============================
        same_train = (train1 == train2)

        if same_train:
            title = f"{i}. 🚆 车内换座（同车分段票） via {mid_station}"
        else:
            title = f"{i}. 🔁 换乘方案 via {mid_station}"

        lines.append(f"\n{title}")
        lines.append("--------------------------------------------------")

        # Segment 1
        lines.append(format_segment(seg1))
        lines.append("")

        # Segment 2
        lines.append(format_segment(seg2))
        lines.append("")

        # ===============================
        # Only show transfer wait if different trains
        # ===============================
        if not same_train:
            lines.append(f"⏳ 换乘时间: {wait}")

        lines.append(f"🕒 总历时: {total}")

    lines.append("")
    lines.append("==================================================")
    lines.append("✅ Plans are generated by official 12306 API.")
    lines.append("⚠️ Seat availability changes quickly.")
    lines.append("==================================================")

    return "\n".join(lines)


# ============================================================
# Local Test
# ============================================================

if __name__ == "__main__":

    print("==============================================")
    print("🚆 12306 Transfer Tool Local Test (Ultimate)")
    print("==============================================")

    # Test A: no via
    q1 = "福州-上海虹桥｜2026-02-27"
    print("\n--- Test A (no via) ---")
    res1 = tool_transfer_query(q1)
    print(pretty_transfer(res1))

    """# Test B: with via
    q2 = "福州-北京南｜徐州东｜2026-02-19"
    print("\n--- Test B (with via) ---")
    res2 = tool_transfer_query(q2)
    print(pretty_transfer(res2))"""
