"""Request-scoped Streamable HTTP transport and searchable tool registry."""

import asyncio
from contextlib import asynccontextmanager
from datetime import timedelta
import re

import httpx
from jsonschema import validators
from jsonschema.exceptions import SchemaError
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from mcp.types import Tool

from .config import Settings
from .errors import AgentError, ToolInputError
from .schemas import CallArguments, SearchArguments


_COLUMN_TOOL_DESCRIPTION = re.compile(r"([A-Za-z_][A-Za-z0-9_$]*)\.([A-Za-z_][A-Za-z0-9_$]*) \(")


def _tool_table(tool: Tool) -> str | None:
    # The column server starts each description with its exact table and column names.
    match = _COLUMN_TOOL_DESCRIPTION.match(tool.description or "")
    if not match:
        return None
    table, column = match.groups()
    if tool.name == f"get_{table.lower()}_{column.lower()}":
        return table
    return None


class McpGateway:
    def __init__(self, session: ClientSession, tools: list[Tool], *, table_names: list[str] | None = None):
        self.session = session
        if table_names is not None:
            selected_tables = set(table_names)
            tools = [tool for tool in tools if _tool_table(tool) in selected_tables]
        self.tools = {tool.name: tool for tool in tools}
        if len(self.tools) != len(tools):
            raise AgentError("MCP 도구 목록에 중복된 이름이 있습니다.")
        self.validators = {}
        try:
            for tool in tools:
                validator_type = validators.validator_for(tool.inputSchema)
                validator_type.check_schema(tool.inputSchema)
                self.validators[tool.name] = validator_type(tool.inputSchema)
        except SchemaError:
            raise AgentError("MCP 도구의 입력 스키마가 올바르지 않습니다.") from None

    def search(self, arguments: SearchArguments) -> dict:
        tokens = set(re.findall(r"\w+", arguments.query.casefold()))
        matches = []
        for tool in self.tools.values():
            haystack = f"{tool.name} {tool.description or ''}".casefold()
            score = sum(token in haystack for token in tokens)
            if not tokens or score:
                matches.append((score, tool))
        matches.sort(key=lambda item: (-item[0], item[1].name))
        page = matches[arguments.offset:arguments.offset + arguments.limit]
        next_offset = arguments.offset + len(page)
        has_more = next_offset < len(matches)
        return {
            "tools": [{"name": tool.name, "description": tool.description, "input_schema": tool.inputSchema}
                      for _, tool in page],
            "total_matches": len(matches), "has_more": has_more,
            "next_offset": next_offset if has_more else None,
        }

    async def call(self, arguments: CallArguments) -> tuple[dict, bool]:
        if arguments.name not in self.tools:
            raise ToolInputError("등록되지 않은 MCP 도구입니다. search_mcp_tools로 이름을 확인하세요.")
        try:
            error = next(self.validators[arguments.name].iter_errors(arguments.arguments), None)
        except Exception:
            raise AgentError("MCP 도구의 입력 스키마를 검증할 수 없습니다.") from None
        if error is not None:
            location = ".".join(str(part) for part in error.absolute_path) or "arguments"
            raise ToolInputError(f"MCP 인자가 입력 스키마에 맞지 않습니다 ({location}): {error.message}")
        try:
            result = await self.session.call_tool(arguments.name, arguments.arguments)
        except asyncio.CancelledError:
            raise
        except Exception:
            raise AgentError("MCP 도구 호출에 실패했습니다. 서버 연결과 응답을 확인하세요.") from None
        return result.model_dump(mode="json", by_alias=True, exclude_none=True), bool(result.isError)


@asynccontextmanager
async def connect_mcp(settings: Settings, *, table_names: list[str] | None = None):
    # Keep transport entry, tool calls and teardown in the same async task.
    async with httpx.AsyncClient(
        timeout=httpx.Timeout(settings.mcp_timeout_seconds), follow_redirects=True, trust_env=False,
    ) as client:
        async with streamable_http_client(settings.mcp_url, http_client=client) as (read, write, _):
            async with ClientSession(
                read, write, read_timeout_seconds=timedelta(seconds=settings.mcp_timeout_seconds),
            ) as session:
                try:
                    await session.initialize()
                    tools = []
                    cursor = None
                    seen_cursors = set()
                    while True:
                        listed = await session.list_tools(cursor=cursor)
                        tools.extend(listed.tools)
                        cursor = listed.nextCursor
                        if not cursor:
                            break
                        if cursor in seen_cursors:
                            raise AgentError("MCP 도구 목록의 페이지 커서가 반복됩니다.")
                        seen_cursors.add(cursor)
                except asyncio.CancelledError:
                    raise
                except AgentError:
                    raise
                except Exception:
                    raise AgentError("MCP 초기화 또는 도구 목록 조회에 실패했습니다.") from None
                yield McpGateway(session, tools, table_names=table_names)
