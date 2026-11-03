"""Site contract for the raw harvester.

A site owns how to build a seed unit and how to turn one fetched page into a
:class:`PageResult` (questions plus follow-up units).  Everything else
(politeness, robots, retries, raw storage, resume, dedupe) is handled by
:mod:`k12_crawler.harvest.runner`.

Adding a site therefore means adding one module in ``harvest/sites/`` and one
line in ``harvest/registry.py``; the runner never changes.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, ClassVar, Iterable, List, Optional, Tuple

from ..models import Question
from ..subjects import Subject
from .unit import Unit

if TYPE_CHECKING:  # pragma: no cover - avoids importing the HTTP client
    from ..fetcher import Fetcher


@dataclass
class PageResult:
    """Everything one fetched page contributes to a harvest.

    ``questions`` are persisted by the runner; ``children`` are appended to
    its work queue.  A page may contribute either, both, or neither.
    """

    questions: List[Question] = field(default_factory=list)
    children: List[Unit] = field(default_factory=list)


class Site(ABC):
    """One website, bound to one subject channel at a time."""

    name: ClassVar[str] = ""
    label: ClassVar[str] = ""
    subjects: ClassVar[Tuple[Subject, ...]] = ()
    #: ``questions.source`` stored in the DB; defaults to ``name``.
    source: ClassVar[str] = ""
    #: An empty leaf page is a valid result (e.g. baidu page past the end).
    allow_empty: ClassVar[bool] = False
    #: Set when an INDEX page can also carry questions.  The default
    #: :meth:`process` is discovery-only for index pages.
    parse_index: ClassVar[bool] = False
    #: Default raw file extension for ``extract`` (seed units set their own).
    raw_suffix: ClassVar[str] = ".html"

    def __init__(self, subject: Subject):
        self.subject = subject

    @property
    def source_name(self) -> str:
        return self.source or self.name

    # -- site specific -------------------------------------------------
    @classmethod
    @abstractmethod
    def seed_for(cls, subject: Subject) -> str:
        """Default entry URL for this subject channel."""

    @abstractmethod
    def seed_unit(self, seed: str) -> Unit:
        """Build the first unit (usually an index page) from a seed URL."""

    def parse(self, unit: Unit, raw: str) -> List[Question]:
        """Parse one fetched page; index pages may return an empty list."""
        return []

    def expand(self, unit: Unit, raw: str) -> Iterable[Unit]:
        """Return follow-up units discovered on this page."""
        return ()

    def process(self, unit: Unit, raw: str) -> PageResult:
        """Turn one fetched page into questions and follow-up units.

        The default is role-aware: an INDEX page is discovery-only unless
        :attr:`parse_index` is set, because most sites use HTML indexes that
        their question parser cannot read.  Leaf pages are parsed normally.

        Sites whose pages do not fit that split — or that need a different
        order, a shared parse, or a page that is both index and leaf — should
        override this method and return their own :class:`PageResult`.
        """
        questions: List[Question] = []
        if not unit.is_index or self.parse_index:
            questions = self.parse(unit, raw)
        return PageResult(
            questions=questions,
            children=list(self.expand(unit, raw)),
        )

    def fetch(self, unit: Unit, fetcher: "Fetcher") -> str:
        """Fetch one unit.  Override for POST/JSON endpoints."""
        return fetcher.get_text(unit.url)

    # -- helpers -------------------------------------------------------
    @classmethod
    def detect(cls, url: str, html: Optional[str] = None) -> Optional[Subject]:
        """Recognise the subject channel from a URL or a fetched page."""
        return None

    def supports(self, url: str) -> bool:
        return False
