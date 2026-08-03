#!/usr/bin/env python3
"""Resolve dependent jobs and submit a BET 2026 model-check report."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.request
from collections import deque
from typing import Any


REPO = "PacificCommunity/ofp-sam-bet-2026-model-checks"
TUNA_FLOW_IMAGE = (
    "ghcr.io/pacificcommunity/tuna-flow:v2.5@"
    "sha256:c87f1f6d9d4f62dc447844b58afe35f96af175bf933cb6cffbbbe39a59172360"
)
FLR4MFCL_REF = "3faaf84a4867175bfea50d89e4d518c085e84739"
MFCLKIT_REF = "cf786007b5261f84faac8f3d24f7084bd323119d"
MFCLSHINY_REF = "a8dffd78de61c99af8cf5b1f6995e861157dc96c"


def repo_runtime_packages(mfclshiny_ref: str = MFCLSHINY_REF) -> str:
    return (
        f"FLR4MFCL=PacificCommunity/ofp-sam-flr4mfcl@{FLR4MFCL_REF},"
        f"mfclkit=PacificCommunity/ofp-sam-mfclkit@{MFCLKIT_REF},"
        f"mfclshiny=PacificCommunity/mfclshiny@{mfclshiny_ref}"
    )


COMPLETED = {"completed", "success"}
COLLECTOR_WORDS = re.compile(r"merge|attach|collector|aggregate|combined", re.I)
CHECKS = {
    "jitter": {
        "title": "Jitter",
        "description": (
            "Report-only mfclshiny jitter diagnostics from existing model outputs, "
            "including annual stock-status trajectories, official WCPFC "
            "recent-period quantities, and regional depletion diagnostics."
        ),
        "dependency_word": "jitter",
        "dependency_pattern": re.compile(r"(^|[^a-z])jitter([^a-z]|$)", re.I),
        "output_dir": "jitter",
        "task": "ofp-sam-bet-2026-diagnostic-checks-jitter",
        "memory": "6GB",
    },
    "retrospective": {
        "title": "Retrospective",
        "dependency_word": "retro",
        "dependency_pattern": re.compile(r"(^|[^a-z])retro(spective)?([^a-z]|$)", re.I),
        "output_dir": "retrospective",
        "task": "ofp-sam-bet-2026-diagnostic-checks-retrospective",
        "memory": "4GB",
    },
    "selftest": {
        "title": "Self-Test",
        "dependency_word": "selftest",
        "dependency_pattern": re.compile(r"(^|[^a-z])self[-_ ]?test([^a-z]|$)", re.I),
        "output_dir": "selftest",
        "task": "ofp-sam-bet-2026-diagnostic-checks-selftest",
        "memory": "8GB",
    },
}


class KflowAPI:
    def __init__(self, base_url: str, token: str, github_token: str = "") -> None:
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.github_token = github_token.strip()

    def request(self, method: str, path: str, payload: dict | None = None) -> dict:
        body = None if payload is None else json.dumps(payload).encode("utf-8")
        headers = {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json",
        }
        if self.github_token:
            headers["X-GitHub-Token"] = self.github_token
        request = urllib.request.Request(
            f"{self.base_url}{path}", data=body, method=method, headers=headers
        )
        try:
            with urllib.request.urlopen(request, timeout=120) as response:
                return json.load(response)
        except urllib.error.HTTPError as error:
            detail = error.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"Kflow API {error.code}: {detail}") from error

    def job(self, job_ref: str | int) -> dict:
        response = self.request("GET", f"/api/job/{str(job_ref).lstrip('#')}")
        return response.get("job", response)


def job_number(job: dict) -> int | None:
    value = job.get("job_number", job.get("run_number"))
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def job_label(job: dict) -> str:
    env = job.get("env") if isinstance(job.get("env"), dict) else {}
    tags = job.get("tags") if isinstance(job.get("tags"), dict) else {}
    metadata = job.get("metadata") if isinstance(job.get("metadata"), dict) else {}
    for key in ("MODEL_LABEL", "PLOT_LABEL", "JOB_TITLE"):
        value = str(env.get(key) or "").strip()
        if value:
            return value
    for key in ("model_label", "plot_label", "model", "job_title"):
        value = str(tags.get(key) or metadata.get(key) or "").strip()
        if value:
            return value
    number = job_number(job)
    return f"Model job {number}" if number is not None else str(job.get("report_code") or "Model")


def metadata_input_refs(job: dict) -> list[str]:
    metadata = job.get("metadata") if isinstance(job.get("metadata"), dict) else {}
    values = metadata.get("input_jobs")
    if not isinstance(values, list):
        return []
    return list(
        dict.fromkeys(
            str(value).strip().lstrip("#")
            for value in values
            if isinstance(value, (str, int)) and str(value).strip()
        )
    )


def resolve_jitter_artifact_units(api: KflowAPI, check_jobs: list[dict]) -> list[dict]:
    """Find completed leaf jitter jobs carrying the recoverable seed payloads."""
    queue: deque[str] = deque()
    for check_job in check_jobs:
        queue.extend(metadata_input_refs(check_job))
    seen: set[str] = set()
    units: dict[str, dict] = {}
    while queue:
        ref = queue.popleft()
        if ref in seen:
            continue
        seen.add(ref)
        job = api.job(ref)
        nested = metadata_input_refs(job)
        queue.extend(nested)
        identity = " ".join(
            (str(job.get("report_code") or ""), str(job.get("batch_name") or ""))
        )
        status = str(job.get("status") or "").lower()
        if (
            status in COMPLETED
            and re.search(r"(^|[^a-z])jitter([^a-z]|$)", identity, re.I)
            and not COLLECTOR_WORDS.search(identity)
        ):
            key = str(job.get("id") or job_number(job) or ref)
            units[key] = job
    return sorted(
        units.values(),
        key=lambda item: (job_number(item) is None, job_number(item) or 0),
    )


def explicit_child_refs(job: dict) -> list[str]:
    """Return job references from Kflow's supported relationship shapes."""
    roots: list[tuple[str, Any]] = []
    relationship_keys = (
        "triggered_children",
        "child_jobs",
        "attached_work",
        "attached_work_latest_by_slot",
    )
    for container_name in ("details", "metadata"):
        container = job.get(container_name)
        if not isinstance(container, dict):
            continue
        for key in relationship_keys:
            value = container.get(key)
            if value:
                roots.append((key, value))

    refs: list[str] = []
    stack = list(roots)
    direct_ref_keys = (
        "output_job",
        "current_output_job",
        "job_number",
        "job_id",
        "job",
        "job_ref",
        "id",
    )
    ref_collection_keys = {
        "triggered_children",
        "child_jobs",
        "children",
        "jobs",
        "output_jobs_by_slot",
        "attached_work_latest_by_slot",
    }

    while stack:
        context, value = stack.pop()
        if isinstance(value, list):
            for item in value:
                if isinstance(item, (dict, list)):
                    stack.append((context, item))
                elif context in ref_collection_keys and str(item).strip():
                    refs.append(str(item).strip().lstrip("#"))
            continue
        if not isinstance(value, dict):
            if context in ref_collection_keys and str(value).strip():
                refs.append(str(value).strip().lstrip("#"))
            continue

        for key in direct_ref_keys:
            ref = value.get(key)
            if isinstance(ref, (str, int)) and str(ref).strip():
                refs.append(str(ref).strip().lstrip("#"))
                break
        for key, nested in value.items():
            if isinstance(nested, (dict, list)):
                nested_context = key if key in ref_collection_keys else context
                stack.append((nested_context, nested))
            elif context in ref_collection_keys and key not in direct_ref_keys:
                text = str(nested).strip().lstrip("#")
                if text.isdigit() or re.fullmatch(r"j[0-9a-f]{8,}", text):
                    refs.append(text)

    return list(dict.fromkeys(refs))


