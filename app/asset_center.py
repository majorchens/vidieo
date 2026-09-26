"""Unified, reference-only asset catalogue for Work OS and the legacy AI studio.

The registry contains metadata and source links. It never copies an existing
asset or reads provider credentials. Original systems keep their authority.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import sqlite3
from collections import Counter
from pathlib import Path

import store

REGISTRY_ID = re.compile(r"uar_[a-f0-9]{16}\Z")
SAFE_TEXT = re.compile(r"(?:api[_-]?key|password|passwd|secret|access[_-]?token|authorization)\s*[:=]\s*\S+|-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----", re.I)
BAD_NAME = re.compile(r"(?:^|[\\/\s._-])(?:auth|tokens?|secrets?|credentials?|password|passwd|api[-_\s]?key|env)(?=$|[\\/\s._-])", re.I)
LEGACY_ROOT = Path(os.environ.get("YOODUN_LEGACY_DATA_DIR", "/var/lib/wujing-ai-studio")).resolve()
MAX_LEGACY_HASH_BYTES = 16 * 1024 * 1024
CATEGORIES = {"角色", "真人动作", "AI图片", "AI视频", "教学视频", "演练视频",
              "Voice", "OS语音", "BGM", "音效", "Prompt", "文档", "场景", "道具",
              "图片", "视频", "音频", "其他"}
REUSE_ACTIONS = {"new_task", "ai_creation", "character_reference", "video_reference", "save_project", "associate"}
CAMPAIGN_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,79}\Z")


def initialize() -> None:
    with store.connect() as c:
        c.execute("INSERT OR IGNORE INTO workflows(id,project_id,name,description,version,input_contract,qc_contract) VALUES(?,?,?,?,?,?,?)",
                  ("WF-AI",None,"素材复用任务","从已有数字资产继续制作","0.1","{}","{}"))
        c.executescript("""
        CREATE TABLE IF NOT EXISTS asset_registry(
            asset_id TEXT PRIMARY KEY,
            source_system TEXT NOT NULL,
            original_id TEXT NOT NULL,
            project_id TEXT,
            type TEXT NOT NULL,
            subtype TEXT NOT NULL DEFAULT '',
            name TEXT NOT NULL,
            file_ref TEXT NOT NULL,
            sha256 TEXT,
            access_scope TEXT NOT NULL DEFAULT '',
            creator TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL,
            model TEXT NOT NULL DEFAULT '',
            provider TEXT NOT NULL DEFAULT '',
            prompt_ref TEXT NOT NULL DEFAULT '',
            task_ref TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'active',
            version INTEGER NOT NULL DEFAULT 1,
            metadata TEXT NOT NULL DEFAULT '{}',
            updated_at TEXT NOT NULL
        );
        CREATE UNIQUE INDEX IF NOT EXISTS idx_asset_registry_source ON asset_registry(source_system,original_id);
        CREATE INDEX IF NOT EXISTS idx_asset_registry_project ON asset_registry(project_id,type,created_at);
        CREATE TABLE IF NOT EXISTS asset_registry_sources(
            source_system TEXT NOT NULL,
            original_id TEXT NOT NULL,
            asset_id TEXT NOT NULL REFERENCES asset_registry(asset_id),
            file_ref TEXT NOT NULL,
            sha256 TEXT,
            PRIMARY KEY(source_system,original_id)
        );
        CREATE TABLE IF NOT EXISTS asset_registry_links(
            id TEXT PRIMARY KEY,
            asset_id TEXT NOT NULL REFERENCES asset_registry(asset_id),
            action TEXT NOT NULL,
            project_id TEXT,
            art_id TEXT,
            master_id TEXT,
            move_id TEXT,
            task_id TEXT,
            campaign_id TEXT,
            created_by TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_asset_registry_links ON asset_registry_links(asset_id,project_id);
        """)
        columns={r[1] for r in c.execute("PRAGMA table_info(asset_registry)")}
        if "access_scope" not in columns:
            c.execute("ALTER TABLE asset_registry ADD COLUMN access_scope TEXT NOT NULL DEFAULT ''")
        link_columns={r[1] for r in c.execute("PRAGMA table_info(asset_registry_links)")}
        if "campaign_id" not in link_columns:
            c.execute("ALTER TABLE asset_registry_links ADD COLUMN campaign_id TEXT")
        c.execute("DROP INDEX IF EXISTS idx_asset_registry_hash")
        c.execute("CREATE INDEX idx_asset_registry_hash ON asset_registry(sha256,project_id,type,access_scope)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_asset_registry_links_context ON asset_registry_links(project_id,art_id,master_id,move_id,task_id,campaign_id)")


def _access_scope(item: dict) -> str:
    source=str(item["source_system"])
    meta=item.get("metadata") or {}
    if source=="work_os":return "work_os_project"
    if source=="work_os_ai_studio":return "work_os_private:"+str(meta.get("owner_id") or item.get("creator") or "")
    if source.startswith("legacy_ai"):
        if meta.get("visibility")=="team":return "legacy_team"
        return "legacy_private:"+str(meta.get("owner_username") or "unknown")
    return source+":"+str(item["original_id"])


def _digest(path: Path, max_bytes: int | None = None) -> str | None:
    try:
        if not path.is_file():
            return None
        if max_bytes is not None and path.stat().st_size > max_bytes:
            return None
        return store.digest_file(path)
    except (OSError, PermissionError):
        return None


def _register(c: sqlite3.Connection, item: dict) -> tuple[str, str]:
    """Return registry ID and inserted/updated/deduplicated outcome."""
    source = str(item["source_system"])
    original = str(item["original_id"])
    if not source or not original:
        raise ValueError("素材来源缺失")
    sha = item.get("sha256") or None
    scope = _access_scope(item)
    if sha and not re.fullmatch(r"[a-f0-9]{64}", str(sha)):
        raise ValueError("素材 SHA-256 无效")
    existing = c.execute("SELECT asset_id FROM asset_registry_sources WHERE source_system=? AND original_id=?", (source, original)).fetchone()
    if existing:
        canonical=c.execute("SELECT source_system,original_id,metadata FROM asset_registry WHERE asset_id=?",(existing[0],)).fetchone()
        if canonical and canonical[0]==source and canonical[1]==original:
            metadata=dict(item.get("metadata") or {})
            saved_tags=(store.parse(canonical["metadata"],{}) or {}).get("tags")
            if saved_tags and "tags" not in metadata:metadata["tags"]=saved_tags
            c.execute("""UPDATE asset_registry SET project_id=?,type=?,subtype=?,name=?,file_ref=?,sha256=?,access_scope=?,
                         creator=?,model=?,provider=?,prompt_ref=?,task_ref=?,status=?,version=?,metadata=?,updated_at=?
                         WHERE asset_id=?""",
                      (item.get("project_id"),item["type"],item.get("subtype") or "",str(item.get("name") or "未命名素材")[:180],
                       item.get("file_ref") or "",sha,scope,item.get("creator") or "",item.get("model") or "",item.get("provider") or "",
                       item.get("prompt_ref") or "",item.get("task_ref") or "",item.get("status") or "active",
                       int(item.get("version") or 1),store.dumps(metadata),store.now(),existing[0]))
        return existing[0], "existing"
    # A digest is only a deduplication key within the same business context.
    # This avoids merging private output with a team asset from another project.
    duplicate = c.execute("SELECT asset_id,subtype,metadata FROM asset_registry WHERE sha256=? AND project_id IS ? AND type=? AND access_scope=? LIMIT 1",
                          (sha, item.get("project_id"), item["type"],scope)).fetchone() if sha else None
    if duplicate:
        asset_id = duplicate["asset_id"]
        if item.get("subtype") and not duplicate["subtype"]:
            meta=store.parse(duplicate["metadata"],{}) or {}
            meta.update(item.get("metadata") or {})
            c.execute("UPDATE asset_registry SET subtype=?,metadata=?,updated_at=? WHERE asset_id=?",
                      (item["subtype"],store.dumps(meta),store.now(),asset_id))
        outcome = "deduplicated"
    else:
        asset_id = "uar_" + secrets.token_hex(8)
        c.execute("""INSERT INTO asset_registry(asset_id,source_system,original_id,project_id,type,subtype,name,file_ref,
                     sha256,access_scope,creator,created_at,model,provider,prompt_ref,task_ref,status,version,metadata,updated_at)
                     VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                  (asset_id,source,original,item.get("project_id"),item["type"],item.get("subtype") or "",
                   str(item.get("name") or "未命名素材")[:180],item.get("file_ref") or "",sha,scope,
                   item.get("creator") or "",item.get("created_at") or store.now(),item.get("model") or "",
                   item.get("provider") or "",item.get("prompt_ref") or "",item.get("task_ref") or "",
                   item.get("status") or "active",int(item.get("version") or 1),store.dumps(item.get("metadata") or {}),store.now()))
        outcome = "imported"
    c.execute("INSERT INTO asset_registry_sources(source_system,original_id,asset_id,file_ref,sha256) VALUES(?,?,?,?,?)",
              (source,original,asset_id,item.get("file_ref") or "",sha))
    return asset_id,outcome


