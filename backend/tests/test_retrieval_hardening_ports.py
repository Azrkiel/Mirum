"""Dependency-free retrieval regressions; telemetry success means compile success."""

import json

import pytest

from backend import retrieval


@pytest.fixture(autouse=True)
def isolated_index(monkeypatch, tmp_path):
    monkeypatch.setattr(retrieval, "_LOG_FILE", tmp_path / "critic_loop.jsonl")
    monkeypatch.setattr(retrieval, "_INDEX", [])
    monkeypatch.setattr(retrieval, "_IDF", {})
    monkeypatch.setattr(retrieval, "_TFIDF", [])
    monkeypatch.setattr(retrieval, "_INDEX_BUILT", False)


def record(description, code, domain="A", **extra):
    return {
        "part_id": description,
        "generated_code": code,
        "domain_classification": domain,
        "success_status": True,
        **extra,
    }


def write_records(*records):
    retrieval._LOG_FILE.write_text(
        "".join(json.dumps(item) + "\n" for item in records), encoding="utf-8"
    )


@pytest.mark.parametrize("corpus_size", [1, 2])
def test_small_corpus_identical_query_has_positive_weights(corpus_size):
    examples = [record("flanged housing", "result = body.extrude(4)")]
    if corpus_size == 2:
        examples.append(record("bevel gear", "result = body.revolve(360)"))
    write_records(*examples)

    matches = retrieval.retrieve_few_shots("flanged housing", "A", k=1)

    assert [match["code"] for match in matches] == [examples[0]["generated_code"]]
    assert all(weight > 0 for weight in retrieval._IDF.values())


@pytest.mark.parametrize("seeded", [False, True])
def test_incremental_new_vocabulary_matches_fresh_corpus(seeded):
    if seeded:
        write_records(record("flanged housing", "result = body.extrude(4)"))
    retrieval.build_index()

    retrieval.add_to_index("helical nozzle", "result = body.sweep(path)", "B")

    matches = retrieval.retrieve_few_shots("helical nozzle", "B", k=1)
    assert [match["description"] for match in matches] == ["helical nozzle"]
    incremental_idf = retrieval._IDF.copy()
    incremental_vectors = list(retrieval._TFIDF)
    write_records(*(record(r["description"], r["code"], r["domain"]) for r in retrieval._INDEX))
    retrieval.build_index()
    assert retrieval._IDF == incremental_idf
    assert retrieval._TFIDF == incremental_vectors


@pytest.mark.parametrize("k", [0, -1, -3])
@pytest.mark.parametrize("api", ["description", "failure"])
def test_nonpositive_k_returns_no_examples(k, api):
    write_records(*(record("housing", f"result = body.loft({n})") for n in range(4)))
    if api == "description":
        matches = retrieval.retrieve_few_shots("housing", "A", k=k)
    else:
        matches = retrieval.retrieve_for_failure("loft", ["loft"], "A", k=k)
    assert matches == []


def test_similarity_threshold_is_applied_to_each_candidate():
    write_records(
        record("housing", "result = body.extrude(4)"),
        record("housing " + "gear " * 100, "result = body.revolve(360)"),
        record("unrelated bracket", "result = body.fillet(2)"),
    )

    matches = retrieval.retrieve_few_shots("housing", "A", k=3)

    assert [match["description"] for match in matches] == ["housing"]


def test_loaded_dedup_compares_entire_source_not_shared_prefix():
    prefix = "# shared source preamble\n" * 20
    first = prefix + "result = body.loft()"
    second = prefix + "result = body.shell(2)"
    write_records(record("first", first), record("second", second), record("duplicate", first))

    retrieval.build_index()

    assert [r["code"] for r in retrieval._INDEX] == [first, second]
    assert [r["techniques"] for r in retrieval._INDEX] == [["loft"], ["shell"]]


