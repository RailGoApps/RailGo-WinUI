import json
import queue
import re

from agent.capabilities import IntentEnvelope, missing_required_evidence, resolve_query_id
from agent.coach_media_resolver import CoachMediaResolverAgent
from agent.fast_coordinator import FastCoordinator
from agent.fast_tool_views import build_fast_views
from agent.pending_utils import normalize_pending_payload
from agent.psw import AgentState
from llm.llm_client import LLMClient
from memory.episodic import MemoryManager
from tools.rail.coach_assets import coach_asset_service
from thinking.thinking_engine import ThinkingEngine


class _BufferedEventSequence:
    def __init__(self, iterator):
        self._iterator = iter(iterator)
        self._cache = []
        self._exhausted = False

    def _pull_next(self):
        if self._exhausted:
            raise StopIteration
        item = next(self._iterator)
        self._cache.append(item)
        return item

    def _materialize_until(self, index: int):
        while len(self._cache) <= index and not self._exhausted:
            try:
                self._pull_next()
            except StopIteration:
                self._exhausted = True
                break

    def _materialize_all(self):
        while not self._exhausted:
            try:
                self._pull_next()
            except StopIteration:
                self._exhausted = True
                break

    def __iter__(self):
        idx = 0
        while True:
            if idx < len(self._cache):
                yield self._cache[idx]
                idx += 1
                continue
            if self._exhausted:
                break
            try:
                item = self._pull_next()
            except StopIteration:
                self._exhausted = True
                break
            yield item
            idx += 1

    def __getitem__(self, index):
        if isinstance(index, slice):
            self._materialize_all()
            return self._cache[index]
        if index < 0:
            self._materialize_all()
            return self._cache[index]
        self._materialize_until(index)
        return self._cache[index]


