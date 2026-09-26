import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from agent_factory.cli.main import app, normalize_argv
from agent_factory.setup.checks import EXIT_CONFIG, EXIT_INFRA, EXIT_OK, run_doctor
from agent_factory.setup.init_project import InitOptions, run_init
from tests.conftest import git

runner = CliRunner()
ENV = {"NEO4J_URI": "bolt://localhost:1", "NEO4J_USERNAME": "neo4j", "NEO4J_PASSWORD": "super-secret-pw"}


class _BrokenStores:
    """Stands in for GraphStores when the database is down."""

    def __init__(self):
        from agent_factory.config import ConnectionSettings
        from agent_factory.db.stores import Neo4jStore

        settings = ConnectionSettings("bolt://localhost:1", "neo4j", "super-secret-pw")
        self.domain = Neo4jStore(_DeadDriver(), None, "domain", settings)  # type: ignore[arg-type]
        self.memory = None
        self.same_instance = True

    def close(self):
        pass


class _DeadDriver:
    def verify_connectivity(self):
        raise ConnectionError("connection refused")


def test_doctor_without_config_is_a_config_failure(repo: Path):
    report = run_doctor(repo, env={})
    assert report.exit_code == EXIT_CONFIG
    assert next(c for c in report.checks if c.name == "config").status == "fail"


def test_doctor_reports_missing_env(repo: Path):
    run_init(repo, InitOptions())
    report = run_doctor(repo, env={})
    env_check = next(c for c in report.checks if c.name == "env:domain")
    assert env_check.status == "fail" and "NEO4J_URI" in env_check.detail
    assert report.exit_code == EXIT_CONFIG


def test_doctor_reports_unreachable_database_without_leaking_password(repo: Path):
    run_init(repo, InitOptions())
    report = run_doctor(repo, env=ENV, store_factory=lambda cfg: _BrokenStores())  # type: ignore[arg-type,return-value]
    db = next(c for c in report.checks if c.name == "neo4j:domain")
    assert db.status == "fail"
    assert report.exit_code == EXIT_INFRA
    assert "super-secret-pw" not in json.dumps(report.to_dict())


def test_doctor_fails_when_env_file_is_tracked(repo: Path):
    run_init(repo, InitOptions())
    (repo / ".env").write_text("X=1\n", encoding="utf-8")
    git(repo, "add", "-f", ".env")
    report = run_doctor(repo, env={})
    assert next(c for c in report.checks if c.name == ".env").status == "fail"


def test_doctor_mcp_checks_pass_after_init(repo: Path):
    run_init(repo, InitOptions())
    report = run_doctor(repo, env={})
    statuses = {c.name: c.status for c in report.checks if c.name.startswith("mcp:")}
    assert statuses == {"mcp:claude-code": "pass", "mcp:cursor": "pass", "mcp:vscode": "pass"}


@pytest.mark.parametrize(
    "argv,expected",
    [
        (["init", "--json"], ["--json", "init"]),
        (["reuse", "X", "--json", "-v"], ["--json", "-v", "reuse", "X"]),
        (["--json", "status"], ["--json", "status"]),
        (["memory", "propose", "--", "--json"], ["memory", "propose", "--", "--json"]),
    ],
)
def test_normalize_argv(argv, expected):
    assert normalize_argv(argv) == expected


def test_cli_init_json_output(repo: Path):
    result = runner.invoke(app, ["--repo", str(repo), "--json", "init", "--dry-run"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.stdout)
    assert data["dry_run"] is True
    assert {"path", "kind", "detail"} <= set(data["actions"][0])


def test_cli_init_outside_git_exits_2(tmp_path: Path):
    result = runner.invoke(app, ["--repo", str(tmp_path), "init"])
    assert result.exit_code == EXIT_CONFIG


def test_cli_doctor_json_exit_code(repo: Path, monkeypatch):
    for key in ENV:
        monkeypatch.delenv(key, raising=False)
    runner.invoke(app, ["--repo", str(repo), "init"])
    result = runner.invoke(app, ["--repo", str(repo), "--json", "doctor"])
    data = json.loads(result.stdout)
    assert result.exit_code == data["exit_code"] != EXIT_OK
