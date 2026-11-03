"""Tests for the physics batch2 figure/formula split and release counters."""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT / "src" / "scripts"))

import build_physics_batch2 as builder  # noqa: E402
import finalize_physics_batch2 as finalize  # noqa: E402


def _record(stem: str, images: list) -> dict:
    return {
        "stem_text": stem,
        "resources": [
            {"width": width, "height": height} for width, height in images
        ],
    }


def test_pure_text_is_tier_zero():
    assert builder._figure_tier(_record("一物体做匀速直线运动", [])) == 0


def test_formula_fragments_are_tier_one():
    record = _record("已知 $v_0$ 和 $t$，求位移", [(120, 30), (90, 24)])
    assert builder._figure_tier(record) == 1


def test_large_picture_makes_it_a_figure_question():
    assert builder._figure_tier(_record("如图，求合力", [(400, 120)])) is None
    assert builder._figure_tier(_record("求合力", [(160, 90)])) is None
    # width alone is enough: a three-panel figure can be short.
    assert builder._figure_tier(_record("如图，求合力", [(320, 40)])) is None


def test_figure_words_are_last_resort():
    record = _record("如图所示，求电源内阻", [(120, 30)])
    assert builder._figure_tier(record) == 3


def test_missing_size_is_tier_two():
    record = _record("某质点运动", [(None, None)])
    assert builder._figure_tier(record) == 2


def test_no_image_rows_do_not_require_images():
    assert finalize._images_ready(_record("纯文本", []), require_images=False)
    assert not finalize._images_ready(_record("纯文本", []), require_images=True)
