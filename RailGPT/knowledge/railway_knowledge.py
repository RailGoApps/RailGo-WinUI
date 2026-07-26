from __future__ import annotations

import hashlib
import json
import math
import re
import sqlite3
import threading
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from tools.rail.rail_store import railstore
from tools.rail.station_dict import station_dict
from utils.simple_lru import SimpleLRUCache


TRAIN_RE = re.compile(r"[GDKTZC]\d{1,5}", re.IGNORECASE)
EMU_RE = re.compile(r"(?:CRH|CR|CJ)\d+[A-Z-]*\d+", re.IGNORECASE)
TELE_RE = re.compile(r"\b[A-Z]{3}\b")
TOKEN_RE = re.compile(r"[A-Za-z0-9]+|[\u4e00-\u9fff]+")


@dataclass
class KnowledgeDoc:
    doc_id: str
    kind: str
    title: str
    text: str
    metadata: Dict[str, Any]
    entities: List[str]


class HashEmbeddingEncoder:
    """
    Dependency-free hashing encoder for lightweight vector retrieval.
    """

    def __init__(self, dim: int = 384):
        self.dim = dim

    def encode(self, text: str) -> Dict[int, float]:
        counts = Counter(self.tokenize(text))
        if not counts:
            return {}

        vector = defaultdict(float)
        for token, count in counts.items():
            index = self._hash_index(token)
            sign = self._hash_sign(token)
            weight = float(count) * (1.0 + min(len(token), 8) * 0.08)
            vector[index] += sign * weight

        norm = math.sqrt(sum(value * value for value in vector.values()))
        if norm <= 1e-9:
            return {}

        return {index: value / norm for index, value in vector.items()}

    def similarity(self, left: Dict[int, float], right: Dict[int, float]) -> float:
        if not left or not right:
            return 0.0
        if len(left) > len(right):
            left, right = right, left
        return sum(value * right.get(index, 0.0) for index, value in left.items())

    def tokenize(self, text: str) -> List[str]:
        normalized = re.sub(r"\s+", " ", str(text or "").strip().lower())
        if not normalized:
            return []

        tokens: List[str] = []
        for chunk in TOKEN_RE.findall(normalized):
            if re.fullmatch(r"[\u4e00-\u9fff]+", chunk):
                tokens.append(chunk)
                for size in (2, 3):
                    if len(chunk) < size:
                        continue
                    for idx in range(len(chunk) - size + 1):
                        tokens.append(chunk[idx:idx + size])
            else:
                tokens.append(chunk)
                if len(chunk) >= 4:
                    tokens.append(chunk[:4])
        return tokens

    def _hash_index(self, token: str) -> int:
        digest = hashlib.sha1(token.encode("utf-8")).hexdigest()
        return int(digest[:8], 16) % self.dim

    def _hash_sign(self, token: str) -> int:
        digest = hashlib.sha1(f"sign:{token}".encode("utf-8")).hexdigest()
        return 1 if int(digest[-2:], 16) % 2 == 0 else -1


