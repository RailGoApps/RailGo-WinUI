"""
s2s_query.py  (v2.6 Final)

Station-to-Station Query Tool (RailGo)

特点：
- 完全兼容新版 RailStore（三表资产）
- 优先使用覆盖目标日期的本地季度证书
- V1 先查用户日期，必要时回退最近运行图，最多 3 次
- UUH 仅在 HTTP 时加 /
"""

from agent.psw import AgentState
import time
from datetime import datetime, timedelta
from typing import Optional, List,Any

from tools.rail.path_query import query_train_path
from tools.rail.railgo_client import (
    RailGoError,
    RailGoNotFoundError,
    fetch_s2s_v1,
    normalize_railgo_date,
    railgo_source,
)
from tools.rail.rail_store import railstore
from tools.rail.station_dict import station_dict


# =========================================================
# Station Normalize
# =========================================================

def normalize_station(name: str) -> Optional[str]:
    """
    支持：
    - NKH → NKH
    - 南京南 → NKH
    - 南京南站 → NKH
    """

    if not name:
        return None

    name = name.strip()

    # telecode
    if len(name) == 3 and name.isascii() and name.isalpha():
        return name.upper()

    # 中文去站
    clean = name.replace("站", "").strip()
    tele = station_dict.telecode_of(clean)

    return tele.upper() if tele else None


# =========================================================
# 日期工具
# =========================================================

def today_str() -> str:
    return datetime.now().strftime("%Y%m%d")

def normalize_bureau(bureau_raw: str) -> str:
    """
    RailGo bureauName is sometimes abbreviated:
    - 南局 → 南昌局
    - 宁局 → 南宁局
    - 上局 → 上海局
    - 广铁 → 广铁
    """

    if not bureau_raw:
        return "未知"

    bureau_raw = bureau_raw.strip()

    mapping = {
        # ===== 华东 =====
        "上局": "上海局",
        "沪局": "上海局",

        "南局": "南昌局",  # ⚠️ 注意：payload 里的“南局”不是南宁
        "杭局": "杭州局",  # 少见但可能出现
        "合局": "合肥局",
        "福局": "福州局",  # 若你未来扩展到地方局

        # ===== 华北 =====
        "京局": "北京局",
        "津局": "北京局",  # 天津客运段常混写
        "太局": "太原局",

        # ===== 华中 =====
        "郑局": "郑州局",
        "武局": "武汉局",
        "广铁": "广铁U彩",  # 你要求的品牌化输出
        "广局": "广铁U彩",

        # ===== 华南 =====
        "宁局": "南宁局",  # ⚠️ payload 的宁局=南宁局
        "柳局": "南宁局",  # 历史遗留叫法

        # ===== 西南 =====
        "成局": "成都局",
        "昆局": "昆明局",
        "贵局": "贵阳局",
        "重局": "重庆局",  # 少见（通常归成局）

        # ===== 西北 =====
        "西局": "西安局",
        "兰局": "兰州局",
        "乌局": "乌鲁木齐局",

        # ===== 东北 =====
        "哈局": "哈尔滨局",
        "沈局": "沈阳局",
        "长局": "长春局",

        # ===== 华东补充 =====
        "济局": "济南局",
        "青局": "济南局",  # 青岛段有时会混写

        # ===== 特殊/可能出现 =====
        "呼局": "呼和浩特局",
        "南铁": "南昌局",
        "广铁集团": "广铁U彩",
    }

    return mapping.get(bureau_raw, bureau_raw)
# =========================================================
# UUH HTTP 特殊兼容
# =========================================================

def tele_for_http(tele: str) -> str:
    """
    RailGo 特殊要求：
    UUH 查询必须写 UUH/
    但存储永远保持 UUH
    """
    if tele == "UUH":
        return "UUH/"
    return tele


# =========================================================
# Path 注入（证书沿用 query_date）
# =========================================================

def build_path_payload_from_s2s(item: dict) -> dict:
    return {
        "bureauName": item.get("bureauName"),
        "runner": item.get("runner"),
        "car": item.get("car"),
        "carOwner": item.get("carOwner"),
        "rundays": item.get("rundays", []),
        "timetable": item.get("timetable", []),
        "fromStationTelecode": item.get("fromStationTelecode"),
        "toStationTelecode": item.get("toStationTelecode"),
        "passTime": item.get("passTime"),
        "numberFull": item.get("numberFull"),
        "numberKind": item.get("numberKind"),
    }


def inject_s2s_into_path(raw_list: list, cert: str, psw=None):
    """
    ⭐注入 path_cache
    cert 必须沿用 query_date
    """

    if not raw_list:
        return

    injected = 0

    for item in raw_list:
        train_code = item.get("number")
        if not train_code:
            continue

        payload = build_path_payload_from_s2s(item)
        railstore.save_path(train_code, payload)

        injected += 1

    if psw:
        psw.set_state(
            AgentState.RAIL_MEMORY_WRITE,
            f"injected {injected} trains into path cache"
        )


# =========================================================
# 主查询入口
# =========================================================

def query_s2s_route(dep: str, arr: str, date: str, psw=None):
    """
    优先复用 RailStore 的有效季度证书；缓存失效时先向 V1 查询
    用户指定日期，再有限回退到最近运行图。
    """

    # ----------------------------
    # Normalize
    # ----------------------------
    dep = normalize_station(dep)
    arr = normalize_station(arr)
    try:
        date = normalize_railgo_date(date)
    except ValueError as exc:
        if psw:
            psw.set_state(AgentState.ERROR, f"s2s invalid date: {exc}")
        return None

    if not dep or not arr:
        if psw:
            psw.set_state(
                AgentState.ERROR,
                f"s2s invalid station: {dep}-{arr}"
            )
        return None

    route_key = f"{dep}-{arr}"

    # ----------------------------
    # Cache Hit?
    # ----------------------------
    cached = railstore.get_s2s(route_key)
    cached_status = None
    if cached:
        status = railstore.check_validity("s2s_cache", route_key, date)
        cached_status = status

        if status.get("cert_valid"):
            if psw:
                psw.set_state(
                    AgentState.RAIL_MEMORY_HIT,
                    f"s2s {route_key} (cert_valid=True)"
                )
            return {
                "payload": cached,
                "status": status,
                "source": cached.get("_railgo") or {"provider": "RailGo", "api_version": "v1-legacy"},
            }

        if psw:
            psw.set_state(
                AgentState.RAIL_MEMORY_MISS,
                f"s2s {route_key} cache certificate expired"
            )
    elif psw:
        psw.set_state(
            AgentState.RAIL_MEMORY_MISS,
            f"s2s {route_key}"
        )

    # ----------------------------
    # API Fetch Strategy
    # ----------------------------
    candidates = [
        date,
        datetime.now().strftime("%Y%m%d"),
        (datetime.now() - timedelta(days=1)).strftime("%Y%m%d"),
        (datetime.now() - timedelta(days=5)).strftime("%Y%m%d"),
    ]
    query_dates = list(dict.fromkeys(candidates))[:3]
    last_error = None

    for query_date in query_dates:

        if psw:
            psw.set_state(
                AgentState.S2S_INFLIGHT,
                f"HTTP GET s2s:{dep}->{arr} ({query_date})"
            )

        try:
            payload = fetch_s2s_v1(
                tele_for_http(dep),
                tele_for_http(arr),
                query_date,
            )

            if not payload:
                continue

            # ----------------------------
            # Save into RailStore
            # ----------------------------
            source = railgo_source(
                "v1",
                "/api/train/sts_query",
                query_date=query_date,
                requested_date=date,
                route=route_key,
            )
            stored_payload = {"trains": payload, "_railgo": source}
            railstore.save_s2s(route_key, stored_payload)

            # Inject path cache
            inject_s2s_into_path(payload, cert=query_date)

            # Check validity for user date
            status = railstore.check_validity("s2s_cache", route_key, date)

            if psw:
                psw.set_state(
                    AgentState.QUERY_HIT,
                    f"s2s fetched √ {len(payload)} trains"
                )

            return {
                "payload": stored_payload,
                "status": status,
                "source": source,
            }

        except (RailGoNotFoundError, RailGoError) as exc:
            last_error = exc

            if psw:
                psw.set_state(
                    AgentState.ERROR,
                    f"s2s failed ({query_date}) {exc}"
                )

    if cached:
        cached["_fallback_warning"] = f"RailGo refresh failed: {last_error or 'no trains'}"
        return {
            "payload": cached,
            "status": cached_status or {},
            "source": cached.get("_railgo") or {"provider": "RailGo", "api_version": "v1-legacy"},
        }

    if psw:
        psw.set_state(AgentState.READY, "RESET AFTER S2S ERROR")
    return None

