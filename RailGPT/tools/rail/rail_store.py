"""Local SQLite cache for RailGo timetable assets.

The database is a cache, not the network source of truth. Existing v1-era
databases remain readable without a schema migration. New RailGo source
metadata stays inside the JSON payload so bundled databases do not need to be
rewritten when the application is upgraded.
"""

from __future__ import annotations

import json
import hashlib
import sqlite3
import threading
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from app_runtime import writable_path


_DATE_FORMATS = ("%Y%m%d", "%Y-%m-%d", "%Y.%m.%d")
_CACHE_TABLES = {"s2s_cache", "path_cache", "station_cache"}
_BUSY_TIMEOUT_MS = 10_000
_OPERATIONAL_CACHE_SCHEMA_VERSION = 1


def today_yyyymmdd() -> str:
    return datetime.now().strftime("%Y%m%d")


def normalize_cache_date(value: Any) -> str:
    """Normalize a cache certificate date to YYYYMMDD."""

    raw = str(value or "").strip()
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(raw, fmt).strftime("%Y%m%d")
        except ValueError:
            continue
    raise ValueError(f"invalid railway cache date: {value!r}")


def normalize_run_days(values: Iterable[Any] | None) -> List[str]:
    """Return sorted, unique, valid run days and ignore malformed entries."""

    if values is None or isinstance(values, (str, bytes, dict)):
        return []

    days = set()
    for value in values:
        try:
            days.add(normalize_cache_date(value))
        except ValueError:
            continue
    return sorted(days)


def extract_valid_range(runways: List[str]) -> Optional[Dict[str, str]]:
    days = normalize_run_days(runways)
    if not days:
        return None
    return {"valid_from": days[0], "valid_to": days[-1]}


def is_running_today(date: str, runways: List[str]) -> bool:
    try:
        normalized = normalize_cache_date(date)
    except ValueError:
        return False
    return normalized in set(normalize_run_days(runways))


def _extract_s2s_trains(payload: Any) -> List[Dict[str, Any]]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if not isinstance(payload, dict):
        return []

    for key in ("trains", "data"):
        value = payload.get(key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]

    nested = payload.get("payload")
    if nested is not None and nested is not payload:
        return _extract_s2s_trains(nested)
    return []


def extract_s2s_runways(payload: Any) -> List[str]:
    all_days = set()
    for train in _extract_s2s_trains(payload):
        all_days.update(normalize_run_days(train.get("rundays") or train.get("runways")))
    return sorted(all_days)


def _unwrap_path_payload(payload: Any) -> Dict[str, Any]:
    if not isinstance(payload, dict):
        return {}
    train = payload.get("train")
    if isinstance(train, dict):
        return train
    nested = payload.get("payload")
    if isinstance(nested, dict):
        return _unwrap_path_payload(nested)
    return payload


def extract_path_runways(payload: Dict[str, Any]) -> List[str]:
    train = _unwrap_path_payload(payload)
    return normalize_run_days(train.get("rundays") or train.get("runways"))


def _mark_cached_source(payload: Any) -> Any:
    """Label legacy rows in memory without rewriting the user's database."""

    if not isinstance(payload, dict):
        return payload

    metadata = payload.get("_railgo")
    if not isinstance(metadata, dict):
        metadata = {
            "provider": "RailGo",
            "api_version": "v1-legacy",
            "endpoint": "local-cache",
        }
    else:
        metadata = dict(metadata)
    metadata["cached"] = True
    payload["_railgo"] = metadata
    return payload


