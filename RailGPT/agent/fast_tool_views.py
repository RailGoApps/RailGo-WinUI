from __future__ import annotations

from typing import Any, Dict, Iterable, List

from tools.rail.s2s_leftTicket_12306 import SEAT_DISPLAY_ORDER, parse_train_line
from tools.rail.s2s_query import normalize_bureau
from tools.rail.station_dict import station_dict
from tools.rail.train_query import (
    get_latest_record,
    summarize_emu_history,
    summarize_train_history,
)
from tools.rail.railgo_v2_tools import RAILGO_V2_TOOL_OBJECTS
from tools.rail.railgo_catalog_tools import RAILGO_CATALOG_TOOL_OBJECTS


S2S_BATCH_SIZE = 6
LEFT_TICKET_BATCH_SIZE = 6
TRANSFER_BATCH_SIZE = 4
PATH_BATCH_SIZE = 12
STOPCHECK_BATCH_SIZE = 8
HISTORY_BATCH_SIZE = 8
REGULAR_RUN_THRESHOLD = 76


def build_fast_views(query: Dict[str, Any], raw_payload: Any = None) -> List[Dict[str, Any]]:
    if not isinstance(query, dict):
        return []

    obj = str(query.get("object") or "").strip()

    if obj == "train":
        return _build_train_history_views(query)

    if obj == "emu":
        return _build_emu_history_views(query)

    if obj == "smartemu_analysis":
        return _build_smartemu_views(query, raw_payload)

    if obj in {"path_detail", "path_future", "path_past"}:
        return _build_path_views(query, raw_payload)

    if obj == "path_stopcheck":
        return _build_stopcheck_views(query, raw_payload)

    if obj == "left_ticket_12306":
        return _build_left_ticket_views(query, raw_payload)

    if obj == "transfer_12306":
        return _build_transfer_views(query, raw_payload)

    if obj.startswith("station_to_station") or obj.startswith("s2s_"):
        return _build_s2s_views(query, raw_payload)

    if obj in RAILGO_V2_TOOL_OBJECTS:
        return _build_railgo_v2_views(query, raw_payload)

    if obj in RAILGO_CATALOG_TOOL_OBJECTS:
        return _build_railgo_catalog_views(query, raw_payload)

    return []


def _build_railgo_catalog_views(query: Dict[str, Any], raw_payload: Any) -> List[Dict[str, Any]]:
    obj = str(query.get("object") or "").strip()

    if obj == "station_preselect":
        rows = [item for item in (raw_payload or []) if isinstance(item, dict)]
        views = [
            _make_view(
                query,
                view_id="overview",
                view_type="station_preselect_overview",
                priority=120,
                lines=[f"CANDIDATE_COUNT: {len(rows)}", "RESULT_TYPE: fuzzy station candidates"],
            )
        ]
        for batch_index, batch in enumerate(_chunked(rows, 10), start=1):
            lines = []
            for row in batch:
                name = row.get("name") or row.get("station") or row.get("stationName") or "?"
                telecode = row.get("telecode") or row.get("stationTelecode") or row.get("code") or "?"
                pinyin = row.get("pinyin") or row.get("py") or ""
                lines.append(f"- station={name} telecode={telecode} pinyin={pinyin}")
            views.append(
                _make_view(
                    query,
                    view_id=f"station_candidates_{batch_index:02d}",
                    view_type="station_preselect_candidate_batch",
                    priority=max(80, 108 - batch_index),
                    lines=lines,
                )
            )
        return views

    if obj == "train_preselect":
        rows = [str(item).strip() for item in (raw_payload or []) if str(item or "").strip()]
        return [
            _make_view(
                query,
                view_id="candidates",
                view_type="train_preselect_candidates",
                priority=120,
                lines=[f"CANDIDATE_COUNT: {len(rows)}", f"TRAINS: {', '.join(rows)}"],
            )
        ]

    info = raw_payload if isinstance(raw_payload, dict) else {}
    return [
        _make_view(
            query,
            view_id="random_train",
            view_type="random_train_result",
            priority=120,
            lines=[
                f"TRAIN: {info.get('number', '?')}",
                f"ROUTE: {info.get('fromStation', '?')} -> {info.get('toStation', '?')}",
                f"DEPARTURE: {info.get('departTime', '?')}",
            ],
        )
    ]


