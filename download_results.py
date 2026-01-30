#!/usr/bin/env python3
"""
Download the scanner's results of a PipelineRun.

Resolves the PipelineRun by name or by pipeline (latest run). Finds the PVC
attached to the run-roxctl-scan task, creates a temporary pod that mounts it,
copies the scanner's results locally, then deletes the pod.
"""

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile


def run(cmd: list[str], capture: bool = True) -> subprocess.CompletedProcess:
    """Run a command; raise on failure."""
    result = subprocess.run(
        cmd,
        capture_output=capture,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"Command failed: {' '.join(cmd)}\n{result.stderr or result.stdout or ''}"
        )
    return result


def get_oc_cmd() -> list[str]:
    """Return oc or kubectl, preferring oc."""
    for name in ("oc", "kubectl"):
        try:
            run([name, "version", "--client"], capture=True)
            return [name]
        except (RuntimeError, FileNotFoundError):
            continue
    raise RuntimeError("Neither 'oc' nor 'kubectl' found in PATH")


def get_latest_pipelinerun(oc_cmd: list[str], namespace: str, pipeline_name: str) -> str:
    """Return the name of the most recent PipelineRun for the given pipeline."""
    # Tekton adds label tekton.dev/pipeline=<pipelineName> to PipelineRuns
    result = run(
        oc_cmd
        + [
            "get",
            "pipelineruns",
            "-n",
            namespace,
            "-l",
            f"tekton.dev/pipeline={pipeline_name}",
            "-o",
            "json",
        ]
    )
    data = json.loads(result.stdout)
    items = data.get("items") or []
    if not items:
        raise RuntimeError(
            f"No PipelineRun found for pipeline '{pipeline_name}' in namespace '{namespace}'"
        )
    # Sort by creation timestamp, newest first
    items.sort(
        key=lambda pr: pr.get("metadata", {}).get("creationTimestamp") or "",
        reverse=True,
    )
    return items[0]["metadata"]["name"]


def get_output_pvc_and_file_path_from_pipelinerun(
    oc_cmd: list[str], namespace: str, pipelinerun_name: str
) -> tuple[str, str]:
    """
    Resolve the PVC and output file path from the run-roxctl-scan task.

    Uses PipelineRun status.childReferences to find the TaskRun with
    pipelineTaskName run-roxctl-scan, then TaskRun spec.workspaces (name: output)
    for the PVC and status.results (name: output-file-path) for the file path.

    Returns (pvc_claim_name, output_file_path) where output_file_path is the
    absolute path from the task result (use its basename for the fetch pod).
    """
    result = run(
        oc_cmd
        + [
            "get",
            "pipelinerun",
            pipelinerun_name,
            "-n",
            namespace,
            "-o",
            "json",
        ]
    )
    pr = json.loads(result.stdout)
    child_refs = (pr.get("status") or {}).get("childReferences") or []
    taskrun_ref = None
    for ref in child_refs:
        if ref.get("pipelineTaskName") == "run-roxctl-scan":
            taskrun_ref = ref
            break
    if not taskrun_ref:
        raise RuntimeError(
            f"PipelineRun '{pipelinerun_name}' has no child TaskRun with pipelineTaskName run-roxctl-scan"
        )
    taskrun_name = taskrun_ref.get("name")
    if not taskrun_name:
        raise RuntimeError(
            f"Child reference for run-roxctl-scan in PipelineRun '{pipelinerun_name}' has no name"
        )

    result = run(
        oc_cmd
        + [
            "get",
            "taskrun",
            taskrun_name,
            "-n",
            namespace,
            "-o",
            "json",
        ]
    )
    tr = json.loads(result.stdout)
    spec = tr.get("spec") or {}
    workspaces = spec.get("workspaces") or []
    output_ws = None
    for ws in workspaces:
        if ws.get("name") == "output":
            output_ws = ws
            break
    if not output_ws:
        raise RuntimeError(
            f"TaskRun '{taskrun_name}' has no workspace with name 'output'"
        )
    pvc = output_ws.get("persistentVolumeClaim")
    if not pvc or not pvc.get("claimName"):
        raise RuntimeError(
            f"TaskRun '{taskrun_name}' workspace 'output' is not bound to a persistentVolumeClaim"
        )
    pvc_name = pvc["claimName"]

    # Get output file path from task result output-file-path
    tr_results = (tr.get("status") or {}).get("results") or []
    output_file_path = None
    for r in tr_results:
        if r.get("name") == "output-file-path":
            output_file_path = (r.get("value") or "").strip()
            break
    if not output_file_path:
        raise RuntimeError(
            f"TaskRun '{taskrun_name}' has no result 'output-file-path' (task may not have completed)"
        )

    return (pvc_name, output_file_path)


