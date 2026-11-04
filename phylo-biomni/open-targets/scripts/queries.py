#!/usr/bin/env python3
"""Pinned nine-operation query registry for the Open Targets Platform API.

Each entry carries the exact GraphQL field selection, the variable schema, the
semantic-nonempty expectation (which data keys must be present and non-null for
the operation to count as semantically successful), and the pagination policy.

On schema errors the client returns a bounded diagnostic and preserves the
failure; it never silently rewrites a field and calls the original operation
successful (OT-03).

The nine operation families:
  1. meta_and_associated_targets  — metadata/release + disease association ranking
  2. search                       — name → ID resolution
  3. target                       — target annotation
  4. disease_drugs                — disease annotation + drug/clinical candidates
  5. evidence                     — target–disease evidence rows
  6. drug                         — drug profile
  7. variant                      — variant annotation
  8. study                        — GWAS study metadata
  9. credible_sets                — credible sets + L2G + colocalisation
"""

from __future__ import annotations

REGISTRY: dict[str, dict] = {
    "meta": {
        "description": "API/data release metadata (meta.apiVersion, meta.dataVersion).",
        "query": (
            "query { meta { dataVersion { year month } apiVersion { x y z suffix } product } }"
        ),
        "variables": {},
        "semantic_nonempty": ["meta.dataVersion.year", "meta.dataVersion.month", "meta.apiVersion.x"],
        "pagination": "none",
        "record_types": ["release_metadata"],
    },
    "meta_and_associated_targets": {
        "description": "Release metadata plus top-N disease-associated targets with datatype scores.",
        "query": """
query AssociatedTargets($efoId: String!, $size: Int!) {
  meta { dataVersion { year month } apiVersion { x y z suffix } product }
  disease(efoId: $efoId) {
    id name dbXRefs
    associatedTargets(page: { index: 0, size: $size }) {
      count
      rows {
        target { id approvedSymbol approvedName biotype }
        score
        datatypeScores { id score }
      }
    }
  }
}""",
        "variables": {"efoId": "String!", "size": "Int"},
        "semantic_nonempty": ["meta.dataVersion.year", "disease.id", "disease.associatedTargets.rows"],
        "pagination": "index",
        "page_field": "disease.associatedTargets",
        "record_types": ["association"],
    },
    "search": {
        "description": "Resolve a free-text name to standardised IDs across target/disease/drug.",
        "query": """
query Search($q: String!) {
  search(queryString: $q, entityNames: ["target", "disease", "drug"]) {
    hits { id name entity }
    total
  }
}""",
        "variables": {"q": "String!"},
        "semantic_nonempty": ["search.hits"],
        "pagination": "index",
        "page_field": "search",
        "record_types": ["search_hit"],
    },
    "target": {
        "description": "Target annotation: constraint, tractability, biotype.",
        "query": """
query Target($ensemblId: String!) {
  target(ensemblId: $ensemblId) {
    id approvedSymbol approvedName biotype
    geneticConstraint { constraintType score oe oeLower oeUpper }
    tractability { label modality value }
  }
}""",
        "variables": {"ensemblId": "String!"},
        "semantic_nonempty": ["target.id", "target.approvedSymbol"],
        "pagination": "none",
        "record_types": ["target_annotation"],
    },
    "disease_drugs": {
        "description": "Disease annotation plus drug and clinical candidates.",
        "query": """
query Disease($efoId: String!) {
  disease(efoId: $efoId) {
    id name dbXRefs
    drugAndClinicalCandidates {
      count
      rows {
        id maxClinicalStage
        drug { id name drugType }
        clinicalReports { clinicalStage trialPhase trialOverallStatus source }
      }
    }
  }
}""",
        "variables": {"efoId": "String!"},
        "semantic_nonempty": ["disease.id", "disease.name"],
        "pagination": "none",
        "record_types": ["disease_annotation", "drug_candidate"],
    },
    "evidence": {
        "description": "Target–disease evidence rows with datasource, score, literature.",
        "query": """
query Evidence($efoId: String!, $ensemblId: String!, $size: Int!, $cursor: String) {
  disease(efoId: $efoId) {
    evidences(ensemblIds: [$ensemblId], size: $size, cursor: $cursor) {
      count
      cursor
      rows {
        datasourceId
        datatypeId
        score
        literature
        urls { url niceName }
        studyOverview
        publicationFirstAuthor
        publicationYear
      }
    }
  }
}""",
        "variables": {"efoId": "String!", "ensemblId": "String!", "size": "Int", "cursor": "String"},
        "semantic_nonempty": ["disease.evidences.count"],
        "pagination": "cursor",
        "page_field": "disease.evidences",
        "record_types": ["evidence"],
    },
    "drug": {
        "description": "Drug profile: mechanism of action, indications, adverse events.",
        "query": """
query Drug($chemblId: String!) {
  drug(chemblId: $chemblId) {
    id name drugType maximumClinicalStage
    mechanismsOfAction { rows { mechanismOfAction targetName actionType } }
    indications { count rows { disease { id name } maxClinicalStage } }
    adverseEvents(page: { index: 0, size: 10 }) {
      count
      rows { name count logLR }
    }
  }
}""",
        "variables": {"chemblId": "String!"},
        "semantic_nonempty": ["drug.id", "drug.name"],
        "pagination": "none",
        "record_types": ["drug_profile"],
    },
    "variant": {
        "description": "Variant annotation: consequence, allele frequencies, transcript consequences.",
        "query": """
query Variant($variantId: String!) {
  variant(variantId: $variantId) {
    id chromosome position referenceAllele alternateAllele rsIds
    mostSevereConsequence { id label }
    alleleFrequencies { populationName alleleFrequency }
    transcriptConsequences {
      target { id approvedSymbol }
      variantConsequences { id label }
      isEnsemblCanonical
    }
  }
}""",
        "variables": {"variantId": "String!"},
        "semantic_nonempty": ["variant.id", "variant.chromosome"],
        "pagination": "none",
        "record_types": ["variant_annotation"],
    },
    "study": {
        "description": "GWAS study metadata with linked credible sets.",
        "query": """
query Study($studyId: String!) {
  study(studyId: $studyId) {
    id studyType traitFromSource pubmedId publicationFirstAuthor
    nSamples nCases nControls
    diseases { id name }
    credibleSets(page: { index: 0, size: 10 }) {
      count
      rows { studyLocusId region pValueMantissa pValueExponent }
    }
  }
}""",
        "variables": {"studyId": "String!"},
        "semantic_nonempty": ["study.id", "study.traitFromSource"],
        "pagination": "none",
        "record_types": ["study"],
    },
    "credible_sets": {
        "description": "Credible sets with L2G predictions and colocalisation (former Genetics Portal core).",
        "query": """
query CredibleSets($studyIds: [String!]!, $size: Int!) {
  credibleSets(page: { index: 0, size: $size }, studyIds: $studyIds) {
    count
    rows {
      studyLocusId region
      pValueMantissa pValueExponent
      variant { id rsIds mostSevereConsequence { label } }
      l2GPredictions {
        rows { target { id approvedSymbol } score }
      }
      colocalisation {
        rows { otherStudyLocus { studyId } h4 clpp }
      }
    }
  }
}""",
        "variables": {"studyIds": "[String!]!", "size": "Int"},
        "semantic_nonempty": ["credibleSets.count"],
        "pagination": "index",
        "page_field": "credibleSets",
        "record_types": ["credible_set", "l2g_prediction", "colocalisation"],
    },
}

