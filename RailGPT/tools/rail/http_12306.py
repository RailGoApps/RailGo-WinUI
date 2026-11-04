"""
🚆 12306 Dedicated HTTP Client (Final Ultimate)

Features
-------------------------------------------------
✅ Global Session (TCP reuse)
✅ Cookie persistence (Session-wide)
✅ HTML Block detection
✅ Retry once with truncated exponential backoff
✅ Retry only on retryable errors
✅ Rate limit: max 5 live queries / minute
✅ PSW compatible status reporting
✅ JSON safe return
✅ getjson() convenience wrapper

Author:
- Student Project / RailAIagent (SEU)
"""

from __future__ import annotations

import time
import random
import threading
from typing import Dict, Any, Optional

import requests
from requests.adapters import HTTPAdapter


# ============================================================
# Default Headers (Polite + Explain Purpose)
# ============================================================

DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/121.0.0.0 Safari/537.36"
    ),
    "Accept": "*/*",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "Connection": "keep-alive",
    "Referer": "https://kyfw.12306.cn/otn/leftTicket/init?linktypeid=dc&fs=%E5%8C%97%E4%BA%AC,BJP&ts=%E4%B8%8A%E6%B5%B7,SHH&date=2026-02-18&flag=N,N,Y",
    "Origin": "https://kyfw.12306.cn",
}


# ============================================================
# PSW Helper (Optional)
# ============================================================

def _psw_emit(psw, state: str, msg: str = ""):
    """
    Compatible with your PSW.set_state(state, message)

    If psw is None → silent
    """
    if psw is None:
        return
    try:
        psw.set_state(state, msg)
    except Exception:
        pass


# ============================================================
# Global Shared Session (TCP KeepAlive + Connection Pool)
# ============================================================

_session = requests.Session()
_session.headers.update(DEFAULT_HEADERS)

_adapter = HTTPAdapter(
    pool_connections=8,
    pool_maxsize=8,
    max_retries=0,
)

_session.mount("https://", _adapter)
_session.mount("http://", _adapter)


# ============================================================
# Rate Limit Gate (Max 5/min)
# ============================================================

_rate_lock = threading.Lock()
_recent_calls: list[float] = []

MAX_CALLS_PER_MIN = 5
# ============================================================
# Throttle Gate (Min Interval)
# ============================================================

_min_interval_lock = threading.Lock()
_last_call_time = 0.0

MIN_INTERVAL = 0.3   # ⭐你要的 0.3s
def _throttle():
    """
    Ensure at least MIN_INTERVAL seconds between requests.
    Global across all threads.
    """
    global _last_call_time

    with _min_interval_lock:
        now = time.time()
        gap = now - _last_call_time

        if gap < MIN_INTERVAL:
            sleep_time = MIN_INTERVAL - gap
            time.sleep(sleep_time)

        _last_call_time = time.time()


def _allow_request() -> bool:
    """
    Sliding window rate limiter.
    Max 5 live queries per 60 seconds.
    """
    now = time.time()

    with _rate_lock:
        # drop expired timestamps
        while _recent_calls and now - _recent_calls[0] > 60:
            _recent_calls.pop(0)

        if len(_recent_calls) >= MAX_CALLS_PER_MIN:
            return False

        _recent_calls.append(now)
        return True


# ============================================================
# HTML Block Detection
# ============================================================

def _is_html_block(text: str) -> bool:
    """
    12306 风控常见：返回 HTML 而不是 JSON
    """
    head = text[:200].lower()
    return (
        "<html" in head
        or "<!doctype html" in head
        or "passport" in head
        or "access denied" in head
    )


# ============================================================
# Retryable Error Detector
# ============================================================

def _is_retryable_error(err: str) -> bool:
    """
    Only retry on errors that are likely temporary.
    """
    e = err.lower()

    return (
        "blocked_html" in e
        or "timed out" in e
        or "timeout" in e
        or "connection reset" in e
        or "connection aborted" in e
        or "remote end closed" in e
        or "502" in e
        or "503" in e
    )


# ============================================================
# Main GET Wrapper (12306 Ultimate)
# ============================================================

