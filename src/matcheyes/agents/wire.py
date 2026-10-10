"""Strict wire format between a hosted model and the reasoning contracts.

Hosted structured outputs (Microsoft Foundry / Azure OpenAI `strict: true`) accept only a subset of
JSON Schema: every property required (optional means nullable), `additionalProperties: false`,
no `pattern`, length, count or range keywords, no maps with free keys, `anyOf` never at the root.
The Stage 4 contracts use all of those, deliberately, so they are not sent to the model as-is.

Instead, each model-produced contract has a wire model with the same information in that subset:
argument maps become `{name, value}` lists, defaults become required fields. The wire layer only
reshapes: `to_contract` builds the contract model, so every limit and pattern of the contracts is
still enforced exactly as before. The contracts stay the authority; the wire format is transport.
"""

import json
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ValidationError

from matcheyes.agents.contracts import (
    Assessment,
    Challenge,
    Comparator,
    EvidenceRequest,
    FactAssertion,
    HypothesisKind,
    InvestigationPlan,
    ProposedHypothesis,
    Status,
    ToolName,
)
from matcheyes.agents.reasoning import Step
from matcheyes.domain.base import DomainModel
from matcheyes.domain.claims import ClaimStrength

MAX_PROPERTIES = 100
MAX_DEPTH = 5
UNSUPPORTED_KEYWORDS = frozenset(
    {
        "minLength",
        "maxLength",
        "pattern",
        "format",
        "minimum",
        "maximum",
        "exclusiveMinimum",
        "exclusiveMaximum",
        "multipleOf",
        "patternProperties",
        "unevaluatedProperties",
        "propertyNames",
        "minProperties",
        "maxProperties",
        "unevaluatedItems",
        "contains",
        "minContains",
        "maxContains",
        "minItems",
        "maxItems",
        "uniqueItems",
        "default",
        "allOf",
        "not",
        "if",
        "then",
        "else",
        "oneOf",
    }
)
"""Keywords the strict subset rejects (or that this module never needs)."""

Scalar = int | float | str
Value = int | float | str | bool | None


class WireError(ValueError):
    """The reply does not have the wire shape (or cannot become a contract model)."""


class WireArgument(DomainModel):
    name: str
    value: Scalar


class WireRequest(DomainModel):
    request_id: str
    tool: ToolName
    arguments: tuple[WireArgument, ...]
    hypothesis: HypothesisKind
    purpose: str


class WireAssertion(DomainModel):
    evidence_id: str
    fact: str
    comparator: Comparator
    value: Value


class WireHypothesis(DomainModel):
    kind: HypothesisKind
    status: Status
    statement: str
    supporting: tuple[WireAssertion, ...]
    contradicting: tuple[WireAssertion, ...]


class WirePlan(DomainModel):
    hypotheses: tuple[HypothesisKind, ...]
    requests: tuple[WireRequest, ...]


class WireAssessment(DomainModel):
    hypotheses: tuple[WireHypothesis, ...]
    leading: HypothesisKind | None
    proposed_strength: ClaimStrength
    summary: str


class WireChallenge(DomainModel):
    alternatives: tuple[HypothesisKind, ...]
    objections: tuple[str, ...]
    requests: tuple[WireRequest, ...]


WIRE_OF_STEP: dict[Step, type[DomainModel]] = {
    Step.PLAN: WirePlan,
    Step.ASSESS: WireAssessment,
    Step.CHALLENGE: WireChallenge,
}


# --- strict JSON schema -----------------------------------------------------------------------


def _obj(properties: dict[str, dict[str, Any]]) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": properties,
        "required": list(properties),
        "additionalProperties": False,
    }


def _enum(kind: type[StrEnum]) -> dict[str, Any]:
    return {"type": "string", "enum": [member.value for member in kind]}


def _array(items: dict[str, Any]) -> dict[str, Any]:
    return {"type": "array", "items": items}