def _source_meta(c, asset_id: str) -> tuple[str | None, str | None, str | None, dict]:
    master_id = move_id = art_id = None
    production={}
    if c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='martial_master_versions'").fetchone():
        row=c.execute("""SELECT v.master_id,json_extract(v.payload,'$.voice_preview') AS voice_preview,
                         json_extract(v.payload,'$.costume') AS costume,
                         json_extract(v.payload,'$.digital_model') AS digital_model
                         FROM martial_master_versions v JOIN martial_masters m
                         ON m.id=v.master_id AND m.current_version=v.version
                         WHERE json_extract(v.payload,'$.portrait')=? OR json_extract(v.payload,'$.front_view')=?
                         OR json_extract(v.payload,'$.side_view')=? OR json_extract(v.payload,'$.back_view')=?
                         OR json_extract(v.payload,'$.turnaround')=? OR json_extract(v.payload,'$.voice_preview')=?
                         OR json_extract(v.payload,'$.costume')=? OR json_extract(v.payload,'$.digital_model')=?
                         LIMIT 1""",(asset_id,)*8).fetchone()
        if row:
            master_id=row["master_id"]
            if row["voice_preview"]==asset_id:production["role"]="voice_preview"
            elif row["costume"]==asset_id:production["role"]="costume"
            elif row["digital_model"]==asset_id:production["role"]="digital_model"
    if c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='martial_motion_refs'").fetchone():
        row=c.execute("SELECT move_id,cover_asset_id,duration FROM martial_motion_refs WHERE video_asset_id=? ORDER BY created_at DESC LIMIT 1",(asset_id,)).fetchone()
        if row:
            move_id=row["move_id"]
            production={"work_kind":"motion_reference","cover_asset_id":row["cover_asset_id"],
                        "duration_ms":int(float(row["duration"] or 0)*1000)}
    if c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='martial_media_jobs'").fetchone():
        media_columns={r[1] for r in c.execute("PRAGMA table_info(martial_media_jobs)")}
        asset_type_column="j.asset_type" if "asset_type" in media_columns else "NULL"
        row=c.execute(f"""SELECT j.move_id,j.provider,j.model,j.provider_job_id,j.duration,t.assignee_id,
                         {asset_type_column} AS asset_type
                         FROM martial_media_jobs j JOIN tasks t ON t.id=j.task_id
                         WHERE j.candidate_asset_id=? ORDER BY j.created_at DESC LIMIT 1""",(asset_id,)).fetchone()
        if row:
            move_id=row["move_id"]
            production={"work_kind":"candidate","provider":row["provider"],"model":row["model"],
                        "provider_job_id":row["provider_job_id"],"duration_ms":row["duration"]*1000,
                        "owner_id":row["assignee_id"]}
            if row["asset_type"] in {"teaching","practice"}:
                production["role"]=row["asset_type"]+"_video"
    if c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='martial_final_assets'").fetchone():
        row=c.execute("SELECT move_id,status FROM martial_final_assets WHERE asset_id=? ORDER BY created_at DESC LIMIT 1",(asset_id,)).fetchone()
        if row:move_id=row["move_id"];production["work_kind"]="final";production["final_status"]=row["status"]
    if c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='martial_mm_asset_links'").fetchone():
        for link in c.execute("""SELECT scope,scope_id,role FROM martial_mm_asset_links
                               WHERE asset_id=? AND status='active' ORDER BY created_at""",(asset_id,)):
            role=link["role"]
            production.setdefault("asset_roles",[]).append(role)
            production.setdefault("role",role)
            if link["scope"]=="art" and not art_id:art_id=link["scope_id"]
            if link["scope"]=="master" and not master_id:master_id=link["scope_id"]
            if link["scope"]=="move" and not move_id:move_id=link["scope_id"]
    if c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='martial_mm_tts_assets'").fetchone():
        row=c.execute("""SELECT move_id,kind,language FROM martial_mm_tts_assets
                         WHERE asset_id=? AND status='active' ORDER BY created_at DESC LIMIT 1""",(asset_id,)).fetchone()
        if row:
            move_id=row["move_id"]
            production["role"]="os_audio"
            production["os_kind"]=row["kind"]
            production["language"]=row["language"]
    if move_id:
        row=c.execute("SELECT martial_art_id FROM martial_moves WHERE id=?",(move_id,)).fetchone()
        if row: art_id=row[0]
    elif master_id:
        row=c.execute("SELECT id FROM martial_arts WHERE master_id=? ORDER BY created_at LIMIT 1",(master_id,)).fetchone()
        if row: art_id=row[0]
    return art_id, master_id, move_id, production


