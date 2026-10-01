import asyncio
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
import json
import logging
import os
from time import perf_counter
from uuid import uuid4

import numpy as np
from .encoder import Encoder, DIMENSION
from . import SERVICE_API_VERSION
from .storage import Store

logger = logging.getLogger("e5_service")


class QueueFull(Exception):
    pass


class NotReady(Exception):
    pass


@dataclass
class Job:
    request_id: str
    operation: str
    payload: dict
    created_at: float
    future: asyncio.Future


class Service:
    def __init__(self, settings, encoder_factory=Encoder):
        self.settings = settings
        self.encoder_factory = encoder_factory
        self.queue = asyncio.Queue(maxsize=settings.max_queue_size)
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="e5-inference")
        self.ready = False
        self.library_size = 0
        self.requests_total = self.errors_total = self.completed = self.batch_total = 0
        self.wait_samples = deque(maxlen=4096)
        self.inference_samples = deque(maxlen=4096)
        self.store = None

    async def start(self):
        loop = asyncio.get_running_loop()
        try:
            await loop.run_in_executor(self.executor, self._initialize)
        except BaseException:
            self.executor.shutdown(wait=True)
            raise
        self.ready = True
        self.worker = asyncio.create_task(self._worker())

    def _initialize(self):
        self.encoder = self.encoder_factory(self.settings)
        self.store = Store(self.settings, self.encoder)
        self.library_size = len(self.store.ids)

    async def stop(self):
        self.ready = False
        # Drain accepted work before closing SQLite and the model thread.
        await self.queue.join()
        await self.queue.put(None)
        await self.worker
        await asyncio.get_running_loop().run_in_executor(self.executor, self.store.close)
        self.executor.shutdown(wait=True)

    async def submit(self, operation, payload):
        if not self.ready:
            raise NotReady()
        self.requests_total += 1
        future = asyncio.get_running_loop().create_future()
        job = Job(uuid4().hex, operation, payload, perf_counter(), future)
        try:
            self.queue.put_nowait(job)
        except asyncio.QueueFull:
            self.errors_total += 1
            raise QueueFull() from None
        try:
            return await asyncio.shield(future)
        except asyncio.CancelledError:
            # Accepted writes still finish. Consume their exception if the caller
            # disconnects, without cancelling the worker or changing FIFO order.
            future.add_done_callback(lambda f: f.exception() if not f.cancelled() else None)
            raise

    async def _worker(self):
        loop = asyncio.get_running_loop()
        while True:
            job = await self.queue.get()
            if job is None:
                self.queue.task_done()
                return
            started = perf_counter()
            wait_ms = (started - job.created_at) * 1000
            status = "ok"
            try:
                result = await loop.run_in_executor(self.executor, self._execute, job)
                job.future.set_result(result)
            except Exception as exc:
                status = "error"
                self.errors_total += 1
                job.future.set_exception(exc)
            finally:
                elapsed = (perf_counter() - started) * 1000
                batch_size = len(job.payload.get("texts", [])) or 1
                self.wait_samples.append(wait_ms)
                self.inference_samples.append(elapsed)
                self.completed += 1
                self.batch_total += batch_size
                logger.info(json.dumps({
                    "request_id": job.request_id, "operation": job.operation,
                    "queue_wait_ms": round(wait_ms, 3), "inference_ms": round(elapsed, 3),
                    "batch_size": batch_size, "model_version": self.settings.model_version, "status": status,
                }))
                self.queue.task_done()

    def _execute(self, job):
        try:
            if job.operation == "encode":
                return {"embeddings": self.encoder.encode(job.payload["texts"], "query").tolist(), "dimension": DIMENSION}
            if job.operation == "similarity":
                return self.store.similarity(job.payload)
            if job.operation == "upsert":
                return self.store.upsert(job.payload)
            if job.operation == "delete":
                return self.store.delete(job.payload["field_id"])
            if job.operation == "feedback":
                return self.store.feedback(job.payload)
            raise ValueError("Unknown operation")
        finally:
            self.library_size = len(self.store.ids)

    def health(self):
        return {"status": "ready" if self.ready else "starting",
                "service": "local-e5-field-embedding", "pid": os.getpid(),
                "api_version": SERVICE_API_VERSION,
                "configuration": self.settings.identity(),
                "model": "multilingual-e5-small",
                "precision": getattr(getattr(self, "encoder", None), "precision", "unknown"), "model_version": self.settings.model_version,
                "library_size": self.library_size, "queue_size": self.queue.qsize()}

    def metrics(self):
        def percentile(samples, p):
            return float(np.percentile(list(samples), p)) if samples else 0.0
        return {
            "requests_total": self.requests_total, "queue_depth": self.queue.qsize(),
            "queue_wait_p50": percentile(self.wait_samples, 50), "queue_wait_p95": percentile(self.wait_samples, 95),
            "inference_p50": percentile(self.inference_samples, 50), "inference_p95": percentile(self.inference_samples, 95),
            "avg_batch_size": self.batch_total / self.completed if self.completed else 0.0,
            "errors_total": self.errors_total,
        }
