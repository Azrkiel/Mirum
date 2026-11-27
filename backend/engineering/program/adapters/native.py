"""mirum-native placeholder adapter (N1c-1): registered, never available, never falls back.

N1c-2 replaces this with the real NativePlateAdapter. Until then the backend exists in the records, the
registry and the locks, but nothing builds: probe says runtime_missing, build is unavailable (or
unsupported when the plan is not one the native backend declares), and export never produces bytes.
It does not import or call the OCCT adapter.
"""
from __future__ import annotations

from ..kernel import (
    Artifact, BackendDescription, Built, BuildPlan, BuildResult, ExecutionContext, ExportResult,
    InspectResult, KernelFormat, ProbeResult, SupportDecision, check_plan, make_description,
)

__all__ = ['NativeUnavailableAdapter', 'ADAPTER_VERSION']

ADAPTER_VERSION = 'mirum-native-unavailable-0.0.0'


class NativeUnavailableAdapter:
    backend = 'mirum-native'
    mode = 'native'

    def __init__(self, **_options):
        # Same constructor shape as the other native adapters (capability=...), options are ignored.
        pass

    def describe(self) -> BackendDescription:
        return make_description(self.backend, mode='native', adapter_version=ADAPTER_VERSION)

    async def probe(self) -> ProbeResult:
        return ProbeResult(backend=self.backend, mode='native', available=False, reasons=('runtime_missing',))

    def supports(self, plan: BuildPlan) -> SupportDecision:
        return check_plan(self.describe(), plan)

    async def build(self, plan: BuildPlan, context: ExecutionContext) -> Built:
        decision = self.supports(plan)
        common = dict(backend=self.backend, adapter_version=ADAPTER_VERSION, mode='native',
                      capability_id=plan.capability_id, capability_version=plan.capability_version)
        if not decision.supported:
            return Built(BuildResult(outcome='unsupported', reasons=decision.reasons,
                                     diagnostic_code=decision.diagnostic_code, **common))
        return Built(BuildResult(outcome='unavailable', reasons=('runtime_missing',), **common))

    async def inspect(self, artifact: Artifact, plan: BuildPlan) -> InspectResult:
        return InspectResult(backend=self.backend, mode='native', outcome='unavailable',
                             reasons=('runtime_missing',), artifact_sha256=artifact.ref.sha256)

    def export(self, artifact: Artifact | None, fmt: KernelFormat) -> ExportResult:
        return ExportResult(backend=self.backend, mode='native', outcome='unsupported',
                            reasons=('export_not_supported',), format=fmt)