def http12306_get(
    url: str,
    params: Optional[Dict[str, Any]] = None,
    cookies: Optional[Dict[str, str]] = None,
    timeout: int = 12,
    retry: int = 1,
    psw=None,
) -> Dict[str, Any]:
    """
    Ultimate 12306 GET Request.

    Return format (unchanged):
    -------------------------------------------------
    Success:
    {
        "ok": True,
        "status": 200,
        "json": {...}
    }

    Failure:
    {
        "ok": False,
        "error": "...",
        "preview": "...",
        "status": ...
    }
    """

    # ======================================================
    # 0) Rate Limit Gate
    # ======================================================

    if not _allow_request():
        _psw_emit(psw, "T12306_RATE_LIMIT", "max 5 queries/min")

        return {
            "ok": False,
            "error": "rate_limited",
            "status": None,
            "preview": "Too many requests (>5/min)."
        }
    _throttle()
    # ======================================================
    # 1) Try request (with retry loop)
    # ======================================================

    for attempt in range(retry + 1):

        try:
            if attempt == 0:
                _psw_emit(psw, "T12306_QUERYING", "sending request...")
            else:
                _psw_emit(psw, "T12306_RETRYING", f"retry {attempt}...")

            resp = _session.get(
                url,
                params=params,
                cookies=cookies,
                timeout=timeout,
                verify=True,
            )

            text = resp.text.strip()

            # ==================================================
            # HTML Block Detection
            # ==================================================

            if _is_html_block(text):
                raise RuntimeError("blocked_html")

            # ==================================================
            # JSON Parse
            # ==================================================

            try:
                data = resp.json()
            except Exception:
                return {
                    "ok": False,
                    "error": "non_json",
                    "status": resp.status_code,
                    "preview": text[:300],
                }

            _psw_emit(psw, "T12306_DONE", "json received")

            return {
                "ok": True,
                "status": resp.status_code,
                "json": data,
            }

        except Exception as e:

            err = str(e)

            # ==================================================
            # Retry decision
            # ==================================================

            retryable = _is_retryable_error(err)

            # last attempt OR non-retryable → fail fast
            if attempt >= retry or not retryable:
                _psw_emit(psw, "T12306_ERROR", err)

                return {
                    "ok": False,
                    "error": err,
                    "status": None,
                    "preview": "Request failed after retry.",
                }

            # ==================================================
            # Truncated Binary Exponential Backoff
            # ==================================================

            backoff = 0.4 * (2 ** attempt)
            backoff += random.uniform(0, 0.2)
            backoff = min(backoff, 1.2)

            _psw_emit(psw, "T12306_BACKOFF", f"sleep {backoff:.2f}s")

            time.sleep(backoff)


# ============================================================
# Convenience Wrapper: getjson()
# ============================================================

def getjson(
    url: str,
    params: Optional[Dict[str, Any]] = None,
    cookies: Optional[Dict[str, str]] = None,
    psw=None,
) -> Dict[str, Any]:
    """
    Convenience wrapper:
    Always returns payload JSON or error dict.
    """

    res = http12306_get(url, params=params, cookies=cookies, psw=psw)

    if not res["ok"]:
        return {
            "error": res["error"],
            "preview": res.get("preview", "")
        }

    return res["json"]


# ============================================================
# Wrapper: QueryG LeftTicket
# ============================================================

def query_leftticket(
    date: str,
    dep: str,
    arr: str,
    purpose: str,
    psw=None,
) -> Dict[str, Any]:
    """
    Wrapper for:
    https://kyfw.12306.cn/otn/leftTicket/queryG
    """

    url = "https://kyfw.12306.cn/otn/leftTicket/queryG"

    params = {
        "leftTicketDTO.train_date": date,
        "leftTicketDTO.from_station": dep,
        "leftTicketDTO.to_station": arr,
        "purpose_codes": purpose,
    }

    cookies = {
        "_jc_save_fromStation": f"{dep}%2C{dep}",
        "_jc_save_toStation": f"{arr}%2C{arr}",
        "_jc_save_fromDate": date,
        "_jc_save_toDate": date,
        "_jc_save_wfdc_flag": "dc",
    }

    return http12306_get(url, params=params, cookies=cookies, psw=psw)