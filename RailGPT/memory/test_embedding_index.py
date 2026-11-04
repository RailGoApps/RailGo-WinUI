import tempfile
import unittest
from pathlib import Path

from memory.embedding_index import resolve_sentence_transformer_source


class EmbeddingIndexTest(unittest.TestCase):
    def _build_local_model_dir(self, root_dir: str) -> Path:
        model_dir = Path(root_dir).joinpath(
            "models",
            "sentence-transformers",
            "paraphrase-multilingual-MiniLM-L12-v2",
        )
        model_dir.joinpath("1_Pooling").mkdir(parents=True, exist_ok=True)
        model_dir.joinpath("modules.json").write_text("{}", encoding="utf-8")
        model_dir.joinpath("config.json").write_text("{}", encoding="utf-8")
        model_dir.joinpath("tokenizer_config.json").write_text("{}", encoding="utf-8")
        model_dir.joinpath("model.safetensors").write_text("stub", encoding="utf-8")
        return model_dir

    def test_resolve_sentence_transformer_source_prefers_project_local_model(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            model_dir = self._build_local_model_dir(temp_dir)

            source, is_local = resolve_sentence_transformer_source(
                "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2",
                candidate_roots=[temp_dir],
            )

        self.assertTrue(is_local)
        self.assertEqual(source, str(model_dir.resolve(strict=False)))

    def test_resolve_sentence_transformer_source_falls_back_when_local_bundle_is_incomplete(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            model_dir = Path(temp_dir).joinpath(
                "models",
                "sentence-transformers",
                "paraphrase-multilingual-MiniLM-L12-v2",
            )
            model_dir.mkdir(parents=True, exist_ok=True)
            model_dir.joinpath("modules.json").write_text("{}", encoding="utf-8")

            source, is_local = resolve_sentence_transformer_source(
                "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2",
                candidate_roots=[temp_dir],
            )

        self.assertFalse(is_local)
        self.assertEqual(source, "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2")


if __name__ == "__main__":
    unittest.main()
