from pathlib import Path

import pytest

from agent_factory.config import ConfigError, load_config, parse_config, resolve_target
from agent_factory.config.loader import apply_env_overrides, missing_env

MINIMAL = {"project": {"id": "shop", "name": "Shop"}}


def test_defaults_fill_everything():
    cfg = parse_config(MINIMAL)
    assert cfg.embeddings.dimensions == 1536
    assert cfg.validation.auto_validate_min_confidence == 0.75
    assert cfg.neo4j.domain.uri_env == "NEO4J_URI"
    assert cfg.neo4j.memory.uri_env == "MVP_NEO4J_URI"
    assert cfg.index.include == ["**"]


def test_yaml_overrides_defaults_and_env_overrides_yaml():
    raw = {**MINIMAL, "embeddings": {"dimensions": 768}}
    assert parse_config(raw).embeddings.dimensions == 768
    cfg = parse_config(raw, env={"AF_EMBEDDINGS__DIMENSIONS": "384", "AF_PROJECT__NAME": "Shop 2"})
    assert cfg.embeddings.dimensions == 384
    assert cfg.project.name == "Shop 2"


def test_env_overrides_ignore_reserved_and_test_vars():
    data = apply_env_overrides(MINIMAL, {"AF_CONFIG": "x", "AF_TEST_NEO4J_URI": "bolt://x", "AF_NOPE": "1"})
    assert data == MINIMAL


def test_unknown_key_is_named():
    with pytest.raises(ConfigError, match="unknown key 'dimensionz'"):
        parse_config({**MINIMAL, "embeddings": {"dimensionz": 3}})


@pytest.mark.parametrize(
    "raw",
    [
        {
            **MINIMAL,
            "neo4j": {"domain": {"uri_env": "NEO4J_URI", "user_env": "U", "password_env": "P", "password": "x"}},
        },
        {**MINIMAL, "llm": {"model": "m", "api_key": "sk-abc"}},
        {**MINIMAL, "token": "abc"},
    ],
)
def test_secret_literals_are_rejected(raw):
    with pytest.raises(ConfigError, match=r"store secrets in \.env"):
        parse_config(raw)


def test_env_names_must_look_like_env_vars():
    raw = {**MINIMAL, "neo4j": {"domain": {"uri_env": "bolt://localhost", "user_env": "U", "password_env": "P"}}}
    with pytest.raises(ConfigError, match="not an environment variable name"):
        parse_config(raw)


def test_bad_project_id_and_agents():
    with pytest.raises(ConfigError, match="lowercase slug"):
        parse_config({"project": {"id": "My Shop", "name": "x"}})
    with pytest.raises(ConfigError, match="unknown agents"):
        parse_config({**MINIMAL, "agents": ["notepad"]})


def test_resolve_target_and_missing_env():
    cfg = parse_config(MINIMAL)
    env = {"NEO4J_URI": "bolt://localhost:7687", "NEO4J_USERNAME": "neo4j", "NEO4J_PASSWORD": "pw"}
    settings = resolve_target(cfg.neo4j.domain, env)
    assert settings is not None and settings.database is None
    assert "pw" not in settings.describe()
    assert resolve_target(cfg.neo4j.memory, env) is None
    assert missing_env(cfg.neo4j.memory, env) == ["MVP_NEO4J_URI", "MVP_NEO4J_USERNAME", "MVP_NEO4J_PASSWORD"]


def test_load_config_missing_file(tmp_path: Path):
    with pytest.raises(ConfigError, match="agent-factory init"):
        load_config(tmp_path, env={}, dotenv=False)


def test_load_config_invalid_yaml(tmp_path: Path):
    (tmp_path / "agent-factory.yaml").write_text("project: [unclosed", encoding="utf-8")
    with pytest.raises(ConfigError, match="not valid YAML"):
        load_config(tmp_path, env={}, dotenv=False)
