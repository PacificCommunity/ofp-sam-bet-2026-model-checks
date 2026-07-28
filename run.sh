#!/usr/bin/env bash
set -euo pipefail

ROOT="$(pwd)"
INPUT_DIR="${INPUT_DIR:-inputs}"
MODEL_CHECKS="${MODEL_CHECKS:-jitter}"
MODEL_CHECK_OUTPUT_DIR="${MODEL_CHECK_OUTPUT_DIR:-}"
if [[ -z "${MODEL_CHECK_OUTPUT_DIR}" ]]; then
  case "${MODEL_CHECKS}" in
    retrospective) MODEL_CHECK_OUTPUT_DIR="retrospective" ;;
    selftest) MODEL_CHECK_OUTPUT_DIR="selftest" ;;
    *) MODEL_CHECK_OUTPUT_DIR="${JITTER_OUTPUT_DIR:-jitter}" ;;
  esac
fi
R_LIBRARY="${R_LIBS_USER:-${ROOT}/.R-library}"

mkdir -p "${INPUT_DIR}" "${MODEL_CHECK_OUTPUT_DIR}" "${R_LIBRARY}"
export R_LIBS_USER="${R_LIBRARY}"

first_runtime_token() {
  local name
  for name in GITHUB_PAT GIT_PAT GH_TOKEN GITHUB_TOKEN KFLOW_GITHUB_TOKEN KFLOW_PERSONAL_TOKEN; do
    if [[ -n "${!name:-}" ]]; then
      printf '%s' "${!name}"
      return 0
    fi
  done
  return 1
}

RUNTIME_GIT_TOKEN="$(first_runtime_token || true)"
RUNTIME_GIT_ASKPASS=""
if [[ -n "${RUNTIME_GIT_TOKEN}" ]]; then
  RUNTIME_GIT_ASKPASS="$(mktemp)"
  cat > "${RUNTIME_GIT_ASKPASS}" <<'ASKPASS'
#!/bin/sh
case "$1" in
  *Username*) printf '%s\n' x-access-token ;;
  *) printf '%s\n' "$KFLOW_GIT_ASKPASS_TOKEN" ;;
esac
ASKPASS
  chmod 700 "${RUNTIME_GIT_ASKPASS}"
  trap 'rm -f "${RUNTIME_GIT_ASKPASS}"' EXIT
fi

runtime_git() {
  if [[ -n "${RUNTIME_GIT_TOKEN}" ]]; then
    GIT_ASKPASS="${RUNTIME_GIT_ASKPASS}" \
      GIT_TERMINAL_PROMPT=0 \
      KFLOW_GIT_ASKPASS_TOKEN="${RUNTIME_GIT_TOKEN}" \
      git "$@"
  else
    GIT_TERMINAL_PROMPT=0 git "$@"
  fi
}

install_runtime_repo() {
  local package="$1"
  local repo="$2"
  local ref="$3"
  local source_dir="${ROOT}/.runtime-sources/${package}"

  rm -rf "${source_dir}"
  mkdir -p "$(dirname "${source_dir}")"
  echo "[model-checks] installing ${package} from ${repo}@${ref}"
  runtime_git clone --quiet --depth 50 "https://github.com/${repo}.git" "${source_dir}"
  if ! runtime_git -C "${source_dir}" checkout --quiet "${ref}"; then
    runtime_git -C "${source_dir}" fetch --quiet --depth 1 origin "${ref}"
    runtime_git -C "${source_dir}" checkout --quiet FETCH_HEAD
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
  "${MFCLSHINY_GITHUB_REF:-a81dc8cd70fe4c6f4f4c354da1c4f56fc65a81f3}"

Rscript - <<'RS'
lib <- Sys.getenv("R_LIBS_USER")
dir.create(lib, recursive = TRUE, showWarnings = FALSE)
.libPaths(unique(c(lib, .libPaths())))

required_ref <- Sys.getenv("MFCLSHINY_GITHUB_REF", "main")
source_dir <- Sys.getenv("MFCLSHINY_SOURCE_DIR", "")
has_api <- requireNamespace("mfclshiny", quietly = TRUE) &&
  all(vapply(c("build_jitter_report", "build_retrospective_report", "build_selftest_report"), exists, logical(1), envir = asNamespace("mfclshiny"), inherits = FALSE))

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
    all(vapply(c("build_jitter_report", "build_retrospective_report", "build_selftest_report"), exists, logical(1), envir = asNamespace("mfclshiny"), inherits = FALSE))
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
    all(vapply(c("build_jitter_report", "build_retrospective_report", "build_selftest_report"), exists, logical(1), envir = asNamespace("mfclshiny"), inherits = FALSE))
}

required_api <- switch(
  Sys.getenv("MODEL_CHECKS", "jitter"),
  retrospective = "build_retrospective_report",
  selftest = "build_selftest_report",
  "build_jitter_report"
)
if (!exists(required_api, envir = asNamespace("mfclshiny"), inherits = FALSE)) {
  stop("Installed mfclshiny does not provide ", required_api, "().", call. = FALSE)
}
RS

INPUT_DIR="${INPUT_DIR}" OUTPUT_DIR="${MODEL_CHECK_OUTPUT_DIR}" MODEL_CHECKS="${MODEL_CHECKS}" Rscript R/run_model_checks.R
