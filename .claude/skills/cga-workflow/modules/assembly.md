# CGA wide-table assembly

How to materialize the per-gene Delta wide table from cached CTS outputs. **This module is self-contained.** All required parsing code is embedded below. The skill runs on the BERDL JupyterHub (kbderl), where `get_minio_client()`, `get_task_service_client()`, `get_spark_session()`, and the tenant Delta databases (`u_<user>__prototype`, public tables, etc.) are all in-process. Do not look for external scripts; everything you need is in this module.

## Inputs (MinIO paths from orchestration step)

For run_label `<RL>` under `cts/io/<USER>/output/cga/<RL>/`:

- `cdm_bakta_proteins/<i>/<basename>.tsv`  (one per genome)
- `cdm_bakta_proteins/<i>/<basename>.json` (one per genome - **richer than TSV**, see step 2)
- `cdm_kofamscan/<i>/<basename>.annotations.tsv`
- `cdm_psortb/<i>/<basename>.psortb.tsv`
- `cdm_mmseqs2/<RL>_cluster.tsv`
- `cdm_gtdbtk/<i>/gtdbtk.bac120.summary.tsv` and/or `gtdbtk.ar53.summary.tsv` (top-level only - exclude `/classify/`, `/identify/`, `/align/`, `/ani_screen/` subpaths)
- `cdm_skani/triangle.tsv` (auxiliary)
- `cdm_skani_gtdb/hits.tsv` (auxiliary)
- `cdm_checkm2/quality_report.tsv` (one combined TSV across all input genomes from the cdm_checkm2 CTS job submitted in the orchestration step)

## Step-by-step

### 1. Load bakta_proteins TSV (the spine)

Per genome, read the TSV with `pd.read_csv(..., sep="\t", comment="#")`. The TSV has 10 columns: `ID, Length, Gene, Product, EC, GO, COG, RefSeq, UniParc, UniRef`. Rename to `input_locus_tag, bakta_length, bakta_gene, bakta_product, bakta_ec, bakta_go, bakta_cog, bakta_refseq, bakta_uniparc, bakta_uniref` and add `genome_id`. Cast `input_locus_tag` to string.

### 2. Augment from bakta_proteins JSON

The TSV silently drops several useful categories. For each genome's `.json`, iterate features and pull:

