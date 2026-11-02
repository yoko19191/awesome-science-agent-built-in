#!/usr/bin/env python3
"""Provider-neutral scientific report model for single-cell-multimodal-integration.

This module has no import-time artifact reads or writes. It preserves the custom renderer's existing
facts, ordered narrative, tables, and exact validated figure inventory in a style-independent JSON
model consumed by either the default or an explicitly selected installed report-style provider.
"""

from __future__ import annotations

import csv
import datetime as dt
import hashlib
import json
import pathlib
from dataclasses import dataclass
from typing import Any

from adt_contract import canonical_adt_feature_name, canonical_adt_feature_names
from report_language import (
    adt_figure_caption,
    adt_normalization_sentence,
    validate_adt_normalization_status,
)

REPORT_NAME_DEFAULT = "report_single_cell_multimodal_integration.pdf"
INFOGRAPHIC_DEFAULT = "report_single_cell_multimodal_integration_infographic.png"
CONTENT_NAME = "report_content.json"
PREPARATION_NAME = "report_preparation.json"
SELECTION_NAME = "report_style_selection.json"
CONTENT_SCHEMA = "single-cell-multimodal-report-content/1"
PREPARATION_SCHEMA = "single-cell-multimodal-report-preparation/1"
SELECTION_SCHEMA = "single-cell-multimodal-report-style-selection/1"
DEFAULT_PROVIDER = "pdf-report-generation"
LOCAL_NARRATIVE_FIELDS = ("claim", "evidence", "interpretation", "limitation")
ADT_MARKER_EFFECT = "avg_normalized_adt_difference"
ADT_MARKER_COLUMNS = (
    "cluster",
    "gene",
    ADT_MARKER_EFFECT,
    "p_val_adj",
    "pct.1",
    "pct.2",
    "normalization_method",
)

EXPECTED_FIGURES = (
    (3, "figures/figure_3_qc_each_modality.png", 470,
     "Figure 3. Per-modality QC after filtering, with retained-cell thresholds marked."),
    (4, "figures/figure_4_denoise_antibody_derived.png", 430, None),
    (5, "figures/figure_5_build_the_wnn.png", 500,
     "Figure 5. WNN joint UMAP by cluster and the per-cell RNA modality-weight distribution in [0,1]."),
    (6, "figures/figure_6_compute_the_totalvi.png", 480,
     "Figure 6. Integration metrics (label purity shown only when an independent reference exists) "
     "beside the annotation-vs-reference confusion, which exposes any novel label and cluster split/merge."),
    (7, "figures/figure_7_annotate_cells_and.png", 470,
     "Figure 7. Per-cluster surface-protein signal (row z-scored) with supported cell types or "
     "explicit unassigned states and confidence flags."),
    (8, "figures/figure_8_test_replicate_aware.png", 460,
     "Figure 8. Milo neighborhood differential abundance across conditions "
     "(or the insufficient-replication skip flag)."),
)

REFERENCES = (
    "10x Genomics. Feature-barcode matrices (Cell Ranger HDF5 outputs). Product documentation.",
    "Mulè MP, Martins AJ, Tsang JS. Normalizing and denoising protein expression data from "
    "droplet-based single-cell profiling (dsb). <i>Nature Communications</i> 13, 2099 (2022). "
    "doi:10.1038/s41467-022-29356-8",
    "Hao Y, Hao S, Andersen-Nissen E, et al. Integrated analysis of multimodal single-cell data "
    "(WNN). <i>Cell</i> 184, 3573–3587 (2021). doi:10.1016/j.cell.2021.04.048",
    "Gayoso A, Steier Z, Lopez R, et al. Joint probabilistic modeling of single-cell multi-omic "
    "data with totalVI. <i>Nature Methods</i> 18, 272–282 (2021). "
    "doi:10.1038/s41592-020-01050-x",
    "Dann E, Henderson NC, Teichmann SA, Morgan MD, Marioni JC. Differential abundance testing "
    "on single-cell data using k-nearest neighbor graphs (Milo). <i>Nature Biotechnology</i> 40, "
    "245–253 (2022). doi:10.1038/s41587-021-01033-z",
)


class ReportError(RuntimeError):
    """The report contract cannot be completed without changing or inventing evidence."""


