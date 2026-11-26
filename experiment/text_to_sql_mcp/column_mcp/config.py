"""Environment configuration; credentials are checked only when querying."""

from dataclasses import dataclass, field
from ipaddress import ip_address
import os
from pathlib import Path
import re
from urllib.parse import urlsplit

from dotenv import load_dotenv


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_ALLOWED_HOSTS = (
    "localhost", "localhost:*", "127.0.0.1", "127.0.0.1:*",
    "[::1]", "[::1]:*", "mcp-server", "mcp-server:*",
)
DEFAULT_ALLOWED_ORIGINS = (
    "http://localhost", "http://localhost:*",
    "http://127.0.0.1", "http://127.0.0.1:*",
    "http://[::1]", "http://[::1]:*",
)


class ConfigurationError(ValueError):
    """An actionable configuration error without credential values."""


def _positive_integer(name: str, default: int, *, maximum: int | None = None) -> int:
    value = os.environ.get(name, str(default))
    try:
        number = int(value)
    except ValueError:
        raise ConfigurationError(f"{name}에는 양의 정수를 설정해야 합니다.") from None
    if number < 1 or (maximum is not None and number > maximum):
        raise ConfigurationError(f"{name}의 값이 허용 범위를 벗어났습니다.")
    return number


def _binding_host() -> str:
    host = os.environ.get("MCP_HOST", "127.0.0.1").strip()
    if ":" in host:
        try:
            ip_address(host)
            return host
        except ValueError:
            pass
    elif re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9_.-]*[A-Za-z0-9])?", host):
        return host
    raise ConfigurationError("MCP_HOST에는 URL이나 포트가 아닌 호스트명 또는 IP를 설정해야 합니다.")


def _valid_authority(value: str) -> bool:
    # The SDK supports exact hosts and a wildcard PORT, not a wildcard hostname.
    if not value or re.search(r"[\s/\\?#@]", value):
        return False
    authority = value[:-1] + "1" if value.endswith(":*") else value
    if "*" in authority:
        return False
    try:
        parsed = urlsplit("//" + authority)
        hostname = parsed.hostname
        port = parsed.port
        if not hostname or (port is not None and not 1 <= port <= 65535):
            return False
        if ":" in hostname:
            ip_address(hostname)
            if not re.fullmatch(r"\[[^\]]+\](?::[0-9]+)?", authority):
                return False
        elif not re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9_.-]*[A-Za-z0-9])?", hostname):
            return False
        return not authority.endswith(":")
    except ValueError:
        return False


def _allowed_values(name: str, default: tuple[str, ...], *, origins: bool = False) -> tuple[str, ...]:
    if name not in os.environ:
        return default
    values = tuple(value.strip() for value in os.environ[name].split(","))
    for value in values:
        if origins:
            try:
                parsed = urlsplit(value)
                valid = (
                    parsed.scheme in {"http", "https"}
                    and _valid_authority(parsed.netloc)
                    and value == f"{parsed.scheme}://{parsed.netloc}"
                )
            except ValueError:
                valid = False
        else:
            valid = _valid_authority(value)
        if not valid:
            raise ConfigurationError(f"{name}에 올바른 쉼표 구분 허용 목록을 설정해야 합니다.")
    return values


@dataclass(frozen=True)
class Settings:
    host: str = "localhost"
    port: int = 31066
    dbname: str = ""
    user: str = ""
    password: str = field(default="", repr=False)
    schema: str = "public"
    identifier_case: str = "lower"
    sslmode: str = "prefer"
    connect_timeout: int = 10
    statement_timeout_ms: int = 30000
    mcp_host: str = "127.0.0.1"
    mcp_port: int = 31068
    mcp_allowed_hosts: tuple[str, ...] = DEFAULT_ALLOWED_HOSTS
    mcp_allowed_origins: tuple[str, ...] = DEFAULT_ALLOWED_ORIGINS

    @classmethod
    def from_env(cls, env_path: Path | None = None) -> "Settings":
        load_dotenv(env_path if env_path is not None else PROJECT_ROOT / ".env", override=False)
        case = os.environ.get("DB_IDENTIFIER_CASE", "lower")
        if case not in {"lower", "preserve"}:
            raise ConfigurationError("DB_IDENTIFIER_CASE는 lower 또는 preserve여야 합니다.")
        schema = os.environ.get("DB_SCHEMA", "public")
        if not schema.strip() or "\x00" in schema:
            raise ConfigurationError("DB_SCHEMA에 유효한 스키마 이름을 설정해야 합니다.")
        sslmode = os.environ.get("DB_SSLMODE", "prefer")
        if sslmode not in {"disable", "allow", "prefer", "require", "verify-ca", "verify-full"}:
            raise ConfigurationError("DB_SSLMODE에 유효한 PostgreSQL SSL 모드를 설정해야 합니다.")
        return cls(
            host=os.environ.get("DB_HOST", "localhost"),
            port=_positive_integer("DB_PORT", 31066, maximum=65535),
            dbname=os.environ.get("DB_NAME", ""),
            user=os.environ.get("DB_USER", ""),
            password=os.environ.get("DB_PASSWORD", ""),
            schema=schema,
            identifier_case=case,
            sslmode=sslmode,
            connect_timeout=_positive_integer("DB_CONNECT_TIMEOUT", 10),
            statement_timeout_ms=_positive_integer("DB_STATEMENT_TIMEOUT_MS", 30000),
            mcp_host=_binding_host(),
            mcp_port=_positive_integer("MCP_PORT", 31068, maximum=65535),
            mcp_allowed_hosts=_allowed_values("MCP_ALLOWED_HOSTS", DEFAULT_ALLOWED_HOSTS),
            mcp_allowed_origins=_allowed_values("MCP_ALLOWED_ORIGINS", DEFAULT_ALLOWED_ORIGINS, origins=True),
        )

    def identifier(self, name: str) -> str:
        return name.lower() if self.identifier_case == "lower" else name

    def connection_kwargs(self) -> dict[str, str | int]:
        missing = [name for name, value in (("DB_NAME", self.dbname), ("DB_USER", self.user)) if not value]
        if missing:
            raise ConfigurationError(".env에 DB 접속정보를 설정하세요: " + ", ".join(missing))
        return {
            "host": self.host,
            "port": self.port,
            "dbname": self.dbname,
            "user": self.user,
            "password": self.password,
            "sslmode": self.sslmode,
            "connect_timeout": self.connect_timeout,
            "application_name": "column-query-mcp",
        }