def _build_railgo_v2_views(query: Dict[str, Any], raw_payload: Any) -> List[Dict[str, Any]]:
    obj = str(query.get("object") or "").strip()
    data = raw_payload if isinstance(raw_payload, (dict, list)) else query.get("evidence")
    freshness = query.get("freshness") if isinstance(query.get("freshness"), dict) else {}
    freshness_lines = []
    if freshness.get("fetched_at"):
        freshness_lines.append(f"OBSERVED_AT: {freshness['fetched_at']}")
    if freshness.get("age_seconds") is not None:
        freshness_lines.append(f"DATA_AGE_SECONDS: {freshness['age_seconds']}")

    if obj == "train_delay":
        rows = [item for item in (data or []) if isinstance(item, dict)]
        status_counts: Dict[str, int] = {}
        for row in rows:
            status = str(row.get("delayStatus") or row.get("delayStatusCode") or "unknown")
            status_counts[status] = status_counts.get(status, 0) + 1
        views = [
            _make_view(
                query,
                view_id="overview",
                view_type="train_delay_overview",
                priority=130,
                lines=freshness_lines + [f"STATION_COUNT: {len(rows)}", f"STATUS_COUNTS: {status_counts}"],
            )
        ]
        for batch_index, batch in enumerate(_chunked(rows, 10), start=1):
            lines = [
                (
                    f"- station={row.get('stationName', '?')}({row.get('stationTelecode', '?')}) "
                    f"status={row.get('delayStatus', '?')} code={row.get('delayStatusCode', '?')} "
                    f"delay_min={row.get('delayTime', '?')} "
                    f"plan_arr={row.get('planArriveTime', '--')} plan_dep={row.get('planDepartTime', '--')}"
                )
                for row in batch
            ]
            views.append(
                _make_view(
                    query,
                    view_id=f"delay_batch_{batch_index:02d}",
                    view_type="train_delay_station_batch",
                    priority=max(85, 115 - batch_index),
                    lines=lines,
                )
            )
        return views

    if obj == "train_station_access":
        info = data if isinstance(data, dict) else {}
        return [
            _make_view(
                query,
                view_id="access",
                view_type="train_station_access_live",
                priority=130,
                lines=[
                    *freshness_lines,
                    f"ENTRANCE_OR_CHECK_GATE: {info.get('entrance') or []}",
                    f"EXIT: {info.get('exit') or []}",
                    f"PLATFORM: {info.get('platform') or 'not published'}",
                    "EMPTY_FIELDS_MEAN: RailGo has not published that field for this train/station/date",
                ],
            )
        ]

    if obj == "station_board":
        rows = [item for item in (data or []) if isinstance(item, dict)]
        grounded_slots = query.get("grounded_slots") if isinstance(query.get("grounded_slots"), dict) else {}
        station_name = str(grounded_slots.get("station") or "?").strip()
        direction = str(grounded_slots.get("direction") or "?").strip()
        views = [
            _make_view(
                query,
                view_id="overview",
                view_type="station_board_overview",
                priority=130,
                lines=freshness_lines + [
                    "TOOL_EXECUTION_STATUS: completed",
                    f"BOARD_STATION: {station_name}",
                    f"BOARD_DIRECTION: {direction}",
                    f"VISIBLE_ROW_COUNT: {len(rows)}",
                    "TIME_SEMANTICS: OBSERVED_AT is the exact snapshot time; do not estimate elapsed time or rename it as another clock time",
                    "SNAPSHOT_BOUNDARY: rows may change after OBSERVED_AT; never claim continuous real-time updates",
                    "SUBJECT_BOUNDARY: row origins, destinations, and train numbers are board entries, not the queried station or conversation anchor",
                ],
            )
        ]
        for batch_index, batch in enumerate(_chunked(rows, 8), start=1):
            lines = [
                (
                    f"- train={row.get('trainNum', '?')} time={row.get('time', '--')} "
                    f"status={row.get('bigScreenStatus', '?')} delay_min={row.get('timeDelay', 0)} "
                    f"from={row.get('trainStartStation', '?')} to={row.get('trainEndStation', '?')} "
                    f"ports={row.get('bigScreenPort') or []}"
                )
                for row in batch
            ]
            views.append(
                _make_view(
                    query,
                    view_id=f"board_batch_{batch_index:02d}",
                    view_type="station_board_row_batch",
                    priority=max(80, 112 - batch_index),
                    lines=lines,
                )
            )
        return views

    if obj == "coach_layout":
        info = data if isinstance(data, dict) else {}
        car_info = [item for item in (info.get("carInfo") or []) if isinstance(item, dict)]
        coaches = [item for item in (info.get("coachPicList") or []) if isinstance(item, dict)]
        views = [
            _make_view(
                query,
                view_id="overview",
                view_type="coach_layout_overview",
                priority=130,
                lines=[
                    *freshness_lines,
                    f"PUBLISHED_CAR_CODE: {info.get('carCode', '?')}",
                    f"CAR_TYPE: {info.get('carType', '?')}",
                    f"TRAIN_STYLE: {info.get('trainStyle', '?')}",
                    "BOUNDARY: this is the current published coach product; historical assignment analysis belongs to rail.re train/emu tools",
                ],
            )
        ]
        if car_info:
            views.append(
                _make_view(
                    query,
                    view_id="specifications",
                    view_type="coach_layout_specifications",
                    priority=120,
                    lines=[
                        f"- {row.get('pictureName', '?')}: {row.get('pictureValue', '')}"
                        for row in car_info
                    ],
                )
            )
        for batch_index, batch in enumerate(_chunked(coaches, 8), start=1):
            views.append(
                _make_view(
                    query,
                    view_id=f"coach_batch_{batch_index:02d}",
                    view_type="coach_layout_car_batch",
                    priority=max(82, 110 - batch_index),
                    lines=[
                        f"- {row.get('pictureName', '?')}: {row.get('pictureValue', '')}"
                        for row in batch
                    ],
                )
            )
        return views

    if obj == "train_route_map":
        info = data if isinstance(data, dict) else {}
        stations = [str(item) for item in (info.get("stations") or []) if str(item).strip()]
        segments = [str(item) for item in (info.get("segments") or []) if str(item).strip()]
        views = [
            _make_view(
                query,
                view_id="overview",
                view_type="train_route_map_overview",
                priority=130,
                lines=[
                    *freshness_lines,
                    f"COORDINATE_SOURCE: {info.get('coordinate_source', 'RailGo GCJ-02')}",
                    f"STATION_COUNT: {info.get('station_count', len(stations))}",
                    f"LINE_SEGMENT_COUNT: {info.get('segment_count', len(segments))}",
                    f"DISPLAY_POLYLINE_POINTS: {info.get('display_point_count', '?')}",
                    f"ESTIMATED_POLYLINE_KM: {info.get('estimated_polyline_km', '?')}",
                    f"ESTIMATED_DIRECT_KM: {info.get('estimated_direct_km', '?')}",
                    f"DISTANCE_NOTE: {info.get('distance_note', 'coordinate estimate')}",
                    "BOUNDARY: coordinates are map evidence; timetable and stops belong to path_detail",
                ],
            )
        ]
        for batch_index, batch in enumerate(_chunked(stations, 12), start=1):
            views.append(
                _make_view(
                    query,
                    view_id=f"station_names_{batch_index:02d}",
                    view_type="train_route_map_station_batch",
                    priority=max(88, 115 - batch_index),
                    lines=[
                        f"- station={row}"
                        for row in batch
                    ],
                )
            )
        for batch_index, batch in enumerate(_chunked(segments, 8), start=1):
            views.append(
                _make_view(
                    query,
                    view_id=f"line_segments_{batch_index:02d}",
                    view_type="train_route_map_segment_batch",
                    priority=max(75, 102 - batch_index),
                    lines=[
                        f"- segment={row}"
                        for row in batch
                    ],
                )
            )
        return views

    return _build_pretty_fallback_view(query, "railgo_v2_pretty_fallback")


def build_fast_candidates(query: Dict[str, Any], raw_payload: Any = None) -> List[Dict[str, Any]]:
    if not isinstance(query, dict):
        return []

    obj = str(query.get("object") or "").strip()

    if obj == "train":
        return _build_train_seed_candidates(query)

    if obj == "emu":
        return _build_emu_seed_candidates(query)

    if obj == "smartemu_analysis":
        return _build_smartemu_seed_candidates(query, raw_payload)

    if obj in {"path_detail", "path_future", "path_past"}:
        return _build_path_seed_candidates(query, raw_payload)

    if obj == "path_stopcheck":
        return _build_stopcheck_seed_candidates(query, raw_payload)

    if obj == "left_ticket_12306":
        return _build_left_ticket_seed_candidates(query, raw_payload)

    if obj == "transfer_12306":
        return _build_transfer_seed_candidates(query, raw_payload)

    if obj.startswith("station_to_station") or obj.startswith("s2s_"):
        return _build_s2s_seed_candidates(query, raw_payload)

    return []


def _build_train_seed_candidates(query: Dict[str, Any]) -> List[Dict[str, Any]]:
    records = query.get("records") or []
    if not isinstance(records, list) or not records:
        return []

    latest = get_latest_record(records)
    summary = summarize_train_history(records)
    unique_emu_count = summary.get("unique_emu_count", 0)

    if unique_emu_count <= 1:
        score = 92
    elif unique_emu_count <= 2:
        score = 86
    else:
        score = 78

    return [
        _make_seed_candidate(
            candidate_key=f"train:{query.get('id', '?')}",
            candidate_type="train",
            label=str(query.get("id") or "?"),
            score=score,
            why="Deterministic train-history summary.",
            supporting_points=[
                f"latest_emu={latest.get('emu_no', '?') if latest else '?'}",
                f"latest_record_time={latest.get('date', '?') if latest else '?'}",
                f"unique_emu_count={unique_emu_count}",
            ],
            attributes={
                "train_no": str(query.get("id") or ""),
                "emu": str(latest.get("emu_no") or "") if latest else "",
            },
        )
    ]


