import argparse
import json
import sys

import psycopg

from .config import Settings
from .database import seed_database, verify_database
from .schema import SeedError, load_catalog


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="원본 스키마에 기반한 PostgreSQL 가상 데이터 초기화·검증")
    commands = parser.add_subparsers(dest="command", required=True)
    seed = commands.add_parser("seed", help="빈 DB를 초기화하거나 기존 데이터 검증 후 건너뛰기")
    seed.add_argument("--reset", action="store_true", help="대상 테이블의 데이터를 비우고 재생성")
    commands.add_parser("verify", help="스키마, 행 수, 가상 데이터, 주요 조인 키 검증")
    args = parser.parse_args(argv)
    try:
        settings = Settings.from_env()
        tables = load_catalog()
        with psycopg.connect(**settings.connection_kwargs(), autocommit=True) as connection:
            if args.command == "seed":
                result = seed_database(connection, tables, settings, reset=args.reset)
            else:
                result = verify_database(connection, tables, settings)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except SeedError as exc:
        print(f"오류: {exc}", file=sys.stderr)
    except psycopg.Error as exc:
        print(f"DB 작업에 실패했습니다 (SQLSTATE={exc.sqlstate or 'connection'}). "
              "DB 접속 설정, 권한, 서버 로그를 확인하세요. 실행 중이던 트랜잭션은 롤백됩니다.", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
