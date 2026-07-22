#!/usr/bin/env python3
"""Resolve dependent Jitter jobs and submit a BET 2026 model-check report."""

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


TASK = "ofp-sam-bet-2026-model-checks"
REPO = "PacificCommunity/ofp-sam-bet-2026-model-checks"
MFCLSHINY_REF = "cfc7f8e789e66feb25a3835636fcbb743a7347d8"
COMPLETED = {"completed", "success"}
COLLECTOR_WORDS = re.compile(r"merge|attach|collector|aggregate|combined", re.I)


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


def is_jitter_job(job: dict) -> bool:
    text = job_text(job).lower()
    return bool(re.search(r"(^|[^a-z])jitter([^a-z]|$)", text))


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


def resolve_jitter_jobs(api: KflowAPI, model: dict) -> list[dict]:
    metadata = model.get("metadata") if isinstance(model.get("metadata"), dict) else {}
    latest_by_slot = metadata.get("attached_work_latest_by_slot")
    latest_refs: list[str] = []
    if isinstance(latest_by_slot, dict):
        for slot, record in latest_by_slot.items():
            if "jitter" not in str(slot).lower() or not isinstance(record, dict):
                continue
            ref = record.get("output_job") or record.get("job_number") or record.get("job")
            if isinstance(ref, (str, int)) and str(ref).strip():
                latest_refs.append(str(ref).strip().lstrip("#"))

    if latest_refs:
        latest_jobs = [api.job(ref) for ref in dict.fromkeys(latest_refs)]
        completed_latest = [
            job
            for job in latest_jobs
            if is_jitter_job(job) and str(job.get("status") or "").lower() in COMPLETED
        ]
        if completed_latest:
            return sorted(completed_latest, key=lambda item: int(job_number(item)))

    children, edges = descendants(api, model)
    candidates = {
        job_id: job
        for job_id, job in children.items()
        if is_jitter_job(job) and str(job.get("status") or "").lower() in COMPLETED
    }
    if not candidates:
        number = job_number(model)
        observed = [
            f"#{job_number(job)}={job.get('status')}:{job.get('report_code')}"
            for job in children.values()
            if is_jitter_job(job)
        ]
        raise RuntimeError(
            f"Model job #{number} has no completed dependent Jitter job. "
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
    provenance: list[dict] = []
    input_refs: list[str] = []
    models: list[dict] = []
    for ref in model_refs:
        model = api.job(ref)
        status = str(model.get("status") or "").lower()
        if status not in COMPLETED:
            raise RuntimeError(f"Model job #{job_number(model)} is {status or 'unknown'}, not completed.")
        jitter_jobs = resolve_jitter_jobs(api, model)
        model_id = str(model.get("id") or "")
        input_refs.append(model_id or str(job_number(model)))
        model_record = {
            "model_job": str(job_number(model) or ref),
            "model_id": model_id,
            "model_label": job_label(model),
            "jitter_jobs": [job_number(job) for job in jitter_jobs],
        }
        models.append(model_record)
        for jitter in jitter_jobs:
            jitter_id = str(jitter.get("id") or "")
            input_refs.append(jitter_id or str(job_number(jitter)))
            provenance.append(
                {
                    "model_job": model_record["model_job"],
                    "model_id": model_id,
                    "model_label": model_record["model_label"],
                    "jitter_job": str(job_number(jitter) or ""),
                    "jitter_id": jitter_id,
                }
            )

    input_refs = list(dict.fromkeys(ref for ref in input_refs if ref))
    model_numbers = ",".join(record["model_job"] for record in models)
    job_name = f"bet-2026-jitter-models-{model_numbers.replace(',', '-')}"
    model_labels = " + ".join(
        f"{record['model_label'].removesuffix(' fitted model')} #{record['model_job']}"
        for record in models
    )
    report_label = f"Jitter | {model_labels}"
    payload = {
        "repo": args.repo,
        "branch": args.branch,
        "docker_image": "ghcr.io/pacificcommunity/tuna-flow:v2.5@sha256:c87f1f6d9d4f62dc447844b58afe35f96af175bf933cb6cffbbbe39a59172360",
        "batch_name": job_name,
        "remote_user": args.remote_user,
        "remote_host": args.remote_host,
        "remote_base_dir": args.remote_base_dir,
        "input_jobs": input_refs,
        "output_patterns": ["jitter/**"],
        "cpus": 2,
        "memory": "6GB",
        "disk": "12GB",
        "env": {
            "MODEL_JOBS": model_numbers,
            "MODEL_CHECKS": "jitter",
            "MODEL_CHECK_TITLE": f"BET 2026 Model Checks - {report_label}",
            "KFLOW_JOB_PROVENANCE": json.dumps(provenance, separators=(",", ":")),
            "JITTER_GRAD_REFERENCE": str(args.grad_reference),
            "JITTER_REL_DIFF_THRESHOLD": str(args.rel_diff_threshold),
            "JITTER_REPORT_DPI": str(args.dpi),
            "MFCLSHINY_GITHUB_REF": args.mfclshiny_ref,
            "KFLOW_RUNTIME_GITHUB_AUTH": "true",
            "KFLOW_FORWARD_GITHUB_TOKEN_TO_RUNTIME": "true",
        },
        "tags": {
            "species": "BET",
            "assessment_year": "2026",
            "stage": "model-checks",
            "check_type": "jitter",
            "model_jobs": model_numbers,
            "job_label": report_label,
        },
        "metadata": {
            "input_jobs_override": True,
            "source_model_jobs": models,
            "resolved_jitter_jobs": provenance,
            "job_name": job_name,
            "job_label": report_label,
            "job_title": (
                "BET 2026 Model Checks - Jitter | Model jobs #"
                + ", #".join(record["model_job"] for record in models)
            ),
            "job_description": "Report-ready mfclshiny Jitter figures and Word/LaTeX tables.",
        },
    }
    return payload, models


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("model_jobs", nargs="*", default=["8146", "8096"])
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--api-url", default=os.environ.get("KFLOW_API_URL", "http://127.0.0.1:8089"))
    parser.add_argument("--repo", default=os.environ.get("MODEL_CHECK_REPO", REPO))
    parser.add_argument("--branch", default=os.environ.get("MODEL_CHECK_BRANCH", "main"))
    parser.add_argument("--remote-user", default=os.environ.get("KFLOW_REMOTE_USER", "kyuhank"))
    parser.add_argument("--remote-host", default=os.environ.get("KFLOW_REMOTE_HOST", "nouofpsubmit.corp.spc.int"))
    parser.add_argument("--remote-base-dir", default=os.environ.get("KFLOW_REMOTE_BASE_DIR", "/home/kyuhank/KflowOutput"))
    parser.add_argument("--mfclshiny-ref", default=os.environ.get("MFCLSHINY_GITHUB_REF", MFCLSHINY_REF))
    parser.add_argument("--grad-reference", type=float, default=0.001)
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
            f"Jitter jobs {', '.join('#' + str(x) for x in model['jitter_jobs'])}"
        )
    if args.dry_run:
        safe_payload = dict(payload)
        safe_payload["env"] = dict(payload["env"])
        print(json.dumps(safe_payload, indent=2, sort_keys=True))
        return 0

    api.request(
        "POST",
        f"/api/report/{TASK}",
        {
            "name": "BET 2026 Model Checks - Jitter",
            "description": (
                "Portable, report-ready BET 2026 model checks built with "
                "mfclshiny, starting with Jitter reports."
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
    response = api.request("POST", f"/api/job/{TASK}", payload)
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
