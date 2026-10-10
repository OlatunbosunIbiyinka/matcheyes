"""The strict wire format: inside the hosted strict-schema subset, lossless, and never a way
around a contract rule."""

import json
from functools import cache
from typing import Any

import pytest
from pydantic import BaseModel, ValidationError

from matcheyes.agents.contracts import Assessment, Challenge, InvestigationPlan
from matcheyes.agents.reasoning import OUTPUT_OF_STEP, AgentTask, RuleBasedReasoner, Step
from matcheyes.agents.wire import (
    MAX_PROPERTIES,
    WIRE_OF_STEP,
    WireError,
    contract_json,
    strict_violations,
    to_contract,
    to_wire,
    wire_schema,
)
from matcheyes.orchestration.investigation import Orchestrator
from tests.agents.support import CONTROL, RED_CARD, SUBSTITUTION, RecordingModel, workspace


@cache
def reference_outputs() -> tuple[tuple[AgentTask, str], ...]:
    """Every raw output of full reference investigations over three scenarios."""
    calls: list[tuple[AgentTask, str]] = []
    for scenario in (CONTROL, RED_CARD, SUBSTITUTION):
        ws = workspace(scenario)
        for candidate in ws.stage3.candidates[:4]:
            recorder = RecordingModel(RuleBasedReasoner())
            Orchestrator(ws, recorder).investigate(candidate.candidate_id)
            calls.extend(recorder.calls)
    return tuple(calls)


@pytest.mark.parametrize("step", list(Step))
def test_every_wire_schema_is_inside_the_strict_subset(step: Step) -> None:
    assert strict_violations(wire_schema(step)) == []


def test_the_contract_schemas_themselves_are_not_strict_compatible() -> None:
    # Why the wire layer exists: sending the contracts as-is would be rejected in strict mode.
    for contract in (InvestigationPlan, Assessment, Challenge):
        assert strict_violations(contract.model_json_schema())


def _properties(schema: dict[str, Any]) -> set[str]:
    return set(schema["properties"])


@pytest.mark.parametrize("step", list(Step))
def test_wire_models_and_wire_schemas_describe_the_same_fields(step: Step) -> None:
    schema = wire_schema(step)
    model = WIRE_OF_STEP[step]
    assert _properties(schema) == set(model.model_fields)
    for name, field in model.model_fields.items():
        assert field.is_required(), f"{model.__name__}.{name} must be required"


@pytest.mark.parametrize(
    ("mutate", "problem"),
    [
        (lambda s: s["properties"]["hypotheses"].update(minItems=1), "minItems"),
        (lambda s: s.update(additionalProperties=True), "additionalProperties"),
        (lambda s: s["required"].pop(), "required"),
        (lambda s: s["properties"].update(x={"type": "string", "pattern": "^a$"}), "pattern"),
        (lambda s: s["properties"].update(x={"$ref": "#/$defs/X"}), "references"),
        (lambda s: s.update(anyOf=[{"type": "object"}]), "root"),
        (lambda s: s["properties"].update(n={"type": "integer", "maximum": 3}), "maximum"),
        (lambda s: s["properties"].update(m={"type": "string", "maxLength": 3}), "maxLength"),
    ],
)
def test_the_subset_checker_catches_each_violation(mutate: Any, problem: str) -> None:
    schema = wire_schema(Step.PLAN)
    mutate(schema)
    assert any(problem in p for p in strict_violations(schema))


def test_the_subset_checker_bounds_depth_and_property_count() -> None:
    leaf: dict[str, Any] = {"type": "string"}
    for _ in range(6):
        leaf = {
            "type": "object",
            "properties": {"x": leaf},
            "required": ["x"],
            "additionalProperties": False,
        }
    assert any("deeper" in p for p in strict_violations(leaf))
    wide = {
        "type": "object",
        "properties": {f"p{i}": {"type": "string"} for i in range(MAX_PROPERTIES + 1)},
        "required": [f"p{i}" for i in range(MAX_PROPERTIES + 1)],
        "additionalProperties": False,
    }
    assert any("properties" in p for p in strict_violations(wide))


def test_every_reference_output_round_trips_through_the_wire_unchanged() -> None:
    outputs = reference_outputs()
    assert {task.step for task, _ in outputs} == set(Step)
    for task, raw in outputs:
        contract = OUTPUT_OF_STEP[task.step].model_validate_json(raw)
        wire = to_wire(contract)
        assert isinstance(wire, WIRE_OF_STEP[task.step])
        rebuilt = OUTPUT_OF_STEP[task.step].model_validate_json(
            contract_json(task.step, wire.model_dump_json())
        )
        assert rebuilt == contract
        assert to_contract(task.step, wire) == contract


def _plan(**overrides: object) -> dict[str, Any]:
    request = {
        "request_id": "r-1",
        "tool": "get_candidate_assessment",
        "arguments": [{"name": "candidate_id", "value": "c-1"}],
        "hypothesis": "tactical_change",
        "purpose": "",
    }
    plan: dict[str, Any] = {"hypotheses": ["tactical_change"], "requests": [request]}
    plan.update(overrides)
    return plan


def test_a_valid_wire_plan_becomes_a_contract_with_an_argument_map() -> None:
    plan = InvestigationPlan.model_validate_json(contract_json(Step.PLAN, json.dumps(_plan())))
    assert plan.requests[0].arguments == {"candidate_id": "c-1"}


@pytest.mark.parametrize(
    "plan",
    [
        _plan(hypotheses=[]),
        _plan(requests=[_plan()["requests"][0] | {"request_id": "Not Valid!"}]),
        _plan(requests=[_plan()["requests"][0]] * 21),
        _plan(
            requests=[
                _plan()["requests"][0]
                | {"arguments": [{"name": "a", "value": 1}, {"name": "a", "value": 2}]}
            ]
        ),
        _plan(
            requests=[_plan()["requests"][0] | {"arguments": [{"name": "Bad-Name", "value": 1}]}]
        ),
        _plan(requests=[_plan()["requests"][0] | {"purpose": "x" * 401}]),
    ],
)
def test_contract_rules_still_reject_what_the_wire_shape_allows(plan: dict[str, Any]) -> None:
    converted = contract_json(Step.PLAN, json.dumps(plan))
    with pytest.raises(ValidationError):
        InvestigationPlan.model_validate_json(converted)


@pytest.mark.parametrize(
    "raw",
    [
        "not json",
        json.dumps({"hypotheses": ["tactical_change"]}),
        json.dumps(_plan(extra="field")),
        json.dumps(_plan(hypotheses=["made_up"])),
        json.dumps(_plan(requests=[_plan()["requests"][0] | {"tool": "run_python"}])),
        json.dumps(_plan(requests=[_plan()["requests"][0] | {"arguments": {"candidate_id": "c"}}])),
    ],
)
def test_replies_without_the_wire_shape_are_wire_errors(raw: str) -> None:
    with pytest.raises(WireError):
        contract_json(Step.PLAN, raw)


def test_unknown_models_are_refused_by_both_converters() -> None:
    class Other(BaseModel):
        x: int = 0

    with pytest.raises(WireError):
        to_wire(Other())
    with pytest.raises(WireError):
        to_contract(Step.PLAN, Other())  # type: ignore[arg-type]
