"""Control and probe the release Compose stack."""
from __future__ import annotations

import json
import os
import subprocess
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path


class StackError(RuntimeError):
    """A release stack operation failed."""


@dataclass(frozen=True)
class StackConfig:
    repo_root: Path
    compose_files: tuple[Path, ...]
    project: str
    token: str = field(repr=False)
    api_url: str = "http://127.0.0.1:8000"
    ui_url: str = "http://127.0.0.1:8501"

    @classmethod
    def from_env(cls, repo_root: Path, environ: Mapping[str, str] | None = None) -> StackConfig:
        env = os.environ if environ is None else environ
        token = env.get("MIRUM_E2E_TOKEN", "")
        if len(token) < 32:
            raise StackError("MIRUM_E2E_TOKEN requires at least 32 characters")
        root = repo_root.resolve()
        return cls(
            root, (root / "compose.release.yml", root / "e2e/compose/e2e.override.yml"),
            env.get("MIRUM_E2E_PROJECT", "mirum-e2e"), token,
            env.get("MIRUM_E2E_API_URL", cls.api_url), env.get("MIRUM_E2E_UI_URL", cls.ui_url),
        )


def _http_get(url: str, headers: Mapping[str, str], timeout: float) -> int:
    request = urllib.request.Request(url)
    for key, value in headers.items():
        request.add_unredirected_header(key, value)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status
    except urllib.error.HTTPError as error:
        with error:
            return error.code
    except OSError:
        return 0


class Stack:
    def __init__(
        self, config: StackConfig, runner: Callable[..., subprocess.CompletedProcess] = subprocess.run,
        clock: Callable[[], float] = time.monotonic, sleep: Callable[[float], None] = time.sleep,
        http_get: Callable[[str, Mapping[str, str], float], int] | None = None,
    ) -> None:
        self.config = config
        self._runner = runner
        self._clock = clock
        self._sleep = sleep
        self._http_get = _http_get if http_get is None else http_get

    def redact(self, text: str) -> str:
        return text.replace(self.config.token, "***") if self.config.token else text

    def compose_argv(self, *args: str) -> list[str]:
        argv = ["docker", "compose", "-p", self.config.project]
        for path in self.config.compose_files:
            argv.extend(("-f", str(path)))
        return [*argv, *args]

    def _run(self, argv: list[str], timeout: float = 60, *, include_stderr: bool = False) -> str:
        try:
            proc = self._runner(
                argv, capture_output=True, text=True, timeout=timeout, cwd=self.config.repo_root,
                env={**os.environ, "MIRUM_ACCESS_TOKEN": self.config.token},
            )
        except subprocess.TimeoutExpired as error:
            chunks = [str(error)]
            for output in (error.stdout, error.stderr):
                if isinstance(output, bytes):
                    output = output.decode(errors="replace")
                if output:
                    chunks.append(output)
            raise StackError(self.redact("\n".join(chunks))) from None
        except Exception as error:
            raise StackError(self.redact(str(error))) from None
        if proc.returncode:
            message = f"Docker exited {proc.returncode}\n{proc.stdout}\n{proc.stderr}"
            raise StackError(self.redact(message))
        output = proc.stdout
        if include_stderr and proc.stderr:
            output = output.rstrip("\n") + "\n" + proc.stderr if output else proc.stderr
        return self.redact(output)

    def up(self, timeout_s: int = 300) -> None:
        try:
            self._run(self.compose_argv("up", "-d", "--wait", "--wait-timeout", str(timeout_s)), timeout_s + 30)
        except StackError as error:
            try:
                tail = self.logs()
            except StackError as log_error:
                tail = f"Could not collect logs: {log_error}"
            raise StackError(self.redact(f"{error}\n{tail}")) from None

    def down(self, remove_volumes: bool = False) -> None:
        argv = self.compose_argv("down", "--remove-orphans")
        if remove_volumes:
            argv.append("-v")
        self._run(argv)

    def restart_backend(self, timeout_s: int = 180) -> None:
        self._run(self.compose_argv("restart", "backend"), timeout_s)
        self.wait_healthy(timeout_s)

    def wait_healthy(self, timeout_s: int = 180) -> None:
        endpoints = {
            self.config.api_url.rstrip("/") + "/auth/check": {"X-Mirum-Access-Token": self.config.token},
            self.config.ui_url.rstrip("/") + "/_stcore/health": {},
        }
        deadline = self._clock() + timeout_s
        statuses = dict.fromkeys(endpoints, 0)
        while self._clock() < deadline:
            statuses = dict.fromkeys(endpoints, 0)
            for url, headers in endpoints.items():
                remaining = deadline - self._clock()
                if remaining <= 0:
                    break
                try:
                    statuses[url] = self._http_get(url, headers, min(5.0, remaining))
                except OSError:
                    statuses[url] = 0
                except Exception as error:
                    raise StackError(self.redact(f"Health probe {url} failed: {error}")) from None
            if all(status == 200 for status in statuses.values()):
                return
            remaining = deadline - self._clock()
            if remaining > 0:
                self._sleep(min(2.0, remaining))
        failed = ", ".join(f"{url} ({status})" for url, status in statuses.items() if status != 200)
        raise StackError(self.redact(f"Health check timed out: {failed}"))

    def logs(self, tail: int = 500) -> str:
        return self._run(self.compose_argv("logs", "--no-color", "--tail", str(tail)), include_stderr=True)

    def image_ids(self) -> dict[str, str]:
        """Use Compose service labels, inspecting containers for older CLI formats."""
        try:
            raw = self._run(self.compose_argv("images", "--format", "json"))
            rows = json.loads(raw)
            if isinstance(rows, dict):
                rows = [rows]
            if isinstance(rows, list) and rows and all(
                isinstance(row, dict) and row.get("Service") and (row.get("ID") or row.get("ImageID"))
                for row in rows
            ):
                return {row["Service"]: row.get("ID") or row["ImageID"] for row in rows}
        except (StackError, json.JSONDecodeError):
            pass
        containers = self._run(self.compose_argv("ps", "--all", "--quiet")).split()
        if not containers:
            return {}
        template = '{{index .Config.Labels "com.docker.compose.service"}} {{.Image}}'
        output = self._run(["docker", "inspect", "--format", template, *containers])
        images = {}
        for line in output.splitlines():
            parts = line.split()
            if len(parts) != 2:
                raise StackError("Invalid Docker inspect service/image pair")
            service, image_id = parts
            images[service] = image_id
        return images

    def pip_freeze(self, service: str) -> str:
        return self._run(self.compose_argv("exec", "-T", service, "python", "-m", "pip", "freeze", "--all"))
