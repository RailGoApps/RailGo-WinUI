"""
path_query.py  (v2.6 Stable + Future Support)

RailGo Path Query Tool (SQLite Edition)

RailGo compatibility policy:
- valid local timetable certificate first
- V1 quarterly timetable second
- V2 exact-date train-main query when V1 cannot cover the requested date
- V1/cache skeleton fallback on temporary V2 failures
"""

from typing import Dict, Any, Optional
from datetime import datetime

from agent.psw import AgentState
from tools.rail.rail_store import railstore
from tools.rail.railgo_client import (
    RailGoError,
    RailGoNotFoundError,
    fetch_train_main_v2,
    fetch_train_path_v1,
    normalize_railgo_date,
)
from tools.rail.station_dict import station_dict


# =========================================================
# Date Helpers
# =========================================================

def today_str() -> str:
    return datetime.now().strftime("%Y%m%d")


# =========================================================
# Main Query Entry
# =========================================================

def query_train_path(
    train_no: str,
    query_date: Optional[str] = None,
    psw=None
) -> Optional[Dict[str, Any]]:
    """
    查询单车次经停路径（path）

    本地有效证书优先，其次使用 RailGo V1 的季度运行图。
    V1 无法覆盖目标日期时，再用 V2 做指定日期精确补全。
    """

    train_no = train_no.strip().upper()

    try:
        query_date = normalize_railgo_date(query_date)
    except ValueError as exc:
        if psw:
            psw.set_state(AgentState.ERROR, f"path invalid date: {exc}")
        return None

    if psw:
        psw.set_state(AgentState.QUERYING, f"path {train_no} ({query_date})")

    # =====================================================
    # ✅ 1) Cache Lookup
    # =====================================================

    cached = railstore.get_path(train_no)

    cached_status = None
    if cached:
        cached_status = railstore.check_validity(
            table="path_cache",
            key=train_no,
            date=query_date
        )

        cache_is_usable = bool(cached_status.get("cert_valid"))
        if cache_is_usable:
            if psw:
                psw.set_state(
                    AgentState.RAIL_MEMORY_HIT,
                    f"path cache hit ({train_no}) running={cached_status.get('running_today')}"
                )
            cached["_status"] = cached_status
            return cached

        if psw:
            psw.set_state(
                AgentState.RAIL_MEMORY_MISS,
                f"path cache refresh required ({train_no})"
            )
    elif psw:
        psw.set_state(AgentState.RAIL_MEMORY_MISS, f"path {train_no}")

    if psw:
        psw.set_state(
            AgentState.IN_FLIGHT,
            f"HTTP GET RailGo path:{train_no} date={query_date}"
        )

    v1_payload = None
    v1_status = None
    v1_error = None

    try:
        v1_payload = fetch_train_path_v1(train_no)
        railstore.save_path(train_no, v1_payload)
        v1_status = railstore.check_validity("path_cache", train_no, query_date)
        v1_payload["_status"] = v1_status

        if v1_status.get("cert_valid"):
            if psw:
                psw.set_state(
                    AgentState.QUERY_HIT,
                    f"path fetched from RailGo v1 running={v1_status.get('running_today')}"
                )
            return v1_payload
    except RailGoError as exc:
        v1_error = exc

    if psw:
        detail = f"V1 certificate miss: {v1_error}" if v1_error else "V1 certificate misses query date"
        psw.set_state(AgentState.RETRYING, f"{detail}; querying RailGo V2")

    try:
        payload = fetch_train_main_v2(train_no, query_date)
        railstore.save_path(train_no, payload)
        status = railstore.check_validity("path_cache", train_no, query_date)
        payload["_status"] = status
        if psw:
            psw.set_state(
                AgentState.QUERY_HIT,
                f"path fetched from RailGo v2 running={status.get('running_today')}"
            )
        return payload
    except RailGoNotFoundError as exc:
        if v1_payload:
            v1_payload["_status"] = {
                "exists": True,
                "cert_valid": True,
                "running_today": False,
                "need_refresh": False,
                "exact_date_source": "RailGo v2",
            }
            v1_payload["_fallback_warning"] = f"V2 exact-date check: {exc}"
            return v1_payload
        if psw:
            psw.set_state(
                AgentState.QUERY_EMPTY,
                f"RailGo V2 has no running train {train_no} on {query_date}: {exc}"
            )
        return None
    except RailGoError as v2_error:
        fallback = v1_payload or cached
        if fallback:
            fallback["_status"] = v1_status or cached_status or {}
            fallback["_fallback_warning"] = (
                f"RailGo V2 refresh failed; V1/cache skeleton used: {v2_error}"
            )
            return fallback
        if psw:
            psw.set_state(
                AgentState.ERROR,
                f"RailGo path failed on V1 and V2: {v1_error}; {v2_error}"
            )
        return None


