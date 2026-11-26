"""Read the supplied DDL as metadata, without executing it."""

from dataclasses import dataclass
import json
from pathlib import Path
import re

from .config import PROJECT_ROOT


class CatalogError(ValueError):
    """The source schema or the column descriptions are inconsistent."""


@dataclass(frozen=True)
class Column:
    name: str
    data_type: str
    description: str


@dataclass(frozen=True)
class Table:
    name: str
    description: str
    columns: tuple[Column, ...]

    @property
    def column_names(self) -> tuple[str, ...]:
        return tuple(column.name for column in self.columns)


_IDENTIFIER = r"[A-Za-z_][A-Za-z0-9_$]*"
_DDL = re.compile(
    rf"\s*CREATE\s+TABLE\s+({_IDENTIFIER})\s*\((.*)\)\s*;\s*",
    re.IGNORECASE | re.DOTALL,
)
_COLUMN = re.compile(
    rf"\s*({_IDENTIFIER})\s+((?:VARCHAR2|NUMBER|DATE|CHAR)(?:\(\s*\d+(?:\s*,\s*\d+)?\s*\))?)\s*",
    re.IGNORECASE,
)


def _unique_object(pairs: list[tuple[str, object]]) -> dict:
    result: dict = {}
    for key, value in pairs:
        if key in result:
            raise CatalogError(f"중복 JSON 키: {key}")
        result[key] = value
    return result


def _read_json(path: Path) -> object:
    try:
        return json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=_unique_object)
    except (OSError, json.JSONDecodeError) as exc:
        raise CatalogError(f"메타데이터 파일을 읽을 수 없습니다: {path.name}") from exc


def parse_columns(table_name: str, ddl: str) -> tuple[tuple[str, str], ...]:
    # The input is an Oracle-style schema description, not PostgreSQL executable SQL.
    without_comments = re.sub(r"--[^\n]*", "", ddl)
    match = _DDL.fullmatch(without_comments)
    if match is None or match[1] != table_name:
        raise CatalogError(f"CREATE TABLE 정의가 올바르지 않습니다: {table_name}")
    definitions = re.split(r",(?![^()]*\))", match[2])
    columns: list[tuple[str, str]] = []
    for definition in definitions:
        column = _COLUMN.fullmatch(definition)
        if column is None:
            raise CatalogError(f"지원하지 않는 컬럼 정의: {table_name}: {definition.strip()}")
        columns.append((column[1], column[2].upper()))
    if not columns or len({name for name, _ in columns}) != len(columns):
        raise CatalogError(f"비어 있거나 중복된 컬럼 정의: {table_name}")
    return tuple(columns)


def tool_name(table: Table, column: Column) -> str:
    return f"get_{table.name.lower()}_{column.name.lower()}"


def load_catalog(
    schema_path: Path | None = None,
    descriptions_path: Path | None = None,
) -> tuple[Table, ...]:
    source = _read_json(schema_path or PROJECT_ROOT / "dataset/db_schema.json")
    descriptions = _read_json(descriptions_path or PROJECT_ROOT / "dataset/column_descriptions.json")
    if not isinstance(source, list) or not source or not isinstance(descriptions, dict):
        raise CatalogError("스키마는 비어 있지 않은 배열, 컬럼 설명은 객체여야 합니다.")
    tables: list[Table] = []
    seen_tables: set[str] = set()
    seen_tools: set[str] = set()
    for item in source:
        if not isinstance(item, dict) or not all(
            isinstance(item.get(key), str) and item[key].strip()
            for key in ("table_name", "schema", "description")
        ):
            raise CatalogError("각 테이블에는 table_name, schema, description 문자열이 필요합니다.")
        name = item["table_name"]
        if name in seen_tables:
            raise CatalogError(f"중복 테이블: {name}")
        seen_tables.add(name)
        parsed = parse_columns(name, item["schema"])
        metadata = descriptions.get(name)
        if not isinstance(metadata, dict) or set(metadata) != {col for col, _ in parsed}:
            raise CatalogError(f"컬럼 설명과 스키마가 일치하지 않습니다: {name}")
        if not all(isinstance(value, str) and value.strip() for value in metadata.values()):
            raise CatalogError(f"비어 있거나 잘못된 컬럼 설명: {name}")
        table = Table(name, item["description"], tuple(
            Column(col, data_type, metadata[col]) for col, data_type in parsed
        ))
        for column in table.columns:
            name_for_tool = tool_name(table, column)
            if len(name_for_tool) > 64 or name_for_tool in seen_tools:
                raise CatalogError(f"중복되거나 64자를 초과하는 도구 이름: {name_for_tool}")
            seen_tools.add(name_for_tool)
        tables.append(table)
    if set(descriptions) != seen_tables:
        raise CatalogError("컬럼 설명과 스키마의 테이블 목록이 일치하지 않습니다.")
    return tuple(tables)
