"""
planner_executor_local_test.py  (RailAI v2.6 Regression)

Goal:
- Cover every railway query tool once
- Prefer local cache only (no remote API)
- Use 京沪高铁 dataset (VNP/NKH/AOH/UUH)

Run:
    python planner_executor_local_test.py
"""

from datetime import datetime

# ============================
# Import Executor Core
# ============================

from agent.executor import Executor
from agent.psw import PSW

# ============================
# Safe Date (PAST, cache hit)
# ============================

SAFE_DATE = "20260217"   # Past → should always use RailStore cache


# ============================
# Helper: Run One Case
# ============================

def run_case(title: str, task: dict):

    print("\n" + "=" * 70)
    print(f"🧪 TEST CASE: {title}")
    print("=" * 70)

    exe = Executor(psw=PSW())

    result = exe.execute(task)

    if not result:
        print("❌ Result = None")
        return

    print("\n✅ Unified Result:")
    print("- domain :", result.get("domain"))
    print("- object :", result.get("object"))
    print("- id     :", result.get("id"))
    print("- date   :", result.get("date"))

    print("\n📌 Pretty Evidence Output:")
    print(result.get("pretty", "⚠️ No pretty evidence."))


# ============================
# MAIN Regression Suite
# ============================

def main():

    print("\n==================================================")
    print("🚄 RailAI v2.6 Executor Local Regression Test Suite")
    print("==================================================")

    # =====================================================
    # 1) Train History Tool
    # =====================================================
    run_case(
        "query:train (京沪标杆 G1)",
        {
            "action": "query",
            "params": {
                "domain": "railway",
                "object": "train",
                "id": "G1"
            }
        }
    )

    # =====================================================
    # 2) EMU History Tool
    # =====================================================
    run_case(
        "query:emu (京沪 CR400BF sample)",
        {
            "action": "query",
            "params": {
                "domain": "railway",
                "object": "emu",
                "id": "AFBS"
            }
        }
    )

    # =====================================================
    # 3) Path Detail Tool
    # =====================================================
    run_case(
        "query:path_detail (G1 timetable evidence)",
        {
            "action": "query",
            "params": {
                "domain": "railway",
                "object": "path_detail",
                "id": "G1",
                "date": SAFE_DATE
            }
        }
    )

    # =====================================================
    # 4) Path StopCheck Tool (南京南悖论)
    # =====================================================
    run_case(
        "query:path_stopcheck (G1,G3|南京南)",
        {
            "action": "query",
            "params": {
                "domain": "railway",
                "object": "path_stopcheck",
                "id": "G1,G3,G5|南京南",
                "date": SAFE_DATE
            }
        }
    )

    # =====================================================
    # 5) Station Metadata Tool
    # =====================================================
    run_case(
        "query:station metadata (南京南 NKH)",
        {
            "action": "query",
            "params": {
                "domain": "railway",
                "object": "station",
                "id": "南京南"
            }
        }
    )

    # =====================================================
    # 6) Telecode Tool
    # =====================================================
    run_case(
        "query:telecode (南京南 → NKH)",
        {
            "action": "query",
            "params": {
                "domain": "railway",
                "object": "telecode",
                "id": "南京南"
            }
        }
    )

    # =====================================================
    # 7) Name Tool
    # =====================================================
    run_case(
        "query:name (NKH → 南京南)",
        {
            "action": "query",
            "params": {
                "domain": "railway",
                "object": "name",
                "id": "NKH"
            }
        }
    )

    # =====================================================
    # 8) Station-to-Station Detail Tool
    # =====================================================
    run_case(
        "query:s2s detail (北京南-上海虹桥)",
        {
            "action": "query",
            "params": {
                "domain": "railway",
                "object": "station_to_station_detail",
                "id": "北京南-上海虹桥",
                "date": SAFE_DATE
            }
        }
    )

    # =====================================================
    # 9) s2s Benchmark Tool
    # =====================================================
    run_case(
        "query:s2s benchmark directory (京沪标杆)",
        {
            "action": "query",
            "params": {
                "domain": "railway",
                "object": "s2s_benchmark",
                "id": "北京南-上海虹桥",
                "date": SAFE_DATE
            }
        }
    )

    # =====================================================
    # 10) 12306 LeftTicket Tool (cache-only expected)
    # =====================================================
    run_case(
        "query:left_ticket_s2s (北京南-上海虹桥)",
        {
            "action": "query",
            "params": {
                "domain": "railway",
                "object": "left_ticket_s2s",
                "id": "北京南-上海虹桥",
                "date": SAFE_DATE
            }
        }
    )

    # =====================================================
    # 11) 12306 Transfer Tool (cache-only expected)
    # =====================================================
    run_case(
        "query:transfer_12306 (北京南-上海虹桥)",
        {
            "action": "query",
            "params": {
                "domain": "railway",
                "object": "transfer_12306",
                "id": "北京南-上海虹桥",
                "date": SAFE_DATE
            }
        }
    )

    print("\n==================================================")
    print("✅ ALL TOOL REGRESSION CASES FINISHED")
    print("==================================================\n")


# ============================
# Entry
# ============================

if __name__ == "__main__":
    main()