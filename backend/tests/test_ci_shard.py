"""Unit tests for scripts/ci_shard.py (stdlib only; no pytest plugins needed)."""
import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("ci_shard", ROOT / "scripts" / "ci_shard.py")
ci_shard = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ci_shard)


def _ids():
    ids = []
    for f, n in [("backend/tests/test_a.py", 5), ("backend/tests/test_b.py", 3),
                 ("analysis/tests/test_c.py", 9), ("frontend/x/test_d.py", 1),
                 ("backend/tests/test_e.py", 4), ("backend/tests/test_f.py", 2)]:
        ids += [f"{f}::test_{i}" for i in range(n)]
    ids.append("backend/tests/test_b.py::TestK::test_p[a b-1]")
    return ids


def test_every_id_in_exactly_one_shard():
    ids = _ids()
    shards = ci_shard.assign(ids, 3)
    flat = [i for s in shards for i in s]
    assert sorted(flat) == sorted(ids)
    assert len(flat) == len(set(flat))


def test_deterministic_and_order_independent():
    ids = _ids()
    assert ci_shard.assign(ids, 3) == ci_shard.assign(list(ids), 3)
    assert ci_shard.assign(ids, 3) == ci_shard.assign(list(reversed(ids)), 3)


def test_whole_file_stays_together_including_parametrized_ids():
    shards = ci_shard.assign(_ids(), 3)
    where = {}
    for n, s in enumerate(shards):
        for i in s:
            where.setdefault(ci_shard.file_of(i), set()).add(n)
    assert all(len(v) == 1 for v in where.values())


def test_balance_uses_weight_hints():
    ids = _ids()
    shards = ci_shard.assign(ids, 3, hints={"analysis/tests/test_c.py": 1000})
    heavy = [s for s in shards if any(ci_shard.file_of(i) == "analysis/tests/test_c.py" for i in s)][0]
    assert {ci_shard.file_of(i) for i in heavy} == {"analysis/tests/test_c.py"}


def test_shard_files_matches_assign():
    ids = _ids()
    shards = ci_shard.assign(ids, 3)
    for n in range(3):
        assert ci_shard.shard_files(ids, n, 3) == sorted({ci_shard.file_of(i) for i in shards[n]})


def test_parse_collect_keeps_ids_with_spaces_and_drops_noise():
    text = "\n".join([
        "backend/tests/test_a.py::test_x",
        "backend/tests/test_a.py::test_y[a b]",
        "",
        "  indented/warn.py::nope",
        "backend/tests/x.py:12: UserWarning: boo",
        "4047/4048 tests collected (1 deselected) in 3.2s",
    ])
    assert ci_shard.parse_collect(text) == [
        "backend/tests/test_a.py::test_x", "backend/tests/test_a.py::test_y[a b]"]


def _run(ids, rc=0):
    return {"exit_code": rc, "ids": list(ids)}


def test_check_passes_when_union_equals_collection():
    ids = _ids()
    shards = ci_shard.assign(ids, 3)
    assert ci_shard.check(ids, [_run(s) for s in shards]) == []


def test_check_fails_on_missing_id():
    ids = _ids()
    shards = ci_shard.assign(ids, 3)
    shards[1] = shards[1][1:]
    problems = ci_shard.check(ids, [_run(s) for s in shards])
    assert any("missing" in p for p in problems)


def test_check_fails_on_duplicate_and_unexpected_id():
    ids = _ids()
    shards = ci_shard.assign(ids, 3)
    shards[0] = shards[0] + [shards[1][0], "backend/tests/test_zz.py::test_new"]
    problems = ci_shard.check(ids, [_run(s) for s in shards])
    assert any("duplicate" in p for p in problems)
    assert any("unexpected" in p for p in problems)


def test_check_fails_on_failed_shard_or_missing_report():
    ids = _ids()
    shards = ci_shard.assign(ids, 3)
    runs = [_run(s) for s in shards]
    runs[2]["exit_code"] = 1
    assert ci_shard.check(ids, runs)
    assert ci_shard.check(ids, [runs[0], runs[1], None])


def test_check_fails_on_empty_collection():
    assert ci_shard.check([], [_run([])])


def test_main_check_cli(tmp_path):
    ids = _ids()
    shards = ci_shard.assign(ids, 3)
    allf = tmp_path / "all.txt"
    allf.write_text("\n".join(ids) + "\n", encoding="utf-8")
    paths = []
    for n, s in enumerate(shards):
        p = tmp_path / f"shard-{n}.json"
        p.write_text(json.dumps(_run(s)), encoding="utf-8")
        paths.append(str(p))
    assert ci_shard.main(["check", "--all", str(allf), *paths]) == 0
    broken = json.loads(Path(paths[0]).read_text())
    broken["ids"] = broken["ids"][1:]
    Path(paths[0]).write_text(json.dumps(broken), encoding="utf-8")
    assert ci_shard.main(["check", "--all", str(allf), *paths]) == 1
