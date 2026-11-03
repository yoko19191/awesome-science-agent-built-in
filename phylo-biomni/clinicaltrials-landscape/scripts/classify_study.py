"""
Record-grounded study classification for the ClinicalTrials.gov landscape skill.

Two orthogonal, mutually-exclusive axes are assigned to every trial, so the composition always sums
to the retrieved denominator:

* ``study_purpose``       - Observational | Diagnostic/Imaging | Therapeutic | Other/Supportive | Unresolved
* ``intervention_category`` - a generic, record-grounded modality bucket (see INTERVENTION_CATEGORIES)

Design constraints (deliberate):

* This is NOT a target-development / mechanism-of-action taxonomy. Generic buckets are structural.
* The radioligand split fires ONLY on explicit evidence: a DIAGNOSTIC_TEST intervention type, a
  diagnostic radioisotope in an imaging context, a therapeutic radioisotope, or a named
  radiopharmaceutical. Otherwise the record is labeled ``Unclassified (insufficient description)``.
* A record whose modality cannot be resolved is LABELED, never assigned an invented modality.

The classifier accepts either raw API records (list of dicts from query_trials(); uses intervention
``type``/``name``/``description`` and ``brief_summary``) or a compiled ``pandas.DataFrame`` (offline;
uses ``study_type``, ``brief_title``, ``official_title``, ``intervention_names_str`` /
``drug_names_str`` and, for radioligand evidence, ``intervention_descriptions_str`` jointly with
the names). It uses whatever evidence fields are present and records their availability
so downstream match-evidence never implies an absent field was present.

An optional user-supplied disease config (disease_config.py) may add a config-driven ``mechanism``
label; none ships, so ``mechanism`` defaults to the intervention_category for backwards compatibility.
"""

from __future__ import annotations

import re

import pandas as pd

try:
    from alias_match import alias_matches
    from disease_config import get_mechanism_patterns, get_drug_normalization
except ImportError:  # pragma: no cover - import shim for package vs. flat execution
    from scripts.alias_match import alias_matches
    from scripts.disease_config import get_mechanism_patterns, get_drug_normalization


# ============================================================
# TAXONOMY (pinned label sets)
# ============================================================

STUDY_PURPOSES = (
    "Observational",
    "Diagnostic/Imaging",
    "Therapeutic",
    "Other/Supportive",
    "Unresolved",
)

INTERVENTION_CATEGORIES = (
    "Diagnostic radiotracer / imaging agent",
    "Radioligand therapy (radiopharmaceutical)",
    "Small molecule",
    "Biologic (antibody/protein)",
    "Cell or gene therapy",
    "Radiation therapy (external/brachytherapy)",
    "Device / Procedure",
    "Behavioral / Supportive / Other",
    "Unclassified (insufficient description)",
)

# ---- pinned evidence tokens (lowercase; short acronyms require token boundaries) ----

