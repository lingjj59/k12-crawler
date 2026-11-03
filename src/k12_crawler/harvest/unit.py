"""The unit of work in the raw harvester.

A *unit* is one fetchable page, not necessarily a paper.  jyeoo/cooco expose
papers, baidu exposes API pages, and a site may also expose index pages that
only lead to more units.  Keeping this generic is what lets the runner stay
site-agnostic: it only knows "fetch -> process -> PageResult".
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict

#: ``role`` values
INDEX = "index"
LEAF = "leaf"


@dataclass
class Unit:
    key: str
    url: str
    role: str = LEAF
    kind: str = "page"
    document_id: str = ""
    document_title: str = ""
    document_url: str = ""
    suffix: str = ".html"
    meta: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.document_id:
            self.document_id = self.key
        if not self.document_url:
            self.document_url = self.url

    @property
    def is_index(self) -> bool:
        return self.role == INDEX

    @property
    def is_leaf(self) -> bool:
        return not self.is_index
