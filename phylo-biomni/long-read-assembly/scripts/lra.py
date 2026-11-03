"""Core long-read de novo assembly, polishing, and assembly-QC library.

Deterministic and dependency-light (numpy/matplotlib optional for figures). The headline assembly-QC metrics (contiguity, reference-
free QV, reference genome fraction, marker completeness) are computed here and are the single source
source of truth for the facts artifact and receipt.

INHERITANCE (declared, not silent): the read-parsing, run.json provenance schema, the model/chemistry
compatibility gate, and the contamination-screen contract are inherited verbatim from the upstream
`long-read-qc-basecalling` package. This module does not redefine those schemas; it consumes reads
assumed to have passed upstream read-level QC and adds the assembly layer.

HONESTY: nothing here fabricates an assembly. Production assembly routes to Flye/hifiasm/Verkko
(Biomni HPC or a user-supplied binary); when neither a real assembler nor a supplied draft is
available the run stops cleanly and records status rather than inventing contigs.
"""
from __future__ import annotations
import collections, gzip, hashlib, json, math, os, pathlib, shutil, subprocess

# =============================================================================================
# INHERITED FROM long-read-qc-basecalling — identical schemas; do not diverge.
# =============================================================================================
def open_maybe_gzip(path):
    path = str(path)
    return gzip.open(path, "rt") if path.endswith(".gz") else open(path, "rt")


def read_fastq(path):
    """Yield (name, sequence, quality_string) for each record. (Inherited from long-read-qc-basecalling.)"""
    with open_maybe_gzip(path) as fh:
        while True:
            header = fh.readline()
            if not header:
                break
            seq = fh.readline().rstrip("\n")
            plus = fh.readline()
            qual = fh.readline().rstrip("\n")
            if not header.startswith("@") or not plus.startswith("+"):
                raise ValueError(f"malformed FASTQ near record starting {header[:40]!r}")
            if len(seq) != len(qual):
                raise ValueError("FASTQ sequence/quality length mismatch")
            yield header[1:].rstrip("\n"), seq, qual


def read_mean_quality(qual_string):
    """Per-read mean quality as the error-probability-averaged Phred score. (Inherited from long-read-qc-basecalling.)"""
    if not qual_string:
        return 0.0
    errs = [10 ** (-(ord(c) - 33) / 10.0) for c in qual_string]
    mean_err = sum(errs) / len(errs)
    if mean_err <= 0:
        return 60.0
    return -10.0 * math.log10(mean_err)


def _n50(lengths):
    """bp-weighted N50 (inherited definition): length L at which sequences >= L reach >=50% of total."""
    s = sorted(lengths, reverse=True)
    total = sum(s)
    if total == 0:
        return 0
    half, cum = total / 2.0, 0
    for L in s:
        cum += L
        if cum >= half:
            return L
    return s[-1]


UPSTREAM_IDENTITY_SCHEMA = "phylo-long-read-qc-identity/1"
UPSTREAM_IDENTITY_VERSION = "1.0"
UPSTREAM_IDENTITY_TOOL = "long-read-qc-basecalling"


def load_run_metadata(path):
    """Load the selected run.json; identity semantics belong to the upstream artifact."""
    data = json.loads(pathlib.Path(path).read_text())
    if not isinstance(data, dict):
        raise ValueError("run.json must be a JSON object")
    return data


def sha256_file(path):
    digest = hashlib.sha256()
    with pathlib.Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_string_set(values):
    digest = hashlib.sha256()
    for value in sorted(values):
        digest.update(value.encode("ascii"))
        digest.update(b"\n")
    return digest.hexdigest()


