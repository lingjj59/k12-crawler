"""Small HTML/text helpers shared by the source adapter and validators."""

from __future__ import annotations

import hashlib
import html
import json
import re
from typing import Iterable, List, Optional, Tuple
from urllib.parse import urljoin

from bs4 import BeautifulSoup, NavigableString, Tag


def clean_text(value: Optional[str], multiline: bool = False) -> str:
    text = html.unescape(value or "")
    text = text.replace("\xa0", " ").replace("\u3000", " ")
    text = re.sub(r"[\u200b-\u200d\ufeff]", "", text)
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    if multiline:
        text = re.sub(r" *\n *", "\n", text)
        text = re.sub(r"\n{2,}", "\n", text)
    else:
        text = re.sub(r"\s*\n\s*", " ", text)
    return text.strip()


def absolute_url(base_url: str, src: Optional[str]) -> Optional[str]:
    if not src:
        return None
    src = src.strip()
    if not src or src.startswith("data:"):
        return None
    if src.startswith("//"):
        src = "http:" + src
    return urljoin(base_url, src)


def parse_int(value: Optional[str]) -> Optional[int]:
    if value is None:
        return None
    match = re.search(r"\d+", str(value))
    return int(match.group(0)) if match else None


def sniff_image_format(data: bytes, url: str = "") -> str:
    """Detect content by magic bytes; never trust the URL extension alone."""
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if data.startswith(b"\xff\xd8\xff"):
        return "jpg"
    if data.startswith(b"GIF87a") or data.startswith(b"GIF89a"):
        return "gif"
    if data.startswith(b"BM"):
        return "bmp"
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    if data.lstrip().startswith(b"<svg") or b"<svg" in data[:200].lower():
        return "svg"
    # WMF: placeable header D7 CD C6 9A, or standard header 01 00 09 00.
    if data.startswith(b"\xd7\xcd\xc6\x9a") or data.startswith(b"\x01\x00\x09\x00"):
        return "wmf"
    # EMF header starts with 01 00 00 00 and has " EMF" at offset 40.
    if data.startswith(b"\x01\x00\x00\x00") and b" EMF" in data[40:80]:
        return "emf"
    lower_url = url.lower().split("?")[0]
    for ext in ("png", "jpg", "jpeg", "gif", "bmp", "webp", "svg", "wmf", "emf"):
        if lower_url.endswith("." + ext):
            return "jpg" if ext == "jpeg" else ext
    return "bin"


def extension_for_format(fmt: str) -> str:
    return {"jpg": "jpg", "jpeg": "jpg", "bin": "bin"}.get(fmt, fmt or "bin")


def _append_rich_text(node: Tag, parts: List[str]) -> None:
    for child in node.children:
        if isinstance(child, NavigableString):
            parts.append(str(child))
        elif isinstance(child, Tag):
            if child.name == "sup":
                inner = clean_text(child.get_text(" ", strip=True))
                parts.append("^{" + inner + "}")
            elif child.name == "sub":
                inner = clean_text(child.get_text(" ", strip=True))
                parts.append("_{" + inner + "}")
            elif child.name == "br":
                parts.append(" ")
            else:
                _append_rich_text(child, parts)


def rich_text_to_plain(value: Optional[str]) -> str:
    """Normalize HTML text while keeping <sup>/<sub> as ``^{...}`` / ``_{...}``.

    The normalized text is what goes into ``stem_text`` / option ``text``.
    The original markup remains in ``stem_html`` / option ``html`` and the raw
    block HTML, so no information is lost.
    """
    if not value:
        return ""
    soup = BeautifulSoup(value, "lxml")
    parts: List[str] = []
    _append_rich_text(soup, parts)
    text = "".join(parts)
    # "10 ^{11}" -> "10^{11}" (the source often separates the superscript).
    text = re.sub(r"\s+(\^\{|_\{)", r"\1", text)
    return clean_text(text)


def strip_math_markup(value: Optional[str]) -> str:
    """Remove the ^{...}/_{...} markers for raw-text comparisons."""
    text = value or ""
    text = re.sub(r"\^\{([^{}]*)\}", r"\1", text)
    text = re.sub(r"_\{([^{}]*)\}", r"\1", text)
    return text


def markup_to_html(value: Optional[str]) -> str:
    """Turn normalized text with ^{...}/_{...} back into safe HTML.

    Used when the original option markup cannot be sliced cleanly (for
    example when the ``A`` label and the ``．`` are in different tags).  The
    raw paragraph HTML is still stored separately in the raw question block.
    """
    text = html.escape(value or "", quote=False)
    text = re.sub(r"\^\{([^{}]*)\}", r"<sup>\1</sup>", text)
    text = re.sub(r"_\{([^{}]*)\}", r"<sub>\1</sub>", text)
    return text


