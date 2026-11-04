# smartemu_analysis.py
"""
Smart EMU Analysis Tool (Probability Edition)
=============================================

Purpose
-------
Given a train number, fetch its recent EMU assignment history and infer
the most likely EMU family for upcoming assignments.

Railway-aware Conservative Rules
--------------------------------
1) Recent 3–5 assignments dominate (运用规律 > 全历史统计)
2) If last 3 are identical -> very stable, high confidence
3) Intelligent EMU ("BS") must appear in recent window to claim high probability
4) If assignments are chaotic -> fallback to dominant historical family with LOW confidence
5) Max 5 trains per request (防止LLM一次查爆API)

Outputs
-------
- Candidate family + probability distribution (Top3)
- Stability level (HIGH/MED/LOW)
- Railway notes & warnings
"""

from typing import List, Dict, Any, Optional, Tuple
from collections import Counter
import datetime
import re

# ============================================================
# 0) Real Fetch API (must exist in your project)
# ============================================================

try:
    from tools.rail.train_query import fetch_train_history, fetch_emu_history
except Exception as e:
    raise ImportError(
        "❌ smartemu_analysis requires tools.rail.emu_api.\n"
        "Please implement:\n"
        "  fetch_train_history(train_no:str)->List[records]\n"
        "Record format:\n"
        "  {date:'YYYY-MM-DD HH:MM', emu_no:'CR400AFBS2344', train_no:'G7'}\n"
        f"Import error: {e}"
    )


# ============================================================
# 1) Railway Knowledge Base (丰富车型知识)
# ============================================================

EMU_FAMILY_NOTES = {
    # ---- Fuxing Hao ----
    "CR400AF": "复兴号标准型 (8编组, 350km/h)",
    "CR400BF": "复兴号标准型 (8编组, 350km/h)",
    "CR400AFBS": "智能复兴号 (BS 智能, 17编组)",
    "CR400AFBZ": "智能复兴号 (BZ 智能, 17编组)",
    "CR400BFBZ": "智能复兴号 (BZ 智能, 17编组)",
    "CR400BFBS": "智能复兴号 (BS 智能, 17编组)",
    "CR400BFZ": "复兴号技术提升型 (Z 系,8编组, 350km/h)",
    "CR400AFZ": "复兴号技术提升型 (Z 系,8编组, 350km/h)",
    "CR400BFS": "复兴号技术提升型 (S 系,8编组, 350km/h)",
    "CR400AFS": "复兴号技术提升型 (S 系,8编组, 350km/h)",

    # ---- CR300 ----
    "CR300AF": "CR300 新型动车组 (300km/h, 8编组)",
    "CR300BF": "CR300 新型动车组 (300km/h, 8编组)",

    # ---- Hexie Hao ----
    "CRH380A": "和谐号 CRH380A (8编组, 350)",
    "CRH380B": "和谐号 CRH380B (8编组, 350)",
    "CRH380C": "和谐号 CRH380C (8编组, 350)",
    "CRH380D": "和谐号 CRH380D (8编组, 350)",
    "CRH380AL": "CRH380A 长编组 (16节, L=Long)",
    "CRH380BL": "CRH380B 长编组 (16节, L=Long)",

    # ---- CRH1/2/3/5 ----
    "CRH1A": "CRH1A (8编组, 南方常见)",
    "CRH1AA": "CRH1A-A (16编组)",
    "CRH2A": "CRH2A (8编组)",
    "CRH2B": "CRH2B (16编组)",
    "CRH3C": "CRH3C (8编组, 城际经典)",
    "CRH5A": "CRH5A (8编组, 东北常见)",
}


# ============================================================
# 2) Helpers: Parse EMU -> Family
# ============================================================

def emu_family(emu_no: Optional[str]) -> str:
    """
    Normalize EMU number into family tag.
    Example:
      CR400AFBS2344 -> CR400AFBS
      CR400BFZ5223  -> CR400BFZ
    """
    if not emu_no:
        return "UNKNOWN"

    emu = emu_no.strip().upper()

    # Main pattern
    m = re.match(r"(CR\d{3,4}[A-Z]{1,4}(?:BS|Z|AL|BL)?)", emu)
    if m:
        return m.group(1)

    return emu[:8]


def is_intelligent(fam: str) -> bool:
    """BS indicates intelligent EMU."""
    return fam and "BS" in fam


def parse_date(date_str: str) -> datetime.datetime:
    """Robust datetime parser."""
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.datetime.strptime(date_str, fmt)
        except:
            continue
    return datetime.datetime.min


# ============================================================
# 3) Probability Engine (核心概率推断)
# ============================================================

def softmax(counter: Counter) -> List[Tuple[str, float]]:
    """
    Convert frequency counts into probability distribution.
    Simple normalized freq (not neural net).
    """
    total = sum(counter.values())
    if total == 0:
        return []
    return [(k, v / total) for k, v in counter.most_common()]


from collections import Counter, defaultdict
import math

# ============================================================
# Enhanced Predictor: Markov + Decay + Streak Boost
# ============================================================

