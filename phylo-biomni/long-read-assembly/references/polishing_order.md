# Polishing order and the research-only hard-refusal gate

Read on demand. Two rules are enforced in code (`scripts/lra.py`): polishing must proceed in an
**accuracy-non-decreasing order** (`check_polish_order`), and **research-use-only polishers are
hard-refused** (`medaka_gate`). Tool facts from R04 `tool_comparison_matrix.csv`.

## Accuracy-improving order

Apply long-read consensus polishing before short-read polishing, and never apply a less-accurate
polisher after a more-accurate one. The rank the skill uses:

| Polisher | Rank | Role | Offered here? |
|---|---|---|---|
| Racon | 1 | fast long-read consensus (rough) | **yes — the only offered polisher (MIT)** |
| NextPolish / POLCA | 3 | short-read polish, applied last | not offered by this skill |

Valid orders are non-decreasing in rank. A wrong order (a short-read polish before long-read consensus)
is rejected with `not_computable` and `polish_order_violations = 1` before any facts are written.
Over-polishing (repeated rounds past convergence) can introduce errors. The package records requested
production polishing commands and stops when the selected tool is unavailable; it does not substitute
a native consensus implementation.

## Racon is the only offered polisher — and its accuracy limits

The commercially-usable route is `--polish racon` (MIT). Racon is a **rough** alignment/partial-order
consensus polisher: it corrects many indel/mismatch errors but is **weaker than a neural-network
consensus step**, so residual consensus errors — especially in homopolymers — can remain. Assembly
consensus accuracy depends on the selected reads and assembly. The package does not execute an
in-process consensus substitute. Request `--polish none` to QC an unpolished draft.

## Research-use-only polishers are hard-refused (never run)

Neural-network ONT consensus polishers (e.g. **Medaka**) are distributed under the **Oxford Nanopore
Technologies Public License** (research purposes only; commercial use prohibited). This skill therefore
**does not offer them and hard-refuses any request for one**:

- never bundled, never invoked, and there is **no user-licensed adapter** or "ready"/would-run path;
- a `--polish …,medaka`-style request returns `polish_status.json:medaka_gate.status =
  refused_research_only`, falls back to Racon (MIT)/none, and **never fabricates** a polished consensus;
- availability on `PATH` is deliberately not probed, so no restricted adapter is retained.

To use a research-use-only polisher you must obtain a separate license and run it entirely outside this
skill, then QC the result via `--assembler provided_draft --draft <polished.fasta>`.

## What the QV numbers mean

The skill reports reference-free Merqury-style consensus QV for the draft and the polished assembly
(`qv_draft`, `qv_polished`). Expect `qv_polished >= qv_draft`; a drop indicates a bad read placement or
an over-aggressive polish. A QV that reaches the **Q60 display ceiling** (100% of assembly k-mers
supported) is reported as a **saturated lower bound** (`qv_polished_saturated = true`, state
`capped_at_60`), never as an exact measured Q60. QV is consensus quality, not reference accuracy — see
`assembly_qc_interpretation.md`.
