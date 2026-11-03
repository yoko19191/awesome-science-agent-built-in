#!/usr/bin/env Rscript
# Fail unless every dependency required by the selected analysis branch is installed.
# This script never installs packages: dependency setup belongs before the single run_all event.

args <- commandArgs(trailingOnly = TRUE)
if (length(args) < 2 || !file.exists(args[[1]])) {
  stop("preflight requires the run config path and selected route")
}

if (!requireNamespace("jsonlite", quietly = TRUE)) {
  stop("missing bootstrap dependency before dependency preflight: jsonlite")
}

here <- dirname(sub("^--file=", "", grep("^--file=", commandArgs(FALSE), value = TRUE)[1]))
contract_command <- c(
  file.path(here, "dependency_contract.py"),
  "--config", args[[1]],
  "--selected-route", args[[2]]
)
contract_json <- system2("python3", shQuote(contract_command), stdout = TRUE, stderr = TRUE)
contract_status <- attr(contract_json, "status")
if (!is.null(contract_status) && contract_status != 0L) {
  stop("could not derive selected-branch dependency contract: ", paste(contract_json, collapse = "\n"))
}
contract <- jsonlite::fromJSON(paste(contract_json, collapse = "\n"), simplifyVector = FALSE)

required <- unlist(contract$required_r_packages, use.names = FALSE)
missing <- required[!vapply(required, requireNamespace, logical(1), quietly = TRUE)]
if (length(missing)) {
  stop(
    "missing dependencies for selected route ", contract$selected_route, ": ",
    paste(missing, collapse = ", ")
  )
}

inference <- contract$branches$replicate_aware_inference
if (!is.null(inference)) {
  cat(
    "Replicate-aware inference:",
    if (isTRUE(inference$selected)) "selected" else "skipped",
    "-", inference$reason, "\n"
  )
}
cat("SCATAC_DEPENDENCY_PREFLIGHT_OK route=", contract$selected_route, "\n", sep = "")
