---
name: k12-crawler
description: 面向 Agent 的 K12 多学科试题原始数据爬虫。站点用 Site/Unit 模型描述，抓取任意层级的列表、试卷、详情或 JSON API 分页，保存原始响应并输出结构化 JSONL；支持断点、限速、单页提取与学科自动识别。
---

# K12 Crawler（Agent 工具）

只做一件事：**从网站抓取原始题目数据**。不写 SQLite，不做质量报告。
下游的校验、去重、入库、导出继续使用 `src/k12_crawler/` 里的现有框架。

站点与学科解耦：一个 `Site` 类描述一个网站，绑定一个“学科频道”；物理、化学、数学、生物、语文、英语、地理、历史、政治等共用同一套 runner。站点目录在 `src/k12_crawler/harvest/sites/`，注册表在 `harvest/registry.py`。

## 命令

```bash
# 可用站点及学科频道
python src/main.py sites --json
python src/main.py sites --subject gzhx --json

# 批量抓取（每次运行是一个批次；重复运行会跳过已完成 unit）
python src/main.py harvest --site jyeoo --subject gzhx --max-units 20 --max-pages 5 --json
python src/main.py harvest --site cooco --subject gzwl --max-units 10 --max-pages 3 --json
# 百度题库：章节目录 → 知识点 API 分页
python src/main.py harvest --site baidu --subject gzhx --max-units 20 --json

# 中学段可直接写中文（同名跨学段时必须写学段或代码）
python src/main.py harvest --site jyeoo --subject 高中化学 --max-units 5 --json

# 指定入口（列表页、章节目录或任意 seed URL）
python src/main.py harvest --site jyeoo \
  --url https://www.jyeoo.com/shijuan/gzhx/ \
  --max-units 5 --json

# 下载题目图片
python src/main.py harvest --site jyeoo --subject gzhx --max-units 3 --images --json

# 忽略进度，重新抓取
python src/main.py harvest --site jyeoo --subject gzhx --max-units 3 --force --json

# 单页提取：给一个试卷/单题/API URL，直接返回 JSON，不落盘
# /pp/、/paper/ 这类 URL 不含学科，程序会读页面标题/meta 自动识别；
# 识别不了时用 --subject 指定。
python src/main.py extract --site jyeoo \
  --url https://www.jyeoo.com/pp/6e618272-89e6-4757-a293-5f8e796e258a --json
python src/main.py extract --site cooco --subject gzhx \
  --url http://gzhx.cooco.net.cn/paper/1688356417/ --json
python src/main.py extract --site baidu --subject gzhx \
  --url 'https://tiku.baidu.com/bigque/interface/getqueinfos?action=getquebykp&format=jsonp&kp_id=...&start=1&rn=20&kp_type=knowpoint' --json

# 把 harvest 产出的 items.jsonl 入库（去重；release_id 可回填）
python src/main.py import-items \
  --items data/chemistry/raw/baidu/gzhx/items.jsonl \
  --release-id batch03_baidu --dry-run --json
```

`--max-units` 是 leaf 单元预算（旧名 `--max-papers` 仍可用）；`--max-pages` 只限制 index 分页；`--max-questions` 按 leaf 粒度停止，可能超出最后一份的题量。

`--subject` 接受三种写法：

- 频道代码：`gzhx`（高中化学）、`czhx`（初中化学）、`gzwl`（高中物理）；
- 完整名称：`高中化学`；
- 学科名：`化学`（仅当该站点只有一个学段时可用，否则报错并列出代码）。

站点支持的代码不同：菁优网没有历史/政治频道（对应“道德与法治”），可圈可点有历史/政治；百度目前只登记高中 `gzsx/gzwl/gzhx/gzsw`。`sites --json` 返回每个站点的 `subjects` 列表，是唯一权威清单。

## 输出目录

```text
data/<subject_slug>/raw/<site>/<subject_code>/
├── units/<unit_key>.html     原始单元（百度为 .json API 响应）
├── units/<unit_key>.jsonl    该单元解析出的题目（原子写入）
├── items.jsonl               该学科全部题目（由 units/*.jsonl 重建，自动去重）
├── state.json                该学科已完成 unit 与计数（断点依据）
├── last_run.json             本次运行摘要
└── images/                   --images 时的图片
```

`unit_key` 由站点决定：菁优网/可圈可点用试卷 id，百度用 `kp/<kp_id>/start=<n>`。
例如化学：`data/chemistry/raw/jyeoo/gzhx/`；物理：`data/physics/raw/cooco/gzwl/`。
学科之间完全隔离：两个学科各自维护 `state.json`、`items.jsonl`，互不覆盖。最终题库导出到
`data/<subject_slug>/releases/<release_id>/high_school_<subject>.jsonl`；不要把导出直接写进学科根目录。

`items.jsonl` 每行一道题，关键字段：

```json
{
  "site": "jyeoo",
  "paper_id": "...",
  "paper_title": "...",
  "paper_url": "...",
  "subject": "化学",
  "stage": "高中",
  "source_question_id": "<paper_id>-<number>",
  "question_number": 1,
  "question_type": "选择题",
  "choice_type": "unknown",
  "stem_text": "...",
  "stem_html": "...",
  "options": [{"label": "A", "text": "...", "html": "..."}],
  "answer": null,
  "analysis_text": null,
  "resources": [{"ordinal": 1, "original_url": "...", "placeholder": "[[IMG:1]]", "local_path": null}],
  "content_hash": "...",
  "raw_html_path": "data/chemistry/raw/jyeoo/gzhx/units/<unit_key>.html",
  "collected_at": "..."
}
```

