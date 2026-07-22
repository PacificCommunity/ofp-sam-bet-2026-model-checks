# BET 2026 Model Checks

This public repository turns one or more fitted-model Kflow job numbers into
portable, report-ready model-check bundles. It resolves the relevant completed
dependent jobs and delegates plotting, tables, and self-contained HTML reports
to `mfclshiny`.

Implemented checks are Jitter and Retrospective. The same task structure can be
extended to profile, self-test, and Hessian reports.

## Submit

The default example uses model jobs 8146 and 8096. Preview the resolved jobs
before submission with `--dry-run`.

```bash
export KFLOW_API_TOKEN=...
python3 scripts/submit.py --check jitter --dry-run 8146 8096
python3 scripts/submit.py --check retrospective --dry-run 8146 8096
```

Submit the retrospective report to the public Kflow task named
`BET 2026 Model Checks - Retrospective`:

```bash
python3 scripts/submit.py --check retrospective 8146 8096
```

The resolver selects each model's latest completed terminal `retro-merge` job.
The number of retrospective peels is discovered from `retro/peel_*` outputs;
it is not configured or hard-coded in this repository.

## Retrospective output

Each run writes a compact `retrospective/` folder containing:

- `retrospective-report.html`: self-contained report with model tabs.
- `figures/retrospective-diagnostics-*.png`: publication PNG figures.
- matching vector PDF figures.
- `tables/retrospective-mohn-*.tex`: Mohn's rho tables.
- `tables/retrospective-peels-*.tex`: peel convergence tables.

The HTML uses the same publication PNG files stored in `figures/` and includes
copy controls for Word figures, captions, methods, results, tables, and LaTeX.

## Standalone use

The implementation lives in public `mfclshiny` and does not require Kflow.
Point the builder at one or more expanded model folders containing a colocated
`model_payload.rds` and `retro/peel_*` outputs.

```r
result <- mfclshiny::build_retrospective_report(
  model_dir = "/path/to/models-or-expanded-archives",
  output_dir = "retrospective"
)

plot <- mfclshiny::plot_retrospective_diagnostics(result$data)
```

Jitter remains available through `mfclshiny::build_jitter_report()` and
`python3 scripts/submit.py --check jitter ...`.