def pretty_format_s2s(payload: Any, query_date: str) -> str:
    """
    RailGo s2s Pretty Formatter (Final Compatible)

    RailGo 实际返回结构：

    {
      "payload": {
         "trains": [...]
      },
      "status": {...}
    }
    """

    # ============================
    # 3.5) Duration Inference Patch (核心修复)
    # ============================

    def duration_minutes_from_timetable(t):
        """
        Infer real duration using timetable:
        Duration = toArrive - fromDepart + dayDiff
        Much more reliable than passTime.
        """

        dep = t.get("fromDepart")
        arr = t.get("toArrive")
        day_diff = t.get("dayDiff", 0)

        if not dep or not arr:
            return None

        try:
            dep_h, dep_m = map(int, dep.split(":"))
            arr_h, arr_m = map(int, arr.split(":"))

            dep_min = dep_h * 60 + dep_m
            arr_min = arr_h * 60 + arr_m

            # Cross-day compensation
            arr_min += int(day_diff) * 1440

            return arr_min - dep_min

        except:
            return None

    def duration_str(minutes: int) -> str:
        """Convert minutes → '4h33m' style"""
        if minutes is None:
            return "?"
        h = minutes // 60
        m = minutes % 60
        return f"{h}h{m:02d}m"

    def stop_count(t):
        """Number of timetable stops"""
        return len(t.get("timetable", []))

    # ============================
    # Benchmark Score (0–100)
    # ============================

    def benchmark_score(duration, best, worst):
        """
        Normalize duration into score:
        Fastest train = 100
        Slowest train = 0
        """
        if duration is None:
            return None
        if worst == best:
            return 100
        return int(100 * (worst - duration) / (worst - best))

    def benchmark_tier(score):
        """Tier classification"""
        if score is None:
            return "❓ Unknown"
        if score >= 80:
            return "🏆 顶级标杆车 (80–100)"
        elif score >= 50:
            return "⚡ 优质快车 (50–79)"
        elif score >= 30:
            return "🚄 普通较快 (30–49)"
        else:
            return "🐢 慢车/多停 (<30)"



    # ============================
    # 1) 提取 trains 列表（核心修复）
    # ============================

    trains = None

    # 情况1：payload 是 list（旧版）
    if isinstance(payload, list):
        trains = payload

    # 情况2：payload 是 dict（新版）
    elif isinstance(payload, dict):

        # ✅ RailGo v2.6 主结构
        if "payload" in payload and isinstance(payload["payload"], dict):
            trains = payload["payload"].get("trains", [])

        # 兼容其它可能结构
        elif "trains" in payload:
            trains = payload["trains"]

        elif "data" in payload:
            trains = payload["data"]

        else:
            return (
                "⚠️ Unknown s2s payload structure.\n"
                f"Keys = {list(payload.keys())}"
            )

    else:
        return f"⚠️ Invalid payload type: {type(payload)}"

    # ============================
    # 2) 空结果判断
    # ============================

    if not trains:
        return "⚠️ No trains found for this route."

    # ============================
    # 3.6) Sort Trains by Real Duration
    # ============================

    # Compute durations
    for t in trains:
        t["_duration_min"] = duration_minutes_from_timetable(t)

    # Filter valid durations
    valid_durations = [t["_duration_min"] for t in trains if t["_duration_min"] is not None]

    best = min(valid_durations)
    worst = max(valid_durations)

    # Compute benchmark scores
    for t in trains:
        t["_benchmark_score"] = benchmark_score(t["_duration_min"], best, worst)

    # ✅ Sort by duration ascending
    trains = sorted(trains, key=lambda x: x["_duration_min"] or 99999)

    # ============================
    # 3) Pretty 输出
    # ============================

    lines = []
    # ============================
    # ABSTRACT SUMMARY HEADER
    # ============================

    lines.append("")
    lines.append("🏁 Benchmark Tier Summary (标杆分类)")
    lines.append("--------------------------------------------------")

    tiers = {
        "🏆 顶级标杆车 (80–100)": [],
        "⚡ 优质快车 (50–79)": [],
        "🚄 普通较快 (30–49)": [],
        "🐢 慢车/多停 (<30)": [],
    }

    for t in trains:
        score = t["_benchmark_score"]
        tier_name = benchmark_tier(score)
        train_no = t.get("number", "?")

        # 图定/临客标签
        rundays = t.get("rundays", [])
        run_count = len(rundays)

        if run_count > 0 and run_count < 76:
            tag = "Temporary 临客"
        else:
            tag = "Regular 图定"

        tiers[tier_name].append(
            f"- {train_no} ({tag}) ⏱{duration_str(t['_duration_min'])} Score={score}"
        )

    # Print tier groups
    for tier_name, items in tiers.items():
        if items:
            lines.append("")
            lines.append(tier_name)
            for x in items[:35]:  # 每类最多展示5条
                lines.append(" " + x)

    # ============================
    # Departure Time Group Summary
    # ============================

    lines.append("")
    lines.append("🕒 Departure Time Groups (发车时间分类)")
    lines.append("--------------------------------------------------")

    time_groups = {
        "🌅 上午车次 (06–11)": [],
        "🌞 午间车次 (11–14)": [],
        "🌇 下午车次 (14–18)": [],
        "🌙 晚间车次 (18–24)": [],
    }

    for t in trains:
        dep = t.get("fromDepart", "00:00")
        train_no = t.get("number", "?")

        rundays = t.get("rundays", [])
        run_count = len(rundays)

        if run_count > 0 and run_count < 76:
            tag = "临客"
        else:
            tag = "图定"

        try:
            h = int(dep.split(":")[0])
        except:
            h = 0

        entry = f"- {train_no} ({tag}) {dep}→{t.get('toArrive')} ({duration_str(t['_duration_min'])})"

        if 6 <= h < 11:
            time_groups["🌅 上午车次 (06–11)"].append(entry)
        elif 11 <= h < 14:
            time_groups["🌞 午间车次 (11–14)"].append(entry)
        elif 14 <= h < 18:
            time_groups["🌇 下午车次 (14–18)"].append(entry)
        else:
            time_groups["🌙 晚间车次 (18–24)"].append(entry)

    # Print time groups
    for g, items in time_groups.items():
        if items:
            lines.append("")
            lines.append(g)
            for x in items:
                lines.append(" " + x)

    lines.append("")
    lines.append("==================================================")
    lines.append("📌 Full Train Evidence Section (Detailed Records)")
    lines.append("==================================================")
    lines.append(
        "Below is the COMPLETE station-to-station train list.\n"
        "⚠️ This section is very long (many candidates).\n"
        "Use the Benchmark Summary above to pick 1–3 key trains,\n"
        "then request [path] for stop-by-stop verification.\n"
        "Do NOT conclude skip-stop patterns only from scanning this long list."
    )
    lines.append("--------------------------------------------------")

    for idx, t in enumerate(trains, start=1):

        train_no = t.get("number", "?")
        kind = t.get("numberKind", "?")

        bureau = t.get("bureauName", "?")
        car = t.get("car", "?")
        owner = t.get("carOwner", "?")
        runner = t.get("runner", "?")

        dep_time = t.get("fromDepart", "?")
        arr_time = t.get("toArrive", "?")
        pass_time = t.get("passTime", "?")

        rundays = t.get("rundays", [])
        running_today = query_date in rundays
        # =====================================================
        # Temporary Train Detection (Hard Threshold)
        # =====================================================

        run_count = len(rundays)

        if run_count > 0 and run_count < (91 - 31):  # 76
            train_type = "🚧 临时旅客列车 (temporary/seasonal)"
        elif run_count >= (91 - 31):
            train_type = "✅ 图定列车 (regular scheduled)"
        else:
            train_type = "❓ 未知 (no rundays data)"

        lines.append("")
        lines.append(f"【{idx}】🚆 {train_no} ({kind})")
        lines.append(f"   🚦 Train Type : {train_type}")
        lines.append(f"   📆 Run Days   : {run_count} days")
        lines.append(f"   📍 Bureau   : {bureau}")
        lines.append(f"   🚄 Model    : {car}")
        lines.append(f"   🏢 Owner    : {owner}")
        lines.append(f"   👷 Runner   : {runner}")

        lines.append(f"   🕒 Time     : {dep_time} → {arr_time}")
        lines.append(f"   ⏱ Duration : {pass_time}")

        if running_today:
            lines.append("   ✅ Running  : YES (operates today)")
        else:
            lines.append("   ⚠️ Running  : NO (not scheduled today)")

        # 不限制经停站，全部输出
        timetable = t.get("timetable", [])
        if timetable:
            lines.append("   🗺 Full Stops:")

            for stop in timetable:
                st = stop.get("station", "?")
                tele = stop.get("stationTelecode", "?")
                arr = stop.get("arrive", "--")
                dep = stop.get("depart", "--")

                lines.append(f"      - {st} ({tele}) {arr} → {dep}")

    lines.append("")
    lines.append("==================================================")

    return "\n".join(lines)

# =========================================================
# MINI Pretty Formatter (Top50 Benchmark Pool)
# =========================================================

