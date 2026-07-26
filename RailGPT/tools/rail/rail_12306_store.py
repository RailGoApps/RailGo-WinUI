# rail_12306_store.py
"""
Railgo-style 12306 Store (Ultimate Final Stable)

Core Principles
-------------------------------------------------
1. Cert cache independent from query cache
2. Cert validity = 6 hours (21600s)
3. Query key = api + date + dep + arr + kwargs
4. Cache stampede protection (inflight lock)
5. WAL + thread-safe write lock
6. PSW compatible (optional)
7. Rate Limit: max 5 live queries / 60s
8. Stale fallback when blocked

Return Format (unchanged)
-------------------------------------------------
{
  "source": "cache" | "live",
  "query_key": "...",
  "payload": {...}
}
"""

from __future__ import annotations

import sqlite3
import json
import time
import threading
from typing import Any, Dict, Optional, Callable

from app_runtime import writable_path


# ============================================================
# Query Key Builder
# ============================================================

def make_query_key(api: str, date: str, dep: str, arr: str, **kwargs) -> str:
    """
    Example:
      left_ticket|20260215|BJP|SHH|purpose=ADULT
    """
    parts = [api, date, dep, arr]

    for k in sorted(kwargs.keys()):
        parts.append(f"{k}={kwargs[k]}")

    return "|".join(parts)


# ============================================================
# Rail12306Store
# ============================================================

