"""Append-only candidate, experiment, prediction and outcome ledger.

The ledger separates a prediction from its result. Predictions are write-once;
a correction is a new outcome row. The score is deliberately narrow: a
directional Brier score over decisive outcomes, with every exclusion counted.
"""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from typing import Any

from . import calibration as calibration_mod
from . import candidates as candidates_mod
from . import clock, discovery, stats
from . import generate as generate_mod
from . import power as power_mod
from . import precursors as precursors_mod
from .models import Experiment, Outcome, Prediction
from .store import Store

DECISIVE = {"increase", "decrease"}
OBSERVED = DECISIVE | {"none", "mixed", "unclear"}
QC = {"pass", "warn", "fail"}
BASELINE_BRIER = 0.25
UNDERPOWERED_N = 10
GROUP_MIN_N = 3
REGISTRATION_SCHEMA = "ntx-registration-v1"
OUTCOME_SCHEMA = "ntx-outcomes-v1"
CHECKPOINT_SCHEMA = "ntx-checkpoint-v1"
CHECKPOINT_REPORT_SCHEMA = "ntx-checkpoint-report-v1"
REGISTRATION_KEYS = {
    "schema",
    "hypothesis_id",
    "candidate_id",
    "design",
    "assay",
    "primary_outcome",
    "prediction",
    "probability",
    "n_planned",
    "preregistration",
    "rationale",
    "power",
}
OUTCOME_KEYS = {
    "experiment_id",
    "source_id",
    "observed",
    "effect_size",
    "ci_low",
    "ci_high",
    "n",
    "qc",
    "note",
}


class LedgerError(ValueError):
    """Invalid ledger transition or input value."""


def _canonical(payload: Any) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _new_id(prefix: str, payload: dict[str, Any]) -> str:
    return prefix + hashlib.sha256(_canonical(payload).encode("utf-8")).hexdigest()[:12]


def _finite(value: float, field: str) -> float:
    if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value):
        raise LedgerError("%s must be a finite number" % field)
    return float(value)


def _resolve_hypothesis(store: Store, hypothesis_id: str) -> discovery.Hypothesis:
    if not hypothesis_id:
        raise LedgerError("hypothesis id is required")
    hypotheses = discovery.generate(store, top=100000, include_untested=True)
    for hypothesis in hypotheses:
        if hypothesis.hypothesis_id == hypothesis_id:
            return hypothesis
    raise LedgerError("unknown hypothesis id: %s" % hypothesis_id)


def _power_check(power: dict[str, Any], n_planned: int) -> dict[str, Any]:
    if not power:
        return {
            "provided": False,
            "required_total": None,
            "n_planned": n_planned,
            "meets_required": None,
        }
    required = int(power["n_total_with_dropout"])
    return {
        "provided": True,
        "required_total": required,
        "n_planned": n_planned,
        "meets_required": (n_planned >= required) if n_planned > 0 else None,
    }


