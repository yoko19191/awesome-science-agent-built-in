# Phylo-Biomni CLI Tools Specification & Installation Guide

This document specifies the required external Command Line Interface (CLI) tools, workflow engines, and binary packages needed to execute bioinformatics pipelines, sequencing analysis, structural biology modeling, and variant calling across the **92 skills** in `phylo-biomni`.

---

## 1. Quick Installation via Conda / Mamba

The fastest and most reproducible way to install all bioinformatics CLI tools is through [Miniforge / Mamba](https://github.com/conda-forge/miniforge):

```bash
# 1. Create a dedicated bioinformatics CLI environment
mamba create -n phylo-cli -c bioconda -c conda-forge \
    samtools bcftools bedtools mosdepth bwa bwa-mem2 \
    star salmon fastqc \
    macs3 mageck \
    flye hifiasm sniffles cutesv \
    picrust2 \
    autodock-vina fpocket \
    gatk4 snpeff \
    nextflow openjdk=17 -y

# 2. Activate environment
conda activate phylo-cli
```

An automated shell installer is also provided:
```bash
bash install_cli_tools.sh
```

---

## 2. Comprehensive CLI Tools Matrix

| Tool | Category | Bioconda / Conda Package | Associated Skills | Verification Command |
| :--- | :--- | :--- | :--- | :--- |
| **samtools** | Sequence Analysis | `bioconda::samtools` | `rnaseq-fastq-to-counts`, `long-read-assembly`, `long-read-structural-variants`, `rare-disease-case-prioritization`, `variant-calling-from-sequencing` | `samtools --version` |
| **bcftools** | Variant Analysis | `bioconda::bcftools` | `variant-calling-from-sequencing`, `rare-disease-case-prioritization`, `genetic-variant-annotation` | `bcftools --version` |
| **bedtools** | Genomic Intervals | `bioconda::bedtools` | `chip-atlas-diff-analysis`, `chip-atlas-peak-enrichment`, `chip-atlas-target-genes`, `long-read-structural-variants`, `scatac-multiome-analysis` | `bedtools --version` |
| **mosdepth** | Coverage Analysis | `bioconda::mosdepth` | `rare-disease-case-prioritization`, `variant-calling-from-sequencing` | `mosdepth --version` |
| **bwa** / **bwa-mem2** | Read Alignment | `bioconda::bwa`, `bioconda::bwa-mem2` | `variant-calling-from-sequencing`, `rare-disease-case-prioritization` | `bwa` / `bwa-mem2 version` |
| **STAR** | RNA Alignment | `bioconda::star` | `rnaseq-fastq-to-counts` | `STAR --version` |
| **salmon** | Transcript Quantification | `bioconda::salmon` | `rnaseq-fastq-to-counts`, `bulk-rnaseq-counts-to-de-deseq2` | `salmon --version` |
| **FastQC** | Sequencing QC | `bioconda::fastqc` | `rnaseq-fastq-to-counts` | `fastqc --version` |
| **MACS3** | Peak Calling | `bioconda::macs3` | `scatac-multiome-analysis`, `chip-atlas-diff-analysis` | `macs3 --version` |
| **MAGeCK** | CRISPR Screens | `bioconda::mageck` | `pooled-crispr-screens` | `mageck --version` |
| **Flye** | Long-Read Assembly | `bioconda::flye` | `long-read-assembly` | `flye --version` |
| **Hifiasm** | HiFi Genome Assembly | `bioconda::hifiasm` | `long-read-assembly` | `hifiasm --version` |
| **Sniffles2** | Long-Read SV Calling | `bioconda::sniffles` | `long-read-structural-variants` | `sniffles --version` |
| **cuteSV** | Long-Read SV Detection | `bioconda::cutesv` | `long-read-structural-variants` | `cuteSV --version` |
| **PICRUSt2** | Microbiome Prediction | `bioconda::picrust2` | `microbiome-analysis` | `picrust2_pipeline.py --version` |
| **AutoDock Vina** | Molecular Docking | `conda-forge::vina` / `bioconda::autodock-vina` | `chembl-hit-series-sar-docking`, `large-scale-virtual-screening`, `ligand-binding-mode-analysis` | `vina --version` |
| **fpocket** | Cavity / Pocket Detection | `bioconda::fpocket` | `target-tractability-druggability`, `ligand-binding-mode-analysis` | `fpocket --version` |
| **GATK4** | Variant Calling | `bioconda::gatk4` | `variant-calling-from-sequencing` | `gatk --version` |
| **SnpEff** | Variant Annotation | `bioconda::snpeff` | `genetic-variant-annotation`, `rare-disease-case-prioritization` | `snpEff -version` |
| **Nextflow** | Workflow Orchestration | `bioconda::nextflow` | `pipeline-from-analysis`, `pipeline-hpc-validate`, `pipeline-module-author` | `nextflow -version` |

---

## 3. Specialized Tools & Container Images

### DeepVariant
For production-grade deep learning variant calling, Google's **DeepVariant** is recommended via container to ensure GPU acceleration and model weight alignment:
```bash
# Docker invocation
docker run --gpus all -v "${PWD}":/workdir google/deepvariant:1.6.0 \
    /opt/deepvariant/bin/run_deepvariant \
    --model_type=WGS \
    --ref=/workdir/reference.fasta \
    --reads=/workdir/input.bam \
    --output_vcf=/workdir/output.vcf.gz

# Singularity / Apptainer invocation
singularity exec --nv docker://google/deepvariant:1.6.0 run_deepvariant ...
```

### Ensembl VEP (Variant Effect Predictor)
```bash
# Bioconda install or Docker
mamba install -c bioconda ensembl-vep
# Alternatively via official Docker:
docker pull ensemblorg/ensembl-vep:latest
```

---

## 4. System Prerequisites

### Linux (Ubuntu / Debian)
```bash
sudo apt-get update && sudo apt-get install -y \
    build-essential \
    cmake \
    libcurl4-openssl-dev \
    libssl-dev \
    libxml2-dev \
    zlib1g-dev \
    libbz2-dev \
    liblzma-dev \
    libdeflate-dev \
    openjdk-17-jre-headless \
    graphviz
```

### macOS (Homebrew)
```bash
brew install \
    cmake \
    openssl \
    libxml2 \
    libdeflate \
    openjdk@17 \
    graphviz
```