def _normalize_html_fragment(value: str) -> str:
    soup = BeautifulSoup(value, "lxml")
    body = soup.body or soup
    # Slicing concatenated paragraph HTML can leave empty trailing <p> tags.
    for tag in body.find_all(["p", "span", "div"]):
        if not tag.get_text(strip=True) and tag.find(["img", "sup", "sub"]) is None:
            tag.decompose()
    return "".join(str(child) for child in body.children).strip()


_OPTION_MARKER = re.compile(r"(?<![A-Za-z0-9])([A-D])\s*[．.、]\s*")


def split_option_html(value: str) -> List[Tuple[str, str, str]]:
    """Split concatenated option HTML into (label, normalized text, html).

    The markers (``A．`` etc.) are literal text in the adapter's HTML, so
    slicing the rich HTML at marker positions preserves per-option
    ``<sup>``/``<sub>`` tags and image placeholders.
    """
    if not value:
        return []
    matches = list(_OPTION_MARKER.finditer(value))
    if not matches:
        return []
    for index, first in enumerate(matches):
        if first.group(1) != "A":
            continue
        sequence = [first]
        expected = "B"
        for match in matches[index + 1 :]:
            if match.group(1) == expected:
                sequence.append(match)
                expected = chr(ord(expected) + 1)
                if expected > "D":
                    break
        if len(sequence) < 2:
            continue
        result: List[Tuple[str, str, str]] = []
        for pos, match in enumerate(sequence):
            end = sequence[pos + 1].start() if pos + 1 < len(sequence) else len(value)
            fragment = value[match.end() : end].strip()
            result.append(
                (
                    match.group(1),
                    rich_text_to_plain(fragment),
                    _normalize_html_fragment(fragment),
                )
            )
        return result
    return []


def split_options(text: str) -> List[Tuple[str, str]]:
    """Split "A．... B．... C．... D．..." into (label, text) pairs.

    Deliberately conservative: the first marker must be A, labels must be in
    ascending order, and at least two options are required.  This avoids
    treating ordinary "A、B 两点" prose in the stem as an option marker.
    """
    text = clean_text(text)
    matches = list(_OPTION_MARKER.finditer(text))
    if not matches:
        return []
    for index, first in enumerate(matches):
        if first.group(1) != "A":
            continue
        sequence = [first]
        expected = "B"
        for match in matches[index + 1 :]:
            if match.group(1) == expected:
                sequence.append(match)
                expected = chr(ord(expected) + 1)
                if expected > "D":
                    break
        if len(sequence) < 2:
            continue
        result: List[Tuple[str, str]] = []
        for pos, match in enumerate(sequence):
            end = sequence[pos + 1].start() if pos + 1 < len(sequence) else len(text)
            value = clean_text(text[match.end() : end])
            result.append((match.group(1), value))
        return result
    return []


def normalize_answer(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    letters = re.findall(r"[A-D]", value.upper())
    if not letters:
        return None
    # Preserve first occurrence order and remove duplicates.
    seen = set()
    ordered = []
    for letter in letters:
        if letter not in seen:
            seen.add(letter)
            ordered.append(letter)
    return "".join(ordered)


def content_hash(stem_text: str, options: Iterable[Tuple[str, str]], answer: Optional[str]) -> str:
    payload = {
        "stem": clean_text(stem_text),
        "options": [(label, clean_text(text)) for label, text in options],
        "answer": answer,
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


HTML_TAG_RE = re.compile(
    r"</?(?:p|br|span|div|img|sub|sup|strong|em|table|thead|tbody|tr|td|th|"
    r"li|ul|ol|dl|dt|dd|font|hr|section|article|figure|figcaption|"
    r"blockquote|pre|code|h[1-6])\b[^>]*>",
    re.IGNORECASE,
)


def contains_html_residue(text: str) -> bool:
    """Detect real tags, not chemistry comparisons such as ``P<S<O``."""
    return bool(HTML_TAG_RE.search(text or ""))


def looks_mojibake(text: str) -> bool:
    if "\ufffd" in text:
        return True
    # Common GBK-as-Latin-1 artifacts; keep this conservative.
    markers = ("Ã", "Â", "â€", "ï¿½")
    return any(marker in text for marker in markers)