def create_fetch_pod(
    oc_cmd: list[str],
    namespace: str,
    pvc_name: str,
    pod_name: str,
) -> None:
    """Create a pod that mounts the given PVC at /workspaces/output."""
    pod = {
        "apiVersion": "v1",
        "kind": "Pod",
        "metadata": {
            "name": pod_name,
            "namespace": namespace,
        },
        "spec": {
            "containers": [
                {
                    "name": "fetch",
                    "image": "registry.access.redhat.com/ubi8/ubi-minimal",
                    "command": ["sleep", "300"],
                    "volumeMounts": [
                        {"name": "output", "mountPath": "/workspaces/output"}
                    ],
                }
            ],
            "volumes": [
                {"name": "output", "persistentVolumeClaim": {"claimName": pvc_name}}
            ],
            "restartPolicy": "Never",
        },
    }
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".json", delete=False
    ) as f:
        json.dump(pod, f, indent=2)
        path = f.name
    try:
        run(oc_cmd + ["apply", "-f", path])
    finally:
        os.unlink(path)


def wait_pod_ready(oc_cmd: list[str], namespace: str, pod_name: str, timeout_sec: int = 60) -> None:
    """Wait until the pod is Ready."""
    run(
        oc_cmd
        + [
            "wait",
            "--for=condition=Ready",
            f"pod/{pod_name}",
            "-n",
            namespace,
            f"--timeout={timeout_sec}s",
        ]
    )


def copy_file_from_pod(
    oc_cmd: list[str], namespace: str, pod_name: str, remote_path: str, local_path: str
) -> None:
    """Copy a file from the pod to the local path."""
    run(
        oc_cmd
        + [
            "cp",
            f"{namespace}/{pod_name}:{remote_path}",
            local_path,
        ]
    )


def delete_pod(oc_cmd: list[str], namespace: str, pod_name: str) -> None:
    """Delete the pod."""
    subprocess.run(
        oc_cmd + ["delete", "pod", pod_name, "-n", namespace, "--ignore-not-found=true"],
        capture_output=True,
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Download the output file of a PipelineRun."
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--pipeline",
        metavar="NAME",
        help="Pipeline name; use the latest PipelineRun for this pipeline",
    )
    group.add_argument(
        "--pipelinerun",
        metavar="NAME",
        help="PipelineRun name to use directly",
    )
    parser.add_argument(
        "--namespace",
        "-n",
        default="default",
        help="Kubernetes namespace (default: default)",
    )
    parser.add_argument(
        "--output",
        "-o",
        metavar="PATH",
        default=None,
        help="Local path to write the output file (default: current dir, filename from task result)",
    )
    args = parser.parse_args()

    try:
        oc_cmd = get_oc_cmd()
    except RuntimeError as e:
        print(e, file=sys.stderr)
        return 1

    try:
        if args.pipelinerun:
            pipelinerun_name = args.pipelinerun
            print(f"Using PipelineRun '{pipelinerun_name}'", file=sys.stderr)
        else:
            print(f"Looking up latest PipelineRun for pipeline '{args.pipeline}'...", file=sys.stderr)
            pipelinerun_name = get_latest_pipelinerun(
                oc_cmd, args.namespace, args.pipeline
            )
            print(f"Latest PipelineRun: {pipelinerun_name}", file=sys.stderr)

        print(f"Looking up PVC and output file path from PipelineRun '{pipelinerun_name}'...", file=sys.stderr)
        pvc_name, output_file_path = get_output_pvc_and_file_path_from_pipelinerun(
            oc_cmd, args.namespace, pipelinerun_name
        )
        output_file_basename = os.path.basename(output_file_path)
        print(f"Found PVC '{pvc_name}', output file '{output_file_basename}'", file=sys.stderr)

        # Sanitize pipelinerun name for use in pod name (RFC 1123: lowercase, alphanumeric, hyphens)
        suffix = re.sub(r"[^a-z0-9-]", "-", pipelinerun_name.lower())
        suffix = re.sub(r"-+", "-", suffix).strip("-")[:45] or "run"
        pod_name = f"download-results-{suffix}"

        try:
            print(f"Creating temporary pod '{pod_name}'...", file=sys.stderr)
            create_fetch_pod(oc_cmd, args.namespace, pvc_name, pod_name)
            print("Waiting for pod to be ready...", file=sys.stderr)
            wait_pod_ready(oc_cmd, args.namespace, pod_name)
            remote_path = f"/workspaces/output/{output_file_basename}"
            local_path = args.output or output_file_basename
            print(f"Copying file to '{local_path}'...", file=sys.stderr)
            copy_file_from_pod(
                oc_cmd, args.namespace, pod_name, remote_path, local_path
            )
            print(f"Downloaded to {local_path}", file=sys.stderr)
        finally:
            print(f"Deleting temporary pod '{pod_name}'...", file=sys.stderr)
            delete_pod(oc_cmd, args.namespace, pod_name)

        return 0
    except RuntimeError as e:
        print(e, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
