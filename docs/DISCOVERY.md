# Discovery and mechanism testing for nootropic neuropeptides

This document describes the part of the system that goes beyond surveillance:
how to generate candidates, how to test mechanisms causally, and how to close the
loop so that every experiment improves the next prediction.

## 1. Be precise about "new"

Four different claims get called discovery; they need different pipelines.

| Kind of "new" | Example | Primary method |
| --- | --- | --- |
| New **hypothesis** | "NPY was never tested for social cognition in humans" | literature gap mining (`ntx discover`) |
| New **analog** | Semax is ACTH(4-7) extended with Pro-Gly-Pro | SAR + stability engineering |
| New **endogenous peptide** | unannotated cleavage product of a prohormone | peptidomics + cleavage prediction |
| New **indication** | oxytocin for a condition never studied | evidence mapping + causal translation |

Most commercially interesting "new nootropics" are analogs or new indications,
not new molecules. Say which one you are pursuing before choosing assays.

## 2. Layer 1 — hypothesis generation (implemented)

`ntx discover` reads the claim database and emits ranked, evidence-linked
hypotheses in four classes:

- **contradiction** — same entity/outcome with opposing directions across studies;
- **weak_evidence** — only animal/in-vitro/observational evidence, no RCT or meta-analysis;
- **mechanism_gap** — behavioural effects with no plasticity/neuroprotection readout;
- **untested** — entity × outcome combinations never reported.

Each hypothesis carries its doc_ids, a proposed assay, a design skeleton, and
risks. This is triage, not truth: the output says where an experiment would buy
the most information, not what the answer will be.

**Prior-art check is mandatory** before any wet-lab work: search Europe PMC,
PubMed, Google Scholar, and patents (Espacenet, Google Patents) for the exact
peptide–outcome pair. "Never reported" in an 18-paper demo corpus is not "never
reported" in the literature.

### 2.1 Mechanistic path hypotheses (implemented in 0.6.0)

`ntx mechanism` adds an inspectable mechanism graph: ligand → receptor →
pathway → process, plus mechanism → outcome edges from claim sentences. It has
two edge sources, always distinguishable:

- a curated prior of textbook-level couplings (assumptions, not citations);
- extracted edges from sentences naming an entity and a mechanism with a
  direction cue, retaining doc ids and refusing negated or overlapping mentions.

`ntx mechanism simulate --entity NP:OXT --block MECH:OXTR` removes paths through
the blocked node and reports the predicted **direction of change** per outcome.
`ntx mechanism check --holdout 0.2` rebuilds the graph without held-out documents
and asks whether the sign of the predicted baseline agrees with their claims.
This is hypothesis triage under explicit assumptions; it is not a kinetic model
and does not establish that the real mechanism is the modelled one. Every path
that reaches an outcome is printed with its evidence so the assumption can be
attacked.

## 3. Layer 2 — candidate generation

### 3.1 Endogenous peptide mining
- Prohormone precursors (UniProt, NeuroPedia, Neuropeptidomics DB) → predicted
  cleavage by prohormone convertases (PC1/3, PC2), carboxypeptidase E, PAM.
- Filters: signal peptide, dibasic cleavage sites, C-terminal amidation, conserved
  cysteine pattern, species conservation.
- Mass-spec peptidomics of CSF/plasma/tissue is the ground truth; prediction only
  nominates candidates.

### 3.2 Analog design
- Truncation/series from known active fragments (ACTH(4-10) → Semax;
  ACTH(4-7) → Selank) — the fastest route to a patentable, stabilised peptide.
- Stabilisation: D-amino acids, N-methylation, cyclisation, PEGylation, lipidation,
  C-terminal amidation. Each change must be re-tested for activity, not assumed.
- Natural diversity: venom/secretome libraries, comparative genomics.

### 3.3 Triage filters (cheap, before synthesis)
- Size/charge/lipophilicity heuristics for BBB (MW, PSA, HBD, logP) — weak but
  useful to rank; **measure** permeability, never trust the heuristic alone.
- Protease hot-spot scan (DPP-IV, NEP, ACE) and plasma-stability prediction.
- Aggregation/immunogenicity risk; off-target GPCR panel at the family level.
- Synthesis feasibility and cost.

## 4. Layer 3 — mechanism triage in silico

- Signed mechanism graph (`ntx mechanism`): qualitative path and block
  simulation over curated plus literature-extracted edges. Use it to expose
  assumptions and choose discriminating experiments, not to rank potency.
- Receptor docking / MD for GPCR targets: use for ranking analogs only; binding
  pose is not affinity and not efficacy.
