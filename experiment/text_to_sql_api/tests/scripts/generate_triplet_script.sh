#!/bin/bash
set -euo pipefail

usage() {
  printf 'Usage: %s\n' "$0"
  printf '  POST /retrieve/generate_triplet without a request body\n'
  printf '  BASE_URL    API server URL (default: http://localhost:31065)\n'
  printf '  OUTPUT_DIR  Response directory (default: tests/results relative to the project root)\n'
}

if [[ $# -gt 0 ]]; then
  if [[ $# -eq 1 && ( "$1" == '-h' || "$1" == '--help' ) ]]; then
    usage
    exit 0
  fi
  usage >&2
  exit 1
fi

for executable in curl jq date awk mkdir dirname; do
  if ! command -v "$executable" >/dev/null 2>&1; then
    printf 'Required command not found: %s\n' "$executable" >&2
    exit 1
  fi
done

script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
base_url="${BASE_URL:-http://localhost:31065}"
output_dir="${OUTPUT_DIR:-${script_dir}/../results}"
endpoint="${base_url%/}/retrieve/generate_triplet"
raw_file="${output_dir%/}/generate_triplet_raw_1.json"
response_file="${output_dir%/}/generate_triplet_response_1.json"
triplet_file="${output_dir%/}/generate_triplet_1.json"

mkdir -p -- "$output_dir"
# Clear previous results so a failed request cannot leave old triplets.
: > "$raw_file"
: > "$response_file"
: > "$triplet_file"

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
printf '[1] POST %s\n' "$endpoint"

if metrics=$(curl --silent --show-error \
  --connect-timeout 10 \
  --request POST \
  --header 'Accept: application/json' \
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
  def nonblank:
    if type == "string" then test("[^[:space:]]") else false end;
  def entity:
    if type == "object" then
      (.table_name | nonblank) and (.description | nonblank)
    else false end;
  .data | if type == "array" then
    all(.[];
      if type == "array" then
        length == 3
        and (.[0] | entity)
        and (.[1] | nonblank)
        and (.[2] | entity)
      else false end)
  else false end
' "$response_file" >/dev/null; then
  printf '[1] Triplet 응답 형식 오류: data는 [주체 객체, 관계 문자열, 대상 객체] 목록이어야 하며 테이블명·설명·관계는 공백뿐이지 않은 문자열이어야 합니다. 상세 응답: %s\n' \
    "$response_file" >&2
  exit 1
fi

jq '.data' "$response_file" > "$triplet_file"
printf '[1] Triplet 파일 저장: %s\n' "$triplet_file"
jq -r '
  "[1] Triplet 생성 성공: 관계 \(.data | length)개",
  (.data | to_entries[] |
    "[\(.key + 1)] \(.value[0].table_name) -> \(.value[1]) -> \(.value[2].table_name)")
' "$response_file"
