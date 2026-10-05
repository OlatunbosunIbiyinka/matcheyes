"""Independent audit of an audience view: a second, separately written check of what a reader sees.

`audit_view` never calls the renderer. It reads the view's sections as text and checks them
against the *current* verified insight and the observable match, re-deriving what each section may
say from the upstream contracts (claim templates, labels, integrity, evidence pool, squads,
glossary, the audience table). A view built by any means, including a faulty or tampered
renderer, is held to the same rules.

Checks (docs/personalization.md#view-audit):

* source       - the view wraps the current insight, unaltered (fingerprint; staleness).
* fact         - exactly one mandatory FACT, Stage 3's statement verbatim.
* verdict      - an explanation is shown if and only if the verdict carries one.
* label        - exactly one verification label, with the lead claim's strength and the
                 integrity-correct prefix; nothing stronger than the integrity cap.
* claim        - the lead claim's templated text, at its own strength; no other claim text.
* language     - no causal, certainty or intent wording beyond what the strength allows.
* caveat       - the claim's uncertainty verbatim; "Not ruled out" lists exactly the open
                 alternatives.
* integrity    - the compromised warning or exclusion notice, mandatory and before the
                 explanation; never a false warning.
* evidence     - only pool IDs; quarantined IDs only where shown as excluded.
* players      - a player is named only in the involvement line, only if they act in a cited
                 event, with the recounted number of events.
* clubs        - another club only where its own evidence or trigger names it; an unrelated
                 favourite club never appears.
* numbers      - every number comes from the insight, its evidence or the match.
* omitted      - the omitted list is the audience's optional content left out, and nothing
                 mandatory.
* audience     - the audience's required sections are present and complete.
* relevance    - the relevance score is the sum of its stated basis, and the preference terms
                 match a recount from the match.
"""

import re

from matcheyes.agents.contracts import (
    HYPOTHESIS_DESCRIPTIONS,
    HYPOTHESIS_HEDGED,
    INTEGRITY_CAP,
    ClaimType,
    EvidenceIntegrity,
    FinalInsight,
    Status,
    Verdict,
    VerifiedClaim,
)
from matcheyes.agents.hypotheses import TRIGGER_FACTS
from matcheyes.agents.tools import MatchWorkspace
from matcheyes.analytics.contextual import ContextualCandidate
from matcheyes.domain.claims import ClaimStrength
from matcheyes.domain.events import MatchEvent
from matcheyes.orchestration.audit import calibration_findings
from matcheyes.personalization.contracts import (
    EXCLUSION_TEXT,
    INTEGRITY_TEXT,
    Audience,
    PersonalizedInsight,
    SectionKind,
    ViewSection,
    fingerprint,
)
from matcheyes.personalization.glossary import GLOSSARY
from matcheyes.personalization.policy import ALL_OPTIONAL, MANDATORY_SECTIONS, OPTIONAL_SECTIONS

K = SectionKind

VIEW_CHECKS = (
    "source",
    "fact",
    "verdict",
    "label",
    "claim",
    "language",
    "caveat",
    "integrity",
    "evidence",
    "players",
    "clubs",
    "numbers",
    "omitted",
    "audience",
    "relevance",
)

_ID = r"\bev-\d+\b"
_NUMBER = r"\d+(?:\.\d+)?"
_LABEL = r"\[(verified on remaining evidence|verified), ([a-z]+)\]"
_STRENGTH_WORD = r"\[[^\]]*\b(observed|associated|hypothesised|supported)\b[^\]]*\]"
_INVOLVEMENT = r"^(.+) appears in (\d+) of the events behind this insight\.$"
_TERM = r"^.*\(([+-])(\d+)\)$"
_STATE_WORD = {"leading": "leading", "drawing": "level", "trailing": "trailing"}
_UNEXPLAINED = (Verdict.INSUFFICIENT_EVIDENCE, Verdict.UNAVAILABLE)


def _lead(final: FinalInsight) -> VerifiedClaim | None:
    return next((c for c in final.claims if c.hypothesis is not None), None)


def _factual(final: FinalInsight) -> VerifiedClaim | None:
    return next((c for c in final.claims if c.hypothesis is None), None)