class RailStore:
    """Thread-aware, backward-compatible RailGo cache."""

    def __init__(self, db_path: str = "rail_store.db"):
        self.db_path = writable_path(db_path)
        self._local = threading.local()
        self._write_lock = threading.RLock()
        self._connections_lock = threading.RLock()
        self._connections: set[sqlite3.Connection] = set()
        self.last_error: Optional[str] = None
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self._init_tables()

    def _new_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(
            self.db_path,
            timeout=_BUSY_TIMEOUT_MS / 1000,
            check_same_thread=False,
        )
        conn.row_factory = sqlite3.Row
        conn.execute(f"PRAGMA busy_timeout={_BUSY_TIMEOUT_MS}")
        with self._connections_lock:
            self._connections.add(conn)
        return conn

    def _get_conn(self) -> sqlite3.Connection:
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = self._new_connection()
            self._local.conn = conn
        return conn

    def _init_tables(self) -> None:
        conn = self._new_connection()
        try:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS s2s_cache (
                    id TEXT PRIMARY KEY,
                    data TEXT NOT NULL,
                    runways TEXT,
                    valid_from TEXT,
                    valid_to TEXT,
                    updated_at TEXT
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS path_cache (
                    id TEXT PRIMARY KEY,
                    data TEXT NOT NULL,
                    runways TEXT,
                    valid_from TEXT,
                    valid_to TEXT,
                    updated_at TEXT
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS station_cache (
                    id TEXT PRIMARY KEY,
                    data TEXT NOT NULL,
                    updated_at TEXT
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS coach_train_binding (
                    train_no TEXT PRIMARY KEY,
                    car_code TEXT NOT NULL,
                    observed_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    source_json TEXT
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS coach_asset (
                    car_code TEXT PRIMARY KEY,
                    car_type TEXT,
                    train_style TEXT,
                    car_info_json TEXT NOT NULL,
                    coach_catalog_json TEXT NOT NULL,
                    detail_catalog_json TEXT NOT NULL,
                    structural_fingerprint TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS coach_media_locator (
                    car_code TEXT NOT NULL,
                    media_kind TEXT NOT NULL,
                    selector TEXT NOT NULL,
                    remote_url TEXT NOT NULL,
                    observed_at TEXT NOT NULL,
                    PRIMARY KEY (car_code, media_kind, selector)
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS coach_media_asset (
                    car_code TEXT NOT NULL,
                    media_kind TEXT NOT NULL,
                    selector TEXT NOT NULL,
                    content_hash TEXT NOT NULL UNIQUE,
                    mime_type TEXT NOT NULL,
                    local_path TEXT NOT NULL,
                    downloaded_at TEXT NOT NULL,
                    PRIMARY KEY (car_code, media_kind, selector)
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS map_line_cache (
                    train_no TEXT PRIMARY KEY,
                    certificate_quarter TEXT NOT NULL,
                    path_fingerprint TEXT,
                    content_hash TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    source_json TEXT
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS route_asset (
                    content_hash TEXT PRIMARY KEY,
                    geojson_json TEXT NOT NULL,
                    summary_json TEXT NOT NULL,
                    raw_metadata_json TEXT NOT NULL,
                    fallback_svg TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS railgo_operational_cache (
                    object TEXT NOT NULL,
                    cache_key TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    payload_hash TEXT NOT NULL,
                    service_date TEXT NOT NULL,
                    fetched_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    source_json TEXT,
                    schema_version INTEGER NOT NULL,
                    PRIMARY KEY (object, cache_key)
                )
                """
            )
            conn.commit()
        finally:
            conn.close()
            with self._connections_lock:
                self._connections.discard(conn)

    def _record_error(self, context: str, exc: Exception) -> None:
        self.last_error = f"{context}: {exc}"

    def _load_payload(self, table: str, key: str) -> Any | None:
        if table not in _CACHE_TABLES:
            return None
        try:
            row = self._get_conn().execute(
                f"SELECT data FROM {table} WHERE id=?", (key,)
            ).fetchone()
            if not row:
                return None
            return _mark_cached_source(json.loads(row["data"]))
        except (sqlite3.Error, json.JSONDecodeError, TypeError) as exc:
            self._record_error(f"read {table}:{key}", exc)
            return None

    def _save_payload(
        self,
        table: str,
        key: str,
        payload: Any,
        runways: Optional[List[str]] = None,
    ) -> bool:
        now = datetime.now().isoformat(timespec="seconds")
        try:
            encoded = json.dumps(payload, ensure_ascii=False)
        except (TypeError, ValueError) as exc:
            self._record_error(f"encode {table}:{key}", exc)
            return False

        columns = "id, data, updated_at"
        placeholders = "?, ?, ?"
        values: tuple[Any, ...] = (key, encoded, now)
        if runways is not None:
            days = normalize_run_days(runways)
            cert = extract_valid_range(days)
            columns = "id, data, runways, valid_from, valid_to, updated_at"
            placeholders = "?, ?, ?, ?, ?, ?"
            values = (
                key,
                encoded,
                json.dumps(days, ensure_ascii=False),
                cert["valid_from"] if cert else None,
                cert["valid_to"] if cert else None,
                now,
            )

        with self._write_lock:
            conn = self._get_conn()
            try:
                conn.execute(
                    f"INSERT OR REPLACE INTO {table} ({columns}) VALUES ({placeholders})",
                    values,
                )
                conn.commit()
                self.last_error = None
                return True
            except sqlite3.Error as exc:
                conn.rollback()
                self._record_error(f"write {table}:{key}", exc)
                return False

    def save_s2s(self, key: str, payload: Any) -> bool:
        return self._save_payload("s2s_cache", key, payload, extract_s2s_runways(payload))

    def get_s2s(self, key: str) -> Any | None:
        return self._load_payload("s2s_cache", key)

    def save_path(self, train_id: str, payload: Dict[str, Any]) -> bool:
        train_id = str(train_id or "").strip().upper()
        return self._save_payload(
            "path_cache", train_id, payload, extract_path_runways(payload)
        )

    def get_path(self, train_id: str) -> Optional[Dict[str, Any]]:
        train_id = str(train_id or "").strip().upper()
        payload = self._load_payload("path_cache", train_id)
        return payload if isinstance(payload, dict) else None

    def save_station(self, station_name: str, payload: Dict[str, Any]) -> bool:
        station_name = str(station_name or "").strip().upper()
        return self._save_payload("station_cache", station_name, payload)

    def get_station(self, station_name: str) -> Optional[Dict[str, Any]]:
        station_name = str(station_name or "").strip().upper()
        payload = self._load_payload("station_cache", station_name)
        return payload if isinstance(payload, dict) else None

    @staticmethod
    def _operational_payload_hash(payload: Any) -> str:
        canonical = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def get_operational_cache(
        self,
        object_name: str,
        cache_key: str,
        *,
        now: datetime,
    ) -> Dict[str, Any]:
        """Read and authenticate one short-lived RailGo operational row."""

        object_name = str(object_name or "").strip()
        cache_key = str(cache_key or "").strip()
        try:
            row = self._get_conn().execute(
                "SELECT * FROM railgo_operational_cache WHERE object=? AND cache_key=?",
                (object_name, cache_key),
            ).fetchone()
        except sqlite3.Error as exc:
            self._record_error(f"read operational cache:{object_name}:{cache_key}", exc)
            return {
                "exists": False,
                "cert_valid": False,
                "need_refresh": True,
                "error": str(exc),
            }

        if row is None:
            return {"exists": False, "cert_valid": False, "need_refresh": True}

        result = dict(row)
        try:
            payload = json.loads(result["payload_json"])
            source = json.loads(result.get("source_json") or "{}")
            expected_hash = self._operational_payload_hash(payload)
            fetched_at = datetime.fromisoformat(result["fetched_at"])
            expires_at = datetime.fromisoformat(result["expires_at"])
            if fetched_at.tzinfo is None or expires_at.tzinfo is None or now.tzinfo is None:
                raise ValueError("operational cache timestamps must be timezone-aware")
            if int(result.get("schema_version") or 0) != _OPERATIONAL_CACHE_SCHEMA_VERSION:
                raise ValueError("unsupported operational cache schema")
            if not isinstance(payload, dict) or not isinstance(source, dict):
                raise ValueError("invalid operational cache JSON contract")
            if expected_hash != str(result.get("payload_hash") or ""):
                raise ValueError("operational cache payload hash mismatch")
        except (json.JSONDecodeError, TypeError, ValueError, KeyError) as exc:
            self._record_error(f"authenticate operational cache:{object_name}:{cache_key}", exc)
            return {
                "exists": True,
                "cert_valid": False,
                "need_refresh": True,
                "rejected": True,
                "error": str(exc),
            }

        cert_valid = now < expires_at
        return {
            "exists": True,
            "cert_valid": cert_valid,
            "need_refresh": not cert_valid,
            "payload": payload if cert_valid else None,
            "source": source,
            "service_date": result["service_date"],
            "fetched_at": fetched_at.isoformat(timespec="seconds"),
            "expires_at": expires_at.isoformat(timespec="seconds"),
            "age_seconds": max(0, int((now - fetched_at).total_seconds())),
        }

    def save_operational_cache(
        self,
        object_name: str,
        cache_key: str,
        payload: Dict[str, Any],
        *,
        service_date: str,
        fetched_at: datetime,
        expires_at: datetime,
        source: Dict[str, Any] | None = None,
    ) -> bool:
        """Write one authenticated operational cache certificate."""

        if fetched_at.tzinfo is None or expires_at.tzinfo is None:
            self._record_error(
                f"write operational cache:{object_name}:{cache_key}",
                ValueError("timestamps must be timezone-aware"),
            )
            return False
        try:
            payload_json = json.dumps(payload, ensure_ascii=False, sort_keys=True)
            source_json = json.dumps(source or {}, ensure_ascii=False, sort_keys=True)
            payload_hash = self._operational_payload_hash(payload)
        except (TypeError, ValueError) as exc:
            self._record_error(f"encode operational cache:{object_name}:{cache_key}", exc)
            return False

        values = (
            str(object_name or "").strip(),
            str(cache_key or "").strip(),
            payload_json,
            payload_hash,
            str(service_date or "").strip(),
            fetched_at.isoformat(timespec="seconds"),
            expires_at.isoformat(timespec="seconds"),
            source_json,
            _OPERATIONAL_CACHE_SCHEMA_VERSION,
        )
        with self._write_lock:
            conn = self._get_conn()
            try:
                conn.execute(
                    "INSERT OR REPLACE INTO railgo_operational_cache "
                    "(object,cache_key,payload_json,payload_hash,service_date,fetched_at,"
                    "expires_at,source_json,schema_version) VALUES (?,?,?,?,?,?,?,?,?)",
                    values,
                )
                conn.commit()
                self.last_error = None
                return True
            except sqlite3.Error as exc:
                conn.rollback()
                self._record_error(f"write operational cache:{object_name}:{cache_key}", exc)
                return False

    def prune_operational_cache(self, *, older_than: datetime) -> int:
        """Delete certificates that have been expired for the retention window."""

        if older_than.tzinfo is None:
            return 0
        with self._write_lock:
            conn = self._get_conn()
            try:
                cursor = conn.execute(
                    "DELETE FROM railgo_operational_cache WHERE expires_at < ?",
                    (older_than.isoformat(timespec="seconds"),),
                )
                conn.commit()
                return max(0, int(cursor.rowcount or 0))
            except sqlite3.Error as exc:
                conn.rollback()
                self._record_error("prune operational cache", exc)
                return 0

    def check_validity(
        self, table: str, key: str, date: Optional[str] = None
    ) -> Dict[str, Any]:
        """Check whether a local certificate covers the requested date."""

        if table not in _CACHE_TABLES:
            return {
                "exists": False,
                "cert_valid": False,
                "need_refresh": False,
                "error": "unsupported_cache_table",
            }

        try:
            query_date = normalize_cache_date(date or today_yyyymmdd())
        except ValueError as exc:
            return {
                "exists": False,
                "cert_valid": False,
                "need_refresh": False,
                "error": str(exc),
            }

        try:
            row = self._get_conn().execute(
                f"SELECT * FROM {table} WHERE id=?", (key,)
            ).fetchone()
        except sqlite3.Error as exc:
            self._record_error(f"validity {table}:{key}", exc)
            return {
                "exists": False,
                "cert_valid": False,
                "need_refresh": True,
                "error": str(exc),
            }

        if not row:
            return {"exists": False, "cert_valid": False, "need_refresh": True}

        if table == "station_cache":
            return {
                "exists": True,
                "cert_valid": True,
                "need_refresh": False,
                "updated_at": row["updated_at"],
            }

        try:
            runways = normalize_run_days(json.loads(row["runways"] or "[]"))
        except (json.JSONDecodeError, TypeError):
            runways = []

        # Old databases may contain a valid payload but an empty certificate
        # column. Recover it in memory instead of rewriting the bundled DB.
        if not runways:
            try:
                payload = json.loads(row["data"])
                if table == "s2s_cache":
                    runways = extract_s2s_runways(payload)
                else:
                    runways = extract_path_runways(payload)
            except (json.JSONDecodeError, TypeError):
                runways = []

        cert = extract_valid_range(runways)
        valid_from = cert["valid_from"] if cert else None
        valid_to = cert["valid_to"] if cert else None
        cert_valid = bool(valid_from and valid_to and valid_from <= query_date <= valid_to)

        result: Dict[str, Any] = {
            "exists": True,
            "cert_valid": cert_valid,
            "need_refresh": not cert_valid,
            "valid_from": valid_from,
            "valid_to": valid_to,
            "certificate_days": len(runways),
            "updated_at": row["updated_at"],
        }
        if cert_valid:
            result["running_today"] = query_date in set(runways)
        return result

    @staticmethod
    def _decode_row_json(row: sqlite3.Row | None, *fields: str) -> Dict[str, Any] | None:
        if row is None:
            return None
        result = dict(row)
        for field in fields:
            try:
                result[field] = json.loads(result.get(field) or "null")
            except (json.JSONDecodeError, TypeError):
                result[field] = None
        return result

    def get_coach_binding(self, train_no: str) -> Dict[str, Any] | None:
        train_no = str(train_no or "").strip().upper()
        try:
            row = self._get_conn().execute(
                "SELECT * FROM coach_train_binding WHERE train_no=?", (train_no,)
            ).fetchone()
            result = self._decode_row_json(row, "source_json")
            if result:
                now = datetime.now()
                observed = datetime.fromisoformat(result["observed_at"])
                expires = datetime.fromisoformat(result["expires_at"])
                result["fresh"] = now <= expires
                result["stale_usable"] = now - observed <= timedelta(days=7)
            return result
        except (sqlite3.Error, ValueError, TypeError) as exc:
            self._record_error(f"read coach binding:{train_no}", exc)
            return None

    def save_coach_binding(
        self, train_no: str, car_code: str, source: Dict[str, Any] | None = None
    ) -> bool:
        now = datetime.now()
        values = (
            str(train_no or "").strip().upper(),
            str(car_code or "").strip().upper(),
            now.isoformat(timespec="seconds"),
            (now + timedelta(hours=24)).isoformat(timespec="seconds"),
            json.dumps(source or {}, ensure_ascii=False),
        )
        with self._write_lock:
            conn = self._get_conn()
            try:
                conn.execute(
                    "INSERT OR REPLACE INTO coach_train_binding "
                    "(train_no,car_code,observed_at,expires_at,source_json) VALUES (?,?,?,?,?)",
                    values,
                )
                conn.commit()
                return True
            except sqlite3.Error as exc:
                conn.rollback()
                self._record_error(f"write coach binding:{train_no}", exc)
                return False

    def get_coach_asset(self, car_code: str) -> Dict[str, Any] | None:
        try:
            row = self._get_conn().execute(
                "SELECT * FROM coach_asset WHERE car_code=?",
                (str(car_code or "").strip().upper(),),
            ).fetchone()
            return self._decode_row_json(
                row, "car_info_json", "coach_catalog_json", "detail_catalog_json"
            )
        except sqlite3.Error as exc:
            self._record_error(f"read coach asset:{car_code}", exc)
            return None

    def save_coach_asset(self, asset: Dict[str, Any]) -> str:
        """Insert an immutable coach asset and report inserted/existing/conflict."""

        car_code = str(asset.get("car_code") or "").strip().upper()
        fingerprint = str(asset.get("structural_fingerprint") or "")
        existing = self.get_coach_asset(car_code)
        if existing:
            return "existing" if existing.get("structural_fingerprint") == fingerprint else "conflict"
        values = (
            car_code,
            asset.get("car_type"),
            asset.get("train_style"),
            json.dumps(asset.get("car_info") or [], ensure_ascii=False),
            json.dumps(asset.get("coach_catalog") or [], ensure_ascii=False),
            json.dumps(asset.get("detail_catalog") or [], ensure_ascii=False),
            fingerprint,
            datetime.now().isoformat(timespec="seconds"),
        )
        with self._write_lock:
            conn = self._get_conn()
            try:
                conn.execute(
                    "INSERT OR IGNORE INTO coach_asset "
                    "(car_code,car_type,train_style,car_info_json,coach_catalog_json,"
                    "detail_catalog_json,structural_fingerprint,created_at) VALUES (?,?,?,?,?,?,?,?)",
                    values,
                )
                conn.commit()
            except sqlite3.Error as exc:
                conn.rollback()
                self._record_error(f"write coach asset:{car_code}", exc)
                return "error"
        stored = self.get_coach_asset(car_code)
        if stored and stored.get("structural_fingerprint") == fingerprint:
            return "inserted"
        return "conflict"

    def save_coach_media_locators(self, car_code: str, locators: List[Dict[str, Any]]) -> bool:
        now = datetime.now().isoformat(timespec="seconds")
        rows = [
            (
                str(car_code or "").strip().upper(),
                str(item.get("media_kind") or ""),
                str(item.get("selector") or "default"),
                str(item.get("remote_url") or ""),
                now,
            )
            for item in locators
            if item.get("remote_url")
        ]
        if not rows:
            return True
        with self._write_lock:
            conn = self._get_conn()
            try:
                conn.executemany(
                    "INSERT OR REPLACE INTO coach_media_locator "
                    "(car_code,media_kind,selector,remote_url,observed_at) VALUES (?,?,?,?,?)",
                    rows,
                )
                conn.commit()
                return True
            except sqlite3.Error as exc:
                conn.rollback()
                self._record_error(f"write coach media locators:{car_code}", exc)
                return False

    def get_coach_media_locator(
        self, car_code: str, media_kind: str, selector: str
    ) -> Dict[str, Any] | None:
        try:
            row = self._get_conn().execute(
                "SELECT * FROM coach_media_locator WHERE car_code=? AND media_kind=? AND selector=?",
                (str(car_code).upper(), media_kind, selector),
            ).fetchone()
            return dict(row) if row else None
        except sqlite3.Error as exc:
            self._record_error("read coach media locator", exc)
            return None

    def list_coach_media_locators(self, car_code: str) -> List[Dict[str, Any]]:
        try:
            rows = self._get_conn().execute(
                "SELECT car_code,media_kind,selector,observed_at FROM coach_media_locator "
                "WHERE car_code=? ORDER BY media_kind,selector",
                (str(car_code).upper(),),
            ).fetchall()
            return [dict(row) for row in rows]
        except sqlite3.Error as exc:
            self._record_error("list coach media locators", exc)
            return []

    def save_coach_media_asset(self, asset: Dict[str, Any]) -> bool:
        values = (
            str(asset["car_code"]).upper(), asset["media_kind"], asset["selector"],
            asset["content_hash"], asset["mime_type"], asset["local_path"],
            datetime.now().isoformat(timespec="seconds"),
        )
        with self._write_lock:
            conn = self._get_conn()
            try:
                conn.execute(
                    "INSERT OR REPLACE INTO coach_media_asset "
                    "(car_code,media_kind,selector,content_hash,mime_type,local_path,downloaded_at) "
                    "VALUES (?,?,?,?,?,?,?)", values,
                )
                conn.commit()
                return True
            except sqlite3.Error as exc:
                conn.rollback()
                self._record_error("write coach media asset", exc)
                return False

    def get_coach_media_asset(
        self, car_code: str, media_kind: str, selector: str
    ) -> Dict[str, Any] | None:
        try:
            row = self._get_conn().execute(
                "SELECT * FROM coach_media_asset WHERE car_code=? AND media_kind=? AND selector=?",
                (str(car_code).upper(), media_kind, selector),
            ).fetchone()
            return dict(row) if row else None
        except sqlite3.Error as exc:
            self._record_error("read coach media asset", exc)
            return None

    def get_coach_media_by_hash(self, content_hash: str) -> Dict[str, Any] | None:
        try:
            row = self._get_conn().execute(
                "SELECT * FROM coach_media_asset WHERE content_hash=?", (content_hash,)
            ).fetchone()
            return dict(row) if row else None
        except sqlite3.Error as exc:
            self._record_error("read coach media hash", exc)
            return None

    def get_map_line_cache(self, train_no: str) -> Dict[str, Any] | None:
        try:
            row = self._get_conn().execute(
                "SELECT * FROM map_line_cache WHERE train_no=?", (str(train_no).upper(),)
            ).fetchone()
            return self._decode_row_json(row, "source_json")
        except sqlite3.Error as exc:
            self._record_error("read map line cache", exc)
            return None

    def save_route_asset(
        self,
        train_no: str,
        quarter: str,
        path_fingerprint: str,
        content_hash: str,
        geojson: Dict[str, Any],
        summary: Dict[str, Any],
        raw_metadata: Dict[str, Any],
        fallback_svg: str,
        source: Dict[str, Any] | None = None,
    ) -> bool:
        now = datetime.now().isoformat(timespec="seconds")
        with self._write_lock:
            conn = self._get_conn()
            try:
                conn.execute(
                    "INSERT OR IGNORE INTO route_asset "
                    "(content_hash,geojson_json,summary_json,raw_metadata_json,fallback_svg,created_at) "
                    "VALUES (?,?,?,?,?,?)",
                    (content_hash, json.dumps(geojson, ensure_ascii=False),
                     json.dumps(summary, ensure_ascii=False), json.dumps(raw_metadata, ensure_ascii=False),
                     fallback_svg, now),
                )
                conn.execute(
                    "INSERT OR REPLACE INTO map_line_cache "
                    "(train_no,certificate_quarter,path_fingerprint,content_hash,updated_at,source_json) "
                    "VALUES (?,?,?,?,?,?)",
                    (str(train_no).upper(), quarter, path_fingerprint, content_hash, now,
                     json.dumps(source or {}, ensure_ascii=False)),
                )
                conn.commit()
                return True
            except sqlite3.Error as exc:
                conn.rollback()
                self._record_error("write route asset", exc)
                return False

    def get_route_asset(self, content_hash: str) -> Dict[str, Any] | None:
        try:
            row = self._get_conn().execute(
                "SELECT * FROM route_asset WHERE content_hash=?", (content_hash,)
            ).fetchone()
            return self._decode_row_json(row, "geojson_json", "summary_json", "raw_metadata_json")
        except sqlite3.Error as exc:
            self._record_error("read route asset", exc)
            return None

    def close(self) -> None:
        conn = getattr(self._local, "conn", None)
        if conn is None:
            return
        conn.close()
        with self._connections_lock:
            self._connections.discard(conn)
        del self._local.conn

    def close_all(self) -> None:
        """Close connections opened by every executor worker."""

        with self._connections_lock:
            connections = list(self._connections)
            self._connections.clear()
        for conn in connections:
            try:
                conn.close()
            except sqlite3.Error:
                continue
        if hasattr(self._local, "conn"):
            del self._local.conn


railstore = RailStore()
