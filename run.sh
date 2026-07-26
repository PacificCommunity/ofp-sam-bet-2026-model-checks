#!/usr/bin/env bash
set -euo pipefail

ROOT="$(pwd)"
INPUT_DIR="${INPUT_DIR:-inputs}"
MODEL_CHECKS="${MODEL_CHECKS:-jitter}"
MODEL_CHECK_OUTPUT_DIR="${MODEL_CHECK_OUTPUT_DIR:-}"
if [[ -z "${MODEL_CHECK_OUTPUT_DIR}" ]]; then
  if [[ "${MODEL_CHECKS}" == "retrospective" ]]; then MODEL_CHECK_OUTPUT_DIR="retrospective"; else MODEL_CHECK_OUTPUT_DIR="${JITTER_OUTPUT_DIR:-jitter}"; fi
fi
R_LIBRARY="${R_LIBS_USER:-${ROOT}/.R-library}"

mkdir -p "${INPUT_DIR}" "${MODEL_CHECK_OUTPUT_DIR}" "${R_LIBRARY}"
export R_LIBS_USER="${R_LIBRARY}"

install_runtime_repo() {
  local package="$1"
  local repo="$2"
  local ref="$3"
  local source_dir="${ROOT}/.runtime-sources/${package}"

  rm -rf "${source_dir}"
  mkdir -p "$(dirname "${source_dir}")"
  echo "[model-checks] installing ${package} from ${repo}@${ref}"
  GIT_TERMINAL_PROMPT=0 git clone --quiet --depth 50 "https://github.com/${repo}.git" "${source_dir}"
  if ! git -C "${source_dir}" checkout --quiet "${ref}"; then
    GIT_TERMINAL_PROMPT=0 git -C "${source_dir}" fetch --quiet --depth 1 origin "${ref}"
    git -C "${source_dir}" checkout --quiet FETCH_HEAD
  fi
  R CMD INSTALL -l "${R_LIBRARY}" "${source_dir}"
}

# R CMD INSTALL does not try to rediscover non-CRAN dependencies. Installing
# the three repositories explicitly keeps the same tested dependency order as
# the diagnostic merge workflow.
install_runtime_repo \
  FLR4MFCL \
  PacificCommunity/ofp-sam-flr4mfcl \
  "${FLR4MFCL_GITHUB_REF:-3faaf84a4867175bfea50d89e4d518c085e84739}"
install_runtime_repo \
  mfclkit \
  PacificCommunity/ofp-sam-mfclkit \
  "${MFCLKIT_GITHUB_REF:-9b949db539619be52a63b321bd138c937f868199}"
install_runtime_repo \
  mfclshiny \
  PacificCommunity/mfclshiny \
  "${MFCLSHINY_GITHUB_REF:-2a4781bf03b7cfc52acd7bb23c3a6ae53af22a15}"

Rscript - <<'RS'
lib <- Sys.getenv("R_LIBS_USER")
dir.create(lib, recursive = TRUE, showWarnings = FALSE)
.libPaths(unique(c(lib, .libPaths())))

required_ref <- Sys.getenv("MFCLSHINY_GITHUB_REF", "main")
source_dir <- Sys.getenv("MFCLSHINY_SOURCE_DIR", "")
has_api <- requireNamespace("mfclshiny", quietly = TRUE) &&
  all(vapply(c("build_jitter_report", "build_retrospective_report"), exists, logical(1), envir = asNamespace("mfclshiny"), inherits = FALSE))

if (nzchar(source_dir) && dir.exists(source_dir)) {
  if (isNamespaceLoaded("mfclshiny")) unloadNamespace("mfclshiny")
  output <- system2(
    file.path(R.home("bin"), "R"),
    c("CMD", "INSTALL", "-l", lib, normalizePath(source_dir)),
    stdout = TRUE,
    stderr = TRUE
  )
  status <- attr(output, "status")
  if (!is.null(status) && status != 0L) stop(paste(output, collapse = "\n"), call. = FALSE)
  has_api <- requireNamespace("mfclshiny", quietly = TRUE) &&
    all(vapply(c("build_jitter_report", "build_retrospective_report"), exists, logical(1), envir = asNamespace("mfclshiny"), inherits = FALSE))
}

if (!has_api) {
  if (isNamespaceLoaded("mfclshiny")) unloadNamespace("mfclshiny")
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
  has_api <- requireNamespace("mfclshiny", quietly = TRUE) &&
    all(vapply(c("build_jitter_report", "build_retrospective_report"), exists, logical(1), envir = asNamespace("mfclshiny"), inherits = FALSE))
}

required_api <- if (identical(Sys.getenv("MODEL_CHECKS", "jitter"), "retrospective")) "build_retrospective_report" else "build_jitter_report"
if (!exists(required_api, envir = asNamespace("mfclshiny"), inherits = FALSE)) {
  stop("Installed mfclshiny does not provide ", required_api, "().", call. = FALSE)
}
RS

INPUT_DIR="${INPUT_DIR}" OUTPUT_DIR="${MODEL_CHECK_OUTPUT_DIR}" MODEL_CHECKS="${MODEL_CHECKS}" Rscript R/run_model_checks.R