- Expression maps: Allen Brain Atlas, GTEx, Human Protein Atlas — is the receptor
  in the circuit you claim?
- Co-expression and pathway enrichment for a mechanistic hypothesis.
- Off-target selectivity panel (IUPHAR/BPS Guide to Pharmacology, ChEMBL).

## 5. Layer 4 — experimental ladder (go/no-go)

| Tier | Question | Assays | Gate to next tier |
| --- | --- | --- | --- |
| T0 | Is it a stable, brain-exposed molecule? | purity/identity, plasma/CSF stability, PAMPA/MDCK, PK, CSF/plasma ratio | measurable exposure at a tolerable dose |
| T1 | Does it engage the intended target? | radioligand binding, cAMP/Gq/β-arrestin, selectivity panel, receptor KO cells | on-target potency + selectivity window |
| T2 | Does it change the circuit? | primary neurons/iPSC, slice LTP, calcium imaging, c-Fos, spine density | effect at behaviourally relevant concentrations |
| T3 | Does it change behaviour causally? | dose-response, i.n. vs i.v., receptor antagonist/KO, chemogenetic circuit control, confound battery | effect survives antagonism/KO and confound controls |
| T4 | Does it help humans? | preregistered RCT, objective cognitive endpoints, exposure confirmation, blinding | replicated effect with CI excluding null |

**Mechanism is not proven by behaviour.** A peptide that improves memory in mice
may do so through arousal, motor speed, anxiety, or appetite. T3 must include:
antagonist reversal, receptor knockout, dose-response, temporal window, and
control tasks that separate cognition from motivation/arousal/locomotion.

## 6. Nootropic-specific pitfalls

- "Nootropic" has no regulatory definition; pick a measurable cognitive domain
  (episodic memory, working memory, attention) and a primary endpoint in advance.
- Healthy volunteers show ceiling effects and strong expectancy; use validated
  parallel batteries and active/placebo controls.
- Intranasal delivery is variable; without CSF/plasma exposure data a null result
  is uninterpretable (was the drug even there?).
- Commercial conflict of interest and small-sample positive studies dominate this
  literature; preregistration and replication are not optional.

## 7. The closed-loop system

```
literature ──> claims DB ──> hypotheses ──> candidates ──> assay plan
     ^                                                        │
     │                                                        v
 calibration <── prediction ledger <── results <── experiment registry
```

Components to build on top of the current prototype:

1. **Candidate table** — sequence, modifications, target, predicted properties,
   provenance (which hypothesis it addresses).
2. **Experiment registry** — hypothesis_id, design, n (power calculation),
   randomization/blinding plan, preregistration DOI, status.
3. **Result capture** — effect size, CI, n, exclusions, assay QC; negative results
   stored with the same weight as positive ones.
4. **Prediction ledger** — every hypothesis gets an explicit prior probability
   before the experiment; afterwards compute calibration (Brier score). This is
   what makes the system "learn" instead of accumulating anecdotes.
5. **Audit trail** — who changed what, when; immutable evidence links.

## 8. What exists now, and what is next

Implemented: claim extraction with provenance, evidence appraisal, hypothesis
mining (`ntx discover`), the signed mechanism graph and qualitative block
simulation (`ntx mechanism`), candidate import with heuristic descriptors
(`ntx candidate`), precursor import and explicit cleavage/analog enumeration with
append-only lineage (`ntx precursor`, `ntx generate`), two-arm power worksheets,
versioned registration manifests, transactional external outcome import, the
append-only experiment/prediction/outcome ledger with directional Brier scoring,
and a descriptive calibration report (`ntx power`, `ntx ledger`,
`ntx ledger calibration`). Not implemented: signal-peptide/PTM-aware cleavage,
quantitative kinetics, power for paired/cluster/time-to-event/adaptive designs,
verification of external sources, trusted registration time, automatic outcome
extraction, docking/ML ranking, and any calibration claim from small ledgers.

Recommended order: (1) accumulate enough pre-registered, imported outcomes and use
the descriptive calibration report without overclaiming; (2) signal-peptide/
PTM-aware cleavage only with curated, inspectable rules; (3) external source
verification and trusted registration time; (4) only then consider docking or ML
ranking, because without outcomes there is nothing to train or calibrate on.

## 9. Ethics and safety

These are investigational drugs, not supplements. Any human work needs ethics
approval, regulatory authorization, and safety monitoring. The system must never
be used to generate self-experimentation protocols or to market unapproved
peptides; the direct-to-consumer market already does that, and the surveillance
side of this project exists partly to detect it.
