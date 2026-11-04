---
name: med-chem-filters
description: 'Use to screen a set of small molecules (SMILES, CSV/TSV, Excel .xlsx,
  SDF or InChI) with medicinal-chemistry filters: assay-interference alerts (PAINS),
  reactive and toxic structural alerts (Brenk, NIH, ChEMBL alert sets), NIBR severity,
  Lilly MedChem demerits, drug-likeness rules (Ro5, Veber, lead-like, CNS, ...) and
  chemical sanity checks, via RDKit FilterCatalog and datamol-io/medchem. Interprets
  the dataset and asks which filters to apply and whether to reject (strict pass/fail),
  score (demerits, severities) or apply the consensus of expert rule sets (a configurable
  agreement heuristic), before filtering anything; when the user delegates, applies
  only the consensus. Returns passing and failing datasets with all scores attached
  plus md and html reports. Not for dataset cleaning (use molecular-dataset-qc) or
  ADMET property prediction (use molecular-property-admet).'
category: drug_discovery
visibility: public
starting-prompt: Flag PAINS and reactive compounds in my screening library and split
  it into molecules that pass and fail.
---
# Med-Chem Filters

Applies structural-alert, drug-likeness and sanity filters to a molecule set and splits it into
molecules that pass and molecules that fail, with every score and the reason for each failure kept.

