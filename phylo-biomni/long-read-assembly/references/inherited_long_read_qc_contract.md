# Inherited read-QC contract

Read on demand. `long-read-assembly` requires the exact completed identity artifact from
`long-read-qc-basecalling`. It validates and hash-binds that artifact and the selected `run.json`;
it does not vendor or recompute the anchor's chemistry gate.

## Upstream identity

- Slug: `long-read-qc-basecalling`
- Relationship: upstream read QC → assembly.

## Consumed from the upstream package

| `scripts/lra.py` symbol | Inherited responsibility | Artifact / field |
|---|---|---|
| `open_maybe_gzip`, `read_fastq` | FASTQ reading (plain/gzip) | reads in |
| `read_mean_quality`, `_n50` | per-read mean-Q and bp-weighted N50 definitions | read/contig stats |
| `load_upstream_identity` | Validate the upstream schema/tool/verdict/applicability/hash contract | selected `identity_check.json` |
| `derive_read_type_from_identity` | Derive assembler read type from validated upstream fields | `assembly_readtype_check.json:read_type` |
| `build_provenance`, `tool_versions` | provenance echo | `provenance.json` |
| `screen_contamination_contigs` | Assembly-specific contig triage; its `ok_kmer_triage` state is deliberately distinct from upstream reference-alignment `ok` | `contamination.tsv`, `contamination_screen_status` |

The `report_qc.py` harness also preserves the upstream facts-payload → `report_facts.json` →
`run_receipt.json` (`phylo-run-receipt`) pattern.

## Additive assembly extensions

- **`read_type`** — derived from the validated upstream identity fields and used by the assembler
  compatibility guard.
- **Contamination applied at the contig level** — an assembly-specific triage contract dispositioning
  contigs (`contaminant_flagged_contigs + non_contaminant_contigs = screened_contig_count`) instead of
  reads.
- **Assembly layer** — `derive_read_type`, `check_assembler_readtype`, `check_polish_order`,
  `medaka_gate`, `contiguity_metrics`, `merqury_qv`, `genome_fraction`, `busco_proxy`,
  `make_figures`, and `in_scope` extend the upstream read-level contract.

## Inherited gate ordering (enforced before facts)

1. `load_upstream_identity` validates schema/tool/status/applicability/hash semantics and requires
   its `run_json_sha256` to match the selected `run.json` bytes.
2. A missing, malformed, failed, or mismatched artifact stops before assembly output.
3. Only then does the assembly → polish → QC → contamination pipeline run.

This preserves the upstream guarantee that provenance/identity failures never yield quotable claims, and
extends it so an assembler incompatible with the declared read type is rejected on the same footing
(`assembly_readtype_check.json:incompatible_violations`).
