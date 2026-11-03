"""Thin optional LLM translator. Explicit ambiguity and human review precede physics."""
import asyncio
import json
import re

from pydantic import Field
from cem.core import StrictModel
from cem.glider.requirements import GliderMissionRequirements


class TextRequest(StrictModel):
    prompt: str = Field(min_length=1, max_length=5000)


def clarifications(prompt):
    text = prompt.lower()
    questions = []
    if (re.search(r'\b\d+(?:\.\d+)?\s*(?:m|metres?|meters?)\s+(?:of\s+)?(?:flight\s+)?(?:duration|seconds?|time)', text)
            or re.search(r'\b(?:duration|time)\b.{0,20}\b\d+(?:\.\d+)?\s*(?:m|metres?|meters?)\b', text)):
        questions.append('Does the flight objective mean distance in metres or duration in seconds?')
    if not re.search(r'\b(drop|dropped|hand[ -]toss|discus|catapult|hi[ -]start)\b', text):
        questions.append('Which launch method: drop, hand toss, discus, catapult or hi-start?')
    if not re.search(r'\b(maximi[sz]e|maximum|as far|as long|stretch goal|reference target)\b', text):
        questions.append('Should Mirum maximize performance, and is the stated value a reference goal or a hard minimum?')
    return questions


async def extract(prompt):
    questions = clarifications(prompt)
    if questions:
        return dict(status='needs_clarification', questions=questions)
    import google.generativeai as genai
    from config import GEMINI_MODEL_PRO
    # A deliberately flat SDK schema: no anyOf/exclusiveMinimum/$ref conversion.
    schema = dict(type='object', properties={
        'requirements_json': dict(type='string'),
        'questions': dict(type='array', items=dict(type='string'))},
        required=['requirements_json', 'questions'])
    model = genai.GenerativeModel(GEMINI_MODEL_PRO, system_instruction=(
        'Translate the user request into the attached typed engineering requirements schema. '
        'Never calculate physics, performance, sizing or geometry. Never output executable code. '
        'Return requirements_json as a JSON string and questions as an array. '
        'If units, launch or objective are ambiguous, return questions and an empty requirements_json. '
        'Use mode=maximize; reference_target is advisory. Put an explicitly requested hard minimum '
        'in minimum_performance. All environmental effects other than still air require explicit user choice. '
        'Use only documented defaults and make the result reviewable. Schema: ' +
        json.dumps(GliderMissionRequirements.model_json_schema())))
    response = await asyncio.wait_for(model.generate_content_async(prompt, generation_config={
        'temperature': 0, 'max_output_tokens': 4096, 'response_mime_type': 'application/json',
        'response_schema': schema}), timeout=30)
    if len(response.text) > 32768:
        raise ValueError('Extraction exceeded response budget')
    value = json.loads(response.text)
    questions = value.get('questions')
    if not isinstance(questions, list) or len(questions) > 8 or any(not isinstance(q, str) or len(q) > 500 for q in questions):
        raise ValueError('Malformed clarification evidence')
    if questions:
        return dict(status='needs_clarification', questions=questions)
    req = GliderMissionRequirements.model_validate_json(value['requirements_json'])
    return dict(status='review_requirements', requirements=req.model_dump(mode='json'),
                detail='Review defaults, units and hard minimum before submitting a physics job.')
