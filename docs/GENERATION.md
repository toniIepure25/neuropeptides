# Precursor cleavage and analog enumeration

This layer generates **hypothesis sources**, not drug candidates. A generated
sequence is a defined molecule with a recorded parent and operation, but nothing
here claims novelty, stability, exposure, selectivity or activity. The user still
has to choose a hypothesis and pre-register a prediction before an experiment.

## 1. Precursor input

A precursor is a user-supplied protein sequence, optionally linked to a UniProt
accession. The system never downloads a sequence automatically.

```json
{"name": "example precursor", "sequence": "AKRPLG", "source": "user-supplied", "uniprot": "TEST1"}
```

```powershell
ntx precursor import --db build/demo.db --file precursors.jsonl --dry-run
ntx precursor import --db build/demo.db --file precursors.jsonl
ntx precursor list --db build/demo.db
ntx precursor show --db build/demo.db --id PR-...
```

Validation: canonical 20 residues, length 2–10,000, non-empty name and source,
duplicate sequence rejected. The `PR-...` id is derived from the sequence.

## 2. Cleavage

```powershell
ntx generate cleavage --db build/demo.db --precursor PR-... `
  --enzyme trypsin --missed 1 --min-length 2 --max-length 30
```

Supported rules are deliberately simple:

| Enzyme | Cleaves after | Exception |
| --- | --- | --- |
| `trypsin` | K, R | not before P |
| `chymotrypsin` | F, W, Y | not before P |
| `lys-c` | K | not before P |
| `arg-c` | R | not before P |
| `glu-c` | D, E | not before P |

A cut after the final residue is not a cut; the natural terminus already ends the
peptide. `--missed N` enumerates every span that joins up to `N + 1` adjacent
digest segments. It is a combinatorial allowance, **not** an enzyme-kinetics or
probability model. There is no pH, temperature, time or accessibility model, and
no signal-peptide or propeptide prediction.

Each generated candidate records:

- `source_kind="precursor"`, `source_id=PR-...`;
- `operation="cleavage"`, enzyme and missed-cleavage setting;
- the first `start`/`end` span and every equivalent site in
  `parameters.sites`, with `parameters.occurrences`;
- no target and no hypothesis (the user supplies these later).

Identical peptide sequences at multiple sites become one candidate row; all
observed sites are still retained in the lineage parameters.

## 3. Analog enumeration

```powershell
ntx generate analogs --db build/demo.db --candidate C-... `
  --truncations 5 --max-variants 250
```

Two explicit operations are supported:

1. **N/C truncations** — remove 1…`--truncations` residues from either terminus
   while keeping at least two residues.
2. **Alanine scan** — replace one non-alanine position with A. This is a
   systematic perturbation, not a claim that alanine is tolerated.

Use `--no-alanine-scan` for truncations only. Generated analogs inherit the
parent’s `target_entity_id`, but **not** its modifications or hypothesis
association; terminal modifications may no longer make sense after truncation.
Each analog records `parent_id`, operation, span and parameters.

## 4. Caps, dry runs and idempotence

`--max-candidates` (cleavage, default 2000) and `--max-variants` (analogs,
default 1000) are hard limits. A request above the limit **fails**; it never
silently truncates the expansion. `--dry-run` validates and returns the proposed
rows without writing.

Generation is idempotent: re-running the same operation reports `existing`
rather than duplicating rows. Every written candidate and lineage row is
append-only. If a candidate already exists from another route, it is skipped and
no new lineage is invented.

## 5. Lineage and export

```powershell
ntx candidate lineage --db build/demo.db --id C-...
ntx ledger export --db build/demo.db --out reports/ledger.json
```

`candidate lineage` shows the direct generation rows for one candidate. Ledger
export schema **v2** adds `precursors` and `lineage` alongside candidates,
experiments, predictions and outcomes; the content hash covers all of them.

`lineage_id` is deterministic from the child, source, operation, span and
parameters. The SQLite triggers reject `UPDATE` and `DELETE` on precursors and
lineage, like the rest of the ledger.

## 6. What is deliberately absent

- No ranking of generated peptides by predicted activity, potency or safety.
- No docking, MD, permeability, stability or immunogenicity prediction.
- No automatic target assignment, hypothesis selection or preregistration.
- No claim that a cleavage pattern occurs in vivo.
- No claim that an analog is novel, patentable or synthetically accessible.
- No substitution matrix, double mutants or combinatorial library design.

The generated rows are inputs to the same prediction ledger and the same
scientific rules: a directional prediction must be registered before the
outcome, and a small ledger is descriptive, not calibrated.
