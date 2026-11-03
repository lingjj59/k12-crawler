"""百度题库 (baidu) as a normal site.

baidu does not expose "papers": a chapter list links to knowledge points (kp)
and each kp is paged through a JSON API (``start`` / ``rn``).  Both shapes are
modelled directly as units here:

* chapterlist page  -> index unit, expands into kp API pages
* API page          -> leaf unit, parses questions and expands to the next page

No pseudo-paper IDs are involved; resuming a kp halfway is just a different
unit key (``kp/<id>/start=21``).
"""

from __future__ import annotations

import html as html_lib
import json
import re
from typing import Any, Dict, Iterable, List, Optional
from urllib.parse import parse_qsl, urlparse

from bs4 import BeautifulSoup

from ...html_utils import clean_text, content_hash, normalize_answer
from ...models import Option, Question
from ...subjects import Subject, subject
from ..site import PageResult, Site
from ..unit import INDEX, Unit

BASE = "https://tiku.baidu.com"
CHAPTERLIST = (
    "https://tiku.baidu.com/tikupc/chapterlist/"
    "1bfd700abb68a98271fefa04-{course_id}-knowpoint-11"
)
API_URL = "https://tiku.baidu.com/bigque/interface/getqueinfos"

#: baidu course ids verified against the chapterlist pages.
COURSE_IDS = {"gzsx": 17, "gzwl": 18, "gzhx": 19, "gzsw": 20}
CODE_BY_ID = {value: key for key, value in COURSE_IDS.items()}

SUBJECTS = (
    subject("gzsx", "高中", "数学"),
    subject("gzwl", "高中", "物理"),
    subject("gzhx", "高中", "化学"),
    subject("gzsw", "高中", "生物"),
)

META_PLACEHOLDER = {"略", "解析", "解析已在路上飞奔，马上就到！"}
PAGE_SUFFIX = re.compile(r"\s*第\d+页\s*$")


def _node_text(node: Any) -> str:
    """Flatten baidu's rich-text nodes: ``{"t": "p", "c": ...}``."""
    if node is None:
        return ""
    if isinstance(node, str):
        return html_lib.unescape(node)
    if isinstance(node, (int, float)):
        return str(node)
    if isinstance(node, list):
        return "".join(_node_text(item) for item in node)
    if isinstance(node, dict):
        for key in ("c", "text", "ret", "content", "value", "stem"):
            if key in node:
                text = _node_text(node[key])
                if text:
                    return text
        return ""
    return ""


def parse_chapterlist(html: str) -> List[Dict[str, str]]:
    """Extract ``{kp_id, kp_name, chapter}`` rows from a chapterlist page."""
    soup = BeautifulSoup(html, "lxml")
    rows: List[Dict[str, str]] = []
    seen = set()
    for chapter in soup.select(".detail-chapter"):
        title_node = chapter.select_one(".detail-chapter-title h3") or chapter.select_one("h3")
        chapter_name = clean_text(title_node.get_text(" ", strip=True)) if title_node else ""
        for anchor in chapter.select("a[href*='/chapterdetail/']"):
            href = anchor.get("href") or ""
            match = re.search(r"/chapterdetail/([0-9a-f]+)-", href)
            if not match:
                continue
            kp_id = match.group(1)
            if kp_id in seen:
                continue
            seen.add(kp_id)
            name_node = (
                anchor.find_parent(class_="detail-kpoint-2")
                or anchor.find_parent(class_="detail-kpoint-1")
                or anchor
            )
            heading = name_node.select_one("h5") or name_node.select_one("h4")
            kp_name = clean_text(
                (heading or anchor).get_text(" ", strip=True)
            ) or clean_text(anchor.get_text(" ", strip=True))
            kp_name = PAGE_SUFFIX.sub("", re.sub(r"^\d+(?:\.\d+)*\s*", "", kp_name))
            rows.append(
                {"kp_id": kp_id, "kp_name": kp_name or kp_id, "chapter": chapter_name}
            )
    return rows


def api_url(kp_id: str, start: int, rn: int = 20) -> str:
    return (
        f"{API_URL}?action=getquebykp&format=jsonp&kp_id={kp_id}"
        f"&start={int(start)}&rn={int(rn)}&kp_type=knowpoint"
    )