- `bakta_pfam` = comma-joined Pfam IDs from `feature["pfams"][*]["id"]`
- `bakta_pfam_top_name` / `bakta_pfam_top_score` = the highest-scoring Pfam hit
- `start`, `end`, `strand` = parsed from `feature["description"]` (prodigal-style `# start # end # strand-as-1-or-minus-1`)
- `bakta_aa_hexdigest` = `feature["aa_hexdigest"]` (MD5 of sequence; matches KBDatalakeApps's `sequence_hash` for cross-DB joining)
- `bakta_aa` = `feature["aa"]` (full protein sequence; bulky but useful for downstream AI embedding work)
- `bakta_hypothetical` = `feature["hypothetical"]` (boolean)
- `bakta_kegg` = comma-joined values where `feature["db_xrefs"][*]` starts with `"KEGG:"`
- `bakta_so` = same for `"SO:"`
- `bakta_alt_genes` = comma-joined `feature["genes"]` list (when bakta knows multiple aliases)
- `bakta_molecular_weight` = `feature["seq_stats"]["molecular_weight"]` (null when bakta has no hit)
- `bakta_isoelectric_point` = `feature["seq_stats"]["isoelectric_point"]`
- `bakta_expert_product` / `bakta_expert_source` / `bakta_expert_top_score` = from `feature["expert"]` list (rare; IS-element + AMRFinderPlus overlays)

Merge with the TSV-derived frame on `(genome_id, input_locus_tag)`. Result: `bakta` frame, one row per input protein.

### JSON-parsing code (embedded; copy as-is)

```python
import re, io, json
import pandas as pd

_COORD_RE = re.compile(r"#\s*(\d+)\s*#\s*(\d+)\s*#\s*(-?1)\s*#")

def _parse_coords(desc):
    if not desc:
        return (None, None, None)
    m = _COORD_RE.search(desc)
    if not m:
        return (None, None, None)
    start, end = int(m.group(1)), int(m.group(2))
    strand = "+" if m.group(3) == "1" else "-"
    return (start, end, strand)

def parse_bakta_for_genome(mincli, bucket, tsv_key, json_key, genome_id):
    """Returns a single pandas DataFrame for one genome, with TSV-typed cols + JSON-derived cols."""
    raw = mincli.get_object(bucket, tsv_key).read().decode("utf-8")
    tsv = pd.read_csv(io.StringIO(raw), sep="\t", comment="#").rename(columns={
        "ID":"input_locus_tag","Length":"bakta_length","Gene":"bakta_gene","Product":"bakta_product",
        "EC":"bakta_ec","GO":"bakta_go","COG":"bakta_cog","RefSeq":"bakta_refseq",
        "UniParc":"bakta_uniparc","UniRef":"bakta_uniref",
    })
    tsv["genome_id"] = genome_id
    tsv["input_locus_tag"] = tsv["input_locus_tag"].astype(str)

    j = json.loads(mincli.get_object(bucket, json_key).read().decode("utf-8"))
    rows = []
    for feat in j.get("features", []):
        pfams = feat.get("pfams") or []
        pfam_ids = ",".join(p.get("id","") for p in pfams) if pfams else None
        top = max(pfams, key=lambda p: p.get("score", 0)) if pfams else None
        start, end, strand = _parse_coords(feat.get("description", ""))

        by_src = {}
        for x in feat.get("db_xrefs") or []:
            if ":" in x:
                src, val = x.split(":", 1)
                by_src.setdefault(src, []).append(val)

        experts   = feat.get("expert") or []
        e_products = [e["product"] for e in experts if isinstance(e, dict) and e.get("product")]
        e_sources  = [e["source"]  for e in experts if isinstance(e, dict) and e.get("source")]
        e_scores   = [float(e["score"]) for e in experts
                      if isinstance(e, dict) and e.get("score") is not None]

        ss  = feat.get("seq_stats") or {}
        alt = feat.get("genes") or []

        rows.append({
            "genome_id":                genome_id,
            "input_locus_tag":          str(feat.get("id")),
            "bakta_pfam":               pfam_ids,
            "bakta_pfam_top_name":      top["name"]  if top else None,
            "bakta_pfam_top_score":     float(top["score"]) if top else None,
            "start":                    start,
            "end":                      end,
            "strand":                   strand,
            "bakta_aa_hexdigest":       feat.get("aa_hexdigest"),
            "bakta_hypothetical":       bool(feat.get("hypothetical", False)),
            "bakta_kegg":               ",".join(by_src.get("KEGG", [])) or None,
            "bakta_so":                 ",".join(by_src.get("SO", []))   or None,
            "bakta_alt_genes":          ",".join(alt) if alt else None,
            "bakta_molecular_weight":   ss.get("molecular_weight"),
            "bakta_isoelectric_point":  ss.get("isoelectric_point"),
            "bakta_expert_product":     ";".join(e_products) if e_products else None,
            "bakta_expert_source":      ";".join(e_sources)  if e_sources  else None,
            "bakta_expert_top_score":   max(e_scores) if e_scores else None,
            "bakta_aa":                 feat.get("aa"),
        })
    json_df = pd.DataFrame(rows)
    return tsv.merge(json_df, on=["genome_id", "input_locus_tag"], how="left")
```

Loop this per genome, concat the results into a single `bakta` frame.

### 3. Load per-gene tools

- **kofamscan:** read each genome's annotations.tsv, skip the second header row (`skiprows=[1]`), filter rows where `#` column == `"*"` (significant), sort by score desc, dedupe by `gene name`, compute `kofam_alt_hits` = (group count - 1). Output columns: `kofam_KO`, `kofam_KO_definition`, `kofam_score`, `kofam_evalue`, `kofam_alt_hits`.
- **psortb:** read each genome's `.psortb.tsv`, split `SeqID` on whitespace to recover `input_locus_tag`, keep `Localization` -> `psortb_localization` and `Score` -> `psortb_score`.
- **mmseqs2:** read the single cluster TSV (`rep_full TAB member_full`), parse each as `<genome_id>::<input_locus_tag>`. Compute per cluster `n_genomes_in_cluster` (count distinct `member_genome`). Derive `pangenome_is_core = (n_genomes_in_cluster == total_n_input_genomes)`. Emit per-locus rows: `input_locus_tag, mmseqs2_cluster_rep, pangenome_is_core`.

### 4. Load per-genome broadcast tools

- **checkm2:** read the cdm_checkm2 CTS output (one or more `quality_report.tsv` files; concat if more than one container ran). Rename `Name->genome_id, Completeness->checkm2_completeness, Contamination->checkm2_contamination, Genome_Size->checkm2_genome_size, GC_Content->checkm2_gc_content, Contig_N50->checkm2_n50, Total_Coding_Sequences->checkm2_total_cds`.
- **gtdbtk:** concat the top-level `bac120` and `ar53` summary TSVs. Drop rows where `classification` is null. Parse the semicolon-joined classification into 7 columns (`gtdbtk_domain` ... `gtdbtk_species`) by splitting on `;` and matching `d__`, `p__`, `c__`, `o__`, `f__`, `g__`, `s__` prefixes. Keep `classification` -> `gtdbtk_classification`, `closest_genome_reference` -> `gtdbtk_closest_genome_reference`, `closest_genome_ani` -> `gtdbtk_closest_genome_ani` (numeric), `closest_genome_af` -> `gtdbtk_closest_genome_af` (numeric). The `user_genome` column matches our `genome_id`.

### 5. Build wide table

```python
wide = (bakta_sdf
        .join(kofam_sdf,   on="input_locus_tag", how="left")
        .join(psortb_sdf,  on="input_locus_tag", how="left")
        .join(mm_sdf,      on="input_locus_tag", how="left")
        .join(checkm2_sdf, on="genome_id",       how="left")
        .join(gtdbtk_sdf,  on="genome_id",       how="left"))
wide = wide.withColumn("evidence_count",
    (F.col("bakta_product").isNotNull().cast("int")
     + F.col("kofam_KO").isNotNull().cast("int")
     + F.col("psortb_localization").isNotNull().cast("int")
     + F.col("mmseqs2_cluster_rep").isNotNull().cast("int")))
# Order columns per references/cga-schema.md
wide = wide.select(*ordered).cache()
```

Then materialize:

```python
spark.sql(f"CREATE DATABASE IF NOT EXISTS {TARGET_DB}")
spark.sql(f"DROP TABLE IF EXISTS {TARGET_DB}.{TARGET_TABLE}")
(wide.write.format("delta").mode("overwrite").option("overwriteSchema", "true")
     .saveAsTable(f"{TARGET_DB}.{TARGET_TABLE}"))
```

Default `TARGET_DB = f"u_{user}__prototype"` (where `user = tscli.whoami()["user"]`), `TARGET_TABLE = "comparative_genome_annotation_v1"`. Override via the skill's `--table-name` argument.

### 6. Build auxiliary genome-level tables (optional but recommended)

- **Within-set ANI:** load `cdm_skani/triangle.tsv`. If empty (header only), report "no within-set edges above 15% AF threshold" (the correct answer for cross-phylum sets). If non-empty, display the edge list.
- **Top-N reference ANI + novelty gap:** load `cdm_skani_gtdb/hits.tsv`. Parse `Ref_file` and `Query_file` to extract GCA/GCF accessions (regex `(GC[AF]_\d+\.\d+)`). Group by query, sort by ANI desc, take top-2: report top-1 ref + ANI, top-2 ref + ANI, and `top1_top2_gap = top1_ani - top2_ani`. Join to gtdbtk's `closest_genome_reference` for an agreement column. If only top-1 is present (no second hit clears skani's threshold), `top1_top2_gap = None` is the right answer.

## Reporting back to the user

After successful assembly, print:

1. Target table name + row count + column count.
2. Per-line coverage: how many rows have `bakta_product`, `kofam_KO`, `psortb_localization`, `mmseqs2_cluster_rep`, `gtdbtk_domain` populated.
3. Evidence count distribution (how many genes have 1/2/3/4 per-gene sources).
4. Per-genome taxonomy + closest-ref ANI table (one row per genome).
5. Auxiliary section summary: within-set edge count (or "empty"), top-1-vs-top-2 gap table.
6. **Warnings**, if any: psortb Gram-stain approximation (all genomes run with `-n` until per-genome flag-selection is wired), mmseqs2 default 80% identity producing no core genes for cross-phylum sets.
7. Point at `references/single-genome-view.md` for the agent's query pattern when the user follows up with "show me what one genome looks like".

Do not pre-emptively run the single-genome view. Wait for the user to ask.
