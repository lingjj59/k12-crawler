"""可圈可点 (cooco): exam lists -> paper pages.

The parsing logic is intentionally conservative: options may be split across
``<p>``/``<span>``/``<font>`` tags, and answers may be a short letter string or
a multi-blank fill-in.  Watermarks and recommendation blocks are excluded.
"""

from __future__ import annotations

import copy
import re
from pathlib import Path
from typing import Iterable, List, Optional, Sequence, Tuple
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup, NavigableString, Tag

from ...html_utils import (
    absolute_url,
    clean_text,
    content_hash,
    markup_to_html,
    normalize_answer,
    parse_int,
    rich_text_to_plain,
    split_option_html,
    split_options,
)
from ...models import Option, Question, Resource
from ...subjects import Subject, detect_from_html, subject
from ..site import Site
from ..unit import INDEX, Unit

SUBJECTS = (
    subject("gzwl", "高中", "物理"),
    subject("gzsx", "高中", "数学"),
    subject("gzhx", "高中", "化学"),
    subject("gzyy", "高中", "英语"),
    subject("gzyw", "高中", "语文"),
    subject("gzls", "高中", "历史"),
    subject("gzsw", "高中", "生物"),
    subject("gzdl", "高中", "地理"),
    subject("gzzz", "高中", "政治"),
    subject("czwl", "初中", "物理"),
    subject("czsx", "初中", "数学"),
    subject("czhx", "初中", "化学"),
    subject("czyy", "初中", "英语"),
    subject("czyw", "初中", "语文"),
    subject("czls", "初中", "历史"),
    subject("czsw", "初中", "生物"),
    subject("czdl", "初中", "地理"),
    subject("czzz", "初中", "政治"),
)

# Subject/stage codes used by cooco's subject subdomains.
SUBJECT_MAP = {
    "czwl": ("初中", "物理"),
    "czsx": ("初中", "数学"),
    "czhx": ("初中", "化学"),
    "czyy": ("初中", "英语"),
    "czyw": ("初中", "语文"),
    "czls": ("初中", "历史"),
    "czsw": ("初中", "生物"),
    "czdl": ("初中", "地理"),
    "czzz": ("初中", "政治"),
    "gzwl": ("高中", "物理"),
    "gzsx": ("高中", "数学"),
    "gzhx": ("高中", "化学"),
    "gzyy": ("高中", "英语"),
    "gzyw": ("高中", "语文"),
    "gzls": ("高中", "历史"),
    "gzsw": ("高中", "生物"),
    "gzdl": ("高中", "地理"),
    "gzzz": ("高中", "政治"),
}

PROVINCES = (
    "北京", "天津", "河北", "山西", "内蒙古", "辽宁", "吉林", "黑龙江",
    "上海", "江苏", "浙江", "安徽", "福建", "江西", "山东", "河南", "湖北",
    "湖南", "广东", "广西", "海南", "重庆", "四川", "贵州", "云南", "西藏",
    "陕西", "甘肃", "青海", "宁夏", "新疆",
)

ANSWER_MARKERS = ("【分析】", "【详解】", "【点睛】")


class _Collector:
    """Assign stable ordinals and placeholders to images in document order."""

    def __init__(self, base_url: str):
        self.base_url = base_url
        self.resources: List[Resource] = []
        self._next = 1

    def clone(self, node: Tag, role: str, option_label: Optional[str] = None) -> Tag:
        clone = copy.deepcopy(node)
        for img in clone.find_all("img"):
            src = img.get("src") or img.get("data-src") or img.get("data-lazysrc")
            original_url = absolute_url(self.base_url, src)
            ordinal = self._next
            self._next += 1
            placeholder = f"[[IMG:{ordinal}]]"
            resource = Resource(
                ordinal=ordinal,
                role=role,
                option_label=option_label,
                original_url=original_url or "",
                original_extension=(
                    Path(urlparse(original_url).path).suffix.lower().lstrip(".")
                    if original_url
                    else None
                ),
                placeholder=placeholder,
                conversion_status="pending",
                width=parse_int(img.get("width")),
                height=parse_int(img.get("height")),
                error=None if original_url else "missing image src",
            )
            self.resources.append(resource)
            img.replace_with(NavigableString(placeholder))
        return clone


