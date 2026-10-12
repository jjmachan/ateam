"""Thin HTTP client for HomeBase."""

import httpx

from ateam.config import Config


class HomeBaseError(Exception):
    def __init__(self, status: int, detail):
        self.status = status
        self.detail = detail
        message = detail.get("message", detail) if isinstance(detail, dict) else detail
        super().__init__(str(message))

    @property
    def code(self) -> str | None:
        return self.detail.get("error") if isinstance(self.detail, dict) else None


class HomeBase:
    def __init__(self, cfg: Config, transport: httpx.BaseTransport | None = None):
        headers = {"X-ATeam-Node": cfg.dev_node} if cfg.dev_node else {}
        self.http = httpx.Client(base_url=cfg.homebase_url, headers=headers, timeout=60, transport=transport)

    def request(self, method: str, url: str, **kwargs) -> httpx.Response:
        try:
            response = self.http.request(method, url, **kwargs)
        except httpx.TransportError as e:
            raise HomeBaseError(0, f"cannot reach HomeBase at {self.http.base_url}: {e}") from e
        if response.status_code >= 400:
            try:
                detail = response.json().get("detail", response.text)
            except ValueError:
                detail = response.text
            raise HomeBaseError(response.status_code, detail)
        return response

    def get(self, url: str, **params):
        return self.request("GET", url, params={k: v for k, v in params.items() if v is not None}).json()

    def post(self, url: str, body: dict):
        return self.request("POST", url, json=body).json()

    def put_bytes(self, url: str, data: bytes, **params):
        return self.request("PUT", url, content=data, params=params).json()

    def get_bytes(self, url: str) -> bytes:
        return self.request("GET", url).content
