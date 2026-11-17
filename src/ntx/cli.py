"""Command-line interface: python -m ntx <command>."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from . import __version__, annotation, calibration_html, clock, config, discovery, log, report, stats
from . import ablate as ablate_mod
from . import audit as audit_mod
from . import calibration as calibration_mod
from . import candidates as candidates_mod
from . import corpus as corpus_mod
from . import dashboard as dashboard_mod
from . import dbadmin as dbadmin_mod
from . import eval as eval_mod
from . import fulltext as fulltext_mod
from . import generate as generate_mod
from . import ingest as ingest_mod
from . import ledger as ledger_mod
from . import manifest as manifest_mod
from . import mechanism as mechanism_mod
from . import power as power_mod
from . import precursors as precursors_mod
from . import quality as quality_mod
from . import serve as serve_mod
from . import settings as settings_mod
from . import synthesis as synthesis_mod
from . import verify as verify_mod
from .build_paths import default_db, demo_corpus
from .extract import FeatureFlags
from .pipeline import load_demo_corpus, process_documents, run_demo, run_ingest
from .store import Store

EXIT_OK = 0
EXIT_INTEGRITY = 1
EXIT_USAGE = 2
EXIT_CONFIG = 3


def _store(path: str) -> Store:
    return Store(Path(path))


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _corpus_hash(corpus: Path | None) -> str:
    if corpus is None:
        return _sha256_bytes(config.read_resource("demo_corpus.json"))
    return _sha256_bytes(corpus.read_bytes())


def cmd_demo(args: argparse.Namespace) -> int:
    db_path = Path(args.db)
    corpus = Path(args.corpus) if args.corpus else demo_corpus()
    if corpus is not None and not corpus.exists():
        print("demo corpus not found: %s" % corpus, file=sys.stderr)
        return 2
    manifest = manifest_mod.build("demo", corpus_hash=_corpus_hash(corpus))
    run_demo(corpus, db_path, manifest=manifest, flags=FeatureFlags(resolve_coref=args.resolve_coref))
    manifest_path = manifest_mod.write(manifest)
    store = Store(db_path)
    try:
        rendered = report.render_all(store, Path(args.out))
        counts = store.counts()
        errors = store.provenance_errors()
    finally:
        store.close()
    print("demo corpus: %s" % (corpus if corpus is not None else "bundled"))
    print("database:    %s" % db_path)
    print("counts:      %s" % json.dumps(counts, sort_keys=True))
    print("provenance:  %d error(s)" % len(errors))
    print("run:         %s (%s)" % (manifest.run_id, manifest_path))
    for entity_id, paths in rendered.items():
        print("  %-12s %s" % (entity_id, paths["html"]))
    return 0


def cmd_ingest(args: argparse.Namespace) -> int:
    store = _store(args.db)
    manifest = manifest_mod.build("ingest", notes="query=%s limit=%d" % (args.query, args.limit))
    try:
        stats = run_ingest(
            store,
            args.query,
            args.limit,
            Path(args.cache),
            offline=args.offline,
            enrich_crossref=args.crossref,
            flags=FeatureFlags(resolve_coref=args.resolve_coref),
        )
        store.add_manifest(manifest)
        print(json.dumps(stats, sort_keys=True))
        print("run: " + manifest.run_id)
        if args.report:
            rendered = report.render_all(store, Path(args.out))
            print(json.dumps({k: v["html"] for k, v in rendered.items()}, indent=2))
    finally:
        store.close()
    manifest_mod.write(manifest)
    return 0


def cmd_report(args: argparse.Namespace) -> int:
    store = _store(args.db)
    try:
        rendered = report.render_all(store, Path(args.out), include_fulltext=args.with_fulltext)
    finally:
        store.close()
    print(json.dumps(rendered, indent=2))
    return 0


def cmd_db_backup(args: argparse.Namespace) -> int:
    try:
        payload = dbadmin_mod.backup(Path(args.db), Path(args.out), force=args.force)
    except (FileNotFoundError, OSError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return EXIT_CONFIG
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print("backup: %s" % args.out)
        print("sha256: %s" % payload["sha256"])
        print("schema: v%d" % payload["schema_version"])
    return EXIT_OK


def cmd_db_verify(args: argparse.Namespace) -> int:
    payload = dbadmin_mod.verify(Path(args.db), Path(args.sidecar) if args.sidecar else None)
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print(dbadmin_mod.format_checks(payload))
    return EXIT_OK if payload["ok"] else EXIT_INTEGRITY


def cmd_db_restore(args: argparse.Namespace) -> int:
    try:
        payload = dbadmin_mod.restore(Path(args.backup), Path(args.db), force=args.force)
    except (FileNotFoundError, FileExistsError, ValueError, OSError) as exc:
        print(str(exc), file=sys.stderr)
        return EXIT_INTEGRITY
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print("restored: %s" % payload["restored"])
        print("schema: v%d" % payload["schema_version"])
    return EXIT_OK


def cmd_doctor(args: argparse.Namespace) -> int:
    payload = dbadmin_mod.doctor(Path(args.db))
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print(dbadmin_mod.format_checks(payload))
    return EXIT_OK if payload["ok"] else EXIT_INTEGRITY


def cmd_quality(args: argparse.Namespace) -> int:
    store = _store(args.db)
    try:
        gold = eval_mod.load_gold(Path(args.gold)) if args.gold else None
        try:
            payload = quality_mod.build_quality(
                store,
                gold,
                min_effect_coverage=args.min_effect_coverage,
                require_gold=args.require_gold,
            )
        except ValueError as exc:
            print(str(exc), file=sys.stderr)
            return EXIT_USAGE
    finally:
        store.close()
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print(quality_mod.format_quality(payload))
    return EXIT_OK if payload["ok"] else EXIT_INTEGRITY


def cmd_dashboard(args: argparse.Namespace) -> int:
    store = _store(args.db)
    try:
        gold = eval_mod.load_gold(Path(args.gold)) if args.gold else None
        target = dashboard_mod.render_dashboard(store, Path(args.out), gold=gold)
    finally:
        store.close()
    print(str(target))
    return EXIT_OK


def cmd_eval(args: argparse.Namespace) -> int:
    store = _store(args.db)
    try:
        gold = eval_mod.load_gold(Path(args.gold))
        if any(annotation.is_redacted(record) for record in gold):
            print(
                "gold is redacted; hydrate it first: ntx gold hydrate --in %s --corpus <local-corpus> --out build/gold.full.jsonl"
                % args.gold,
                file=sys.stderr,
            )
            return EXIT_USAGE
        if args.require_gold:
            non_human = sorted(
                str(record["doc_id"])
                for record in gold
                if str(record.get("status") or "").casefold() != "human"
            )
            if not gold or non_human:
                print(
                    "eval --require-gold: human gold required; %d draft/non-human record(s)%s"
                    % (
                        len(non_human),
                        (": " + ", ".join(non_human[:5])) if non_human else "",
                    ),
                    file=sys.stderr,
                )
                return EXIT_CONFIG
        if args.bootstrap:
            strata: dict[str, str] = {}
            if args.frame:
                frame = json.loads(Path(args.frame).read_text(encoding="utf-8"))
                strata = {doc["doc_id"]: doc.get("stratum", "unknown") for doc in frame.get("documents", [])}
            result = eval_mod.evaluate_with_ci(store, gold, strata or None, n_boot=args.bootstrap)
        else:
            result = eval_mod.evaluate(store, gold)
    finally:
        store.close()
    if args.json:
        print(json.dumps(result, indent=2))
    elif args.bootstrap:
        print(eval_mod.format_ci_report(result))
    else:
        print(eval_mod.format_report(result))
    aggregate = result.get("aggregate", result)
    if args.strict_gold and (aggregate["gold_span_errors"] or aggregate["gold_missing_documents"]):
        print(
            "gold integrity failed: %d bad span(s), %d gold document(s) missing from the database"
            % (aggregate["gold_span_errors"], aggregate["gold_missing_documents"]),
            file=sys.stderr,
        )
        return EXIT_INTEGRITY
    return EXIT_OK


def cmd_check(args: argparse.Namespace) -> int:
    store = _store(args.db)
    try:
        errors = store.provenance_errors()
        fulltext_errors = store.fulltext_provenance_errors()
        counts = store.counts()
    finally:
        store.close()
    if args.json:
        print(
            json.dumps(
                {
                    "counts": counts,
                    "provenance_errors": errors,
                    "fulltext_provenance_errors": fulltext_errors,
                },
                indent=2,
            )
        )
    else:
        print("counts: " + json.dumps(counts, sort_keys=True))
        print("provenance errors: %d" % len(errors))
        print("full-text provenance errors: %d" % len(fulltext_errors))
        if errors:
            print(json.dumps(errors[:10], indent=2))
        if fulltext_errors:
            print(json.dumps(fulltext_errors[:10], indent=2))
    return EXIT_INTEGRITY if errors or fulltext_errors else EXIT_OK


def cmd_fulltext_fetch(args: argparse.Namespace) -> int:
    store = _store(args.db)
    try:
        payload = fulltext_mod.fetch_fulltext(
            store,
            doc_id=args.doc_id or None,
            limit=args.limit,
            cache_dir=Path(args.cache),
            offline=args.offline,
        )
    finally:
        store.close()
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print(fulltext_mod.format_fetch(payload))
    return EXIT_OK


def cmd_fulltext_extract(args: argparse.Namespace) -> int:
    store = _store(args.db)
    try:
        payload = fulltext_mod.extract_fulltext(store, doc_id=args.doc_id or None, limit=args.limit)
    finally:
        store.close()
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print(fulltext_mod.format_extract(payload))
    return EXIT_OK


def cmd_fulltext_report(args: argparse.Namespace) -> int:
    store = _store(args.db)
    try:
        payload = fulltext_mod.build_report(store, entity_id=args.entity or None)
    finally:
        store.close()
    try:
        if args.out:
            target = Path(args.out)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    except OSError as exc:
        print("could not write full-text report: %s" % exc, file=sys.stderr)
        return EXIT_USAGE
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print(fulltext_mod.format_report(payload))
        if args.out:
            print("\nreport: %s" % args.out)
    failed = fulltext_mod.should_fail(payload, args.fail_on)
    if args.fail_on_conflict and payload["counts"].get("direction_conflict", 0):
        failed = True
    return EXIT_INTEGRITY if failed else EXIT_OK


def cmd_verify(args: argparse.Namespace) -> int:
    store = _store(args.db)
    try:
        payload = verify_mod.verify_documents(
            store,
            doc_id=args.doc_id or None,
            limit=args.limit,
            cache_dir=Path(args.cache),
            offline=args.offline,
        )
    except verify_mod.VerificationError as exc:
        print(str(exc), file=sys.stderr)
        return EXIT_USAGE
    finally:
        store.close()
    try:
        if args.out:
            target = Path(args.out)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    except OSError as exc:
        print("could not write verification report: %s" % exc, file=sys.stderr)
        return EXIT_USAGE
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print(verify_mod.format_verification(payload))
    drift = payload["counts"].get("metadata_mismatch", 0) + payload["counts"].get("retraction_drift", 0)
    return EXIT_INTEGRITY if args.fail_on_drift and drift else EXIT_OK


def cmd_config(args: argparse.Namespace) -> int:
    try:
        settings = settings_mod.load_settings(Path(args.config) if args.config else None)
    except settings_mod.SettingsError as exc:
        print(str(exc), file=sys.stderr)
        return EXIT_CONFIG
    print(json.dumps(settings.to_dict(), indent=2, sort_keys=True))
    return EXIT_OK


def cmd_serve(args: argparse.Namespace) -> int:
    if not Path(args.db).exists():
        print("database not found: %s" % args.db, file=sys.stderr)
        return EXIT_INTEGRITY
    print("ntx API (read-only): http://%s:%d/" % (args.host, args.port))
    print("database: %s" % args.db)
    try:
        serve_mod.serve(Path(args.db), host=args.host, port=args.port)
    except KeyboardInterrupt:
        print("stopped", file=sys.stderr)
    except PermissionError as exc:
        print(str(exc), file=sys.stderr)
        return EXIT_CONFIG
    return EXIT_OK


def cmd_discover(args: argparse.Namespace) -> int:
    store = _store(args.db)
    try:
        hypotheses = discovery.generate(store, top=args.top, include_untested=not args.no_untested)
    finally:
        store.close()
    if args.json:
        print(json.dumps([h.to_dict() for h in hypotheses], indent=2))
    else:
        print(discovery.format_report(hypotheses))
    return 0


def cmd_mechanism_edges(args: argparse.Namespace) -> int:
    store = _store(args.db)
    try:
        payload = mechanism_mod.edges_payload(store, min_support=args.min_support)
    finally:
        store.close()
    if args.json:
        print(json.dumps(payload, indent=2))
    else:
        print(mechanism_mod.format_edges(payload))
    return EXIT_OK


def cmd_mechanism_simulate(args: argparse.Namespace) -> int:
    known = {entity["id"] for entity in config.ontology().get("entities", [])}
    if args.entity not in known:
        print("unknown entity id: %s" % args.entity, file=sys.stderr)
        return EXIT_USAGE
    if not 0 < args.min_confidence <= 1:
        print("--min-confidence must be in (0, 1]", file=sys.stderr)
        return EXIT_USAGE
    store = _store(args.db)
    try:
        result = mechanism_mod.simulate(
            store,
            args.entity,
            blocked=args.block or (),
            max_depth=args.max_depth,
            min_confidence=args.min_confidence,
        )
    finally:
        store.close()
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(mechanism_mod.format_simulation(result))
    return EXIT_OK


def cmd_mechanism_check(args: argparse.Namespace) -> int:
    if not 0 <= args.holdout <= 1:
        print("--holdout must be between 0 and 1", file=sys.stderr)
        return EXIT_USAGE
    store = _store(args.db)
    try:
        result = mechanism_mod.check(
            store,
            holdout=args.holdout,
            max_depth=args.max_depth,
            min_confidence=args.min_confidence,
        )
    finally:
        store.close()
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(mechanism_mod.format_check(result))
    return EXIT_OK


def cmd_mechanism_export(args: argparse.Namespace) -> int:
    store = _store(args.db)
    try:
        payload = mechanism_mod.export_graph(store)
    finally:
        store.close()
    if args.out:
        mechanism_mod.write_export(payload, Path(args.out))
        print("wrote %s (%d nodes, %d edges)" % (args.out, len(payload["nodes"]), len(payload["edges"])))
    else:
        print(json.dumps(payload, indent=2))
    return EXIT_OK


def cmd_mechanism_enrich(args: argparse.Namespace) -> int:
    try:
        payload = mechanism_mod.fetch_uniprot(args.uniprot, Path(args.cache), offline=args.offline)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return EXIT_USAGE
    except Exception as exc:  # network, cache and malformed payloads
        print("uniprot enrichment failed: %s" % exc, file=sys.stderr)
        return EXIT_USAGE
    if args.json:
        print(json.dumps(payload, indent=2))
    else:
        print("UniProt %s" % payload["accession"])
        print("protein: %s" % (payload["protein_name"] or "(unknown)"))
        print("gene: %s" % (payload["gene"] or "(unknown)"))
        print("organism: %s" % (payload["organism"] or "(unknown)"))
        if payload["function"]:
            print("function: %s" % payload["function"])
    return EXIT_OK


def cmd_synthesize(args: argparse.Namespace) -> int:
    store = _store(args.db)
    try:
        rows = synthesis_mod.synthesize(
            store,
            n_boot=args.bootstrap,
            seed=args.seed,
            include_comparator=args.include_comparator,
        )
    finally:
        store.close()
    text = synthesis_mod.format_synthesis(rows)
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(text + "\n", encoding="utf-8")
    if args.json:
        print(json.dumps(rows, indent=2))
        return EXIT_OK
    print(text)
    return EXIT_OK


def cmd_ablate(args: argparse.Namespace) -> int:
    docs, annotations = corpus_mod.load_corpus(Path(args.corpus))
    gold = eval_mod.load_gold(Path(args.gold))
    configs = [name.strip() for name in args.configs.split(",") if name.strip()] if args.configs else None
    try:
        result = ablate_mod.run_ablation(
            docs, gold, annotations, configs=configs, n_boot=args.bootstrap, seed=args.seed
        )
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(result, indent=2))
        return EXIT_OK
    print(ablate_mod.format_ablation(result))
    return EXIT_OK


def _load_corpus_texts(path: str) -> dict[str, str]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    texts: dict[str, str] = {}
    for doc in payload.get("documents", []):
        text = (doc.get("title") or "") + "\n\n" + (doc.get("abstract") or "")
        texts[doc["doc_id"]] = text.strip()
    return texts


def _write_jsonl_atomic(records: Sequence[dict[str, Any]], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".tmp")
    try:
        annotation.write_records(records, temp)
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def cmd_gold_redact(args: argparse.Namespace) -> int:
    source, target = Path(args.input), Path(args.out)
    if target.exists() and not args.force:
        print("output exists; pass --force to replace it: %s" % target, file=sys.stderr)
        return EXIT_CONFIG
    records = annotation.load_records(source)
    redacted = [annotation.redact_record(record) for record in records]
    _write_jsonl_atomic(redacted, target)
    print("redacted records: %d -> %s" % (len(redacted), target))
    return EXIT_OK


def cmd_gold_hydrate(args: argparse.Namespace) -> int:
    source, target = Path(args.input), Path(args.out)
    if target.exists() and not args.force:
        print("output exists; pass --force to replace it: %s" % target, file=sys.stderr)
        return EXIT_CONFIG
    records = annotation.load_records(source)
    texts = _load_corpus_texts(args.corpus)
    missing = sorted({str(record.get("doc_id")) for record in records} - set(texts))
    if missing:
        print("corpus is missing document(s): " + ", ".join(missing[:10]), file=sys.stderr)
        return EXIT_CONFIG
    try:
        hydrated = [annotation.hydrate_record(record, texts[str(record["doc_id"])]) for record in records]
    except (KeyError, ValueError) as exc:
        print("hydration failed: %s" % exc, file=sys.stderr)
        return EXIT_USAGE
    _write_jsonl_atomic(hydrated, target)
    print("hydrated records: %d -> %s" % (len(hydrated), target))
    return EXIT_OK


def cmd_gold_validate(args: argparse.Namespace) -> int:
    records = annotation.load_records(Path(args.file))
    texts = _load_corpus_texts(args.corpus) if args.corpus else {}
    problems: list[str] = []
    for record in records:
        text = texts.get(record["doc_id"])
        problems.extend(annotation.validate_record(record, text))
    if args.json:
        print(json.dumps({"records": len(records), "errors": problems}, indent=2))
    else:
        print("records: %d" % len(records))
        print("errors:  %d" % len(problems))
        for problem in problems[:50]:
            print("  - " + problem)
    return 1 if problems else 0


def cmd_gold_agreement(args: argparse.Namespace) -> int:
    left = annotation.load_records(Path(args.a))
    right = annotation.load_records(Path(args.b))
    pairs = annotation.agreement_pairs(left, right, field=args.field)
    result: dict[str, Any] = dict(stats.cohen_kappa(pairs))
    result["field"] = args.field
    result["compared_items"] = len(pairs)
    if args.effects:
        result["effects"] = annotation.effect_agreement(left, right)
    print(json.dumps(result, indent=2))
    return 0


def cmd_gold_merge(args: argparse.Namespace) -> int:
    primary = annotation.load_records(Path(args.primary))
    secondary = annotation.load_records(Path(args.secondary)) if args.secondary else []
    merged = annotation.merge_records(primary, secondary)
    if args.adjudications:
        adjudications = annotation.load_records(Path(args.adjudications))
        merged = annotation.apply_adjudications(merged, adjudications)
    annotation.write_records(merged, Path(args.out))
    print("merged records: %d -> %s" % (len(merged), args.out))
    return 0


def _gold_claim_keys(records: list[dict[str, Any]]) -> dict[str, set[tuple[Any, ...]]]:
    keys: dict[str, set[tuple[Any, ...]]] = {}
    for record in records:
        doc_id = str(record["doc_id"])
        bucket = keys.setdefault(doc_id, set())
        for claim in record.get("claims", []):
            bucket.add(
                (
                    claim["entity_id"],
                    int(claim["start"]),
                    int(claim["end"]),
                    str(claim.get("direction", "")),
                    str(claim.get("outcome", "")),
                    str(claim.get("role", "")),
                )
            )
    return keys


def cmd_gold_progress(args: argparse.Namespace) -> int:
    records = annotation.load_records(Path(args.gold))
    annotated = {str(record["doc_id"]): record for record in records}
    frame_path = Path(args.frame) if args.frame else None
    frame_docs = config.load_json(frame_path).get("documents", []) if frame_path else []
    frame_ids = {str(doc["doc_id"]) for doc in frame_docs}

    by_stratum: dict[str, dict[str, int]] = {}
    for doc in frame_docs:
        stratum = str(doc.get("stratum", "unknown"))
        bucket = by_stratum.setdefault(stratum, {"documents": 0, "annotated": 0, "claims": 0})
        bucket["documents"] += 1
        if str(doc["doc_id"]) in annotated:
            bucket["annotated"] += 1
            bucket["claims"] += len(annotated[str(doc["doc_id"])].get("claims", []))

    in_frame_annotated = sorted(frame_ids & set(annotated))
    missing = sorted(frame_ids - set(annotated))
    outside = sorted(set(annotated) - frame_ids) if frame_ids else []
    annotated_count = len(annotated)
    in_frame_count = len(in_frame_annotated)
    missing_count = len(missing)
    claim_count = sum(len(record.get("claims", [])) for record in records)
    mention_count = sum(len(record.get("mentions", [])) for record in records)
    frame_count = len(frame_ids)
    counts = {
        "frame_documents": frame_count if frame_ids else None,
        "annotated_documents": annotated_count,
        "annotated_in_frame": in_frame_count,
        "missing_documents": missing_count,
        "claims": claim_count,
        "mentions": mention_count,
    }
    if args.json:
        print(
            json.dumps(
                {
                    "counts": counts,
                    "by_stratum": dict(sorted(by_stratum.items())),
                    "missing": missing,
                    "outside_frame": outside,
                },
                indent=2,
                sort_keys=True,
            )
        )
        return EXIT_OK
    print("frame documents:      %s" % (frame_count if frame_ids else "n/a"))
    print("annotated documents:  %d (%d in frame)" % (annotated_count, in_frame_count))
    print("missing documents:    %d" % missing_count)
    print("claims:               %d" % claim_count)
    print("mentions:             %d" % mention_count)
    if by_stratum:
        print("")
        print("| Stratum | Documents | Annotated | Claims |")
        print("| --- | ---: | ---: | ---: |")
        for stratum, bucket in sorted(by_stratum.items()):
            print(
                "| %s | %d | %d | %d |"
                % (stratum, bucket["documents"], bucket["annotated"], bucket["claims"])
            )
    if outside:
        print("\noutside frame: " + ", ".join(outside[:10]))
    return EXIT_OK


def cmd_gold_readiness(args: argparse.Namespace) -> int:
    gold = eval_mod.load_gold(Path(args.gold))
    frame_docs: list[dict[str, Any]] = []
    if args.frame:
        frame_docs = config.load_json(Path(args.frame)).get("documents", [])
    second = eval_mod.load_gold(Path(args.second)) if args.second else []
    payload = eval_mod.gold_readiness(gold, frame_docs, second)
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        counts = payload["counts"]
        print("ready:               %s" % ("yes" if payload["ready"] else "no"))
        print("gold documents:      %d" % counts["gold_documents"])
        print("frame documents:     %s" % counts["frame_documents"])
        print("missing documents:   %d" % counts["missing_documents"])
        print("draft/non-human:     %d" % counts["draft_documents"])
        print("double-annotated:    %d" % counts["double_annotated_documents"])
        print("claims:              %d" % counts["claims"])
        print("effect claims:       %d" % counts["effect_claims"])
        if payload["blockers"]:
            print("blockers:            " + ", ".join(payload["blockers"]))
    return EXIT_OK if payload["ready"] else EXIT_CONFIG


def cmd_gold_diff(args: argparse.Namespace) -> int:
    left = _gold_claim_keys(annotation.load_records(Path(args.a)))
    right = _gold_claim_keys(annotation.load_records(Path(args.b)))
    docs = sorted(set(left) | set(right))
    only_left = {doc: sorted(left.get(doc, set()) - right.get(doc, set())) for doc in docs}
    only_right = {doc: sorted(right.get(doc, set()) - left.get(doc, set())) for doc in docs}
    only_left = {doc: rows for doc, rows in only_left.items() if rows}
    only_right = {doc: rows for doc, rows in only_right.items() if rows}
    documents_a = len(left)
    documents_b = len(right)
    only_a_count = sum(len(rows) for rows in only_left.values())
    only_b_count = sum(len(rows) for rows in only_right.values())
    payload: dict[str, Any] = {
        "documents_a": documents_a,
        "documents_b": documents_b,
        "claims_only_in_a": only_a_count,
        "claims_only_in_b": only_b_count,
        "only_in_a": only_left,
        "only_in_b": only_right,
    }
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
        return EXIT_OK
    print("documents: A=%d B=%d" % (documents_a, documents_b))
    print("claims only in A: %d" % only_a_count)
    print("claims only in B: %d" % only_b_count)
    for label, rows in (("A", only_left), ("B", only_right)):
        for doc, claims in rows.items():
            for claim in claims:
                print("  %s only | %s | %s" % (label, doc, claim))
    return EXIT_OK


def cmd_gold_conflicts(args: argparse.Namespace) -> int:
    fields = [field.strip() for field in args.fields.split(",") if field.strip()]
    unknown = sorted(set(fields) - {"direction", "outcome", "role", "design", "effect_type"})
    if unknown:
        print("unknown field(s): " + ", ".join(unknown), file=sys.stderr)
        return EXIT_USAGE
    defaults = {"direction": "unclear", "outcome": "other", "role": "unknown", "design": "unknown"}
    left = _gold_claim_keys(annotation.load_records(Path(args.a)))
    right = _gold_claim_keys(annotation.load_records(Path(args.b)))

    def field_map(path: Path) -> dict[tuple[Any, ...], dict[str, set[str]]]:
        mapped: dict[tuple[Any, ...], dict[str, set[str]]] = {}
        for record in annotation.load_records(path):
            for claim in record.get("claims", []):
                key = (record["doc_id"], claim["entity_id"], int(claim["start"]))
                bucket = mapped.setdefault(key, {field: set() for field in fields})
                for field in fields:
                    bucket[field].add(str(claim.get(field) or defaults[field]))
        return mapped

    left_fields = field_map(Path(args.a))
    right_fields = field_map(Path(args.b))
    rows: list[dict[str, Any]] = []
    for key in sorted(set(left_fields) & set(right_fields)):
        for field in fields:
            a_values = left_fields[key][field]
            b_values = right_fields[key][field]
            if a_values == b_values:
                continue
            rows.append(
                {
                    "doc_id": key[0],
                    "entity_id": key[1],
                    "start": key[2],
                    "field": field,
                    "value": "",
                    "value_a": "+".join(sorted(a_values)),
                    "value_b": "+".join(sorted(b_values)),
                }
            )
    by_field: dict[str, int] = {}
    for row in rows:
        by_field[row["field"]] = by_field.get(row["field"], 0) + 1
    if args.out:
        target = Path(args.out)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            "".join(json.dumps(row, sort_keys=True) + "\n" for row in rows),
            encoding="utf-8",
            newline="\n",
        )
    payload: dict[str, Any] = {
        "documents_a": len(left),
        "documents_b": len(right),
        "conflicts": len(rows),
        "by_field": dict(sorted(by_field.items())),
        "rows": rows,
    }
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
        return EXIT_OK
    print("documents: A=%d B=%d" % (payload["documents_a"], payload["documents_b"]))
    print("field conflicts: %d" % payload["conflicts"])
    for field, count in sorted(by_field.items()):
        print("  %-10s %d" % (field, count))
    if args.out:
        print("wrote %s (fill 'value' and pass to gold merge --adjudications)" % args.out)
    return EXIT_OK


def cmd_audit_sample(args: argparse.Namespace) -> int:
    store = _store(args.db)
    try:
        rows = audit_mod.sample_claims(store, n=args.n, seed=args.seed, effects_only=args.effects_only)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return EXIT_USAGE
    finally:
        store.close()
    text = audit_mod.format_worksheet(rows)
    if args.out:
        target = Path(args.out)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    if args.verdicts_out:
        target = Path(args.verdicts_out)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(audit_mod.format_verdict_template(rows), encoding="utf-8", newline="\n")
    if args.json:
        print(json.dumps(rows, indent=2))
        return EXIT_OK
    strata = (
        {(row["effect_type"], row["role"], row["design"]) for row in rows}
        if args.effects_only
        else {(row["role"], row["design"]) for row in rows}
    )
    print("sampled claims: %d across %d role/design strata" % (len(rows), len(strata)))
    if args.out:
        print("worksheet: " + args.out)
    if args.verdicts_out:
        print("verdict template: " + args.verdicts_out)
    return EXIT_OK


def cmd_audit_score(args: argparse.Namespace) -> int:
    try:
        verdicts = audit_mod.load_verdicts(Path(args.verdicts))
    except (OSError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return EXIT_USAGE
    store = _store(args.db)
    try:
        result = audit_mod.score_verdicts(store, verdicts)
    finally:
        store.close()
    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True))
        return EXIT_OK
    print(audit_mod.format_audit_score(result))
    return EXIT_OK


def cmd_from_fixture(args: argparse.Namespace) -> int:
    """Build a database from a local corpus file without any network access."""
    corpus = load_demo_corpus(Path(args.corpus))
    docs = [ingest_mod.doc_from_epmc(r) for r in corpus.results]
    docs = [d for d in docs if d.title]
    db_path = Path(args.db)
    if db_path.exists():
        db_path.unlink()
    manifest = manifest_mod.build("from-fixture", corpus_hash=_corpus_hash(Path(args.corpus)))
    store = Store(db_path)
    try:
        stats = process_documents(
            store, docs, corpus.annotations, flags=FeatureFlags(resolve_coref=args.resolve_coref)
        )
        store.add_manifest(manifest)
    finally:
        store.close()
    manifest_mod.write(manifest)
    print(json.dumps(stats, sort_keys=True))
    print("run: " + manifest.run_id)
    return 0


def cmd_candidate_import(args: argparse.Namespace) -> int:
    store = _store(args.db)
    try:
        result = candidates_mod.import_candidates(store, Path(args.file), dry_run=args.dry_run)
    except (candidates_mod.CandidateError, OSError) as exc:
        print(str(exc), file=sys.stderr)
        return EXIT_USAGE
    finally:
        store.close()
    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        print(
            "%s %d candidate(s) from %s"
            % ("validated" if result["dry_run"] else "imported", result["imported"], args.file)
        )
        for candidate in result["candidates"]:
            print("  %s  %s" % (candidate["candidate_id"], candidate["name"]))
    return EXIT_OK


def cmd_candidate_list(args: argparse.Namespace) -> int:
    store = _store(args.db)
    try:
        candidates = candidates_mod.candidate_rows(store)
    finally:
        store.close()
    if args.json:
        print(json.dumps({"candidates": candidates}, indent=2, sort_keys=True))
    else:
        print(candidates_mod.format_candidates(candidates))
    return EXIT_OK


def cmd_candidate_show(args: argparse.Namespace) -> int:
    store = _store(args.db)
    try:
        candidate = candidates_mod.get_candidate(store, args.id)
    finally:
        store.close()
    if candidate is None:
        print("candidate not found: %s" % args.id, file=sys.stderr)
        return EXIT_USAGE
    if args.json:
        print(json.dumps(candidate, indent=2, sort_keys=True))
    else:
        print(candidates_mod.format_candidate(candidate))
    return EXIT_OK


def cmd_candidate_lineage(args: argparse.Namespace) -> int:
    store = _store(args.db)
    try:
        candidate = candidates_mod.get_candidate(store, args.id)
        rows = generate_mod.lineage_rows(store, args.id) if candidate is not None else []
    finally:
        store.close()
    if candidate is None:
        print("candidate not found: %s" % args.id, file=sys.stderr)
        return EXIT_USAGE
    if args.json:
        print(json.dumps({"candidate_id": args.id, "lineage": rows}, indent=2, sort_keys=True))
    else:
        print(generate_mod.format_lineage(args.id, rows))
    return EXIT_OK


def cmd_precursor_import(args: argparse.Namespace) -> int:
    store = _store(args.db)
    try:
        result = precursors_mod.import_precursors(store, Path(args.file), dry_run=args.dry_run)
    except (precursors_mod.PrecursorError, OSError) as exc:
        print(str(exc), file=sys.stderr)
        return EXIT_USAGE
    finally:
        store.close()
    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        print(
            "%s %d precursor(s) from %s"
            % ("validated" if result["dry_run"] else "imported", result["imported"], args.file)
        )
        for precursor in result["precursors"]:
            print("  %s  %s" % (precursor["precursor_id"], precursor["name"]))
    return EXIT_OK


def cmd_precursor_list(args: argparse.Namespace) -> int:
    store = _store(args.db)
    try:
        precursors = precursors_mod.precursor_rows(store)
    finally:
        store.close()
    if args.json:
        print(json.dumps({"precursors": precursors}, indent=2, sort_keys=True))
    else:
        print(precursors_mod.format_precursors(precursors))
    return EXIT_OK


def cmd_precursor_show(args: argparse.Namespace) -> int:
    store = _store(args.db)
    try:
        precursor = precursors_mod.get_precursor(store, args.id)
    finally:
        store.close()
    if precursor is None:
        print("precursor not found: %s" % args.id, file=sys.stderr)
        return EXIT_USAGE
    if args.json:
        print(json.dumps(precursor, indent=2, sort_keys=True))
    else:
        print(precursors_mod.format_precursor(precursor))
    return EXIT_OK


def cmd_generate_cleavage(args: argparse.Namespace) -> int:
    store = _store(args.db)
    try:
        result = generate_mod.generate_cleavage(
            store,
            precursor_id=args.precursor,
            enzyme=args.enzyme,
            missed_cleavages=args.missed,
            min_length=args.min_length,
            max_length=args.max_length,
            max_candidates=args.max_candidates,
            dry_run=args.dry_run,
        )
    except generate_mod.GenerationError as exc:
        print(str(exc), file=sys.stderr)
        return EXIT_USAGE
    finally:
        store.close()
    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        print(
            "cleavage: precursor=%s enzyme=%s missed=%d"
            % (result["precursor_id"], result["enzyme"], result["missed_cleavages"])
        )
        print(
            "considered: %d  generated: %d  existing: %d%s"
            % (
                result["considered"],
                result["generated"],
                result["existing"],
                "  (dry run)" if result["dry_run"] else "",
            )
        )
        for candidate in result["candidates"]:
            print("  %s  %-32s %s" % (candidate["candidate_id"], candidate["name"], candidate["sequence"]))
    return EXIT_OK


def cmd_generate_analogs(args: argparse.Namespace) -> int:
    store = _store(args.db)
    try:
        result = generate_mod.generate_analogs(
            store,
            candidate_id=args.candidate,
            truncations=args.truncations,
            alanine_scan=args.alanine_scan,
            max_variants=args.max_variants,
            dry_run=args.dry_run,
        )
    except generate_mod.GenerationError as exc:
        print(str(exc), file=sys.stderr)
        return EXIT_USAGE
    finally:
        store.close()
    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        print(
            "analogs: parent=%s truncations=%d alanine_scan=%s"
            % (result["parent_id"], result["truncations"], result["alanine_scan"])
        )
        print(
            "considered: %d  generated: %d  existing: %d%s"
            % (
                result["considered"],
                result["generated"],
                result["existing"],
                "  (dry run)" if result["dry_run"] else "",
            )
        )
        for candidate in result["candidates"]:
            print("  %s  %-32s %s" % (candidate["candidate_id"], candidate["name"], candidate["sequence"]))
    return EXIT_OK


def cmd_power(args: argparse.Namespace) -> int:
    try:
        payload = power_mod.calculate(
            args.test,
            effect_size=args.effect_size,
            p1=args.p1,
            p2=args.p2,
            alpha=args.alpha,
            target_power=args.target_power,
            dropout=args.dropout,
            sided=args.sided,
        )
    except power_mod.PowerError as exc:
        print(str(exc), file=sys.stderr)
        return EXIT_USAGE
    if args.out:
        target = Path(args.out)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print("wrote %s" % args.out)
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    elif not args.out:
        print(power_mod.format_power(payload))
    return EXIT_OK


def cmd_ledger_register(args: argparse.Namespace) -> int:
    store = _store(args.db)
    try:
        if args.file:
            if (
                args.power
                or args.hypothesis
                or args.design
                or args.assay
                or args.primary_outcome
                or args.prediction is not None
                or args.probability is not None
                or args.candidate
                or args.n_planned is not None
                or args.preregistration
                or args.rationale
            ):
                print("--file cannot be combined with registration flags", file=sys.stderr)
                return EXIT_USAGE
            result = ledger_mod.register_from_file(store, Path(args.file), dry_run=args.dry_run)
        else:
            missing = [
                name
                for name, value in (
                    ("--hypothesis", args.hypothesis),
                    ("--design", args.design),
                    ("--assay", args.assay),
                    ("--primary-outcome", args.primary_outcome),
                    ("--prediction", args.prediction),
                    ("--probability", args.probability),
                )
                if not value
            ]
            if missing:
                print("missing required arguments: %s" % ", ".join(missing), file=sys.stderr)
                return EXIT_USAGE
            power = ledger_mod.load_power_file(Path(args.power)) if args.power else None
            result = ledger_mod.register(
                store,
                hypothesis_id=args.hypothesis,
                candidate_id=args.candidate,
                design=args.design,
                assay=args.assay,
                primary_outcome=args.primary_outcome,
                prediction=args.prediction,
                probability=args.probability,
                n_planned=args.n_planned or 0,
                preregistration=args.preregistration,
                rationale=args.rationale,
                power=power,
                dry_run=args.dry_run,
            )
    except ledger_mod.LedgerError as exc:
        print(str(exc), file=sys.stderr)
        return EXIT_USAGE
    finally:
        store.close()
    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        print("experiment: %s" % result["experiment"]["experiment_id"])
        print(
            "prediction: %s %s p=%.2f"
            % (
                result["prediction"]["prediction_id"],
                result["prediction"]["direction"],
                result["prediction"]["probability"],
            )
        )
        print(
            "hypothesis: %s (%s)"
            % (
                result["experiment"]["hypothesis_id"],
                result["experiment"]["hypothesis_kind"],
            )
        )
        check = result["power_check"]
        if check["provided"]:
            suffix = ""
            if check["meets_required"] is not None:
                suffix = " (meets)" if check["meets_required"] else " (shortfall, not a block)"
            print(
                "power: required total %d; planned %d%s"
                % (check["required_total"], check["n_planned"], suffix)
            )
        else:
            print("power: not supplied; n_planned is recorded only")
        if result["dry_run"]:
            print("dry run: no rows written")
    return EXIT_OK


def cmd_ledger_outcome(args: argparse.Namespace) -> int:
    store = _store(args.db)
    try:
        result = ledger_mod.record_outcome(
            store,
            experiment_id=args.experiment,
            observed_direction=args.observed,
            effect_size=args.effect_size,
            ci_low=args.ci_low,
            ci_high=args.ci_high,
            n=args.n,
            qc=args.qc,
            note=args.note,
            source_id=args.source_id,
        )
    except ledger_mod.LedgerError as exc:
        print(str(exc), file=sys.stderr)
        return EXIT_USAGE
    finally:
        store.close()
    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        print(
            "outcome: %s observed=%s qc=%s"
            % (
                result["outcome"]["outcome_id"],
                result["outcome"]["observed_direction"],
                result["outcome"]["qc"],
            )
        )
    return EXIT_OK


def cmd_ledger_import_outcomes(args: argparse.Namespace) -> int:
    store = _store(args.db)
    try:
        result = ledger_mod.import_outcomes(store, Path(args.file), dry_run=args.dry_run)
    except ledger_mod.LedgerError as exc:
        print(str(exc), file=sys.stderr)
        return EXIT_USAGE
    finally:
        store.close()
    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        label = "would import" if result["dry_run"] else "imported"
        print(
            "%s: %d  skipped: %d%s"
            % (
                label,
                result["would_import"] if result["dry_run"] else result["imported"],
                result["skipped"],
                "  (dry run; no rows written)" if result["dry_run"] else "",
            )
        )
    return EXIT_OK


def cmd_ledger_status(args: argparse.Namespace) -> int:
    store = _store(args.db)
    try:
        payload = ledger_mod.status(store)
    finally:
        store.close()
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print(ledger_mod.format_status(payload))
    return EXIT_OK


def cmd_ledger_score(args: argparse.Namespace) -> int:
    store = _store(args.db)
    try:
        payload = ledger_mod.score(store)
    finally:
        store.close()
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print(ledger_mod.format_score(payload))
    return EXIT_OK


def cmd_ledger_calibration(args: argparse.Namespace) -> int:
    store = _store(args.db)
    try:
        payload = ledger_mod.calibration(store, bins=args.bins, n_boot=args.bootstrap, cluster=args.cluster)
    except calibration_mod.CalibrationError as exc:
        print(str(exc), file=sys.stderr)
        return EXIT_USAGE
    finally:
        store.close()
    try:
        if args.out:
            target = Path(args.out)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        if args.html:
            target = Path(args.html)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(calibration_html.render_html(payload) + "\n", encoding="utf-8")
    except OSError as exc:
        print("could not write calibration report: %s" % exc, file=sys.stderr)
        return EXIT_USAGE
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print(calibration_mod.format_calibration(payload))
    return EXIT_OK


def cmd_ledger_export(args: argparse.Namespace) -> int:
    store = _store(args.db)
    try:
        if args.out:
            payload = ledger_mod.write_export(store, Path(args.out))
        else:
            payload = ledger_mod.export_ledger(store)
    finally:
        store.close()
    if args.out:
        print("wrote %s" % args.out)
        print("content hash: %s" % payload["content_hash"])
    else:
        print(json.dumps(payload, indent=2, sort_keys=True))
    return EXIT_OK


def cmd_ledger_checkpoint(args: argparse.Namespace) -> int:
    store = _store(args.db)
    try:
        payload = ledger_mod.write_checkpoint(
            store,
            Path(args.out),
            previous_path=Path(args.previous) if args.previous else None,
        )
    except ledger_mod.CheckpointError as exc:
        print(str(exc), file=sys.stderr)
        return EXIT_USAGE
    finally:
        store.close()
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print(ledger_mod.format_checkpoint(payload))
    return EXIT_OK


def cmd_ledger_verify_checkpoints(args: argparse.Namespace) -> int:
    directory = Path(args.dir)
    paths = sorted(directory.glob("*.json")) if directory.is_dir() else []
    store = _store(args.db)
    try:
        report = ledger_mod.verify_checkpoint_chain(paths, store=store)
    except ledger_mod.CheckpointError as exc:
        print(str(exc), file=sys.stderr)
        return EXIT_USAGE
    finally:
        store.close()
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(ledger_mod.format_checkpoint_report(report))
    return EXIT_OK if report["valid"] else EXIT_INTEGRITY


def build_parser(defaults: dict[str, object] | None = None) -> argparse.ArgumentParser:
    d = defaults or {}
    parser = argparse.ArgumentParser(
        prog="ntx", description="Provenance-first neuropeptide evidence surveillance"
    )
    parser.add_argument("--version", action="version", version="ntx " + __version__)
    parser.add_argument(
        "--frozen-time",
        action="store_true",
        help="freeze all timestamps to the epoch for a byte-identical run "
        "(set NTX_FROZEN_TIME to an ISO instant for a specific timestamp)",
    )
    parser.add_argument("--log-level", default=None, help="DEBUG, INFO, WARNING, ERROR")
    parser.add_argument("--config", default="", help="path to ntx.toml")
    sub = parser.add_subparsers(dest="command", required=True)

    demo = sub.add_parser("demo", help="build the offline demo database and reports")
    demo.add_argument("--corpus", default="")
    demo.add_argument("--db", default=d.get("db", str(default_db())))
    demo.add_argument("--out", default=d.get("out", "reports"))
    demo.add_argument(
        "--resolve-coref",
        action="store_true",
        default=d.get("resolve_coref", False),
        help="experimental: attribute definite anaphora to the single prior entity",
    )
    demo.set_defaults(func=cmd_demo)

    ing = sub.add_parser("ingest", help="fetch records from Europe PMC and process them")
    ing.add_argument("--query", required=True)
    ing.add_argument("--limit", type=int, default=50)
    ing.add_argument("--db", default=d.get("db", str(default_db())))
    ing.add_argument("--cache", default=d.get("cache", "data/cache"))
    ing.add_argument("--offline", action="store_true")
    ing.add_argument("--crossref", action="store_true")
    ing.add_argument("--report", action="store_true")
    ing.add_argument("--out", default=d.get("out", "reports"))
    ing.add_argument(
        "--resolve-coref",
        action="store_true",
        default=d.get("resolve_coref", False),
        help="experimental: attribute definite anaphora to the single prior entity",
    )
    ing.set_defaults(func=cmd_ingest)

    rep = sub.add_parser("report", help="render briefs from an existing database")
    rep.add_argument("--db", default=d.get("db", str(default_db())))
    rep.add_argument("--out", default=d.get("out", "reports"))
    rep.add_argument(
        "--with-fulltext",
        action="store_true",
        help="add an abstract/full-text reconciliation section when snapshots exist",
    )
    rep.set_defaults(func=cmd_report)
    qual = sub.add_parser("quality", help="machine-readable quality gates")
    qual.add_argument("--db", default=d.get("db", str(default_db())))
    qual.add_argument("--gold", default="", help="optional gold JSONL; enables the human-gold gate")
    qual.add_argument(
        "--min-effect-coverage",
        type=float,
        default=0.2,
        help="warn when fewer claims carry an explicit effect (default: 0.2)",
    )
    qual.add_argument(
        "--require-gold", action="store_true", help="fail unless a ready human gold is supplied"
    )
    qual.add_argument("--json", action="store_true")
    qual.set_defaults(func=cmd_quality)
    dash = sub.add_parser("dashboard", help="write a self-contained local HTML dashboard")
    dash.add_argument("--db", default=d.get("db", str(default_db())))
    dash.add_argument("--out", default="build/dashboard")
    dash.add_argument("--gold", default="", help="optional gold JSONL")
    dash.set_defaults(func=cmd_dashboard)
    doc = sub.add_parser("doctor", help="run database integrity, schema and provenance checks")
    doc.add_argument("--db", default=d.get("db", str(default_db())))
    doc.add_argument("--json", action="store_true")
    doc.set_defaults(func=cmd_doctor)
    dbp = sub.add_parser("db", help="backup, verify and restore databases")
    db_sub = dbp.add_subparsers(dest="db_command", required=True)
    bkp = db_sub.add_parser("backup", help="copy a database and write a sidecar manifest")
    bkp.add_argument("--db", default=d.get("db", str(default_db())))
    bkp.add_argument("--out", required=True)
    bkp.add_argument("--force", action="store_true", help="replace an existing backup and sidecar")
    bkp.add_argument("--json", action="store_true")
    bkp.set_defaults(func=cmd_db_backup)
    vfy = db_sub.add_parser("verify", help="verify a database against its sidecar")
    vfy.add_argument("--db", required=True)
    vfy.add_argument("--sidecar", default="")
    vfy.add_argument("--json", action="store_true")
    vfy.set_defaults(func=cmd_db_verify)
    rst = db_sub.add_parser("restore", help="restore a verified database backup")
    rst.add_argument("--backup", required=True)
    rst.add_argument("--db", required=True)
    rst.add_argument("--force", action="store_true", help="replace an existing destination")
    rst.add_argument("--json", action="store_true")
    rst.set_defaults(func=cmd_db_restore)

    cand = sub.add_parser("candidate", help="import and inspect candidate peptides")
    cand_sub = cand.add_subparsers(dest="candidate_command", required=True)

    cand_import = cand_sub.add_parser("import", help="validate and import candidate JSONL/JSON")
    cand_import.add_argument("--db", default=d.get("db", str(default_db())))
    cand_import.add_argument("--file", required=True)
    cand_import.add_argument("--dry-run", action="store_true")
    cand_import.add_argument("--json", action="store_true")
    cand_import.set_defaults(func=cmd_candidate_import)

    cand_list = cand_sub.add_parser("list", help="list imported candidates")
    cand_list.add_argument("--db", default=d.get("db", str(default_db())))
    cand_list.add_argument("--json", action="store_true")
    cand_list.set_defaults(func=cmd_candidate_list)

    cand_show = cand_sub.add_parser("show", help="show one candidate and its descriptors")
    cand_show.add_argument("--db", default=d.get("db", str(default_db())))
    cand_show.add_argument("--id", required=True)
    cand_show.add_argument("--json", action="store_true")
    cand_show.set_defaults(func=cmd_candidate_show)

    cand_lineage = cand_sub.add_parser("lineage", help="show why and from where a candidate was generated")
    cand_lineage.add_argument("--db", default=d.get("db", str(default_db())))
    cand_lineage.add_argument("--id", required=True)
    cand_lineage.add_argument("--json", action="store_true")
    cand_lineage.set_defaults(func=cmd_candidate_lineage)

    pre = sub.add_parser("precursor", help="import and inspect precursor protein sequences")
    pre_sub = pre.add_subparsers(dest="precursor_command", required=True)

    pre_import = pre_sub.add_parser("import", help="validate and import precursor JSONL/JSON")
    pre_import.add_argument("--db", default=d.get("db", str(default_db())))
    pre_import.add_argument("--file", required=True)
    pre_import.add_argument("--dry-run", action="store_true")
    pre_import.add_argument("--json", action="store_true")
    pre_import.set_defaults(func=cmd_precursor_import)

    pre_list = pre_sub.add_parser("list", help="list imported precursors")
    pre_list.add_argument("--db", default=d.get("db", str(default_db())))
    pre_list.add_argument("--json", action="store_true")
    pre_list.set_defaults(func=cmd_precursor_list)

    pre_show = pre_sub.add_parser("show", help="show one precursor")
    pre_show.add_argument("--db", default=d.get("db", str(default_db())))
    pre_show.add_argument("--id", required=True)
    pre_show.add_argument("--json", action="store_true")
    pre_show.set_defaults(func=cmd_precursor_show)

    gen = sub.add_parser("generate", help="generate peptides by explicit cleavage or analog rules")
    gen_sub = gen.add_subparsers(dest="generate_command", required=True)

    gen_cleavage = gen_sub.add_parser("cleavage", help="digest a precursor with a named protease rule")
    gen_cleavage.add_argument("--db", default=d.get("db", str(default_db())))
    gen_cleavage.add_argument("--precursor", required=True)
    gen_cleavage.add_argument("--enzyme", default="trypsin", choices=sorted(generate_mod.ENZYME_RULES))
    gen_cleavage.add_argument("--missed", type=int, default=0)
    gen_cleavage.add_argument("--min-length", type=int, default=2)
    gen_cleavage.add_argument("--max-length", type=int, default=30)
    gen_cleavage.add_argument("--max-candidates", type=int, default=generate_mod.MAX_GENERATED)
    gen_cleavage.add_argument("--dry-run", action="store_true")
    gen_cleavage.add_argument("--json", action="store_true")
    gen_cleavage.set_defaults(func=cmd_generate_cleavage)

    gen_analogs = gen_sub.add_parser("analogs", help="truncate or alanine-scan one candidate")
    gen_analogs.add_argument("--db", default=d.get("db", str(default_db())))
    gen_analogs.add_argument("--candidate", required=True)
    gen_analogs.add_argument("--truncations", type=int, default=5)
    gen_analogs.add_argument("--no-alanine-scan", dest="alanine_scan", action="store_false")
    gen_analogs.add_argument("--max-variants", type=int, default=generate_mod.MAX_ANALOGS)
    gen_analogs.add_argument("--dry-run", action="store_true")
    gen_analogs.add_argument("--json", action="store_true")
    gen_analogs.set_defaults(func=cmd_generate_analogs)

    power = sub.add_parser("power", help="compute a two-arm sample-size worksheet")
    power.add_argument("--test", required=True, choices=["mean", "proportion"])
    power.add_argument("--effect-size", type=float, default=None, help="Cohen's d for test=mean")
    power.add_argument("--p1", type=float, default=None, help="first event proportion")
    power.add_argument("--p2", type=float, default=None, help="second event proportion")
    power.add_argument("--alpha", type=float, default=0.05)
    power.add_argument("--target-power", type=float, default=0.8)
    power.add_argument("--dropout", type=float, default=0.0)
    power.add_argument("--sided", choices=["two", "one"], default="two")
    power.add_argument("--out", default="")
    power.add_argument("--json", action="store_true")
    power.set_defaults(func=cmd_power)

    led = sub.add_parser("ledger", help="pre-register predictions and record outcomes")
    led_sub = led.add_subparsers(dest="ledger_command", required=True)

    led_reg = led_sub.add_parser("register", help="register an experiment and one directional prediction")
    led_reg.add_argument("--db", default=d.get("db", str(default_db())))
    led_reg.add_argument("--hypothesis", default="")
    led_reg.add_argument("--candidate", default="")
    led_reg.add_argument("--design", default="")
    led_reg.add_argument("--assay", default="")
    led_reg.add_argument("--primary-outcome", default="")
    led_reg.add_argument("--prediction", default=None, choices=["increase", "decrease"])
    led_reg.add_argument("--probability", default=None, type=float)
    led_reg.add_argument("--n-planned", type=int, default=None)
    led_reg.add_argument("--preregistration", default="")
    led_reg.add_argument("--rationale", default="")
    led_reg.add_argument("--file", default="", help="versioned registration manifest")
    led_reg.add_argument("--power", default="", help="power worksheet JSON")
    led_reg.add_argument("--dry-run", action="store_true")
    led_reg.add_argument("--json", action="store_true")
    led_reg.set_defaults(func=cmd_ledger_register)

    led_out = led_sub.add_parser("outcome", help="append an outcome for a registered experiment")
    led_out.add_argument("--db", default=d.get("db", str(default_db())))
    led_out.add_argument("--experiment", required=True)
    led_out.add_argument(
        "--observed", required=True, choices=["increase", "decrease", "none", "mixed", "unclear"]
    )
    led_out.add_argument("--effect-size", type=float, default=None)
    led_out.add_argument("--ci-low", type=float, default=None)
    led_out.add_argument("--ci-high", type=float, default=None)
    led_out.add_argument("--n", type=int, default=None)
    led_out.add_argument("--qc", default="pass", choices=["pass", "warn", "fail"])
    led_out.add_argument("--note", default="")
    led_out.add_argument("--source-id", default="")
    led_out.add_argument("--json", action="store_true")
    led_out.set_defaults(func=cmd_ledger_outcome)

    led_import = led_sub.add_parser("import-outcomes", help="append validated external outcome records")
    led_import.add_argument("--db", default=d.get("db", str(default_db())))
    led_import.add_argument("--file", required=True)
    led_import.add_argument("--dry-run", action="store_true")
    led_import.add_argument("--json", action="store_true")
    led_import.set_defaults(func=cmd_ledger_import_outcomes)

    led_status = led_sub.add_parser("status", help="show ledger progress without scoring")
    led_status.add_argument("--db", default=d.get("db", str(default_db())))
    led_status.add_argument("--json", action="store_true")
    led_status.set_defaults(func=cmd_ledger_status)

    led_score = led_sub.add_parser("score", help="directional Brier score over decisive outcomes")
    led_score.add_argument("--db", default=d.get("db", str(default_db())))
    led_score.add_argument("--json", action="store_true")
    led_score.set_defaults(func=cmd_ledger_score)

    led_cal = led_sub.add_parser("calibration", help="descriptive reliability table for scored outcomes")
    led_cal.add_argument("--db", default=d.get("db", str(default_db())))
    led_cal.add_argument("--bins", type=int, default=calibration_mod.DEFAULT_BINS)
    led_cal.add_argument("--bootstrap", type=int, default=calibration_mod.DEFAULT_BOOTSTRAP)
    led_cal.add_argument("--cluster", choices=calibration_mod.CLUSTER_MODES, default="hypothesis")
    led_cal.add_argument("--out", default="")
    led_cal.add_argument("--html", default="")
    led_cal.add_argument("--json", action="store_true")
    led_cal.set_defaults(func=cmd_ledger_calibration)

    led_export = led_sub.add_parser("export", help="export the append-only ledger with a content hash")
    led_export.add_argument("--db", default=d.get("db", str(default_db())))
    led_export.add_argument("--out", default="")
    led_export.set_defaults(func=cmd_ledger_export)

    led_checkpoint = led_sub.add_parser("checkpoint", help="write a hash-chained ledger checkpoint")
    led_checkpoint.add_argument("--db", default=d.get("db", str(default_db())))
    led_checkpoint.add_argument("--out", required=True)
    led_checkpoint.add_argument("--previous", default="")
    led_checkpoint.add_argument("--json", action="store_true")
    led_checkpoint.set_defaults(func=cmd_ledger_checkpoint)

    led_verify = led_sub.add_parser("verify-checkpoints", help="verify a directory of ledger checkpoints")
    led_verify.add_argument("--db", default=d.get("db", str(default_db())))
    led_verify.add_argument("--dir", required=True)
    led_verify.add_argument("--json", action="store_true")
    led_verify.set_defaults(func=cmd_ledger_verify_checkpoints)

    ev = sub.add_parser("eval", help="score the database against a gold file")
    ev.add_argument("--db", default=d.get("db", str(default_db())))
    ev.add_argument("--gold", default="gold/pilot.jsonl")
    ev.add_argument(
        "--bootstrap",
        type=int,
        default=d.get("bootstrap", 0),
        help="bootstrap resamples for 95%% CIs",
    )
    ev.add_argument("--frame", default="", help="sampling frame JSON used for per-stratum breakdown")
    ev.add_argument(
        "--strict-gold",
        action="store_true",
        help="exit 1 when gold spans are invalid or gold documents are absent from the database",
    )
    ev.add_argument(
        "--require-gold",
        action="store_true",
        help="exit 3 unless every gold record has status 'human'",
    )
    ev.add_argument("--json", action="store_true")
    ev.set_defaults(func=cmd_eval)

    chk = sub.add_parser("check", help="verify provenance invariants")
    chk.add_argument("--db", default=d.get("db", str(default_db())))
    chk.add_argument("--json", action="store_true")
    chk.set_defaults(func=cmd_check)

    ver = sub.add_parser("verify", help="verify document metadata against Crossref/PubMed")
    ver.add_argument("--db", default=d.get("db", str(default_db())))
    ver.add_argument("--doc-id", default="")
    ver.add_argument("--limit", type=int, default=0)
    ver.add_argument("--cache", default=d.get("cache", "data/cache"))
    ver.add_argument("--offline", action="store_true")
    ver.add_argument("--out", default="")
    ver.add_argument(
        "--fail-on-drift",
        action="store_true",
        help="exit 1 for metadata mismatches or newly detected retractions",
    )
    ver.add_argument("--json", action="store_true")
    ver.set_defaults(func=cmd_verify)

    ft = sub.add_parser("fulltext", help="fetch, extract and compare open-access full text")
    ft_sub = ft.add_subparsers(dest="fulltext_command", required=True)

    ft_fetch = ft_sub.add_parser("fetch", help="fetch JATS full text for PMC documents")
    ft_fetch.add_argument("--db", default=d.get("db", str(default_db())))
    ft_fetch.add_argument("--doc-id", default="")
    ft_fetch.add_argument("--limit", type=int, default=0)
    ft_fetch.add_argument("--cache", default=d.get("cache", "data/cache"))
    ft_fetch.add_argument("--offline", action="store_true")
    ft_fetch.add_argument("--json", action="store_true")
    ft_fetch.set_defaults(func=cmd_fulltext_fetch)

    ft_extract = ft_sub.add_parser("extract", help="extract claims from stored full text")
    ft_extract.add_argument("--db", default=d.get("db", str(default_db())))
    ft_extract.add_argument("--doc-id", default="")
    ft_extract.add_argument("--limit", type=int, default=0)
    ft_extract.add_argument("--json", action="store_true")
    ft_extract.set_defaults(func=cmd_fulltext_extract)

    ft_report = ft_sub.add_parser("report", help="report abstract/full-text reconciliation")
    ft_report.add_argument("--db", default=d.get("db", str(default_db())))
    ft_report.add_argument("--out", default="")
    ft_report.add_argument("--entity", default="", help="filter to one ontology entity id")
    ft_report.add_argument(
        "--fail-on",
        choices=("none", "low", "medium", "high"),
        default="none",
        help="exit 1 when a finding meets or exceeds this severity",
    )
    ft_report.add_argument("--fail-on-conflict", action="store_true")
    ft_report.add_argument("--json", action="store_true")
    ft_report.set_defaults(func=cmd_fulltext_report)

    disc = sub.add_parser("discover", help="rank testable gaps from the extracted evidence")
    disc.add_argument("--db", default=d.get("db", str(default_db())))
    disc.add_argument("--top", type=int, default=15)
    disc.add_argument("--no-untested", action="store_true", help="hide never-reported combinations")
    disc.add_argument("--json", action="store_true")
    disc.set_defaults(func=cmd_discover)

    mech = sub.add_parser("mechanism", help="inspect and simulate the signed mechanism graph")
    mech_sub = mech.add_subparsers(dest="mechanism_command", required=True)

    mech_edges = mech_sub.add_parser("edges", help="list entity-mechanism edges extracted from text")
    mech_edges.add_argument("--db", default=d.get("db", str(default_db())))
    mech_edges.add_argument("--min-support", type=int, default=1)
    mech_edges.add_argument("--json", action="store_true")
    mech_edges.set_defaults(func=cmd_mechanism_edges)

    mech_sim = mech_sub.add_parser("simulate", help="block mechanism nodes and propagate signs to outcomes")
    mech_sim.add_argument("--db", default=d.get("db", str(default_db())))
    mech_sim.add_argument("--entity", required=True, help="entity id, e.g. NP:OXT")
    mech_sim.add_argument(
        "--block",
        action="append",
        default=[],
        help="mechanism node to block, repeatable (e.g. MECH:OXTR)",
    )
    mech_sim.add_argument("--max-depth", type=int, default=5)
    mech_sim.add_argument("--min-confidence", type=float, default=0.40)
    mech_sim.add_argument("--json", action="store_true")
    mech_sim.set_defaults(func=cmd_mechanism_simulate)

    mech_check = mech_sub.add_parser(
        "check", help="compare baseline path signs with held-out claim directions"
    )
    mech_check.add_argument("--db", default=d.get("db", str(default_db())))
    mech_check.add_argument(
        "--holdout",
        type=float,
        default=0.2,
        help="fraction of documents held out; 0 means in-sample descriptive check",
    )
    mech_check.add_argument("--max-depth", type=int, default=5)
    mech_check.add_argument("--min-confidence", type=float, default=0.40)
    mech_check.add_argument("--json", action="store_true")
    mech_check.set_defaults(func=cmd_mechanism_check)

    mech_export = mech_sub.add_parser("export", help="export the graph with provenance as JSON")
    mech_export.add_argument("--db", default=d.get("db", str(default_db())))
    mech_export.add_argument("--out", default="", help="output path; omit to print to stdout")
    mech_export.set_defaults(func=cmd_mechanism_export)

    mech_enrich = mech_sub.add_parser(
        "enrich", help="optional online UniProtKB metadata lookup for a mechanism node"
    )
    mech_enrich.add_argument("--uniprot", required=True, help="UniProt accession, e.g. P30559")
    mech_enrich.add_argument("--cache", default=d.get("cache", "data/cache"))
    mech_enrich.add_argument("--offline", action="store_true")
    mech_enrich.add_argument("--json", action="store_true")
    mech_enrich.set_defaults(func=cmd_mechanism_enrich)

    syn = sub.add_parser("synthesize", help="design-weighted evidence summary per entity and outcome")
    syn.add_argument("--db", default=d.get("db", str(default_db())))
    syn.add_argument("--out", default="", help="optional Markdown output path")
    syn.add_argument(
        "--bootstrap",
        type=int,
        default=d.get("bootstrap", 2000),
        help="document-level bootstrap resamples",
    )
    syn.add_argument("--seed", type=int, default=0)
    syn.add_argument(
        "--include-comparator",
        action="store_true",
        help="include comparator-role claims (excluded by default)",
    )
    syn.add_argument("--json", action="store_true")
    syn.set_defaults(func=cmd_synthesize)

    abl = sub.add_parser("ablate", help="paired ablation of extraction feature flags")
    abl.add_argument("--corpus", required=True)
    abl.add_argument("--gold", required=True)
    abl.add_argument(
        "--configs",
        default="",
        help="comma-separated names (baseline, no_clause, no_role, no_section, coref, legacy)",
    )
    abl.add_argument("--bootstrap", type=int, default=d.get("bootstrap", 500))
    abl.add_argument("--seed", type=int, default=0)
    abl.add_argument("--json", action="store_true")
    abl.set_defaults(func=cmd_ablate)

    fx = sub.add_parser("from-fixture", help="build a database from a local corpus JSON")
    fx.add_argument("--corpus", required=True)
    fx.add_argument("--db", default=d.get("db", str(default_db())))
    fx.add_argument(
        "--resolve-coref",
        action="store_true",
        default=d.get("resolve_coref", False),
        help="experimental: attribute definite anaphora to the single prior entity",
    )
    fx.set_defaults(func=cmd_from_fixture)

    cfg = sub.add_parser("config", help="print the effective configuration")
    cfg.set_defaults(func=cmd_config)

    srv = sub.add_parser("serve", help="read-only local JSON API over a database")
    srv.add_argument("--db", default=d.get("db", str(default_db())))
    srv.add_argument("--host", default=d.get("host", "127.0.0.1"))
    srv.add_argument("--port", type=int, default=d.get("port", 8765))
    srv.set_defaults(func=cmd_serve)

    gold = sub.add_parser("gold", help="validate and combine gold annotation files")
    gold_sub = gold.add_subparsers(dest="gold_command", required=True)
    val = gold_sub.add_parser("validate", help="check an annotation JSONL file")
    val.add_argument("--file", required=True)
    val.add_argument("--corpus", default="", help="corpus JSON with documents[].title/abstract")
    val.add_argument("--json", action="store_true")
    val.set_defaults(func=cmd_gold_validate)
    red = gold_sub.add_parser("redact", help="replace verbatim annotation text with SHA-256 hashes")
    red.add_argument("--in", dest="input", required=True)
    red.add_argument("--out", required=True)
    red.add_argument("--force", action="store_true")
    red.set_defaults(func=cmd_gold_redact)
    hyd = gold_sub.add_parser("hydrate", help="restore redacted annotations from a local corpus")
    hyd.add_argument("--in", dest="input", required=True)
    hyd.add_argument("--corpus", required=True)
    hyd.add_argument("--out", required=True)
    hyd.add_argument("--force", action="store_true")
    hyd.set_defaults(func=cmd_gold_hydrate)
    agr = gold_sub.add_parser("agreement", help="Cohen's kappa between two annotators")
    agr.add_argument("--a", required=True)
    agr.add_argument("--b", required=True)
    agr.add_argument(
        "--field",
        default="direction",
        choices=["direction", "outcome", "role", "effect_type"],
    )
    agr.add_argument("--effects", action="store_true", help="also report effect type/value/CI agreement")
    agr.set_defaults(func=cmd_gold_agreement)
    mrg = gold_sub.add_parser("merge", help="merge annotation sets and apply adjudications")
    mrg.add_argument("--primary", required=True)
    mrg.add_argument("--secondary", default="")
    mrg.add_argument("--adjudications", default="")
    mrg.add_argument("--out", required=True)
    mrg.set_defaults(func=cmd_gold_merge)
    prog = gold_sub.add_parser("progress", help="annotation progress against the sampling frame")
    prog.add_argument("--gold", required=True)
    prog.add_argument("--frame", default="gold/v2/sampling_frame.json")
    prog.add_argument("--json", action="store_true")
    prog.set_defaults(func=cmd_gold_progress)
    rdy = gold_sub.add_parser("readiness", help="human-gold completion and double-annotation gate")
    rdy.add_argument("--gold", default="gold/v2/draft.jsonl")
    rdy.add_argument("--frame", default="gold/v2/sampling_frame.json")
    rdy.add_argument("--second", default="", help="second annotator JSONL")
    rdy.add_argument("--json", action="store_true")
    rdy.set_defaults(func=cmd_gold_readiness)
    dif = gold_sub.add_parser("diff", help="claim-level differences between two gold files")
    dif.add_argument("--a", required=True)
    dif.add_argument("--b", required=True)
    dif.add_argument("--json", action="store_true")
    dif.set_defaults(func=cmd_gold_diff)
    con = gold_sub.add_parser("conflicts", help="field-level disagreements and adjudication skeleton")
    con.add_argument("--a", required=True)
    con.add_argument("--b", required=True)
    con.add_argument("--fields", default="direction,outcome,role,design")
    con.add_argument("--out", default="", help="optional adjudication JSONL skeleton")
    con.add_argument("--json", action="store_true")
    con.set_defaults(func=cmd_gold_conflicts)

    aud = sub.add_parser("audit", help="manual audit sampling of model claims")
    aud_sub = aud.add_subparsers(dest="audit_command", required=True)
    sample = aud_sub.add_parser("sample", help="draw a deterministic audit worksheet")
    sample.add_argument("--db", default=d.get("db", str(default_db())))
    sample.add_argument("--n", type=int, default=40)
    sample.add_argument("--seed", type=int, default=0)
    sample.add_argument(
        "--effects-only", action="store_true", help="sample only claims with an explicit effect"
    )
    sample.add_argument("--out", default="")
    sample.add_argument("--verdicts-out", default="", help="write a blank verdict JSONL template")
    sample.add_argument("--json", action="store_true")
    sample.set_defaults(func=cmd_audit_sample)
    score = aud_sub.add_parser("score", help="score a completed verdict JSONL")
    score.add_argument("--db", default=d.get("db", str(default_db())))
    score.add_argument("--verdicts", required=True)
    score.add_argument("--json", action="store_true")
    score.set_defaults(func=cmd_audit_score)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    pre = argparse.ArgumentParser(add_help=False)
    pre.add_argument("--config", default="")
    known, _ = pre.parse_known_args(argv)
    try:
        settings = settings_mod.load_settings(Path(known.config) if known.config else None)
    except settings_mod.SettingsError as exc:
        print(str(exc), file=sys.stderr)
        return EXIT_CONFIG
    parser = build_parser(settings.defaults())
    args = parser.parse_args(argv)
    if args.frozen_time:
        os.environ[clock.FROZEN_ENV] = "1"
    log.configure(args.log_level or os.environ.get("NTX_LOG_LEVEL", "WARNING"))
    try:
        return args.func(args)
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return EXIT_INTEGRITY


if __name__ == "__main__":
    raise SystemExit(main())
