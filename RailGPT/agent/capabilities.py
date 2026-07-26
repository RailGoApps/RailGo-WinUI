from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass, field, replace
from typing import Any, Dict, Iterable, Mapping, Sequence


REGISTRY_VERSION = "2026-07-mcp-capability-manifest-v5"


SLOT_SCHEMAS: Dict[str, Dict[str, Any]] = {
    "train": {"type": "string", "description": "完整车次号，例如 G1"},
    "trains": {"type": "array", "items": {"type": "string"}, "description": "明确车次号列表"},
    "emu": {"type": "string", "description": "完整动车组编号，例如 CR400AFZ2333"},
    "dep": {"type": "string", "description": "出发城市或车站"},
    "arr": {"type": "string", "description": "到达城市或车站"},
    "station": {"type": "string", "description": "明确车站名"},
    "stations": {"type": "array", "items": {"type": "string"}, "description": "明确车站名列表"},
    "date": {"type": "string", "format": "date", "description": "YYYY-MM-DD"},
    "telecode": {"type": "string", "pattern": "^[A-Za-z]{3}$", "description": "三字母电报码"},
    "keyword": {"type": "string", "description": "用户给出的模糊检索词"},
    "bureau": {"type": "string", "description": "铁路局/集团简称"},
    "hub": {"type": "string", "description": "用户明确指定的中转车站"},
    "direction": {
        "type": "string",
        "enum": ["arrival", "departure"],
        "description": "到达（arrival）或出发（departure）业务方向",
    },
    "timeband": {"type": "string", "description": "用户表达的出发或到达时间范围"},
    "model": {"type": "string", "description": "用户明确表达的动车组车型偏好，例如 AFZ"},
}


SLOT_CLARIFICATION_GUIDANCE: Dict[str, str] = {
    "train": "请用户提供完整车次号，例如 G1；不要用线路、车型或目的地猜车次。",
    "trains": "请用户提供需要比较或分析的完整车次号列表。",
    "emu": "请用户提供完整动车组编号，例如 CR400AFZ2333。",
    "dep": "请用户明确出发城市或出发站。",
    "arr": "请用户明确到达城市或到达站。",
    "station": "请用户明确一个具体车站名，不要用城市名擅自猜唯一车站。",
    "stations": "请用户明确需要核验的一个或多个车站名。",
    "date": "请用户明确查询日期；可以接受自然语言日期，由 Date Normalizer 解析。",
    "telecode": "请用户提供需要反查的三字母车站电报码。",
    "keyword": "请用户给出希望模糊搜索的站名或车次关键词。",
    "bureau": "请用户明确希望筛选的铁路局或集团。",
    "hub": "请用户明确希望指定的中转站或中转城市。",
    "direction": "请用户明确要查到达（arrival）还是出发（departure）；不得静默默认。",
    "timeband": "请用户明确希望筛选的时间范围。",
    "model": "请用户明确希望筛选的动车组车型或家族。",
}


SLOT_FALLBACK_QUESTIONS: Dict[str, str] = {
    "train": "请告诉我完整车次号，例如 G1。",
    "trains": "请告诉我需要分析的具体车次号，可以一次给多个。",
    "emu": "请告诉我完整动车组编号，例如 CR400AFZ2333。",
    "dep": "请告诉我从哪里出发。",
    "arr": "请告诉我要到哪里。",
    "station": "请告诉我要查询的具体车站。",
    "stations": "请告诉我要核验的具体车站。",
    "date": "请告诉我想查询哪一天。",
    "telecode": "请告诉我要反查的三字母车站电报码。",
    "keyword": "请告诉我希望搜索的关键词。",
    "bureau": "请告诉我希望筛选哪个铁路局或集团。",
    "hub": "请告诉我希望经由哪个中转站或城市。",
    "direction": "请告诉我是查到达信息还是出发信息。",
    "timeband": "请告诉我希望筛选的时间范围。",
    "model": "请告诉我希望筛选的动车组车型。",
}


