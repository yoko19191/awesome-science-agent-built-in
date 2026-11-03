# Review-decision receipts shared by gating, resolution, and annotation.
# A receipt binds a human decision to the exact proposal and applied artifact.

REVIEW_RECEIPT_SCHEMA_VERSION <- 1L
REVIEW_HUMAN_ROLES <- c("user", "immunologist", "scientist", "reviewer")

review_sha256_file <- function(path) {
  if (!file.exists(path)) stop(sprintf("Review artifact does not exist: %s", path))
  if (requireNamespace("digest", quietly = TRUE))
    return(tolower(digest::digest(file = path, algo = "sha256", serialize = FALSE)))
  executable <- Sys.which("sha256sum")
  args <- path
  if (!nzchar(executable)) {
    executable <- Sys.which("shasum")
    args <- c("-a", "256", path)
  }
  if (!nzchar(executable)) stop("SHA-256 requires the R digest package, sha256sum, or shasum.")
  output <- system2(executable, args, stdout = TRUE, stderr = TRUE)
  status <- attr(output, "status")
  if (!is.null(status) && status != 0L) stop(paste(output, collapse = "\n"))
  hash <- sub("[[:space:]].*$", "", output[[1]])
  if (!grepl("^[0-9a-fA-F]{64}$", hash)) stop("Could not parse the SHA-256 command output.")
  tolower(hash)
}

review_write_proposal <- function(stage, proposal_path, outdir, context = list()) {
  if (!requireNamespace("jsonlite", quietly = TRUE))
    stop("The jsonlite package is required to write review proposal metadata.")
  record <- c(list(
    schema_version = REVIEW_RECEIPT_SCHEMA_VERSION,
    stage = stage,
    proposal_file = basename(proposal_path),
    proposal_sha256 = review_sha256_file(proposal_path)
  ), context)
  path <- file.path(outdir, sprintf("%s_review_proposal.json", stage))
  writeLines(jsonlite::toJSON(record, auto_unbox = TRUE, pretty = TRUE, null = "null"), path)
  record
}

review_read_receipt <- function(path) {
  if (is.na(path) || !nzchar(path) || !file.exists(path)) return(NULL)
  if (!requireNamespace("jsonlite", quietly = TRUE))
    stop("The jsonlite package is required to read --review-receipt.")
  tryCatch(jsonlite::fromJSON(path, simplifyVector = TRUE),
           error = function(error) stop(sprintf("Malformed review receipt: %s", error$message)))
}

