from enum import Enum
import threading
import time


class AgentState(Enum):
    IDLE = "idle"
    ROUTING = "routing"
    PLANNING = "planning"
    QUERYING = "querying"
    QUERY_HIT = "query_hit"
    DISPATCH = "dispatch"
    RETRYING = "retrying"
    GENERATING = "generating"
    START_QUERYING = "start_querying"
    RENDERING = "rendering"
    DONE = "done"
    WAITING = "waiting"
    CACHE_HIT = "cache_hit"
    CACHE_MISS = "cache_miss"
    ERROR = "error"
    SKIP = "skip"
    IN_FLIGHT = "in_flight"
    DONE_THINKING = "done"
    RAIL_MEMORY_HIT = "rail_memory_hit"
    RAIL_MEMORY_MISS = "rail_memory_miss"
    RAIL_MEMORY_WRITE = "rail_memory_write"
    S2S_INFLIGHT = "s2s_inflight"
    READY = "ready"
    SKIP_DIALOG = "skip_dialog"
    QUERY_EMPTY = "query_empty"
    QUERY_ERROR = "query_error"
    ADDING_PLANS = "adding_plan"
    NEED_USER_INPUT = "need_user_input"
    ROUTER_FAST_EXPERTS = "router_fast_experts"
    ROUTER_FAST_HIT = "router_fast_hit"
    ROUTER_MICRO_LLM = "router_micro_llm"
    ROUTER_CONTEXT_AGENT = "router_context_agent"
    ROUTER_CONTEXT_READY = "router_context_ready"
    ROUTER_LEGACY_FALLBACK = "router_legacy_fallback"
    ROUTER_ROUTE_REPAIRED = "router_route_repaired"
    ROUTER_ROUTE_BLOCKED = "router_route_blocked"
    CAPABILITY_ROUTING = "capability_routing"
    CAPABILITY_VALIDATED = "capability_validated"
    WORKFLOW_STEP = "workflow_step"
    EVIDENCE_MISMATCH = "evidence_mismatch"
    COACH_BINDING_HIT = "coach_binding_hit"
    COACH_BINDING_MISS = "coach_binding_miss"
    COACH_BINDING_STALE = "coach_binding_stale"
    COACH_ASSET_WRITE = "coach_asset_write"
    COACH_ASSET_CONFLICT = "coach_asset_conflict"
    MEDIA_CACHE_HIT = "media_cache_hit"
    MEDIA_CACHE_WRITE = "media_cache_write"
    MEDIA_REJECTED = "media_rejected"
    ROUTE_ASSET_READY = "route_asset_ready"
    RAILGO_OP_CACHE_HIT = "railgo_op_cache_hit"
    RAILGO_OP_CACHE_MISS = "railgo_op_cache_miss"
    RAILGO_OP_CACHE_EXPIRED = "railgo_op_cache_expired"
    RAILGO_OP_CACHE_WRITE = "railgo_op_cache_write"
    RAILGO_OP_CACHE_REFRESH_FAILED = "railgo_op_cache_refresh_failed"
    RAILGO_OP_CACHE_REJECTED = "railgo_op_cache_rejected"
    FAST_REDUCING = "fast_reducing"
    FAST_MERGING = "fast_merging"
    FAST_CACHE_HIT = "fast_cache_hit"
    FAST_CACHE_MISS = "fast_cache_miss"
    FAST_DIRECT_FINAL = "fast_direct_final"
    FAST_COORDINATING = "fast_coordinating"
    FAST_RAG_RETRIEVING = "fast_rag_retrieving"
    FAST_RAG_SKIPPED = "fast_rag_skipped"
    FAST_TEMPLATE_PLANNING = "fast_template_planning"
    FAST_COORD_READY = "fast_coord_ready"
    MEMORY_RECALLING = "memory_recalling"
    MEMORY_SESSION_HIT = "memory_session_hit"
    MEMORY_EPISODIC_HIT = "memory_episodic_hit"
    MEMORY_LONGTERM_HIT = "memory_longterm_hit"
    MEMORY_READY = "memory_ready"
    MEMORY_CURATING = "memory_curating"
    MEMORY_ARBITRATING = "memory_arbitrating"
    MEMORY_PACKET_READY = "memory_packet_ready"
    MEMORY_REJECTED = "memory_rejected"
    MEMORY_INDEX_QUEUED = "memory_index_queued"
    MEMORY_INDEXING = "memory_indexing"
    MEMORY_INDEX_DONE = "memory_index_done"
    MEMORY_INDEX_FAILED = "memory_index_failed"
    T12306_QUERYING = "t12306_querying"
    T12306_CACHE_HIT = "t12306_cache_hit"
    T12306_CACHE_MISS = "t12306_cache_miss"
    T12306_CERT_REFRESH_OK = "t12306_cert_refresh_ok"
    T12306_CERT_HIT = "t12306_cert_hit"
    T12306_CERT_REFRESH = "t12306_cert_refresh"
    T12306_BLOCKED_HTML = "t12306_blocked_html"
    T12306_NON_JSON = "t12306_non_json"
    T12306_RETRYING = "t12306_retrying"
    T12306_BACKOFF = "t12306_backoff"
    T12306_RATE_LIMIT = "t12306_rate_limit"
    T12306_DONE = "t12306_done"


class PSW:
    def __init__(self):
        self.state = None
        self.detail = ""
        self.round = 0
        self.updated_at = time.time()

        self._lock = threading.Lock()
        self._listeners = []
        self.enable_console = True

    def add_listener(self, fn):
        with self._lock:
            if fn not in self._listeners:
                self._listeners.append(fn)

    def remove_listener(self, fn):
        with self._lock:
            if fn in self._listeners:
                self._listeners.remove(fn)

    def clear_listeners(self):
        with self._lock:
            self._listeners.clear()

    def set_state(self, state: AgentState, message: str = ""):
        with self._lock:
            self.state = state
            self.detail = message
            self.updated_at = time.time()

        self._emit()

    def set_round(self, round_idx: int):
        with self._lock:
            self.round = round_idx
            self.updated_at = time.time()

        self._emit()

    def _emit(self):
        event = {
            "timestamp": time.strftime("%H:%M:%S"),
            "round": self.round,
            "state": self.state.name if self.state else None,
            "detail": self.detail,
        }

        if self.enable_console:
            print(
                f"[{event['timestamp']}] "
                f"[round {event['round']}] "
                f"[{event['state']}] "
                f"{event['detail']}"
            )

        with self._lock:
            listeners_snapshot = list(self._listeners)

        for listener in listeners_snapshot:
            try:
                listener(event)
            except Exception as exc:
                print("PSW listener error:", exc)
