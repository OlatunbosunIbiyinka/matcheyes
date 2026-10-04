from pydantic import BaseModel, ConfigDict


class DomainModel(BaseModel):
    """Immutable, closed-schema base for every observable fact.

    `extra="forbid"` is a security boundary as well as a hygiene rule: any field that is not
    part of the published observable schema (for example, hidden ground truth) is rejected on
    ingestion instead of being silently carried along.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")
