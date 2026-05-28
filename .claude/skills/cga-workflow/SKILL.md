---
name: cga-workflow
description: Run the Comparative Genome Annotation (CGA) workflow on user-supplied bacterial or archaeal genomes via the CDM Task Service (CTS). Submits 8 CTS tools (bakta_proteins, kofamscan, psortb, mmseqs2, gtdbtk, skani, skani_gtdb, checkm2) and assembles their outputs into a per-gene Delta wide table plus genome-level auxiliary tables. Use when the user says "annotate these genomes", "build a lines-of-evidence table for these genomes", "characterise this genome set", or asks to run the CGA pipeline.
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
- "annotate these genomes"
- "build a CGA / lines-of-evidence table for ..."
- "run the comparative annotation workflow"
- "compute the wide table for genomes X, Y, Z"
- "characterise this genome set"

Do NOT use this skill for:
- A single tool in isolation (use `remote-compute` and the relevant tool's demo notebook directly)
- Metabolic modeling / flux predictions (Layer 2, not yet implemented; see issue #22)
- Ontology enrichment of an existing CGA table (Layer 1 sibling; see issue #23)
- Genomes already annotated by KBase RAST (this skill calls bakta_proteins, not RAST)

## Runtime: kbderl JupyterHub only (v1)

This skill is designed for the BERDL JupyterHub (kbderl) as the primary and only supported runtime in v1. The reason: the assembly step writes to a Delta table in the lakehouse Hive metastore, which is only reachable from a kbderl kernel where the tenant Delta databases (e.g. `u_<user>__prototype`) live. Off-cluster invocation is refused (see `modules/orchestration.md`).

If the user is not on kbderl, stop and direct them to https://hub.berdl.kbase.us before doing anything else.

## Pre-flight

Before submitting any CTS job:

1. **Confirm runtime.** Verify the kernel is on kbderl JupyterHub: `get_task_service_client`, `get_minio_client`, `get_spark_session` should be in the global namespace. If they aren't, refuse and direct the user to kbderl.
2. **Resolve inputs.** v1 accepts MinIO paths only. The user must provide:
   - A list of genome assembly FASTAs in `cts/io/<user>/...` (gzipped `.fna.gz` or `.fna`)
   - A matching list of prodigal-called protein FASTAs (one `.faa` per genome, same naming convention)
   - If the user has only local files, hand off to the `remote-compute` skill for upload first, then re-invoke this skill.
3. **Resolve target.** Default output table: `u_<USERNAME>__prototype.comparative_genome_annotation_v1`. Where `<USERNAME>` is the value of `tscli.whoami()["user"]`. If the user supplied `--table-name`, use that.
4. **Sanity-check refdata.** The CGA workflow binds three refdata UUIDs (see `modules/tool-catalog.md`). Confirm each is reachable via `tscli.refdata.list_refdata()` before submitting jobs.

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
