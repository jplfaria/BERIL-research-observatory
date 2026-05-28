# CGA tool catalog

Per-tool reference for the 8 CTS tools in the CGA workflow. Each entry includes the image reference, refdata binding, args template, default resource budget, and what comes out the other end. **Always verify image SHAs and refdata UUIDs at runtime before submitting** - the values below are the latest known but tools get re-registered:

- Images: `tscli.get_images()` returns a formatted text listing (one record per image starting with `# <image-ref>`); parse with `re.split(r"^(?=# )", raw, flags=re.MULTILINE)` and look for `digest:` lines to pin SHAs. Each image record also carries `refdata_id`, `default_refdata_mount_point`, `entrypoint`, and a **`usage:`** field — the registrar's authoritative note on required args + env vars. The templates below were verified against the `usage:` fields on 2026-05-28. **If a `usage:` field disagrees with a template here, the image record wins** — registrars update images more often than this catalog. Refdata is bound at image registration time, not at job submission: `submit_job` only needs `refdata_mount_point` to override the default (typically `/ref_data`).
- Refdata: `CTSClient` v0.2.1 has no public refdata accessor; use the internal request helper `tscli._cts_request("refdata")["data"]` (list of dicts with `id`, `file`, `crc64nvme`, `statuses`). Switch to a public accessor when `cdm-task-service-client` adds one.

The canonical demo notebook for each tool is linked from the `kbaseincubator/cdm_tool_skeleton` README's "Demo notebooks" table. When in doubt, read the demo.

---

## cdm_bakta_proteins (per-genome, parallel)

**Role in CGA:** the spine. Annotates pre-called protein FASTAs and produces both a typed TSV (10 columns) and a rich JSON (per-protein Pfam hits, coordinates from prodigal headers, sequence hash, hypothetical flag, KEGG/SO from db_xrefs, expert-curated overlay, sequence properties).

- **Image:** `ghcr.io/kbaseincubator/cdm_bakta_proteins:0.1.0` (pin by sha at submission time; sha lives in the demo notebook)
- **Refdata:** bakta v6.0_amr20260324, UUID `30f8ba11-...` (shared with `cdm_bakta`)
- **Input:** one `.faa` per container (prodigal-called proteins)
- **Output:** `<basename>.tsv`, `<basename>.json`, `<basename>.hypotheticals.tsv`, `<basename>.inference.tsv`, `<basename>.faa`, `<basename>.log`
- **Args (template):**
  ```python
  args=[
      "--prefix", "<basename>",
      "--output", "/out",
      "--threads", "4",
      "--force",                  # CTS pre-creates /out, so bakta_proteins needs --force
      tscli.insert_files(),
  ]
  ```
  `BAKTA_DB=/ref_data/db` is baked into the image; do NOT pass `--db`.
- **Resources:** cpus=4, memory=16GB, runtime=PT60M (per-genome; archaeal genomes can finish in <5 min)
- **Run order:** parallel fan-out; submit one container per input genome

---

## cdm_kofamscan (per-genome, parallel)

**Role in CGA:** KO assignment (KEGG Orthology) via HMM scan. Independent method from bakta's DIAMOND-vs-RefSeq annotation.

- **Image:** `ghcr.io/kbaseincubator/cdm_kofamscan:0.1.0`
- **Refdata:** kofam 2025-04-30, UUID `84b31af0-...`
- **Input:** one `.faa` per container
- **Output:** `<basename>.annotations.tsv` (one row per significant HMM hit)
- **Args (template):**
  ```python
  args=[
      "-o", "/out/<basename>.annotations.tsv",
      "--cpu", "4",
      "-p", "/ref_data/profiles",      # required: KO HMM profile dir inside the refdata mount
      "-k", "/ref_data/ko_list",       # required: KO threshold table
      tscli.insert_files(),
  ]
  ```
- **Resources:** cpus=4, memory=8GB, runtime=PT30M
- **Run order:** parallel fan-out

---

## cdm_psortb (per-genome, parallel)

**Role in CGA:** subcellular localization prediction. Sequence-based, orthogonal to homology-based annotation.

- **Image:** `ghcr.io/kbaseincubator/cdm_psortb:0.1.2`
- **Refdata:** bundled in image; no UUID
- **Input:** one `.faa` per container
- **Output:** `<basename>.psortb.tsv` (one row per protein with Localization + Score)
- **Args (template):**
  ```python
  args=["-n",  # Gram-negative; see Caveat below
        "-o", "long",
        "-i", tscli.insert_files(),
        "-r", "/out"]
  ```