class RailwayKnowledgeBase:
    def __init__(
        self,
        station_entries: Optional[Dict[str, Dict[str, Any]]] = None,
        station_meta: Optional[Dict[str, Any]] = None,
        db_path: str | Path | None = None,
    ):
        self.station_entries = station_entries
        self.station_meta = station_meta
        self.db_path = Path(db_path).resolve() if db_path else Path(railstore.db_path).resolve()
        self.station_dict_path = (
            Path(getattr(station_dict, "filepath", "")).resolve()
            if getattr(station_dict, "filepath", None)
            else None
        )

        self._lock = threading.RLock()
        self._signature: Optional[str] = None
        self._docs: List[KnowledgeDoc] = []
        self._doc_tokens: Dict[str, set[str]] = {}
        self._entity_to_docs: Dict[str, set[str]] = {}
        self._entity_graph: Dict[str, set[str]] = {}

    def get_index(self) -> Dict[str, Any]:
        with self._lock:
            signature = self._build_signature()
            if signature != self._signature:
                self._rebuild_locked(signature)

            return {
                "signature": self._signature,
                "docs": list(self._docs),
                "docs_by_id": {doc.doc_id: doc for doc in self._docs},
                "doc_tokens": self._doc_tokens,
                "entity_to_docs": self._entity_to_docs,
                "entity_graph": self._entity_graph,
            }

    def _build_signature(self) -> str:
        payload = {
            "db_mtime": self._safe_mtime(self.db_path),
            "station_dict_mtime": self._safe_mtime(self.station_dict_path),
            "station_count": len(self._station_entries()),
            "station_meta": self._station_meta(),
        }
        return hashlib.sha1(
            json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
        ).hexdigest()

    def _safe_mtime(self, path: Optional[Path]) -> float:
        if path is None or not path.exists():
            return 0.0
        return path.stat().st_mtime

    def _station_entries(self) -> Dict[str, Dict[str, Any]]:
        if self.station_entries is not None:
            return self.station_entries
        return dict(getattr(station_dict, "data", {}) or {})

    def _station_meta(self) -> Dict[str, Any]:
        if self.station_meta is not None:
            return dict(self.station_meta)
        return dict(getattr(station_dict, "meta", {}) or {})

    def _rebuild_locked(self, signature: str):
        docs: List[KnowledgeDoc] = []
        docs.extend(self._build_station_dict_docs())
        docs.extend(self._build_station_cache_docs())
        docs.extend(self._build_route_docs())
        docs.extend(self._build_path_docs())

        doc_tokens: Dict[str, set[str]] = {}
        entity_to_docs: Dict[str, set[str]] = defaultdict(set)
        entity_graph: Dict[str, set[str]] = defaultdict(set)

        for doc in docs:
            doc_tokens[doc.doc_id] = set(self._tokenize(doc.text + " " + doc.title))
            unique_entities = list(dict.fromkeys(entity for entity in doc.entities if entity))
            for entity in unique_entities:
                entity_to_docs[entity].add(doc.doc_id)
            for left in unique_entities[:24]:
                for right in unique_entities[:24]:
                    if left != right:
                        entity_graph[left].add(right)

        self._signature = signature
        self._docs = docs
        self._doc_tokens = doc_tokens
        self._entity_to_docs = {key: set(value) for key, value in entity_to_docs.items()}
        self._entity_graph = {key: set(value) for key, value in entity_graph.items()}

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        return conn

    def _build_station_dict_docs(self) -> List[KnowledgeDoc]:
        docs: List[KnowledgeDoc] = []
        for telecode, info in self._station_entries().items():
            name = str(info.get("name") or telecode).strip()
            city = str(info.get("city") or "").strip()
            province = str(info.get("province") or "").strip()
            pinyin = str(info.get("pinyin") or "").strip()
            abbr = str(info.get("abbr") or "").strip()

            aliases = [name]
            plain_name = name[:-1] if name.endswith("站") else name
            if plain_name and plain_name != name:
                aliases.append(plain_name)
            if city and city not in aliases:
                aliases.append(city)

            text_parts = [
                f"车站 {name} 电报码 {telecode}",
                f"城市 {city or '?'}",
                f"省份 {province or '?'}",
            ]
            if pinyin:
                text_parts.append(f"拼音 {pinyin}")
            if abbr:
                text_parts.append(f"缩写 {abbr}")
            text_parts.append(f"别名 {' '.join(aliases)}")

            docs.append(
                KnowledgeDoc(
                    doc_id=f"station_dict:{telecode}",
                    kind="station_dict",
                    title=f"车站 {name} ({telecode})",
                    text="；".join(text_parts),
                    metadata={
                        "name": name,
                        "telecode": telecode,
                        "city": city,
                        "province": province,
                        "aliases": aliases,
                    },
                    entities=self._station_entities(name, telecode, city, province),
                )
            )

        return docs

    def _build_station_cache_docs(self) -> List[KnowledgeDoc]:
        docs: List[KnowledgeDoc] = []
        if not self.db_path.exists():
            return docs

        conn = self._connect()
        try:
            rows = conn.execute("SELECT id, data FROM station_cache").fetchall()
        finally:
            conn.close()

        for row in rows:
            payload = self._loads_json(row["data"])
            data = payload.get("data", {}) if isinstance(payload, dict) else {}
            telecode = str(data.get("telecode") or row["id"]).strip()
            name = str(data.get("name") or telecode).strip()
            bureau = str(data.get("bureau") or "").strip()
            belong = str(data.get("belong") or "").strip()
            city = str(data.get("city") or "").strip()
            province = str(data.get("province") or "").strip()
            level = str(data.get("level") or "").strip()
            train_list = data.get("trainList", [])
            if not isinstance(train_list, list):
                train_list = []

            text_parts = [
                f"车站档案 {name} 电报码 {telecode}",
                f"路局 {bureau or '?'}",
                f"归属 {belong or '?'}",
                f"城市 {city or '?'}",
                f"省份 {province or '?'}",
                f"等级 {level or '?'}",
            ]
            if train_list:
                text_parts.append(f"常见车次 {' '.join(str(item) for item in train_list[:20])}")

            entities = self._station_entities(name, telecode, city, province)
            if bureau:
                entities.append(f"bureau:{bureau}")
            if belong:
                entities.append(f"belong:{belong}")
            for train_no in train_list[:20]:
                entities.append(f"train:{str(train_no).upper()}")

            docs.append(
                KnowledgeDoc(
                    doc_id=f"station_profile:{telecode}",
                    kind="station_profile",
                    title=f"车站档案 {name} ({telecode})",
                    text="；".join(text_parts),
                    metadata={
                        "name": name,
                        "telecode": telecode,
                        "bureau": bureau,
                        "belong": belong,
                        "city": city,
                        "province": province,
                    },
                    entities=entities,
                )
            )

        return docs

    def _build_route_docs(self) -> List[KnowledgeDoc]:
        docs: List[KnowledgeDoc] = []
        if not self.db_path.exists():
            return docs

        conn = self._connect()
        try:
            rows = conn.execute("SELECT id, data, valid_from, valid_to FROM s2s_cache").fetchall()
        finally:
            conn.close()

        for row in rows:
            payload = self._loads_json(row["data"])
            trains = self._extract_s2s_trains(payload)
            if not trains:
                continue

            route_id = str(row["id"]).strip()
            dep_token, arr_token = self._split_route_id(route_id)
            dep_name = self._station_name(dep_token)
            arr_name = self._station_name(arr_token)
            bureau_counter = Counter(str(item.get("bureauName") or "").strip() for item in trains if item.get("bureauName"))
            model_counter = Counter(str(item.get("car") or "").strip() for item in trains if item.get("car"))
            train_numbers = [self._train_number(item) for item in trains]
            duration_candidates = [str(item.get("passTime") or "").strip() for item in trains if item.get("passTime")]

            text_parts = [
                f"区间知识 {dep_name} 到 {arr_name}",
                f"路线键 {route_id}",
                f"列车数量 {len(trains)}",
                f"样本车次 {' '.join(train_numbers[:12])}",
            ]
            if duration_candidates:
                text_parts.append(f"典型耗时 {' '.join(duration_candidates[:6])}")
            if bureau_counter:
                text_parts.append("路局分布 " + " ".join(f"{key}:{value}" for key, value in bureau_counter.most_common(6)))
            if model_counter:
                text_parts.append("车型分布 " + " ".join(f"{key}:{value}" for key, value in model_counter.most_common(6)))
            if row["valid_from"] or row["valid_to"]:
                text_parts.append(f"有效期开行 {row['valid_from'] or '?'} 到 {row['valid_to'] or '?'}")

            entities = [
                f"route:{route_id}",
                f"station:{dep_name}",
                f"station:{arr_name}",
            ]
            if dep_token:
                entities.append(f"tele:{dep_token.upper()}")
            if arr_token:
                entities.append(f"tele:{arr_token.upper()}")
            for number in train_numbers[:12]:
                if number:
                    entities.append(f"train:{number}")
            for bureau in bureau_counter:
                entities.append(f"bureau:{bureau}")

            docs.append(
                KnowledgeDoc(
                    doc_id=f"route:{route_id}",
                    kind="route_profile",
                    title=f"区间 {dep_name}->{arr_name}",
                    text="；".join(text_parts),
                    metadata={
                        "route_id": route_id,
                        "dep_name": dep_name,
                        "arr_name": arr_name,
                        "train_count": len(trains),
                    },
                    entities=entities,
                )
            )

        return docs

    def _build_path_docs(self) -> List[KnowledgeDoc]:
        docs: List[KnowledgeDoc] = []
        if not self.db_path.exists():
            return docs

        conn = self._connect()
        try:
            rows = conn.execute("SELECT id, data, valid_from, valid_to FROM path_cache").fetchall()
        finally:
            conn.close()

        for row in rows:
            payload = self._loads_json(row["data"])
            if not isinstance(payload, dict):
                continue

            timetable = payload.get("timetable", [])
            if not isinstance(timetable, list):
                timetable = []

            train_no = str(payload.get("number") or payload.get("trainNo") or row["id"]).strip().upper()
            train_codes = payload.get("numberFull", [])
            if isinstance(train_codes, str):
                train_codes = [train_codes]
            elif not isinstance(train_codes, list):
                train_codes = []

            bureau = str(payload.get("bureauName") or "").strip()
            runner = str(payload.get("runner") or "").strip()
            car = str(payload.get("car") or "").strip()
            car_owner = str(payload.get("carOwner") or "").strip()

            stop_names = []
            for stop in timetable[:16]:
                station_name = str(stop.get("station") or "").strip()
                telecode = str(stop.get("stationTelecode") or "").strip()
                stop_names.append(station_name or self._station_name(telecode))

            start_name = stop_names[0] if stop_names else ""
            end_name = stop_names[-1] if stop_names else ""

            text_parts = [
                f"车次档案 {train_no}",
                f"全称 {' '.join(str(item) for item in train_codes[:4]) or train_no}",
                f"路局 {bureau or '?'}",
                f"担当 {runner or '?'}",
                f"车型 {car or '?'}",
                f"动车段 {car_owner or '?'}",
            ]
            if start_name or end_name:
                text_parts.append(f"始发终到 {start_name or '?'} 到 {end_name or '?'}")
            if stop_names:
                text_parts.append(f"经停样本 {' '.join(name for name in stop_names if name)}")
            if row["valid_from"] or row["valid_to"]:
                text_parts.append(f"有效期开行 {row['valid_from'] or '?'} 到 {row['valid_to'] or '?'}")

            entities = [f"train:{train_no}"]
            for code in train_codes[:4]:
                text = str(code).strip().upper()
                if text:
                    entities.append(f"train:{text}")
            if bureau:
                entities.append(f"bureau:{bureau}")
            if runner:
                entities.append(f"runner:{runner}")
            if car:
                entities.append(f"car:{car}")
            if car_owner:
                entities.append(f"depot:{car_owner}")
            for stop_name in stop_names[:12]:
                if stop_name:
                    entities.append(f"station:{stop_name}")

            docs.append(
                KnowledgeDoc(
                    doc_id=f"train_profile:{train_no}",
                    kind="train_profile",
                    title=f"车次 {train_no}",
                    text="；".join(text_parts),
                    metadata={
                        "train_no": train_no,
                        "bureau": bureau,
                        "runner": runner,
                        "car": car,
                        "car_owner": car_owner,
                        "start_name": start_name,
                        "end_name": end_name,
                    },
                    entities=entities,
                )
            )

        return docs

    def _loads_json(self, raw: Any) -> Any:
        if not isinstance(raw, str):
            return raw
        try:
            return json.loads(raw)
        except Exception:
            return {}

    def _extract_s2s_trains(self, payload: Any) -> List[Dict[str, Any]]:
        if isinstance(payload, list):
            return [item for item in payload if isinstance(item, dict)]
        if isinstance(payload, dict):
            trains = payload.get("trains") or payload.get("data") or []
            if isinstance(trains, list):
                return [item for item in trains if isinstance(item, dict)]
        return []

    def _station_name(self, token: str) -> str:
        if not token:
            return ""
        if len(token) == 3 and token.isascii():
            return station_dict.name_of(token.upper()) or token.upper()
        return token

    def _split_route_id(self, route_id: str) -> tuple[str, str]:
        if "-" not in route_id:
            return route_id, ""
        left, right = route_id.split("-", 1)
        return left.strip(), right.strip()

    def _train_number(self, payload: Dict[str, Any]) -> str:
        number = str(payload.get("number") or "").strip()
        if number:
            return number.upper()

        number_full = payload.get("numberFull", [])
        if isinstance(number_full, list):
            for item in number_full:
                text = str(item).strip()
                if text:
                    return text.upper()
        if isinstance(number_full, str) and number_full.strip():
            return number_full.strip().upper()
        return "?"

    def _station_entities(self, name: str, telecode: str, city: str, province: str) -> List[str]:
        entities = [f"station:{name}", f"tele:{telecode.upper()}"]
        plain_name = name[:-1] if name.endswith("站") else name
        if plain_name and plain_name != name:
            entities.append(f"station:{plain_name}")
        if city:
            entities.append(f"city:{city}")
        if province:
            entities.append(f"province:{province}")
        return entities

    def _tokenize(self, text: str) -> List[str]:
        tokens: List[str] = []
        normalized = str(text or "").lower()
        for chunk in TOKEN_RE.findall(normalized):
            tokens.append(chunk)
            if re.fullmatch(r"[\u4e00-\u9fff]+", chunk):
                for size in (2, 3):
                    if len(chunk) < size:
                        continue
                    for idx in range(len(chunk) - size + 1):
                        tokens.append(chunk[idx:idx + size])
        return tokens


