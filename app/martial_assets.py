"""Import source-verified teacher images into the current character version.

The manifest was audited against the local character source directory. Uploads
must match its exact path, size, dimensions and SHA-256. Attaching a missing
image to an already locked version is provenance, not a character redesign.
"""
from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path

import store

MANIFEST = Path(__file__).resolve().parents[1] / "registry" / "martial_asset_manifest.json"
FIELDS = {"full_body_hero": "portrait", "front_view": "front_view",
          "side_view": "side_view", "back_view": "back_view"}


def ensure_schema(c):
    c.executescript("""
    CREATE TABLE IF NOT EXISTS martial_master_asset_imports(
      id TEXT PRIMARY KEY, manifest_path TEXT NOT NULL UNIQUE,
      master_id TEXT REFERENCES martial_masters(id), master_version INTEGER,
      role TEXT NOT NULL, field TEXT, asset_id TEXT NOT NULL REFERENCES assets(id),
      source_path TEXT NOT NULL, sha256 TEXT NOT NULL, width INTEGER NOT NULL,
      height INTEGER NOT NULL, attached INTEGER NOT NULL DEFAULT 0,
      ignored INTEGER NOT NULL DEFAULT 0, imported_at TEXT NOT NULL,
      attached_at TEXT);
    CREATE INDEX IF NOT EXISTS idx_martial_import_master
      ON martial_master_asset_imports(master_id,master_version);
    """)


def _entries():
    source=json.loads(MANIFEST.read_text(encoding="utf-8"))
    if source.get("schema_version")!=1 or len(source.get("images",[]))!=59:
        raise ValueError("角色素材清单版本或数量不符")
    return {item["relative_path"]:item for item in source["images"]}


def _dimensions(content: bytes) -> tuple[int,int]:
    if len(content)<24 or content[:8]!=b"\x89PNG\r\n\x1a\n" or content[12:16]!=b"IHDR":
        raise ValueError("角色素材必须是可识别 PNG")
    return int.from_bytes(content[16:20],"big"),int.from_bytes(content[20:24],"big")


