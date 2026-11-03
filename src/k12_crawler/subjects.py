"""Subject vocabulary shared by every site.

The actual channel tables live next to each site (``harvest/sites/*.py``); this
module only defines the :class:`Subject` value object, slug maps, and the
lookup helpers used by the CLI and config loader.  Site-specific lookups are
lazy imports from :mod:`k12_crawler.harvest.registry` to avoid an import cycle.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Dict, List, Optional, Sequence, Tuple
from urllib.parse import urlparse

from bs4 import BeautifulSoup

if TYPE_CHECKING:  # pragma: no cover - avoids a circular import
    from .config import Config

#: Chinese subject name -> ASCII slug used in export file names / metadata.
SUBJECT_SLUGS: Dict[str, str] = {
    "物理": "physics",
    "化学": "chemistry",
    "数学": "math",
    "生物": "biology",
    "语文": "chinese",
    "英语": "english",
    "历史": "history",
    "地理": "geography",
    "政治": "politics",
    "道德与法治": "ethics_law",
    "信息": "informatics",
    "信息技术": "information_tech",
    "通用": "general_technology",
    "科学": "science",
    "AI通识": "ai_literacy",
}

STAGE_SLUGS: Dict[str, str] = {
    "高中": "high_school",
    "初中": "middle_school",
    "小学": "primary_school",
}

DEFAULT_SUBJECT_CODE = "gzwl"


@dataclass(frozen=True)
class Subject:
    """One subject channel of one site."""

    code: str
    stage: str
    name: str
    slug: str

    @property
    def display(self) -> str:
        return f"{self.stage}{self.name}"

    @property
    def label(self) -> str:
        return self.display


def subject(code: str, stage: str, name: str) -> Subject:
    """Build a Subject, deriving the slug from the Chinese name."""
    return Subject(code=code, stage=stage, name=name, slug=SUBJECT_SLUGS[name])


# ---------------------------------------------------------------------------
# site lookups (lazy: registry imports the site modules, which import Subject)
# ---------------------------------------------------------------------------
def _site_class(site: str):
    from .harvest.registry import get_site

    return get_site(site)


def known_sites() -> List[str]:
    from .harvest.registry import available_sites

    return available_sites()


def site_subjects(site: str) -> Tuple[Subject, ...]:
    return _site_class(site).subjects


def find_subject(site: str, code: str) -> Optional[Subject]:
    for item in site_subjects(site):
        if item.code == code:
            return item
    return None


def default_subject(site: str) -> Subject:
    subjects = site_subjects(site)
    subject_ = find_subject(site, DEFAULT_SUBJECT_CODE)
    return subject_ or subjects[0]


def seed_url(site: str, subject_: Subject) -> str:
    return _site_class(site).seed_for(subject_)


# ---------------------------------------------------------------------------
# resolution / detection
# ---------------------------------------------------------------------------
def _compact(value: str) -> str:
    return re.sub(r"\s+", "", value or "")


def resolve_in(subjects: Sequence[Subject], value: Optional[str]) -> Subject:
    """Resolve a value against an explicit channel table."""
    if not subjects:
        raise ValueError("no subjects registered")
    if value is None or str(value).strip() == "":
        for item in subjects:
            if item.code == DEFAULT_SUBJECT_CODE:
                return item
        return subjects[0]

    raw = _compact(str(value))
    lowered = raw.lower()
    for item in subjects:
        if lowered == item.code or lowered == item.slug:
            return item
    for item in subjects:
        if raw == item.display:
            return item

    matches = [item for item in subjects if raw == item.name]
    if len(matches) == 1:
        return matches[0]
    if matches:
        options = "、".join(f"{item.code}（{item.display}）" for item in matches)
        raise ValueError(f"学科「{value}」有多个学段，请改用完整名称或代码：{options}")
    options = "、".join(f"{item.code}（{item.display}）" for item in subjects)
    raise ValueError(f"不支持学科「{value}」；可选：{options}")


def resolve_subject(site: str, value: Optional[str]) -> Subject:
    """Resolve a user-supplied subject for one site.

    Accepted forms: channel code (``gzhx``), slug (``chemistry``), full name
    (``高中化学``) or a bare subject name when it is unambiguous for the site.
    """
    return resolve_in(site_subjects(site), value)


def resolve_subject_any(value: Optional[str]) -> Subject:
    """Resolve a subject without knowing the site (used by DB export/list)."""
    seen: Dict[Tuple[str, str], Subject] = {}
    for site in known_sites():
        for item in site_subjects(site):
            seen[(item.stage, item.name)] = item
    unique = list(seen.values())
    return resolve_in(unique, value)


def _stage_hint(text: str) -> Optional[str]:
    if "高考" in text or "高中" in text:
        return "高中"
    if "中考" in text or "初中" in text:
        return "初中"
    if "小学" in text:
        return "小学"
    return None


def match_text(subjects: Sequence[Subject], text: str) -> Optional[Subject]:
    """Match a page title/meta string against a channel table."""
    if not text:
        return None
    compact = _compact(text)
    for item in subjects:
        if item.display in compact:
            return item

    hint = _stage_hint(compact)
    named = [item for item in subjects if item.name in compact]
    if not named:
        return None
    if hint:
        staged = [item for item in named if item.stage == hint]
        if staged:
            named = staged
    names = {item.name for item in named}
    if len(names) == 1 and len(named) == 1:
        return named[0]
    # Ambiguous (same subject in several stages, or several subjects in one
    # title); refuse to guess.
    return None


def detect_subject(
    site: str, url: str, html: Optional[str] = None
) -> Optional[Subject]:
    """Best-effort subject detection from a URL and/or an already fetched page."""
    return _site_class(site).detect(url, html)


def html_hint(html: Optional[str]) -> List[str]:
    """Title/meta strings that usually name the subject on a page."""
    if not html:
        return []
    soup = BeautifulSoup(html, "lxml")
    texts: List[str] = []
    for meta in soup.select('meta[name="keywords"], meta[name="description"]'):
        texts.append(meta.get("content") or "")
    if soup.title is not None:
        texts.append(soup.title.get_text(" ", strip=True))
    return texts


def detect_from_html(
    subjects: Sequence[Subject], html: Optional[str]
) -> Optional[Subject]:
    for text in html_hint(html):
        found = match_text(subjects, text)
        if found is not None:
            return found
    return None


# ---------------------------------------------------------------------------
# config helpers
# ---------------------------------------------------------------------------
def apply_subject_paths(config: "Config") -> None:
    """Move the project's per-subject paths under ``data/<subject_slug>/``."""
    from .config import PROJECT_ROOT

    data_root = (PROJECT_ROOT / "data").resolve()
    known_slugs = set(SUBJECT_SLUGS.values())

    def _relative(path_value: object) -> Optional[Tuple[str, ...]]:
        try:
            return Path(path_value).resolve().relative_to(data_root).parts
        except (TypeError, ValueError):
            return None

    def _is_legacy(parts: Tuple[str, ...], leaf: str) -> bool:
        return parts == (leaf,) or (
            len(parts) >= 2 and parts[0] in known_slugs and parts[1] == leaf
        )

    raw_parts = _relative(config.raw_dir)
    if raw_parts is not None and _is_legacy(raw_parts, "raw"):
        config.raw_dir = data_root / config.subject_slug / "raw"

    image_parts = _relative(config.image_dir)
    if image_parts is not None and _is_legacy(image_parts, "images"):
        config.image_dir = data_root / config.subject_slug / "work" / "images"

    export_parts = _relative(config.export_dir)
    if export_parts is not None and (
        export_parts == () or (len(export_parts) == 1 and export_parts[0] in known_slugs)
    ):
        config.export_dir = data_root / config.subject_slug / "work" / "exports"


