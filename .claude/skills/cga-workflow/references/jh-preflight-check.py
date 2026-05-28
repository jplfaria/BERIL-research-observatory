"""
CGA skill pre-flight check — paste into a kbderl JupyterHub kernel.

Validates the three runtime capabilities and the four refdata UUIDs the skill
requires, without submitting any jobs. Safe to re-run.
"""
from berdl_notebook_utils import get_task_service_client, get_minio_client
from berdl_notebook_utils.setup_spark_session import get_spark_session

print("== capability probe ==")
tscli = get_task_service_client()
me = tscli.whoami()
print(f"  CTS ok       user={me['user']}  roles={me.get('roles')}")

mincli = get_minio_client()
buckets = [b.name for b in mincli.list_buckets()]
print(f"  MinIO ok     buckets={buckets[:5]}{'...' if len(buckets) > 5 else ''}")

spark = get_spark_session()
n_dbs = spark.sql("SHOW DATABASES").count()
print(f"  Spark ok     databases_visible={n_dbs}")

print()
print("== refdata check (4 UUIDs) ==")
needed = {
    "bakta v6.0_amr20260324": "30f8ba11-a456-408c-a9f9-7d232ba3ed8e",
    "kofam 2025-04-30":       "84b31af0-a5a7-4016-906c-9ad9eef34c6a",
    "GTDB R232":              "bb6352b4-b86f-4e3d-a858-4bc77327ab13",
    "checkm2 uniref100":      "b5d76426-0ee2-459a-a875-8e0dc8089b54",
}
entries = tscli._cts_request("refdata")["data"]
have = {r["id"]: r for r in entries}

def _ready(r):
    return any(s.get("cluster") == "kbase" and s.get("state") == "complete"
               for s in (r.get("statuses") or []))

missing = []
for label, uid in needed.items():
    r = have.get(uid)
    if not r:
        missing.append((label, uid, "not registered"))
        print(f"  MISS {label:24s} {uid}  not registered")
    elif not _ready(r):
        missing.append((label, uid, "not complete on kbase"))
        print(f"  WARN {label:24s} {uid}  registered but not complete on kbase")
    else:
        print(f"  OK   {label:24s} {uid}")

print()
print("== image digests (for pinning) ==")
import re
raw = tscli.get_images()  # text listing
for rec in re.split(r"^(?=# )", raw, flags=re.MULTILINE):
    name = rec.partition("\n")[0].lstrip("# ").strip()
    short = name.rsplit("/", 1)[-1].split(":")[0] if name else ""
    if short in {"cdm_bakta_proteins","cdm_kofamscan","cdm_psortb","cdm_mmseqs2",
                 "cdm_gtdbtk","cdm_skani","cdm_skani_gtdb","cdm_checkm2"}:
        m = re.search(r"digest:\s*(\S+)", rec)
        print(f"  {short:24s} {m.group(1)[:25] if m else '(no digest)'}...  {name}")

print()
if missing:
    print(f"FAIL: {len(missing)} refdata issue(s). Skill will refuse until resolved.")
else:
    print("PASS: pre-flight clean. Skill is ready to submit jobs.")
