# Bio-Research Plugin Environment Specification

This document details the computing environments, runtime prerequisites, container engines, and MCP connector configurations required to execute the skills and integrations in `knowledge-work-plugins/bio-research`.

---

## 1. System Architecture Overview

```
┌────────────────────────────────────────────────────────────────────────┐
│                      Claude Code / Cowork Agent                        │
└───────────────────────────────────┬────────────────────────────────────┘
                                    │
           ┌────────────────────────┴────────────────────────┐
           ▼                                                 ▼
┌──────────────────────────────────────┐  ┌──────────────────────────────────────┐
│       11 Remote HTTP MCP Servers     │  │       5 Analytical Bio Skills        │
│ (.mcp.json connectors to PubMed,     │  │ - instrument-data-to-allotrope       │
│  ChEMBL, Open Targets, Synapse, etc.)│  │ - single-cell-rna-qc                 │
│                                      │  │ - scvi-tools                         │
│                                      │  │ - nextflow-development               │
│                                      │  │ - scientific-problem-selection       │
└──────────────────────────────────────┘  └──────────────────┬───────────────────┘
                                                             │
                                                             ▼
                                          ┌──────────────────────────────────────┐
                                          │      Execution Engines & Tools       │
                                          │ - Python 3.11/3.12 Virtual Env       │
                                          │ - PyTorch (GPU recommended for scVI) │
                                          │ - Nextflow + Docker/Singularity/Conda│
                                          └──────────────────────────────────────┘
```

---

## 2. Execution Environments by Skill

### 1. `instrument-data-to-allotrope`
* **Purpose**: Converts raw data outputs from 40+ laboratory instruments (plate readers, qPCR, spectrophotometers, cell counters) into standardized Allotrope Simple Model (ASM) JSON.
* **Environment**: Pure Python (`>= 3.10`). CPU only.
* **Core Library**: `allotropy==0.1.55` (provides vendor parsers for Agilent, Bio-Rad, Molecular Devices, Roche, Thermo Fisher, Beckman Coulter, etc.).
* **I/O Dependencies**: `pandas`, `openpyxl` (Excel spreadsheets), `pdfplumber` (raw PDF exports).

### 2. `single-cell-rna-qc`
* **Purpose**: Automated quality control, filtering, and doublet detection for scRNA-seq datasets (`.h5ad`, 10x `.h5`).
* **Environment**: Python `>= 3.10`. Multi-core CPU (>= 16 GB RAM recommended).
* **Core Libraries**: `scanpy`, `anndata`, `scipy`, `numpy`, `scrublet` (doublet prediction), `matplotlib`, `seaborn`.

### 3. `scvi-tools`
* **Purpose**: Deep generative probabilistic modeling of single-cell omics (integration, batch correction, label transfer, RNA velocity).
* **Environment**: Python `>= 3.10`, `torch>=2.1.0`. NVIDIA GPU (>= 8 GB VRAM) recommended for neural network training; runs on CPU with fallback.
* **Core Libraries**: `scvi-tools`, `torch`, `scanpy`, `anndata`.

### 4. `nextflow-development`
* **Purpose**: Running and authoring nf-core bioinformatics pipelines (`nf-core/rnaseq`, `nf-core/sarek`, `nf-core/atacseq`) with automatic sample sheet creation.
* **System Prerequisites**:
  * **Java**: OpenJDK 17 or 21 (`java -version`)
  * **Nextflow**: `>= 23.04.0` (`nextflow -version`)
  * **Container Engine**: Docker, Singularity / Apptainer, or Conda (used by Nextflow to isolate pipeline process execution).
  * **Network Access**: Access to GitHub (`nf-core`), NCBI SRA/GEO, and AWS iGenomes reference genome buckets.

### 5. `scientific-problem-selection`
* **Purpose**: Systematic strategic framework for research problem choice, risk derisking, and project decision trees (Fischbach & Walsh, Cell 2024).
* **Environment**: Prompt and knowledge-based reasoning framework; no external binary execution required.

---

## 3. Remote MCP Connector Architecture

The plugin defines 11 cloud-hosted HTTP MCP endpoints in `.mcp.json`. These servers require zero local installation and authenticate through Claude Code / Cowork connectors:

| Connector Name | Endpoint URL | Category |
| :--- | :--- | :--- |
| `pubmed` | `https://pubmed.mcp.claude.com/mcp` | Literature Search |
| `biorender` | `https://mcp.services.biorender.com/mcp` | Scientific Illustration |
| `biorxiv` | `https://hcls.mcp.claude.com/biorxiv/mcp` | Preprints (bioRxiv/medRxiv) |
| `consensus` | `https://mcp.consensus.app/mcp` | AI Literature Synthesis |
| `c-trials` | `https://hcls.mcp.claude.com/clinical_trials/mcp` | ClinicalTrials.gov |
| `chembl` | `https://hcls.mcp.claude.com/chembl/mcp` | ChEMBL Bioactivity |
| `synapse` | `https://mcp.synapse.org/mcp` | Collaborative Research Data |
| `wiley` | `https://connector.scholargateway.ai/mcp` | Wiley Academic Publications |
| `owkin` | `https://mcp.k.owkin.com/mcp` | AI for Biology / Histopathology |
| `ot` | `https://mcp.platform.opentargets.org/mcp` | Open Targets Platform |
| `benchling` | *(Configurable workspace endpoint)* | Electronic Lab Notebook (ELN) |