def _direct_paragraphs(node: Tag) -> List[Tag]:
    paragraphs = [child for child in node.children if isinstance(child, Tag) and child.name == "p"]
    if paragraphs:
        return paragraphs
    return node.find_all("p", recursive=False)


def _text_of(node: Tag) -> str:
    # Keep <sup>/<sub> visible as ^{...}/_{...} in the normalized text.
    return clean_text(rich_text_to_plain(str(node)))


def _find_option_start(paragraph_texts: Sequence[str]) -> Optional[int]:
    for index, text in enumerate(paragraph_texts):
        if re.match(r"^\s*A\s*[．.、]", text):
            # One paragraph with A/B/C/D, or a series of paragraphs starting with A.
            if "B" in text or any(
                re.match(r"^\s*B\s*[．.、]", later) for later in paragraph_texts[index + 1 :]
            ):
                return index
    return None


def _earliest_marker(text: str) -> Optional[Tuple[int, str]]:
    found = []
    for marker in ANSWER_MARKERS:
        pos = text.find(marker)
        if pos >= 0:
            found.append((pos, marker))
    return min(found) if found else None


def _answer_and_analysis(
    answer_ct: Tag, collector: _Collector
) -> Tuple[Optional[str], Optional[str], Optional[str], List[Resource]]:
    """Return (answer, analysis_text, analysis_html, resources)."""
    paragraphs = _direct_paragraphs(answer_ct)
    if not paragraphs:
        clone = collector.clone(answer_ct, "analysis")
        text = _text_of(clone)
        return normalize_answer(text), None, str(clone), collector.resources

    paragraph_texts = [_text_of(p) for p in paragraphs]
    marker_info = None
    marker_index = None
    for index, text in enumerate(paragraph_texts):
        info = _earliest_marker(text)
        if info:
            marker_info = info
            marker_index = index
            break

    answer_parts: List[str] = []
    analysis_parts: List[str] = []
    analysis_html_parts: List[str] = []
    for index, paragraph in enumerate(paragraphs):
        role = "answer"
        if marker_index is not None and index >= marker_index:
            role = "analysis"
        clone = collector.clone(paragraph, role)
        html_part = str(clone)
        text_part = _text_of(clone)

        if marker_index is None:
            if index == 0 and re.fullmatch(r"[\sA-D、,，.．]+", text_part) and len(text_part) <= 12:
                answer_parts.append(text_part)
            else:
                analysis_parts.append(text_part)
                analysis_html_parts.append(html_part)
        elif index < marker_index:
            answer_parts.append(text_part)
            analysis_html_parts.append(html_part)
        elif index == marker_index and marker_info is not None:
            pos, marker = marker_info
            before = text_part[:pos]
            after = text_part[pos + len(marker) :]
            if before.strip():
                answer_parts.append(before)
            analysis_parts.append(after)
            analysis_html_parts.append(html_part)
        else:
            analysis_parts.append(text_part)
            analysis_html_parts.append(html_part)

    if marker_index is None and not answer_parts and paragraph_texts:
        # Be conservative: no explicit marker and no short answer paragraph.
        analysis_parts = paragraph_texts
        analysis_html_parts = [str(collector.clone(p, "analysis")) for p in paragraphs]

    answer = normalize_answer(" ".join(answer_parts))
    analysis_text = clean_text(" ".join(part for part in analysis_parts if part), multiline=False)
    analysis_html = "".join(analysis_html_parts) or None
    if not analysis_text:
        analysis_text = None
    return answer, analysis_text, analysis_html, collector.resources