**These filters are expert opinion, not fact.** The rule sets were written by different groups for
different purposes and often disagree: in Pat Walters' comparison, 4 of the 8 ChEMBL alert sets
reject a given sulfonic acid and the other 4 do not ("a lot of these filters are somewhat subjective
and based on people's experience"). Many marketed drugs and good probes carry alerts, and PAINS came
from one assay technology (Baell & Holloway, 2010; Capuzzi et al., 2017). A single hit is therefore a
prompt for a chemist's review, not a verdict, and the skill rejects only what the user chooses to
reject. When the user leaves the decision to the agent, it applies the **consensus**: only what a
majority of the expert rule sets agree on (a heuristic; the lists overlap, so it is not independent
corroboration). Never every available filter.

Use the printed **Skill Directory** as `SKILL_DIR`.

## Installation

```bash
uv pip install "medchem>=2.1.1" openpyxl   # openpyxl only for .xlsx input
medchem install-lilly --no-test             # only when Lilly filters are wanted
```

`medchem install-lilly` downloads and compiles LillyMol with `make` and a C++ compiler (about a
minute; not supported on native Windows). If it fails, run without `lilly` and say so.

## 1. Read and interpret the input

Accepted: a SMILES list or `.smi`, CSV/TSV/`.xlsx` with a SMILES or InChI column (`--smiles-col` if the
name is not `smiles`, `canonical_smiles`, `structure`, `smi` or `inchi`), SDF. `.csv` is comma and
`.tsv`/`.txt` tab separated; leading `#` comment lines are skipped. SMILES pasted in chat go to a
`.smi` file first. Records that cannot be parsed, including invalid SDF records, are kept in the
failing table as `unparsable structure` with their other fields. For SDF the structure column is
`smiles` and replaces any property of that name. Keep an identifier column and give
it with `--id-col` (default: `id`, `compound_id`, `name` or `compound_name` if present); it shows in
the report and links every row back to the input.

Then look at the data and form a view of what it is: how many molecules, the structure, ID, name and
label columns, whether it looks raw (salts or mixtures, odd charge states, duplicates, parse
failures), and the kind of set: marketed or reference drugs (recognisable names, drug-like property
ranges), screening or commercial library, generated molecules, hit list or optimization series, or
natural products, peptides, PROTACs and macrocycles. State what you see and why in two or three lines.
The kind of set decides what to suggest in step 3.

## 2. Profile the set

Install the dependencies (Lilly too, unless it fails; then drop `lilly` and say so). Run the script
twice into a scratch directory, never the results directory, and do not deliver these files, once with
`--mode consensus` and once with `--mode strict`, both over
`--filters interference reactive-toxic nibr lilly druglike sanity`. For sets above 2,000 molecules
profile a random sample of 2,000 written to a scratch file. In `summary.json`, `filters[].n_hit` and
`groups` are the molecules each filter and group hit, `n_fail` is how many each mode would remove, and
`expert_panel.vote_distribution` (parsable molecules only) says how many molecules the expert sets agree on (flagged by many
sets) and how many they contest (flagged by one or two). This tells the user what rejecting costs on
their own data before they choose.

## 3. Stop and ask

**Stop here. Do not clean or run the final filter until the user has answered.** This holds even when
the request names filters or a purpose: pre-tick what they named and confirm it. There are two
exceptions. The user says explicitly to go ahead without questions, or the user **delegates the
decision** ("you decide", "best practice", "whatever you think", "defaults"). Then skip the
questionnaire: ask only the cleaning question if the set looks raw, run the consensus mode (step 5),
and say plainly what you applied and why. Delegation is never a licence to apply every filter.

Otherwise ask in one message, as checkbox and single-choice questions worded as below, with the
dataset-specific parts filled in from steps 1 and 2. Each filter line gives its one-line description
and reference from the filter catalog below.

> I read your file as  (<why></why>). <Only if raw: It looks uncleaned: <salts,
> mixtures, duplicates, parse failures>.> The nine expert rule sets agree on <a></a> of N molecules
> (flagged by at least 5), contest <b></b> (flagged by one to four) and flag none of <c></c>. Applying every
> filter I offer would remove <y></y> of N.
>
> Filters are expert opinion and the sets often disagree, so unless you prefer otherwise I would only
> reject what they agree on.
>
> **Clean the dataset first with QC?** (only when it looks raw) yes or no, and what it would change.
>
> **Who decides which filters apply?** (choose one)
>
> - **You decide (recommended)**: consensus. I reject molecules flagged by at least 5 of the 9 expert
>   rule sets (a heuristic: the lists overlap, and you can change the threshold or drop a set).
>   Everything else is flagged or scored but kept. This would remove <x></x> of N.
> - **I choose the filters**: answer the next two questions.
>
> **Which filters should be applied?** (multi-select; pre-ticked from the table below)
>
> - **Assay interference**: <description, reference>; <n></n> molecules flagged
> - **Reactive and toxic alerts**: <description, references>; <n></n> molecules flagged
> - **NIBR**: <description, reference>; <n></n> molecules flagged
> - **Lilly MedChem**: <description, reference>; <n></n> molecules flagged
> - **Drug-likeness rules**: one profile, suggested <profile></profile>; <n></n> molecules flagged
> - **Chemical sanity**: <description></description>; <n></n> molecules flagged
>
> **What should the filters do?** (choose one; or name the ones that reject and the ones that only flag)
>
> - **Strict pass/fail**: every hit rejects the molecule.
> - **Scores only**: nothing is removed; Lilly demerits, NIBR severity, alert counts and QED are
>   attached so you can rank or set thresholds later.
> - **Consensus**: reject only on the agreement of the expert sets; the filters you ticked are
>   flagged and scored.
>
> Rejecting filters use a Lilly demerit cutoff of 100 and NIBR severity 10 unless you give others;
> lower is stricter.
>
> **Is the set expected to contain anything the filters usually reject?** (covalent warheads,
> prodrugs, PROTACs or macrocycles, fragments, natural products, known drugs.) I assumed <inferred></inferred>.

What to suggest, by the kind of set:

| Looks like                                                                  | Suggest                                                                                                                                                                                                                                                                                                                        |
| --------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| Marketed or approved drugs, clinical candidates, reference or known actives | Alerts are expected: in a test on 31 common marketed drugs, rejecting on every filter removed nearly all, while consensus removed one (captopril). Suggest consensus or scores only; offer one rule profile (Ro5 + Veber) as the only other thing that rejects. Say that rejecting on single filters removes many known drugs. |
| Commercial or HTS screening library                                         | Consensus. A stricter library cut can add NIBR, Lilly or a rule profile as rejecting filters if the user wants it.                                                                                                                                                                                                             |
| Generated molecules                                                         | Consensus, with the sanity checks as flags to review, then a drug-like rule profile as rejecting if the user wants it.                                                                                                                                                                                                         |
| Hit list or optimization series                                             | Scores and flags; reject only what the user names.                                                                                                                                                                                                                                                                             |
| Natural products, peptides, PROTACs, macrocycles                            | Scores and flags; see the exceptions table.                                                                                                                                                                                                                                                                                    |

## 4. Clean first when the user said yes

SMARTS alerts are sensitive to salts, charge states, tautomers and duplicates. The question is part of
step 3; skip it when the input is `cleaned_data/cleaned_data.csv` from a complete
`molecular-dataset-qc` run, or the user says it is already clean. On yes, load and follow
`molecular-dataset-qc` in its own results directory. Run only `validity,standardize`; add
`deduplicate` only when the user wants unique molecules or duplicates would distort the counts, since
it changes the number of rows. Choose each other policy yourself from the context, state the choice
and its reason in one line, and do not relay QC's questions. Mixtures need judgement: salts and
solvates usually mean the largest fragment is the molecule to screen, but a genuine multi-component
entry may not. Filter only the `cleaned_data.csv` of a run whose status is `complete`; if QC reports
`needs_resolution`, settle it through QC's own review loop rather than filtering its candidate rows.
Run the filters on that file with
`--qc-dir <QC results directory>` so both reports state what QC removed or quarantined (rows in
`qc_details/removed_data.csv` and `quarantined_data.csv`) apart from filter failures. QC's
`row_audit.csv` maps the retained rows back to raw rows through the identifier column.

## 5. Run

Use the user's answers: `--filters` from the ticked groups, `--mode` from their choice (it is
required), `--reject`, `--flag` and the cutoffs from what they asked for, `--context` from the
expected chemistry.

