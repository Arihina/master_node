"""Регрессия на двойную загрузку эмбеддера.

Раньше `routing/embedding_router.py` создавал `SentenceTransformer` на
уровне модуля. При `python main.py` (uvicorn reload) модель грузилась дважды:
в родителе-супервизоре и в spawn-потомке, что давало CUDA OOM. Теперь модель
и индекс создаются лениво, при первом обращении к роутингу.

Тест намеренно не трогает реальную карту: импорт `main` идёт в отдельном
процессе с `CUDA_VISIBLE_DEVICES=""` и подменённым `sentence_transformers`
(как и остальные STUB-тесты), а проверяется инвариант «при импорте модель и
индекс не созданы».
"""

from __future__ import annotations

import os
import pathlib
import subprocess
import sys
import unittest
from unittest import mock

from tests.support import env  # noqa: F401  — добавляет корень репозитория в sys.path
from tests.support import stubs

stubs.install_dependency_stubs()

from routing import embedding_router  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parents[1]


class NoEagerEmbedderImportTests(unittest.TestCase):
    """Импорт приложения не должен загружать модель или строить индекс."""

    def test_app_import_does_not_load_embedder(self):
        env = {
            **os.environ,
            "CUDA_VISIBLE_DEVICES": "",  # не рискуем реальной картой
            "EMBEDD_DEVICE": "cpu",
        }
        code = "\n".join([
            "import tests.support.stubs as stubs",
            "stubs.install_dependency_stubs()",
            "import main",
            "from routing import embedding_router",
            "assert embedding_router._model is None, (",
            "    'эмбеддер загрузился при импорте main — вернулась eager-загрузка')",
            "assert embedding_router._index is None, (",
            "    'индекс пассажей собран при импорте main — модель грузится зря')",
        ])
        proc = subprocess.run(
            [sys.executable, "-c", code],
            cwd=ROOT, env=env, capture_output=True, text=True, timeout=120,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)


class LazyEmbedderSingletonTests(unittest.TestCase):
    """`get_embed_model()` грузит ровно одну копию и передаёт device."""

    def setUp(self):
        self._old_model = embedding_router._model
        embedding_router._model = None
        self.addCleanup(self._restore)

    def _restore(self):
        embedding_router._model = self._old_model

    def test_model_is_loaded_once_with_configured_device(self):
        calls = []

        class FakeSentenceTransformer:
            def __init__(self, model_name, **kwargs):
                calls.append((model_name, kwargs))

            def encode(self, *args, **kwargs):
                return [0.0]

        with mock.patch.object(embedding_router.settings,
                               "embedd_device", "cpu"), \
                mock.patch.object(embedding_router, "SentenceTransformer",
                                  FakeSentenceTransformer):
            first = embedding_router.get_embed_model()
            second = embedding_router.get_embed_model()

        self.assertIs(first, second, "второй вызов вернул новую копию модели")
        self.assertEqual(len(calls), 1,
                         f"модель создана {len(calls)} раз(а) вместо одного")
        self.assertEqual(calls[0][1]["device"], "cpu")
        self.assertTrue(calls[0][1]["local_files_only"])


class RegistryIndexUpdateTests(unittest.TestCase):
    """Правка реестра не должна грузить модель, а уже собранный индекс
    обязана обновлять атомарно (copy-on-write)."""

    def setUp(self):
        self._old_index = embedding_router._index
        self._old_model = embedding_router._model
        embedding_router._index = None
        embedding_router._model = None
        self.addCleanup(self._restore)

    def _restore(self):
        embedding_router._index = self._old_index
        embedding_router._model = self._old_model

    def test_updates_existing_index_without_loading_model(self):
        index = embedding_router.EmbeddingIndex(encoder=None)
        index.replace({"a": 1.0, "c": 3.0})
        embedding_router._index = index
        agents = {"a": object()}

        with mock.patch.object(embedding_router, "AGENTS", agents), \
                mock.patch.object(embedding_router, "get_embed_model") as load:
            embedding_router.apply_registry_update(
                {"a": object(), "b": object()},
                vectors={"b": 2.0},
                removed={"c"},
            )

        load.assert_not_called()
        self.assertEqual(embedding_router.index_ids(), {"a", "b"})
        self.assertEqual(set(agents), {"a", "b"})

    def test_drops_index_when_precomputed_vectors_are_missing(self):
        """Индекс успел создаться в параллельном потоке — сбрасываем его,
        чтобы он пересобрался из новых AGENTS, а не остался со старыми."""
        index = embedding_router.EmbeddingIndex(encoder=None)
        index.replace({"a": 1.0})
        embedding_router._index = index

        with mock.patch.object(embedding_router, "AGENTS", {}):
            embedding_router.apply_registry_update(
                {"a": object(), "b": object()},
                vectors={},
                removed=set(),
                needs_rebuild=True,
            )

        self.assertIsNone(embedding_router._index)

    def test_does_not_build_index_when_it_was_not_loaded(self):
        with mock.patch.object(embedding_router, "AGENTS", {}), \
                mock.patch.object(embedding_router, "get_embed_model") as load:
            embedding_router.apply_registry_update(
                {"a": object()}, vectors={}, removed=set())

        load.assert_not_called()
        self.assertIsNone(embedding_router._index)


if __name__ == "__main__":
    unittest.main()
