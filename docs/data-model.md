# Data model

Status: **not started (Stage 1)**.

No schema is assumed. Stage 1 will inspect the official hackathon synthetic data and record:

- source files, formats and sizes
- fields, types and units
- event types and their frequencies
- timestamps, ordering and clock semantics
- match / team / player identifiers and relationships
- coordinates / pitch model, if present
- missing, ambiguous or inconsistent values

Only then will the normalized internal domain model (`src/matcheyes/domain`) be defined, with
a field-by-field mapping from source to domain.
