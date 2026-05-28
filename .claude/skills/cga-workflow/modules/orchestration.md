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
        |     - cdm_gtdbtk          (consumes .fna.gz)
        |     - cdm_checkm2         (consumes .fna.gz)
        |
        +-> cross-genome (one container, all inputs):
              - cdm_mmseqs2         (consumes all .faa together)
              - cdm_skani           (triangle: consumes all .fna.gz)
              - cdm_skani_gtdb      (search: consumes all .fna.gz)
```

No hard dependencies between submission groups in v1: the assembly script joins everything by `(genome_id, input_locus_tag)`, so jobs can launch in any order. Recommended pattern: **submit ALL jobs in one burst, then poll until everything is `complete`**.

## Submission pattern

**This skill is designed for kbderl (BERDL JupyterHub) as the primary runtime.** That's where the tenant Delta databases (`u_<user>__prototype` and the public lakehouse tables) live, and where `get_task_service_client()`, `get_minio_client()`, and `get_spark_session()` are auto-injected into the kernel. Use the Python client throughout.

```python
tscli  = get_task_service_client()
mincli = get_minio_client()
spark  = get_spark_session()
user   = tscli.whoami()["user"]
```

If a user invokes this skill from a local Claude Code session (off-cluster), the right move is to refuse: the assembly step writes to a Delta table that only exists in the kbderl Hive metastore. Tell the user to switch to kbderl JupyterHub and re-invoke. Do not try to do a partial run.

For each tool, build the `submit_job` call from `tool-catalog.md`. Always set:

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

- `failed` with exit code != 0: read the job's stderr from MinIO at `cts/<output_dir>/.../stderr.log`. Common causes: refdata UUID mismatch (most common), OOM (bump memory in the override and re-run that one job with `declobber=True`), input file malformed (psortb on archaea with `-n` flag etc.).
- `cancelled`: user or admin cancelled. Surface this; do not silently retry.
- `pending` for >runtime budget: the cluster is busy. Wait or escalate to the user.

**Never auto-retry on `failed`.** Report the failure with the stderr tail, ask the user how to proceed.

## Safe re-runs

If the user says "re-run the CGA on this same input set", do *not* re-submit jobs that already have outputs in MinIO. Check `cts/io/<USER>/output/cga/<run_label>/<tool>/.../` for the expected output files. If present, skip submission and reuse. The assembly step is purely a read of MinIO outputs + a Spark write; it's cheap to re-run on its own.

The `--force-resubmit` flag (advanced) overrides this and submits with `declobber=True`.

## Off-cluster execution: not supported in v1

If a user invokes this skill from a local Claude Code session (not on kbderl JupyterHub), refuse early:

> "This skill writes to a Delta table in the BERDL lakehouse Hive metastore, which is only reachable from the kbderl JupyterHub kernel. Please open the BERDL JupyterHub at https://hub.berdl.kbase.us, start a kernel, and re-invoke this skill there. Your CTS jobs and MinIO outputs are persistent across sessions, so if you've already submitted jobs, just attach and run the assembly step on kbderl."

Do not try to fan out CTS jobs via REST from a local session and then "hope to" assemble later. The user pays cluster time and ends with an unassembled pile of outputs.