def _build_emu_seed_candidates(query: Dict[str, Any]) -> List[Dict[str, Any]]:
    records = query.get("records") or []
    if not isinstance(records, list) or not records:
        return []

    latest = get_latest_record(records)
    summary = summarize_emu_history(records)

    return [
        _make_seed_candidate(
            candidate_key=f"emu:{query.get('id', '?')}",
            candidate_type="train",
            label=str(query.get("id") or "?"),
            score=84,
            why="Deterministic EMU-history summary.",
            supporting_points=[
                f"latest_train={latest.get('train_no', '?') if latest else '?'}",
                f"latest_record_time={latest.get('date', '?') if latest else '?'}",
                f"unique_train_count={summary.get('unique_train_count', 0)}",
            ],
            attributes={
                "emu": str(query.get("id") or ""),
                "train_no": str(latest.get("train_no") or "") if latest else "",
            },
        )
    ]


def _is_smartemu_family(family: Any) -> bool:
    fam = str(family or "").strip().upper().replace("-", "")
    if not fam or fam == "UNKNOWN":
        return False
    return fam.endswith(("BS", "BZ", "AZ", "AS", "AFZ", "BFZ", "AFS", "BFS"))


def _extract_smartemu_results(query: Dict[str, Any], raw_payload: Any) -> List[Dict[str, Any]]:
    results = query.get("results")
    if isinstance(results, list):
        return results
    if isinstance(raw_payload, list):
        return raw_payload
    return []


def _build_smartemu_seed_candidates(query: Dict[str, Any], raw_payload: Any) -> List[Dict[str, Any]]:
    results = _extract_smartemu_results(query, raw_payload)
    if not results:
        return []

    candidates = []
    for index, item in enumerate(results[:5]):
        train_no = str(item.get("train") or "").strip().upper()
        inference = item.get("inference") or {}
        candidate_family = str(inference.get("candidate") or "UNKNOWN").strip().upper()
        confidence = str(inference.get("confidence") or "LOW").strip().upper()
        recent5 = [
            str(family).strip().upper()
            for family in (inference.get("recent5") or [])
            if str(family).strip()
        ]
        top3 = inference.get("prob_top3") or []
        top_prob = 0.0
        if top3:
            try:
                top_prob = float(top3[0][1])
            except Exception:
                top_prob = 0.0

        score = 70 + min(25, int(round(top_prob * 25)))
        if confidence == "HIGH":
            score += 4
        elif confidence == "MEDIUM":
            score += 2
        if _is_smartemu_family(candidate_family):
            score += 3
        score = max(40, min(100, score - index))

        candidates.append(
            _make_seed_candidate(
                candidate_key=f"smartemu:{train_no or index}",
                candidate_type="train",
                label=train_no or f"train#{index + 1}",
                score=score,
                why="Deterministic smart-EMU probability summary.",
                supporting_points=[
                    f"candidate_family={candidate_family}",
                    f"confidence={confidence}",
                    f"recent5={'/'.join(recent5[:5]) or 'none'}",
                    f"smart_signal={_is_smartemu_family(candidate_family) or any(_is_smartemu_family(x) for x in recent5)}",
                ],
                attributes={
                    "train_no": train_no,
                    "candidate_family": candidate_family,
                    "smart_hint": "yes" if (_is_smartemu_family(candidate_family) or any(_is_smartemu_family(x) for x in recent5)) else "no",
                },
            )
        )

    return candidates


def _build_s2s_seed_candidates(query: Dict[str, Any], raw_payload: Any) -> List[Dict[str, Any]]:
    trains = _filter_s2s_trains_for_object(query, _extract_s2s_trains(raw_payload))
    if not trains:
        return []

    ranked_rows = []
    for train in trains:
        duration_min = _minutes_between(
            train.get("fromDepart"),
            train.get("toArrive"),
            train.get("dayDiff"),
        )
        ranked_rows.append(
            {
                "train_no": str(train.get("number") or _first_train_code(train.get("numberFull")) or "?"),
                "dep": str(train.get("fromDepart") or "?"),
                "arr_tag": _arrival_tag(train.get("toArrive"), train.get("dayDiff")),
                "duration_min": duration_min,
                "duration_text": _format_minutes(duration_min),
                "od_stops": _od_stop_count(train),
                "bureau": normalize_bureau(str(train.get("bureauName") or "unknown")),
                "model": str(train.get("car") or "?"),
                "run_flag": _run_flag(train.get("rundays"), query.get("date")),
                "train_type": _train_type(train.get("rundays")),
            }
        )

    ranked_rows.sort(
        key=lambda item: (
            _run_rank(item["run_flag"]),
            item["duration_min"] if item["duration_min"] is not None else 10**9,
            item["od_stops"],
            item["train_no"],
        )
    )

    candidates = []
    for index, row in enumerate(ranked_rows[:5]):
        score = 98 - index * 5
        if row["run_flag"] == "unknown":
            score -= 8
        elif row["run_flag"] == "not_running":
            score -= 20

        if row["train_type"] == "temporary":
            score -= 2

        candidates.append(
            _make_seed_candidate(
                candidate_key=f"train:{row['train_no']}",
                candidate_type="train",
                label=row["train_no"],
                score=max(40, score),
                why="Deterministic s2s fast pre-rank.",
                supporting_points=[
                    f"route={query.get('id', '?')}",
                    f"duration={row['duration_text']}",
                    f"od_stops={row['od_stops']}",
                    f"run={row['run_flag']}",
                    f"bureau={row['bureau']}",
                ],
                attributes={
                    "train_no": row["train_no"],
                    "route": str(query.get("id") or ""),
                    "date": str(query.get("date") or ""),
                },
            )
        )

    return candidates


def _build_left_ticket_seed_candidates(query: Dict[str, Any], raw_payload: Any) -> List[Dict[str, Any]]:
    payload = _extract_payload(raw_payload)
    if not isinstance(payload, dict) or "error" in payload:
        return []

    data = payload.get("data", {})
    trains_raw = data.get("result", [])
    if not trains_raw:
        return []

    rows = []
    for line in trains_raw:
        parsed = parse_train_line(line)
        seats = parsed.get("seats", {})
        available_summary = _seat_summary(seats, only_available=True)

        rows.append(
            {
                "train_no": parsed.get("train", "?"),
                "dep": parsed.get("dep", "?"),
                "arr": parsed.get("arr", "?"),
                "dur": parsed.get("dur", "?"),
                "dur_min": _duration_text_to_minutes(parsed.get("dur")),
                "has_ticket": bool(available_summary),
                "seats_text": available_summary or _seat_summary(seats, only_available=False),
                "seat_score": _seat_score(seats),
            }
        )

    rows.sort(
        key=lambda item: (
            0 if item["has_ticket"] else 1,
            item["dur_min"] if item["dur_min"] is not None else 10**9,
            -item["seat_score"],
            item["train_no"],
        )
    )

    candidates = []
    for index, row in enumerate(rows[:5]):
        score = 97 - index * 6
        if not row["has_ticket"]:
            score -= 28

        candidates.append(
            _make_seed_candidate(
                candidate_key=f"ticket:{row['train_no']}",
                candidate_type="ticket",
                label=row["train_no"],
                score=max(35, score),
                why="Deterministic left-ticket fast pre-rank.",
                supporting_points=[
                    f"route={query.get('id', '?')}",
                    f"duration={row['dur']}",
                    f"ticket={'available' if row['has_ticket'] else 'sold_out'}",
                    f"seats={row['seats_text']}",
                ],
                attributes={
                    "train_no": row["train_no"],
                    "route": str(query.get("id") or ""),
                    "date": str(query.get("date") or ""),
                },
            )
        )

    return candidates