@dataclass(frozen=True)
class ToolCapability:
    object: str
    intent_family: str
    required_slots: tuple[str, ...]
    optional_slots: tuple[str, ...]
    temporal_scope: str
    required_evidence: tuple[str, ...]
    description_l0: str
    description_l1: str
    providers: tuple[str, ...] = ()
    workflow: tuple[str, ...] = ()
    execution_strategy: str = "single"
    kind: str = "tool"
    id_format: str = ""
    choose_when: tuple[str, ...] = ()
    avoid_when: tuple[str, ...] = ()
    examples: tuple[str, ...] = ()
    cost_tier: str = "normal"
    city_od_policy: str = "none"
    max_fanout: int = 1
    workflow_inputs: tuple[str, ...] = ()
    workflow_conditions: tuple[str, ...] = ()
    availability: str = "available"
    unavailable_reason: str = ""
    slot_guidance: tuple[tuple[str, str], ...] = ()
    slot_defaults: tuple[tuple[str, str], ...] = ()

    @property
    def is_available(self) -> bool:
        return self.availability == "available"

    @property
    def is_executable(self) -> bool:
        return self.kind == "tool" and self.is_available

    def input_schema(self) -> Dict[str, Any]:
        properties = {
            slot: dict(SLOT_SCHEMAS.get(slot, {"type": "string"}))
            for slot in (*self.required_slots, *self.optional_slots)
        }
        for slot, default in self.slot_defaults:
            if slot in properties:
                properties[slot]["default"] = default
        return {
            "type": "object",
            "properties": properties,
            "required": list(self.required_slots),
            "additionalProperties": False,
        }

    def clarification_guidance(self, slot: str) -> str:
        overrides = dict(self.slot_guidance)
        return str(overrides.get(slot) or SLOT_CLARIFICATION_GUIDANCE.get(slot) or "请用户补充该查询必需的信息。")

    def default_for(self, slot: str) -> str:
        return str(dict(self.slot_defaults).get(slot) or "")

    def missing_slot_contract(
        self,
        missing_slots: Iterable[str],
        known_slots: Mapping[str, Any] | None = None,
    ) -> Dict[str, Any]:
        missing = [slot for slot in self.required_slots if slot in set(missing_slots)]
        known = {
            slot: value
            for slot, value in dict(known_slots or {}).items()
            if slot in (*self.required_slots, *self.optional_slots)
            and slot not in missing
            and value not in (None, "", [], {})
        }
        return {
            "capability": self.object,
            "intent_family": self.intent_family,
            "purpose": self.description_l1,
            "required_slots": list(self.required_slots),
            "optional_slots": list(self.optional_slots),
            "defaults": dict(self.slot_defaults),
            "missing_slots": missing,
            "known_slots": known,
            "questions": [
                {
                    "slot": slot,
                    "description": str(SLOT_SCHEMAS.get(slot, {}).get("description") or slot),
                    "guidance": self.clarification_guidance(slot),
                    "fallback_question": str(SLOT_FALLBACK_QUESTIONS.get(slot) or "请补充这项查询所需的信息。"),
                }
                for slot in missing
            ],
        }
    def mcp_manifest(self, level: str = "l1") -> Dict[str, Any]:
        description = self.description_l1 if level == "l1" else self.description_l0
        manifest: Dict[str, Any] = {
            "name": self.object,
            "description": description,
            "inputSchema": self.input_schema(),
            "annotations": {
                "kind": self.kind,
                "intent_family": self.intent_family,
                "temporal_scope": self.temporal_scope,
                "required_evidence": list(self.required_evidence),
                "cost_tier": self.cost_tier,
                "city_od_policy": self.city_od_policy,
                "availability": self.availability,
                "clarification_policy": self.missing_slot_contract(self.required_slots),
            },
        }
        if self.kind == "workflow" or len(self.workflow) > 1 or tuple(self.workflow) != (self.object,):
            manifest["workflow"] = [
                {
                    "capability": name,
                    "input": self.workflow_inputs[index] if index < len(self.workflow_inputs) else "query_id",
                    "condition": self.workflow_conditions[index] if index < len(self.workflow_conditions) else "always",
                }
                for index, name in enumerate(self.workflow)
            ]
            manifest["executionStrategy"] = self.execution_strategy
        if level == "l1":
            manifest["annotations"].update(
                {
                    "id_format": self.id_format,
                    "choose_when": list(self.choose_when),
                    "avoid_when": list(self.avoid_when),
                    "examples": list(self.examples),
                    "providers": list(self.providers),
                    "max_fanout": self.max_fanout,
                }
            )
        return manifest

    def prompt_line(self, level: str = "l0") -> str:
        description = self.description_l1 if level == "l1" else self.description_l0
        required = ",".join(self.required_slots) or "none"
        optional = ",".join(self.optional_slots) or "none"
        evidence = ",".join(self.required_evidence) or "none"
        workflow = ",".join(self.workflow) or self.object
        choose = " / ".join(self.choose_when)
        avoid = " / ".join(self.avoid_when)
        if level == "l0":
            return (
                f"- {self.object} [{self.kind}]: {description}; input={self.id_format}; "
                f"required=[{required}]; use={choose}; avoid={avoid}; workflow=[{workflow}]; "
                f"city_od={self.city_od_policy}; cost={self.cost_tier}; defaults={json.dumps(dict(self.slot_defaults), ensure_ascii=False)}; "
                "missing_required_slots=ask_only_those_slots"
            )
        examples = " | ".join(self.examples) or "none"
        return (
            f"- {self.object} [{self.kind}]\n"
            f"  purpose: {description}\n"
            f"  input: {self.id_format}; required=[{required}]; optional=[{optional}]; time={self.temporal_scope}\n"
            f"  choose_when: {choose}\n"
            f"  avoid_when: {avoid}\n"
            f"  evidence=[{evidence}]; workflow=[{workflow}]; execution={self.execution_strategy}; "
            f"cost={self.cost_tier}; city_od={self.city_od_policy}; examples={examples}\n"
            f"  clarification: {json.dumps(self.missing_slot_contract(self.required_slots), ensure_ascii=False)}"
        )


def _capability(
    object_name: str,
    intent_family: str,
    required_slots: Sequence[str],
    optional_slots: Sequence[str],
    temporal_scope: str,
    description_l0: str,
    description_l1: str,
    required_evidence: Sequence[str] | None = None,
    providers: Sequence[str] | None = None,
    workflow: Sequence[str] | None = None,
    execution_strategy: str = "single",
) -> ToolCapability:
    return ToolCapability(
        object=object_name,
        intent_family=intent_family,
        required_slots=tuple(required_slots),
        optional_slots=tuple(optional_slots),
        temporal_scope=temporal_scope,
        required_evidence=tuple(required_evidence or (object_name,)),
        description_l0=description_l0,
        description_l1=description_l1,
        providers=tuple(providers or ()),
        workflow=tuple(workflow or (object_name,)),
        execution_strategy=str(execution_strategy or "single"),
    )


