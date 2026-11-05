# Life Sciences Marketplace Plugins Environment Specification

This document details the computing environments, runtime prerequisites, biostatistical simulation requirements, and GPU cluster infrastructure required by the skills in `life-science-plugins/skills`.

---

## 1. Skill Suite Overview

The `anthropics/life-sciences` marketplace plugin provides four specialized workflows for life sciences research:

| Skill | Category | Primary Execution Requirements | Core Scripts / Tooling |
| :--- | :--- | :--- | :--- |
| **`binder-gauntlet`** | Protein Binder Validation & Developability | Local Python + Remote GPU Cluster (SLURM / Cloud) | `sequence_profile.py`, `gpu-job.example.yaml`, `thresholds.md` |
| **`clinical-trial-forecast`** | Clinical Trial Success Probability | Local Python 3.10+ (CPU) | `ft_model.py`, `ft_figures.py`, `ft_report.py`, `ft_registry.py` |
| **`clinical-trial-protocol-skill`** | FDA/NIH Clinical Protocol Design | Local Python 3.10+ (CPU) | `sample_size_calculator.py`, `FDA-Clinical-Protocol-Template.md` |
| **`scientific-problem-selection`** | Strategic Problem Choice & Planning | Markdown / Reasoning Framework | 9 Structured Intuition & Decision Frameworks |

---

## 2. Environment Specifications by Skill

### 1. `binder-gauntlet`
* **Workflow**: Validates de novo designed protein binders (from RFdiffusion, ProteinMPNN, ColabDesign, Boltz, AlphaFold, Chai) via 4 gates:
  1. Independent re-prediction with a distinct model.
  2. Negative controls (scrambled binder, non-cognate targets).
  3. Developability liability profiling (charge, hydrophobicity, aggregation propensity, SAP score).
  4. Dossier generation stating explicit limitations.
* **Local Python Environment**: `biopython>=1.83`, `pandas`, `numpy`, `scipy`.
* **Cluster GPU Execution (SLURM / Cloud)**:
  * Running re-prediction models (Boltz-2, ColabFold/AF2, Chai-1) requires NVIDIA GPUs (A100 or H100, >= 24 GB VRAM).
  * Provided templates (`cluster/gpu-job.example.yaml`, `cluster/workspace.example.yaml`) define standard cluster job specifications.

### 2. `clinical-trial-forecast`
* **Workflow**: Ingests registry records from ClinicalTrials.gov and literature; executes a transparent Monte Carlo hazard simulation model to compute trial probability of success (POS); renders an evidence-backed HTML dashboard and cited report.
* **Environment**: Python `>= 3.10`. Runs entirely on CPU.
* **Dependencies**: `numpy`, `scipy`, `pandas` (hazard curve simulation), `matplotlib` (survival curve plots), `markdown` (report HTML formatting), `pyarrow` (Parquet registry ingest).

### 3. `clinical-trial-protocol-skill`
* **Workflow**: Generates FDA-compliant investigational device exemption (IDE) and investigational new drug (IND) protocols adhering to ICH E6(R2), ICH E8, and ICH E9 standards.
* **Environment**: Python `>= 3.10`. Pure CPU.
* **Dependencies**: `scipy`, `statsmodels`, `numpy` (provides power and sample size calculation across binary, continuous, and time-to-event log-rank endpoints via `sample_size_calculator.py`).

### 4. `scientific-problem-selection`
* **Workflow**: Guides life science researchers through Fischbach & Walsh's systematic decision framework (Cell, 2024) across 5 core domains: problem choice, idea pitching, project de-risking, decision trees, and adversity planning.
* **Environment**: Prompt and structured knowledge reference; zero external dependencies.
