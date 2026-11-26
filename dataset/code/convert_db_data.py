"""Convert BEAVER table metadata into CREATE TABLE schema strings."""

import json
from pathlib import Path


def build_schema(table: dict) -> str:
    table_name = table["table_name"]
    column_names = table["column_names"]
    column_types = table["column_types"]
    if len(column_names) != len(column_types):
        raise ValueError(
            f"{table_name}: column_names와 column_types의 개수가 다릅니다."
        )

    column_definitions = ",\n".join(
        f"    {name} {data_type}"
        for name, data_type in zip(column_names, column_types)
    )
    return (
        f"-- {table_name}\n"
        f"CREATE TABLE {table_name} (\n"
        f"{column_definitions}\n"
        ");"
    )


def main() -> None:
    dataset_dir = Path(__file__).resolve().parent.parent
    input_path = dataset_dir / "db_set.json"
    output_path = dataset_dir / "converted_db_set.json"

    with input_path.open(encoding="utf-8") as input_file:
        tables = json.load(input_file)
    converted = []
    seen_table_names = set()
    for table in tables:
        table_name = table["table_name"]
        if table_name in seen_table_names:
            continue
        converted.append({"table_name": table_name, "schema": build_schema(table)})
        seen_table_names.add(table_name)

    with output_path.open("w", encoding="utf-8") as output_file:
        json.dump(
            converted, output_file, ensure_ascii=False, indent=2, allow_nan=False
        )

    print(f"Saved {len(converted)} tables to {output_path}")


if __name__ == "__main__":
    main()