def _any(*types: str) -> dict[str, Any]:
    return {"anyOf": [{"type": t} for t in types]}


def _request() -> dict[str, Any]:
    return _obj(
        {
            "request_id": {"type": "string"},
            "tool": _enum(ToolName),
            "arguments": _array(
                _obj({"name": {"type": "string"}, "value": _any("integer", "number", "string")})
            ),
            "hypothesis": _enum(HypothesisKind),
            "purpose": {"type": "string"},
        }
    )


def _assertion() -> dict[str, Any]:
    return _obj(
        {
            "evidence_id": {"type": "string"},
            "fact": {"type": "string"},
            "comparator": _enum(Comparator),
            "value": _any("integer", "number", "string", "boolean", "null"),
        }
    )


def wire_schema(step: Step) -> dict[str, Any]:
    """The strict JSON schema the model is asked to fill for one step."""
    if step is Step.PLAN:
        return _obj({"hypotheses": _array(_enum(HypothesisKind)), "requests": _array(_request())})
    if step is Step.ASSESS:
        hypothesis = _obj(
            {
                "kind": _enum(HypothesisKind),
                "status": _enum(Status),
                "statement": {"type": "string"},
                "supporting": _array(_assertion()),
                "contradicting": _array(_assertion()),
            }
        )
        return _obj(
            {
                "hypotheses": _array(hypothesis),
                "leading": {"anyOf": [_enum(HypothesisKind), {"type": "null"}]},
                "proposed_strength": _enum(ClaimStrength),
                "summary": {"type": "string"},
            }
        )
    return _obj(
        {
            "alternatives": _array(_enum(HypothesisKind)),
            "objections": _array({"type": "string"}),
            "requests": _array(_request()),
        }
    )


def strict_violations(schema: dict[str, Any]) -> list[str]:
    """Where `schema` leaves the documented strict structured-output subset (empty if nowhere)."""
    problems: list[str] = []
    if schema.get("type") != "object" or "anyOf" in schema:
        problems.append("root must be a plain object schema")
    count = 0

    def walk(node: dict[str, Any], path: str, depth: int) -> None:
        nonlocal count
        for keyword in sorted(UNSUPPORTED_KEYWORDS & node.keys()):
            problems.append(f"{path}: unsupported keyword {keyword}")
        if "$ref" in node or "$defs" in node:
            problems.append(f"{path}: references are not used")
        if node.get("type") == "object":
            if depth > MAX_DEPTH:
                problems.append(f"{path}: nested deeper than {MAX_DEPTH}")
            properties: dict[str, Any] = node.get("properties", {})
            count += len(properties)
            if node.get("additionalProperties") is not False:
                problems.append(f"{path}: additionalProperties must be false")
            if sorted(node.get("required", [])) != sorted(properties):
                problems.append(f"{path}: every property must be required")
            for name, child in properties.items():
                walk(child, f"{path}.{name}", depth + 1)
        if node.get("type") == "array":
            walk(node.get("items", {}), f"{path}[]", depth)
        for index, option in enumerate(node.get("anyOf", [])):
            walk(option, f"{path}|{index}", depth)

    walk(schema, "$", 1)
    if count > MAX_PROPERTIES:
        problems.append(f"more than {MAX_PROPERTIES} properties")
    return problems


# --- conversion -------------------------------------------------------------------------------


def _arguments(wire: tuple[WireArgument, ...]) -> dict[str, Scalar]:
    names = [argument.name for argument in wire]
    if len(set(names)) != len(names):
        raise WireError("duplicate argument name")
    return {argument.name: argument.value for argument in wire}


def _request_to_contract(wire: WireRequest) -> EvidenceRequest:
    return EvidenceRequest(
        request_id=wire.request_id,
        tool=wire.tool,
        arguments=_arguments(wire.arguments),
        hypothesis=wire.hypothesis,
        purpose=wire.purpose,
    )