def test_incremental_dedup_compares_entire_source():
    prefix = "# shared source preamble\n" * 20
    first = prefix + "result = body.loft()"
    second = prefix + "result = body.shell(2)"
    retrieval.add_to_index("first", first, "A")
    retrieval.add_to_index("second", second, "B")
    retrieval.add_to_index("duplicate", first, "A")

    assert [r["code"] for r in retrieval._INDEX] == [first, second]
    assert len(retrieval._TFIDF) == 2


def test_invalid_utf8_line_preserves_valid_surrounding_telemetry():
    before = record("before housing", "result = body.loft()")
    after = record("after housing", "result = body.shell(2)")
    retrieval._LOG_FILE.write_bytes(
        (json.dumps(before) + "\n").encode("utf-8")
        + b'{"part_id": "broken \xff"}\n'
        + (json.dumps(after) + "\n").encode("utf-8")
    )

    retrieval.build_index()

    assert [r["description"] for r in retrieval._INDEX] == ["before housing", "after housing"]
    assert [r["techniques"] for r in retrieval._INDEX] == [["loft"], ["shell"]]


@pytest.mark.parametrize("bad_line", ["{broken", "[]", "null", "42", '"text"', "true"])
def test_malformed_or_nonmapping_line_preserves_surrounding_records(bad_line):
    before = record("before", "result = body.loft()")
    after = record("after", "result = body.shell(2)")
    retrieval._LOG_FILE.write_text(
        json.dumps(before) + "\n" + bad_line + "\n" + json.dumps(after) + "\n",
        encoding="utf-8",
    )

    retrieval.build_index()

    assert [r["description"] for r in retrieval._INDEX] == ["before", "after"]


# Characterization tests: keep Mirum's existing domain/technique-aware APIs.
def test_extract_techniques_keeps_ast_dedup_and_syntax_fallback():
    assert retrieval.extract_techniques(
        'result = body.loft().polarArray(1).shell(2).loft().makeHelix(3)\n'
        'note = "body.sweep(path)"'
    ) == ["loft", "polar_array", "shell", "thread"]
    assert retrieval.extract_techniques("result = (") == []


def test_failure_retrieval_keeps_primary_secondary_and_domain_ordering():
    write_records(
        record("cross primary", "result = body.loft(1)", "B"),
        record("same secondary", "result = body.shell(2)", "A"),
        record("same primary", "result = body.loft(3)", "A"),
        record("unrelated", "result = body.extrude(4)", "A"),
    )
    matches = retrieval.retrieve_for_failure("loft", ["shell"], "A", k=3)
    assert [r["description"] for r in matches] == [
        "same primary", "cross primary", "same secondary"
    ]
    fallback = retrieval.retrieve_for_failure("unknown", ["shell"], "A")
    assert [r["description"] for r in fallback] == ["same secondary"]


def test_description_retrieval_keeps_same_domain_boost():
    write_records(
        record("housing", "result = body.extrude(1)", "A"),
        record("housing", "result = body.extrude(2)", "B"),
    )
    assert retrieval.retrieve_few_shots("housing", "A", k=1)[0]["domain"] == "A"


def test_index_keeps_compile_success_semantics_and_excludes_assemblies():
    write_records(
        record("compile only", "result = body.loft()", final_accepted=False),
        record("failed", "result = body.shell(1)", success_status=False),
        record("assembly", "result = body.union(other)", event_type="assembly"),
    )
    retrieval.build_index()
    assert [r["description"] for r in retrieval._INDEX] == ["compile only"]


def test_incremental_trim_keeps_latest_500_and_reweights_retained_corpus():
    write_records(*(record(f"housing {n}", f"result = body.loft({n})") for n in range(500)))
    retrieval.add_to_index("nozzle", "result = body.sweep(path)", "B")
    assert len(retrieval._INDEX) == len(retrieval._TFIDF) == 500
    assert retrieval._INDEX[0]["description"] == "housing 1"
    assert "0" not in retrieval._IDF
    assert retrieval.retrieve_few_shots("nozzle", "B", k=1)[0]["description"] == "nozzle"
