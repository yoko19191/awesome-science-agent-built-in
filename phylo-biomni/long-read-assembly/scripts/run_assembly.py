#!/usr/bin/env python3
"""Bundled entrypoint for long-read-assembly.

One invocation runs the whole de-novo pipeline: inherited provenance + model/chemistry gate ->
assembler<->read-type guard -> assemble -> polish (order guard + Medaka research-only gate) ->
contamination screen (contig level) -> assembly QC (contiguity / genome fraction / BUSCO-proxy /
Merqury-style QV) -> figures -> facts payload, all beneath --out-root.

It never fabricates an assembly. `--backend hpc|local` with a real assembler routes to Flye/hifiasm/Verkko
(Biomni HPC or a user binary); when that tool is unavailable it records a clean stop and the planned
command instead of inventing contigs. `--assembler provided_draft --draft F.fasta` QCs/polishes a
supplied draft.

Exit codes: 0 = completed; 3 = not_computable (model/chemistry mismatch OR incompatible assembler);
2 = input error; 4 = stopped_no_assembler (real assembler/HPC unavailable, nothing fabricated).

    python scripts/run_assembly.py --reads reads.fastq.gz --run-json run.json \
        --reference reference.fasta --markers markers.fasta --contaminant-ref contaminant_ref.fasta \
        --assembler flye --backend hpc --polish racon --contam-method kmer --mode isolate \
        --out-root /mnt/results
"""
from __future__ import annotations
import argparse, json, os, pathlib, shutil, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lra
import input_contract


def _write_tsv(path, rows, header):
    with open(path, "w") as fh:
        fh.write("\t".join(header) + "\n")
        for r in rows:
            fh.write("\t".join(str(x) for x in r) + "\n")


def _read_summary(reads_path):
    lengths = []
    for _, seq, _ in lra.read_fastq(reads_path):
        lengths.append(len(seq))
    return {"input_read_count": len(lengths),
            "input_total_bp": sum(lengths),
            "input_read_n50": lra._n50(lengths)}


def _resolve_assembler(assembler, backend, read_type):
    if assembler != "auto":
        return assembler
    return "hifiasm" if read_type == "pacbio-hifi" else "flye"


