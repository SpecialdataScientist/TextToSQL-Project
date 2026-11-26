"""Transactional seed/verify operations; only catalog tables are managed."""

from collections import Counter

import psycopg
from psycopg import sql

from .config import Settings
from .data import Generator
from .relations import relationships
from .schema import SeedError, Table


def _table(schema: str, name: str):
    return sql.Identifier(schema, name.lower())


def _columns(table: Table):
    return sql.SQL(", ").join(sql.Identifier(c.name.lower()) for c in table.columns)


def _state(connection, tables: tuple[Table, ...], schema: str) -> dict[str, int]:
    names = [t.name.lower() for t in tables]
    kinds = dict(connection.execute(
        "SELECT table_name, table_type FROM information_schema.tables "
        "WHERE table_schema = %s AND table_name = ANY(%s)", (schema, names),
    ).fetchall())
    state = {}
    for table in tables:
        name = table.name.lower()
        if name not in kinds:
            continue
        if kinds[name] != "BASE TABLE":
            raise SeedError(f"대상 이름이 일반 테이블이 아닙니다: {schema}.{name}")
        columns = connection.execute(
            "SELECT column_name, data_type, character_maximum_length, datetime_precision, "
            "numeric_precision, is_nullable, column_default, is_identity, is_generated "
            "FROM information_schema.columns WHERE table_schema=%s AND table_name=%s ORDER BY ordinal_position",
            (schema, name),
        ).fetchall()
        expected = []
        for c in table.columns:
            pg_type = {"VARCHAR2": "text", "CHAR": "character", "NUMBER": "numeric", "DATE": "timestamp without time zone"}[c.source_type]
            expected.append((c.name.lower(), pg_type, 1 if c.source_type == "CHAR" else None,
                             0 if c.source_type == "DATE" else None, None, "YES", None, "NO", "NEVER"))
        if columns != expected:
            raise SeedError(f"스키마가 원본의 PostgreSQL 변환 정의와 다릅니다: {schema}.{name}. 자동 변경하지 않습니다.")
        state[table.name] = connection.execute(sql.SQL("SELECT count(*) FROM {}").format(_table(schema, name))).fetchone()[0]
    return state


def _verify(connection, tables: tuple[Table, ...], settings: Settings, data: dict, state: dict) -> dict:
    for table in tables:
        count = state.get(table.name)
        if count is None:
            raise SeedError(f"테이블이 없습니다: {settings.schema}.{table.name.lower()}")
        if count != settings.rows:
            raise SeedError(f"행 수 불일치: {table.name}: 기대 {settings.rows}, 실제 {count}. 재생성하려면 seed --reset을 사용하세요.")
        actual = connection.execute(sql.SQL("SELECT {} FROM {}").format(
            _columns(table), _table(settings.schema, table.name),
        )).fetchall()
        if Counter(actual) != Counter(data[table.name]):
            raise SeedError(f"가상 데이터 불일치: {table.name}. SEED 설정을 확인하거나 seed --reset으로 재생성하세요.")
    relations, excluded = relationships(tables)
    for relation in relations:
        predicates = sql.SQL(" AND ").join(
            sql.SQL("c.{} = p.{}").format(sql.Identifier(c.lower()), sql.Identifier(p.lower()))
            for c, p in zip(relation.child_columns, relation.parent_columns)
        )
        nulls = sql.SQL(" OR ").join(sql.SQL("c.{} IS NULL").format(sql.Identifier(c.lower()))
                                    for c in relation.child_columns)
        query = sql.SQL(
            "SELECT count(*) FROM {} c WHERE ({}) OR NOT EXISTS (SELECT 1 FROM {} p WHERE {})"
        ).format(_table(settings.schema, relation.child), nulls, _table(settings.schema, relation.parent), predicates)
        if connection.execute(query).fetchone()[0]:
            raise SeedError(f"연결되지 않은 키: {relation.child} -> {relation.parent}")
    return dict(status="verified", tables=len(tables), columns=sum(len(t.columns) for t in tables),
                rows_per_table=settings.rows, total_rows=sum(state.values()), seed=settings.seed,
                relationships_checked=len(relations), excluded_relationships=list(excluded))


def verify_database(connection: psycopg.Connection, tables: tuple[Table, ...], settings: Settings) -> dict:
    data = Generator(settings.rows, settings.seed).generate(tables)
    with connection.transaction():
        connection.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
        connection.execute("SET LOCAL statement_timeout = '30s'")
        return _verify(connection, tables, settings, data, _state(connection, tables, settings.schema))


def seed_database(connection: psycopg.Connection, tables: tuple[Table, ...], settings: Settings,
                  *, reset: bool = False) -> dict:
    # Generate and validate metadata before acquiring database locks.
    data = Generator(settings.rows, settings.seed).generate(tables)
    with connection.transaction():
        connection.execute("SET LOCAL lock_timeout = '30s'")
        connection.execute("SET LOCAL statement_timeout = '30s'")
        connection.execute("SELECT pg_advisory_xact_lock(hashtext('text_to_sql_seed'), hashtext(%s))", (settings.schema,))
        connection.execute(sql.SQL("CREATE SCHEMA IF NOT EXISTS {}").format(sql.Identifier(settings.schema)))
        existing = connection.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_schema=%s AND table_name=ANY(%s)",
            (settings.schema, [t.name.lower() for t in tables]),
        ).fetchall()
        if existing:
            connection.execute(sql.SQL("LOCK TABLE {} IN SHARE ROW EXCLUSIVE MODE").format(
                sql.SQL(", ").join(_table(settings.schema, row[0]) for row in sorted(existing)),
            ))
        state = _state(connection, tables, settings.schema)
        if not reset and any(state.values()):
            result = _verify(connection, tables, settings, data, state)
            result["status"] = "skipped"
            return result
        for table in tables:
            if table.name not in state:
                definitions = sql.SQL(", ").join(
                    sql.SQL("{} {}").format(sql.Identifier(c.name.lower()), sql.SQL(c.pg_type)) for c in table.columns
                )
                connection.execute(sql.SQL("CREATE TABLE {} ({})").format(_table(settings.schema, table.name), definitions))
                connection.execute(sql.SQL("COMMENT ON TABLE {} IS {}").format(
                    _table(settings.schema, table.name), sql.Literal(table.description),
                ))
        if reset:
            connection.execute(sql.SQL("TRUNCATE TABLE {}").format(
                sql.SQL(", ").join(_table(settings.schema, t.name) for t in tables),
            ))
        for table in tables:
            query = sql.SQL("INSERT INTO {} ({}) VALUES ({})").format(
                _table(settings.schema, table.name), _columns(table),
                sql.SQL(", ").join(sql.Placeholder() for _ in table.columns),
            )
            with connection.cursor() as cursor:
                cursor.executemany(query, data[table.name])
        result = _verify(connection, tables, settings, data, _state(connection, tables, settings.schema))
        result["status"] = "reset" if reset else "seeded"
        return result
