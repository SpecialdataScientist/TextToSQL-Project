"""Creates and drops an isolated schema ONLY in TEST_DATABASE_URL."""

from dataclasses import replace
from datetime import date, datetime, timezone
from decimal import Decimal
import os
from uuid import uuid4

import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict
import pytest

from column_mcp.catalog import Column, Table
from column_mcp.config import Settings
from column_mcp.database import Database, QueryError, QueryOptions


pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not os.environ.get("TEST_DATABASE_URL"), reason="TEST_DATABASE_URL is not configured"),
]


@pytest.fixture(scope="module")
def postgres_settings():
    dsn = os.environ["TEST_DATABASE_URL"]
    schema = "column_mcp_test_" + uuid4().hex
    with psycopg.connect(dsn, autocommit=True) as connection:
        settings = Settings(
            host=connection.info.host, port=connection.info.port, dbname=connection.info.dbname,
            user=connection.info.user, password=conninfo_to_dict(dsn).get("password", ""),
            schema=schema, sslmode=connection.info.get_parameters().get("sslmode", "prefer"),
        )
        connection.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
        try:
            for table, org, dlc in [
                ("fclt_org_dlc_key", "fclt_organization_key", "dlc_key"),
                ("FCLT_ORG_DLC_KEY", "FCLT_ORGANIZATION_KEY", "DLC_KEY"),
            ]:
                relation = sql.Identifier(schema, table)
                connection.execute(sql.SQL("CREATE TABLE {} ({} text, {} text)").format(
                    relation, sql.Identifier(org), sql.Identifier(dlc),
                ))
                with connection.cursor() as cursor:
                    cursor.executemany(sql.SQL("INSERT INTO {} VALUES (%s, %s)").format(relation), [
                        ("ORG1", "b"), ("ORG1", "a"), ("ORG1", "a"), ("ORG2", None), ("ORG3", "z"),
                    ])
            connection.execute(sql.SQL("CREATE TABLE {} (money numeric(30,8), event_date date, event_time timestamptz)").format(
                sql.Identifier(schema, "test_types"),
            ))
            connection.execute(sql.SQL("INSERT INTO {} VALUES (%s, %s, %s)").format(sql.Identifier(schema, "test_types")), (
                Decimal("12345678901234567890.12345678"), date(2026, 11, 6), datetime(2026, 11, 6, tzinfo=timezone.utc),
            ))
            connection.execute(sql.SQL("CREATE VIEW {} AS SELECT current_setting('transaction_read_only') AS read_only").format(
                sql.Identifier(schema, "tx_status"),
            ))
            yield settings
        finally:
            connection.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))


@pytest.mark.anyio
@pytest.mark.parametrize("options,expected,has_more", [
    (QueryOptions(), ["a", "a", "b", "z", None], False),
    (QueryOptions(distinct=True), ["a", "b", "z", None], False),
    (QueryOptions(filters={"FCLT_ORGANIZATION_KEY": "ORG1", "DLC_KEY": "b"}), ["b"], False),
    (QueryOptions(filters={"DLC_KEY": None}), [None], False),
    (QueryOptions(filters={"DLC_KEY": "absent"}), [], False),
    (QueryOptions(filters={"DLC_KEY": "' OR 1=1 --"}), [], False),
    (QueryOptions(limit=2), ["a", "a"], True),
    (QueryOptions(limit=2, offset=2), ["b", "z"], True),
    (QueryOptions(limit=2, offset=4), [None], False),
])
async def test_real_column_queries(postgres_settings, catalog, options, expected, has_more):
    table = catalog[0]
    result = await Database(postgres_settings).query_column(table, table.columns[1], options)
    assert result.values == expected
    assert result.has_more is has_more


@pytest.mark.anyio
async def test_real_uppercase_quoted_identifiers(postgres_settings, catalog):
    table = catalog[0]
    db = Database(replace(postgres_settings, identifier_case="preserve"))
    result = await db.query_column(table, table.columns[1], QueryOptions(filters={"FCLT_ORGANIZATION_KEY": "ORG1"}))
    assert result.values == ["a", "a", "b"]


@pytest.mark.anyio
async def test_real_numeric_date_and_timestamp_values(postgres_settings):
    table = Table("TEST_TYPES", "테스트 자료형", (
        Column("MONEY", "NUMBER", "금액"), Column("EVENT_DATE", "DATE", "일자"), Column("EVENT_TIME", "DATE", "시각"),
    ))
    db = Database(postgres_settings)
    results = [await db.query_column(table, column, QueryOptions()) for column in table.columns]
    assert results[0].values == ["12345678901234567890.12345678"]
    assert results[1].values == ["2026-11-06"]
    assert datetime.fromisoformat(results[2].values[0]) == datetime(2026, 11, 6, tzinfo=timezone.utc)


@pytest.mark.anyio
async def test_real_transaction_is_read_only(postgres_settings):
    table = Table("TX_STATUS", "트랜잭션 상태", (Column("READ_ONLY", "VARCHAR2", "읽기 전용 상태"),))
    result = await Database(postgres_settings).query_column(table, table.columns[0], QueryOptions())
    assert result.values == ["on"]


@pytest.mark.anyio
async def test_real_missing_table_error(postgres_settings):
    table = Table("MISSING_TABLE", "없는 테이블", (Column("ID", "NUMBER", "식별자"),))
    with pytest.raises(QueryError, match="DB_SCHEMA"):
        await Database(postgres_settings).query_column(table, table.columns[0], QueryOptions())