# =========================================================
# Pretty Format Evidence
# =========================================================

def pretty_format_path(payload: Dict[str, Any],query_date:str) -> str:
    if not payload:
        return "⚠️ No path data available."

    status = payload.get("_status", {})

    train_codes = payload.get("numberFull", [])
    bureau = payload.get("bureauName") or payload.get("bureauShortName") or "?"
    car = payload.get("car", "?")
    source_meta = payload.get("_railgo") or {}

    cert_valid = status.get("cert_valid", False)
    need_refresh = status.get("need_refresh", False)

    lines = []
    lines.append("==================================================")
    lines.append(f"🚄 Train Path Profile: {' / '.join(train_codes)}")
    lines.append("==================================================")
    lines.append(f"📍 Bureau : {bureau}")
    lines.append(f"🚆 Model  : {car}")
    if source_meta:
        source_version = str(source_meta.get("api_version", "?")).upper()
        source_endpoint = source_meta.get("endpoint", "")
        lines.append(f"🔎 Source : RailGo {source_version} {source_endpoint}".rstrip())

    # =====================================================
    # Temporary Train Detection
    # =====================================================

    rundays = payload.get("rundays", [])
    run_count = len(rundays)

    if run_count > 0 and run_count < (91 - 21):
        lines.append("🚧 列车类型：阶段性加开的临客 (seasonal/temporary)")
        lines.append(f"📆 Run Days Count: {run_count} days (below 70)")
    elif run_count >= (91 - 21):
        lines.append("✅ 列车类型：长期稳定的图定列车 (regular scheduled train)")
        lines.append(f"📆 Run Days Count: {run_count} days (stable)")
    else:
        lines.append("❓ 列车类型：暂无足够开行数据判断")

    # =====================================================
    # ✅ Running Detection (INLINE Multi-Code Safe)
    # =====================================================

    running_today = None
    hit_code = None

    # normalize train_codes → list[str]
    if isinstance(train_codes, str):
        train_codes = [train_codes]

    if isinstance(train_codes, list) and train_codes:

        for code in train_codes:

            code = str(code).strip().upper().replace("次", "")
            if not code:
                continue

            try:
                info = railstore.check_validity(
                    "path_cache",
                    code,
                    query_date  # ✅pretty里直接用 query_date
                )
            except Exception:
                continue

            flag = info.get("running_today", None)

            # ⭐任意一个 True → Running YES
            if flag is True:
                running_today = True
                hit_code = code
                break

            # 如果明确 False，先记下来，但不能 break
            if flag is False and running_today is None:
                running_today = False

    # =====================================================
    # Running Display (Final Correct)
    # =====================================================

    if cert_valid and running_today is True:
        if hit_code:
            lines.append(f"✅ Running On QUERYDATE: YES (matched {hit_code})")
        else:
            lines.append("✅ Running On QUERYDATE: YES")

    elif cert_valid and running_today is False:
        lines.append("⚠️ Running: NO (train exists but not running that day)")

    elif need_refresh:
        lines.append("❓ Running: Unknown (certificate expired, refresh needed)")

    else:
        lines.append("❓ Running: Unknown (no valid running evidence)")

    lines.append("--------------------------------------------------")

    timetable = payload.get("timetable", [])
    if not timetable:
        lines.append("⚠️ Timetable missing.")
        return "\n".join(lines)

    lines.append("站序 | 到达 | 发车 | 停时 | 里程(km) | 车站")
    lines.append("--------------------------------------------------")

    for i, stop in enumerate(timetable, start=1):
        arr = stop.get("arrive", "--")
        dep = stop.get("depart", "--")
        stp = stop.get("stopTime", 0)
        dist = stop.get("distance", "--")

        tele = stop.get("stationTelecode", "")
        station = stop.get("station", "")

        zh = station_dict.name_of(tele)
        if zh:
            station = zh

        lines.append(
            f"{i:>3} | {arr:>5} | {dep:>5} | "
            f"{stp:>3}分 | {dist:>7} | {station} ({tele})"
        )

    lines.append("==================================================")

    return "\n".join(lines)

