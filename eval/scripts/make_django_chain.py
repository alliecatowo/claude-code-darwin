#!/usr/bin/env python3
"""Build the django chain dataset: all SWE-bench Lite django instances,
ordered by created_at (oldest first) — a longitudinal chain on ONE repo."""
import json, os, sys, urllib.request

CACHE = "/tmp/swe_lite.json"

def fetch():
    if os.path.exists(CACHE):
        return json.load(open(CACHE))
    rows = []
    for page in range(3):
        url = (f"https://datasets-server.huggingface.co/rows?dataset=princeton-nlp%2FSWE-bench_Lite"
               f"&config=default&split=test&offset={page*100}&length=100")
        with urllib.request.urlopen(url, timeout=30) as r:
            rows.extend(json.load(r)["rows"])
    json.dump(rows, open(CACHE, "w"))
    return rows

rows = fetch()
django = []
for row in rows:
    inst = row.get("row", row)
    if inst.get("repo") == "django/django":
        django.append({
            "instance_id": inst["instance_id"],
            "repo": inst["repo"],
            "base_commit": inst["base_commit"],
            "version": inst.get("version"),
            "created_at": inst.get("created_at"),
        })
django.sort(key=lambda d: (d.get("created_at") or "", d["instance_id"]))
n = int(sys.argv[1]) if len(sys.argv) > 1 else 30
out = django[:n]
with open("eval/datasets/django_chain.json", "w") as f:
    json.dump(out, f, indent=1)
print(f"wrote eval/datasets/django_chain.json: {len(out)} instances "
      f"({out[0]['instance_id']} … {out[-1]['instance_id']}) of {len(django)} django total")