def _build_transfer_seed_candidates(query: Dict[str, Any], raw_payload: Any) -> List[Dict[str, Any]]:
    payload = _extract_payload(raw_payload)
    if not isinstance(payload, dict) or "error" in payload:
        return []

    data = payload.get("data", {})
    if not isinstance(data, dict):
        return []

    middle_list = data.get("middleList", [])
    if not middle_list:
        return []

    rows = []
    for index, plan in enumerate(middle_list, start=1):
        full_list = plan.get("fullList", [])
        if len(full_list) < 2:
            continue

        seg1, seg2 = full_list[0], full_list[1]
        rows.append(
            {
                "plan_index": index,
                "via_station": seg1.get("to_station_name", "?"),
                "train_key": f"{seg1.get('station_train_code', '?')}+{seg2.get('station_train_code', '?')}",
                "total_text": str(plan.get("all_lishi") or "?"),
                "total_min": _duration_text_to_minutes(plan.get("all_lishi")),
                "wait_text": str(plan.get("wait_time") or "?"),
                "wait_min": _duration_text_to_minutes(plan.get("wait_time")),
            }
        )

    rows.sort(
        key=lambda item: (
            item["total_min"] if item["total_min"] is not None else 10**9,
            item["wait_min"] if item["wait_min"] is not None else 10**9,
            item["train_key"],
        )
    )

    candidates = []
    for index, row in enumerate(rows[:4]):
        score = 94 - index * 7
        wait_penalty = min((row["wait_min"] or 0) // 30, 8)
        score -= wait_penalty

        candidates.append(
            _make_seed_candidate(
                candidate_key=f"transfer:{row['plan_index']}:{row['train_key']}",
                candidate_type="transfer",
                label=f"transfer via {row['via_station']}",
                score=max(38, score),
                why="Deterministic transfer fast pre-rank.",
                supporting_points=[
                    f"route={query.get('id', '?')}",
                    f"via={row['via_station']}",
                    f"total={row['total_text']}",
                    f"wait={row['wait_text']}",
                ],
                attributes={
                    "route": str(query.get("id") or ""),
                    "date": str(query.get("date") or ""),
                    "station": str(row["via_station"]),
                },
            )
        )

    return candidates


def _build_path_seed_candidates(query: Dict[str, Any], raw_payload: Any) -> List[Dict[str, Any]]:
    payload = raw_payload if isinstance(raw_payload, dict) else {}
    timetable = payload.get("timetable", [])
    if not isinstance(timetable, list) or not timetable:
        return []

    start_stop = timetable[0]
    end_stop = timetable[-1]
    stop_count = len(timetable)

    return [
        _make_seed_candidate(
            candidate_key=f"train:{query.get('id', '?')}",
            candidate_type="route",
            label=str(query.get("id") or "?"),
            score=82,
            why="Deterministic path summary.",
            supporting_points=[
                f"start={_station_label(start_stop)} {start_stop.get('depart', '--')}",
                f"end={_station_label(end_stop)} {end_stop.get('arrive', '--')}",
                f"stop_count={stop_count}",
                f"model={payload.get('car', '?')}",
            ],
            attributes={
                "train_no": str(query.get("id") or ""),
                "date": str(query.get("date") or ""),
            },
        )
    ]


def _build_stopcheck_seed_candidates(query: Dict[str, Any], raw_payload: Any) -> List[Dict[str, Any]]:
    scan = []
    if isinstance(raw_payload, dict):
        if isinstance(raw_payload.get("scan"), list):
            scan = raw_payload.get("scan", [])
        elif isinstance(raw_payload.get("payload"), dict):
            scan = raw_payload["payload"].get("scan", [])

    if not scan:
        return []

    candidates = []
    fallback_candidates = []

    for item in scan:
        train_no = str(item.get("train") or "?")
        checks = item.get("checks", [])
        if not isinstance(checks, list):
            checks = []

        skip_stations = [
            str(check.get("station") or "?")
            for check in checks
            if str(check.get("result") or "").upper() == "SKIP"
        ]
        stop_stations = [
            str(check.get("station") or "?")
            for check in checks
            if str(check.get("result") or "").upper() == "STOP"
        ]

        if skip_stations:
            candidates.append(
                _make_seed_candidate(
                    candidate_key=f"train:{train_no}",
                    candidate_type="train",
                    label=train_no,
                    score=95,
                    why="Deterministic stopcheck skip hit.",
                    supporting_points=[
                        f"skip_stations={', '.join(skip_stations[:3])}",
                        f"query={query.get('id', '?')}",
                    ],
                    attributes={
                        "train_no": train_no,
                        "date": str(query.get("date") or ""),
                        "station": skip_stations[0],
                    },
                )
            )
            continue

        fallback_candidates.append(
            _make_seed_candidate(
                candidate_key=f"train:{train_no}",
                candidate_type="train",
                label=train_no,
                score=68,
                why="Deterministic stopcheck non-skip summary.",
                supporting_points=[
                    f"stop_stations={', '.join(stop_stations[:3]) if stop_stations else 'none'}",
                    f"query={query.get('id', '?')}",
                ],
                attributes={
                    "train_no": train_no,
                    "date": str(query.get("date") or ""),
                },
            )
        )

    if candidates:
        return candidates[:6]

    return fallback_candidates[:3]


def _build_train_history_views(query: Dict[str, Any]) -> List[Dict[str, Any]]:
    records = query.get("records") or []
    if not isinstance(records, list) or not records:
        return []

    latest = get_latest_record(records)
    summary = summarize_train_history(records)

    top_emus = ", ".join(
        f"{emu}:{count}" for emu, count in summary.get("most_common_emus", [])[:5]
    ) or "none"

    overview_lines = [
        f"LATEST_EMU: {latest.get('emu_no', '?') if latest else '?'}",
        f"LATEST_RECORD_TIME: {latest.get('date', '?') if latest else '?'}",
        f"TOTAL_RECORDS: {summary.get('total_records', 0)}",
        f"UNIQUE_EMU_COUNT: {summary.get('unique_emu_count', 0)}",
        f"TOP_EMUS: {top_emus}",
    ]

    views = [
        _make_view(
            query,
            view_id="overview",
            view_type="train_history_overview",
            priority=120,
            lines=overview_lines,
        )
    ]

    for batch_index, batch in enumerate(_chunked(records, HISTORY_BATCH_SIZE), start=1):
        lines = []
        for item in batch:
            lines.append(
                f"- {item.get('date', '?')} | train={item.get('train_no', query.get('id', '?'))} | emu={item.get('emu_no', '?')}"
            )

        views.append(
            _make_view(
                query,
                view_id=f"recent_batch_{batch_index:02d}",
                view_type="train_history_batch",
                priority=max(80, 100 - batch_index),
                lines=lines,
            )
        )

    return views


def _build_emu_history_views(query: Dict[str, Any]) -> List[Dict[str, Any]]:
    records = query.get("records") or []
    if not isinstance(records, list) or not records:
        return []

    latest = get_latest_record(records)
    summary = summarize_emu_history(records)

    top_trains = ", ".join(
        f"{train}:{count}" for train, count in summary.get("most_common_trains", [])[:5]
    ) or "none"

    overview_lines = [
        f"LATEST_TRAIN: {latest.get('train_no', '?') if latest else '?'}",
        f"LATEST_RECORD_TIME: {latest.get('date', '?') if latest else '?'}",
        f"TOTAL_RECORDS: {summary.get('total_records', 0)}",
        f"UNIQUE_TRAIN_COUNT: {summary.get('unique_train_count', 0)}",
        f"TOP_TRAINS: {top_trains}",
    ]

    views = [
        _make_view(
            query,
            view_id="overview",
            view_type="emu_history_overview",
            priority=120,
            lines=overview_lines,
        )
    ]

    for batch_index, batch in enumerate(_chunked(records, HISTORY_BATCH_SIZE), start=1):
        lines = []
        for item in batch:
            lines.append(
                f"- {item.get('date', '?')} | emu={item.get('emu_no', query.get('id', '?'))} | train={item.get('train_no', '?')}"
            )

        views.append(
            _make_view(
                query,
                view_id=f"recent_batch_{batch_index:02d}",
                view_type="emu_history_batch",
                priority=max(80, 100 - batch_index),
                lines=lines,
            )
        )

    return views


def _build_smartemu_views(query: Dict[str, Any], raw_payload: Any) -> List[Dict[str, Any]]:
    results = _extract_smartemu_results(query, raw_payload)
    if not results:
        return _build_pretty_fallback_view(query, "smartemu_pretty_fallback")

    overview_lines = [
        f"TRAIN_COUNT: {len(results)}",
        f"QUERY_TRAINS: {query.get('id', '?')}",
    ]

    views = [
        _make_view(
            query,
            view_id="overview",
            view_type="smartemu_overview",
            priority=120,
            lines=overview_lines,
        )
    ]

    for batch_index, batch in enumerate(_chunked(results, HISTORY_BATCH_SIZE), start=1):
        lines = []
        for item in batch:
            train_no = str(item.get("train") or "?").strip().upper()
            inference = item.get("inference") or {}
            candidate_family = str(inference.get("candidate") or "UNKNOWN").strip().upper()
            confidence = str(inference.get("confidence") or "LOW").strip().upper()
            recent5 = [
                str(family).strip().upper()
                for family in (inference.get("recent5") or [])
                if str(family).strip()
            ]
            recent_text = " -> ".join(recent5[:5]) or "none"
            lines.append(
                f"- {train_no} | candidate={candidate_family} | confidence={confidence} | recent5={recent_text}"
            )

        views.append(
            _make_view(
                query,
                view_id=f"smartemu_batch_{batch_index:02d}",
                view_type="smartemu_batch",
                priority=max(80, 100 - batch_index),
                lines=lines,
            )
        )

    return views


def _build_s2s_views(query: Dict[str, Any], raw_payload: Any) -> List[Dict[str, Any]]:
    all_trains = _extract_s2s_trains(raw_payload)
    trains = _filter_s2s_trains_for_object(query, all_trains)
    status = raw_payload.get("status", {}) if isinstance(raw_payload, dict) else {}

    if not all_trains:
        return _build_pretty_fallback_view(query, "s2s_pretty_fallback")

    rows = []
    running_count = 0
    regular_count = 0
    temporary_count = 0

    for train in trains:
        duration_min = _minutes_between(
            train.get("fromDepart"),
            train.get("toArrive"),
            train.get("dayDiff"),
        )
        od_stops = _od_stop_count(train)
        run_flag = _run_flag(train.get("rundays"), query.get("date"))
        train_type = _train_type(train.get("rundays"))

        if run_flag == "running":
            running_count += 1
        if train_type == "regular":
            regular_count += 1
        elif train_type == "temporary":
            temporary_count += 1

        rows.append(
            {
                "train_no": str(train.get("number") or _first_train_code(train.get("numberFull")) or "?"),
                "dep": str(train.get("fromDepart") or "?"),
                "arr": str(train.get("toArrive") or "?"),
                "arr_tag": _arrival_tag(train.get("toArrive"), train.get("dayDiff")),
                "duration_min": duration_min,
                "duration_text": _format_minutes(duration_min),
                "od_stops": od_stops,
                "bureau": normalize_bureau(str(train.get("bureauName") or "未知")),
                "model": str(train.get("car") or "?"),
                "run_flag": run_flag,
                "train_type": train_type,
                "benchmark_score": train.get("_benchmark_score"),
                "benchmark_tier": str(train.get("_benchmark_tier") or ""),
            }
        )

    rows.sort(
        key=lambda item: (
            _run_rank(item["run_flag"]),
            item["duration_min"] if item["duration_min"] is not None else 10**9,
            item["od_stops"],
            item["train_no"],
        )
    )

    top_preview = ", ".join(row["train_no"] for row in rows[:3]) or "none"

    overview_lines = [
        f"ROUTE: {query.get('id', '?')}",
        f"TRAIN_COUNT: {len(rows)}",
        f"BENCHMARK_MODE: {'tool_scored' if str(query.get('object') or '').strip() == 's2s_benchmark' else 'general'}",
        f"RUNNING_ON_QUERY_DATE: {running_count}",
        f"REGULAR_COUNT: {regular_count}",
        f"TEMPORARY_COUNT: {temporary_count}",
        f"FASTEST_DURATION: {rows[0]['duration_text'] if rows else '?'}",
        f"TOP_CANDIDATES: {top_preview}",
        f"CERT_VALID: {status.get('cert_valid')}",
        f"NEED_REFRESH: {status.get('need_refresh')}",
    ]

    views = [
        _make_view(
            query,
            view_id="overview",
            view_type="s2s_overview",
            priority=120,
            lines=overview_lines,
        )
    ]

    for batch_index, batch in enumerate(_chunked(rows, S2S_BATCH_SIZE), start=1):
        lines = []
        for row in batch:
            lines.append(
                f"- {row['train_no']} | dep={row['dep']} | arr={row['arr_tag']} | dur={row['duration_text']} | od_stops={row['od_stops']} | benchmark_score={row['benchmark_score'] if row['benchmark_score'] is not None else '?'} | benchmark_tier={row['benchmark_tier'] or '?'} | bureau={row['bureau']} | model={row['model']} | run={row['run_flag']} | type={row['train_type']}"
            )

        views.append(
            _make_view(
                query,
                view_id=f"candidate_batch_{batch_index:02d}",
                view_type="s2s_candidate_batch",
                priority=max(70, 100 - batch_index),
                lines=lines,
            )
        )

    return views


def _build_left_ticket_views(query: Dict[str, Any], raw_payload: Any) -> List[Dict[str, Any]]:
    payload = _extract_payload(raw_payload)
    if not isinstance(payload, dict):
        return _build_pretty_fallback_view(query, "left_ticket_pretty_fallback")

    if "error" in payload:
        return [
            _make_view(
                query,
                view_id="error",
                view_type="left_ticket_error",
                priority=120,
                lines=[f"ERROR: {payload.get('error')}"],
            )
        ]

    data = payload.get("data", {})
    trains_raw = data.get("result", [])
    station_map = data.get("map", {})

    if not trains_raw:
        return _build_pretty_fallback_view(query, "left_ticket_pretty_fallback")

    rows = []
    available_count = 0

    for line in trains_raw:
        parsed = parse_train_line(line)
        seats = parsed.get("seats", {})
        available_summary = _seat_summary(seats, only_available=True)
        any_ticket = bool(available_summary)

        if any_ticket:
            available_count += 1

        rows.append(
            {
                "train_no": parsed.get("train", "?"),
                "from_name": station_map.get(parsed.get("from_code"), parsed.get("from_code")),
                "to_name": station_map.get(parsed.get("to_code"), parsed.get("to_code")),
                "dep": parsed.get("dep", "?"),
                "arr": parsed.get("arr", "?"),
                "dur": parsed.get("dur", "?"),
                "dur_min": _duration_text_to_minutes(parsed.get("dur")),
                "has_ticket": any_ticket,
                "ticket_flag": "available" if any_ticket else "sold_out",
                "seats_text": available_summary or _seat_summary(seats, only_available=False),
                "seat_score": _seat_score(seats),
            }
        )

    rows.sort(
        key=lambda item: (
            0 if item["has_ticket"] else 1,
            item["dur_min"] if item["dur_min"] is not None else 10**9,
            -item["seat_score"],
            item["train_no"],
        )
    )

    overview_lines = [
        f"ROUTE: {query.get('id', '?')}",
        f"TRAIN_COUNT: {len(rows)}",
        f"AVAILABLE_TRAIN_COUNT: {available_count}",
        f"BEST_AVAILABLE: {rows[0]['train_no'] if rows else 'none'}",
    ]

    views = [
        _make_view(
            query,
            view_id="overview",
            view_type="left_ticket_overview",
            priority=120,
            lines=overview_lines,
        )
    ]

    for batch_index, batch in enumerate(_chunked(rows, LEFT_TICKET_BATCH_SIZE), start=1):
        lines = []
        for row in batch:
            lines.append(
                f"- {row['train_no']} | {row['from_name']}->{row['to_name']} | {row['dep']}->{row['arr']} | dur={row['dur']} | ticket={row['ticket_flag']} | seats={row['seats_text']}"
            )

        views.append(
            _make_view(
                query,
                view_id=f"ticket_batch_{batch_index:02d}",
                view_type="left_ticket_batch",
                priority=max(70, 100 - batch_index),
                lines=lines,
            )
        )

    return views


def _build_transfer_views(query: Dict[str, Any], raw_payload: Any) -> List[Dict[str, Any]]:
    payload = _extract_payload(raw_payload)
    if not isinstance(payload, dict):
        return _build_pretty_fallback_view(query, "transfer_pretty_fallback")

    if "error" in payload:
        return [
            _make_view(
                query,
                view_id="error",
                view_type="transfer_error",
                priority=120,
                lines=[f"ERROR: {payload.get('error')}"],
            )
        ]

    data = payload.get("data", {})
    if not isinstance(data, dict):
        error_msg = str(payload.get("errorMsg") or "").strip() or "No transfer plans found."
        return [
            _make_view(
                query,
                view_id="info",
                view_type="transfer_info",
                priority=120,
                lines=[f"INFO: {error_msg}"],
            )
        ]

    middle_list = data.get("middleList", [])
    if not middle_list:
        error_msg = str(payload.get("errorMsg") or "").strip() or "No transfer plans found."
        return [
            _make_view(
                query,
                view_id="info",
                view_type="transfer_info",
                priority=120,
                lines=[f"INFO: {error_msg}"],
            )
        ]

    rows = []
    for index, plan in enumerate(middle_list, start=1):
        full_list = plan.get("fullList", [])
        if len(full_list) < 2:
            continue

        seg1, seg2 = full_list[0], full_list[1]
        via_station = seg1.get("to_station_name", "?")
        train1 = seg1.get("station_train_code", "?")
        train2 = seg2.get("station_train_code", "?")

        rows.append(
            {
                "plan_index": index,
                "via_station": via_station,
                "total_text": str(plan.get("all_lishi") or "?"),
                "total_min": _duration_text_to_minutes(plan.get("all_lishi")),
                "wait_text": str(plan.get("wait_time") or "?"),
                "wait_min": _duration_text_to_minutes(plan.get("wait_time")),
                "same_train": train1 == train2,
                "seg1": _format_transfer_segment(seg1),
                "seg2": _format_transfer_segment(seg2),
                "train_key": f"{train1}+{train2}",
            }
        )

    rows.sort(
        key=lambda item: (
            item["total_min"] if item["total_min"] is not None else 10**9,
            item["wait_min"] if item["wait_min"] is not None else 10**9,
            item["train_key"],
        )
    )

    overview_lines = [
        f"ROUTE: {query.get('id', '?')}",
        f"PLAN_COUNT: {len(rows)}",
        f"FASTEST_PLAN_TOTAL: {rows[0]['total_text'] if rows else '?'}",
        f"SHORTEST_WAIT: {min((row['wait_min'] for row in rows if row['wait_min'] is not None), default='?')}",
    ]

    views = [
        _make_view(
            query,
            view_id="overview",
            view_type="transfer_overview",
            priority=120,
            lines=overview_lines,
        )
    ]

    for batch_index, batch in enumerate(_chunked(rows, TRANSFER_BATCH_SIZE), start=1):
        lines = []
        for row in batch:
            lines.append(
                f"- plan#{row['plan_index']} | via={row['via_station']} | total={row['total_text']} | wait={row['wait_text']} | same_train={row['same_train']}"
            )
            lines.append(f"  seg1={row['seg1']}")
            lines.append(f"  seg2={row['seg2']}")

        views.append(
            _make_view(
                query,
                view_id=f"transfer_batch_{batch_index:02d}",
                view_type="transfer_plan_batch",
                priority=max(70, 100 - batch_index),
                lines=lines,
            )
        )

    return views


def _build_path_views(query: Dict[str, Any], raw_payload: Any) -> List[Dict[str, Any]]:
    payload = raw_payload if isinstance(raw_payload, dict) else {}
    timetable = payload.get("timetable", [])

    if not timetable:
        return _build_pretty_fallback_view(query, "path_pretty_fallback")

    train_codes = payload.get("numberFull", [])
    if isinstance(train_codes, str):
        train_codes = [train_codes]

    status = payload.get("_status", {})
    start_stop = timetable[0]
    end_stop = timetable[-1]
    run_days = payload.get("rundays", [])
    long_dwells = []

    for stop in timetable:
        stop_time = _safe_int(stop.get("stopTime"))
        if stop_time is None or stop_time < 10:
            continue
        long_dwells.append(f"{_station_label(stop)}:{stop_time}m")
        if len(long_dwells) >= 4:
            break

    overview_lines = [
        f"TRAIN_CODES: {', '.join(str(x) for x in train_codes) if train_codes else query.get('id', '?')}",
        f"STOP_COUNT: {len(timetable)}",
        f"BUREAU: {payload.get('bureauName', '?')}",
        f"MODEL: {payload.get('car', '?')}",
        f"RUNNING_ON_QUERY_DATE: {status.get('running_today')}",
        f"CERT_VALID: {status.get('cert_valid')}",
        f"NEED_REFRESH: {status.get('need_refresh')}",
        f"RUN_PATTERN: {_train_type(run_days)}",
        f"START: {_station_label(start_stop)} dep={start_stop.get('depart', '--')}",
        f"END: {_station_label(end_stop)} arr={end_stop.get('arrive', '--')}",
        f"LONG_DWELLS: {', '.join(long_dwells) if long_dwells else 'none'}",
    ]

    views = [
        _make_view(
            query,
            view_id="overview",
            view_type="path_overview",
            priority=120,
            lines=overview_lines,
        )
    ]

    for batch_index, batch in enumerate(_chunked(timetable, PATH_BATCH_SIZE), start=1):
        lines = []
        base_idx = (batch_index - 1) * PATH_BATCH_SIZE
        for offset, stop in enumerate(batch, start=1):
            lines.append(
                f"- #{base_idx + offset:02d} | station={_station_label(stop)} | arr={stop.get('arrive', '--')} | dep={stop.get('depart', '--')} | stop={stop.get('stopTime', 0)}m | dist={stop.get('distance', '?')}"
            )

        views.append(
            _make_view(
                query,
                view_id=f"stops_batch_{batch_index:02d}",
                view_type="path_stop_batch",
                priority=max(75, 105 - batch_index),
                lines=lines,
            )
        )

    return views


def _build_stopcheck_views(query: Dict[str, Any], raw_payload: Any) -> List[Dict[str, Any]]:
    scan = []

    if isinstance(raw_payload, dict):
        if isinstance(raw_payload.get("scan"), list):
            scan = raw_payload.get("scan", [])
        elif isinstance(raw_payload.get("payload"), dict):
            scan = raw_payload["payload"].get("scan", [])

    if not scan:
        return _build_pretty_fallback_view(query, "stopcheck_pretty_fallback")

    skip_count = 0
    pass_count = 0
    stop_count = 0

    for item in scan:
        for check in item.get("checks", []):
            result = str(check.get("result") or "").upper()
            if result == "SKIP":
                skip_count += 1
            elif result == "PASS":
                pass_count += 1
            elif result == "STOP":
                stop_count += 1

    views = [
        _make_view(
            query,
            view_id="overview",
            view_type="stopcheck_overview",
            priority=120,
            lines=[
                f"QUERY_ID: {query.get('id', '?')}",
                f"TRAIN_COUNT: {len(scan)}",
                f"SKIP_RESULT_COUNT: {skip_count}",
                f"PASS_RESULT_COUNT: {pass_count}",
                f"STOP_RESULT_COUNT: {stop_count}",
            ],
        )
    ]

    for batch_index, batch in enumerate(_chunked(scan, STOPCHECK_BATCH_SIZE), start=1):
        lines = []
        for item in batch:
            train_no = item.get("train", "?")
            if item.get("status"):
                lines.append(f"- {train_no} | status={item.get('status')}")
                continue

            check_parts = []
            for check in item.get("checks", []):
                result = str(check.get("result") or "").upper()
                station = check.get("station", "?")
                if result == "STOP":
                    check_parts.append(
                        f"{station}=STOP({check.get('stopTime', 0)}m {check.get('arrive', '--')}/{check.get('depart', '--')})"
                    )
                elif result == "PASS":
                    check_parts.append(
                        f"{station}=PASS({check.get('arrive', '--')}/{check.get('depart', '--')})"
                    )
                else:
                    check_parts.append(f"{station}={result or 'UNKNOWN'}")

            lines.append(f"- {train_no} | {'; '.join(check_parts)}")

        views.append(
            _make_view(
                query,
                view_id=f"scan_batch_{batch_index:02d}",
                view_type="stopcheck_scan_batch",
                priority=max(75, 105 - batch_index),
                lines=lines,
            )
        )

    return views


def _build_pretty_fallback_view(query: Dict[str, Any], view_type: str) -> List[Dict[str, Any]]:
    pretty = str(query.get("pretty") or "").strip()
    if not pretty:
        return []

    return [
        _make_view(
            query,
            view_id="pretty",
            view_type=view_type,
            priority=60,
            lines=[pretty],
        )
    ]


def _extract_payload(raw_payload: Any) -> Any:
    if not isinstance(raw_payload, dict):
        return raw_payload

    if "payload" in raw_payload:
        return raw_payload.get("payload")

    return raw_payload


def _extract_s2s_trains(raw_payload: Any) -> List[Dict[str, Any]]:
    payload = _extract_payload(raw_payload)

    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]

    if isinstance(payload, dict):
        trains = payload.get("trains")
        if isinstance(trains, list):
            return [item for item in trains if isinstance(item, dict)]

        nested = payload.get("payload")
        if isinstance(nested, dict) and isinstance(nested.get("trains"), list):
            return [item for item in nested["trains"] if isinstance(item, dict)]

    return []


