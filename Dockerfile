# MatchEyes public demo: the read-only broadcast surface replaying recorded model transcripts.
#
# The image holds the inference engine wheel (src/matcheyes only: no synthetic generator, no
# evaluation package), its hash-pinned runtime dependencies (pydantic only: no azure-identity),
# observable match data and recorded transcripts. It is given no model endpoint and no
# credentials, and `serve` never constructs a live model: recorded matches replay their pinned
# transcripts, every other match uses the deterministic reference reasoner.

FROM python:3.13-slim AS build
WORKDIR /build
COPY pyproject.toml README.md LICENSE ./
COPY src/matcheyes ./src/matcheyes
RUN pip wheel --no-cache-dir --no-deps --wheel-dir /wheels .

FROM python:3.13-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
COPY deploy/requirements.txt /tmp/requirements.txt
COPY --from=build /wheels /tmp/wheels
RUN pip install --no-cache-dir --require-hashes -r /tmp/requirements.txt \
    && pip install --no-cache-dir --no-deps /tmp/wheels/*.whl \
    && rm -rf /tmp/wheels /tmp/requirements.txt \
    && useradd --uid 10001 --no-create-home --shell /usr/sbin/nologin matcheyes
COPY deploy/matches /app/matches
COPY deploy/recordings /app/recordings
USER 10001
EXPOSE 8000
CMD ["python", "-m", "matcheyes", "serve", "/app/matches", "--recordings", "/app/recordings", \
     "--host", "0.0.0.0", "--port", "8000", "--speed", "20"]
