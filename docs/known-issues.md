# Known issues

## Edition numbers do not identify a server process

**Status:** fixed by run identity ([ADR-0017](decisions/0017-replay-lifecycle-identity-and-serving-bounds.md)).
Stream IDs are now `{run}:{edition}:{index}`, with a random `run` per replay; a `Last-Event-ID`
from another run gets 204 and the page rebuilds. The description below is kept as history.

**What happens.** Each stream message carries the id `edition:index`, and a reconnecting browser
sends the id of the last message it received as `Last-Event-ID`. The server resumes after `index`
when `edition` equals its current edition number. Edition numbers start at 0 in every server
process, so after a restart the first edition of the new process has the same number as the first
edition of the old one.

**Failure mode.** If the restarted server serves different content for the same match (corrected
data, a different evaluator or a different build), a browser that reconnects with `0:k` receives
the new content from `k + 1` and no `edition` change, so it does not rebuild. The page then shows
the old replay's first `k + 1` messages followed by the new replay's remainder: a mixture that
neither server produced. With identical, deterministic content the mixture happens to equal the new
replay, which is why the issue is not visible in normal demos. When the numbers differ (for example
the old process had looped to edition 3), the browser resets correctly.

**Reproduction.** `tests/api/test_edition_identity.py` runs two servers for the same match, the
second with one event removed, and resumes against the second with an id from the first. The page
misses the second server's "Data incomplete" status message. The test now passes without `xfail`:
the second server answers 204 and the page rebuilds from the second server's replay.

**Effect on pause and resume.** None. Overflow recovery opens a new `EventSource`, which sends no
`Last-Event-ID`, so the server always sends its current edition from index 0. Ordinary pauses
apply the same messages the page would have applied without pausing, so a paused page is exactly
as affected by a restart as one that never paused.

**Fix (done, ADR-0017).** Include a per-process token in the edition identity (in the message id
and the `edition` event), and treat an unknown token like an older edition: replay from index 0.
