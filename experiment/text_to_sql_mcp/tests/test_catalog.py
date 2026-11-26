import json
import re

import pytest

from column_mcp.catalog import CatalogError, load_catalog, parse_columns, tool_name
from column_mcp.config import PROJECT_ROOT


def test_all_schema_columns_have_unique_tools_and_korean_descriptions(catalog):
    assert len(catalog) == 97
    assert sum(len(table.columns) for table in catalog) == 1530
    names = [tool_name(table, column) for table in catalog for column in table.columns]
    assert len(set(names)) == 1530
    assert all(re.fullmatch(r"[a-z0-9_]{1,64}", name) for name in names)
    assert all(re.search(r"[가-힣]", column.description) for table in catalog for column in table.columns)
    assert "슈퍼바이저" in next(t for t in catalog if t.name == "SPACE_SUPERVISOR_USAGE").columns[0].description


def test_parser_handles_comments_and_numeric_precision():
    assert parse_columns("EXAMPLE", "-- metadata\nCREATE TABLE EXAMPLE (ID NUMBER(12, 2), LABEL VARCHAR2(50), DAY DATE);") == (
        ("ID", "NUMBER(12, 2)"), ("LABEL", "VARCHAR2(50)"), ("DAY", "DATE"),
    )


@pytest.mark.parametrize("ddl", [
    "CREATE TABLE OTHER (ID NUMBER);",
    "CREATE TABLE EXAMPLE (ID NUMBER, ID VARCHAR2);",
    "CREATE TABLE EXAMPLE ();",
    "CREATE TABLE EXAMPLE (ID NUMBER); DROP TABLE EXAMPLE;",
    "CREATE TABLE EXAMPLE (ID NUMBER DEFAULT 1);",
])
def test_parser_rejects_invalid_or_unsupported_definitions(ddl):
    with pytest.raises(CatalogError):
        parse_columns("EXAMPLE", ddl)


@pytest.mark.parametrize("mutation", ["missing_column", "extra_column", "extra_table", "empty_description", "duplicate_table"])
def test_catalog_fails_on_inconsistent_metadata(tmp_path, mutation):
    source = json.loads((PROJECT_ROOT / "dataset/db_schema.json").read_text())[:1]
    descriptions = {"FCLT_ORG_DLC_KEY": {"FCLT_ORGANIZATION_KEY": "조직 키", "DLC_KEY": "DLC 키"}}
    if mutation == "missing_column":
        del descriptions["FCLT_ORG_DLC_KEY"]["DLC_KEY"]
    elif mutation == "extra_column":
        descriptions["FCLT_ORG_DLC_KEY"]["UNKNOWN"] = "알 수 없는 컬럼"
    elif mutation == "extra_table":
        descriptions["OTHER"] = {}
    elif mutation == "empty_description":
        descriptions["FCLT_ORG_DLC_KEY"]["DLC_KEY"] = " "
    else:
        source.append(source[0])
    schema_path = tmp_path / "schema.json"
    descriptions_path = tmp_path / "descriptions.json"
    schema_path.write_text(json.dumps(source))
    descriptions_path.write_text(json.dumps(descriptions))
    with pytest.raises(CatalogError):
        load_catalog(schema_path, descriptions_path)


def test_duplicate_json_keys_are_rejected(tmp_path):
    descriptions = tmp_path / "descriptions.json"
    descriptions.write_text('{"TABLE": {}, "TABLE": {}}')
    with pytest.raises(CatalogError, match="중복 JSON 키"):
        load_catalog(descriptions_path=descriptions)
