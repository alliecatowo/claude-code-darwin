#!/usr/bin/env python3
"""Build per-repo chain datasets from SWE-bench Lite (cached /tmp/swe_lite.json).
Repos with >= MIN instances get a chain (cap CAP), ordered by created_at.
Writes eval/datasets/chains/<repo-slug>.json + a manifest for the night matrix."""
import json, os, sys, re

CACHE = "/tmp/swe_lite.json"
MIN = int(sys.argv[1]) if len(sys.argv) > 1 else 18
CAP = int(sys.argv[2]) if len(sys.argv) > 2 else 25

rows = json.load(open(CACHE))
by_repo = {}
for row in rows:
    inst = row.get("row", row)
    by_repo.setdefault(inst.get("repo", ""), []).append(inst)

outdir = "eval/datasets/chains"
os.makedirs(outdir, exist_ok=True)
manifest = []
for repo, insts in sorted(by_repo.items(), key=lambda kv: -len(kv[1])):
    if repo == "django/django":  # already have django_chain.json; include for night matrix
        pass
    if len(insts) < MIN:
        continue
    insts.sort(key=lambda d: (d.get("created_at") or "", d["instance_id"]))
    chain = [
        {
            "instance_id": i["instance_id"],
            "repo": i["repo"],
            "base_commit": i["base_commit"],
            "version": i.get("version"),
            "created_at": i.get("created_at"),
        }
        for i in insts[:CAP]
    ]
    slug = re.sub(r"[^a-z0-9]+", "-", repo.split("/")[1].lower()).strip("-")
    path = f"{outdir}/{slug}.json"
    json.dump(chain, open(path, "w"), indent=1)
    manifest.append({"repo": repo, "slug": slug, "path": path, "n": len(chain)})
    print(f"{repo:<30} {len(chain):>3} tasks -> {path}")

json.dump(manifest, open(f"{outdir}/manifest.json", "w"), indent=1)
print(f"\nmanifest: {len(manifest)} repos (min {MIN}, cap {CAP}) -> {outdir}/manifest.json")
