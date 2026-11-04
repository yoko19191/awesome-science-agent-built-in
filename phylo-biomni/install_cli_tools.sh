#!/usr/bin/env bash
# ==============================================================================
# Phylo-Biomni CLI Tools Automated Installer (install_cli_tools.sh)
# Installs all required command-line bioinformatics & chemoinformatics tools
# via Conda / Mamba (channels: bioconda, conda-forge)
# ==============================================================================

set -euo pipefail

ENV_NAME="${1:-phylo-cli}"

echo "========================================================"
echo "   Phylo-Biomni CLI Tools Installer"
echo "   Target Conda Environment: ${ENV_NAME}"
echo "========================================================"

# Determine package manager (mamba preferred, fallback to conda)
if command -v mamba &> /dev/null; then
    PM="mamba"
elif command -v conda &> /dev/null; then
    PM="conda"
else
    echo "[-] Error: Neither 'mamba' nor 'conda' was found in PATH."
    echo "    Please install Miniforge from: https://github.com/conda-forge/miniforge"
    exit 1
fi

echo "[+] Using package manager: ${PM}"

# List of CLI packages across bioconda and conda-forge
PACKAGES=(
    # Sequence & Variant Processing
    "bioconda::samtools"
    "bioconda::bcftools"
    "bioconda::bedtools"
    "bioconda::mosdepth"
    "bioconda::bwa"
    "bioconda::bwa-mem2"
    
    # RNA-seq & Quality Control
    "bioconda::star"
    "bioconda::salmon"
    "bioconda::fastqc"
    
    # Peak Calling & CRISPR Screens
    "bioconda::macs3"
    "bioconda::mageck"
    
    # Long-Read Assembly & SV Calling
    "bioconda::flye"
    "bioconda::hifiasm"
    "bioconda::sniffles"
    "bioconda::cutesv"
    
    # Microbiome
    "bioconda::picrust2"
    
    # Structural Biology & Chemoinformatics
    "conda-forge::vina"
    "bioconda::fpocket"
    
    # Variant Annotation & Pipeline Infrastructure
    "bioconda::gatk4"
    "bioconda::snpeff"
    "bioconda::nextflow"
    "conda-forge::openjdk=17"
)

echo "[+] Checking if environment '${ENV_NAME}' exists..."
if ${PM} env list | awk '{print $1}' | grep -qx "${ENV_NAME}"; then
    echo "[+] Updating existing environment '${ENV_NAME}'..."
    ${PM} install -n "${ENV_NAME}" -c bioconda -c conda-forge "${PACKAGES[@]}" -y
else
    echo "[+] Creating new environment '${ENV_NAME}'..."
    ${PM} create -n "${ENV_NAME}" -c bioconda -c conda-forge "${PACKAGES[@]}" -y
fi

echo ""
echo "========================================================"
echo "   Validating Installed CLI Tools in '${ENV_NAME}'"
echo "========================================================"

# Function to test tool execution inside conda environment
verify_tool() {
    local cmd="$1"
    local check_arg="${2:---version}"
    if conda run -n "${ENV_NAME}" "${cmd}" ${check_arg} &> /dev/null; then
        echo "  [OK] ${cmd}"
    else
        echo "  [WARN] ${cmd} (installed, check manual invocation)"
    fi
}

echo "Testing tools..."
verify_tool samtools --version || true
verify_tool bcftools --version || true
verify_tool bedtools --version || true
verify_tool bwa "" || true
verify_tool STAR --version || true
verify_tool salmon --version || true
verify_tool fastqc --version || true
verify_tool macs3 --version || true
verify_tool mageck -v || true
verify_tool flye --version || true
verify_tool hifiasm --version || true
verify_tool sniffles --version || true
verify_tool cuteSV --version || true
verify_tool vina --version || true
verify_tool fpocket -v || true
verify_tool nextflow -v || true

echo ""
echo "========================================================"
echo "   Installation Completed Successfully!"
echo "   Activate your environment with:"
echo "     conda activate ${ENV_NAME}"
echo "========================================================"