```bash
python3 "$SKILL_DIR/scripts/med_chem_filters.py" \
  --input "$INPUT" --output-dir /mnt/results/med_chem_filters \
  --filters interference reactive-toxic nibr lilly druglike \
  --mode consensus \
  --context "covalent series, acrylamide warheads expected" --flag lilly
```

Other options: `--smiles-col`, `--id-col`, `--qc-dir`, `--min-agree` (consensus threshold),
`--reject` and `--flag` (tokens or groups, not both for one filter), `--context`, `--n-jobs` (default all cores), `--sdf` for SDF copies of the pass
and fail sets. Cost is roughly 0.5 ms per molecule per alert set (NIBR ~1.3 ms);
for libraries beyond ~100k molecules select fewer sets. Use a new output directory per run.

## 6. Review what was removed

The filters cannot be used blindly: look at what they remove. After the run, and before presenting the
result as final, show about ten removed molecules from `filtered_fail.csv` (ID, SMILES, `fail_reasons`,
which names the sets that agreed) and the number of contested molecules (flagged by some sets but kept).
Ask whether anything removed looks wrong, or anything kept looks bad, and offer a different
`--min-agree`, `--flag` or `--reject` and a rerun. Do this for every mode that rejects.

## 7. Read the results

Quote every number from `summary.json`. When filters reject (`pass_fail` is true), start with how many
molecules were removed or flagged. In a score-only run (`pass_fail` is false) nothing passes or fails:
do not use those words; report the scores, the score distributions and the molecules flagged per
filter, and suggest thresholds. Point the user to `report.html` first, then give the
counts and the two or three filters that removed or flagged the most. Describe alert hits as
things to look at: name the alert (`hit_details` lists every match, `fail_reasons` what rejected, when anything rejects) and, for borderline
molecules, whether a chemist would likely accept it. Mention when Lilly hard-rejects (small size,
reactive groups) removed molecules that had no demerit score, and state QC's removals separately.

If the user wants a PDF, follow `pdf-report-generation` using `summary.json` and the tables.

## Filter catalog

Source for the descriptions in the step 3 question. What rejects is set by the mode, not by the group.

**`interference`**: compounds that give false positives in assays.