_BASE_CAPABILITIES: Dict[str, ToolCapability] = {
    item.object: item
    for item in (
        _capability("train", "train_assignment", ("train",), (), "recent_history", "Recent EMU assignment by train.", "Use rail.re assignment history for a concrete train number."),
        _capability(
            "train_overview",
            "train_overview",
            ("train",),
            ("date",),
            "timetable_and_recent_history",
            "Combined train profile: route plus recent EMU assignment.",
            "Use when the user asks about one train's features, highlights, overall profile, introduction, or what is special about it. Run path_detail and train together; never ask for OD.",
            required_evidence=("path_detail", "train"),
            workflow=("path_detail", "train"),
            execution_strategy="parallel",
        ),
        _capability("emu", "emu_rotation", ("emu",), (), "recent_history", "Recent duties by EMU set.", "Use rail.re history for one concrete EMU set number."),
        _capability("smartemu_analysis", "multi_train_assignment", ("trains",), (), "recent_history", "Multi-train intelligent EMU analysis.", "Analyze assignment history for several explicit or context-resolved trains."),
        _capability(
            "route_smartemu_search",
            "route_smartemu_search",
            ("dep", "arr"),
            ("date", "model"),
            "dated_timetable_and_recent_history",
            "Find OD trains, then analyze their intelligent EMU usage.",
            "Use when the user asks which trains on an explicit route use a requested EMU family or intelligent EMU. First discover real OD trains, then analyze only those returned train numbers.",
            required_evidence=("station_to_station_mini", "smartemu_analysis"),
            workflow=("station_to_station_mini", "smartemu_analysis"),
            execution_strategy="sequential",
        ),
        _capability("path_detail", "train_path", ("train",), ("date",), "dated_timetable", "Train timetable and stop path.", "Use for origin, destination, stops, scheduled times and line membership.", providers=("local_cache", "railgo_v2_train_main", "railgo_v1_train")),
        _capability("path_future", "train_path", ("train", "date"), (), "future_timetable", "Future train timetable path.", "Use only for an explicitly future timetable date."),
        _capability("path_past", "train_path", ("train", "date"), (), "past_timetable", "Historical train timetable path.", "Use only for an explicitly historical timetable date."),
        _capability("path_stopcheck", "train_stop_history", ("trains", "stations"), ("date",), "timetable_history", "Stop history and stop matrix.", "Use for whether/when trains stopped or began stopping at named stations."),
        _capability("station_to_station_mini", "route_listing", ("dep", "arr"), ("date",), "dated_timetable", "OD train listing.", "List trains for a departure-arrival pair.", providers=("local_cache", "railgo_v1_s2s")),
        _capability("station_to_station_detail", "route_listing", ("dep", "arr"), ("date",), "dated_timetable", "Detailed OD train listing.", "Use only when detailed OD records are explicitly needed."),
        _capability("station_to_station_future", "route_listing", ("dep", "arr", "date"), (), "future_timetable", "Future OD train listing.", "List future trains for an explicit date."),
        _capability("station_to_station_past", "route_listing", ("dep", "arr", "date"), (), "past_timetable", "Historical OD train listing.", "List historical trains for an explicit date."),
        _capability("s2s_benchmark", "route_benchmark", ("dep", "arr"), ("date",), "dated_timetable", "Fastest/benchmark OD trains.", "Rank benchmark or fastest trains for a route."),
        _capability(
            "route_train_benchmark",
            "route_train_benchmark",
            ("train", "dep", "arr"),
            ("date",),
            "dated_timetable_and_recent_history",
            "Verify one train's tool-rated benchmark status on one OD segment.",
            "Use when the user asks whether a specific train is a benchmark/fastest/flagship candidate for an explicit route. The s2s_benchmark rating is authoritative; path and assignment are supporting evidence. A train may qualify for the requested segment even when it continues beyond the segment destination.",
            required_evidence=("s2s_benchmark", "path_detail", "train"),
            workflow=("s2s_benchmark", "path_detail", "train"),
            execution_strategy="parallel",
        ),
        _capability("s2s_timeband_dep", "route_filter", ("dep", "arr"), ("date",), "dated_timetable", "OD departure time-band filter.", "Filter OD services by departure time band."),
        _capability("s2s_timeband_arr", "route_filter", ("dep", "arr"), ("date",), "dated_timetable", "OD arrival time-band filter.", "Filter OD services by arrival time band."),
        _capability("s2s_regular_only", "route_filter", ("dep", "arr"), ("date",), "dated_timetable", "Regular OD services only.", "Filter an OD route to regular services."),
        _capability("s2s_temporary_only", "route_filter", ("dep", "arr"), ("date",), "dated_timetable", "Temporary OD services only.", "Filter an OD route to temporary services."),
        _capability("s2s_bureau_filter", "route_filter", ("dep", "arr"), ("date", "bureau"), "dated_timetable", "OD bureau filter.", "Filter OD trains by bureau when requested."),
        _capability("left_ticket_s2s", "route_ticket", ("dep", "arr", "date"), ("train",), "live_ticket", "12306 live ticket inventory.", "This is the primary OD-constrained capability. It requires departure, arrival and date."),
        _capability("transfer_12306", "route_transfer", ("dep", "arr", "date"), ("hub",), "live_ticket", "12306 transfer options.", "Find transfer options for a complete OD and date."),
        _capability("telecode", "station_telecode", ("station",), (), "static", "Station name to telecode.", "Convert an explicit station name to telecode."),
        _capability("name", "station_reverse", ("telecode",), (), "static", "Telecode to station name.", "Reverse lookup an explicit station telecode."),
        _capability("station", "station_metadata", ("station",), (), "mostly_static", "Single-station metadata.", "Use only for explicit bureau/city/province/pinyin metadata about one station.", providers=("local_cache", "railgo_v1_station")),
        _capability("station_preselect", "station_discovery", ("keyword",), (), "mostly_static", "Fuzzy station-name suggestions.", "Use only when the user explicitly asks to search an ambiguous or partial station name.", providers=("railgo_v1_station_preselect",)),
        _capability("train_preselect", "train_discovery", ("keyword",), (), "mostly_static", "Fuzzy train-number suggestions.", "Use only when the user explicitly asks to search an incomplete or ambiguous train number.", providers=("railgo_v1_train_preselect",)),
        _capability("random_train", "train_discovery", (), (), "current_catalog", "Random train discovery.", "Use only when the user explicitly asks RailGPT to pick or recommend a random train.", providers=("railgo_v1_random_train",)),
        _capability("train_delay", "live_delay", ("train",), ("dep", "arr", "station"), "current_only", "Current delay/punctuality by station.", "RailGo v2 live delay needs only a train number. OD is optional local display scope, never a required API slot.", providers=("railgo_v2_delay",)),
        _capability("train_station_access", "live_station_access", ("train", "station"), ("date", "direction"), "published_current", "Platform/check gate/exit for one train at one station.", "Use an exact train and station. Date defaults to today and direction defaults to departure unless the user explicitly overrides either one.", providers=("railgo_v2_access",)),
        _capability("station_board", "live_station_board", ("station",), ("direction",), "current_only", "Current station arrival/departure board.", "Use an explicit station. Direction defaults to departure unless the user explicitly asks for arrivals.", providers=("railgo_v2_station_board",)),
        _capability("coach_layout", "coach_layout", ("train",), (), "published_current", "Published coach composition and capacity.", "Use for coach layout/capacity, never for exact recent EMU assignment.", providers=("railgo_v2_coach",)),
        _capability("train_route_map", "train_route_map", ("train",), (), "published_current", "GCJ-02 route map coordinates.", "Use for map coordinates only, never timetable or stop evidence.", providers=("railgo_v2_map",)),
    )
}


def _contract(
    id_format: str,
    choose_when: Sequence[str],
    avoid_when: Sequence[str],
    *,
    examples: Sequence[str] = (),
    kind: str = "tool",
    cost_tier: str = "normal",
    city_od_policy: str = "none",
    max_fanout: int = 1,
    workflow: Sequence[str] | None = None,
    workflow_inputs: Sequence[str] = (),
    workflow_conditions: Sequence[str] = (),
    execution_strategy: str | None = None,
    availability: str = "available",
    unavailable_reason: str = "",
    slot_guidance: Mapping[str, str] | Sequence[tuple[str, str]] = (),
    slot_defaults: Mapping[str, str] | Sequence[tuple[str, str]] = (),
) -> Dict[str, Any]:
    value: Dict[str, Any] = {
        "id_format": id_format,
        "choose_when": tuple(choose_when),
        "avoid_when": tuple(avoid_when),
        "examples": tuple(examples),
        "kind": kind,
        "cost_tier": cost_tier,
        "city_od_policy": city_od_policy,
        "max_fanout": int(max_fanout),
        "workflow_inputs": tuple(workflow_inputs),
        "workflow_conditions": tuple(workflow_conditions),
        "availability": availability,
        "unavailable_reason": unavailable_reason,
        "slot_guidance": tuple(dict(slot_guidance).items()),
        "slot_defaults": tuple(dict(slot_defaults).items()),
    }
    if workflow is not None:
        value["workflow"] = tuple(workflow)
    if execution_strategy is not None:
        value["execution_strategy"] = execution_strategy
    return value


