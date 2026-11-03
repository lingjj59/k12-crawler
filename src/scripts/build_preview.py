#!/usr/bin/env python3
"""Build a self-contained, double-clickable HTML preview for a question JSONL.

The page embeds the question data, so it works from ``file://`` without a
local web server.  Images keep their original URLs (or relative local paths
when the crawler downloaded them), and formula markup is rendered with simple
``<sub>`` / ``<sup>`` tags.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict, Iterable, List

PROJECT_ROOT = Path(__file__).resolve().parents[2]

FIELDS = (
    "source",
    "source_question_id",
    "paper_id",
    "paper_title",
    "paper_year",
    "paper_region",
    "paper_exam_type",
    "subject",
    "stage",
    "question_type",
    "choice_type",
    "question_number",
    "stem_text",
    "answer",
    "analysis_text",
    "knowledge_points",
    "difficulty_stars",
    "validation_status",
    "source_url",
)


def _records(path: Path) -> List[Dict[str, Any]]:
    records: List[Dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            raw = json.loads(line)
            item = {key: raw.get(key) for key in FIELDS}
            item["options"] = [
                {"label": option.get("label"), "text": option.get("text")}
                for option in raw.get("options") or []
            ]
            item["resources"] = [
                {
                    "ordinal": resource.get("ordinal"),
                    "role": resource.get("role"),
                    "placeholder": resource.get("placeholder"),
                    "original_url": resource.get("original_url"),
                    "original_extension": resource.get("original_extension"),
                    "local_path": resource.get("local_path"),
                }
                for resource in raw.get("resources") or []
            ]
            records.append(item)
    return records


TEMPLATE = r"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>%%TITLE%%</title>
<style>
  :root{
    --bg:#f2f5fa; --card:#fff; --ink:#172033; --muted:#667085; --line:#e4e9f2;
    --blue:#2563eb; --blue-soft:#eaf1ff; --green:#0f9f6e; --green-soft:#e8f8f1;
    --amber:#b45309; --amber-soft:#fff4e5; --red:#dc2626; --red-soft:#feecec;
    --shadow:0 10px 30px rgba(23,32,51,.07);
  }
  *{box-sizing:border-box}
  html{scroll-behavior:smooth}
  body{
    margin:0; background:var(--bg); color:var(--ink);
    font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Hiragino Sans GB","Microsoft YaHei",sans-serif;
    font-size:15px; line-height:1.7;
  }
  a{color:var(--blue); text-decoration:none}
  a:hover{text-decoration:underline}
  .wrap{width:min(1120px,calc(100% - 32px)); margin:0 auto}
  .hero{
    background:
      radial-gradient(900px 400px at 85% -20%, rgba(96,165,250,.38), transparent 60%),
      linear-gradient(135deg,#0f172a 0%,#172554 55%,#1d4ed8 100%);
    color:#fff; padding:34px 0 30px;
  }
  .eyebrow{font-size:12px; letter-spacing:.16em; color:#bfdbfe; text-transform:uppercase; font-weight:700}
  h1{margin:8px 0 6px; font-size:clamp(24px,3.2vw,38px); line-height:1.25; letter-spacing:-.02em}
  .sub{margin:0; color:#c7d2fe; font-size:14px}
  .sub a{color:#dbeafe; text-decoration:underline}
  .stats{display:flex; flex-wrap:wrap; gap:10px; margin-top:18px}
  .stat{
    background:rgba(255,255,255,.12); border:1px solid rgba(255,255,255,.18);
    border-radius:12px; padding:8px 12px; min-width:104px;
  }
  .stat b{display:block; font-size:20px; line-height:1.25}
  .stat span{font-size:12px; color:#c7d2fe}
  main{padding:22px 0 40px}
  .toolbar{
    position:sticky; top:0; z-index:20; display:flex; flex-wrap:wrap; gap:10px;
    align-items:center; padding:12px; background:rgba(242,245,250,.93);
    backdrop-filter:blur(10px); border:1px solid var(--line); border-radius:16px;
    box-shadow:0 4px 18px rgba(23,32,51,.05); margin-bottom:14px;
  }
  .toolbar input[type=search]{
    flex:1 1 300px; min-width:220px; height:40px; padding:0 13px;
    border:1px solid #d4dbe8; border-radius:10px; background:#fff; color:var(--ink);
    font-size:14px; outline:none;
  }
  .toolbar input[type=search]:focus{border-color:var(--blue); box-shadow:0 0 0 3px rgba(37,99,235,.12)}
  select{
    height:40px; padding:0 30px 0 10px; border:1px solid #d4dbe8; border-radius:10px;
    background:#fff; color:var(--ink); font-size:13px; outline:none; cursor:pointer;
  }
  .check{display:inline-flex; align-items:center; gap:6px; font-size:13px; color:var(--muted); white-space:nowrap; cursor:pointer}
  .check input{accent-color:var(--blue)}
  button{
    height:40px; padding:0 14px; border:1px solid transparent; border-radius:10px;
    background:var(--blue); color:#fff; font-size:13px; font-weight:650; cursor:pointer;
    transition:transform .08s ease, filter .15s ease;
  }
  button:hover{filter:brightness(1.06)}
  button:active{transform:translateY(1px)}
  button.ghost{background:#fff; color:var(--ink); border-color:#d4dbe8}
  .meta{display:flex; flex-wrap:wrap; gap:8px; align-items:center; color:var(--muted); font-size:13px; margin:4px 2px 12px}
  .meta b{color:var(--ink)}
  .note{background:#fff; border:1px solid var(--line); border-left:4px solid var(--blue); border-radius:12px; padding:10px 14px; color:#475467; font-size:13px; margin-bottom:14px}
  .card{background:var(--card); border:1px solid var(--line); border-radius:16px; box-shadow:var(--shadow); padding:18px 20px; margin-bottom:16px}
  .card-head{display:flex; flex-wrap:wrap; gap:10px; align-items:flex-start; justify-content:space-between}
  .badges{display:flex; flex-wrap:wrap; gap:6px}
  .badge{display:inline-flex; align-items:center; gap:4px; padding:2px 8px; border-radius:999px; font-size:12px; font-weight:650; background:#eef2f7; color:#475467; white-space:nowrap}
  .badge.src{background:var(--blue-soft); color:#1d4ed8}
  .badge.pass{background:var(--green-soft); color:#067a55}
  .badge.warn{background:var(--amber-soft); color:var(--amber)}
  .badge.fail{background:var(--red-soft); color:var(--red)}
  .badge.ghost{background:#f7f9fc; color:#667085; border:1px solid #e7ecf4}
  .qid{color:#98a2b3; font-size:12px; font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace}
  .paper{margin:10px 0 8px; color:#475467; font-size:13px}
  .paper span{color:#98a2b3}
  .stem{font-size:16px; line-height:1.95; color:#1d2939}
  .rich sub,.rich sup{font-size:.72em; line-height:0}
  .rich p{margin:.4em 0}
  .options{display:grid; gap:8px; margin:14px 0 10px}
  .option{display:flex; gap:10px; align-items:flex-start; background:#f8fafc; border:1px solid #edf1f7; border-radius:10px; padding:9px 12px; color:#344054}
  .option .label{flex:0 0 auto; width:24px; height:24px; border-radius:50%; display:grid; place-items:center; background:#fff; border:1px solid #d6deeb; color:#344054; font-weight:750; font-size:12px; margin-top:1px}
  .answer{display:flex; flex-wrap:wrap; align-items:center; gap:8px; margin:12px 0 4px; padding:9px 12px; border-radius:10px; background:var(--green-soft); color:#067a55; font-size:14px}
  .answer b{font-size:16px; letter-spacing:.06em}
  details{margin-top:10px; border-top:1px dashed #e4e9f2; padding-top:10px}
  summary{cursor:pointer; color:var(--blue); font-size:13px; font-weight:650; user-select:none; width:max-content}
  .analysis{margin-top:8px; color:#475467; font-size:14px; line-height:1.85; white-space:pre-wrap}
  .card-foot{display:flex; flex-wrap:wrap; gap:10px; justify-content:space-between; align-items:center; margin-top:12px; padding-top:10px; border-top:1px solid #f0f3f8}
  .chips{display:flex; flex-wrap:wrap; gap:6px}
  .chip{font-size:12px; color:#475467; background:#f4f7fb; border:1px solid #e8edf5; border-radius:999px; padding:2px 8px}
  .chip.stars{color:#b45309; background:#fff8ec; border-color:#fde8c8}
  .links{display:flex; gap:10px; align-items:center; font-size:12px; color:#98a2b3}
  .links a{font-weight:650}
  .inline-img-wrap{display:inline-flex; align-items:center; gap:4px; margin:4px 3px; vertical-align:middle}
  .inline-img-wrap img{max-width:100%; max-height:320px; border:1px solid #e4e9f2; border-radius:8px; background:#fff; padding:2px; vertical-align:middle}
  .inline-img-wrap img:hover{outline:2px solid rgba(37,99,235,.28); outline-offset:2px}
  .img-fallback{display:none; align-items:center; padding:2px 8px; border:1px dashed #cbd5e1; border-radius:6px; color:#64748b; font-size:12px; background:#f8fafc}
  .inline-img-wrap.img-error .img-fallback{display:inline-flex}
  .img-chip{display:inline-flex; align-items:center; padding:2px 8px; border:1px dashed #cbd5e1; border-radius:6px; color:#64748b; font-size:12px; background:#f8fafc}
  .empty{background:#fff; border:1px dashed #cbd5e1; border-radius:16px; padding:40px; text-align:center; color:#667085}
  .pager{display:flex; flex-wrap:wrap; gap:6px; justify-content:center; align-items:center; margin:22px 0 0}
  .pager button{height:34px; padding:0 11px; background:#fff; color:#344054; border:1px solid #d4dbe8; font-weight:600}
  .pager button.active{background:var(--blue); color:#fff; border-color:var(--blue)}
  .pager button:disabled{opacity:.45; cursor:not-allowed}
  .pager .dots{color:#98a2b3; padding:0 3px}
  footer{padding:0 0 30px; color:#98a2b3; font-size:12px; text-align:center}
  @media (max-width:640px){
    .hero{padding:26px 0 22px}
    .card{padding:15px}
    .stem{font-size:15px}
    .toolbar{position:static}
  }
</style>
</head>
<body>
<header class="hero">
  <div class="wrap">
    <div class="eyebrow">K12 题库 · 数据预览</div>
    <h1>%%TITLE%%</h1>
    <p class="sub">数据源：%%SOURCE%% · 只读预览，不修改数据 · <a href="index.html">切换题库</a></p>
    <div class="stats" id="stats"></div>
  </div>
</header>
<main class="wrap">
  <section class="toolbar" aria-label="筛选">
    <input id="q" type="search" placeholder="搜索题干 / 选项 / 解析 / ID / 知识点…" autocomplete="off">
    <select id="year" aria-label="年份"><option value="">全部年份</option></select>
    <select id="status" aria-label="校验状态"><option value="">全部状态</option></select>
    <select id="type" aria-label="单选多选"><option value="">全部类型</option></select>
    <label class="check"><input type="checkbox" id="onlyImg"> 只看有图</label>
    <button id="random" type="button">随机看 10 条</button>
  </section>
  <div class="meta" id="meta"></div>
  <div class="note">%%NOTE%%</div>
  <div id="list"></div>
  <div class="pager" id="pager"></div>
</main>
<footer>由 <code>src/scripts/build_preview.py</code> 生成 · 题目数据来自项目采集结果</footer>
<script>
const DATA = %%DATA%%;

const state = {
  q: "", year: "", status: "", type: "", onlyImg: false,
  page: 1, pageSize: 10, order: "default", randomIds: null
};

const $ = (selector) => document.querySelector(selector);
const ESC = {"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"};
const esc = (value) => String(value ?? "").replace(/[&<>"']/g, (ch) => ESC[ch]);

function hasImage(item) {
  return Array.isArray(item.resources) && item.resources.length > 0;
}
function choiceLabel(value) {
  return value === "single" ? "单选" : value === "multiple" ? "多选" : "题型未定";
}
function starsLabel(value) {
  const n = Number(value || 0);
  return n > 0 ? "★".repeat(Math.min(n, 5)) : "";
}
function paperLabel(item) {
  return [item.paper_year, item.paper_region, item.paper_exam_type].filter(Boolean).join(" · ");
}

const ASSET_PREFIX = "%%ASSET_PREFIX%%";

const HAYSTACK = new Map(DATA.map((item) => [
  item.source_question_id,
  [
    item.source_question_id, item.paper_title, item.stem_text, item.answer,
    item.analysis_text, (item.knowledge_points || []).join(" "),
    (item.options || []).map((option) => option.text).join(" ")
  ].filter(Boolean).join("\n").toLowerCase()
]));

function imageSrc(resource) {
  if (!resource) return "";
  if (resource.local_path) {
    const path = String(resource.local_path);
    return path.startsWith("/") || path.startsWith("../") ? path : ASSET_PREFIX + path;
  }
  return resource.original_url || "";
}

function imageHtml(resource, ordinal) {
  const label = "图 " + ordinal;
  const src = imageSrc(resource);
  if (!src) return '<span class="img-chip">' + label + '（无链接）</span>';
  const safeSrc = esc(src);
  return '<span class="inline-img-wrap">' +
    '<img src="' + safeSrc + '" alt="' + label + '" loading="lazy" referrerpolicy="no-referrer" ' +
    'onerror="this.style.display=\'none\';this.parentElement.classList.add(\'img-error\')">' +
    '<a class="img-fallback" href="' + safeSrc + '" target="_blank" rel="noreferrer">' + label + '</a>' +
    '</span>';
}

function renderRich(text, resources) {
  const byOrdinal = new Map((resources || []).map((resource) => [String(resource.ordinal), resource]));
  let html = esc(text || "");
  html = html.replace(/_\{([^{}]*)\}/g, "<sub>$1</sub>");
  html = html.replace(/\^\{([^{}]*)\}/g, "<sup>$1</sup>");
  html = html.replace(/_([0-9]+)/g, "<sub>$1</sub>");
  html = html.replace(/\^([0-9+\-]+)/g, "<sup>$1</sup>");
  html = html.replace(/\n/g, "<br>");
  html = html.replace(/\[\[IMG:(\d+)\]\]/g, (_, ordinal) => imageHtml(byOrdinal.get(String(ordinal)), ordinal));
  return html;
}

function filtered() {
  const keyword = state.q.trim().toLowerCase();
  let items = DATA.filter((item) => {
    if (state.year && String(item.paper_year || "") !== state.year) return false;
    if (state.status && item.validation_status !== state.status) return false;
    if (state.type && item.choice_type !== state.type) return false;
    if (state.onlyImg && !hasImage(item)) return false;
    if (keyword && !(HAYSTACK.get(item.source_question_id) || "").includes(keyword)) return false;
    return true;
  });
  if (state.order === "random" && state.randomIds) {
    const byId = new Map(items.map((item) => [item.source_question_id, item]));
    items = state.randomIds.map((id) => byId.get(id)).filter(Boolean);
  }
  return items;
}

function card(item) {
  const resources = item.resources || [];
  const options = (item.options || []).map((option) =>
    '<div class="option"><span class="label">' + esc(option.label || "") + '</span>' +
    '<div class="rich">' + renderRich(option.text || "", resources) + '</div></div>'
  ).join("");
  const knowledge = (item.knowledge_points || []).map((point) =>
    '<span class="chip">' + esc(point) + '</span>'
  ).join("");
  const stars = starsLabel(item.difficulty_stars);
  const status = item.validation_status || "unknown";
  const analysis = item.analysis_text
    ? '<details><summary>查看解析（' + (item.analysis_text.length > 0 ? "已收录" : "") + '）</summary>' +
      '<div class="analysis rich">' + renderRich(item.analysis_text, resources) + '</div></details>'
    : '<div class="meta">暂无解析</div>';
  const sourceLink = item.source_url
    ? '<a href="' + esc(item.source_url) + '" target="_blank" rel="noreferrer">原题链接 ↗</a>'
    : "";
  return '<article class="card">' +
    '<div class="card-head">' +
      '<div class="badges">' +
        '<span class="badge src">' + esc(item.source || "") + '</span>' +
        '<span class="badge ' + esc(status) + '">' + esc(status) + '</span>' +
        '<span class="badge">' + esc(choiceLabel(item.choice_type)) + '</span>' +
        (item.paper_year ? '<span class="badge ghost">' + esc(item.paper_year) + '</span>' : '') +
        (item.paper_region ? '<span class="badge ghost">' + esc(item.paper_region) + '</span>' : '') +
        '<span class="badge ghost">' + esc(item.paper_exam_type || "未标注") + '</span>' +
      '</div>' +
      '<div class="qid">#' + esc(item.source_question_id || "") + '</div>' +
    '</div>' +
    '<div class="paper">' + esc(item.paper_title || "") +
      (item.question_number ? '<span> · 第 ' + esc(item.question_number) + ' 题</span>' : '') +
      '</div>' +
    '<div class="stem rich">' + renderRich(item.stem_text, resources) + '</div>' +
    (options ? '<div class="options">' + options + '</div>' : '') +
    '<div class="answer">答案：<b>' + esc(item.answer || "—") + '</b>' +
      '<span style="color:#667085;font-size:12px">' + esc(paperLabel(item)) + '</span></div>' +
    analysis +
    '<div class="card-foot">' +
      '<div class="chips">' + knowledge +
        (stars ? '<span class="chip stars">' + stars + '</span>' : '') +
        '<span class="chip">' + (resources.length ? resources.length + " 张图" : "无图") + '</span>' +
      '</div>' +
      '<div class="links">' + sourceLink + '</div>' +
    '</div>' +
  '</article>';
}

function pageNumbers(current, total) {
  if (total <= 7) return Array.from({length: total}, (_, index) => index + 1);
  const pages = [1];
  const start = Math.max(2, current - 1);
  const end = Math.min(total - 1, current + 1);
  if (start > 2) pages.push("…");
  for (let page = start; page <= end; page += 1) pages.push(page);
  if (end < total - 1) pages.push("…");
  pages.push(total);
  return pages;
}

function renderPager(totalPages) {
  if (totalPages <= 1) {
    $("#pager").innerHTML = "";
    return;
  }
  const buttons = [];
  buttons.push('<button type="button" data-page="' + (state.page - 1) + '" ' +
    (state.page === 1 ? "disabled" : "") + '>上一页</button>');
  pageNumbers(state.page, totalPages).forEach((page) => {
    if (page === "…") {
      buttons.push('<span class="dots">…</span>');
    } else {
      buttons.push('<button type="button" data-page="' + page + '" ' +
        (page === state.page ? 'class="active"' : "") + '>' + page + '</button>');
    }
  });
  buttons.push('<button type="button" data-page="' + (state.page + 1) + '" ' +
    (state.page === totalPages ? "disabled" : "") + '>下一页</button>');
  $("#pager").innerHTML = buttons.join("");
}

function render() {
  const items = filtered();
  const totalPages = Math.max(1, Math.ceil(items.length / state.pageSize));
  if (state.page > totalPages) state.page = totalPages;
  if (state.page < 1) state.page = 1;
  const start = (state.page - 1) * state.pageSize;
  const pageItems = items.slice(start, start + state.pageSize);
  const randomTag = state.order === "random" ? ' · 随机顺序 <a href="#" id="resetOrder">恢复默认顺序</a>' : "";
  $("#meta").innerHTML = "当前筛选 <b>" + items.length + "</b> / " + DATA.length + " 条" +
    " · 第 <b>" + state.page + "</b> / " + totalPages + " 页" + randomTag;
  $("#list").innerHTML = pageItems.length
    ? pageItems.map(card).join("")
    : '<div class="empty">没有符合条件的题目，试试清空搜索或筛选条件。</div>';
  renderPager(totalPages);
  const reset = $("#resetOrder");
  if (reset) reset.addEventListener("click", (event) => {
    event.preventDefault();
    state.order = "default";
    state.randomIds = null;
    state.page = 1;
    render();
  });
}

function optionsFor(select, values, allLabel, labeler) {
  select.innerHTML = '<option value="">' + allLabel + '</option>' +
    values.map((value) => '<option value="' + esc(value) + '">' + esc(labeler(value)) + '</option>').join("");
}

function init() {
  const years = [...new Set(DATA.map((item) => item.paper_year).filter(Boolean))].sort((a, b) => b - a);
  const statuses = [...new Set(DATA.map((item) => item.validation_status).filter(Boolean))];
  const types = [...new Set(DATA.map((item) => item.choice_type).filter(Boolean))];
  optionsFor($("#year"), years, "全部年份", (value) => value + " 年");
  optionsFor($("#status"), statuses, "全部状态", (value) => value);
  optionsFor($("#type"), types, "全部类型", choiceLabel);

  const papers = new Set(DATA.map((item) => item.source + ":" + item.paper_id)).size;
  const withImages = DATA.filter(hasImage).length;
  const analyses = DATA.filter((item) => item.analysis_text).length;
  $("#stats").innerHTML = [
    [DATA.length, "题目"],
    [papers, "试卷"],
    [withImages, "含图题"],
    [analyses, "含解析"]
  ].map(([value, label]) => '<div class="stat"><b>' + value + '</b><span>' + label + '</span></div>').join("");

  $("#q").addEventListener("input", (event) => {
    state.q = event.target.value;
    state.page = 1;
    render();
  });
  ["year", "status", "type"].forEach((id) => {
    $("#" + id).addEventListener("change", (event) => {
      state[id] = event.target.value;
      state.page = 1;
      render();
    });
  });
  $("#onlyImg").addEventListener("change", (event) => {
    state.onlyImg = event.target.checked;
    state.page = 1;
    render();
  });
  $("#random").addEventListener("click", () => {
    const ids = filtered().map((item) => item.source_question_id);
    for (let index = ids.length - 1; index > 0; index -= 1) {
      const swap = Math.floor(Math.random() * (index + 1));
      [ids[index], ids[swap]] = [ids[swap], ids[index]];
    }
    state.randomIds = ids;
    state.order = "random";
    state.page = 1;
    render();
  });
  $("#pager").addEventListener("click", (event) => {
    const button = event.target.closest("button[data-page]");
    if (!button) return;
    const page = Number(button.dataset.page);
    if (page >= 1 && page <= Math.max(1, Math.ceil(filtered().length / state.pageSize))) {
      state.page = page;
      render();
      window.scrollTo({top: 0, behavior: "smooth"});
    }
  });
  render();
}

init();
</script>
</body>
</html>
"""