review_validate_receipt <- function(path, stage, proposal_path = NULL, proposal_sha256 = NULL,
                                    decision_artifact = NULL, selected_value = NULL,
                                    uncertainty_ids = character(0),
                                    expected_decisions = c("apply", "approve", "accept", "select")) {
  receipt <- review_read_receipt(path)
  empty <- list(valid = FALSE, receipt = receipt, reason = "No review receipt was supplied.")
  if (is.null(receipt)) return(empty)
  required <- c("schema_version", "stage", "proposal_sha256", "reviewer_role",
                "reviewer", "source_task_id", "source_message_id", "decision",
                "decision_reason", "created_at")
  missing <- required[!vapply(required, function(field) {
    value <- receipt[[field]]
    !is.null(value) && length(value) == 1L && !is.na(value) && nzchar(trimws(as.character(value)))
  }, logical(1))]
  if (length(missing))
    return(list(valid = FALSE, receipt = receipt,
                reason = sprintf("Review receipt is missing: %s.", paste(missing, collapse = ", "))))
  if (!grepl("^msg_[0-9a-f]{16}$", as.character(receipt$source_message_id)) ||
      !grepl("^tsk_[A-Za-z0-9]+$", as.character(receipt$source_task_id)))
    return(list(valid = FALSE, receipt = receipt,
                reason = "Review receipt requires actual source task and message IDs, not placeholders."))
  decision <- tolower(trimws(as.character(receipt$decision)))
  if (!decision %in% expected_decisions)
    return(list(valid = FALSE, receipt = receipt,
                reason = "Review receipt decision does not authorize this checkpoint action."))
  if (!identical(as.integer(receipt$schema_version), REVIEW_RECEIPT_SCHEMA_VERSION))
    return(list(valid = FALSE, receipt = receipt, reason = "Unsupported review receipt schema version."))
  if (!identical(as.character(receipt$stage), stage))
    return(list(valid = FALSE, receipt = receipt, reason = "Review receipt stage does not match this checkpoint."))
  if (!tolower(as.character(receipt$reviewer_role)) %in% REVIEW_HUMAN_ROLES)
    return(list(valid = FALSE, receipt = receipt, reason = "Review receipt does not identify a human reviewer role."))
  if (is.null(proposal_path) || !file.exists(proposal_path))
    return(list(valid = FALSE, receipt = receipt,
                reason = "The persisted review proposal artifact is missing."))
  observed_proposal <- review_sha256_file(proposal_path)
  if (!is.null(proposal_sha256) && nzchar(proposal_sha256) &&
      !identical(tolower(proposal_sha256), observed_proposal))
    return(list(valid = FALSE, receipt = receipt,
                reason = "The proposal record does not match its persisted artifact."))
  if (!identical(tolower(as.character(receipt$proposal_sha256)), observed_proposal))
    return(list(valid = FALSE, receipt = receipt, reason = "Review receipt proposal fingerprint does not match."))
  if (is.null(decision_artifact) || !file.exists(decision_artifact))
    return(list(valid = FALSE, receipt = receipt,
                reason = "The persisted review decision artifact is missing."))
  expected_decision <- receipt$decision_artifact_sha256
  if (is.null(expected_decision) || !nzchar(as.character(expected_decision)))
    return(list(valid = FALSE, receipt = receipt, reason = "Review receipt has no decision-artifact fingerprint."))
  observed_decision <- review_sha256_file(decision_artifact)
  if (!identical(tolower(as.character(expected_decision)), observed_decision))
    return(list(valid = FALSE, receipt = receipt, reason = "Review receipt decision-artifact fingerprint does not match."))
  selection_decision <- tryCatch(read.csv(decision_artifact, stringsAsFactors = FALSE,
                                          check.names = FALSE), error = function(error) NULL)
  if (is.data.frame(selection_decision) && nrow(selection_decision) == 1L &&
      all(c("action", "selected_value", "decision_reason") %in% names(selection_decision))) {
    artifact_action <- tolower(trimws(as.character(selection_decision$action[[1]])))
    if ((artifact_action == "reject" && decision != "reject") ||
        (artifact_action == "select" && !decision %in% c("apply", "approve", "accept", "select")) ||
        !artifact_action %in% c("reject", "select"))
      return(list(valid = FALSE, receipt = receipt,
                  reason = "Review receipt does not authorize the persisted selection action."))
    if (!identical(trimws(as.character(receipt$decision_reason)),
                   trimws(as.character(selection_decision$decision_reason[[1]]))))
      return(list(valid = FALSE, receipt = receipt,
                  reason = "Review receipt reason does not match the persisted selection decision."))
    if (!is.null(selected_value) &&
        !identical(as.character(selected_value), as.character(selection_decision$selected_value[[1]])))
      return(list(valid = FALSE, receipt = receipt,
                  reason = "Persisted selection decision does not match the selected value."))
  }
  if (!is.null(selected_value)) {
    recorded <- receipt$selected_value
    if (is.null(recorded) || !identical(as.character(recorded), as.character(selected_value)))
      return(list(valid = FALSE, receipt = receipt, reason = "Review receipt selected value does not match."))
  }
  if (length(uncertainty_ids)) {
    acknowledged <- unique(as.character(receipt$acknowledged_uncertainties))
    unresolved <- setdiff(uncertainty_ids, acknowledged)
    if (length(unresolved))
      return(list(valid = FALSE, receipt = receipt,
                  reason = sprintf("Review receipt did not acknowledge QC uncertainty: %s.",
                                   paste(unresolved, collapse = ", "))))
  }
  list(valid = TRUE, receipt = receipt, reason = "Receipt matches the persisted proposal and decision artifacts.",
       receipt_path = normalizePath(path),
       proposal_path = normalizePath(proposal_path), proposal_sha256 = observed_proposal,
       decision_artifact = normalizePath(decision_artifact), decision_artifact_sha256 = observed_decision)
}