# MCP-style capability manifests migrated from the frozen Deep router contract.
# These descriptions are the only semantic tool contract consumed by Fast-Go
# and Fast-Plus. Deterministic code validates the selected manifest afterwards.
_CAPABILITY_CONTRACTS: Dict[str, Dict[str, Any]] = {
    "train": _contract(
        "id=纯 G/D/C 车次号，例如 G87",
        ("询问某一车次近期或具体动车组担当、车底编号",),
        ("余票、经停、路线、车厢布局、仅问车型知识",),
        examples=("G20这几天用什么车", "G1今天的具体车组"),
    ),
    "train_overview": _contract(
        "semantic slots: train=G3；不要把 workflow 名称放进 query_id",
        ("询问单一车次的特色、亮点、介绍、整体画像或综合情况",),
        ("只问路线时用 path_detail；只问担当时用 train；不得追问 OD",),
        examples=("G3次列车有什么特色",),
        kind="workflow",
        workflow=("path_detail", "train"),
        workflow_inputs=("train", "train"),
        workflow_conditions=("always", "always"),
        execution_strategy="parallel",
    ),
    "emu": _contract(
        "id=完整动车组编号，全大写且去掉连字符/空格，例如 CR400AFZ2333",
        ("明确具体动车组编号，询问近期担当车次或交路",),
        ("AFBS、CR400 等车型简称；车次号；普通车型知识",),
        examples=("CR400BF5033最近跑什么交路",),
    ),
    "smartemu_analysis": _contract(
        "id=一个或多个 G/D/C 车次，逗号分隔，例如 G7,G20,G33",
        ("多车次动车组使用、智能动车概率、横向担当分析",),
        ("K/T/Z 等非动车车次；具体单组动车交路",),
        examples=("分析G7-G20-G33的智能动车使用情况",),
    ),
    "route_smartemu_search": _contract(
        "semantic slots: dep + arr，可选 date/model；第二步车次必须来自第一步工具事实",
        ("明确 OD 上哪些车由某智能动车组/车型家族担当",),
        ("已有明确车次列表时直接用 smartemu_analysis；绝不凭空选择代表车次",),
        examples=("明天上海虹桥到北京南有哪些AFZ智能动车担当",),
        kind="workflow",
        city_od_policy="bounded_expand_4",
        max_fanout=4,
        workflow=("station_to_station_mini", "smartemu_analysis"),
        workflow_inputs=("route", "result_trains"),
        workflow_conditions=("always", "after_previous_evidence"),
        execution_strategy="sequential",
    ),
    "path_detail": _contract(
        "id=纯车次号；date 可省略并默认查询日",
        ("单车次始终站、经停、图定时刻、路线、途经线路",),
        ("OD列车推荐；实时晚点；历史停站变化；地图坐标",),
        examples=("G6742的路线是什么", "G20具体经停站"),
    ),
    "path_future": _contract(
        "id=纯车次号；date=用户明确给出的未来日期",
        ("单车次在明确未来日期的运行路径或时刻",),
        ("未给未来日期；今日路径；历史日期",),
        examples=("下月1日G1经过哪些站",),
    ),
    "path_past": _contract(
        "id=纯车次号；date=用户明确给出的过去日期",
        ("单车次在明确历史日期的路径或时刻回溯",),
        ("未给历史日期；今日路径；未来日期",),
        examples=("去年国庆G1停哪些站",),
    ),
    "path_stopcheck": _contract(
        "id=车次列表|站名列表，例如 G1,G3|南京南,济南西；站名须有用户依据",
        ("何时开始/曾经是否停站、多车次多站停站矩阵、历史停站变化",),
        ("普通当前经停表；凭空猜测待核验车站",),
        examples=("G1最早是不是只停南京南，何时加停济南西",),
    ),
    "station_to_station_mini": _contract(
        "id=出发地-到达地；date 可省略并默认查询日",
        ("普通 OD 有哪些车、推荐候选、Top 车次列表；默认目录工具",),
        ("明确要求全量明细；余票；中转；单车次路径",),
        examples=("南京南到福州有什么车",),
        city_od_policy="bounded_expand_4",
        max_fanout=4,
    ),
    "station_to_station_detail": _contract(
        "id=具体出发站-具体到达站；date 可省略",
        ("用户明确要求某 OD 全量车次或完整明细",),
        ("普通推荐不得滥用；只有城市且要求全量时应先确认具体站",),
        examples=("南京南到福州所有车次完整列表",),
        cost_tier="expensive",
        city_od_policy="explicit_station_preferred",
    ),
    "station_to_station_future": _contract(
        "id=出发地-到达地；date=用户明确未来日期",
        ("明确未来日期的 OD 车次目录",),
        ("未给未来日期；今日或历史查询",),
        city_od_policy="bounded_expand_4",
        max_fanout=4,
    ),
    "station_to_station_past": _contract(
        "id=出发地-到达地；date=用户明确过去日期",
        ("明确历史日期的 OD 车次目录回溯",),
        ("未给历史日期；今日或未来查询",),
        city_od_policy="bounded_expand_4",
        max_fanout=4,
    ),
    "s2s_benchmark": _contract(
        "id=出发地-到达地；date 可省略",
        ("某 OD 最快、标杆、推荐度或速度评级；评级只认工具证据",),
        ("不得把少停站自行定义为标杆；不得用于余票",),
        examples=("南京南到福州哪些是标杆车",),
        city_od_policy="bounded_expand_4",
        max_fanout=4,
    ),
    "route_train_benchmark": _contract(
        "semantic slots: train + dep + arr；不要把 workflow 名称放进 query_id",
        ("核验一个明确车次在一个明确 OD 区间是否属于标杆/最快/旗舰候选",),
        ("没有明确车次或没有明确区间；不得自行用终到站或停站数否定",),
        examples=("G3089是不是南京南到福州的标杆车",),
        kind="workflow",
        workflow=("s2s_benchmark", "path_detail", "train"),
        workflow_inputs=("route", "train", "train"),
        workflow_conditions=("always", "always", "always"),
        execution_strategy="parallel",
    ),
    "s2s_timeband_dep": _contract(
        "id=出发地-到达地；date 可省略",
        ("按出发时段筛选或比较 OD 车次",),
        ("按到达时段应使用 s2s_timeband_arr；余票",),
        city_od_policy="bounded_expand_4",
        max_fanout=4,
    ),
    "s2s_timeband_arr": _contract(
        "id=出发地-到达地；date 可省略",
        ("按到达时段筛选或比较 OD 车次",),
        ("按出发时段应使用 s2s_timeband_dep；余票",),
        city_od_policy="bounded_expand_4",
        max_fanout=4,
    ),
    "s2s_regular_only": _contract(
        "id=出发地-到达地；date 可省略",
        ("明确只看图定/常规开行车次",),
        ("临客筛选；普通列表无需该过滤器",),
        city_od_policy="bounded_expand_4",
        max_fanout=4,
    ),
    "s2s_temporary_only": _contract(
        "id=出发地-到达地；date 可省略",
        ("明确只看临时旅客列车、临客或加开车",),
        ("图定车筛选；普通列表无需该过滤器",),
        city_od_policy="bounded_expand_4",
        max_fanout=4,
    ),
    "s2s_bureau_filter": _contract(
        "id=出发地-到达地|路局，例如 南京南-福州|上局；date 可省略",
        ("明确按担当路局筛选某 OD 车次",),
        ("未给路局偏好；普通 OD 列表",),
        city_od_policy="bounded_expand_4",
        max_fanout=4,
    ),
    "left_ticket_s2s": _contract(
        "id=出发城市/站-到达城市/站；date=乘车日期；只发起一次官方查询",
        ("实时余票、席位库存、售罄核验、用12306验证",),
        ("缺少 OD 或日期；刷票式重复调用；只给车次但未给乘坐区间",),
        examples=("用12306验证G1今天商务座是否售罄",),
        cost_tier="sensitive",
        city_od_policy="native_city_od_single",
        slot_guidance={
            "dep": "余票接口必须有完整出发地，请用户明确出发城市或车站。",
            "arr": "余票接口必须有完整到达地，请用户明确到达城市或车站。",
            "date": "实时余票按乘车日查询，请用户明确日期，不能静默使用今天。",
        },
    ),
    "transfer_12306": _contract(
        "id=出发地-到达地，可选 |中转站；date=乘车日期",
        ("官方中转换乘方案；用户可选指定中转车站",),
        ("直达车列表；余票单查；用户提到经由但未说具体中转站时需追问",),
        examples=("南京到福州经上饶怎么换乘",),
        cost_tier="sensitive",
        city_od_policy="native_city_od_single",
        slot_guidance={
            "dep": "中转查询必须有出发地，请用户明确出发城市或车站。",
            "arr": "中转查询必须有目的地，请用户明确到达城市或车站。",
            "date": "中转方案按乘车日生成，请用户明确日期，不能静默使用今天。",
        },
    ),
    "telecode": _contract(
        "id=明确中文站名",
        ("站名转三字母电报码",),
        ("电报码转站名；模糊找站；静态车站资料",),
        examples=("南京南的电报码是什么",),
        cost_tier="local",
    ),
    "name": _contract(
        "id=三字母电报码，例如 NKH",
        ("电报码反查车站名",),
        ("站名转电报码；非三字母代码",),
        examples=("NKH是什么站",),
        cost_tier="local",
    ),
    "station": _contract(
        "id=明确单一车站名；Planner 在本地转电报码",
        ("明确询问单站的路局、城市、省份、拼音等静态元数据",),
        ("只是提到站名；线路归属/换乘原理；实时大屏；电报码转换",),
        examples=("南京南站属于哪个铁路局",),
        cost_tier="low_frequency",
    ),
    "station_preselect": _contract(
        "id=用户给出的模糊或不完整站名关键词",
        ("用户明确要搜索、补全或消歧一个不完整站名",),
        ("已知精确站名；普通单站元数据；不得擅自高频调用",),
        examples=("帮我找名字里有句容的站",),
        cost_tier="expensive",
    ),
    "train_preselect": _contract(
        "id=用户给出的不完整车次关键词",
        ("用户明确要搜索或补全模糊车次号",),
        ("已有完整车次；不得用于随机推荐",),
        examples=("帮我找G30开头的车次",),
        cost_tier="expensive",
    ),
    "random_train": _contract(
        "id=random",
        ("用户明确要求随机挑一趟车",),
        ("普通推荐、最快车、路线查询",),
        examples=("随机给我选一趟高铁",),
        cost_tier="expensive",
    ),
    "train_delay": _contract(
        "id=纯车次号；RailGo 请求只传 train，OD/车站仅用于本地展示范围",
        ("当前是否晚点、正晚点状态、各站实时状态",),
        ("历史/未来晚点；不得以图定时刻或 path_detail 代替实时证据；不得要求 OD",),
        examples=("G813今天有没有晚点", "G813徐州东到福州晚点了吗"),
        cost_tier="low_frequency",
        workflow=("path_detail", "train_delay"),
        workflow_inputs=("train", "train"),
        workflow_conditions=("scope_missing", "always"),
        execution_strategy="adaptive",
        slot_guidance={
            "train": "正晚点接口只需要一个完整车次号；只追问车次，不要追问出发站、到达站或日期。",
        },
    ),
    "train_station_access": _contract(
        "id=车次|车站|arrival/departure；date=查询日期",
        ("明确车次在明确车站的检票口、站台、停台或出站口",),
        ("整站大屏；路线经停；缺车次或缺车站时必须追问",),
        examples=("G1今天北京南从哪个检票口进站",),
        cost_tier="low_frequency",
        slot_guidance={
            "train": "检票口、站台和出站口接口必须绑定具体车次，请用户提供完整车次号。",
            "station": "同一车次沿途各站信息不同，请用户明确要查哪个具体车站。",
            "date": "站台与检票口信息按运行日期发布；用户未说明时按北京时间当天，明确日期时以用户输入为准。",
            "direction": "该接口区分到达或出发；用户未说明时按出发，明确说到达/出站时以用户输入为准。",
        },
        slot_defaults={"date": "today", "direction": "departure"},
    ),
    "station_board": _contract(
        "id=车站|arrival/departure，例如 南京南|departure",
        ("明确车站当前到达/出发大屏、实时车次列表",),
        ("车站静态资料；单车检票口；缺车站",),
        examples=("切换到南京南站的出发大屏",),
        cost_tier="low_frequency",
        slot_guidance={
            "station": "车站大屏必须绑定一个具体车站，请用户明确站名。",
            "direction": "大屏接口分到达榜和出发榜；用户未说明时默认出发榜，明确说到达时以用户输入为准。",
        },
        slot_defaults={"direction": "departure"},
    ),
    "coach_layout": _contract(
        "id=纯车次号",
        ("车厢编组、定员、速度、设施或车厢图片",),
        ("具体动车组担当应使用 train；当前暂不对用户开放",),
        cost_tier="low_frequency",
        availability="disabled",
        unavailable_reason="车厢图体验正在重做，暂时下线",
    ),
    "train_route_map": _contract(
        "id=纯车次号",
        ("明确要求线路地图、坐标点或可视化绘图",),
        ("普通路线/经停/时刻应使用 path_detail；当前暂不对用户开放",),
        cost_tier="low_frequency",
        availability="disabled",
        unavailable_reason="线路点可视化体验正在重做，暂时下线",
    ),
}

