# BET 2026 Model Checks

This repository turns one or more BET 2026 model-job numbers into portable,
report-ready model-check bundles. It resolves each model's dependent Kflow
jobs, attaches the requested check outputs, and delegates all plotting and
table generation to `mfclshiny`.

The first implemented check is Jitter. Retrospective, profile, self-test, and
Hessian modules can use the same job resolver and output contract later.

## Submit the example

The default example compares model jobs 8146 and 8096:

```bash
export KFLOW_API_TOKEN=...
python3 scripts/submit.py 8146 8096
```

Preview the resolved dependent jobs without submitting:

```bash
python3 scripts/submit.py --dry-run 8146 8096
```

The submission includes each base model archive for its reference objective
and maximum gradient, plus the terminal completed Jitter archive or archives
found below that model in the Kflow job graph.

## Output

Every run writes a `jitter/` folder containing:

- `jitter-report.html`: self-contained comparison report for sharing.
- `figures/jitter-diagnostics-combined.png`: combined report figure.
- `figures/jitter-diagnostics-*.png`: one report figure per model.
- matching vector PDF figures.
- `tables/jitter-model-summary.*`: model-level summary.
- `tables/jitter-seed-details.*`: OBJ, MGC, convergence, and provenance by seed.
- per-model tables in CSV, Word-friendly TSV, and LaTeX formats.
- `indices/`: machine-readable source, figure, and table indexes.

The HTML includes copy buttons for Word tables and LaTeX source. It embeds its
figures, so the HTML file can be sent by itself.

## Standalone use

The plotting and reporting implementation lives in `mfclshiny`, not this
Kflow wrapper. The same output can be generated from any expanded local model
folder:

The Kflow task pins `mfclshiny` commit
`80f1b62502f214d37e6619657484eb43dab2d4eb` so its figures remain reproducible.

```r
mfclshiny::build_jitter_report(
  model_dir = "/path/to/models-or-expanded-kflow-archives",
  output_dir = "jitter",
  title = "BET 2026 jitter model checks"
)
```

For an already-normalized data frame, the exact plot used by the Shiny Jitter
tab is also available directly:

```r
plot <- mfclshiny::plot_jitter_diagnostics(jitter_data)
```

## Local batch run

Place expanded Kflow archives below `inputs/`, then run:

```bash
MODEL_JOBS=8146,8096 bash run.sh
```

`KFLOW_JOB_PROVENANCE` is normally supplied by `scripts/submit.py`. Standalone
folders do not require it when model payloads and Jitter folders are colocated.