def main(argv=None):
    ap = argparse.ArgumentParser(description="Long-read de novo assembly + polishing + assembly QC")
    ap.add_argument("--execution-mode", required=True, choices=input_contract.EXECUTION_MODES)
    ap.add_argument("--requested-dataset", required=True)
    ap.add_argument("--selection-evidence", required=True)
    ap.add_argument("--acquisition-json", default="{}")
    ap.add_argument("--reads", required=True)
    ap.add_argument("--reads-origin", required=True, choices=input_contract.INPUT_ORIGINS)
    ap.add_argument("--reads-requested-source", required=True)
    ap.add_argument("--run-json", required=True)
    ap.add_argument("--run-json-origin", required=True, choices=input_contract.INPUT_ORIGINS)
    ap.add_argument("--run-json-requested-source", required=True)
    ap.add_argument("--identity-check", required=True,
                    help="completed long-read-qc-basecalling identity_check.json")
    ap.add_argument("--identity-check-origin", required=True, choices=input_contract.INPUT_ORIGINS)
    ap.add_argument("--identity-check-requested-source", required=True)
    ap.add_argument("--reference", default=None, help="host reference FASTA for QUAST genome fraction")
    ap.add_argument("--reference-origin", choices=input_contract.INPUT_ORIGINS)
    ap.add_argument("--reference-requested-source")
    ap.add_argument("--markers", default=None, help="single-copy marker FASTA for BUSCO-proxy")
    ap.add_argument("--markers-origin", choices=input_contract.INPUT_ORIGINS)
    ap.add_argument("--markers-requested-source")
    ap.add_argument("--contaminant-ref", default=None)
    ap.add_argument("--contaminant-ref-origin", choices=input_contract.INPUT_ORIGINS)
    ap.add_argument("--contaminant-ref-requested-source")
    ap.add_argument("--assembler", default="auto",
                    choices=["auto", "flye", "hifiasm", "verkko", "canu", "raven", "unicycler",
                             "wtdbg2", "provided_draft"])
    ap.add_argument("--backend", required=True, choices=["hpc", "local"])
    ap.add_argument("--draft", default=None, help="draft FASTA for --assembler provided_draft")
    ap.add_argument("--draft-origin", choices=input_contract.INPUT_ORIGINS)
    ap.add_argument("--draft-requested-source")
    ap.add_argument("--polish", default="racon",
                    help="comma-ordered polishers, e.g. 'racon' or 'racon,medaka' or 'none'")
    ap.add_argument("--contam-method", default="kmer",
                    choices=["kmer", "reference_align", "taxonomic_db", "builtin_only"])
    ap.add_argument("--mode", default="isolate", choices=["isolate", "metagenome"])
    ap.add_argument("--out-root", default="/mnt/results")
    ap.add_argument("--evidence-tier", default="hypothesis_generating_assembly")
    ap.add_argument("--k", type=int, default=19)
    args = ap.parse_args(argv)

    for label, path, origin, source in (
        ("reference", args.reference, args.reference_origin, args.reference_requested_source),
        ("markers", args.markers, args.markers_origin, args.markers_requested_source),
        ("contaminant-ref", args.contaminant_ref, args.contaminant_ref_origin,
         args.contaminant_ref_requested_source),
        ("draft", args.draft, args.draft_origin, args.draft_requested_source),
    ):
        if bool(path) != bool(origin and source):
            ap.error(f"--{label} requires path, origin, and requested-source together")

    try:
        acquisitions = json.loads(args.acquisition_json)
        selections = [
            ("long_reads", args.reads, args.reads_origin, args.reads_requested_source),
            ("run_metadata", args.run_json, args.run_json_origin, args.run_json_requested_source),
            ("upstream_identity_check", args.identity_check, args.identity_check_origin,
             args.identity_check_requested_source),
            ("reference", args.reference, args.reference_origin, args.reference_requested_source),
            ("markers", args.markers, args.markers_origin, args.markers_requested_source),
            ("contaminant_reference", args.contaminant_ref, args.contaminant_ref_origin,
             args.contaminant_ref_requested_source),
            ("provided_draft", args.draft, args.draft_origin, args.draft_requested_source),
        ]
        records = [input_contract.input_record(
            role=role, path=path, origin=origin, requested_source=source,
            execution_mode=args.execution_mode, acquisition=acquisitions.get(role),
        ) for role, path, origin, source in selections if path]
        input_contract.validate_acquisition_map(acquisitions, records)
        input_manifest = input_contract.build_manifest(
            execution_mode=args.execution_mode, requested_dataset=args.requested_dataset,
            inputs=records, result_root=args.out_root, selection_evidence=args.selection_evidence,
        )
        out = input_contract.require_fresh_result_root(args.out_root)
    except (OSError, ValueError) as exc:
        ap.error(str(exc))
    lrq = out / "long_read_assembly"
    figs = out / "figures"
    lrq.mkdir(parents=True, exist_ok=True)
    figs.mkdir(parents=True, exist_ok=True)
    input_contract.write_manifest(input_manifest, lrq / "input_selection.json")

    # --- consume the upstream identity artifact; do not copy or re-run its scientific gate ---
    try:
        run_meta = lra.load_run_metadata(args.run_json)
        idcheck = lra.load_upstream_identity(args.identity_check, run_json_path=args.run_json)
    except (ValueError, OSError) as exc:
        print(f"INPUT ERROR: {exc}", file=sys.stderr)
        return 2
    if not pathlib.Path(args.reads).is_file():
        print(f"INPUT ERROR: reads not found: {args.reads}", file=sys.stderr)
        return 2

    read_type = lra.derive_read_type_from_identity(idcheck)
    versions = lra.tool_versions()
    upstream_identity_sha256 = lra.sha256_file(args.identity_check)
    shutil.copyfile(args.identity_check, lrq / "identity_check.json")

    # --- assembler <-> read-type guard (brief: reject an incompatible assembler) ---
    resolved_assembler = _resolve_assembler(args.assembler, args.backend, read_type)
    rtcheck = lra.check_assembler_readtype(resolved_assembler, read_type)
    (lrq / "assembly_readtype_check.json").write_text(json.dumps(rtcheck, indent=2))
    if rtcheck["incompatible_violations"] > 0:
        (lrq / "not_computable.json").write_text(json.dumps(
            {"status": "not_computable", "reason": rtcheck["detail"],
             "assembler_readtype_incompatible_violations": rtcheck["incompatible_violations"]},
            indent=2))
        print(f"NOT_COMPUTABLE: {rtcheck['detail']}", file=sys.stderr)
        return 3

    read_summary = _read_summary(args.reads)
    if read_summary["input_read_count"] == 0:
        print("INPUT ERROR: selected FASTQ contains zero reads", file=sys.stderr)
        return 2
    reads = list(lra.read_fastq(args.reads))

    # --- assemble (never fabricates) ---
    backend = args.backend
    if resolved_assembler == "provided_draft":
        if not args.draft or not pathlib.Path(args.draft).is_file():
            print("INPUT ERROR: --assembler provided_draft requires --draft FASTA", file=sys.stderr)
            return 2
        raw_contigs = [(seq, seq[:args.k] == seq[-args.k:]) for _, seq in lra.read_fasta(args.draft)]
        backend = "provided_draft"
    else:
        # real assembler path: require the binary (or HPC). Never fabricate.
        planned = {
            "flye": ["flye", lra.FLYE_MODE.get(read_type, "--nano-hq"), args.reads, "--out-dir", str(lrq / "flye")],
            "hifiasm": ["hifiasm", "-o", str(lrq / "asm"), args.reads],
            "verkko": ["verkko", "-d", str(lrq / "verkko"), "--hifi", args.reads],
        }.get(resolved_assembler, [resolved_assembler, args.reads])
        if not shutil.which(resolved_assembler):
            status = {"status": "stopped_no_assembler", "assembler": resolved_assembler,
                      "backend": backend, "planned_command": planned,
                      "detail": (f"{resolved_assembler} not found on PATH and no HPC execution wired "
                                 f"into this bundled command. Run it via Biomni HPC or a user binary, "
                                 f"then re-run with --assembler provided_draft --draft <asm.fasta>. "
                                 f"No contigs fabricated.")}
            input_contract.bind_artifact(input_manifest, status)
            (lrq / "assembly_status.json").write_text(json.dumps(status, indent=2))
            print(f"STOPPED_NO_ASSEMBLER: {status['detail']}", file=sys.stderr)
            return 4
        # (If present, a real integration would run `planned` here and load its FASTA.)
        status = {"status": "real_assembler_present_not_run_in_bundle", "planned_command": planned}
        input_contract.bind_artifact(input_manifest, status)
        (lrq / "assembly_status.json").write_text(json.dumps(status, indent=2))
        print("STOPPED_NO_ASSEMBLER: real assembler present but execution is not wired into this "
              "bundle; use provided_draft.", file=sys.stderr)
        return 4

    # --- polish: order guard + Medaka research-only gate (never fabricates) ---
    polishers = [p for p in (args.polish.split(",") if args.polish else []) if p.strip()]
    order = lra.check_polish_order(polishers)
    mgate = lra.medaka_gate(polishers)
    (lrq / "polish_status.json").write_text(json.dumps({"requested": polishers, "order": order,
                                                        "medaka_gate": mgate}, indent=2))
    if order["violations"] > 0:
        (lrq / "not_computable.json").write_text(json.dumps(
            {"status": "not_computable", "reason": order["detail"],
             "polish_order_violations": order["violations"]}, indent=2))
        print(f"NOT_COMPUTABLE: {order['detail']}", file=sys.stderr)
        return 3
    # effective polishers: research-only polishers are HARD-REFUSED (never run) -> always dropped
    effective = [p for p in polishers if p not in lra.RESEARCH_ONLY_POLISHERS]
    research_refused = mgate["status"] == "refused_research_only"
    polish_status = ("no_polish" if not effective else
                     ("racon_after_refusing_research_only" if research_refused and "racon" in effective
                      else "polished"))

    if effective:
        planned = [[polisher, str(lrq / "assembly_draft.fasta"), args.reads]
                   for polisher in effective]
        status = {"status": "stopped_external_tool", "stage": "polishing",
                  "requested": effective, "planned_commands": planned,
                  "detail": "Production polishing is not executed by the bundled adapter; no substitute polisher was run."}
        input_contract.bind_artifact(input_manifest, status)
        (lrq / "assembly_status.json").write_text(json.dumps(status, indent=2))
        print("STOPPED_EXTERNAL_TOOL: production polishing requires an external tool", file=sys.stderr)
        return 4
    draft_contigs = polished_contigs = raw_contigs
    if not effective:
        polished_contigs = draft_contigs  # no polishing requested -> polished == draft

    # --- QC: contiguity, QV (draft/polished) with saturation state, genome fraction, BUSCO-proxy ---
    try:
        qv_kmers = lra.derive_solid_kmer_db(reads, args.k)
    except ValueError as exc:
        (lrq / "not_computable.json").write_text(json.dumps(
            {"status": "not_computable", "reason": str(exc), "stage": "qv_kmer_spectrum"},
            indent=2,
        ))
        print(f"NOT_COMPUTABLE: {exc}", file=sys.stderr)
        return 3
    read_db = qv_kmers["solid_kmers"]
    _write_tsv(lrq / "qv_kmer_spectrum.tsv", qv_kmers["spectrum"],
               ["multiplicity", "distinct_kmer_count"])
    contig_metrics = lra.contiguity_metrics(polished_contigs)   # pre-screen (all contigs)
    qv_draft_state = lra.merqury_qv_state(draft_contigs, read_db, args.k)
    qv_polished_state = lra.merqury_qv_state(polished_contigs, read_db, args.k)
    qv_draft = qv_draft_state["qv"]
    qv_polished = qv_polished_state["qv"]
    gf_pct = 0.0
    if args.reference and pathlib.Path(args.reference).is_file():
        gf_pct = round(100.0 * lra.genome_fraction(polished_contigs,
                                                   lra._read_single_fasta(args.reference)), 4)
    busco = {"busco_markers_total": 0, "busco_complete": 0, "busco_complete_pct": 0.0, "busco_missing": 0}
    if args.markers and pathlib.Path(args.markers).is_file():
        markers = [seq for _, seq in lra.read_fasta(args.markers)]
        busco = lra.busco_proxy(polished_contigs, markers)

    # --- contamination screen (contig level) ---
    contam = lra.screen_contamination_contigs(
        polished_contigs, contaminant_ref=args.contaminant_ref, host_ref=args.reference,
        method=args.contam_method)
    # Retained-summary contiguity excludes triage-flagged contigs. assembly.fasta remains the
    # all-contig assembly; this summary does not silently rewrite or relabel that artifact as clean.
    retained_contigs = lra.post_screen_contigs(polished_contigs, contam)
    post_metrics = lra.contiguity_metrics(retained_contigs)
    screen_ok = contam["status"] in {"ok", "ok_kmer_triage"}
    flagged_bp = sum(pc["length"] for pc in contam["per_contig"] if pc.get("flagged"))
    (lrq / "contamination.tsv").write_text("")  # header + rows below
    _write_tsv(lrq / "contamination.tsv", [
        ("requested_method", contam["requested_method"], ""),
        ("effective_method", contam.get("effective_method"), ""),
        ("status", contam["status"], ""),
        ("triage_completed", contam["triage_completed"], "bool"),
        ("n_contigs", contam["n_contigs"], "contigs"),
        ("contaminant_flagged_contigs", contam["contaminant_flagged_contigs"], "contigs"),
        ("retained_after_triage_contigs", contam["retained_after_triage_contigs"], "contigs"),
        ("non_contaminant_contigs_compatibility_alias", contam["non_contaminant_contigs"], "contigs"),
    ], ["field", "value", "unit"])
    (lrq / "contamination_per_contig.json").write_text(json.dumps(contam["per_contig"], indent=2))

    # --- write assembly + graph + QC table ---
    lra.write_fasta(lrq / "assembly.fasta", polished_contigs)
    lra.write_fasta(lrq / "assembly_draft.fasta", draft_contigs)
    lra.write_gfa(lrq / "assembly_graph.gfa", polished_contigs, k=args.k)
    _write_tsv(lrq / "assembly_qc.tsv", [
        # pre-screen contiguity (all contigs, including any contaminant) — the screened set
        ("n_contigs", contig_metrics["n_contigs"], "contigs"),
        ("assembly_length_bp", contig_metrics["assembly_length_bp"], "bp"),
        ("largest_contig_bp", contig_metrics["largest_contig_bp"], "bp"),
        ("contig_n50", contig_metrics["contig_n50"], "bp"),
        ("mean_contig_bp", contig_metrics["mean_contig_bp"], "bp"),
        ("n_circular", contig_metrics["n_circular"], "contigs"),
        ("gc_percent", contig_metrics["gc_percent"], "percent"),
        # Retained-summary contiguity; assembly.fasta itself remains all-contig.
        ("n_contigs_post_screen", post_metrics["n_contigs"], "contigs"),
        ("assembly_length_bp_post_screen", post_metrics["assembly_length_bp"], "bp"),
        ("largest_contig_bp_post_screen", post_metrics["largest_contig_bp"], "bp"),
        ("contig_n50_post_screen", post_metrics["contig_n50"], "bp"),
        ("contamination_screen_applied", screen_ok, "bool"),
        ("genome_fraction_pct", gf_pct, "percent"),
        ("busco_complete_pct", busco["busco_complete_pct"], "percent"),
        # consensus QV with explicit saturation disclosure (Finding 3)
        ("merqury_qv_draft", qv_draft, "phred"),
        ("merqury_qv_polished", qv_polished, "phred"),
        ("merqury_qv_draft_saturated", qv_draft_state["saturated"], "bool"),
        ("merqury_qv_polished_saturated", qv_polished_state["saturated"], "bool"),
        ("merqury_qv_polished_state", qv_polished_state["state"], "state"),
    ], ["metric", "value", "unit"])

    # --- provenance ---
    assembler_executed = resolved_assembler != "provided_draft"
    assembly_meta = {"assembler_requested": args.assembler,
                     "assembler_resolved": resolved_assembler,
                     "assembler_executed": assembler_executed,
                     "assembly_source": ("production_assembler" if assembler_executed
                                         else "user_supplied_draft"),
                     "backend": backend, "read_type": read_type,
                     "polishers_requested": polishers, "polishers_effective": effective,
                     "polish_status": polish_status, "mode": args.mode, "k": args.k,
                     "contamination_requested_method": contam["requested_method"],
                     "contamination_effective_method": contam.get("effective_method"),
                     "contamination_triage_completed": contam["triage_completed"],
                     "upstream_identity_path": str(pathlib.Path(args.identity_check).resolve()),
                     "upstream_identity_sha256": upstream_identity_sha256,
                     "qv_inputs": {
                         "method": "reference_free_merqury_style",
                         "k": args.k,
                         "solid_kmer_min_count": qv_kmers["threshold"],
                         "distinct_read_kmers": qv_kmers["distinct_kmers"],
                         "kmer_spectrum_artifact": "long_read_assembly/qv_kmer_spectrum.tsv",
                         "kmer_spectrum_sha256": lra.sha256_file(lrq / "qv_kmer_spectrum.tsv"),
                         "long_reads_sha256": lra.sha256_file(args.reads),
                         "draft_assembly_sha256": lra.sha256_file(lrq / "assembly_draft.fasta"),
                         "polished_assembly_sha256": lra.sha256_file(lrq / "assembly.fasta"),
                         "solid_kmer_db_sha256": lra.sha256_string_set(read_db),
                     }}
    provenance = lra.build_provenance(
        run_meta, tool_versions=versions, assembly_meta=assembly_meta, read_type=read_type,
    )
    input_contract.bind_artifact(input_manifest, provenance)
    (lrq / "provenance.json").write_text(json.dumps(provenance, indent=2))
    (lrq / "read_summary.json").write_text(json.dumps(read_summary, indent=2))

    # --- figures ---
    polished_assembly_distinct = bool(effective) and draft_contigs != polished_contigs
    manifest = lra.make_figures(polished_contigs, qv_draft, qv_polished, busco, contam, figs,
                                genome_fraction_pct=gf_pct,
                                qv_draft_saturated=qv_draft_state["saturated"],
                                qv_polished_saturated=qv_polished_state["saturated"],
                                polished_assembly_distinct=polished_assembly_distinct)
    (figs / "manifest.json").write_text(json.dumps(manifest, indent=2))

    # --- facts payload (headline + partitions + caveat fields + sentences; NO figures key) ---
    n_contigs = contig_metrics["n_contigs"]
    flagged = contam["contaminant_flagged_contigs"]
    retained = contam["retained_after_triage_contigs"]
    qv_improved = polished_assembly_distinct and qv_polished > qv_draft
    # QV display strings: a saturated QV is rendered as a ceiling/lower-bound, never a bare exact Q60 (Finding 3)
    qv_pol_disp = (f"\u2265Q{qv_polished:g} (saturated ceiling: >=100% of assembly k-mers supported; "
                   f"a lower bound, not an exact estimate)" if qv_polished_state["saturated"]
                   else f"Q{qv_polished:g}")
    qv_draft_disp = (f"\u2265Q{qv_draft:g} (saturated)" if qv_draft_state["saturated"]
                     else f"Q{qv_draft:g}")
    qv_label = (
        "polished consensus QV" if polished_assembly_distinct else "assembly QV"
    )
    if polished_assembly_distinct:
        polish_sentence = (
            f"Reference-free consensus QV "
            f"{'rose' if qv_polished > qv_draft else 'held'} from "
            f"{qv_draft_disp} (draft) to {qv_pol_disp} (polished); "
            f"polish status {polish_status}."
        )
    else:
        polish_sentence = (
            f"Reference-free assembly QV was {qv_pol_disp}; "
            f"no polisher produced a distinct assembly (status: {polish_status})."
        )
    if screen_ok:
        headline_sentence = (
            f"Retained-after-triage summary: {post_metrics['n_contigs']} contig(s), "
            f"{post_metrics['assembly_length_bp']:,} bp; largest {post_metrics['largest_contig_bp']:,} bp, "
            f"contig N50 {post_metrics['contig_n50']:,} bp; {qv_label} {qv_pol_disp}. "
            f"The delivered assembly.fasta remains the all-contig assembly: "
            f"{contig_metrics['assembly_length_bp']:,} bp in {n_contigs} contig(s), of which "
            f"{flagged} ({flagged_bp:,} bp) were flagged by {contam['effective_method']}."
        )
        contiguity_sentence = (
            f"All-contig assembly: {n_contigs} contig(s) ({contig_metrics['n_circular']} circular). "
            f"K-mer triage flagged {flagged}, leaving a retained summary of "
            f"{post_metrics['n_contigs']} contig(s) / {post_metrics['assembly_length_bp']:,} bp; "
            f"retained-summary N50 {post_metrics['contig_n50']:,} bp vs all-contig N50 "
            f"{contig_metrics['contig_n50']:,} bp; genome fraction {gf_pct:g}% vs the supplied reference."
        )
    else:
        headline_sentence = (
            f"All-contig assembly: {n_contigs} contig(s), {contig_metrics['assembly_length_bp']:,} bp; "
            f"largest {contig_metrics['largest_contig_bp']:,} bp, contig N50 "
            f"{contig_metrics['contig_n50']:,} bp; {qv_label} {qv_pol_disp}. "
            f"Contamination triage did not run ({contam['status']}); all contigs were not evaluated. "
            "Compatibility post-screen metrics equal the all-contig metrics."
        )
        contiguity_sentence = (
            f"All-contig assembly has {n_contigs} contig(s) ({contig_metrics['n_circular']} circular), "
            f"{contig_metrics['assembly_length_bp']:,} bp, and N50 {contig_metrics['contig_n50']:,} bp. "
            f"Triage did not run ({contam['status']}); no retained-after-triage claim is made, and "
            f"compatibility post-screen fields mirror all-contig values; genome fraction {gf_pct:g}%."
        )
    facts = {
        # headlines
        "n_contigs": n_contigs,
        "assembly_length_bp": contig_metrics["assembly_length_bp"],
        "largest_contig_bp": contig_metrics["largest_contig_bp"],
        "contig_n50": contig_metrics["contig_n50"],
        "genome_fraction_pct": gf_pct,
        "busco_complete_pct": busco["busco_complete_pct"],
        "merqury_qv": qv_polished,
        "qv_draft": qv_draft,
        "qv_polished": qv_polished,
        "qv_improved": bool(qv_improved),
        # QV saturation disclosure (Finding 3)
        "qv_polished_saturated": bool(qv_polished_state["saturated"]),
        "qv_draft_saturated": bool(qv_draft_state["saturated"]),
        "qv_polished_state": qv_polished_state["state"],
        # All-contig assembly plus a separate retained-after-triage summary.
        "n_contigs_post_screen": post_metrics["n_contigs"],
        "assembly_length_bp_post_screen": post_metrics["assembly_length_bp"],
        "contig_n50_post_screen": post_metrics["contig_n50"],
        "largest_contig_bp_post_screen": post_metrics["largest_contig_bp"],
        "contamination_screen_applied": bool(screen_ok),
        "triage_completed": bool(screen_ok),
        "contaminant_flagged_contigs": flagged,
        "assembler_readtype_compatible": bool(rtcheck["compatible"]),
        "model_chemistry_applicable": bool(idcheck["applicable"]),
        "model_chemistry_compatible": bool(idcheck["model_chemistry_compatible"]),
        "upstream_identity_sha256": upstream_identity_sha256,
        # partition members (contamination disposition): flagged + retained == n_contigs
        "screened_contig_count": n_contigs,
        "retained_after_triage_contigs": retained,
        "non_contaminant_contigs": retained,
        "non_contaminant_contigs_semantics": "compatibility alias for retained_after_triage_contigs",
        # caveat-bound status fields
        "contamination_screen_status": contam["status"],
        "contamination_requested_method": contam["requested_method"],
        "contamination_effective_method": contam.get("effective_method"),
        "evidence_tier": args.evidence_tier,
        "model_chemistry_mismatch_violations": idcheck["model_chemistry_mismatch_violations"],
        "assembler_readtype_incompatible_violations": rtcheck["incompatible_violations"],
        "polish_status": polish_status,
        "assembly_backend": backend,
        # provenance echo
        "platform": run_meta.get("platform"),
        "flowcell_chemistry": run_meta.get("flowcell_chemistry"),
        "basecaller_model": run_meta.get("basecaller_model"),
        "reference_build": run_meta.get("reference_build"),
        "read_type": read_type,
        "assembler": resolved_assembler,
        "assembler_executed": assembler_executed,
        "assembly_source": assembly_meta["assembly_source"],
        # pre-formatted sentences the report may quote verbatim
        "headline_sentence": headline_sentence,
        "contiguity_sentence": contiguity_sentence,
        "polish_sentence": polish_sentence,
        "completeness_sentence": (
            f"BUSCO-proxy completeness {busco['busco_complete_pct']:g}% "
            f"({busco['busco_complete']}/{busco['busco_markers_total']} single-copy markers)."),
        "contamination_sentence": lra.contamination_summary_sentence(contam),
        "provenance_sentence": (
            f"Platform {idcheck['platform_normalized']} / chemistry {idcheck['declared_chemistry']} / "
            f"read type {read_type}; "
            + (f"assembler {resolved_assembler} executed via {backend}. " if assembler_executed else
               "the selected draft was supplied by the user; no assembler was executed here. ")
            + (f"Model {idcheck['basecaller_model']} matches declared chemistry."
               if idcheck["applicable"] else
               "The ONT model-name chemistry check was not applicable; no compatibility pass is claimed.")),
    }
    input_contract.assert_inputs_unchanged(input_manifest)
    input_contract.bind_artifact(input_manifest, facts)
    (lrq / "facts_payload.json").write_text(json.dumps(facts, indent=2))

    print("run_assembly complete:", json.dumps({
        "n_contigs": n_contigs, "contig_n50": contig_metrics["contig_n50"],
        "assembly_len": contig_metrics["assembly_length_bp"], "genome_fraction_pct": gf_pct,
        "busco_pct": busco["busco_complete_pct"], "qv_draft": qv_draft, "qv_polished": qv_polished,
        "contaminant_flagged": flagged, "assembler": resolved_assembler, "backend": backend}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