if set(_CAPABILITY_CONTRACTS) != set(_BASE_CAPABILITIES):
    missing = sorted(set(_BASE_CAPABILITIES) - set(_CAPABILITY_CONTRACTS))
    extra = sorted(set(_CAPABILITY_CONTRACTS) - set(_BASE_CAPABILITIES))
    raise RuntimeError(f"capability manifest mismatch: missing={missing} extra={extra}")

CAPABILITIES: Dict[str, ToolCapability] = {
    object_name: replace(capability, **_CAPABILITY_CONTRACTS[object_name])
    for object_name, capability in _BASE_CAPABILITIES.items()
}


class ToolCapabilityRegistry:
    """Single source of truth shared by routing, validation and answering."""

    def __init__(self, capabilities: Mapping[str, ToolCapability], version: str = REGISTRY_VERSION):
        self.version = str(version)
        self._capabilities = dict(capabilities)

    def get(self, object_name: str | None) -> ToolCapability | None:
        return self._capabilities.get(str(object_name or "").strip())

    def objects(self, *, include_disabled: bool = True) -> set[str]:
        return {
            name
            for name, capability in self._capabilities.items()
            if include_disabled or capability.is_available
        }

    def routable_objects(self) -> set[str]:
        return self.objects(include_disabled=False)

    def executable_objects(self) -> set[str]:
        return {
            name
            for name, capability in self._capabilities.items()
            if capability.is_executable
        }

    def workflow_objects(self) -> set[str]:
        return {
            name
            for name, capability in self._capabilities.items()
            if capability.kind == "workflow" and capability.is_available
        }

    def manifests(
        self,
        level: str = "l1",
        objects: Iterable[str] | None = None,
        *,
        include_disabled: bool = False,
        include_workflows: bool = True,
    ) -> list[Dict[str, Any]]:
        selected = set(objects or self._capabilities)
        manifests = []
        for object_name, capability in self._capabilities.items():
            if object_name not in selected:
                continue
            if not include_disabled and not capability.is_available:
                continue
            if not include_workflows and capability.kind == "workflow":
                continue
            manifests.append(capability.mcp_manifest(level=level))
        return manifests

    def catalog(
        self,
        level: str = "l0",
        objects: Iterable[str] | None = None,
        *,
        include_disabled: bool = False,
        include_workflows: bool = True,
    ) -> str:
        normalized_level = "l1" if str(level).lower() == "l1" else "l0"
        selected = set(objects or self._capabilities)
        lines = [
            f"RailGPT MCP-style capability registry version={self.version} level={normalized_level}",
            "Discovery rule: select exactly one semantic capability or workflow first; never invent an unlisted object.",
        ]
        for object_name, capability in self._capabilities.items():
            if object_name not in selected:
                continue
            if not include_disabled and not capability.is_available:
                continue
            if not include_workflows and capability.kind == "workflow":
                continue
            lines.append(capability.prompt_line(level=normalized_level))
        lines.extend(self._routing_doctrine(level=normalized_level))
        return "\n".join(lines)

    def _routing_doctrine(self, level: str) -> tuple[str, ...]:
        core = (
            "Contract rule: ask only for missing required slots of the selected capability; optional slots never block execution.",
            "Grounding rule: latest explicit user entities/date outrank dialogue and memory; assistant prose and long-term profile are never hard slots.",
            "Evidence rule: dynamic tool evidence outranks memory and assistant statements; required_evidence cannot be substituted by a different tool.",
            "Precision rule: choose the smallest non-overlapping capability; use a declared workflow only when the user asks for that composite result.",
            "Chat rule: broad knowledge, principles, history, travel inspiration, social/meta reactions and creative requests are chat unless concrete live/tool facts are requested.",
            "Temporary service boundary: coach composition/image requests and train route map/coordinate visualizations are unavailable; choose a capability-boundary chat and never substitute assignment or timetable evidence.",
        )
        if level == "l0":
            return core
        return core + (
            "Date rule: explicit future/past tools require an explicit date; current-only tools must never answer historical/future claims.",
            "OD rule: city expansion is allowed only when city_od_policy says so; bounded_expand_4 allows at most four RailGo combinations, native_city_od_single sends one 12306 query.",
            "Assignment boundary: train/emu/smartemu_analysis concern actual set assignment; coach structure is not assignment evidence.",
            "Path boundary: path tools concern timetable/stops; live delay, station operations, ticket inventory and map geometry require their own evidence.",
            "Cost rule: expensive/low_frequency/sensitive capabilities require a clear user intent and must never be speculative fan-out.",
            "Continuation rule: resolve omitted references from the latest complete dialogue pair, but do not import stale trains/routes into a new topic.",
        )

    def infer_composite(self, evidence_objects: Iterable[str]) -> ToolCapability | None:
        available = {str(item or "").strip() for item in evidence_objects if str(item or "").strip()}
        matches = [
            capability
            for capability in self._capabilities.values()
            if capability.kind == "workflow"
            and capability.is_available
            and len(capability.workflow) > 1
            and set(capability.workflow).issubset(available)
        ]
        if not matches:
            return None
        return max(matches, key=lambda item: len(item.workflow))