def pretty_format_s2s_mini(payload: Any, query_date: str) -> str:
    """
    Station-to-Station MINI Candidate Pool

    Output:
    - Top50 benchmark candidates
    - 综合标杆指数（耗时90% + 停站数10%）
    - 路局分类
    - 图定/临客分类
    - 适用于“南京南悖论/标杆车大全”类问题

    ⚠️ MINI 不输出全量经停表
    → 后续必须用 path_stopcheck/path_detail 验证
    """
    today = today_str()

    future_mode = query_date > today
    today_mode = query_date == today

    # ============================
    # 1) Extract trains
    # ============================

    trains = None

    if isinstance(payload, list):
        trains = payload

    elif isinstance(payload, dict):

        if "payload" in payload and isinstance(payload["payload"], dict):
            trains = payload["payload"].get("trains", [])

        elif "trains" in payload:
            trains = payload["trains"]

        else:
            return "⚠️ Unknown s2s payload structure."

    if not trains:
        return "⚠️ No trains found."

    # ============================
    # 2) Duration inference
    # ============================
    def od_stop_count(t):
        """
        Stops count ONLY between fromPos and toPos
        (OD segment, not full route)
        """

        timetable = t.get("timetable", [])
        if not timetable:
            return 0

        fp = t.get("fromPos", 0)
        tp = t.get("toPos", len(timetable) - 1)

        try:
            fp = int(fp)
            tp = int(tp)
        except:
            return len(timetable)

        # safety clamp
        fp = max(0, fp)
        tp = min(len(timetable) - 1, tp)

        # slice OD segment
        segment = timetable[fp:tp + 1]

        # stops = intermediate stations only (exclude endpoints)
        if len(segment) <= 2:
            return 0

        return len(segment) - 2

    def infer_duration_min(t):
        dep = t.get("fromDepart")
        arr = t.get("toArrive")
        day_diff = t.get("dayDiff", 0)

        if not dep or not arr:
            return None

        try:
            dh, dm = map(int, dep.split(":"))
            ah, am = map(int, arr.split(":"))

            dep_min = dh * 60 + dm
            arr_min = ah * 60 + am + int(day_diff) * 1440

            return arr_min - dep_min
        except:
            return None

    def duration_str(m):
        if m is None:
            return "?"
        return f"{m//60}h{m%60:02d}m"

    def stop_count(t):
        # timetable length = stops count
        return len(t.get("timetable", []))

    # ============================
    # 3) Compute raw metrics
    # ============================

    # ============================
    # 3) Compute raw metrics
    # ============================

    for t in trains:
        t["_dur"] = infer_duration_min(t)
        t["_stops"] = od_stop_count(t)

    durations = [t["_dur"] for t in trains if t["_dur"] is not None]

    if not durations:
        return "⚠️ No valid duration data."

    # ============================
    # Duration Range
    # ============================

    best_dur = min(durations)
    worst_dur = max(durations)

    # Stops Range
    stop_vals = [t["_stops"] for t in trains]
    best_stop = min(stop_vals)
    worst_stop = max(stop_vals)

    # ============================
    # 京沪压缩线路检测
    # ============================
    import statistics

    # =========================================================
    # 0) Preconditions
    # =========================================================
    # trains must already have:
    #   t["_dur"]   = OD duration minutes
    #   t["_stops"] = OD intermediate stop count

    durations = [t["_dur"] for t in trains if t.get("_dur") is not None]

    if not durations:
        return "⚠️ No valid duration data."

    best_dur = min(durations)
    worst_dur = max(durations)

    median_dur = int(statistics.median(durations))

    # ⭐压缩程度指标：中位数 - 最快
    gap = median_dur - best_dur

    # =========================================================
    # 1) Rank Map (Percentile Mode Only)
    # =========================================================

    sorted_by_dur = sorted(trains, key=lambda x: x.get("_dur") or 999999)
    N = len(sorted_by_dur)

    rank_map = {}
    for i, t in enumerate(sorted_by_dur, start=1):
        train_no = t.get("number")
        if train_no:
            rank_map[train_no] = i  # 1 = fastest

    # =========================================================
    # 2) Normalize Helper (Absolute Mode)
    # =========================================================

    def norm(x, best, worst):
        """
        Normalize:
        best → 1.0
        worst → 0.0
        """
        if x is None:
            return 0.0
        if worst == best:
            return 1.0
        return (worst - x) / (worst - best)

    # Stops range
    stop_vals = [t["_stops"] for t in trains if t.get("_stops") is not None]

    best_stop = min(stop_vals) if stop_vals else 0
    worst_stop = max(stop_vals) if stop_vals else 1

    # =========================================================
    # 3) Benchmark Score Engine (NEW Stable Logic)
    # =========================================================

    def benchmark_score(t):

        train_no = t.get("number")

        # ============================================
        # 🚦 京沪压缩线路 → Percentile Ranking Score
        # ============================================

        if gap <= 20:

            if not train_no or train_no not in rank_map:
                return 0

            rank = rank_map[train_no]  # 1 = fastest

            if N <= 1:
                return 100

            # Fastest=100, Slowest=0
            return int(100 * (N - rank) / (N - 1))

        # ============================================
        # ✅ 普通线路 → Absolute Benchmark Score
        # ============================================

        dur_score = norm(t.get("_dur"), best_dur, worst_dur)
        stop_score = norm(t.get("_stops"), best_stop, worst_stop)

        final = 0.90 * dur_score + 0.10 * stop_score
        return int(final * 100)

    # =========================================================
    # 4) Apply Score + Running Status
    # =========================================================

    filtered = []

    for t in trains:

        # --- Score ---
        t["_score"] = benchmark_score(t)

        # --- Running Today ---
        rundays = t.get("rundays", [])

        if not rundays:
            t["_running"] = "❓Unknown"
        elif query_date in rundays:
            t["_running"] = "✅Running"
        else:
            t["_running"] = "⚠️NotRun"

        if today_mode:
            if t["_running"] != "✅Running":
                continue
        filtered.append(t)
    trains = filtered

    # Sort by score descending
    trains = sorted(trains, key=lambda x: x.get("_score", 0), reverse=True)

    # =========================================================
    # 5) 🥇⭐ 巅峰标杆判定（Super Benchmark）
    # =========================================================

    # Top10 综合评分车次（过滤 None）
    top10_trains = set(
        [t["number"] for t in trains[:10] if t.get("number")]
    )

    def is_super_g(train_no: str) -> bool:
        """
        Rule A:
        G车号段 < 400 → 京沪传统超级标杆王者
        """
        if not train_no:
            return False

        if not train_no.startswith("G"):
            return False

        try:
            num = int(train_no[1:])
            return num < 400
        except:
            return False

    # =========================================================
    # 6) Tier Classification (Improved Thresholds)
    # =========================================================
    # ⭐巅峰时间阈值（仅压缩线路启用）
    PEAK_DELTA_MIN = 6

    def tier(score: int, train_no: str, dur: int):

        # ============================
        # 🥇⭐ 巅峰标杆（超级标杆）
        # ============================

        # Rule A: 京沪王者号段 G<400 永久巅峰
        if is_super_g(train_no):
            return "🥇⭐ 巅峰标杆"

        # ============================
        # 🚦 京沪压缩线路特殊规则
        # ============================

        if gap <= 20:

            # ⭐只有最快车 + 6min 内才配巅峰
            if (
                    train_no in top10_trains
                    and score >= 90
                    and dur is not None
                    and dur <= best_dur + PEAK_DELTA_MIN
            ):
                return "🥇⭐ 巅峰标杆"

            # 否则全部进入 percentile tier
            if score >= 90:
                return "🏆 顶级标杆"
            elif score >= 60:
                return "⚡ 优质快车"
            elif score >= 30:
                return "🚄 普通较快"
            else:
                return "🐢 慢车/多停"

        # ============================
        # 普通线路规则
        # ============================

        # Rule B: Top10 + score≥90
        if train_no in top10_trains and score >= 90:
            return "🥇⭐ 巅峰标杆"

        # Rule C: G400–G899 干线主力标杆
        if train_no and train_no.startswith("G"):
            try:
                num = int(train_no[1:])
                if num < 900:
                    return "🏆 顶级标杆"
            except:
                pass

        # Normal tier by score
        if score >= 70:
            return "🏆 顶级标杆"
        elif score >= 46:
            return "⚡ 优质快车"
        elif score >= 20:
            return "🚄 普通较快"
        else:
            return "🐢 慢车/多停"
    # ============================
    # 6) MINI Output (Top50)
    # ============================

    lines = []
    dep_name = station_dict.name_of(trains[0].get("fromStationTelecode", ""))
    arr_name = station_dict.name_of(trains[0].get("toStationTelecode", ""))

    dep_tele = trains[0].get("fromStationTelecode", "?")
    arr_tele = trains[0].get("toStationTelecode", "?")
    lines.append("==================================================")
    lines.append("🚄 Station-to-Station MINI Benchmark Candidate Pool")
    lines.append("==================================================")
    lines.append(f"📅 Query Date : {query_date}")

    if today_mode:
        lines.append("🕒 Mode       : TODAY (Only Running trains shown)")
    elif future_mode:
        lines.append("🛰 Mode       : FUTURE (NotRun trains kept for validity)")
    else:
        lines.append("📆 Mode       : PAST / Diagram fallback")
    lines.append(f"📍 Route      : {dep_name} ({dep_tele}) → {arr_name} ({arr_tele})")
    lines.append(f"🚆 Total Trains Found : {len(trains)}")
    lines.append("")
    lines.append("⚡ MINI Mode Output = Top50 综合标杆候选池")
    lines.append("Score = 90% Duration + 10% StopCount")
    lines.append("Stops = Intermediate stops between dep→arr (OD only)")
    lines.append("⚠️ For paradox/skip-stop questions, MUST follow with [path_stopcheck].")
    lines.append("--------------------------------------------------")

    topN = trains[:50]

    # ============================
    # 7) Print Top50 Table
    # ============================

    for idx, t in enumerate(topN, start=1):

        train_no = t.get("number", "?")
        bureau = t.get("bureauName", "?")

        # Regular vs Temporary
        rundays = t.get("rundays", [])
        run_count = len(rundays)

        if run_count > 0 and run_count < 76:
            tag = "临客"
        else:
            tag = "图定"

        running=t.get("_running", "?")

        lines.append(
            f"{idx:>2}. {train_no:<6} "
            f"{tier(t['_score'],train_no,t['_dur'])} "
            f"Score={t['_score']:>3}  "
            f"⏱{duration_str(t['_dur'])}  "
            f"Stops={t['_stops']:>2}  "
            f"Bureau={bureau} ({tag})"
            f"{running}"
        )

    # ============================
    # Bureau Distribution + 대표标杆 Top5
    # ============================
    # ============================
    # Bureau Name Normalizer (核心修复)
    # ============================



    lines.append("")
    lines.append("--------------------------------------------------")
    lines.append("🏢 Bureau Distribution (Top50)")
    lines.append("--------------------------------------------------")

    from collections import defaultdict

    # 1) Count bureau trains
    bureau_map = defaultdict(list)

    for t in trains[:50]:
        bureau_raw = t.get("bureauName", "?")
        bureau = normalize_bureau(bureau_raw)

        bureau_map[bureau].append(t)
    # 2) Output each bureau summary
    for bureau, items in bureau_map.items():

        # Count
        lines.append(f"- {bureau}: {len(items)} trains")

        # Sort by score descending
        top_items = sorted(items, key=lambda x: x["_score"], reverse=True)[:6]

        # Representative trains
        reps = []
        for x in top_items:
            reps.append(f"{x.get('number')}({x['_score']})")

        lines.append(f"    标杆: {', '.join(reps)}")
        lines.append("")

    lines.append("")
    lines.append("==================================================")
    lines.append("Next Step Suggestion:")
    lines.append("👉 Pick 3–6 candidates above, then call:")
    lines.append("   {object:'path_stopcheck', id:'G25,G31|南京南'}")
    lines.append("==================================================")

    return "\n".join(lines)

import statistics

# ============================
# 0) Telecode Helpers
# ============================

JINGHU_CORE = {"VNP", "AOH", "NKH", "UUH", "JGK"}

def is_jinghu_route(dep_code, arr_code):
    return dep_code in JINGHU_CORE and arr_code in JINGHU_CORE

def stops_at(t, telecode):
    timetable = t.get("timetable", [])
    return any(s.get("stationTelecode") == telecode for s in timetable)

