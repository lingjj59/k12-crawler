"""菁优网 (jyeoo): paper lists -> paper pages.

Paper pages render questions and options but not the answer / analysis (those
are behind the site's member flow).  The harvester records ``answer=None`` in
that case instead of guessing.  Single-question pages that do expose an answer
are parsed as-is.
"""

from __future__ import annotations

import copy
import re
from typing import Iterable, List, Optional
from urllib.parse import urljoin

from bs4 import BeautifulSoup, NavigableString, Tag

from ...html_utils import (
    absolute_url,
    clean_text,
    content_hash,
    normalize_answer,
    parse_int,
    rich_text_to_plain,
)
from ...models import Option, Question, Resource
from ...subjects import (
    Subject,
    detect_from_html,
    subject,
)
from ..site import Site
from ..unit import INDEX, Unit

BASE = "https://www.jyeoo.com"


def _channel(code: str, stage: str, name: str) -> Subject:
    return subject(code, stage, name)


SUBJECTS = (
    _channel("gzsx", "高中", "数学"),
    _channel("gzwl", "高中", "物理"),
    _channel("gzhx", "高中", "化学"),
    _channel("gzsw", "高中", "生物"),
    _channel("gzdl", "高中", "地理"),
    _channel("gzyw", "高中", "语文"),
    _channel("gzyy", "高中", "英语"),
    _channel("gzxx", "高中", "信息"),
    _channel("gzty", "高中", "通用"),
    _channel("gzaits", "高中", "AI通识"),
    _channel("czsx", "初中", "数学"),
    _channel("czwl", "初中", "物理"),
    _channel("czhx", "初中", "化学"),
    _channel("czsw", "初中", "生物"),
    _channel("czdl", "初中", "地理"),
    _channel("czyw", "初中", "语文"),
    _channel("czyy", "初中", "英语"),
    _channel("czddyfz", "初中", "道德与法治"),
    _channel("czkx", "初中", "科学"),
    _channel("czxxjs", "初中", "信息技术"),
    _channel("czaits", "初中", "AI通识"),
)


