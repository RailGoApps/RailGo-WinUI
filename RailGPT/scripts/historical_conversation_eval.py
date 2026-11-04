from __future__ import annotations

import argparse
import contextlib
import io
import json
import statistics
import sys
import time
from unittest.mock import patch
from collections import Counter
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

for stream in (sys.stdout, sys.stderr):
    reconfigure = getattr(stream, "reconfigure", None)
    if callable(reconfigure):
        reconfigure(encoding="utf-8", errors="replace")


from agent.actions import is_valid_action, validate_action_params, validate_query_semantics
from agent.capabilities import capability_catalog_for_mode, get_capability
from agent.planner import Planner
from agent.router import Router
from llm.json_utils import loads_llm_json
from llm.llm_client import LLMClient
from memory.session import SessionMemory


ALLOWED_ACTIONS = {"chat", "query", "pending", "compare", "analyze", "summarize"}
DISABLED_PRESENTATION_OBJECTS = {"coach_layout", "train_route_map"}
PROMPT_LEAK_MARKERS = (
    "you are railgpt",
    "return json only",
    "system prompt",
    "mcp-style",
    "tool contract summary",
    "semantic router council",
)


class QuietPSW:
    def set_state(self, _state, _detail):
        return None


class BoundedLLMClient(LLMClient):
    """Keep a live audit moving when a legacy router fallback omits a timeout."""

    def generate(self, messages, timeout=None, max_retries=None):
        return super().generate(
            messages,
            timeout=20.0 if timeout is None else timeout,
            max_retries=0 if max_retries is None else max_retries,
        )


def _load_conversations(root: Path) -> list[dict[str, Any]]:
    conversations: list[dict[str, Any]] = []
    for path in sorted(root.glob("*.json"), key=lambda item: item.name):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        messages = payload.get("messages") if isinstance(payload, dict) else None
        if not isinstance(messages, list):
            continue
        if not any(isinstance(item, dict) and item.get("role") == "user" for item in messages):
            continue
        conversations.append(
            {
                "path": str(path),
                "id": payload.get("id"),
                "title": str(payload.get("title") or ""),
                "updated": str(payload.get("updated") or ""),
                "messages": messages,
            }
        )
    return conversations


def _task_summary(tasks: Iterable[dict[str, Any]]) -> str:
    parts: list[str] = []
    for task in tasks or []:
        action = str(task.get("action") or "")
        params = task.get("params") if isinstance(task.get("params"), dict) else {}
        if action == "query":
            parts.append(f"query/{params.get('object', '')}:{params.get('id', '')}")
        elif action == "pending":
            parts.append(f"pending[{','.join(params.get('slot') or [])}]:{params.get('question', '')}")
        else:
            parts.append(f"{action}:{params.get('message', '')}")
    return " | ".join(parts)


def _validate_turn(tasks: list[dict[str, Any]], envelope: dict[str, Any], planner: Planner) -> tuple[list[str], list[dict]]:
    issues: list[str] = []
    if not tasks:
        return ["router_returned_no_tasks"], []

    for task in tasks:
        if not isinstance(task, dict):
            issues.append("task_is_not_an_object")
            continue
        action = str(task.get("action") or "")
        params = task.get("params")
        if action not in ALLOWED_ACTIONS or not is_valid_action(action):
            issues.append(f"invalid_action:{action or '<empty>'}")
            continue
        if not isinstance(params, dict) or not validate_action_params(action, params):
            issues.append(f"invalid_action_params:{action}")
            continue
        if action == "query":
            if not validate_query_semantics(params):
                issues.append(f"invalid_query_semantics:{params.get('object', '')}")
            if str(params.get("object") or "") in DISABLED_PRESENTATION_OBJECTS:
                issues.append(f"disabled_capability_routed:{params.get('object')}")

        exposed_text = json.dumps(params, ensure_ascii=False).lower()
        if any(marker in exposed_text for marker in PROMPT_LEAK_MARKERS):
            issues.append(f"prompt_contract_leak:{action}")

    selected = str(envelope.get("selected_capability") or "")
    missing = [str(item) for item in envelope.get("missing_slots") or [] if str(item)]
    if any(task.get("action") == "query" for task in tasks) and missing:
        issues.append(f"query_has_missing_slots:{selected}:{','.join(missing)}")

    planned = planner.build_plan(json.loads(json.dumps(tasks, ensure_ascii=False)))
    if not planned:
        issues.append("planner_returned_no_tasks")
    if any(task.get("action") == "query" for task in tasks) and planned and planned[0].get("action") == "pending":
        issues.append("planner_rejected_router_query")

    return list(dict.fromkeys(issues)), planned