def job_text(job: dict) -> str:
    pieces: list[str] = [
        str(job.get("report_code") or ""),
        str(job.get("batch_name") or ""),
    ]
    for field in ("tags", "metadata", "env"):
        value = job.get(field)
        if isinstance(value, dict):
            pieces.extend(f"{key}={item}" for key, item in value.items())
    return " ".join(pieces)


def is_check_job(job: dict, check: str) -> bool:
    return bool(CHECKS[check]["dependency_pattern"].search(job_text(job)))


def descendants(api: KflowAPI, root: dict) -> tuple[dict[str, dict], dict[str, set[str]]]:
    jobs: dict[str, dict] = {}
    edges: dict[str, set[str]] = {}
    root_id = str(root.get("id") or "")
    queue: deque[tuple[str, str]] = deque((root_id, ref) for ref in explicit_child_refs(root))
    seen: set[str] = set()
    while queue:
        parent_id, ref = queue.popleft()
        if ref in seen:
            continue
        seen.add(ref)
        child = api.job(ref)
        child_id = str(child.get("id") or ref)
        jobs[child_id] = child
        edges.setdefault(parent_id, set()).add(child_id)
        for grandchild in explicit_child_refs(child):
            queue.append((child_id, grandchild))
    return jobs, edges


def resolve_check_jobs(api: KflowAPI, model: dict, check: str) -> list[dict]:
    config = CHECKS[check]
    metadata = model.get("metadata") if isinstance(model.get("metadata"), dict) else {}
    latest_by_slot = metadata.get("attached_work_latest_by_slot")
    latest_refs: list[str] = []
    if isinstance(latest_by_slot, dict):
        for slot, record in latest_by_slot.items():
            if config["dependency_word"] not in str(slot).lower() or not isinstance(record, dict):
                continue
            ref = record.get("output_job") or record.get("job_number") or record.get("job")
            if isinstance(ref, (str, int)) and str(ref).strip():
                latest_refs.append(str(ref).strip().lstrip("#"))

    if latest_refs:
        latest_jobs = [api.job(ref) for ref in dict.fromkeys(latest_refs)]
        completed_latest = [
            job
            for job in latest_jobs
            if is_check_job(job, check) and str(job.get("status") or "").lower() in COMPLETED
        ]
        if completed_latest:
            return sorted(completed_latest, key=lambda item: int(job_number(item)))

    children, edges = descendants(api, model)
    candidates = {
        job_id: job
        for job_id, job in children.items()
        if is_check_job(job, check) and str(job.get("status") or "").lower() in COMPLETED
    }
    if not candidates:
        number = job_number(model)
        observed = [
            f"#{job_number(job)}={job.get('status')}:{job.get('report_code')}"
            for job in children.values()
            if is_check_job(job, check)
        ]
        raise RuntimeError(
            f"Model job #{number} has no completed dependent {config['title']} job. "
            f"Observed: {', '.join(observed) or 'none'}"
        )

    terminal = {
        job_id: job
        for job_id, job in candidates.items()
        if not any(child_id in candidates for child_id in edges.get(job_id, set()))
    }
    collector = {
        job_id: job
        for job_id, job in terminal.items()
        if COLLECTOR_WORDS.search(job_text(job))
    }
    selected = collector or terminal or candidates
    return sorted(
        selected.values(),
        key=lambda job: (job_number(job) is None, job_number(job) or 0),
    )