def register(
    store: Store,
    *,
    hypothesis_id: str,
    prediction: str,
    probability: float,
    design: str,
    assay: str,
    primary_outcome: str,
    candidate_id: str = "",
    n_planned: int = 0,
    preregistration: str = "",
    rationale: str = "",
    power: dict[str, Any] | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    hypothesis = _resolve_hypothesis(store, hypothesis_id)
    if prediction not in DECISIVE:
        raise LedgerError("prediction must be increase or decrease")
    probability = _finite(probability, "probability")
    if not 0.5 <= probability < 1.0:
        raise LedgerError("probability must be in [0.5, 1.0)")
    for name, value in (
        ("design", design),
        ("assay", assay),
        ("primary_outcome", primary_outcome),
    ):
        if not isinstance(value, str) or not value.strip():
            raise LedgerError("%s is required" % name)
    if not isinstance(n_planned, int) or isinstance(n_planned, bool) or n_planned < 0:
        raise LedgerError("n_planned must be a non-negative integer")
    if not isinstance(preregistration, str) or not isinstance(rationale, str):
        raise LedgerError("preregistration and rationale must be strings")
    if candidate_id and candidates_mod.get_candidate(store, candidate_id) is None:
        raise LedgerError("unknown candidate id: %s" % candidate_id)
    power_sheet = {}
    if power:
        try:
            power_sheet = power_mod.validate_power(power)
        except power_mod.PowerError as exc:
            raise LedgerError("invalid power worksheet: %s" % exc) from exc

    created_at = clock.iso()
    experiment_id = _new_id(
        "E-",
        {
            "hypothesis_id": hypothesis_id,
            "candidate_id": candidate_id,
            "design": design,
            "assay": assay,
            "primary_outcome": primary_outcome,
            "prediction": prediction,
            "probability": probability,
            "created_at": created_at,
        },
    )
    prediction_id = _new_id(
        "P-",
        {
            "experiment_id": experiment_id,
            "direction": prediction,
            "probability": probability,
            "rationale": rationale,
            "created_at": created_at,
        },
    )
    experiment = Experiment(
        experiment_id=experiment_id,
        hypothesis_id=hypothesis.hypothesis_id,
        hypothesis_kind=hypothesis.kind,
        entity_id=hypothesis.entity_id,
        outcome=hypothesis.outcome,
        hypothesis_snapshot=hypothesis.to_dict(),
        candidate_id=candidate_id,
        design=design.strip(),
        assay=assay.strip(),
        primary_outcome=primary_outcome.strip(),
        n_planned=n_planned,
        preregistration=preregistration.strip(),
        power=power_sheet,
        created_at=created_at,
    )
    prediction_row = Prediction(
        prediction_id=prediction_id,
        experiment_id=experiment_id,
        direction=prediction,
        probability=probability,
        rationale=rationale.strip(),
        created_at=created_at,
    )
    try:
        if not dry_run:
            store.add_experiment(experiment, commit=False)
            store.add_prediction(prediction_row, commit=False)
            store.conn.commit()
    except Exception as exc:
        store.conn.rollback()
        raise LedgerError("could not register experiment: %s" % exc) from exc
    return {
        "experiment": experiment.to_dict(),
        "prediction": prediction_row.to_dict(),
        "power_check": _power_check(power_sheet, n_planned),
        "dry_run": dry_run,
    }


def _read_json_object(path: Path, label: str) -> dict[str, Any]:
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    except OSError as exc:
        raise LedgerError("could not read %s %s: %s" % (label, path, exc)) from exc
    except json.JSONDecodeError as exc:
        raise LedgerError("invalid JSON in %s %s: %s" % (label, path, exc)) from exc
    if not isinstance(payload, dict):
        raise LedgerError("%s %s must contain a JSON object" % (label, path))
    return payload


def load_power_file(path: Path) -> dict[str, Any]:
    payload = _read_json_object(path, "power worksheet")
    try:
        return power_mod.validate_power(payload)
    except power_mod.PowerError as exc:
        raise LedgerError("invalid power worksheet: %s" % exc) from exc


def register_from_file(store: Store, path: Path, *, dry_run: bool = False) -> dict[str, Any]:
    payload = _read_json_object(path, "registration manifest")
    if payload.get("schema") != REGISTRATION_SCHEMA:
        raise LedgerError("registration schema must be %s" % REGISTRATION_SCHEMA)
    unknown = set(payload) - REGISTRATION_KEYS
    if unknown:
        raise LedgerError("unknown registration field(s): %s" % ", ".join(sorted(unknown)))
    required = {"hypothesis_id", "design", "assay", "primary_outcome", "prediction", "probability"}
    missing = required - set(payload)
    if missing:
        raise LedgerError("missing registration field(s): %s" % ", ".join(sorted(missing)))
    return register(
        store,
        hypothesis_id=payload["hypothesis_id"],
        candidate_id=payload.get("candidate_id", ""),
        design=payload["design"],
        assay=payload["assay"],
        primary_outcome=payload["primary_outcome"],
        prediction=payload["prediction"],
        probability=payload["probability"],
        n_planned=payload.get("n_planned", 0),
        preregistration=payload.get("preregistration", ""),
        rationale=payload.get("rationale", ""),
        power=payload.get("power"),
        dry_run=dry_run,
    )


def _prepare_outcome(
    *,
    experiment_id: str,
    observed_direction: str,
    effect_size: float | None = None,
    ci_low: float | None = None,
    ci_high: float | None = None,
    n: int | None = None,
    qc: str = "pass",
    note: str = "",
    source_id: str = "",
    created_at: str | None = None,
) -> Outcome:
    if observed_direction not in OBSERVED:
        raise LedgerError("observed_direction must be one of: %s" % ", ".join(sorted(OBSERVED)))
    if qc not in QC:
        raise LedgerError("qc must be one of: %s" % ", ".join(sorted(QC)))
    if not isinstance(note, str):
        raise LedgerError("note must be a string")
    if not isinstance(source_id, str):
        raise LedgerError("source_id must be a string")
    source_id = source_id.strip()
    if effect_size is not None:
        effect_size = _finite(effect_size, "effect_size")
    if (ci_low is None) != (ci_high is None):
        raise LedgerError("ci_low and ci_high must be provided together")
    if ci_low is not None and ci_high is not None:
        ci_low = _finite(ci_low, "ci_low")
        ci_high = _finite(ci_high, "ci_high")
        if ci_low > ci_high:
            raise LedgerError("ci_low must be <= ci_high")
    if n is not None and (not isinstance(n, int) or isinstance(n, bool) or n < 0):
        raise LedgerError("n must be a non-negative integer")

    created_at = created_at or clock.iso()
    note = note.strip()
    outcome_id = _new_id(
        "O-",
        {
            "experiment_id": experiment_id,
            "observed_direction": observed_direction,
            "effect_size": effect_size,
            "ci_low": ci_low,
            "ci_high": ci_high,
            "n": n,
            "qc": qc,
            "note": note,
            "source_id": source_id,
            "created_at": created_at,
        },
    )
    return Outcome(
        outcome_id=outcome_id,
        experiment_id=experiment_id,
        observed_direction=observed_direction,
        effect_size=effect_size,
        ci_low=ci_low,
        ci_high=ci_high,
        n=n,
        qc=qc,
        note=note,
        source_id=source_id,
        created_at=created_at,
    )


def record_outcome(
    store: Store,
    *,
    experiment_id: str,
    observed_direction: str,
    effect_size: float | None = None,
    ci_low: float | None = None,
    ci_high: float | None = None,
    n: int | None = None,
    qc: str = "pass",
    note: str = "",
    source_id: str = "",
) -> dict[str, Any]:
    row = store.conn.execute(
        "SELECT experiment_id FROM experiments WHERE experiment_id=?", (experiment_id,)
    ).fetchone()
    if row is None:
        raise LedgerError("unknown experiment id: %s" % experiment_id)
    outcome = _prepare_outcome(
        experiment_id=experiment_id,
        observed_direction=observed_direction,
        effect_size=effect_size,
        ci_low=ci_low,
        ci_high=ci_high,
        n=n,
        qc=qc,
        note=note,
        source_id=source_id,
    )
    try:
        store.add_outcome(outcome, commit=True)
    except Exception as exc:
        store.conn.rollback()
        raise LedgerError("could not record outcome: %s" % exc) from exc
    return {"outcome": outcome.to_dict()}


def _outcome_signature(value: Outcome | dict[str, Any]) -> tuple[Any, ...]:
    row = value.to_dict() if isinstance(value, Outcome) else dict(value)
    return tuple(
        row.get(key)
        for key in (
            "observed_direction",
            "effect_size",
            "ci_low",
            "ci_high",
            "n",
            "qc",
            "note",
        )
    )


def _load_outcome_records(path: Path) -> list[tuple[int, dict[str, Any]]]:
    try:
        text = Path(path).read_text(encoding="utf-8-sig")
    except OSError as exc:
        raise LedgerError("could not read outcome file %s: %s" % (path, exc)) from exc
    if not text.strip():
        raise LedgerError("outcome file is empty")

    stripped = text.lstrip()
    if stripped.startswith("["):
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise LedgerError("invalid outcome JSON: %s" % exc) from exc
        if not isinstance(data, list):
            raise LedgerError("outcome JSON must be an array")
        return [(index + 1, item) for index, item in enumerate(data)]
    if stripped.startswith("{"):
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            data = None
        if isinstance(data, dict):
            if "outcomes" in data:
                unknown = set(data) - {"schema", "outcomes"}
                if unknown:
                    raise LedgerError("unknown outcome wrapper field(s): %s" % ", ".join(sorted(unknown)))
                if data.get("schema", OUTCOME_SCHEMA) != OUTCOME_SCHEMA:
                    raise LedgerError("outcome wrapper schema must be %s" % OUTCOME_SCHEMA)
                if not isinstance(data["outcomes"], list):
                    raise LedgerError("outcome wrapper 'outcomes' must be an array")
                return [(index + 1, item) for index, item in enumerate(data["outcomes"])]
            return [(1, data)]

    records: list[tuple[int, dict[str, Any]]] = []
    for line_number, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError as exc:
            raise LedgerError("invalid outcome JSON on line %d: %s" % (line_number, exc)) from exc
        records.append((line_number, item))
    if not records:
        raise LedgerError("outcome file is empty")
    return records


def import_outcomes(store: Store, path: Path, *, dry_run: bool = False) -> dict[str, Any]:
    records = _load_outcome_records(path)
    batch_time = clock.iso()
    prepared: list[Outcome] = []
    skipped = 0
    seen: set[tuple[str, str]] = set()

    for line_number, item in records:
        if not isinstance(item, dict):
            raise LedgerError("outcome record at line %d must be an object" % line_number)
        unknown = set(item) - OUTCOME_KEYS
        if unknown:
            raise LedgerError(
                "unknown outcome field(s) at line %d: %s" % (line_number, ", ".join(sorted(unknown)))
            )
        experiment_id = item.get("experiment_id")
        if not isinstance(experiment_id, str) or not experiment_id.strip():
            raise LedgerError("experiment_id is required at line %d" % line_number)
        experiment_id = experiment_id.strip()
        source_id = item.get("source_id")
        if not isinstance(source_id, str) or not source_id.strip():
            raise LedgerError("source_id is required at line %d" % line_number)
        source_id = source_id.strip()
        key = (experiment_id, source_id)
        if key in seen:
            raise LedgerError(
                "duplicate source_id for experiment %s at line %d" % (experiment_id, line_number)
            )
        seen.add(key)
        row = store.conn.execute(
            "SELECT experiment_id FROM experiments WHERE experiment_id=?", (experiment_id,)
        ).fetchone()
        if row is None:
            raise LedgerError("unknown experiment id at line %d: %s" % (line_number, experiment_id))

        observed = item.get("observed")
        if not isinstance(observed, str) or observed not in OBSERVED:
            raise LedgerError(
                "observed must be one of %s at line %d" % (", ".join(sorted(OBSERVED)), line_number)
            )
        outcome = _prepare_outcome(
            experiment_id=experiment_id,
            observed_direction=observed,
            effect_size=item.get("effect_size"),
            ci_low=item.get("ci_low"),
            ci_high=item.get("ci_high"),
            n=item.get("n"),
            qc=item.get("qc", "pass"),
            note=item.get("note", ""),
            source_id=source_id,
            created_at=batch_time,
        )
        existing = store.conn.execute(
            "SELECT * FROM outcomes WHERE experiment_id=? AND source_id=?",
            (experiment_id, source_id),
        ).fetchone()
        if existing is not None:
            if _outcome_signature(existing) == _outcome_signature(outcome):
                skipped += 1
                continue
            raise LedgerError(
                "source_id %s already exists for experiment %s with different fields; "
                "use a new source_id for a correction" % (source_id, experiment_id)
            )
        prepared.append(outcome)

    if dry_run:
        return {
            "imported": 0,
            "would_import": len(prepared),
            "skipped": skipped,
            "dry_run": True,
        }
    if prepared:
        try:
            for outcome in prepared:
                store.add_outcome(outcome, commit=False)
            store.conn.commit()
        except Exception as exc:
            store.conn.rollback()
            raise LedgerError("outcome import rolled back: %s" % exc) from exc
    return {
        "imported": len(prepared),
        "would_import": len(prepared),
        "skipped": skipped,
        "dry_run": False,
    }


def _experiments(store: Store) -> list[dict[str, Any]]:
    rows = store.conn.execute("SELECT * FROM experiments ORDER BY created_at, experiment_id").fetchall()
    out = []
    for row in rows:
        item = dict(row)
        item["hypothesis_snapshot"] = json.loads(item.get("hypothesis_snapshot") or "{}")
        item["power"] = json.loads(item.pop("power_json", "{}") or "{}")
        out.append(item)
    return out


def _predictions(store: Store) -> list[dict[str, Any]]:
    return [
        dict(row) for row in store.conn.execute("SELECT * FROM predictions ORDER BY experiment_id").fetchall()
    ]


def _outcomes(store: Store) -> list[dict[str, Any]]:
    return [
        dict(row)
        for row in store.conn.execute("SELECT * FROM outcomes ORDER BY created_at, rowid").fetchall()
    ]


def _latest_outcomes(store: Store) -> dict[str, dict[str, Any]]:
    latest: dict[str, dict[str, Any]] = {}
    for row in _outcomes(store):
        latest[row["experiment_id"]] = row
    return latest


def _outcome_history(store: Store) -> dict[str, list[dict[str, Any]]]:
    history: dict[str, list[dict[str, Any]]] = {}
    for row in _outcomes(store):
        history.setdefault(row["experiment_id"], []).append(row)
    return history


def _ci_consistent(outcome: dict[str, Any]) -> bool:
    low = outcome.get("ci_low")
    high = outcome.get("ci_high")
    observed = outcome.get("observed_direction")
    if low is None or high is None:
        return False
    if observed == "increase":
        return bool(low > 0)
    if observed == "decrease":
        return bool(high < 0)
    return False


def _eligible_rows(
    store: Store,
    *,
    outcome: str = "latest",
    qc_warn: bool = True,
    precise_ci: bool = False,
    cluster: str = "hypothesis",
) -> tuple[list[dict[str, Any]], int, int]:
    """Build eligible score rows with an explicit clustering identifier."""

    if outcome not in {"latest", "first"}:
        raise calibration_mod.CalibrationError("outcome selection must be latest or first")
    if cluster not in calibration_mod.CLUSTER_MODES:
        raise calibration_mod.CalibrationError(
            "cluster must be one of: %s" % ", ".join(calibration_mod.CLUSTER_MODES)
        )
    experiments = _experiments(store)
    predictions = {row["experiment_id"]: row for row in _predictions(store)}
    history = _outcome_history(store)
    rows: list[dict[str, Any]] = []
    pending = 0
    excluded = 0
    for experiment in experiments:
        experiment_id = experiment["experiment_id"]
        prediction = predictions.get(experiment_id)
        if prediction is None:
            excluded += 1
            continue
        outcomes = history.get(experiment_id, [])
        if not outcomes:
            pending += 1
            continue
        selected = outcomes[-1] if outcome == "latest" else outcomes[0]
        if selected["qc"] == "fail" or selected["observed_direction"] not in DECISIVE:
            excluded += 1
            continue
        if not qc_warn and selected["qc"] == "warn":
            excluded += 1
            continue
        if precise_ci and not _ci_consistent(selected):
            excluded += 1
            continue
        probability = float(prediction["probability"])
        agree = int(selected["observed_direction"] == prediction["direction"])
        rows.append(
            {
                "experiment_id": experiment_id,
                "hypothesis_id": experiment["hypothesis_id"],
                "cluster": (experiment["hypothesis_id"] if cluster == "hypothesis" else experiment_id),
                "kind": experiment.get("hypothesis_kind") or "unknown",
                "design": experiment.get("design") or "unknown",
                "prediction": prediction["direction"],
                "probability": probability,
                "observed": selected["observed_direction"],
                "agree": agree,
                "brier": (probability - agree) ** 2,
                "qc": selected["qc"],
                "source_id": selected.get("source_id") or "",
                "effect_size": selected.get("effect_size"),
                "ci_low": selected.get("ci_low"),
                "ci_high": selected.get("ci_high"),
                "outcome_id": selected.get("outcome_id") or "",
            }
        )
    return rows, pending, excluded


def _missingness(store: Store) -> list[dict[str, Any]]:
    experiments = _experiments(store)
    predictions = {row["experiment_id"]: row for row in _predictions(store)}
    latest = _latest_outcomes(store)
    grouped: dict[str, dict[str, Any]] = {}
    for experiment in experiments:
        design = experiment.get("design") or "unknown"
        bucket = grouped.setdefault(
            design,
            {"registered": 0, "eligible": 0, "pending": 0, "excluded": 0},
        )
        bucket["registered"] += 1
        outcome = latest.get(experiment["experiment_id"])
        if experiment["experiment_id"] not in predictions:
            bucket["excluded"] += 1
        elif outcome is None:
            bucket["pending"] += 1
        elif outcome["qc"] == "fail" or outcome["observed_direction"] not in DECISIVE:
            bucket["excluded"] += 1
        else:
            bucket["eligible"] += 1
    out: list[dict[str, Any]] = []
    for design, bucket in sorted(grouped.items()):
        item = dict(bucket)
        item["design"] = design
        item["eligible_fraction"] = (
            round(bucket["eligible"] / bucket["registered"], 4) if bucket["registered"] else None
        )
        out.append(item)
    return out


def _corrections(store: Store, eligible: int) -> dict[str, Any]:
    counts: dict[str, int] = {}
    outcomes = _outcomes(store)
    for row in outcomes:
        counts[row["experiment_id"]] = counts.get(row["experiment_id"], 0) + 1
    return {
        "outcomes_total": len(outcomes),
        "experiments_with_multiple_outcomes": sum(1 for count in counts.values() if count > 1),
        "experiments_eligible": eligible,
    }


def status(store: Store) -> dict[str, Any]:
    experiments = _experiments(store)
    latest = _latest_outcomes(store)
    eligible = 0
    excluded = 0
    pending = 0
    for experiment in experiments:
        outcome = latest.get(experiment["experiment_id"])
        if outcome is None:
            pending += 1
        elif outcome["qc"] == "fail" or outcome["observed_direction"] not in DECISIVE:
            excluded += 1
        else:
            eligible += 1
    return {
        "candidates": int(store.conn.execute("SELECT COUNT(*) FROM candidates").fetchone()[0]),
        "experiments": len(experiments),
        "predictions": int(store.conn.execute("SELECT COUNT(*) FROM predictions").fetchone()[0]),
        "outcomes": int(store.conn.execute("SELECT COUNT(*) FROM outcomes").fetchone()[0]),
        "pending": pending,
        "completed": len(experiments) - pending,
        "eligible": eligible,
        "excluded": excluded,
    }


def _score_rows(store: Store) -> tuple[list[dict[str, Any]], int, int]:
    return _eligible_rows(store)


def _group(rows: list[dict[str, Any]], field: str) -> dict[str, Any]:
    groups: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        groups.setdefault(row[field], []).append(row)
    out: dict[str, Any] = {}
    for name, items in sorted(groups.items()):
        n = len(items)
        payload: dict[str, Any] = {"n": n}
        if n >= GROUP_MIN_N:
            payload["brier"] = round(sum(item["brier"] for item in items) / n, 4)
            payload["accuracy"] = round(sum(item["agree"] for item in items) / n, 4)
            payload["descriptive_only"] = True
        out[name] = payload
    return out


def score(store: Store) -> dict[str, Any]:
    rows, pending, excluded = _score_rows(store)
    n = len(rows)
    result: dict[str, Any] = {
        "n": n,
        "pending": pending,
        "excluded": excluded,
        "baseline_brier": BASELINE_BRIER,
        "underpowered": n < UNDERPOWERED_N,
    }
    if n == 0:
        result.update(
            {
                "brier": None,
                "brier_skill": None,
                "accuracy": None,
                "accuracy_ci": None,
                "warning": "no eligible observations; record a decisive outcome first",
                "by_kind": {},
                "by_design": {},
            }
        )
        return result
    brier = sum(row["brier"] for row in rows) / n
    agree = sum(row["agree"] for row in rows)
    low, high = stats.wilson_interval(agree, n)
    warning = ""
    if n < UNDERPOWERED_N:
        warning = "underpowered: n<%d; descriptive only, not evidence of calibration" % UNDERPOWERED_N
    result.update(
        {
            "brier": round(brier, 4),
            "brier_skill": round(1.0 - brier / BASELINE_BRIER, 4),
            "accuracy": round(agree / n, 4),
            "accuracy_ci": [round(low, 4), round(high, 4)],
            "warning": warning,
            "by_kind": _group(rows, "kind"),
            "by_design": _group(rows, "design"),
        }
    )
    return result


def calibration(
    store: Store,
    *,
    bins: int = calibration_mod.DEFAULT_BINS,
    n_boot: int = calibration_mod.DEFAULT_BOOTSTRAP,
    cluster: str = "hypothesis",
) -> dict[str, Any]:
    rows, pending, excluded = _eligible_rows(store, cluster=cluster)
    variants = {
        "first_outcome": _eligible_rows(store, outcome="first", cluster=cluster)[0],
        "qc_pass_only": _eligible_rows(store, qc_warn=False, cluster=cluster)[0],
        "precise_ci_only": _eligible_rows(store, precise_ci=True, cluster=cluster)[0],
    }
    payload = calibration_mod.analyze(
        rows,
        pending=pending,
        excluded=excluded,
        cluster=cluster,
        bins=bins,
        n_boot=n_boot,
        missingness=_missingness(store),
        corrections=_corrections(store, len(rows)),
        sensitivity=variants,
    )
    payload["generated_at"] = clock.iso()
    payload["content_hash"] = calibration_mod.content_hash(payload)
    return payload


def export_ledger(store: Store) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "schema": "ntx-ledger-v3",
        "precursors": precursors_mod.precursor_rows(store),
        "candidates": candidates_mod.candidate_rows(store),
        "lineage": generate_mod.lineage_rows(store),
        "experiments": _experiments(store),
        "predictions": _predictions(store),
        "outcomes": _outcomes(store),
    }
    content_hash = hashlib.sha256(_canonical(payload).encode("utf-8")).hexdigest()
    payload["exported_at"] = clock.iso()
    payload["content_hash"] = content_hash
    return payload