def _chunked(items: Iterable[Any], size: int) -> List[List[Any]]:
    bucket: List[Any] = []
    result: List[List[Any]] = []

    for item in items:
        bucket.append(item)
        if len(bucket) >= size:
            result.append(bucket)
            bucket = []

    if bucket:
        result.append(bucket)

    return result


def _make_view(
    query: Dict[str, Any],
    view_id: str,
    view_type: str,
    priority: int,
    lines: List[str],
) -> Dict[str, Any]:
    header = [
        f"OBJECT: {query.get('object', '?')}",
        f"QUERY_ID: {query.get('id', '?')}",
    ]

    if query.get("date"):
        header.append(f"QUERY_DATE: {query.get('date')}")

    header.append(f"VIEW_TYPE: {view_type}")
    header.append(f"VIEW_ID: {view_id}")

    text = "\n".join(header + list(lines))

    return {
        "view_id": view_id,
        "view_type": view_type,
        "priority": priority,
        "text": text,
    }


def _first_train_code(value: Any) -> str | None:
    if isinstance(value, list) and value:
        for item in value:
            text = str(item).strip()
            if text:
                return text
        return None

    text = str(value).strip()
    return text or None


def _safe_int(value: Any) -> int | None:
    try:
        return int(value)
    except Exception:
        return None