review_read_proposal <- function(path, stage) {
  if (!file.exists(path)) stop(sprintf("The %s proposal record is missing: %s", stage, path))
  if (!requireNamespace("jsonlite", quietly = TRUE)) stop("The jsonlite package is required to read proposal metadata.")
  proposal <- jsonlite::fromJSON(path, simplifyVector = TRUE)
  if (!identical(as.character(proposal$stage), stage) ||
      is.null(proposal$proposal_file) || !nzchar(as.character(proposal$proposal_file)) ||
      is.null(proposal$proposal_sha256) || !grepl("^[0-9a-fA-F]{64}$", proposal$proposal_sha256))
    stop(sprintf("The %s proposal record is malformed or belongs to a different checkpoint.", stage))
  proposal_path <- file.path(dirname(path), basename(as.character(proposal$proposal_file)))
  if (!file.exists(proposal_path))
    stop(sprintf("The %s proposal artifact is missing: %s", stage, proposal_path))
  if (!identical(tolower(as.character(proposal$proposal_sha256)), review_sha256_file(proposal_path)))
    stop(sprintf("The %s proposal record does not match its persisted artifact.", stage))
  proposal$proposal_path <- normalizePath(proposal_path)
  proposal
}

# QC uncertainty IDs persisted with the proposal: what the reviewer was shown. A receipt must
# acknowledge them even when the apply pass no longer recomputes them (a finite edited cutoff
# removes REVIEW_unimodal from the recomputed table; the warning the reviewer approved against
# must still be acknowledged).
review_proposal_uncertainty_ids <- function(proposal) {
  ids <- if (is.list(proposal)) proposal$uncertainty_ids else NULL
  if (is.null(ids)) return(character(0))
  ids <- as.character(unlist(ids, use.names = FALSE))
  unique(ids[!is.na(ids) & nzchar(ids)])
}

review_write_decision_template <- function(stage, outdir) {
  path <- file.path(outdir, sprintf("%s_decision.csv", stage))
  if (!file.exists(path))
    write.csv(data.frame(action = "", selected_value = "", decision_reason = "",
                         stringsAsFactors = FALSE), path, row.names = FALSE)
  path
}

review_read_selection_decision <- function(path, action, selected_value, decision_reason) {
  if (!file.exists(path)) stop(sprintf("Review decision artifact is missing: %s", path))
  decision <- tryCatch(read.csv(path, stringsAsFactors = FALSE, check.names = FALSE),
                       error = function(error) stop(sprintf("Malformed review decision artifact: %s", error$message)))
  required <- c("action", "selected_value", "decision_reason")
  if (nrow(decision) != 1L || !all(required %in% names(decision)))
    stop("Review decision artifact must contain exactly one action, selected_value, and decision_reason row.")
  observed <- trimws(vapply(required, function(column)
    as.character(decision[[column]][[1]]), character(1)))
  expected <- c(trimws(as.character(action)), trimws(as.character(selected_value)),
                trimws(as.character(decision_reason)))
  if (!identical(unname(observed), unname(expected)))
    stop("Review decision artifact does not match the requested action, selected value, and reason.")
  normalizePath(path)
}

review_receipt_state <- function(validation, complete_decision, incomplete_state) {
  if (!isTRUE(complete_decision)) return(incomplete_state)
  if (isTRUE(validation$valid)) return("PASS_reviewed")
  "REVIEW_applied_unverified"
}

