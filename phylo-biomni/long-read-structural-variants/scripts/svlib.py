#!/usr/bin/env python3
"""Production Sniffles2 calling, normalization, parsing, and reference checks."""
from __future__ import annotations

import hashlib
import json
import os
import pathlib
import shutil
import subprocess
import tempfile
from collections import defaultdict

import pysam
import pysam.bcftools as _bcftools  # submodule must be imported explicitly

# --- SV class canonicalization -------------------------------------------------------------------
CANON = {
    "INS": "INS", "DEL": "DEL", "DUP": "DUP", "DUP:TANDEM": "DUP", "DUP:INT": "DUP",
    "INV": "INV", "BND": "BND", "TRA": "BND", "CNV": "CNV", "DUP:CNV": "CNV",
}
KNOWN_CLASSES = ("INS", "DEL", "DUP", "INV", "BND", "CNV")
UPSTREAM_IDENTITY_SCHEMA = "phylo-long-read-qc-identity/1"
UPSTREAM_IDENTITY_VERSION = "1.0"
UPSTREAM_IDENTITY_TOOL = "long-read-qc-basecalling"


# --- tool + metadata -----------------------------------------------------------------------------
def _first_line(args: list[str]) -> str:
    try:
        r = subprocess.run(args, capture_output=True, text=True)
        return (r.stdout or r.stderr).strip().splitlines()[0]
    except Exception as exc:  # noqa: BLE001
        return f"NA:{exc}"


def tool_versions() -> dict:
    v = {
        "sniffles": _first_line(["sniffles", "--version"]),
        "cutesv": _first_line(["cuteSV", "--version"]),
    }
    try:
        v["pysam"] = pysam.__version__
    except Exception:  # noqa: BLE001
        pass
    return v


def load_run_metadata(path: str) -> dict:
    data = json.loads(pathlib.Path(path).read_text())
    if not isinstance(data, dict):
        raise ValueError("run.json must be a JSON object")
    return data


def file_sha256(path: str) -> str:
    digest = hashlib.sha256()
    with pathlib.Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_upstream_identity(path: str, *, run_json_path: str) -> dict:
    """Validate the anchor artifact without reimplementing its chemistry/identity gate."""
    identity = json.loads(pathlib.Path(path).read_text())
    required = {
        "schema", "schema_version", "tool", "input_manifest_sha256", "run_json_sha256",
        "platform_normalized", "declared_chemistry", "basecaller", "basecaller_model",
        "basecaller_mode", "molecule_type", "reference_build", "read_type", "applicable",
        "model_chemistry_compatible", "model_chemistry_mismatch_violations",
        "declaration_identity_status", "observed_identity_status", "observed_identities",
        "qc_metrics_sha256", "coverage_estimate_status", "coverage_estimate_x", "detail",
    }
    if not isinstance(identity, dict) or not required.issubset(identity):
        missing = sorted(required - set(identity if isinstance(identity, dict) else ()))
        raise ValueError(f"upstream identity artifact missing required fields: {missing}")
    if (identity["schema"] != UPSTREAM_IDENTITY_SCHEMA
            or identity["schema_version"] != UPSTREAM_IDENTITY_VERSION
            or identity["tool"] != UPSTREAM_IDENTITY_TOOL):
        raise ValueError("upstream identity artifact has unsupported schema/tool")
    for field in ("input_manifest_sha256", "run_json_sha256", "qc_metrics_sha256"):
        value = identity[field]
        if not isinstance(value, str) or len(value) != 64 or any(c not in "0123456789abcdef" for c in value):
            raise ValueError(f"upstream identity artifact has invalid {field}")
    if identity["run_json_sha256"] != file_sha256(run_json_path):
        raise ValueError("upstream identity run_json_sha256 does not match selected run.json")
    if identity["platform_normalized"] not in {"ont", "pacbio"}:
        raise ValueError("upstream identity has unsupported normalized platform")
    if identity["observed_identity_status"] not in {"pass", "fail", "not_evaluable"}:
        raise ValueError("upstream identity has invalid observed_identity_status")
    if type(identity["applicable"]) is not bool or type(identity["model_chemistry_compatible"]) is not bool:
        raise ValueError("upstream identity applicability/compatibility fields must be booleans")
    violations = identity["model_chemistry_mismatch_violations"]
    if type(violations) is not int or violations not in {0, 1}:
        raise ValueError("upstream identity mismatch count must be integer 0 or 1")
    if not isinstance(identity["observed_identities"], list):
        raise ValueError("upstream identity observed_identities must be a list")
    read_type = identity["read_type"]
    if ((identity["platform_normalized"] == "pacbio" and read_type not in {"clr", "hifi"})
            or (identity["platform_normalized"] == "ont" and read_type is not None)):
        raise ValueError("upstream identity read_type disagrees with normalized platform")
    if identity["declaration_identity_status"] != "pass" or identity["observed_identity_status"] == "fail":
        raise ValueError(f"upstream identity did not pass: {identity['detail']}")
    if identity["applicable"]:
        if (identity["model_chemistry_compatible"] is not True
                or identity["model_chemistry_mismatch_violations"] != 0):
            raise ValueError("upstream identity records an applicable chemistry mismatch")
    elif identity["model_chemistry_compatible"] is not False:
        raise ValueError("non-applicable upstream chemistry check cannot claim compatibility")
    return identity


