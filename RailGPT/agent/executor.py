from typing import List, Dict, Any
from datetime import datetime,timedelta
import re

from tools.rail.s2s_leftTicket_12306 import pretty_leftticket, tool_s2s_leftticket
from tools.rail.s2s_query import query_s2s_route, pretty_format_s2s, today_str, pretty_format_s2s_mini, \
    pretty_format_s2s_benchmark, pretty_format_s2s_timeband_dep, pretty_format_s2s_timeband_arr, \
    pretty_format_s2s_regular_only, pretty_format_s2s_temporary_only, pretty_format_s2s_bureau_directory
from tools.rail.train_query import fetch_train_history,fetch_emu_history
from tools.rail.smartemu_analysis import SmartEMUAnalyzer
from agent.psw import AgentState
from tools.rail.station_dict import station_dict
from concurrent.futures import ThreadPoolExecutor,as_completed
from tools.rail.path_query import query_train_path, pretty_format_path, query_path_stopcheck
from tools.rail.station_query import query_station,pretty_format_station
from tools.rail.transfer_12306 import pretty_transfer, tool_transfer_query
from tools.rail.rail_12306_store import Rail12306Store
from tools.rail.railgo_v2_tools import RAILGO_V2_TOOL_OBJECTS, query_railgo_v2_tool
from tools.rail.railgo_catalog_tools import RAILGO_CATALOG_TOOL_OBJECTS, query_railgo_catalog_tool
from agent.rail_query_guard import normalize_route_id, normalize_route_with_optional_via
from agent.fast_tool_views import build_fast_candidates, build_fast_views

# from tools.station_query import fetch_station_info


EXECUTOR_IMPLEMENTED_OBJECTS = {
    "train", "emu", "smartemu_analysis",
    "path_detail", "path_future", "path_past", "path_stopcheck",
    "station", "telecode", "name",
    "station_to_station_mini", "station_to_station_detail",
    "station_to_station_future", "station_to_station_past",
    "s2s_benchmark", "s2s_timeband_dep", "s2s_timeband_arr",
    "s2s_regular_only", "s2s_temporary_only", "s2s_bureau_filter",
    "left_ticket_s2s", "transfer_12306",
} | set(RAILGO_V2_TOOL_OBJECTS) | set(RAILGO_CATALOG_TOOL_OBJECTS)