def sync_internal() -> dict:
    """Index current Work OS assets and completed AI Studio results idempotently."""
    counts=Counter()
    with store.connect() as c:
        for row in c.execute("SELECT * FROM assets ORDER BY created_at").fetchall():
            a=dict(row)
            if BAD_NAME.search(a["name"]): counts["safety_filtered"]+=1; continue
            art_id,master_id,move_id,production=_source_meta(c,a["id"])
            subtype=("martial_motion" if production.get("work_kind")=="motion_reference" or a["type"]=="motion_reference" else
                     production["role"] if production.get("role") else
                     "digital_teacher_video" if production.get("work_kind") in {"candidate","final"} and a["type"]=="video" else
                     "character" if a["type"]=="character" else "")
            item={"source_system":"work_os","original_id":a["id"],"project_id":a["project_id"],
                  "type":a["type"],"subtype":subtype,"name":a["name"],"file_ref":"work-os://"+a["id"],
                  "sha256":a["sha256"],"creator":a["created_by"],"created_at":a["created_at"],
                  "model":production.get("model"),"provider":production.get("provider"),
                  "status":a["status"],"version":a["version"],"metadata":{"art_id":art_id,"master_id":master_id,
                  "move_id":move_id,"source_refs":store.parse(a["source_refs"],[]),**production}}
            _,outcome=_register(c,item);counts[outcome]+=1
        if c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='ai_studio_artifacts'").fetchone():
            for row in c.execute("SELECT * FROM ai_studio_artifacts WHERE status='completed' ORDER BY created_at").fetchall():
                a=dict(row)
                output=store.parse(a.get("output"),{})
                if not isinstance(output,dict): output={"text":str(output)}
                name=str(output.get("title") or {"write":"文案","ideas":"创意","analyze":"分析","assistant":"AI 助手","image":"图片","video":"视频"}.get(a["capability"],"AI 创作成果"))
                if BAD_NAME.search(name): counts["safety_filtered"]+=1;continue
                sha=None
                if a["asset_id"]:
                    found=c.execute("SELECT sha256 FROM assets WHERE id=?",(a["asset_id"],)).fetchone()
                    if found:sha=found[0]
                if not sha and a["type"] in {"text","analysis","concept"}:
                    value=output.get("text") or store.dumps(output)
                    if SAFE_TEXT.search(value):counts["safety_filtered"]+=1;continue
                    sha=hashlib.sha256(value.encode()).hexdigest()
                if not sha and a["file_ref"]:
                    p=Path(a["file_ref"])
                    if p.is_file() and p.resolve().is_relative_to((store.DATA/"ai_studio"/"results").resolve()):sha=_digest(p)
                item={"source_system":"work_os_ai_studio","original_id":a["id"],"project_id":a["project_id"],
                      "type":a["type"],"subtype":a["capability"],"name":name,"file_ref":"ai-studio://"+a["id"],
                      "sha256":sha,"creator":a["user_id"],"created_at":a["created_at"],"model":a["model"],
                      "provider":a["provider"],"prompt_ref":a["id"],"task_ref":a["task_id"],"status":a["status"],
                      "metadata":{"owner_id":a["user_id"],"asset_id":a["asset_id"],"capability":a["capability"]}}
                _,outcome=_register(c,item);counts[outcome]+=1
    return dict(counts)


def _category(row: dict) -> str:
    subtype=str(row.get("subtype") or "").lower()
    typ=str(row.get("type") or "").lower()
    meta=store.parse(row.get("metadata"),{}) or {}
    role=str(meta.get("role") or "").lower()
    source=str(row.get("source_system") or "")
    if subtype in {"martial_motion","motion_reference"} or typ=="motion_reference":return "真人动作"
    if subtype in {"os_audio","os_voice","tts"} or role=="os_audio":return "OS语音"
    if subtype in {"voice","voice_persona","voice_preview","voice_audition","voice_reference"} or role in {"voice_preview","voice_audition","voice_reference","intro_audio"}:return "Voice"
    if subtype in {"bgm","theme_music","training_bgm"} or role in {"theme_music","training_bgm"}:return "BGM"
    if subtype in {"sound_effect","sfx","ui_sound"} or role in {"sound_effect","sfx","ui_sound"}:return "音效"
    if subtype=="teaching_video" or role=="teaching_video":return "教学视频"
    if subtype=="practice_video" or role=="practice_video":return "演练视频"
    if subtype in {"scene","scene_reference"}:return "场景"
    if subtype in {"prop","prop_reference"}:return "道具"
    if subtype in {"character","master","character_reference"} or typ=="character":return "角色"
    if typ=="image" and (subtype in {"ai_result","ai_image"} or source=="work_os_ai_studio" or row.get("model")):return "AI图片"
    if typ=="video" and (subtype in {"ai_result","ai_video","digital_teacher_video"} or source=="work_os_ai_studio" or row.get("model")):return "AI视频"
    if typ=="prompt":return "Prompt"
    if typ=="image":return "图片"
    if typ=="video":return "视频"
    if typ=="audio":return "音频"
    if typ in {"document","script","text","analysis","concept"}:return "文档"
    return "其他"


def _category_matches(row: dict, requested: str) -> bool:
    if requested=="数字老师视频":
        return row.get("subtype")=="digital_teacher_video" or _category(row) in {"教学视频","演练视频"}
    return _category(row)==requested