def _build_router(mode: str) -> Router:
    memory = SessionMemory()
    router = Router(memory)
    router.llm = BoundedLLMClient(mode=mode)
    router.psw = QuietPSW()
    router.set_mode(mode)
    return router


def run_conversation(conversation: dict[str, Any], mode: str) -> list[dict[str, Any]]:
    session = SessionMemory()
    session.session_id = conversation.get("id")
    router = _build_router(mode)
    planner = Planner()
    results: list[dict[str, Any]] = []
    user_index = 0

    for message in conversation["messages"]:
        if not isinstance(message, dict):
            continue
        role = str(message.get("role") or "")
        content = str(message.get("content") or "").strip()
        if not content:
            continue
        if role == "assistant":
            session.add_ai_message(content)
            continue
        if role != "user":
            continue

        user_index += 1
        # Match RailwayAgentApp._prepare_memory_for_turn: the current user turn
        # is present in working memory before routing, while history builders
        # explicitly exclude that final duplicate from LLM dialogue context.
        session.add_user_message(content)
        context_package = session.build_agent_context_package(mode=mode, user_text=content)
        started = time.perf_counter()
        route_error = ""
        tasks: list[dict[str, Any]] = []
        console_output = io.StringIO()
        try:
            replay_datetime = _replay_datetime_class(conversation.get("updated"))
            with contextlib.redirect_stdout(console_output), patch("agent.router.datetime", replay_datetime), patch(
                "agent.date_normalizer.datetime", replay_datetime
            ):
                tasks = router.route(content, session)
        except Exception as exc:  # pragma: no cover - live audit safety
            route_error = f"{type(exc).__name__}: {exc}"

        elapsed = round(time.perf_counter() - started, 3)
        envelope = router.get_last_intent_envelope() if hasattr(router, "get_last_intent_envelope") else {}
        issues, plan = _validate_turn(tasks, envelope, planner) if not route_error else (["router_exception"], [])
        if route_error:
            issues.append(route_error)

        results.append(
            {
                "conversation_id": conversation.get("id"),
                "conversation_title": conversation.get("title"),
                "conversation_path": conversation.get("path"),
                "user_turn_index": user_index,
                "user_text": content,
                "context_fingerprint": context_package.get("context_fingerprint", ""),
                "history_count": context_package.get("dialogue_history_count", 0),
                "dialogue_excerpt": context_package.get("dialogue_excerpt", ""),
                "tasks": tasks,
                "task_summary": _task_summary(tasks),
                "intent_envelope": envelope,
                "context_agent_state": dict(session.context_agent_state or {}),
                "semantic_council": (
                    dict(next(reversed(router._semantic_council_cache.values())))
                    if getattr(router, "_semantic_council_cache", None)
                    else {}
                ),
                "plan": plan,
                "issues": list(dict.fromkeys(issues)),
                "route_error": route_error,
                "elapsed_sec": elapsed,
                "semantic_judgement": {},
            }
        )
        session.update_from_tasks(tasks)
        first_task = tasks[0] if tasks and isinstance(tasks[0], dict) else {}
        if first_task.get("action") == "pending":
            params = first_task.get("params") if isinstance(first_task.get("params"), dict) else {}
            session.enter_followup(
                question=str(params.get("question") or "").strip(),
                slot=params.get("slot") or [],
                context=params.get("context") or {},
            )
        elif any(isinstance(task, dict) and task.get("action") == "query" for task in tasks):
            session.exit_followup()

    return results


def _replay_datetime_class(updated_at: Any):
    try:
        replay_now = datetime.fromisoformat(str(updated_at or "").strip())
    except (TypeError, ValueError):
        replay_now = datetime.now()

    class ReplayDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            if tz is None:
                return cls.fromtimestamp(replay_now.timestamp())
            return cls.fromtimestamp(replay_now.timestamp(), tz=tz)

    return ReplayDateTime


def _semantic_judge_prompt(batch: list[dict[str, Any]], mode: str) -> list[dict[str, str]]:
    cases = []
    for index, item in enumerate(batch):
        cases.append(
            {
                "case_id": index,
                "recent_dialogue": item.get("dialogue_excerpt", ""),
                "latest_user": item.get("user_text", ""),
                "router_result": item.get("task_summary", ""),
                "selected_capability": item.get("intent_envelope", {}).get("selected_capability", ""),
                "missing_slots": item.get("intent_envelope", {}).get("missing_slots", []),
            }
        )
    return [
        {
            "role": "system",
            "content": (
                "You are RailGPT Router QA Arbiter. Judge routing only, not the historical assistant answer. "
                "Professional railway requests covered by an available capability should use query; ordinary knowledge, "
                "social, creative, travel, explanation and reactions should use chat. Pending is valid only when the selected "
                "capability truly lacks a required user-provided slot. Defaults declared by the capability are not missing. "
                "A short follow-up must be interpreted using recent_dialogue. Do not invent facts. "
                "Requests for statistical or Markov analysis should still query supported assignment/path evidence first; "
                "the answer generator may perform analysis over those facts. Explicit train material inside a creative-writing request remains chat. "
                f"Available capability contracts:\n{capability_catalog_for_mode(mode)}\n\n"
                "Return JSON only as an array with one object per case: "
                "{case_id, verdict:'pass|fail|uncertain', severity:'p0|p1|p2|none', "
                "expected_action:'chat|query|pending', expected_capability:'', reason:'short Chinese reason'}."
            ),
        },
        {"role": "user", "content": json.dumps(cases, ensure_ascii=False)},
    ]


