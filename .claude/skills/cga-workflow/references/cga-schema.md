# CGA wide-table schema reference

55 columns total. The table key is `(genome_id, input_locus_tag)` - one row per input protein.

## Identity + derived (3)

| Column | Type | Notes |
|---|---|---|
| `genome_id` | string | The input genome filename minus extension. |
| `input_locus_tag` | string | Prodigal-assigned protein ID (e.g. `AP009389.1_2223`). |
| `evidence_count` | int | Count of 4 per-gene sources that have a value (bakta_product + kofam_KO + psortb_localization + mmseqs2_cluster_rep). Range 0..4. Taxonomy + checkm2 are broadcast and not counted. |

## Coordinates (3)

| Column | Type | Notes |
|---|---|---|
| `start` | int | Start position on contig (1-based, prodigal convention). |
| `end` | int | End position. |
| `strand` | string | `"+"` or `"-"`. Null if description doesn't carry coords. |

## Bakta (20)

| Column | Type | Notes |
|---|---|---|
| `bakta_length` | int | Protein length in aa. |
| `bakta_molecular_weight` | float | From bakta seq_stats; null when bakta couldn't characterise (e.g. hypothetical with no scoring data). |
| `bakta_isoelectric_point` | float | Same caveat as MW. |
| `bakta_gene` | string | Canonical gene symbol (e.g. `dnaA`). Often null. |
| `bakta_alt_genes` | string | Comma-joined alternative gene symbols when bakta knows >1. |
| `bakta_product` | string | Free-text functional description. The headline annotation. |
| `bakta_ec` | string | Enzyme Commission number; standard pathway-DB join key. |
| `bakta_kegg` | string | Comma-joined KEGG IDs from `db_xrefs` (not in the simplified TSV). Different signal from `kofam_KO`. |
| `bakta_go` | string | Comma-joined Gene Ontology terms. |
| `bakta_so` | string | Comma-joined Sequence Ontology terms (from `db_xrefs`). Usually `SO:0001217` (CDS). |
| `bakta_cog` | string | COG cluster ID (e.g. `COG0593`) or single-letter functional category. |
| `bakta_pfam` | string | Comma-joined Pfam IDs from HMM scan. Multiple per protein possible. |
| `bakta_pfam_top_name` | string | Human-readable name of the highest-scoring Pfam hit. |
| `bakta_pfam_top_score` | float | Score of the top Pfam hit. |
| `bakta_refseq` | string | NCBI RefSeq accession (cross-ref). |
| `bakta_uniparc` | string | UniParc accession (sequence-identity cross-ref). |
| `bakta_uniref` | string | Combined UniRef100/90/50 cluster IDs. |
| `bakta_expert_product` | string | Curated overlay annotation (rare; IS-elements + AMRFinderPlus). Semicolon-joined when multiple. |
| `bakta_expert_source` | string | Source of the expert annotation (e.g. `IS`, `AMRFinderPlus`). |
| `bakta_expert_top_score` | float | Top score among the expert annotations. |
| `bakta_hypothetical` | bool | Explicit flag; true when bakta could not annotate beyond "hypothetical protein". |
| `bakta_aa_hexdigest` | string | MD5 hash of the protein sequence. Use as a join key for cross-DB lookup or as a cache key for expensive per-protein computations. Matches `sequence_hash` in KBDatalakeApps. |
| `bakta_aa` | string | Full amino-acid sequence. Bulky (~300 aa average) but useful for downstream AI embedding / structure prediction. |

## Kofamscan (5)

| Column | Type | Notes |
|---|---|---|
| `kofam_KO` | string | The KEGG Orthology ID of the top significant HMM hit. |
| `kofam_KO_definition` | string | Human-readable KO definition. |
| `kofam_score` | float | HMM bit score. |
| `kofam_evalue` | float | HMM E-value. |
| `kofam_alt_hits` | int | Count of additional significant KO HMM hits beyond the top one. |

## Psortb (2)

| Column | Type | Notes |
|---|---|---|
| `psortb_localization` | string | Predicted subcellular compartment (e.g. `Cytoplasmic`, `OuterMembrane`, `Unknown`). |
| `psortb_score` | float | Confidence score (higher = more confident). |

## MMseqs2 (2)

| Column | Type | Notes |
|---|---|---|
| `mmseqs2_cluster_rep` | string | `<genome_id>::<input_locus_tag>` of the cluster representative. Within-set clustering only. |
| `pangenome_is_core` | bool | True if the cluster has a member in *every* input genome. At default mmseqs2 80% identity, expect false for cross-phylum sets. |

## CheckM2 broadcast (6)

| Column | Type | Notes |
|---|---|---|
| `checkm2_completeness` | float | %. |
| `checkm2_contamination` | float | %. |
| `checkm2_genome_size` | bigint | bp. |
| `checkm2_gc_content` | float | 0..1 fraction. |
| `checkm2_n50` | bigint | bp. |
| `checkm2_total_cds` | int | Total CDS count. |

All six are repeated identically on every gene row of a given genome (broadcast pattern). Null on every row of a genome whose `cdm_checkm2` CTS job failed or hasn't been run.

## GTDB-Tk broadcast (11)

| Column | Type | Notes |
|---|---|---|
| `gtdbtk_domain` | string | Bacteria / Archaea. |
| `gtdbtk_phylum` | string |  |
| `gtdbtk_class` | string |  |
| `gtdbtk_order` | string |  |
| `gtdbtk_family` | string |  |
| `gtdbtk_genus` | string |  |
| `gtdbtk_species` | string |  |
| `gtdbtk_classification` | string | Full semicolon-joined lineage (the raw GTDB-Tk output column). |
| `gtdbtk_closest_genome_reference` | string | GCA/GCF accession of the closest GTDB R232 representative. |
| `gtdbtk_closest_genome_ani` | float | ANI to that closest reference. |
| `gtdbtk_closest_genome_af` | float | Aligned fraction. |

All 11 are broadcast onto every gene row of a given genome.

## Null semantics

- A null in a per-gene column means "the tool didn't return a hit for this protein" (e.g. kofamscan found no significant HMM hit at the default cutoffs).
- A null in a broadcast column (checkm2, gtdbtk) means the per-genome CTS job for that tool failed for that genome. Broadcast columns are all-or-nothing per genome: every gene row of a given genome either has the full set of broadcast values, or every gene row has them null.
- `evidence_count = 0` is genuinely a feature: the gene has bakta's spine entry but no functional annotation, no localization, no cluster. Worth flagging in QC.