TOOL_CAPABILITY_REGISTRY = ToolCapabilityRegistry(CAPABILITIES)


def get_capability(object_name: str | None) -> ToolCapability | None:
    return TOOL_CAPABILITY_REGISTRY.get(object_name)


def build_missing_slot_contract(
    object_name: str,
    missing_slots: Iterable[str],
    known_slots: Mapping[str, Any] | None = None,
) -> Dict[str, Any]:
    capability = get_capability(object_name)
    if capability is None:
        return {
            "capability": str(object_name or ""),
            "missing_slots": [str(slot) for slot in missing_slots if str(slot or "").strip()],
            "known_slots": dict(known_slots or {}),
            "questions": [],
        }
    return capability.missing_slot_contract(missing_slots, known_slots)


def capability_catalog(
    level: str = "l0",
    objects: Iterable[str] | None = None,
    *,
    include_disabled: bool = False,
    include_workflows: bool = True,
) -> str:
    return TOOL_CAPABILITY_REGISTRY.catalog(
        level=level,
        objects=objects,
        include_disabled=include_disabled,
        include_workflows=include_workflows,
    )


def capability_catalog_for_mode(
    mode: str,
    objects: Iterable[str] | None = None,
    *,
    include_workflows: bool = True,
) -> str:
    normalized = str(mode or "fast-go").strip().lower()
    level = "l1" if normalized in {"fast-plus", "fastplus"} else "l0"
    return capability_catalog(level=level, objects=objects, include_workflows=include_workflows)


def executable_capability_objects() -> set[str]:
    return TOOL_CAPABILITY_REGISTRY.executable_objects()


def routable_capability_objects() -> set[str]:
    return TOOL_CAPABILITY_REGISTRY.routable_objects()


def infer_composite_capability(evidence_objects: Iterable[str]) -> ToolCapability | None:
    return TOOL_CAPABILITY_REGISTRY.infer_composite(evidence_objects)


