from dataclasses import dataclass, field
import os

from dotenv import load_dotenv

from .schema import IDENTIFIER, PROJECT_ROOT, SeedError


def _integer(name: str, default: int, minimum: int | None = None, maximum: int | None = None) -> int:
    try:
        value = int(os.environ.get(name, str(default)))
    except ValueError:
        raise SeedError(f"{name}에는 정수를 설정해야 합니다.") from None
    if (minimum is not None and value < minimum) or (maximum is not None and value > maximum):
        raise SeedError(f"{name} 값이 허용 범위를 벗어났습니다.")
    return value


@dataclass(frozen=True)
class Settings:
    host: str = "127.0.0.1"
    port: int = 31066
    dbname: str = "text_to_sql"
    user: str = "text_to_sql"
    password: str = field(default="", repr=False)
    schema: str = "public"
    rows: int = 50
    seed: int = 42

    @classmethod
    def from_env(cls) -> "Settings":
        load_dotenv(PROJECT_ROOT / ".env", override=False)
        schema = os.environ.get("DB_SCHEMA", "public")
        if not IDENTIFIER.fullmatch(schema) or schema.startswith("pg_") or schema == "information_schema":
            raise SeedError("DB_SCHEMA에는 일반 PostgreSQL 스키마 식별자를 설정해야 합니다.")
        host = os.environ.get("DB_HOST", "127.0.0.1")
        dbname = os.environ.get("DB_NAME", "text_to_sql")
        user = os.environ.get("DB_USER", "text_to_sql")
        if not all(value.strip() and "\x00" not in value for value in (host, dbname, user)):
            raise SeedError("DB_HOST, DB_NAME, DB_USER는 비어 있을 수 없습니다.")
        return cls(
            host=host, port=_integer("DB_PORT", 31066, 1, 65535),
            dbname=dbname, user=user, password=os.environ.get("DB_PASSWORD", ""),
            schema=schema, rows=_integer("SEED_ROWS", 50, 1), seed=_integer("SEED", 42),
        )

    def connection_kwargs(self) -> dict:
        return dict(host=self.host, port=self.port, dbname=self.dbname, user=self.user,
                    password=self.password, connect_timeout=10, application_name="text_to_sql_seed")
