"""Apply medicinal-chemistry alert, drug-likeness and sanity filters to a set of molecules.

Engines: RDKit FilterCatalog (PAINS A/B/C, Brenk, NIH, ZINC), datamol-io/medchem (23 alert sets,
NIBR, Lilly demerits, 22 property rules, Bredt/graph/complexity sanity filters).
Modes: strict (every hit rejects), score (nothing rejects) and consensus (reject only what a majority
of the voting expert rule sets agree on; a heuristic, since the lists overlap).
Writes passing and failing tables with every score attached, a JSON summary, and md/html reports.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys
from dataclasses import asdict, dataclass
from importlib import metadata
from typing import Literal

import datamol as dm
import medchem.functional as mcf
import pandas as pd
import report
from medchem.rules import RuleFilters
from medchem.structural import CommonAlertsFilters, NIBRFilters
from medchem.structural.lilly_demerits import LillyDemeritsFilters
from rdkit import Chem
from rdkit import __version__ as rdkit_version
from rdkit.Chem.FilterCatalog import FilterCatalog, FilterCatalogParams, RunFilterCatalog

Role = Literal["reject", "flag", "vote"]

RDKIT_CATALOGS = {
    "pains_a": FilterCatalogParams.FilterCatalogs.PAINS_A,
    "pains_b": FilterCatalogParams.FilterCatalogs.PAINS_B,
    "pains_c": FilterCatalogParams.FilterCatalogs.PAINS_C,
    "brenk": FilterCatalogParams.FilterCatalogs.BRENK,
    "nih": FilterCatalogParams.FilterCatalogs.NIH,
    "zinc": FilterCatalogParams.FilterCatalogs.ZINC,
}
SANITY = {
    "bredt": ("Bredt-rule violation", mcf.bredt_filter),
    "graph": ("unstable molecular graph", mcf.molecular_graph_filter),
    "complexity": ("complexity above ZINC 99th percentile", mcf.complexity_filter),
}
GROUPS = {
    "interference": [
        "pains_a",
        "pains_b",
        "pains_c",
        "alarm-nmr",
        "alphascreen-hitters",
        "gst-hitters",
        "his-hitters",
        "luciferaseinhibitor",
        "chelator",
        "frequent-hitter",
    ],
    "reactive-toxic": [
        "brenk",
        "nih",
        "glaxo",
        "bms",
        "mlsmr",
        "inpharmatica",
        "lint",
        "surechembl",
        "electrophilic",
        "toxicophore",
        "genotoxic-carcinogenicity",
    ],
    "nibr": ["nibr"],
    "lilly": ["lilly"],
    "druglike": ["rule_of_five", "rule_of_veber"],
    "sanity": ["bredt", "graph", "complexity"],
}
# Expert rule sets whose agreement defines the consensus heuristic: the eight ChEMBL alert sets
# (PAINS A/B/C pooled) plus NIH. Brenk is the Dundee set, so it is not counted twice. The lists
# overlap (Glaxo and MLSMR share most SMARTS), so agreement is not independent corroboration.
EXPERT_PANEL = {
    "glaxo": ("glaxo",),
    "dundee": ("dundee",),
    "bms": ("bms",),
    "pains": ("pains_a", "pains_b", "pains_c"),
    "surechembl": ("surechembl",),
    "mlsmr": ("mlsmr",),
    "inpharmatica": ("inpharmatica",),
    "lint": ("lint",),
    "nih": ("nih",),
}
SAME_LIST = {"brenk": "dundee", "dundee": "brenk"}  # Brenk et al. 2008: RDKit and medchem versions
PANEL_TOKENS = tuple(token for tokens in EXPERT_PANEL.values() for token in tokens)
STRUCTURE_COLUMNS = ("smiles", "canonical_smiles", "structure", "smi", "inchi")
ID_COLUMNS = ("id", "compound_id", "name", "compound_name")
SCORE_TOKENS = {"nibr", "lilly"}
NIBR_EXCLUDE_SEVERITY = 10
LILLY_DEFAULT_CUTOFF = 100
# Columns this script adds besides one per selected filter; computed physchem columns start "calc_".
# "mol" is the internal RDKit column that is dropped on write; an input column of that name would be lost.
OUTPUT_COLUMNS = (
    "mol",
    "input_row",
    "flags",
    "hit_details",
    "n_filters_hit",
    "n_expert_sets_hit",
    "fail_reasons",
    "pass_filters",
    "nibr_severity",
    "nibr_status",
    "nibr_covalent_motifs",
    "lilly_demerits",
    "lilly_status",
)
CALCULATED_PREFIX = "calc_"


@dataclass(frozen=True)
class Thresholds:
    """Rejection thresholds. Strict mode rejects on any NIBR flag or Lilly demerit; annotation-only
    NIBR matches never reject.

    The Lilly engine cutoff is handed to the engine, which scores a rule that reaches it as a reject
    at the cutoff and scales its size penalty to it; strict mode leaves it at the native default so
    scores do not change with the mode.
    """

    nibr_reject_at: int
    lilly_reject_at: int
    lilly_engine_cutoff: int
    consensus_min_agree: int | None  # voting sets that must flag a molecule; consensus mode only


@dataclass(frozen=True)
class Outcome:
    detail: pd.Series  # matched alert names or violation note; "" when clean
    hit: pd.Series  # True where the filter matched at all, including annotation-only matches
    reject: pd.Series  # True where the molecule fails this filter at the configured thresholds
    scores: pd.DataFrame  # extra numeric/status columns (empty when the filter has none)

    @classmethod
    def from_detail(cls, detail: pd.Series, scores: pd.DataFrame | None = None) -> Outcome:
        """Outcome of a filter where any reported detail is both a hit and a rejection."""
        hit = detail != ""
        return cls(detail, hit, hit, pd.DataFrame() if scores is None else scores)

    @classmethod
    def empty(cls) -> Outcome:
        none = pd.Series([], dtype=bool)
        return cls(pd.Series([], dtype=str), none, none, pd.DataFrame())


def medchem_alert_sets() -> dict[str, str]:
    names = CommonAlertsFilters.list_default_available_alerts()["rule_set_name"]
    return {name.lower(): name for name in names}


def expand(selection: list[str]) -> list[str]:
    """Replace group names (and the `pains` alias) by their tokens, preserving order."""
    tokens: list[str] = []
    for item in selection:
        key = item.lower()
        if key == "pains":
            members = GROUPS["interference"][:3]
        else:
            members = GROUPS.get(key, [key])
        tokens.extend(t for t in members if t not in tokens)
    return tokens


def validate_tokens(tokens: list[str]) -> None:
    valid = {
        *RDKIT_CATALOGS,
        *SANITY,
        *SCORE_TOKENS,
        *medchem_alert_sets(),
        *RuleFilters.list_available_rules_names(),
    }
    unknown = [t for t in tokens if t not in valid]
    if unknown:
        sys.exit(f"Unknown filters {unknown}. Groups: {sorted(GROUPS)}. Tokens: {sorted(valid)}")


def is_rule(token: str) -> bool:
    return token in RuleFilters.list_available_rules_names()


def resolve_min_agree(mode: str, requested: int | None, roles: dict[str, Role]) -> int | None:
    """Consensus threshold: a majority of the voting sets unless given, checked against their number."""
    if mode != "consensus":
        return None
    voters = len(voting_sets(roles))
    if voters < 2:
        sys.exit(
            f"Consensus needs at least two voting expert sets, {voters} left after --flag and "
            "--reject; use --reject for a single filter or --mode score."
        )
    threshold = voters // 2 + 1 if requested is None else requested
    if not 1 <= threshold <= voters:
        sys.exit(
            f"--min-agree must be between 1 and {voters} (the voting expert sets), not {threshold}."
        )
    return threshold


def resolve_thresholds(args: argparse.Namespace, roles: dict[str, Role]) -> Thresholds:
    strict = args.mode == "strict"
    cutoff = LILLY_DEFAULT_CUTOFF if args.max_demerits is None else args.max_demerits
    nibr = NIBR_EXCLUDE_SEVERITY if args.nibr_max_severity is None else args.nibr_max_severity
    return Thresholds(
        nibr_reject_at=1 if strict and args.nibr_max_severity is None else nibr,
        lilly_reject_at=1 if strict and args.max_demerits is None else cutoff,
        lilly_engine_cutoff=cutoff,
        consensus_min_agree=resolve_min_agree(args.mode, args.min_agree, roles),
    )


def check_no_column_collisions(columns: list[str], tokens: list[str]) -> None:
    owned = {*OUTPUT_COLUMNS, *tokens}
    clash = [c for c in columns if c in owned or str(c).startswith(CALCULATED_PREFIX)]
    if clash:
        sys.exit(
            f"Input columns {clash} collide with columns this skill adds "
            f"(filter names, scores, '{CALCULATED_PREFIX}*'); rename them in a copy of the input."
        )


def base_role(token: str, mode: str) -> Role:
    if mode == "strict":
        return "reject"
    if mode == "consensus" and token in PANEL_TOKENS:
        return "vote"
    return "flag"


def with_same_list(tokens: list[str]) -> set[str]:
    """Add the twin of Brenk/Dundee: one list in two implementations, so a role covers both."""
    return {*tokens, *(SAME_LIST[t] for t in tokens if t in SAME_LIST)}


def resolve_roles(
    tokens: list[str], mode: str, reject_extra: list[str], flag_extra: list[str]
) -> dict[str, Role]:
    promoted = with_same_list(reject_extra)
    demoted = with_same_list(flag_extra)
    both = promoted & demoted
    if both:
        sys.exit(f"Filters given to both --reject and --flag: {sorted(both)}")
    roles: dict[str, Role] = {}
    for token in tokens:
        roles[token] = base_role(token, mode)
        if token in promoted:
            roles[token] = "reject"
        if token in demoted:
            roles[token] = "flag"
    return roles


def with_consensus_filters(tokens: list[str], mode: str) -> list[str]:
    """Consensus mode always computes the whole expert panel."""
    if mode != "consensus":
        return tokens
    return tokens + [t for t in PANEL_TOKENS if t not in tokens]


def voting_sets(roles: dict[str, Role]) -> dict[str, tuple[str, ...]]:
    """Panel sets that still vote: those with a token whose role is `vote` (--flag removes a vote)."""
    voting = {
        name: tuple(t for t in tokens if roles.get(t) == "vote")
        for name, tokens in EXPERT_PANEL.items()
    }
    return {name: tokens for name, tokens in voting.items() if tokens}


def panel_votes(hit: pd.DataFrame, roles: dict[str, Role], mode: str) -> pd.DataFrame | None:
    """One boolean column per voting set; None outside consensus mode."""
    if mode != "consensus":
        return None
    return pd.DataFrame(
        {name: hit[list(tokens)].any(axis=1) for name, tokens in voting_sets(roles).items()}
    )


def consensus_reasons(votes: pd.DataFrame, min_agree: int) -> pd.Series:
    def reason(row: pd.Series) -> str:
        if row.sum() < min_agree:
            return ""
        sets = ", ".join(row.index[row])
        return f"consensus: flagged by {row.sum()} of {len(row)} expert rule sets ({sets})"

    return votes.apply(reason, axis=1)


def sdf_structure(mol: Chem.Mol | None) -> str | None:
    """SMILES of a record RDKit can parse and sanitize, otherwise None."""
    if mol is None or Chem.SanitizeMol(mol, catchErrors=True) != Chem.SanitizeFlags.SANITIZE_NONE:
        return None
    return Chem.MolToSmiles(mol)


def read_sdf(path: pathlib.Path) -> pd.DataFrame:
    """One row per record; the `smiles` column holds only the parsed structure, None if invalid."""
    rows = []
    for mol in Chem.ForwardSDMolSupplier(str(path), sanitize=False, removeHs=False):
        props = {} if mol is None else mol.GetPropsAsDict(includePrivate=False)
        if mol is not None and mol.HasProp("_Name"):
            props["name"] = mol.GetProp("_Name")
        props.pop("smiles", None)
        rows.append({"smiles": sdf_structure(mol), **props})
    return pd.DataFrame(rows, columns=list(dict.fromkeys(k for row in rows for k in row)))


def leading_comment_lines(path: pathlib.Path) -> int:
    """Leading '#' lines are comments; later '#' is a SMILES triple bond."""
    count = 0
    with open(path, encoding="utf-8-sig") as handle:
        for line in handle:
            if not line.startswith("#"):
                break
            count += 1
    return count


def read_table(path: pathlib.Path, smiles_col: str | None) -> tuple[pd.DataFrame, str]:
    name = path.name.lower()
    if name.endswith(".sdf"):
        return read_sdf(path), "smiles"
    if name.endswith(".smi"):
        df = pd.read_csv(path, sep=r"\s+", header=None, names=["smiles", "name"], engine="python")
        return df.dropna(axis=1, how="all"), "smiles"
    if name.endswith(".xls"):
        sys.exit("Legacy .xls files are not supported; save the sheet as .xlsx or CSV.")
    if name.endswith(".xlsx"):
        df = pd.read_excel(path)
    else:
        separator = "\t" if name.endswith((".tsv", ".txt")) else ","
        df = pd.read_csv(path, sep=separator, skiprows=leading_comment_lines(path))
    column = smiles_col or next((c for c in df.columns if c.lower() in STRUCTURE_COLUMNS), None)
    if column is None or column not in df.columns:
        sys.exit(f"No structure column found; pass --smiles-col. Columns: {list(df.columns)}")
    return df, column


def parse_mol(value: object) -> Chem.Mol | None:
    if not isinstance(value, str):
        return None
    if value.startswith("InChI="):
        return dm.from_inchi(value)
    return dm.to_mol(value)


def rdkit_outcome(mols: list[Chem.Mol], token: str, n_jobs: int) -> Outcome:
    params = FilterCatalogParams()
    params.AddCatalog(RDKIT_CATALOGS[token])
    catalog = FilterCatalog(params)
    smiles = [Chem.MolToSmiles(m) for m in mols]
    matches = RunFilterCatalog(catalog, smiles, numThreads=max(n_jobs, 0))
    return Outcome.from_detail(
        pd.Series(["; ".join(e.GetDescription() for e in hits) for hits in matches])
    )


def alert_set_outcome(mols: list[Chem.Mol], name: str, n_jobs: int) -> Outcome:
    result = CommonAlertsFilters(alerts_set=[name])(mols, n_jobs=n_jobs)
    return Outcome.from_detail(result["reasons"].fillna("").reset_index(drop=True))


def nibr_outcome(mols: list[Chem.Mol], n_jobs: int, max_severity: int) -> Outcome:
    """Severity 10 = exclude, 1-9 = number of flags, 0 = annotation-only matches."""
    result = NIBRFilters()(mols, n_jobs=n_jobs).reset_index(drop=True)
    severity = result["severity"]
    detail = result["reasons"].fillna("")
    scores = pd.DataFrame(
        {
            "nibr_severity": severity,
            "nibr_status": result["status"],
            "nibr_covalent_motifs": result["n_covalent_motif"],
        }
    )
    return Outcome(detail, detail != "", severity >= max_severity, scores)


def lilly_outcome(mols: list[Chem.Mol], n_jobs: int, thresholds: Thresholds) -> Outcome:
    """The engine rejects hard-reject queries and molecules with demerits >= its cutoff."""
    engine = LillyDemeritsFilters(dthresh=thresholds.lilly_engine_cutoff)
    result = engine(mols, n_jobs=n_jobs).reset_index(drop=True)
    demerits = result["demerit_score"]
    rejected = result["status"] == "exclude"
    detail = result["reasons"].fillna("")
    detail = detail.mask((detail == "") & rejected, "rejected by a Lilly reject rule")
    scores = pd.DataFrame({"lilly_demerits": demerits, "lilly_status": result["status"]})
    reject = rejected | (demerits >= thresholds.lilly_reject_at)
    return Outcome(detail, rejected | (demerits > 0), reject, scores)


def rule_outcomes(mols: list[Chem.Mol], rules: list[str], n_jobs: int) -> dict[str, Outcome]:
    """One outcome per rule; the descriptors the rules read are attached to the first one."""
    result = RuleFilters(rules)(mols, n_jobs=n_jobs, keep_props=True).reset_index(drop=True)
    skipped = {*rules, "pass_all", "pass_any", "mol"}
    descriptors = result[[c for c in result.columns if c not in skipped]].add_prefix(
        CALCULATED_PREFIX
    )
    outcomes = {}
    for position, rule in enumerate(rules):
        violated = ~result[rule].astype(bool)
        detail = violated.map({True: "violated", False: ""})
        outcomes[rule] = Outcome.from_detail(detail, descriptors if position == 0 else None)
    return outcomes


def sanity_outcome(mols: list[Chem.Mol], token: str, n_jobs: int) -> Outcome:
    label, check = SANITY[token]
    violated = pd.Series(~check(mols, n_jobs=n_jobs))
    return Outcome.from_detail(violated.map({True: label, False: ""}))


def compute_outcomes(
    mols: list[Chem.Mol], tokens: list[str], n_jobs: int, thresholds: Thresholds
) -> dict[str, Outcome]:
    """Run every selected filter. Each medchem engine gets its own molecule copies: some of them
    (for example the Bredt check) alter molecules in place when run in-process, which would change
    what later filters match, so results would depend on the filter order. The RDKit catalogs only
    read SMILES and use the untouched originals.
    """

    def copies() -> list[Chem.Mol]:
        return [Chem.Mol(m) for m in mols]

    medchem_sets = medchem_alert_sets()
    rules = [t for t in tokens if is_rule(t)]
    outcomes = rule_outcomes(copies(), rules, n_jobs) if rules else {}
    for token in tokens:
        if token in outcomes:
            continue
        if token in RDKIT_CATALOGS:
            outcomes[token] = rdkit_outcome(mols, token, n_jobs)
        elif token == "nibr":
            outcomes[token] = nibr_outcome(copies(), n_jobs, thresholds.nibr_reject_at)
        elif token == "lilly":
            outcomes[token] = lilly_outcome(copies(), n_jobs, thresholds)
        elif token in SANITY:
            outcomes[token] = sanity_outcome(copies(), token, n_jobs)
        else:
            outcomes[token] = alert_set_outcome(copies(), medchem_sets[token], n_jobs)
    return outcomes


def annotate(
    df: pd.DataFrame,
    structure_col: str,
    tokens: list[str],
    roles: dict[str, Role],
    args: argparse.Namespace,
    thresholds: Thresholds,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Annotate every input row with filter columns, scores, flags and the pass/fail decision.

    Also returns the per-filter hit matrix (rows x filters).
    """
    check_no_column_collisions(list(df.columns), tokens)
    df = df.copy()
    df.insert(0, "input_row", range(len(df)))
    parsed = df[structure_col].map(parse_mol)
    valid = parsed.notna().to_numpy()
    mols = list(parsed[valid])
    if mols:
        outcomes = compute_outcomes(mols, tokens, args.n_jobs, thresholds)
    else:
        outcomes = {token: Outcome.empty() for token in tokens}

    def spread(series: pd.Series, fill: object) -> pd.Series:
        full = pd.Series(fill, index=df.index, dtype=object)
        full[valid] = series.to_numpy()
        return full

    df[f"{CALCULATED_PREFIX}qed"] = spread(
        pd.Series([dm.descriptors.qed(m) for m in mols]), float("nan")
    )
    hit = pd.DataFrame(index=df.index)
    reject = pd.DataFrame(index=df.index)
    for token, outcome in outcomes.items():
        df[token] = spread(outcome.detail, "")
        hit[token] = spread(outcome.hit, False).astype(bool)
        reject[token] = spread(outcome.reject, False).astype(bool)
        for column in outcome.scores:
            df[column] = spread(outcome.scores[column], float("nan"))
    rejecting = [t for t in tokens if roles[t] == "reject"]
    flagging = [t for t in tokens if roles[t] != "reject"]
    df["flags"] = hit[flagging].apply(lambda row: "; ".join(t for t in flagging if row[t]), axis=1)
    df["hit_details"] = hit.apply(
        lambda row: "; ".join(f"{t}: {df.at[row.name, t]}" for t in tokens if row[t]), axis=1
    )
    df["n_filters_hit"] = hit.sum(axis=1)
    votes = panel_votes(hit, roles, args.mode)
    consensus = pd.Series("", index=df.index)
    if votes is not None:
        df["n_expert_sets_hit"] = votes.sum(axis=1)
        consensus = consensus_reasons(votes, thresholds.consensus_min_agree)
    failed_by = reject[rejecting].apply(
        lambda row: "; ".join(
            t if df.at[row.name, t] == "" else f"{t}: {df.at[row.name, t]}"
            for t in rejecting
            if row[t]
        ),
        axis=1,
    )
    reasons = ["; ".join(part for part in parts if part) for parts in zip(failed_by, consensus)]
    df["fail_reasons"] = pd.Series(reasons, index=df.index).where(valid, "unparsable structure")
    df["pass_filters"] = df["fail_reasons"] == ""
    df["mol"] = parsed
    return df, hit


