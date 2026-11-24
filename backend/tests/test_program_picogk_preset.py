"""Offline MEL conformance for the registered PicoGK example."""

from pathlib import Path

import pytest

from engineering.language.diagnostics import MelError
from engineering.language.semantic import compile_source
from engineering.program.planning import plan_program
from engineering.program.regen_bridge import content_from_program

SOURCE = (Path(__file__).parents[1] / 'engineering/fixtures/picogk/reference.mir').read_bytes()


def test_pico_preset_has_one_typed_generator_candidate():
    compiled = compile_source(SOURCE)
    plan = plan_program(compiled)
    assert plan.backend == 'picogk'
    assert len(plan.plans) == 1
    assert plan.plans[0].exports == ('stl',)
    assert content_from_program(compiled.ir) == plan.plans[0].candidate.content
    assert compiled.ir.requirements.requirements[0].value.text == plan.plans[0].candidate.candidate_sha256


@pytest.mark.parametrize('source,code', [
    (SOURCE.replace(b'"reference"', b'"custom"'), 'MEL-S015'),
    (SOURCE.replace(b'"stl"', b'"step"'), 'MEL-S013'),
    (SOURCE.replace(b'"picogk"', b'"occt"'), 'MEL-S012'),
])
def test_pico_preset_refuses_unknown_inputs(source, code):
    with pytest.raises(MelError) as caught:
        compile_source(source)
    assert caught.value.diagnostic.code == code
