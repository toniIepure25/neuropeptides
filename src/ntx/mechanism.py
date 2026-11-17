"""Mechanistic graph and qualitative perturbation simulation.

The module adds a second, explicitly hypothetical layer on top of extracted
claims: a signed directed graph from neuropeptide/neurotrophic factor to
receptor, intracellular pathway and process. Edges come from either the curated
prior in ``data/mechanisms.json`` (textbook-level, no doc provenance) or from
sentences in the corpus that name both an entity and a mechanism and carry a
direction cue (doc provenance retained).

Simulation is qualitative. It propagates signs along simple paths and compares a
baseline with a blocked node. It is *not* a kinetic model, does not estimate
concentrations or effect sizes, and must not be read as a prediction. Its job is
to expose which mechanistic assumptions a hypothesis depends on and which
observations would discriminate between them.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import config, http
from .extract import split_clauses, split_sentences
from .nlp import Lexicon
from .store import Store

ACTIVATE = 1
INHIBIT = -1
LINK_ONLY = 0
SIGN_EPS = 0.15
DELTA_EPS = 0.08
MAX_PATHS_PER_OUTCOME = 500

_CUE = re.compile(
    r"\b("
    r"activat\w*|stimulat\w*|enhanc\w*|increas\w*|induc\w*|promot\w*|"
    r"upregulat\w*|phosphorylat\w*|agonist\w*|"
    r"inhibit\w*|block\w*|antagon\w*|suppress\w*|attenuat\w*|decreas\w*|"
    r"downregulat\w*|reduc\w*|"
    r"via|through|mediated|dependent|requires?|associat\w*"
    r")\b",
    re.IGNORECASE,
)
_NEGATION = re.compile(r"\b(not|no|never|neither|without|failed to|did not)\b", re.IGNORECASE)
_INHIBIT_WORDS = (
    "inhibit",
    "block",
    "antagon",
    "suppress",
    "attenuat",
    "decreas",
    "downregulat",
    "reduc",
)
_LINK_WORDS = ("via", "through", "mediated", "dependent", "requires", "associat")
_TOKEN_LEFT = r"(?<![A-Za-z0-9])"
_TOKEN_RIGHT = r"(?![A-Za-z0-9])"


@dataclass(frozen=True)
class Mechanism:
    id: str
    name: str
    cls: str
    aliases: tuple[str, ...]
    uniprot: str = ""


@dataclass
class GraphEdge:
    source: str
    target: str
    sign: int
    confidence: float
    support: int = 0
    origin: str = "prior"
    relation: str = ""
    doc_ids: set[str] = field(default_factory=set)
    entities: set[str] = field(default_factory=set)

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "target": self.target,
            "sign": self.sign,
            "relation": self.relation or _relation_name(self.sign),
            "confidence": round(self.confidence, 3),
            "support": self.support,
            "origin": self.origin,
            "doc_ids": sorted(self.doc_ids),
            "entity_ids": sorted(self.entities),
        }


@dataclass(frozen=True)
class MechanismMention:
    doc_id: str
    entity_id: str
    mechanism_id: str
    surface: str
    sentence: str
    sentence_start: int
    start: int
    end: int
    sign: int
    relation: str
    confidence: float


@dataclass
class MechanismPath:
    nodes: tuple[str, ...]
    edges: tuple[GraphEdge, ...]
    sign: int
    confidence: float

    @property
    def doc_ids(self) -> set[str]:
        out: set[str] = set()
        for edge in self.edges:
            out.update(edge.doc_ids)
        return out

    def to_dict(self) -> dict[str, Any]:
        return {
            "nodes": list(self.nodes),
            "sign": self.sign,
            "confidence": round(self.confidence, 3),
            "evidence_doc_ids": sorted(self.doc_ids),
            "via_prior": all(edge.origin == "prior" for edge in self.edges),
        }


class MechanismLexicon:
    def __init__(self, payload: dict[str, Any]):
        self.mechanisms: dict[str, Mechanism] = {}
        pairs: list[tuple[str, str]] = []
        for item in payload.get("mechanisms", []):
            mechanism = Mechanism(
                id=item["id"],
                name=item["name"],
                cls=item.get("class", "process"),
                aliases=tuple(item.get("aliases") or [item["name"]]),
                uniprot=item.get("uniprot", ""),
            )
            self.mechanisms[mechanism.id] = mechanism
            for alias in mechanism.aliases:
                pairs.append((alias, mechanism.id))
        pairs.sort(key=lambda pair: len(pair[0]), reverse=True)
        seen: set[tuple[str, str]] = set()
        unique: list[tuple[str, str]] = []
        for alias, mid in pairs:
            key = (alias.lower(), mid)
            if key not in seen:
                seen.add(key)
                unique.append((alias, mid))
        self._pairs = unique
        self._by_alias = {alias.lower(): mid for alias, mid in unique}
        pattern = "|".join(re.escape(alias) for alias, _ in unique)
        self._regex = re.compile(_TOKEN_LEFT + "(" + pattern + ")" + _TOKEN_RIGHT, re.IGNORECASE)

    def find(self, sentence: str) -> list[tuple[int, int, str, str]]:
        out: list[tuple[int, int, str, str]] = []
        for match in self._regex.finditer(sentence):
            surface = match.group(0)
            mid = self._by_alias.get(surface.lower())
            if not mid:
                continue
            out.append((match.start(), match.end(), mid, surface))
        return out


def load_prior() -> tuple[MechanismLexicon, list[GraphEdge]]:
    payload = config.resource_json("mechanisms.json")
    lexicon = MechanismLexicon(payload)
    edges: list[GraphEdge] = []
    for item in payload.get("edges", []):
        sign = _sign_from_relation(item.get("relation", ""))
        if sign == 0:
            continue
        edges.append(
            GraphEdge(
                source=item["source"],
                target=item["target"],
                sign=sign,
                confidence=float(item.get("confidence", 0.6)),
                origin="prior",
                relation=item.get("relation", _relation_name(sign)),
            )
        )
    return lexicon, edges


def _sign_from_relation(relation: str) -> int:
    low = relation.lower()
    if "inhibit" in low or "block" in low:
        return INHIBIT
    if "activat" in low or "increase" in low:
        return ACTIVATE
    return LINK_ONLY


def _relation_name(sign: int) -> str:
    if sign > 0:
        return "activates"
    if sign < 0:
        return "inhibits"
    return "associates"


def _cue_sign(cue: str) -> int:
    low = cue.lower()
    if any(word in low for word in _INHIBIT_WORDS):
        return INHIBIT
    if any(low.startswith(word) for word in _LINK_WORDS):
        return LINK_ONLY
    return ACTIVATE


def _clause_for(sentence: str, start: int, end: int) -> tuple[int, int]:
    for clause_start, clause_end in split_clauses(sentence):
        if clause_start <= start < clause_end:
            return clause_start, clause_end
    return 0, len(sentence)


def classify_relation(clause: str, start: int, end: int) -> tuple[int, str, float] | None:
    """Return (sign, relation, confidence) for one mechanism mention in a clause.

    A direction cue must be present in the same clause; negation immediately
    before the cue vetoes the edge. With only a linking cue ("via", "through")
    the edge is returned with sign 0 so it can be inspected but not simulated.
    """
    centre = (start + end) // 2
    candidates: list[tuple[int, int, str]] = []
    for match in _CUE.finditer(clause):
        distance = abs((match.start() + match.end()) // 2 - centre)
        candidates.append((distance, match.start(), match.group(0)))
    if not candidates:
        return None
    directional = [item for item in candidates if _cue_sign(item[2]) != LINK_ONLY]
    pool = directional or candidates
    pool.sort(key=lambda item: (item[0], item[1]))
    distance, cue_start, cue = pool[0]
    prefix = clause[max(0, cue_start - 45) : cue_start]
    if _NEGATION.search(prefix):
        return None
    sign = _cue_sign(cue)
    base = 0.78 if sign != 0 else 0.55
    if distance > 50:
        base -= 0.10
    confidence = max(0.40, min(0.90, base))
    return sign, _relation_name(sign), confidence


def _paragraph_sentences(text: str) -> list[tuple[int, int]]:
    """Sentence spans that never merge a title with the following abstract."""
    spans: list[tuple[int, int]] = []
    offset = 0
    for block in text.split("\n\n"):
        for start, end in split_sentences(block):
            spans.append((offset + start, offset + end))
        offset += len(block) + 2
    return spans


def extract_document_mentions(
    doc_id: str,
    text: str,
    lexicon: MechanismLexicon,
    entities: Lexicon,
) -> list[MechanismMention]:
    """Extract entity-mechanism mentions from every sentence of a document."""
    entity_mentions = entities.detect(text)
    if not entity_mentions:
        return []
    out: list[MechanismMention] = []
    for sentence_start, sentence_end in _paragraph_sentences(text):
        sentence = text[sentence_start:sentence_end]
        local_entities = [
            mention
            for mention in entity_mentions
            if mention.start >= sentence_start and mention.end <= sentence_end
        ]
        if not local_entities:
            continue
        for start, end, mechanism_id, surface in lexicon.find(sentence):
            abs_start = sentence_start + start
            abs_end = sentence_start + end
            if any(mention.start < abs_end and abs_start < mention.end for mention in local_entities):
                # "substance P receptor" is a receptor name, not ligand -> receptor evidence.
                continue
            entity = min(
                local_entities,
                key=lambda mention: min(abs(mention.start - abs_start), abs(mention.end - abs_end)),
            )
            clause_start, clause_end = _clause_for(sentence, start, end)
            clause = sentence[clause_start:clause_end]
            relation = classify_relation(clause, start - clause_start, end - clause_start)
            if relation is None:
                continue
            sign, name, confidence = relation
            entity_distance = min(abs(entity.start - abs_start), abs(entity.end - abs_end))
            if entity_distance > 120:
                confidence = max(0.40, confidence - 0.10)
            out.append(
                MechanismMention(
                    doc_id=doc_id,
                    entity_id=entity.entity_id,
                    mechanism_id=mechanism_id,
                    surface=surface,
                    sentence=sentence,
                    sentence_start=sentence_start,
                    start=start,
                    end=end,
                    sign=sign,
                    relation=name,
                    confidence=round(confidence, 3),
                )
            )
    return out


def _aggregate(
    edges: dict[tuple[str, str, int], GraphEdge],
    source: str,
    target: str,
    sign: int,
    confidence: float,
    relation: str,
    doc_id: str,
    entity_id: str = "",
) -> None:
    key = (source, target, sign)
    edge = edges.get(key)
    if edge is None:
        entities = {entity_id} if entity_id else set()
        edges[key] = GraphEdge(
            source=source,
            target=target,
            sign=sign,
            confidence=confidence,
            support=1,
            origin="claim",
            relation=relation,
            doc_ids={doc_id},
            entities=entities,
        )
        return
    edge.doc_ids.add(doc_id)
    edge.support = len(edge.doc_ids)
    edge.confidence = min(0.90, max(edge.confidence, confidence) + 0.03)
    if entity_id:
        edge.entities.add(entity_id)
    if edge.origin == "prior":
        edge.origin = "prior+claim"


def build_graph(
    store: Store, exclude_docs: frozenset[str] | set[str] | None = None
) -> tuple[MechanismLexicon, list[GraphEdge]]:
    lexicon, prior = load_prior()
    entities = Lexicon(config.ontology())
    excluded = set(exclude_docs or ())
    edge_map: dict[tuple[str, str, int], GraphEdge] = {}
    for edge in prior:
        edge_map[(edge.source, edge.target, edge.sign)] = edge

    docs: list[tuple[str, str]] = []
    for row in store.conn.execute("SELECT doc_id, title, abstract FROM documents ORDER BY doc_id"):
        if row["doc_id"] in excluded:
            continue
        text = (row["title"] + "\n\n" + row["abstract"]).strip()
        docs.append((row["doc_id"], text))

    mentions_by_sentence: dict[tuple[str, str], list[MechanismMention]] = defaultdict(list)
    for doc_id, text in docs:
        for mention in extract_document_mentions(doc_id, text, lexicon, entities):
            _aggregate(
                edge_map,
                mention.entity_id,
                mention.mechanism_id,
                mention.sign,
                mention.confidence,
                mention.relation,
                mention.doc_id,
            )
            mentions_by_sentence[(mention.doc_id, mention.sentence)].append(mention)

    for row in store.conn.execute(
        "SELECT doc_id, entity_id, direction, outcome, role, start, sentence, negated FROM claims"
    ):
        doc_id = row["doc_id"]
        if doc_id in excluded:
            continue
        if str(row["role"] or "") == "comparator" or int(row["negated"] or 0):
            continue
        direction = row["direction"]
        if direction not in {"increase", "decrease"}:
            continue
        mechanisms = mentions_by_sentence.get((doc_id, str(row["sentence"] or "")))
        if not mechanisms:
            continue
        sign = ACTIVATE if direction == "increase" else INHIBIT
        for mention in mechanisms:
            _aggregate(
                edge_map,
                mention.mechanism_id,
                "OUTCOME:" + row["outcome"],
                sign,
                0.70,
                "claim_direction",
                doc_id,
                entity_id=row["entity_id"],
            )

    return lexicon, sorted(
        edge_map.values(),
        key=lambda edge: (edge.source, edge.target, -edge.sign, -edge.confidence),
    )


def _adjacency(edges: list[GraphEdge]) -> dict[str, list[GraphEdge]]:
    out: dict[str, list[GraphEdge]] = defaultdict(list)
    for edge in edges:
        if edge.sign != 0:
            out[edge.source].append(edge)
    return out


def find_paths(
    edges: list[GraphEdge],
    source: str,
    targets: set[str] | None = None,
    max_depth: int = 5,
    min_confidence: float = 0.40,
) -> dict[str, list[MechanismPath]]:
    adjacency = _adjacency(edges)
    found: dict[str, list[MechanismPath]] = defaultdict(list)

    def walk(
        node: str,
        nodes: tuple[str, ...],
        path_edges: tuple[GraphEdge, ...],
    ) -> None:
        if len(path_edges) >= max_depth:
            return
        for edge in adjacency.get(node, []):
            if edge.confidence < min_confidence or edge.target in nodes:
                continue
            next_nodes = nodes + (edge.target,)
            next_edges = path_edges + (edge,)
            if edge.target.startswith("OUTCOME:"):
                if targets is not None and edge.target not in targets:
                    continue
                if edge.entities and source not in edge.entities:
                    continue
                if len(found[edge.target]) >= MAX_PATHS_PER_OUTCOME:
                    continue
                found[edge.target].append(
                    MechanismPath(
                        nodes=next_nodes,
                        edges=next_edges,
                        sign=_path_sign(next_edges),
                        confidence=min(item.confidence for item in next_edges),
                    )
                )
                continue
            walk(edge.target, next_nodes, next_edges)

    walk(source, (source,), ())
    return dict(found)


def _path_sign(edges: tuple[GraphEdge, ...]) -> int:
    sign = 1
    for edge in edges:
        sign *= edge.sign
    return sign


def _weighted_score(paths: list[MechanismPath]) -> float:
    total = sum(path.confidence for path in paths)
    if total <= 0:
        return 0.0
    return sum(path.sign * path.confidence for path in paths) / total


def _direction(score: float, eps: float) -> str:
    if score > eps:
        return "increase"
    if score < -eps:
        return "decrease"
    return "ambiguous"


def _outcome_directions(store: Store, entity_id: str) -> dict[str, dict[str, int]]:
    out: dict[str, dict[str, int]] = defaultdict(lambda: {"increase": 0, "decrease": 0})
    for row in store.conn.execute(
        "SELECT outcome, direction, role, negated FROM claims WHERE entity_id=?",
        (entity_id,),
    ):
        if str(row["role"] or "") == "comparator" or int(row["negated"] or 0):
            continue
        if row["direction"] in {"increase", "decrease"}:
            out[row["outcome"]][row["direction"]] += 1
    return dict(out)


def _entity_name(entity_id: str) -> str:
    for entity in config.ontology().get("entities", []):
        if entity["id"] == entity_id:
            return str(entity.get("name") or entity_id)
    return entity_id


def simulate(
    store: Store,
    entity_id: str,
    blocked: list[str] | tuple[str, ...] = (),
    max_depth: int = 5,
    min_confidence: float = 0.40,
) -> dict[str, Any]:
    """Simulate blocking one or more mechanism nodes for an entity."""
    _lexicon, edges = build_graph(store)
    blocked_set = set(blocked)
    node_ids = {edge.source for edge in edges} | {edge.target for edge in edges}
    unknown_blocked = sorted(blocked_set - node_ids)
    paths_by_outcome = find_paths(edges, entity_id, max_depth=max_depth, min_confidence=min_confidence)
    observed = _outcome_directions(store, entity_id)
    outcomes: dict[str, Any] = {}
    for target, paths in sorted(paths_by_outcome.items()):
        outcome = target.split(":", 1)[1]
        eligible = [path for path in paths if not (set(path.nodes) & blocked_set)]
        baseline = _weighted_score(paths)
        perturbed = _weighted_score(eligible) if eligible else 0.0
        delta = perturbed - baseline
        outcomes[outcome] = {
            "baseline": round(baseline, 3),
            "perturbed": round(perturbed, 3),
            "delta": round(delta, 3),
            "baseline_direction": _direction(baseline, SIGN_EPS),
            "predicted_change": _direction(delta, DELTA_EPS),
            "paths": len(paths),
            "remaining_paths": len(eligible),
            "evidence": sorted({doc for path in paths for doc in path.doc_ids}),
            "top_paths": [
                path.to_dict() for path in sorted(paths, key=lambda path: path.confidence, reverse=True)[:3]
            ],
            "observed_claims": observed.get(outcome, {"increase": 0, "decrease": 0}),
        }
    return {
        "entity_id": entity_id,
        "entity_name": _entity_name(entity_id),
        "blocked": sorted(blocked_set),
        "unknown_blocked": unknown_blocked,
        "model": "signed qualitative paths; not a kinetic or quantitative prediction",
        "outcomes": outcomes,
        "observed_claim_note": (
            "observed claims describe the unperturbed peptide; they are context, "
            "not a test of the blocked simulation"
        ),
    }


def _holdout_docs(doc_ids: list[str], fraction: float) -> set[str]:
    if fraction <= 0:
        return set()
    threshold = int(round(fraction * 20))
    chosen = {
        doc_id
        for doc_id in doc_ids
        if int(hashlib.sha1(doc_id.encode("utf-8")).hexdigest()[:2], 16) % 20 < threshold
    }
    return chosen


def check(
    store: Store,
    holdout: float = 0.2,
    max_depth: int = 5,
    min_confidence: float = 0.40,
) -> dict[str, Any]:
    """Compare baseline path signs with claim directions.

    With ``holdout > 0`` the graph is built without the held-out documents and
    only their claims are scored, which makes the comparison non-circular. With
    ``holdout == 0`` the comparison is in-sample and descriptive only.
    """
    doc_ids = [row["doc_id"] for row in store.conn.execute("SELECT doc_id FROM documents ORDER BY doc_id")]
    held = _holdout_docs(doc_ids, holdout)
    lexicon, edges = build_graph(store, exclude_docs=held)
    predictions: dict[str, dict[str, float]] = {}
    agree = 0
    disagree = 0
    uncovered = 0
    examples: list[dict[str, Any]] = []
    for row in store.conn.execute(
        "SELECT doc_id, entity_id, direction, outcome, role, negated FROM claims ORDER BY doc_id, claim_id"
    ):
        if held and row["doc_id"] not in held:
            continue
        if str(row["role"] or "") == "comparator" or int(row["negated"] or 0):
            continue
        if row["direction"] not in {"increase", "decrease"}:
            continue
        entity_id = row["entity_id"]
        if entity_id not in predictions:
            paths = find_paths(edges, entity_id, max_depth=max_depth, min_confidence=min_confidence)
            predictions[entity_id] = {
                target.split(":", 1)[1]: _weighted_score(path_list) for target, path_list in paths.items()
            }
        score = predictions[entity_id].get(row["outcome"])
        if score is None:
            uncovered += 1
            continue
        predicted = _direction(score, SIGN_EPS)
        if predicted == "ambiguous":
            uncovered += 1
            continue
        if predicted == row["direction"]:
            agree += 1
        else:
            disagree += 1
            if len(examples) < 10:
                examples.append(
                    {
                        "doc_id": row["doc_id"],
                        "entity_id": entity_id,
                        "outcome": row["outcome"],
                        "observed": row["direction"],
                        "predicted": predicted,
                        "score": round(score, 3),
                    }
                )
    total = agree + disagree
    return {
        "mode": "holdout" if held else "in-sample",
        "holdout_docs": len(held),
        "train_docs": len(doc_ids) - len(held),
        "evaluated": total,
        "agree": agree,
        "disagree": disagree,
        "uncovered": uncovered,
        "agreement": round(agree / total, 3) if total else None,
        "mechanism_nodes": len(lexicon.mechanisms),
        "examples": examples,
        "note": (
            "Holdout signs are a weak structural check, not validation of the mechanism or of "
            "causal direction."
            if held
            else "In-sample comparison: descriptive only, not a performance estimate."
        ),
    }


def edges_payload(store: Store, min_support: int = 0) -> list[dict[str, Any]]:
    _lexicon, edges = build_graph(store)
    return [edge.to_dict() for edge in edges if edge.support >= min_support and edge.origin != "prior"]


def export_graph(store: Store) -> dict[str, Any]:
    lexicon, edges = build_graph(store)
    node_ids = {edge.source for edge in edges} | {edge.target for edge in edges}
    nodes: list[dict[str, Any]] = []
    for node_id in sorted(node_ids):
        if node_id.startswith("OUTCOME:"):
            nodes.append({"id": node_id, "kind": "outcome", "name": node_id.split(":", 1)[1]})
        elif node_id in lexicon.mechanisms:
            mechanism = lexicon.mechanisms[node_id]
            nodes.append({"id": node_id, "kind": mechanism.cls, "name": mechanism.name})
        else:
            nodes.append({"id": node_id, "kind": "entity", "name": node_id})
    return {
        "model": "signed qualitative mechanism graph",
        "nodes": nodes,
        "edges": [edge.to_dict() for edge in edges],
    }


def format_edges(payload: list[dict[str, Any]]) -> str:
    lines = ["Extracted entity-mechanism edges", "================================", ""]
    if not payload:
        return "\n".join(
            lines
            + [
                "No entity-mechanism sentences found.",
                "The curated prior still exists; run `ntx mechanism export` to inspect it.",
            ]
        )
    for edge in payload:
        relation = edge["relation"]
        lines.append(
            "%s -> %s [%s, %.2f, %d doc(s), %s]"
            % (edge["source"], edge["target"], relation, edge["confidence"], edge["support"], edge["origin"])
        )
    return "\n".join(lines)


def format_simulation(result: dict[str, Any]) -> str:
    lines = [
        "Qualitative mechanism simulation",
        "================================",
        "",
        "entity: %s | blocked: %s" % (result["entity_id"], ", ".join(result["blocked"]) or "(none)"),
        "model: %s" % result["model"],
        "",
    ]
    if result["unknown_blocked"]:
        lines.append(
            "warning: not present in the graph, block has no effect: %s"
            % ", ".join(result["unknown_blocked"])
        )
        lines.append("")
    if not result["outcomes"]:
        return "\n".join(
            lines
            + [
                "No mechanistic path reaches an outcome.",
                "This is a coverage limit, not evidence that no mechanism exists.",
            ]
        )
    lines.append(
        "%-18s %8s %9s %8s %11s %6s" % ("outcome", "baseline", "perturbed", "delta", "prediction", "paths")
    )
    for outcome, item in result["outcomes"].items():
        lines.append(
            "%-18s %8.2f %9.2f %8.2f %11s %6d"
            % (
                outcome,
                item["baseline"],
                item["perturbed"],
                item["delta"],
                item["predicted_change"],
                item["paths"],
            )
        )
    lines.append("")
    lines.append(result["observed_claim_note"])
    return "\n".join(lines)


def format_check(result: dict[str, Any]) -> str:
    lines = [
        "Mechanistic sign check",
        "======================",
        "",
        "mode: %s | holdout docs: %d | train docs: %d"
        % (result["mode"], result["holdout_docs"], result["train_docs"]),
        "evaluated: %d | agree: %d | disagree: %d | uncovered: %d"
        % (result["evaluated"], result["agree"], result["disagree"], result["uncovered"]),
    ]
    if result["agreement"] is not None:
        lines.append("agreement: %.3f" % result["agreement"])
    lines.append("note: %s" % result["note"])
    if result["examples"]:
        lines.append("")
        lines.append("disagreements:")
        for item in result["examples"]:
            lines.append(
                "  %s %s / %s: observed %s, predicted %s (score %.2f)"
                % (
                    item["doc_id"],
                    item["entity_id"],
                    item["outcome"],
                    item["observed"],
                    item["predicted"],
                    item["score"],
                )
            )
    return "\n".join(lines)


def fetch_uniprot(accession: str, cache: Path, offline: bool = False) -> dict[str, Any]:
    """Fetch and flatten a UniProtKB entry for mechanism-node metadata."""
    if not re.fullmatch(r"[A-Za-z0-9]{6,10}", accession):
        raise ValueError("invalid UniProt accession: " + accession)
    url = "https://rest.uniprot.org/uniprotkb/%s.json" % accession.upper()
    payload = http.get_json(url, cache, offline=offline)
    description = payload.get("proteinDescription", {})
    recommended = description.get("recommendedName", {}) or {}
    full_name = recommended.get("fullName", {}) or {}
    genes = payload.get("genes") or []
    gene = ""
    if genes:
        gene = (genes[0].get("geneName") or {}).get("value") or ""
    functions: list[str] = []
    for comment in payload.get("comments", []) or []:
        if comment.get("commentType") != "FUNCTION":
            continue
        for text in comment.get("texts", []) or []:
            value = text.get("value")
            if value:
                functions.append(value)
    return {
        "accession": payload.get("primaryAccession", accession.upper()),
        "protein_name": full_name.get("value", ""),
        "gene": gene,
        "organism": (payload.get("organism") or {}).get("scientificName", ""),
        "function": " ".join(functions)[:1200],
        "source": url,
    }


def write_export(payload: dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=False) + "\n", encoding="utf-8")