review_write_receipt_state <- function(stage, validation, outdir) {
  receipt <- validation$receipt
  receipt_path <- file.path(outdir, sprintf("%s_review_receipt.json", stage))
  if (isTRUE(validation$valid)) {
    if (!identical(normalizePath(validation$receipt_path), normalizePath(receipt_path, mustWork = FALSE))) {
      if (!file.copy(validation$receipt_path, receipt_path, overwrite = TRUE))
        stop("Could not preserve the original review receipt.")
    }
  }
  row <- data.frame(
    receipt_file = if (isTRUE(validation$valid)) basename(receipt_path) else "",
    receipt_sha256 = if (isTRUE(validation$valid)) review_sha256_file(receipt_path) else "",
    stage = stage,
    state = if (isTRUE(validation$valid)) "PASS_verified" else "REVIEW_unverified",
    proposal_sha256 = if (is.null(receipt$proposal_sha256)) "" else as.character(receipt$proposal_sha256),
    proposal_file = if (is.null(validation$proposal_path)) "" else basename(validation$proposal_path),
    decision_artifact = if (is.null(validation$decision_artifact)) "" else basename(validation$decision_artifact),
    decision_artifact_sha256 = if (is.null(receipt$decision_artifact_sha256)) "" else
      as.character(receipt$decision_artifact_sha256),
    reviewer = if (is.null(receipt$reviewer)) "" else as.character(receipt$reviewer),
    reviewer_role = if (is.null(receipt$reviewer_role)) "" else as.character(receipt$reviewer_role),
    source_task_id = if (is.null(receipt$source_task_id)) "" else as.character(receipt$source_task_id),
    source_message_id = if (is.null(receipt$source_message_id)) "" else as.character(receipt$source_message_id),
    decision = if (is.null(receipt$decision)) "" else as.character(receipt$decision),
    decision_reason = if (is.null(receipt$decision_reason)) "" else as.character(receipt$decision_reason),
    created_at = if (is.null(receipt$created_at)) "" else as.character(receipt$created_at),
    validation_reason = validation$reason,
    source = "receipt",
    stringsAsFactors = FALSE
  )
  write.csv(row, file.path(outdir, sprintf("%s_review_receipt_state.csv", stage)), row.names = FALSE)
  row
}

# Unattended application (`--gate-review auto` with an EMPTY QC uncertainty table). No human receipt
# exists, so the row claims none: it records the persisted proposal it applied, `source = "auto"`,
# and the check-derived reason. The state stays REVIEW_auto_applied; only a receipt can record a pass.
review_write_auto_receipt_state <- function(stage, proposal_record, outdir,
                                            reason = "no QC uncertainties") {
  if (!is.list(proposal_record) || is.null(proposal_record$proposal_sha256))
    stop("The unattended receipt state requires the persisted proposal record.")
  row <- data.frame(
    receipt_file = "", receipt_sha256 = "", stage = stage, state = "REVIEW_auto_applied",
    proposal_sha256 = tolower(as.character(proposal_record$proposal_sha256)),
    proposal_file = basename(as.character(proposal_record$proposal_file)),
    decision_artifact = "", decision_artifact_sha256 = "", reviewer = "", reviewer_role = "",
    source_task_id = "", source_message_id = "", decision = "", decision_reason = "", created_at = "",
    validation_reason = reason, source = "auto", stringsAsFactors = FALSE)
  write.csv(row, file.path(outdir, sprintf("%s_review_receipt_state.csv", stage)), row.names = FALSE)
  row
}

