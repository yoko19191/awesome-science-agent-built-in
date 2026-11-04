#!/usr/bin/env Rscript
# ==============================================================================
# Phylo-Biomni R Dependencies Installer (install_packages.R)
# Automated installation script for all R packages required across 92 skills
# Recommended R version: >= 4.3 (R >= 4.4 recommended)
# ==============================================================================

options(warn = 1)
options(repos = c(CRAN = "https://cloud.r-project.org"))

message("\n========================================================")
message("   Starting Phylo-Biomni R Package Installation")
message("========================================================\n")

# --- 1. Bootstrap Base Tooling ---
if (!requireNamespace("remotes", quietly = TRUE)) {
  install.packages("remotes")
}
if (!requireNamespace("BiocManager", quietly = TRUE)) {
  install.packages("BiocManager")
}

# --- 2. CRAN Packages ---
cran_packages <- c(
  # Data Manipulation & I/O
  "data.table", "dplyr", "tidyr", "readr", "reshape2", "jsonlite", "yaml", "openxlsx", "optparse",
  
  # Visualization & Graphics
  "ggplot2", "patchwork", "ggrepel", "svglite", "ggprism", "ggplate", "RColorBrewer",
  
  # Clustering & Matrix Operations
  "Matrix", "matrixStats", "cluster", "factoextra", "dendextend", "circlize", "mclust",
  
  # Statistical Modeling & Machine Learning
  "glmnet", "lme4", "pROC", "survival", "survminer", "meta", "metafor", "vegan",
  
  # Pharmacometrics & Clinical Trials
  "nlmixr2", "rxode2", "rpact", "designit",
  
  # Single-cell & Multi-omics Infrastructure
  "Seurat", "SeuratObject", "WGCNA", "harmony", "lisi", "susieR", "bigstatsr", "bigsnpr", "immunarch"
)

message("--> Installing CRAN packages (", length(cran_packages), " packages)...")
for (pkg in cran_packages) {
  if (!requireNamespace(pkg, quietly = TRUE)) {
    message("Installing CRAN: ", pkg)
    tryCatch({
      install.packages(pkg)
    }, error = function(e) {
      warning("Failed to install CRAN package: ", pkg, " -> ", e$message)
    })
  } else {
    message("Already installed: ", pkg)
  }
}

# --- 3. Bioconductor Packages ---
bioc_packages <- c(
  # Differential Expression & Bulk RNA-seq
  "DESeq2", "edgeR", "limma", "apeglm", "DEqMS", "tximport", "sva", "airway", "pasilla",
  
  # Functional Enrichment & Annotation
  "clusterProfiler", "org.Hs.eg.db", "AnnotationDbi", "GEOquery", "ExperimentHub", "Biobase",
  "fgsea", "msigdbr", "enrichplot", "ComplexHeatmap", "ConsensusClusterPlus", "impute",
  
  # Single-cell RNA-seq & Multi-modal Integration
  "SingleCellExperiment", "SummarizedExperiment", "GenomicRanges", "GenomicFeatures",
  "Signac", "SingleR", "celldex", "Azimuth", "miloR", "glmGamPoi", "zellkonverter", "muscat",
  
  # Flow & Mass Cytometry / Proteomics
  "flowCore", "flowWorkspace", "openCyto", "CATALYST", "diffcyt",
  
  # Multi-omics & Microbiome
  "MOFA2", "MOFAdata", "phyloseq", "ANCOMBC", "ALDEx2", "Maaslin2", "RTCGA.clinical",
  
  # Power Analysis & Benchmarks
  "RNASeqPower", "RnaSeqSampleSize", "ALL", "IMvigor210CoreBiologies"
)

message("\n--> Installing Bioconductor packages (", length(bioc_packages), " packages)...")
for (pkg in bioc_packages) {
  if (!requireNamespace(pkg, quietly = TRUE)) {
    message("Installing Bioconductor: ", pkg)
    tryCatch({
      BiocManager::install(pkg, update = FALSE, ask = FALSE)
    }, error = function(e) {
      warning("Failed to install Bioconductor package: ", pkg, " -> ", e$message)
    })
  } else {
    message("Already installed: ", pkg)
  }
}

# --- 4. GitHub / Remotes Packages ---
github_packages <- c(
  "jinworks/CellChat",
  "chris-mcginnis-ucsf/DoubletFinder",
  "MRCIEU/TwoSampleMR",
  "mrcieu/ieugwasr",
  "immunogenomics/presto",
  "satijalab/seurat-data",
  "constantAmateur/SoupX"
)

message("\n--> Installing GitHub packages...")
for (repo in github_packages) {
  pkg_name <- strsplit(repo, "/")[[1]][2]
  if (!requireNamespace(pkg_name, quietly = TRUE)) {
    message("Installing from GitHub: ", repo)
    tryCatch({
      remotes::install_github(repo, upgrade = "never")
    }, error = function(e) {
      warning("Failed to install GitHub package: ", repo, " -> ", e$message)
    })
  } else {
    message("Already installed: ", pkg_name)
  }
}

message("\n========================================================")
message("   Installation Process Completed!")
message("========================================================\n")