def build_submission(api: KflowAPI, model_refs: list[str], args: argparse.Namespace) -> tuple[dict, list[dict]]:
    config = CHECKS[args.check]
    provenance: list[dict] = []
    input_refs: list[str] = []
    models: list[dict] = []
    label_overrides = list(args.model_label or [])
    if len(label_overrides) not in (0, 1, len(model_refs)):
        raise RuntimeError("Provide --model-label once for all models or once per model job.")
    for model_index, ref in enumerate(model_refs):
        model = api.job(ref)
        status = str(model.get("status") or "").lower()
        if status not in COMPLETED:
            raise RuntimeError(f"Model job #{job_number(model)} is {status or 'unknown'}, not completed.")
        check_jobs = resolve_check_jobs(api, model, args.check)
        model_id = str(model.get("id") or "")
        input_refs.append(model_id or str(job_number(model)))
        override = ""
        if len(label_overrides) == 1:
            override = label_overrides[0].strip()
        elif len(label_overrides) == len(model_refs):
            override = label_overrides[model_index].strip()
        model_record = {
            "model_job": str(job_number(model) or ref),
            "model_id": model_id,
            "model_label": override or job_label(model),
            "check_jobs": [job_number(job) for job in check_jobs],
        }
        models.append(model_record)
        for check_job in check_jobs:
            check_id = str(check_job.get("id") or "")
            input_refs.append(check_id or str(job_number(check_job)))
            record = {
                "model_job": model_record["model_job"],
                "model_id": model_id,
                "model_label": model_record["model_label"],
                "check_type": args.check,
                "check_job": str(job_number(check_job) or ""),
                "check_id": check_id,
            }
            prefix = "retro" if args.check == "retrospective" else args.check
            record[f"{prefix}_job"] = record["check_job"]
            record[f"{prefix}_id"] = check_id
            provenance.append(record)
        if args.check == "jitter":
            unit_jobs = resolve_jitter_artifact_units(api, check_jobs)
            if not unit_jobs and args.regional_jitter:
                raise RuntimeError(
                    f"Model job #{model_record['model_job']} has no completed recoverable jitter unit jobs."
                )
            model_record["jitter_artifact_jobs"] = [job_number(job) for job in unit_jobs]
            for unit_job in unit_jobs:
                unit_id = str(unit_job.get("id") or "")
                unit_number = str(job_number(unit_job) or "")
                input_refs.append(unit_id or unit_number)
                provenance.append(
                    {
                        "model_job": model_record["model_job"],
                        "model_id": model_id,
                        "model_label": model_record["model_label"],
                        "check_type": "jitter",
                        "check_job": unit_number,
                        "check_id": unit_id,
                        "jitter_job": unit_number,
                        "jitter_id": unit_id,
                        "artifact_recovery_source": True,
                        "regional_recovery_source": args.regional_jitter,
                    }
                )

    input_refs = list(dict.fromkeys(ref for ref in input_refs if ref))
    model_numbers = ",".join(record["model_job"] for record in models)
    job_name = f"bet-2026-{args.check}-models-{model_numbers.replace(',', '-')}"
    model_labels = " + ".join(
        f"{record['model_label'].removesuffix(' fitted model')} #{record['model_job']}"
        for record in models
    )
    report_label = f"{config['title']} | {model_labels}"
    report_title = args.title or f"BET 2026 Diagnostic Checks - {config['title']}"
    payload = {
        "repo": args.repo,
        "branch": args.branch,
        "docker_image": TUNA_FLOW_IMAGE,
        "batch_name": job_name,
        "remote_user": args.remote_user,
        "remote_host": args.remote_host,
        "remote_base_dir": args.remote_base_dir,
        "input_jobs": input_refs,
        "output_patterns": [f"{config['output_dir']}/**"],
        "cpus": 2,
        "memory": config["memory"],
        "disk": "12GB",
        "env": {
            "MODEL_JOBS": model_numbers,
            "MODEL_CHECKS": args.check,
            "MODEL_CHECK_OUTPUT_DIR": config["output_dir"],
            "MODEL_CHECK_TITLE": report_title,
            "KFLOW_JOB_PROVENANCE": json.dumps(provenance, separators=(",", ":")),
            "MODEL_CHECK_REPORT_DPI": str(args.dpi),
            "JITTER_REL_DIFF_THRESHOLD": str(args.rel_diff_threshold),
            "JITTER_REPORT_DPI": str(args.dpi),
            "JITTER_REGIONAL_DIAGNOSTICS": "true" if args.regional_jitter else "false",
            "JITTER_REGIONAL_QUANTITIES": ",".join(
                args.regional_quantity or ["depletion"]
            ),
            "JITTER_TRAJECTORY_STYLE": args.trajectory_style,
            "JITTER_REFERENCE_LABEL": args.reference_label,
            "JITTER_BASE_LABEL": args.base_label,
            "JITTER_REFERENCE_COLOUR": args.reference_colour,
            "JITTER_BASE_COLOUR": args.base_colour,
            "JITTER_SHOW_OBJECTIVE_REFERENCE_LINE": "false" if args.hide_objective_reference_line else "true",
            "FLR4MFCL_GITHUB_REF": FLR4MFCL_REF,
            "MFCLKIT_GITHUB_REF": MFCLKIT_REF,
            "MFCLSHINY_GITHUB_REF": args.mfclshiny_ref,
            "KFLOW_RUNTIME_PACKAGES": "none",
            "KFLOW_REPO_RUNTIME_PACKAGES": repo_runtime_packages(
                args.mfclshiny_ref
            ),
            "KFLOW_REPO_RUNTIME_UPDATE": "always",
            "KFLOW_RUNTIME_UPDATE": "always",
            "TUNA_FLOW_RUNTIME_UPDATE": "always",
            "KFLOW_RUNTIME_UPDATE_INTERVAL_HOURS": "0",
            "KFLOW_RUNTIME_GITHUB_AUTH": "true",
            "KFLOW_FORWARD_GITHUB_TOKEN_TO_RUNTIME": "true",
        },
        "tags": {
            "species": "BET",
            "assessment_year": "2026",
            "stage": "model-checks",
            "check_type": args.check,
            "model_jobs": model_numbers,
            "job_label": report_label,
        },
        "metadata": {
            "input_jobs_override": True,
            "source_model_jobs": models,
            "resolved_check_jobs": provenance,
            "job_name": job_name,
            "job_label": report_label,
            "job_title": (
                f"{report_title} | Model jobs #"
                + ", #".join(record["model_job"] for record in models)
            ),
            "job_description": config.get(
                "description",
                f"Report-ready mfclshiny {config['title']} figures and Word/LaTeX tables.",
            ),
        },
    }
    if args.check != "jitter":
        for name in ("JITTER_GRAD_REFERENCE", "JITTER_REL_DIFF_THRESHOLD", "JITTER_REPORT_DPI"):
            payload["env"].pop(name, None)
    if args.grad_reference is not None:
        payload["env"]["MODEL_CHECK_GRAD_REFERENCE"] = str(args.grad_reference)
        if args.check == "jitter":
            payload["env"]["JITTER_GRAD_REFERENCE"] = str(args.grad_reference)
    return payload, models


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("model_jobs", nargs="*", default=["8146", "8096"])
    parser.add_argument("--check", choices=tuple(CHECKS), default="jitter")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--api-url", default=os.environ.get("KFLOW_API_URL", "http://127.0.0.1:8089"))
    parser.add_argument("--repo", default=os.environ.get("MODEL_CHECK_REPO", REPO))
    parser.add_argument("--branch", default=os.environ.get("MODEL_CHECK_BRANCH", "main"))
    parser.add_argument("--title", default="")
    parser.add_argument(
        "--model-label",
        action="append",
        default=[],
        help="Display label override; provide once for all models or once per model job.",
    )
    parser.add_argument(
        "--regional-jitter",
        action="store_true",
        help="Build additional regional diagnostics from recoverable jitter outputs.",
    )
    parser.add_argument(
        "--regional-quantity",
        action="append",
        choices=("depletion", "recruitment"),
        default=[],
        help="Regional figure to include; repeat to include both (default: depletion).",
    )
    parser.add_argument(
        "--trajectory-style",
        choices=("distribution", "individual"),
        default="distribution",
        help="Show pointwise distribution bands or all included jitter trajectories.",
    )
    parser.add_argument("--reference-label", default="Reference model")
    parser.add_argument("--base-label", default="Attached base fit")
    parser.add_argument("--reference-colour", default="#C62828")
    parser.add_argument("--base-colour", default="#111827")
    parser.add_argument(
        "--hide-objective-reference-line",
        action="store_true",
        help="Omit the horizontal reference-objective line.",
    )
    parser.add_argument("--remote-user", default=os.environ.get("KFLOW_REMOTE_USER", "kyuhank"))
    parser.add_argument("--remote-host", default=os.environ.get("KFLOW_REMOTE_HOST", "nouofpsubmit.corp.spc.int"))
    parser.add_argument("--remote-base-dir", default=os.environ.get("KFLOW_REMOTE_BASE_DIR", "/home/kyuhank/KflowOutput"))
    parser.add_argument("--mfclshiny-ref", default=os.environ.get("MFCLSHINY_GITHUB_REF", MFCLSHINY_REF))
    parser.add_argument(
        "--grad-reference",
        type=float,
        default=None,
        help="Optional diagnostic MGC cutoff; omitted means use recorded run convergence.",
    )
    parser.add_argument("--rel-diff-threshold", type=float, default=10.0)
    parser.add_argument("--dpi", type=int, default=300)
    args = parser.parse_args()

    token = os.environ.get("KFLOW_API_TOKEN", "").strip()
    if not token:
        raise RuntimeError("KFLOW_API_TOKEN is required.")
    api = KflowAPI(
        args.api_url,
        token,
        github_token=os.environ.get("KFLOW_GITHUB_TOKEN", os.environ.get("GITHUB_PAT", "")),
    )
    model_refs = [value.lstrip("#") for value in args.model_jobs if value.strip()]
    if not model_refs:
        raise RuntimeError("Provide at least one model job number.")
    payload, models = build_submission(api, model_refs, args)
    for model in models:
        print(
            f"Model #{model['model_job']} ({model['model_label']}): "
            f"{CHECKS[args.check]['title']} jobs {', '.join('#' + str(x) for x in model['check_jobs'])}"
        )
    if args.dry_run:
        safe_payload = dict(payload)
        safe_payload["env"] = dict(payload["env"])
        print(json.dumps(safe_payload, indent=2, sort_keys=True))
        return 0

    api.request(
        "POST",
        f"/api/report/{CHECKS[args.check]['task']}",
        {
            "name": f"BET 2026 Diagnostic Checks - {CHECKS[args.check]['title']}",
            "description": (
                CHECKS[args.check].get("description")
                or "Portable, report-ready BET 2026 model checks built with "
                f"mfclshiny for {CHECKS[args.check]['title']} reports."
            ),
            "repo": args.repo,
            "branch": args.branch,
            "make_target": "all",
            "docker_image": payload["docker_image"],
            "remote_user": args.remote_user,
            "remote_host": args.remote_host,
            "remote_base_dir": args.remote_base_dir,
            "cpus": payload["cpus"],
            "memory": payload["memory"],
            "disk": payload["disk"],
            "output_patterns": payload["output_patterns"],
            "tags": {
                "species": "BET",
                "assessment_year": "2026",
                "stage": "model-checks",
            },
            "metadata": {
                "internal_task": False,
                "task_visibility": "primary",
                "task_role": "model-checks",
            },
        },
    )
    response = api.request("POST", f"/api/job/{CHECKS[args.check]['task']}", payload)
    job = response.get("job", response)
    print(
        f"Submitted {payload['batch_name']} as job "
        f"#{job_number(job)} ({job.get('status')})."
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (RuntimeError, urllib.error.URLError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        raise SystemExit(1)
