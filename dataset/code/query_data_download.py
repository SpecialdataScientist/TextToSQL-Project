"""Download BEAVER's dw queries and save them as a JSON array."""

import json
from pathlib import Path

from datasets import load_dataset


# Hugging Face Access Token을 아래 따옴표 안에 입력하세요.
HF_TOKEN = ""


JSON_FIELDS = {
    "tables",
    "join_keys",
    "column_mapping",
    "domain_knowledge",
    "sub_questions",
    "sub_sqls",
}


def main() -> None:
    token = HF_TOKEN.strip()
    if not token:
        raise ValueError(
            "code/query_data_download.py의 HF_TOKEN에 "
            "Hugging Face Access Token을 입력하세요."
        )

    domain = "dw"
    data = load_dataset("beaverbench/beaver-query", split=domain, token=token)
    samples = [
        {
            key: json.loads(value)
            if key in JSON_FIELDS and isinstance(value, str)
            else value
            for key, value in sample.items()
        }
        for sample in data
    ]

    output_path = Path(__file__).resolve().parent.parent / "test_set.json"
    with output_path.open("w", encoding="utf-8") as output_file:
        json.dump(samples, output_file, ensure_ascii=False, indent=2)

    print(f"Saved {len(samples)} records to {output_path}")


if __name__ == "__main__":
    main()