"""payload = query_train_path("G858", "20260219")

cached = railstore.get_path("G858")
print(cached.keys())
print(cached.get("numberFull"))
print(railstore.check_validity("path_cache", "G858", "20260214"))"""

def query_path_stopcheck(
    query_id: str,
    query_date: Optional[str] = None,
    psw=None
) -> Dict[str, Any]:
    """
    path_stopcheck (Batch Micro Evidence Tool)

    Supports:
    - Up to 50 trains
    - Up to 10 stations

    query_id format:
        "G25,G31,G35|南京南,济南西"

    Output:
    - Aggregated skip/stop evidence table
    """

    if query_date is None:
        query_date = today_str()

    # ============================
    # 1) Parse query_id
    # ============================

    if "|" not in query_id:
        return {
            "domain": "railway",
            "object": "path_stopcheck",
            "id": query_id,
            "pretty": "⚠️ Invalid format. Use: G25,G31|南京南",
            "payload": None
        }

    train_part, station_part = query_id.split("|", 1)

    trains = [x.strip().upper() for x in train_part.split(",") if x.strip()]
    stations = [x.strip() for x in station_part.split(",") if x.strip()]

    # 去重
    trains = list(dict.fromkeys(trains))[:50]
    stations = list(dict.fromkeys(stations))[:10]

    if not trains or not stations:
        return {
            "domain": "railway",
            "object": "path_stopcheck",
            "id": query_id,
            "pretty": "⚠️ Empty train list or station list.",
            "payload": None
        }

    if psw:
        psw.set_state(
            AgentState.QUERYING,
            f"stopcheck {len(trains)} trains × {len(stations)} stations"
        )

    # ============================
    # 2) Scan each train
    # ============================

    scan = []

    for train_no in trains:

        path_payload = query_train_path(train_no, query_date=query_date, psw=psw)

        if not path_payload:
            scan.append({"train": train_no, "status": "NO_PATH"})
            continue

        # ✅ timetable extraction (FULL compatible)
        timetable = []

        if "timetable" in path_payload:
            timetable = path_payload.get("timetable", [])

        elif "payload" in path_payload:
            timetable = path_payload["payload"].get("timetable", [])

        if not timetable:
            scan.append({"train": train_no, "status": "NO_TIMETABLE"})
            continue

        # Build station lookup map
        station_map = {}

        for stop in timetable:

            st = stop.get("station", "").strip()
            tele = stop.get("stationTelecode", "").strip()

            if st:
                station_map[st] = stop

            if tele:
                station_map[tele] = stop

            zh = station_dict.name_of(tele)
            if zh:
                station_map[zh] = stop

        # Per-train evidence
        train_result = {
            "train": train_no,
            "checks": []
        }

        for target in stations:

            stop = station_map.get(target)

            if not stop:
                train_result["checks"].append({
                    "station": target,
                    "result": "SKIP"
                })
                continue

            stop_time = stop.get("stopTime", 0)
            arr = stop.get("arrive", "--")
            dep = stop.get("depart", "--")

            if stop_time == 0:
                train_result["checks"].append({
                    "station": target,
                    "result": "PASS",
                    "arrive": arr,
                    "depart": dep
                })
            else:
                train_result["checks"].append({
                    "station": target,
                    "result": "STOP",
                    "stopTime": stop_time,
                    "arrive": arr,
                    "depart": dep
                })

        scan.append(train_result)

    # ============================
    # 3) Pretty Output
    # ============================

    pretty = pretty_format_path_stopcheck(scan, query_date)

    return {
        "domain": "railway",
        "object": "path_stopcheck",
        "id": query_id,
        "payload": {"scan": scan},
        "pretty": pretty,
        "meta": {
            "train_count": len(trains),
            "station_count": len(stations),
            "date": query_date
        }
    }