def pretty_format_s2s_benchmark(payload: Any, query_date: str) -> str:
    """
    🏆 Station-to-Station BENCHMARK Directory
    (Standalone Version)

    - Does NOT depend on mini fields (_tier/_score)
    - Computes duration/stops/score internally
    - Outputs ONLY Peak + Top benchmark trains
    """

    # ============================
    # 1) Extract trains
    # ============================

    if isinstance(payload, dict):
        trains = payload.get("payload", {}).get("trains", [])
    elif isinstance(payload, list):
        trains = payload
    else:
        return "⚠️ Invalid payload."

    if not trains:
        return "⚠️ No trains found."

    # ============================
    # 2) Bureau Name Normalize
    # ============================

    bureau_map = {
        # ===== 华东 =====
        "上局": "上海局",
        "沪局": "上海局",

        "南局": "南昌局",  # ⚠️ 注意：payload 里的“南局”不是南宁
        "杭局": "杭州局",  # 少见但可能出现
        "合局": "合肥局",
        "福局": "福州局",  # 若你未来扩展到地方局

        # ===== 华北 =====
        "京局": "北京局",
        "津局": "北京局",  # 天津客运段常混写
        "太局": "太原局",

        # ===== 华中 =====
        "郑局": "郑州局",
        "武局": "武汉局",
        "广铁": "广铁U彩",  # 你要求的品牌化输出
        "广局": "广铁U彩",

        # ===== 华南 =====
        "宁局": "南宁局",  # ⚠️ payload 的宁局=南宁局
        "柳局": "南宁局",  # 历史遗留叫法

        # ===== 西南 =====
        "成局": "成都局",
        "昆局": "昆明局",
        "贵局": "贵阳局",
        "重局": "重庆局",  # 少见（通常归成局）

        # ===== 西北 =====
        "西局": "西安局",
        "兰局": "兰州局",
        "乌局": "乌鲁木齐局",

        # ===== 东北 =====
        "哈局": "哈尔滨局",
        "沈局": "沈阳局",
        "长局": "长春局",

        # ===== 华东补充 =====
        "济局": "济南局",
        "青局": "济南局",  # 青岛段有时会混写

        # ===== 特殊/可能出现 =====
        "呼局": "呼和浩特局",
        "南铁": "南昌局",
        "广铁集团": "广铁U彩",
    }

    def norm_bureau(x):
        return bureau_map.get(x.strip(), x.strip()) if x else "未知路局"

    # ============================
    # 3) Duration + OD Stops
    # ============================

    def infer_duration_min(t):
        dep = t.get("fromDepart")
        arr = t.get("toArrive")
        day_diff = int(t.get("dayDiff", 0))

        if not dep or not arr:
            return None

        try:
            dh, dm = map(int, dep.split(":"))
            ah, am = map(int, arr.split(":"))

            dep_m = dh * 60 + dm
            arr_m = ah * 60 + am + day_diff * 1440

            return arr_m - dep_m
        except:
            return None

    def dur_str(m):
        if m is None:
            return "?"
        return f"{m // 60}h{m % 60:02d}m"

    def train_type_tag(t):
        """
        图定 vs 临客判定
        rundays 很短 → 临客
        rundays 接近满表 → 图定
        """

        rundays = t.get("rundays", [])
        run_count = len(rundays)

        if run_count == 0:
            return "❓未知"

        # ⭐ RailGo: 未来90天窗口，图定通常接近满
        if run_count >= 66:
            return "图定"

        return "临客/高峰线"

    def od_stop_count(t):
        """
        OD intermediate stops only:
        timetable includes dep+arr → subtract 2
        """
        tt = t.get("timetable", [])
        if not tt:
            return 0
        return max(len(tt) - 2, 0)

    # ============================
    # 4) Compute raw metrics
    # ============================
    sample = trains[0]
    dep_code = sample.get("fromStationTelecode")
    arr_code = sample.get("toStationTelecode")

    jinghu_mode = is_jinghu_route(dep_code, arr_code)

    for t in trains:
        t["_dur"] = infer_duration_min(t)
        t["_stops"] = od_stop_count(t)

    # Benchmark is a relative rating over every valid OD train.  A nonstop
    # service naturally scores well on stops, but it is not a prerequisite.
    trains = [t for t in trains if t.get("_dur") is not None]
    durations = [x["_dur"] for x in trains if x["_dur"] is not None]

    if not durations:
        return "⚠️ No valid duration data."

    best_dur = min(durations)
    worst_dur = max(durations)

    # ============================
    # 5) Score Engine (Absolute Mode)
    # ============================

    def norm(x, best, worst):
        if worst == best:
            return 1.0
        return (worst - x) / (worst - best)

    stop_values = [x["_stops"] for x in trains]
    best_stops = min(stop_values)
    worst_stops = max(stop_values)

    def score(t):
        dur_score = norm(t["_dur"], best_dur, worst_dur)
        stop_score = norm(t["_stops"], best_stops, worst_stops)

        return int((0.9 * dur_score + 0.1 * stop_score) * 100)

    for t in trains:
        t["_score"] = score(t)
        t["_train_types"]=train_type_tag(t)

    # Sort by score desc
    trains = sorted(trains, key=lambda x: x["_score"], reverse=True)

    # ============================
    # 6) Tier 判定
    # ============================

    top10 = set([x.get("number") for x in trains[:10]])

    def is_super_g(train_no):
        if not train_no or not train_no.startswith("G"):
            return False
        try:
            return int(train_no[1:]) < 400
        except:
            return False

    def tier(t):
        train_no = t.get("number")
        s = t["_score"]

        if is_super_g(train_no):
            return "🥇⭐ 巅峰标杆"

        if train_no in top10 and s >= 85:
            return "🥇⭐ 巅峰标杆"

        if s >= 70:
            return "🏆 顶级标杆"

        return None

    # ============================
    # 7) Collect Benchmark Only
    # ============================

    benchmark = []

    for t in trains:

        label = tier(t)
        t["_benchmark_score"] = t["_score"]
        t["_benchmark_tier"] = label or ""
        t["_benchmark_running"] = running_status(t, query_date)
        if not label:
            continue

        # ✅ NKH flag only in 京沪模式
        if jinghu_mode:
            flag = compute_nkh_flag_from_train(t)
        else:
            flag = ""

        t["_running"] = t["_benchmark_running"]

        benchmark.append({
            "train": t.get("number"),
            "tier": label,
            "score": t["_score"],
            "dur": dur_str(t["_dur"]),
            "stops": t["_stops"],
            "bureau": norm_bureau(t.get("bureauName", "?")),
            "flag": flag , # ✅ here
            "traintype":t["_train_types"],
            "running":t["_running"],
        })

    if not benchmark:
        return "⚠️ No benchmark trains found."

    # ============================
    # 8) Pretty Output
    # ============================

    # =========================================================
    # 🚫 8.5) Hide NotRun trains (Passenger Strict Mode)
    # =========================================================

    benchmark = [
        x for x in benchmark
        if x.get("running") == "✅开行"
    ]

    if not benchmark:
        return (
            "⚠️ No benchmark trains are running on this query date.\n"
            f"📅 Query Date : {query_date}\n"
            "Try another date or use history mode."
        )

    # =========================================================
    # 8.6) Pretty Output (Benchmark Only)
    # =========================================================

    lines = []
    lines.append("==================================================")
    lines.append("🏆 Station-to-Station BENCHMARK Directory")
    lines.append("==================================================")
    lines.append(f"📅 Query Date : {query_date}")
    lines.append(
        "⚠️ Passenger Mode: NotRun trains are automatically hidden."
    )
    lines.append(f"🚆 Benchmark Trains Found : {len(benchmark)}")
    lines.append("--------------------------------------------------")

    for i, x in enumerate(benchmark, start=1):

        lines.append(
            f"{i:>2}. {x['train']:<6} {x['tier']} "
            f"Score={x['score']:>3} "
            f"⏱{x['dur']} Stops={x['stops']:>2} "
            f"Bureau={x['bureau']}"
            f"{x.get('flag','')}"
            f" ({x.get('traintype','?')})"
            f"({x.get('running','')} on query date)"
        )

    # =========================================================
    # 9) Bureau Directory (Only Running Benchmarks)
    # =========================================================

    lines.append("")
    lines.append("==================================================")
    lines.append("🏢 Bureau Peak Benchmarks (Running Only)")
    lines.append("==================================================")

    bureau_groups = {}
    for x in benchmark:
        bureau_groups.setdefault(x["bureau"], []).append(x)

    for bureau, items in bureau_groups.items():

        peak = [z for z in items if z["tier"] == "🥇⭐ 巅峰标杆"]
        top = [z for z in items if z["tier"] == "🏆 顶级标杆"]

        lines.append("")
        lines.append(f"- {bureau}:")

        # ✅巅峰全输出
        if peak:
            for z in peak:
                lines.append(
                    f"   🥇⭐ {z['train']}({z['score']}) ⏱{z['dur']}"
                )

        # 否则输出顶级标杆
        else:
            for z in top:
                lines.append(
                    f"   🏆 {z['train']}({z['score']}) ⏱{z['dur']}"
                )

    lines.append("")
    lines.append("==================================================")
    return "\n".join(lines)

print(railstore.check_validity("path_cache","G4035","20260314"))


def running_status(t, query_date):
    rundays = t.get("rundays", [])

    if not rundays:
        return "❓Unknown"

    if query_date in rundays:
        return "✅开行"

    return "⚠️未开行"

def compute_nkh_flag_from_train(t: dict) -> str:
    """
    ✅只基于 payload train 本体判断南京南
    不依赖 railstore，不会爆 dict 错误
    """

    timetable = t.get("timetable", [])
    if not timetable:
        return ""

    for stop in timetable:

        tele = stop.get("stationTelecode")
        name = stop.get("station")
        stop_time = stop.get("stopTime", 0)

        if tele == "NKH" or name == "南京南":

            # 出现但不停靠 → 跨越
            if stop_time == 0:
                return "未知"

            # 正常停车
            return "🛑停靠南京南"

    # 没出现南京南
    return "➡️跨越南京南"

import random

def sample_evenly(trains_sorted, k=5, jitter=1):
    """
    均匀抽样：覆盖快→慢全区间
    jitter: 动态扰动（±1）
    """

    M = len(trains_sorted)

    if M <= k:
        return trains_sorted

    result = []
    step = (M - 1) / (k - 1)

    for i in range(k):
        idx = int(round(i * step))

        # 动态扰动
        idx += random.randint(-jitter, jitter)

        idx = max(0, min(M - 1, idx))

        result.append(trains_sorted[idx])

    # 去重（扰动可能重复）
    uniq = []
    seen = set()
    for t in result:
        no = t.get("number")
        if no and no not in seen:
            uniq.append(t)
            seen.add(no)

    return uniq

import random
from typing import Any