def context_fingerprint(payload: Mapping[str, Any] | None) -> str:
    raw = json.dumps(payload or {}, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def _first(values: Any) -> str:
    if isinstance(values, str):
        return values.strip()
    if isinstance(values, (list, tuple)):
        for value in values:
            text = str(value or "").strip()
            if text:
                return text
    return ""


def grounded_slots_from_context(context: Mapping[str, Any] | None) -> Dict[str, Any]:
    context = context or {}
    explicit_route = str(context.get("explicit_route") or "").strip()
    route = explicit_route or str(context.get("route") or "").strip()
    dep = str(context.get("dep") or "").strip()
    arr = str(context.get("arr") or "").strip()
    if route and "-" in route:
        route_dep, route_arr = route.split("-", 1)
        dep = dep or route_dep.strip()
        arr = arr or route_arr.strip()
    trains = [str(item).strip().upper() for item in (context.get("train_numbers") or []) if str(item or "").strip()]
    stations = [str(item).strip() for item in (context.get("station_mentions") or []) if str(item or "").strip()]
    return {
        "train": _first(trains),
        "trains": trains,
        "emu": str(context.get("emu_id") or "").strip(),
        "dep": dep,
        "arr": arr,
        "stations": stations,
        "station": _first(stations),
        "date": str(context.get("query_date") or "").strip(),
        "telecode": str(context.get("telecode") or "").strip(),
        "keyword": str(context.get("search_keyword") or context.get("keyword") or "").strip(),
        "bureau": _first(context.get("bureau_preferences") or context.get("bureaus") or context.get("bureau")),
        "hub": str(context.get("transfer_hub") or context.get("hub") or "").strip(),
        "direction": str(context.get("direction") or "").strip().lower(),
        "timeband": str(context.get("timeband") or "").strip(),
        "model": _first(context.get("emu_preferences") or context.get("model_preferences") or context.get("model")),
    }


def grounded_slots_for_capability(
    object_name: str,
    context: Mapping[str, Any] | None,
) -> Dict[str, Any]:
    slots = grounded_slots_from_context(context)
    capability = get_capability(object_name)
    if capability is None:
        return slots
    for slot, default in capability.slot_defaults:
        if slots.get(slot):
            continue
        if default == "today":
            slots[slot] = str((context or {}).get("query_date") or "").strip()
        else:
            slots[slot] = default
    return slots


def grounded_slots_from_query_params(
    object_name: str,
    query_id: str,
    query_date: str = "",
) -> Dict[str, Any]:
    """Decode already-validated planner parameters back into semantic slots."""

    object_name = str(object_name or "").strip()
    query_id = str(query_id or "").strip()
    slots: Dict[str, Any] = {}
    if query_date:
        slots["date"] = str(query_date).strip()

    train_objects = {
        "train", "train_overview", "route_train_benchmark", "path_detail",
        "path_future", "path_past", "train_delay", "coach_layout", "train_route_map",
    }
    if object_name in train_objects:
        match = re.search(r"[GDKTZC]\d{1,5}", query_id.upper())
        if match:
            slots["train"] = match.group(0)
            slots["trains"] = [match.group(0)]
        return slots
    if object_name == "smartemu_analysis":
        trains = re.findall(r"[GDKTZC]\d{1,5}", query_id.upper())
        if trains:
            slots["train"] = trains[0]
            slots["trains"] = trains
        return slots
    if object_name == "emu":
        slots["emu"] = query_id
        return slots
    if object_name == "path_stopcheck":
        train_text, separator, station_text = query_id.partition("|")
        trains = re.findall(r"[GDKTZC]\d{1,5}", train_text.upper())
        stations = [item.strip() for item in station_text.replace("，", ",").split(",") if item.strip()]
        if trains:
            slots["train"] = trains[0]
            slots["trains"] = trains
        if separator and stations:
            slots["station"] = stations[0]
            slots["stations"] = stations
        return slots

    route_objects = {
        "route_smartemu_search", "route_train_benchmark", "station_to_station_mini",
        "station_to_station_detail", "station_to_station_future", "station_to_station_past",
        "s2s_benchmark", "s2s_timeband_dep", "s2s_timeband_arr", "s2s_regular_only",
        "s2s_temporary_only", "s2s_bureau_filter", "left_ticket_s2s", "transfer_12306",
    }
    if object_name in route_objects:
        route_text, _, suffix = query_id.partition("|")
        if "-" in route_text:
            dep, arr = route_text.split("-", 1)
            if dep.strip() and arr.strip():
                slots["dep"] = dep.strip()
                slots["arr"] = arr.strip()
        if suffix and object_name == "s2s_bureau_filter":
            slots["bureau"] = suffix.strip()
        if suffix and object_name == "transfer_12306":
            slots["hub"] = suffix.strip()
        return slots

    if object_name == "station_board":
        station, separator, direction = query_id.partition("|")
        if station.strip():
            slots["station"] = station.strip()
            slots["stations"] = [station.strip()]
        if separator and direction.strip().lower() in {"arrival", "departure"}:
            slots["direction"] = direction.strip().lower()
        return slots
    if object_name == "train_station_access":
        parts = [part.strip() for part in query_id.replace("｜", "|").split("|") if part.strip()]
        if parts:
            match = re.search(r"[GDKTZC]\d{1,5}", parts[0].upper())
            if match:
                slots["train"] = match.group(0)
                slots["trains"] = [match.group(0)]
        if len(parts) > 1:
            slots["station"] = parts[1]
            slots["stations"] = [parts[1]]
        if len(parts) > 2 and parts[2].lower() in {"arrival", "departure"}:
            slots["direction"] = parts[2].lower()
        return slots
    if object_name in {"station", "telecode"}:
        slots["station"] = query_id
    elif object_name == "name":
        slots["telecode"] = query_id
    elif object_name in {"station_preselect", "train_preselect"}:
        slots["keyword"] = query_id
    return slots


def resolve_query_id(
    object_name: str,
    context: Mapping[str, Any] | None,
    suggested_id: str = "",
) -> str:
    slots = grounded_slots_for_capability(object_name, context)
    suggested = str(suggested_id or "").strip()
    train = str(slots.get("train") or "").upper()
    trains = list(slots.get("trains") or [])
    station = str(slots.get("station") or "")
    dep = str(slots.get("dep") or "")
    arr = str(slots.get("arr") or "")
    bureau = str(slots.get("bureau") or "")
    hub = str(slots.get("hub") or "")
    direction = str(slots.get("direction") or "")

    train_objects = {"train", "train_overview", "route_train_benchmark", "path_detail", "path_future", "path_past", "train_delay", "coach_layout", "train_route_map"}
    if object_name in train_objects:
        match = re.search(r"[GDKTZC]\d{1,5}", train or suggested.upper())
        return match.group(0) if match else ""
    if object_name == "smartemu_analysis":
        return ",".join(trains[:10]) or suggested
    if object_name == "emu":
        return str(slots.get("emu") or suggested).strip()
    if object_name == "path_stopcheck":
        suggested_train_text, separator, suggested_station_text = suggested.partition("|")
        merged_trains: list[str] = []
        for item in re.findall(r"[GDKTZC]\d{1,5}", suggested_train_text.upper()) + trains:
            if item not in merged_trains:
                merged_trains.append(item)
        merged_stations: list[str] = []
        for item in [part.strip() for part in suggested_station_text.replace("，", ",").split(",")] + list(slots.get("stations") or []):
            if item and item not in merged_stations:
                merged_stations.append(item)
        if not merged_trains or not merged_stations:
            return suggested if separator else ""
        return f"{','.join(merged_trains)}|{','.join(merged_stations)}"
    if object_name in {
        "station_to_station_mini", "station_to_station_detail", "station_to_station_future",
        "station_to_station_past", "s2s_benchmark", "s2s_timeband_dep", "s2s_timeband_arr",
        "s2s_regular_only", "s2s_temporary_only", "s2s_bureau_filter", "left_ticket_s2s",
        "transfer_12306",
    }:
        route = f"{dep}-{arr}" if dep and arr and dep != arr else suggested
        if object_name == "s2s_bureau_filter" and route and bureau and "|" not in route:
            return f"{route}|{bureau}"
        if object_name == "transfer_12306" and route and hub and "|" not in route:
            return f"{route}|{hub}"
        return route
    if object_name in {"station", "telecode"}:
        return station or suggested
    if object_name in {"station_preselect", "train_preselect"}:
        return str(slots.get("keyword") or suggested).strip()
    if object_name == "random_train":
        return "random"
    if object_name == "name":
        return str(slots.get("telecode") or suggested).strip()
    if object_name == "station_board":
        if suggested and "|" in suggested:
            return suggested
        return f"{station}|{direction}" if station and direction else ""
    if object_name == "train_station_access":
        if suggested and suggested.count("|") >= 2:
            return suggested
        return f"{train}|{station}|{direction}" if train and station and direction else ""
    return suggested


@dataclass
class IntentEnvelope:
    intent_family: str = "unknown"
    selected_capability: str = ""
    grounded_slots: Dict[str, Any] = field(default_factory=dict)
    missing_slots: list[str] = field(default_factory=list)
    scope: Dict[str, str] = field(default_factory=dict)
    required_evidence: list[str] = field(default_factory=list)
    workflow: list[str] = field(default_factory=list)
    execution_strategy: str = "single"
    confidence: int = 0
    context_fingerprint: str = ""
    registry_version: str = REGISTRY_VERSION
    presentation_mode: str = "summary"
    media_target: Dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: Mapping[str, Any] | None) -> "IntentEnvelope":
        data = dict(value or {})
        return cls(
            intent_family=str(data.get("intent_family") or "unknown"),
            selected_capability=str(data.get("selected_capability") or ""),
            grounded_slots=dict(data.get("grounded_slots") or {}),
            missing_slots=list(data.get("missing_slots") or []),
            scope=dict(data.get("scope") or {}),
            required_evidence=list(data.get("required_evidence") or []),
            workflow=list(data.get("workflow") or []),
            execution_strategy=str(data.get("execution_strategy") or "single"),
            confidence=int(data.get("confidence") or 0),
            context_fingerprint=str(data.get("context_fingerprint") or ""),
            registry_version=str(data.get("registry_version") or REGISTRY_VERSION),
            presentation_mode=str(data.get("presentation_mode") or "summary"),
            media_target={str(k): str(v) for k, v in dict(data.get("media_target") or {}).items()},
        )


