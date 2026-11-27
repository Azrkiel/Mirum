"""Mock kernel adapter (F03): plans without geometry and never touches native code.

Every record it emits is labelled mode 'mock'; the record validators make a completed result, a
verdict, a claim scope, artifacts or measurements unconstructible in that mode.
"""
from __future__ import annotations

from ..kernel import (
    Artifact, BackendDescription, Built, BuildPlan, BuildResult, ExecutionContext, ExportResult,
    InspectResult, KernelFormat, ProbeResult, SupportDecision, check_plan, make_description,
)

MOCK_ADAPTER_VERSION = 'mock-0.1.0'


class MockKernelAdapter:
    mode = 'mock'

    def __init__(self, backend: str):
        if backend not in ('occt', 'picogk', 'mirum-native'):
            raise ValueError(f'unknown backend {backend!r}')
        self.backend = backend

    def describe(self) -> BackendDescription:
        return make_description(self.backend, mode='mock', adapter_version=MOCK_ADAPTER_VERSION)

    async def probe(self) -> ProbeResult:
        return ProbeResult(backend=self.backend, mode='mock', available=False,
                           reasons=('mock_backend',))

    def supports(self, plan: BuildPlan) -> SupportDecision:
        return check_plan(self.describe(), plan)

    async def build(self, plan: BuildPlan, context: ExecutionContext) -> Built:
        decision = self.supports(plan)
        common = dict(backend=self.backend, adapter_version=MOCK_ADAPTER_VERSION, mode='mock',
                      capability_id=plan.capability_id, capability_version=plan.capability_version)
        if not decision.supported:
            return Built(BuildResult(outcome='unsupported', reasons=decision.reasons,
                                     diagnostic_code=decision.diagnostic_code, **common))
        return Built(BuildResult(outcome='planned', op_stream=plan.op_stream, **common))

    async def inspect(self, artifact: Artifact, plan: BuildPlan) -> InspectResult:
        return InspectResult(backend=self.backend, mode='mock', outcome='unavailable',
                             reasons=('mock_backend',), artifact_sha256=artifact.ref.sha256)

    def export(self, artifact: Artifact, fmt: KernelFormat) -> ExportResult:
        return ExportResult(backend=self.backend, mode='mock', outcome='unsupported',
                            reasons=('export_not_supported',), format=fmt)