def _links_for_user(c: sqlite3.Connection, asset_id: str, user: dict) -> list[dict]:
    links=[dict(r) for r in c.execute("""SELECT action,project_id,art_id,master_id,move_id,task_id,campaign_id,created_at
                                       FROM asset_registry_links WHERE asset_id=? ORDER BY created_at DESC""",(asset_id,))]
    if user["role"] in {"manager","founder"}:return links
    return [link for link in links if link["project_id"] and _project_allowed(c,user,link["project_id"])]


def _associations(c: sqlite3.Connection, row: dict, user: dict, meta: dict, links: list[dict]) -> dict:
    ids={key:set() for key in ("project_id","art_id","master_id","move_id","task_id","campaign_id","lesson_id","shot_id")}
    source_project=row.get("project_id")
    source_visible=bool(source_project and _project_allowed(c,user,source_project))
    if source_visible:
        ids["project_id"].add(source_project)
        for key in ("art_id","master_id","move_id"):
            if meta.get(key):ids[key].add(str(meta[key]))
        task_ref=row.get("task_ref")
        if task_ref and c.execute("SELECT 1 FROM tasks WHERE id=? AND project_id=?",(task_ref,source_project)).fetchone():
            ids["task_id"].add(task_ref)
    for link in links:
        for key in ids:
            if link.get(key):ids[key].add(str(link[key]))
    if source_visible and c.execute("SELECT 1 FROM sqlite_master WHERE name='martial_lesson_asset_versions'").fetchone():
        for linked in c.execute("""SELECT lesson_id,shot_id FROM martial_lesson_asset_versions
             WHERE registry_asset_id=? AND status='active'""",(row["asset_id"],)):
            ids["lesson_id"].add(linked["lesson_id"])
            if linked["shot_id"]:ids["shot_id"].add(linked["shot_id"])
    return {key+"s":sorted(values) for key,values in ids.items()}


def _visible(c, row: dict, user: dict) -> bool:
    if user["role"] in {"manager","founder"}:return True
    if row["source_system"].startswith("legacy_ai"):
        import legacy_asset_bridge
        return legacy_asset_bridge.can_access(c,row,user)
    meta=store.parse(row["metadata"],{}) or {}
    if row["source_system"]=="work_os_ai_studio":return meta.get("owner_id")==user["id"]
    if row["project_id"]:
        if _project_allowed(c,user,row["project_id"]):return True
    for link in c.execute("SELECT DISTINCT project_id FROM asset_registry_links WHERE asset_id=?",(row["asset_id"],)):
        if link[0] and _project_allowed(c,user,link[0]):return True
    return False


def _project_allowed(c, user: dict, project_id: str) -> bool:
    if user["role"] in {"manager","founder"}:return True
    if c.execute("SELECT 1 FROM tasks WHERE project_id=? AND assignee_id=? LIMIT 1",(project_id,user["id"])).fetchone():return True
    if project_id=="wuxiang" and c.execute("SELECT 1 FROM sqlite_master WHERE name='martial_specialists'").fetchone():
        return bool(c.execute("SELECT 1 FROM martial_specialists WHERE user_id=? AND project_id='wuxiang' AND active=1",(user["id"],)).fetchone())
    return False


def _public(c, row: dict, user: dict, advanced=False) -> dict:
    meta=store.parse(row["metadata"],{}) or {}
    aid=row["asset_id"]
    links=_links_for_user(c,aid,user)
    associations=_associations(c,row,user,meta,links)
    out={key:row[key] for key in ("asset_id","source_system","original_id","project_id","type","subtype","name","file_ref","sha256",
                                   "creator","created_at","model","provider","prompt_ref","task_ref","status","version")}
    out["project_id"]=row["project_id"] if row["project_id"] in associations["project_ids"] else (associations["project_ids"][0] if associations["project_ids"] else None)
    out["project"]=out["project_id"]
    out["projects"]=associations["project_ids"]
    out["associations"]=associations
    out["hash"]=out["sha256"]
    out["category"]=_category(row)
    for key in ("art_id","master_id","move_id","campaign_id","lesson_id","shot_id"):
        values=associations[key+"s"]
        out[key]=str(meta[key]) if key in meta and str(meta[key]) in values else (values[0] if values else None)
    out["usage"]=meta.get("usage_type") or meta.get("usage") or ""
    out["tags"]=meta.get("tags") or []
    out["duration_ms"]=meta.get("duration_ms")
    out["work_kind"]=meta.get("work_kind")
    out["final_status"]=meta.get("final_status")
    out["is_my_work"]=row["creator"]==user["id"] or meta.get("owner_id")==user["id"]
    if not out["is_my_work"] and out["work_kind"] in {"candidate","final"}:
        out["is_my_work"]=c.execute("""SELECT 1 FROM asset_registry_sources s
            JOIN martial_media_jobs j ON j.candidate_asset_id=s.original_id
            JOIN tasks t ON t.id=j.task_id
            WHERE s.asset_id=? AND s.source_system='work_os' AND t.assignee_id=? LIMIT 1""",
            (aid,user["id"])).fetchone() is not None
    out["thumbnail_url"]=None
    out["preview_url"]=None
    # The old centre's /api/assets/{id}/content is not a Work OS route.
    # Until a verified absolute legacy URL is configured, do not offer a dead
    # relative link or claim that a private OSS object can be opened here.
    out["source_url"]=None
    if row["status"] in {"active","completed","succeeded"} and row["file_ref"].startswith("work-os://"):
        original=row["file_ref"].split("://",1)[1]
        local=c.execute("SELECT storage_ref FROM assets WHERE id=?",(original,)).fetchone()
        if local and not local[0].startswith(("https://","studio://")) and Path(local[0]).is_file():
            out["preview_url"]=f"/api/asset-center/{aid}/media"
            if out["type"] in {"image","character"}:out["thumbnail_url"]=out["preview_url"]
            if meta.get("cover_asset_id"):out["thumbnail_url"]=f"/api/assets/{meta['cover_asset_id']}/download?inline=1"
    elif row["status"] in {"active","completed","succeeded"} and row["file_ref"].startswith("ai-studio://") and row["type"]=="video":
        out["preview_url"]=f"/api/ai-studio/artifacts/{row['original_id']}/download"
    elif row["status"] in {"active","completed","succeeded"} and row["file_ref"].startswith("legacy-job://") and meta.get("local_file_available"):
        out["preview_url"]=f"/api/asset-center/{aid}/media"
        if out["type"]=="image":out["thumbnail_url"]=out["preview_url"]
    if row["source_system"].startswith("legacy_ai"):
        import legacy_asset_bridge
        bridge=legacy_asset_bridge.descriptor(user,aid)
        if bridge["preview_url"]:
            out["preview_url"]=bridge["preview_url"]
            if out["type"]=="image":out["thumbnail_url"]=out["preview_url"]
        out["can_select"]=bridge["can_select"]
        out["requires_old_login"]=bridge["requires_old_login"]
    if user["role"]=="employee":
        for key in ("model","provider","prompt_ref","file_ref","hash","sha256","original_id"):
            out.pop(key,None)
    if advanced:
        if user["role"]=="employee":
            out["metadata"]={k:meta[k] for k in ("usage_type","tags","duration_ms","size_bytes","work_kind") if k in meta}
        else:
            out["metadata"]={k:v for k,v in meta.items() if k not in {"prompt_text","local_path","object_key","bucket","oss_profile_id","owner_id"}}
        if row["type"]=="prompt" and _visible(c,row,user):out["text"]=meta.get("prompt_text")
        if user["role"]!="employee":
            out["sources"]=[dict(r) for r in c.execute("SELECT source_system,original_id FROM asset_registry_sources WHERE asset_id=?",(aid,))]
            out["links"]=links
    return out