# The one apply-path contract for every gating engine: read the persisted proposal record, bind it to
# the current scientific inputs, validate the receipt against the uncertainty IDs the reviewer was
# shown (persisted) plus whatever the apply pass recomputed, and record the receipt state. Callers
# decide what to do with an invalid result; the state row is written either way so an aborted apply
# leaves REVIEW_unverified rather than nothing.
review_validate_gating_apply <- function(receipt_path, proposal_record, thresholds_path,
                                         uncertainty_ids, outdir, stage = "gating",
                                         input_fingerprint = NULL) {
  if (is.character(proposal_record)) proposal_record <- review_read_proposal(proposal_record, stage)
  if (!is.list(proposal_record) || is.null(proposal_record$proposal_path))
    stop(sprintf("The %s proposal record is missing or was not read through review_read_proposal.", stage))
  if (!is.null(input_fingerprint))
    review_validate_proposal_input(proposal_record$proposal_path, input_fingerprint)
  ids <- union(review_proposal_uncertainty_ids(proposal_record),
               as.character(unlist(uncertainty_ids, use.names = FALSE)))
  ids <- ids[!is.na(ids) & nzchar(ids)]
  validation <- review_validate_receipt(
    receipt_path, stage, proposal_path = proposal_record$proposal_path,
    proposal_sha256 = proposal_record$proposal_sha256,
    decision_artifact = thresholds_path, uncertainty_ids = ids)
  validation$proposal_record <- proposal_record
  validation$uncertainty_ids <- ids
  if (!is.null(outdir)) review_write_receipt_state(stage, validation, outdir)
  validation
}

review_reject_output_as_input <- function(input, output) {
  if (!is.na(input) && identical(normalizePath(input, mustWork = FALSE),
                                  normalizePath(output, mustWork = FALSE)))
    stop("The reviewed decision must be a separate input file; an output cannot overwrite approved bytes.")
}

# The fingerprint is stored inside the CSV covered by the approval receipt hash.
# Sidecar metadata alone is not sufficient to bind a decision to its inputs.
review_fingerprint_object <- function(value) {
  path <- tempfile("review-input-")
  on.exit(unlink(path), add = TRUE)
  saveRDS(value, path, version = 2, compress = FALSE)
  review_sha256_file(path)
}

review_bind_proposal_input <- function(path, fingerprint) {
  proposal <- read.csv(path, stringsAsFactors = FALSE, check.names = FALSE)
  if (!nrow(proposal)) stop("Cannot bind an empty review proposal to scientific inputs.")
  proposal$review_input_sha256 <- fingerprint
  write.csv(proposal, path, row.names = FALSE)
  invisible(path)
}

review_validate_proposal_input <- function(path, fingerprint) {
  proposal <- read.csv(path, stringsAsFactors = FALSE, check.names = FALSE)
  bound <- proposal$review_input_sha256
  if (!nrow(proposal) || is.null(bound) || anyNA(bound) ||
      !all(as.character(bound) == fingerprint))
    stop("Review proposal does not match current scientific inputs and settings; generate and review a new proposal.")
  TRUE
}

# An automatic retry must not replace the evidence the reviewer was shown. With the current input
# fingerprint supplied, an unattended re-entry into the same output directory is allowed only when the
# persisted proposal was bound to exactly those inputs and settings (the rerun then reproduces the
# same proposal deterministically); different inputs, or a call without a fingerprint, still refuse.
review_preserve_auto_proposal <- function(mode, stage, outdir, input_fingerprint = NULL) {
  record <- file.path(outdir, sprintf("%s_review_proposal.json", stage))
  if (!identical(mode, "auto") || !file.exists(record)) return(invisible(TRUE))
  if (!is.null(input_fingerprint)) {
    persisted <- tryCatch(review_read_proposal(record, stage), error = function(error) NULL)
    bound <- if (is.null(persisted)) NULL else tryCatch(
      unique(as.character(read.csv(persisted$proposal_path, stringsAsFactors = FALSE,
                                   check.names = FALSE)$review_input_sha256)),
      error = function(error) NULL)
    if (length(bound) == 1L && !is.na(bound) && identical(bound, as.character(input_fingerprint)))
      return(invisible(TRUE))
  }
  stop("A review proposal already exists. Resume with reviewed apply, or use a new output directory for a separate automatic run.")
}

review_record_skipped_gating <- function(outdir) {
  review_write_receipt_state("gating", list(valid = FALSE, receipt = NULL,
                            reason = "QC gating was skipped; no reviewed gating decision exists."), outdir)
}
