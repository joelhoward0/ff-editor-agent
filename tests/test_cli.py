"""CLI exit-code contract: 0 ok, 1 hard-gate failure, 2 missing config/asset."""

from __future__ import annotations

from typer.testing import CliRunner

from prose_forge.cli import app

runner = CliRunner()


def test_init_creates_layout(workspace):
    result = runner.invoke(app, ["init"])
    assert result.exit_code == 0
    for d in ["corpus", "prompts", "manuscript", "data/control", "data/triples", "runs"]:
        assert (workspace / d).is_dir()
    assert (workspace / "prompts/example.md").exists()


def test_missing_chunks_exits_2(workspace):
    result = runner.invoke(app, ["baseline"])
    assert result.exit_code == 2
    assert "forge ingest" in result.output


def test_missing_corpus_exits_2(workspace):
    result = runner.invoke(app, ["ingest"])
    assert result.exit_code == 2


def test_lint_hard_fail_exits_1(workspace, tmp_path):
    bad = tmp_path / "bad.md"
    bad.write_text("A beat passed and the silence stretched.", encoding="utf-8")
    result = runner.invoke(app, ["lint", str(bad)])
    assert result.exit_code == 1
    assert "HARD FAIL" in result.output


def test_lint_clean_exits_0(workspace, tmp_path):
    good = tmp_path / "good.md"
    good.write_text("The tide went out and the mud held its shape.", encoding="utf-8")
    result = runner.invoke(app, ["lint", str(good)])
    assert result.exit_code == 0


def test_lint_missing_file_exits_2(workspace):
    result = runner.invoke(app, ["lint", "does-not-exist.md"])
    assert result.exit_code == 2


def test_run_missing_prompt_exits_2(workspace):
    result = runner.invoke(app, ["run", "prompts/nope.md"])
    assert result.exit_code == 2


def test_status_runs_clean(workspace):
    result = runner.invoke(app, ["status"])
    assert result.exit_code == 0
    assert "models" in result.output
