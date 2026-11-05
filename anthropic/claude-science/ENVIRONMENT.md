# Claude Science Execution Environments Specification

This document details the computing environments, runtime architectures, container images, hardware requirements, and remote execution backends required to run the skills, foundation models, agents, and MCP server suites in `claude-science`.

---

## 1. Runtime Architecture Overview

Claude Science operates across a tiered hybrid compute architecture:

```
┌────────────────────────────────────────────────────────────────────────┐
│                        Claude Science Host                             │
│       Operon Orchestrator / Agents (Reviewer, Bookmarker, Onboarding)  │
└───────────────────────────────────┬────────────────────────────────────┘
                                    │
           ┌────────────────────────┼────────────────────────┐
           ▼                        ▼                        ▼
┌──────────────────────┐ ┌──────────────────────┐ ┌──────────────────────┐
│     MCP Servers      │ │   Local Containers   │ │ Remote Compute (HPC) │
│ - bio-tools (Python) │ │ - 6 CPU Environments │ │ - Modal Serverless   │
│ - ketcher (Node.js)  │ │ - 6 GPU Environments │ │ - SSH / SLURM Clust. │
└──────────────────────┘ └──────────────────────┘ └──────────────────────┘
```

1. **Host Layer**:
   * Claude Science assistant with tool dispatch (`operon`), memory, artifact rendering (figures inline via `{{artifact:VERSION_ID}}`, 3D structures via Mol* viewer), and review verification (`Verifier`).
2. **MCP Tool Suite Layer**:
   * `bio-tools`: Vendored Python MCP server fleet exposing 25 MCP modules and 62 domain databases over stdio.
   * `ketcher-chemistry`: Node.js MCP server providing chemical structure sketching, SMILES/MOL conversion, and molecular rendering.
3. **Execution Envs (BYOC / Container Images)**:
   * **6 Standardized CPU Environments**: Fast startup, zero weight egress, targeted domain packages.
   * **6 Heavyweight GPU Environments**: Pre-baked model weights, CUDA 12.x / PyTorch / JAX, Hopper/Ada/Ampere GPU optimizations.
4. **Remote Compute Providers**:
   * **Modal** (`remote-compute-modal`): Serverless GPU execution on user-provided Modal accounts for massive jobs.
   * **SSH / SLURM** (`remote-compute-ssh`): HPC cluster submission via batch scripts and polling hooks.

---

## 2. Standardized CPU Environments

All CPU environments build on `python:3.12-slim` with isolated dependency closures:

| Environment | Base Image | Default Tier | Key System Libraries (`apt`) | Primary Python Packages (`pip`) | Associated Skills |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **`dataml-cpu`** | `python:3.12-slim` | 4 vCPU / 16 GB | `libgomp1`, `build-essential` | `scikit-learn`, `xgboost`, `statsmodels`, `pymc`, `arviz`, `shap`, `umap-learn`, `polars`, `zarr` | `figure-composer`, `paper-narrative` |
| **`bio-cpu`** | `python:3.12-slim` | 4 vCPU / 16 GB | `libgomp1`, `build-essential`, `libgl1`, `libglib2.0-0` | `biopython`, `prody`, `biotite`, `scikit-bio`, `pyopenms`, `ete3`, `cobra`, `matchms` | `indication-dossier`, `literature-review` |
| **`chem-cpu`** | `python:3.12-slim` | 4 vCPU / 16 GB | `libxrender1`, `libxext6`, `libsm6`, `libgomp1` | `rdkit`, `openbabel-wheel`, `datamol`, `molfeat`, `PyTDC`, `aizynthfinder` | `ketcher-chemistry`, `diffdock` |
| **`singlecell-cpu`** | `python:3.12-slim` | 8 vCPU / 32 GB | `libgomp1`, `build-essential` | `scanpy`, `anndata`, `leidenalg`, `igraph`, `scrublet`, `cellxgene-census` | `scvi-tools`, `scgpt` |
| **`genomics-cpu`** | `python:3.12-slim` | 8 vCPU / 64 GB | `samtools`, `bedtools`, `bwa`, `wget`, `bzip2`, `libcurl4-openssl-dev`, `liblzma-dev` | `pysam`, `bwa-mem2`, `deeptools`, `gtars`, `pydeseq2`, `biopython` | `borzoi`, `evo2` |
| **`imaging-cpu`** | `python:3.12-slim` | 4 vCPU / 32 GB | `libopenslide0`, `libopenslide-dev`, `libvips42`, `libgl1` | `pydicom`, `pylibjpeg`, `openslide-python`, `pillow`, `scikit-image` | `figure-composer`, `figure-style` |

---

## 3. Specialized GPU Environments & Foundation Models

Heavy molecular modeling and foundation sequence models run in pre-configured GPU containers with read-only weight mounts:

### 1. `proteomics-gpu` (Multimodal Structural Biology)
* **Base Image**: `pytorch/pytorch:2.7.1-cuda12.8-cudnn9-devel`
* **GPU Hardware**: NVIDIA A100 (40/80 GB) or H100 (80 GB), Hopper SM_90 optimized.
* **Pre-baked Models & Weights (~19.5 GB)**:
  * **Boltz-2** (`BOLTZ_CACHE=/datavol/boltz`): `boltz2_conf.ckpt`, `aff.ckpt` (~7.5 GB)
  * **Chai-1** (`CHAI_DOWNLOADS_DIR=/datavol/chai`): models_v2, conformers, ESM2-3B trunk (5.3 GB)
  * **OpenFold3** (`OPENFOLD_CACHE=/datavol/openfold3`): `of3-p2-155k.pt` (2.29 GB)
  * **ESM-2** (`TORCH_HOME=/datavol/torch`): 8M, 650M checkpoints
  * **ProteinMPNN / SolubleMPNN / LigandMPNN**: Checked out under `/app/{proteinmpnn,ligandmpnn}`
* **Associated Skills**: `boltz`, `chai1`, `openfold3`, `proteinmpnn`, `solublempnn`, `ligandmpnn`, `fair-esm2`

### 2. `proteomics-jax-gpu` (ColabFold / AlphaFold2)
* **Base Image**: `ghcr.io/sokrypton/colabfold:1.5.5-cuda12.2.2` (Python 3.9)
* **GPU Hardware**: NVIDIA A10G / A100 / H100 (>= 16 GB VRAM)
* **Software Upgrades**: `jax[cuda12]>=0.4.26,<0.5` with Hopper SM_90 PTX, `dm-haiku<0.0.13`, `numpy<2`
* **Environment Flags**: `XLA_PYTHON_CLIENT_MEM_FRACTION=0.9 TF_FORCE_UNIFIED_MEMORY=0`
* **Pre-baked Weights (4.9 GB)**:
  * `alphafold_params_2022-12-06.tar` extracted to `/datavol/colabfold/params/` with `download_finished.txt` and `download_complexes_multimer_v3_finished.txt` markers
* **Associated Skills**: `alphafold2`

### 3. `genomics-gpu` (Sequence Foundation Models)
* **Base Image**: `pytorch/pytorch:2.7.1-cuda12.8-cudnn9-devel`
* **GPU Hardware**: NVIDIA A100 (80 GB) or H100 (80 GB) (High VRAM required for long contexts)
* **Pre-baked Models & Weights**:
  * **Borzoi** (~17 GB): 4 ensemble folds predicting RNA-seq, CAGE, DNase, ChIP tracks at 524kb window size
  * **Evo 2** (~66 GB): StripedHyena architecture 7B / 40B weights supporting 1M bp context length
* **Associated Skills**: `borzoi`, `evo2`

### 4. `singlecell-gpu` (Single-Cell Foundation Models)
* **Base Image**: `pytorch/pytorch:2.7.1-cuda12.6-cudnn9-devel`
* **GPU Hardware**: NVIDIA A10G / A100 (>= 24 GB VRAM)
* **Acceleration**: `flash-attn==2.8.0.post2`, `ninja-build`
* **Pre-baked Models & Weights**:
  * **scGPT**: Whole-human 33M-cell pre-trained model checkpoint (`scGPT_human.pt`, `vocab.json`)
  * **scvi-tools**: scVI and scANVI PyTorch neural networks
* **Associated Skills**: `scgpt`, `scvi-tools`

### 5. `diffdock-gpu` (Equivariant Diffusion Docking)
* **Base Image**: `pytorch/pytorch:2.4.1-cuda12.4-cudnn9-devel`
* **GPU Hardware**: NVIDIA T4 / A10G / A100 (>= 16 GB VRAM)
* **Pre-baked Weights (~1.6 GB)**:
  * DiffDock-L diffusion model score weights and confidence predictor weights
* **Associated Skills**: `diffdock`

---

## 4. MCP Servers Runtime Setup

### 1. `bio-tools` (Python stdio MCP)
* **Location**: `anthropic/claude-science/mcp-servers/bio-tools`
* **Execution**: Runs directly via Python stdio:
  ```bash
  python run_server.py <package_name>
  # Example: python run_server.py mcp_pubmed
  # Example: python run_server.py mcp_chembl
  ```
* **Dependencies**: `mcp`, `httpx`, `requests`, `pandas`, `biopython`, `lxml`

### 2. `ketcher-chemistry` (Node.js stdio MCP)
* **Location**: `anthropic/claude-science/mcp-servers/ketcher-chemistry`
* **Execution**: Runs directly via Node.js stdio:
  ```bash
  node server.js
  ```
* **System Prerequisite**: Node.js >= 18.0.0 (Node.js 20 LTS recommended)

---

## 5. Remote Compute & Cluster Gateways

When heavy compute cannot be run locally:
* **Modal** (`remote-compute-modal`):
  * Configure API tokens: `modal token set --token-id ... --token-secret ...`
  * Claude Science automatically deploys serverless containers with pre-attached GPU volumes (`@app.function(gpu="A100", volumes={...})`).
* **SSH / SLURM** (`remote-compute-ssh`):
  * Configured via `~/.ssh/config` or cluster credentials.
  * Workflows submit SLURM batch scripts (`sbatch`), monitor queue state via `wait_for_notification`, and rsync back result artifacts (`save_artifacts`).
