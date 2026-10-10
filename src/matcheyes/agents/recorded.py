"""Recorded model runs: record a real model once, replay it deterministically and offline.

`RecordingModel` wraps a live model and keeps one transcript entry per distinct request.
`RecordedModel` answers from a transcript only: it has no endpoint, no credentials and no
fallback, so the public surface can replay a real model run without being able to call one.

Transcript format (`matcheyes.transcript/1`), one JSON document:

* `reasoner`: the recorded model's name, reported unchanged on replay;
* `metadata`: model identity only (deployment, served model versions...), strings;
* `entries`, sorted by key, each with
  - `request`: the canonical request (namespace + the AgentTask: observable case file, tool
    results, output contract, retry feedback) - exactly what the model was asked;
  - `key`: sha256 of `request`, the content address the replay looks up;
  - `step`, `response` (raw model text) and `response_sha256`;
  - `latency_ms`: measured during recording (observability; never used on replay).

The transcript's identity is the sha256 of its canonical JSON (`transcript_sha256`); a deployment
can pin it. Integrity is checked on load and never repaired:

* a malformed document, an unknown format, or a pinned hash that does not match makes the whole
  transcript unusable;
* an entry whose key is not the hash of its request, whose response does not match its hash,
  whose step disagrees with its request, or whose key appears twice is dropped.

Every request the transcript cannot answer - a miss, a dropped entry, an unusable transcript -
raises ModelUnavailableError. The roles then fail safely (an `unavailable` insight, never stale or
invented model output), and the lifecycle and broadcast layers handle it like any outage.
"""

import gzip
import hashlib
import json
import threading
import time
from collections.abc import Callable, Mapping
from pathlib import Path

from pydantic import Field, ValidationError

from matcheyes.agents.reasoning import AgentTask, ModelUnavailableError, ReasoningModel, Step
from matcheyes.domain.base import DomainModel

TRANSCRIPT_FORMAT = "matcheyes.transcript/1"
MAX_TRANSCRIPT_BYTES = 200_000_000


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def canonical_request(task: AgentTask, namespace: str = "") -> str:
    """The canonical text of one model request; its sha256 is the transcript key."""
    return json.dumps(
        {"namespace": namespace, "task": task.model_dump(mode="json")},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    )


def request_key(task: AgentTask, namespace: str = "") -> str:
    return _sha256(canonical_request(task, namespace))


class TranscriptEntry(DomainModel):
    key: str = Field(pattern=r"^[0-9a-f]{64}$")
    step: Step
    request: str
    response: str
    response_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    latency_ms: float | None = Field(default=None, ge=0)

    def problem(self) -> str | None:
        """Why this entry cannot be trusted, or None."""
        if _sha256(self.request) != self.key:
            return "request hash mismatch"
        if _sha256(self.response) != self.response_sha256:
            return "response hash mismatch"
        try:
            step = json.loads(self.request)["task"]["step"]
        except (ValueError, KeyError, TypeError):
            return "malformed request"
        if step != self.step.value:
            return "step mismatch"
        return None


class Transcript(DomainModel):
    format: str = TRANSCRIPT_FORMAT
    reasoner: str = Field(min_length=1, max_length=200)
    metadata: dict[str, str] = Field(default_factory=dict)
    entries: tuple[TranscriptEntry, ...]

    def canonical(self) -> str:
        return json.dumps(
            self.model_dump(mode="json"), sort_keys=True, separators=(",", ":"), ensure_ascii=True
        )

    @property
    def sha256(self) -> str:
        return _sha256(self.canonical())

    def write(self, path: Path) -> str:
        """Writes the canonical document (gzip-compressed for a `.gz` path, reproducibly);
        returns its sha256, which does not depend on the compression."""
        text = self.canonical()
        data = text.encode("utf-8")
        if path.suffix == ".gz":
            data = gzip.compress(data, mtime=0)
        path.write_bytes(data)
        return _sha256(text)


def _read(path: Path) -> str:
    if path.stat().st_size > MAX_TRANSCRIPT_BYTES:
        raise ValueError("transcript too large")
    if path.suffix != ".gz":
        return path.read_text(encoding="utf-8")
    with gzip.open(path, "rb") as handle:
        data = handle.read(MAX_TRANSCRIPT_BYTES + 1)
    if len(data) > MAX_TRANSCRIPT_BYTES:
        raise ValueError("transcript too large")
    return data.decode("utf-8")