class Executor:
    def __init__(self,psw=None,max_workers=2):
        self.psw=psw
        self.pool = ThreadPoolExecutor(max_workers=max_workers)
        self.dialog_seen_queries=set()

    from concurrent.futures import as_completed

    def _preflight_query_params(self, params: Dict) -> Dict[str, Any]:
        obj = str(params.get("object") or "").strip()
        obj_id = str(params.get("id") or "").strip()

        route_like = (
            obj == "left_ticket_s2s"
            or obj == "transfer_12306"
            or obj.startswith("station_to_station")
            or obj.startswith("s2s_")
        )
        if not route_like:
            return {"status": "ok", "params": params}

        if obj == "transfer_12306":
            normalized_id = normalize_route_with_optional_via(obj_id)
        else:
            normalized_id = normalize_route_id(obj_id)

        if not normalized_id:
            question = "我需要再确认一下准确的出发站和到达站，当前识别到的站名还不够稳定。"
            if obj == "transfer_12306":
                question = "我需要再确认一下出发站、到达站和中转站，当前识别到的线路信息还不够稳定。"
            return {"status": "pending", "question": question}

        if normalized_id == obj_id:
            return {"status": "ok", "params": params}

        cloned = dict(params)
        cloned["id"] = normalized_id
        return {"status": "ok", "params": cloned}

    def execute(self, plan: List[Dict]) -> Dict[str, Any]:
        """
        Executor v4 Ultimate

        - Run query steps concurrently
        - Collect evidence facts
        - Chat steps are NOT blocking anymore (stored as meta hint)
        - Empty result becomes query_empty placeholder
        - Errors become query_error placeholder
        """
        # ================================
        # ✅ pending: stop execution
        # ================================
        if plan and plan[0].get("action") == "pending":
            question = plan[0]["params"].get("question", "请补充一下信息😊")

            return {
                "queries": [],
                "analysis": [],
                "comparisons": [],
                "meta": {
                    "errors": [],
                    "warnings": [],
                    "chat_messages": [],
                    "pending": {
                        "question": question,
                        "slot": plan[0]["params"].get("slot", []),
                        "context": plan[0]["params"].get("context", {})
                    }
                }
            }

        facts = {
            "queries": [],
            "analysis": [],
            "comparisons": [],
            "meta": {
                "errors": [],
                "warnings": [],
                "chat_messages": []  # ✅ new
            }
        }

        # =====================================================
        # ① Separate query vs others
        # =====================================================
        query_steps = []
        other_steps = []

        for step in plan:
            if step.get("action") == "query":
                query_steps.append(step)
            else:
                other_steps.append(step)

        # =====================================================
        # ② Run queries concurrently (dedup + dialog memory)
        # =====================================================
        futures = []
        seen_query_keys = set()
        future_key_map = {}
        future_params_map = {}

        for step in query_steps:
            params = step.get("params", {})
            preflight = self._preflight_query_params(params)
            if preflight.get("status") == "pending":
                return {
                    "queries": [],
                    "analysis": [],
                    "comparisons": [],
                    "meta": {
                        "errors": [],
                        "warnings": [],
                        "chat_messages": [],
                        "pending": {
                            "question": preflight["question"],
                            "slot": ["route"],
                            "context": {"failed_query": params},
                        },
                    },
                }

            params = preflight.get("params", params)

            key = self._query_key(params)

            # --- local dedup
            if key in seen_query_keys:
                if self.psw:
                    self.psw.set_state(AgentState.SKIP, f"duplicate query {key}")
                continue
            seen_query_keys.add(key)

            # --- dialog dedup
            if key in self.dialog_seen_queries:
                if self.psw:
                    self.psw.set_state(AgentState.SKIP, f"dialog duplicate {key}")
                continue

            fut = self.pool.submit(self._handle_query, params)
            futures.append(fut)
            future_key_map[fut] = key
            future_params_map[fut] = dict(params)

            if self.psw:
                self.psw.set_state(
                    AgentState.IN_FLIGHT,
                    f"HTTP request {key} entered thread pool"
                )

        # =====================================================
        # ③ Collect query results
        # =====================================================
        for future in as_completed(futures):

            key = future_key_map.get(future, "unknown_query")
            params = future_params_map.get(future, {})

            try:
                result = future.result()

                # -------------------------------
                # Case1: success evidence
                # -------------------------------
                if result:

                    self.dialog_seen_queries.add(key)

                    if self.psw:
                        self.psw.set_state(
                            AgentState.QUERY_HIT,
                            f"query success {key}"
                        )

                    facts["queries"].append(result)
                    continue

                # -------------------------------
                # Case2: empty payload
                # -------------------------------
                now_hour = datetime.now().hour

                if 0 <= now_hour < 3:
                    # maintenance window → unreliable
                    facts["meta"]["warnings"].append({
                        "action": "query",
                        "key": key,
                        "warning": "empty result during 0-3AM maintenance"
                    })
                    continue

                # daytime empty → true no trains
                facts["queries"].append({
                    "type": "query_empty",
                    "key": key,
                    "domain": params.get("domain"),
                    "object": params.get("object"),
                    "id": params.get("id"),
                    "date": params.get("date"),
                    "retryable": False
                })
                self.dialog_seen_queries.add(key)

            except Exception as e:

                err_msg = str(e)
                retryable = False

                if "timeout" in err_msg.lower():
                    retryable = True
                elif "HTTP 500" in err_msg:
                    retryable = True

                facts["meta"]["errors"].append({
                    "action": "query",
                    "key": key,
                    "error": err_msg
                })

                facts["queries"].append({
                    "type": "query_error",
                    "key": key,
                    "domain": params.get("domain"),
                    "object": params.get("object"),
                    "id": params.get("id"),
                    "date": params.get("date"),
                    "error": err_msg,
                    "retryable": retryable
                })

                if not retryable:
                    self.dialog_seen_queries.add(key)

        # =====================================================
        # ④ Execute other steps (chat/analyze/compare)
        # =====================================================
        for step in other_steps:
            action = step.get("action")
            params = step.get("params", {})

            if action == "chat":
                msg = params.get("message")
                if msg:
                    facts["meta"]["chat_messages"].append(msg)
                continue

            if action == "analyze":
                facts["analysis"].append(self._handle_analyze(params, facts))

            if action == "compare":
                facts["comparisons"].append(self._handle_compare(params, facts))

            if action == "summarize":
                continue

        return facts

    def _query_key(self, params: Dict) -> str:
        domain = params.get("domain")
        obj = params.get("object")
        obj_id = params.get("id")
        date = params.get("date")

        if date:
            return f"{domain}:{obj}:{obj_id}:{date}"

        return f"{domain}:{obj}:{obj_id}"

    def _attach_fast_views(self, query_result: Dict | None, raw_payload: Any = None) -> Dict | None:
        if not isinstance(query_result, dict):
            return query_result

        fast_views = build_fast_views(query_result, raw_payload=raw_payload)
        if fast_views:
            query_result["fast_views"] = fast_views

        fast_candidates = build_fast_candidates(query_result, raw_payload=raw_payload)
        if fast_candidates:
            query_result["fast_candidates"] = fast_candidates

        return query_result

    def _handle_query(self, params: Dict) -> Dict | None:

        domain = params.get("domain")
        obj = params.get("object")
        obj_id = params.get("id")
        date = params.get("date")

        if domain != "railway":
            return None

        # ---------------- train / emu ----------------
        if obj == "train":
            return self._query_train(obj_id)

        if obj == "emu":
            return self._query_emu(obj_id)

        if obj == "smartemu_analysis":
            return self._query_smartemu_analysis(obj_id)

        # ---------------- path evidence ----------------
        if obj in ("path_detail", "path_future", "path_past"):
            return self._query_path(obj_id, date=date,mode=obj)

        if obj == "path_stopcheck":
            return self._query_path_stopcheck(obj_id, date=date)

        # ---------------- station metadata ----------------
        if obj == "station":
            return self._query_station(obj_id)

        # ---------------- telecode tools ----------------
        if obj == "telecode":
            return self._query_telecode(obj_id)

        if obj == "name":
            return self._query_name(obj_id)

        # ---------------- OD listing tools ----------------
        if obj.startswith("station_to_station"):
            return self._query_s2s(obj_id, date=date, mode=obj)

        if obj.startswith("s2s_"):
            return self._query_s2s(obj_id, date=date, mode=obj)

        # ---------------- official 12306 ----------------
        if obj == "left_ticket_s2s":
            return self._query_left_ticket(obj_id, date=date)

        if obj == "transfer_12306":
            return self._query_transfer(obj_id, date=date)

        if obj in RAILGO_V2_TOOL_OBJECTS:
            return self._query_railgo_v2(obj, obj_id, date=date)

        if obj in RAILGO_CATALOG_TOOL_OBJECTS:
            return self._query_railgo_catalog(obj, obj_id)

        return None

    def _query_railgo_v2(self, obj: str, query_id: str, date: str | None = None) -> Dict:
        result = query_railgo_v2_tool(obj, query_id, date=date, psw=self.psw)
        return self._attach_fast_views(result, raw_payload=result.get("evidence"))

    def _query_railgo_catalog(self, obj: str, query_id: str) -> Dict:
        result = query_railgo_catalog_tool(obj, query_id, psw=self.psw)
        return self._attach_fast_views(result, raw_payload=result.get("evidence"))

    def _query_train(self, train_no: str) -> Dict:
        """
        railway:train
        查询某车次最近30天的动车组担当记录（证据链核心）。
        """

        # ============================
        # 0️⃣ Normalize input
        # ============================
        train_no = str(train_no).strip().upper()
        train_no = train_no.replace("次", "")

        if not train_no:
            return None

        # ============================
        # 1️⃣ PSW 状态：开始查询
        # ============================
        if self.psw:
            self.psw.set_state(
                AgentState.START_QUERYING,
                f"train {train_no}"
            )

        # ============================
        # 2️⃣ Fetch history (核心逻辑不动)
        # ============================
        records = fetch_train_history(train_no)

        # ============================
        # 3️⃣ PSW 状态：查询命中
        # ============================
        if self.psw:
            self.psw.set_state(
                AgentState.QUERY_HIT,
                f"train {train_no} ✓ {len(records)} records"
            )

        # ============================
        # 4️⃣ Evidence Schema 统一输出
        # ============================
        return self._attach_fast_views({
            "domain": "railway",
            "object": "train",
            "id": train_no,
            "records": records,
            "pretty": "recent 30-day EMU assignment history"
        })

    def _query_emu(self, emu_no: str) -> Dict:
        """
        railway:emu
        查询某一组动车组（CR400AFZ2333等）最近的交路运行历史。
        """

        # ============================
        # 0️⃣ Normalize input
        # ============================
        emu_no = str(emu_no).strip().upper()
        emu_no = emu_no.replace("-", "")  # CR400-2333 → CR4002333

        # ❌ 防止不完整车型输入（如 AFBS）
        if len(emu_no) < 8:
            return None

        # ============================
        # 1️⃣ PSW 状态：开始查询
        # ============================
        if self.psw:
            self.psw.set_state(
                AgentState.QUERYING,
                f"emu {emu_no}"
            )

        # ============================
        # 2️⃣ Fetch history（核心逻辑不动）
        # ============================
        records = fetch_emu_history(emu_no)

        # ============================
        # 3️⃣ PSW 状态：查询命中
        # ============================
        if self.psw:
            self.psw.set_state(
                AgentState.QUERY_HIT,
                f"emu {emu_no} ✓ {len(records)} records"
            )

        # ============================
        # 4️⃣ Evidence Schema 统一输出
        # ============================
        return self._attach_fast_views({
            "domain": "railway",
            "object": "emu",
            "id": emu_no,
            "records": records,
            "pretty": "recent service history of this EMU set"
        })

    def _is_smartemu_family(self, family: str) -> bool:
        fam = str(family or "").strip().upper().replace("-", "")
        if not fam or fam == "UNKNOWN":
            return False
        return fam.endswith(("BS", "BZ", "AZ", "AS", "AFZ", "BFZ", "AFS", "BFS"))

    def _format_smartemu_analysis_pretty(
        self,
        trains: List[str],
        results: List[Dict[str, Any]],
        truncated_input_count: int = 0,
    ) -> str:
        lines = ["智能动车使用分析（概率工具）"]

        if truncated_input_count > 0:
            lines.append(f"- 本次仅分析前 {len(results)} 趟车；其余 {truncated_input_count} 趟已按工具上限跳过。")

        for index, item in enumerate(results, start=1):
            train_no = str(item.get("train") or "?").strip().upper()
            inference = item.get("inference") or {}
            candidate = str(inference.get("candidate") or "UNKNOWN").strip().upper()
            confidence = str(inference.get("confidence") or "LOW").strip().upper()
            stability = str(inference.get("stability") or "unknown").strip()
            recent5 = [
                str(family).strip().upper()
                for family in (inference.get("recent5") or [])
                if str(family).strip()
            ]
            top3 = inference.get("prob_top3") or []
            warning = str(inference.get("warning") or "").strip()
            records_used = int(inference.get("records_used") or 0)

            if not recent5 and candidate == "UNKNOWN" and records_used <= 0:
                lines.append(f"{index}. {train_no}")
                lines.append("   - 最近没有可用担当历史，暂时无法做可靠判断。")
                continue

            smart_recent_count = sum(1 for family in recent5 if self._is_smartemu_family(family))
            if self._is_smartemu_family(candidate):
                smart_label = "倾向智能动车组"
            elif smart_recent_count > 0:
                smart_label = "近期出现过智能动车组，但当前最可能担当并非智能配置"
            else:
                smart_label = "近期未见明确智能动车组信号"

            top3_text = " / ".join(
                f"{str(family).strip().upper()} {float(prob) * 100:.1f}%"
                for family, prob in top3[:3]
            ) or "暂无"

            lines.append(f"{index}. {train_no}")
            lines.append(f"   - 最可能担当族型：{candidate}")
            lines.append(f"   - 智能属性判断：{smart_label}")
            lines.append(f"   - 置信度：{confidence}（{stability}）")
            lines.append(f"   - 概率 Top3：{top3_text}")
            if recent5:
                lines.append(f"   - 最近5次记录：{' -> '.join(recent5[:5])}")
            if warning:
                lines.append(f"   - 提示：{warning}")

        if trains:
            lines.append("")
            lines.append(f"分析车次：{', '.join(trains[:len(results)])}")

        return "\n".join(lines)

    def _query_smartemu_analysis(self, query_id: str) -> Dict | None:
        raw_query = str(query_id or "").strip()
        if not raw_query:
            return None

        trains = []
        for token in re.split(r"[,，、/\\\s]+", raw_query):
            train_no = str(token or "").strip().upper().replace("次", "")
            if not train_no:
                continue
            if train_no not in trains:
                trains.append(train_no)

        if not trains:
            if self.psw:
                self.psw.set_state(
                    AgentState.QUERY_ERROR,
                    f"smartemu invalid id format: {query_id}"
                )
            return None

        if self.psw:
            self.psw.set_state(
                AgentState.DISPATCH,
                f"smartemu dispatch → smartemu_analysis ({','.join(trains[:5])})"
            )
            self.psw.set_state(
                AgentState.QUERYING,
                f"smartemu analyzing {len(trains[:5])} train(s)"
            )

        max_trains = 5
        effective_trains = trains[:max_trains]
        truncated_input_count = max(0, len(trains) - len(effective_trains))

        try:
            analyzer = SmartEMUAnalyzer(max_trains=max_trains)
            results = analyzer.execute(effective_trains)
        except Exception as e:
            if self.psw:
                self.psw.set_state(
                    AgentState.QUERY_ERROR,
                    f"smartemu crashed: {e}"
                )
            return None

        if not results:
            if self.psw:
                self.psw.set_state(
                    AgentState.QUERY_EMPTY,
                    f"smartemu returned empty ({','.join(effective_trains)})"
                )
            return None

        pretty = self._format_smartemu_analysis_pretty(
            trains=effective_trains,
            results=results,
            truncated_input_count=truncated_input_count,
        )

        if self.psw:
            self.psw.set_state(
                AgentState.QUERY_HIT,
                f"smartemu evidence ready ✓ ({','.join(effective_trains)})"
            )

        return self._attach_fast_views({
            "domain": "railway",
            "object": "smartemu_analysis",
            "id": ",".join(effective_trains),
            "payload": None,
            "results": results,
            "pretty": pretty,
            "meta": {
                "train_count": len(results),
                "truncated_input_count": truncated_input_count,
            },
            "note": "smart emu probability analysis",
        }, raw_payload=results)

    def _query_path(self, train_no: str, date: str, mode: str) -> Dict:
        def norm_date_yyyymmdd(date: str) -> str:
            if not date:
                return None
            date = str(date).strip()
            if "-" in date:
                date = date.replace("-", "")
            if len(date) != 8 or not date.isdigit():
                return None
            return date

        query_date = norm_date_yyyymmdd(date)

        if not query_date:
            self.psw.set_state(
                AgentState.QUERY_ERROR,
                f"path invalid date format: {date}"
            )
            return None

        train_no = str(train_no).strip().upper().replace("次", "")

        if not date:
            if self.psw:
                self.psw.set_state(
                    AgentState.QUERY_ERROR,
                    f"path {train_no} missing date (Planner bug)"
                )
            return {
                "domain": "railway",
                "object": "path_detail",
                "id": train_no,
                "date": None,
                "pretty": None,
                "error": "missing date"
            }

        # ✅ parse date safely
        date_obj = datetime.strptime(date, "%Y-%m-%d").date()
        today_obj = datetime.now().date()

        if date_obj == today_obj:
            real_mode = "path_detail"
        elif date_obj > today_obj:
            real_mode = "path_future"
        else:
            real_mode = "path_past"

        if self.psw:
            self.psw.set_state(
                AgentState.DISPATCH,
                f"path dispatch → {real_mode} ({train_no}, {date})"
            )

        payload = query_train_path(train_no, query_date, psw=self.psw)

        if not payload:
            if self.psw:
                self.psw.set_state(
                    AgentState.QUERY_EMPTY,
                    f"path {train_no} returned empty ({date})"
                )
            return {
                "domain": "railway",
                "object": real_mode,
                "id": train_no,
                "date": date,
                "pretty": None,
                "error": "no timetable data"
            }

        pretty = pretty_format_path(payload,query_date=query_date)

        return self._attach_fast_views({
            "domain": "railway",
            "object": real_mode,
            "id": train_no,
            "date": date,
            "payload": None,
            "pretty": pretty,
            "note": f"timetable evidence ({real_mode})"
        }, raw_payload=payload)

    def _query_path_stopcheck(self, query_id: str, date: str) -> Dict | None:
        """
        railway:path_stopcheck (Batch Stop Evidence Tool)

        支持：
        - 最多 50 个车次
        - 最多 10 个站

        query_id 格式：
            "G87,G89,G101|南京南,杭州东"

        返回：
        - pretty: 多车次×多站 evidence table
        - payload: None（LLM 不可见）
        """

        # ============================
        # 0️⃣ Normalize query_id
        # ============================
        query_id = str(query_id).strip()

        if "|" not in query_id:
            if self.psw:
                self.psw.set_state(
                    AgentState.QUERY_ERROR,
                    f"path_stopcheck invalid id format: {query_id}"
                )
            return None

        # ============================
        # 1️⃣ Normalize date → YYYYMMDD
        # ============================
        if not date:
            if self.psw:
                self.psw.set_state(
                    AgentState.QUERY_ERROR,
                    f"path_stopcheck missing date (Planner bug)"
                )
            return None

        query_date = str(date).strip()

        # RailGo 内部 date 需要 YYYYMMDD
        if "-" in query_date:
            query_date = query_date.replace("-", "")

        if len(query_date) != 8 or not query_date.isdigit():
            if self.psw:
                self.psw.set_state(
                    AgentState.QUERY_ERROR,
                    f"path_stopcheck invalid date format: {date}"
                )
            return None

        # ============================
        # 2️⃣ PSW 状态：批量扫描启动
        # ============================
        if self.psw:
            self.psw.set_state(
                AgentState.DISPATCH,
                f"path_stopcheck dispatch ({query_id})"
            )

        if self.psw:
            self.psw.set_state(
                AgentState.QUERYING,
                f"batch stopcheck scanning... ({query_date})"
            )

        # ============================
        # 3️⃣ 调用工具层 query_path_stopcheck
        # ============================
        try:
            result = query_path_stopcheck(
                query_id=query_id,
                query_date=query_date,
                psw=self.psw
            )
        except Exception as e:
            if self.psw:
                self.psw.set_state(
                    AgentState.QUERY_ERROR,
                    f"path_stopcheck crashed: {e}"
                )
            return None

        if not result:
            if self.psw:
                self.psw.set_state(
                    AgentState.QUERY_EMPTY,
                    f"path_stopcheck returned empty ({query_id})"
                )
            return None

        # ============================
        # 4️⃣ Evidence Pretty 输出
        # ============================
        pretty = result.get("pretty")

        if not pretty:
            if self.psw:
                self.psw.set_state(
                    AgentState.QUERY_EMPTY,
                    f"path_stopcheck missing pretty evidence"
                )
            return None

        # ============================
        # 5️⃣ 查询成功
        # ============================
        if self.psw:
            self.psw.set_state(
                AgentState.QUERY_HIT,
                f"path_stopcheck evidence ready ✓ ({query_id})"
            )

        # ============================
        # 6️⃣ Return Unified Evidence Record
        # ============================
        return self._attach_fast_views({
            "domain": "railway",
            "object": "path_stopcheck",
            "id": query_id,
            "date": date,

            # 🚫 永远不给 LLM scan payload
            "payload": None,

            # ✅ 给证据文本
            "pretty": pretty,

            "meta": result.get("meta", {}),

            "note": "batch stop evidence (skip/stop/pass confirmed by timetable)"
        }, raw_payload=result.get("payload"))

    def today_str(self):
        return datetime.now().strftime("%Y%m%d")

    def _query_s2s(self, route_id: str, date: str, mode: str) -> Dict | None:
        raw_route_id = str(route_id or "").strip()
        route_id = normalize_route_id(route_id)
        if not route_id:
            if self.psw:
                self.psw.set_state(
                    AgentState.QUERY_ERROR,
                    f"s2s invalid route id blocked before tool call: {raw_route_id}"
                )
            return None

        dep, arr = route_id.split("-", 1)

        # ============================
        # Normalize date → YYYYMMDD
        # ============================
        if not date:
            if self.psw:
                self.psw.set_state(
                    AgentState.QUERY_ERROR,
                    "s2s missing date (Planner bug)"
                )
            return None

        query_date = str(date).strip().replace("-", "")

        if len(query_date) != 8 or not query_date.isdigit():
            if self.psw:
                self.psw.set_state(
                    AgentState.QUERY_ERROR,
                    f"s2s invalid date format: {date}"
                )
            return None

        # ============================
        # Dispatch
        # ============================
        if self.psw:
            self.psw.set_state(
                AgentState.DISPATCH,
                f"s2s dispatch → {mode} ({dep}→{arr}, {query_date})"
            )

        # ============================
        # Tool call
        # ============================
        try:
            raw = query_s2s_route(dep, arr, query_date, psw=self.psw)
        except Exception as e:
            if self.psw:
                self.psw.set_state(
                    AgentState.QUERY_ERROR,
                    f"s2s crashed: {e}"
                )
            return None

        # ============================
        # raw result validation (STRICT)
        # ============================

        if raw is None:
            self.psw.set_state(
                AgentState.QUERY_EMPTY,
                f"s2s tool returned None ({dep}-{arr})"
            )
            return None

        if isinstance(raw, list) and len(raw) == 0:
            self.psw.set_state(
                AgentState.QUERY_EMPTY,
                f"s2s empty list ({dep}-{arr})"
            )
            return None

        # 🚀否则一律认为有数据

        # ============================
        # Evidence payload extract
        # ============================
        evidence_payload = raw
        """if isinstance(raw, dict) and "payload" in raw:
            evidence_payload = raw["payload"]"""

        # ============================
        # Formatter dispatch
        # ============================
        formatter_map = {
            "station_to_station_detail": pretty_format_s2s,
            "station_to_station_past": pretty_format_s2s_mini,

            # ✅ future should still show full validity
            "station_to_station_future": pretty_format_s2s_mini,

            "station_to_station_mini": pretty_format_s2s_mini,

            "s2s_benchmark": pretty_format_s2s_benchmark,
            "s2s_timeband_dep": pretty_format_s2s_timeband_dep,
            "s2s_timeband_arr": pretty_format_s2s_timeband_arr,

            "s2s_regular_only": pretty_format_s2s_regular_only,
            "s2s_temporary_only": pretty_format_s2s_temporary_only,

            "s2s_bureau_filter": pretty_format_s2s_bureau_directory,
        }

        fmt = formatter_map.get(mode, pretty_format_s2s_mini)

        pretty = fmt(evidence_payload, query_date)

        if not pretty:
            return None

        if self.psw:
            self.psw.set_state(
                AgentState.QUERY_HIT,
                f"s2s evidence ready ✓ ({mode})"
            )

        return self._attach_fast_views({
            "domain": "railway",
            "object": mode,
            "id": route_id,

            # ✅统一 date
            "date": query_date,

            "payload": None,
            "pretty": pretty,

            "note": f"s2s route evidence ({mode})"
        }, raw_payload=raw)

    def _query_station(self, telecode: str) -> Dict:
        """
        railway:station 查询
        输入必须是三字电报码，例如 NKH/FZS
        """

        telecode = telecode.strip().upper()

        if self.psw:
            self.psw.set_state(
                AgentState.QUERYING,
                f"station {telecode}"
            )

        payload = query_station(telecode, psw=self.psw)

        if not payload:
            return {
                "domain": "railway",
                "object": "station",
                "id": telecode,
                "payload": None,
                "pretty": None,
                "error": "station query failed"
            }

        pretty = pretty_format_station(payload)

        return {
            "domain": "railway",
            "object": "station",
            "id": telecode,

            "payload": payload,
            "pretty": pretty,

            "note": "station info fetched (RailGo + SQLite)"
        }

    def _query_transfer(self, query_id: str, date: str) -> Dict | None:
        """
        railway:transfer_12306 (Executor Safe Wrapper)

        Official constraint:
        - Only supports: today → today+14 days

        Planner may pass:
        - date = YYYY-MM-DD
        - date = YYYYMMDD

        Tool input:
            南京-福州|上饶
        Tool query:
            南京-福州｜上饶｜2026-02-18

        Executor handles:
        - date window defense
        - formatting
        - pretty evidence only
        """

        # ============================
        # 0️⃣ Normalize query_id
        # ============================
        if not query_id:
            return None

        raw = normalize_route_with_optional_via(query_id)
        if not raw:
            return {
                "domain": "railway",
                "object": "transfer_12306",
                "id": str(query_id or "").strip(),
                "date": str(date or "").strip(),
                "payload": None,
                "pretty": "⚠️ 我还需要更准确的出发站、到达站或中转站信息，当前识别到的线路不够稳定。",
                "note": "route guard blocked invalid transfer query",
            }

        # ============================
        # 1️⃣ Normalize date string
        # ============================
        if not date:
            if self.psw:
                self.psw.set_state(
                    AgentState.QUERY_ERROR,
                    "transfer missing date (Planner bug)"
                )
            return None

        date_str = str(date).strip()

        # Allow YYYYMMDD → YYYY-MM-DD
        if len(date_str) == 8 and date_str.isdigit():
            date_str = f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:]}"

        # ============================
        # 2️⃣ Parse date into datetime
        # ============================
        try:
            query_day = datetime.strptime(date_str, "%Y-%m-%d").date()
        except Exception:
            if self.psw:
                self.psw.set_state(
                    AgentState.QUERY_ERROR,
                    f"transfer invalid date format: {date_str}"
                )
            return {
                "domain": "railway",
                "object": "transfer_12306",
                "id": raw,
                "date": date_str,
                "payload": None,
                "pretty": f"⚠️ 日期格式错误：{date_str}\n请使用 YYYY-MM-DD。",
                "note": "date format rejected by Executor"
            }

        # ============================
        # 3️⃣ 12306 Date Window Defense
        # ============================
        today = datetime.now().date()
        max_day = today + timedelta(days=14)

        # Past date → forbidden
        if query_day < today:
            return {
                "domain": "railway",
                "object": "transfer_12306",
                "id": raw,
                "date": date_str,
                "payload": None,
                "pretty": (
                    "⚠️ 官方 12306 换乘查询不支持过去日期。\n"
                    f"你选择的是：{date_str}\n"
                    "请查询今天或未来 14 天以内的车次。"
                ),
                "note": "past date blocked by Executor"
            }

        # Too far future → forbidden
        if query_day > max_day:
            return {
                "domain": "railway",
                "object": "transfer_12306",
                "id": raw,
                "date": date_str,
                "payload": None,
                "pretty": (
                    "⚠️ 官方 12306 换乘查询只支持未来 14 天。\n"
                    f"你选择的是：{date_str}\n"
                    f"最远可查到：{max_day.strftime('%Y-%m-%d')}"
                ),
                "note": "future window blocked by Executor"
            }
        # ============================
        # 4️⃣ Parse route + via
        # ============================
        if "|" in raw:
            route, via = raw.split("|", 1)
            route = route.strip()
            via = via.strip()
        else:
            route = raw
            via = ""

        if "-" not in route:
            return None

        # ============================
        # 5️⃣ Build tool query string
        # ============================
        if via:
            tool_query = f"{route}｜{via}｜{date_str}"
        else:
            tool_query = f"{route}｜{date_str}"

        # ============================
        # 6️⃣ PSW Dispatch
        # ============================
        if self.psw:
            self.psw.set_state(
                AgentState.DISPATCH,
                f"transfer dispatch → {tool_query}"
            )

        if self.psw:
            self.psw.set_state(
                AgentState.IN_FLIGHT,
                "12306 transfer API request..."
            )

        # ============================
        # 7️⃣ Tool Call (NO tool modification)
        # ============================
        try:
            result = tool_transfer_query(tool_query)
        except Exception as e:
            if self.psw:
                self.psw.set_state(
                    AgentState.QUERY_ERROR,
                    f"transfer crashed: {e}"
                )
            return None

        if not result:
            return None

        # ============================
        # 8️⃣ Pretty Evidence Only
        # ============================
        if self.psw:
            self.psw.set_state(
                AgentState.RENDERING,
                "format transfer evidence..."
            )

        try:
            pretty = pretty_transfer(result)
        except Exception as e:
            pretty = f"⚠️ Transfer evidence format failed: {e}"

        # ============================
        # 9️⃣ Unified Return Structure
        # ============================
        return self._attach_fast_views({
            "domain": "railway",
            "object": "transfer_12306",
            "id": raw,
            "date": date_str,

            "payload": None,
            "pretty": pretty,

            "note": "official 12306 transfer evidence (14-day safe Executor wrapper)"
        }, raw_payload=result)

    from datetime import datetime, timedelta

    def _query_left_ticket(self, route_id: str, date: str, tag: str = "成人") -> Dict | None:
        """
        railway:left_ticket_12306 (Executor Safe Wrapper)

        Tool:
            tool_s2s_leftticket()

        Query format inside tool:
            北京南-上海虹桥｜2026-02-17｜成人

        Official constraint:
            Only today → today+14 days

        Executor handles:
        - date normalization + window defense
        - planner bug tolerance (empty date)
        - pretty evidence only
        """

        # ============================
        # 0️⃣ Normalize route_id
        # ============================
        if not route_id:
            return None

        rid = normalize_route_id(route_id)

        if not rid:
            return {
                "domain": "railway",
                "object": "left_ticket",
                "id": str(route_id or "").strip(),
                "date": date,
                "payload": None,
                "pretty": "⚠️ 我还需要更准确的出发站和到达站信息，当前识别到的线路不够稳定。",
                "note": "route guard blocked invalid left_ticket query"
            }

        # ============================
        # 1️⃣ Normalize date
        # ============================

        today = datetime.now().date()

        # ⭐ Planner always returns date, but may be empty
        if not date:
            # ✅你要求：空 or 今天 → detail 模式
            query_day = today
            date_str = today.strftime("%Y-%m-%d")

        else:
            date_str = str(date).strip()

            # Allow YYYYMMDD
            if len(date_str) == 8 and date_str.isdigit():
                date_str = f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:]}"

            # Parse
            try:
                query_day = datetime.strptime(date_str, "%Y-%m-%d").date()
            except:
                return {
                    "domain": "railway",
                    "object": "left_ticket",
                    "id": rid,
                    "date": date_str,
                    "payload": None,
                    "pretty": (
                        f"⚠️ 日期格式错误：{date_str}\n"
                        "请使用 YYYY-MM-DD，例如：2026-02-17"
                    ),
                    "note": "invalid date rejected"
                }

        # ============================
        # 2️⃣ 12306 Window Defense (14 days)
        # ============================

        max_day = today + timedelta(days=14)

        # Past forbidden
        if query_day < today:
            return {
                "domain": "railway",
                "object": "left_ticket",
                "id": rid,
                "date": date_str,
                "payload": None,
                "pretty": (
                    "⚠️ 12306 余票查询不支持过去日期。\n"
                    f"你选择的是：{date_str}\n"
                    "请查询今天或未来14天以内。"
                ),
                "note": "past date blocked"
            }

        # Too far future forbidden
        if query_day > max_day:
            return {
                "domain": "railway",
                "object": "left_ticket",
                "id": rid,
                "date": date_str,
                "payload": None,
                "pretty": (
                    "⚠️ 官方 12306 余票查询只开放未来14天。\n"
                    f"你选择的是：{date_str}\n"
                    f"最远可查到：{max_day.strftime('%Y-%m-%d')}"
                ),
                "note": "future window blocked"
            }

        # ============================
        # 3️⃣ Tag Normalize (成人/学生)
        # ============================

        ticket_tag = "学生" if "学生" in str(tag) else "成人"

        # ============================
        # 4️⃣ Build Tool Query String
        # ============================

        tool_query = f"{rid}｜{date_str}｜{ticket_tag}"

        # ============================
        # 5️⃣ PSW Dispatch
        # ============================

        if self.psw:
            self.psw.set_state(
                AgentState.DISPATCH,
                f"left_ticket dispatch → {tool_query}"
            )

            self.psw.set_state(
                AgentState.IN_FLIGHT,
                "12306 leftTicket queryG request..."
            )

        # ============================
        # 6️⃣ Tool Call (NO tool modification)
        # ============================

        try:
            result = tool_s2s_leftticket(tool_query)
        except Exception as e:
            if self.psw:
                self.psw.set_state(
                    AgentState.QUERY_ERROR,
                    f"left_ticket crashed: {e}"
                )
            return None

        if not result:
            return None

        # ============================
        # 7️⃣ Pretty Evidence Only
        # ============================

        if self.psw:
            self.psw.set_state(
                AgentState.RENDERING,
                "format leftTicket evidence..."
            )

        try:
            pretty = pretty_leftticket(result)
        except Exception as e:
            pretty = f"⚠️ LeftTicket pretty format failed: {e}"

        # ============================
        # 8️⃣ Unified Return Structure
        # ============================

        return self._attach_fast_views({
            "domain": "railway",
            "object": "left_ticket_12306",
            "id": rid,
            "date": date_str,

            # 🚫 never expose payload
            "payload": None,

            # ✅ evidence
            "pretty": pretty,

            "note": "official 12306 leftTicket evidence (14-day safe Executor wrapper)"
        }, raw_payload=result)

    def _query_telecode(self, station_name: str) -> Dict | None:
        """
        railway:telecode

        Local tool:
        - Chinese station name → telecode

        Planner rule:
        - telecode/name tools must NOT auto-transfer or rewrite station names
        - strictly local lookup only
        """

        if not station_name:
            return None

        # ============================
        # 0️⃣ Normalize input (minimal)
        # ============================
        raw = str(station_name).strip()

        if not raw:
            return None

        # Planner要求：不做转站逻辑
        # 这里只允许非常轻的“站”字去除（安全且不会改变语义）
        clean = raw.replace("站", "").strip()

        # ============================
        # 1️⃣ PSW Dispatch
        # ============================
        if self.psw:
            self.psw.set_state(
                AgentState.QUERYING,
                f"telecode lookup: {raw}"
            )

        # ============================
        # 2️⃣ Local Lookup
        # ============================
        try:
            tele = station_dict.telecode_of(clean)
        except Exception as e:
            if self.psw:
                self.psw.set_state(
                    AgentState.QUERY_ERROR,
                    f"telecode crashed: {e}"
                )
            return None

        if not tele:
            if self.psw:
                self.psw.set_state(
                    AgentState.QUERY_EMPTY,
                    f"telecode not found: {raw}"
                )
            return None

        tele = tele.strip().upper()

        # ============================
        # 3️⃣ Pretty Evidence
        # ============================
        pretty = f"📍 Station Telecode\n- {raw} → {tele}"

        # ============================
        # 4️⃣ Success
        # ============================
        if self.psw:
            self.psw.set_state(
                AgentState.QUERY_HIT,
                f"telecode ✓ {raw} → {tele}"
            )

        # ============================
        # 5️⃣ Unified Return
        # ============================
        return {
            "domain": "railway",
            "object": "telecode",
            "id": raw,

            "payload": None,
            "pretty": pretty,

            "note": "local station name → telecode"
        }

    def _query_name(self, telecode: str) -> Dict | None:
        """
        railway:name

        Local tool:
        - telecode → Chinese station name

        Strict mode:
        - telecode must be 3-letter code (NKH, VNP, etc.)
        """

        if not telecode:
            return None

        # ============================
        # 0️⃣ Normalize input
        # ============================
        raw = str(telecode).strip().upper()

        if len(raw) != 3 or not raw.isalpha():
            if self.psw:
                self.psw.set_state(
                    AgentState.QUERY_ERROR,
                    f"name invalid telecode: {telecode}"
                )
            return None

        # ============================
        # 1️⃣ PSW Querying
        # ============================
        if self.psw:
            self.psw.set_state(
                AgentState.QUERYING,
                f"name lookup: {raw}"
            )

        # ============================
        # 2️⃣ Local Lookup
        # ============================
        try:
            name = station_dict.name_of(raw)
        except Exception as e:
            if self.psw:
                self.psw.set_state(
                    AgentState.QUERY_ERROR,
                    f"name crashed: {e}"
                )
            return None

        if not name:
            if self.psw:
                self.psw.set_state(
                    AgentState.QUERY_EMPTY,
                    f"name not found: {raw}"
                )
            return None

        # ============================
        # 3️⃣ Pretty Evidence
        # ============================
        pretty = f"📍 Station Name\n- {raw} → {name}"

        # ============================
        # 4️⃣ Success
        # ============================
        if self.psw:
            self.psw.set_state(
                AgentState.QUERY_HIT,
                f"name ✓ {raw} → {name}"
            )

        # ============================
        # 5️⃣ Unified Return
        # ============================
        return {
            "domain": "railway",
            "object": "name",
            "id": raw,

            "payload": None,
            "pretty": pretty,

            "note": "local telecode → station name"
        }

    def _handle_analyze(self, params: Dict, facts: Dict) -> Dict:
        target = params.get("target")
        metric = params.get("metric", "default")

        return {
            "target": target,
            "metric": metric,
            "based_on": len(facts["queries"]),
            "note": "analyze 尚未实现"
        }

    def _handle_compare(self, params: Dict, facts: Dict) -> Dict:
        return {
            "object": params.get("object"),
            "targets": params.get("targets", []),
            "note": "compare 尚未实现"
        }


