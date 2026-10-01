import asyncio
import importlib.util
import json
from pathlib import Path
import sqlite3
import threading
from types import SimpleNamespace

import httpx
import numpy as np
import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.encoder import DIMENSION, Encoder, normalize
from app.main import create_app
from app.service import Service
from app.storage import Store


class FakeEncoder:
    def __init__(self, settings):
        self.calls = []
        self.block = False
        self.entered = threading.Event()
        self.release = threading.Event()

    def encode(self, texts, role="query"):
        self.calls.append((list(texts), role, threading.get_ident()))
        if self.block:
            self.entered.set()
            assert self.release.wait(5)
        vectors = np.zeros((len(texts), DIMENSION), np.float32)
        for i, text in enumerate(texts):
            vectors[i, 0 if ("visa" in text or "签证" in text) else 1] = 1
        return vectors


def settings(tmp_path, **kwargs):
    return Settings(tmp_path / "model", tmp_path / "data", **kwargs)


def field(identifier="visa", text="visa sponsorship"):
    return {"field_id": identifier, "canonical_text": text, "aliases": ["签证"], "field_type": "boolean"}


def test_http_library_feedback_persistence(tmp_path):
    config = settings(tmp_path)
    app = create_app(config, FakeEncoder)
    with TestClient(app) as client:
        assert client.get("/health").json()["library_size"] == 0
        encoded = client.post("/v1/encode", json={"texts": ["签证", "education"]}).json()
        assert encoded["dimension"] == 384
        assert np.allclose(np.linalg.norm(encoded["embeddings"], axis=1), 1)
        assert client.post("/v1/similarity", json={"texts": ["visa"]}).json()["results"][0]["candidates"] == []
        for definition in [field(), field("degree", "education degree")]:
            assert client.post("/v1/library/upsert", json=definition).status_code == 200
        result = client.post("/v1/similarity", json={"texts": ["签证"], "top_k": 5}).json()
        assert result["results"][0]["candidates"] == [{"field_id": "visa", "cosine": 1.0}, {"field_id": "degree", "cosine": 0.0}]
        feedback = {"query_text": "visa?", "positive_field_id": "visa",
                    "retrieved_candidates": [["visa", 1], ["degree", 0]], "source": "human_confirmed", "language_pair": "en-en"}
        assert client.post("/v1/feedback", json=feedback).status_code == 200
        feedback["positive_field_id"] = "missing"
        assert client.post("/v1/feedback", json=feedback).status_code == 404
        assert client.delete("/v1/library/degree").status_code == 200
        assert client.delete("/v1/library/degree").status_code == 404
    with TestClient(create_app(config, FakeEncoder)) as client:
        assert client.get("/health").json()["library_size"] == 1
        assert client.post("/v1/similarity", json={"texts": ["签证"]}).json()["results"][0]["candidates"][0]["field_id"] == "visa"
    with sqlite3.connect(config.data_dir / "fields.db") as db:
        assert db.execute("SELECT active FROM fields WHERE field_id='degree'").fetchone() == (0,)
    with sqlite3.connect(config.data_dir / "training_feedback.db") as db:
        row = db.execute("SELECT definitions_json FROM feedback").fetchone()
        assert json.loads(row[0])["degree"] == "education degree"


@pytest.mark.parametrize("path,payload", [
    ("/v1/encode", {"texts": []}),
    ("/v1/encode", {"texts": ["x"] * 65}),
    ("/v1/encode", {"texts": [" "]}),
    ("/v1/encode", {"texts": ["x" * 8193]}),
    ("/v1/encode", {"texts": ["secret"], "value": "private-answer"}),
    ("/v1/similarity", {"texts": ["x"], "top_k": 0}),
    ("/v1/similarity", {"texts": ["x"], "top_k": 1.5}),
    ("/v1/library/upsert", {**field(), "value": "private-answer"}),
    ("/v1/feedback", {"query_text": "x", "positive_field_id": "visa", "source": "unconfirmed"}),
])
def test_input_limits_and_no_answer_storage(tmp_path, path, payload):
    with TestClient(create_app(settings(tmp_path), FakeEncoder)) as client:
        response = client.post(path, json=payload)
        assert response.status_code == 422
        assert "private-answer" not in response.text


def test_request_byte_limit(tmp_path):
    with TestClient(create_app(settings(tmp_path), FakeEncoder)) as client:
        assert client.post("/v1/encode", content=b" " * (1024 * 1024 + 1)).status_code == 413