- `pains_a`, `pains_b`, `pains_c`: PAINS families, 480 substructures from AlphaScreen frequent
  hitters (Baell & Holloway, J Med Chem 2010). They transfer poorly to other assay types
  (Capuzzi et al., J Chem Inf Model 2017).
- `alarm-nmr`: thiol-reactive false positives found by ALARM NMR (Huth et al., JACS 2005).
- `alphascreen-hitters`, `gst-hitters`, `his-hitters`: frequent hitters in AlphaScreen, GST/GSH and
  His-tag/Ni-chelate assay technologies.
- `luciferaseinhibitor`, `chelator`, `frequent-hitter`: likely luciferase inhibitors, metal chelators
  and general frequent hitters.

**`reactive-toxic`**: reactive, unstable or toxic groups.

- `brenk`: unwanted reactive, toxic or pharmacokinetically poor groups, from Dundee's library design
  for neglected diseases (Brenk et al., ChemMedChem 2008).
- `nih`: NIH reactive, aggregation and fluorescence artifact groups (Jadhav et al., J Med Chem 2010).
- `glaxo`, `bms`, `mlsmr`, `inpharmatica`, `lint`, `surechembl`: pharma and screening-center alert
  sets collected in ChEMBL: Glaxo Wellcome hard filters (Hann et al., J Chem Inf Comput Sci 1999),
  BMS HTS deck filters (Pearce et al., J Chem Inf Model 2006), NIH MLSMR excluded functionality,
  Inpharmatica unwanted fragments, Pfizer LINT and the SureChEMBL list of groups unfriendly to
  medicinal chemistry.
- `electrophilic`, `toxicophore`: electrophiles that bind proteins unselectively; general toxicophores.
- `genotoxic-carcinogenicity`: Benigni-Bossa mutagenicity and carcinogenicity rules (JRC, 2008).

**`nibr`** (rejects at severity 10 when set to reject): 444 Novartis substructures, each marked exclude, flag or
annotation. `nibr_severity` is 10 for any exclude, otherwise the number of flags; `nibr_covalent_motifs`
counts covalent warheads (Schuffenhauer et al., J Med Chem 2020).

**`lilly`** (rejects at demerits ≥ 100 or a hard reject when set to reject): Lilly MedChem Rules, 275 queries for reactive,
interfering or promiscuous groups. Hard rejects remove a molecule outright; demerits accumulate and
rank the rest. Molecules under 7 or over 40 heavy atoms are rejected (Bruns & Watson, J Med Chem 2012).

**`druglike`**: property rules; pick one profile that suits the project.

- `rule_of_five` (Lipinski et al., Adv Drug Deliv Rev 1997) with `rule_of_veber` (Veber et al.,
  J Med Chem 2002): the default pair for oral small molecules.
- `rule_of_leadlike_soft`, `rule_of_druglike_soft`: FAF-Drugs4 lead-like and drug-like profiles
  (Lagorce et al., Bioinformatics 2017).
- `rule_of_three`: fragments (Congreve et al., Drug Discov Today 2003).
- `rule_of_five_beyond`: bRo5 space for macrocycles and PROTACs (Doak et al., J Med Chem 2016).
- `rule_of_cns`: CNS-penetrant ranges (Jeffrey & Summerfield, Trends Pharmacol Sci 2010).
- `rule_of_reos`: HTS hit filter (Walters & Namchuk, Nat Rev Drug Discov 2003).
- `rule_of_gsk_4_400` (Gleeson, J Med Chem 2008), `rule_of_pfizer_3_75` (Hughes et al., Bioorg Med
  Chem Lett 2008): ADMET and toxicity rules of thumb.
- `rule_of_generative_design(_strict)`: medchem's profile for generative-model output.
- Also `rule_of_four`, `rule_of_ghose`, `rule_of_egan` and others; an unknown token lists them all.

