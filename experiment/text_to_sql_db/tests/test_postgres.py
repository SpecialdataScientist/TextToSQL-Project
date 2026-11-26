"""Only an explicit test DSN is used; every test owns an isolated schema."""

from dataclasses import replace
import json
import os
from pathlib import Path
import subprocess
import sys
import uuid

import psycopg
from psycopg import sql
import pytest

from seed_db.config import Settings
from seed_db.database import seed_database, verify_database
from seed_db.schema import SeedError, load_catalog


pytestmark = [pytest.mark.integration, pytest.mark.skipif(
    not os.environ.get("TEST_DATABASE_URL"), reason="TEST_DATABASE_URL is not configured",
)]


@pytest.fixture
def db():
    settings = Settings(schema="seed_test_" + uuid.uuid4().hex)
    with psycopg.connect(os.environ["TEST_DATABASE_URL"], autocommit=True) as connection:
        try:
            yield connection, load_catalog(), settings
        finally:
            connection.execute(sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(sql.Identifier(settings.schema)))


@pytest.fixture
def seeded(db):
    connection, tables, settings = db
    assert seed_database(connection, tables, settings)["status"] == "seeded"
    return db


def count_tables(connection, schema):
    return connection.execute("SELECT count(*) FROM information_schema.tables WHERE table_schema=%s", (schema,)).fetchone()[0]


def test_complete_database_and_real_joins(seeded):
    connection, tables, settings = seeded
    report = verify_database(connection, tables, settings)
    assert (report["tables"], report["columns"], report["total_rows"]) == (97, 1530, 4850)
    assert report["relationships_checked"] >= 100
    assert count_tables(connection, settings.schema) == 97
    # Schema is a generated UUID identifier, nevertheless quote it in SQL.
    connection.execute(sql.SQL("SET search_path TO {}").format(sql.Identifier(settings.schema)))
    assert connection.execute("""
        SELECT count(*) FROM subject_summary s
        JOIN academic_terms t USING (term_code)
        JOIN sis_department d USING (department_code)
        JOIN subject_selector x USING (subject_summary_key)
    """).fetchone()[0] == 50
    assert connection.execute("""
        SELECT count(*) FROM fclt_rooms r
        JOIN fclt_floor f USING (fclt_floor_key, fclt_building_key)
        JOIN fclt_building b USING (fclt_building_key)
    """).fetchone()[0] == 50
    assert connection.execute("""
        SELECT count(*) FROM tip_detail d
        JOIN tip_material m USING (tip_material_key, isbn)
        JOIN tip_subject_offered s USING (tip_subject_offered_key, term_code, subject_id)
        JOIN tip_material_status t USING (tip_material_status_key)
    """).fetchone()[0] == 50
    assert connection.execute("""
        SELECT count(*) FROM drupal_employee_directory d
        JOIN employee_directory e USING (mit_id, full_name, email_address)
        JOIN hr_org_unit h USING (hr_org_unit_id)
    """).fetchone()[0] == 50
    groups = connection.execute("SELECT department_code, sum(subject_enrollment_number) FROM subject_summary GROUP BY department_code").fetchall()
    assert len(groups) == 10
    assert sum(row[1] for row in groups) == connection.execute("SELECT sum(subject_enrollment_number) FROM subject_summary").fetchone()[0]


def test_repeat_skip_and_explicit_reset_with_new_settings(seeded):
    connection, tables, settings = seeded
    assert seed_database(connection, tables, settings)["status"] == "skipped"
    new = replace(settings, rows=7, seed=99)
    with pytest.raises(SeedError):
        seed_database(connection, tables, new)
    assert verify_database(connection, tables, settings)["total_rows"] == 4850
    report = seed_database(connection, tables, new, reset=True)
    assert report["status"] == "reset"
    assert report["total_rows"] == 679
    assert verify_database(connection, tables, new)["total_rows"] == 679


