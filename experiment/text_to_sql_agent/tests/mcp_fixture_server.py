"""A real HTTP MCP server with paginated metadata and deterministic sample data."""

import asyncio
import os

from mcp.server.fastmcp import Context, FastMCP
from mcp.server.fastmcp.exceptions import ToolError
from mcp.types import ListToolsRequest, ListToolsResult
from pydantic import BaseModel, Field
from starlette.responses import JSONResponse


server = FastMCP("Agent Test MCP", host="127.0.0.1", port=int(os.environ["FIXTURE_PORT"]))


class ColumnResult(BaseModel):
    table: str = "FCLT_ORG_DLC_KEY"
    column: str = "DLC_KEY"
    values: list[str | None]
    returned_count: int
    limit: int
    offset: int
    distinct: bool
    has_more: bool
    session_id: str


@server.tool(name="get_fclt_org_dlc_key_dlc_key", description="FCLT_ORG_DLC_KEY.DLC_KEY (VARCHAR2) 시설 조직 DLC 키 조회")
async def query(
    ctx: Context, filters: dict[str, str | None] | None = None,
    limit: int = Field(default=100, ge=1, le=1000),
    offset: int = Field(default=0, ge=0), distinct: bool = False,
) -> ColumnResult:
    value = (filters or {}).get("DLC_KEY", "DLC001")
    if value == "FAIL":
        raise ToolError("샘플 필터 오류: 다른 값을 사용하세요.")
    if value == "SLOW":
        await asyncio.sleep(10)
    request = ctx.request_context.request
    session_id = request.headers.get("mcp-session-id", "") if request else ""
    return ColumnResult(
        values=[value], returned_count=1, limit=limit, offset=offset,
        distinct=distinct, has_more=offset == 0, session_id=session_id,
    )


def sample_tool():
    async def sample(value: str = "sample") -> str:
        return value
    return sample


for index in range(160):
    server.add_tool(sample_tool(), name=f"get_sample_{index:03d}", description=f"샘플 컬럼 {index}")
server.add_tool(sample_tool(), name="get_z_page_last", description="Z_PAGE.LAST (VARCHAR2) 마지막 페이지 도구")
server.add_tool(
    sample_tool(), name="get_fclt_org_dlc_key_extra_dlc_key",
    description="FCLT_ORG_DLC_KEY_EXTRA.DLC_KEY (VARCHAR2) 별도 테이블의 DLC 키 조회",
)


@server._mcp_server.list_tools()
async def paginated_tools(request: ListToolsRequest) -> ListToolsResult:
    tools = sorted(await server.list_tools(), key=lambda tool: tool.name)
    cursor = request.params.cursor if request.params else None
    start = int(cursor or 0)
    end = start + 50
    return ListToolsResult(tools=tools[start:end], nextCursor=str(end) if end < len(tools) else None)


@server.custom_route("/stats", methods=["GET"])
async def stats(_request):
    # MCP retains terminated transports so subsequent requests receive 404.
    transports = server.session_manager._server_instances.values()
    return JSONResponse({"sessions": sum(not transport.is_terminated for transport in transports)})


if __name__ == "__main__":
    server.run(transport="streamable-http")
