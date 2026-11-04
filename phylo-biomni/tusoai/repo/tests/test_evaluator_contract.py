from __future__ import annotations

from pathlib import Path

from tusoai.optimization import run_and_evaluate


def test_run_and_evaluate_accepts_plain_decimal_score(tmp_path: Path) -> None:
    runner = tmp_path / "runner.py"
    runner.write_text("print('diagnostic')\nprint('tuso_evaluate: 0.125')\n", encoding="utf-8")

    result = run_and_evaluate(runner, timeout=10, val_limit=2)

    assert isinstance(result, dict)
    assert result["evaluation"] == 0.125


def test_run_and_evaluate_returns_error_string_when_score_missing(tmp_path: Path) -> None:
    runner = tmp_path / "runner.py"
    runner.write_text("print('no score here')\n", encoding="utf-8")

    result = run_and_evaluate(runner, timeout=10, val_limit=2)

    assert isinstance(result, str)
