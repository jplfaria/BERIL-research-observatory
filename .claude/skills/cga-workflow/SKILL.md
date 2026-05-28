---
name: cga-workflow
description: Run the Comparative Genome Annotation (CGA) workflow on user-supplied bacterial or archaeal genomes, MAGs, or isolates via the CDM Task Service (CTS). Submits 8 CTS tools (bakta_proteins, kofamscan, psortb, mmseqs2, gtdbtk, skani, skani_gtdb, checkm2) and assembles their outputs into a per-gene Delta wide table plus genome-level auxiliary tables. Use when the user says "annotate these genomes / MAGs / isolates", "characterise / characterize this genome", "build a lines-of-evidence table", or asks to run the CGA pipeline. Do NOT use for a single tool in isolation (use remote-compute), metabolic modeling (Layer 2), ontology enrichment (Layer 3), or genomes already annotated by KBase RAST.
allowed-tools: Bash, Read, Write, AskUserQuestion, Agent
user-invocable: true
---

# Comparative Genome Annotation (CGA) workflow

## What this skill does

Given one or more bacterial / archaeal genome assemblies (and matching prodigal-called protein FASTAs), submits the 8 live CTS tools that make up Layer 1 of the CGA pipeline, polls them to completion, and assembles their outputs into a single per-gene Delta wide table:

- `u_<user>__prototype.comparative_genome_annotation_v1` (default; override via `--table-name`)
- 55 columns per gene (3 identity/derived + 3 coords + 20 bakta + 5 kofam + 2 psortb + 2 mmseqs2 + 6 checkm2 broadcast + 11 gtdbtk broadcast + 3 skani_gtdb broadcast)
- Plus two auxiliary genome-level tables (within-set skani triangle + skani_gtdb top-N hits with novelty gap)

