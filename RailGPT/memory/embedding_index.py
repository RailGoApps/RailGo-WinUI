from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
import warnings
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

import numpy as np


_LOCAL_MODEL_REQUIRED_FILES = (
    "modules.json",
    "config.json",
    "tokenizer_config.json",
)
_LOCAL_MODEL_WEIGHT_FILES = (
    "model.safetensors",
    "pytorch_model.bin",
    "pytorch_model.bin.index.json",
)
_LOCAL_MODEL_REQUIRED_DIRS = ("1_Pooling",)


def _iter_candidate_model_roots() -> List[Path]:
    roots: List[Path] = []

    for env_name in ("RAILGPT_BUNDLE_ROOT", "RAILGPT_APP_ROOT"):
        value = str(os.environ.get(env_name) or "").strip()
        if value:
            roots.append(Path(value))

    try:
        from app_runtime import app_root, runtime_root

        roots.extend([app_root(), runtime_root()])
    except Exception:
        pass

    roots.append(Path(__file__).resolve().parents[1])

    unique_roots: List[Path] = []
    seen = set()
    for root in roots:
        try:
            resolved = root.resolve(strict=False)
        except Exception:
            resolved = root
        key = os.path.normcase(str(resolved))
        if key in seen:
            continue
        seen.add(key)
        unique_roots.append(resolved)
    return unique_roots


def _looks_like_sentence_transformer_dir(path: Path) -> bool:
    if not path.is_dir():
        return False
    for filename in _LOCAL_MODEL_REQUIRED_FILES:
        if not path.joinpath(filename).is_file():
            return False
    if not any(path.joinpath(filename).is_file() for filename in _LOCAL_MODEL_WEIGHT_FILES):
        return False
    for dirname in _LOCAL_MODEL_REQUIRED_DIRS:
        if not path.joinpath(dirname).is_dir():
            return False
    return True


def resolve_sentence_transformer_source(
    model_name: str,
    candidate_roots: Optional[Iterable[Path]] = None,
) -> Tuple[str, bool]:
    raw_name = str(model_name or "").strip()
    if not raw_name:
        return raw_name, False

    explicit_path = Path(raw_name)
    if _looks_like_sentence_transformer_dir(explicit_path):
        return str(explicit_path.resolve(strict=False)), True

    if candidate_roots is None:
        roots = _iter_candidate_model_roots()
    else:
        roots = [Path(root) for root in candidate_roots]

    model_parts = [part for part in raw_name.replace("\\", "/").split("/") if part and part not in (".", "..")]
    if not model_parts:
        return raw_name, False

    for root in roots:
        candidate = root.joinpath("models", *model_parts)
        if _looks_like_sentence_transformer_dir(candidate):
            return str(candidate.resolve(strict=False)), True

    return raw_name, False


def _default_hf_cache_dir() -> Path:
    try:
        from app_runtime import writable_path

        cache_dir = Path(writable_path("hf_cache"))
    except Exception:
        cache_dir = Path(__file__).resolve().parents[1].joinpath("hf_cache")

    cache_dir.mkdir(parents=True, exist_ok=True)
    for subdir in ("hub", "transformers", "sentence_transformers"):
        cache_dir.joinpath(subdir).mkdir(parents=True, exist_ok=True)
    return cache_dir


class MemoryEmbedder:
    def __init__(
        self,
        enabled: bool = False,
        model_name: str = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2",
    ):
        self.enabled = enabled
        self.model_name = model_name
        self._model = None
        self._load_failed = False
        self._lock = threading.Lock()
        self._warming = False
        self._ready_event = threading.Event()
        self._cache_dir = _default_hf_cache_dir()
        self.model_source, self.using_local_model = resolve_sentence_transformer_source(model_name)

        os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
        os.environ.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "1")
        os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")
        os.environ.setdefault("HF_HOME", str(self._cache_dir))
        os.environ.setdefault("HUGGINGFACE_HUB_CACHE", str(self._cache_dir.joinpath("hub")))
        os.environ.setdefault("TRANSFORMERS_CACHE", str(self._cache_dir.joinpath("transformers")))
        os.environ.setdefault("SENTENCE_TRANSFORMERS_HOME", str(self._cache_dir.joinpath("sentence_transformers")))
        os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
        os.environ.setdefault("TRANSFORMERS_NO_ADVISORY_WARNINGS", "1")
        os.environ.setdefault("TRANSFORMERS_VERBOSITY", "error")
        logging.getLogger("huggingface_hub").setLevel(logging.ERROR)
        logging.getLogger("sentence_transformers").setLevel(logging.ERROR)
        logging.getLogger("transformers").setLevel(logging.ERROR)

    def _ensure_model(self, block: bool = True):
        if not self.enabled or self._load_failed:
            return None

        with self._lock:
            if self._model is not None:
                return self._model
            if self._load_failed:
                return None
            if not self._warming:
                self._warming = True
                self._ready_event = threading.Event()
                ready_event = self._ready_event
                should_start = True
            else:
                ready_event = self._ready_event
                should_start = False

        if should_start:
            if not block:
                threading.Thread(
                    target=self._load_model_worker,
                    args=(ready_event,),
                    daemon=True,
                ).start()
                return None
            return self._load_model_worker(ready_event)

        if not block:
            return None

        ready_event.wait()
        with self._lock:
            return self._model

    def _load_model_worker(self, ready_event: threading.Event):
        model = None
        load_failed = False
        try:
            from sentence_transformers import SentenceTransformer

            with warnings.catch_warnings():
                warnings.filterwarnings(
                    "ignore",
                    message=".*symlinks.*",
                    category=UserWarning,
                )
                load_kwargs = {"cache_folder": str(self._cache_dir)}
                if self.using_local_model:
                    load_kwargs["local_files_only"] = True
                try:
                    model = SentenceTransformer(self.model_source, **load_kwargs)
                except TypeError:
                    load_kwargs.pop("local_files_only", None)
                    try:
                        model = SentenceTransformer(self.model_source, **load_kwargs)
                    except TypeError:
                        model = SentenceTransformer(self.model_source)
        except Exception:
            load_failed = True
        finally:
            with self._lock:
                if model is not None:
                    self._model = model
                if load_failed:
                    self._load_failed = True
                self._warming = False
            ready_event.set()
        return model

    def warmup_async(self):
        self._ensure_model(block=False)

    def embed_texts(self, texts: Iterable[str], block: bool = True) -> Optional[np.ndarray]:
        model = self._ensure_model(block=block)
        if model is None:
            return None

        values = [str(text or "") for text in texts]
        if not values:
            return np.zeros((0, 0), dtype=float)

        embeddings = model.encode(
            values,
            normalize_embeddings=True,
            show_progress_bar=False,
        )
        return np.asarray(embeddings, dtype=float)

    @property
    def is_ready(self) -> bool:
        return self._model is not None