DIAGNOSTIC_ISOTOPES = (
    "18f", "f-18", "f 18", "68ga", "ga-68", "ga 68", "gallium-68", "gallium 68",
    "64cu", "cu-64", "89zr", "zr-89", "99mtc", "tc-99m", "11c", "c-11",
)
THERAPEUTIC_ISOTOPES = (
    "177lu", "lu-177", "lu 177", "lutetium", "225ac", "ac-225", "actinium",
    "223ra", "ra-223", "radium-223", "radium 223", "131i", "i-131", "iodine-131",
    "90y", "yttrium-90", "212pb", "lead-212",
)
IMAGING_CONTEXT = (
    "pet", "spect", "pet/ct", "pet-ct", "pet ct", "positron emission", "imaging",
    "radiotracer", "tracer", "scintigraphy", "scan ", "biodistribution", "dosimetry",
)
DIAGNOSTIC_WORDS = ("diagnostic", "diagnosis", "detection", "staging", "restaging", "localization")
NAMED_DIAGNOSTIC_TRACERS = (
    "piflufolastat", "fluciclovine", "psma-11", "psma 11", "dcfpyl", "pylarify",
    "illuccix", "locametz", "dotatoc", "gozetotide", "flotufolastat", "posluma",
)
NAMED_THERAPEUTIC_RADIOLIGANDS = (
    "psma-617", "psma 617", "lu-psma", "lupsma", "177lu-psma", "pluvicto", "vipivotide",
    "lutathera", "177lu-dotatate", "lu-dotatate",
)
THERAPY_WORDS = (
    "therapy", "therapeutic", "treatment", "radioligand therapy", "radioligand",
    "targeted therapy", "chemotherapy",
)
ANTIBODY_TOKENS = (
    "antibody", "monoclonal", "bispecific", " bite", "immunoglobulin", "nanobody",
    "immunoconjugate", "antibody-drug", "adc ",
)
CELL_GENE_TOKENS = (
    "car-t", "car t", "car-nk", "chimeric antigen", "til", "tils", "tumor infiltrating",
    "cell therapy", "gene therapy", "vaccine", "dendritic cell", "tcr-t", "tcr t",
    "adoptive cell", "oncolytic",
)
SMALL_MOLECULE_TOKENS = (
    "inhibitor", "antagonist", "agonist", "kinase", "abiraterone", "enzalutamide",
    "apalutamide", "darolutamide", "olaparib", "-inib", "-lutamide", "-parib",
)
RADIATION_TOKENS = (
    "radiotherapy", "radiation therapy", "external beam", "ebrt", "sbrt", "imrt",
    "brachytherapy", "stereotactic", "proton therapy", "radiosurgery",
)
DEVICE_PROC_TOKENS = ("device", "procedure", "surgery", "surgical", "ablation", "biopsy", "prostatectomy")
SUPPORTIVE_TOKENS = (
    "behavioral", "behaviour", "exercise", "diet", "dietary", "supplement", "counseling",
    "education", "quality of life", "supportive care", "physical activity", "lifestyle",
)

_MAB_SUFFIX = re.compile(r"\b\w+mab\b")

_THERAPEUTIC_TYPES = {"DRUG", "BIOLOGICAL", "RADIATION", "GENETIC", "COMBINATION_PRODUCT"}
_SUPPORTIVE_TYPES = {"BEHAVIORAL", "DIETARY_SUPPLEMENT"}
_DEVICE_TYPES = {"DEVICE", "PROCEDURE"}


def _text(value) -> str:
    return "" if pd.isna(value) else str(value)


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", _text(text)).lower()


# Acronyms are complete tokens, never suffixes of ordinary words (e.g. until).
# Longer lexical cues and intentional drug-name stems retain substring semantics.
_BOUNDARY_TOKENS = {
    "til", "tils", "bite", "adc", "pet", "spect", "ebrt", "sbrt", "imrt",
    "car-t", "car t", "car-nk", "tcr-t", "tcr t",
}
_BOUNDARY_PATTERNS = {
    token: re.compile(r"(?<!\w)" + re.escape(token) + r"(?!\w)")
    for token in _BOUNDARY_TOKENS
}


def _has_any(corpus: str, tokens) -> bool:
    for token in tokens:
        pattern = _BOUNDARY_PATTERNS.get(token.strip())
        if pattern is not None:
            if pattern.search(corpus):
                return True
        elif token in corpus:
            return True
    return False


# ============================================================
# EVIDENCE EXTRACTION (works for raw records and compiled rows)
# ============================================================