def _minutes_between(dep: Any, arr: Any, day_diff: Any) -> int | None:
    dep_minutes = _hhmm_to_minutes(dep)
    arr_minutes = _hhmm_to_minutes(arr)

    if dep_minutes is None or arr_minutes is None:
        return None

    diff_days = _safe_int(day_diff) or 0
    return arr_minutes + diff_days * 1440 - dep_minutes


def _hhmm_to_minutes(value: Any) -> int | None:
    text = str(value or "").strip()
    if ":" not in text:
        return None

    try:
        hour_text, minute_text = text.split(":", 1)
        return int(hour_text) * 60 + int(minute_text)
    except Exception:
        return None


def _duration_text_to_minutes(value: Any) -> int | None:
    text = str(value or "").strip()
    if ":" not in text:
        return None

    parts = text.split(":")
    if len(parts) == 2:
        return _hhmm_to_minutes(text)

    try:
        return int(parts[-3]) * 1440 + int(parts[-2]) * 60 + int(parts[-1])
    except Exception:
        return None


def _format_minutes(value: int | None) -> str:
    if value is None or value < 0:
        return "?"
    return f"{value // 60}h{value % 60:02d}m"


def _arrival_tag(arrive: Any, day_diff: Any) -> str:
    arr_text = str(arrive or "?")
    diff_days = _safe_int(day_diff) or 0
    if diff_days <= 0:
        return arr_text
    return f"{arr_text}(+{diff_days})"