def list_assets(user: dict, filters: dict | None = None) -> dict:
    sync_internal()
    filters=filters or {}
    try:
        offset=int(filters.get("offset") or 0)
        limit=int(filters.get("limit") or 2000)
    except (TypeError,ValueError):raise ValueError("素材分页参数无效")
    if offset<0 or not 1<=limit<=2000:raise ValueError("素材分页参数无效")
    with store.connect() as c:
        rows=[dict(r) for r in c.execute("SELECT * FROM asset_registry ORDER BY created_at DESC")]
        rows=[r for r in rows if _visible(c,r,user)]
        categories=Counter(_category(r) for r in rows)
        project_names={r["id"]:r["name"] for r in c.execute("SELECT id,name FROM projects")}
        tables={r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        art_names={r["id"]:r["chinese_name"] for r in c.execute("SELECT id,chinese_name FROM martial_arts")} if "martial_arts" in tables else {}
        master_names={r["id"]:r["name"] for r in c.execute("SELECT id,name FROM martial_masters")} if "martial_masters" in tables else {}
        move_names={r["id"]:r["name"] for r in c.execute("""SELECT m.id,
            COALESCE(json_extract(v.payload,'$.chinese_name'),'') AS name FROM martial_moves m
            LEFT JOIN martial_move_versions v ON v.move_id=m.id AND v.version=m.current_version""")} if "martial_moves" in tables else {}
        def matches(r):
            meta=store.parse(r["metadata"],{}) or {}
            links=_links_for_user(c,r["asset_id"],user)
            associations=_associations(c,r,user,meta,links)
            for key,column in (("type","type"),("status","status"),("creator","creator"),
                               ("model","model"),("provider","provider")):
                if filters.get(key) and str(r[column])!=str(filters[key]):return False
            for key in ("project_id","art_id","master_id","move_id","task_id","campaign_id","lesson_id","shot_id"):
                if filters.get(key) and str(filters[key]) not in associations[key+"s"]:return False
            if filters.get("category") and not _category_matches(r,str(filters["category"])):return False
            if filters.get("usage") and str(meta.get("usage") or meta.get("usage_type") or "")!=str(filters["usage"]):return False
            if filters.get("date_from") and r["created_at"][:10]<str(filters["date_from"]):return False
            if filters.get("date_to") and r["created_at"][:10]>str(filters["date_to"]):return False
            if filters.get("tags") and str(filters["tags"]).lower() not in " ".join(meta.get("tags") or []).lower():return False
            query=str(filters.get("q") or "").strip().lower()
            if query and query not in " ".join(str(x or "") for x in (
                r["name"],meta.get("tags"),
                *associations["project_ids"],*(project_names.get(x) for x in associations["project_ids"]),
                *associations["art_ids"],*(art_names.get(x) for x in associations["art_ids"]),
                *associations["master_ids"],*(master_names.get(x) for x in associations["master_ids"]),
                *associations["move_ids"],*(move_names.get(x) for x in associations["move_ids"]),
                *associations["campaign_ids"])).lower():return False
            return True
        selected=[r for r in rows if matches(r)]
        page=selected[offset:offset+limit]
        return {"assets":[_public(c,r,user) for r in page],"total":len(selected),
                "next_offset":offset+len(page) if offset+len(page)<len(selected) else None,
                "facets":{"categories":dict(categories)}}


def detail(user: dict, asset_id: str) -> dict:
    if not REGISTRY_ID.fullmatch(asset_id):raise ValueError("素材编号无效")
    sync_internal()
    with store.connect() as c:
        row=c.execute("SELECT * FROM asset_registry WHERE asset_id=?",(asset_id,)).fetchone()
        if not row:raise KeyError(asset_id)
        if not _visible(c,dict(row),user):raise PermissionError("没有此素材的访问权限")
        return {"asset":_public(c,dict(row),user,True)}


def media_path(user: dict, asset_id: str) -> Path:
    if not REGISTRY_ID.fullmatch(asset_id):raise ValueError("素材编号无效")
    with store.connect() as c:
        row=c.execute("SELECT * FROM asset_registry WHERE asset_id=?",(asset_id,)).fetchone()
        if not row:raise KeyError(asset_id)
        item=dict(row)
        if not _visible(c,item,user):raise PermissionError("没有此素材的访问权限")
        if item["file_ref"].startswith("work-os://"):
            original=item["file_ref"].split("://",1)[1]
            if user["role"]=="employee":
                import martial_product
                if martial_product.asset_is_technical_history(original):raise PermissionError("此素材仅在技术历史中查看")
            asset=store.record(c,"assets",original)
            if asset["storage_ref"].startswith(("https://","studio://")):raise ValueError("此素材没有本机文件")
            path=Path(asset["storage_ref"]).resolve(strict=True)
            if not any(path.is_relative_to(root) for root in store.asset_roots(asset["project_id"])):
                store.valid_asset_path(asset["project_id"],str(path))
            limit=(store.MOTION_FILE_LIMIT if asset["type"] in {"motion_reference","video"}
                   and path.suffix.lower() in {".mp4",".mov"} else 200_000_000)
            if path.stat().st_size>limit:raise ValueError("文件过大，请从原工具下载")
            return path
        if not item["file_ref"].startswith("legacy-job://"):raise ValueError("此素材不在本机")
        meta=store.parse(item["metadata"],{}) or {}
        raw=meta.get("local_path")
        if not raw:raise FileNotFoundError("旧媒体文件不可用")
        path=Path(raw).resolve(strict=True)
        if not path.is_file() or not path.is_relative_to(LEGACY_ROOT) or path.suffix.lower() not in {".mp4",".mov",".png",".jpg",".jpeg",".webp",".wav",".mp3"}:
            raise PermissionError("旧媒体路径不允许读取")
        return path


def _set_character_reference(user: dict, master_id: str, source_asset_id: str, registry_id: str) -> str:
    """Use the existing Work OS image in a real teacher version."""
    import martial
    martial.allow(user,True)
    with store.connect() as c:
        asset=store.record(c,"assets",source_asset_id)
        if asset["project_id"]!="wuxiang" or asset["status"]!="active" or asset["type"] not in {"image","character"}:
            raise ValueError("只能使用本项目已保存的角色图片")
        path=Path(asset["storage_ref"]).resolve(strict=True)
        roots=store.asset_roots("wuxiang")
        if not any(path.is_relative_to(root) for root in roots):
            raise ValueError("角色图片需要在受控工作区")
        master=martial._row(c,"martial_masters",master_id)
        current=martial._version(c,"martial_master_versions","master_id",master_id,master["current_version"])
        payload=dict(current["payload"])
        if payload.get("portrait")==source_asset_id:return "active"
        if payload.get("created_in_work_os") and not master["draft_version"] and not payload.get("portrait"):
            payload["portrait"]=source_asset_id
            version=master["current_version"]+1
            stamp=store.now()
            c.execute("""INSERT INTO martial_master_versions(master_id,version,payload,status,source_ref,
                         created_by,approved_by,created_at,approved_at) VALUES(?,?,?,?,?,?,?,?,?)""",
                      (master_id,version,store.dumps(payload),"locked","asset_registry:"+registry_id,
                       user["id"],user["id"],stamp,stamp))
            c.execute("UPDATE martial_masters SET current_version=?,updated_at=? WHERE id=?",(version,stamp,master_id))
            store.audit(c,user["id"],"martial.master.asset_add",None,
                        {"master_id":master_id,"version":version,"field":"portrait","asset_id":source_asset_id})
            return "active"
    martial.attach_master_asset(user,master_id,{"field":"portrait","asset_id":source_asset_id,
                                                "source_ref":"asset_registry:"+registry_id})
    return "needs_review"


def reuse(user: dict, asset_id: str, data: dict) -> dict:
    action=str(data.get("action") or "")
    if action not in REUSE_ACTIONS:raise ValueError("素材操作无效")
    with store.connect() as c:
        row=c.execute("SELECT * FROM asset_registry WHERE asset_id=?",(asset_id,)).fetchone()
        if not row:raise KeyError(asset_id)
        item=dict(row)
        if not _visible(c,item,user):raise PermissionError("没有此素材的访问权限")
        project_id=str(data.get("project_id") or item["project_id"] or "")
        if not project_id or not _project_allowed(c,user,project_id):raise PermissionError("请选择可访问的项目")
        if not c.execute("SELECT 1 FROM projects WHERE id=? AND active=1",(project_id,)).fetchone():raise ValueError("项目不存在或已停用")
        art_id=str(data.get("art_id") or "") or None
        master_id=str(data.get("master_id") or "") or None
        move_id=str(data.get("move_id") or "") or None
        requested_task_id=str(data.get("task_id") or "") or None
        campaign_id=str(data.get("campaign_id") or "") or None
        if campaign_id and (user["role"] not in {"manager","founder"} or not CAMPAIGN_ID.fullmatch(campaign_id)):
            raise PermissionError("活动关联需管理员确认")
        if action=="new_task" and requested_task_id:raise ValueError("新建任务不能同时关联现有任务")
        if action=="character_reference" and not master_id:raise ValueError("请选择功法老师")
        if action=="video_reference" and not move_id:raise ValueError("请选择招式")
        if any((art_id,master_id,move_id)) and project_id!="wuxiang":raise ValueError("武学关联仅适用于万象武境项目")
        art=c.execute("SELECT id,master_id FROM martial_arts WHERE id=?",(art_id,)).fetchone() if art_id else None
        master=c.execute("SELECT id FROM martial_masters WHERE id=?",(master_id,)).fetchone() if master_id else None
        move=c.execute("SELECT id,martial_art_id FROM martial_moves WHERE id=?",(move_id,)).fetchone() if move_id else None
        if art_id and not art:raise ValueError("功法不存在")
        if master_id and not master:raise ValueError("老师不存在")
        if move_id and not move:raise ValueError("招式不存在")
        if art and master and art["master_id"] and art["master_id"]!=master_id:raise ValueError("功法与老师不匹配")
        if move and art_id and move["martial_art_id"]!=art_id:raise ValueError("招式与功法不匹配")
        if move and master_id:
            move_art=c.execute("SELECT master_id FROM martial_arts WHERE id=?",(move["martial_art_id"],)).fetchone()
            if move_art and move_art["master_id"] and move_art["master_id"]!=master_id:raise ValueError("招式与老师不匹配")
        if requested_task_id:
            task=c.execute("SELECT project_id,assignee_id FROM tasks WHERE id=?",(requested_task_id,)).fetchone()
            if not task or task["project_id"]!=project_id:raise ValueError("关联任务不属于所选项目")
            if user["role"]=="employee" and task["assignee_id"]!=user["id"]:raise PermissionError("只能关联自己的任务")
        if action=="character_reference" and item["type"] not in {"image","character"}:raise ValueError("请选择图片素材")
        if action=="video_reference" and item["type"] not in {"video","motion_reference"}:raise ValueError("请选择视频素材")
        source=c.execute("SELECT original_id FROM asset_registry_sources WHERE asset_id=? AND source_system='work_os' LIMIT 1",(asset_id,)).fetchone()
        source_asset_id=source[0] if source else None
        prompt_text=(store.parse(item["metadata"],{}) or {}).get("prompt_text") if item["type"]=="prompt" else None
        if action=="ai_creation":
            if prompt_text and not SAFE_TEXT.search(prompt_text):pass
            elif not source_asset_id:raise ValueError("此历史素材尚无可供当前 AI 创作读取的文件")
            else:
                original=c.execute("SELECT storage_ref,project_id FROM assets WHERE id=?",(source_asset_id,)).fetchone()
                if not original or original["project_id"]!=project_id or Path(original["storage_ref"]).suffix.lower() not in {".md",".txt",".csv",".docx",".xlsx",".pdf"}:
                    raise ValueError("当前 AI 创作只支持已保存到同项目的文本文件；图片和视频参考尚未接通")
        if action=="new_task" and not source_asset_id and item["source_system"].startswith("legacy_ai"):
            raise ValueError("此历史素材尚无可供员工使用的文件，暂不能据此创建任务")
        if action in {"character_reference","video_reference"} and not source_asset_id:
            raise ValueError("此历史素材尚无可供当前制作直接读取的文件；可先在来源中查看，或加入项目登记其用途")
    task_id=requested_task_id
    motion_ref_id=None
    reference_status=None
    if action in {"new_task","save_project"} and item["source_system"]=="work_os_ai_studio":
        import ai_studio
        saved=ai_studio.save_to_project(user,item["original_id"],{"project_id":project_id})
        source_asset_id=saved["asset_id"]
    if action=="new_task":
        task_id="t_"+secrets.token_hex(8)
        stamp=store.now()
        with store.connect() as c:
            c.execute("""INSERT INTO tasks(id,project_id,workflow_id,title,why,assignee_id,priority,status,
                         context,input_assets,instructions,deliverable_contract,qc_contract,budget_cap,
                         created_at,updated_at,created_by) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                      (task_id,project_id,"WF-AI",str(data.get("title") or "使用素材："+item["name"])[:180],
                       "基于已有数字资产继续制作",user["id"] if user["role"]=="employee" else None,2,
                       "assigned" if user["role"]=="employee" else "ready",
                       store.dumps({"asset_registry_id":asset_id,"source_system":item["source_system"]}),
                       store.dumps([source_asset_id] if source_asset_id and item["project_id"]==project_id else []),
                       store.dumps(["查看素材并明确本次产出"]),"{}","{}",0,stamp,stamp,user["id"]))
            store.audit(c,user["id"],"asset_registry.task",task_id,{"asset_id":asset_id})
    if action=="video_reference":
        with store.connect() as c:
            existing=c.execute("SELECT id FROM martial_motion_refs WHERE move_id=? AND video_asset_id=? ORDER BY created_at DESC LIMIT 1",
                               (move_id,source_asset_id)).fetchone()
        if existing:motion_ref_id=existing[0]
        else:
            import martial
            created=martial.link_motion(user,move_id,{"asset_id":source_asset_id,"orientation":"正面"})
            motion_ref_id=next((r["id"] for r in created.get("motions",[]) if r["video_asset_id"]==source_asset_id),None)
    if action=="character_reference":
        reference_status=_set_character_reference(user,master_id,source_asset_id,asset_id)
    link_id="arl_"+secrets.token_hex(8)
    with store.connect() as c:
        c.execute("INSERT INTO asset_registry_links(id,asset_id,action,project_id,art_id,master_id,move_id,task_id,campaign_id,created_by,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                  (link_id,asset_id,action,project_id,art_id,master_id,move_id,task_id,campaign_id,user["id"],store.now()))
        if action=="save_project" and data.get("tags") is not None:
            tags=data["tags"]
            if not isinstance(tags,list) or len(tags)>20 or any(not isinstance(tag,str) or not tag.strip() or len(tag)>40 for tag in tags):
                raise ValueError("标签无效")
            meta=store.parse(item["metadata"],{}) or {}
            meta["tags"]=list(dict.fromkeys(tag.strip() for tag in tags))
            c.execute("UPDATE asset_registry SET metadata=?,updated_at=? WHERE asset_id=?",
                      (store.dumps(meta),store.now(),asset_id))
        store.audit(c,user["id"],"asset_registry.reuse",task_id,
                    {"asset_id":asset_id,"action":action,"project_id":project_id,"campaign_id":campaign_id,"motion_ref_id":motion_ref_id})
    return {"asset_id":asset_id,"action":action,"project_id":project_id,"task_id":task_id,
            "campaign_id":campaign_id,"source_asset_id":source_asset_id,"motion_ref_id":motion_ref_id,
            "suggested_prompt":prompt_text if action=="ai_creation" and prompt_text and not SAFE_TEXT.search(prompt_text) else None,
            "status":reference_status or ("reference_only" if action=="save_project" and not source_asset_id else "linked")}


def _matching_legacy_asset(c: sqlite3.Connection, object_key: str | None,
                           output_kind: str, owner: str) -> str | None:
    """Only merge an exact, unique object of the same kind and owner."""
    if not object_key or not owner:
        return None
    rows=c.execute("""SELECT asset_id FROM asset_registry
                      WHERE source_system='legacy_ai_center'
                        AND json_extract(metadata,'$.object_key')=?
                        AND type=? AND creator=? LIMIT 2""",
                   (object_key,output_kind,owner)).fetchall()
    return rows[0][0] if len(rows)==1 else None


def import_legacy(db_path: Path, legacy_data_root: Path | None = None) -> dict:
    """Read only selected old-centre metadata; register references, never binaries."""
    root=(legacy_data_root or LEGACY_ROOT).resolve()
    uri=f"file:{db_path.resolve()}?mode=ro"
    legacy=sqlite3.connect(uri,uri=True)
    legacy.row_factory=sqlite3.Row
    legacy.execute("PRAGMA query_only=ON")
    report=Counter()
    project_map=Counter()
    unmapped=[]
    initialize()
    try:
        users={r["id"]:r["username"] for r in legacy.execute("SELECT id,username FROM users")}
        columns={r[1] for r in legacy.execute("PRAGMA table_info(assets)")}
        if not {"id","name","media_type","object_key"}.issubset(columns):raise ValueError("旧资产表结构不匹配")
        profiles={r["id"]:dict(r) for r in legacy.execute("SELECT id,public_base_url,public_read FROM oss_profiles")}
        with store.connect() as c:
            for row in legacy.execute("SELECT id,owner_id,name,media_type,usage_type,object_key,size_bytes,duration_ms,visibility,source_type,source_job_id,status,created_at,oss_profile_id FROM assets"):
                report["discovered"]+=1
                a=dict(row)
                if BAD_NAME.search(a["name"]) or BAD_NAME.search(a["object_key"]):report["safety_filtered"]+=1;continue
                if a["media_type"] not in {"image","video","audio","document"}:
                    report["unrecognized"]+=1;continue
                typ=a["media_type"]
                name=a["name"] or Path(a["object_key"]).name or "旧素材"
                content=(name+" "+a["object_key"]).lower()
                subtype="character" if any(x in content for x in ("角色","人物","npc","cryn","pongda","wongkey","boor","bara")) else \
                        "scene" if any(x in content for x in ("场景","scene")) else \
                        "prop" if any(x in content for x in ("道具","prop")) else a["usage_type"] or ""
                owner=users.get(a["owner_id"],"")
                meta={"owner_username":owner,"visibility":a["visibility"],"usage_type":a["usage_type"],
                      "duration_ms":a["duration_ms"],"size_bytes":a["size_bytes"],"object_key":a["object_key"],
                      "oss_profile_id":a["oss_profile_id"],"source_type":a["source_type"],
                      "source_job_id":a["source_job_id"],"public_read":bool(profiles.get(a["oss_profile_id"],{}).get("public_read"))}
                item={"source_system":"legacy_ai_center","original_id":a["id"],"project_id":"wuxiang", "type":typ,
                      "subtype":subtype,"name":name,"file_ref":"legacy-oss://"+a["id"],"sha256":None,
                      "creator":owner,"created_at":a["created_at"],"status":a["status"],"metadata":meta}
                _,outcome=_register(c,item);report[outcome]+=1;report[typ]+=1
                project_map["wuxiang"]+=1
                if not a["oss_profile_id"]:unmapped.append({"source_system":"legacy_ai_center","original_id":a["id"],"reason":"OSS profile missing"})
            for row in legacy.execute("SELECT id,owner_id,prompt,model,provider,output_kind,status,output_path,output_oss_key,created_at,upstream_id FROM jobs"):
                job=dict(row)
                owner=users.get(job["owner_id"],"")
                prompt=str(job["prompt"] or "")
                if prompt:
                    report["discovered"]+=1
                if prompt and not SAFE_TEXT.search(prompt):
                    item={"source_system":"legacy_ai_prompt","original_id":job["id"],"project_id":"wuxiang","type":"prompt",
                          "subtype":"generation_prompt","name":"生成提示词 · "+job["id"][:8],"file_ref":"legacy-prompt://"+job["id"],
                          "sha256":hashlib.sha256(prompt.encode()).hexdigest(),"creator":owner,"created_at":job["created_at"],
                          "model":job["model"],"provider":job["provider"],"task_ref":job["id"],"status":job["status"],
                          "metadata":{"owner_username":owner,"visibility":"private","prompt_text":prompt,"usage_type":"generation"}}
                    _,outcome=_register(c,item);report[outcome]+=1;report["prompt"]+=1;project_map["wuxiang"]+=1
                elif prompt:report["safety_filtered"]+=1
                if job["status"] not in {"completed","succeeded"}:continue
                report["discovered"]+=1
                raw=job["output_path"] or ""
                path=Path(raw).resolve() if raw else None
                local=bool(path and path.is_file() and path.is_relative_to(root))
                sha=_digest(path,MAX_LEGACY_HASH_BYTES) if local else None
                if job["output_kind"] not in {"image","video","audio"}:
                    report["unrecognized"]+=1;continue
                typ=job["output_kind"]
                item={"source_system":"legacy_ai_job","original_id":job["id"],"project_id":"wuxiang","type":typ,
                      "subtype":"ai_result","name":"AI 生成结果 · "+job["id"][:8],"file_ref":"legacy-job://"+job["id"],
                      "sha256":sha,"creator":owner,"created_at":job["created_at"],"model":job["model"],
                      "provider":job["provider"],"prompt_ref":job["id"],"task_ref":job["id"],"status":"active",
                      "metadata":{"owner_username":owner,"visibility":"private","local_path":str(path) if local else None,
                                  "local_file_available":local,"output_oss_key":job["output_oss_key"],"upstream_id":job["upstream_id"]}}
                canonical=_matching_legacy_asset(c,job["output_oss_key"],typ,owner)
                prior_source=c.execute("SELECT asset_id FROM asset_registry_sources WHERE source_system='legacy_ai_job' AND original_id=?",
                                       (job["id"],)).fetchone()
                if prior_source and prior_source[0]!=canonical:
                    canonical=None  # Never rewrite an existing source to another asset.
                if canonical:
                    if not prior_source:
                        c.execute("INSERT INTO asset_registry_sources(source_system,original_id,asset_id,file_ref,sha256) VALUES(?,?,?,?,?)",
                                  ("legacy_ai_job",job["id"],canonical,item["file_ref"],sha))
                    prior=c.execute("SELECT metadata,sha256 FROM asset_registry WHERE asset_id=?",(canonical,)).fetchone()
                    merged=store.parse(prior["metadata"],{}) or {}
                    # The old asset's visibility is authoritative. A job output
                    # on its own is private to its creator; never promote its
                    # prompt or output to team access during deduplication.
                    merged.update({k:v for k,v in item["metadata"].items()
                                   if k not in {"owner_username","visibility"}})
                    c.execute("UPDATE asset_registry SET file_ref=?,sha256=COALESCE(sha256,?),model=?,provider=?,prompt_ref=?,task_ref=?,metadata=?,updated_at=? WHERE asset_id=?",
                              (item["file_ref"] if local else c.execute("SELECT file_ref FROM asset_registry WHERE asset_id=?",(canonical,)).fetchone()[0],
                               sha,job["model"] or "",job["provider"] or "",job["id"],job["id"],store.dumps(merged),store.now(),canonical))
                    report["existing" if prior_source else "deduplicated"]+=1
                else:
                    _,outcome=_register(c,item);report[outcome]+=1
                report[typ]+=1;project_map["wuxiang"]+=1
                if not local:unmapped.append({"source_system":"legacy_ai_job","original_id":job["id"],"reason":"local output not found"})
    finally:legacy.close()
    counts={"discovered":report["discovered"],"imported":report["imported"],"deduplicated":report["deduplicated"],
            "already_indexed":report["existing"],
            "unrecognized":report["unrecognized"],"safety_filtered":report["safety_filtered"],
            "images":report["image"],"videos":report["video"],"audios":report["audio"],
            "documents":report["document"],"prompts":report["prompt"],
            "character_assets":0,"project_map":dict(project_map),"unmapped_assets":unmapped}
    with store.connect() as c:
        counts["character_assets"]=c.execute("SELECT COUNT(*) FROM asset_registry WHERE source_system LIKE 'legacy_ai%' AND subtype='character'").fetchone()[0]
    return counts
