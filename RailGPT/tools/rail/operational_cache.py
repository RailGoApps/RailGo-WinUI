"""Short-lived, authenticated cache for RailGo operational evidence."""

from __future__ import annotations

import copy
import threading
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from typing import Any, Callable, Dict
from zoneinfo import ZoneInfo

from agent.psw import AgentState
from tools.rail.rail_store import RailStore, railstore
from tools.rail.railgo_client import RailGoContractError


BEIJING_TZ = ZoneInfo("Asia/Shanghai")
OPERATIONAL_CACHE_RETENTION_DAYS = 7


@dataclass(frozen=True)
class OperationalCachePolicy:
    expected_type: type
    ttl_seconds: int | None


OPERATIONAL_CACHE_POLICIES: Dict[str, OperationalCachePolicy] = {
    "station_board": OperationalCachePolicy(list, 5 * 60),
    "train_delay": OperationalCachePolicy(list, 15 * 60),
    "train_station_access": OperationalCachePolicy(dict, None),
}


def beijing_now() -> datetime:
    return datetime.now(BEIJING_TZ)


def _as_beijing(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=BEIJING_TZ)
    return value.astimezone(BEIJING_TZ)


def _next_beijing_midnight(now: datetime) -> datetime:
    next_date = now.date() + timedelta(days=1)
    return datetime.combine(next_date, time.min, tzinfo=BEIJING_TZ)


def _emit(psw: Any, state: AgentState, detail: str) -> None:
    if psw:
        psw.set_state(state, detail)