class RailwayKnowledgeRAG:
    def __init__(
        self,
        knowledge_base: Optional[RailwayKnowledgeBase] = None,
        encoder: Optional[HashEmbeddingEncoder] = None,
        cache_size: int = 128,
    ):
        self.knowledge_base = knowledge_base or RailwayKnowledgeBase()
        self.encoder = encoder or HashEmbeddingEncoder()
        self._retrieval_cache = SimpleLRUCache(cache_size)
        self._vector_cache: Dict[str, Dict[int, float]] = {}
        self._vector_signature: Optional[str] = None
        self._lock = threading.RLock()

    def retrieve(
        self,
        user_text: str,
        facts: Optional[Dict[str, Any]] = None,
        context_bundle: Optional[Dict[str, Any]] = None,
        top_k: int = 6,
    ) -> Dict[str, Any]:
        cache_key = self._build_cache_key(user_text, facts, context_bundle, top_k)
        cached = self._retrieval_cache.get(cache_key)
        if cached is not None:
            return cached

        index = self.knowledge_base.get_index()
        self._ensure_vectors(index)
        query_profile = self._build_query_profile(user_text, facts, context_bundle)
        query_vector = self.encoder.encode(query_profile["text"])

        scored = []
        for doc in index["docs"]:
            detail = self._score_doc(doc, index, query_profile, query_vector)
            if detail["score"] > 0:
                scored.append(detail)

        scored.sort(
            key=lambda item: (
                -item["score"],
                -item["entity_hits"],
                item["doc"].title,
            )
        )

        result = {
            "query": user_text,
            "query_profile": {
                "entities": query_profile["entities"][:16],
                "keywords": query_profile["keywords"][:16],
            },
            "top_k": top_k,
            "doc_count": len(index["docs"]),
            "docs": [],
        }
        for item in scored[:top_k]:
            doc = item["doc"]
            result["docs"].append(
                {
                    "doc_id": doc.doc_id,
                    "kind": doc.kind,
                    "title": doc.title,
                    "score": round(item["score"], 4),
                    "reasons": item["reasons"][:4],
                    "metadata": doc.metadata,
                    "snippet": self._build_snippet(doc.text, query_profile["keywords"]),
                }
            )

        self._retrieval_cache.put(cache_key, result)
        return result

    def build_prompt_context(
        self,
        user_text: str,
        facts: Optional[Dict[str, Any]] = None,
        context_bundle: Optional[Dict[str, Any]] = None,
        top_k: int = 5,
    ) -> str:
        result = self.retrieve(user_text, facts=facts, context_bundle=context_bundle, top_k=top_k)
        return self.build_prompt_context_from_result(result)

    def build_prompt_context_from_result(self, result: Optional[Dict[str, Any]]) -> str:
        if not isinstance(result, dict) or not result.get("docs"):
            return ""

        lines = [
            "Knowledge-Augmented Railway Context",
            "Use this local railway knowledge only as grounding support.",
            "If it conflicts with live tool facts, prefer live tool facts.",
            "",
        ]
        for idx, doc in enumerate(result["docs"], start=1):
            lines.append(f"[RAG-{idx}] {doc['title']} | score={doc['score']}")
            if doc["reasons"]:
                lines.append("reasons: " + " | ".join(doc["reasons"]))
            lines.append("snippet: " + doc["snippet"])
        return "\n".join(lines)

    def _ensure_vectors(self, index: Dict[str, Any]):
        signature = str(index["signature"])
        with self._lock:
            if signature == self._vector_signature:
                return

            vectors: Dict[str, Dict[int, float]] = {}
            for doc in index["docs"]:
                vectors[doc.doc_id] = self.encoder.encode(f"{doc.title}\n{doc.text}")

            self._vector_signature = signature
            self._vector_cache = vectors

    def _score_doc(
        self,
        doc: KnowledgeDoc,
        index: Dict[str, Any],
        query_profile: Dict[str, Any],
        query_vector: Dict[int, float],
    ) -> Dict[str, Any]:
        doc_entities = set(doc.entities)
        matched_entities = [entity for entity in query_profile["entities"] if entity in doc_entities]
        doc_tokens = index["doc_tokens"].get(doc.doc_id, set())
        keyword_hits = [token for token in query_profile["keywords"] if token in doc_tokens]

        graph_docs = set()
        for entity in query_profile["entities"]:
            graph_docs.update(index["entity_to_docs"].get(entity, set()))
            for neighbor in index["entity_graph"].get(entity, set()):
                graph_docs.update(index["entity_to_docs"].get(neighbor, set()))

        vector_score = self.encoder.similarity(query_vector, self._vector_cache.get(doc.doc_id, {}))
        graph_boost = 0.18 if doc.doc_id in graph_docs else 0.0
        entity_score = min(1.8, len(matched_entities) * 0.55)
        keyword_score = min(0.9, len(keyword_hits) * 0.16)
        source_boost = 0.12 if doc.kind in {"station_profile", "train_profile"} else 0.0
        total = entity_score + keyword_score + graph_boost + vector_score * 0.65 + source_boost

        reasons = []
        if matched_entities:
            reasons.append("entity=" + ",".join(matched_entities[:4]))
        if keyword_hits:
            reasons.append("keyword=" + ",".join(keyword_hits[:5]))
        if graph_boost > 0:
            reasons.append("graph_expansion")
        if vector_score > 0.18:
            reasons.append(f"vector={vector_score:.2f}")

        return {
            "doc": doc,
            "score": total,
            "entity_hits": len(matched_entities),
            "reasons": reasons,
        }

    def _build_query_profile(
        self,
        user_text: str,
        facts: Optional[Dict[str, Any]],
        context_bundle: Optional[Dict[str, Any]],
    ) -> Dict[str, Any]:
        text_parts = [str(user_text or "").strip()]
        entities: List[str] = []

        for train_no in TRAIN_RE.findall(user_text or ""):
            entities.append(f"train:{train_no.upper()}")
        for emu_no in EMU_RE.findall(user_text or ""):
            entities.append(f"emu:{str(emu_no).upper()}")
        for telecode in TELE_RE.findall(str(user_text or "").upper()):
            entities.append(f"tele:{telecode.upper()}")

        for station_name in self._extract_station_names(user_text):
            entities.append(f"station:{station_name}")

        if isinstance(facts, dict):
            for entry in facts.get("queries", []):
                if not isinstance(entry, dict):
                    continue

                object_name = str(entry.get("object") or "")
                object_id = str(entry.get("id") or "").strip()
                if object_id:
                    text_parts.append(object_id)

                if object_name in {"train", "path_detail", "path_future", "path_past"} and object_id:
                    entities.append(f"train:{object_id.upper()}")
                elif object_name == "emu" and object_id:
                    entities.append(f"emu:{object_id.upper()}")
                elif "-" in object_id:
                    entities.append(f"route:{object_id}")
                    left, right = object_id.split("-", 1)
                    entities.extend(self._route_entities(left, right))

        if isinstance(context_bundle, dict):
            for candidate in context_bundle.get("candidates", [])[:8]:
                if not isinstance(candidate, dict):
                    continue

                attributes = candidate.get("attributes", {})
                if not isinstance(attributes, dict):
                    attributes = {}

                for key in ("train_no", "route", "station", "emu"):
                    value = str(attributes.get(key) or "").strip()
                    if not value:
                        continue
                    text_parts.append(value)
                    if key == "train_no":
                        entities.append(f"train:{value.upper()}")
                    elif key == "route" and "-" in value:
                        left, right = value.split("-", 1)
                        entities.append(f"route:{value}")
                        entities.extend(self._route_entities(left, right))
                    elif key == "station":
                        entities.append(f"station:{value}")
                    elif key == "emu":
                        entities.append(f"emu:{value.upper()}")

        normalized_text = "\n".join(part for part in text_parts if part)
        keywords = self._extract_keywords(normalized_text)
        return {
            "text": normalized_text,
            "entities": list(dict.fromkeys(entity for entity in entities if entity)),
            "keywords": keywords,
        }

    def _extract_station_names(self, text: Optional[str]) -> List[str]:
        if not text:
            return []

        raw_text = str(text)
        names = []
        for _, info in self.knowledge_base._station_entries().items():
            name = str(info.get("name") or "").strip()
            if len(name) < 2:
                continue
            if name in raw_text:
                names.append(name)
                continue
            plain_name = name[:-1] if name.endswith("站") else ""
            if plain_name and len(plain_name) >= 2 and plain_name in raw_text:
                names.append(plain_name)
        return list(dict.fromkeys(names))[:8]

    def _route_entities(self, left: str, right: str) -> List[str]:
        entities = []
        left = left.strip()
        right = right.strip()
        if not left or not right:
            return entities

        left_name = self.knowledge_base._station_name(left)
        right_name = self.knowledge_base._station_name(right)
        entities.extend([f"station:{left_name}", f"station:{right_name}"])
        if len(left) == 3 and left.isascii():
            entities.append(f"tele:{left.upper()}")
        if len(right) == 3 and right.isascii():
            entities.append(f"tele:{right.upper()}")
        return entities

    def _extract_keywords(self, text: str) -> List[str]:
        keywords = []
        for token in self.encoder.tokenize(text):
            cleaned = token.strip()
            if len(cleaned) <= 1 and not cleaned.isdigit():
                continue
            if cleaned not in keywords:
                keywords.append(cleaned)
            if len(keywords) >= 32:
                break
        return keywords

    def _build_snippet(self, text: str, keywords: Iterable[str]) -> str:
        raw = str(text or "").strip().replace("\n", " ")
        if len(raw) <= 220:
            return raw

        for keyword in keywords:
            idx = raw.find(keyword)
            if idx >= 0:
                start = max(0, idx - 70)
                end = min(len(raw), idx + 150)
                return raw[start:end]
        return raw[:220]

    def _build_cache_key(
        self,
        user_text: str,
        facts: Optional[Dict[str, Any]],
        context_bundle: Optional[Dict[str, Any]],
        top_k: int,
    ) -> str:
        payload = {
            "user_text": user_text,
            "top_k": top_k,
            "facts_queries": [
                {
                    "object": item.get("object"),
                    "id": item.get("id"),
                    "date": item.get("date"),
                }
                for item in (facts or {}).get("queries", [])[:12]
                if isinstance(item, dict)
            ],
            "fast_candidates": [
                {
                    "candidate_key": item.get("candidate_key"),
                    "attributes": item.get("attributes"),
                }
                for item in (context_bundle or {}).get("candidates", [])[:8]
                if isinstance(item, dict)
            ],
            "kb_signature": self.knowledge_base.get_index()["signature"],
        }
        digest = hashlib.sha1(
            json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
        ).hexdigest()
        return f"railrag:{digest}"
