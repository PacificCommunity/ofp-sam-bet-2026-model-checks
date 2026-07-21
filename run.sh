#!/usr/bin/env bash
set -euo pipefail

ROOT="$(pwd)"
INPUT_DIR="${INPUT_DIR:-inputs}"
OUTPUT_DIR="${OUTPUT_DIR:-jitter}"
R_LIBRARY="${R_LIBS_USER:-${ROOT}/.R-library}"

mkdir -p "${INPUT_DIR}" "${OUTPUT_DIR}" "${R_LIBRARY}"
export R_LIBS_USER="${R_LIBRARY}"

Rscript - <<'RS'
lib <- Sys.getenv("R_LIBS_USER")
dir.create(lib, recursive = TRUE, showWarnings = FALSE)
.libPaths(unique(c(lib, .libPaths())))

required_ref <- Sys.getenv("MFCLSHINY_GITHUB_REF", "main")
source_dir <- Sys.getenv("MFCLSHINY_SOURCE_DIR", "")
has_api <- requireNamespace("mfclshiny", quietly = TRUE) &&
  exists("build_jitter_report", envir = asNamespace("mfclshiny"), inherits = FALSE)

if (nzchar(source_dir) && dir.exists(source_dir)) {
  output <- system2(
    file.path(R.home("bin"), "R"),
    c("CMD", "INSTALL", "-l", lib, normalizePath(source_dir)),
    stdout = TRUE,
    stderr = TRUE
  )
  status <- attr(output, "status")
  if (!is.null(status) && status != 0L) stop(paste(output, collapse = "\n"), call. = FALSE)
  has_api <- TRUE
}

if (!has_api) {
  if (!requireNamespace("remotes", quietly = TRUE)) {
    install.packages("remotes", lib = lib, repos = "https://cloud.r-project.org")
  }
  token <- ""
  for (name in c("GITHUB_PAT", "GITHUB_TOKEN", "GH_TOKEN", "KFLOW_GITHUB_TOKEN")) {
    value <- Sys.getenv(name, "")
    if (nzchar(value)) {
      token <- value
      break
    }
  }
  if (!nzchar(token)) {
    stop("mfclshiny report API is unavailable and no GitHub token was forwarded.", call. = FALSE)
  }
  Sys.setenv(GITHUB_PAT = token)
  remotes::install_github(
    paste0("PacificCommunity/mfclshiny@", required_ref),
    lib = lib,
    upgrade = "never",
    dependencies = NA,
    quiet = TRUE
  )
}

if (!exists("build_jitter_report", envir = asNamespace("mfclshiny"), inherits = FALSE)) {
  stop("Installed mfclshiny does not provide build_jitter_report().", call. = FALSE)
}
RS

INPUT_DIR="${INPUT_DIR}" OUTPUT_DIR="${OUTPUT_DIR}" Rscript R/run_model_checks.R