def extract_evidence(record) -> dict:
    """Build a uniform evidence bundle from a raw API record (dict) or a compiled row (Mapping).

    Returns a dict with:
      corpus                 - normalized (lowercase) concatenated text used for token matching
      corpus_cased           - the same text with letter case preserved, for alias-aware literals
      intervention_types     - set of API intervention types (empty when unavailable)
      intervention_names     - list of intervention names (raw) or drug names (compiled)
      intervention_descriptions - raw or compiled descriptions (empty when unavailable)
      study_type             - INTERVENTIONAL / OBSERVATIONAL / ''
      brief_title, official_title, brief_summary, detailed_description, conditions_str
      field_availability     - which source fields were actually present (for match-evidence honesty)
    """
    get = record.get if hasattr(record, "get") else (lambda k, d=None: record[k] if k in record else d)

    study_type = _text(get("study_type", "")).upper()
    brief_title = _text(get("brief_title", ""))
    official_title = _text(get("official_title", ""))

    interventions = get("interventions", None)
    intervention_types: set[str] = set()
    intervention_names: list[str] = []
    intervention_descriptions: list[str] = []
    descriptions_available = False

    if isinstance(interventions, list) and interventions:
        # Raw API record path.
        for intv in interventions:
            if not isinstance(intv, dict):
                continue
            itype = _text(intv.get("type", "")).upper()
            if itype:
                intervention_types.add(itype)
            name = _text(intv.get("name", "")).strip()
            if name:
                intervention_names.append(name)
            desc = _text(intv.get("description", "")).strip()
            if desc:
                intervention_descriptions.append(desc)
                descriptions_available = True
    else:
        # Compiled DataFrame path: use retained intervention-name fields.
        drug_names_str = (_text(get("intervention_names_str", "")) or _text(get("drug_names_str", ""))
                          or _text(get("drug_names_normalized_str", "")))
        for token in re.split(r"[;,|]", drug_names_str):
            token = token.strip()
            if token:
                intervention_names.append(token)

    if not intervention_descriptions:
        description = _text(get("intervention_descriptions_str", "")).strip()
        if description:
            intervention_descriptions = [description]
            descriptions_available = True

    brief_summary = _text(get("brief_summary", ""))
    brief_summary_available = bool(brief_summary.strip())
    detailed_description = _text(get("detailed_description", ""))
    detailed_description_available = bool(detailed_description.strip())

    conditions = get("conditions", None)
    if isinstance(conditions, list):
        conditions_str = "; ".join(str(c) for c in conditions if c)
    else:
        conditions_str = _text(get("conditions_str", ""))

    # Case-preserving corpus for alias-aware literal matching (letter-case transitions are
    # boundaries, e.g. PSMAxCD3); the lowercase corpus keeps the pinned-token rules unchanged.
    corpus_cased = re.sub(
        r"\s+",
        " ",
        " ".join(
            [brief_title, official_title, brief_summary, detailed_description, conditions_str]
            + intervention_names
            + intervention_descriptions
        ),
    )
    corpus = corpus_cased.lower()

    return {
        "corpus": corpus,
        "corpus_cased": corpus_cased,
        "intervention_types": intervention_types,
        "intervention_names": intervention_names,
        "interventions": interventions if isinstance(interventions, list) else [],
        "intervention_descriptions": intervention_descriptions,
        "study_type": study_type,
        "brief_title": brief_title,
        "official_title": official_title,
        "brief_summary": brief_summary,
        "detailed_description": detailed_description,
        "conditions_str": conditions_str,
        "field_availability": {
            "intervention_types_available": bool(intervention_types),
            "intervention_descriptions_available": descriptions_available,
            "brief_summary_available": brief_summary_available,
            "detailed_description_available": detailed_description_available,
        },
    }


# ============================================================
# CLASSIFICATION RULES (deterministic, priority-ordered)
# ============================================================

def _diagnostic_evidence(ev: dict) -> bool:
    corpus, types = ev["corpus"], ev["intervention_types"]
    if "DIAGNOSTIC_TEST" in types:
        return True
    if _has_any(corpus, NAMED_DIAGNOSTIC_TRACERS):
        return True
    if _has_any(corpus, DIAGNOSTIC_ISOTOPES) and (
        _has_any(corpus, IMAGING_CONTEXT) or _has_any(corpus, DIAGNOSTIC_WORDS)
    ):
        return True
    return False