def validate_platform_choice(identity: dict, operator_platform: str) -> str:
    expected = "ont"
    if identity["platform_normalized"] == "pacbio":
        if identity.get("read_type") != "hifi":
            raise ValueError("the SV caller presets support sealed PacBio HiFi, not PacBio CLR")
        expected = "hifi"
    if operator_platform != expected:
        raise ValueError(
            f"--platform {operator_platform!r} disagrees with upstream identity {expected!r}"
        )
    return expected


def coverage_scaled_support(identity: dict) -> dict:
    if identity.get("coverage_estimate_status") != "computed":
        raise ValueError("coverage-scaled caller support requires computed upstream coverage")
    coverage = identity.get("coverage_estimate_x")
    if not isinstance(coverage, (int, float)) or isinstance(coverage, bool) or coverage <= 0:
        raise ValueError("upstream coverage_estimate_x must be a positive number")
    import math
    scaled = max(2, int(math.ceil(float(coverage) * 0.10)))
    return {
        "coverage_estimate_x": float(coverage),
        "scaling_fraction": 0.10,
        "sniffles2_min_support": scaled,
        "cutesv_min_support": max(10, scaled),
    }


def facts_derived_infographic(facts: dict) -> tuple[dict, str]:
    """Build the infographic claim ledger and exact prompt only from computed facts."""
    claims = {
        "schema": "phylo-facts-derived-infographic-claims/1",
        "source_artifact": "sv/facts_payload.json",
        "claims": [
            {"text": "BAM/reference identity passed", "field": "bam_reference_identity_passed",
             "value": facts["bam_reference_identity_passed"]},
            {"text": f"Sniffles2 and {facts['second_caller']} callsets", "field": "second_caller",
             "value": facts["second_caller"]},
            {"text": "Both callsets normalized and indexed", "field": "normalization_indexing_passed",
             "value": facts["normalization_indexing_passed"]},
            {"text": "Class-aware caller concordance computed", "field": "shared_sv_count",
             "value": facts["shared_sv_count"]},
        ],
        "forbidden_claims": ["accuracy", "sensitivity", "precision", "read quality",
                             "basecalling quality", "clinical validity"],
    }
    lines = [
        "Create a clean scientific workflow infographic using only these facts-derived claim texts:",
        *[f"- {claim['text']}" for claim in claims["claims"]],
        "Use a restrained professional genomics style and clear arrows.",
        "Do not add numerical results, sample/accession identifiers, logos, or any of these claims: "
        + ", ".join(claims["forbidden_claims"]) + ".",
        "Save exactly as workflow_infographic_long_read_sv.png.",
    ]
    return claims, "\n".join(lines) + "\n"


# --- contigs / reference-build consistency -------------------------------------------------------
def fasta_contigs(ref: str) -> dict:
    fa = pysam.FastaFile(ref)
    try:
        return {n: l for n, l in zip(fa.references, fa.lengths)}
    finally:
        fa.close()


def bam_contigs(bam: str) -> dict:
    af = pysam.AlignmentFile(bam, "rb")
    try:
        return {sq["SN"]: {"length": sq["LN"], "assembly": sq.get("AS")}
                for sq in af.header.get("SQ", [])}
    finally:
        af.close()


