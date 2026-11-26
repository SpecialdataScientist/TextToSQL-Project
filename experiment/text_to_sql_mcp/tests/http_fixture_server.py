"""A subprocess fixture exercising Streamable HTTP with a fake DB."""

from column_mcp.database import ColumnResult
from column_mcp.server import create_server


class FixtureDatabase:
    async def query_column(self, table, column, options):
        return ColumnResult(
            table=table.name, column=column.name,
            values=[(options.filters or {}).get(column.name, column.name)],
            returned_count=1, limit=options.limit, offset=options.offset,
            distinct=options.distinct, has_more=False,
        )


if __name__ == "__main__":
    create_server(database=FixtureDatabase()).run(transport="streamable-http")