def active_workflow_steps(
    capability: ToolCapability | None,
    context: Mapping[str, Any] | None,
) -> list[tuple[str, str]]:
    """Resolve declarative workflow conditions without making semantic choices."""

    if capability is None or not capability.is_available:
        return []
    route = str((context or {}).get("explicit_route") or (context or {}).get("route") or "").strip()
    slots = grounded_slots_for_capability(capability.object, context)
    scope_present = bool(route or (slots.get("dep") and slots.get("arr")))
    steps: list[tuple[str, str]] = []
    for index, object_name in enumerate(capability.workflow or (capability.object,)):
        condition = capability.workflow_conditions[index] if index < len(capability.workflow_conditions) else "always"
        if condition == "scope_missing" and scope_present:
            continue
        if condition == "scope_present" and not scope_present:
            continue
        input_source = capability.workflow_inputs[index] if index < len(capability.workflow_inputs) else "query_id"
        steps.append((object_name, input_source))
    return steps


def build_intent_envelope(
    selected_capability: str,
    context: Mapping[str, Any] | None,
    intent_family: str = "",
    confidence: int = 0,
) -> IntentEnvelope:
    capability = get_capability(selected_capability)
    slots = grounded_slots_for_capability(selected_capability, context)
    required_slots = capability.required_slots if capability else ()
    missing = [slot for slot in required_slots if not slots.get(slot)]
    date_source = str((context or {}).get("query_date_source") or "").strip().lower()
    if "date" in required_slots and date_source == "default_today" and "date" not in missing:
        # Required-date APIs need a user/context-grounded service date. Merely
        # having the router's clock default must not silently authorize a live
        # ticket or station-access request.
        missing.append("date")
    explicit_route = str((context or {}).get("explicit_route") or "").strip()
    scope: Dict[str, str] = {}
    if explicit_route and "-" in explicit_route:
        dep, arr = explicit_route.split("-", 1)
        scope = {"dep": dep.strip(), "arr": arr.strip()}
    active_steps = active_workflow_steps(capability, context)
    workflow: list[str] = [name for name, _input_source in active_steps]
    execution_strategy = str(capability.execution_strategy if capability else "single")
    if execution_strategy == "adaptive":
        execution_strategy = "sequential" if len(workflow) > 1 else "single"
    package = (context or {}).get("agent_context_package") or {}
    fingerprint = str(package.get("context_fingerprint") or "") or context_fingerprint(
        {
            "text": (context or {}).get("raw_text") or (context or {}).get("text"),
            "history": package.get("dialogue_history") or package.get("dialogue_excerpt"),
        }
    )
    return IntentEnvelope(
        intent_family=intent_family or (capability.intent_family if capability else "unknown"),
        selected_capability=selected_capability,
        grounded_slots=slots,
        missing_slots=missing,
        scope=scope,
        required_evidence=list(capability.required_evidence if capability else ()),
        workflow=workflow,
        execution_strategy=execution_strategy,
        confidence=max(0, min(100, int(confidence or 0))),
        context_fingerprint=fingerprint,
    )


def positive_evidence_objects(facts: Mapping[str, Any] | None) -> set[str]:
    objects: set[str] = set()
    for item in (facts or {}).get("queries", []):
        if not isinstance(item, dict):
            continue
        if str(item.get("type") or "") in {"query_empty", "query_error"}:
            continue
        object_name = str(item.get("object") or "").strip()
        has_payload = bool(
            item.get("evidence")
            or item.get("pretty")
            or item.get("fast_views")
            or item.get("result")
            or item.get("records")
        )
        if object_name and has_payload:
            objects.add(object_name)
    return objects


def missing_required_evidence(
    envelope: IntentEnvelope | Mapping[str, Any] | None,
    facts: Mapping[str, Any] | None,
) -> list[str]:
    resolved = envelope if isinstance(envelope, IntentEnvelope) else IntentEnvelope.from_dict(envelope)
    available = positive_evidence_objects(facts)
    return [item for item in resolved.required_evidence if item not in available]