def _actors(event: MatchEvent) -> set[str]:
    found = {getattr(event, "player_id", None), getattr(event, "replacement_id", None)}
    return {p for p in found if isinstance(p, str)}


def _words(text: str, term: str) -> bool:
    return (
        re.search(rf"(?<![a-z0-9-]){re.escape(term.lower())}(?![a-z0-9-])", text.lower())
        is not None
    )


def _numbers(text: str) -> set[str]:
    return set(re.findall(_NUMBER, re.sub(_ID, "", text).replace("Stage 3", "")))


def _of(view: PersonalizedInsight, kind: SectionKind) -> list[ViewSection]:
    return [s for s in view.sections if s.kind is kind]


class _Audit:
    def __init__(self, view: PersonalizedInsight, ws: MatchWorkspace, current: FinalInsight):
        self.view, self.ws, self.final = view, ws, current
        self.found: list[str] = []
        self.lead = _lead(current)
        self.candidate: ContextualCandidate | None = ws.candidates.get(current.candidate_id)
        self.pool = {e.evidence_id: e for e in current.evidence}
        self.quarantined = set(current.quarantined)
        self.compromised = current.evidence_integrity is EvidenceIntegrity.COMPROMISED
        self.explained = current.verdict not in _UNEXPLAINED and self.lead is not None
        self.clubs = {s.club.club_id: s.club.name for s in (ws.info.home, ws.info.away)}
        self.players = {p.player_id: p.name for s in (ws.info.home, ws.info.away) for p in s.squad}

    def add(self, check: str, detail: str) -> None:
        self.found.append(f"{check}: {detail}")

    def run(self) -> list[str]:
        for step in (
            self.source,
            self.fact,
            self.verdict,
            self.label,
            self.claim,
            self.language,
            self.caveat,
            self.integrity,
            self.evidence,
            self.players_named,
            self.clubs_named,
            self.numbers,
            self.omitted,
            self.audience,
            self.relevance,
        ):
            step()
        return self.found

    def source(self) -> None:
        if self.view.source_fingerprint != fingerprint(self.view.source):
            self.add("source", "fingerprint does not match the wrapped insight")
        if fingerprint(self.view.source) != fingerprint(self.final):
            self.add("source", "the view wraps a stale or altered insight")

    def fact(self) -> None:
        facts = _of(self.view, K.FACT)
        factual = _factual(self.final)
        expected = self.candidate.statement if self.candidate else None
        if len(facts) != 1 or not facts[0].mandatory:
            self.add("fact", f"{len(facts)} FACT sections; exactly one mandatory is required")
            return
        if facts[0].text != expected or factual is None or factual.text != expected:
            self.add("fact", "FACT is not Stage 3's statement")
        if factual is not None and not set(facts[0].evidence_ids) <= set(
            factual.supporting_evidence_ids
        ):
            self.add("fact", "FACT cites evidence the factual claim does not")

    def verdict(self) -> None:
        interpretations = _of(self.view, K.INTERPRETATION)
        no_insight = _of(self.view, K.NO_INSIGHT)
        if self.explained:
            if len(interpretations) != 1 or not interpretations[0].mandatory:
                self.add("verdict", "a verified explanation must be shown exactly once, mandatory")
            if no_insight:
                self.add("verdict", "an explained insight is shown as having no explanation")
        else:
            if interpretations or _of(self.view, K.CAVEAT) or _of(self.view, K.TRIGGER):
                self.add("verdict", f"a {self.final.verdict.value} insight gains an explanation")
            if len(no_insight) != 1 or not no_insight[0].mandatory:
                self.add("verdict", "the absence of a verified explanation is not stated")
            elif not no_insight[0].text.startswith("No verified explanation"):
                self.add("verdict", "the no-explanation section does not say so")
        framing = " ".join(s.text for s in interpretations)
        if self.lead is not None:
            framing = framing.replace(self.lead.text, "")
        lowered = framing.lower()
        if "explained" in lowered and self.final.verdict is not Verdict.EXPLAINED:
            self.add("verdict", "framing calls a non-explained insight explained")
        if "ordinary variation" in lowered and self.final.verdict is not Verdict.NATURAL_VARIATION:
            self.add("verdict", "framing calls the change ordinary variation")

    def label(self) -> None:
        shown = [
            (s.kind, m)
            for s in self.view.sections
            if s.kind is not K.NARRATIVE
            for m in re.findall(_LABEL, s.text)
        ]
        loose = [
            s.kind
            for s in self.view.sections
            if s.kind is not K.NARRATIVE
            for _ in re.findall(_STRENGTH_WORD, re.sub(_LABEL, "", s.text))
        ]
        if loose:
            self.add("label", f"unrecognised strength label in {[k.value for k in loose]}")
        if not self.explained or self.lead is None:
            if shown:
                self.add("label", "a verification label on an insight with no explanation")
            return
        prefix = "verified on remaining evidence" if self.compromised else "verified"
        expected = (prefix, self.lead.strength.value)
        if [m for _, m in shown] != [expected]:
            self.add("label", f"labels {[m for _, m in shown]}, expected exactly {expected}")
        if any(k is not K.INTERPRETATION for k, _ in shown):
            self.add("label", "a verification label outside the explanation")
        if self.compromised and self.lead.strength.rank > INTEGRITY_CAP.rank:
            self.add("label", "explanation above the compromised-integrity cap")

    def claim(self) -> None:
        templates = {
            text
            for table in (HYPOTHESIS_DESCRIPTIONS, HYPOTHESIS_HEDGED)
            for text in table.values()
        }
        lead_text = self.lead.text if self.lead is not None and self.explained else None
        if self.lead is not None and lead_text is not None and self.lead.hypothesis is not None:
            causal = (
                self.lead.claim_type is ClaimType.CAUSAL
                and self.lead.strength is ClaimStrength.SUPPORTED
            )
            table = HYPOTHESIS_DESCRIPTIONS if causal else HYPOTHESIS_HEDGED
            if lead_text != table[self.lead.hypothesis]:
                self.add("claim", "lead claim text is not its templated wording")
            interpretation = _of(self.view, K.INTERPRETATION)
            if interpretation and lead_text not in interpretation[0].text:
                self.add("claim", "the explanation does not carry the verified claim text")
            if interpretation and interpretation[0].evidence_ids != (
                self.lead.supporting_evidence_ids
            ):
                self.add("claim", "the explanation's evidence differs from the claim's support")
        for s in self.view.sections:
            if s.kind is K.NARRATIVE:
                continue
            text = s.text.replace(lead_text, "") if lead_text else s.text
            for other in templates:
                if other in text:
                    self.add("claim", f"{s.kind.value} carries claim text not verified here")

    def language(self) -> None:
        for s in self.view.sections:
            if s.kind is K.NARRATIVE:
                continue
            text = s.text
            if s.kind is K.EVIDENCE:
                for item in self.pool.values():
                    text = text.replace(item.summary, "")
            strength = ClaimStrength.OBSERVED
            if s.kind in (K.INTERPRETATION, K.CAVEAT) and self.lead is not None:
                strength = self.lead.strength
            for finding in calibration_findings(text, strength):
                self.add("language", f"{s.kind.value}: {finding}")

    def caveat(self) -> None:
        caveats = _of(self.view, K.CAVEAT)
        if not self.explained or self.lead is None:
            return
        if [(c.text, c.mandatory) for c in caveats] != [(self.lead.uncertainty, True)]:
            self.add("caveat", "the claim's uncertainty is missing, altered or not mandatory")
        open_ = {
            k.value.replace("_", " ")
            for k, s in self.final.alternatives
            if s is not Status.CONTRADICTED
        }
        marker = "Not ruled out: "
        for s in self.view.sections:
            if s.kind is K.NARRATIVE or marker not in s.text:
                continue
            tail = s.text.split(marker, 1)[1].split(".", 1)[0]
            listed = {p.strip() for p in tail.split(",") if p.strip()}
            if listed != open_:
                self.add(
                    "caveat", f"'Not ruled out' lists {sorted(listed)}, open is {sorted(open_)}"
                )
        shown = any(marker in c.text for c in caveats)
        if self.final.verdict is Verdict.TENTATIVE and open_ and not shown:
            self.add("caveat", "open alternatives are not shown as not ruled out")

    def integrity(self) -> None:
        sections = _of(self.view, K.INTEGRITY)
        expected: list[tuple[str, bool]] = []
        if self.compromised:
            expected = [(INTEGRITY_TEXT, True)]
        elif self.quarantined:
            expected = [(EXCLUSION_TEXT, True)]
        if [(s.text, s.mandatory) for s in sections] != expected:
            self.add("integrity", "integrity disclosure missing, altered, optional or false")
        if not self.compromised and any(
            INTEGRITY_TEXT in s.text for s in self.view.sections if s.kind is not K.NARRATIVE
        ):
            self.add("integrity", "a compromised warning on an insight that is not compromised")
        kinds = [s.kind for s in self.view.sections]
        if (
            sections
            and K.INTERPRETATION in kinds
            and kinds.index(K.INTEGRITY) > kinds.index(K.INTERPRETATION)
        ):
            self.add("integrity", "the integrity disclosure follows the explanation")

    def evidence(self) -> None:
        lead_ids = set(self.lead.supporting_evidence_ids) if self.lead else set()
        for s in self.view.sections:
            named = set(re.findall(_ID, s.text))
            fields = set(s.evidence_ids)
            if s.kind is K.NARRATIVE:
                if s.text != self.final.narrative:
                    self.add("evidence", "the canonical narrative was altered")
                continue
            if s.kind is K.QUARANTINE:
                if fields != self.quarantined or not named <= self.quarantined:
                    self.add("evidence", "the quarantine list differs from the excluded evidence")
                continue
            if s.kind is K.DETAIL:
                for eid in named & self.quarantined:
                    if not re.search(rf"\b{eid} quarantined\b", s.text):
                        self.add("evidence", f"{eid} named in detail without its exclusion")
                named -= self.quarantined
            cited = named | fields
            if cited & self.quarantined:
                self.add(
                    "evidence",
                    f"{s.kind.value} shows quarantined {sorted(cited & self.quarantined)}",
                )
            elif not cited <= set(self.pool):
                self.add("evidence", f"{s.kind.value} cites {sorted(cited - set(self.pool))}")
            if s.kind in (K.INTERPRETATION, K.TRIGGER) and not cited <= lead_ids:
                self.add("evidence", f"{s.kind.value} cites beyond the claim's support")
            if s.kind is K.EVIDENCE:
                item = self.pool.get(next(iter(fields))) if len(fields) == 1 else None
                head = f"{item.evidence_id} ({item.tool.value}): {item.summary}" if item else ""
                if item is None or not s.text.startswith(head):
                    self.add("evidence", "an evidence section misstates its item")

    def _recount(self) -> tuple[str | None, int]:
        player = self.view.profile.favourite_player_id
        if player is None or player not in self.players:
            return None, 0
        n = sum(
            1
            for e in self.final.event_ids
            if e in self.ws.events and player in _actors(self.ws.events[e])
        )
        return player, n

    def players_named(self) -> None:
        player, n = self._recount()
        lines = _of(self.view, K.INVOLVEMENT)
        for s in self.view.sections:
            if s.kind is K.NARRATIVE:
                continue
            facts = ""
            if s.kind is K.EVIDENCE:
                facts = " ".join(
                    str(v)
                    for i in s.evidence_ids
                    if i in self.pool
                    for v in self.pool[i].facts.values()
                )
            for pid, name in self.players.items():
                if pid in s.text and pid not in facts:
                    self.add("players", f"{s.kind.value} names player {pid}")
                if _words(s.text, name) and not (s.kind is K.INVOLVEMENT and pid == player and n):
                    self.add("players", f"{s.kind.value} names {name}")
        if len(lines) > 1:
            self.add("players", "more than one involvement line")
        for line in lines:
            match = re.match(_INVOLVEMENT, line.text)
            if player is None or not n or match is None:
                self.add("players", "an involvement line without verified involvement")
                continue
            if match.group(1) != self.players[player] or int(match.group(2)) != n:
                self.add("players", f"involvement states {match.group(2)}, recount is {n}")

    def clubs_named(self) -> None:
        team = self.final.team_id
        trigger_teams = self._trigger_teams()
        for s in self.view.sections:
            if s.kind is K.NARRATIVE:
                continue
            for club_id, name in self.clubs.items():
                if club_id == team or not _words(s.text, name):
                    continue
                own = s.kind is K.EVIDENCE and any(
                    name in self.pool[i].summary for i in s.evidence_ids if i in self.pool
                )
                if not own and not (s.kind is K.TRIGGER and club_id in trigger_teams):
                    self.add("clubs", f"{s.kind.value} mentions {name}")
        favourite = self.view.profile.favourite_club_id
        if favourite is not None and favourite not in self.clubs:
            spaced = favourite.replace("-", " ").replace("_", " ")
            for s in self.view.sections:
                if _words(s.text, favourite) or _words(s.text, spaced):
                    self.add("clubs", f"{s.kind.value} mentions a club not in this match")

    def _trigger(self) -> tuple[str, str, str | None] | None:
        """(minute, kind word or "", club ID of an earlier change) the verified explanation's
        own support identifies, or None."""
        lead = self.lead
        if lead is None or lead.hypothesis not in TRIGGER_FACTS:
            return None
        if self.final.verdict not in (Verdict.EXPLAINED, Verdict.TENTATIVE):
            return None
        keys = {k.event_id: k for k in self.ws.key_events}
        for eid in lead.supporting_evidence_ids:
            item = self.pool.get(eid)
            value = item.facts.get(TRIGGER_FACTS[lead.hypothesis]) if item else None
            if not isinstance(value, str):
                continue
            if value in keys and value in self.ws.events:
                kind = keys[value].kind.replace("_", " ")
                return self.ws.events[value].instant.display_minute, kind, None
            earlier = self.ws.candidates.get(value)
            if earlier is not None:
                return earlier.at.display_minute, "", earlier.team_id
        return None

    def _trigger_teams(self) -> set[str]:
        found = self._trigger()
        return {found[2]} if found and found[2] else set()

    def numbers(self) -> None:
        final, c = self.final, self.candidate
        sources = [final.narrative, final.at.display_minute, *final.downgrades, final.failure or ""]
        sources += [t for cl in final.claims for t in (cl.text, cl.uncertainty)]
        if c is not None:
            sources += [c.statement, *c.level_basis, GLOSSARY.get(c.metric, "")]
        sources += [i.summary for i in final.evidence]
        sources += [f"{k} {v}" for i in final.evidence for k, v in i.facts.items()]
        sources += [i.tool.value for i in final.evidence]
        sources += [str(len(final.alternatives) + (1 if final.leading else 0))]
        found = self._trigger()
        if found:
            sources.append(found[0])
        sources.append(str(self._recount()[1]))
        allowed = set().union(*(_numbers(t) for t in sources))
        for s in self.view.sections:
            extra = _numbers(s.text) - allowed
            if extra:
                self.add("numbers", f"{s.kind.value} states {sorted(extra)}")

    def omitted(self) -> None:
        audience = self.view.profile.audience
        expected = {k.value for k in ALL_OPTIONAL - OPTIONAL_SECTIONS[audience]}
        if set(self.view.omitted) != expected:
            self.add("omitted", f"omitted {sorted(self.view.omitted)}, expected {sorted(expected)}")
        if {k.value for k in MANDATORY_SECTIONS} & set(self.view.omitted):
            self.add("omitted", "mandatory content listed as omitted")
        present = {s.kind for s in self.view.sections} - MANDATORY_SECTIONS
        if not present <= OPTIONAL_SECTIONS[audience]:
            extra = sorted(k.value for k in present - OPTIONAL_SECTIONS[audience])
            self.add("omitted", f"sections outside this audience's content: {extra}")
        if {k.value for k in present} & set(self.view.omitted):
            self.add("omitted", "a section is both shown and listed as omitted")

    def audience(self) -> None:
        audience = self.view.profile.audience
        if audience is Audience.ANALYST:
            self._analyst()
            return
        c = self.candidate
        minute = self.final.at.display_minute
        team = self.clubs.get(self.final.team_id, "")
        context = _of(self.view, K.CONTEXT)
        if len(context) != 1 or minute not in context[0].text or team not in context[0].text:
            self.add("audience", "context must state the minute and the team")
        if audience is Audience.BROADCASTER:
            found, shown = self._trigger(), _of(self.view, K.TRIGGER)
            if (found is None) != (not shown) or len(shown) > 1:
                self.add("audience", "the trigger is shown if and only if it is verified")
            if found and shown:
                text = shown[0].text
                if found[0] not in text or (found[1] and found[1] not in text):
                    self.add("audience", "the trigger misstates its type or minute")
            return
        if c is not None:
            state = _STATE_WORD[c.context.game_state.value]
            if context and f" {state}." not in context[0].text:
                self.add("audience", "context misstates the game state")
            if [s.text for s in _of(self.view, K.GLOSSARY)] != [GLOSSARY[c.metric]]:
                self.add("audience", "the metric definition is missing or altered")

    def _analyst(self) -> None:
        final = self.final
        narrative = _of(self.view, K.NARRATIVE)
        if [s.text for s in narrative] != [final.narrative]:
            self.add("audience", "the canonical narrative is missing")
        shown = [s.evidence_ids for s in _of(self.view, K.EVIDENCE)]
        if shown != [(e.evidence_id,) for e in final.evidence]:
            self.add("audience", "not every verified evidence item is shown")
        if bool(_of(self.view, K.QUARANTINE)) != bool(final.quarantined):
            self.add("audience", "quarantined evidence is not listed")
        alternatives = " ".join(s.text for s in _of(self.view, K.ALTERNATIVES))
        for kind, status in final.alternatives:
            entry = f"{kind.value.replace('_', ' ')}: {status.value.replace('_', ' ')}"
            if entry not in alternatives:
                self.add("audience", f"alternative '{entry}' is missing or misstated")
        details = " ".join(s.text for s in _of(self.view, K.DETAIL))
        required = [f"Evidence integrity: {final.evidence_integrity.value}."]
        required += [f"Stage 3 level: {final.stage3_level}"]
        required += list(final.downgrades)
        for claim in final.claims:
            required.append(
                f"{claim.claim_type.value}, {claim.strength.value}, status "
                f"{claim.status.value.replace('_', ' ')}; supporting "
                f"{', '.join(claim.supporting_evidence_ids) or 'none'}; contradicting "
                f"{', '.join(claim.contradicting_evidence_ids) or 'none'}."
            )
        for text in required:
            if text not in details:
                self.add("audience", f"verification detail missing: {text[:60]}")

    def relevance(self) -> None:
        view, final = self.view, self.final
        total = 0
        for term in view.relevance_basis:
            match = re.match(_TERM, term)
            if match is None:
                self.add("relevance", f"unrecognised basis term '{term}'")
                continue
            total += int(match.group(2)) * (1 if match.group(1) == "+" else -1)
        if total != view.relevance:
            self.add("relevance", f"score {view.relevance} is not the sum of its basis ({total})")
        basis = " | ".join(view.relevance_basis)
        if not basis.startswith(f"verdict {final.verdict.value} "):
            self.add("relevance", "the basis misstates the verdict")
        club = view.profile.favourite_club_id
        _, n = self._recount()
        metric = self.candidate.metric if self.candidate else None
        expected = {
            "favourite club is this insight's team": club == final.team_id,
            "favourite club is the opponent": club in self.clubs and club != final.team_id,
            f"favourite player acts in {n} cited event(s)": n > 0,
            "favourite metric": metric is not None and view.profile.favourite_metric == metric,
            "evidence integrity compromised": self.compromised,
        }
        for phrase, should in expected.items():
            if (phrase in basis) != should:
                self.add("relevance", f"basis {'lacks' if should else 'claims'} '{phrase}'")
        if "favourite player" in basis and not n:
            self.add("relevance", "the basis credits a player who is not involved")


def audit_view(view: PersonalizedInsight, ws: MatchWorkspace, current: FinalInsight) -> list[str]:
    """Findings, as "check: detail", for one audience view against the current verified insight.
    Empty means the view is truthful to it."""
    return _Audit(view, ws, current).run()
