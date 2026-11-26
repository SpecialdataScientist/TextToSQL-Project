import os
from unittest.mock import patch

import pytest

from app.core import config


@pytest.fixture(autouse=True)
def isolated_environment(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "PROJECT_ROOT", tmp_path)
    with patch.dict(os.environ, {}, clear=True):
        yield tmp_path


def test_port_default_when_no_environment_file_exists():
    assert config.get_default_settings().port == 31065
    assert config.parse_args([]).port == 31065


def test_dotenv_port_and_process_environment_precedence(isolated_environment):
    (isolated_environment / ".env").write_text("API_PORT=32065\n")
    assert config.get_default_settings().port == 32065
    assert config.parse_args([]).port == 32065
    os.environ["API_PORT"] = "33065"
    assert config.get_default_settings().port == 33065
    assert config.parse_args([]).port == 33065


@pytest.mark.parametrize("environment_port", ["32065", "invalid", "0", "65536"])
def test_cli_port_overrides_environment(environment_port, isolated_environment):
    (isolated_environment / ".env").write_text("API_PORT=34065\n")
    os.environ["API_PORT"] = environment_port
    assert config.parse_args(["--port", "35065"]).port == 35065


def test_cli_port_overrides_invalid_dotenv(isolated_environment):
    (isolated_environment / ".env").write_text("API_PORT=invalid\n")
    assert config.parse_args(["--port", "35065"]).port == 35065


@pytest.mark.parametrize("value", ["", "invalid", "0", "-1", "65536", "31065.5"])
def test_invalid_environment_port_is_rejected(value):
    os.environ["API_PORT"] = value
    with pytest.raises(ValueError, match="API_PORT"):
        config.get_default_settings()
    with pytest.raises(SystemExit) as error:
        config.parse_args([])
    assert error.value.code == 2


@pytest.mark.parametrize("value", ["0", "65536", "invalid"])
def test_invalid_cli_port_is_rejected(value):
    with pytest.raises(SystemExit) as error:
        config.parse_args(["--port", value])
    assert error.value.code == 2


@pytest.mark.parametrize("value", ["1", "65535"])
def test_port_boundaries(value):
    os.environ["API_PORT"] = value
    assert config.get_default_settings().port == int(value)
    assert config.parse_args(["--port", value]).port == int(value)