def apply_to_config(config: "Config", subject_: Subject) -> None:
    """Write subject fields onto a Config (kept for the CLI/config loader)."""
    config.subject_code = subject_.code
    config.subject = subject_.name
    config.stage = subject_.stage
    config.subject_slug = subject_.slug
    apply_subject_paths(config)
    known = known_sites()
    site = getattr(config, "source", "")
    if site not in known:
        return
    seed = seed_url(site, subject_)
    if not seed:
        return
    host = urlparse(getattr(config, "base_url", "") or "").netloc
    standard_host = (
        not host
        or host.endswith(".cooco.net.cn")
        or host.endswith(".jyeoo.com")
        or host == "cooco.net.cn"
        or host == "jyeoo.com"
        or host == "tiku.baidu.com"
    )
    if not standard_host:
        return
    config.list_url = seed
    parsed = urlparse(seed)
    config.base_url = f"{parsed.scheme}://{parsed.netloc}"


def export_basename(subject_: Subject) -> str:
    stage = STAGE_SLUGS.get(subject_.stage, subject_.stage)
    return f"{stage}_{subject_.slug}.jsonl"


def catalog_entry(site: str, subject_: Subject) -> Dict[str, str]:
    return {
        "code": subject_.code,
        "stage": subject_.stage,
        "subject": subject_.name,
        "slug": subject_.slug,
        "label": subject_.display,
        "default_seed": seed_url(site, subject_),
    }