@dataclass(frozen=True)
class ReportConfig:
    results: pathlib.Path
    workspace: pathlib.Path
    title: str
    subtitle: str
    sample_description: str
    report_name: str
    infographic_name: str
    selected_branch_ids: tuple[str, ...]


def _value(mapping: dict, key: str, default: str) -> str:
    value = (mapping or {}).get(key)
    return str(value) if value is not None and value != "" else default


def load_config(path: str | pathlib.Path) -> ReportConfig:
    """Load runtime paths and report labels only when a phase is executed."""
    import yaml

    config_path = pathlib.Path(path)
    with config_path.open(encoding="utf-8") as handle:
        raw = yaml.safe_load(handle) or {}
    project = raw.get("project") or {}
    report = raw.get("report") or {}
    input_config = raw.get("input") or {}
    results = pathlib.Path(project.get("results_dir", "/mnt/results"))
    workspace = pathlib.Path(project.get("outdir", "/workspace/s38_run"))
    route = str(raw.get("route", "auto"))
    input_mode = str(input_config.get("execution_mode", "analysis"))
    adt_norm = str((raw.get("adt") or {}).get("normalization", "dsb"))
    return ReportConfig(
        results=results,
        workspace=workspace,
        title=_value(report, "title", "Single-Cell Multimodal Integration"),
        subtitle=_value(
            report,
            "subtitle",
            "Paired CITE-seq: WNN joint embedding, totalVI, annotation & differential abundance",
        ),
        sample_description=_value(report, "sample_description", "a paired 10x CITE-seq bundle"),
        report_name=_value(report, "pdf_name", REPORT_NAME_DEFAULT),
        infographic_name=_value(report, "infographic_name", INFOGRAPHIC_DEFAULT),
        selected_branch_ids=(
            f"input_mode:{input_mode}",
            f"route_override:{route}",
            f"adt_norm:{adt_norm}",
        ),
    )


def _result(config: ReportConfig, relative: str) -> pathlib.Path:
    root = config.results.resolve(strict=True)
    candidate = (config.results / relative).resolve(strict=False)
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise ReportError(f"report artifact escapes the results root: {relative!r}") from exc
    return candidate


