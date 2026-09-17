"""Private HTTP API for one user's bridge and GitHub publisher; run one worker."""

from datetime import date
import hmac
import os
import re
from typing import Annotated
from urllib.parse import urlparse

from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel, Field, field_validator

from .core import Assistant, OpenAIModel, render_digest
from .store import Store


class PaperInput(BaseModel):
    title: str = Field(min_length=1, max_length=1000)
    abstract: str = Field(default="", max_length=12000)
    url: str = Field(max_length=1000)
    pdf_url: str | None = Field(default=None, max_length=1000)
    full_text: str | None = Field(default=None, max_length=16000)
    tldr: str | None = Field(default=None, max_length=4000)
    score: float | None = Field(default=None, allow_inf_nan=False)

    @field_validator("url", "pdf_url")
    @classmethod
    def valid_url(cls, value):
        if value is None:
            return value
        parsed = urlparse(value)
        if parsed.scheme not in ("https", "http") or not parsed.hostname or parsed.username or parsed.password:
            raise ValueError("A public HTTP(S) paper link is required")
        return value


class DigestInput(BaseModel):
    date: date
    papers: list[PaperInput] = Field(max_length=30)
    deliver: bool = True


class ChatInput(BaseModel):
    message_id: str = Field(min_length=1, max_length=160, pattern=r"^[a-zA-Z0-9_.:-]+$")
    text: str = Field(min_length=1, max_length=3000)
    deliver: bool = False


class AckInput(BaseModel):
    id: str = Field(max_length=300)
    lease_token: str = Field(max_length=100)


class BodyLimit:
    def __init__(self, app, limit=2_000_000):
        self.app, self.limit = app, limit

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        messages, size = [], 0
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            size += len(message.get("body", b""))
            if size > self.limit:
                await send({"type": "http.response.start", "status": 413, "headers": []})
                await send({"type": "http.response.body", "body": b"Payload too large"})
                return
            messages.append(message)
            if not message.get("more_body", False):
                break
        async def replay():
            return messages.pop(0) if messages else await receive()
        return await self.app(scope, replay, send)


def create_app(store=None, token=None, model=None):
    token = token or os.environ.get("ASSISTANT_TOKEN", "")
    if len(token) < 32 or not re.fullmatch(r"[A-Za-z0-9_-]+", token):
        raise ValueError("Set ASSISTANT_TOKEN to a random URL-safe token of at least 32 characters")
    if model is None and os.environ.get("ASSISTANT_MODEL"):
        key = os.environ.get("ASSISTANT_LLM_KEY", "")
        if not key:
            raise ValueError("ASSISTANT_MODEL requires ASSISTANT_LLM_KEY")
        model = OpenAIModel(key, os.environ.get("ASSISTANT_LLM_BASE") or "https://api.openai.com/v1",
                            os.environ["ASSISTANT_MODEL"])
    store = store or Store(os.environ.get("ASSISTANT_DB", "data/assistant.sqlite3"))
    assistant = Assistant(store, model)
    app = FastAPI(title="Paper Assistant", docs_url=None, redoc_url=None, openapi_url=None)
    app.add_middleware(BodyLimit)
    app.state.assistant = assistant

    def authorized(authorization: Annotated[str | None, Header()] = None):
        if not hmac.compare_digest((authorization or "").encode(), ("Bearer " + token).encode()):
            raise HTTPException(401, "Unauthorized")

    auth = [Depends(authorized)]

    @app.get("/health")
    def health():
        return {"status": "ok", "model_enabled": assistant.model is not None}

    @app.get("/preferences", dependencies=auth)
    def preferences():
        return assistant.preferences()

    @app.post("/digests", dependencies=auth)
    def publish(body: DigestInput):
        digest = store.put_digest(str(body.date), [p.model_dump() for p in body.papers])
        text = render_digest(digest)
        if body.deliver:
            store.enqueue("digest:" + digest["id"], text)
        return {"id": digest["id"], "text": text, "queued": body.deliver}

    @app.post("/chat", dependencies=auth)
    def chat(body: ChatInput):
        try:
            reply = assistant.chat(body.message_id, body.text, body.deliver)
        except ValueError as error:
            raise HTTPException(409, str(error)) from None
        return {"reply": reply, "queued": body.deliver}

    @app.post("/outbox/claim", dependencies=auth)
    def claim():
        return {"item": store.claim()}

    @app.post("/outbox/ack", dependencies=auth)
    def acknowledge(body: AckInput):
        # Use the same lock as chat to keep delivered digest references ordered with turns.
        with assistant.lock:
            if not store.acknowledge(body.id, body.lease_token):
                raise HTTPException(409, "Delivery lease is stale or already acknowledged")
        return {"ok": True}

    @app.post("/outbox/release", dependencies=auth)
    def release(body: AckInput):
        if not store.release(body.id, body.lease_token):
            raise HTTPException(409, "Delivery lease is stale or already acknowledged")
        return {"ok": True}

    return app