def write_export(store: Store, path: Path) -> dict[str, Any]:
    payload = export_ledger(store)
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return payload


class CheckpointError(ValueError):
    """Invalid checkpoint input or checkpoint file."""


def _is_hash(value: Any) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def _checkpoint_hash(payload: dict[str, Any]) -> str:
    clone = dict(payload)
    clone.pop("content_hash", None)
    return hashlib.sha256(_canonical(clone).encode("utf-8")).hexdigest()


def load_checkpoint(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise CheckpointError("could not read checkpoint %s: %s" % (path, exc)) from exc
    if not isinstance(payload, dict):
        raise CheckpointError("checkpoint must be a JSON object: %s" % path)
    return payload


def checkpoint_issues(checkpoint: dict[str, Any]) -> list[str]:
    issues: list[str] = []
    if checkpoint.get("schema") != CHECKPOINT_SCHEMA:
        issues.append("schema_mismatch")
    sequence = checkpoint.get("sequence")
    if not isinstance(sequence, int) or isinstance(sequence, bool) or sequence < 1:
        issues.append("invalid_sequence")
    previous_hash = checkpoint.get("previous_hash")
    if previous_hash is not None and not _is_hash(previous_hash):
        issues.append("invalid_previous_hash")
    if not _is_hash(checkpoint.get("ledger_hash")):
        issues.append("invalid_ledger_hash")
    if not isinstance(checkpoint.get("created_at"), str) or not checkpoint.get("created_at"):
        issues.append("invalid_created_at")
    content_hash = checkpoint.get("content_hash")
    if not _is_hash(content_hash):
        issues.append("invalid_content_hash")
    elif content_hash != _checkpoint_hash(checkpoint):
        issues.append("content_hash_mismatch")
    return issues


def build_checkpoint(store: Store, previous: dict[str, Any] | None = None) -> dict[str, Any]:
    if previous is not None:
        previous_issues = checkpoint_issues(previous)
        if previous_issues:
            raise CheckpointError("previous checkpoint is invalid: %s" % ", ".join(previous_issues))
    sequence = int(previous["sequence"]) + 1 if previous is not None else 1
    payload: dict[str, Any] = {
        "schema": CHECKPOINT_SCHEMA,
        "sequence": sequence,
        "previous_hash": previous["content_hash"] if previous is not None else None,
        "ledger_hash": export_ledger(store)["content_hash"],
        "created_at": clock.iso(),
    }
    payload["content_hash"] = _checkpoint_hash(payload)
    return payload


def write_checkpoint(
    store: Store,
    path: Path,
    *,
    previous_path: Path | None = None,
) -> dict[str, Any]:
    previous = load_checkpoint(previous_path) if previous_path is not None else None
    checkpoint = build_checkpoint(store, previous)
    target = Path(path)
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("x", encoding="utf-8") as handle:
            handle.write(json.dumps(checkpoint, indent=2, sort_keys=True) + "\n")
    except FileExistsError as exc:
        raise CheckpointError("checkpoint already exists: %s" % target) from exc
    except OSError as exc:
        raise CheckpointError("could not write checkpoint %s: %s" % (target, exc)) from exc
    return checkpoint


def verify_checkpoint_chain(paths: list[Path], store: Store | None = None) -> dict[str, Any]:
    loaded: list[tuple[Path, dict[str, Any]]] = []
    for path in paths:
        loaded.append((Path(path), load_checkpoint(Path(path))))
    if not loaded:
        raise CheckpointError("no checkpoint files supplied")

    def sort_key(item: tuple[Path, dict[str, Any]]) -> int:
        sequence = item[1].get("sequence")
        return sequence if isinstance(sequence, int) and not isinstance(sequence, bool) else 0

    loaded.sort(key=sort_key)
    checks: list[dict[str, Any]] = []
    issues: list[str] = []
    previous_hash: str | None = None
    for index, (path, checkpoint) in enumerate(loaded, start=1):
        check_issues = checkpoint_issues(checkpoint)
        if checkpoint.get("sequence") != index:
            check_issues.append("sequence_gap_or_duplicate")
        if checkpoint.get("previous_hash") != previous_hash:
            check_issues.append("previous_hash_mismatch")
        check = {
            "path": str(path),
            "sequence": checkpoint.get("sequence"),
            "valid": not check_issues,
            "issues": check_issues,
        }
        checks.append(check)
        issues.extend("%s: %s" % (path.name, issue) for issue in check_issues)
        if not check_issues:
            previous_hash = str(checkpoint["content_hash"])

    current_ledger_hash = None
    if store is not None:
        current_ledger_hash = export_ledger(store)["content_hash"]
        if loaded[-1][1].get("ledger_hash") != current_ledger_hash:
            issues.append("ledger_hash_mismatch")
    latest = loaded[-1][1]
    report: dict[str, Any] = {
        "schema": CHECKPOINT_REPORT_SCHEMA,
        "valid": not issues,
        "count": len(loaded),
        "latest_sequence": latest.get("sequence"),
        "ledger_hash": latest.get("ledger_hash"),
        "current_ledger_hash": current_ledger_hash,
        "issues": issues,
        "checks": checks,
    }
    return report


def format_checkpoint(checkpoint: dict[str, Any]) -> str:
    return "\n".join(
        [
            "Ledger checkpoint",
            "=================",
            "",
            "sequence:      %d" % checkpoint["sequence"],
            "ledger hash:   %s" % checkpoint["ledger_hash"],
            "previous hash: %s" % (checkpoint["previous_hash"] or "(genesis)"),
            "created at:    %s" % checkpoint["created_at"],
            "content hash:  %s" % checkpoint["content_hash"],
        ]
    )


def format_checkpoint_report(report: dict[str, Any]) -> str:
    lines = [
        "Ledger checkpoint chain",
        "=======================",
        "",
        "checkpoints: %d" % report["count"],
        "latest:      %s" % report["latest_sequence"],
        "ledger hash: %s" % report["ledger_hash"],
        "valid:       %s" % ("yes" if report["valid"] else "no"),
    ]
    if report["issues"]:
        lines.append("")
        lines.extend("issue: " + issue for issue in report["issues"])
    return "\n".join(lines)


def format_status(payload: dict[str, Any]) -> str:
    return "\n".join(
        [
            "Prediction ledger",
            "================",
            "candidates:   %d" % payload["candidates"],
            "experiments:  %d" % payload["experiments"],
            "predictions:  %d" % payload["predictions"],
            "outcomes:     %d" % payload["outcomes"],
            "pending:      %d" % payload["pending"],
            "completed:    %d" % payload["completed"],
            "eligible:     %d" % payload["eligible"],
            "excluded:     %d" % payload["excluded"],
        ]
    )


def format_score(payload: dict[str, Any]) -> str:
    lines = ["Directional prediction score", "=============================", ""]
    if payload["n"] == 0:
        lines.append("no eligible observations")
        lines.append("pending: %d  excluded: %d" % (payload["pending"], payload["excluded"]))
        lines.append("warning: " + payload["warning"])
        return "\n".join(lines)
    lines.append(
        "eligible: %d  pending: %d  excluded: %d" % (payload["n"], payload["pending"], payload["excluded"])
    )
    lines.append("Brier:    %.4f (baseline %.2f)" % (payload["brier"], payload["baseline_brier"]))
    lines.append("skill:    %.4f" % payload["brier_skill"])
    lines.append(
        "accuracy: %.4f (95%% CI %.4f-%.4f)"
        % (payload["accuracy"], payload["accuracy_ci"][0], payload["accuracy_ci"][1])
    )
    if payload["warning"]:
        lines.append("warning: " + payload["warning"])
    for label, key in (("kind", "by_kind"), ("design", "by_design")):
        if payload[key]:
            lines.append("")
            lines.append("by %s:" % label)
            for name, group in payload[key].items():
                if group.get("brier") is None:
                    lines.append("  %-18s n=%d (too few to describe)" % (name, group["n"]))
                else:
                    lines.append(
                        "  %-18s n=%d Brier=%.4f accuracy=%.4f"
                        % (name, group["n"], group["brier"], group["accuracy"])
                    )
    lines.append("")
    lines.append("note: directional Brier only; exclusions are counted, not imputed")
    return "\n".join(lines)