def reference_build_consistency(bam: str, reference: str, run_meta: dict) -> dict:
    """Count reference-build inconsistencies across BAM, reference, and run.json.

    A consistent run reports zero. The declared build is compared to the BAM @SQ assembly (AS) tag
    when present; BAM contigs must exist in the reference with matching lengths.
    """
    ref = fasta_contigs(reference)
    bam_sq = bam_contigs(bam)
    violations = 0
    details: list[str] = []
    declared = run_meta.get("reference_build")

    for name, meta in bam_sq.items():
        if name not in ref or ref[name] != meta["length"]:
            violations += 1
            details.append(f"BAM contig {name}:{meta['length']} absent/length-mismatched in reference")

    asm = next((m["assembly"] for m in bam_sq.values() if m.get("assembly")), None)
    if declared and asm and declared != asm:
        violations += 1
        details.append(f"run.json.reference_build {declared!r} != BAM assembly tag {asm!r}")

    return {
        "reference_build_mismatch_violations": violations,
        "declared_reference_build": declared,
        "bam_assembly_tag": asm,
        "bam_sq_reference": sorted(bam_sq),
        "detail": "; ".join(details) or "reference build consistent across BAM/reference/run.json",
    }


# --- SV calling ----------------------------------------------------------------------------------
def call_sniffles(bam: str, reference: str, out_vcf: str, *, threads: int = 1,
                  min_support: int = 2, min_svlen: int = 50) -> str:
    # --all-contigs: Sniffles2 skips small contigs by default, which drops small references,
    # plasmids, and targeted/subset BAMs; enabling it is harmless on full genomes.
    argv = ["sniffles", "--input", bam, "--vcf", out_vcf, "--reference", reference,
            "--threads", str(threads), "--minsupport", str(min_support),
            "--minsvlen", str(min_svlen), "--all-contigs", "--allow-overwrite"]
    subprocess.run(argv, check=True, capture_output=True, text=True)
    return out_vcf


def call_cutesv(bam: str, reference: str, out_vcf: str, workdir: str, *, threads: int = 1,
                platform: str = "ont", min_support: int = 2, min_size: int = 50) -> str:
    os.makedirs(workdir, exist_ok=True)
    if platform.lower() in ("hifi", "pacbio", "ccs"):
        preset = ["--max_cluster_bias_INS", "1000", "--diff_ratio_merging_INS", "0.9",
                  "--max_cluster_bias_DEL", "1000", "--diff_ratio_merging_DEL", "0.5"]
    else:
        preset = ["--max_cluster_bias_INS", "100", "--diff_ratio_merging_INS", "0.3",
                  "--max_cluster_bias_DEL", "100", "--diff_ratio_merging_DEL", "0.3"]
    argv = ["cuteSV", bam, reference, out_vcf, workdir, "--threads", str(threads),
            "--min_support", str(min_support), "--min_size", str(min_size),
            "--genotype", *preset]
    subprocess.run(argv, check=True, capture_output=True, text=True)
    return out_vcf


# --- normalization ------------------------------------------------------------------------------
def normalize_vcf(in_vcf: str, reference: str, out_vcf_gz: str) -> str:
    """Sort, left-align/split multiallelics (bcftools norm), bgzip, and tabix-index."""
    tmp = tempfile.mkdtemp(prefix="svnorm_")
    try:
        sorted_vcf = os.path.join(tmp, "sorted.vcf.gz")
        _bcftools.sort("-Oz", "-o", sorted_vcf, in_vcf, catch_stdout=False)
        _bcftools.norm("-f", reference, "-c", "w", "-m", "-any", "-Oz",
                       "-o", out_vcf_gz, sorted_vcf, catch_stdout=False)
        pysam.tabix_index(out_vcf_gz, preset="vcf", force=True)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return out_vcf_gz


def is_valid_indexed_vcf(vcf_gz: str) -> bool:
    """True when the VCF is bgzipped, tabix-indexed, and openable with fetch by pysam."""
    if not (os.path.exists(vcf_gz) and os.path.exists(vcf_gz + ".tbi")):
        return False
    try:
        vf = pysam.VariantFile(vcf_gz)
        _ = vf.header
        vf.close()
        return True
    except Exception:  # noqa: BLE001
        return False


# --- SV record parsing --------------------------------------------------------------------------
def _canon_svtype(rec) -> str:
    st = rec.info.get("SVTYPE")
    if isinstance(st, (tuple, list)):
        st = st[0] if st else None
    if st is None:
        for a in (rec.alts or ()):
            if a and a.startswith("<") and a.endswith(">"):
                st = a[1:-1]
                break
            if a and ("[" in a or "]" in a):
                st = "BND"
                break
    if st is None:
        return "OTHER"
    key = str(st).upper()
    if key in CANON:
        return CANON[key]
    head = key.split(":")[0]
    return CANON.get(head, head if head in KNOWN_CLASSES else "OTHER")


