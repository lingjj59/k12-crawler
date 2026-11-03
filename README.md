# k12-crawler

多学科 K12 试题爬虫：把站点适配成统一的 **Site / Unit** 模型，抓取列表、试卷、题目详情或 JSON API 分页，保存原始响应，并输出结构化 JSONL / SQLite。

- 内置站点：百度题库（baidu）、菁优网（jyeoo）、可圈可点（cooco）
- 学科：高中/初中的物理、化学、数学、生物、语文、英语、地理、历史、政治等
- 子命令：`sites`、`harvest`、`extract`、`stats`、`export`、`import-items`、`list`
- 支持断点续跑、限速、去重、状态/台账、字段校验、SQLite 入库
- 纯 Python，Windows / macOS / Linux 均可运行

> 仅用于个人学习与研究。抓取前请阅读目标站点的 robots.txt 与服务条款，保持低频率，不要批量转载或商用。

## Windows 用户：两种方式

### 方式 A：下载源码 + 一键脚本（需要 Python）

1. 在 GitHub 页面点 **Code → Download ZIP**，解压到任意目录（路径尽量不要含中文）。
2. 安装 [Python 3.9+](https://www.python.org/downloads/windows/)，安装时勾选 **Add python.exe to PATH**。
3. 双击 `install.bat`。脚本会创建 `.venv` 并安装依赖，第一次需要联网。
4. 双击或在命令行运行：

```bat
k12-crawler.bat --help
k12-crawler.bat sites --json
```

试抓 5 个单元（输出到 `data\` 目录）：

```bat
k12-crawler.bat harvest --site jyeoo --subject gzhx --max-units 5 --out data\try
```

### 方式 B：下载单文件 exe（不需要 Python）

直接下载最新版：[k12-crawler-windows-x64.zip](https://github.com/lingjj59/k12-crawler/releases/latest/download/k12-crawler-windows-x64.zip)

也可以在 **Releases** 页面手动选择版本。解压后双击 `k12-crawler.exe`，或在命令行使用：

```bat
k12-crawler.exe sites --json
```

单文件版的数据写在 `%LOCALAPPDATA%\k12-crawler\data`，不会污染程序目录。Releases 由 GitHub Actions 在 Windows 上构建；如果还没有 Release，请使用方式 A，或按下文自行构建。

> exe 未做代码签名，Windows 可能提示“未知发布者”。确认文件来自本仓库后，可点“更多信息 → 仍要运行”；介意的话请用方式 A 从源码运行。

## 安装（macOS / Linux / 任意 Python 环境）

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e .          # 开发/源码方式
# 或：python -m pip install .
k12-crawler --help
```

也可以直接安装 GitHub 上的版本：

```bash
python -m pip install "git+https://github.com/lingjj59/k12-crawler.git"
```

## 常用命令

```bash
# 查看站点与学科频道
k12-crawler sites --json
k12-crawler sites --subject gzhx --json

# 批量抓取（保存原始页 + items.jsonl，不写数据库）
k12-crawler harvest --site jyeoo --subject gzhx --max-units 20 --json
k12-crawler harvest --site baidu --subject gzwl --max-units 10 --images --json

# 抓取单个试卷/题目 URL，直接输出结构化 JSON
k12-crawler extract --site jyeoo --subject gzhx --url "<paper_url>" --json

# 入库、统计、导出
k12-crawler import-items --items data/.../items.jsonl --json
k12-crawler stats --json
k12-crawler export --subject gzhx --output data/export/gzhx.jsonl
```

所有命令都支持 `--json`，便于脚本或 Agent 调用；退出码：`0` 成功、`1` 运行失败/部分成功、`2` 参数错误、`3` 被站点拦截（403/429/验证码/WAF）。

完整参数：

```bash
k12-crawler <command> --help
```

## 数据目录与配置

数据根目录按以下顺序确定：

1. 环境变量 `K12_HOME`（推荐自定义位置时使用）
2. **源码目录**（同时存在 `pyproject.toml` 和 `src/k12_crawler`）→ 使用仓库下的 `data/`
3. **安装版 / exe** → 用户目录：
   - Windows：`%LOCALAPPDATA%\k12-crawler`
   - macOS：`~/Library/Application Support/k12-crawler`
   - Linux：`$XDG_DATA_HOME/k12-crawler` 或 `~/.local/share/k12-crawler`

目录结构：

```text
<数据根目录>/
├── config/config.yaml      # 可选；不存在时使用内置默认值
└── data/
    ├── raw/                # 原始 HTML / JSON 响应
    ├── images/             # 题目图片
    ├── shared/             # SQLite 与合规台账
    └── ...                 # --out / --output 指向的位置
```

自定义配置：

```bash
export K12_HOME=/path/to/k12-data
export K12_CONFIG=/path/to/config.yaml   # 也可用 --config 指定
k12-crawler stats --json
```

Windows PowerShell：

```powershell
$env:K12_HOME = "D:\k12-data"
k12-crawler.bat stats --json
```

`config/config.yaml` 可覆盖站点、学科、数据库/图片/导出路径、超时、限速、重试次数等；相对路径按数据根目录解析。常用键见 [`config/config.yaml`](config/config.yaml)。

## 开发

```bash
python -m pip install -e ".[dev]"
python -m pytest -q
```

可选的整理/评估脚本在 `src/scripts/`；其中读取 parquet 的脚本需要额外依赖：

```bash
python -m pip install -e ".[scripts]"
```

构建 wheel：

```bash
python -m pip install build
python -m build
```

构建 Windows 单文件 exe（必须在 Windows 上执行，PyInstaller 不支持交叉编译）：

```bat
python -m pip install . pyinstaller
pyinstaller packaging\k12-crawler.spec --noconfirm
```

产物在 `dist\k12-crawler.exe`。仓库内的 `.github/workflows/release.yml` 会在推送 `v*` 标签时自动完成构建并上传到 Releases。

## 目录结构

```text
src/k12_crawler/
├── cli_main.py        # 命令行实现（k12-crawler 入口）
├── cli.py             # 兼容入口（旧代码 from main import main）
├── config.py          # 配置与数据根目录
├── subjects.py        # 学科目录与路径
├── fetcher.py         # HTTP、限速、重试、拦截检测
├── harvest/           # Site / Unit 抽象、runner、各站点适配器
├── storage.py         # SQLite
├── importer.py        # JSONL 入库
├── validation.py      # 字段校验
├── resources.py       # 图片等资源
└── exporter.py        # 导出
src/main.py            # 兼容入口：python src/main.py ...
src/scripts/           # 可选：批量数据整理/评估脚本（部分测试会导入）
src/tests/             # pytest
config/config.yaml     # 默认配置
packaging/             # PyInstaller 入口与 spec
```

## License

[MIT](LICENSE)
