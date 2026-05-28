# Single-genome query patterns

When a user asks "what does CGA look like for genome X?" or "show me the evidence on one genome", use these patterns. They mirror section 12 of the `comparative_genome_annotation_demo.ipynb` notebook on the hub.

Set `TABLE` to the target table name (default `u_<USER>__prototype.comparative_genome_annotation_v1`).

## 1. Per-line coverage for one genome

```python
from pyspark.sql import functions as F
GENOME = "GCA_000010565.1_ASM1056v1_genomic"  # user-supplied
sdf = spark.table(TABLE).filter(F.col("genome_id") == GENOME)

total = sdf.count()
cov = sdf.agg(
    F.sum(F.col("bakta_product").isNotNull().cast("int")).alias("bakta"),
    F.sum(F.col("kofam_KO").isNotNull().cast("int")).alias("kofam"),
    F.sum(F.col("psortb_localization").isNotNull().cast("int")).alias("psortb"),
    F.sum(F.col("mmseqs2_cluster_rep").isNotNull().cast("int")).alias("mmseqs2"),
).collect()[0].asDict()
print(f"Genes: {total}")
for k, v in cov.items():
    print(f"  {k:8s}: {v:5d} / {total} ({100.0*v/total:5.1f}%)")
```

## 2. Broadcast values (one set per genome)

```python
sdf.select(
    "checkm2_completeness", "checkm2_contamination", "checkm2_genome_size", "checkm2_n50",
    "gtdbtk_domain", "gtdbtk_phylum", "gtdbtk_class", "gtdbtk_order", "gtdbtk_family",
    "gtdbtk_genus", "gtdbtk_species",
    "gtdbtk_closest_genome_reference", "gtdbtk_closest_genome_ani", "gtdbtk_closest_genome_af",
).limit(1).show(truncate=False, vertical=True)
```

## 3. Sample gene with all 4 per-gene lines present (cross-method agreement)

```python
ex = sdf.filter(F.col("evidence_count") == 4).limit(1).toPandas()
if len(ex):
    r = ex.iloc[0]
    print("--- bakta ---")
    print(f"  product:  {r['bakta_product']}")
    print(f"  EC:       {r['bakta_ec']}")
    print(f"  Pfam top: {r['bakta_pfam_top_name']}")
    print("--- kofam ---")
    print(f"  KO:       {r['kofam_KO']}  ({r['kofam_KO_definition']})")
    print(f"  score:    {r['kofam_score']}, evalue: {r['kofam_evalue']}")
    print("--- psortb ---")
    print(f"  {r['psortb_localization']} (score {r['psortb_score']})")
    print("--- mmseqs2 ---")
    print(f"  cluster: {r['mmseqs2_cluster_rep']}  core={r['pangenome_is_core']}")
```

## 4. Sample gene with partial evidence (illustrative of when sources disagree)

```python
ex = sdf.filter("kofam_KO IS NULL AND evidence_count = 3").limit(1).toPandas()
```

Then narrate: "bakta annotated this as <product>, but kofamscan's HMM scan found no significant KO match. The predictor should treat this as lower-confidence functional assignment."

## 5. Compact 20-row table view

```python
import pandas as pd
pd.set_option("display.max_columns", None); pd.set_option("display.width", 240); pd.set_option("display.max_colwidth", 50)
sample = sdf.select(
    "input_locus_tag", "evidence_count",
    "bakta_product", "kofam_KO", "kofam_KO_definition",
    "psortb_localization", "mmseqs2_cluster_rep",
).orderBy("input_locus_tag").limit(20).toPandas()
print(sample.to_string(index=False))
```

## 6. "Predictor stub" record for one gene

```python
def predict_function(input_locus_tag, table=TABLE):
    rows = spark.sql(f"SELECT * FROM {table} WHERE input_locus_tag = '{input_locus_tag}'").collect()
    if not rows:
        return {"input_locus_tag": input_locus_tag, "found": False}
    r = rows[0].asDict()
    return {
        "input_locus_tag": input_locus_tag,
        "genome_id":       r["genome_id"],
        "evidence": {
            "bakta":  {k: r[f"bakta_{k}"] for k in ["product","gene","ec","go","cog","pfam","uniref","kegg","so"]},
            "kofam":  None if r["kofam_KO"] is None else {"KO": r["kofam_KO"], "definition": r["kofam_KO_definition"],
                                                            "score": r["kofam_score"], "evalue": r["kofam_evalue"],
                                                            "alt_hits": r["kofam_alt_hits"]},
            "psortb": None if r["psortb_localization"] is None else {"localization": r["psortb_localization"],
                                                                       "score": r["psortb_score"]},
            "mmseqs2": None if r["mmseqs2_cluster_rep"] is None else {"cluster_rep": r["mmseqs2_cluster_rep"],
                                                                        "is_core": r["pangenome_is_core"]},
        },
        "genome_metadata": {
            "completeness":  r["checkm2_completeness"],
            "contamination": r["checkm2_contamination"],
            "taxonomy": None if r["gtdbtk_domain"] is None else {k: r[f"gtdbtk_{k}"] for k in
                ["domain","phylum","class","order","family","genus","species"]},
            "closest_reference":     r["gtdbtk_closest_genome_reference"],
            "closest_reference_ani": r["gtdbtk_closest_genome_ani"],
        },
    }
```

This is the shape a downstream function predictor would consume.

## When to use which

- User asks "summarise genome X" -> sections 1 + 2
- User asks "show me a gene" -> sections 3 or 4 (full or partial example)
- User asks "show me the table" -> section 5
- User asks "what would the predictor see" -> section 6
