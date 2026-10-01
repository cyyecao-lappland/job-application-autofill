from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import FastAPI, Path
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from .config import Settings
from . import SERVICE_API_VERSION
from .encoder import Encoder
from .lock import InstanceLock
from .schemas import EncodeRequest, SimilarityRequest, FieldDefinition, Feedback, SearchRequest
from .service import Service, QueueFull, NotReady


class BodyLimit:
    """Bound raw request bytes before JSON parsing, including chunked requests."""
    def __init__(self, app, limit=1024 * 1024):
        self.app, self.limit = app, limit

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        chunks = []
        size = 0
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            size += len(message.get("body", b""))
            if size > self.limit:
                return await JSONResponse({"error": "body_too_large"}, status_code=413)(scope, receive, send)
            chunks.append(message)
            if not message.get("more_body", False):
                break
        async def replay():
            return chunks.pop(0) if chunks else await receive()
        await self.app(scope, replay, send)


def create_app(settings=None, encoder_factory=Encoder):
    settings = settings or Settings.from_env()

    @asynccontextmanager
    async def lifespan(app):
        lock = InstanceLock(settings.data_dir)
        service = Service(settings, encoder_factory)
        app.state.service = service
        try:
            await service.start()
            try:
                yield
            finally:
                await service.stop()
        finally:
            lock.close()

    app = FastAPI(title="Local Field Retrieval Service", version=SERVICE_API_VERSION, lifespan=lifespan)
    app.add_middleware(BodyLimit)

    @app.exception_handler(RequestValidationError)
    async def invalid_request(request, exc):
        # FastAPI's default validation response includes submitted input.
        return JSONResponse({"error": "invalid_request", "fields": [list(e["loc"]) for e in exc.errors()]}, status_code=422)

    async def dispatch(operation, payload):
        texts = payload.get("texts", [])
        if len(texts) > settings.max_batch_size or any(len(x) > settings.max_text_length for x in texts):
            return JSONResponse({"error": "batch_limit_exceeded"}, status_code=422)
        try:
            return await app.state.service.submit(operation, payload)
        except QueueFull:
            return JSONResponse({"error": "queue_full"}, status_code=429)
        except NotReady:
            return JSONResponse({"error": "not_ready"}, status_code=503)
        except KeyError:
            return JSONResponse({"error": "field_not_found"}, status_code=404)
        except Exception:
            return JSONResponse({"error": "operation_failed"}, status_code=500)

    @app.post("/v1/encode")
    async def encode(body: EncodeRequest):
        return await dispatch("encode", body.model_dump())

    @app.post("/v1/similarity")
    async def similarity(body: SimilarityRequest):
        return await dispatch("similarity", body.model_dump())

    @app.post("/v1/library/upsert")
    async def upsert(body: FieldDefinition):
        return await dispatch("upsert", body.model_dump())

    @app.post("/v1/search")
    async def search(body: SearchRequest):
        return await dispatch("similarity", body.model_dump())

    @app.delete("/v1/library/{field_id}")
    async def delete(field_id: Annotated[str, Path(pattern=r"^[A-Za-z0-9_.-]{1,128}$")]):
        return await dispatch("delete", {"field_id": field_id})

    @app.post("/v1/feedback")
    async def feedback(body: Feedback):
        return await dispatch("feedback", body.model_dump())

    @app.get("/health")
    async def health():
        return app.state.service.health()

    @app.get("/metrics")
    async def metrics():
        return app.state.service.metrics()

    return app