class CoocoSite(Site):
    name = "cooco"
    label = "可圈可点"
    subjects = SUBJECTS

    @classmethod
    def seed_for(cls, subject_: Subject) -> str:
        return f"http://{subject_.code}.cooco.net.cn/exam_list_new/{subject_.code}/"

    def seed_unit(self, seed: str) -> Unit:
        seed = (seed or self.seed_for(self.subject)).rstrip("/") + "/"
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
        return f"{seed}?page={int(page)}"

    def expand(self, unit: Unit, raw: str) -> Iterable[Unit]:
        if unit.kind != "list":
            return ()
        soup = BeautifulSoup(raw, "lxml")
        page = int(unit.meta.get("page") or 1)
        seed = str(unit.meta.get("seed") or unit.url)

        children: List[Unit] = []
        seen = set()
        for item in soup.select("div.m-examList div.item"):
            link = item.select_one('a[href*="/paper/"]')
            if not link:
                continue
            href = link.get("href") or ""
            match = re.search(r"/paper/(\d+)/", href)
            if not match:
                continue
            paper_id = match.group(1)
            if paper_id in seen:
                continue
            seen.add(paper_id)
            title = clean_text(link.get("title") or link.get_text(" ", strip=True))
            children.append(
                Unit(
                    key=paper_id,
                    url=urljoin(unit.url, href),
                    kind="paper",
                    document_id=paper_id,
                    document_title=title or paper_id,
                    meta={"page": page},
                )
            )

        page_input = soup.select_one("#pageIpt")
        total_pages = parse_int(page_input.get("data-total")) if page_input else None
        if children and page < (total_pages or page):
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

    def parse(self, unit: Unit, raw: str) -> List[Question]:
        soup = BeautifulSoup(raw, "lxml")
        url = unit.url
        page_kind = "paper" if "/paper/" in url else "detail"
        paper_match = re.search(r"/paper/(\d+)/", url)
        detail_match = re.search(r"/testdetail/(\d+)/", url)
        if paper_match:
            paper_id = paper_match.group(1)
        elif detail_match:
            paper_id = f"detail_{detail_match.group(1)}"
        else:
            paper_id = unit.document_id

        title = clean_text(soup.title.get_text(" ", strip=True) if soup.title else "")
        if unit.document_title and page_kind == "paper":
            title = unit.document_title
        if not title:
            title = f"题目 {detail_match.group(1)}" if detail_match else paper_id

        container = soup.select_one("div.makeContent")
        candidate_groups = (
            container.select("div.subjectGroup") if container else soup.select("div.subjectGroup")
        )
        groups: List[Tag] = []
        for group in candidate_groups:
            if group.find_parent("div", class_="recommendCT"):
                continue
            if group.find_parent("div", class_="list"):
                continue
            groups.append(group)
        if page_kind == "detail" and groups:
            # The detail page can expose the main question in more than one
            # place; keep the first (main) block only.
            groups = groups[:1]

        stage, subject_name = self.subject.stage, self.subject.name
        if groups:
            lesson_code = groups[0].get("data-lesson") or self.subject.code
            stage, subject_name = SUBJECT_MAP.get(
                lesson_code, (self.subject.stage, self.subject.name)
            )
        year = None
        year_match = re.search(r"(20\d{2})", title)
        if year_match:
            year = int(year_match.group(1))
        region = next((p for p in PROVINCES if p in title), None)
        exam_type = self._exam_type(title)
        unit.document_id = paper_id
        unit.document_title = title
        unit.meta.update(
            {
                "page_kind": page_kind,
                "subject": subject_name,
                "stage": stage,
                "year": year,
                "region": region,
                "exam_type": exam_type,
            }
        )

        questions: List[Question] = []
        for group in groups:
            question = self._parse_question(
                group=group,
                paper_id=paper_id,
                paper_title=title,
                paper_url=url,
                stage=stage,
                subject_name=subject_name,
            )
            if question is not None:
                questions.append(question)
        return questions

    def _parse_question(
        self,
        group: Tag,
        paper_id: str,
        paper_title: str,
        paper_url: str,
        stage: str,
        subject_name: str,
    ) -> Optional[Question]:
        question_id = group.get("data-id")
        if not question_id:
            return None
        question_type = clean_text(group.get("data-tname") or "未知")
        parsed = urlparse(paper_url)
        collector = _Collector(f"{parsed.scheme}://{parsed.netloc}")

        ct = group.select_one("div.ct")
        if ct is None:
            return None
        paragraphs = _direct_paragraphs(ct)
        paragraph_texts = [_text_of(p) for p in paragraphs]
        option_start = _find_option_start(paragraph_texts)

        stem_nodes = paragraphs[:option_start] if option_start is not None else paragraphs
        option_nodes = paragraphs[option_start:] if option_start is not None else []
        stem_html_parts: List[str] = []
        stem_text_parts: List[str] = []
        for paragraph in stem_nodes:
            clone = collector.clone(paragraph, "stem")
            stem_html_parts.append(str(clone))
            stem_text_parts.append(_text_of(clone))

        option_html_parts: List[str] = []
        option_text_parts: List[str] = []
        for paragraph in option_nodes:
            clone = collector.clone(paragraph, "option")
            option_html_parts.append(str(clone))
            option_text_parts.append(_text_of(clone))
        options: List[Option] = []
        option_html_blob = "\n".join(option_html_parts)
        rich_options = split_option_html(option_html_blob)
        if rich_options and all(part[2] for part in rich_options):
            options = [
                Option(label=label, text=text, html=option_html)
                for label, text, option_html in rich_options
            ]
        else:
            # The marker letters are often split across <span>/<font> tags, so
            # slicing the original HTML is not always possible.  Fall back to
            # normalized text and rebuild a small HTML fragment that keeps
            # <sup>/<sub> and [[IMG:n]] placeholders.
            option_blob = clean_text("\n".join(option_text_parts), multiline=True)
            options = [
                Option(label=label, text=text, html=markup_to_html(text))
                for label, text in split_options(option_blob)
            ]
        for resource in collector.resources:
            if resource.role != "option":
                continue
            for option in options:
                if resource.placeholder in option.text or (
                    option.html and resource.placeholder in option.html
                ):
                    resource.option_label = option.label
                    break

        answer_ct = group.select_one("div.answer .answerCt")
        if answer_ct is not None:
            answer, analysis_text, analysis_html, answer_resources = _answer_and_analysis(
                answer_ct, collector
            )
        else:
            answer, analysis_text, analysis_html, answer_resources = None, None, None, []

        question_number = None
        number_node = group.select_one("div.subjectNumber")
        if number_node:
            question_number = parse_int(number_node.get_text(" ", strip=True))

        knowledge_points: List[str] = []
        zsd = group.select_one("div.zsd")
        if zsd:
            raw = clean_text(zsd.get_text(" ", strip=True))
            raw = re.sub(r"^知识点[:：]\s*", "", raw)
            knowledge_points = [
                item.strip()
                for item in re.split(r"[、,，;；]", raw)
                if item.strip() and item.strip() != "未分类"
            ]

        difficulty = None
        starbox = group.select_one("span.starbox")
        if starbox and starbox.get("data-diff"):
            difficulty = clean_text(starbox.get("data-diff"))
        difficulty_stars = len(group.select("i.icon_star.active")) or None

        stem_text = clean_text(" ".join(stem_text_parts))
        detail_url = f"{parsed.scheme}://{parsed.netloc}/testdetail/{question_id}/"
        choice_type = "unknown"
        # Only choice questions have option-letter answers.  Non-choice
        # answers (e.g. chemistry fill-in blanks) must not be classified as
        # single/multiple choice.
        if answer and options and question_type == "选择题":
            choice_type = "single" if len(answer) == 1 else "multiple"

        digest = content_hash(
            stem_text,
            [(option.label, option.text) for option in options],
            answer,
        )
        return Question(
            source=self.source_name,
            source_question_id=str(question_id),
            paper_id=paper_id,
            paper_title=paper_title,
            subject=subject_name,
            stage=stage,
            question_type=question_type,
            question_number=question_number,
            stem_html="".join(stem_html_parts),
            stem_text=stem_text,
            options=options,
            answer=answer,
            analysis_html=analysis_html,
            analysis_text=analysis_text,
            knowledge_points=knowledge_points,
            difficulty=difficulty,
            difficulty_stars=difficulty_stars,
            source_url=detail_url,
            paper_url=paper_url,
            raw_block_html=str(group),
            resources=collector.resources,
            content_hash=digest,
            choice_type=choice_type,
        )

    @staticmethod
    def _exam_type(title: str) -> Optional[str]:
        if "高考真题" in title or "高考" in title:
            return "高考真题"
        if "普通高校招生" in title or "选考" in title:
            return "高考真题"
        if "模拟" in title:
            return "模拟"
        if "单元测试" in title:
            return "单元测试"
        if "专题" in title:
            return "专题练习"
        if "期中" in title:
            return "期中"
        if "期末" in title:
            return "期末"
        if "月考" in title:
            return "月考"
        return None

    @classmethod
    def detect(cls, url: str, html: Optional[str] = None) -> Optional[Subject]:
        match = re.match(r"([a-z0-9]+)\.cooco\.net\.cn", urlparse(url or "").netloc)
        if match:
            for item in cls.subjects:
                if item.code == match.group(1):
                    return item
        return detect_from_html(cls.subjects, html)

    def supports(self, url: str) -> bool:
        return "cooco.net.cn" in url and ("/paper/" in url or "/testdetail/" in url)
