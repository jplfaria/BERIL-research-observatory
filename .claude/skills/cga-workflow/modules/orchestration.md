# CGA orchestration

How to submit, poll, and recover the 8 CTS jobs that feed CGA assembly. Read `tool-catalog.md` first for per-tool specifics.

## Dependency graph

```
   input: .fna.gz (assemblies)  +  .faa (prodigal-called proteins)
        |
        +-> per-genome (parallel fan-out, one container per genome):
        |     - cdm_bakta_proteins  (consumes .faa)
        |     - cdm_kofamscan       (consumes .faa)
        |     - cdm_psortb          (consumes .faa)
        |     - cdm_checkm2         (consumes .fna.gz)
        |
        +-> cross-genome (one container, all inputs):
              - cdm_gtdbtk          (classify_wf reads a genome_dir; all inputs land on one tree)
              - cdm_mmseqs2         (consumes all .faa together)
              - cdm_skani           (triangle: consumes all .fna.gz)
              - cdm_skani_gtdb      (search: consumes all .fna.gz)
```

No hard dependencies between submission groups in v1: the assembly script joins everything by `(genome_id, input_locus_tag)`, so jobs can launch in any order. Recommended pattern: **submit ALL jobs in one burst, then poll until everything is `complete`**.

## Submission pattern

**This skill runs from any Python process on the BERDL cluster network** — BERDL JupyterHub kernels (where the three helpers below are auto-injected into the kernel as globals) and on-cluster Claude Code / scripts (where they are imported explicitly). Both paths use the same `cdm-task-service-client` underneath; the only difference is whether the helpers are pre-bound in `globals()`. Off-cluster (laptop) is refused — see "Off-cluster execution" below.

Import explicitly. Do not assume Jupyter-injected globals — that assumption breaks every non-Jupyter runtime, even ones that are perfectly capable of running the skill:

```python
from berdl_notebook_utils import get_task_service_client, get_minio_client
from berdl_notebook_utils.setup_spark_session import get_spark_session

tscli  = get_task_service_client()
mincli = get_minio_client()
spark  = get_spark_session()
user   = tscli.whoami()["user"]
```

On kbderl JupyterHub the imports are redundant but harmless — the names just rebind to the same callables.

For each tool, build the `submit_job` call from `tool-catalog.md`, but **cross-check against the image record's `usage:` field at submission time**. The catalog templates in this skill have drifted from image-side requirements before (see 2026-05-28 run: bakta needed `--force`, kofamscan needed `-p`/`-k`, gtdbtk's `--skip_ani_screen` had been removed). If the `usage:` field disagrees with the template here, the image wins. Parse with:

```python
import re
raw = tscli.get_images()
for rec in re.split(r"^(?=# )", raw, flags=re.MULTILINE):
    if "<image-short-name>" in rec.partition("\n")[0]:
        m = re.search(r"^\s*usage:\s*(.+?)(?=^\s*\w+:|\Z)", rec, re.MULTILINE | re.DOTALL)
        if m: print(m.group(1).strip())
```

Always set:

- `cluster="kbase"`
- `declobber=False` on first runs (idempotent re-runs are explicit, not accidental)
- `output_mount_point="/out"`
- `output_dir=f"cts/io/<USER>/output/cga/<run_label>/<tool_name>"` - one consistent naming scheme so assembly can find everything

Run label convention: `<run_label>` is `<YYYYMMDD>_<short_hash_of_input_basenames>` so re-running on the same input set is idempotent and re-running on a different set lands somewhere else.

Print every submitted job ID immediately, so the user can monitor independently if the agent loses its session.

```
Submitted CTS jobs for run_label=20260527_a3b9f1:
  cdm_bakta_proteins   GCA_000008085.1   job=550e8400-...
  cdm_bakta_proteins   GCA_000010565.1   job=6ba7b810-...
  ...
  cdm_mmseqs2          (all)             job=8f1f6dc6-...
```

## Polling pattern

Two acceptable poll strategies:

1. **One-shot synchronous wait** if the user is sitting in the chat and the run is small:
   ```python
   for j in jobs:
       j.wait_for_completion()  # blocks; uses tscli's default poll interval
   ```
   Acceptable for <=4 genomes. Loses the chat session if a job hangs.

2. **Async poll-and-yield** for larger runs or when running unattended:
   ```python
   import time
   pending = list(jobs)
   while pending:
       still_pending = []
       for j in pending:
           s = j.get_job_status()["state"]
           if s in ("complete", "failed", "cancelled"):
               print(f"  {j.id}: {s}")
           else:
               still_pending.append(j)
       pending = still_pending
       if pending:
           time.sleep(30)
   ```
   Report state changes as they happen.

In either case, after every poll cycle log: how many jobs are pending, how many done, how many failed.

## Failure handling

Per-job failure modes you may see:

- `error` with exit code != 0: read the job's stderr via the CTS REST endpoint `GET /jobs/{job_id}/log/0/stderr` (returns plain text, NOT JSON; `CTSClient._cts_request` will raise `UnexpectedServerResponseError` and leak the body in the exception message, which is currently the easiest way to read it). Common causes seen in real runs:
  - **Bad args** (most common in practice): e.g. bakta wrote-into-existing-`/out` without `--force`; kofamscan missing `-p`/`-k`; gtdbtk passed a flag from a different release (`--skip_ani_screen` removed in R232). The image record's `usage:` field is authoritative — cross-check it before re-submitting.
  - Refdata UUID mismatch (rare once registered): caught by SKILL.md Pre-flight step 4.
  - OOM: bump memory in the resource override and re-run that one job with `declobber=True`.
  - Input file malformed: psortb on archaea with `-n` is a known soft case (wrong but won't crash).
- `cancelled`: user or admin cancelled. Surface this; do not silently retry.
- States that pre-empt the container actually running: `error_processing_submitting`/`error_processing_submitted` mean the container exited fast (often within seconds of `job_submitted`) — usually an arg parsing failure, not a real compute failure.
- `pending` for >runtime budget: the cluster is busy. Wait or escalate to the user.

**Never auto-retry on `failed`.** Report the failure with the stderr tail, ask the user how to proceed.

## Safe re-runs

If the user says "re-run the CGA on this same input set", do *not* re-submit jobs that already have outputs in MinIO. Check `cts/io/<USER>/output/cga/<run_label>/<tool>/.../` for the expected output files. If present, skip submission and reuse. The assembly step is purely a read of MinIO outputs + a Spark write; it's cheap to re-run on its own.

The `--force-resubmit` flag (advanced) overrides this and submits with `declobber=True`.

## Off-cluster execution: not supported in v1

The supported runtimes are kbderl JupyterHub kernels and any other Python process on the BERDL cluster network (Claude Code, scripts, batch jobs) where `berdl_notebook_utils` is installed. Detection is by *capability probe*, not by hostname — run the three checks in SKILL.md "Pre-flight" step 1.

If any probe fails (typical for a laptop without proxy + Spark Connect remote setup), refuse early:

> "This skill needs CTS submission, MinIO access, and Spark write to the lakehouse Hive metastore. The runtime probe failed on: `<which check>`. The supported v1 runtimes are BERDL JupyterHub (https://hub.berdl.kbase.us) and on-cluster Python sessions. Off-cluster (laptop) Spark Connect writes to the kbderl Hive metastore have not been validated and are not supported in v1. Your CTS jobs and MinIO outputs are persistent across sessions, so if you've already submitted jobs, switch to a supported runtime and run the assembly step there."

Do not try to fan out CTS jobs via REST from an unverified runtime and then "hope to" assemble later. The user pays cluster time and ends with an unassembled pile of outputs.