- **Resources:** cpus=2, memory=4GB, runtime=PT30M
- **Run order:** parallel fan-out
- **Caveat (Gram hint):** psortb needs a Gram-stain hint (`-n` Gram-negative, `-p` Gram-positive, `-a` archaea). The current CGA demo runs `-n` for all four test genomes which is wrong for the archaeal pair; per-genome config is a known gap. For a clean run, the skill should infer Gram from gtdbtk's phylum once gtdbtk is done - but for v1 we accept the same approximation (run `-n` by default) and surface a warning in the report.
- **Caveat (input format — found 2026-05-28):** psortb silently produces an EMPTY `out.psortb.tsv` (0 bytes, job still marked `complete`) when the input `.faa` has proteins ending in `*` (stop codon). pyrodigal and standard prodigal both emit trailing `*` by default. Pre-process protein FASTAs to strip `*` characters before upload: `sed -i 's/\*$//' input.faa` or, in Python, `seq = seq.rstrip("*")` per record. Verify after a run by checking `os.stat(...).st_size > 0` on the psortb output and surfacing "psortb empty — check input format" if it is.

---

## cdm_mmseqs2 (cross-genome, sequential after bakta_proteins)

**Role in CGA:** within-set protein clustering. Produces `cluster_rep` per gene; we derive `pangenome_is_core` (cluster contains at least one member from every input genome).

- **Image:** `ghcr.io/kbaseincubator/cdm_mmseqs2:0.1.0`
- **Refdata:** none (user-set clustering)
- **Input:** *all* input protein FASTAs in one container (one job, not fan-out)
- **Output:** `<runlabel>_cluster.tsv` (TSV: `rep_full TAB member_full`, where each `<full>` is `<genome_id>::<input_locus_tag>`)
- **Args (template):**
  ```python
  args=["easy-cluster",
        tscli.insert_files(),       # list of all .faa files
        "/out/<runlabel>",
        "/tmp",
        "--min-seq-id", "0.8",      # default; loosen for cross-phylum sets
        "--threads", "8"]
  ```
- **Resources:** cpus=8, memory=16GB, runtime=PT60M
- **Run order:** submit ONCE all bakta_proteins jobs finish (or in parallel if the user's protein FASTAs are independent of bakta_proteins' output, which is typical for prodigal-called inputs - in that case fan-out with bakta_proteins)
- **Caveat for cross-phylum sets:** at the default 80% identity, no clusters will be core. For ortholog-detection across phyla, loosen to 30% identity. v1 of this skill uses defaults; an advanced override can change it.

---

## cdm_gtdbtk (per-genome, parallel)

**Role in CGA:** taxonomic placement + closest-reference ANI. Broadcast onto every gene row of a given genome.

- **Image:** `ghcr.io/kbaseincubator/cdm_gtdbtk:0.1.1`
- **Refdata:** GTDB R232 bundle, UUID `bb6352b4-b86f-4e3d-a858-4bc77327ab13` (also used by `cdm_skani_gtdb`; skani sketches live at `/ref_data/release232/skani/database/`)
- **Input:** one assembly `.fna.gz` per container (genome-mode tool)
- **Output:** `gtdbtk.bac120.summary.tsv` and/or `gtdbtk.ar53.summary.tsv` at the top level of the output directory (plus a deep tree of intermediate files - the skill only reads the top-level summaries; the assembly module spells out the exclusions)
- **Args (template):**
  ```python
  args=["classify_wf",
        "--genome_dir", "/input_files",   # the INPUT MOUNT DIR — gtdbtk scans for files matching --extension
        "--out_dir", "/out",
        "--cpus", "4",
        "--extension", "fna.gz"]          # match your uploaded assembly extension exactly
  ```
  Notes:
  - Pass `--genome_dir /input_files` (the default `input_mount_point`), **not** `tscli.insert_files()`. `classify_wf` takes a directory, not a file list; the inserted-file placeholder would expand to a file path and gtdbtk would error.
  - Do **not** pass `--skip_ani_screen` — it's not a flag in `gtdbtk:0.1.1` (`gtdbtk: error: unrecognized arguments: --skip_ani_screen`). Earlier R220 builds accepted it; R232 dropped it.
  - `GTDBTK_DATA_PATH=/ref_data/release232` is baked into the image; do NOT pass `--db`.
  - To run all input genomes on a single shared marker-gene alignment + tree, keep `num_containers=1` (default).
