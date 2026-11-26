from datetime import datetime
from decimal import Decimal
import json

import pytest

from seed_db.config import Settings
from seed_db.data import Generator
from seed_db.relations import relationships
from seed_db.schema import Column, SeedError, load_catalog


def test_complete_catalog_and_postgres_types():
    tables = load_catalog()
    assert len(tables) == 97
    assert sum(len(t.columns) for t in tables) == 1530
    assert {c.source_type for t in tables for c in t.columns} == {"VARCHAR2", "NUMBER", "DATE", "CHAR"}
    assert Column("VALUE", "DATE").pg_type == "timestamp(0) without time zone"
    assert Column("VALUE", "CHAR").pg_type == "char(1)"


@pytest.mark.parametrize("ddl,name", [
    ("CREATE TABLE OTHER (VALUE NUMBER);", "EXAMPLE"),
    ("CREATE TABLE EXAMPLE (VALUE NUMBER, value DATE);", "EXAMPLE"),
    ("CREATE TABLE EXAMPLE (VALUE JSON);", "EXAMPLE"),
    ("CREATE TABLE EXAMPLE (VALUE NUMBER); DROP TABLE example;", "EXAMPLE"),
    ("CREATE TABLE EXAMPLE ();", "EXAMPLE"),
    ("CREATE TABLE EXAMPLE (VALUE VARCHAR2(100));", "EXAMPLE"),
    ("CREATE TABLE EXAMPLE (VALUE NUMBER);", "example;DROP"),
])
def test_invalid_ddl_is_rejected(tmp_path, ddl, name):
    path = tmp_path / "schema.json"
    path.write_text(json.dumps([dict(table_name=name, schema=ddl, description="sample")]))
    with pytest.raises(SeedError):
        load_catalog(path)


def test_duplicate_json_keys_and_table_names_rejected(tmp_path):
    path = tmp_path / "schema.json"
    path.write_text('[{"table_name":"A","table_name":"B"}]')
    with pytest.raises(SeedError, match="중복 JSON 키"):
        load_catalog(path)
    item = dict(table_name="A", schema="CREATE TABLE A (X DATE);", description="sample")
    path.write_text(json.dumps([item, item]))
    with pytest.raises(SeedError, match="중복된 테이블"):
        load_catalog(path)


@pytest.mark.parametrize("size", [1, 7, 50])
def test_all_columns_have_valid_types_and_all_supported_relations_resolve(size):
    tables = load_catalog()
    data = Generator(size).generate(tables)
    types = dict(VARCHAR2=str, NUMBER=Decimal, DATE=datetime, CHAR=str)
    records = {}
    for table in tables:
        assert len(data[table.name]) == size
        records[table.name] = []
        for row in data[table.name]:
            assert len(row) == len(table.columns)
            records[table.name].append(dict(zip((c.name for c in table.columns), row)))
            for column, value in zip(table.columns, row):
                if value is not None:
                    assert isinstance(value, types[column.source_type]), (table.name, column)
                    if column.source_type == "CHAR":
                        assert len(value) == 1
                    if column.source_type == "NUMBER":
                        assert value.is_finite()
    relations, excluded = relationships(tables)
    assert len(relations) >= 100
    assert len(excluded) == 4
    for relation in relations:
        parent = {tuple(row[c] for c in relation.parent_columns) for row in records[relation.parent]}
        child = {tuple(row[c] for c in relation.child_columns) for row in records[relation.child]}
        assert child <= parent, relation


def test_reproducible_seed_and_domain_invariants():
    tables = load_catalog()
    data = Generator().generate(tables)
    assert data == Generator().generate(tuple(reversed(tables)))
    assert data != Generator(seed=99).generate(tables)
    records = {t.name: [dict(zip((c.name for c in t.columns), row)) for row in data[t.name]] for t in tables}
    assert sum(row["IS_CURRENT_TERM"] == "Y" for row in records["ACADEMIC_TERMS"]) == 1
    for row in records["ACADEMIC_TERMS"]:
        assert row["TERM_START_DATE"] <= row["TERM_END_DATE"]
        assert row["PRE_REGISTRATION_START_DAY"] < row["REGISTRATION_DAY"]
        assert row["FIRST_DAY_OF_CLASSES"] <= row["LAST_DAY_OF_CLASSES"]
    for row in records["TIP_MATERIAL"]:
        assert row["NEW_SHELF_PRICE"] >= row["USED_SHELF_PRICE"] > 0
        assert sum(int(d) * (1 if i % 2 == 0 else 3) for i, d in enumerate(row["ISBN"])) % 10 == 0
    for row in records["SUBJECT_SUMMARY"]:
        assert row["TOTAL_UNITS"] == row["LECTURE_UNITS"] + row["LAB_UNITS"] + row["PREP_UNITS"]
    assert isinstance(records["CIS_COURSE_CATALOG"][0]["DEPARTMENT_NAME"], datetime)
    assert isinstance(records["FAC_BUILDING"][0]["BUILDING_NAME"], Decimal)


@pytest.mark.parametrize("name,value", [("SEED_ROWS", "0"), ("SEED_ROWS", "abc"), ("SEED", "abc"),
                                       ("DB_PORT", "65536"), ("DB_SCHEMA", "public; DROP SCHEMA public"), ("DB_NAME", "")])
def test_invalid_configuration(monkeypatch, name, value):
    monkeypatch.setenv(name, value)
    with pytest.raises(SeedError):
        Settings.from_env()


def test_environment_overrides_dotenv_and_password_not_in_repr(monkeypatch, tmp_path):
    import seed_db.config as config
    (tmp_path / ".env").write_text("SEED_ROWS=3\nDB_PASSWORD=example-secret\nDB_NAME=from_file\n")
    monkeypatch.setattr(config, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(config.os, "environ", {"SEED_ROWS": "7"})
    settings = Settings.from_env()
    assert settings.rows == 7
    assert settings.dbname == "from_file"
    assert "example-secret" not in repr(settings)