def _assertion_to_contract(wire: WireAssertion) -> FactAssertion:
    return FactAssertion(**wire.model_dump())


def to_contract(step: Step, wire: DomainModel) -> BaseModel:
    """Builds the contract model; raises pydantic's ValidationError if a contract rule fails."""
    if isinstance(wire, WirePlan):
        return InvestigationPlan(
            hypotheses=wire.hypotheses,
            requests=tuple(_request_to_contract(r) for r in wire.requests),
        )
    if isinstance(wire, WireAssessment):
        return Assessment(
            hypotheses=tuple(
                ProposedHypothesis(
                    kind=h.kind,
                    status=h.status,
                    statement=h.statement,
                    supporting=tuple(_assertion_to_contract(a) for a in h.supporting),
                    contradicting=tuple(_assertion_to_contract(a) for a in h.contradicting),
                )
                for h in wire.hypotheses
            ),
            leading=wire.leading,
            proposed_strength=wire.proposed_strength,
            summary=wire.summary,
        )
    if isinstance(wire, WireChallenge):
        return Challenge(
            alternatives=wire.alternatives,
            objections=wire.objections,
            requests=tuple(_request_to_contract(r) for r in wire.requests),
        )
    raise WireError(f"no wire model for step {step.value}")


def _request_to_wire(request: EvidenceRequest) -> WireRequest:
    return WireRequest(
        request_id=request.request_id,
        tool=request.tool,
        arguments=tuple(WireArgument(name=k, value=v) for k, v in request.arguments.items()),
        hypothesis=request.hypothesis,
        purpose=request.purpose,
    )


def to_wire(contract: BaseModel) -> DomainModel:
    """The wire form of a contract model (the inverse of `to_contract`)."""
    if isinstance(contract, InvestigationPlan):
        return WirePlan(
            hypotheses=contract.hypotheses,
            requests=tuple(_request_to_wire(r) for r in contract.requests),
        )
    if isinstance(contract, Assessment):
        return WireAssessment(
            hypotheses=tuple(
                WireHypothesis(
                    kind=h.kind,
                    status=h.status,
                    statement=h.statement,
                    supporting=tuple(WireAssertion(**a.model_dump()) for a in h.supporting),
                    contradicting=tuple(WireAssertion(**a.model_dump()) for a in h.contradicting),
                )
                for h in contract.hypotheses
            ),
            leading=contract.leading,
            proposed_strength=contract.proposed_strength,
            summary=contract.summary,
        )
    if isinstance(contract, Challenge):
        return WireChallenge(
            alternatives=contract.alternatives,
            objections=contract.objections,
            requests=tuple(_request_to_wire(r) for r in contract.requests),
        )
    raise WireError(f"no wire form for {type(contract).__name__}")


def contract_json(step: Step, raw: str) -> str:
    """Wire JSON from a model -> contract JSON for the roles to parse and validate.

    Raises WireError when the reply does not have the wire shape. A reply that has the shape but
    breaks a contract rule (too many requests, a bad request id...) is passed through as contract
    JSON *without* being validated here, so the roles reject it with the same feedback and retry
    budget as any other invalid output.
    """
    try:
        wire = WIRE_OF_STEP[step].model_validate_json(raw)
    except ValidationError as exc:
        raise WireError(
            f"reply does not match the wire schema: {exc.error_count()} error(s)"
        ) from exc
    try:
        return to_contract(step, wire).model_dump_json()
    except (ValidationError, WireError):
        return json.dumps(_loose(wire))


def _loose(wire: DomainModel) -> dict[str, Any]:
    """The contract-shaped dict of a wire model, built without contract validation.

    Duplicate argument names are left as a list, which the contract then rejects."""
    data = wire.model_dump(mode="json")
    for request in data.get("requests", ()):
        names = [a["name"] for a in request["arguments"]]
        if len(set(names)) == len(names):
            request["arguments"] = {a["name"]: a["value"] for a in request["arguments"]}
    return data