def read_qc_summary(qc_dir: pathlib.Path | None) -> dict | None:
    """Row accounting of an earlier molecular-dataset-qc run, kept apart from filter failures."""
    if qc_dir is None:
        return None
    path = qc_dir / "qc_details" / "qc_summary.json"
    if not path.is_file():
        sys.exit(
            f"No QC summary at {path}; --qc-dir must be the molecular-dataset-qc results directory."
        )
    summary = json.loads(path.read_text())
    if not str(summary.get("schema", "")).startswith("molecular-dataset-qc/"):
        sys.exit(f"{path} is not a molecular-dataset-qc summary.")
    return {
        "status": summary["status"],
        "steps": summary["qc_steps"],
        "n_records": summary["input"]["n_records"],
        "row_partition": summary["row_partition"],
    }


def rejects_anything(roles: dict[str, Role], mode: str) -> bool:
    """False for a score-only run, where every filter only flags and pass/fail has no meaning."""
    return mode == "consensus" or any(role != "flag" for role in roles.values())


def summarize(
    df: pd.DataFrame,
    hits: pd.DataFrame,
    tokens: list[str],
    roles: dict[str, Role],
    args: argparse.Namespace,
    thresholds: Thresholds,
    qc: dict | None,
) -> dict:
    rejected = df["fail_reasons"] != ""
    votes = panel_votes(hits, roles, args.mode)
    pass_fail = rejects_anything(roles, args.mode)
    return {
        "input": str(args.input),
        "mode": args.mode,
        "context": args.context,
        "id_col": args.id_col or next((c for c in df.columns if c.lower() in ID_COLUMNS), None),
        "qc": qc,
        "pass_fail": pass_fail,
        "n_input": len(df),
        "n_unparsable": int((df["fail_reasons"] == "unparsable structure").sum()),
        **(
            {"n_pass": int(df["pass_filters"].sum()), "n_fail": int(rejected.sum())}
            if pass_fail
            else {}
        ),
        "thresholds": asdict(thresholds),
        "filters": [
            {
                "token": t,
                "n_hit": int(hits[t].sum()),
                **(
                    {
                        "role": roles[t],
                        "n_fail_reason": int(
                            df["fail_reasons"]
                            .str.contains(rf"(?:^|; ){t}(?::|;|$)", regex=True)
                            .sum()
                        ),
                    }
                    if pass_fail
                    else {}
                ),
            }
            for t in tokens
        ],
        "expert_panel": None
        if votes is None
        else {
            "sets": list(votes.columns),
            "min_agree": thresholds.consensus_min_agree,
            "n_consensus_fail": int(df["fail_reasons"].str.contains("consensus:").sum()),
            # Unparsable structures were not tested, so they are not counted as "no set flagged".
            "vote_distribution": {
                count: int((votes[df["mol"].notna().to_numpy()].sum(axis=1) == count).sum())
                for count in range(votes.shape[1] + 1)
            },
        },
        "groups": {
            g: int(hits[[t for t in members if t in tokens]].any(axis=1).sum())
            for g, members in GROUPS.items()
            if any(t in tokens for t in members)
        },
        "versions": {
            "medchem": metadata.version("medchem"),
            "rdkit": rdkit_version,
            "datamol": metadata.version("datamol"),
        },
    }