def read_transcript(path: Path) -> Transcript:
    """Parses a transcript file (`.gz` or plain). Raises on any read or validation error."""
    transcript = Transcript.model_validate_json(_read(path))
    if transcript.format != TRANSCRIPT_FORMAT:
        raise ValueError("unknown transcript format")
    return transcript


class RecordingModel:
    """Wraps a live model; records one entry per distinct request. Thread-safe.

    A repeated request gets the first recorded answer, because a replay can only give one answer
    per request. Calls that fail are not recorded: their replay is a miss, which fails the same
    way (ModelUnavailableError)."""

    def __init__(
        self,
        inner: ReasoningModel,
        namespace: str = "",
        clock: Callable[[], float] = time.perf_counter,
    ) -> None:
        self.inner = inner
        self.namespace = namespace
        self.clock = clock
        self._entries: dict[str, TranscriptEntry] = {}
        self._lock = threading.Lock()

    @property
    def name(self) -> str:
        return self.inner.name

    def scoped(self, namespace: str) -> "RecordingModel":
        """A view recording into the same transcript under another namespace."""
        view = RecordingModel(self.inner, namespace, self.clock)
        view._entries, view._lock = self._entries, self._lock
        return view

    def respond(self, task: AgentTask) -> str:
        request = canonical_request(task, self.namespace)
        key = _sha256(request)
        with self._lock:
            known = self._entries.get(key)
        if known is not None:
            return known.response
        start = self.clock()
        response = self.inner.respond(task)
        entry = TranscriptEntry(
            key=key,
            step=task.step,
            request=request,
            response=response,
            response_sha256=_sha256(response),
            latency_ms=(self.clock() - start) * 1000,
        )
        with self._lock:
            return self._entries.setdefault(key, entry).response

    def transcript(self, metadata: Mapping[str, str] | None = None) -> Transcript:
        with self._lock:
            entries = tuple(sorted(self._entries.values(), key=lambda e: e.key))
        return Transcript(reasoner=self.name, metadata=dict(metadata or {}), entries=entries)


class RecordedModel:
    """Answers only from a transcript; anything it cannot answer is ModelUnavailableError."""

    def __init__(self, transcript: Transcript | None, namespace: str = "") -> None:
        self.namespace = namespace
        self.problems: list[str] = []
        self.reasoner = transcript.reasoner if transcript is not None else "recorded:unavailable"
        self.transcript_sha256 = transcript.sha256 if transcript is not None else None
        self.metadata = dict(transcript.metadata) if transcript is not None else {}
        self._answers: dict[str, str] = {}
        if transcript is None:
            return
        seen: dict[str, int] = {}
        for entry in transcript.entries:
            seen[entry.key] = seen.get(entry.key, 0) + 1
        for entry in transcript.entries:
            problem = "duplicate key" if seen[entry.key] > 1 else entry.problem()
            if problem is None:
                self._answers[entry.key] = entry.response
            else:
                self.problems.append(f"{entry.key[:12]}: {problem}")

    @classmethod
    def load(
        cls, path: Path, expected_sha256: str | None = None, namespace: str = ""
    ) -> "RecordedModel":
        """A replaying model for a transcript file. A file that cannot be read, parsed or matched
        to its pinned hash gives a model whose every answer is ModelUnavailableError."""
        try:
            transcript = read_transcript(path)
        except (OSError, EOFError, ValueError, ValidationError) as exc:
            model = cls(None, namespace)
            model.problems.append(type(exc).__name__)
            return model
        if expected_sha256 is not None and transcript.sha256 != expected_sha256:
            model = cls(None, namespace)
            model.reasoner = transcript.reasoner
            model.problems.append("transcript hash mismatch")
            return model
        return cls(transcript, namespace)

    @property
    def name(self) -> str:
        return self.reasoner

    @property
    def usable(self) -> bool:
        return self.transcript_sha256 is not None

    def scoped(self, namespace: str) -> "RecordedModel":
        """A view answering from the same transcript under another namespace."""
        view = RecordedModel(None, namespace)
        view.problems, view.reasoner = self.problems, self.reasoner
        view.transcript_sha256, view.metadata = self.transcript_sha256, self.metadata
        view._answers = self._answers
        return view

    def respond(self, task: AgentTask) -> str:
        if not self.usable:
            raise ModelUnavailableError("transcript unusable")
        answer = self._answers.get(request_key(task, self.namespace))
        if answer is None:
            raise ModelUnavailableError("transcript miss")
        return answer
