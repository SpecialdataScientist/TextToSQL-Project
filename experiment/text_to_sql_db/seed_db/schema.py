"""Parse the supplied Oracle-style DDL as metadata, never as executable SQL."""

from dataclasses import dataclass
import json
from pathlib import Path
import re


PROJECT_ROOT = Path(__file__).resolve().parent.parent
IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,62}\Z")
PG_TYPES = {
    "VARCHAR2": "text",
    "NUMBER": "numeric",
    "CHAR": "char(1)",
    "DATE": "timestamp(0) without time zone",
}


class SeedError(ValueError):
    """An actionable configuration, metadata or dataset validation error."""


@dataclass(frozen=True)
class Column:
    name: str
    source_type: str

    @property
    def pg_type(self) -> str:
        return PG_TYPES[self.source_type]


@dataclass(frozen=True)
class Table:
    name: str
    columns: tuple[Column, ...]
    description: str


def _unique_object(pairs: list[tuple[str, object]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise SeedError(f"중복 JSON 키: {key}")
        result[key] = value
    return result


def load_catalog(path: Path | None = None) -> tuple[Table, ...]:
    source_path = path or PROJECT_ROOT / "dataset/db_schema.json"
    try:
        items = json.loads(source_path.read_text(encoding="utf-8"), object_pairs_hook=_unique_object)
    except (OSError, json.JSONDecodeError) as exc:
        raise SeedError(f"스키마 JSON을 읽을 수 없습니다: {source_path.name}") from exc
    if not isinstance(items, list) or not items:
        raise SeedError("스키마는 비어 있지 않은 배열이어야 합니다.")
    tables = []
    seen = set()
    for item in items:
        if not isinstance(item, dict) or not all(
            isinstance(item.get(key), str) and item[key].strip()
            for key in ("table_name", "schema", "description")
        ):
            raise SeedError("각 테이블에는 table_name, schema, description 문자열이 필요합니다.")
        name = item["table_name"]
        if not IDENTIFIER.fullmatch(name) or name.lower() in seen:
            raise SeedError(f"잘못되었거나 중복된 테이블 식별자: {name}")
        seen.add(name.lower())
        ddl = re.sub(r"--[^\n]*", "", item["schema"])
        match = re.fullmatch(r"\s*CREATE\s+TABLE\s+(\w+)\s*\((.*?)\)\s*;\s*", ddl, re.I | re.S)
        if match is None or match[1] != name:
            raise SeedError(f"CREATE TABLE 정의와 테이블명이 일치하지 않습니다: {name}")
        columns = []
        column_names = set()
        for definition in match[2].split(","):
            column = re.fullmatch(r"\s*(\w+)\s+(VARCHAR2|NUMBER|DATE|CHAR)\s*", definition, re.I)
            if column is None or not IDENTIFIER.fullmatch(column[1]):
                raise SeedError(f"지원하지 않는 컬럼 정의: {name}: {definition.strip()}")
            if column[1].lower() in column_names:
                raise SeedError(f"중복 컬럼: {name}.{column[1]}")
            column_names.add(column[1].lower())
            columns.append(Column(column[1], column[2].upper()))
        tables.append(Table(name, tuple(columns), item["description"]))
    return tuple(tables)
