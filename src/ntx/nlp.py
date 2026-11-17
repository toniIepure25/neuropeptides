"""Entity detection and normalization.

Strategy (deliberately conservative):
1. Curated synonym gazetteer, longest match first, token boundaries.
2. Ambiguous acronyms are accepted only when the document expands them.
3. Generic tokens ("peptide", "neuropeptide") never become entities. Europe PMC
   itself tagged the word "neuropeptide" as UniProt P69145 in a spot check, so
   this filter is load-bearing, not cosmetic.
4. Europe PMC annotations are imported as candidates but must resolve to a
   known UniProt/CHEBI identifier and pass the same filters.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Any

from .models import Mention

_TOKEN_LEFT = r"(?<![A-Za-z0-9])"
# Hyphenated compounds such as "BDNF-depression" or "NPY-mRNA" are not the
# entity; "NT-3" must still match, so only a hyphen followed by a letter blocks.
_TOKEN_RIGHT = r"(?![A-Za-z0-9])(?!-[A-Za-z])"


class Lexicon:
    def __init__(self, ontology: dict[str, Any]):
        self.ontology = ontology
        self.generic = {t.lower() for t in ontology.get("generic_terms", [])}
        self.entities: dict[str, dict[str, Any]] = {}
        self.by_term: list[tuple[str, str, bool]] = []  # term, entity_id, ambiguous
        self.by_uniprot: dict[str, str] = {}
        self.by_chebi: dict[str, str] = {}

        for entity in ontology.get("entities", []):
            eid = entity["id"]
            self.entities[eid] = entity
            ambiguous = {a.lower() for a in entity.get("ambiguous_acronyms", [])}
            for term in [entity["name"]] + list(entity.get("synonyms", [])):
                term = term.strip()
                if not term:
                    continue
                self.by_term.append((term, eid, term.lower() in ambiguous))
            if entity.get("uniprot"):
                self.by_uniprot[entity["uniprot"].upper()] = eid
            if entity.get("chebi"):
                self.by_chebi[entity["chebi"].upper()] = eid

        # Longest first so "neuropeptide Y" wins over a hypothetical "neuropeptide".
        self.by_term.sort(key=lambda item: len(item[0]), reverse=True)
        seen = set()
        unique: list[tuple[str, str, bool]] = []
        for term, eid, amb in self.by_term:
            key = (term.lower(), eid)
            if key not in seen:
                seen.add(key)
                unique.append((term, eid, amb))
        self.by_term = unique
        pattern = "|".join(re.escape(t) for t, _, _ in self.by_term)
        self.regex = re.compile(_TOKEN_LEFT + "(" + pattern + ")" + _TOKEN_RIGHT, re.IGNORECASE)

    # ------------------------------------------------------------------ util
    def entity(self, entity_id: str) -> dict[str, Any]:
        return self.entities[entity_id]

    def class_of(self, entity_id: str) -> str:
        return self.entities[entity_id].get("class", "")

    def _terms_for(self, entity_id: str) -> list[str]:
        entity = self.entities[entity_id]
        return [entity["name"]] + list(entity.get("synonyms", []))

    def _acronym_expanded(self, text: str, entity_id: str, acronym: str) -> bool:
        entity = self.entities[entity_id]
        name = re.escape(entity["name"])
        acr = re.escape(acronym)
        patterns = [
            name + r"[^()]{0,60}\(" + acr + r"\)",
            acr + r"\s*\(" + name + r"\)",
        ]
        for pattern in patterns:
            if re.search(pattern, text, re.IGNORECASE):
                return True
        # Fall back to "full name appears somewhere" - still safer than accepting
        # a bare acronym in a document that never names the entity.
        return bool(re.search(r"\b" + name + r"\b", text, re.IGNORECASE))

    # --------------------------------------------------------------- detect
    def detect(self, text: str) -> list[Mention]:
        candidates: list[tuple[int, int, str, str, bool]] = []
        for match in self.regex.finditer(text):
            surface = match.group(0)
            low = surface.lower()
            if low in self.generic:
                continue
            entity_id = ""
            ambiguous = False
            for term, eid, amb in self.by_term:
                if term.lower() == low:
                    entity_id, ambiguous = eid, amb
                    break
            if not entity_id:
                continue
            entity = self.entities[entity_id]
            # Brand/drug names are case-sensitive in practice: "SeMax" is an
            # orthodontic landmark, not the peptide drug Semax.
            if entity.get("class") in {"NTS", "NTS_PATHOGEN"}:
                canonical = entity["name"]
                internal_caps = any(ch.isupper() for ch in surface[1:])
                if internal_caps and surface != canonical and not surface.isupper():
                    continue
            if ambiguous and not self._acronym_expanded(text, entity_id, surface):
                continue
            candidates.append((match.start(), match.end(), surface, entity_id, ambiguous))

        # Longest, then leftmost; drop overlaps.
        candidates.sort(key=lambda c: (c[0], -(c[1] - c[0])))
        chosen: list[tuple[int, int, str, str, bool]] = []
        for cand in candidates:
            if chosen and cand[0] < chosen[-1][1]:
                continue
            chosen.append(cand)

        mentions: list[Mention] = []
        for start, end, surface, entity_id, _amb in chosen:
            method = "gazetteer"
            if surface.isupper() and len(surface) <= 6:
                method = "acronym"
            mentions.append(
                Mention(
                    doc_id="",
                    entity_id=entity_id,
                    surface=surface,
                    start=start,
                    end=end,
                    method=method,
                    confidence=0.92 if method == "gazetteer" else 0.80,
                )
            )
        return mentions

    # ------------------------------------------------- Europe PMC annotations
    def mentions_from_annotations(self, text: str, annotations: Iterable[dict[str, Any]]) -> list[Mention]:
        out: list[Mention] = []
        for ann in annotations or []:
            exact = ann.get("exact") or ""
            if not exact or exact.lower() in self.generic:
                continue
            section = ann.get("section") or ""
            if "References" in section:
                continue
            entity_id: str | None = None
            for tag in ann.get("tags", []) or []:
                uri = (tag.get("uri") or "").upper()
                if "UNIPROT" in uri:
                    acc = uri.rstrip("/").split("/")[-1]
                    entity_id = self.by_uniprot.get(acc)
                elif "CHEBI" in uri:
                    acc = uri.rstrip("/").split("/")[-1]
                    entity_id = entity_id or self.by_chebi.get(acc)
                if entity_id:
                    break
            if not entity_id:
                continue
            prefix = ann.get("prefix") or ""
            start = text.find(exact, max(0, len(prefix) - 200))
            if start < 0:
                start = text.find(exact)
            if start < 0:
                continue
            out.append(
                Mention(
                    doc_id="",
                    entity_id=entity_id,
                    surface=exact,
                    start=start,
                    end=start + len(exact),
                    method="annotation",
                    confidence=0.70,
                )
            )
        return out

    # -------------------------------------------------------------- helpers
    def focus_entities(self) -> list[dict[str, Any]]:
        return [e for e in self.entities.values() if e.get("class") != "NTS_PATHOGEN"]


def document_flags(text: str) -> list[str]:
    """Document-level interpretation warnings that must travel with a report."""
    flags: list[str] = []
    low = text.lower()
    if "neurotropic" in low and "neurotrophic" in low:
        flags.append("uses both 'neurotropic' and 'neurotrophic'")
    if re.search(r"neurotropic\s+(virus|pathogen|strain)", low):
        flags.append("mentions a neurotropic pathogen")
    if "nootropic" in low:
        flags.append("mentions nootropics")
    return flags