图片位置用 `[[IMG:n]]` 占位，`resources[n-1]` 对应原图。
公式的上下标规范化为 `_{2}` / `^{2-}`（化学式 `H_{2}O`、离子 `SO_{4}^{2-}`），原始标签保留在 `stem_html` / option 的 `html` 里。

答案与解析因站点而异：

- 菁优网试卷页的答案/解析在会员流程后，`answer` 通常为 `null`；需要答案的化学/生物题优先用可圈可点。
- 可圈可点的非选择题（工业流程题、实验探究题等）答案可能是多空拼接的字母串（如 `ADCB`），`choice_type` 会保持 `unknown`；做题库前需要按小问拆分，不能直接当选择题答案用。

## 入库（import-items）

`harvest` 只写 JSONL，不改 SQLite。要把原始题并入题库：

```bash
python src/main.py import-items --items <items.jsonl> --release-id <batch_id> --json
```

- 按 `(source, source_question_id)` 与 `content_hash` 去重，重复执行是幂等的；
- `--release-id` 只回填本次新入库的题，不改历史批次；
- `--dry-run` 只统计；`--revalidate` 强制重跑校验；`--no-check-files` 跳过图片文件存在性检查。

## 百度题库（分页即单元）

百度没有“试卷”实体：章节目录 → 知识点 `kp_id` → `getqueinfos` API 按 `start/rn` 分页（服务端 `rn` 固定 20）。

- 章节目录页是一个 **index unit**，`expand()` 产出每个知识点的第一页；
- 每个 API 分页是一个 **leaf unit**，key 为 `kp/<kp_id>/start=<n>`，`kind="api_page"`，原始响应存 `.json`；
- 抓完一页后用 `que_num` 判断是否还有下一页（每知识点最多 `pages_per_kp=2` 页）；
- 空页/不足一页是正常结果（`allow_empty=True`），不记失败；
- `source` 统一记 `baidu_tiku`，与历史批次一致。

## 学科识别

`harvest` 的 seed 若是学科列表页，会直接从 URL 识别学科；`extract` 的试卷页 URL 不含学科时，按以下顺序识别：

1. URL 中的 `/shijuan/<code>/`、cooco 子域名、百度 `-<course_id>-knowpoint`；
2. 页面 `meta[name=keywords]` / `meta[name=description]`（如“高中化学试卷”）；
3. `<title>`（如“高考化学”“中考物理”）。

显式 `--subject` 与 URL 识别结果冲突时直接报错，不会把化学题标成物理。

## 行为约定

- `--max-units` / `--max-questions` 是**本次运行预算**，不是数据库总量；重复运行会继续抓新单元。
- 已完成单元按 `units/<unit_key>.jsonl` 判断；中断后直接重跑即可。缓存的单元不会重新解析，但仍会 `expand()`，保证分页继续。
- 请求按 `--min-interval`（默认 1.5s）串行限速；遇到 403/429/验证码退出码为 `3`，不自动绕过。
- 每个 URL 先查 `robots.txt`；被禁止的 URL 会跳过并写入 `errors`。
- `max_units=0` / `max_questions=0` 表示不设上限。
- 图片下载保留原格式：`wmf`/`emf` 不再改名为 `.png`，而是以原后缀落盘并标记 `conversion_status=unsupported`，交由下游转码。
- 旧版 `papers/` 布局会在首次运行时自动改名为 `units/`，旧 `state["papers"]` 键平迁到 `state["units"]`。
- 退出码：`0` 成功；`1` 部分失败；`2` 参数错误；`3` 站点限制。

## 评测

抓完一批数据后用离线评测脚本生成 Markdown 报告、JSON 指标和分卷 CSV：

```bash
python src/scripts/evaluate_subject.py --subject gzhx \
  --image-run jyeoo=/tmp/eval_img_jyeoo \
  --image-run cooco=/tmp/eval_img_cooco \
  --pipeline-json data/chemistry/work/evaluation/run_cooco_gzhx.json \
  --pipeline-export data/chemistry/work/evaluation/high_school_chemistry.jsonl
```

输出：`data/chemistry/work/evaluation/<name>_eval.md`、`<name>_eval.json`、`<name>_papers.csv`。
评测报告是临时产物，放 `work/`；交付统计写 release 的 `manifest.json`，不写一次性报告。

## 增加站点

1. 在 `src/k12_crawler/harvest/sites/<name>.py` 新建 `Site` 子类，声明 `name/label/source/subjects`（必要时 `allow_empty`、`raw_suffix`）；
2. 实现 `seed_for(subject) -> str` 与 `seed_unit(seed) -> Unit`；
3. 实现 `parse(unit, raw) -> List[Question]` 与 `expand(unit, raw) -> Iterable[Unit]`；默认 `process` 对 INDEX 页只发现链接、对 LEAF 页解析。若页面处理顺序特殊（例如索引页是 HTML、题目接口是 JSON），覆盖 `process(unit, raw) -> PageResult(questions, children)` 自行组合；
4. 在 `harvest/sites/__init__.py` 导出，并在 `harvest/registry.py` 的 `SITES` 加一行；
5. 用 `python src/main.py extract --site <name> --subject <code> --url <url> --json` 验证解析。

`Unit` 的 `kind` 由站点自定义（`paper`/`list`/`api_page`/`detail`…），runner 只区分 `role=index|leaf`。JSON API 站点声明 `raw_suffix=".json"`，允许空页时声明 `allow_empty=True`。

限速、重试、robots、原始文件、JSONL、断点、学科目录均由 runner 统一处理，站点代码不碰这些。

## 增加学科

在本站点的 `SUBJECTS = (subject("code", "高中", "化学"), ...)` 中增加频道；若出现新的学科名，在 `subjects.py` 的 `SUBJECT_SLUGS` 补充英文 slug（用于 `export` 默认文件名）。站点没有的频道不要登记，`sites` 会按站点分别展示。