class RailGoOperationalCacheService:
    """Cache-aside service with per-key single-flight refresh."""

    def __init__(
        self,
        store: RailStore | None = None,
        *,
        now_fn: Callable[[], datetime] = beijing_now,
    ):
        self.store = store or railstore
        self.now_fn = now_fn
        self._locks_guard = threading.Lock()
        self._key_locks: Dict[str, threading.Lock] = {}

    def _now(self) -> datetime:
        return _as_beijing(self.now_fn())

    def _lock_for(self, object_name: str, cache_key: str) -> threading.Lock:
        key = f"{object_name}:{cache_key}"
        with self._locks_guard:
            return self._key_locks.setdefault(key, threading.Lock())

    @staticmethod
    def _validate_payload(object_name: str, payload: Any) -> Dict[str, Any]:
        policy = OPERATIONAL_CACHE_POLICIES.get(object_name)
        if policy is None:
            raise ValueError(f"unsupported operational cache object: {object_name}")
        if not isinstance(payload, dict):
            raise RailGoContractError(f"{object_name} payload is not an object")
        data = payload.get("data")
        if not isinstance(data, policy.expected_type):
            raise RailGoContractError(
                f"{object_name} data is not {policy.expected_type.__name__}"
            )
        source = payload.get("_railgo")
        if not isinstance(source, dict):
            raise RailGoContractError(f"{object_name} payload has no source metadata")
        return payload

    @staticmethod
    def _expires_at(object_name: str, now: datetime) -> datetime:
        policy = OPERATIONAL_CACHE_POLICIES[object_name]
        midnight = _next_beijing_midnight(now)
        if policy.ttl_seconds is None:
            return midnight
        return min(now + timedelta(seconds=policy.ttl_seconds), midnight)

    @staticmethod
    def _decorate_cached_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
        cloned = copy.deepcopy(payload)
        source = dict(cloned.get("_railgo") or {})
        source["cached"] = True
        cloned["_railgo"] = source
        return cloned

    def _read_valid(
        self,
        object_name: str,
        cache_key: str,
        *,
        now: datetime,
        psw: Any,
        emit_miss: bool,
    ) -> Dict[str, Any] | None:
        certificate = self.store.get_operational_cache(
            object_name,
            cache_key,
            now=now,
        )
        if certificate.get("cert_valid"):
            try:
                payload = self._validate_payload(object_name, certificate.get("payload"))
            except RailGoContractError as exc:
                _emit(
                    psw,
                    AgentState.RAILGO_OP_CACHE_REJECTED,
                    f"rejected cached {object_name}:{cache_key}: {exc}",
                )
                return None
            _emit(
                psw,
                AgentState.RAILGO_OP_CACHE_HIT,
                f"operational cache hit {object_name}:{cache_key} age={certificate['age_seconds']}s",
            )
            return {
                "payload": self._decorate_cached_payload(payload),
                "cache_status": "hit",
                "fetched_at": certificate["fetched_at"],
                "expires_at": certificate["expires_at"],
                "age_seconds": certificate["age_seconds"],
            }

        if certificate.get("rejected"):
            _emit(
                psw,
                AgentState.RAILGO_OP_CACHE_REJECTED,
                f"invalid operational certificate {object_name}:{cache_key}",
            )
        elif certificate.get("exists"):
            _emit(
                psw,
                AgentState.RAILGO_OP_CACHE_EXPIRED,
                f"operational cache expired {object_name}:{cache_key}",
            )
        elif emit_miss:
            _emit(
                psw,
                AgentState.RAILGO_OP_CACHE_MISS,
                f"operational cache miss {object_name}:{cache_key}",
            )
        return None

    def get_or_fetch(
        self,
        object_name: str,
        cache_key: str,
        fetcher: Callable[[], Dict[str, Any]],
        *,
        service_date: str,
        psw: Any = None,
    ) -> Dict[str, Any]:
        if object_name not in OPERATIONAL_CACHE_POLICIES:
            raise ValueError(f"unsupported operational cache object: {object_name}")

        now = self._now()
        cached = self._read_valid(
            object_name,
            cache_key,
            now=now,
            psw=psw,
            emit_miss=True,
        )
        if cached is not None:
            return cached

        with self._lock_for(object_name, cache_key):
            now = self._now()
            cached = self._read_valid(
                object_name,
                cache_key,
                now=now,
                psw=psw,
                emit_miss=False,
            )
            if cached is not None:
                return cached

            try:
                payload = self._validate_payload(object_name, fetcher())
            except Exception as exc:
                state = (
                    AgentState.RAILGO_OP_CACHE_REJECTED
                    if isinstance(exc, RailGoContractError)
                    else AgentState.RAILGO_OP_CACHE_REFRESH_FAILED
                )
                _emit(psw, state, f"operational refresh failed {object_name}:{cache_key}: {exc}")
                raise

            fetched_at = self._now()
            expires_at = self._expires_at(object_name, fetched_at)
            source = dict(payload.get("_railgo") or {})
            saved = self.store.save_operational_cache(
                object_name,
                cache_key,
                payload,
                service_date=service_date,
                fetched_at=fetched_at,
                expires_at=expires_at,
                source=source,
            )
            cache_status = "network"
            if saved:
                _emit(
                    psw,
                    AgentState.RAILGO_OP_CACHE_WRITE,
                    f"operational cache write {object_name}:{cache_key} until={expires_at.isoformat(timespec='seconds')}",
                )
                self.store.prune_operational_cache(
                    older_than=fetched_at - timedelta(days=OPERATIONAL_CACHE_RETENTION_DAYS)
                )
            else:
                cache_status = "network_uncached"
                _emit(
                    psw,
                    AgentState.RAILGO_OP_CACHE_REJECTED,
                    f"operational cache write rejected {object_name}:{cache_key}",
                )

            return {
                "payload": payload,
                "cache_status": cache_status,
                "fetched_at": fetched_at.isoformat(timespec="seconds"),
                "expires_at": expires_at.isoformat(timespec="seconds"),
                "age_seconds": 0,
            }


railgo_operational_cache = RailGoOperationalCacheService()


__all__ = [
    "BEIJING_TZ",
    "OPERATIONAL_CACHE_POLICIES",
    "RailGoOperationalCacheService",
    "beijing_now",
    "railgo_operational_cache",
]
