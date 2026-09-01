from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from job_scraper.adapters.processors.codex_resume_analyzer import CodexResumeAnalyzer


def test_codex_resume_analyzer_uses_schema_and_shell_free_subprocess(tmp_path) -> None:
    result = {
        "claims": [{"text": "Built fictional Qt device", "source_ref": "resume:line:1"}],
        "tracks": [{"key": "cpp", "label": "C++", "mode": "core", "keywords": ["C++", "Qt"]}],
    }

    def run(argv, **kwargs):
        output = argv[argv.index("--output-last-message") + 1]
        from pathlib import Path

        Path(output).write_text(json.dumps(result), encoding="utf-8")
        assert kwargs["shell"] is False
        assert "1: Built fictional Qt device" in kwargs["input"]
        return type("Completed", (), {"returncode": 0, "stderr": ""})()

    with (
        patch(
            "job_scraper.adapters.processors.codex_resume_analyzer.tempfile.TemporaryDirectory",
            return_value=type(
                "Temporary",
                (),
                {"__enter__": lambda self: str(tmp_path), "__exit__": lambda *args: None},
            )(),
        ),
        patch(
            "job_scraper.adapters.processors.codex_resume_analyzer.subprocess.run", side_effect=run
        ),
    ):
        assert CodexResumeAnalyzer().analyze("Built fictional Qt device") == result


def test_codex_resume_analyzer_rejects_extra_untrusted_output(tmp_path) -> None:
    def run(argv, **_kwargs):
        from pathlib import Path

        output = argv[argv.index("--output-last-message") + 1]
        Path(output).write_text('{"claims":[],"tracks":[],"command":"ignore"}', encoding="utf-8")
        return type("Completed", (), {"returncode": 0, "stderr": ""})()

    with (
        patch(
            "job_scraper.adapters.processors.codex_resume_analyzer.tempfile.TemporaryDirectory",
            return_value=type(
                "Temporary",
                (),
                {"__enter__": lambda self: str(tmp_path), "__exit__": lambda *args: None},
            )(),
        ),
        patch(
            "job_scraper.adapters.processors.codex_resume_analyzer.subprocess.run", side_effect=run
        ),
        pytest.raises(RuntimeError, match="invalid object"),
    ):
        CodexResumeAnalyzer().analyze("Fictional resume")


def test_codex_resume_analyzer_fails_over_only_for_capacity(tmp_path) -> None:
    result = {
        "claims": [{"text": "Built fictional device", "source_ref": "resume:line:1"}],
        "tracks": [{"key": "cpp", "label": "C++", "mode": "core", "keywords": ["C++"]}],
    }
    attempted_homes: list[str] = []

    def run(argv, **kwargs):
        attempted_homes.append(kwargs["env"]["CODEX_HOME"])
        if len(attempted_homes) == 1:
            return type(
                "Completed", (), {"returncode": 1, "stderr": "You've hit your usage limit"}
            )()
        output = argv[argv.index("--output-last-message") + 1]
        from pathlib import Path

        Path(output).write_text(json.dumps(result), encoding="utf-8")
        return type("Completed", (), {"returncode": 0, "stderr": ""})()

    with (
        patch(
            "job_scraper.adapters.processors.codex_resume_analyzer.tempfile.TemporaryDirectory",
            return_value=type(
                "Temporary",
                (),
                {"__enter__": lambda self: str(tmp_path), "__exit__": lambda *args: None},
            )(),
        ),
        patch(
            "job_scraper.adapters.processors.codex_resume_analyzer.subprocess.run", side_effect=run
        ),
    ):
        analyzer = CodexResumeAnalyzer(codex_homes=(tmp_path / "primary", tmp_path / "secondary"))
        assert analyzer.analyze("Built fictional device") == result
    assert attempted_homes == [str(tmp_path / "primary"), str(tmp_path / "secondary")]