def test_partial_data_requires_reset_and_does_not_create_more_tables(db):
    connection, tables, settings = db
    seed_database(connection, tables[:1], settings)
    with pytest.raises(SeedError, match="테이블이 없습니다"):
        seed_database(connection, tables, settings)
    assert count_tables(connection, settings.schema) == 1
    assert seed_database(connection, tables, settings, reset=True)["total_rows"] == 4850


def test_schema_mismatch_is_not_automatically_modified(db):
    connection, tables, settings = db
    connection.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(settings.schema)))
    connection.execute(sql.SQL("CREATE TABLE {} (fclt_organization_key integer, dlc_key text)").format(
        sql.Identifier(settings.schema, "fclt_org_dlc_key")))
    with pytest.raises(SeedError, match="스키마가"):
        seed_database(connection, tables, settings, reset=True)
    assert count_tables(connection, settings.schema) == 1


def test_insert_failure_rolls_back_created_tables(db):
    connection, tables, settings = db
    connection.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(settings.schema)))
    connection.execute(sql.SQL("CREATE TABLE {} (fclt_organization_key text, dlc_key text, CHECK (false))").format(
        sql.Identifier(settings.schema, "fclt_org_dlc_key")))
    with pytest.raises(psycopg.errors.CheckViolation):
        seed_database(connection, tables, settings)
    assert count_tables(connection, settings.schema) == 1
    assert connection.execute(sql.SQL("SELECT count(*) FROM {}").format(
        sql.Identifier(settings.schema, "fclt_org_dlc_key"))).fetchone()[0] == 0


def test_reset_failure_restores_original_data_and_unrelated_table(seeded):
    connection, tables, settings = seeded
    connection.execute(sql.SQL("CREATE TABLE {} (value text)").format(sql.Identifier(settings.schema, "unrelated")))
    connection.execute(sql.SQL("INSERT INTO {} VALUES ('keep')").format(sql.Identifier(settings.schema, "unrelated")))
    target = sql.Identifier(settings.schema, "subject_summary")
    connection.execute(sql.SQL("ALTER TABLE {} ADD CONSTRAINT test_failure CHECK (subject_enrollment_number = -1) NOT VALID").format(target))
    with pytest.raises(psycopg.errors.CheckViolation):
        seed_database(connection, tables, replace(settings, seed=99), reset=True)
    assert verify_database(connection, tables, settings)["total_rows"] == 4850
    connection.execute(sql.SQL("ALTER TABLE {} DROP CONSTRAINT test_failure").format(target))
    seed_database(connection, tables, settings, reset=True)
    assert connection.execute(sql.SQL("SELECT value FROM {}").format(sql.Identifier(settings.schema, "unrelated"))).fetchone()[0] == "keep"


def test_data_tampering_detected_and_not_overwritten(seeded):
    connection, tables, settings = seeded
    target = sql.Identifier(settings.schema, "employee_directory")
    connection.execute(sql.SQL("UPDATE {} SET email_address='changed@example.com' WHERE mit_id='900000001'").format(target))
    with pytest.raises(SeedError, match="가상 데이터 불일치"):
        seed_database(connection, tables, settings)
    assert connection.execute(sql.SQL("SELECT email_address FROM {} WHERE mit_id='900000001'").format(target)).fetchone()[0] == "changed@example.com"


def test_cli_verify_and_sanitized_connection_failure(seeded):
    connection, tables, settings = seeded
    env = dict(os.environ, DB_HOST=connection.info.host, DB_PORT=str(connection.info.port),
               DB_NAME=connection.info.dbname, DB_USER=connection.info.user,
               DB_PASSWORD=connection.info.password or "", DB_SCHEMA=settings.schema, SEED_ROWS="50", SEED="42")
    root = Path(__file__).resolve().parent.parent
    result = subprocess.run([sys.executable, "-m", "seed_db", "verify"], env=env, cwd=root,
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["total_rows"] == 4850
    env.update(DB_HOST="127.0.0.1", DB_PORT="1", DB_PASSWORD="never-print-this-secret")
    result = subprocess.run([sys.executable, "-m", "seed_db", "verify"], env=env, cwd=root,
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 1
    assert "never-print-this-secret" not in result.stderr
    assert "DB 작업에 실패" in result.stderr
