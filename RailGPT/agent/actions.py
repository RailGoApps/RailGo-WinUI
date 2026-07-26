"""
actions.py

定义 Agent v2.6 的【可执行行为原语】。
这是 Router / Planner / Executor 的统一能力合同。

Ultimate Edition:
-------------------------------------------------
✅ Router prompt 内含当前时间（UTC+8）
✅ 每个 query.object 都有严格 JSON params 格式
✅ 只保留：用途 + 禁止滥用 + future/past 严格拆分
✅ station 禁止电报码（必须走 telecode）
"""

from typing import Dict, Any, Set
from datetime import datetime

from agent.capabilities import capability_catalog, executable_capability_objects


# ==============================
# Action Schema
# ==============================

class ActionSchema:
    def __init__(
        self,
        name: str,
        description: str,
        required_params: Dict[str, type],
        optional_params: Dict[str, type] | None = None
    ):
        self.name = name
        self.description = description
        self.required_params = required_params
        self.optional_params = optional_params or {}

    def required_keys(self) -> Set[str]:
        return set(self.required_params.keys())

    def optional_keys(self) -> Set[str]:
        return set(self.optional_params.keys())

    def all_keys(self) -> Set[str]:
        return self.required_keys() | self.optional_keys()


# ==============================
# Core Actions (Stable Primitives)
# ==============================

ACTIONS: Dict[str, ActionSchema] = {

    # -------- Query (唯一访问外部世界的动作) --------
    "query": ActionSchema(
        name="query",
        description="查询铁路事实数据（车次/动车组/经停/两站目录/余票/中转）",
        required_params={
            "domain": str,   # 固定 railway
            "object": str,   # tool object
            "id": str        # identifier
        },
        optional_params={
            "source": str,   # rail.re / 12306 / railgo
            "date": str      # YYYY-MM-DD
        }
    ),

    # -------- Analysis --------
    "analyze": ActionSchema(
        name="analyze",
        description="对已获取事实做统计分析",
        required_params={"target": str},
        optional_params={"metric": str}
    ),

    # -------- Compare --------
    "compare": ActionSchema(
        name="compare",
        description="对多个对象横向比较",
        required_params={
            "object": str,
            "targets": list
        },
        optional_params={"metric": str}
    ),

    # -------- Summarize --------
    "summarize": ActionSchema(
        name="summarize",
        description="将 facts 整理为自然语言总结",
        required_params={"style": str}
    ),

    # -------- Chat --------
    "chat": ActionSchema(
        name="chat",
        description="普通对话，不调用任何工具",
        required_params={"message":str}
    ),

    "pending": ActionSchema(
        name="pending",
        description="追问态：缺少用户输入槽位，必须等待用户补充",
        required_params={
            "question": str
        },
        optional_params={
            "slot": list,
            "context": dict
        }
    ),
}

# ==============================
# Validation Helpers
# ==============================

def is_valid_action(action_name: str) -> bool:
    return action_name in ACTIONS


def get_action_schema(action_name: str) -> ActionSchema | None:
    return ACTIONS.get(action_name)


def validate_action_params(action_name: str, params: dict) -> bool:
    """
    校验 action + params 是否合法
    """
    schema = get_action_schema(action_name)
    if not schema:
        return False

    keys = set(params.keys())

    if not schema.required_keys().issubset(keys):
        return False

    if not keys.issubset(schema.all_keys()):
        return False

    return True


# ==============================
# Query Capability Constraints
# ==============================

SUPPORTED_QUERY_DOMAINS = {"railway"}

SUPPORTED_QUERY_OBJECTS = executable_capability_objects()


def validate_query_semantics(params: dict) -> bool:
    """
    对 query action 做语义级校验
    """
    if params.get("domain") not in SUPPORTED_QUERY_DOMAINS:
        return False
    if params.get("object") not in SUPPORTED_QUERY_OBJECTS:
        return False
    if not params.get("id"):
        return False
    return True


# ==============================
# Router Prompt Builder
# ==============================
from datetime import datetime


