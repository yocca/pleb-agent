"""Test doubles: fake websites served through httpx.MockTransport, and FunctionModel extractors."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import httpx
from pydantic_ai import BinaryContent
from pydantic_ai.messages import ModelMessage, ModelRequest, ModelResponse, ToolCallPart, UserPromptPart
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.usage import RequestUsage

from pleb_agent.extract.agents import Extractor

HTML = "text/html; charset=utf-8"


@dataclass
class FakeResponse:
    body: bytes | str = b""
    status: int = 200
    content_type: str = HTML
    headers: dict[str, str] = field(default_factory=dict)


@dataclass
class Request:
    url: str
    user_agent: str


class FakeWeb:
    """Serves fixed responses by URL; anything else is a 404. Records each request."""

    def __init__(self, pages: dict[str, FakeResponse | str] | None = None) -> None:
        self.pages: dict[str, FakeResponse] = {}
        self.requests: list[Request] = []
        for url, page in (pages or {}).items():
            self.add(url, page)

    def add(self, url: str, page: FakeResponse | str) -> None:
        self.pages[url] = FakeResponse(page) if isinstance(page, str) else page

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self._handle)

    def client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=self.transport())

    def fetched(self, url: str) -> bool:
        return any(r.url == url for r in self.requests)

    def _handle(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        self.requests.append(Request(url, request.headers.get("user-agent", "")))
        page = self.pages.get(url)
        if page is None:
            return httpx.Response(404, text="not found")
        body = page.body.encode() if isinstance(page.body, str) else page.body
        return httpx.Response(page.status, content=body, headers={"content-type": page.content_type, **page.headers})


@dataclass
class ModelCall:
    role: str
    text: str
    images: list[bytes]


Answer = dict[str, Any]


class FakeModels:
    """Text and vision FunctionModels answering with canned MenuExtraction dicts.

    `answer(role, text, images)` decides the reply; every call is recorded.
    """

    def __init__(self, answer: Callable[[str, str, list[bytes]], Answer], tokens: tuple[int, int] = (1000, 200)):
        self.answer = answer
        self.tokens = tokens
        self.calls: list[ModelCall] = []

    def extractor(self) -> Extractor:
        return Extractor(self._model("text"), self._model("vision"), "fake-text", "fake-vision")

    def _model(self, role: str) -> FunctionModel:
        def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
            text, images = _user_input(messages)
            self.calls.append(ModelCall(role, text, images))
            return ModelResponse(
                parts=[ToolCallPart(info.output_tools[0].name, self.answer(role, text, images))],
                usage=RequestUsage(input_tokens=self.tokens[0], output_tokens=self.tokens[1]),
            )

        return FunctionModel(respond, model_name=f"fake-{role}")


def _user_input(messages: list[ModelMessage]) -> tuple[str, list[bytes]]:
    texts: list[str] = []
    images: list[bytes] = []
    for msg in messages:
        if not isinstance(msg, ModelRequest):
            continue
        for part in msg.parts:
            if not isinstance(part, UserPromptPart):
                continue
            items = [part.content] if isinstance(part.content, str) else part.content
            for item in items:
                if isinstance(item, str):
                    texts.append(item)
                elif isinstance(item, BinaryContent):
                    images.append(item.data)
    return "\n".join(texts), images


NO_HH: Answer = {"is_happy_hour": False, "windows": [], "deals": [], "confidence": 0.9}


def hh(days: str = "Mon-Fri", start: str = "4pm", end: str = "7pm", confidence: float = 0.9, **extra: Any) -> Answer:
    return {
        "is_happy_hour": True,
        "windows": [{"days": days, "start": start, "end": end}],
        "deals": [{"item": "Draft beer", "price": 5}],
        "confidence": confidence,
        **extra,
    }


def text_pdf(line: str) -> bytes:
    """A one-page PDF whose text layer holds line."""
    stream = f"BT /F1 12 Tf 72 720 Td ({line}) Tj ET".encode()
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R"
        b" /Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length %d >>\nstream\n%s\nendstream" % (len(stream), stream),
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for i, obj in enumerate(objects, 1):
        offsets.append(len(out))
        out += b"%d 0 obj\n%s\nendobj\n" % (i, obj)
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    out += b"".join(b"%010d 00000 n \n" % o for o in offsets)
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objects) + 1, xref)
    return bytes(out)


def png(color: str = "white") -> bytes:
    import io

    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (40, 20), color).save(buf, format="PNG")
    return buf.getvalue()


def image_pdf() -> bytes:
    """A one-page PDF that is only an image, with no text layer."""
    import io

    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (200, 100), "white").save(buf, format="PDF")
    return buf.getvalue()