**`sanity`**, mostly for generated molecules, always flags to review and never an automatic reject in
consensus: `graph` (strained ring topologies; it also matches real scaffolds such as
bicyclo[1.1.1]pentane, spiro[3.3]heptane and cubane),
`bredt` (anti-Bredt bridgehead double bonds; medchem's version also fails every cumulated double bond,
such as azides, isocyanates, allenes and ketenes, e.g. zidovudine, so check what it removes before
letting it reject), `complexity` (Bertz complexity above the ZINC 99th percentile for the molecule's
weight).

Other tokens: `pains` (A+B+C), `zinc` (ZINC drug-likeness and functional-group filters) and the
remaining medchem alert sets (`dundee`, `dnabinder`, `skin`, `ld50-oral`, `reactive-unstable-toxic`, ...).
Overlapping sets (Brenk is Dundee) add noise, not information.

## Expected exceptions

Filters encode what is usually unwanted, and some projects want it. The step 3 question asks whether
the set is expected to contain any of the rows below; adjust roles with `--flag` (demote to flag only) and `--reject`, pick the matching rule profile,
and pass a short `--context` text so the reports say why filters were relaxed.

| Expected                            | Adjustment                                                                                                                                                                                                                                                     |
| ----------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Covalent ligands                    | `electrophilic`, `alarm-nmr` and the reactive sets stay flags; demote `lilly`, which hard-rejects Michael acceptors (`michael_rejected`). Read `nibr_covalent_motifs` and report molecules whose only failures are warhead alerts.                   |
| Prodrugs                            | Esters, carbamates and phosphates trip Lilly and Brenk (aspirin is rejected as `phenolic_ester_or_carbamate`); demote `lilly` and review the reasons.                                                                                                      |
| PROTACs, macrocycles, bRo5          | Demote `druglike`, `lilly` (40 heavy-atom limit) and `complexity` to flags. `rule_of_five_beyond` is itself bounded (MW up to 1000, at most 20 rotatable bonds) and PROTACs can exceed it, so use it as a flag unless the project adopts those bounds. |
| Fragments                           | Use `rule_of_three`; demote `lilly` (7 heavy-atom minimum).                                                                                                                                                                                                |
| Natural products, peptides          | Demote `complexity` and `nibr`.                                                                                                                                                                                                                            |
| Dyes, probes, known assay artifacts | Keep `interference` as flags and say interference is expected.                                                                                                                                                                                               |

## Modes and thresholds

The modes offered in step 3. In every mode `--reject` promotes tokens or groups to reject (for example
`--reject pains_a` for a hard PAINS cut) and `--flag` demotes them, so "these reject, those only flag"
is a mode plus the user's own lists.

- `strict`: every selected filter rejects on any hit: NIBR severity of at least 1 and any Lilly
  demerit or hard reject (Lilly scores are the same as in the other modes). Annotation-only NIBR
  matches (severity 0) are reported but do not reject.
- `score`: nothing is rejected; each molecule gets alert names, NIBR severity, Lilly demerits, QED,
  rule results and a hit count, so the user can rank or threshold later. Only unparsable rows fail.
- `consensus`: rejects a molecule flagged by at least `--min-agree` of the voting expert rule sets
  and nothing else; every other filter is flagged and scored but never rejects. Panel: Glaxo, Dundee,
  BMS, PAINS (A/B/C pooled), SureChEMBL, MLSMR, Inpharmatica, LINT and NIH (Brenk is the Dundee set).
  The default threshold is a majority of the voting sets (5 of 9). `--flag <set>` removes that set's
  vote and `--reject <set>` makes it reject on its own; a role given to `brenk` or `dundee` covers
  both (one list, two implementations), so `--flag reactive-toxic` demotes Dundee too. At least two
  sets must vote, and `--min-agree` must be between 1 and the number of voting sets. The column `n_expert_sets_hit` and the reason `consensus: flagged by N of M expert rule sets (...)` show who agreed. The `sanity` filters stay flags: `graph` also matches real
  scaffolds (bicyclo[1.1.1]pentane, spiro[3.3]heptane, cubane) and medchem's `bredt` also fails every
  cumulated double bond (azides, isocyanates, allenes; e.g. zidovudine).
  **It is a heuristic, not independent corroboration.** The lists overlap: Glaxo and MLSMR share 46 of
  Glaxo's 55 SMARTS, Glaxo and SureChEMBL 31, SureChEMBL and MLSMR about a quarter; captopril's six
  votes all trace to its thiol. ChEMBL's own documentation keeps five of these sets (Glaxo, Dundee,
  BMS, PAINS, MLSMR) because it removed poorly documented ones, so SureChEMBL, Inpharmatica and LINT
  carry less documentation. The default was checked on a small set only: 30 of 31 common marketed
  drugs survive (captopril is removed) and 18 of 20 uncontroversially bad molecules (acyl and sulfonyl
  halides, isocyanates, azide, peroxide, alkylators, quinone, ene-rhodanine, organomercury, ...) are
  rejected, while a threshold of 4 would remove three more drugs (penicillin G, amoxicillin, warfarin). Known drugs
  that carry a widely disliked group still fall out (captopril's thiol, zidovudine's azide), which is
  why step 6 matters.