class JyeooSite(Site):
    name = "jyeoo"
    label = "菁优网"
    subjects = SUBJECTS
    allow_empty = False

    # ------------------------------------------------------------------
    # seeding / discovery
    # ------------------------------------------------------------------
    @classmethod
    def seed_for(cls, subject_: Subject) -> str:
        return f"{BASE}/shijuan/{subject_.code}/"

    def seed_unit(self, seed: str) -> Unit:
        seed = (seed or self.seed_for(self.subject)).rstrip("/")
        return Unit(
            key="list/1",
            url=self.list_url(seed, 1),
            role=INDEX,
            kind="list",
            suffix=".html",
            meta={"seed": seed, "page": 1},
        )

    @staticmethod
    def list_url(seed: str, page: int) -> str:
        seed = seed.rstrip("/")
        if page <= 1:
            return seed + "/"
        if seed.endswith("/101"):
            return f"{seed}/{page}"
        return f"{seed}/101/{page}"

    def expand(self, unit: Unit, raw: str) -> Iterable[Unit]:
        if unit.kind != "list":
            return ()
        soup = BeautifulSoup(raw, "lxml")
        page = int(unit.meta.get("page") or 1)
        seed = str(unit.meta.get("seed") or unit.url)

        seen = set()
        children: List[Unit] = []
        for anchor in soup.select('a[href^="/pp/"]'):
            href = anchor.get("href") or ""
            match = re.search(r"/pp/([^/?#]+)", href)
            if not match:
                continue
            paper_id = match.group(1)
            if paper_id in seen:
                continue
            seen.add(paper_id)
            title = clean_text(anchor.get("title") or "")
            if not title:
                parent = anchor.find_parent("li")
                if parent is not None:
                    title = clean_text(parent.get_text(" ", strip=True))
                    title = re.sub(r"\s*查看\s*$", "", title)
            paper_url = urljoin(BASE, href)
            children.append(
                Unit(
                    key=paper_id,
                    url=paper_url,
                    kind="paper",
                    document_id=paper_id,
                    document_title=title or paper_id,
                    meta={"page": page},
                )
            )

        total_pages = page
        for anchor in soup.select('a[href*="/101/"]'):
            match = re.search(r"/101/(\d+)", anchor.get("href") or "")
            if match:
                total_pages = max(total_pages, int(match.group(1)))
        if children and page < total_pages:
            children.append(
                Unit(
                    key=f"list/{page + 1}",
                    url=self.list_url(seed, page + 1),
                    role=INDEX,
                    kind="list",
                    suffix=".html",
                    meta={"seed": seed, "page": page + 1},
                )
            )
        return children

    # ------------------------------------------------------------------
    # paper parsing
    # ------------------------------------------------------------------
    def parse(self, unit: Unit, raw: str) -> List[Question]:
        soup = BeautifulSoup(raw, "lxml")
        url = unit.url
        paper_id = self._paper_id(url) or unit.document_id
        title = self._title(soup, url) or unit.document_title or paper_id

        year_match = re.search(r"(20\d{2})", title)
        unit.document_id = paper_id
        unit.document_title = title
        unit.meta.setdefault(
            "year", int(year_match.group(1)) if year_match else None
        )

        questions: List[Question] = []
        for index, section in enumerate(soup.select("section.quesborder"), start=1):
            question = self._parse_section(
                section, url=url, paper_id=paper_id, paper_title=title, index=index
            )
            if question is not None:
                questions.append(question)
        return questions

    def _parse_section(
        self,
        section: Tag,
        *,
        url: str,
        paper_id: str,
        paper_title: str,
        index: int,
    ) -> Optional[Question]:
        resources: List[Resource] = []

        number_node = section.select_one("span.qseq")
        number = parse_int(number_node.get_text(" ", strip=True)) if number_node else None
        if number is None:
            number = index

        stem_source = section.select_one("div.pt1") or section
        stem_clone = self._clone_with_images(stem_source, url, resources, "stem")
        for marker in stem_clone.select("span.qseq"):
            marker.decompose()
        stem_html = str(stem_clone)
        # Keep <sub>/<sup> semantics: chemistry formulas (H2O, SO4^2-) would
        # otherwise become "H 2 O" because get_text() inserts separators.
        stem_text = rich_text_to_plain(stem_html)
        if not stem_text:
            return None

        options: List[Option] = []
        option_nodes = section.select("div.pt2 td.selectoption label")
        if not option_nodes:
            option_nodes = section.select("div.pt2 label")
        for position, label_node in enumerate(option_nodes):
            option_clone = self._clone_with_images(
                label_node, url, resources, "option"
            )
            option_html = str(option_clone)
            raw_text = rich_text_to_plain(option_html)
            match = re.match(r"^\s*([A-D])\s*[．.、]\s*", raw_text)
            label = match.group(1) if match else chr(ord("A") + position)
            text = re.sub(r"^\s*[A-D]\s*[．.、]\s*", "", raw_text).strip()
            options.append(Option(label=label, text=text, html=option_html))

        answer_node = section.select_one("div.pt11")
        answer: Optional[str] = None
        if answer_node is not None:
            answer_text = rich_text_to_plain(str(answer_node))
            answer_text = re.sub(r"^【答案】\s*", "", answer_text)
            answer = normalize_answer(answer_text) or (answer_text or None)

        analysis_node = section.select_one("div.pt6")
        analysis_text = None
        if analysis_node is not None:
            analysis_text = rich_text_to_plain(str(analysis_node))
            analysis_text = re.sub(r"^【解答】\s*", "", analysis_text) or None

        knowledge_points: List[str] = []
        point_node = section.select_one("div.pt3")
        if point_node is not None:
            point_text = clean_text(point_node.get_text(" ", strip=True))
            point_text = re.sub(r"^【考点】\s*", "", point_text)
            knowledge_points = [p for p in re.split(r"[、,，;；]", point_text) if p]

        choice_type = "unknown"
        if options and answer and re.fullmatch(r"[A-D]+", answer):
            choice_type = "single" if len(answer) == 1 else "multiple"

        question_type = "选择题" if options else "非选择题"
        # Use the document order, not the printed number: some papers restart
        # numbering in each section, which would make IDs collide.
        source_url = f"{url.split('#', 1)[0]}#q{index}"
        digest = content_hash(
            stem_text, [(option.label, option.text) for option in options], answer
        )

        return Question(
            source=self.source_name,
            source_question_id=f"{paper_id}-q{index}",
            paper_id=paper_id,
            paper_title=paper_title,
            subject=self.subject.name,
            stage=self.subject.stage,
            question_type=question_type,
            question_number=number,
            stem_html=stem_html,
            stem_text=stem_text,
            options=options,
            answer=answer,
            analysis_html=str(analysis_node) if analysis_node is not None else None,
            analysis_text=analysis_text,
            knowledge_points=knowledge_points,
            difficulty=None,
            difficulty_stars=None,
            source_url=source_url,
            paper_url=url,
            raw_block_html=str(section),
            resources=resources,
            content_hash=digest,
            choice_type=choice_type,
        )

    @staticmethod
    def _clone_with_images(
        node: Tag, base_url: str, resources: List[Resource], role: str
    ) -> Tag:
        clone = copy.deepcopy(node)
        for image in clone.find_all("img"):
            source = (
                image.get("src")
                or image.get("data-src")
                or image.get("data-original")
            )
            original_url = absolute_url(base_url, source)
            ordinal = len(resources) + 1
            placeholder = f"[[IMG:{ordinal}]]"
            extension = None
            if original_url:
                path = original_url.split("?", 1)[0]
                if "." in path.rsplit("/", 1)[-1]:
                    extension = path.rsplit(".", 1)[-1].lower()
            resources.append(
                Resource(
                    ordinal=ordinal,
                    role=role,
                    original_url=original_url or "",
                    placeholder=placeholder,
                    original_extension=extension,
                    conversion_status="pending",
                    error=None if original_url else "missing image src",
                )
            )
            image.replace_with(NavigableString(placeholder))
        return clone

    @staticmethod
    def _paper_id(url: str) -> Optional[str]:
        match = re.search(r"/(?:pp|shiti)/([^/?#]+)", url)
        return match.group(1) if match else None

    @staticmethod
    def _title(soup: BeautifulSoup, url: str) -> str:
        if "/shiti/" in url:
            bread = soup.select_one("#divBread")
            if bread is not None:
                link = bread.select_one("a.active")
                if link is not None and clean_text(link.get_text(" ", strip=True)):
                    return clean_text(link.get_text(" ", strip=True))
        raw = soup.title.get_text(" ", strip=True) if soup.title else ""
        return re.sub(r"\s*-\s*菁优网.*$", "", clean_text(raw)).strip()

    @classmethod
    def detect(cls, url: str, html: Optional[str] = None) -> Optional[Subject]:
        match = re.search(r"/shijuan/([a-z0-9]+)/", url or "")
        if match:
            for item in cls.subjects:
                if item.code == match.group(1):
                    return item
        return detect_from_html(cls.subjects, html)

    def supports(self, url: str) -> bool:
        return "jyeoo.com" in url and ("/pp/" in url or "/shiti/" in url)