def build(input_path: Path, output_path: Path, title: str, note: str, asset_prefix: str = "../") -> int:
    records = _records(input_path)
    payload = json.dumps(records, ensure_ascii=False, separators=(",", ":"))
    # Never let question text terminate the <script> block.
    payload = payload.replace("</", "<\\/").replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")
    source = input_path.resolve().relative_to(PROJECT_ROOT)
    html = (
        TEMPLATE.replace("%%TITLE%%", title)
        .replace("%%SOURCE%%", str(source))
        .replace("%%NOTE%%", note)
        .replace("%%ASSET_PREFIX%%", asset_prefix)
        .replace("%%DATA%%", payload)
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(html, encoding="utf-8")
    return len(records)


def main() -> int:
    parser = argparse.ArgumentParser(description="Build a self-contained question preview HTML")
    parser.add_argument("--input", type=Path, required=True, help="question JSONL")
    parser.add_argument("--output", type=Path, required=True, help="output HTML")
    parser.add_argument("--title", required=True)
    parser.add_argument(
        "--note",
        default="图片为原始外链；WMF 公式图若无法直接显示，可点击“图 n”打开原图。",
    )
    parser.add_argument(
        "--asset-prefix",
        default="../",
        help="prefix for project-relative local image paths (default ../ for docs/)",
    )
    args = parser.parse_args()
    count = build(args.input, args.output, args.title, args.note, args.asset_prefix)
    print(f"wrote {args.output} with {count} questions")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