class BaiduSite(Site):
    name = "baidu"
    label = "百度题库"
    source = "baidu_tiku"
    subjects = SUBJECTS
    allow_empty = True
    raw_suffix = ".json"

    #: The API ignores ``rn``; 20 questions per page is a server constant.
    rn = 20
    #: How many API pages to walk per knowledge point.
    pages_per_kp = 2

    @classmethod
    def seed_for(cls, subject_: Subject) -> str:
        course_id = COURSE_IDS.get(subject_.code, "")
        return CHAPTERLIST.format(course_id=course_id)

    def seed_unit(self, seed: str) -> Unit:
        seed = (seed or self.seed_for(self.subject)).strip()
        return Unit(
            key=f"chapterlist/{self.subject.code}",
            url=seed,
            role=INDEX,
            kind="chapterlist",
            suffix=".html",
            document_id=self.subject.code,
            document_title=f"{self.subject.display}知识点目录",
            meta={"seed": seed},
        )

    def expand(self, unit: Unit, raw: str) -> Iterable[Unit]:
        if unit.kind == "chapterlist":
            return self._expand_chapterlist(unit, raw)
        if unit.kind == "api_page":
            return self._expand_api_page(unit, raw)
        return ()

    def process(self, unit: Unit, raw: str) -> PageResult:
        """Baidu pages are either HTML discovery or JSON questions.

        A chapter list is HTML and must never reach ``parse`` (which only
        accepts the JSON API).  An API page contributes both its questions and
        the next page unit, if any.
        """
        if unit.kind == "chapterlist":
            return PageResult(children=list(self.expand(unit, raw)))
        if unit.kind == "api_page":
            return PageResult(
                questions=self.parse(unit, raw),
                children=list(self.expand(unit, raw)),
            )
        return PageResult()

    def _expand_chapterlist(self, unit: Unit, raw: str) -> List[Unit]:
        seed = str(unit.meta.get("seed") or unit.url)
        children: List[Unit] = []
        for row in parse_chapterlist(raw):
            children.append(
                Unit(
                    key=f"kp/{row['kp_id']}/start=1",
                    url=api_url(row["kp_id"], 1, self.rn),
                    kind="api_page",
                    document_id=row["kp_id"],
                    document_title=row["kp_name"],
                    document_url=f"{BASE}/tikupc/chapterdetail/{row['kp_id']}-1-15-knowpoint",
                    suffix=".json",
                    meta={
                        "kp_id": row["kp_id"],
                        "kp_name": row["kp_name"],
                        "chapter": row["chapter"],
                        "seed": seed,
                        "start": 1,
                        "page_index": 1,
                    },
                )
            )
        return children

    def _expand_api_page(self, unit: Unit, raw: str) -> List[Unit]:
        payload = self._payload(raw)
        data = payload.get("data") or {}
        questions = data.get("question") or []
        info = data.get("kp_info") or {}
        total = int(info.get("que_num") or 0)
        start = int(unit.meta.get("start") or 1)
        page_index = int(unit.meta.get("page_index") or 1)

        if not questions or len(questions) < self.rn:
            return []
        if page_index >= self.pages_per_kp:
            return []
        next_start = start + self.rn
        if total and next_start > total:
            return []
        meta = dict(unit.meta)
        meta.update({"start": next_start, "page_index": page_index + 1})
        kp_id = str(unit.meta.get("kp_id") or unit.document_id)
        return [
            Unit(
                key=f"kp/{kp_id}/start={next_start}",
                url=api_url(kp_id, next_start, self.rn),
                kind="api_page",
                document_id=kp_id,
                document_title=unit.document_title,
                document_url=unit.document_url,
                suffix=".json",
                meta=meta,
            )
        ]

    # ------------------------------------------------------------------
    # parsing
    # ------------------------------------------------------------------
    def parse(self, unit: Unit, raw: str) -> List[Question]:
        payload = self._payload(raw)
        info = (payload.get("data") or {}).get("kp_info") or {}
        query = dict(parse_qsl(urlparse(unit.url).query))
        kp_id = str(info.get("kp_id") or query.get("kp_id") or unit.document_id)
        start = int(query.get("start") or unit.meta.get("start") or 1)
        unit.document_id = kp_id
        unit.meta.setdefault("kp_id", kp_id)
        unit.meta.setdefault("start", start)
        if not unit.document_url or unit.document_url == unit.url:
            unit.document_url = f"{BASE}/tikupc/chapterdetail/{kp_id}-1-15-knowpoint"
        data = payload.get("data") or {}
        questions: List[Question] = []
        for item in data.get("question") or []:
            record = self._parse_question(item, unit)
            if record is not None:
                questions.append(record)
        return questions

    def _payload(self, raw: str) -> Dict[str, Any]:
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError(f"invalid baidu JSON: {exc}") from exc
        status = payload.get("status") or {}
        code = status.get("code")
        if code not in (0, None):
            raise ValueError(f"baidu API error {code}: {status.get('msg') or ''}")
        return payload

    def _parse_question(self, item: Dict[str, Any], unit: Unit) -> Optional[Question]:
        raw_json = item.get("bdjson")
        if isinstance(raw_json, str):
            try:
                data = json.loads(raw_json)
            except json.JSONDecodeError:
                return None
        elif isinstance(raw_json, dict):
            data = raw_json
        else:
            return None

        question_id = str(item.get("qid") or data.get("qid") or "").strip()
        if not question_id:
            return None

        info = data.get("que_info") or {}
        qtype = str(info.get("type") or "")
        if qtype not in ("sc", "single", "mc", "multi"):
            return None

        stem_text = clean_text(_node_text(data.get("que_stem")))
        if len(stem_text) < 8 or stem_text in META_PLACEHOLDER:
            return None

        options: List[Option] = []
        answers: List[str] = []
        option_groups = data.get("que_options") or []
        if option_groups:
            for position, option in enumerate(option_groups[0][:8]):
                label = chr(ord("A") + position)
                text = clean_text(_node_text(option.get("ret")))
                if not text:
                    continue
                options.append(Option(label=label, text=text))
                if option.get("yes") is True:
                    answers.append(label)

        answer = normalize_answer("".join(answers))
        if not answer:
            answer = normalize_answer(str(data.get("que_answer") or "")) or None

        analysis = clean_text(str(info.get("analysis") or ""))
        if analysis in META_PLACEHOLDER or len(analysis) <= 2:
            analysis = None

        kp_name = PAGE_SUFFIX.sub("", str(unit.meta.get("kp_name") or ""))
        knowledge: List[str] = []
        if kp_name:
            knowledge.append(kp_name)
        for value in (info.get("know_point"), data.get("know_point"), info.get("know_point_v2")):
            if isinstance(value, str) and value.strip():
                knowledge.append(value.strip())
            elif isinstance(value, list):
                knowledge.extend(
                    str(part).strip() for part in value if str(part).strip()
                )
        knowledge = list(dict.fromkeys(knowledge))

        difficulty = info.get("difficulty")
        if isinstance(difficulty, (int, float)):
            difficulty_text = str(difficulty)
        elif isinstance(difficulty, str) and difficulty.strip():
            difficulty_text = difficulty.strip()
        else:
            difficulty_text = None

        year = None
        year_match = re.search(r"(20\d{2})", stem_text)
        if year_match:
            year = int(year_match.group(1))

        kp_id = str(unit.meta.get("kp_id") or unit.document_id)
        paper_url = (
            f"{BASE}/tikupc/chapterdetail/{kp_id}-1-15-knowpoint"
        )
        choice_type = "single" if qtype in ("sc", "single") else "multiple"
        digest = content_hash(
            stem_text, [(option.label, option.text) for option in options], answer
        )
        return Question(
            source=self.source_name,
            source_question_id=question_id,
            paper_id=kp_id,
            paper_title=kp_name or kp_id,
            subject=self.subject.name,
            stage=self.subject.stage,
            question_type="选择题",
            question_number=None,
            stem_html=stem_text,
            stem_text=stem_text,
            options=options,
            answer=answer,
            analysis_html=None,
            analysis_text=analysis,
            knowledge_points=knowledge,
            difficulty=difficulty_text,
            difficulty_stars=None,
            source_url=f"{BASE}/tikupc/singledetail/{question_id}",
            paper_url=paper_url,
            raw_block_html=None,
            resources=[],
            content_hash=digest,
            choice_type=choice_type,
        )

    @classmethod
    def detect(cls, url: str, html: Optional[str] = None) -> Optional[Subject]:
        match = re.search(r"-(\d+)-knowpoint", url or "")
        if match:
            code = CODE_BY_ID.get(int(match.group(1)), "")
            for item in cls.subjects:
                if item.code == code:
                    return item
        return None

    def supports(self, url: str) -> bool:
        return "tiku.baidu.com" in url and (
            "/chapterlist/" in url or "getqueinfos" in url
        )
