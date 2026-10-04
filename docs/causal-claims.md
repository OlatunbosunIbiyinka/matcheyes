# Causal claims: correlation is not causation

Status: **vocabulary defined (Stage 1)** in `matcheyes.domain.claims.ClaimStrength`. Tests that
earn each level are implemented in Stages 2 and 5.

MatchEyes never claims causality just because two things happen close together. Every "why"
statement carries a claim strength and may only be shown at the level its evidence earns.

## The ladder

| Level | Meaning | Earned by | Example wording |
| --- | --- | --- | --- |
| `observed` | Directly in the event data | An event or count from the feed | "Kestrel Bay won the ball 9 times in the final third between 60' and 70'." |
| `correlated` | Two metric series move together across the match | A statistical relationship over the whole match. Symmetric, no direction. | "Across the match, Thornvale's pass completion tended to fall when Kestrel Bay pressed more." |
| `associated` | Two changes co-occur in a specific window, both beyond their baselines | Window-level change detection on both series. **Proximity in time is never enough to go higher.** | "Thornvale's completion dropped in the same spell that Kestrel Bay's pressing rose." |
| `hypothesised` | A proposed cause with a mechanism and temporal precedence | An agent or rule proposes `cause → mechanism → effect`. Not yet tested. | "Kestrel Bay's higher press *may* have disrupted Thornvale's build-up." |
| `supported` | A hypothesis that passed every causal test | All tests below pass and alternatives were checked | "The evidence supports Kestrel Bay's pressing as the main reason for the shift." |

`supported` is never presented as proof. No level above `supported` exists.

## Tests a causal hypothesis must pass to become `supported`

| Test | Question | Example check |
| --- | --- | --- |
| Temporal precedence | Did the cause change *before* the effect? | Pressing rose at 60'; completion fell from 62'. |
| Magnitude | Are both changes large compared with the team's own baseline and normal variation? | Change beyond the team's earlier variability, not one noisy minute. |
| Mechanism | Does the effect happen *where and how* the cause acts? | Thornvale's lost passes happen under pressure, in the zones Kestrel Bay is pressing. |
| Persistence | Does it hold over a sustained window? | Not driven by one or two events. |
| Alternatives | Are competing explanations weaker? | Score change, red card, substitution, formation change, fatigue, and opponent changes are each checked. |
| Specificity (where measurable) | Is the effect stronger when the cause is stronger? | Completion is lowest in the minutes with the most pressure. |

If any test fails, the claim is **downgraded** (usually to `associated` or `hypothesised`) or
**rejected**, with the failing test recorded as the reason.

## How ground truth checks this

- Planted interventions define which causal claims *should* reach `supported`.
- **Decoys** (S07, S08) cap claims for a team and window, usually at `associated`. Anything
  stronger is a measured false causal claim.
- **Controls** (S01) have no planted cause. A `supported` claim counts as a false positive
  unless it matches a background effect (game state, fatigue) recorded in the answer key.
- Expected insights list the mechanism signals a correct explanation should cite, so
  "right answer for the right reason" is scorable.