def judge_results(results: list[dict[str, Any]], mode: str, batch_size: int = 8) -> None:
    judge = LLMClient(mode="fast-plus", credential_slot="thinking")
    for start in range(0, len(results), batch_size):
        batch = results[start : start + batch_size]
        try:
            raw = judge.generate(_semantic_judge_prompt(batch, mode), timeout=60, max_retries=1)
            parsed = loads_llm_json(raw)
        except Exception as exc:  # pragma: no cover - live audit safety
            for item in batch:
                item["semantic_judgement"] = {
                    "verdict": "uncertain",
                    "severity": "none",
                    "reason": f"judge_error:{type(exc).__name__}",
                }
            continue
        if not isinstance(parsed, list):
            parsed = []
        by_id = {
            int(item.get("case_id")): item
            for item in parsed
            if isinstance(item, dict) and str(item.get("case_id", "")).isdigit()
        }
        for offset, result in enumerate(batch):
            judgement = by_id.get(offset, {})
            result["semantic_judgement"] = judgement if isinstance(judgement, dict) else {}


def _summary(results: list[dict[str, Any]], mode: str, conversations: list[dict[str, Any]]) -> dict[str, Any]:
    action_counts: Counter[str] = Counter()
    object_counts: Counter[str] = Counter()
    issue_counts: Counter[str] = Counter()
    verdict_counts: Counter[str] = Counter()
    severity_counts: Counter[str] = Counter()
    elapsed: list[float] = []

    for item in results:
        elapsed.append(float(item.get("elapsed_sec") or 0.0))
        tasks = item.get("tasks") or []
        first = tasks[0] if tasks else {}
        action = str(first.get("action") or "error")
        action_counts[action] += 1
        if action == "query":
            object_counts[str(first.get("params", {}).get("object") or "")] += 1
        for issue in item.get("issues") or []:
            issue_counts[str(issue).split(":", 1)[0]] += 1
        judgement = item.get("semantic_judgement") or {}
        verdict_counts[str(judgement.get("verdict") or "not_judged")] += 1
        severity_counts[str(judgement.get("severity") or "none")] += 1

    semantic_failures = [
        item
        for item in results
        if str(item.get("semantic_judgement", {}).get("verdict") or "") == "fail"
    ]
    structural_failures = [item for item in results if item.get("issues")]
    return {
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "mode": mode,
        "conversation_count": len(conversations),
        "user_turn_count": len(results),
        "unique_user_input_count": len({item["user_text"] for item in results}),
        "action_counts": dict(action_counts),
        "query_object_counts": dict(object_counts),
        "issue_counts": dict(issue_counts),
        "semantic_verdict_counts": dict(verdict_counts),
        "semantic_severity_counts": dict(severity_counts),
        "structural_failure_count": len(structural_failures),
        "semantic_failure_count": len(semantic_failures),
        "latency": {
            "avg_sec": round(sum(elapsed) / len(elapsed), 3) if elapsed else 0.0,
            "p50_sec": round(statistics.median(elapsed), 3) if elapsed else 0.0,
            "max_sec": round(max(elapsed), 3) if elapsed else 0.0,
            "total_sec": round(sum(elapsed), 3),
        },
    }


