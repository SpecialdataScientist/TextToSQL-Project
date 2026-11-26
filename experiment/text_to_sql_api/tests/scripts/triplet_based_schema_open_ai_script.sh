#!/bin/bash
set -euo pipefail

usage() {
  printf 'Usage: %s [request.json]\n' "$0"
  printf '  Default request: ../test_doc/triplet_based_schema.json relative to this script\n'
  printf '  Request: query, history (default: []), max_hop (default: 3), top_k (default: -1)\n'
  printf '  top_k limits seed tables; -1 means unlimited. Expanded results may exceed top_k.\n'
  printf '  BASE_URL    API server URL (default: http://localhost:31065)\n'
  printf '  OUTPUT_DIR  Response directory (default: tests/results relative to the project root)\n'
}

if [[ $# -gt 1 ]]; then
  usage >&2
  exit 1
fi
if [[ $# -eq 1 && ( "$1" == '-h' || "$1" == '--help' ) ]]; then
  usage
  exit 0
fi

for executable in curl jq date awk mkdir dirname; do
  if ! command -v "$executable" >/dev/null 2>&1; then
    printf 'Required command not found: %s\n' "$executable" >&2
    exit 1
  fi
done

script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
input_file="${1:-${script_dir}/../test_doc/triplet_based_schema.json}"
if [[ ! -f "$input_file" ]]; then
  printf 'Input JSON file not found: %s\n' "$input_file" >&2
  exit 1
fi
if ! jq -e -s 'length == 1 and (.[0] | type == "object")' "$input_file" >/dev/null; then
  printf 'Input must contain exactly one JSON request object: %s\n' "$input_file" >&2
  exit 1
fi

base_url="${BASE_URL:-http://localhost:31065}"
output_dir="${OUTPUT_DIR:-${script_dir}/../results}"
endpoint="${base_url%/}/retrieve/triplet_based_schema_open_ai"
return_all_candidates=false
raw_file="${output_dir%/}/triplet_based_schema_open_ai_raw_1.json"
response_file="${output_dir%/}/triplet_based_schema_open_ai_response_1.json"

mkdir -p -- "$output_dir"
: > "$raw_file"
: > "$response_file"

start=$(date +%s.%N)
finish() {
  local exit_status=$?
  local end runtime
  end=$(date +%s.%N)
  runtime=$(awk -v start="$start" -v end="$end" 'BEGIN {printf "%.3f", end - start}')
  printf '전체 요청 처리 시간: %s 초\n' "$runtime"
  exit "$exit_status"
}
trap finish EXIT

printf '[1] 요청 시각: %s\n' "$(date '+%Y-%m-%d %H:%M:%S')"
printf '[1] 요청 파일: %s\n' "$input_file"
printf '[1] POST %s\n' "$endpoint"

if metrics=$(curl --silent --show-error \
  --connect-timeout 10 \
  --request POST \
  --header 'Content-Type: application/json' \
  --header 'Accept: application/json' \
  --data-binary "@$input_file" \
  --output "$raw_file" \
  --write-out '%{http_code} %{time_total}' \
  -- "$endpoint"); then
  read -r http_status request_time <<< "$metrics"
else
  curl_status=$?
  printf '[1] 요청 전송 실패 (curl exit %s). 원문: %s\n' "$curl_status" "$raw_file" >&2
  exit "$curl_status"
fi

printf '[1] HTTP %s, 응답 소요 시간: %ss\n' "$http_status" "$request_time"
if ! jq -e -s '
  if length == 1 and (.[0] | type == "object") then .[0]
  else error("Expected exactly one JSON response object") end
' "$raw_file" > "$response_file"; then
  printf '[1] 응답 JSON 파싱 실패. 원문: %s\n' "$raw_file" >&2
  exit 1
fi

printf '[1] 응답 저장: %s, %s\n' "$raw_file" "$response_file"
if [[ ! "$http_status" =~ ^2[0-9][0-9]$ ]]; then
  printf '[1] HTTP 오류: %s. 상세 응답: %s\n' "$http_status" "$response_file" >&2
  exit 1
fi
if ! jq -e '.status == "success"' "$response_file" >/dev/null; then
  printf '[1] API 오류. 상세 응답: %s\n' "$response_file" >&2
  exit 1
fi
if ! jq -e '
  .data |
  if type == "object" then
    if all((.table_name, .schema, .description); type == "array") then
      all((.table_name[], .schema[], .description[]); type == "string")
      and ((.table_name | length) == (.schema | length))
      and ((.table_name | length) == (.description | length))
      and ((.table_name | unique | length) == (.table_name | length))
    else false end
  else false end
' "$response_file" >/dev/null; then
  printf '[1] 검색 결과 형식 오류: 세 필드는 같은 길이의 문자열 리스트여야 하고 테이블명은 중복될 수 없습니다. 상세 응답: %s\n' \
    "$response_file" >&2
  exit 1
fi

if ! jq -e --slurpfile request "$input_file" --argjson return_all "$return_all_candidates" '
  def integer: if type == "number" then . == floor else false end;
  def count: if integer then . >= 0 else false end;
  ($request[0] | if has("top_k") then .top_k else -1 end) as $top_k |
  ($request[0] | if has("max_hop") then .max_hop else 3 end) as $max_hop |
  if ($top_k | integer) and ($max_hop | integer) then
    if ($top_k == -1 or $top_k > 0) and $max_hop >= -1 then
      if (.meta_data | type) == "array" and (.meta_data | length) > 0 then
        .meta_data[0] as $meta |
        if ($meta | type) == "object" then
          all(($meta.seed_count, $meta.expanded_count, $meta.retrieved_count, $meta.graph_depth); count)
          and ($meta.seed_count <= $meta.expanded_count)
          and ($meta.retrieved_count <= $meta.expanded_count)
          and ($meta.retrieved_count == (.data.table_name | length))
          and ($meta.graph_depth == (if $max_hop == -1 then 10 else $max_hop end))
          and ($top_k == -1 or $meta.seed_count <= $top_k)
          and (if $return_all then $meta.retrieved_count == $meta.expanded_count else true end)
        else false end
      else false end
    else false end
  else false end
' "$response_file" >/dev/null; then
  printf '[1] 검색 메타데이터 오류: 시작점·확장·반환 수 또는 top_k·max_hop 조건이 일치하지 않습니다. 상세 응답: %s\n' \
    "$response_file" >&2
  exit 1
fi

jq -r '
  "[1] 검색 성공: 시작 \(.meta_data[0].seed_count)개, 확장 \(.meta_data[0].expanded_count)개, 반환 \(.data.table_name | length)개 (탐색 상한 \(.meta_data[0].graph_depth)-hop)",
  (.data as $data | range(0; ($data.table_name | length)) as $i |
    "\n[\($i + 1)] table_name: \($data.table_name[$i])\ndescription: \($data.description[$i])\nschema:\n\($data.schema[$i])")
' "$response_file"
