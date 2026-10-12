"""Working out which tailnet device is calling HomeBase (ADR 0002)."""

import json
import subprocess
from dataclasses import dataclass
from functools import lru_cache
from typing import Protocol

from fastapi import HTTPException, Request


@dataclass(frozen=True)
class Caller:
    node_id: str
    node_name: str


class Identity(Protocol):
    def __call__(self, request: Request) -> Caller: ...


@lru_cache(maxsize=256)
def _whois(ip: str) -> Caller | None:
    try:
        out = subprocess.run(
            ["tailscale", "whois", "--json", ip], capture_output=True, text=True, timeout=5, check=True
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    node = json.loads(out).get("Node") or {}
    if not node.get("StableID"):
        return None
    return Caller(node_id=node["StableID"], node_name=node.get("ComputedName") or node.get("Name", ""))


class TailscaleIdentity:
    def __call__(self, request: Request) -> Caller:
        ip = request.client.host if request.client else ""
        caller = _whois(ip)
        if caller is None:
            raise HTTPException(403, f"{ip} is not a device on this tailnet")
        return caller


class HeaderIdentity:
    """For tests and local development only: trusts the `X-ATeam-Node` header."""

    def __call__(self, request: Request) -> Caller:
        node = request.headers.get("x-ateam-node")
        if not node:
            raise HTTPException(403, "missing X-ATeam-Node header")
        return Caller(node_id=node, node_name=node)