class Rail12306Store:

    CERT_VALID_SEC = 6 * 3600     # 6 hours
    RATE_LIMIT_MAX = 5           # max live queries
    RATE_LIMIT_WINDOW = 60       # per 60 seconds

    def __init__(self, db_path: str = "rail12306.db", psw: Optional[Any] = None):

        self.db_path = writable_path(db_path)

        # SQLite write lock
        self._write_lock = threading.Lock()

        # inflight locks per query key
        self._inflight: Dict[str, threading.Lock] = {}

        # rate limit history
        self._recent_requests: list[int] = []
        self._rate_lock = threading.Lock()
        self._cert_lock = threading.Lock()

        # optional PSW
        self.psw = psw

        self._init_db()

    # ============================================================
    # PSW Helper
    # ============================================================

    def _psw(self, state: str, msg: str = ""):
        """
        Safe PSW emit
        """
        if not self.psw:
            return
        try:
            self.psw.set_state(state, msg)
        except:
            pass

    # ============================================================
    # Rate Limit Gate
    # ============================================================

    def _allow_live_query(self) -> bool:
        """
        Allow at most RATE_LIMIT_MAX live queries per window.
        """
        now = int(time.time())

        with self._rate_lock:
            # drop expired timestamps
            self._recent_requests = [
                t for t in self._recent_requests
                if now - t < self.RATE_LIMIT_WINDOW
            ]

            if len(self._recent_requests) >= self.RATE_LIMIT_MAX:
                return False

            self._recent_requests.append(now)
            return True

    # ============================================================
    # DB Init
    # ============================================================

    def _init_db(self):

        conn = sqlite3.connect(self.db_path)
        cur = conn.cursor()

        # WAL mode
        cur.execute("PRAGMA journal_mode=WAL;")
        cur.execute("PRAGMA synchronous=NORMAL;")

        # cert cache
        cur.execute("""
        CREATE TABLE IF NOT EXISTS cert_cache (
            id INTEGER PRIMARY KEY,
            token TEXT,
            ts INTEGER
        )
        """)

        # query cache
        cur.execute("""
        CREATE TABLE IF NOT EXISTS query_cache (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            api TEXT,
            query_key TEXT,
            ts INTEGER,
            response TEXT
        )
        """)

        # raw log
        cur.execute("""
        CREATE TABLE IF NOT EXISTS rawlog (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            api TEXT,
            query_key TEXT,
            ts INTEGER,
            response TEXT
        )
        """)

        conn.commit()
        conn.close()

    # ============================================================
    # Cert Status
    # ============================================================

    def cert_status(self) -> Dict[str, Any]:
        """
        Return cert remaining lifetime.
        """
        conn = sqlite3.connect(self.db_path)
        cur = conn.cursor()

        cur.execute("""
        SELECT ts FROM cert_cache
        ORDER BY ts DESC LIMIT 1
        """)

        row = cur.fetchone()
        conn.close()

        now = int(time.time())

        if not row:
            return {"exists": False, "valid_left": 0}

        cert_ts = row[0]
        age = now - cert_ts
        valid_left = max(0, self.CERT_VALID_SEC - age)

        return {"exists": True, "valid_left": valid_left}

    # ============================================================
    # Query Cache Load
    # ============================================================

    def load_latest(self, api: str, query_key: str, max_age: int) -> Optional[Dict[str, Any]]:
        """
        Load cached response if not expired.
        """
        conn = sqlite3.connect(self.db_path)
        cur = conn.cursor()

        cur.execute("""
        SELECT response, ts
        FROM query_cache
        WHERE api=? AND query_key=?
        ORDER BY ts DESC
        LIMIT 1
        """, (api, query_key))

        row = cur.fetchone()
        conn.close()

        if not row:
            return None

        resp_text, ts = row
        now = int(time.time())

        if now - ts > max_age:
            return None

        return json.loads(resp_text)

    # ============================================================
    # Save Query
    # ============================================================

    def save_query(self, api: str, query_key: str, response: Dict[str, Any]):

        now = int(time.time())
        text = json.dumps(response, ensure_ascii=False)

        with self._write_lock:

            conn = sqlite3.connect(self.db_path)
            cur = conn.cursor()

            cur.execute("""
            INSERT INTO query_cache (api, query_key, ts, response)
            VALUES (?, ?, ?, ?)
            """, (api, query_key, now, text))

            cur.execute("""
            INSERT INTO rawlog (api, query_key, ts, response)
            VALUES (?, ?, ?, ?)
            """, (api, query_key, now, text))

            conn.commit()
            conn.close()

    # ============================================================
    # Cached Query Wrapper (Ultimate)
    # ============================================================

    def cached_query(
            self,
            api: str,
            date: str,
            dep: str,
            arr: str,
            fetch_func: Callable[..., Dict[str, Any]],
            cache_ttl: int = 300,
            **kwargs
    ) -> Dict[str, Any]:

        key = make_query_key(api, date, dep, arr, **kwargs)

        lock = self._inflight.setdefault(key, threading.Lock())

        with lock:

            # ======================================================
            # 1) Cache Hit (no cert needed)
            # ======================================================
            cached = self.load_latest(api, key, max_age=cache_ttl)
            if cached:
                self._psw("T12306_CACHE_HIT", key)
                return {
                    "source": "cache",
                    "query_key": key,
                    "payload": cached
                }

            # ======================================================
            # 2) Stale Backup
            # ======================================================
            stale = self.load_latest(api, key, max_age=999999999)

            # ======================================================
            # 3) Cert Gate → refresh if expired
            # ======================================================
            cert = self.cert_status()
            valid_left = cert["valid_left"]

            if valid_left <= 0:

                self._psw("T12306_CERT_EXPIRED", "refreshing...")

                with self._cert_lock:
                    self.refresh_cert()

                cert = self.cert_status()
                valid_left = cert["valid_left"]

                if valid_left <= 0:
                    self._psw("T12306_CERT_REFRESH_FAILED", key)

                    if stale:
                        return {
                            "source": "cache",
                            "query_key": key,
                            "payload": stale
                        }

                    return {
                        "source": "cache",
                        "query_key": key,
                        "payload": {
                            "error": "cert_refresh_failed",
                            "msg": "Cert refresh failed. Live query unavailable."
                        }
                    }

            # ======================================================
            # 4) Rate Limit
            # ======================================================
            if not self._allow_live_query():

                self._psw("T12306_RATE_LIMIT", "fallback stale")

                if stale:
                    return {
                        "source": "cache",
                        "query_key": key,
                        "payload": stale
                    }

                return {
                    "source": "cache",
                    "query_key": key,
                    "payload": {
                        "error": "rate_limited",
                        "msg": "Too many live queries."
                    }
                }

            # ======================================================
            # 5) Live Query
            # ======================================================
            self._psw("T12306_QUERYING", f"{dep}->{arr} {date}")

            live = fetch_func(date=date, dep=dep, arr=arr, **kwargs)

            self.save_query(api, key, live)

            self._psw("T12306_DONE", "saved")

            return {
                "source": "live",
                "query_key": key,
                "payload": live
            }

    def save_cert(self, token: str):

        now = int(time.time())

        with self._write_lock:
            conn = sqlite3.connect(self.db_path)
            cur = conn.cursor()

            cur.execute("""
                        INSERT INTO cert_cache (token, ts)
                        VALUES (?, ?)
                        """, (token, now))

            conn.commit()
            conn.close()

    def refresh_cert(self):

        token = f"TOKEN_{int(time.time())}"

        self.save_cert(token)

        self._psw("T12306_CERT_UPDATED", token)

# ============================================================
# Local Test
# ============================================================

if __name__ == "__main__":

    print("==============================================")
    print("🚆 Rail12306Store Local Test (Ultimate)")
    print("==============================================")

    store = Rail12306Store("rail12306.db")

    def fake_query(date, dep, arr, **kw):
        print("LIVE QUERY:", date, dep, arr, kw)
        return {"ok": True, "ts": int(time.time())}

    r1 = store.cached_query(
        api="left_ticket",
        date="20260215",
        dep="BJP",
        arr="SHH",
        fetch_func=fake_query,
        cache_ttl=60,
        purpose="ADULT"
    )

    print("Result1:", r1["source"])

    r2 = store.cached_query(
        api="left_ticket",
        date="20260215",
        dep="BJP",
        arr="SHH",
        fetch_func=fake_query,
        cache_ttl=60,
        purpose="ADULT"
    )

    print("Result2:", r2["source"])
