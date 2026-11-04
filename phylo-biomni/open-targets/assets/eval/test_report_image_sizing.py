#!/usr/bin/env python3
"""Regression tests for proportional report-image sizing."""

from __future__ import annotations

import pathlib
import sys
import tempfile
import unittest

from PIL import Image as PILImage


PACKAGE = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PACKAGE / "scripts"))

import build_report  # noqa: E402


class ProportionalImageTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temporary = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self._temporary.name)

    def tearDown(self) -> None:
        self._temporary.cleanup()

    def _image(self, name: str, size: tuple[int, int]) -> pathlib.Path:
        path = self.root / name
        PILImage.new("RGB", size, "#D4A04A").save(path)
        return path

    def _assert_fitted(self, size: tuple[int, int], max_width: float, max_height: float) -> None:
        image = build_report.proportional_image(
            self._image(f"{size[0]}x{size[1]}.png", size),
            max_width=max_width,
            max_height=max_height,
        )
        self.assertLessEqual(image.drawWidth, max_width)
        self.assertLessEqual(image.drawHeight, max_height)
        self.assertAlmostEqual(
            image.drawWidth / image.drawHeight,
            size[0] / size[1],
            places=10,
        )
        self.assertTrue(
            abs(image.drawWidth - max_width) < 1e-9
            or abs(image.drawHeight - max_height) < 1e-9
        )

    def test_wide_image_preserves_ratio(self) -> None:
        self._assert_fitted((1600, 600), max_width=432, max_height=306)

    def test_tall_image_preserves_ratio(self) -> None:
        self._assert_fitted((600, 1600), max_width=432, max_height=306)

    def test_open_targets_chart_ratios_preserved(self) -> None:
        self._assert_fitted((1050, 855), max_width=432, max_height=306)
        self._assert_fitted((1033, 906), max_width=432, max_height=306)

    def test_invalid_bounds_fail(self) -> None:
        path = self._image("valid.png", (100, 100))
        with self.assertRaisesRegex(ValueError, "positive"):
            build_report.proportional_image(path, max_width=0, max_height=100)


if __name__ == "__main__":
    unittest.main()