- **Resources:** cpus=4, memory=64GB, runtime=PT120M (skani's `sketches.db` is ~75 GB; classify_wf for small inputs takes 30-90 min)
- **Run order:** one container per submission with all inputs (NOT parallel fan-out — the image's `usage:` field recommends `num_containers=1` so all genomes land on the same tree)
- **Note:** internal classify_wf already uses skani against a mash-prescreened candidate set and reports the single closest reference. That's the top-1 ANI we broadcast.

---

## cdm_skani (cross-genome, parallel - independent set)

**Role in CGA auxiliary section:** within-set pairwise ANI across all input genomes. Answers "are any of my inputs the same strain?" Outputs an empty edge list above the 15% AF default threshold when genomes are distant (the 4-genome demo set produces an empty output by design - that is the correct answer, not a tool failure).

- **Image:** `ghcr.io/kbaseincubator/cdm_skani:0.1.0` (sha `3c645fa6...`)
- **Refdata:** none
- **Input:** all input assemblies in one container
- **Output:** `triangle.tsv` (sparse edge list)
- **Args (template):**
  ```python
  args=["triangle",
        "-E", "-o", "/out/triangle.tsv",
        "-t", "4",
        tscli.insert_files()]
  ```
- **Resources:** cpus=4, memory=8GB, runtime=PT15M
- **Run order:** parallel with all per-genome jobs

---

## cdm_skani_gtdb (per-genome OR all-at-once, parallel)

**Role in CGA auxiliary section:** top-N nearest GTDB R232 references for each input genome. Used to surface the top-1-vs-top-2 ANI gap (novelty signal that gtdbtk's classify_wf does not surface).

- **Image:** `ghcr.io/kbaseincubator/cdm_skani_gtdb:0.1.0` (sha `682e6d44...`)
- **Refdata:** reuses GTDB R232 UUID `bb6352b4-...`; skani sketch DB lives at `/ref_data/release232/skani/database/`
- **Input:** all input assemblies in one container (skani search supports multi-query)
- **Output:** `hits.tsv` (Ref_file, Query_file, ANI, Align_fraction_ref/query, Ref_name, Query_name)
- **Args (template):**
  ```python
  args=["search",
        "-d", "/ref_data/release232/skani/database/",
        "-o", "/out/hits.tsv",
        "-t", "4",
        "-n", "5",                  # top-5 per query
        "--short-header",
        tscli.insert_files()]
  ```
- **Resources:** cpus=4, memory=32GB, runtime=PT30M
- **Run order:** parallel with all per-genome jobs
- **Caveat on top-N:** for very novel genomes only the top-1 self-hit may clear skani's default ANI threshold, leaving the top-N gap as None. That is the correct answer (no near neighbor), not a tool bug.

---

## cdm_checkm2 (per-genome, parallel)

**Role in CGA:** per-genome quality assessment (completeness, contamination, genome size, GC, N50, total CDS). Broadcast onto every gene row.

- **Image:** `ghcr.io/kbasetest/cdm_checkm2:0.3.0` (note: `kbasetest` org, not `kbaseincubator` - this tool predates `cdm_tool_skeleton` and was Gavin's reference prototype the other 7 wrappers were modelled on; that's why there's no `handoffs/checkm2.md` and no entry in the skeleton's Demo notebooks table)
- **Refdata:** checkm2 uniref100 diamond DB, UUID `b5d76426-0ee2-459a-a875-8e0dc8089b54`, mounted at `/ref_data` by default. (The CheckM2 DB is *not* bundled in the image — it ships separately and is bound to the image record. `submit_job` does not need to pass `refdata_id`.)
- **Input:** one assembly `.fna.gz` per container (genome-mode)
- **Output:** `quality_report.tsv` (one row per input genome, six columns: Name, Completeness, Contamination, Genome_Size, GC_Content, Contig_N50, Total_Coding_Sequences)
- **Args (template):**
  ```python
  args=["--input", tscli.insert_files(),
        "--output-directory", "/out",
        "--threads", "4"]
  ```
- **Resources:** cpus=4, memory=16GB, runtime=PT30M
- **Run order:** parallel fan-out (each genome is independent)
- **Auto-import bonus:** the `cdm_checkm2` image is wired to an importer in `kbase/cdm-spark-events-importers` (`cdmeventimporters/checkm2.yaml`). When a `cdm_checkm2` CTS job completes, the CDM Spark Event Processor (CSEP) auto-writes results into a per-tool Iceberg table. The CGA assembly does NOT depend on this; we read MinIO directly like every other tool. But: advanced users can query the auto-imported table for already-processed genomes without resubmitting.
- **No other CTS tool currently has an auto-importer.** That whole pattern is still prototype.

---

## Shelved tools (do NOT call from this skill)

- `cdm_mmseqs2_gtdb`: cluster user proteins against GTDB rep proteins. Shelved 2026-05-22 with full scoping at `cdm_tool_skeleton/docs/shelved_mmseqs2_gtdb.md`. If a user asks for cross-reference clustering, point them at the shelving doc and stop.

---

## Resource override (advanced)

If a user invokes the skill with `--resource-overrides '{"cdm_gtdbtk": {"memory": "128GB"}}'`, merge that into the per-tool defaults above before submission. Do not silently apply un-validated overrides; print the effective per-tool resource budget before submission.