def _therapeutic_radioligand_evidence(ev: dict) -> bool:
    if ev["interventions"]:
        for intervention in ev["interventions"]:
            if not isinstance(intervention, dict):
                continue
            name = _norm(_text(intervention.get("name", "")))
            description = _norm(_text(intervention.get("description", "")))
            kind = _text(intervention.get("type", "")).upper()
            # Descriptions can mention treatment received outside the intervention.
            # Keep each description attached to its own intervention role.
            diagnostic_name = ((_has_any(name, DIAGNOSTIC_ISOTOPES)
                                or _has_any(name, NAMED_DIAGNOSTIC_TRACERS))
                               and not _has_any(name, THERAPEUTIC_ISOTOPES))
            sampling_name = _has_any(name, ("blood sample", "tissue sample", "biopsy", "mri", "pet scan"))
            if diagnostic_name or sampling_name:
                continue
            # Registry OTHER can still describe an administered therapeutic agent.
            # Require that intervention's own agent identity and administration evidence.
            other_agent = (kind == "OTHER"
                           and (_has_any(name, THERAPEUTIC_ISOTOPES)
                                or _has_any(name, ("radioconjugate", "radiopharmaceutical", "radioligand")))
                           and not _has_any(name, IMAGING_CONTEXT)
                           and _has_any(description, ("given", "administer", "injection", "infusion")))
            if _has_any(name, NAMED_THERAPEUTIC_RADIOLIGANDS) or (
                _has_any(name, THERAPEUTIC_ISOTOPES)
                and (_has_any(name, THERAPY_WORDS) or kind in _THERAPEUTIC_TYPES or other_agent)
            ):
                return True
            if (kind in _THERAPEUTIC_TYPES or other_agent) and not (
                _has_any(description, DIAGNOSTIC_ISOTOPES) and _has_any(description, IMAGING_CONTEXT)
            ) and (_has_any(description, NAMED_THERAPEUTIC_RADIOLIGANDS)
                   or _has_any(description, THERAPEUTIC_ISOTOPES)):
                return True
        return False
    names = _norm(" ".join(ev["intervention_names"]))
    if names:
        if _has_any(names, NAMED_THERAPEUTIC_RADIOLIGANDS) or (
            _has_any(names, THERAPEUTIC_ISOTOPES)
            and (_has_any(names, THERAPY_WORDS) or bool(ev["intervention_types"] & _THERAPEUTIC_TYPES))
        ):
            return True
        # Compiled rows lose the per-intervention structure, so a generic name is evaluated jointly
        # with the retained descriptions (mirroring the raw-record path). A description that is itself
        # diagnostic imaging never supplies therapeutic evidence (background therapy mentions).
        descriptions = _norm(" ".join(ev["intervention_descriptions"]))
        if not descriptions:
            return False
        if _has_any(descriptions, DIAGNOSTIC_ISOTOPES) and _has_any(descriptions, IMAGING_CONTEXT):
            return False
        joint = f"{names} {descriptions}"
        return _has_any(joint, NAMED_THERAPEUTIC_RADIOLIGANDS) or (
            _has_any(joint, THERAPEUTIC_ISOTOPES) and _has_any(joint, THERAPY_WORDS)
        )
    # Unstructured records need explicit treatment context and no diagnostic evidence.
    return (not _diagnostic_evidence(ev) and _has_any(ev["corpus"], THERAPY_WORDS)
            and (_has_any(ev["corpus"], THERAPEUTIC_ISOTOPES)
                 or _has_any(ev["corpus"], NAMED_THERAPEUTIC_RADIOLIGANDS)))


def classify_intervention_category(ev: dict) -> str:
    """Assign exactly one intervention_category from record-grounded evidence (priority-ordered)."""
    corpus, types = ev["corpus"], ev["intervention_types"]

    # 1. An actual therapeutic intervention dominates a theranostic pair.
    if _therapeutic_radioligand_evidence(ev):
        return "Radioligand therapy (radiopharmaceutical)"
    # 2. Diagnostic evidence with no supported therapeutic intervention.
    if _diagnostic_evidence(ev):
        return "Diagnostic radiotracer / imaging agent"
    # 3. Cell or gene therapy.
    if "GENETIC" in types or _has_any(corpus, CELL_GENE_TOKENS):
        return "Cell or gene therapy"
    # 4. Biologic (antibody/protein).
    if "BIOLOGICAL" in types or _has_any(corpus, ANTIBODY_TOKENS) or _MAB_SUFFIX.search(corpus):
        return "Biologic (antibody/protein)"
    # 5. Small molecule (explicit small-molecule cues, or DRUG type).
    if _has_any(corpus, SMALL_MOLECULE_TOKENS) or "DRUG" in types:
        return "Small molecule"
    # 6. Radiation therapy (external/brachy), no radiopharmaceutical drug.
    if "RADIATION" in types or _has_any(corpus, RADIATION_TOKENS):
        return "Radiation therapy (external/brachytherapy)"
    # 7. Device / Procedure.
    if types & _DEVICE_TYPES or _has_any(corpus, DEVICE_PROC_TOKENS):
        return "Device / Procedure"
    # 8. Behavioral / Supportive / Other.
    if types & _SUPPORTIVE_TYPES or "OTHER" in types or _has_any(corpus, SUPPORTIVE_TOKENS):
        return "Behavioral / Supportive / Other"
    # 9. Nothing resolvable.
    return "Unclassified (insufficient description)"