def pretty_format_s2s_timeband_dep(payload: Any, query_date: str) -> str:
    """
    🕒 Station-to-Station TIMEBAND Directory (Departure)

    Upgraded Rules:
    - NotRun trains hidden
    - Group by departure band
    - Sort by duration
    - Too many trains → even sample
    - 🥇⭐ Peak Benchmarks are ALWAYS forced to appear
    """

    # ============================
    # 1) Extract trains
    # ============================

    if isinstance(payload, dict):
        trains = payload.get("payload", {}).get("trains", [])
    elif isinstance(payload, list):
        trains = payload
    else:
        return "⚠️ Invalid payload."

    if not trains:
        return "⚠️ No trains found."

    # ============================
    # 2) Bureau Normalize
    # ============================

    bureau_map = {
        # ===== 华东 =====
        "上局": "上海局",
        "沪局": "上海局",

        "南局": "南昌局",  # ⚠️ 注意：payload 里的“南局”不是南宁
        "杭局": "杭州局",  # 少见但可能出现
        "合局": "合肥局",
        "福局": "福州局",  # 若你未来扩展到地方局

        # ===== 华北 =====
        "京局": "北京局",
        "津局": "北京局",  # 天津客运段常混写
        "太局": "太原局",

        # ===== 华中 =====
        "郑局": "郑州局",
        "武局": "武汉局",
        "广铁": "广铁U彩",  # 你要求的品牌化输出
        "广局": "广铁U彩",

        # ===== 华南 =====
        "宁局": "南宁局",  # ⚠️ payload 的宁局=南宁局
        "柳局": "南宁局",  # 历史遗留叫法

        # ===== 西南 =====
        "成局": "成都局",
        "昆局": "昆明局",
        "贵局": "贵阳局",
        "重局": "重庆局",  # 少见（通常归成局）

        # ===== 西北 =====
        "西局": "西安局",
        "兰局": "兰州局",
        "乌局": "乌鲁木齐局",

        # ===== 东北 =====
        "哈局": "哈尔滨局",
        "沈局": "沈阳局",
        "长局": "长春局",

        # ===== 华东补充 =====
        "济局": "济南局",
        "青局": "济南局",  # 青岛段有时会混写

        # ===== 特殊/可能出现 =====
        "呼局": "呼和浩特局",
        "南铁": "南昌局",
        "广铁集团": "广铁U彩",
    }

    def norm_bureau(x):
        return bureau_map.get(x.strip(), x.strip()) if x else "未知路局"

    # ============================
    # 3) Duration + OD Stops
    # ============================

    def infer_duration_min(t):
        dep = t.get("fromDepart")
        arr = t.get("toArrive")
        day_diff = int(t.get("dayDiff", 0))

        if not dep or not arr:
            return None

        try:
            dh, dm = map(int, dep.split(":"))
            ah, am = map(int, arr.split(":"))

            dep_m = dh * 60 + dm
            arr_m = ah * 60 + am + day_diff * 1440

            return arr_m - dep_m
        except:
            return None

    def dur_str(m):
        if m is None:
            return "?"
        return f"{m // 60}h{m % 60:02d}m"

    def od_stop_count(t):
        tt = t.get("timetable", [])
        if not tt:
            return 0
        return max(len(tt) - 2, 0)

    # ============================
    # 4) Train Type Tag
    # ============================

    def train_type_tag(t):
        rundays = t.get("rundays", [])
        if not rundays:
            return "(未知)"

        if len(rundays) < 60:
            return "(临客/高峰线)"
        return "(图定)"

    # ============================
    # 5) Passenger Mode Filter
    # ============================

    running = []

    for t in trains:

        rundays = t.get("rundays", [])

        if not rundays:
            continue

        if query_date not in rundays:
            continue

        t["_dur"] = infer_duration_min(t)
        if t["_dur"] is None:
            continue

        t["_stops"] = od_stop_count(t)

        running.append(t)

    if not running:
        return "⚠️ No running trains on this date."

    # ============================
    # 6) Peak Benchmark Detection
    # ============================

    # Sort by duration
    sorted_by_dur = sorted(running, key=lambda x: x["_dur"])
    best_dur = sorted_by_dur[0]["_dur"]

    # Top10 fastest
    top10 = set([x.get("number") for x in sorted_by_dur[:10]])

    PEAK_DELTA = 6  # min

    def is_super_g(train_no):
        if not train_no or not train_no.startswith("G"):
            return False
        try:
            return int(train_no[1:]) < 400
        except:
            return False

    def is_peak_train(t):
        train_no = t.get("number")
        dur = t["_dur"]

        # Rule A: G<400 always peak
        if is_super_g(train_no):
            return True

        # Rule B: Top10 + within best+6min
        if train_no in top10 and dur <= best_dur + PEAK_DELTA:
            return True

        return False

    # Mark peak trains
    for t in running:
        t["_peak"] = is_peak_train(t)

    # ============================
    # 7) Time Band Grouping
    # ============================

    bands = {
        "🌅 Morning (06–11)": [],
        "🌞 Midday (11–14)": [],
        "🌇 Afternoon (14–18)": [],
        "🌙 Evening (18–24)": [],
    }

    def dep_hour(t):
        try:
            return int(t.get("fromDepart", "00:00").split(":")[0])
        except:
            return 0

    for t in running:
        h = dep_hour(t)

        if 6 <= h < 11:
            bands["🌅 Morning (06–11)"].append(t)
        elif 11 <= h < 14:
            bands["🌞 Midday (11–14)"].append(t)
        elif 14 <= h < 18:
            bands["🌇 Afternoon (14–18)"].append(t)
        else:
            bands["🌙 Evening (18–24)"].append(t)

    # ============================
    # 8) Even Sampling + Peak Forced
    # ============================

    MAX_PER_BAND = 8

    def sample_with_peak(lst):

        if len(lst) <= MAX_PER_BAND:
            return sorted(lst, key=lambda x: x["_dur"])

        # Always keep peak trains
        peaks = [x for x in lst if x["_peak"]]
        normals = [x for x in lst if not x["_peak"]]

        normals = sorted(normals, key=lambda x: x["_dur"])

        remain = MAX_PER_BAND - len(peaks)
        if remain <= 0:
            return sorted(peaks, key=lambda x: x["_dur"])

        # Even sample normals
        step = len(normals) / remain
        picked = []

        for i in range(remain):
            idx = int(i * step)
            jitter = random.choice([-1, 0, 1])
            idx = max(0, min(len(normals) - 1, idx + jitter))
            picked.append(normals[idx])

        # Merge peak + picked
        final = peaks + picked

        # Deduplicate
        uniq = []
        seen = set()
        for x in final:
            if x["number"] not in seen:
                uniq.append(x)
                seen.add(x["number"])

        return sorted(uniq, key=lambda x: x["_dur"])

    # ============================
    # 9) Pretty Output
    # ============================

    lines = []
    lines.append("==================================================")
    lines.append("🕒 Station-to-Station TIMEBAND Directory (Departure)")
    lines.append("==================================================")
    lines.append(f"📅 Query Date : {query_date}")
    lines.append("⚠️ Passenger Mode: NotRun trains hidden.")
    lines.append("🥇⭐ Peak Benchmarks are always displayed.")
    lines.append(f"🚆 Running Trains Found : {len(running)}")
    lines.append("--------------------------------------------------")

    for band_name, items in bands.items():

        if not items:
            continue

        items = sample_with_peak(items)

        lines.append("")
        lines.append(band_name)
        lines.append("--------------------------------------------------")

        for t in items:

            train_no = t.get("number", "?")
            bureau = norm_bureau(t.get("bureauName"))
            tag = train_type_tag(t)

            peak_tag = "🥇⭐巅峰标杆 " if t["_peak"] else ""

            lines.append(
                f"{train_no:<6} {peak_tag}"
                f"⏱{dur_str(t['_dur'])} "
                f"Stops={t['_stops']:>2} "
                f"Bureau={bureau} "
                f"{tag}"
                f"(✅开行)"
            )

    lines.append("")
    lines.append("==================================================")
    lines.append("👉 Next Step: Use [path_stopcheck/path_detail] for stop verification.")
    lines.append("==================================================")

    return "\n".join(lines)

import random


import random