def _sha256(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _file_evidence(config: ReportConfig, relative: str) -> dict:
    path = _result(config, relative)
    if not path.is_file() or path.stat().st_size == 0:
        raise ReportError(f"required report source is missing or empty: {path}")
    return {"path": relative, "bytes": path.stat().st_size, "sha256": _sha256(path)}


def _load_json(config: ReportConfig, relative: str, *, required: bool = True) -> dict:
    path = _result(config, relative)
    if not path.is_file():
        if required:
            raise ReportError(f"required report source is missing: {path}")
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        if required:
            raise ReportError(f"cannot read report JSON source {path}: {exc}") from exc
        return {}
    if not isinstance(value, dict):
        raise ReportError(f"report JSON source must contain an object: {path}")
    return value


def _fmt(value: Any) -> str:
    try:
        numeric = float(value)
        if numeric == int(numeric) and abs(numeric) >= 1:
            return f"{int(numeric):,}"
        return f"{numeric:g}"
    except (ValueError, TypeError, OverflowError):
        return str(value)


def _fact(facts: dict, key: str, default: str = "NA") -> str:
    value = facts.get(key)
    return _fmt(value) if value is not None else default


def _csv_block(
    config: ReportConfig,
    relative: str,
    widths: list[int],
    *,
    max_rows: int,
) -> dict | None:
    path = _result(config, relative)
    if not path.is_file():
        return None
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.reader(handle)
        rows = list(reader)
    if not rows:
        return None
    return {
        "type": "table",
        "source": relative,
        "columns": [str(value) for value in rows[0]],
        "rows": [[_fmt(value) for value in row] for row in rows[1:max_rows + 1]],
        "column_widths": widths,
    }


def _annotation_statement(config: ReportConfig) -> str:
    path = _result(config, "joint_cluster_celltype_assignment.csv")
    if not path.is_file():
        return "No cluster annotation table was available; no cell-type assignment is claimed."
    with path.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    assigned = [row for row in rows if row.get("cell_type") not in (None, "", "unassigned")]
    if not assigned:
        return (
            f"None of the {len(rows)} joint clusters matched a supported surface-protein program; "
            "they are explicitly reported as unassigned rather than receiving an arbitrary label."
        )
    return (
        f"{len(assigned)} of {len(rows)} joint clusters matched a supported surface-protein program; "
        "any unmatched cluster is explicitly reported as unassigned."
    )


def _validate_protein_marker_contract(
    config: ReportConfig,
    marker_status: dict,
    adt_status: dict,
) -> None:
    """Fail closed before a report can mislabel isotypes or DSB-score effects."""
    if marker_status.get("effect_statistic") != ADT_MARKER_EFFECT:
        raise ReportError(
            "protein marker status does not declare the normalized-score difference effect"
        )
    if marker_status.get("isotype_controls_excluded") is not True:
        raise ReportError("protein marker status does not attest isotype-control exclusion")
    if marker_status.get("effect_definition") != (
        "arithmetic mean normalized ADT score in cluster minus outside cluster"
    ):
        raise ReportError("protein marker status has an unsupported effect definition")
    if str(marker_status.get("normalization_method")) != str(adt_status.get("method")):
        raise ReportError("protein marker normalization method disagrees with the run")

    marker_path = _result(config, "protein_markers.csv")
    if not marker_path.is_file():
        raise ReportError(f"required report source is missing: {marker_path}")
    with marker_path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        if tuple(reader.fieldnames or ()) != ADT_MARKER_COLUMNS:
            raise ReportError(
                "protein_markers.csv must use normalized-score effects, not a log2 fold change"
            )
        rows = list(reader)
    isotypes = set(canonical_adt_feature_names(
        [str(value) for value in (adt_status.get("isotype_controls") or [])],
        context="reported isotype controls",
    ))
    # This is a long-format per-cluster marker table, so the same biological
    # ADT may legitimately recur for multiple clusters.  Canonicalize each
    # value and compare the resulting set with the isotype panel; the
    # one-to-one panel validator above remains appropriate for isotypes.
    marker_features = {
        canonical_adt_feature_name(str(row.get("gene", ""))) for row in rows
    }
    leaked = sorted(marker_features & isotypes)
    if leaked:
        raise ReportError(
            "protein_markers.csv contains isotype controls as biological markers: "
            + ", ".join(leaked)
        )
    try:
        status_count = int(marker_status["n_markers"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ReportError("protein marker status has no valid marker count") from exc
    if status_count != len(rows):
        raise ReportError("protein marker status count disagrees with protein_markers.csv")


def _paragraph(text: str) -> dict:
    return {"type": "paragraph", "text": text}


def _subheading(text: str) -> dict:
    return {"type": "subheading", "text": text}


def _figure(path: str, width: int, caption: str, local_narrative: dict[str, str]) -> dict:
    missing = [field for field in LOCAL_NARRATIVE_FIELDS if not local_narrative.get(field, "").strip()]
    if missing:
        raise ReportError(f"figure {path} has incomplete local narrative fields: {missing}")
    return {
        "type": "figure",
        "path": path,
        "width": width,
        "caption": caption,
        "local_narrative": {field: local_narrative[field] for field in LOCAL_NARRATIVE_FIELDS},
    }


def _lp_sentence(status: dict) -> str:
    """Preserve the existing independent-label-only narration."""
    state = status.get("state")
    if state == "evaluable":
        metrics = status.get("metrics") or {}
        source = (status.get("label_source") or {}).get("field", "reference")
        coverage = (status.get("coverage") or {}).get("coverage_frac", "NA")
        return (
            f"Label purity was scored only against the independent reference label <b>{source}</b> "
            f"(coverage {coverage}; {status.get('n_scored', 'NA')} cells), never the cluster-derived "
            f"annotation: ARI={metrics.get('label_purity_ARI')}, "
            f"NMI={metrics.get('label_purity_NMI')}. Independence is established from data lineage "
            f"— the reference is preserved before annotation — plus circularity guards, so this "
            f"measures agreement with external labels rather than a self-referential comparison [3]."
        )
    return (
        f"Label purity was <b>not evaluable</b> "
        f"({status.get('reason', 'no independent labels')}): no independent reference-label ARI/NMI "
        f"is claimed, and a cluster-derived annotation is never scored against its own clusters [3]."
    )


def _concordance_blocks(config: ReportConfig, status: dict) -> list[dict]:
    concordance = status.get("annotation_concordance") or {}
    blocks = [_subheading("Annotation concordance")]
    if not concordance or concordance.get("n_cells_scored", 0) == 0:
        blocks.append(_paragraph(
            "The marker-derived annotation was not scored against an independent reference "
            "(none available)."
        ))
        return blocks
    unreconciled = concordance.get("unreconciled_annotation_labels") or []
    splits = concordance.get("splits") or {}
    parts = [
        "The marker-derived annotation was compared against the independent reference as a "
        f"separate result (best-match concordance {concordance.get('concordance_frac', 'NA')} over "
        f"{concordance.get('n_cells_scored', 'NA')} cells)."
    ]
    if unreconciled:
        names = ", ".join(
            f"{item['annotation']} (n={item['n_cells']}, "
            f"{int(round(item['frac_from_dominant'] * 100))}% from "
            f"{item['dominant_reference']})"
            for item in unreconciled
        )
        parts.append(
            f"Annotation label(s) with no independent-reference counterpart: <b>{names}</b> — "
            "surfaced here rather than hidden inside a purity scalar."
        )
    if splits:
        split_text = "; ".join(
            f"{reference} → " + " + ".join(f"{key} ({value})" for key, value in values.items())
            for reference, values in splits.items()
        )
        parts.append(f"Reference type(s) split across multiple annotation labels: {split_text}.")
    blocks.append(_paragraph(" ".join(parts)))
    table = _csv_block(
        config,
        "annotation_concordance.csv",
        [150, 150, 80],
        max_rows=30,
    )
    if table:
        blocks.append(table)
    return blocks


def _scorecard_metrics(config: ReportConfig) -> dict[str, str]:
    path = _result(config, "joint_qc_scorecard.csv")
    if not path.is_file():
        return {}
    with path.open(newline="", encoding="utf-8") as handle:
        return {
            str(row.get("metric")): str(row.get("value"))
            for row in csv.DictReader(handle)
            if row.get("metric")
        }


def _figure_local_narratives(
    config: ReportConfig,
    facts: dict,
    provenance: dict,
    adt_status: dict,
    totalvi_status: dict,
    da_status: dict,
    label_status: dict,
    marker_status: dict,
    annotation_statement: str,
) -> dict[str, dict[str, str]]:
    n_input = _fact(facts, "n_input_cells")
    n_joint = _fact(facts, "n_joint_cells")
    n_dropped = _fact(facts, "n_cells_dropped_qc")
    n_clusters = _fact(facts, "n_joint_clusters")
    median_weight = _fact(facts, "median_rna_modality_weight")
    weight_violations = (provenance.get("wnn") or {}).get(
        "modality_weight_out_of_unit_interval", "NA"
    )
    metrics = _scorecard_metrics(config)
    background = adt_status.get("background_selection") or {}
    method = str(adt_status.get("method", "NA"))
    if method.startswith("dsb"):
        background_evidence = (
            f"adt_normalization_status.json records {adt_status.get('n_empty_droplets', 'NA')} "
            "captured low-depth non-cell background droplets, "
            f"{background.get('called_cell_overlap_count', 'NA')} called-cell overlaps, and "
            f"the declared depth bounds {background.get('thresholds', {})}."
        )
        background_limitation = (
            "DSB validity still depends on the declared depth bounds representing ambient droplets "
            "for this assay; the measured isotype median is a diagnostic, not a universal pass threshold."
        )
    else:
        background_evidence = (
            f"adt_normalization_status.json records method={method} and reason="
            f"{adt_status.get('reason', 'not recorded')}."
        )
        background_limitation = (
            "CLR does not model an ambient-droplet population; comparisons remain on the CLR scale."
        )
    label_state = str(label_status.get("state", "not_evaluable"))
    totalvi_clause = (
        f"totalVI ran with final training ELBO {totalvi_status.get('final_elbo_train', 'NA')}"
        if totalvi_status.get("ran") else
        f"totalVI did not run ({totalvi_status.get('reason', 'reason not recorded')})"
    )
    da_ran = bool(da_status.get("ran"))
    return {
        "figures/figure_3_qc_each_modality.png": {
            "claim": f"Modality-specific QC retained {n_joint} of {n_input} paired cells.",
            "evidence": (
                f"report_facts.json records {n_dropped} QC-dropped cells and preserves the identity "
                f"{n_input} input = {n_joint} retained + {n_dropped} dropped + 0 unpaired."
            ),
            "interpretation": "Only the joint RNA-and-ADT passing population enters integration.",
            "limitation": (
                "Threshold passing establishes technical inclusion, not biological identity or absence "
                "of residual low-quality cells."
            ),
        },
        "figures/figure_4_denoise_antibody_derived.png": {
            "claim": f"ADT normalization used the declared {method} route.",
            "evidence": background_evidence,
            "interpretation": adt_normalization_sentence(adt_status).replace("<b>", "").replace("</b>", ""),
            "limitation": background_limitation,
        },
        "figures/figure_5_build_the_wnn.png": {
            "claim": f"WNN produced {n_clusters} joint clusters with median RNA weight {median_weight}.",
            "evidence": (
                f"provenance_manifest.json records {weight_violations} modality weights outside [0,1]; "
                "modality_weights_summary.csv reports ADT weight as 1 minus RNA weight."
            ),
            "interpretation": "The weight distribution describes relative modality influence on the joint graph.",
            "limitation": "WNN weights and a UMAP layout do not independently validate the recovered cell states.",
        },
        "figures/figure_6_compute_the_totalvi.png": {
            "claim": "The available integration diagnostics quantify separation and cross-method overlap without inventing unavailable label or batch metrics.",
            "evidence": (
                f"joint_qc_scorecard.csv records silhouette={metrics.get('cluster_silhouette_wnn', 'NA')}, "
                f"WNN-totalVI kNN agreement={metrics.get('wnn_totalvi_knn_agreement', 'NA')}, "
                f"label-purity state={label_state}, and batch-mixing entropy="
                f"{metrics.get('batch_mixing_entropy', 'NA')}; {totalvi_clause}."
            ),
            "interpretation": "Numeric diagnostics are interpreted only where their reference or batch design is evaluable.",
            "limitation": "A final training ELBO alone does not establish totalVI convergence or biological accuracy.",
        },
        "figures/figure_7_annotate_cells_and.png": {
            "claim": annotation_statement,
            "evidence": (
                "joint_cluster_celltype_assignment.csv contains finite program scores for matched "
                "clusters and explicit null scores/margins for unmatched clusters; declared isotype "
                "controls are excluded from the biological heatmap and marker ranking. "
                f"protein_marker_status.json records n_markers={marker_status.get('n_markers', 'NA')} "
                f"and effect_statistic={marker_status.get('effect_statistic', 'NA')}."
            ),
            "interpretation": "Assignments summarize support from the declared surface-protein programs.",
            "limitation": "Marker-program assignments are hypothesis-generating without an independent cell-type reference.",
        },
        "figures/figure_8_test_replicate_aware.png": {
            "claim": (
                "Replicate-aware differential abundance was run."
                if da_ran else "Replicate-aware differential abundance was not estimable and was skipped."
            ),
            "evidence": f"diffabund_status.json records ran={str(da_ran).lower()} and reason={da_status.get('reason', 'not recorded')}.",
            "interpretation": (
                "Reported neighborhoods use the declared replicate-structured comparison."
                if da_ran else "The displayed counts are descriptive only; no condition effect or FDR is claimed."
            ),
            "limitation": (
                "Neighborhood inference remains sensitive to graph construction and replicate composition."
                if da_ran else "A replicate-sufficient multi-condition cohort is required for inference."
            ),
        },
    }


def _validated_figure_blocks(
    config: ReportConfig,
    facts: dict,
    adt_status: dict,
    local_narratives: dict[str, dict[str, str]],
) -> list[dict]:
    declared = facts.get("figures")
    if not isinstance(declared, list):
        raise ReportError("report_facts.json has no validated figures array")
    manifest_by_step = {
        item.get("step"): item for item in declared if isinstance(item, dict)
    }
    blocks = []
    for step, relative, width, caption in EXPECTED_FIGURES:
        if step == 4:
            caption = "Figure 4. " + adt_figure_caption(adt_status)
        item = manifest_by_step.get(step)
        if not isinstance(item, dict):
            raise ReportError(f"validated figure inventory has no step {step}")
        declared_file = item.get("file")
        if declared_file is None:
            continue
        if declared_file != relative:
            raise ReportError(
                f"validated figure step {step} names {declared_file!r}, expected {relative!r}"
            )
        expected_manifest_caption = caption.removeprefix("Figure 4. ") if step == 4 else item.get("caption")
        if step == 4 and item.get("caption") != expected_manifest_caption:
            raise ReportError(
                "validated figure step 4 caption disagrees with the measured isotype status"
            )
        _file_evidence(config, relative)
        narrative = local_narratives.get(relative)
        if narrative is None:
            raise ReportError(f"validated figure {relative} has no local narrative")
        blocks.append(_figure(relative, width, caption, narrative))
    return blocks


def build_report_content(config: ReportConfig) -> dict:
    """Serialize exactly the current renderer's facts, tables, figures, and narrative."""
    facts = _load_json(config, "report_facts.json")
    provenance = _load_json(config, "provenance_manifest.json", required=False)
    adt_status = _load_json(config, "adt_normalization_status.json")
    try:
        validate_adt_normalization_status(adt_status)
    except ValueError as exc:
        raise ReportError(str(exc)) from exc
    totalvi_status = _load_json(config, "totalvi_status.json", required=False)
    da_status = _load_json(config, "diffabund_status.json", required=False)
    label_status = _load_json(config, "label_purity_status.json", required=False)
    marker_status = _load_json(config, "protein_marker_status.json")
    _validate_protein_marker_contract(config, marker_status, adt_status)

    n_input = _fact(facts, "n_input_cells")
    n_joint = _fact(facts, "n_joint_cells")
    n_dropped = _fact(facts, "n_cells_dropped_qc")
    n_clusters = _fact(facts, "n_joint_clusters")
    n_adt = _fact(facts, "n_adt_features")
    median_weight = _fact(facts, "median_rna_modality_weight")
    identity_violations = (provenance.get("assay") or {}).get("identity_violations", "NA")
    weight_violations = (provenance.get("wnn") or {}).get(
        "modality_weight_out_of_unit_interval", "NA"
    )
    n_samples = facts.get("n_samples", 1)
    sample_phrase = "a single sample" if n_samples <= 1 else f"{n_samples} samples"

    raw_weight = facts.get("median_rna_modality_weight")
    try:
        numeric_weight = float(raw_weight) if raw_weight is not None else None
    except (ValueError, TypeError):
        numeric_weight = None
    if numeric_weight is not None and (numeric_weight < 0.25 or numeric_weight > 0.75):
        dominant = "surface protein (ADT)" if numeric_weight < 0.5 else "transcriptome (RNA)"
        weight_sentence = (
            f"The median RNA modality weight ({median_weight}) indicates {dominant} dominated "
            "the joint graph rather than both contributing equally."
        )
    else:
        weight_sentence = (
            f"The median RNA modality weight ({median_weight}) shows both modalities contributed "
            "to the joint graph rather than one dominating."
        )

    dataset_phrase = sample_phrase
    annotation_statement = _annotation_statement(config)
    local_narratives = _figure_local_narratives(
        config,
        facts,
        provenance,
        adt_status,
        totalvi_status,
        da_status,
        label_status,
        marker_status,
        annotation_statement,
    )
    figure_blocks = _validated_figure_blocks(config, facts, adt_status, local_narratives)
    figures_by_step = {
        block["path"]: block for block in figure_blocks
    }

    results_blocks: list[dict] = [
        _paragraph(
            f"QC retained {n_joint} of {n_input} cells ({n_dropped} removed). WNN produced "
            f"{n_clusters} joint clusters; the median per-cell RNA modality weight was "
            f"{median_weight}, indicating how much transcriptome vs. surface protein drove the "
            "joint neighbor graph."
        )
    ]
    for relative in (
        "figures/figure_3_qc_each_modality.png",
        "figures/figure_4_denoise_antibody_derived.png",
        "figures/figure_5_build_the_wnn.png",
    ):
        if relative in figures_by_step:
            results_blocks.append(figures_by_step[relative])
    results_blocks.extend([
        _subheading("Integration metrics"),
        _paragraph(_lp_sentence(label_status)),
    ])
    scorecard = _csv_block(
        config,
        "joint_qc_scorecard.csv",
        [170, 70, 230],
        max_rows=20,
    )
    if scorecard:
        results_blocks.append(scorecard)
    totalvi_figure = figures_by_step.get("figures/figure_6_compute_the_totalvi.png")
    if totalvi_figure:
        results_blocks.append(totalvi_figure)
    results_blocks.extend(_concordance_blocks(config, label_status))
    results_blocks.append(_subheading("Cluster annotation &amp; markers"))
    results_blocks.append(_paragraph(annotation_statement))
    annotation_figure = figures_by_step.get("figures/figure_7_annotate_cells_and.png")
    if annotation_figure:
        results_blocks.append(annotation_figure)
    assignment = _csv_block(
        config,
        "joint_cluster_celltype_assignment.csv",
        [40, 70, 110, 90, 55, 55, 40],
        max_rows=40,
    )
    if assignment:
        results_blocks.append(assignment)
    results_blocks.append(_subheading("Modality weights"))
    weights = _csv_block(
        config,
        "modality_weights_summary.csv",
        [70, 65, 65, 55, 55, 55, 75],
        max_rows=30,
    )
    if weights:
        results_blocks.append(weights)
    results_blocks.append(_subheading("Differential abundance"))
    da_figure = figures_by_step.get("figures/figure_8_test_replicate_aware.png")
    if da_figure:
        results_blocks.append(da_figure)

    limitations = [
        f"Label purity is reported only against an independent reference label and is "
        f"'{label_status.get('state', '?')}' here "
        f"({label_status.get('reason') or 'independent reference established'}); scoring clusters "
        "against a cluster-derived annotation is circular and is never reported as integration "
        "quality — the annotation is evaluated separately as concordance. Evidence: "
        "label_purity_status.json.",
        adt_normalization_sentence(adt_status),
        "Per-cell WNN modality weights describe graph influence; a near-degenerate distribution "
        "(median ≈ 0 or 1) means one modality dominated and the joint embedding is close to "
        "single-modality.",
        f"totalVI outputs are model predictions, not measurements "
        f"({'trained here' if totalvi_status.get('ran') else 'documented skip'}); cross-modality "
        "imputation must be validated against measured data.",
        f"Differential abundance "
        f"{'ran with the replicate-structured design' if da_status.get('ran') else 'was skipped for insufficient replication'}; "
        "FDR is never fabricated when replication is absent.",
        f"This is a hypothesis-generating analysis on {dataset_phrase}; joint clusters, metrics, "
        "and neighborhoods are exploratory and not validated at any threshold.",
    ]

    content = {
        "schema": CONTENT_SCHEMA,
        "title": config.title,
        "subtitle": config.subtitle,
        "generated_on": dt.date.today().isoformat(),
        "front_matter": {
            "infographic": {
                "path": config.infographic_name,
                "width": 360,
                "caption": "Workflow infographic (generated with the Biomni GenerateImage tool).",
            },
            "callout": (
                "A paired CITE-seq bundle was confirmed to carry Gene Expression and Antibody "
                f"Capture (not Peaks) before any integration. Of {n_input} input cells, {n_joint} "
                f"passed joint RNA+ADT QC ({n_dropped} removed) and were embedded with Seurat WNN "
                f"into {n_clusters} joint clusters over {n_adt} antibody-derived tags. The median "
                f"per-cell RNA modality weight was {median_weight} (ADT = 1 − this). "
                f"Assay-identity violations: {identity_violations}; out-of-[0,1] modality weights: "
                f"{weight_violations}."
            ),
        },
        "sections": [
            {
                "heading": "Task Context",
                "blocks": [_paragraph(
                    f"This report integrates {config.sample_description}: a "
                    "filtered_feature_bc_matrix.h5 carrying paired RNA and antibody-derived tags "
                    "(ADT), a raw/unfiltered matrix for empty-droplet background, and per-cell "
                    "condition/replicate metadata. The governing decision is whether the modalities "
                    "are genuinely paired RNA+ADT — rather than paired RNA+ATAC multiome, which is "
                    "routed to scatac-multiome-analysis — and, if so, which joint cell "
                    "states and modality weights emerge when transcriptome and surface protein are "
                    f"integrated. The run is hypothesis-generating on {sample_phrase}."
                )],
            },
            {
                "heading": "Methods & Sources",
                "blocks": [
                    _subheading("Regime &amp; assay-identity gate"),
                    _paragraph(
                        "The design regime is established from the actual 10x feature types before "
                        "any integration. The bundle resolved to the CITE-seq route with "
                        f"{identity_violations} assay-identity violation(s): the declared assay is "
                        "reconciled against the observed feature types and the named isotype "
                        "controls, and a valid declaration attached to mismatched feature types is "
                        "excluded rather than force-integrated [1]."
                    ),
                    _subheading("Per-modality QC and ADT denoising"),
                    _paragraph(
                        "RNA QC (counts, genes, percent-mitochondrial) and ADT QC (total protein "
                        "counts) use modality-specific thresholds; a cell is retained only if it "
                        "passes both. " + adt_normalization_sentence(adt_status)
                    ),
                    _subheading("Joint embedding, totalVI &amp; annotation"),
                    _paragraph(
                        "A Seurat Weighted Nearest Neighbor (WNN) joint embedding was built from "
                        "RNA PCA and ADT PCA, yielding per-cell modality weights asserted to lie in "
                        f"the closed unit interval [0,1] ({weight_violations} out-of-interval) [3]. "
                        "A totalVI probabilistic model was "
                        f"{'trained as an alternative latent space' if totalvi_status.get('ran') else 'recorded as a documented skip'} "
                        f"({totalvi_status.get('reason', '')}) [4]. Joint clusters were evaluated "
                        "against a built-in PBMC surface-protein panel, yielding a supported label "
                        "or explicit unassigned state with confidence/margin flags; per-cluster RNA "
                        "markers were ranked with a Wilcoxon test and RNA log-fold change. ADT "
                        "markers excluded isotype controls and used the arithmetic mean normalized "
                        "ADT-score difference between each cluster and all other cells; no log2 fold "
                        "change was applied to DSB standardized scores [3]."
                    ),
                    _subheading("Integration metrics &amp; differential abundance"),
                    _paragraph(
                        "Integration quality (cluster silhouette, label purity, kNN batch-mixing "
                        "entropy) was scored with scikit-learn into a joint QC scorecard. "
                        "Replicate-aware differential abundance used Milo only when at least two "
                        "biological replicates per condition and more than one condition were "
                        f"present; here it {'ran' if da_status.get('ran') else 'was skipped and flagged'} "
                        f"({da_status.get('reason', '')}) [5]."
                    ),
                ],
            },
            {"heading": "Results", "page_break_before": True, "blocks": results_blocks},
            {
                "heading": "Conclusions & Interpretation",
                "blocks": [_paragraph(
                    f"The bundle is genuinely paired RNA+ADT and integrates into {n_clusters} joint "
                    f"clusters resolved by combining transcriptome and surface protein. "
                    f"{weight_sentence} {annotation_statement} Integration metrics summarize cluster "
                    "separation, label agreement, and batch mixing. These joint states are "
                    "exploratory and intended to generate hypotheses for confirmation in "
                    "independent data."
                )],
            },
            {
                "heading": "Next Steps",
                "blocks": [_paragraph(
                    "Validate supported cluster labels against an independent reference or orthogonal "
                    "marker evidence, inspect explicitly unassigned or low-confidence clusters, and "
                    "repeat condition comparisons in a replicate-sufficient cohort before treating "
                    "these exploratory states as biological conclusions."
                )],
            },
            {
                "heading": "Limitations",
                "blocks": [{"type": "bullet_list", "items": limitations}],
            },
            {
                "heading": "References",
                "blocks": [
                    _paragraph(f"[{index}] {reference}")
                    for index, reference in enumerate(REFERENCES, 1)
                ],
            },
        ],
    }

    used = {
        "report_facts.json",
        "figures/manifest.json",
        config.infographic_name,
    }
    optional_jsons = (
        "provenance_manifest.json",
        "adt_normalization_status.json",
        "totalvi_status.json",
        "diffabund_status.json",
        "label_purity_status.json",
        "protein_marker_status.json",
    )
    optional_tables = (
        "annotation_concordance.csv",
        "joint_qc_scorecard.csv",
        "joint_cluster_celltype_assignment.csv",
        "modality_weights_summary.csv",
    )
    for relative in optional_jsons + optional_tables:
        if _result(config, relative).is_file():
            used.add(relative)
    for block in figure_blocks:
        used.add(block["path"])
    content["source_artifacts"] = [
        _file_evidence(config, relative) for relative in sorted(used)
    ]
    return content