Thresholds reject at or above the value: `--nibr-max-severity` (default 10) and `--max-demerits`
(integer, default 100); strict mode rejects from 1 unless they are given. Lilly reject rules (reason
"rejected by a Lilly reject rule") reject at any cutoff. `--max-demerits` is the Lilly engine's own
cutoff: demerits are exact below it, a rule that reaches it becomes a reject scored at the cutoff, and
it also scales the molecule-size penalty. Compare `lilly_demerits` only between runs with the same
cutoff, and leave it at 100 when the scores matter.

## Outputs (under the output directory)

| File                                                                          | Content                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                           |
| ----------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `filtered_pass.csv` / `filtered_fail.csv` (only when some filter rejects) | Original columns, unchanged, plus `calc_qed`, one column per filter (matched alerts, empty if clean), `nibr_severity`, `nibr_status`, `nibr_covalent_motifs`, `lilly_demerits`, `lilly_status`, the descriptors the rules read (`calc_mw`, `calc_clogp`, `calc_tpsa`, `calc_n_hbd`, ... when a rule is selected), `flags` (flag-role filters that matched), `hit_details` (every match with its reason, whatever the role), `n_filters_hit`, `n_expert_sets_hit` (how many of the nine expert sets flagged it, when the whole panel ran), `fail_reasons`, `pass_filters`. Input columns that collide with these names or the `calc_` prefix stop the run: rename them in a copy |
| `annotated_all.csv`                                                         | Every input row with the same columns, the one table of a score-only run (without `pass_filters`; `fail_reasons` then only marks unparsable structures)                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                       |
| `*.sdf`                                                                     | Pass and fail sets (or the one scored table in a score-only run) as SDF with the same properties (with `--sdf`)                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                 |
| `summary.json`                                                              | `pass_fail` (false for a score-only run, which then has no `n_pass`, `n_fail` or roles), counts per filter and group, thresholds, expected chemistry, QC row accounting, software versions                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                  |
| `report.md`, `report.html`                                                | Summary; the html adds bars of molecules flagged per filter, score histograms and a sortable, searchable molecule table with IDs, SMILES, structures (first 300 rows) and matched alerts                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          |

## Caveats

- Filters see the structures as given; salts, counter-ions and charge states change alert hits.
- Lilly hard rejects have no `lilly_demerits` value; use `lilly_status`. Strict mode also rejects any
  single demerit, which is far stricter than the Lilly default.
- Property-rule failures name only the rule; compare the descriptor columns with the rule's bounds.
- `rdkit` PAINS A/B/C and medchem sets overlap in part; compare per-filter columns, not totals.
- Property rules are guidelines for oral small molecules; see Expected exceptions for chemistry that needs
  other profiles or flag-only filters.
- Complexity percentiles are relative to ZINC purchasable compounds.

## Sources

References are given with each filter in the filter catalog;
[datamol-io/medchem](https://github.com/datamol-io/medchem) (Apache-2.0); RDKit FilterCatalog.