def pretty_format_s2s_timeband_arr(payload: Any, query_date: str) -> str:
    """
    🕒 Station-to-Station TimeBand (Arrival)

    Optimized Features:
    - Arrival grouped into timebands
    - Cross-day arrivals show (+1/+2/+3)
    - Passenger Mode: NotRun hidden
    - Each band sorted by duration
    - Too many trains → dynamic uniform sampling (PCM-style)
    - Peak benchmark trains always pinned on top
    - Output matches benchmark style
    """

    # ============================
    # 1) Extract trains
    # ============================

    if isinstance(payload, dict):
        trains = payload.get("payload", {}).get("trains", [])
    elif isinstance(payload, list):
        trains = payload
    else:
        return "⚠️ Invalid payload."

    if not trains:
        return "⚠️ No trains found."

    # ============================
    # 2) Bureau Normalize
    # ============================

    bureau_map = {
        "上局": "上海局",
        "京局": "北京局",
        "郑局": "郑州局",
        "济局": "济南局",
        "宁局": "南宁局",
        "广铁": "广铁U彩",
        "南局": "南昌局",
        "哈局": "哈尔滨局",
        "沈局": "沈阳局",
        "西局": "西安局",
        "兰局": "兰州局",
        "呼局": "呼和浩特局",
        "成局": "成都局",
        "昆局": "昆明局",
        "乌局": "乌鲁木齐局",
    }

    def norm_bureau(x):
        return bureau_map.get(x.strip(), x.strip()) if x else "未知路局"

    # ============================
    # 3) Duration + OD Stops
    # ============================

    def infer_duration_min(t):
        dep = t.get("fromDepart")
        arr = t.get("toArrive")
        day_diff = int(t.get("dayDiff", 0))

        if not dep or not arr:
            return None

        try:
            dh, dm = map(int, dep.split(":"))
            ah, am = map(int, arr.split(":"))

            dep_m = dh * 60 + dm
            arr_m = ah * 60 + am + day_diff * 1440

            return arr_m - dep_m
        except:
            return None

    def dur_str(m):
        if m is None:
            return "?"
        return f"{m // 60}h{m % 60:02d}m"

    def od_stop_count(t):
        tt = t.get("timetable", [])
        return max(len(tt) - 2, 0)

    # ============================
    # 4) Running Filter (Passenger Mode)
    # ============================

    running = []

    for t in trains:
        rundays = t.get("rundays", [])

        # Passenger Mode: hide NotRun
        if rundays and query_date not in rundays:
            continue

        t["_dur"] = infer_duration_min(t)
        t["_stops"] = od_stop_count(t)

        running.append(t)

    if not running:
        return "⚠️ No running trains on this date."

    # ============================
    # 5) Peak Benchmark Detection
    # ============================

    # ============================
    # 5) Benchmark Tier Engine (Full Rule)
    # ============================

    import statistics

    durations = [t["_dur"] for t in running if t["_dur"] is not None]

    best_dur = min(durations)
    worst_dur = max(durations)

    median_dur = int(statistics.median(durations))

    # ⭐压缩程度指标
    gap = median_dur - best_dur

    # ---- Rank Map (Percentile Mode) ----
    sorted_by_dur = sorted(running, key=lambda x: x["_dur"] or 999999)
    N = len(sorted_by_dur)

    rank_map = {}
    for i, t in enumerate(sorted_by_dur, start=1):
        train_no = t.get("number")
        if train_no:
            rank_map[train_no] = i

    # ---- Score Engine ----
    def benchmark_score(t):

        # 京沪压缩线路 → 百分位
        if gap <= 20:
            train_no = t.get("number")
            if train_no not in rank_map:
                return 0
            rank = rank_map[train_no]

            if N <= 1:
                return 100

            return int(100 * (N - rank) / (N - 1))

        # 普通线路 → 90%耗时 +10%停站
        def norm(x, best, worst):
            if worst == best:
                return 1.0
            return (worst - x) / (worst - best)

        dur_score = norm(t["_dur"], best_dur, worst_dur)

        stop_vals = [x["_stops"] for x in running]
        best_stop = min(stop_vals)
        worst_stop = max(stop_vals)

        stop_score = norm(t["_stops"], best_stop, worst_stop)

        return int((0.90 * dur_score + 0.10 * stop_score) * 100)

    # Apply score
    for t in running:
        t["_score"] = benchmark_score(t)

    # Sort by score
    running = sorted(running, key=lambda x: x["_score"], reverse=True)

    # ---- Top10 Trains ----
    top10_trains = set(
        [t["number"] for t in running[:10] if t.get("number")]
    )

    # ---- Super G Rule ----
    def is_super_g(train_no: str) -> bool:
        if not train_no:
            return False
        if not train_no.startswith("G"):
            return False
        try:
            return int(train_no[1:]) < 400
        except:
            return False

    # ---- Tier Classification ----
    PEAK_DELTA_MIN = 6

    def tier(score: int, train_no: str, dur: int):

        # Rule A: G<400 永久巅峰
        if is_super_g(train_no):
            return "🥇⭐ 巅峰标杆"

        # 京沪压缩线路
        if gap <= 20:

            if (
                    train_no in top10_trains
                    and score >= 90
                    and dur is not None
                    and dur <= best_dur + PEAK_DELTA_MIN
            ):
                return "🥇⭐ 巅峰标杆"

            if score >= 90:
                return "🏆 顶级标杆"
            elif score >= 60:
                return "⚡ 优质快车"
            elif score >= 30:
                return "🚄 普通较快"
            else:
                return "🐢 慢车/多停"

        # 普通线路 Rule B
        if train_no in top10_trains and score >= 80:
            return "🥇⭐ 巅峰标杆"

        # Rule C: G<900 → 顶级标杆
        if train_no and train_no.startswith("G"):
            try:
                if int(train_no[1:]) < 900:
                    return "🏆 顶级标杆"
            except:
                pass

        # Normal tier
        if score >= 70:
            return "🏆 顶级标杆"
        elif score >= 46:
            return "⚡ 优质快车"
        elif score >= 20:
            return "🚄 普通较快"
        else:
            return "🐢 慢车/多停"

    # ---- Peak Benchmark Set ----
    peak_trains = set()

    for t in running:
        train_no = t.get("number")
        label = tier(t["_score"], train_no, t["_dur"])

        if label == "🥇⭐ 巅峰标杆":
            peak_trains.add(train_no)

    # ============================
    # 6) Arrival Band Classification
    # ============================

    def arr_hour_day(t):
        try:
            h = int(t.get("toArrive", "00:00").split(":")[0])
            day = int(t.get("dayDiff", 0))
            return h, day
        except:
            return 0, 0

    bands = {
        "🌌 Cross-Day Arrivals (+1/+2)": [],
        "🌅 Morning Arrivals (06–11)": [],
        "🌞 Midday Arrivals (11–14)": [],
        "🌇 Afternoon Arrivals (14–18)": [],
        "🌙 Evening Arrivals (18–24)": [],
        "🌌 Late Night Arrivals (00–06)": [],
    }

    for t in running:

        h, day = arr_hour_day(t)

        if day >= 1:
            bands["🌌 Cross-Day Arrivals (+1/+2)"].append(t)
            continue

        if 6 <= h < 11:
            bands["🌅 Morning Arrivals (06–11)"].append(t)
        elif 11 <= h < 14:
            bands["🌞 Midday Arrivals (11–14)"].append(t)
        elif 14 <= h < 18:
            bands["🌇 Afternoon Arrivals (14–18)"].append(t)
        elif 18 <= h < 24:
            bands["🌙 Evening Arrivals (18–24)"].append(t)
        else:
            bands["🌌 Late Night Arrivals (00–06)"].append(t)

    # ============================
    # 7) Dynamic Uniform Sampling (PCM-style)
    # ============================

    def uniform_sample_dynamic(sorted_list, k=6):
        """
        PCM-style sampling:
        evenly spaced + random jitter
        """
        n = len(sorted_list)
        if n <= k:
            return sorted_list

        step = n / k
        sampled = []

        for i in range(k):
            base = int(i * step)

            # jitter within segment
            jitter = random.randint(0, max(0, int(step) - 1))

            idx = min(base + jitter, n - 1)
            sampled.append(sorted_list[idx])

        return sampled

    # ============================
    # 8) Pretty Output
    # ============================

    lines = []
    lines.append("==================================================")
    lines.append("🕒 Station-to-Station TIMEBAND Directory (Arrival)")
    lines.append("==================================================")
    lines.append(f"📅 Query Date : {query_date}")
    lines.append("⚠️ Passenger Mode: NotRun trains are hidden.")
    lines.append("--------------------------------------------------")

    for band_name, items in bands.items():

        if not items:
            continue

        # Sort by duration
        items = sorted(items, key=lambda x: x["_dur"] or 999999)

        # Peak pinned
        peak_items = [t for t in items if t.get("number") in peak_trains]
        peak_items = sorted(peak_items, key=lambda x: x["_dur"] or 999999)

        normal_items = [t for t in items if t.get("number") not in peak_trains]

        sampled = uniform_sample_dynamic(normal_items, k=6)

        final_list = peak_items + sampled

        # Deduplicate
        seen = set()
        final_out = []
        for t in final_list:
            no = t.get("number")
            if no and no not in seen:
                seen.add(no)
                final_out.append(t)

        lines.append("")
        lines.append(band_name)
        lines.append("--------------------------------------------------")

        for t in final_out:

            train_no = t.get("number", "?")
            bureau = norm_bureau(t.get("bureauName"))
            dur = dur_str(t["_dur"])
            stops = t["_stops"]

            arr = t.get("toArrive", "?")
            day = int(t.get("dayDiff", 0))

            # Arrival Tag (+n)
            arr_tag = f"{arr}(+{day})" if day >= 1 else arr

            # Train Type
            rundays = t.get("rundays", [])
            traintype = "临客/高峰线" if len(rundays) < 76 else "图定"

            # Peak Label
            tier = "🥇⭐ 巅峰标杆" if train_no in peak_trains else ""

            lines.append(
                f"- {train_no:<6} {tier:<6} "
                f"⏱{dur} Stops={stops:>2} "
                f"Arr={arr_tag:<8} "
                f"Bureau={bureau} ({traintype})(✅开行)"
            )

    lines.append("")
    lines.append("==================================================")
    lines.append("👉 Next Step: Use [path_detail] for full stop evidence.")
    lines.append("==================================================")

    return "\n".join(lines)

import random
import statistics
from typing import Any