def test_fifo_backpressure_health_and_single_thread(tmp_path):
    async def run():
        app = create_app(settings(tmp_path, max_queue_size=1), FakeEncoder)
        async with app.router.lifespan_context(app):
            service = app.state.service
            encoder = service.encoder
            encoder.block = True
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                first = asyncio.create_task(client.post("/v1/encode", json={"texts": ["first"]}))
                try:
                    assert await asyncio.to_thread(encoder.entered.wait, 2)
                    second = asyncio.create_task(client.post("/v1/encode", json={"texts": ["second"]}))
                    for _ in range(100):
                        if service.queue.qsize() == 1:
                            break
                        await asyncio.sleep(0.001)
                    assert service.queue.qsize() == 1
                    rejected = await client.post("/v1/encode", json={"texts": ["third"]})
                    assert rejected.status_code == 429 and rejected.json() == {"error": "queue_full"}
                    health = await asyncio.wait_for(client.get("/health"), timeout=0.5)
                    assert health.json()["status"] == "ready"
                finally:
                    encoder.release.set()
                assert (await first).status_code == 200
                assert (await second).status_code == 200
                assert [x[0] for x in encoder.calls] == [["first"], ["second"]]
                assert len({x[2] for x in encoder.calls}) == 1
                metrics = (await client.get("/metrics")).json()
                assert metrics["requests_total"] == 3
                assert metrics["errors_total"] == 1
                assert metrics["queue_depth"] == 0
    asyncio.run(run())


def test_cancelled_caller_does_not_drop_write(tmp_path):
    async def run():
        service = Service(settings(tmp_path), FakeEncoder)
        await service.start()
        try:
            service.encoder.block = True
            write = asyncio.create_task(service.submit("upsert", field()))
            assert await asyncio.to_thread(service.encoder.entered.wait, 2)
            write.cancel()
            with pytest.raises(asyncio.CancelledError):
                await write
            service.encoder.release.set()
            await service.queue.join()
            assert service.library_size == 1
            with pytest.raises(KeyError):
                await service.submit("delete", {"field_id": "missing"})
            assert (await service.submit("encode", {"texts": ["ok"]}))["dimension"] == 384
        finally:
            service.encoder.release.set()
            await service.stop()
    asyncio.run(run())


def test_cache_version_change_rebuilds_and_reactivation(tmp_path):
    config = settings(tmp_path)
    encoder = FakeEncoder(config)
    store = Store(config, encoder)
    store.upsert(field())
    store.close()
    cached = FakeEncoder(config)
    store = Store(config, cached)
    assert cached.calls == []
    store.close()
    new_config = settings(tmp_path, model_version="v2")
    updated = FakeEncoder(new_config)
    store = Store(new_config, updated)
    assert len(updated.calls) == 1 and updated.calls[0][1] == "passage"
    assert store.db.execute("SELECT model_version FROM fields").fetchone()[0] == "v2"
    store.delete("visa")
    store.upsert(field())
    assert store.ids == ["visa"]
    store.close()


def test_corrupt_cache_is_repaired(tmp_path):
    config = settings(tmp_path)
    store = Store(config, FakeEncoder(config))
    store.upsert(field())
    store.db.execute("UPDATE fields SET embedding=?", (b"bad",))
    store.db.commit()
    store.close()
    encoder = FakeEncoder(config)
    store = Store(config, encoder)
    assert len(encoder.calls) == 1 and store.matrix.shape == (1, 384)
    store.close()


def test_second_instance_rejected_and_lock_released(tmp_path):
    config = settings(tmp_path)
    with TestClient(create_app(config, FakeEncoder)):
        with pytest.raises(RuntimeError, match="Another E5"):
            with TestClient(create_app(config, FakeEncoder)):
                pass
    with TestClient(create_app(config, FakeEncoder)) as client:
        assert client.get("/health").status_code == 200


def test_pooling_padding_and_prefixes():
    encoder = Encoder.__new__(Encoder)
    seen = []
    class Tokenizer:
        def encode_batch(self, texts):
            seen.extend(texts)
            return [SimpleNamespace(ids=[1, 2], attention_mask=[1, 0], type_ids=[0, 0])]
    class Session:
        def run(self, names, feed):
            hidden = np.zeros((1, 2, 384), np.float32)
            hidden[0, 0, 0], hidden[0, 1, 1] = 2, 100
            return [hidden]
    encoder.tokenizer, encoder.session = Tokenizer(), Session()
    encoder.inputs = ["input_ids", "attention_mask"]
    vector = encoder.encode(["测试"], "passage")
    assert seen == ["passage: 测试"]
    assert vector[0, 0] == 1 and vector[0, 1] == 0


def test_export_snapshots(tmp_path):
    config = settings(tmp_path)
    store = Store(config, FakeEncoder(config))
    store.upsert(field())
    store.upsert(field("degree", "original degree"))
    store.feedback({"query_text": "visa", "positive_field_id": "visa", "retrieved_candidates": [("degree", 0.5)],
                    "source": "human_confirmed", "language_pair": "en-en"})
    store.upsert(field("degree", "changed degree"))
    store.close()
    script = Path(__file__).resolve().parents[1] / "scripts" / "export_feedback.py"
    spec = importlib.util.spec_from_file_location("export_feedback", script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    target = tmp_path / "dataset.jsonl"
    module.export(config.data_dir / "training_feedback.db", target)
    assert json.loads(target.read_text(encoding="utf-8"))["hard_negatives"] == ["original degree"]
