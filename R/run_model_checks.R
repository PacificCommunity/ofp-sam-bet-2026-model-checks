env <- function(name, default = "") {
  value <- Sys.getenv(name, unset = "")
  if (nzchar(value)) value else default
}

input_dir <- env("INPUT_DIR", "inputs")
output_dir <- env("OUTPUT_DIR", "jitter")
checks <- trimws(strsplit(tolower(env("MODEL_CHECKS", "jitter")), "[,[:space:]]+", perl = TRUE)[[1L]])
checks <- checks[nzchar(checks)]
unsupported <- setdiff(checks, "jitter")
if (length(unsupported)) {
  stop(
    "Unsupported model check(s): ", paste(unsupported, collapse = ", "),
    ". The shared runner is ready for additional renderers, but this version implements jitter.",
    call. = FALSE
  )
}

provenance_json <- env("KFLOW_JOB_PROVENANCE", "")
provenance <- if (nzchar(provenance_json)) {
  jsonlite::fromJSON(provenance_json, simplifyDataFrame = TRUE)
} else {
  NULL
}

grad_reference <- suppressWarnings(as.numeric(env("JITTER_GRAD_REFERENCE", "0.001")))
if (!is.finite(grad_reference) || grad_reference <= 0) grad_reference <- 0.001
rel_diff_threshold <- suppressWarnings(as.numeric(env("JITTER_REL_DIFF_THRESHOLD", "10")))
if (!is.finite(rel_diff_threshold) || rel_diff_threshold <= 0) rel_diff_threshold <- 10
dpi <- suppressWarnings(as.integer(env("JITTER_REPORT_DPI", "300")))
if (!is.finite(dpi) || dpi < 72L) dpi <- 300L

result <- mfclshiny::build_jitter_report(
  model_dir = input_dir,
  output_dir = output_dir,
  title = env("MODEL_CHECK_TITLE", "BET 2026 jitter model checks"),
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
