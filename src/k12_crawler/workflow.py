"""Application services used by main.py.

The CLI only parses arguments and prints results; all crawl/parse/storage
orchestration lives here or in the modules this file calls.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from .config import Config
from .ledger import ComplianceLedger
from .storage import Storage
from .subjects import STAGE_SLUGS


def harvest(
    config: Config,
    site: str = "jyeoo",
    subject: Optional[str] = None,
    seed: Optional[str] = None,
    max_units: int = 20,
    max_pages: int = 5,
    max_questions: int = 0,
    min_interval: float = 1.5,
    out_dir: Optional[Path] = None,
    download_images: bool = False,
    force: bool = False,
) -> Dict[str, Any]:
    """Raw website harvesting; does not touch the SQLite database."""
    from .harvest.runner import run_harvest

    return run_harvest(
        site=site,
        subject=subject,
        seed=seed,
        max_units=max_units,
        max_pages=max_pages,
        max_questions=max_questions,
        min_interval=min_interval,
        out_dir=str(out_dir) if out_dir is not None else None,
        download_images=download_images,
        force=force,
    )


def extract(
    config: Config,
    site: str,
    url: str,
    subject: Optional[str] = None,
    download_images: bool = False,
    min_interval: float = 1.5,
) -> Dict[str, Any]:
    """Fetch and parse one URL; return structured questions as JSON."""
    from .harvest.runner import extract_url

    return extract_url(
        site=site,
        url=url,
        subject=subject,
        download_images=download_images,
        min_interval=min_interval,
    )


def stats(config: Config) -> Dict[str, Any]:
    storage = Storage(config.db_path)
    try:
        data = storage.stats()
        ledger = ComplianceLedger(config.ledger_path, config.source)
        ledger.seed_from_storage(storage)
        data["db_path"] = str(config.db_path)
        data["raw_dir"] = str(
            config.raw_dir / config.source / (config.subject_code or "gzwl")
        )
        data["export_dir"] = str(config.export_dir)
        data["authorized"] = config.authorized
        data["ledger"] = ledger.summary(config.source)
        return data
    finally:
        storage.close()


def export(
    config: Config,
    output: Optional[Path] = None,
    paper_ids: Optional[Sequence[str]] = None,
    source: Optional[str] = None,
    validation_status: Optional[str] = None,
    stage: Optional[str] = None,
    subject: Optional[str] = None,
    limit: Optional[int] = None,
) -> Dict[str, Any]:
    if output:
        path = Path(output)
    else:
        stage_slug = STAGE_SLUGS.get(stage or config.stage, stage or config.stage)
        path = (
            config.export_dir
            / "exports"
            / f"{stage_slug}_{config.subject_slug or 'questions'}.jsonl"
        )
    storage = Storage(config.db_path)
    try:
        count = storage.export_jsonl(
            path,
            source=source or None,
            paper_ids=list(paper_ids) if paper_ids else None,
            validation_status=validation_status or None,
            stage=stage or None,
            subject=subject or None,
            limit=limit,
        )
        return {
            "exported": count,
            "path": str(path),
            "filters": {
                "source": source,
                "paper_ids": list(paper_ids) if paper_ids else [],
                "validation_status": validation_status,
                "stage": stage,
                "subject": subject,
                "limit": limit,
            },
        }
    finally:
        storage.close()


def list_questions(
    config: Config,
    limit: int = 20,
    paper_id: Optional[str] = None,
    source: Optional[str] = None,
    validation_status: Optional[str] = None,
    stage: Optional[str] = None,
    subject: Optional[str] = None,
) -> List[Dict[str, Any]]:
    storage = Storage(config.db_path)
    try:
        return storage.list_questions(
            limit=limit,
            paper_id=paper_id,
            source=source,
            validation_status=validation_status,
            stage=stage,
            subject=subject,
        )
    finally:
        storage.close()


def list_papers(
    config: Config, limit: int = 20, source: Optional[str] = None
) -> List[Dict[str, Any]]:
    storage = Storage(config.db_path)
    try:
        return storage.list_papers(limit=limit, source=source or config.source)
    finally:
        storage.close()


def import_items(
    config: Config,
    items: Path,
    release_id: Optional[str] = None,
    dry_run: bool = False,
    revalidate: bool = False,
    check_files: bool = True,
) -> Dict[str, Any]:
    """Bridge harvested JSONL into SQLite without re-fetching any page."""
    from .importer import import_items as _import_items

    return _import_items(
        config,
        items,
        release_id=release_id,
        dry_run=dry_run,
        revalidate=revalidate,
        check_files=check_files,
    )
