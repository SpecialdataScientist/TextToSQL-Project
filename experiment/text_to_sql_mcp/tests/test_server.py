from mcp.server.fastmcp.exceptions import ToolError
import pytest

from column_mcp.config import Settings
from column_mcp.database import ColumnResult
from column_mcp.server import create_server


class RecordingDatabase:
    def __init__(self):
        self.calls = []

    async def query_column(self, table, column, options):
        self.calls.append((table.name, column.name, options))
        return ColumnResult(
            table=table.name, column=column.name, values=[column.name], returned_count=1,
            limit=options.limit, offset=options.offset, distinct=options.distinct, has_more=False,
        )


@pytest.mark.anyio
async def test_registration_schemas_and_correct_column_binding(catalog):
    database = RecordingDatabase()
    server = create_server(Settings(), catalog=catalog, database=database)
    tools = await server.list_tools()
    assert len(tools) == 1530
    for tool in tools:
        assert set(tool.inputSchema["properties"]) == {"filters", "limit", "offset", "distinct"}
        assert tool.inputSchema["properties"]["limit"]["maximum"] == 1000
        assert tool.annotations.readOnlyHint is True
        assert tool.outputSchema["properties"]["values"]["type"] == "array"
        assert "filters 컬럼" in tool.description
    for name in [tools[0].name, tools[1].name, tools[-1].name]:
        await server.call_tool(name, {"filters": None, "limit": 3, "offset": 2, "distinct": True})
    assert [(table, col) for table, col, _ in database.calls] == [
        ("FCLT_ORG_DLC_KEY", "FCLT_ORGANIZATION_KEY"), ("FCLT_ORG_DLC_KEY", "DLC_KEY"),
        ("LIBRARY_RESERVE_MATRL_DETAIL", "WAREHOUSE_LOAD_DATE"),
    ]
    assert all(options.limit == 3 and options.offset == 2 and options.distinct for _, _, options in database.calls)


@pytest.mark.anyio
@pytest.mark.parametrize("arguments", [{"limit": 0}, {"limit": "5"}, {"limit": True}, {"distinct": "false"}, {"offset": -1}])
async def test_mcp_input_validation(catalog, arguments):
    database = RecordingDatabase()
    server = create_server(Settings(), catalog=catalog[:1], database=database)
    with pytest.raises(ToolError):
        await server.call_tool("get_fclt_org_dlc_key_dlc_key", arguments)
    assert database.calls == []


def test_http_configuration_and_transport_security(catalog):
    settings = Settings(
        mcp_host="0.0.0.0", mcp_port=9000,
        mcp_allowed_hosts=("service.internal:9000",),
        mcp_allowed_origins=("https://llm.internal",),
    )
    server = create_server(settings, catalog=catalog[:1], database=RecordingDatabase())
    assert server.settings.host == "0.0.0.0"
    assert server.settings.port == 9000
    assert server.settings.streamable_http_path == "/mcp"
    assert server.settings.transport_security.enable_dns_rebinding_protection
    assert server.settings.transport_security.allowed_hosts == ["service.internal:9000"]
    assert server.settings.transport_security.allowed_origins == ["https://llm.internal"]


def test_entrypoint_runs_streamable_http(monkeypatch):
    from unittest.mock import Mock
    import column_mcp.server as module

    server = Mock()
    monkeypatch.setattr(module, "create_server", Mock(return_value=server))
    module.main()
    server.run.assert_called_once_with(transport="streamable-http")