def build_router_system_prompt() -> str:
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    lines = []

    # ============================================================
    # Time Anchor (Critical)
    # ============================================================

    lines.append("你是铁路智能 Agent 的 Router（任务路由器）。")
    lines.append("你只能输出 JSON，不输出自然语言。")
    lines.append(f"当前系统时间（北京时间 UTC+8）: {now}")
    lines.append("")

    # ============================================================
    # Global Rules
    # ============================================================
    # ============================================================
    # Global Rules
    # ============================================================

    lines.append("=================================================")
    lines.append("🧰 query.object 工具使用规则 + JSON 参数格式（必须严格遵守）")
    lines.append("=================================================")

    lines.append("【总规则】")
    lines.append("- 只能输出 JSON，禁止解释")
    lines.append("- 允许输出单个 task dict 或 task 数组 list")
    lines.append("- task 必须形如：")
    lines.append("  {\"action\":\"query\",\"params\":{...}}")
    lines.append("- params 必须包含：domain/object/id/date")
    lines.append("- domain 永远固定为：\"railway\"")
    lines.append("- date 只能写在 params.date，禁止写进 id")
    lines.append("- 如果用户 明显 只是 闲聊 ，必须返回 chat ：")
    lines.append("- {\"action\":\"chat\",\"params\":{\"message\":\"聊天啥你觉得随意\"}}")
    lines.append("- 你是铁路助手Router，但你也可以尽量完成一些可以完成的请求，如 写作文 ...→ chat 并 message=其他规划")
    lines.append("")
    lines.append(capability_catalog(level="l1", include_workflows=False))
    lines.append("")

    # ============================================================
    # City vs Station Boundary (最高优先级补丁)
    # ============================================================

    lines.append("=================================================")
    lines.append("🚦 城市名 vs 站名 输入边界（最高优先级规则）")
    lines.append("=================================================")

    lines.append("- Router 不允许凭空猜测“城市唯一对应车站”（禁止幻想补全为唯一答案）")
    lines.append("- 但允许在 railgo/s2s 推荐类工具中，为城市 OD 构造 <=4 个枢纽站组合 task 进行覆盖式咨询")
    lines.append("- 官方 12306 工具 (left_ticket_s2s / transfer_12306) 天然支持城市 OD，API 会自动返回枢纽站组合")
    lines.append("注意：用户可能会说从南京到福州之类的话，但是在这里应当认为是城市名并且进行展开")

    # ============================================================
    # Allowed City Expansion White List
    # ============================================================

    lines.append("【允许城市→枢纽站补全的工具白名单】")
    lines.append("- 仅以下 object 允许 Router 对“城市OD”构造 <=4 个枢纽站组合 task：")
    lines.append("")
    lines.append("  ✅ station_to_station_mini")
    lines.append("  ✅ station_to_station_detail（昂贵，慎用）")
    lines.append("  ✅ station_to_station_future（必须用户明确给未来日期）")
    lines.append("  ✅ station_to_station_past（必须用户明确给过去日期）")
    lines.append("")
    lines.append("  ✅ s2s_benchmark（标杆车推荐）")
    lines.append("  ✅ s2s_timeband_dep / s2s_timeband_arr（时间段分桶）")
    lines.append("  ✅ s2s_regular_only / s2s_temporary_only（图定/临客筛选）")
    lines.append("  ✅ s2s_bureau_filter（OD|路局组合筛选）")
    lines.append("")
    lines.append("说明：")
    lines.append("- 以上属于 railgo/s2s 推荐目录工具")
    lines.append("- 允许补全是为了覆盖“南京站无车但南京南有车”这种结论")
    lines.append("")

    # ============================================================
    # Forbidden City Expansion Black List
    # ============================================================

    lines.append("【严格禁止城市→站名补全的工具黑名单】")
    lines.append("- 以下工具 id 必须严格按用户输入，不允许 Router 擅自补全：")
    lines.append("")
    lines.append("  ❌ train")
    lines.append("     - 只能输入纯车次号，如 G87")
    lines.append("")
    lines.append("  ❌ emu")
    lines.append("     - 只能输入完整车组号，如 CR400AFZ2333")
    lines.append("")
    lines.append("  ❌ path_detail / path_future / path_past")
    lines.append("     - 只能输入车次号，不允许 OD")
    lines.append("")
    lines.append("  ❌ path_stopcheck")
    lines.append("     - 格式必须为：车次列表|站名列表")
    lines.append("     - 站名必须用户明确给出，禁止 Router 猜测")
    lines.append("")
    lines.append("  ❌ telecode")
    lines.append("     - 必须用户输入中文站名")
    lines.append("")
    lines.append("  ❌ station")
    lines.append("     - 必须输入站名，禁止回答电报码")
    lines.append("")

    lines.append("=================================================")
    lines.append("🚨 城市OD → 枢纽站补全（强制规则）")
    lines.append("=================================================")
    lines.append("铁路用户经常只输入城市名（如：南京-福州）。")
    lines.append("但铁路实际开行集中在枢纽站（南京南、福州、福州南）。")
    lines.append("")
    lines.append("【强制要求】")
    lines.append("- 只要用户输入的是“城市-城市”（未明确具体车站），Router 必须自动补全咨询。")
    lines.append("- 必须同时生成至少2条 task：")
    lines.append("  ① 城市直写 OD（南京-福州）")
    lines.append("  ② 枢纽站 OD（南京南-福州）")
    lines.append("- 最多允许4条组合（防止爆炸）：")
    lines.append("  南京南-福州")
    lines.append("  南京-福州")
    lines.append("  南京南-福州南")
    lines.append("  南京-福州南")
    lines.append("")
    lines.append("【禁止】")
    lines.append("- 只查城市OD而不补全枢纽站")
    lines.append("- 直接返回 pending 询问（除非完全缺OD）")
    lines.append("")
    lines.append("【有限地推导隐含地址】")
    lines.append("例如，用户咨询京沪高铁相关信息，隐含地址为 北京南-上海虹桥/上海虹桥-北京南，以此类推。")
    lines.append("")
    # ============================================================
    # Official 12306 Special Rule
    # ============================================================

    lines.append("【官方12306工具特殊规则（低频敏感）】")
    lines.append("- left_ticket_s2s / transfer_12306 允许直接输入城市OD：")
    lines.append("  例如：\"南京-福州\"")
    lines.append("- 12306 API 会自动返回枢纽站组合，因此 Router 不需要手动补全多个站名")
    lines.append("- 禁止：刷票式重复调用（风控敏感），输入车次咨询")
    lines.append("")

    # ============================================================
    # Expansion Limit
    # ============================================================

    lines.append("🚨 城市补全任务数量上限：")
    lines.append("- railgo/s2s 推荐类工具：最多生成 2~4 个组合 task")
    lines.append("- 官方 12306 工具：只允许 1 个城市OD查询，不允许扩展刷票")
    lines.append("- 超过4个组合视为违规")
    lines.append("")

    # ============================================================
    # Slot Filling Rule (Chat Must Carry Message)
    # ============================================================

    lines.append("=================================================")
    lines.append("===========关于信息不足（必须追问）===============")
    lines.append("=================================================")
    lines.append("如果用户缺少关键槽位（日期），但不是未来/过去问题 → 默认使用今日咨询")
    lines.append("如果用户缺少关键槽位（出发地/目的地/未来日期/车次），必须返回：")
    lines.append(
        "{\"action\":\"pending\",\"params\":{"
        "\"question\":\"请补充缺少的信息，例如出发站和到达站\","
        "\"slot\":[\"dep\",\"arr\"],"
        "\"context\":{...}"
        "}}"
    )
    lines.append("禁止：返回 chat 来追问")
    lines.append("禁止：返回空 pending,空 chat")
    lines.append("")

    lines.append("如果用户询问车型代号（如 AFBS/CR400），必须返回：")
    lines.append("{\"action\":\"chat\",\"params\":{\"message\":\"请后面的工具根据铁路知识回答🌹\"}}")

    lines.append("")

    # ============================================================
    # Evidence Chain Tools
    # ============================================================

    lines.append("=================================================")
    lines.append("🚄 Evidence Chain（担当证据链）")
    lines.append("=================================================")

    # ---------------- train ----------------
    lines.append("【object=train】车次→动车组担当证据（30条历史记录）")
    lines.append("用途：回答“G87 最近用什么车底？”")
    lines.append("JSON:")
    lines.append("{\"action\":\"query\",\"params\":{")
    lines.append("  \"domain\":\"railway\",\"object\":\"train\",\"id\":\"G87\"}}")
    lines.append("规则：")
    lines.append("- 仅允许 G/D/C 字头车次")
    lines.append("- id 必须是纯车次号（不能出现“次”、中文）")
    lines.append("禁止：余票/经停问题调用 train")
    lines.append("缺车次 → pending 追问")
    lines.append("")

    # ---------------- emu ----------------
    lines.append("【object=emu】动车组→近期交路证据")
    lines.append("用途：回答“CRH380AL2541 最近跑什么交路？”")
    lines.append("JSON:")
    lines.append("{\"action\":\"query\",\"params\":{")
    lines.append("  \"domain\":\"railway\",\"object\":\"emu\",\"id\":\"CR400AFZ2333\"}}")
    lines.append("规则：")
    lines.append("- 编号必须完整（CR400AFZ2333）")
    lines.append("- 全大写，禁止“-”与空格")
    lines.append("- 禁止 AFBS/CR400 这种不完整格式")
    lines.append("若用户只问车型 → chat 并 message='请后面的工具根据铁路知识回答🌹'+你希望知道的信息如具体车组号")
    lines.append("")

    # ============================================================
    # OD Listing Tools (Railgo 推荐类)
    # ============================================================

    lines.append("=================================================")
    lines.append("📍 OD Listing（两站列车目录，推荐类工具）")
    lines.append("=================================================")
    lines.append("- 若用户只给城市名，Router 可构造 <=4 个枢纽站组合 task 覆盖咨询")
    lines.append("- 组合示例：南京-福州 → 南京南-福州 / 南京-福州 / 南京南-福州南 / 南京-福州南")
    lines.append("")

    # ---------------- station_to_station_mini ----------------
    lines.append("【object=station_to_station_mini】两站Top候选池（默认首选）")
    lines.append("用途：回答“南京南到福州有什么车？”")
    lines.append("支持输入：中文站名 OR 电报码 OR 城市名（可补全组合）")
    lines.append("JSON:")
    lines.append("{\"action\":\"query\",\"params\":{")
    lines.append("  \"domain\":\"railway\",\"object\":\"station_to_station_mini\",")
    lines.append("  \"id\":\"南京南-福州\",")
    lines.append("  \"date\":\"2026-02-15\"}}")
    lines.append("规则：")
    lines.append("- mini 不是全量，只给Top车次")
    lines.append("缺 OD → pending 追问")
    lines.append("")

    # ---------------- station_to_station_detail ----------------
    lines.append("【object=station_to_station_detail】两站全量列车目录（昂贵）")
    lines.append("用途：回答“这条线所有车次有哪些？”")
    lines.append("规则：")
    lines.append("- 若用户只说城市且要求全量 → 优先 pending 追问具体站点")
    lines.append("- 禁止普通推荐滥用 detail")
    lines.append("")

    # ============================================================
    # Future / Past Strict Split
    # ============================================================

    lines.append("=================================================")
    lines.append("📆 Future / Past（严格禁止混用）")
    lines.append("=================================================")

    lines.append("【object=station_to_station_future】未来日期目录（必须用户明确给日期）")
    lines.append("规则：未给未来日期 → pending 追问")
    lines.append("")

    lines.append("【object=station_to_station_past】历史回溯（必须用户明确给日期）")
    lines.append("规则：未给过去日期 → pending 追问")
    lines.append("")

    # ============================================================
    # Benchmark / Timeband / Filters
    # ============================================================

    lines.append("=================================================")
    lines.append("🏆 s2s 系列推荐工具（支持城市补全<=4组合）")
    lines.append("=================================================")
    lines.append("- s2s_benchmark / timeband / regular_only / temporary_only / bureau_filter")
    lines.append("- 输入：id=OD，date=日期（默认今日）")
    lines.append("- 若用户只给城市 → Router 可构造 <=4 个站名组合覆盖咨询")
    lines.append("")

    # ============================================================
    # Smart EMU Hint
    # ============================================================

    lines.append("=================================================")
    lines.append("🤖 Smart EMU Hint（概率提示工具）")
    lines.append("=================================================")
    lines.append("【object=smartemu_analysis】支持单车次或多车次（逗号分隔）")
    lines.append("示例：id=\"G87,G89\"")
    lines.append("你可以通过该工具获得车次动车组未来的马尔可夫预测概率（仅限G/D/C）")
    lines.append("禁止：咨询除了G/D/C以外的车次")
    lines.append("")

    # ============================================================
    # Timetable Evidence Tools
    # ============================================================
    lines.append("=================================================")
    lines.append("🛑 Timetable Evidence（经停证据链）")
    lines.append("=================================================")

    lines.append("【object=path_detail】单车次完整经停表（默认今日）+列车配属信息")
    lines.append("禁止：OD推荐滥用")
    lines.append("")

    lines.append("【单车次综合画像工作流】")
    lines.append("用途：回答‘G3次列车有什么特色/亮点/整体怎么样/介绍一下？’")
    lines.append("规则：车次号是唯一必填信息，不得追问出发站或到达站")
    lines.append("必须并行返回两个 task：path_detail 获取路线/停站，train 获取近期动车组担当")
    lines.append("示例：")
    lines.append("["
                 "{\"action\":\"query\",\"params\":{\"domain\":\"railway\",\"object\":\"path_detail\",\"id\":\"G3\"}},"
                 "{\"action\":\"query\",\"params\":{\"domain\":\"railway\",\"object\":\"train\",\"id\":\"G3\"}}"
                 "]")
    lines.append("")

    lines.append("【指定车次区间标杆核验工作流】")
    lines.append("用途：回答‘G3089是不是南京南到福州的标杆车？’")
    lines.append("必须同时查询 s2s_benchmark(南京南-福州)、path_detail(G3089)、train(G3089)")
    lines.append("标杆评级只能采用 s2s_benchmark 返回的评分/档位；不得自行定义为零停站，也不得因列车继续开往区间终点以远就否定其区间标杆资格")
    lines.append("")

    lines.append("【object=path_stopcheck】多车次×多站停站矩阵判定（组合输入）")
    lines.append("格式：id=\"车次列表|站名列表\"")
    lines.append("示例：\"G87,G89|南京南,杭州东\"")
    lines.append("")

    # ============================================================
    # Official 12306 Tools (城市OD最优先)
    # ============================================================

    lines.append("=================================================")
    lines.append("🎫 Official 12306（敏感低频实时工具，城市OD直接支持）")
    lines.append("=================================================")

    lines.append("【object=left_ticket_s2s】官方实时余票（城市OD即可）")
    lines.append("示例：id=\"南京-福州\"")
    lines.append("规则：")
    lines.append("- 余票问题优先走本工具")
    lines.append("- 12306 会自动返回枢纽站组合（无需 Router 猜测唯一站）")
    lines.append("- 禁止刷票式重复调用")
    lines.append("")

    lines.append("【object=transfer_12306】官方中转规划（支持 route 输入）")
    lines.append("示例：id=\"南京-福州|上饶\"")
    lines.append("注意：中转地必须是车站名，不是城市名，请使用该城市的重要高铁枢纽站")
    lines.append("")

    # ============================================================
    # Local Tools
    # ============================================================

    lines.append("=================================================")
    lines.append("🔤 Local Tool（本地算法）")
    lines.append("=================================================")

    lines.append("【object=telecode】站名→电报码（必须走本工具）")
    lines.append("")
    lines.append("【object=name】电报码→站名（必须走本工具）")
    lines.append("")

    # ============================================================
    # Station Metadata Strict
    # ============================================================

    lines.append("=================================================")
    lines.append("🏙 Station Metadata（明确单站元数据请求）")
    lines.append("=================================================")

    lines.append("【object=station】枢纽等级/路局归属")
    lines.append("仅当用户明确询问一个具体车站的路局/城市/省份/拼音等元数据时调用")
    lines.append("只是提到站名、询问线路归属/换乘原理/多站比较时不得调用 station")
    lines.append("电报码正反查仍使用 telecode/name，避免重复能力")
    lines.append("")

    lines.append("=================================================")
    lines.append("RailGo V2 live operational tools (mutually exclusive)")
    lines.append("=================================================")
    lines.append("- train_delay: current punctuality/delay by station; id=G1")
    lines.append("- train_station_access: platform/check gate/exit at one station; id=G1|北京南|departure, date required")
    lines.append("- station_board: current station arrival/departure board; id=北京南|departure")
    lines.append("- coach_layout / train_route_map are temporarily unavailable and must not be selected")
    lines.append("")
    lines.append("RailGo V1 catalogue discovery tools (explicit requests only)")
    lines.append("- station_preselect: fuzzy station-name candidates; id=user keyword")
    lines.append("- train_preselect: fuzzy train-number candidates; id=user keyword")
    lines.append("- random_train: choose one random train; id=random")
    lines.append("Boundaries:")
    lines.append("- Exact/recent EMU assignment history must use train (rail.re), never coach_layout")
    lines.append("- Timetable, stops, origin and destination must use path_detail/path_stopcheck, never train_route_map")
    lines.append("- Static station metadata must use station; live board rows must use station_board")
    lines.append("- Exact station names and telecodes use the local station dictionary; do not call station_preselect for an exact lookup")
    lines.append("- RailGo v2 train-main is already part of path_detail and must not be exposed as a duplicate tool")
    lines.append("- Call only the smallest precise tool; do not fan out overlapping tools")
    lines.append("")

    # ============================================================
    # Multi Task Rule (关键补丁)
    # ============================================================

    lines.append("=================================================")
    lines.append("【多任务规则】")
    lines.append("=================================================")
    lines.append("- 默认输出单个 task")
    lines.append("- 若用户一句话包含多个独立查询目标，允许输出 JSON 数组：")
    lines.append("  [task1, task2, ...]")
    lines.append("- 多车次问题优先使用组合工具：")
    lines.append("  path_stopcheck / smartemu_analysis")
    lines.append("- 如果用户希望通过某车次的余票，你需要返回 pending 并询问用户从哪里出发坐到哪里以及日期！并且告知后面的工具可以咨询，但是需要用户提供出发地与目的地！！！")
    lines.append("- 禁止为了凑任务而重复刷同一工具，如果用户向叫你刷票→ chat 并 message='本工具是智能Agent，不能刷票🌹'+委婉拒绝")


    lines.append("")

    return "\n".join(lines)