def output_tables(df: pd.DataFrame, pass_fail: bool) -> dict[str, pd.DataFrame]:
    if pass_fail:
        return {
            "annotated_all": df,
            "filtered_pass": df[df["pass_filters"]],
            "filtered_fail": df[~df["pass_filters"]],
        }
    return {"annotated_all": df.drop(columns="pass_filters")}


def write_outputs(df: pd.DataFrame, summary: dict, out: pathlib.Path, sdf: bool) -> None:
    out.mkdir(parents=True, exist_ok=True)
    pass_fail = summary["pass_fail"]
    for name, table in output_tables(df, pass_fail).items():
        table.drop(columns="mol").to_csv(out / f"{name}.csv", index=False)
        if sdf and (not pass_fail or name != "annotated_all"):
            dm.to_sdf(table[table["mol"].notna()], out / f"{name}.sdf", mol_column="mol")
    (out / "summary.json").write_text(json.dumps(summary, indent=2, allow_nan=False))
    report.write_markdown(summary, out / "report.md")
    report.write_html(summary, df, out / "report.html")


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--input", type=pathlib.Path, required=True)
    p.add_argument("--output-dir", type=pathlib.Path, required=True)
    p.add_argument("--smiles-col", help="structure column (SMILES or InChI) in a table")
    p.add_argument("--filters", nargs="+", required=True, help="group names and/or filter tokens")
    p.add_argument("--mode", choices=["strict", "score", "consensus"], required=True)
    p.add_argument("--reject", nargs="*", default=[], help="tokens/groups that always reject")
    p.add_argument("--flag", nargs="*", default=[], help="tokens/groups that only flag")
    p.add_argument("--context", default="", help="expected chemistry, shown in the reports")
    p.add_argument(
        "--nibr-max-severity", type=int, help="reject at or above (default 10; strict 1)"
    )
    p.add_argument(
        "--max-demerits",
        type=int,
        help="Lilly demerit cutoff, also its rejection level (default 100; strict rejects any demerit)",
    )
    p.add_argument(
        "--min-agree",
        type=int,
        help="consensus mode: reject molecules flagged by at least this many voting expert rule "
        f"sets, out of up to {len(EXPERT_PANEL)} (default: a majority of the voting sets)",
    )
    p.add_argument("--id-col", help="compound identifier column shown in the report")
    p.add_argument("--qc-dir", type=pathlib.Path, help="molecular-dataset-qc results directory")
    p.add_argument("--n-jobs", type=int, default=-1)
    p.add_argument("--sdf", action="store_true", help="also write pass/fail SDF files")
    return p.parse_args()


def main() -> None:
    args = parse_args()
    tokens = with_consensus_filters(expand(args.filters), args.mode)
    reject_extra = expand(args.reject)
    flag_extra = expand(args.flag)
    validate_tokens(tokens + reject_extra + flag_extra)
    roles = resolve_roles(tokens, args.mode, reject_extra, flag_extra)
    thresholds = resolve_thresholds(args, roles)
    qc = read_qc_summary(args.qc_dir)
    df, structure_col = read_table(args.input, args.smiles_col)
    if args.id_col is not None and args.id_col not in df.columns:
        sys.exit(f"--id-col {args.id_col!r} is not an input column. Columns: {list(df.columns)}")
    annotated, hits = annotate(df, structure_col, tokens, roles, args, thresholds)
    summary = summarize(annotated, hits, tokens, roles, args, thresholds, qc)
    write_outputs(annotated, summary, args.output_dir, args.sdf)
    counts = ("n_input", "n_pass", "n_fail", "n_unparsable")
    print(json.dumps({k: summary[k] for k in counts if k in summary}))


if __name__ == "__main__":
    main()