def import_asset(user: dict, data: dict) -> dict:
    if user["role"] not in {"founder","manager"}:
        raise PermissionError("历史角色资产初始化需要负责人身份")
    relative=str(data.get("relative_path") or "")
    entry=_entries().get(relative)
    if not entry:raise ValueError("素材不在已核对的角色清单中")
    with store.connect() as c:
        ensure_schema(c)
        old=c.execute("SELECT * FROM martial_master_asset_imports WHERE manifest_path=?",(relative,)).fetchone()
        if old:return {"id":old["id"],"asset_id":old["asset_id"],"attached":bool(old["attached"]),"reused":True}
        master_id=entry.get("master_id")
        if master_id:
            master=c.execute("SELECT current_version FROM martial_masters WHERE id=?",(master_id,)).fetchone()
            if not master:raise ValueError("清单包含未知老师")
            version=2 if master_id=="cryn" and master["current_version"]>=3 else master["current_version"]
            row=c.execute("SELECT status FROM martial_master_versions WHERE master_id=? AND version=?",(master_id,version)).fetchone()
            if not row or row["status"]!="locked":raise ValueError("老师人物事实尚未锁定")
    try:content=base64.b64decode((data.get("upload") or {}).get("base64") or "",validate=True)
    except (TypeError,ValueError):raise ValueError("图片数据无效")
    if len(content)!=entry["size_bytes"] or len(content)>40_000_000:
        raise ValueError("图片大小与已核对清单不符")
    digest=hashlib.sha256(content).hexdigest()
    if digest!=entry["sha256"] or _dimensions(content)!=(entry["width"],entry["height"]):
        raise ValueError("图片内容与已核对清单不符")
    with store.connect() as c:
        duplicate=c.execute("SELECT id FROM assets WHERE project_id='wuxiang' AND status='active' AND type IN ('image','character') AND sha256=? ORDER BY created_at LIMIT 1",(digest,)).fetchone()
    if duplicate:asset_id=duplicate["id"]
    else:
        asset=store.register_submission_asset("wuxiang",Path(relative).name,content,user["id"])
        asset_id=asset["id"]
    master_id=entry.get("master_id")
    role=entry["role"]
    field=FIELDS.get(role)
    source_path=str(Path(json.loads(MANIFEST.read_text(encoding="utf-8"))["source_root"])/relative)
    with store.connect() as c:
        ensure_schema(c)
        # A duplicate retry after upload must use the first recorded import.
        old=c.execute("SELECT * FROM martial_master_asset_imports WHERE manifest_path=?",(relative,)).fetchone()
        if old:return {"id":old["id"],"asset_id":old["asset_id"],"attached":bool(old["attached"]),"reused":True}
        version=None;attached=False
        if master_id:
            master=c.execute("SELECT current_version FROM martial_masters WHERE id=?",(master_id,)).fetchone()
            if not master:raise ValueError("清单包含未知老师")
            # P0 Cryn V3 uses a distinct locked production image. Desktop
            # reference sheets belong to historical V2; never mix their wing
            # and costume details into the current V3 generation lock.
            version=2 if master_id=="cryn" and master["current_version"]>=3 else master["current_version"]
            row=c.execute("SELECT payload,status FROM martial_master_versions WHERE master_id=? AND version=?",(master_id,version)).fetchone()
            if not row or row["status"]!="locked":raise ValueError("老师当前人物事实尚未锁定")
            if field:
                payload=store.parse(row["payload"],{})
                if not payload.get(field):
                    payload[field]=asset_id
                    c.execute("UPDATE martial_master_versions SET payload=? WHERE master_id=? AND version=?",
                              (store.dumps(payload),master_id,version))
                    attached=True
                elif payload[field]==asset_id:attached=True
        item_id="mai_"+digest[:12]+"_"+hashlib.sha256(relative.encode()).hexdigest()[:8]
        stamp=store.now()
        c.execute("INSERT INTO martial_master_asset_imports(id,manifest_path,master_id,master_version,role,field,asset_id,source_path,sha256,width,height,attached,imported_at,attached_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                  (item_id,relative,master_id,version,role,field,asset_id,source_path,digest,entry["width"],entry["height"],int(attached),stamp,stamp if attached else None))
        refs=store.parse(c.execute("SELECT source_refs FROM assets WHERE id=?",(asset_id,)).fetchone()[0],[]) or []
        if source_path not in refs:
            refs.append(source_path)
            c.execute("UPDATE assets SET source_refs=? WHERE id=?",(store.dumps(refs),asset_id))
        store.audit(c,user["id"],"martial.master.asset_import",None,{"master_id":master_id,"version":version,"role":role,"asset_id":asset_id,"attached":attached})
        return {"id":item_id,"asset_id":asset_id,"attached":attached,"reused":bool(duplicate)}


def master_imports(c, master_id: str) -> list[dict]:
    ensure_schema(c)
    return [dict(r) for r in c.execute("SELECT id,role,field,asset_id,sha256,width,height,source_path,attached,imported_at,attached_at,master_version FROM martial_master_asset_imports WHERE master_id=? ORDER BY role,source_path",(master_id,))]


def _can_map(user: dict) -> bool:
    if user["role"] in {"founder","manager"}:return True
    if user["role"]!="employee":return False
    with store.connect() as c:
        return c.execute("SELECT 1 FROM martial_specialists WHERE user_id=? AND project_id='wuxiang' AND active=1",(user["id"],)).fetchone() is not None


def unmapped(user: dict) -> dict:
    if not _can_map(user):raise PermissionError("没有角色素材权限")
    with store.connect() as c:
        ensure_schema(c)
        items=[dict(r) for r in c.execute("SELECT id,role,asset_id,source_path,sha256,width,height,ignored FROM martial_master_asset_imports WHERE master_id IS NULL ORDER BY imported_at")]
    return {"count":sum(not item["ignored"] for item in items),"assets":items}


def assign_unmapped(user: dict, item_id: str, master_id: str | None, ignore=False) -> dict:
    if not _can_map(user):raise PermissionError("没有角色素材权限")
    with store.connect() as c:
        ensure_schema(c)
        item=c.execute("SELECT * FROM martial_master_asset_imports WHERE id=?",(item_id,)).fetchone()
        if not item or item["master_id"] or item["ignored"]:raise ValueError("素材已处理或不存在")
        if ignore:
            c.execute("UPDATE martial_master_asset_imports SET ignored=1 WHERE id=?",(item_id,))
            return {"ignored":True}
        master=c.execute("SELECT current_version FROM martial_masters WHERE id=?",(master_id,)).fetchone()
        if not master:raise ValueError("老师不存在")
        c.execute("UPDATE martial_master_asset_imports SET master_id=?,master_version=? WHERE id=?",(master_id,master[0],item_id))
        store.audit(c,user["id"],"martial.master.asset_assign",None,{"master_id":master_id,"asset_id":item["asset_id"]})
        return {"master_id":master_id,"asset_id":item["asset_id"]}
