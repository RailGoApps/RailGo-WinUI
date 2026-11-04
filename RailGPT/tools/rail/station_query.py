"""
station_query.py

RailGo Station Query Tool (SQLite Edition)

v2.6 Final Policy:
- Router 已严格限制 station 工具调用频率
- 因此 station query 允许 remote fetch
- 属于低频补全工具
"""

from typing import Dict, Any, Optional

from agent.psw import AgentState
from tools.rail.railgo_client import RailGoError, fetch_station_v1
from tools.rail.rail_store import railstore
from tools.rail.station_dict import station_dict


# =========================================================
# Station Normalize
# =========================================================

def normalize_station_code(name: str) -> str | None:
    """
    将 station 输入标准化为 telecode

    支持：
    - "FZS"
    - "福州"
    - "福州南站"
    """

    if not name:
        return None

    name = name.strip()

    # ✅ 1. telecode
    if len(name) == 3 and name.isascii() and name.isalpha():
        return name.upper()

    # ✅ 2. 中文站名清洗
    clean = name.replace("站", "").strip()

    # ✅ 3. station_dict 映射
    tele = station_dict.telecode_of(clean)

    if tele:
        return tele.upper()

    return None


# =========================================================
# Query Station Main Entry
# =========================================================
def query_station(telecode: str, psw=None) -> Optional[Dict[str, Any]]:
    telecode = telecode.strip().upper()

    if psw:
        psw.set_state(AgentState.QUERYING, f"station {telecode}")

    cached = railstore.get_station(telecode)
    if cached:
        if psw:
            psw.set_state(AgentState.RAIL_MEMORY_HIT, f"station {telecode}")
        return cached

    try:
        if psw:
            psw.set_state(AgentState.IN_FLIGHT, f"HTTP GET station:{telecode}")

        payload = fetch_station_v1(telecode)

    except RailGoError as e:
        if psw:
            psw.error("STATION API FAILED", e)

            psw.set_state(
                AgentState.READY,
                "RESET AFTER ERROR"
            )
        return None

    railstore.save_station(telecode, payload)
    if psw:
        psw.set_state(
            AgentState.RAIL_MEMORY_WRITE,
            f"station cached {telecode}"
        )
    return payload



# =========================================================
# Pretty Format Evidence
# =========================================================

def pretty_format_station(payload: Dict[str, Any]) -> str:
    """
    将 station payload 转成 LLM 可直接引用的证据文本
    """

    if not payload:
        return "⚠️ No station data."

    data = payload.get("data", {})

    name = data.get("name", "?")
    tele = data.get("telecode", "?")

    bureau = data.get("bureau", "?")
    belong = data.get("belong", "?")

    city = data.get("city", "?")
    prov = data.get("province", "?")

    pinyin = data.get("pinyin", "?")
    triple = data.get("pinyinTriple", "?")

    lines = []
    lines.append("==================================================")
    lines.append(f"🚉 Station Profile: {name} ({tele})")
    lines.append("==================================================")
    lines.append(f"🏢 Bureau        : {bureau}")
    lines.append(f"📍 Belong        : {belong}")
    lines.append(f"🌆 City          : {city}")
    lines.append(f"🗺 Province      : {prov}")
    lines.append(f"🔤 Pinyin        : {pinyin}")
    lines.append(f"🔠 Triple Code   : {triple}")
    lines.append("==================================================")

    return "\n".join(lines)