# Ordered list of the nine operation families (excluding the bare "meta" helper).
NINE_OPERATIONS = [
    "meta_and_associated_targets",
    "search",
    "target",
    "disease_drugs",
    "evidence",
    "drug",
    "variant",
    "study",
    "credible_sets",
]


def get_query(op_name: str) -> str:
    if op_name not in REGISTRY:
        raise KeyError(f"unknown operation {op_name!r}; valid: {sorted(REGISTRY)}")
    return REGISTRY[op_name]["query"]


def get_variables_schema(op_name: str) -> dict:
    return REGISTRY[op_name]["variables"]


def get_semantic_nonempty(op_name: str) -> list[str]:
    return REGISTRY[op_name]["semantic_nonempty"]


def query_hash(op_name: str) -> str:
    import hashlib
    return hashlib.sha256(get_query(op_name).encode("utf-8")).hexdigest()[:16]


def check_semantic_nonempty(op_name: str, data: dict) -> tuple[bool, str]:
    """Return (ok, message).  ok is False when a required path is absent or null."""
    paths = get_semantic_nonempty(op_name)
    for path in paths:
        node = data
        for part in path.split("."):
            if not isinstance(node, dict) or part not in node or node[part] is None:
                return False, f"semantic nonempty check failed: {path!r} is absent or null in {op_name!r}"
            node = node[part]
        # For list-typed leaves, require non-empty
        if isinstance(node, list) and len(node) == 0:
            return False, f"semantic nonempty check failed: {path!r} is an empty list in {op_name!r}"
    return True, "ok"
