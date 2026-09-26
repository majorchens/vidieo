"""Rebase copied, hash-locked pilot inputs after moving the runtime to ECS."""
from __future__ import annotations

import argparse
from pathlib import Path

import store


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--apply",action="store_true")
    args=parser.parse_args()
    store.initialize()
    changes=[]
    with store.connect() as c:
        rows=c.execute("SELECT id,project_id,storage_ref,sha256 FROM assets ORDER BY id").fetchall()
        for row in rows:
            ref=row["storage_ref"]
            if ref.startswith(("https://","studio://")):continue
            path=Path(ref)
            parts=path.parts
            matches=[i for i,x in enumerate(parts) if x=="inputs" and i+1<len(parts) and parts[i+1]==row["project_id"]]
            if not matches:raise SystemExit("非种子资产不能自动迁移："+row["id"])
            rel=Path(*parts[matches[-1]+1:])
            dest=(store.DATA/"inputs"/rel).resolve()
            if not dest.is_relative_to((store.DATA/"inputs"/row["project_id"]).resolve()):raise SystemExit("资产路径越界")
            if not dest.is_file() or store.digest_file(dest)!=row["sha256"]:raise SystemExit("资产哈希不匹配："+row["id"])
            if str(dest)!=ref:changes.append((row["id"],str(dest)))
        if args.apply:
            for aid,dest in changes:c.execute("UPDATE assets SET storage_ref=? WHERE id=?",(dest,aid))
    print(("已迁移" if args.apply else "待迁移"),len(changes),"项；哈希核对通过",len(rows),"项")


if __name__=="__main__":main()
