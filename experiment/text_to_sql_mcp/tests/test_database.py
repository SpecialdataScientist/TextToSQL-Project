from datetime import date, datetime, time, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import psycopg
import pytest
from pydantic import ValidationError

from column_mcp.config import Settings
from column_mcp.database import Database, QueryError, QueryOptions, build_query, serialize_value


def test_safe_query_uses_identifiers_and_bound_values(catalog):
    table = catalog[0]
    attack = "x' OR 1=1; DROP TABLE test; --"
    query, params = build_query(
        Settings(schema='odd"schema'), table, table.columns[1],
        QueryOptions(filters={"FCLT_ORGANIZATION_KEY": attack, "DLC_KEY": None}, limit=2, offset=7, distinct=True),
    )
    rendered = query.as_string()
    assert rendered == (
        'SELECT DISTINCT "dlc_key" FROM "odd""schema"."fclt_org_dlc_key" '
        'WHERE "fclt_organization_key" = %s AND "dlc_key" IS NULL '
        'ORDER BY "dlc_key" ASC NULLS LAST LIMIT %s OFFSET %s'
    )
    assert attack not in rendered
    assert params == [attack, 3, 7]


def test_preserved_identifiers_and_empty_filters(catalog):
    table = catalog[0]
    query, params = build_query(Settings(identifier_case="preserve"), table, table.columns[0], QueryOptions(filters={}))
    assert query.as_string().startswith('SELECT "FCLT_ORGANIZATION_KEY" FROM "public"."FCLT_ORG_DLC_KEY"')
    assert "WHERE" not in query.as_string()
    assert params == [101, 0]


@pytest.mark.parametrize("filters", [{"UNKNOWN": 1}, {"DLC_KEY; DROP TABLE t": "x"}, {"dlc_key": "x"}, {"DLC_KEY": float("nan")}])
def test_invalid_filters_are_rejected_before_connecting(catalog, filters):
    table = catalog[0]
    with pytest.raises(QueryError):
        build_query(Settings(), table, table.columns[0], QueryOptions(filters=filters))


@pytest.mark.parametrize("kwargs", [
    {"limit": 0}, {"limit": 1001}, {"limit": True}, {"limit": "10"},
    {"offset": -1}, {"offset": 1.5}, {"distinct": "true"}, {"filters": {"DLC_KEY": []}},
])
def test_query_option_validation(kwargs):
    with pytest.raises(ValidationError):
        QueryOptions(**kwargs)


@pytest.mark.parametrize("value,expected", [
    (None, None), (True, True), (2, 2), (1.25, 1.25), ("text", "text"),
    (Decimal("12345678901234567890.00100"), "12345678901234567890.00100"),
    (date(2026, 11, 6), "2026-11-06"),
    (datetime(2026, 11, 6, 10, 20, tzinfo=timezone.utc), "2026-11-06T10:20:00+00:00"),
    (time(12, 30), "12:30:00"), (float("inf"), "inf"),
])
def test_json_serialization(value, expected):
    result = serialize_value(value)
    assert result == expected
    assert type(result) is type(expected)


def mock_connection(monkeypatch, rows=(), error=None):
    cursor = AsyncMock()
    cursor.__aenter__.return_value = cursor
    cursor.fetchall.return_value = rows
    connection = MagicMock()
    connection.__aenter__ = AsyncMock(return_value=connection)
    connection.__aexit__ = AsyncMock(return_value=False)
    connection.set_read_only = AsyncMock()
    connection.cursor.return_value = cursor
    connect = AsyncMock(return_value=connection, side_effect=error)
    monkeypatch.setattr(psycopg.AsyncConnection, "connect", connect)
    return connect, connection, cursor


@pytest.mark.anyio
@pytest.mark.parametrize("rows,limit,values,has_more", [
    ([("a",), ("a",), (None,)], 3, ["a", "a", None], False),
    ([("a",), ("b",), ("c",)], 2, ["a", "b"], True),
    ([], 100, [], False),
])
async def test_read_only_query_and_page_results(monkeypatch, catalog, rows, limit, values, has_more):
    _, connection, cursor = mock_connection(monkeypatch, rows)
    table = catalog[0]
    result = await Database(Settings(dbname="test", user="reader")).query_column(table, table.columns[1], QueryOptions(limit=limit))
    assert result.values == values
    assert result.has_more is has_more
    assert result.returned_count == len(values)
    assert result.table == table.name
    assert result.column == "DLC_KEY"
    connection.set_read_only.assert_awaited_once_with(True)
    assert cursor.execute.await_count == 2
    assert cursor.execute.await_args_list[0].args == (
        "SELECT set_config('statement_timeout', %s, true)", ("30000",),
    )
    assert cursor.execute.await_args_list[1].args[1] == [limit + 1, 0]
    connection.__aexit__.assert_awaited_once()


@pytest.mark.anyio
@pytest.mark.parametrize("error,message", [
    (psycopg.OperationalError("password=secret"), "DB에 연결할 수 없거나"),
    (psycopg.errors.QueryCanceled("sensitive query"), "제한 시간"),
    (psycopg.errors.UndefinedTable("sensitive table"), "DB_SCHEMA"),
    (psycopg.errors.UndefinedColumn("sensitive column"), "DB_IDENTIFIER_CASE"),
    (psycopg.errors.InsufficientPrivilege("secret account"), "권한"),
    (psycopg.DataError("sensitive filter"), "자료형"),
])
async def test_driver_errors_do_not_expose_secrets(monkeypatch, catalog, caplog, error, message):
    mock_connection(monkeypatch, error=error)
    table = catalog[0]
    with pytest.raises(QueryError, match=message) as exc:
        await Database(Settings(dbname="test", user="reader", password="secret")).query_column(table, table.columns[0], QueryOptions())
    assert "secret" not in str(exc.value) + caplog.text
    assert "sensitive" not in str(exc.value) + caplog.text


@pytest.mark.anyio
async def test_cursor_failure_closes_connection(monkeypatch, catalog):
    _, connection, cursor = mock_connection(monkeypatch)
    cursor.execute.side_effect = psycopg.errors.QueryCanceled("timeout")
    table = catalog[0]
    with pytest.raises(QueryError):
        await Database(Settings(dbname="test", user="reader")).query_column(table, table.columns[0], QueryOptions())
    connection.__aexit__.assert_awaited_once()
