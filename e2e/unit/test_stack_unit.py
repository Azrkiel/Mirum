"""Offline checks for release stack control."""
import json
import subprocess
import sys
from pathlib import Path
from unittest.mock import Mock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from harness.stack import Stack, StackConfig, StackError


@pytest.fixture
def config(tmp_path: Path) -> StackConfig:
    return StackConfig.from_env(tmp_path, {"MIRUM_E2E_TOKEN": "x" * 64})


def result(stdout: str = "", stderr: str = "", code: int = 0) -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess([], code, stdout, stderr)


@pytest.mark.parametrize("env", [{}, {"MIRUM_E2E_TOKEN": "short"}])
def test_requires_strong_token(tmp_path: Path, env: dict) -> None:
    with pytest.raises(StackError) as exc:
        StackConfig.from_env(tmp_path, env)
    assert "short" not in str(exc.value)


def test_env_overrides(tmp_path: Path) -> None:
    config = StackConfig.from_env(tmp_path, {
        "MIRUM_E2E_TOKEN": "x" * 32, "MIRUM_E2E_PROJECT": "other",
        "MIRUM_E2E_API_URL": "http://api", "MIRUM_E2E_UI_URL": "http://ui",
    })
    assert (config.project, config.api_url, config.ui_url) == ("other", "http://api", "http://ui")


def test_up_invocation(config: StackConfig, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MIRUM_BACKEND_PORT", "18000")
    runner = Mock(return_value=result())
    Stack(config, runner=runner).up(41)
    (argv,), options = runner.call_args
    assert argv == [
        "docker", "compose", "-p", "mirum-e2e", "-f", str(config.repo_root / "compose.release.yml"),
        "-f", str(config.repo_root / "e2e/compose/e2e.override.yml"),
        "up", "-d", "--wait", "--wait-timeout", "41",
    ]
    assert config.token not in " ".join(argv)
    assert options["env"]["MIRUM_ACCESS_TOKEN"] == config.token
    assert options["env"]["MIRUM_BACKEND_PORT"] == "18000"
    assert options["capture_output"] and options["text"]
    assert options["cwd"] == config.repo_root and options["timeout"] >= 41


def test_teardown_preserves_volume_by_default(config: StackConfig) -> None:
    runner = Mock(return_value=result())
    stack = Stack(config, runner=runner)
    stack.down()
    assert runner.call_args.args[0] == stack.compose_argv("down", "--remove-orphans")
    stack.down(remove_volumes=True)
    assert runner.call_args.args[0] == stack.compose_argv("down", "--remove-orphans", "-v")


def test_startup_failure_includes_redacted_error_and_logs(config: StackConfig) -> None:
    runner = Mock(side_effect=[
        result(stderr=f"start {config.token}", code=1),
        result(stdout=f"service {config.token}"),
    ])
    with pytest.raises(StackError) as exc:
        Stack(config, runner=runner).up()
    message = str(exc.value)
    assert config.token not in message and "start ***" in message and "service ***" in message


def test_timeout_output_is_redacted(config: StackConfig) -> None:
    timeout = subprocess.TimeoutExpired(["docker"], 10, stderr=config.token.encode())
    with pytest.raises(StackError) as exc:
        Stack(config, runner=Mock(side_effect=timeout)).down()
    assert config.token not in str(exc.value)


def test_logs_and_freeze(config: StackConfig) -> None:
    runner = Mock(return_value=result(stdout="before " + config.token, stderr="warning " + config.token))
    stack = Stack(config, runner=runner)
    assert stack.logs(7) == "before ***\nwarning ***"
    assert runner.call_args.args[0] == stack.compose_argv("logs", "--no-color", "--tail", "7")
    assert stack.pip_freeze("backend") == "before ***"
    assert runner.call_args.args[0] == stack.compose_argv("exec", "-T", "backend", "python", "-m", "pip", "freeze", "--all")


@pytest.mark.parametrize("failed", ["", "/auth/check", "/_stcore/health"])
def test_wait_healthy(config: StackConfig, failed: str) -> None:
    elapsed = 0.0
    probes = []

    def sleep(seconds: float) -> None:
        nonlocal elapsed
        assert 0 < seconds <= 2
        elapsed += seconds

    def get(url: str, headers: dict, timeout: float) -> int:
        probes.append(url)
        expected = {"X-Mirum-Access-Token": config.token} if url.endswith("/auth/check") else {}
        assert headers == expected and timeout > 0
        return 503 if elapsed == 0 or (failed and url.endswith(failed)) else 200

    stack = Stack(config, clock=lambda: elapsed, sleep=sleep, http_get=get)
    if failed:
        with pytest.raises(StackError, match=failed):
            stack.wait_healthy(5)
        assert elapsed == 5
    else:
        stack.wait_healthy(5)
        assert elapsed == 2
    assert set(probes) == {config.api_url + "/auth/check", config.ui_url + "/_stcore/health"}


def test_restart_then_probe(config: StackConfig) -> None:
    runner = Mock(return_value=result())
    def get(url: str, headers: dict, timeout: float) -> int:
        runner.assert_called_once()
        return 200

    probe = Mock(side_effect=get)
    stack = Stack(config, runner=runner, http_get=probe)
    stack.restart_backend()
    assert runner.call_args.args[0] == stack.compose_argv("restart", "backend")
    assert probe.call_count == 2


def test_image_ids(config: StackConfig) -> None:
    rows = [{"Service": s, "ID": i} for s, i in [("backend", "b"), ("frontend", "f")]]
    runner = Mock(return_value=result(json.dumps(rows)))
    stack = Stack(config, runner=runner)
    assert stack.image_ids() == {"backend": "b", "frontend": "f"}
    assert runner.call_args.args[0] == stack.compose_argv("images", "--format", "json")


def test_image_ids_inspects_containers_when_service_labels_missing(config: StackConfig) -> None:
    runner = Mock(side_effect=[
        result('[{"ContainerName": "custom-name", "ID": "short"}]'),
        result("container-b\ncontainer-f\n"), result("backend sha256:b\nfrontend sha256:f\n"),
    ])
    stack = Stack(config, runner=runner)
    assert stack.image_ids() == {"backend": "sha256:b", "frontend": "sha256:f"}
    assert runner.call_args.args[0][:2] == ["docker", "inspect"]
