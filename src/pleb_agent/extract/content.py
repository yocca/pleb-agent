"""Turning fetched pages into text or images, and the keyword gate that decides what a model sees."""

from __future__ import annotations

import io
import re
from dataclasses import dataclass
from urllib.parse import urljoin

import lxml.html
import pypdfium2
import trafilatura
from pypdf import PdfReader
from pypdf.errors import PdfReadError

GATE_CONTEXT = 1500  # characters kept on each side of a signal
GATE_MAX = 6000  # characters sent per model call
MAX_PDF_PAGES = 3
MAX_IMAGES_PER_PAGE = 3

_PHRASES = re.compile(r"happy\s*hours?|\bhh\b|half[\s-]*price|drink\s+specials?", re.I)
_TIME_RANGE = re.compile(
    r"\b\d{1,2}(?::\d{2})?\s*(?:[ap]\.?m?\.?)?\s*(?:-|–|—|to|until|till)\s*\d{1,2}(?::\d{2})?\s*(?:[ap]\.?m?\.?)?",
    re.I,
)
_PRICE = re.compile(r"\$\s?\d")
_IMAGE_HINT = re.compile(r"happy[\s_-]*hours?|\bhh\b|specials?|drinks?|menu", re.I)


@dataclass
class Image:
    data: bytes
    media_type: str


def html_to_text(html: bytes | str) -> str:
    raw = html.decode("utf-8", errors="replace") if isinstance(html, bytes) else html
    text = trafilatura.extract(raw, include_tables=True, favor_recall=True, include_comments=False)
    if text and text.strip():
        return text
    try:
        return " ".join(lxml.html.fromstring(raw).text_content().split())
    except (lxml.etree.ParserError, ValueError):
        return ""


def pdf_to_text(data: bytes) -> str:
    try:
        reader = PdfReader(io.BytesIO(data))
        return "\n".join((page.extract_text() or "") for page in reader.pages[:MAX_PDF_PAGES]).strip()
    except (PdfReadError, ValueError, KeyError):
        return ""


def pdf_to_images(data: bytes) -> list[Image]:
    """Render the first pages of a PDF without a text layer, for the vision model."""
    images = []
    pdf = pypdfium2.PdfDocument(data)
    try:
        for i in range(min(len(pdf), MAX_PDF_PAGES)):
            pil = pdf[i].render(scale=2).to_pil()
            buf = io.BytesIO()
            pil.save(buf, format="PNG")
            images.append(Image(buf.getvalue(), "image/png"))
    finally:
        pdf.close()
    return images


def has_signal(text: str) -> bool:
    return bool(_signal_spans(text))


def gate(text: str) -> str | None:
    """The text around happy hour signals, or None when there is no signal."""
    spans = _signal_spans(text)
    if not spans:
        return None
    merged: list[list[int]] = []
    for s, e in sorted((max(0, s - GATE_CONTEXT), min(len(text), e + GATE_CONTEXT)) for s, e in spans):
        if merged and s <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], e)
        else:
            merged.append([s, e])
    return "\n…\n".join(text[s:e].strip() for s, e in merged)[:GATE_MAX]


def _signal_spans(text: str) -> list[tuple[int, int]]:
    spans = [m.span() for m in _PHRASES.finditer(text)]
    for m in _TIME_RANGE.finditer(text):
        window = text[max(0, m.start() - 200) : m.end() + 200]
        if _PRICE.search(window):
            spans.append(m.span())
    return spans


def menu_image_urls(html: bytes | str, base_url: str, page_is_candidate: bool) -> list[str]:
    """Images that probably show a happy hour menu.

    An image qualifies when its src, alt or title mentions happy hour, specials, drinks or
    menu, or when it sits on a candidate page whose text has no happy hour wording of its own.
    """
    try:
        doc = lxml.html.fromstring(html)
    except (lxml.etree.ParserError, ValueError):
        return []
    urls = []
    for img in doc.iter("img"):
        src = img.get("src") or img.get("data-src")
        if not src or src.startswith("data:"):
            continue
        hint = " ".join(filter(None, [src, img.get("alt"), img.get("title")]))
        if _IMAGE_HINT.search(hint) or page_is_candidate:
            urls.append(urljoin(base_url, src))
    return urls[:MAX_IMAGES_PER_PAGE]