def _embed_with_fallback(embedder, texts: Iterable[str], block: bool) -> Optional[np.ndarray]:
    try:
        return embedder.embed_texts(texts, block=block)
    except TypeError:
        return embedder.embed_texts(texts)


class JsonEmbeddingCache:
    def __init__(self, path: str, model_name: str):
        self.path = path
        self.model_name = model_name
        self._lock = threading.Lock()
        os.makedirs(os.path.dirname(self.path), exist_ok=True)

    def _load(self) -> Dict[str, Dict[str, object]]:
        if not os.path.exists(self.path):
            return {"model_name": self.model_name, "items": {}}
        try:
            with open(self.path, "r", encoding="utf-8") as handle:
                data = json.load(handle)
        except Exception:
            return {"model_name": self.model_name, "items": {}}

        if data.get("model_name") != self.model_name:
            return {"model_name": self.model_name, "items": {}}
        if not isinstance(data.get("items"), dict):
            data["items"] = {}
        return data

    def _save(self, data: Dict[str, Dict[str, object]]):
        with open(self.path, "w", encoding="utf-8") as handle:
            json.dump(data, handle, ensure_ascii=False, indent=2)

    @staticmethod
    def _signature(text: str) -> str:
        return hashlib.sha1(str(text or "").encode("utf-8")).hexdigest()

    def get_vectors(
        self,
        entries: List[Dict[str, str]],
        embedder: MemoryEmbedder,
        block: bool = False,
    ) -> Dict[str, np.ndarray]:
        if not entries or not embedder.enabled:
            return {}

        with self._lock:
            data = self._load()
            items = data.setdefault("items", {})
            missing_keys: List[str] = []
            missing_texts: List[str] = []

            for entry in entries:
                key = str(entry.get("key") or "").strip()
                text = str(entry.get("text") or "")
                signature = self._signature(text)
                cached = items.get(key)
                if not isinstance(cached, dict) or cached.get("signature") != signature:
                    missing_keys.append(key)
                    missing_texts.append(text)

            if missing_texts:
                embeddings = _embed_with_fallback(embedder, missing_texts, block=block)
                if embeddings is None:
                    return {}
                for key, text, vector in zip(missing_keys, missing_texts, embeddings):
                    items[key] = {
                        "signature": self._signature(text),
                        "vector": np.asarray(vector, dtype=float).tolist(),
                    }
                self._save(data)

            vectors: Dict[str, np.ndarray] = {}
            for entry in entries:
                key = str(entry.get("key") or "").strip()
                payload = items.get(key)
                if not isinstance(payload, dict):
                    continue
                vector = payload.get("vector")
                if isinstance(vector, list) and vector:
                    vectors[key] = np.asarray(vector, dtype=float)
            return vectors

    def query(
        self,
        query_text: str,
        entries: List[Dict[str, str]],
        embedder: MemoryEmbedder,
        block: bool = False,
    ) -> Dict[str, float]:
        if not entries or not embedder.enabled:
            return {}

        query_vector = _embed_with_fallback(embedder, [query_text], block=block)
        if query_vector is None or query_vector.size == 0:
            return {}

        cached_vectors = self.get_vectors(entries, embedder, block=block)
        if not cached_vectors:
            return {}

        scores: Dict[str, float] = {}
        query = np.asarray(query_vector[0], dtype=float)
        for entry in entries:
            key = str(entry.get("key") or "").strip()
            vector = cached_vectors.get(key)
            if vector is None or vector.size == 0:
                continue
            scores[key] = float(np.dot(query, vector))
        return scores
