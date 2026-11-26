import pytest

from column_mcp.config import ConfigurationError, DEFAULT_ALLOWED_HOSTS, DEFAULT_ALLOWED_ORIGINS, Settings


@pytest.fixture(autouse=True)
def clean_database_environment(monkeypatch):
    import os
    for name in os.environ:
        if name.startswith(("DB_", "MCP_")):
            monkeypatch.delenv(name)


def test_dotenv_and_environment_precedence(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text("DB_NAME=from_file\nDB_USER=reader\nDB_PASSWORD='a password with spaces'\nDB_PORT=5433\n")
    monkeypatch.setenv("DB_NAME", "from_process")
    settings = Settings.from_env(env_file)
    assert settings.dbname == "from_process"
    assert settings.password == "a password with spaces"
    assert settings.port == 5433
    assert settings.user == "reader"
    assert settings.identifier("MIT_ID") == "mit_id"
    assert settings.connection_kwargs()["password"] == "a password with spaces"


def test_discovery_does_not_require_credentials(tmp_path):
    settings = Settings.from_env(tmp_path / "absent.env")
    assert settings.schema == "public"
    assert settings.connect_timeout == 10
    assert settings.statement_timeout_ms == 30000
    assert settings.mcp_host == "127.0.0.1"
    assert settings.mcp_port == 31068
    assert settings.port == 31066
    assert settings.mcp_allowed_hosts == DEFAULT_ALLOWED_HOSTS
    assert settings.mcp_allowed_origins == DEFAULT_ALLOWED_ORIGINS
    with pytest.raises(ConfigurationError, match="DB_NAME, DB_USER"):
        settings.connection_kwargs()


@pytest.mark.parametrize("name,value", [
    ("DB_PORT", "0"), ("DB_PORT", "65536"), ("DB_PORT", "secret-value"),
    ("DB_CONNECT_TIMEOUT", "-1"), ("DB_STATEMENT_TIMEOUT_MS", "0"),
    ("DB_IDENTIFIER_CASE", "auto"), ("DB_SCHEMA", ""), ("DB_SSLMODE", "invalid"),
    ("MCP_HOST", ""), ("MCP_HOST", "http://localhost:8000"), ("MCP_HOST", "localhost:8000"),
    ("MCP_PORT", "0"), ("MCP_PORT", "65536"), ("MCP_PORT", "secret-value"),
    ("MCP_ALLOWED_HOSTS", ""), ("MCP_ALLOWED_HOSTS", "*"), ("MCP_ALLOWED_HOSTS", "localhost,,mcp-server"),
    ("MCP_ALLOWED_HOSTS", "https://mcp-server"), ("MCP_ALLOWED_HOSTS", "mcp-server:70000"),
    ("MCP_ALLOWED_HOSTS", "[::1]extra"),
    ("MCP_ALLOWED_ORIGINS", ""), ("MCP_ALLOWED_ORIGINS", "*"),
    ("MCP_ALLOWED_ORIGINS", "http://localhost/path"), ("MCP_ALLOWED_ORIGINS", "http://user@localhost"),
    ("MCP_ALLOWED_ORIGINS", "http://localhost?"), ("MCP_ALLOWED_ORIGINS", "ftp://localhost"),
])
def test_invalid_settings_are_reported_without_values(tmp_path, monkeypatch, name, value):
    monkeypatch.setenv(name, value)
    with pytest.raises(ConfigurationError) as exc:
        Settings.from_env(tmp_path / "absent.env")
    assert name in str(exc.value)
    assert "secret-value" not in str(exc.value)


def test_http_environment_overrides_dotenv_and_parses_allowlists(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text("MCP_HOST=localhost\nMCP_PORT=8001\nMCP_ALLOWED_HOSTS=from-file:*\n")
    monkeypatch.setenv("MCP_HOST", "0.0.0.0")
    monkeypatch.setenv("MCP_PORT", "9000")
    monkeypatch.setenv("MCP_ALLOWED_HOSTS", " localhost:*, mcp-server:9000, [::1]:* ")
    monkeypatch.setenv("MCP_ALLOWED_ORIGINS", " https://llm.internal:*, http://localhost:3000 ")
    settings = Settings.from_env(env_file)
    assert settings.mcp_host == "0.0.0.0"
    assert settings.mcp_port == 9000
    assert settings.mcp_allowed_hosts == ("localhost:*", "mcp-server:9000", "[::1]:*")
    assert settings.mcp_allowed_origins == ("https://llm.internal:*", "http://localhost:3000")


def test_ipv6_binding_host(tmp_path, monkeypatch):
    monkeypatch.setenv("MCP_HOST", "::1")
    assert Settings.from_env(tmp_path / "absent.env").mcp_host == "::1"
