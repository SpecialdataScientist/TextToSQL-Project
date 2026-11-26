"""Safe, bounded SELECT queries for a single registered column."""

from datetime import date, datetime, time
from decimal import Decimal
import logging
import math
from typing import Annotated

import psycopg
from psycopg import sql
from pydantic import BaseModel, ConfigDict, Field

from .catalog import Column, Table
from .config import ConfigurationError, Settings


logger = logging.getLogger(__name__)
FilterValue = str | int | float | bool | None
ResultValue = str | int | float | bool | None


class QueryError(ValueError):
    """A client-facing query failure without raw driver or connection details."""


class QueryOptions(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    filters: dict[str, FilterValue] | None = None
    limit: Annotated[int, Field(ge=1, le=1000)] = 100
    offset: Annotated[int, Field(ge=0)] = 0
    distinct: bool = False


class ColumnResult(BaseModel):
    table: str
    column: str
    values: list[ResultValue]
    returned_count: int
    limit: int
    offset: int
    distinct: bool
    has_more: bool


def build_query(
    settings: Settings, table: Table, column: Column, options: QueryOptions,
) -> tuple[sql.Composed, list[FilterValue]]:
    if column not in table.columns:
        raise QueryError("등록되지 않은 조회 컬럼입니다.")
    filters = options.filters or {}
    if any(name not in table.column_names for name in filters):
        raise QueryError("filters에는 해당 테이블의 등록된 컬럼명만 사용할 수 있습니다.")
    if any(isinstance(value, float) and not math.isfinite(value) for value in filters.values()):
        raise QueryError("필터에 NaN 또는 무한대를 사용할 수 없습니다.")
    selected = sql.Identifier(settings.identifier(column.name))
    relation = sql.Identifier(settings.schema, settings.identifier(table.name))
    query = sql.SQL("SELECT {}{} FROM {}").format(
        sql.SQL("DISTINCT ") if options.distinct else sql.SQL(""), selected, relation,
    )
    predicates: list[sql.Composable] = []
    params: list[FilterValue] = []
    for name, value in filters.items():
        identifier = sql.Identifier(settings.identifier(name))
        if value is None:
            predicates.append(sql.SQL("{} IS NULL").format(identifier))
        else:
            predicates.append(sql.SQL("{} = %s").format(identifier))
            params.append(value)
    if predicates:
        query += sql.SQL(" WHERE ") + sql.SQL(" AND ").join(predicates)
    query += sql.SQL(" ORDER BY {} ASC NULLS LAST LIMIT %s OFFSET %s").format(selected)
    params.extend((options.limit + 1, options.offset))
    return query, params


def serialize_value(value: object) -> ResultValue:
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, (datetime, date, time)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, float):
        return value if math.isfinite(value) else str(value)
    raise QueryError("조회 결과에 지원하지 않는 DB 자료형이 포함되어 있습니다.")


class Database:
    def __init__(self, settings: Settings):
        self.settings = settings

    async def query_column(
        self, table: Table, column: Column, options: QueryOptions,
    ) -> ColumnResult:
        query, params = build_query(self.settings, table, column, options)
        try:
            connection_kwargs = self.settings.connection_kwargs()
        except ConfigurationError as exc:
            raise QueryError(str(exc)) from None
        try:
            async with await psycopg.AsyncConnection.connect(**connection_kwargs) as connection:
                await connection.set_read_only(True)
                async with connection.cursor() as cursor:
                    await cursor.execute(
                        "SELECT set_config('statement_timeout', %s, true)",
                        (str(self.settings.statement_timeout_ms),),
                    )
                    await cursor.execute(query, params)
                    rows = await cursor.fetchall()
        except psycopg.Error as exc:
            # Driver messages can contain connection strings, SQL or filter values.
            logger.warning("DB query failed: type=%s sqlstate=%s", type(exc).__name__, exc.sqlstate)
            if isinstance(exc, psycopg.errors.QueryCanceled):
                message = "DB 조회 제한 시간을 초과했습니다. 필터나 조회 범위를 줄여주세요."
            elif isinstance(exc, (psycopg.errors.UndefinedTable, psycopg.errors.UndefinedColumn)):
                message = "DB의 테이블·컬럼과 메타데이터가 다릅니다. DB_SCHEMA와 DB_IDENTIFIER_CASE를 확인하세요."
            elif isinstance(exc, psycopg.errors.InsufficientPrivilege):
                message = "해당 테이블을 조회할 DB 권한이 없습니다."
            elif isinstance(exc, (psycopg.DataError, psycopg.ProgrammingError)):
                message = "필터 값과 DB 컬럼의 자료형 또는 조회 설정을 확인하세요."
            elif isinstance(exc, psycopg.OperationalError):
                message = "DB에 연결할 수 없거나 연결이 끊겼습니다. .env의 접속정보를 확인하세요."
            else:
                message = "DB 조회에 실패했습니다. 서버 로그의 오류 유형을 확인하세요."
            raise QueryError(message) from None
        values = [serialize_value(row[0]) for row in rows[:options.limit]]
        return ColumnResult(
            table=table.name, column=column.name, values=values,
            returned_count=len(values), limit=options.limit, offset=options.offset,
            distinct=options.distinct, has_more=len(rows) > options.limit,
        )
