"""HTTP client for the release stack and its engineering v1 routes."""
from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

import httpx

TOKEN_HEADER = "X-Mirum-Access-Token"
_V1 = "/engineering/v1"
_TERMINAL = {"completed", "cancelled", "unavailable"}


class Api:
    def __init__(
        self, base_url: str, token: str | None, *, client: httpx.Client | None = None,
        timeout: float = 60.0, clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self._token = token
        self._owns_client = client is None
        self._client = httpx.Client(timeout=timeout) if client is None else client
        self._timeout = timeout
        self._clock = clock
        self._sleep = sleep

    def anonymous(self) -> Api:
        """Borrow this client's transport to make requests without its token."""
        return Api(self.base_url, None, client=self._client, timeout=self._timeout,
                   clock=self._clock, sleep=self._sleep)

    def request(self, method: str, path: str, **kw: Any) -> httpx.Response:
        url = self.base_url + "/" + path.lstrip("/")
        send_options = {key: kw.pop(key) for key in ("auth", "follow_redirects") if key in kw}
        kw.setdefault("timeout", self._timeout)
        try:
            request = self._client.build_request(method, url, **kw)
            if self._token is not None:
                request.headers[TOKEN_HEADER] = self._token
            elif TOKEN_HEADER not in httpx.Headers(kw.get("headers")):
                request.headers.pop(TOKEN_HEADER, None)
            return self._client.send(request, **send_options)
        except Exception as error:
            if self._token and self._token in str(error):
                raise RuntimeError(str(error).replace(self._token, "***")) from None
            raise

    def get(self, path: str, **kw: Any) -> httpx.Response:
        return self.request("GET", path, **kw)

    def post(self, path: str, **kw: Any) -> httpx.Response:
        return self.request("POST", path, **kw)

    def patch(self, path: str, **kw: Any) -> httpx.Response:
        return self.request("PATCH", path, **kw)

    def capabilities(self) -> dict:
        return self._record(self.get(f"{_V1}/capabilities"))

    def create_draft(self, body: dict, *, idempotency_key: str | None = None) -> httpx.Response:
        """Create a draft. The v1 draft endpoint ignores idempotency keys."""
        return self.post(f"{_V1}/drafts", json=body)

    def get_draft(self, draft_id: str) -> httpx.Response:
        return self.get(f"{_V1}/drafts/{draft_id}")

    def patch_draft(self, draft_id: str, body: dict, *, revision: int) -> httpx.Response:
        return self.patch(f"{_V1}/drafts/{draft_id}", json={**body, "expected_revision": revision})

    def finalize(self, body: dict) -> httpx.Response:
        return self.post(f"{_V1}/briefs", json=body)

    def get_brief(self, brief_id: str) -> httpx.Response:
        return self.get(f"{_V1}/briefs/{brief_id}")

    def start_job(self, body: dict, *, idempotency_key: str) -> httpx.Response:
        return self.post(f"{_V1}/jobs", json={**body, "idempotency_key": idempotency_key})

    def get_job(self, job_id: str) -> httpx.Response:
        return self.get(f"{_V1}/jobs/{job_id}")

    @staticmethod
    def _record(response: httpx.Response) -> dict:
        if not response.is_success:
            raise RuntimeError(f"Engineering API returned HTTP {response.status_code}")
        return response.json()

    def wait_job(self, job_id: str, *, timeout_s: float = 600, poll_s: float = 2.0) -> dict:
        deadline = self._clock() + timeout_s
        remaining = deadline - self._clock()
        while remaining > 0:
            response = self.get(f"{_V1}/jobs/{job_id}", timeout=min(self._timeout, remaining))
            job = self._record(response)
            if job["lifecycle"] in _TERMINAL:
                return job
            remaining = deadline - self._clock()
            if remaining > 0:
                self._sleep(min(poll_s, remaining))
            remaining = deadline - self._clock()
        message = f"Timed out waiting for job {job_id}"
        raise TimeoutError(message.replace(self._token, "***") if self._token else message)

    def artifact(self, job_id: str, fmt: str) -> httpx.Response:
        return self.get(f"{_V1}/jobs/{job_id}/artifacts/{fmt}")

    def close(self) -> None:
        if self._owns_client:
            self._client.close()
