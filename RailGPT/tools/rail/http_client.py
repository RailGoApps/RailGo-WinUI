# tools/rail/http_client.py

import requests
import threading
import time
from requests.adapters import HTTPAdapter
from tools.rail.client_identity import get_installation_id


DEFAULT_HEADERS = {
    "User-Agent": "RailGPT/2.6.6 (+https://github.com/EasonWheng/RailGPT)",
    "Accept": "application/json",
    "Accept-Encoding": "gzip, deflate",
    "X-Client-Name": "RailGPT",
    "X-Client-Version": "2.6.6",
    "X-Client-Info": "Open-source railway analysis agent; low-frequency educational use",
    "X-Project": "RailGPT",
    "X-Contact": "https://github.com/EasonWheng/RailGPT/issues",
}

# RailGo receives a stable, explicit identity on every v1/v2 and media request.
# Do not put user credentials, conversation ids or query text in these headers.
RAILGO_HEADERS = {
    **DEFAULT_HEADERS,
    "Referer": "https://github.com/EasonWheng/RailGPT",
    "X-Data-Purpose": "interactive railway query",
    "X-RailGPT-Installation-ID": get_installation_id(),
}

RAIL_RE_HEADERS = dict(DEFAULT_HEADERS)
# =====================================================
# Global Shared Session (2 TCP Connections Only)
# =====================================================

_session = requests.Session()
_session.headers.update(DEFAULT_HEADERS)

adapter = HTTPAdapter(
    pool_connections=2,
    pool_maxsize=2,
    max_retries=0
)

_session.mount("https://", adapter)
_session.mount("http://", adapter)


# =====================================================
# Global Polite Rate Limit
# =====================================================

_lock = threading.Lock()
_last_call = 0


def http_get(url: str, timeout=10, min_interval=0.15, headers=None, params=None):
    time.sleep(0.3)
    global _last_call

    with _lock:
        now = time.time()
        gap = now - _last_call
        if gap < min_interval:
            time.sleep(min_interval - gap)
        _last_call = time.time()

    # ⭐允许临时覆盖 headers
    if headers:
        return _session.get(url, timeout=timeout, headers=headers, params=params)

    return _session.get(url, timeout=timeout, params=params)
