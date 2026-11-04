from __future__ import annotations

import argparse
import json
import re
import statistics
import sys
import time
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

for stream in (sys.stdout, sys.stderr):
    reconfigure = getattr(stream, "reconfigure", None)
    if callable(reconfigure):
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

from app_runtime import prepare_runtime, writable_path


QUESTION_PATTERN = re.compile(r"^\s*\d+\.\s*(.+?)\s*$", flags=re.MULTILINE)
APOLOGY_TOKENS = ("抱歉", "很抱歉", "无法", "没法", "暂时", "查不到", "不能")
FOLLOWUP_TOKENS = ("告诉我", "再告诉我", "请告诉我", "补充", "从哪里出发", "到哪一站", "车次号")


class NullMemoryManager:
    def set_event_callback(self, _fn):
        return None

    def build_recall_bundle(self, _session, _user_text):
        return None

    def record_turn(self, **_kwargs):
        return None


def load_questions(repo_root: Path) -> tuple[Path, list[str]]:
    matches = sorted(repo_root.glob("*50问*.txt"))
    if not matches:
        raise FileNotFoundError("Could not find 火车迷50问.txt in the repository root.")

    question_file = matches[0]
    text = question_file.read_text(encoding="utf-8")
    questions = QUESTION_PATTERN.findall(text)
    if not questions:
        raise RuntimeError(f"No numbered questions found in {question_file}.")
    return question_file, questions


def build_app(mode: str):
    prepare_runtime()

    from agent.answer_generator import AnswerGenerator
    from agent.app import RailwayAgentApp
    from agent.executor import Executor
    from agent.planner import Planner
    from agent.psw import PSW
    from agent.router import Router
    from knowledge.railway_knowledge import RailwayKnowledgeRAG
    from llm.llm_client import LLMClient
    from memory.session import SessionMemory

    session = SessionMemory()
    router = Router(memory=session)
    planner = Planner()
    executor = Executor()
    psw = PSW()
    psw.enable_console = False

    llm = LLMClient(mode="fast-go")
    final_llm = LLMClient(mode="fast-go")
    answer_gen = AnswerGenerator(llm, knowledge_rag=RailwayKnowledgeRAG(), final_llm=final_llm)
    app = RailwayAgentApp(
        router=router,
        planner=planner,
        executor=executor,
        answer_gen=answer_gen,
        psw=psw,
        max_rounds=5,
        memory_manager=NullMemoryManager(),
    )
    app.set_mode(mode)

    # Disable the visual thinking sidecar so the evaluation reflects only the real app pipeline.
    psw.remove_listener(app._on_psw_event)
    app.thinking_engine.start_user_thinking = lambda _text: None
    app.thinking_engine.push_event = lambda _event: None

    route_capture: dict[str, Any] = {"tasks": []}
    original_route = router.route

    def wrapped_route(user_text, session):
        result = original_route(user_text, session)
        route_capture["tasks"] = result
        return result

    router.route = wrapped_route
    return app, psw, route_capture


def run_one_question(app, psw, route_capture: dict[str, Any], question: str, session_id: int):
    from memory.session import SessionMemory

    session = SessionMemory()
    session.session_id = session_id
    route_capture["tasks"] = []
    psw_events: list[dict[str, Any]] = []

    def listener(event):
        psw_events.append(dict(event))

    psw.add_listener(listener)

    start = time.perf_counter()
    answer_text = ""
    stream_text = ""
    error_text = ""
    try:
        for event in app._run_core_loop(question, session):
            event_type = event.get("type")
            if event_type == "token":
                stream_text += str(event.get("text") or "")
            elif event_type == "final":
                answer_text = str(event.get("text") or "")
    except Exception as exc:  # pragma: no cover - batch harness safety
        error_text = f"{type(exc).__name__}: {exc}"
    finally:
        psw.remove_listener(listener)

    elapsed_sec = round(time.perf_counter() - start, 3)
    final_text = answer_text or stream_text
    route_tasks = list(route_capture.get("tasks") or [])
    first_action = route_tasks[0].get("action") if route_tasks else "error"
    first_object = ""
    if route_tasks and isinstance(route_tasks[0], dict):
        first_object = str(route_tasks[0].get("params", {}).get("object") or "")

    compact_answer = re.sub(r"\s+", "", final_text)
    contains_apology = any(token in compact_answer for token in APOLOGY_TOKENS)
    contains_followup = any(token in compact_answer for token in FOLLOWUP_TOKENS)

    return {
        "question": question,
        "route_tasks": route_tasks,
        "first_action": first_action,
        "first_object": first_object,
        "answer": final_text,
        "elapsed_sec": elapsed_sec,
        "psw_events": psw_events,
        "contains_apology": contains_apology,
        "contains_followup": contains_followup,
        "error": error_text,
    }


