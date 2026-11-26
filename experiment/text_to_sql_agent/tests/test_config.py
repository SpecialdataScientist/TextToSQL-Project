import os

import pytest
from pydantic import ValidationError

from agent_server import config
from agent_server.config import Settings
from agent_server.errors import AgentError
from agent_server.llm import resolve_profile
from agent_server.service import parse_arguments
from agent_server.errors import ToolInputError


@pytest.fixture
def clean_environment(monkeypatch):
    for name in list(os.environ):
        if name.startswith(("AGENT_", "OPENAI_", "OPENROUTER_", "LLM_", "MCP_")):
            monkeypatch.delenv(name)


def test_own_env_precedence_and_no_process_mutation(tmp_path, monkeypatch, clean_environment):
    env = tmp_path / ".env"
    env.write_text("AGENT_PORT=9000\nOPENAI_API_KEY=test-file-secret\nLLM_MODEL_NAME=file-model\n")
    monkeypatch.setenv("AGENT_PORT", "9001")
    before = dict(os.environ)
    settings = Settings.from_env(env)
    assert settings.port == 9001
    assert settings.llm_model_name == "file-model"
    assert settings.openai_api_key == "test-file-secret"
    assert "test-file-secret" not in repr(settings)
    assert dict(os.environ) == before


def test_default_env_path_uses_project_root(tmp_path, monkeypatch, clean_environment):
    root = tmp_path / "agent"
    sibling = tmp_path / "text_to_sql_api"
    root.mkdir()
    sibling.mkdir()
    (sibling / ".env").write_text("OPENAI_API_KEY=do-not-load\n")
    (root / ".env").write_text("AGENT_PORT=9002\n")
    monkeypatch.setattr(config, "PROJECT_ROOT", root)
    monkeypatch.chdir(tmp_path)
    settings = Settings.from_env()
    assert settings.port == 9002
    assert settings.openai_api_key == ""


@pytest.mark.parametrize("provider,url,model,key", [
    ("local", "https://local.example/v1", "DeepSeek-V4-Flash-0731", "dummy"),
    ("openai", "https://api.openai.com/v1", "gpt-6-luna", "openai-secret"),
    ("openrouter", "https://openrouter.ai/api/v1", "deepseek/deepseek-v4-flash-0731", "router-secret"),
])
def test_provider_profiles(provider, url, model, key):
    settings = Settings(llm_api_base="https://local.example/v1/", openai_api_key=" openai-secret ",
                        openrouter_api_key="router-secret")
    profile = resolve_profile(settings, provider)
    assert (profile.base_url, profile.options["model"], profile.api_key) == (url, model, key)
    assert key not in repr(profile)
    if provider == "local":
        assert profile.options["extra_body"] == {"thinking": {"type": "disabled"}}
    elif provider == "openai":
        assert profile.options["reasoning_effort"] == "none"
        assert "temperature" not in profile.options
    else:
        assert profile.options["extra_body"] == {"reasoning": {"enabled": False}, "provider": {"require_parameters": True}}


def test_prefix_handling():
    settings = Settings(llm_api_base="http://local/v1", llm_model_name="openai/custom",
                        openai_api_key="test", openai_llm_model_name="openai/custom",
                        openrouter_api_key="test", openrouter_llm_model_name="openai/custom")
    assert resolve_profile(settings, "local").options["model"] == "custom"
    assert resolve_profile(settings, "openai").options["model"] == "custom"
    assert resolve_profile(settings, "openrouter").options["model"] == "openai/custom"


@pytest.mark.parametrize("provider,changes", [
    ("local", {}), ("local", {"llm_api_base": "http://local/v1", "llm_model_name": " "}),
    ("openai", {}), ("openai", {"openai_api_key": "test", "openai_llm_model_name": ""}),
    ("openrouter", {}), ("openrouter", {"openrouter_api_key": "test", "openrouter_llm_model_name": ""}),
])
def test_only_selected_provider_requires_configuration(provider, changes):
    with pytest.raises(AgentError) as error:
        resolve_profile(Settings(**changes), provider)
    assert error.value.status_code == 500


@pytest.mark.parametrize("changes", [
    {"port": 0}, {"port": 65536}, {"max_llm_rounds": 0}, {"max_tool_calls": 0},
    {"request_timeout_seconds": 0}, {"llm_timeout_seconds": float("inf")},
    {"mcp_timeout_seconds": float("nan")}, {"mcp_url": "file:///tmp/mcp"},
    {"mcp_url": "http://secret:password@host/mcp"}, {"mcp_url": "http://host/mcp?key=secret"},
])
def test_invalid_shared_configuration(changes):
    with pytest.raises(ValidationError):
        Settings(**changes)


@pytest.mark.parametrize("raw", ["[]", "null", "{", '{"limit":NaN}', '{"limit":1,"limit":2}'])
def test_invalid_json_tool_arguments(raw):
    with pytest.raises(ToolInputError):
        parse_arguments(raw)
