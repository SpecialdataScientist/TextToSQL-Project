import pytest
from pydantic import ValidationError

from app.core.config import AppSettings, parse_args
from app.schemas.request import (
    HybridOnlyBasedSchemaOpenAiRequest,
    HybridOnlyBasedSchemaRequest,
    OnlyTripletBasedSchemaOpenAiRequest,
    OnlyTripletBasedSchemaOpenRouterRequest,
    OnlyTripletBasedSchemaRequest,
    SchemaTable,
    TripletBasedSchemaOpenAiRequest,
    TripletBasedSchemaOpenRouterRequest,
    TripletBasedSchemaRequest,
)
from app.services.triplet_based_schema_service import TripletBasedSchemaService, resolve_max_hop


REQUEST_MODELS = [
    TripletBasedSchemaOpenRouterRequest,
    OnlyTripletBasedSchemaOpenRouterRequest,
    HybridOnlyBasedSchemaOpenAiRequest,
    HybridOnlyBasedSchemaRequest,
    TripletBasedSchemaRequest, TripletBasedSchemaOpenAiRequest,
    OnlyTripletBasedSchemaRequest, OnlyTripletBasedSchemaOpenAiRequest,
]


def make_tables(names):
    return [SchemaTable.model_validate({
        "table_name": name, "schema": f"CREATE TABLE {name} (id INTEGER);", "description": name,
    }) for name in names]


@pytest.mark.parametrize("request_model", REQUEST_MODELS)
def test_max_hop_defaults_to_three(request_model):
    assert request_model(query="테이블을 찾아줘").max_hop == 3


@pytest.mark.parametrize("request_model", REQUEST_MODELS)
def test_seed_top_k_defaults_to_unlimited(request_model):
    assert request_model(query="테이블을 찾아줘").top_k == -1


@pytest.mark.parametrize("request_model", REQUEST_MODELS)
@pytest.mark.parametrize("top_k", [-1, 1, 3])
def test_seed_top_k_accepts_positive_integers_or_unlimited(request_model, top_k):
    assert request_model(query="테이블을 찾아줘", top_k=top_k).top_k == top_k


@pytest.mark.parametrize("request_model", REQUEST_MODELS)
@pytest.mark.parametrize("top_k", [0, -2, None, "3", 3.0, True])
def test_invalid_seed_top_k_is_rejected(request_model, top_k):
    with pytest.raises(ValidationError, match="top_k"):
        request_model(query="테이블을 찾아줘", top_k=top_k)


@pytest.mark.parametrize("request_model", REQUEST_MODELS)
@pytest.mark.parametrize(("value", "limit"), [(-1, 10), (0, 0), (1, 1), (2, 2), (3, 3), (11, 11)])
def test_max_hop_can_be_passed_in_the_request(request_model, value, limit):
    assert request_model(query="테이블을 찾아줘", max_hop=value).max_hop == value
    assert resolve_max_hop(value) == limit


@pytest.mark.parametrize("request_model", REQUEST_MODELS)
@pytest.mark.parametrize("value", [-2, -10, 1.5, 3.0, True, False, "3", None])
def test_invalid_max_hop_requests_are_rejected(request_model, value):
    with pytest.raises(ValidationError, match="max_hop"):
        request_model(query="테이블을 찾아줘", max_hop=value)


def test_max_hop_is_no_longer_a_server_setting_or_cli_argument(capsys):
    assert not hasattr(AppSettings(), "max_hop")
    with pytest.raises(SystemExit) as error:
        parse_args(["--max_hop", "3"])
    assert error.value.code == 2
    assert "max_hop" in capsys.readouterr().err


@pytest.fixture
def chain():
    # Reverse catalog and edge directions to check ordering and incoming-edge traversal.
    tables = make_tables(f"T{index}" for index in reversed(range(13)))
    relations = [(f"T{index + 1}", "references", f"T{index}") for index in range(12)]
    return tables, relations


@pytest.mark.parametrize(("max_hop", "last_table"), [
    (0, 0), (1, 1), (2, 2), (3, 3), (-1, 10), (11, 11), (20, 12),
])
def test_graph_expansion_respects_depth_and_auto_mode_ten_hop_limit(chain, max_hop, last_table):
    tables, relations = chain
    candidates, candidate_relations = TripletBasedSchemaService._expand_graph(
        ["T0"], tables, relations, max_hop,
    )
    assert [table.table_name for table in candidates] == [
        f"T{index}" for index in reversed(range(last_table + 1))
    ]
    assert candidate_relations == relations[:last_table]


def test_expansion_uses_all_seeds_without_duplicate_candidates(chain):
    tables, relations = chain
    candidates, candidate_relations = TripletBasedSchemaService._expand_graph(
        ["T0", "T12", "T0"], tables, relations, 1,
    )
    assert [table.table_name for table in candidates] == ["T12", "T11", "T1", "T0"]
    assert candidate_relations == [relations[0], relations[-1]]


def test_auto_mode_skips_cycles_and_self_loops_but_explores_unvisited_branches():
    tables = make_tables(["TAIL", "ISOLATED", "LEAF", "C", "B", "A", "UNREACHABLE"])
    relations = [
        ("A", "references", "B"),
        ("B", "references", "C"),
        ("C", "references", "A"),
        ("B", "references", "B"),
        ("C", "references", "LEAF"),
        ("LEAF", "references", "TAIL"),
    ]
    candidates, candidate_relations = TripletBasedSchemaService._expand_graph(
        ["A", "ISOLATED"], tables, relations, -1,
    )
    assert [table.table_name for table in candidates] == ["TAIL", "ISOLATED", "LEAF", "C", "B", "A"]
    assert candidate_relations == relations


def test_auto_mode_preserves_an_isolated_seed():
    tables = make_tables(["ISOLATED", "OTHER"])
    candidates, candidate_relations = TripletBasedSchemaService._expand_graph(
        ["ISOLATED"], tables, [], -1,
    )
    assert [table.table_name for table in candidates] == ["ISOLATED"]
    assert candidate_relations == []