def _write_report(output_dir: Path, summary: dict[str, Any], results: list[dict[str, Any]]) -> tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe_mode = summary["mode"].replace("-", "_")
    json_path = output_dir / f"historical_conversation_eval_{safe_mode}_{stamp}.json"
    md_path = output_dir / f"historical_conversation_eval_{safe_mode}_{stamp}.md"
    json_path.write_text(json.dumps({"summary": summary, "results": results}, ensure_ascii=False, indent=2), encoding="utf-8")

    lines = [
        f"# Historical Conversation Router Eval ({summary['mode']})",
        "",
        f"- Conversations: `{summary['conversation_count']}`",
        f"- User turns: `{summary['user_turn_count']}`",
        f"- Unique inputs: `{summary['unique_user_input_count']}`",
        f"- Actions: `{summary['action_counts']}`",
        f"- Query objects: `{summary['query_object_counts']}`",
        f"- Structural failures: `{summary['structural_failure_count']}`",
        f"- Semantic verdicts: `{summary['semantic_verdict_counts']}`",
        f"- Semantic severity: `{summary['semantic_severity_counts']}`",
        f"- Latency: `{summary['latency']}`",
        "",
        "## Failures",
        "",
    ]
    failures = [
        item
        for item in results
        if item.get("issues") or item.get("semantic_judgement", {}).get("verdict") == "fail"
    ]
    if not failures:
        lines.append("- None")
    for item in failures:
        judge = item.get("semantic_judgement") or {}
        lines.extend(
            [
                f"### Conversation {item['conversation_id']} / turn {item['user_turn_index']}",
                f"- User: {item['user_text']}",
                f"- Route: `{item['task_summary']}`",
                f"- Structural issues: `{item.get('issues') or []}`",
                f"- Semantic: `{judge.get('verdict', 'not_judged')}/{judge.get('severity', 'none')}` {judge.get('reason', '')}",
                f"- Expected: `{judge.get('expected_action', '')}/{judge.get('expected_capability', '')}`",
                "",
            ]
        )
    md_path.write_text("\n".join(lines), encoding="utf-8")
    return json_path, md_path


def main() -> int:
    parser = argparse.ArgumentParser(description="Replay every stored user turn through the current Router without executing tools.")
    parser.add_argument("--mode", default="fast-go", choices=("fast-go", "fast-plus", "deep"))
    parser.add_argument("--conversations", default="conversations")
    parser.add_argument("--output-dir", default="reports")
    parser.add_argument("--limit-conversations", type=int, default=0)
    parser.add_argument("--conversation-ids", default="", help="Comma-separated stored conversation ids to replay.")
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument("--judge", action="store_true", help="Run a batched LLM semantic QA pass after routing.")
    args = parser.parse_args()

    conversations = _load_conversations(REPO_ROOT / args.conversations)
    selected_ids = {
        int(item.strip())
        for item in str(args.conversation_ids or "").split(",")
        if item.strip().isdigit()
    }
    if selected_ids:
        conversations = [item for item in conversations if item.get("id") in selected_ids]
    if args.limit_conversations > 0:
        conversations = conversations[: args.limit_conversations]
    results: list[dict[str, Any]] = []
    worker_count = max(1, min(int(args.workers or 1), 4))
    if worker_count == 1:
        for index, conversation in enumerate(conversations, start=1):
            print(f"[{index}/{len(conversations)}] conversation={conversation.get('id')} {conversation.get('title')}", flush=True)
            results.extend(run_conversation(conversation, args.mode))
    else:
        completed = 0
        by_conversation: dict[int, list[dict[str, Any]]] = {}
        with ProcessPoolExecutor(max_workers=worker_count) as executor:
            futures = {
                executor.submit(run_conversation, conversation, args.mode): index
                for index, conversation in enumerate(conversations)
            }
            for future in as_completed(futures):
                index = futures[future]
                completed += 1
                conversation = conversations[index]
                print(
                    f"[{completed}/{len(conversations)}] conversation={conversation.get('id')} {conversation.get('title')}",
                    flush=True,
                )
                try:
                    by_conversation[index] = future.result()
                except Exception as exc:  # pragma: no cover - process safety
                    by_conversation[index] = [
                        {
                            "conversation_id": conversation.get("id"),
                            "conversation_title": conversation.get("title"),
                            "conversation_path": conversation.get("path"),
                            "user_turn_index": 0,
                            "user_text": "",
                            "context_fingerprint": "",
                            "history_count": 0,
                            "dialogue_excerpt": "",
                            "tasks": [],
                            "task_summary": "",
                            "intent_envelope": {},
                            "plan": [],
                            "issues": ["conversation_worker_exception", f"{type(exc).__name__}: {exc}"],
                            "route_error": f"{type(exc).__name__}: {exc}",
                            "elapsed_sec": 0.0,
                            "semantic_judgement": {},
                        }
                    ]
        for index in range(len(conversations)):
            results.extend(by_conversation.get(index, []))

    if args.judge:
        print(f"Judging {len(results)} routed turns in batches...", flush=True)
        judge_results(results, args.mode)

    summary = _summary(results, args.mode, conversations)
    json_path, md_path = _write_report(REPO_ROOT / args.output_dir, summary, results)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print(f"JSON: {json_path}")
    print(f"Markdown: {md_path}")
    return 0 if not summary["structural_failure_count"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
