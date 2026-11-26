"""Register one MCP tool per schema column using a shared query implementation."""

import logging
from typing import Annotated

from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import ToolAnnotations
from pydantic import Field

from .catalog import Column, Table, load_catalog, tool_name
from .config import Settings
from .database import ColumnResult, Database, FilterValue, QueryError, QueryOptions


def _column_handler(database: Database, table: Table, column: Column):
    # A factory keeps the bound table and column out of the public input schema.
    async def query(
        filters: Annotated[
            dict[str, FilterValue] | None,
            Field(description="같은 테이블의 컬럼명(원본 대문자 이름)과 일치시킬 값. AND 결합, null은 IS NULL."),
        ] = None,
        limit: Annotated[int, Field(strict=True, ge=1, le=1000, description="반환할 값 개수 (1~1000).")] = 100,
        offset: Annotated[int, Field(strict=True, ge=0, description="건너뛸 값 개수.")] = 0,
        distinct: Annotated[bool, Field(strict=True, description="true이면 중복값을 제거합니다. NULL은 유지합니다.")] = False,
    ) -> ColumnResult:
        try:
            options = QueryOptions(filters=filters, limit=limit, offset=offset, distinct=distinct)
            return await database.query_column(table, column, options)
        except QueryError as exc:
            raise ToolError(str(exc)) from None

    return query


def create_server(
    settings: Settings | None = None,
    *,
    catalog: tuple[Table, ...] | None = None,
    database: Database | None = None,
) -> FastMCP:
    tables = catalog if catalog is not None else load_catalog()
    config = settings if settings is not None else Settings.from_env()
    db = database if database is not None else Database(config)
    server = FastMCP(
        "PostgreSQL Column Query",
        host=config.mcp_host,
        port=config.mcp_port,
        streamable_http_path="/mcp",
        transport_security=TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=list(config.mcp_allowed_hosts),
            allowed_origins=list(config.mcp_allowed_origins),
        ),
        instructions=(
            "테이블의 각 컬럼에 해당하는 도구로 PostgreSQL 데이터를 조회합니다. "
            "filters는 같은 테이블의 컬럼에 대한 일치 조건이며 모든 조건을 AND로 결합합니다. "
            "기본값은 limit=100, offset=0, distinct=false입니다. "
            "값은 오름차순이고 NULL은 마지막입니다. Decimal은 문자열, 날짜는 ISO 8601로 반환합니다."
        ),
    )
    for table in tables:
        allowed_filters = ", ".join(table.column_names)
        for column in table.columns:
            description = (
                f"{table.name}.{column.name} ({column.data_type}) 조회. {column.description}\n"
                "일치 조건으로 조회하며 limit/offset으로 페이지를 선택합니다. "
                "중복과 NULL을 유지하며 distinct=true이면 중복만 제거합니다. "
                "값 오름차순, NULL은 마지막에 반환합니다.\n"
                f"사용 가능한 filters 컬럼: {allowed_filters}"
            )
            server.add_tool(
                _column_handler(db, table, column),
                name=tool_name(table, column),
                description=description,
                annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, openWorldHint=False),
                structured_output=True,
            )
    return server


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    create_server().run(transport="streamable-http")