def pretty_format_s2s_regular_only(payload: Any, query_date: str) -> str:
    """
    📘 Station-to-Station REGULAR ONLY Directory (图定列车)

    Features:
    - Only Regular scheduled trains (图定)
    - NotRun trains are NOT hidden, only labeled
    - Sorted by departure time
    - Uniform segmented sampling (dynamic output)
    - Peak benchmark trains always included
    """

    # ============================
    # 1) Extract trains
    # ============================

    if isinstance(payload, dict):
        trains = payload.get("payload", {}).get("trains", [])
    elif isinstance(payload, list):
        trains = payload
    else:
        return "⚠️ Invalid payload."

    if not trains:
        return "⚠️ No trains found."

    # ============================
    # 2) Bureau Normalize
    # ============================

    bureau_map = {
        "上局": "上海局",
        "京局": "北京局",
        "郑局": "郑州局",
        "济局": "济南局",
        "宁局": "南宁局",
        "广铁": "广铁U彩",
        "南局": "南昌局",
        "哈局": "哈尔滨局",
        "沈局": "沈阳局",
        "西局": "西安局",
        "兰局": "兰州局",
        "呼局": "呼和浩特局",
        "成局": "成都局",
        "昆局": "昆明局",
        "武局": "武汉局",
        "太局": "太原局",
        "广局": "广州局",
    }

    def norm_bureau(x):
        return bureau_map.get(x.strip(), x.strip()) if x else "未知路局"

    # ============================
    # 3) Duration + Stops
    # ============================

    def infer_duration_min(t):
        dep = t.get("fromDepart")
        arr = t.get("toArrive")
        day_diff = int(t.get("dayDiff", 0))

        if not dep or not arr:
            return None

        try:
            dh, dm = map(int, dep.split(":"))
            ah, am = map(int, arr.split(":"))

            dep_m = dh * 60 + dm
            arr_m = ah * 60 + am + day_diff * 1440

            return arr_m - dep_m
        except:
            return None

    def dur_str(m):
        if m is None:
            return "?"
        return f"{m//60}h{m%60:02d}m"

    def od_stop_count(t):
        tt = t.get("timetable", [])
        return max(len(tt) - 2, 0)

    # ============================
    # 4) Filter Regular Only
    # ============================

    regular = []

    for t in trains:

        rundays = t.get("rundays", [])

        # 图定判定
        if len(rundays) < 76:
            continue

        t["_dur"] = infer_duration_min(t)
        t["_stops"] = od_stop_count(t)

        # Running flag (label only)
        if rundays and query_date in rundays:
            t["_running"] = "✅开行"
        else:
            t["_running"] = "⚠️不开行"

        regular.append(t)

    if not regular:
        return "⚠️ No regular scheduled trains found."

    # ============================
    # 5) Benchmark Tier Engine
    # ============================

    durations = [t["_dur"] for t in regular if t["_dur"] is not None]
    best_dur = min(durations)
    worst_dur = max(durations)

    median_dur = int(statistics.median(durations))
    gap = median_dur - best_dur

    # Rank Map (Percentile)
    sorted_by_dur = sorted(regular, key=lambda x: x["_dur"] or 999999)
    N = len(sorted_by_dur)

    rank_map = {}
    for i, t in enumerate(sorted_by_dur, start=1):
        train_no = t.get("number")
        if train_no:
            rank_map[train_no] = i

    def benchmark_score(t):

        # 京沪压缩线路 → 百分位
        if gap <= 20:
            rank = rank_map.get(t.get("number"), N)
            return int(100 * (N - rank) / (N - 1))

        # 普通线路 → 90% duration + 10% stops
        def norm(x, best, worst):
            if worst == best:
                return 1.0
            return (worst - x) / (worst - best)

        dur_score = norm(t["_dur"], best_dur, worst_dur)

        stop_vals = [x["_stops"] for x in regular]
        stop_score = norm(t["_stops"], min(stop_vals), max(stop_vals))

        return int((0.9 * dur_score + 0.1 * stop_score) * 100)

    for t in regular:
        t["_score"] = benchmark_score(t)

    regular = sorted(regular, key=lambda x: x["_score"], reverse=True)

    # Peak Benchmark 判定
    top10_trains = set([t["number"] for t in regular[:10]])

    def is_super_g(train_no):
        if not train_no or not train_no.startswith("G"):
            return False
        try:
            return int(train_no[1:]) < 400
        except:
            return False

    PEAK_DELTA_MIN = 6

    def is_peak(t):
        train_no = t.get("number")
        score = t["_score"]
        dur = t["_dur"]

        if is_super_g(train_no):
            return True

        if gap <= 20:
            return (
                train_no in top10_trains
                and score >= 90
                and dur <= best_dur + PEAK_DELTA_MIN
            )

        return train_no in top10_trains and score >= 90

    peak_trains = set([t["number"] for t in regular if is_peak(t)])

    # ============================
    # 6) Departure Time Segmented Sampling
    # ============================

    def dep_minutes(t):
        try:
            h, m = map(int, t.get("fromDepart", "00:00").split(":"))
            return h * 60 + m
        except:
            return 99999

    regular_sorted = sorted(regular, key=dep_minutes)

    # 分段参数
    SEGMENTS = 6
    PICKS_PER_SEG = 2

    sampled = []

    n = len(regular_sorted)
    seg_size = max(1, n // SEGMENTS)

    for i in range(SEGMENTS):

        seg = regular_sorted[i * seg_size:(i + 1) * seg_size]

        if not seg:
            continue

        # 巅峰固定保留
        peak_in_seg = [t for t in seg if t["number"] in peak_trains]

        # 普通随机抽样
        normal = [t for t in seg if t["number"] not in peak_trains]

        random.shuffle(normal)

        pick = peak_in_seg + normal[:PICKS_PER_SEG]

        sampled.extend(pick)

    # Deduplicate
    final = []
    seen = set()
    for t in sampled:
        if t["number"] not in seen:
            seen.add(t["number"])
            final.append(t)

    # ============================
    # 7) Pretty Output
    # ============================

    lines = []
    lines.append("==================================================")
    lines.append("📘 Station-to-Station REGULAR ONLY Directory")
    lines.append("==================================================")
    lines.append(f"📅 Query Date : {query_date}")
    lines.append("✅ 图定列车子集（不隐藏NotRun，仅标注）")
    lines.append("⚡ 动态分段抽样展示（巅峰标杆固定保留）")
    lines.append("--------------------------------------------------")

    for t in final:

        train_no = t.get("number", "?")
        dur = dur_str(t["_dur"])
        stops = t["_stops"]

        bureau = norm_bureau(t.get("bureauName"))
        running = t["_running"]

        peak_tag = "🥇⭐巅峰标杆" if train_no in peak_trains else ""

        dep = t.get("fromDepart", "?")
        arr = t.get("toArrive", "?")

        lines.append(
            f"- {train_no:<6} {peak_tag:<8}"
            f"Dep={dep} Arr={arr} "
            f"⏱{dur} Stops={stops:>2} "
            f"Bureau={bureau} ({running})"
        )

    lines.append("")
    lines.append("==================================================")
    lines.append("👉 Next Step: Use [path_detail] for stop evidence.")
    lines.append("==================================================")

    return "\n".join(lines)

import random
import statistics
from typing import Any


def pretty_format_s2s_temporary_only(payload: Any, query_date: str) -> str:
    """
    🚄 Station-to-Station TEMPORARY ONLY Directory (临客/高峰线)

    Features:
    - Only 临客 trains included
    - Score computed ONLY inside temporary subset
    - 京沪压缩线路 → Percentile ranking
    - 巅峰标杆固定展示（超级标杆规则）
    - Too many trains → uniform segmented sampling + random variation
    - NotRun trains NOT hidden, but marked as ❌不开行
    """

    # ============================
    # 1) Extract trains
    # ============================

    if isinstance(payload, dict):
        trains = payload.get("payload", {}).get("trains", [])
    elif isinstance(payload, list):
        trains = payload
    else:
        return "⚠️ Invalid payload."

    if not trains:
        return "⚠️ No trains found."

    # ============================
    # 2) Bureau Normalize
    # ============================

    bureau_map = {
        "上局": "上海局",
        "京局": "北京局",
        "郑局": "郑州局",
        "济局": "济南局",
        "宁局": "南宁局",
        "广铁": "广铁U彩",
        "南局": "南昌局",
        "哈局": "哈尔滨局",
        "沈局": "沈阳局",
        "西局": "西安局",
        "兰局": "兰州局",
        "呼局": "呼和浩特局",
        "成局": "成都局",
        "昆局": "昆明局",
        "武局": "武汉局",
        "太局": "太原局",
        "青局": "青藏公司",
    }

    def norm_bureau(x):
        return bureau_map.get(x.strip(), x.strip()) if x else "未知路局"

    # ============================
    # 3) Duration + OD Stops
    # ============================

    def infer_duration_min(t):
        dep = t.get("fromDepart")
        arr = t.get("toArrive")
        day_diff = int(t.get("dayDiff", 0))

        if not dep or not arr:
            return None

        try:
            dh, dm = map(int, dep.split(":"))
            ah, am = map(int, arr.split(":"))

            dep_m = dh * 60 + dm
            arr_m = ah * 60 + am + day_diff * 1440

            return arr_m - dep_m
        except:
            return None

    def dur_str(m):
        if m is None:
            return "?"
        return f"{m // 60}h{m % 60:02d}m"

    def od_stop_count(t):
        tt = t.get("timetable", [])
        return max(len(tt) - 2, 0)

    # ============================
    # 4) 临客筛选（temporary_only）
    # ============================

    temporary = []

    for t in trains:

        rundays = t.get("rundays", [])

        # 临客判定：rundays太短
        if rundays and len(rundays) >= 76:
            continue

        t["_dur"] = infer_duration_min(t)
        t["_stops"] = od_stop_count(t)

        # Running status
        if not rundays:
            t["_running"] = "❓未知"
        elif query_date in rundays:
            t["_running"] = "✅开行"
        else:
            t["_running"] = "❌不开行"

        temporary.append(t)

    if not temporary:
        return "⚠️ No temporary trains found."

    # ============================
    # 5) Score Engine (临客内部)
    # ============================

    durations = [t["_dur"] for t in temporary if t["_dur"] is not None]

    if not durations:
        return "⚠️ No valid duration data."

    best_dur = min(durations)
    worst_dur = max(durations)

    median_dur = int(statistics.median(durations))
    gap = median_dur - best_dur

    # Rank map for percentile mode
    sorted_by_dur = sorted(temporary, key=lambda x: x["_dur"] or 999999)
    N = len(sorted_by_dur)

    rank_map = {}
    for i, t in enumerate(sorted_by_dur, start=1):
        train_no = t.get("number")
        if train_no:
            rank_map[train_no] = i

    def norm(x, best, worst):
        if x is None:
            return 0.0
        if worst == best:
            return 1.0
        return (worst - x) / (worst - best)

    def benchmark_score(t):

        # 京沪压缩线路 percentile 模式
        if gap <= 20:

            # ⭐N=1 永不炸
            if N <= 1:
                return 100

            train_no = t.get("number")
            rank = rank_map.get(train_no, N)

            return int(100 * (N - rank) / (N - 1))

        # 普通线路 absolute 模式
        dur_score = norm(t["_dur"], best_dur, worst_dur)

        stop_vals = [x["_stops"] for x in temporary]
        stop_score = norm(t["_stops"], min(stop_vals), max(stop_vals))

        return int((0.9 * dur_score + 0.1 * stop_score) * 100)

    for t in temporary:
        t["_score"] = benchmark_score(t)

    # ============================
    # 6) Tier 判定（巅峰标杆规则）
    # ============================

    top10_trains = set(
        [t.get("number") for t in sorted(temporary, key=lambda x: x["_score"], reverse=True)[:10]]
    )

    PEAK_DELTA_MIN = 6

    def is_super_g(train_no: str) -> bool:
        if not train_no or not train_no.startswith("G"):
            return False
        try:
            return int(train_no[1:]) < 400
        except:
            return False

    def tier(t):

        train_no = t.get("number")
        score = t["_score"]
        dur = t["_dur"]

        # Rule A: G<400 永久巅峰
        if is_super_g(train_no):
            return "🥇⭐ 临客/高峰线中的巅峰标杆"

        # 京沪压缩线路规则
        if gap <= 20:

            if (
                train_no in top10_trains
                and score >= 90
                and dur is not None
                and dur <= best_dur + PEAK_DELTA_MIN
            ):
                return "🥇⭐ 临客/高峰线中的巅峰标杆"

            if score >= 90:
                return "🏆 临客/高峰线中的顶级标杆"
            return None

        # 普通线路规则
        if train_no in top10_trains and score >= 90:
            return "🥇⭐ 临客/高峰线中的巅峰标杆"

        if train_no and train_no.startswith("G"):
            try:
                if int(train_no[1:]) < 900:
                    return "🏆 临客/高峰线中的顶级标杆"
            except:
                pass

        if score >= 70:
            return "🏆 临客/高峰线中的顶级标杆"

        return None

    # ============================
    # 7) 时间排序 + 均匀分段抽样
    # ============================

    temporary = sorted(temporary, key=lambda x: x["_dur"] or 999999)

    peak_fixed = [t for t in temporary if tier(t) == "🥇⭐ 临客/高峰线中的巅峰标杆"]
    others = [t for t in temporary if t not in peak_fixed]

    def segmented_sample(lst, k=8):
        """
        均匀分段抽样 + 段内随机
        """
        n = len(lst)
        if n <= k:
            return lst

        step = n / k
        sampled = []

        for i in range(k):
            start = int(i * step)
            end = int((i + 1) * step)

            chunk = lst[start:end]
            if chunk:
                sampled.append(random.choice(chunk))

        return sampled

    sampled = segmented_sample(others, k=8)

    final_list = peak_fixed + sampled

    # Deduplicate
    seen = set()
    final = []
    for t in final_list:
        no = t.get("number")
        if no and no not in seen:
            seen.add(no)
            final.append(t)

    # ============================
    # 8) Pretty Output
    # ============================

    lines = []
    lines.append("==================================================")
    lines.append("🚄 Station-to-Station TEMPORARY ONLY Directory")
    lines.append("==================================================")
    lines.append(f"📅 Query Date : {query_date}")
    lines.append("⚡ 临客/高峰线子集（不开行也会标注）")
    lines.append("--------------------------------------------------")

    for i, t in enumerate(final, start=1):

        train_no = t.get("number", "?")
        bureau = norm_bureau(t.get("bureauName"))
        dur = dur_str(t["_dur"])
        stops = t["_stops"]

        label = tier(t) or ""
        running = t["_running"]

        lines.append(
            f"{i:>2}. {train_no:<6} {label:<6} "
            f"Score={t['_score']:>3} "
            f"⏱{dur} Stops={stops:>2} "
            f"Bureau={bureau} (临客/高峰线)({running})"
        )

    lines.append("")
    lines.append("==================================================")
    lines.append("👉 Next Step: Use [path_detail] for stop evidence.")
    lines.append("==================================================")

    return "\n".join(lines)

import random
import statistics

import random
import statistics


def pretty_format_s2s_bureau_directory(payload: Any, query_date: str) -> str:
    """
    🏢 Station-to-Station Bureau Benchmark Directory (Final)

    Features:
    - Bureau ranking by Peak Benchmark count (🥇⭐)
    - Passenger Mode: NotRun trains hidden
    - Each bureau:
        1) Peak benchmarks always shown
        2) Top benchmarks sampled (few)
        3) All other trains grouped by departure timeband
           and sampled dynamically by speed
    - Output includes 图定/临客高峰线 label
    """

    # ============================
    # 1) Extract trains
    # ============================

    if isinstance(payload, dict):
        trains = payload.get("payload", {}).get("trains", [])
    elif isinstance(payload, list):
        trains = payload
    else:
        return "⚠️ Invalid payload."

    if not trains:
        return "⚠️ No trains found."

    # ============================
    # 2) Bureau Normalize
    # ============================

    bureau_map = {
        "上局": "上海局",
        "京局": "北京局",
        "郑局": "郑州局",
        "济局": "济南局",
        "宁局": "南宁局",
        "广铁": "广铁U彩",
        "南局": "南昌局",
        "哈局": "哈尔滨局",
        "沈局": "沈阳局",
        "西局": "西安局",
        "兰局": "兰州局",
        "太局": "太原局",
        "呼局": "呼和浩特局",
        "成局": "成都局",
        "昆局": "昆明局",
        "武局": "武汉局",
        "广局": "广州局",
        "青局": "青藏公司",
        "乌局": "乌鲁木齐局",
    }

    def norm_bureau(x):
        return bureau_map.get(x.strip(), x.strip()) if x else "未知路局"

    # ============================
    # 3) Duration + Stops
    # ============================

    def infer_duration_min(t):
        dep = t.get("fromDepart")
        arr = t.get("toArrive")
        day_diff = int(t.get("dayDiff", 0))

        if not dep or not arr:
            return None

        try:
            dh, dm = map(int, dep.split(":"))
            ah, am = map(int, arr.split(":"))

            dep_m = dh * 60 + dm
            arr_m = ah * 60 + am + day_diff * 1440

            return arr_m - dep_m
        except:
            return None

    def dur_str(m):
        if m is None:
            return "?"
        return f"{m//60}h{m%60:02d}m"

    def od_stop_count(t):
        tt = t.get("timetable", [])
        return max(len(tt) - 2, 0)

    # ============================
    # 4) Passenger Mode Filter
    # ============================

    running = []

    for t in trains:
        rundays = t.get("rundays", [])

        if rundays and query_date not in rundays:
            continue

        t["_dur"] = infer_duration_min(t)
        t["_stops"] = od_stop_count(t)

        running.append(t)

    if not running:
        return "⚠️ No running trains on this date."

    # ============================
    # 5) Benchmark Score Engine
    # ============================

    durations = [x["_dur"] for x in running if x["_dur"] is not None]

    best_dur = min(durations)
    median_dur = int(statistics.median(durations))
    gap = median_dur - best_dur

    sorted_by_dur = sorted(running, key=lambda x: x["_dur"] or 999999)
    N = len(sorted_by_dur)

    rank_map = {}
    for i, t in enumerate(sorted_by_dur, start=1):
        rank_map[t["number"]] = i

    top10_trains = set([t["number"] for t in sorted_by_dur[:10]])

    def norm(x, best, worst):
        if worst == best:
            return 1.0
        return (worst - x) / (worst - best)

    def benchmark_score(t):

        if gap <= 20:
            rank = rank_map.get(t["number"], N)
            if N <= 1:
                return 100
            return int(100 * (N - rank) / (N - 1))

        worst_dur = max(durations)
        stop_vals = [x["_stops"] for x in running]
        worst_stop = max(stop_vals)

        dur_score = norm(t["_dur"], best_dur, worst_dur)
        stop_score = norm(t["_stops"], 0, worst_stop)

        return int((0.9 * dur_score + 0.1 * stop_score) * 100)

    for t in running:
        t["_score"] = benchmark_score(t)

    # ============================
    # 6) Tier Classification
    # ============================

    PEAK_DELTA_MIN = 6

    def is_super_g(train_no):
        if not train_no or not train_no.startswith("G"):
            return False
        try:
            return int(train_no[1:]) < 400
        except:
            return False

    def tier(t):
        train_no = t["number"]
        score = t["_score"]
        dur = t["_dur"]

        if is_super_g(train_no):
            return "🥇⭐ 巅峰标杆"

        if gap <= 20:
            if (
                train_no in top10_trains
                and score >= 90
                and dur <= best_dur + PEAK_DELTA_MIN
            ):
                return "🥇⭐ 巅峰标杆"
            if score >= 90:
                return "🏆 顶级标杆"
            elif score >= 60:
                return "⚡ 优质快车"
            elif score >= 30:
                return "🚄 普通较快"
            else:
                return None

        if train_no in top10_trains and score >= 90:
            return "🥇⭐ 巅峰标杆"

        if score >= 70:
            return "🏆 顶级标杆"
        elif score >= 46:
            return "⚡ 优质快车"
        elif score >= 20:
            return "🚄 普通较快"
        else:
            return None

    for t in running:
        t["_tier"] = tier(t)

    # ============================
    # 7) Train Type Label
    # ============================

    def train_type(t):
        rundays = t.get("rundays", [])
        return "临客/高峰线" if len(rundays) < 76 else "图定"

    # ============================
    # 8) Timeband Grouping
    # ============================

    def dep_band(t):
        h = int(t.get("fromDepart", "00:00").split(":")[0])
        if 0 <= h < 6:
            return "🌌 Late Night (00–06)"
        elif 6 <= h < 11:
            return "🌅 Morning (06–11)"
        elif 11 <= h < 14:
            return "🌞 Midday (11–14)"
        elif 14 <= h < 18:
            return "🌇 Afternoon (14–18)"
        else:
            return "🌙 Evening (18–24)"

    def uniform_sample(lst, k=3):
        n = len(lst)
        if n <= k:
            return lst
        idxs = [int(i * (n - 1) / (k - 1)) for i in range(k)]
        return [lst[i] for i in idxs]

    # ============================
    # 9) Bureau Grouping
    # ============================

    bureau_groups = {}
    for t in running:
        bureau = norm_bureau(t.get("bureauName"))
        bureau_groups.setdefault(bureau, []).append(t)

    # Bureau ranking by Peak count
    bureau_sorted = sorted(
        bureau_groups.items(),
        key=lambda kv: len([x for x in kv[1] if x["_tier"] == "🥇⭐ 巅峰标杆"]),
        reverse=True
    )

    # ============================
    # 10) Pretty Output
    # ============================

    lines = []
    lines.append("==================================================")
    lines.append("🏢 Station-to-Station Bureau Benchmark Directory")
    lines.append("==================================================")
    lines.append(f"📅 Query Date : {query_date}")
    lines.append("⚠️ Passenger Mode: NotRun trains hidden.")
    lines.append("⚠️ Non-peak trains are dynamically sampled by timeband.")
    lines.append("--------------------------------------------------")

    for bureau, items in bureau_sorted:

        peak = [t for t in items if t["_tier"] == "🥇⭐ 巅峰标杆"]
        top = [t for t in items if t["_tier"] == "🏆 顶级标杆"]
        normal = [t for t in items if t["_tier"] not in ["🥇⭐ 巅峰标杆", "🏆 顶级标杆"]]

        lines.append("")
        lines.append(f"🏢 {bureau}  (Peak={len(peak)})")
        lines.append("--------------------------------------------------")

        # ① Peak always full
        for t in peak:
            lines.append(
                f"{t['number']:<6} 🥇⭐巅峰标杆 ⏱{dur_str(t['_dur'])} Dep={t.get('fromDepart')} ({train_type(t)})"
            )

        # ② Top benchmark sample (max 2)
        top = sorted(top, key=lambda x: x["_dur"] or 999999)
        for t in top[:2]:
            lines.append(
                f"{t['number']:<6} 🏆顶级标杆 ⏱{dur_str(t['_dur'])} Dep={t.get('fromDepart')} ({train_type(t)})"
            )

        # ③ Normal trains by timeband sampled
        band_groups = {}
        for t in normal:
            band_groups.setdefault(dep_band(t), []).append(t)

        for band, band_items in band_groups.items():

            band_items = sorted(band_items, key=lambda x: x["_dur"] or 999999)
            sampled = uniform_sample(band_items, k=3)

            if not sampled:
                continue

            lines.append(f"  {band} (sampled)")
            for t in sampled:
                lines.append(
                    f"   - {t['number']:<6} ⏱{dur_str(t['_dur'])} Dep={t.get('fromDepart')} ({train_type(t)})"
                )

    lines.append("")
    lines.append("==================================================")
    lines.append("👉 Next Step: Use [path_detail] for stop evidence.")
    lines.append("==================================================")

    return "\n".join(lines)

# =========================================================
# Local Pretty Test (Minimal)
# =========================================================
"""if __name__ == "__main__":
    import time

    print("\n==================================================")
    print("🚆 RailAI v2.6 — s2s_query.py Pretty Local Test")
    print("==================================================\n")

    dep = "上海虹桥"
    arr = "北京南"
    date = "20260212"

    payload = query_s2s_route(dep, arr, date)
    print("DEBUG PAYLOAD TYPE =", type(payload))
    print("DEBUG PAYLOAD KEYS =", payload.keys() if isinstance(payload, dict) else None)
    print("DEBUG PAYLOAD SAMPLE =", str(payload)[:300])

    if payload:
        print("✅ s2s query success.\n")
        print(pretty_format_s2s(payload, date))
    else:
        print("❌ s2s query failed.")

    print("\n⏳ Sleeping 10s to protect RailGo server...")
    time.sleep(10)"""

# ==========================================================
# ✅ Local Test Block (Only runs when executed directly)
# ==========================================================
if __name__ == "__main__":
    import time

    print("\n==================================================")
    print("🚆 RailAI v2.6 — s2s_query.py Pretty Local Test")
    print("==================================================\n")

    dep = "福州"
    arr = "深圳北"
    date = "20260213"
    # ======================================================
    # ✅ REAL LOCAL DATABASE QUERY (RailGo SQLite Cache)
    # ======================================================
    payload = query_s2s_route(dep, arr, date)
    # ======================================================
    # ✅ Extract trains for sorting test
    # ======================================================
    trains = payload.get("payload", {}).get("trains", [])


    # ======================================================
    # ✅ Benchmark Ranking = Fastest Trains
    # =====================================================

    # ⭐这里直接喂给 pretty_format_s2s
    print(pretty_format_s2s_bureau_directory(payload, date))

    print("\n⏳ Sleeping 5s (local safe)...")
    time.sleep(3)