def classify_study_purpose(ev: dict, intervention_category: str) -> str:
    """Assign exactly one study_purpose. Observational is decided by study_type; the rest use evidence."""
    if ev["study_type"] == "OBSERVATIONAL":
        return "Observational"

    corpus, types = ev["corpus"], ev["intervention_types"]

    if intervention_category == "Diagnostic radiotracer / imaging agent":
        return "Diagnostic/Imaging"
    if intervention_category in (
        "Radioligand therapy (radiopharmaceutical)",
        "Small molecule",
        "Biologic (antibody/protein)",
        "Cell or gene therapy",
        "Radiation therapy (external/brachytherapy)",
    ):
        return "Therapeutic"
    if intervention_category == "Device / Procedure":
        # A device/procedure with imaging/diagnostic cues is diagnostic; otherwise supportive/other.
        if _diagnostic_evidence(ev):
            return "Diagnostic/Imaging"
        return "Other/Supportive"
    if intervention_category == "Behavioral / Supportive / Other":
        return "Other/Supportive"

    # Unclassified modality: still infer purpose where the record supports it.
    if _has_any(corpus, THERAPY_WORDS) or ev["intervention_names"] or (types & _THERAPEUTIC_TYPES):
        return "Therapeutic"
    if _diagnostic_evidence(ev):
        return "Diagnostic/Imaging"
    return "Unresolved"


# ============================================================
# OPTIONAL CONFIG-DRIVEN MECHANISM (backwards-compatible extra)
# ============================================================

def _config_mechanism(corpus: str, patterns, corpus_cased: str | None = None) -> str | None:
    """Regex patterns run on the lowercase corpus; literal patterns use the shared alias matcher."""
    literal_text = corpus if corpus_cased is None else corpus_cased
    for mechanism, pats in patterns:
        for pattern, is_regex in pats:
            if is_regex:
                if re.search(pattern, corpus):
                    return mechanism
            elif alias_matches(pattern, literal_text):
                return mechanism
    return None


# ============================================================
# PUBLIC API
# ============================================================

def classify_record(record, config=None) -> dict:
    """Classify one record; returns the fields to attach (does not mutate the input)."""
    ev = extract_evidence(record)
    category = classify_intervention_category(ev)
    purpose = classify_study_purpose(ev, category)
    resolved = category != "Unclassified (insufficient description)" and purpose != "Unresolved"

    mechanism = category
    if config:
        patterns = get_mechanism_patterns(config)
        config_mech = _config_mechanism(ev["corpus"], patterns, ev.get("corpus_cased"))
        if config_mech:
            mechanism = config_mech

    return {
        "study_purpose": purpose,
        "intervention_category": category,
        "classification_resolved": bool(resolved),
        "mechanism": mechanism,
        "field_availability": ev["field_availability"],
    }


def classify_all(data, config=None):
    """Classify every trial. Accepts a list of raw records or a compiled DataFrame.

    For a list, mutates each dict in place (adds study_purpose, intervention_category,
    classification_resolved, mechanism) and returns the list. For a DataFrame, returns a copy with
    those columns added. Prints a short composition summary.
    """
    # DataFrame path.
    if hasattr(data, "columns") and hasattr(data, "iterrows"):
        df = data.copy()
        purposes, categories, resolved_flags, mechanisms = [], [], [], []
        for _, row in df.iterrows():
            result = classify_record(row, config=config)
            purposes.append(result["study_purpose"])
            categories.append(result["intervention_category"])
            resolved_flags.append(result["classification_resolved"])
            mechanisms.append(result["mechanism"])
        df["study_purpose"] = purposes
        df["intervention_category"] = categories
        df["classification_resolved"] = resolved_flags
        df["mechanism"] = mechanisms
        _print_summary(categories)
        return df

    # Raw-records path.
    categories = []
    for trial in data:
        result = classify_record(trial, config=config)
        trial["study_purpose"] = result["study_purpose"]
        trial["intervention_category"] = result["intervention_category"]
        trial["classification_resolved"] = result["classification_resolved"]
        trial["mechanism"] = result["mechanism"]
        trial["field_availability"] = result["field_availability"]
        categories.append(result["intervention_category"])
    _print_summary(categories)
    return data


def _print_summary(categories) -> None:
    counts: dict[str, int] = {}
    for c in categories:
        counts[c] = counts.get(c, 0) + 1
    print(f"\u2713 Classified {len(categories)} trials (study purpose + intervention category)")
    for cat, n in sorted(counts.items(), key=lambda kv: -kv[1]):
        print(f"    {cat}: {n}")