def infer_probability(records):
    """
    Enhanced Railway Predictor:
    - Recency decay weighting
    - Streak boost
    - Markov transition modeling
    - Intelligent BS conservative guard
    """

    out = {
        "candidate": None,
        "confidence": "LOW",
        "prob_top3": [],
        "recent5": [],
        "stability": "unstable",
        "warning": None,
        "records_used": len(records),
    }

    if not records:
        out["warning"] = "no_history"
        return out

    # Sort newest-first
    recs = sorted(records, key=lambda r: parse_date(r.get("date", "")), reverse=True)
    fams = [emu_family(r.get("emu_no")) for r in recs]

    recent5 = fams[:5]
    out["recent5"] = recent5

    # ============================================================
    # 1) Recency Decay Weighting
    # ============================================================
    # newest record weight = 1.0
    # older record weight decays exponentially
    decay = 0.85

    weighted = Counter()
    for i, fam in enumerate(fams):
        w = decay ** i
        weighted[fam] += w

    # ============================================================
    # 2) Streak Boost (recent连续出现加权)
    # ============================================================
    streak_fam = recent5[0]
    streak_len = 1
    for x in recent5[1:]:
        if x == streak_fam:
            streak_len += 1
        else:
            break

    if streak_len >= 3:
        weighted[streak_fam] *= 1.8
        out["stability"] = "very_stable_streak"
        out["confidence"] = "HIGH"
    elif len(set(recent5)) <= 2:
        out["stability"] = "stable_recent5"
        out["confidence"] = "MEDIUM"
    else:
        out["stability"] = "unstable"
        out["confidence"] = "LOW"

    # ============================================================
    # 3) Markov Transition Model (交路轮换)
    # ============================================================

    transitions = defaultdict(Counter)

    # build transitions: fam[i] -> fam[i-1]
    for i in range(1, len(fams)):
        prev = fams[i]
        nxt = fams[i - 1]
        transitions[prev][nxt] += 1

    last = fams[0]
    markov_pool = transitions[last]

    # If Markov has signal, blend into weighted pool
    if markov_pool:
        for fam, cnt in markov_pool.items():
            weighted[fam] += 1.2 * cnt

    # ============================================================
    # 4) Convert to Probability
    # ============================================================

    total = sum(weighted.values())
    probs = [(fam, val / total) for fam, val in weighted.most_common()]

    out["prob_top3"] = probs[:3]
    candidate = probs[0][0]
    out["candidate"] = candidate

    # ============================================================
    # 5) Intelligent Guard
    # ============================================================

    if is_intelligent(candidate):
        if not any(is_intelligent(x) for x in recent5):
            out["warning"] = (
                "BS智能动车组未在最近5次出现，禁止高概率宣称智能车组。"
            )
            out["confidence"] = "LOW"

    return out


# ============================================================
# 4) Pretty Output
# ============================================================

def pretty_report(train_no: str, records: List[Dict[str, Any]], inf: Dict[str, Any]) -> str:
    lines = []
    lines.append("=================================================")
    lines.append(f"🚄 SmartEMU Probability Analysis — {train_no}")
    lines.append("=================================================")
    lines.append(f"Records used: {inf['records_used']}")
    lines.append("")

    cand = inf["candidate"]
    note = EMU_FAMILY_NOTES.get(cand, "未知车型族")
    lines.append(f"🎯 Most Likely Family: {cand}")
    lines.append(f"   Notes: {note}")
    lines.append("")

    lines.append(f"📊 Confidence: {inf['confidence']} ({inf['stability']})")
    if inf.get("warning"):
        lines.append(f"⚠️ Warning: {inf['warning']}")
    lines.append("")

    lines.append("🔝 Probability Pool (Top3):")
    for fam, p in inf["prob_top3"]:
        fam_note = EMU_FAMILY_NOTES.get(fam, "")
        lines.append(f"- {fam:<10}  {p*100:5.1f}%   {fam_note}")

    lines.append("")
    lines.append("🕒 Recent5 Assignments:")
    lines.append(" → ".join(inf["recent5"]))

    lines.append("")
    lines.append("⚠️ Railway Conservative Rules:")
    lines.append("- Recent 3–5 assignments dominate.")
    lines.append("- Intelligent BS cannot be claimed unless seen recently.")
    lines.append("- Chaotic history => LOW confidence fallback.")
    lines.append("=================================================")
    return "\n".join(lines)


# ============================================================
# 5) Tool Executor (最多5车次)
# ============================================================

class SmartEMUAnalyzer:

    def __init__(self, max_trains: int = 5):
        self.max_trains = max_trains

    def analyze_train(self, train_no: str) -> Dict[str, Any]:
        records = fetch_train_history(train_no)

        inf = infer_probability(records)
        pretty = pretty_report(train_no, records, inf)

        return {
            "train": train_no,
            "inference": inf,
            "pretty": pretty,
        }

    def execute(self, trains: List[str]) -> List[Dict[str, Any]]:
        """
        Execute analysis for at most 5 trains.
        """
        trains = trains[: self.max_trains]

        results = []
        for tn in trains:
            results.append(self.analyze_train(tn))

        return results


# ============================================================
# 6) Real Request Test (直接发起请求)
# ============================================================

if __name__ == "__main__":
    analyzer = SmartEMUAnalyzer(max_trains=5)

    # Real fetch test: this will call your API/database
    results = analyzer.execute(["G7", "G31"])

    for r in results:
        print(r["pretty"])