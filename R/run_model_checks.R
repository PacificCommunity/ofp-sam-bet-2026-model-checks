env <- function(name, default = "") {
  value <- Sys.getenv(name, unset = "")
  if (nzchar(value)) value else default
}

input_dir <- env("INPUT_DIR", "inputs")
output_dir <- env("OUTPUT_DIR", "")
checks <- trimws(strsplit(tolower(env("MODEL_CHECKS", "jitter")), "[,[:space:]]+", perl = TRUE)[[1L]])
checks <- checks[nzchar(checks)]
unsupported <- setdiff(checks, c("jitter", "retrospective"))
if (length(unsupported)) {
  stop(
    "Unsupported model check(s): ", paste(unsupported, collapse = ", "),
    ". This version implements jitter and retrospective reports.",
    call. = FALSE
  )
}

provenance_json <- env("KFLOW_JOB_PROVENANCE", "")
provenance <- if (nzchar(provenance_json)) {
  jsonlite::fromJSON(provenance_json, simplifyDataFrame = TRUE)
} else {
  NULL
}

check <- checks[[1L]]
if (!nzchar(output_dir)) output_dir <- if (identical(check, "retrospective")) "retrospective" else "jitter"
grad_reference <- suppressWarnings(as.numeric(env("MODEL_CHECK_GRAD_REFERENCE", env("JITTER_GRAD_REFERENCE", "0.001"))))
if (!is.finite(grad_reference) || grad_reference <= 0) grad_reference <- 0.001
rel_diff_threshold <- suppressWarnings(as.numeric(env("JITTER_REL_DIFF_THRESHOLD", "10")))
if (!is.finite(rel_diff_threshold) || rel_diff_threshold <= 0) rel_diff_threshold <- 10
dpi <- suppressWarnings(as.integer(env("MODEL_CHECK_REPORT_DPI", env("JITTER_REPORT_DPI", "300"))))
if (!is.finite(dpi) || dpi < 72L) dpi <- 300L

if (is.null(provenance) || !nrow(provenance)) {
  stop("KFLOW_JOB_PROVENANCE must contain at least one model/check pair.", call. = FALSE)
}

if (identical(check, "jitter")) {
  result <- mfclshiny::build_jitter_report(
    model_dir = input_dir,
    output_dir = output_dir,
    title = env("MODEL_CHECK_TITLE", "BET 2026 Model Checks - Jitter"),
    provenance = provenance,
    grad_reference = grad_reference,
    rel_diff_threshold = rel_diff_threshold,
    formats = c("png", "pdf"),
    dpi = dpi,
    render_html = TRUE
  )
  message("Jitter report: ", result$html)
  message("Models: ", length(unique(result$data$scenario)))
  message("Jitter seeds: ", nrow(result$data))
} else {
  result <- mfclshiny::build_retrospective_report(
    model_dir = input_dir,
    output_dir = output_dir,
    title = env("MODEL_CHECK_TITLE", "BET 2026 Model Checks - Retrospective"),
    provenance = provenance,
    grad_reference = grad_reference,
    formats = c("png", "pdf"),
    dpi = dpi,
    render_html = TRUE
  )
  message("Retrospective report: ", result$html)
  message("Models: ", length(unique(result$data$runs$scenario)))
  message("Retrospective peels: ", nrow(result$data$runs))
}