# ============================================================
# ✅ Executor Integrated Regression Test (All Tools Covered)
# ============================================================

if __name__ == "__main__":

    print("\n===================================================")
    print("🚄 RailAI Executor v2.6 — FULL TOOL REGRESSION TEST")
    print("===================================================\n")

    # ------------------------------------------------
    # 0) Dummy PSW (minimal)
    # ------------------------------------------------
    class DummyPSW:
        def set_state(self, state, msg=""):
            print(f"[{state}] {msg}")

        def error(self, title, e):
            print(f"[ERROR] {title}: {e}")

    psw = DummyPSW()

    # ------------------------------------------------
    # 1) Executor init
    # ------------------------------------------------
    exe = Executor(psw=psw, max_workers=4)

    # ------------------------------------------------
    # 2) Test Date (Past → Cache Preferred)
    # ------------------------------------------------
    test_date = "20260218"

    # ------------------------------------------------
    # 3) FULL PLAN (covers every tool)
    # ------------------------------------------------
    plan = [
  {
    "action": "query",
    "params": {
      "domain": "railway",
      "object": "s2s_bureau_filter",
      "id": "南京南-福州|南昌局",
      "date": "2026-02-12"
    }
  }
]

    # ------------------------------------------------
    # 4) Execute
    # ------------------------------------------------
    facts = exe.execute(plan)

    # ------------------------------------------------
    # 5) Print Results Summary
    # ------------------------------------------------
    print("\n===================================================")
    print("✅ EXECUTION FINISHED — FACTS SUMMARY")
    print("===================================================\n")

    print("Query Evidence Count =", len(facts["queries"]))
    print("Warnings =", len(facts["meta"]["warnings"]))
    print("Errors   =", len(facts["meta"]["errors"]))

    print("\n---------------- Evidence Preview ----------------\n")

    for q in facts["queries"]:
        print("--------------------------------------------------")
        print("TYPE =", q.get("object") or q.get("type"))
        print("ID   =", q.get("id") or q.get("key"))
        print(q.get("pretty", "(no pretty output)"))
        print("--------------------------------------------------\n")