def _od_stop_count(train: Dict[str, Any]) -> int:
    timetable = train.get("timetable", [])
    if not isinstance(timetable, list) or not timetable:
        return 0

    from_pos = _safe_int(train.get("fromPos"))
    to_pos = _safe_int(train.get("toPos"))

    if from_pos is None or to_pos is None:
        return max(len(timetable) - 2, 0)

    from_pos = max(0, min(from_pos, len(timetable) - 1))
    to_pos = max(from_pos, min(to_pos, len(timetable) - 1))
    return max(to_pos - from_pos - 1, 0)


def _filter_s2s_trains_for_object(query: Dict[str, Any], trains: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    obj = str(query.get("object") or "").strip()
    if obj != "s2s_benchmark":
        return list(trains)

    rated = [
        train
        for train in trains
        if str(train.get("_benchmark_tier") or "").strip()
        and str(train.get("_benchmark_running") or "").strip() == "✅开行"
    ]
    # Direct unit callers may provide unformatted raw payloads.  Production
    # execution annotates ratings in pretty_format_s2s_benchmark first.
    return rated if rated else list(trains)


def _run_flag(rundays: Any, query_date: Any) -> str:
    if not isinstance(rundays, list) or not rundays:
        return "unknown"

    query_text = str(query_date or "").replace("-", "")
    if query_text and query_text in {str(day).replace("-", "") for day in rundays}:
        return "running"

    return "not_running"


def _run_rank(value: str) -> int:
    if value == "running":
        return 0
    if value == "unknown":
        return 1
    return 2


def _train_type(rundays: Any) -> str:
    if not isinstance(rundays, list) or not rundays:
        return "unknown"
    if len(rundays) >= REGULAR_RUN_THRESHOLD:
        return "regular"
    return "temporary"


def _seat_score(seats: Dict[str, Any]) -> int:
    score = 0
    for seat_name in SEAT_DISPLAY_ORDER:
        seat_value = seats.get(seat_name)
        normalized = _seat_numeric_value(seat_value)
        if normalized is not None and normalized > 0:
            score += normalized
    return score


def _seat_numeric_value(value: Any) -> int | None:
    text = str(value or "").strip()
    if text in {"", "--", "—"}:
        return None
    if text in {"无", "无票", "0"}:
        return 0
    if text == "有票":
        return 20

    try:
        return int(text)
    except Exception:
        return 0


def _seat_summary(seats: Dict[str, Any], only_available: bool) -> str:
    parts = []

    for seat_name in SEAT_DISPLAY_ORDER:
        value = seats.get(seat_name)
        normalized_value = _seat_numeric_value(value)

        if normalized_value is None:
            continue

        if only_available and normalized_value <= 0:
            continue

        if not only_available and normalized_value > 0:
            continue

        parts.append(f"{seat_name}:{value}")
        if len(parts) >= 4:
            break

    if parts:
        return "; ".join(parts)

    if only_available:
        return ""

    fallback_parts = []
    for seat_name in SEAT_DISPLAY_ORDER:
        value = seats.get(seat_name)
        if value in {"", "--", "—", None}:
            continue
        fallback_parts.append(f"{seat_name}:{value}")
        if len(fallback_parts) >= 3:
            break
    return "; ".join(fallback_parts) or "none"


def _format_transfer_segment(segment: Dict[str, Any]) -> str:
    seat_parts = []
    for seat_label, key in (("swz", "swz_num"), ("zy", "zy_num"), ("ze", "ze_num")):
        value = segment.get(key)
        if value in {"", None, "--"}:
            continue
        seat_parts.append(f"{seat_label}:{value}")

    seat_text = ";".join(seat_parts) if seat_parts else "seat:unknown"

    return (
        f"{segment.get('station_train_code', '?')} "
        f"{segment.get('from_station_name', '?')} {segment.get('start_time', '?')} -> "
        f"{segment.get('to_station_name', '?')} {segment.get('arrive_time', '?')} "
        f"(dur={segment.get('lishi', '?')}; {seat_text})"
    )


def _make_seed_candidate(
    candidate_key: str,
    candidate_type: str,
    label: str,
    score: int,
    why: str,
    supporting_points: List[str],
    attributes: Dict[str, Any],
) -> Dict[str, Any]:
    cleaned_points = []
    for item in supporting_points:
        text = str(item).strip()
        if text and text not in cleaned_points:
            cleaned_points.append(text)
        if len(cleaned_points) >= 6:
            break

    cleaned_attributes = {}
    for key, value in attributes.items():
        text = str(value).strip()
        if text:
            cleaned_attributes[key] = text

    return {
        "candidate_key": str(candidate_key).strip(),
        "candidate_type": str(candidate_type).strip() or "unknown",
        "label": str(label).strip() or str(candidate_key).strip(),
        "score": max(0, min(int(score), 100)),
        "why": str(why).strip(),
        "supporting_points": cleaned_points,
        "attributes": cleaned_attributes,
    }


def _station_label(stop: Dict[str, Any]) -> str:
    station = str(stop.get("station") or "").strip()
    telecode = str(stop.get("stationTelecode") or "").strip()

    if station:
        return station

    if telecode:
        resolved = station_dict.name_of(telecode)
        if resolved:
            return resolved

    return telecode or "?"
