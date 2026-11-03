"""Image/formula resource download, format sniffing and WMF fallback."""

from __future__ import annotations

import hashlib
import io
import shutil
import subprocess
from pathlib import Path
from typing import Optional, Tuple

from PIL import Image

from .config import PROJECT_ROOT, Config
from .fetcher import AccessBlocked, FetchError, Fetcher
from .html_utils import extension_for_format, sniff_image_format
from .models import Question, Resource


def _relative_to_project(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(PROJECT_ROOT))
    except ValueError:
        return str(path.resolve())


class ResourceManager:
    def __init__(self, config: Config, fetcher: Fetcher):
        self.config = config
        self.fetcher = fetcher
        self.shared_dir = config.image_dir / config.source / "_shared"
        self.shared_dir.mkdir(parents=True, exist_ok=True)
        self._cache: dict[str, str] = {}
        self.downloaded = 0
        self.failed = 0

    def materialize(self, question: Question) -> Tuple[int, int]:
        """Download all resources referenced by a question.

        Returns (downloaded_now, failed).  A 403/429 aborts the run because it
        indicates the site has blocked access; ordinary 404/timeout is recorded
        on the resource and the pipeline continues.
        """
        downloaded = 0
        failed = 0
        for resource in question.resources:
            if not resource.original_url:
                resource.conversion_status = "missing_url"
                resource.error = resource.error or "missing image URL"
                failed += 1
                continue
            try:
                if not self.config.download_images:
                    resource.conversion_status = "skipped"
                    continue
                changed, error = self._materialize_one(resource)
                if changed:
                    downloaded += 1
                if error:
                    failed += 1
            except AccessBlocked:
                raise
            except FetchError as exc:
                resource.error = str(exc)
                resource.conversion_status = "download_failed"
                failed += 1
        self.downloaded += downloaded
        self.failed += failed
        return downloaded, failed

    def _materialize_one(self, resource: Resource) -> Tuple[bool, Optional[str]]:
        url = resource.original_url
        cached = self._cache.get(url)
        if cached:
            resource.local_path = cached
            existing = PROJECT_ROOT / cached if not Path(cached).is_absolute() else Path(cached)
            if existing.exists():
                self._fill_format_from_file(resource, existing)
                if resource.format == "png" and (resource.original_extension or "").lower() in (
                    "wmf",
                    "emf",
                ):
                    resource.conversion_status = "renamed_png"
                elif resource.format in ("png", "jpg", "gif", "bmp", "webp", "svg"):
                    resource.conversion_status = "not_needed"
                else:
                    resource.conversion_status = "unsupported"
                resource.error = None
                return False, None

        data = self.fetcher.get_bytes(url)
        fmt = sniff_image_format(data, url)
        extension = extension_for_format(fmt)
        digest = hashlib.sha256(url.encode("utf-8")).hexdigest()[:16]
        destination = self.shared_dir / f"{digest}.{extension}"
        if not destination.exists():
            destination.write_bytes(data)
        resource.local_path = _relative_to_project(destination)
        resource.format = fmt
        resource.error = None
        self._cache[url] = resource.local_path

        if fmt in ("png", "jpg", "gif", "bmp", "webp"):
            resource.conversion_status = (
                "renamed_png"
                if fmt == "png" and (resource.original_extension or "").lower() in ("wmf", "emf")
                else "not_needed"
            )
            self._fill_dimensions(resource, data)
        elif fmt == "svg":
            resource.conversion_status = "not_needed"
        elif fmt in ("wmf", "emf"):
            converted = self._convert_to_png(destination)
            if converted:
                resource.converted_path = _relative_to_project(converted)
                resource.conversion_status = "converted"
                self._fill_dimensions(resource, converted.read_bytes())
            else:
                resource.conversion_status = "unsupported"
                resource.error = (
                    "real WMF/EMF content; no converter available "
                    "(tried sips/magick/soffice)"
                )
        else:
            resource.conversion_status = "unknown_format"
            resource.error = f"unrecognized image format for {url}"
        return True, resource.error

    @staticmethod
    def _fill_dimensions(resource: Resource, data: bytes) -> None:
        try:
            with Image.open(io.BytesIO(data)) as image:
                resource.width, resource.height = image.size
                if not resource.format:
                    resource.format = (image.format or "").lower() or None
        except Exception:
            pass

    def _fill_format_from_file(self, resource: Resource, path: Path) -> None:
        try:
            data = path.read_bytes()[:64]
            resource.format = sniff_image_format(data, str(path))
            self._fill_dimensions(resource, path.read_bytes())
        except Exception:
            pass

    @staticmethod
    def _convert_to_png(source: Path) -> Optional[Path]:
        destination = source.with_suffix(".png")
        if destination.exists():
            return destination
        commands = []
        sips = shutil.which("sips")
        if sips:
            commands.append([sips, "-s", "format", "png", str(source), "--out", str(destination)])
        magick = shutil.which("magick") or shutil.which("convert")
        if magick:
            commands.append([magick, str(source), str(destination)])
        soffice = shutil.which("soffice") or shutil.which("libreoffice")
        if soffice:
            commands.append(
                [soffice, "--headless", "--convert-to", "png", "--outdir", str(source.parent), str(source)]
            )
        for command in commands:
            try:
                subprocess.run(
                    command,
                    check=False,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    timeout=60,
                )
            except Exception:
                continue
            if destination.exists() and destination.stat().st_size > 0:
                return destination
        return None