def _svlen(rec) -> int:
    sl = rec.info.get("SVLEN")
    if isinstance(sl, (tuple, list)):
        sl = sl[0] if sl else None
    if sl is not None:
        try:
            return abs(int(sl))
        except (TypeError, ValueError):
            pass
    try:
        return abs(int(rec.stop) - int(rec.pos))
    except Exception:  # noqa: BLE001
        return 0


def read_sv_vcf(vcf: str, *, min_size: int = 50, pass_only: bool = True) -> list[dict]:
    vf = pysam.VariantFile(vcf)
    out: list[dict] = []
    try:
        header_samples = list(vf.header.samples)
        if len(header_samples) > 1:
            raise ValueError(
                "multi-sample VCF requires an explicit sample-selection contract; refusing first-sample fallback"
            )
        for rec in vf:
            filt = list(rec.filter.keys())
            is_pass = (not filt) or filt == ["PASS"] or filt == ["."]
            if pass_only and not is_pass:
                continue
            cls = _canon_svtype(rec)
            size = _svlen(rec)
            if cls != "BND" and size < min_size:
                continue
            sample_items = list(rec.samples.items())
            if len(sample_items) > 1:
                raise ValueError(
                    "multi-sample VCF requires an explicit sample-selection contract; refusing first-sample fallback"
                )
            sample_id, sample = sample_items[0] if sample_items else (None, None)
            genotype = None
            genotype_quality = None
            sample_support = None
            if sample is not None:
                gt = sample.get("GT")
                if gt is not None:
                    separator = "|" if getattr(sample, "phased", False) else "/"
                    genotype = separator.join("." if allele is None else str(allele) for allele in gt)
                genotype_quality = sample.get("GQ")
                for key in ("DV", "RE", "SUPPORT"):
                    if sample.get(key) is not None:
                        sample_support = sample.get(key)
                        break
            if sample_support is None:
                for key in ("RE", "SUPPORT", "SU"):
                    if rec.info.get(key) is not None:
                        sample_support = rec.info.get(key)
                        break
            if isinstance(sample_support, (tuple, list)):
                sample_support = sample_support[0] if sample_support else None
            out.append({"chrom": rec.chrom, "pos": int(rec.pos), "end": int(rec.stop),
                        "svtype": cls, "svlen": size, "id": rec.id or "",
                        "filter": ";".join(filt) if filt else "PASS",
                        "quality": None if rec.qual is None else float(rec.qual),
                        "sample_id": sample_id, "genotype": genotype,
                        "genotype_quality": genotype_quality,
                        "read_support": sample_support})
    finally:
        vf.close()
    return out


def counts_by_class(calls: list[dict]) -> dict:
    c: dict = defaultdict(int)
    for r in calls:
        c[r["svtype"]] += 1
    return dict(c)


def _size_sim(x: int, y: int) -> float:
    return 1.0 if max(x, y) == 0 else min(x, y) / max(x, y)


def concordance(calls_a: list[dict], calls_b: list[dict], *, refdist: int = 500,
                size_sim: float = 0.7) -> dict:
    """Class-aware one-to-one caller agreement; this is not an accuracy estimate."""
    classes = sorted({c["svtype"] for c in calls_a + calls_b})
    by_class: dict[str, dict[str, int]] = {}
    for cls in classes:
        a_list = [c for c in calls_a if c["svtype"] == cls]
        b_list = [c for c in calls_b if c["svtype"] == cls]
        used_b: set[int] = set(); shared = 0
        for a in a_list:
            candidates = []
            for index, b in enumerate(b_list):
                if index in used_b or a["chrom"] != b["chrom"]:
                    continue
                distance = abs(a["pos"] - b["pos"])
                if distance <= refdist and (cls == "BND" or _size_sim(a["svlen"], b["svlen"]) >= size_sim):
                    candidates.append((distance, index))
            if candidates:
                _, best = min(candidates); used_b.add(best); shared += 1
        by_class[cls] = {"a": len(a_list), "b": len(b_list), "shared": shared,
                         "a_unique": len(a_list)-shared, "b_unique": len(b_list)-shared}
    totals = {key: sum(row[key] for row in by_class.values())
              for key in ("a", "b", "shared", "a_unique", "b_unique")}
    totals["union"] = totals["shared"] + totals["a_unique"] + totals["b_unique"]
    return {"by_class": by_class, "totals": totals}