def load_upstream_identity(path, *, run_json_path):
    """Validate and consume the anchor artifact without re-running its scientific gate."""
    identity = json.loads(pathlib.Path(path).read_text())
    required = {
        "schema", "schema_version", "tool", "input_manifest_sha256", "run_json_sha256",
        "platform_normalized", "declared_chemistry", "basecaller", "basecaller_model",
        "basecaller_mode", "molecule_type", "reference_build", "read_type", "applicable",
        "model_chemistry_compatible", "model_chemistry_mismatch_violations",
        "declaration_identity_status", "observed_identity_status", "observed_identities",
        "qc_metrics_sha256", "coverage_estimate_status", "detail",
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
    if identity["run_json_sha256"] != sha256_file(run_json_path):
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


def build_provenance(run_meta, tool_versions=None, assembly_meta=None, read_type=None):
    """Inherited provenance echo + assembly-specific additions (assembler/polisher/read_type)."""
    prov = {
        "platform": run_meta.get("platform"),
        "flowcell_chemistry": run_meta.get("flowcell_chemistry"),
        "sequencing_kit": run_meta.get("sequencing_kit"),
        "basecaller": run_meta.get("basecaller"),
        "basecaller_version": run_meta.get("basecaller_version"),
        "basecaller_model": run_meta.get("basecaller_model"),
        "basecaller_mode": run_meta.get("basecaller_mode"),
        "molecule_type": run_meta.get("molecule_type"),
        "reference_build": run_meta.get("reference_build"),
        "sample_id": run_meta.get("sample_id"),
        "run_id": run_meta.get("run_id"),
        "read_type": read_type,
        "tool_versions": tool_versions or {},
    }
    if assembly_meta:
        prov["assembly"] = assembly_meta
    return prov


def tool_versions():
    versions = {}
    for mod in ("numpy", "matplotlib", "mappy"):
        try:
            m = __import__(mod)
            versions[mod] = str(getattr(m, "__version__", "?"))
        except Exception:
            versions[mod] = "not_installed"
    for binary in ("flye", "hifiasm", "verkko", "raven", "canu", "unicycler",
                   "minimap2", "racon", "medaka_consensus", "quast.py", "busco", "checkm2"):
        versions[binary] = "present" if shutil.which(binary) else "not_installed"
    return versions


# =============================================================================================
# READ TYPE + ASSEMBLER COMPATIBILITY — reject incompatible assemblers.
# =============================================================================================
READ_TYPES = ("ont-simplex", "ont-duplex", "ont-hq", "pacbio-hifi", "pacbio-clr")

# Assembler -> read types it is designed for (from R04 tool_comparison_matrix.csv + boundary doc).
# hifiasm(classic) = HiFi only; Verkko needs HiFi(+ONT-UL); Flye/Canu span all with the right mode;
# Raven/wtdbg2 = noisy long reads; Unicycler = bacterial (ONT/PacBio, hybrid).
ASSEMBLER_COMPAT = {
    "flye":      {"ont-simplex", "ont-duplex", "ont-hq", "pacbio-clr", "pacbio-hifi"},
    "hifiasm":   {"pacbio-hifi"},
    "verkko":    {"pacbio-hifi"},                       # + ONT-UL as an add-on, not standalone
    "canu":      {"ont-simplex", "ont-duplex", "ont-hq", "pacbio-clr", "pacbio-hifi"},
    "raven":     {"ont-simplex", "ont-duplex", "ont-hq", "pacbio-clr"},
    "wtdbg2":    {"ont-simplex", "ont-duplex", "ont-hq", "pacbio-clr"},
    "unicycler": {"ont-simplex", "ont-duplex", "ont-hq", "pacbio-clr", "pacbio-hifi"},
    "provided_draft": set(READ_TYPES),
}
# The Flye mode flag that must match the read type (mismatch here is the classic silent error).
FLYE_MODE = {"ont-simplex": "--nano-raw", "ont-duplex": "--nano-hq", "ont-hq": "--nano-hq",
             "pacbio-clr": "--pacbio-raw", "pacbio-hifi": "--pacbio-hifi"}


def derive_read_type(run_meta):
    """Use an explicit run.json `read_type` if present and valid; otherwise derive from platform +
    kit/model + basecaller_mode. Returns a value in READ_TYPES or 'unknown'."""
    rt = str(run_meta.get("read_type", "")).strip().lower()
    if rt in READ_TYPES:
        return rt
    platform = str(run_meta.get("platform", "")).strip().upper()
    mode = str(run_meta.get("basecaller_mode", "")).strip().lower()
    kit = str(run_meta.get("sequencing_kit", "")).lower()
    model = str(run_meta.get("basecaller_model", "")).lower()
    if platform == "PACBIO":
        if "hifi" in kit or "hifi" in model or "ccs" in model:
            return "pacbio-hifi"
        return "pacbio-clr"
    if platform == "ONT":
        if mode == "duplex":
            return "ont-duplex"
        if "sup" in model or "hac" in model:
            return "ont-hq"
        return "ont-simplex"
    return "unknown"


def derive_read_type_from_identity(identity):
    """Derive assembler read type only from the already validated anchor artifact."""
    if identity["platform_normalized"] == "pacbio":
        return "pacbio-hifi" if identity["read_type"] == "hifi" else "pacbio-clr"
    mode = str(identity["basecaller_mode"]).strip().lower()
    if mode == "duplex":
        return "ont-duplex"
    model = str(identity["basecaller_model"]).lower()
    return "ont-hq" if "sup" in model or "hac" in model else "ont-simplex"


def check_assembler_readtype(assembler, read_type):
    """Reject an assembler incompatible with the declared read type. Returns a dict with
    incompatible_violations (0 or 1). This is the resource-identity witness for the assembly guard."""
    assembler = str(assembler).strip().lower()
    read_type = str(read_type).strip().lower()
    compat_set = ASSEMBLER_COMPAT.get(assembler)
    if compat_set is None:
        return {"assembler": assembler, "read_type": read_type, "compatible": False,
                "incompatible_violations": 1, "applicable": True,
                "detail": f"unknown assembler {assembler!r}; cannot verify read-type compatibility."}
    compatible = read_type in compat_set
    detail = ("compatible" if compatible else
              f"assembler {assembler!r} is not designed for read type {read_type!r} "
              f"(supported: {sorted(compat_set)}).")
    return {"assembler": assembler, "read_type": read_type, "compatible": compatible,
            "incompatible_violations": 0 if compatible else 1, "applicable": True,
            "supported_read_types": sorted(compat_set),
            "flye_mode": FLYE_MODE.get(read_type) if assembler == "flye" else None,
            "detail": detail}


# =============================================================================================
# POLISHING ORDER GUARD + MEDAKA (research-only) GATE.
# =============================================================================================
# Accuracy rank: long-read rough (racon) < long-read NN consensus (medaka) < short-read (nextpolish/
# polca). "No less-accurate polisher after a more-accurate one" => ranks must be non-decreasing.
POLISHER_RANK = {"racon": 1, "medaka": 2, "nextpolish": 3, "polca": 3}
# Medaka is Oxford Nanopore Public License (research-use-only; commercial use prohibited) — gate it
# exactly like Dorado in long-read-qc-basecalling: never bundled, user-supplied/licensed, clean stop when absent.
RESEARCH_ONLY_POLISHERS = {"medaka"}


def check_polish_order(steps):
    """steps: ordered list of polisher names. Reject a less-accurate-after-more-accurate order."""
    ranks = []
    for s in steps:
        s = str(s).strip().lower()
        if s in ("none", ""):
            continue
        if s not in POLISHER_RANK:
            return {"order_ok": False, "violations": 1, "steps": steps,
                    "detail": f"unknown polisher {s!r}."}
        ranks.append((s, POLISHER_RANK[s]))
    violations = 0
    detail = "polishing order is accuracy-non-decreasing."
    for i in range(1, len(ranks)):
        if ranks[i][1] < ranks[i - 1][1]:
            violations = 1
            detail = (f"polisher {ranks[i][0]!r} (rank {ranks[i][1]}) applied after "
                      f"{ranks[i-1][0]!r} (rank {ranks[i-1][1]}): less-accurate-after-more-accurate.")
            break
    return {"order_ok": violations == 0, "violations": violations, "steps": steps, "detail": detail}


def medaka_gate(requested_polishers, medaka_bin="medaka_consensus"):
    """Hard commercial-safety refusal for research-use-only polishers. Never fabricates a polish.

    Medaka (and any tool under the Oxford Nanopore Technologies Public License) is research-use-only
    (commercial use prohibited). This skill does NOT offer it: it is never run, never bundled, and
    there is NO user-licensed adapter or 'ready'/would-run path. A request for a research-only
    polisher is refused unconditionally (regardless of whether a binary is on PATH) and the run
    falls back to Racon (MIT)/none. Availability is deliberately not probed, so no restricted adapter
    is retained. `medaka_available` is always False by construction."""
    requested = [str(p).strip().lower() for p in requested_polishers]
    research = [p for p in requested if p in RESEARCH_ONLY_POLISHERS]
    if not research:
        return {"research_only_requested": [], "status": "not_requested",
                "medaka_available": False,
                "detail": "no research-only polisher requested."}
    return {"research_only_requested": research, "status": "refused_research_only",
            "medaka_available": False,
            "detail": ("Research-use-only polisher(s) " + ", ".join(research) + " requested. These "
                       "are distributed under the Oxford Nanopore Technologies Public License "
                       "(research purposes only; commercial use prohibited) and are NOT offered by "
                       "this skill: never run, never bundled, no user-licensed adapter. Falling back "
                       "to Racon (MIT)/none. No polish fabricated.")}


def _revcomp(s):
    return s.translate(str.maketrans("ACGT", "TGCA"))[::-1]


def _kmer_counts(reads, k):
    c = collections.Counter()
    for _, seq, _ in reads:
        for i in range(len(seq) - k + 1):
            c[seq[i:i + k]] += 1
    return c


# =============================================================================================
# ASSEMBLY QC — QUAST-style contiguity, reference-free Merqury-style QV, marker (BUSCO-proxy) completeness.
# =============================================================================================
def contiguity_metrics(contigs):
    lens = sorted((len(s) for s, _ in contigs), reverse=True)
    total = sum(lens)
    gc = 0
    for s, _ in contigs:
        gc += sum(1 for b in s if b in "GC")
    return {
        "n_contigs": len(lens),
        "assembly_length_bp": total,
        "largest_contig_bp": lens[0] if lens else 0,
        "contig_n50": _n50(lens),
        "mean_contig_bp": round(total / len(lens), 2) if lens else 0.0,
        "n_circular": sum(1 for _, c in contigs if c),
        "gc_percent": round(100.0 * gc / total, 3) if total else 0.0,
    }


def derive_solid_kmer_db(reads, k):
    """Derive the trusted-k-mer cutoff from the observed multiplicity spectrum.

    The cutoff is the first local minimum after the multiplicity-one error peak and before a
    higher-multiplicity rise. Ambiguous spectra fail closed instead of silently applying a fixed
    threshold. The returned spectrum is the complete reproduction record for the QV input set.
    """
    counts = _kmer_counts(reads, k)
    spectrum = collections.Counter(counts.values())
    if not counts or max(spectrum, default=0) < 3:
        raise ValueError("read k-mer spectrum is too shallow to derive a solid-k-mer threshold")
    maximum = max(spectrum)
    threshold = None
    for multiplicity in range(2, maximum):
        left = spectrum.get(multiplicity - 1, 0)
        here = spectrum.get(multiplicity, 0)
        right = spectrum.get(multiplicity + 1, 0)
        if here <= left and here < right:
            threshold = multiplicity
            break
    if threshold is None:
        raise ValueError("read k-mer spectrum has no reproducible error/trusted-k-mer valley")
    solid = {kmer for kmer, count in counts.items() if count >= threshold}
    if not solid:
        raise ValueError("derived solid-k-mer threshold retained no read k-mers")
    return {
        "threshold": threshold,
        "spectrum": sorted((multiplicity, distinct) for multiplicity, distinct in spectrum.items()),
        "distinct_kmers": len(counts),
        "solid_kmers": solid,
    }


def solid_kmer_db(reads, k, floor):
    """Legacy explicit-threshold helper retained for compatibility fixtures."""
    return {km for km, n in _kmer_counts(reads, k).items() if n >= floor}


QV_DISPLAY_CAP = 60.0  # Q60: the display ceiling for a saturated (all-k-mers-supported) consensus.


def merqury_qv_state(contigs, read_db, k, cap=QV_DISPLAY_CAP):
    """Reference-free consensus QV with an explicit saturation state.

    The per-base error rate is estimated from the fraction of assembly k-mers supported by the read
    k-mer db (Merqury convention). When 100% of assembly k-mers are supported the true Phred QV is
    unbounded above; the implementation returns the display `cap` (Q60) but flags it as a SATURATED
    LOWER BOUND, never an exact estimate. Returns a dict:
        {qv, saturated, supported_kmer_fraction, cap, state}
    where state is one of 'measured', 'capped_at_60', 'no_kmers'. Callers that need a bare scalar use
    merqury_qv(); callers that render or report the value must read `saturated`/`state` so a ceiling
    is never presented as a measured Q60."""
    total = found = 0
    for seq, circ in contigs:
        cs = seq + seq[:k - 1] if circ else seq
        upper = len(seq) if circ else len(seq) - k + 1
        for i in range(max(0, upper)):
            km = cs[i:i + k]
            total += 1
            if km in read_db or _revcomp(km) in read_db:
                found += 1
    if total == 0:
        return {"qv": 0.0, "saturated": False, "supported_kmer_fraction": 0.0,
                "cap": cap, "state": "no_kmers"}
    frac = found / total
    if frac >= 1.0:
        return {"qv": cap, "saturated": True, "supported_kmer_fraction": 1.0,
                "cap": cap, "state": "capped_at_60"}
    err = 1 - frac ** (1.0 / k)
    if err <= 0:
        return {"qv": cap, "saturated": True, "supported_kmer_fraction": round(frac, 6),
                "cap": cap, "state": "capped_at_60"}
    return {"qv": round(-10 * math.log10(err), 4), "saturated": False,
            "supported_kmer_fraction": round(frac, 6), "cap": cap, "state": "measured"}


def merqury_qv(contigs, read_db, k):
    """Backward-compatible scalar reference-free consensus QV (Phred), capped at Q60. Identical to the
    value in merqury_qv_state()['qv']; use merqury_qv_state() to learn whether the value is saturated."""
    return merqury_qv_state(contigs, read_db, k)["qv"]


def post_screen_contigs(contigs, contam_result):
    """Finding 4 helper: the contigs RETAINED after contamination screening (flagged contaminants
    removed). When the screen did not run (no flagged list), all contigs are retained, so post-screen
    contiguity equals pre-screen contiguity. `contam_result` is a screen_contamination_contigs() dict
    whose 'flagged' field lists the 0-based indices of contaminant contigs."""
    flagged = set(contam_result.get("flagged", []) or [])
    return [c for i, c in enumerate(contigs) if i not in flagged]


def _circular_identity(contig, ref):
    k = 15
    L = len(ref)
    doubled = ref + ref
    idx = collections.defaultdict(list)
    for i in range(len(doubled) - k + 1):
        idx[doubled[i:i + k]].append(i % L)
    best = 0.0
    for cand in (contig, _revcomp(contig)):
        votes = collections.Counter()
        for i in range(0, len(cand) - k + 1, 5):
            for pos in idx.get(cand[i:i + k], ()):
                votes[(pos - i) % L] += 1
        if not votes:
            continue
        off = votes.most_common(1)[0][0]
        m = sum(1 for j, b in enumerate(cand) if b == ref[(off + j) % L])
        best = max(best, m / max(len(cand), L))
    return round(best, 6)


def genome_fraction(contigs, reference_seq):
    """QUAST-style genome fraction: best per-contig identity to the (circular) reference, of the
    single best-matching contig. Returns a fraction in [0,1]."""
    best = 0.0
    for seq, _ in contigs:
        best = max(best, _circular_identity(seq, reference_seq))
    return round(best, 6)


def _kmer_containment(seq, ref, k=21):
    rc = ref + ref[:k - 1]
    rk = {rc[i:i + k] for i in range(len(ref))}
    tot = len(seq) - k + 1
    if tot <= 0:
        return 0.0
    hit = sum(1 for i in range(tot) if seq[i:i + k] in rk or _revcomp(seq[i:i + k]) in rk)
    return round(hit / tot, 6)


def busco_proxy(contigs, markers, kk=31, stride=20, min_frac=0.5):
    """Deterministic BUSCO-STYLE completeness PROXY: fraction of supplied single-copy marker
    sequences found (>= min_frac of their sampled k-mers present) in the assembly. This is a proxy
    for a fast in-package check; real BUSCO/compleasm lineage analysis is the gated production path."""
    asm = "".join(s for s, _ in contigs)
    asm2 = asm + asm  # crude circular allowance
    kmerset = {asm2[i:i + kk] for i in range(len(asm2) - kk + 1)}
    complete = 0
    duplicated = 0
    for mk in markers:
        found_orients = 0
        for cand in (mk, _revcomp(mk)):
            mkmers = [cand[i:i + kk] for i in range(0, len(cand) - kk + 1, stride)]
            if mkmers and sum(1 for x in mkmers if x in kmerset) >= max(1, int(len(mkmers) * min_frac)):
                found_orients += 1
        if found_orients >= 1:
            complete += 1
    total = len(markers)
    return {
        "busco_markers_total": total,
        "busco_complete": complete,
        "busco_complete_pct": round(100.0 * complete / total, 3) if total else 0.0,
        "busco_missing": total - complete,
    }


def screen_contamination_contigs(contigs, contaminant_ref=None, host_ref=None, method="kmer",
                                 k=21, flag_threshold=0.5, reads_path=None):
    """Screen ASSEMBLY CONTIGS for contamination. Never fabricates: a missing reference yields an
    explicit status. Both `kmer` and a requested `reference_align` use the pure-Python k-mer triage
    in this adapter; requested and effective methods remain separate so no alignment is implied."""
    result = {"method": method, "requested_method": method, "effective_method": None,
              "status": None, "triage_completed": False, "n_contigs": len(contigs),
              "contaminant_flagged_contigs": 0, "retained_after_triage_contigs": len(contigs),
              "non_contaminant_contigs": len(contigs),
              "non_contaminant_contigs_semantics": "compatibility alias for retained_after_triage_contigs",
              "flagged": [], "per_contig": [], "detail": ""}
    if method in ("reference_align", "kmer"):
        if not contaminant_ref:
            result["status"] = "not_run_no_reference"
            result["detail"] = ("contamination screen requested but no contaminant reference "
                                "supplied; reported not-run, not clean.")
            return result
        cont = _read_single_fasta(contaminant_ref)
        host = _read_single_fasta(host_ref) if host_ref else None
        if method == "reference_align":
            result["detail"] = (
                "reference_align is not executed by this deterministic adapter; "
                "used k-mer containment triage instead. "
            )
        flagged = 0
        for i, (seq, circ) in enumerate(contigs):
            c_contain = _kmer_containment(seq, cont, k)
            h_contain = _kmer_containment(seq, host, k) if host else None
            is_contam = c_contain >= flag_threshold and (h_contain is None or c_contain > h_contain)
            result["per_contig"].append({
                "contig_index": i, "length": len(seq), "circular": circ,
                "contaminant_containment": c_contain, "host_containment": h_contain,
                "flagged": bool(is_contam)})
            if is_contam:
                flagged += 1
                result["flagged"].append(i)
        retained = len(contigs) - flagged
        result.update(status="ok_kmer_triage", triage_completed=True,
                      effective_method="kmer_containment",
                      contaminant_flagged_contigs=flagged,
                      retained_after_triage_contigs=retained,
                      non_contaminant_contigs=retained,
                      detail=result["detail"] + f"k-mer containment vs supplied contaminant "
                             f"reference; {flagged} contig(s) flagged.")
        return result
    if method == "taxonomic_db":
        result["status"] = "not_run_no_db"
        result["detail"] = ("taxonomic_db (Kraken2/FCS-GX) requested; no DB bundled (FCS-GX needs "
                            "~470 GB RAM). Provide a DB invocation. Not fabricated.")
        return result
    result["status"] = "not_run_unknown_method"
    result["detail"] = f"unknown contamination method {method!r}."
    return result


def contamination_summary_sentence(result):
    """Facts/report-facing wording that preserves requested vs effective evidence strength."""
    if not result.get("triage_completed"):
        return (
            f"Contamination triage (requested {result['requested_method']}; status {result['status']}) "
            f"did not run; all {result['n_contigs']} contig(s) were not evaluated. Compatibility "
            "post-screen metrics equal the all-contig metrics, and assembly.fasta remains all-contig."
        )
    flagged = result["contaminant_flagged_contigs"]
    retained = result["retained_after_triage_contigs"]
    effective = result.get("effective_method") or "not run"
    return (
        f"Contamination triage (requested {result['requested_method']}; effective {effective}; "
        f"status {result['status']}): {flagged} of {result['n_contigs']} contig(s) flagged and "
        f"{retained} retained after triage. assembly.fasta remains the all-contig assembly."
    )


def contamination_figure_semantics(result):
    """Return labels/values/caption without implying triage ran when it did not."""
    flagged = result.get("contaminant_flagged_contigs", 0)
    retained = result.get("retained_after_triage_contigs", 0)
    if result.get("triage_completed") is True:
        return (["flagged by triage", "retained after triage"], [flagged, retained],
                f"Contamination k-mer triage: {flagged} flagged and {retained} retained after triage.")
    return (["flagged", "not evaluated"], [flagged, retained],
            f"Contamination triage did not run ({result.get('status')}); all "
            f"{result.get('n_contigs')} contig(s) were not evaluated.")


# =============================================================================================
# FASTA / GFA IO
# =============================================================================================
def _read_single_fasta(path):
    seq = []
    with open(path) as fh:
        for line in fh:
            if not line.startswith(">"):
                seq.append(line.strip())
    return "".join(seq)


def read_fasta(path):
    """Return list of (name, seq)."""
    out, name, seq = [], None, []
    with open(path) as fh:
        for line in fh:
            if line.startswith(">"):
                if name is not None:
                    out.append((name, "".join(seq)))
                name, seq = line[1:].strip().split()[0], []
            else:
                seq.append(line.strip())
    if name is not None:
        out.append((name, "".join(seq)))
    return out


def write_fasta(path, contigs, prefix="contig"):
    with open(path, "w") as fh:
        for i, (seq, circ) in enumerate(contigs):
            tag = "circular=true" if circ else "circular=false"
            fh.write(f">{prefix}_{i+1} len={len(seq)} {tag}\n")
            for j in range(0, len(seq), 70):
                fh.write(seq[j:j + 70] + "\n")
    return path


def write_gfa(path, contigs, k=19):
    """Minimal GFA1 of the assembled contigs. Circular contigs get a self-link (overlap k-1).
    Honest: a compact segment/link graph of the assembly, not a full unitig overlap graph."""
    with open(path, "w") as fh:
        fh.write("H\tVN:Z:1.0\n")
        for i, (seq, circ) in enumerate(contigs):
            fh.write(f"S\tcontig_{i+1}\t{seq}\tLN:i:{len(seq)}\n")
        for i, (seq, circ) in enumerate(contigs):
            if circ:
                fh.write(f"L\tcontig_{i+1}\t+\tcontig_{i+1}\t+\t{k-1}M\n")
    return path


# =============================================================================================
# FIGURES
# =============================================================================================
def make_figures(contigs, qv_draft, qv_polished, busco, contam_summary, out_dir,
                 genome_fraction_pct=0.0, qv_draft_saturated=False, qv_polished_saturated=False,
                 polished_assembly_distinct=False):
    """Emit result figures for analysis steps 4-8 when each step has something to plot.

    `contigs` is the polished [(seq, circular), ...]. A saturated QV (Finding 3) is drawn
    and captioned as a ceiling (>=Q60), never an exact value. The draft-versus-polished QV
    figure is emitted only when polishing produced a distinct assembly.
    """
    def _qv_label(v, sat):
        return (f"\u2265Q{v:g}" if sat else f"Q{v:g}")
    import matplotlib
    matplotlib.use("Agg")
    matplotlib.rcParams["font.family"] = ["Liberation Sans", "Arimo", "DejaVu Sans"]
    matplotlib.rcParams["svg.fonttype"] = "none"
    import matplotlib.pyplot as plt
    import numpy as np
    out_dir = pathlib.Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    BLUE, AMBER, GREEN, PINK = "#0279EE", "#FF9400", "#75A025", "#FD9BED"
    lens = [len(s) for s, _ in contigs] or [0]
    circ = [c for _, c in contigs] or [False]
    order = sorted(range(len(lens)), key=lambda i: -lens[i])
    manifest = []

    # figure 4 (step: Assemble the genome) — assembly overview: each contig as a bar, circular vs linear
    f = out_dir / "figure_4_assemble_the_genome.png"
    fig, ax = plt.subplots(figsize=(6, 4))
    ys = list(range(len(order)))
    ax.barh(ys, [lens[i] for i in order],
            color=[GREEN if circ[i] else BLUE for i in order], alpha=0.85)
    ax.set_yticks(ys)
    ax.set_yticklabels([f"contig_{i+1}{' (circular)' if circ[i] else ''}" for i in order], fontsize=8)
    ax.invert_yaxis()
    ax.set_xlabel("contig length (bp)")
    n_circ = sum(1 for c in circ if c)
    ax.set_title(f"{len(lens)} contig(s), {n_circ} circular; {sum(lens):,} bp total", fontsize=9)
    fig.tight_layout(); fig.savefig(f, dpi=150); plt.close(fig)
    manifest.append({"step": 4, "file": "figures/figure_4_assemble_the_genome.png",
                     "caption": (f"Assembled structure: {len(lens)} contig(s) ({n_circ} circular), "
                                 f"largest {max(lens):,} bp, {sum(lens):,} bp total."),
                     "interpretation": (f"The assembly is distributed across {len(lens)} contig(s), "
                                        f"including {n_circ} marked circular. Contiguity alone does "
                                        "not establish correctness or exclude collapsed repeats.")})

    # figure 5 (step: Assess assembly contiguity) — cumulative length (Nx) curve
    f = out_dir / "figure_5_assess_assembly_contiguity.png"
    slen = sorted(lens, reverse=True)
    total = sum(slen) or 1
    xs = np.cumsum(slen) / total * 100.0
    fig, ax = plt.subplots(figsize=(6, 4))
    step_x = np.repeat(np.concatenate(([0], xs)), 2)[1:-1]
    step_y = np.repeat(slen, 2)
    ax.plot(step_x, step_y, color=BLUE, lw=2)
    n50 = _n50(slen)
    ax.axhline(n50, color=AMBER, lw=2, ls="--", label=f"contig N50 = {n50:,} bp")
    ax.set_xlabel("cumulative assembly length (%)")
    ax.set_ylabel("contig length (bp)")
    ax.legend(frameon=False, fontsize=8)
    fig.tight_layout(); fig.savefig(f, dpi=150); plt.close(fig)
    manifest.append({"step": 5, "file": "figures/figure_5_assess_assembly_contiguity.png",
                     "caption": (f"Contiguity (Nx) curve; contig N50 {n50:,} bp, largest {max(lens):,} "
                                 f"bp across {len(lens)} contig(s)."),
                     "interpretation": (f"The bp-weighted N50 is {n50:,} bp for this assembly. "
                                        "A high N50 can still reflect misassembly and must be read "
                                        "with completeness, QV, and graph evidence.")})

    # figure 6 (step: Polish and track consensus QV) — QV draft vs polished, when applicable
    if polished_assembly_distinct:
        f = out_dir / "figure_6_polish_and_track.png"
        fig, ax = plt.subplots(figsize=(6, 4))
        ax.bar(["draft", "polished"], [qv_draft, qv_polished], color=[AMBER, GREEN], alpha=0.85)
        for i, (v, sat) in enumerate([(qv_draft, qv_draft_saturated),
                                      (qv_polished, qv_polished_saturated)]):
            ax.text(i, v, _qv_label(v, sat), ha="center", va="bottom", fontsize=9)
        ax.set_ylabel("Merqury-style consensus QV (Phred)")
        fig.tight_layout(); fig.savefig(f, dpi=150); plt.close(fig)
        _sat_note = (" polished QV is saturated at the Q60 display ceiling (>=100% of assembly k-mers "
                     "supported) \u2014 a lower bound, not an exact estimate."
                     if qv_polished_saturated else "")
        manifest.append({"step": 6, "file": "figures/figure_6_polish_and_track.png",
                         "caption": (f"Reference-free consensus QV "
                                     f"{'rises' if qv_polished > qv_draft else 'holds'} from "
                                     f"{_qv_label(qv_draft, qv_draft_saturated)} (draft) to "
                                     f"{_qv_label(qv_polished, qv_polished_saturated)} "
                                     f"(polished).{_sat_note}"),
                         "interpretation": ("The comparison shows the observed reference-free QV "
                                            "change after the selected polishing route. Low read "
                                            "coverage and Q60 saturation limit exact accuracy claims.")})
    else:
        manifest.append({
            "step": 6,
            "file": None,
            "reason": ("No polisher produced a distinct polished assembly; draft and polished "
                       "assemblies are identical, so the single reference-free QV is reported "
                       "in text and tables."),
        })

    # figure 7 (step: Assess genome completeness) — BUSCO-proxy + genome fraction
    f = out_dir / "figure_7_assess_genome_completeness.png"
    comp, miss = busco["busco_complete"], busco["busco_missing"]
    fig, ax = plt.subplots(figsize=(6, 4))
    ax.bar(["complete", "missing"], [comp, miss], color=[GREEN, AMBER], alpha=0.85)
    for i, v in enumerate([comp, miss]):
        ax.text(i, v, f"{v}", ha="center", va="bottom", fontsize=9)
    ax.set_ylabel("single-copy marker count")
    ax.set_title(f"BUSCO-proxy {busco['busco_complete_pct']:g}% · genome fraction "
                 f"{genome_fraction_pct:g}%", fontsize=9)
    fig.tight_layout(); fig.savefig(f, dpi=150); plt.close(fig)
    manifest.append({"step": 7, "file": "figures/figure_7_assess_genome_completeness.png",
                     "caption": (f"{comp}/{busco['busco_markers_total']} single-copy markers present "
                                 f"({busco['busco_complete_pct']:g}% BUSCO-proxy); genome fraction "
                                 f"{genome_fraction_pct:g}% vs reference."),
                     "interpretation": ("Marker completeness and reference genome fraction provide "
                                        "complementary evidence about recovered content. They are "
                                        "proxies tied to the supplied marker set and reference, not "
                                        "proof that contigs are correctly assembled.")})

    # figure 8 (step: Screen contigs for contamination) — disposition or explicit not-evaluated state
    f = out_dir / "figure_8_screen_contigs_for.png"
    labels, values, caption = contamination_figure_semantics(contam_summary)
    fig, ax = plt.subplots(figsize=(6, 4))
    ax.bar(labels, values, color=[PINK, BLUE], alpha=0.85)
    for i, v in enumerate(values):
        ax.text(i, v, f"{v}", ha="center", va="bottom", fontsize=9)
    ax.set_ylabel("contig count")
    ax.set_title(f"requested={contam_summary.get('requested_method')} · "
                 f"effective={contam_summary.get('effective_method') or 'not run'} · "
                 f"status={contam_summary.get('status')}",
                 fontsize=9)
    fig.tight_layout(); fig.savefig(f, dpi=150); plt.close(fig)
    manifest.append({"step": 8, "file": "figures/figure_8_screen_contigs_for.png",
                     "caption": caption,
                     "interpretation": ("The bars show the disposition produced by the recorded "
                                        "screen status and effective method. A missing or narrow "
                                        "contaminant reference cannot support a contaminant-free claim.")})
    return manifest


# =============================================================================================
# ROUTER — assembly requests are in scope; route read-QC / SV / isoform / methylation elsewhere.
# =============================================================================================
_OUT_OF_SCOPE = ("structural variant", "sv calling", "call svs", "call sv", "isoform",
                 "transcript model", "methylation", "5mc", "6ma", "variant calling",
                 "call variants", "snv", "indel", "phasing", "haplotype")
_ASSEMBLY_TERMS = ("assembl", "de novo", "contig", "polish", "quast", "busco", "merqury",
                   "genome", "scaffold", "hifiasm", "flye", "verkko", "metagenome", "mag")
_NEGATORS = ("do not", "don't", "not ", "without", "no ", "avoid", "except")


def in_scope(prompt):
    """True only for de-novo assembly / assembly-QC requests. A request whose primary ask is SV,
    variant, isoform, methylation, or phasing routes elsewhere, even if it mentions reads or assembly."""
    p = " ".join(str(prompt).lower().split())

    def requested(term):
        i = p.find(term)
        if i < 0:
            return False
        pre = p[max(0, i - 30):i]
        return not any(neg in pre for neg in _NEGATORS)

    oos = any(requested(t) for t in _OUT_OF_SCOPE)
    asm = any(t in p for t in _ASSEMBLY_TERMS)
    return asm and not oos