def build_summary(results: list[dict[str, Any]], mode: str, question_file: Path) -> dict[str, Any]:
    action_counts = Counter(item.get("first_action") or "unknown" for item in results)
    object_counts = Counter(item.get("first_object") or "" for item in results if item.get("first_action") == "query")
    elapsed = [float(item.get("elapsed_sec") or 0.0) for item in results]

    suspicious = []
    for idx, item in enumerate(results, start=1):
        if item.get("error"):
            suspicious.append((idx, "error", item))
            continue
        if item.get("first_action") == "query" and item.get("contains_apology"):
            suspicious.append((idx, "query_but_apology", item))
            continue
        if item.get("first_action") == "pending":
            suspicious.append((idx, "pending", item))
            continue
        if len(str(item.get("answer") or "").strip()) < 24:
            suspicious.append((idx, "very_short", item))

    summary = {
        "mode": mode,
        "question_file": str(question_file),
        "question_count": len(results),
        "action_counts": dict(action_counts),
        "query_object_counts": dict(object_counts),
        "apology_count": sum(1 for item in results if item.get("contains_apology")),
        "followup_like_count": sum(1 for item in results if item.get("contains_followup")),
        "error_count": sum(1 for item in results if item.get("error")),
        "latency": {
            "avg_sec": round(sum(elapsed) / len(elapsed), 3) if elapsed else 0.0,
            "p50_sec": round(statistics.median(elapsed), 3) if elapsed else 0.0,
            "max_sec": round(max(elapsed), 3) if elapsed else 0.0,
        },
        "suspicious_cases": [
            {
                "index": idx,
                "flag": flag,
                "question": item["question"],
                "first_action": item["first_action"],
                "first_object": item["first_object"],
                "elapsed_sec": item["elapsed_sec"],
                "answer_excerpt": str(item.get("answer") or "")[:220],
                "error": item.get("error") or "",
            }
            for idx, flag, item in suspicious[:12]
        ],
    }
    return summary


def write_report(output_dir: Path, summary: dict[str, Any], results: list[dict[str, Any]]) -> tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe_mode = str(summary["mode"]).replace("-", "_")
    json_path = output_dir / f"railfan50_live_eval_{safe_mode}_{stamp}.json"
    md_path = output_dir / f"railfan50_live_eval_{safe_mode}_{stamp}.md"

    payload = {"summary": summary, "results": results}
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    lines = [
        f"# Railfan50 Live Eval ({summary['mode']})",
        "",
        f"- Question file: `{summary['question_file']}`",
        f"- Question count: `{summary['question_count']}`",
        f"- Action counts: `{summary['action_counts']}`",
        f"- Query object counts: `{summary['query_object_counts']}`",
        f"- Apology count: `{summary['apology_count']}`",
        f"- Followup-like count: `{summary['followup_like_count']}`",
        f"- Error count: `{summary['error_count']}`",
        (
            "- Latency: "
            f"avg `{summary['latency']['avg_sec']}s`, "
            f"p50 `{summary['latency']['p50_sec']}s`, "
            f"max `{summary['latency']['max_sec']}s`"
        ),
        "",
        "## Suspicious Cases",
        "",
    ]

    if not summary["suspicious_cases"]:
        lines.append("- None")
    else:
        for item in summary["suspicious_cases"]:
            lines.append(
                f"- Q{item['index']} [{item['flag']}] `{item['first_action']}/{item['first_object']}` "
                f"({item['elapsed_sec']}s): {item['question']}"
            )
            if item["answer_excerpt"]:
                lines.append(f"  - {item['answer_excerpt']}")
            if item["error"]:
                lines.append(f"  - ERROR: {item['error']}")

    lines.extend(["", "## Full Results", ""])
    for idx, item in enumerate(results, start=1):
        lines.append(f"### Q{idx}. {item['question']}")
        lines.append(f"- Route: `{item['first_action']}/{item['first_object']}`")
        lines.append(f"- Time: `{item['elapsed_sec']}s`")
        lines.append("- Answer:")
        lines.append("")
        lines.append(item.get("answer") or "(empty)")
        lines.append("")

    md_path.write_text("\n".join(lines), encoding="utf-8")
    return json_path, md_path


def main():
    parser = argparse.ArgumentParser(description="Run a live Railfan50 evaluation through the real app pipeline.")
    parser.add_argument("--mode", default="fast-go", choices=["fast-go", "fast-plus", "deep"])
    parser.add_argument("--limit", type=int, default=50)
    parser.add_argument("--output-dir", default="reports")
    args = parser.parse_args()

    repo_root = REPO_ROOT
    question_file, questions = load_questions(repo_root)
    questions = questions[: max(1, int(args.limit))]

    app, psw, route_capture = build_app(args.mode)

    results = []
    for idx, question in enumerate(questions, start=1):
        print(f"[{idx}/{len(questions)}] {question}")
        item = run_one_question(app, psw, route_capture, question, session_id=10_000 + idx)
        results.append(item)
        print(f"  -> {item['first_action']}/{item['first_object']} in {item['elapsed_sec']}s")

    summary = build_summary(results, args.mode, question_file)
    output_dir = repo_root / str(args.output_dir)
    json_path, md_path = write_report(output_dir, summary, results)

    print("\nSummary:")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print(f"\nJSON report: {json_path}")
    print(f"Markdown report: {md_path}")


if __name__ == "__main__":
    main()