class RailwayAgentApp:
    def __init__(self, router, planner, executor, answer_gen, psw, max_rounds=5, memory_manager=None):
        self.router = router
        self.planner = planner
        self.executor = executor
        self.answer_gen = answer_gen
        self.psw = psw
        self.max_rounds = max_rounds
        self.fast_coordinator = FastCoordinator(answer_gen)
        self.coach_media_resolver = CoachMediaResolverAgent()
        self.memory_manager = memory_manager or MemoryManager()
        if hasattr(self.memory_manager, "set_event_callback"):
            self.memory_manager.set_event_callback(self._on_memory_event)

        self.router.psw = psw
        self.planner.psw = psw
        self.executor.psw = psw
        self.answer_gen.psw = psw

        self._thinking_queue = queue.Queue()
        self.thinking_engine = ThinkingEngine(
            llm=LLMClient(mode="fast-go", credential_slot="thinking")
        )
        self.psw.add_listener(self._on_psw_event)

    def set_mode(self, mode: str):
        normalized = str(mode or "").strip().lower()
        if normalized == "fast":
            normalized = "fast-go"
        if normalized not in {"fast-go", "fast-plus", "deep"}:
            normalized = "deep"

        if hasattr(self.answer_gen, "set_mode_profile"):
            self.answer_gen.set_mode_profile(normalized)

        if hasattr(self.router, "set_mode"):
            self.router.set_mode(normalized)

        coach_resolver = getattr(self, "coach_media_resolver", None)
        if coach_resolver and hasattr(coach_resolver, "set_mode"):
            coach_resolver.set_mode(normalized)

        thinking_llm = getattr(self.thinking_engine, "llm", None)
        if hasattr(thinking_llm, "set_mode"):
            thinking_llm.set_mode(normalized)

    def _merge_facts(self, facts, new_facts):
        for key in ["queries", "analysis", "comparisons"]:
            facts[key].extend(new_facts.get(key, []))

        facts["meta"]["errors"].extend(new_facts.get("meta", {}).get("errors", []))
        facts["meta"]["warnings"].extend(new_facts.get("meta", {}).get("warnings", []))
        facts["meta"]["chat_messages"].extend(new_facts.get("meta", {}).get("chat_messages", []))
        if new_facts.get("meta", {}).get("pending"):
            facts["meta"]["pending"] = new_facts["meta"]["pending"]

    @staticmethod
    def _empty_facts(intent_envelope=None):
        return {
            "queries": [],
            "analysis": [],
            "comparisons": [],
            "meta": {
                "errors": [],
                "warnings": [],
                "chat_messages": [],
                "intent_envelope": dict(intent_envelope or {}),
            },
        }

    def _execute_plan_with_intent(self, plan, intent_envelope):
        envelope = IntentEnvelope.from_dict(intent_envelope)
        workflow = list(envelope.workflow or [])
        if len(workflow) <= 1 or envelope.execution_strategy == "parallel":
            return self.executor.execute(plan)

        aggregate = self._empty_facts(intent_envelope=envelope.to_dict())
        executed_indexes = set()
        for object_name in workflow:
            stage = []
            for index, step in enumerate(plan or []):
                if index in executed_indexes or not isinstance(step, dict):
                    continue
                if step.get("action") == "query" and str(step.get("params", {}).get("object") or "") == object_name:
                    stage.append(step)
                    executed_indexes.add(index)
            if not stage:
                continue
            if self.psw:
                self.psw.set_state(AgentState.WORKFLOW_STEP, f"executing capability workflow step={object_name}")
            self._merge_facts(aggregate, self.executor.execute(stage))

        remaining = [
            step
            for index, step in enumerate(plan or [])
            if index not in executed_indexes and isinstance(step, dict) and step.get("action") != "summarize"
        ]
        if remaining:
            self._merge_facts(aggregate, self.executor.execute(remaining))
        return aggregate

    def _check_required_evidence(self, facts, intent_envelope, allow_replan=True):
        envelope = IntentEnvelope.from_dict(intent_envelope)
        missing = missing_required_evidence(envelope, facts)
        facts.setdefault("meta", {})["intent_envelope"] = envelope.to_dict()
        facts["meta"]["missing_required_evidence"] = list(missing)
        if not missing:
            return None

        if self.psw:
            self.psw.set_state(
                AgentState.EVIDENCE_MISMATCH,
                f"capability={envelope.selected_capability or 'unknown'} missing evidence={','.join(missing)}",
            )

        attempted = {
            str(item.get("object") or "").strip()
            for item in facts.get("queries", [])
            if isinstance(item, dict)
        }
        if not allow_replan:
            return None

        missing_unattempted = [item for item in missing if item not in attempted]
        if not missing_unattempted:
            return None

        slots = dict(envelope.grounded_slots or {})
        context = {
            "train_numbers": slots.get("trains") or ([slots.get("train")] if slots.get("train") else []),
            "emu_id": slots.get("emu") or "",
            "dep": slots.get("dep") or "",
            "arr": slots.get("arr") or "",
            "station_mentions": slots.get("stations") or ([slots.get("station")] if slots.get("station") else []),
            "query_date": slots.get("date") or "",
            "telecode": slots.get("telecode") or "",
            "direction": slots.get("direction") or "",
        }
        missing_queries = []
        for object_name in missing_unattempted:
            if object_name == "smartemu_analysis" and envelope.selected_capability == "route_smartemu_search":
                query_id = ",".join(self._extract_fact_train_numbers(facts)[:20])
            else:
                query_id = resolve_query_id(object_name, context)
            if not query_id:
                continue
            item = {"domain": "railway", "object": object_name, "id": query_id}
            if object_name == "train_station_access" and context["query_date"]:
                item["date"] = context["query_date"]
            missing_queries.append(item)
        return {"missing": missing_queries} if missing_queries else None

    @staticmethod
    def _extract_fact_train_numbers(facts):
        """Bind deferred workflow inputs from tool facts, never model prose."""

        trains = []
        for item in (facts or {}).get("queries", []):
            if not isinstance(item, dict):
                continue
            if str(item.get("type") or "") in {"query_empty", "query_error"}:
                continue
            if str(item.get("object") or "") not in {
                "station_to_station_mini",
                "station_to_station_detail",
                "station_to_station_future",
                "station_to_station_past",
            }:
                continue
            evidence_payload = {
                "evidence": item.get("evidence"),
                "result": item.get("result"),
                "records": item.get("records"),
                "fast_views": item.get("fast_views"),
                "pretty": item.get("pretty"),
            }
            raw = json.dumps(evidence_payload, ensure_ascii=False, default=str)
            for train in re.findall(r"(?<![A-Z0-9])[GDC]\d{1,5}(?!\d)", raw.upper()):
                if train not in trains:
                    trains.append(train)
        return trains

    def _scope_live_delay_evidence(self, facts, intent_envelope):
        envelope = IntentEnvelope.from_dict(intent_envelope)
        if envelope.selected_capability != "train_delay" or not envelope.scope:
            return
        dep = str(envelope.scope.get("dep") or "").strip()
        arr = str(envelope.scope.get("arr") or "").strip()
        if not dep or not arr:
            return

        for item in facts.get("queries", []):
            if not isinstance(item, dict) or item.get("object") != "train_delay":
                continue
            rows = [row for row in (item.get("evidence") or []) if isinstance(row, dict)]
            names = [str(row.get("stationName") or "").strip() for row in rows]
            try:
                start = names.index(dep)
                end = names.index(arr, start)
            except ValueError:
                facts.setdefault("meta", {}).setdefault("warnings", []).append(
                    f"live delay scope boundary not found in returned station rows: {dep}-{arr}"
                )
                continue
            scoped_rows = rows[start:end + 1]
            item["full_evidence_station_count"] = len(rows)
            item["requested_scope"] = {"dep": dep, "arr": arr}
            item["evidence"] = scoped_rows
            item["pretty"] = (
                f"SCOPED LIVE TRAIN DELAY: {item.get('id', '')} {dep}->{arr}\n"
                + json.dumps(scoped_rows, ensure_ascii=False, indent=2)
            )
            item["fast_views"] = build_fast_views(item, raw_payload=scoped_rows)

    def _prepare_query_attachments(self, user_text, facts, intent_envelope):
        """Resolve presentation artifacts without exposing media locators to the LLM."""

        meta = facts.setdefault("meta", {})
        if meta.get("attachments_prepared"):
            return list(meta.get("attachments") or []), None

        attachments = []
        pending = None
        envelope = IntentEnvelope.from_dict(intent_envelope)
        for query in facts.get("queries", []):
            if not isinstance(query, dict):
                continue
            if query.get("object") == "train_route_map":
                attachments.extend(
                    item for item in (query.get("artifacts") or [])
                    if isinstance(item, dict) and item.get("type") == "route_map"
                )
                continue
            if query.get("object") != "coach_layout":
                continue

            evidence = query.get("evidence") if isinstance(query.get("evidence"), dict) else {}
            catalog = [item for item in (query.get("_media_catalog") or []) if isinstance(item, dict)]
            decision = self.coach_media_resolver.resolve(user_text, catalog)
            envelope.presentation_mode = str(decision.get("presentation_mode") or "summary")
            envelope.media_target = {
                "coach_number": str(decision.get("coach_number") or ""),
                "seat_type": str(decision.get("seat_type") or ""),
                "selector": str(decision.get("selector") or ""),
            }
            meta["intent_envelope"] = envelope.to_dict()
            if envelope.presentation_mode == "clarify":
                available = [str(item.get("label") or item.get("selector") or "") for item in catalog][:12]
                pending = {
                    "question": "你想看哪一节车厢或哪一种席别的内部图？",
                    "slot": ["coach_media_target"],
                    "context": {
                        "train": str(query.get("id") or ""),
                        "query_object": "coach_layout",
                        "available_targets": available,
                    },
                }
                break
            if envelope.presentation_mode not in {"whole_train", "coach", "interior"}:
                continue
            try:
                attachment = coach_asset_service.resolve_media(
                    str(query.get("id") or ""),
                    envelope.presentation_mode,
                    str(decision.get("selector") or "default"),
                    psw=self.psw,
                )
                attachments.append(attachment)
            except Exception as exc:
                meta.setdefault("warnings", []).append(f"coach media unavailable: {exc}")

        # Only stable descriptors are retained. URLs and file paths remain in the asset store.
        unique = {}
        for item in attachments:
            key = (str(item.get("type") or ""), str(item.get("asset_id") or ""))
            if key[0] and key[1]:
                unique[key] = item
        meta["attachments"] = list(unique.values())
        meta["attachments_prepared"] = True
        return list(unique.values()), pending

    def _emit_thinking(self, text):
        self._thinking_queue.put(text)

    def _on_psw_event(self, event):
        print("THINKING RECEIVED", event)
        self.thinking_engine.push_event(event)

    def _on_memory_event(self, state_name, detail):
        if not self.psw:
            return
        state = getattr(AgentState, str(state_name or "").strip(), None)
        if state is None:
            return
        self.psw.set_state(state, str(detail or ""))

    def _prepare_memory_for_turn(self, user_text, session):
        if session:
            session.add_user_message(user_text)

        if not self.memory_manager or not session:
            return

        recall_bundle = self.memory_manager.build_recall_bundle(session, user_text)
        session.set_memory_recall(recall_bundle)

    def _record_turn_memory(self, session, user_text, ai_text, tasks=None, facts=None):
        if not self.memory_manager or not session:
            return

        self.memory_manager.record_turn(
            session=session,
            user_text=user_text,
            ai_text=ai_text,
            tasks=tasks,
            facts=facts,
        )

    def _finish_with_pending(self, session, user_text, question, tasks=None, facts=None, pending_params=None):
        params = pending_params if isinstance(pending_params, dict) else {}
        pending_payload = normalize_pending_payload(
            question=question,
            slot=params.get("slot", []),
            context=params.get("context", {}),
            fallback=question,
        )

        def _events():
            session.enter_followup(
                question=pending_payload["question"],
                slot=pending_payload["slot"],
                context=pending_payload["context"],
            )

            if self.psw:
                self.psw.set_state(AgentState.GENERATING, "streaming follow-up clarification")

            full_text = ""
            stream_started = False
            stream_pending = getattr(self.answer_gen, "stream_pending_question", None)

            if callable(stream_pending):
                try:
                    for chunk in stream_pending(
                        user_text=user_text,
                        pending_payload=pending_payload,
                        session=session,
                        tasks=tasks,
                        facts=facts,
                    ):
                        text = str(chunk or "")
                        if not text:
                            continue
                        stream_started = True
                        full_text += text
                        yield {"type": "pending", "text": text}
                except Exception as exc:
                    print(f"[pending-llm] fallback to deterministic prompt: {exc}")
                    if not stream_started:
                        full_text = ""

            full_text = str(full_text or "").strip()
            if not full_text:
                full_text = pending_payload["question"]
                yield {"type": "pending", "text": full_text}

            session.add_ai_message(full_text)
            self._record_turn_memory(session, user_text, full_text, tasks=tasks, facts=facts)
            yield {"type": "final", "text": full_text}

        return _BufferedEventSequence(_events())

    def _finish_with_final(self, session, user_text, answer_text, tasks=None, facts=None):
        session.add_ai_message(answer_text)
        self._record_turn_memory(session, user_text, answer_text, tasks=tasks, facts=facts)
        return {"type": "final", "text": answer_text}

    def _facts_progress_signature(self, facts):
        query_markers = []
        for item in facts.get("queries", []):
            if not isinstance(item, dict):
                continue
            query_markers.append(
                {
                    "type": str(item.get("type") or "query"),
                    "object": str(item.get("object") or ""),
                    "id": str(item.get("id") or ""),
                    "date": str(item.get("date") or ""),
                    "key": str(item.get("key") or ""),
                }
            )

        query_markers.sort(key=lambda marker: json.dumps(marker, ensure_ascii=False, sort_keys=True))
        meta = facts.get("meta", {}) if isinstance(facts, dict) else {}
        payload = {
            "queries": query_markers,
            "analysis_count": len(facts.get("analysis", [])),
            "comparison_count": len(facts.get("comparisons", [])),
            "error_count": len(meta.get("errors", [])),
            "warning_count": len(meta.get("warnings", [])),
            "chat_count": len(meta.get("chat_messages", [])),
        }
        return json.dumps(payload, ensure_ascii=False, sort_keys=True)

    def _extra_request_signature(self, extra_request):
        if not isinstance(extra_request, dict):
            return ""

        missing = []
        for item in extra_request.get("missing", []):
            if not isinstance(item, dict):
                continue
            missing.append(
                {
                    "domain": str(item.get("domain") or ""),
                    "object": str(item.get("object") or ""),
                    "id": str(item.get("id") or ""),
                    "date": str(item.get("date") or ""),
                }
            )

        if not missing:
            return ""

        missing.sort(key=lambda marker: json.dumps(marker, ensure_ascii=False, sort_keys=True))
        return json.dumps(missing, ensure_ascii=False, sort_keys=True)

    def _force_finalize_with_available_facts(self, user_text, facts, session, context_bundle=None):
        if hasattr(self.answer_gen, "force_finalize"):
            return self.answer_gen.force_finalize(
                user_text=user_text,
                facts=facts,
                session=session,
                context_bundle=context_bundle,
            )
        return "根据当前已经拿到的事实，我先给你一个尽量可靠的结论。"

    def _iter_text_chunks(self, text, max_chunk_size=24):
        content = str(text or "")
        if not content:
            return

        chunk = []
        for ch in content:
            chunk.append(ch)
            if ch in "\n。！？!?；;：:" or len(chunk) >= max_chunk_size:
                yield "".join(chunk)
                chunk = []

        if chunk:
            yield "".join(chunk)

    def _yield_force_finalize_events(self, session, user_text, answer_text, tasks=None, facts=None):
        full = str(answer_text or "")
        for chunk in self._iter_text_chunks(full):
            yield {"type": "token", "text": chunk}
        yield self._finish_with_final(session, user_text, full, tasks=tasks, facts=facts)

    def _build_chat_facts(self, tasks):
        chat_route_tags = []
        for task in tasks or []:
            if not isinstance(task, dict) or task.get("action") != "chat":
                continue
            params = task.get("params", {})
            if not isinstance(params, dict):
                continue
            message = str(params.get("message") or "").strip()
            if message:
                tag = self._chat_route_tag_from_message(message)
                if tag and tag not in chat_route_tags:
                    chat_route_tags.append(tag)

        return {
            "queries": [],
            "analysis": [],
            "comparisons": [],
            "meta": {
                "errors": [],
                "warnings": [],
                # Internal router prose must never be treated as evidence or
                # copied into the final answer prompt. Keep only route tags.
                "chat_messages": [],
                "chat_route_tags": chat_route_tags,
            },
        }

    def _chat_route_tag_from_message(self, message: str) -> str:
        text = str(message or "").strip().lower()
        if not text:
            return "general_chat"
        if "memory" in text and "profile" in text:
            return "memory_profile"
        if "directional speed" in text or "easter" in text:
            return "directional_speed"
        if "capability-boundary" in text or "current-only" in text or "current running status" in text:
            return "capability_boundary"
        if "travel inspiration" in text or "attractions" in text:
            return "travel_chat"
        if "railway knowledge" in text or "stable railway background" in text:
            return "railway_knowledge"
        if "contextual social" in text or "reacting" in text:
            return "contextual_social"
        if "creative" in text or "rewriting" in text or "different style" in text:
            return "creative_followup"
        if "contextual" in text or "previous answer" in text or "recent conversation" in text:
            return "contextual_followup"
        if "capabilities" in text or "who railgpt is" in text:
            return "identity_or_capability"
        return "general_chat"

    def _get_static_chat_reply(self, tasks):
        for task in tasks or []:
            if not isinstance(task, dict) or task.get("action") != "chat":
                continue
            params = task.get("params", {})
            if not isinstance(params, dict):
                continue
            direct_reply = str(params.get("direct_reply") or "").strip()
            if direct_reply:
                return direct_reply
        return ""

    def _is_pure_chat_route(self, tasks):
        return bool(tasks) and all(isinstance(task, dict) and task.get("action") == "chat" for task in tasks)

    def _suspend_followup_for_chat(self, session, tasks):
        if not session or not session.in_followup() or not self._is_pure_chat_route(tasks):
            return None

        followup_state = {
            "question": session.followup_question,
            "slot": list(session.followup_slots.get("slot", [])),
            "context": dict(session.followup_slots.get("context", {})),
        }
        session.exit_followup()
        return followup_state

    def _restore_followup(self, session, followup_state):
        if not session or not isinstance(followup_state, dict):
            return
        if session.in_followup():
            return
        question = str(followup_state.get("question") or "").strip()
        if not question:
            return
        session.enter_followup(
            question=question,
            slot=followup_state.get("slot", []),
            context=followup_state.get("context", {}),
        )

    def _run_core_loop(self, user_text, session):
        self.thinking_engine.start_user_thinking(user_text)
        suspended_followup = None
        try:
            self._prepare_memory_for_turn(user_text, session)

            self.psw.set_state(AgentState.ROUTING, "routing user intent")
            route_result = self.router.route(user_text, session)
            intent_envelope = (
                self.router.get_last_intent_envelope()
                if hasattr(self.router, "get_last_intent_envelope")
                else {}
            )
            session.update_from_tasks(route_result)
            suspended_followup = self._suspend_followup_for_chat(session, route_result)

            if route_result and route_result[0]["action"] == "pending":
                question = route_result[0]["params"].get("question", "请补充关键信息。")
                for event in self._finish_with_pending(
                    session,
                    user_text,
                    question,
                    tasks=route_result,
                    pending_params=route_result[0].get("params", {}),
                ):
                    yield event
                return

            if self._is_pure_chat_route(route_result):
                chat_facts = self._build_chat_facts(route_result)
                chat_facts.setdefault("meta", {})["intent_envelope"] = dict(intent_envelope or {})
                self.psw.set_state(AgentState.GENERATING, "answering dedicated chat turn")
                direct_reply = self._get_static_chat_reply(route_result)
                if direct_reply:
                    for event in self._yield_force_finalize_events(
                        session,
                        user_text,
                        direct_reply,
                        tasks=route_result,
                        facts=chat_facts,
                    ):
                        yield event
                    return

                messages = self.answer_gen.build_messages(
                    user_text=user_text,
                    facts=chat_facts,
                    session=session,
                    style="conversational",
                    length="medium",
                )

                full = ""
                for token in self.answer_gen.stream_final(messages):
                    full += token
                    yield {"type": "token", "text": token}

                yield self._finish_with_final(session, user_text, full, tasks=route_result, facts=chat_facts)
                return

            self.executor.dialog_seen_queries.clear()

            facts = self._empty_facts(intent_envelope=intent_envelope)

            extra_request = None
            fast_recovery_attempted = False
            seen_extra_requests = {}
            no_progress_rounds = 0
            evidence_replan_attempted = False

            for round_idx in range(self.max_rounds):
                self.psw.set_state(AgentState.PLANNING, f"round {round_idx}")

                if round_idx == 0:
                    plan = self.planner.build_plan(route_result)
                else:
                    plan = self.planner.build_plan_from_request(extra_request, facts)

                if round_idx > 0 and not plan:
                    self.psw.set_state(
                        AgentState.SKIP,
                        "planner produced no new follow-up queries, forcing best-effort final answer",
                    )
                    if session.in_followup():
                        session.exit_followup()
                    answer_text = self._force_finalize_with_available_facts(
                        user_text=user_text,
                        facts=facts,
                        session=session,
                    )
                    for event in self._yield_force_finalize_events(
                        session,
                        user_text,
                        answer_text,
                        tasks=route_result,
                        facts=facts,
                    ):
                        yield event
                    return

                session.update_from_tasks(plan)
                round_signature_before = self._facts_progress_signature(facts)
                new_facts = self._execute_plan_with_intent(plan, intent_envelope)
                session.update_from_facts(new_facts)

                pending = new_facts.get("meta", {}).get("pending")
                if pending:
                    question = pending.get("question", "请补充关键信息。")
                    for event in self._finish_with_pending(
                        session,
                        user_text,
                        question,
                        tasks=plan,
                        facts=new_facts,
                        pending_params=pending,
                    ):
                        yield event
                    return

                self._merge_facts(facts, new_facts)
                self._scope_live_delay_evidence(facts, intent_envelope)

                if self.answer_gen.is_fast_mode() and not fast_recovery_attempted:
                    repaired_tasks = self.router.recover_fast_tasks(
                        user_text=user_text,
                        facts=facts,
                        prior_tasks=plan,
                    )
                    if repaired_tasks and repaired_tasks != plan:
                        fast_recovery_attempted = True
                        session.update_from_tasks(repaired_tasks)
                        repaired_plan = self.planner.build_plan(repaired_tasks)
                        repaired_facts = self.executor.execute(repaired_plan)
                        session.update_from_facts(repaired_facts)

                        pending = repaired_facts.get("meta", {}).get("pending")
                        if pending:
                            question = pending.get("question") or "请补充关键信息。"
                            for event in self._finish_with_pending(
                                session,
                                user_text,
                                question,
                                tasks=repaired_plan,
                                facts=repaired_facts,
                                pending_params=pending,
                            ):
                                yield event
                            return

                        self._merge_facts(facts, repaired_facts)
                        self._scope_live_delay_evidence(facts, intent_envelope)

                round_signature_after = self._facts_progress_signature(facts)
                if round_signature_after == round_signature_before:
                    no_progress_rounds += 1
                else:
                    no_progress_rounds = 0

                evidence_extra = self._check_required_evidence(
                    facts=facts,
                    intent_envelope=intent_envelope,
                    allow_replan=not evidence_replan_attempted,
                )
                if evidence_extra and evidence_extra.get("missing"):
                    evidence_replan_attempted = True
                    extra_request = evidence_extra
                    continue

                attachments, attachment_pending = self._prepare_query_attachments(
                    user_text, facts, intent_envelope
                )
                if attachment_pending:
                    for event in self._finish_with_pending(
                        session,
                        user_text,
                        attachment_pending["question"],
                        tasks=plan,
                        facts=facts,
                        pending_params=attachment_pending,
                    ):
                        yield event
                    return
                if not facts.get("meta", {}).get("attachments_emitted"):
                    for attachment in attachments:
                        yield {"type": "attachment", "attachment": attachment}
                    facts.setdefault("meta", {})["attachments_emitted"] = True

                context_bundle = None
                rag_context = ""
                presentation_plan = {}

                if self._should_prepare_fast_assets(facts):
                    fast_assets = self.fast_coordinator.prepare(
                        user_text=user_text,
                        facts=facts,
                        psw=self.psw,
                    )
                    context_bundle = fast_assets.context_bundle
                    rag_context = fast_assets.rag_context
                    presentation_plan = fast_assets.presentation_plan
                elif self.answer_gen.is_fast_mode():
                    self.psw.set_state(
                        AgentState.SKIP,
                        "fast coordinator skipped because no reliable evidence is available yet",
                    )

                if self.answer_gen.should_use_fast_direct_final(
                    user_text=user_text,
                    facts=facts,
                    context_bundle=context_bundle,
                ):
                    if session.in_followup():
                        session.exit_followup()

                    self.psw.set_state(
                        AgentState.FAST_DIRECT_FINAL,
                        "fast mode skipped structured arbitration and went directly to final llm",
                    )

                    messages = self.answer_gen.build_messages(
                        user_text=user_text,
                        facts=facts,
                        session=session,
                        style="structured",
                        length="medium",
                        context_bundle=context_bundle,
                        rag_context=rag_context,
                        presentation_plan=presentation_plan,
                    )

                    full = ""
                    for token in self.answer_gen.stream_final(messages):
                        full += token
                        yield {"type": "token", "text": token}

                    yield self._finish_with_final(session, user_text, full, tasks=plan, facts=facts)
                    return

                result = self.answer_gen.generate_structured(
                    user_text=user_text,
                    facts=facts,
                    session=session,
                    context_bundle=context_bundle,
                )

                rtype = result.get("type")
                if rtype == "need_user_input":
                    question = result.get("question", "请补充关键信息。")
                    for event in self._finish_with_pending(
                        session,
                        user_text,
                        question,
                        tasks=plan,
                        facts=facts,
                    ):
                        yield event
                    return

                if rtype == "final":
                    if session.in_followup():
                        session.exit_followup()

                    answer_style = "structured" if self.answer_gen.is_fast_mode() else "detailed"
                    messages = self.answer_gen.build_messages(
                        user_text=user_text,
                        facts=facts,
                        session=session,
                        style=answer_style,
                        length="medium",
                        context_bundle=context_bundle,
                        rag_context=rag_context,
                        presentation_plan=presentation_plan,
                    )

                    full = ""
                    for token in self.answer_gen.stream_final(messages):
                        full += token
                        yield {"type": "token", "text": token}

                    yield self._finish_with_final(session, user_text, full, tasks=plan, facts=facts)
                    return

                if rtype == "need_more_facts":
                    extra_request = result.get("extra_request") or {}
                    if (
                        self.answer_gen.is_fast_mode()
                        and hasattr(self.answer_gen, "should_force_finalize_on_empty_queries")
                        and self.answer_gen.should_force_finalize_on_empty_queries(
                            facts=facts,
                            extra_request=extra_request,
                        )
                    ):
                        self.psw.set_state(
                            AgentState.SKIP,
                            "fast empty-result guard forced best-effort final answer",
                        )
                        if session.in_followup():
                            session.exit_followup()
                        answer_text = self._force_finalize_with_available_facts(
                            user_text=user_text,
                            facts=facts,
                            session=session,
                            context_bundle=context_bundle,
                        )
                        for event in self._yield_force_finalize_events(
                            session,
                            user_text,
                            answer_text,
                            tasks=plan,
                            facts=facts,
                        ):
                            yield event
                        return

                    extra_signature = self._extra_request_signature(extra_request)
                    if extra_signature:
                        seen_extra_requests[extra_signature] = seen_extra_requests.get(extra_signature, 0) + 1

                    if (
                        not extra_signature
                        or seen_extra_requests.get(extra_signature, 0) >= 2
                        or no_progress_rounds >= 1
                        or round_idx >= self.max_rounds - 1
                    ):
                        detail = (
                            "fast loop guard triggered repeated need_more_facts; forcing best-effort final answer"
                            if extra_signature
                            else "structured stage requested more facts without a valid query payload; forcing best-effort final answer"
                        )
                        self.psw.set_state(AgentState.SKIP, detail)
                        if session.in_followup():
                            session.exit_followup()
                        answer_text = self._force_finalize_with_available_facts(
                            user_text=user_text,
                            facts=facts,
                            session=session,
                            context_bundle=context_bundle,
                        )
                        for event in self._yield_force_finalize_events(
                            session,
                            user_text,
                            answer_text,
                            tasks=plan,
                            facts=facts,
                        ):
                            yield event
                        return
                    continue

                yield {"type": "final", "text": "⚠️ 未知状态"}
                return

            yield {"type": "final", "text": "⚠️ 超过最大推理轮次"}

        except Exception:
            # Let the worker/UI error channel handle transport failures instead of
            # serializing a Python traceback into the assistant message stream.
            raise
        finally:
            self._restore_followup(session, suspended_followup)

    def stream_events(self, user_text, session):
        for event in self._run_core_loop(user_text, session):
            while not self._thinking_queue.empty():
                text = self._thinking_queue.get()
                yield {"type": "thinking_token", "text": text}

            yield event

        while not self._thinking_queue.empty():
            text = self._thinking_queue.get()
            yield {"type": "thinking_token", "text": text}

    def _should_prepare_fast_assets(self, facts):
        if not self.answer_gen.is_fast_mode():
            return False

        if facts.get("analysis") or facts.get("comparisons"):
            return True

        for item in facts.get("queries", []):
            if not isinstance(item, dict):
                continue
            if item.get("type") in {"query_empty", "query_error"}:
                continue
            if item.get("pretty") or item.get("fast_views") or item.get("fast_candidates"):
                return True
        return False

    def run_once(self, user_text, session):
        full = ""

        for event in self._run_core_loop(user_text, session):
            if event["type"] == "token":
                print(event["text"], end="", flush=True)
                full += event["text"]

            if event["type"] == "final":
                print()
                return event["text"]

        return full
