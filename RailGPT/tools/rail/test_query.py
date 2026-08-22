"""
test_pretty_s2s_fuzhou_xian.py

RailAI v2.6 — Pretty Format S2S Local Test

测试目标：
- 查询 福州 → 西安北
- 输出 pretty_format_s2s 完整文本（不截断站点）
- 确保 payload 结构兼容 dict/list
- 每次测试后 sleep 10s 防止 RailGo 压力过大
"""

import time
from datetime import datetime

from tools.rail.s2s_query import query_s2s_route, pretty_format_s2s


# =========================================================
# Local Test Main
# =========================================================

def main():
    print("\n==================================================")
    print("🚆 RailAI v2.6 — Pretty Format S2S Test Started")
    print("==================================================\n")

    # ✅测试路线：福州 → 西安北（车少）
    dep = "福州"
    arr = "西安北"

    # ✅测试日期：今天
    date = datetime.now().strftime("%Y%m%d")

    print("📌 TEST Route")
    print(f"Departure : {dep}")
    print(f"Arrival   : {arr}")
    print(f"Date      : {date}")
    print("--------------------------------------------------")

    # =====================================================
    # Step 1 — Query
    # =====================================================
    payload = query_s2s_route(dep, arr, date)

    if not payload:
        print("❌ s2s query failed or returned empty payload.")
        return

    print("✅ s2s query success.\n")

    # =====================================================
    # Step 2 — Pretty Format
    # =====================================================
    try:
        text = pretty_format_s2s(payload, date)
        print(text)

    except Exception as e:
        print("❌ Pretty format failed:")
        print(e)

    # =====================================================
    # Server Protection Sleep
    # =====================================================
    print("\n==================================================")
    print("✅ Pretty Test Finished")
    print("==================================================\n")

    print("⏳ Sleeping 10s to protect RailGo server...\n")
    time.sleep(10)


# =========================================================
# Run Entry
# =========================================================

if __name__ == "__main__":
    main()