This is Layer 1 of a 3-layer model: **CGA -> Metabolic modeling -> Phenotype predictor**. Layers 2 and 3 are tracked as separate enhancement issues on `kbaseincubator/cdm_tool_skeleton` (#22 modeling, #23 ontology). This skill stops at Layer 1; do not extend it.

## When to invoke

Trigger phrases include:
- "annotate these genomes" / "annotate my MAGs" / "annotate this isolate"
- "build a CGA / lines-of-evidence table for ..."
- "run the comparative annotation workflow"
- "compute the wide table for genomes X, Y, Z"
- "characterise / characterize this genome set"
- "what genes/functions are in this genome?" (Layer 1 functional annotation)

Do NOT use this skill for:
- A single tool in isolation (use `remote-compute` and the relevant tool's demo notebook directly)
- Metabolic modeling / flux predictions (Layer 2, not yet implemented; see issue #22)
- Ontology enrichment of an existing CGA table (Layer 1 sibling; see issue #23)
- Genomes already annotated by KBase RAST (this skill calls bakta_proteins, not RAST)

## Runtime: any Python on the BERDL cluster network (v1)

This skill needs three capabilities: CTS submission, MinIO access, and Spark write to the lakehouse Hive metastore (target table lives under `u_<user>__prototype`). All three are available from any Python process with `berdl_notebook_utils` installed and network reach to the BERDL cluster — including BERDL JupyterHub kernels (where the helpers are auto-injected as globals), Claude Code or other agents on cluster-network hosts, and ad-hoc on-cluster scripts.

Off-cluster (laptop) runs are **not** supported in v1 — even with the helpers pip-installed locally, MinIO needs proxy setup and the Spark Connect write path to the kbderl Hive metastore has not been validated. The runtime probe in Pre-flight step 1 detects this and refuses, directing the user to https://hub.berdl.kbase.us.

## Pre-flight

Before submitting any CTS job:

1. **Confirm runtime via explicit imports + live probe** (do not rely on Jupyter-injected globals — they only exist on kbderl kernels, not in regular Python):

   ```python
   from berdl_notebook_utils import get_task_service_client, get_minio_client
   from berdl_notebook_utils.setup_spark_session import get_spark_session
   tscli  = get_task_service_client(); _ = tscli.whoami()                # CTS reachable + auth ok
   mincli = get_minio_client()                                           # MinIO client constructed
   spark  = get_spark_session(); _ = spark.sql("SHOW DATABASES").count() # Spark + metastore reachable
   ```

   If any of the three imports or three probes fails (`ModuleNotFoundError`, `ConnectionError`, auth error, etc.), refuse and direct the user to kbderl JupyterHub. Report which check failed — that distinguishes "wrong runtime" from "transient outage".
2. **Resolve inputs.** v1 accepts MinIO paths only. The user must provide:
   - A list of genome assembly FASTAs in `cts/io/<user>/...` (gzipped `.fna.gz` or `.fna`)
   - A matching list of prodigal-called protein FASTAs (one `.faa` per genome, same naming convention)
   - If the user has only local files, hand off to the `remote-compute` skill for upload first, then re-invoke this skill.
3. **Resolve target.** Default output table: `u_<USERNAME>__prototype.comparative_genome_annotation_v1`. Where `<USERNAME>` is the value of `tscli.whoami()["user"]`. If the user supplied `--table-name`, use that.
4. **Sanity-check refdata.** Refdata binds at the **image record** (each CTS image has `refdata_id` + `default_refdata_mount_point`); `submit_job` only needs to override `refdata_mount_point` if you want a non-default path. The CGA workflow uses four refdata UUIDs across the eight images. Confirm each is registered in CTS and `complete` on the `kbase` cluster before submitting jobs. The `CTSClient` v0.2.1 does not expose a public `refdata` accessor, so call the REST endpoint via the client's internal request helper:

   ```python
   entries = tscli._cts_request("refdata")["data"]
   have    = {r["id"]: r for r in entries}
   needed  = {
       "bakta v6.0_amr20260324": "30f8ba11-a456-408c-a9f9-7d232ba3ed8e",
       "kofam 2025-04-30":       "84b31af0-a5a7-4016-906c-9ad9eef34c6a",
       "GTDB R232":              "bb6352b4-b86f-4e3d-a858-4bc77327ab13",
       "checkm2 uniref100":      "b5d76426-0ee2-459a-a875-8e0dc8089b54",
   }
   def _ready(r):
       return any(s.get("cluster") == "kbase" and s.get("state") == "complete"
                  for s in (r.get("statuses") or []))
   missing = [(label, uid) for label, uid in needed.items()
              if uid not in have or not _ready(have[uid])]
   if missing:
       # refuse: refdata not registered or not staged on kbase cluster
       ...
   ```

   `_cts_request` is an underscored method — if `cdm-task-service-client` adds a public `list_refdata()` in a future release, switch to it. Tracking issue (file if not yet open): "cdm-task-service-client: expose public refdata accessor".

If any of the above fails, stop and tell the user explicitly what's missing. Do not submit jobs into a broken setup.

## High-level flow

```
   1. Validate inputs (MinIO paths exist, .faa pairs each .fna)
        |
   2. Submit CTS jobs in dependency order (modules/orchestration.md)
      a) parallel fan-out: bakta_proteins, kofamscan, psortb, gtdbtk, checkm2,
                            skani triangle, skani_gtdb search
      b) once all bakta_proteins done: submit mmseqs2 (needs all .faa together)
        |
   3. Poll job statuses, collect MinIO output paths (modules/orchestration.md)
        |
   4. Run the assembly (modules/assembly.md):
      - Load all per-tool outputs from MinIO into pandas / Spark
      - Build the bakta spine; left-join the per-gene lines; left-join broadcast columns
      - Compute derived columns (evidence_count, pangenome_is_core, top-1-top-2 ANI gap)
      - Materialize Delta table at target name
        |
   5. Report back to user (modules/assembly.md "Reporting" section)
```

## Files in this skill

- `modules/tool-catalog.md` - per-tool image refs, refdata UUIDs, args templates, cpus/memory/runtime defaults, input/output shapes
- `modules/orchestration.md` - dependency graph, parallel vs sequential rules, polling pattern, declobber-safe re-runs
- `modules/assembly.md` - how to materialize the wide table from CTS outputs (self-contained; all required code is in this module, no external scripts needed)
- `references/cga-schema.md` - the 55-column schema, what each column means, when it's null
- `references/single-genome-view.md` - query patterns to summarise one genome from the wide table

Read each module the first time you invoke the skill. They are concise.

## Hard rules

- **Never submit a CTS job without the user's go-ahead.** When the user invokes the skill, summarise the plan (genome list, tool list, expected runtime, expected output table) and ask for confirmation before the first `tscli.submit_job(...)`.
- **Use `declobber=False` (default) for first runs; `declobber=True` only when explicitly re-running a step.** This keeps cached outputs safe.
- **Always pin tool images by sha256 digest** (see `tool-catalog.md`). Tag-only references can drift.
- **Cluster is always `kbase`.** Do not change without an explicit user request and a reason.
- **Default to printing job IDs prominently** so the user can monitor independently if the skill loses connection.
- **The 4-genome demo numbers (5802 rows, 55 cols) are for the cdm_tool_skeleton test set only.** Do not assert them as expected counts for the user's input.

## Layered framing (for explanation, do not implement)

CGA Layer 1 (this skill) -> Layer 2 metabolic modeling (issue #22, future) -> Layer 3 phenotype predictor (separate workstream). If a user asks about adding flux predictions or essentiality, point them at issue #22 and stop. If they ask about ontology enrichment of the CGA table, point them at issue #23.
