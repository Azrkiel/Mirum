"""Domain-independent identity and stage contracts."""
from __future__ import annotations

import hashlib
import json
from typing import Protocol

from pydantic import BaseModel, ConfigDict


class StrictModel(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True, frozen=True, allow_inf_nan=False)


def canonical_json(value) -> str:
    if isinstance(value, BaseModel):
        value = value.model_dump(mode='json')
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True, allow_nan=False)


def digest(value) -> str:
    return hashlib.sha256(canonical_json(value).encode()).hexdigest()


def output_id(requirements, cem_version: str, calibration_version: str) -> str:
    # A structured envelope prevents ambiguous string concatenation.
    return digest(dict(requirements=requirements.model_dump(mode='json'),
                       cem_version=cem_version, calibration_version=calibration_version))[:12]


class CEM(Protocol):
    name: str
    version: str
    Requirements: type[BaseModel]

    def feasibility(self, req) -> dict: ...
    def size(self, req, budget: int): ...
    def build(self, design) -> dict: ...
    def verify(self, design, bundle) -> dict: ...
    def report(self, *args, **kwargs) -> dict: ...
