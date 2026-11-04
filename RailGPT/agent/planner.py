from typing import List, Dict
from datetime import datetime
from tools.rail.station_dict import station_dict
from agent.pending_utils import normalize_pending_payload
from agent.psw import AgentState

class Planner:
    """
    Planner 的职责：
    - 整理 Router 返回的 tasks
    - 生成最终可执行的 plan
    """

    def __init__(self,psw=None):
        self.status = None
        self.psw = psw

    def normalize_query_id(self, obj: str, obj_id: str) -> str | None:
        """
        Normalize Router task ids into Executor/tool-acceptable ids.

        ==============================
        Core Rule Summary (VERY IMPORTANT)
        ==============================

        ✅ station 工具：
            - 输入允许站名 → 必须转 telecode
            - 因为 station API 需要电报码

        🚫 telecode / name 工具：
            - 严禁转码！
            - 它们自己就是转码工具

        🚫 OD / 余票 / 中转 / benchmark：
            - 必须保留原始站名（Executor 层解析）
            - 不允许 Planner 擅自转成 telecode

        ✅ train / path / emu：
            - 统一大写
            - 清洗掉“次”
            - EMU 去掉非法符号

        ==============================
        """

        if not obj_id:
            return None

        obj_id = str(obj_id).strip()

        if obj == "station_preselect":
            return obj_id

        if obj == "train_preselect":
            return obj_id.upper()

        if obj == "random_train":
            return "random"

        # =====================================================
        # 🚄 Train / Path-like objects (车次工具统一大写)
        # =====================================================
        if obj in (
                "train",
                "path_detail",
                "path_future",
                "path_past",
                "train_delay",
                "coach_layout",
                "train_route_map",
        ):
            # G87次 → G87
            return obj_id.replace("次", "").strip().upper()

        # =====================================================
        # 🚉 Stop-check matrix (保留原格式)
        # =====================================================
        if obj == "path_stopcheck":
            # Example: "G87,G89,G101|南京南,杭州东"
            return obj_id.replace("，", ",").replace("｜", "|")

        # =====================================================
        # 🚆 EMU Tool (车组号严格规范化)
        # =====================================================
        if obj == "emu":
            up = obj_id.upper().replace("-", "").replace(" ", "")

            # ✅ full EMU IDs accepted
            if up.startswith(("CR400", "CR300", "CRH","CR")):
                return up

            # ❌ reject shorthand like AFBS/BFS/AFS
            return None

        # =====================================================
        # 🎫 Ticket / Transfer tools (OD必须保留站名)
        # =====================================================
        if obj in ("left_ticket_s2s", "transfer_12306"):
            # Example:
            # 南京-福州|上饶
            # 南京南-福州南｜上饶站
            return (
                obj_id.replace("｜", "|")
                .replace("—", "-")
                .replace("–", "-")
                .strip()
            )

        # =====================================================
        # 🚄 Station-to-Station family (全部保持原站名)
        # =====================================================
        if obj in (
                "station_to_station_mini",
                "station_to_station_detail",
                "station_to_station_future",
                "station_to_station_past",
                "s2s_benchmark",
                "s2s_timeband_dep",
                "s2s_timeband_arr",
                "s2s_regular_only",
                "s2s_temporary_only",
                "s2s_bureau_filter",
        ):
            # Planner 不动站名，tool层会解析南京→南京南等
            return obj_id

        # =====================================================
        # 🔤 Telecode tool (站名→电报码)
        # MUST keep station name, NOT telecode
        # =====================================================
        if obj == "telecode":
            # Input: 深圳北站 → 深圳北
            name = obj_id
            if name.endswith("站"):
                name = name[:-1]
            return name.strip()

        # =====================================================
        # 🔤 Name tool (电报码→站名)
        # MUST keep telecode, NOT station name
        # =====================================================
        if obj == "name":
            # Only accept pure 3-letter telecodes
            if len(obj_id) == 3 and obj_id.isascii() and obj_id.isalpha():
                return obj_id.upper()
            return None

        # =====================================================
        # 🏙 Station metadata tool (站名→电报码必须转)
        # =====================================================
        if obj == "station":
            """
            station API expects telecode.
            Example:
            - 深圳北 → IOQ
            - 南京南 → NKH
            """

            # Case1: already telecode
            if len(obj_id) == 3 and obj_id.isascii() and obj_id.isalpha():
                return obj_id.upper()

            # Case2: clean suffix
            name = obj_id
            if name.endswith("站"):
                name = name[:-1]
            name = name.strip()

            # Case3: dictionary lookup
            tele = station_dict.telecode_of(name)
            if tele:
                return tele.upper()

            return None

        # =====================================================
        # 🧠 Smart EMU analysis tools (保持原输入)
        # =====================================================
        if obj == "smartemu_analysis":
            # Example: "G87,G89"
            return obj_id.replace("，", ",").strip()

        if obj == "train_station_access":
            parts = [part.strip() for part in obj_id.replace("｜", "|").split("|") if part.strip()]
            if len(parts) < 3:
                return None
            parts[0] = parts[0].replace("次", "").upper()
            return "|".join(parts[:3])

        if obj == "station_board":
            parts = [part.strip() for part in obj_id.replace("｜", "|").split("|") if part.strip()]
            if len(parts) < 2:
                return None
            return "|".join(parts[:2])

        # =====================================================
        # 🚧 Fallback (unknown tool objects)
        # =====================================================
        return obj_id

    def build_plan(self, tasks: List[Dict]) -> List[Dict]:

        plan: List[Dict] = []

        has_query = False
        has_compare_or_analyze = False
        has_summarize = False

        pending_chat: List[Dict] = []

        for task in tasks:
            action = task.get("action")

            # =====================================
            # ✅ PENDING：追问态最高优先级
            # =====================================
            if action == "pending":
                return [task]

            # =====================================
            # QUERY
            # =====================================
            if action == "query":
                has_query = True

                params = task.get("params", {})
                obj = params.get("object")
                obj_id = params.get("id")

                if obj and obj_id:
                    new_id = self.normalize_query_id(obj, obj_id)

                    if new_id is None:
                        return [{
                            "action": "pending",
                            "params": normalize_pending_payload(
                                question="我还需要你补充一下关键信息才能继续查😊",
                            ),
                        }]

                    params["id"] = new_id

                plan.append(task)

            # =====================================
            # ANALYZE / COMPARE
            # =====================================
            elif action in ("compare", "analyze"):
                has_compare_or_analyze = True
                plan.append(task)

            # =====================================
            # SUMMARIZE
            # =====================================
            elif action == "summarize":
                has_summarize = True
                plan.append(task)

            # =====================================
            # CHAT
            # =====================================
            elif action == "chat":
                msg = task.get("params", {}).get("message")
                if msg and isinstance(msg, str):
                    pending_chat.append(task)

        # =====================================
        # 纯 chat
        # =====================================
        if not has_query and pending_chat:
            return pending_chat

        # query + chat
        if has_query and pending_chat:
            plan = pending_chat + plan

        # 自动 summarize
        if (has_query or has_compare_or_analyze) and not has_summarize:
            plan.append({
                "action": "summarize",
                "params": {"style": "detailed"}
            })

        # =====================================
        # ✅最终兜底：plan 不允许为空
        # =====================================
        if not plan:
            return [{
                "action": "pending",
                "params": normalize_pending_payload(
                    question="我还需要你补充一下信息才能继续😊",
                ),
            }]

        return plan

    def build_plan_from_request(self, extra_request: dict, facts: dict) -> List[Dict]:
        """
        Executor 返回 need_more_facts 时：
        Planner 自动生成补全查询 plan（支持所有新工具）
        """

        if not isinstance(extra_request, dict):
            return []

        if self.psw:
            self.psw.set_state(
                AgentState.ADDING_PLANS,
                "We need more facts, generating additional queries..."
            )

        plan: List[Dict] = []

        for item in extra_request.get("missing", []):
            domain = item.get("domain")
            obj = item.get("object")
            obj_id = item.get("id")
            date = self._resolve_query_date(obj, obj_id, item.get("date"), facts)

            if domain != "railway":
                continue

            norm_id = self.normalize_query_id(obj, obj_id)
            if norm_id is None:
                continue

            # ⭐去重 key（包含 date）
            query_key = f"railway:{obj}:{norm_id}:{date or ''}"

            if self.already_nonretryable_failed(facts, query_key):
                continue

            if self.already_satisfied(facts, query_key):
                continue

            params = {
                "domain": "railway",
                "object": obj,
                "id": norm_id
            }

            if date:
                params["date"] = date

            plan.append({
                "action": "query",
                "params": params
            })

        return plan

    def _resolve_query_date(self, obj: str, obj_id: str, date: str | None, facts: dict) -> str | None:
        if date:
            return date

        date_required_objects = {
            "path_detail",
            "path_future",
            "path_past",
            "path_stopcheck",
            "station_to_station_mini",
            "station_to_station_detail",
            "station_to_station_future",
            "station_to_station_past",
            "s2s_benchmark",
            "s2s_timeband_dep",
            "s2s_timeband_arr",
            "s2s_regular_only",
            "s2s_temporary_only",
            "s2s_bureau_filter",
            "left_ticket_s2s",
            "transfer_12306",
            "train_station_access",
        }

        if obj not in date_required_objects:
            return None

        queries = list(facts.get("queries", []))

        for item in reversed(queries):
            if not isinstance(item, dict):
                continue
            if item.get("object") == obj and item.get("id") == obj_id and item.get("date"):
                return item["date"]

        for item in reversed(queries):
            if not isinstance(item, dict):
                continue
            if item.get("id") == obj_id and item.get("date"):
                return item["date"]

        for item in reversed(queries):
            if not isinstance(item, dict):
                continue
            if item.get("date"):
                return item["date"]

        return datetime.now().strftime("%Y-%m-%d")

    def already_nonretryable_failed(self, facts: dict, key: str) -> bool:
        """
        如果某个 query 已经失败且 retryable=False
        则 Planner 不应该再次生成它
        """
        for q in facts.get("queries", []):
            if q.get("key") == key and q.get("type") in ("query_empty", "query_error"):
                if q.get("retryable") is False:
                    return True
        return False

    def already_satisfied(self, facts: dict, key: str) -> bool:
        for q in facts.get("queries", []):
            if not isinstance(q, dict):
                continue
            if q.get("type") in ("query_empty", "query_error"):
                continue
            if q.get("key") == key:
                return True
        return False
