"""Configuration loading and project paths."""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional

import yaml

from .subjects import apply_subject_paths, find_subject, known_sites


def _user_data_root() -> Path:
    """Writable per-user directory for a pip-installed / frozen copy."""
    home = Path.home()
    if sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA") or home / "AppData" / "Local")
    elif sys.platform == "darwin":
        base = home / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_DATA_HOME") or home / ".local" / "share")
    return base / "k12-crawler"


def _detect_root() -> Path:
    """K12_HOME > source checkout > per-user data directory.

    A source checkout is a directory that has both ``pyproject.toml`` and
    ``src/k12_crawler``.  Editable installs resolve to the checkout, so the
    ``data/`` and ``config/`` next to the code keep being used.  Wheels and
    PyInstaller bundles resolve to the user directory instead, which is
    writable without administrator rights.
    """
    env = os.environ.get("K12_HOME")
    if env:
        return Path(env).expanduser().resolve()
    if getattr(sys, "frozen", False):
        return _user_data_root()
    source_root = Path(__file__).resolve().parents[2]
    if (source_root / "pyproject.toml").is_file() and (
        source_root / "src" / "k12_crawler"
    ).is_dir():
        return source_root
    return _user_data_root()


PROJECT_ROOT = _detect_root()


def default_config_path() -> Path:
    """Config file used when ``--config`` is omitted."""
    env = os.environ.get("K12_CONFIG")
    if env:
        return Path(env).expanduser().resolve()
    return PROJECT_ROOT / "config" / "config.yaml"


@dataclass
class Config:
    source: str = "cooco"
    base_url: str = "http://gzwl.cooco.net.cn"
    list_url: str = "http://gzwl.cooco.net.cn/exam_list_new/gzwl/"
    subject_code: str = "gzwl"
    subject: str = "物理"
    subject_slug: str = "physics"
    stage: str = "高中"
    question_type_filter: str = "选择题"

    db_path: Path = PROJECT_ROOT / "data" / "shared" / "k12_high_school.sqlite"
    raw_dir: Path = PROJECT_ROOT / "data" / "raw"
    image_dir: Path = PROJECT_ROOT / "data" / "images"
    export_dir: Path = PROJECT_ROOT / "data"
    ledger_path: Path = PROJECT_ROOT / "data" / "shared" / "compliance" / "ledger.jsonl"

    timeout: float = 20.0
    min_interval: float = 2.0
    max_retries: int = 3
    user_agent: str = (
        "K12Crawler/0.6 (multi-subject educational crawler; polite single-process)"
    )

    max_papers: int = 2
    max_questions: int = 30
    download_images: bool = True
    authorized: bool = False
    #: Legacy run/crawl/parse hard caps (2 papers / 30 questions) are off by
    #: default; per-run --max-papers/--max-questions still apply.
    limits_enabled: bool = False
    compliance_note: str = (
        "采集遵守 robots.txt 与限速；站点级硬上限默认关闭，可用 limits_enabled 开启。"
    )

    def ensure_dirs(self) -> None:
        for path in (
            self.db_path.parent,
            self.raw_dir,
            self.image_dir,
            self.export_dir,
            self.ledger_path.parent,
        ):
            path.mkdir(parents=True, exist_ok=True)


def _resolve_path(value: Any, default: Path) -> Path:
    if value is None:
        return default
    path = Path(str(value)).expanduser()
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    return path.resolve()


def load_config(path: Optional[Path] = None, overrides: Optional[Dict[str, Any]] = None) -> Config:
    """Load YAML config, apply overrides, resolve paths, create directories."""
    config_path = Path(path) if path else default_config_path()
    raw: Dict[str, Any] = {}
    if config_path.exists():
        with config_path.open("r", encoding="utf-8") as fh:
            loaded = yaml.safe_load(fh) or {}
            if not isinstance(loaded, dict):
                raise ValueError(f"config must be a mapping: {config_path}")
            raw = loaded
    if overrides:
        raw.update({k: v for k, v in overrides.items() if v is not None})

    cfg = Config()
    for key in (
        "source",
        "base_url",
        "list_url",
        "subject_code",
        "subject",
        "subject_slug",
        "stage",
        "question_type_filter",
        "timeout",
        "min_interval",
        "max_retries",
        "user_agent",
        "max_papers",
        "max_questions",
        "download_images",
        "authorized",
        "limits_enabled",
        "compliance_note",
    ):
        if key in raw and raw[key] is not None:
            setattr(cfg, key, raw[key])

    # Keep subject metadata coherent when a config only sets subject_code.
    # ``source`` may name a database source (baidu_tiku, ceval, ...) rather than
    # a crawler site; only sites have subject registries.
    if cfg.subject_code:
        subject = find_subject(cfg.source, cfg.subject_code) if cfg.source in known_sites() else None
        if subject is None:
            for site in known_sites():
                subject = find_subject(site, cfg.subject_code)
                if subject is not None:
                    break
        if subject is not None:
            if raw.get("subject") is None:
                cfg.subject = subject.name
            if raw.get("stage") is None:
                cfg.stage = subject.stage
            if raw.get("subject_slug") is None:
                cfg.subject_slug = subject.slug

    cfg.db_path = _resolve_path(raw.get("db_path"), cfg.db_path)
    cfg.raw_dir = _resolve_path(raw.get("raw_dir"), cfg.raw_dir)
    cfg.image_dir = _resolve_path(raw.get("image_dir"), cfg.image_dir)
    cfg.export_dir = _resolve_path(raw.get("export_dir"), cfg.export_dir)
    cfg.ledger_path = _resolve_path(raw.get("ledger_path"), cfg.ledger_path)
    apply_subject_paths(cfg)
    cfg.timeout = float(cfg.timeout)
    cfg.min_interval = float(cfg.min_interval)
    cfg.max_retries = int(cfg.max_retries)
    cfg.max_papers = int(cfg.max_papers)
    cfg.max_questions = int(cfg.max_questions)
    cfg.download_images = bool(cfg.download_images)
    cfg.authorized = bool(cfg.authorized)
    cfg.limits_enabled = bool(cfg.limits_enabled)
    cfg.ensure_dirs()
    return cfg