def pretty_format_path_stopcheck(scan: list, query_date: str) -> str:
    """
    PATH_STOPCHECK Evidence Formatter (v2.6.3 upgraded)

    Multi-station full evidence:
    - STOP: train stops and opens doors
    - PASS: train passes but stopTime=0
    - SKIP: station not in timetable at all

    This is designed for paradox questions:
    - 南京南悖论
    - 哪些车跨越某站
    """

    lines = []
    lines.append("=================================================")
    lines.append("🛑 PATH_STOPCHECK Evidence Table (Full Multi-Station)")
    lines.append("=================================================")
    lines.append(f"📅 Date: {query_date}")
    lines.append("")

    paradox_candidates = []

    for item in scan:

        train = item.get("train", "?")

        # -----------------------------
        # Missing cases
        # -----------------------------
        if item.get("status") == "NO_PATH":
            lines.append(f"【{train}】⚠️ NO PATH DATA")
            lines.append("")
            continue

        if item.get("status") == "NO_TIMETABLE":
            lines.append(f"【{train}】⚠️ NO TIMETABLE DATA")
            lines.append("")
            continue

        checks = item.get("checks", [])

        if not checks:
            lines.append(f"【{train}】⚠️ EMPTY CHECK RESULT")
            lines.append("")
            continue

        # -----------------------------
        # Per-train full evidence
        # -----------------------------
        lines.append(f"【{train}】Stop Evidence:")

        has_skip = False

        for c in checks:

            st = c.get("station", "?")
            result = c.get("result")

            # ---- SKIP ----
            if result == "SKIP":
                has_skip = True
                lines.append(f"   ❌ {st}: SKIP (not in timetable)")

            # ---- PASS ----
            elif result == "PASS":
                arr = c.get("arrive", "--")
                dep = c.get("depart", "--")
                lines.append(
                    f"   ⚠️ {st}: PASS (arr={arr}, dep={dep}, no stop)"
                )

            # ---- STOP ----
            elif result == "STOP":
                arr = c.get("arrive", "--")
                dep = c.get("depart", "--")
                stp = c.get("stopTime", 0)

                lines.append(
                    f"   ✅ {st}: STOP {stp}m (arr={arr}, dep={dep})"
                )

        # -----------------------------
        # Candidate detection
        # -----------------------------
        if has_skip:
            paradox_candidates.append(train)

        lines.append("")

    # =================================================
    # Summary Section
    # =================================================
    lines.append("=================================================")

    if paradox_candidates:
        lines.append("🔥 Key Finding (Paradox Candidates)")
        lines.append(
            f"Found {len(paradox_candidates)} trains SKIPPING at least one target station:"
        )
        lines.append("  " + ", ".join(paradox_candidates))
        lines.append("")
        lines.append("👉 These trains are your 南京南悖论 breakers.")
    else:
        lines.append("✅ Key Finding")
        lines.append("All candidate trains STOP or PASS every target station.")
        lines.append("No paradox-breaking skip patterns found.")

    lines.append("=================================================")

    return "\n".join(lines)

"""if __name__ == "__main__":

    print("\n==================================================")
    print("🚄 RailAI v2.6.3 — PATH_STOPCHECK Local Test")
    print("==================================================\n")

    query_id = "G25,G31,G35,G1,G3,G5,G7,G9,G11,G13,G15,G17,G19,G21,G23,G27|济南西,南京南"

    result = query_path_stopcheck(query_id)

    print(result["pretty"])

"""
