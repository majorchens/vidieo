"""Additive martial asset workspace on top of Work OS tasks and assets.

Only the approved employee specialty may edit Wuxiang martial drafts. Existing
Work OS roles, task transitions, assets and QC records remain authoritative.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import math
import os
import re
import secrets
import struct
import shutil
import subprocess
import tempfile
import threading
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import urlencode

import store
import martial_assets
import martial_product
import martial_revision

SOURCE = "https://lxz97dy21fk.feishu.cn/wiki/SjjTwyEAaiHzmekqW6RczINYnBh"
ARTS = (
    ("beginner", "入门套路", "Beginner Form", "pongda"),
    ("flowing_cloud", "流云掌", "Flowing Cloud Palm", "wongkey"),
    ("mountain_fist", "开山拳", "Mountain-Breaking Fist", "boor"),
    ("taiji", "太极功", "Taiji", "bara"),
    ("bagua", "八卦掌", "Bagua Zhang", "cryn"),
)
MASTERS = (
    ("pongda", "Pongda", "熊猫（V2 中沿用“小熊猫”称呼）", "P12"),
    ("wongkey", "Wongkey", "猴子", "P07"),
    ("boor", "Boor", "熊", "P11"),
    ("bara", "Bara", "水豚", "A01"),
    ("cryn", "Cryn", "丹顶鹤", "C04"),
)
MOVE_FIELDS = ("chinese_name", "english_name", "chinese_action", "english_action",
               "chinese_coaching", "english_coaching", "breathing_notes", "safety_notes")
MASTER_FIELDS = ("portrait", "front_view", "side_view", "back_view", "turnaround", "costume", "digital_model",
                 "voice_id", "voice_preview", "profile", "biography", "personality",
                 "teaching_style", "speaking_style", "forbidden_changes", "world_identity")


def _glb_header_ok(content: bytes, size: int | None = None) -> bool:
    return (len(content) >= 20 and content[:4] == b"glTF" and
            struct.unpack_from("<II", content, 4) == (2, size if size is not None else len(content)) and
            content[16:20] == b"JSON")
MEDIA_STATES = {"queued", "dispatching", "submitted", "running", "download_pending",
                "technical_check", "succeeded", "failed", "unknown_submission"}
PUBLIC_ORIGIN = "https://ai.duodianqian.cn/work-os"
PRICE = {  # Only the verified 5s/480p reservation profiles; not a general price table.
    "sd2.0": {"reservation": 2.5, "model": "doubao-seedance-2.0"},
    "sd2.5": {"reservation": 3.6, "model": "doubao-seedance-2-5"},
}
PRICE_SOURCE = "https://runy.yitd.cn/app/workbench/billing（2026-09-21 实测及保守预留）"
RUNY_VIDEO_REFERENCE_MAX_SECONDS = 30
LONG_VIDEO_RESERVATION_SOURCE = (
    "润元历史账单 480p ¥59.5/百万 tokens；含视频输入尚无实际账单。"
    "按输入+输出帧估算并上浮 20% 预留，实际以供应商账单为准"
)
PACKAGE_PROMPT_VERSION = 3  # Keep existing single-reference packages valid.
VIDEO_TYPES = ("teaching", "practice")
DICTIONARY = Path(__file__).resolve().parents[1] / "registry" / "martial_dictionary.json"
MASTER_SOURCES = Path(__file__).resolve().parents[1] / "registry" / "martial_master_sources.json"
MARTIAL_FILE_LIMIT = 40_000_000  # Legacy JSON/base64 assets and generated candidates only.
CANDIDATE_UPLOAD_LOCK = threading.RLock()
MOTION_COVER_LOCK = threading.RLock()
MOTION_UPLOAD_LOCK = threading.RLock()
QC_CHECKS = ("action_order", "hand_path", "footwork", "center_of_gravity",
             "start_pose", "end_pose", "key_moments", "teaching_suitability")


def initialize():
    with store.connect() as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS martial_specialists(
            user_id TEXT PRIMARY KEY REFERENCES users(id), project_id TEXT NOT NULL DEFAULT 'wuxiang',
            title TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS martial_arts(
            id TEXT PRIMARY KEY, chinese_name TEXT NOT NULL, english_name TEXT NOT NULL,
            volume TEXT NOT NULL, category TEXT NOT NULL, description TEXT NOT NULL DEFAULT '',
            style_traits TEXT NOT NULL DEFAULT '[]', master_id TEXT NOT NULL,
            status TEXT NOT NULL, version INTEGER NOT NULL, draft_version INTEGER NOT NULL DEFAULT 0,
            source_ref TEXT NOT NULL,
            planned_moves INTEGER, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS martial_art_versions(
            art_id TEXT NOT NULL REFERENCES martial_arts(id), version INTEGER NOT NULL,
            payload TEXT NOT NULL, status TEXT NOT NULL, source_ref TEXT NOT NULL,
            created_by TEXT NOT NULL REFERENCES users(id), approved_by TEXT REFERENCES users(id),
            created_at TEXT NOT NULL, approved_at TEXT, PRIMARY KEY(art_id,version));
        CREATE TABLE IF NOT EXISTS martial_moves(
            id TEXT PRIMARY KEY, martial_art_id TEXT NOT NULL REFERENCES martial_arts(id),
            ordinal INTEGER NOT NULL, current_version INTEGER NOT NULL DEFAULT 0,
            draft_version INTEGER NOT NULL DEFAULT 0, task_id TEXT REFERENCES tasks(id),
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
            UNIQUE(martial_art_id,ordinal));
        CREATE TABLE IF NOT EXISTS martial_move_versions(
            move_id TEXT NOT NULL REFERENCES martial_moves(id), version INTEGER NOT NULL,
            payload TEXT NOT NULL, status TEXT NOT NULL, source_ref TEXT NOT NULL,
            created_by TEXT NOT NULL REFERENCES users(id), approved_by TEXT REFERENCES users(id),
            created_at TEXT NOT NULL, approved_at TEXT, PRIMARY KEY(move_id,version));
        CREATE TABLE IF NOT EXISTS martial_masters(
            id TEXT PRIMARY KEY, name TEXT NOT NULL, species TEXT NOT NULL,
            current_version INTEGER NOT NULL, draft_version INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS martial_master_versions(
            master_id TEXT NOT NULL REFERENCES martial_masters(id), version INTEGER NOT NULL,
            payload TEXT NOT NULL, status TEXT NOT NULL, source_ref TEXT NOT NULL,
            created_by TEXT NOT NULL REFERENCES users(id), approved_by TEXT REFERENCES users(id),
            created_at TEXT NOT NULL, approved_at TEXT, PRIMARY KEY(master_id,version));
        CREATE TABLE IF NOT EXISTS martial_motion_refs(
            id TEXT PRIMARY KEY, move_id TEXT NOT NULL REFERENCES martial_moves(id),
            version INTEGER NOT NULL, video_asset_id TEXT NOT NULL REFERENCES assets(id),
            start_time REAL NOT NULL, end_time REAL NOT NULL, orientation TEXT NOT NULL,
            fps REAL, duration REAL, start_pose TEXT NOT NULL, end_pose TEXT NOT NULL,
            key_moments TEXT NOT NULL, notes TEXT NOT NULL, status TEXT NOT NULL,
            confirmed_by TEXT REFERENCES users(id), created_at TEXT NOT NULL, confirmed_at TEXT,
            UNIQUE(move_id,version));
        CREATE TABLE IF NOT EXISTS martial_video_plans(
            id TEXT PRIMARY KEY, move_id TEXT NOT NULL REFERENCES martial_moves(id),
            asset_type TEXT NOT NULL CHECK(asset_type IN ('teaching','practice')),
            version INTEGER NOT NULL, motion_ref_id TEXT NOT NULL REFERENCES martial_motion_refs(id),
            source_start REAL NOT NULL, source_end REAL NOT NULL,
            target_duration REAL NOT NULL, brief TEXT NOT NULL,
            status TEXT NOT NULL CHECK(status IN ('active','superseded')),
            created_by TEXT NOT NULL REFERENCES users(id), created_at TEXT NOT NULL,
            UNIQUE(move_id,asset_type,version));
        CREATE TABLE IF NOT EXISTS martial_packages(
            id TEXT PRIMARY KEY, move_id TEXT NOT NULL REFERENCES martial_moves(id),
            motion_ref_id TEXT NOT NULL REFERENCES martial_motion_refs(id),
            master_version INTEGER NOT NULL, task_id TEXT REFERENCES tasks(id),
            request_key TEXT NOT NULL UNIQUE, status TEXT NOT NULL, facts_hash TEXT NOT NULL,
            facts TEXT NOT NULL, result TEXT, usage TEXT, provider_job_id TEXT,
            error TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS martial_media_jobs(
            id TEXT PRIMARY KEY, move_id TEXT NOT NULL REFERENCES martial_moves(id),
            task_id TEXT NOT NULL REFERENCES tasks(id), package_id TEXT NOT NULL REFERENCES martial_packages(id),
            motion_ref_id TEXT NOT NULL REFERENCES martial_motion_refs(id),
            master_version INTEGER NOT NULL, asset_type TEXT NOT NULL,
            provider TEXT NOT NULL, model_alias TEXT NOT NULL, model TEXT NOT NULL,
            prompt_hash TEXT NOT NULL, prompt TEXT NOT NULL,
            character_asset_id TEXT NOT NULL REFERENCES assets(id),
            duration INTEGER NOT NULL, aspect_ratio TEXT NOT NULL, resolution TEXT NOT NULL,
            status TEXT NOT NULL, request_key TEXT NOT NULL UNIQUE,
            idempotency_key TEXT NOT NULL UNIQUE, local_job_id TEXT, local_media_id TEXT,
            provider_job_id TEXT, candidate_asset_id TEXT REFERENCES assets(id),
            reserved_cost REAL NOT NULL, actual_cost REAL, quote_source TEXT NOT NULL,
            technical_report TEXT, error TEXT, revision_of TEXT REFERENCES martial_media_jobs(id),
            submitted_at TEXT, completed_at TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS martial_qc(
            id TEXT PRIMARY KEY, media_job_id TEXT NOT NULL REFERENCES martial_media_jobs(id),
            move_id TEXT NOT NULL REFERENCES martial_moves(id), stage TEXT NOT NULL,
            result TEXT NOT NULL, findings TEXT NOT NULL, reference_comparison TEXT NOT NULL,
            reviewer_id TEXT REFERENCES users(id), created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS martial_final_assets(
            id TEXT PRIMARY KEY, move_id TEXT NOT NULL REFERENCES martial_moves(id),
            asset_type TEXT NOT NULL, media_job_id TEXT NOT NULL REFERENCES martial_media_jobs(id),
            asset_id TEXT NOT NULL REFERENCES assets(id), status TEXT NOT NULL,
            approved_by TEXT NOT NULL REFERENCES users(id), created_at TEXT NOT NULL,
            UNIQUE(move_id,asset_type,media_job_id));
        CREATE TABLE IF NOT EXISTS martial_selections(
            media_job_id TEXT PRIMARY KEY REFERENCES martial_media_jobs(id),
            reason TEXT NOT NULL, selected_by TEXT NOT NULL REFERENCES users(id),
            selected_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS martial_routes(
            model_alias TEXT PRIMARY KEY, provider TEXT NOT NULL, model TEXT NOT NULL,
            checked_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS martial_budget_policies(
            task_id TEXT PRIMARY KEY REFERENCES tasks(id), unlimited INTEGER NOT NULL CHECK(unlimited=1),
            updated_by TEXT NOT NULL REFERENCES users(id), updated_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS martial_one_time_changes(
            name TEXT PRIMARY KEY, applied_at TEXT NOT NULL);
        CREATE INDEX IF NOT EXISTS idx_martial_media_status ON martial_media_jobs(status,created_at);
        CREATE INDEX IF NOT EXISTS idx_martial_move_art ON martial_moves(martial_art_id,ordinal);
        CREATE INDEX IF NOT EXISTS idx_martial_motion_move ON martial_motion_refs(move_id,version);
        CREATE INDEX IF NOT EXISTS idx_martial_video_plan ON martial_video_plans(move_id,asset_type,status,version);
        """)
        # Existing production databases are migrated in place; no canonical facts are auto-approved.
        for table, additions in {
            "martial_move_versions": {
                "submitted_by":"TEXT", "submitted_at":"TEXT", "submission_note":"TEXT NOT NULL DEFAULT ''",
                "review_flags":"TEXT NOT NULL DEFAULT '[]'", "rejected_by":"TEXT", "rejected_at":"TEXT",
                "rejection_reason":"TEXT NOT NULL DEFAULT ''"},
            "martial_motion_refs": {"width":"INTEGER", "height":"INTEGER",
                                    "frame_orientation":"TEXT", "rotation_degrees":"INTEGER",
                                    "cover_asset_id":"TEXT REFERENCES assets(id)"},
            "martial_packages": {"requested_by":"TEXT"},
            "martial_media_jobs": {"generation_mode":"TEXT", "video_plan_id":"TEXT REFERENCES martial_video_plans(id)",
                                   "segments_json":"TEXT NOT NULL DEFAULT '[]'", "segment_progress":"TEXT NOT NULL DEFAULT '[]'",
                                   "edit_refs_json":"TEXT NOT NULL DEFAULT '{}'",
                                   "background_ref_json":"TEXT NOT NULL DEFAULT '{}'"},
            "martial_qc": {"checks":"TEXT NOT NULL DEFAULT '{}'", "issue_ranges":"TEXT NOT NULL DEFAULT '[]'",
                           "major_dispute":"INTEGER NOT NULL DEFAULT 0"},
        }.items():
            existing={r[1] for r in c.execute(f"PRAGMA table_info({table})")}
            for column, definition in additions.items():
                if column not in existing:c.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
        stamp=store.now()
        for art_id,cn,en,master_id in ARTS:
            c.execute("INSERT OR IGNORE INTO martial_arts(id,chinese_name,english_name,volume,category,master_id,status,version,draft_version,source_ref,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                      (art_id,cn,en,"第一卷","入门" if art_id=="beginner" else "流派套路",master_id,"needs_review",1,1,SOURCE,stamp,stamp))
        master_sources=json.loads(MASTER_SOURCES.read_text(encoding="utf-8"))
        for master_id,name,species,voice in MASTERS:
            pinned=master_sources[master_id]
            c.execute("INSERT OR IGNORE INTO martial_masters(id,name,species,current_version,created_at,updated_at) VALUES(?,?,?,?,?,?)",
                      (master_id,name,species,2,stamp,stamp))
            payload={"voice_id":voice,"profile":pinned["summary"],"canonical_sha256":pinned["sha256"],"biography":"","personality":"", "teaching_style":"",
                     "speaking_style":"","forbidden_changes":[],"world_identity":"", "portrait":None,
                     "front_view":None,"side_view":None,"back_view":None,"turnaround":None,"voice_preview":None}
            c.execute("INSERT OR IGNORE INTO martial_master_versions(master_id,version,payload,status,source_ref,created_by,approved_by,created_at,approved_at) VALUES(?,?,?,?,?,?,?,?,?)",
                      (master_id,2,store.dumps(payload),"locked","/Users/majorchen/Documents/ChatGPT/武术短剧拍摄/制作组/万象武境角色基线.md","u_system","u_system",stamp,stamp))
        if DICTIONARY.is_file():
            source=json.loads(DICTIONARY.read_text(encoding="utf-8"))
            for art in source["arts"]:
                if art["id"] not in {x[0] for x in ARTS}:raise ValueError("武学词典包含未知流派")
                intro=art["intro"]
                traits=intro.partition("：")[2].partition("——")[0].split("，") if "：" in intro else []
                c.execute("UPDATE martial_arts SET description=?,style_traits=?,planned_moves=?,source_ref=?,updated_at=? WHERE id=? AND description='' AND planned_moves IS NULL",
                          (intro,store.dumps([x.strip() for x in traits if x.strip()]),art["planned_moves"],
                           source["source_url"]+";docx-sha256:"+source["source_sha256"],stamp,art["id"]))
                current=dict(c.execute("SELECT * FROM martial_arts WHERE id=?",(art["id"],)).fetchone())
                art_payload={key:current[key] for key in ("chinese_name","english_name","volume","category","description","master_id","planned_moves")}
                art_payload["style_traits"]=store.parse(current["style_traits"],[])
                c.execute("INSERT OR IGNORE INTO martial_art_versions(art_id,version,payload,status,source_ref,created_by,created_at) VALUES(?,?,?,?,?,?,?)",
                          (art["id"],1,store.dumps(art_payload),"needs_review",current["source_ref"],"u_system",stamp))
                for move in art["moves"]:
                    ordinal=int(move["order"])
                    existing=c.execute("SELECT id FROM martial_moves WHERE martial_art_id=? AND ordinal=?",(art["id"],ordinal)).fetchone()
                    if existing:continue
                    move_id=f"mv_{art['id']}_{ordinal:02d}"
                    payload={key:move.get(key,"") for key in MOVE_FIELDS}
                    c.execute("INSERT INTO martial_moves(id,martial_art_id,ordinal,draft_version,created_at,updated_at) VALUES(?,?,?,?,?,?)",
                              (move_id,art["id"],ordinal,1,stamp,stamp))
                    c.execute("INSERT INTO martial_move_versions(move_id,version,payload,status,source_ref,created_by,created_at) VALUES(?,?,?,?,?,?,?)",
                              (move_id,1,store.dumps(payload),"needs_review",move["source_ref"],"u_system",stamp))
        martial_assets.ensure_schema(c)
        martial_revision.ensure_schema(c)
        martial_revision.backfill_p0(c)
        # The owner explicitly approved no task budget cap for the existing
        # flowing-cloud / first-move production run. Apply to that task once;
        # later tasks and a manager's future budget choice are unaffected.
        marker="flowing_cloud_01_unlimited_budget_20260924"
        if not c.execute("SELECT 1 FROM martial_one_time_changes WHERE name=?",(marker,)).fetchone():
            target=c.execute("SELECT m.task_id FROM martial_moves m JOIN tasks t ON t.id=m.task_id "
                             "WHERE m.id='mv_flowing_cloud_01' AND t.project_id='wuxiang'").fetchone()
            if target:
                c.execute("INSERT OR REPLACE INTO martial_budget_policies(task_id,unlimited,updated_by,updated_at) VALUES(?,?,?,?)",
                          (target[0],1,"u_system",stamp))
                c.execute("INSERT INTO martial_one_time_changes(name,applied_at) VALUES(?,?)",(marker,stamp))


def specialty(user: dict) -> bool:
    if user["role"] != "employee": return False
    with store.connect() as c:
        return c.execute("SELECT 1 FROM martial_specialists WHERE user_id=? AND project_id='wuxiang' AND active=1",(user["id"],)).fetchone() is not None


def allow(user: dict, write=False):
    if user["role"] in {"founder","manager"}: return
    if not write and user["role"]=="employee" and store.project_allowed(user,"wuxiang"):return
    if not specialty(user): raise PermissionError("没有武学数字资产权限")
    if write and user["role"] != "employee": raise PermissionError("只有岗位员工可维护草稿")


def _row(c,table,id_):
    if table not in {"martial_arts","martial_moves","martial_masters","martial_motion_refs","martial_packages","martial_media_jobs"}:
        raise ValueError("无效武学记录")
    r=c.execute("SELECT * FROM "+table+" WHERE id=?",(id_,)).fetchone()
    if r is None: raise KeyError(id_)
    return dict(r)


def _version(c,table,key,id_,version):
    r=c.execute(f"SELECT * FROM {table} WHERE {key}=? AND version=?",(id_,version)).fetchone()
    if r is None: raise KeyError(f"{id_} v{version}")
    d=dict(r);d["payload"]=store.parse(d["payload"],{})
    if table=="martial_move_versions":d["review_flags"]=store.parse(d.get("review_flags"),[])
    return d


def _import_confirmation(c,kind: str,record_id: str,version: int) -> dict | None:
    if not c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='martial_import_confirmations'").fetchone():
        return None
    row=c.execute("SELECT status,source_sha256,imported_at,missing_fields FROM martial_import_confirmations WHERE kind=? AND record_id=? AND version=?",(kind,record_id,version)).fetchone()
    if not row:return None
    result=dict(row);result["missing_fields"]=store.parse(result["missing_fields"],[])
    return result


def _image_header_ok(content: bytes, suffix: str) -> bool:
    suffix=suffix.lower()
    if suffix==".png":
        return (len(content)>=24 and content[:8]==b"\x89PNG\r\n\x1a\n" and content[12:16]==b"IHDR" and
                0<struct.unpack_from(">I",content,16)[0]<=8192 and 0<struct.unpack_from(">I",content,20)[0]<=8192)
    if suffix in {".jpg",".jpeg"}:return len(content)>100 and content[:3]==b"\xff\xd8\xff"
    if suffix==".webp":return len(content)>30 and content[:4]==b"RIFF" and content[8:12]==b"WEBP"
    return False


def _master_status(c, master_id: str) -> dict:
    if not master_id:
        return {"version":0,"character_locked":False,"visual_asset_id":None,
                "primary_visual_asset_id":None,"portrait_asset_id":None,"voice_id":None,
                "costume_asset_id":None,"digital_model_asset_id":None,
                "visual_reference_status":"missing","production_ready":False}
    master=_row(c,"martial_masters",master_id)
    version=_version(c,"martial_master_versions","master_id",master_id,master["current_version"])
    payload=version["payload"]
    visual_id=None
    for candidate in (payload.get("portrait"),payload.get("front_view"),payload.get("side_view"),
                      payload.get("back_view"),payload.get("turnaround"),*(payload.get("other_angles") or [])):
        if not candidate:continue
        visual_id=candidate
        asset=c.execute("SELECT type,status,storage_ref FROM assets WHERE id=?",(visual_id,)).fetchone()
        if asset and asset["type"] in {"image","character"} and asset["status"]=="active":
            try:
                path=Path(asset["storage_ref"]).resolve(strict=True)
                roots=store.asset_roots("wuxiang")
                if path.is_file() and any(path.is_relative_to(root) for root in roots):
                    with path.open("rb") as image:
                        if _image_header_ok(image.read(128),path.suffix):break
            except (OSError,RuntimeError):pass
    else:visual_id=None
    locked=version["status"]=="locked" and bool(payload.get("canonical_sha256") or payload.get("profile") or
                                                payload.get("created_in_work_os") and master["name"] and master["species"])
    return {"version":master["current_version"],"character_locked":locked,
            "visual_asset_id":visual_id,
            "primary_visual_asset_id":visual_id,
            "portrait_asset_id":payload.get("portrait"),
            "costume_asset_id":payload.get("costume"),
            "digital_model_asset_id":payload.get("digital_model"),
            "voice_id":payload.get("voice_id"),
            "visual_reference_status":"confirmed" if visual_id else "missing",
            "production_ready":bool(locked and visual_id)}


def _task_lock_error(c, task_id: str, visual_asset_id: str, master_version: int, ref: dict) -> str | None:
    task=store.record(c,"tasks",task_id)
    character=store.parse(task["character_lock"],{}) or {}
    motion=store.parse(task["motion_lock"],{}) or {}
    if (character.get("asset_id")!=visual_asset_id or character.get("version")!=master_version or
        motion.get("asset_id")!=ref["video_asset_id"] or motion.get("version")!=ref["version"] or
        motion.get("start")!=ref["start_time"] or motion.get("end")!=ref["end_time"]):
        return "当前任务锁定旧版角色或真人动作参考；生成当前版本 AI Production Package 后系统会建立新任务"
    return None


def _video_plans(c, move_id: str) -> dict:
    """Each track may use a different locked reference; retain older plans."""
    plans={kind:None for kind in VIDEO_TYPES}
    for row in c.execute("""SELECT p.* FROM martial_video_plans p
         JOIN martial_motion_refs r ON r.id=p.motion_ref_id
         WHERE p.move_id=? AND p.status='active' AND r.move_id=p.move_id AND r.status='locked'
         ORDER BY p.version DESC""",(move_id,)):
        if plans[row["asset_type"]] is None and not martial_product.historical(c,"motion_ref",row["motion_ref_id"]):
            plans[row["asset_type"]]=dict(row)
    return plans


def _plan_ref(c, plan: dict | None) -> dict | None:
    return dict(_row(c,"martial_motion_refs",plan["motion_ref_id"])) if plan else None


def _motion_facts(ref: dict) -> dict:
    return {"id":ref["id"],"version":ref["version"],"asset_id":ref["video_asset_id"],
            "start":ref["start_time"],"end":ref["end_time"],"orientation":ref["orientation"],
            "start_pose":ref["start_pose"],"end_pose":ref["end_pose"],
            "key_moments":store.parse(ref["key_moments"],[]) if isinstance(ref["key_moments"],str) else ref["key_moments"],
            "notes":ref["notes"],"duration":ref["duration"],"width":ref["width"],"height":ref["height"]}


def _plan_facts(plans: dict) -> dict:
    fields=("id","asset_type","version","motion_ref_id","source_start","source_end","target_duration","brief")
    return {kind:{key:plans[kind][key] for key in fields} if plans[kind] else None for kind in VIDEO_TYPES}


def _package_facts_current(c, move: dict, facts: dict, ref: dict | None, plans: dict) -> bool:
    art=_row(c,"martial_arts",move["martial_art_id"])
    master=_row(c,"martial_masters",art["master_id"]) if art["master_id"] else None
    return bool(ref and master and all(plans[kind] for kind in VIDEO_TYPES) and
        facts.get("package_prompt_version")==PACKAGE_PROMPT_VERSION and
        facts.get("move",{}).get("version")==move["current_version"] and
        facts.get("art",{}).get("version")==art["version"] and
        facts.get("master",{}).get("version")==master["current_version"] and
        facts.get("motion",{}).get("id")==ref["id"] and
        all((facts.get("motions",{}).get(kind) or facts.get("motion",{})).get("id")==plans[kind]["motion_ref_id"] for kind in VIDEO_TYPES) and
        facts.get("video_plans")==_plan_facts(plans))


def _package_track_facts_current(c, move: dict, facts: dict, ref: dict, plan: dict, asset_type: str) -> bool:
    art=_row(c,"martial_arts",move["martial_art_id"])
    master=_row(c,"martial_masters",art["master_id"])
    return bool(facts.get("package_prompt_version")==PACKAGE_PROMPT_VERSION and
        facts.get("move",{}).get("version")==move["current_version"] and
        facts.get("art",{}).get("version")==art["version"] and
        facts.get("master",{}).get("version")==master["current_version"] and
        facts.get("motion",{}).get("id")==ref["id"] and
        (facts.get("motions",{}).get(asset_type) or facts.get("motion",{})).get("id")==plan["motion_ref_id"] and
        facts.get("video_plans",{}).get(asset_type)=={key:plan[key] for key in
            ("id","asset_type","version","motion_ref_id","source_start","source_end","target_duration","brief")})


def _current_job_error(c, job: dict) -> str | None:
    """Only candidates from the current two-track AI preparation may be acted on."""
    if job.get("generation_mode") not in {"preview","reproduce","complete","edit_trial"}:
        return "历史候选缺少当前双视频规划记录"
    move=_row(c,"martial_moves",job["move_id"])
    if not martial_product.current_move(c,move["id"]):return "招式已不在当前生产范围"
    ref=next((r for r in c.execute("SELECT * FROM martial_motion_refs WHERE move_id=? AND status='locked' ORDER BY version DESC",(move["id"],))
              if not martial_product.historical(c,"motion_ref",r["id"])),None)
    if not ref:return "真人动作参考已不是当前锁定版本"
    plans=_video_plans(c,move["id"])
    plan=plans.get(job["asset_type"])
    if not plan or job.get("video_plan_id")!=plan["id"]:
        return "候选引用的视频规划已不是当前版本"
    if job["motion_ref_id"]!=plan["motion_ref_id"]:
        return "候选引用的真人动作已不是该视频规划的当前版本"
    package=c.execute("SELECT * FROM martial_packages WHERE id=?",(job["package_id"],)).fetchone()
    latest=next((r for r in c.execute("SELECT id FROM martial_packages WHERE move_id=? ORDER BY created_at DESC,rowid DESC",(move["id"],))
                 if not martial_product.historical(c,"package",r["id"])),None)
    if (not package or not latest or latest["id"]!=job["package_id"] or package["status"]!="complete" or
            package["motion_ref_id"]!=ref["id"] or job["master_version"]!=package["master_version"] or
            not _package_facts_current(c,move,store.parse(package["facts"],{}),dict(ref),plans) or
            store.parse(package["result"],{}).get("missing_inputs")):
        return "候选所用 AI 准备已不是当前版本"
    return None


def _current_final_error(c, job: dict) -> str | None:
    """A formal video must prove its own full-length track and current locks."""
    if job.get("generation_mode")=="preview":return "5 秒样片不能定版为正式作品"
    if job.get("generation_mode") not in {"reproduce","complete"}:
        return "历史候选缺少已核验的完整视频生产记录"
    move=_row(c,"martial_moves",job["move_id"])
    if not martial_product.current_move(c,move["id"]):return "招式已不在当前生产范围"
    ref=next((r for r in c.execute("SELECT * FROM martial_motion_refs WHERE move_id=? AND status='locked' ORDER BY version DESC",(move["id"],))
              if not martial_product.historical(c,"motion_ref",r["id"])),None)
    if not ref:return "真人动作参考已不是当前锁定版本"
    plans=_video_plans(c,move["id"])
    plan=plans.get(job["asset_type"])
    if not plan or job.get("video_plan_id")!=plan["id"]:
        return "候选引用的视频规划已不是当前版本"
    if job["motion_ref_id"]!=plan["motion_ref_id"]:
        return "候选引用的真人动作已不是该视频规划的当前版本"
    package=c.execute("SELECT * FROM martial_packages WHERE id=?",(job["package_id"],)).fetchone()
    if (not package or package["status"]!="complete" or
            package["motion_ref_id"]!=ref["id"] or job["master_version"]!=package["master_version"] or
            not _package_track_facts_current(c,move,store.parse(package["facts"],{}),dict(ref),plan,job["asset_type"])):
        return "候选所用 AI 准备已不是当前双视频规划"
    if not math.isclose(float(job["duration"]),float(plan["target_duration"]),abs_tol=1):
        return "候选记录时长不匹配当前视频规划"
    technical=job.get("technical_report")
    if not isinstance(technical,dict):technical=store.parse(technical,{})
    if technical.get("result")!="pass":return "候选缺少通过的技术核验"
    probe=technical.get("server_probe") or {}
    try:actual=float(probe["duration"])
    except (KeyError,TypeError,ValueError):return "候选缺少服务端核验的视频时长"
    if not math.isfinite(actual) or not math.isclose(actual,float(plan["target_duration"]),abs_tol=1):
        return "候选实际视频时长不匹配当前视频规划"
    return None


def save_video_plan(user: dict, move_id: str, data: dict) -> dict:
    allow(user,True)
    kind=str(data.get("asset_type") or "")
    if kind not in VIDEO_TYPES:raise ValueError("请选择讲解演示或跟教练跟练视频")
    try:
        start=float(data["source_start"]);end=float(data["source_end"])
        duration=float(data["target_duration"])
    except (KeyError,TypeError,ValueError):raise ValueError("请填写参考区间和目标时长")
    if not all(math.isfinite(v) for v in (start,end,duration)) or duration<1 or duration>3600:
        raise ValueError("目标时长须在 1 至 3600 秒之间")
    brief=data.get("brief","")
    if not isinstance(brief,str) or len(brief.strip())>800:
        raise ValueError("制作说明不能超过 800 字")
    with store.connect() as c:
        _row(c,"martial_moves",move_id)
        if not martial_product.current_move(c,move_id):raise ValueError("该招式不在当前生产范围")
        requested_ref=str(data.get("motion_ref_id") or "")
        if requested_ref:
            ref=c.execute("SELECT * FROM martial_motion_refs WHERE id=? AND move_id=? AND status='locked'",
                          (requested_ref,move_id)).fetchone()
            if ref and martial_product.historical(c,"motion_ref",ref["id"]):ref=None
        else:
            ref=next((r for r in c.execute("SELECT * FROM martial_motion_refs WHERE move_id=? AND status='locked' ORDER BY version DESC",(move_id,))
                      if not martial_product.historical(c,"motion_ref",r["id"])),None)
        if ref is None:raise ValueError("请先确认真人标准动作参考")
        if start<ref["start_time"]-0.001 or end>ref["end_time"]+0.001 or end-start<0.1:
            raise ValueError("视频参考区间必须位于当前锁定的真人片段内")
        start=round(start,3);end=round(end,3);duration=round(duration,3);brief=brief.strip()
        old=c.execute("SELECT * FROM martial_video_plans WHERE move_id=? AND asset_type=? AND status='active' ORDER BY version DESC LIMIT 1",
                      (move_id,kind)).fetchone()
        if old and old["motion_ref_id"]==ref["id"] and all((old[key]==value for key,value in
                (("source_start",start),("source_end",end),("target_duration",duration),("brief",brief)))):
            return move_detail(move_id,user)
        version=c.execute("SELECT COALESCE(MAX(version),0)+1 FROM martial_video_plans WHERE move_id=? AND asset_type=?",
                          (move_id,kind)).fetchone()[0]
        c.execute("UPDATE martial_video_plans SET status='superseded' WHERE move_id=? AND asset_type=? AND status='active'",(move_id,kind))
        pid="mvp_"+secrets.token_hex(8)
        c.execute("INSERT INTO martial_video_plans(id,move_id,asset_type,version,motion_ref_id,source_start,source_end,target_duration,brief,status,created_by,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                  (pid,move_id,kind,version,ref["id"],start,end,duration,brief,"active",user["id"],store.now()))
        store.audit(c,user["id"],"martial.video_plan.save",None,{"move_id":move_id,"asset_type":kind,"version":version})
    return move_detail(move_id,user)


def _move_review(c, move: dict, version: dict, art_status: str) -> dict:
    baseline_version=move["current_version"] or 1
    baseline=_version(c,"martial_move_versions","move_id",move["id"],baseline_version)
    before=baseline["payload"];after=version["payload"]
    diff={field:{"from":before.get(field),"to":after.get(field)} for field in MOVE_FIELDS
          if before.get(field)!=after.get(field)}
    if baseline["source_ref"]!=version["source_ref"]:
        diff["source_ref"]={"from":baseline["source_ref"],"to":version["source_ref"]}
    flags=version.get("review_flags") or []
    submitter=c.execute("SELECT display_name FROM users WHERE id=?",(version.get("submitted_by"),)).fetchone() if version.get("submitted_by") else None
    version["submitted_by_name"]=submitter[0] if submitter else None
    version["diff"]=diff
    version["batch_eligible"]=(version["status"]=="ready_for_approval" and
        art_status=="active" and move["current_version"]>0 and not diff and not flags and
        bool(version.get("submitted_by") and after.get("chinese_action") and after.get("english_action")))
    return version


def _production_blockers(c,move: dict,art: dict,master_status: dict,packages: list[dict]) -> list[str]:
    blockers=[]
    if not martial_product.current_move(c,move["id"]):blockers.append("该招式不在当前生产范围")
    if not art["master_id"]:blockers.append("功法尚未绑定老师")
    if art["status"]!="active":blockers.append("武学事实尚未生效")
    if not move["current_version"]:blockers.append("招式事实待负责人确认")
    elif not all(_version(c,"martial_move_versions","move_id",move["id"],move["current_version"])["payload"].get(field) for field in ("chinese_action","english_action")):
        blockers.append("源资料动作字段为空；请先补齐并确认此招事实")
    if not master_status["character_locked"]:blockers.append("功法老师人物事实未锁定")
    if not master_status["production_ready"]:blockers.append("功法老师缺少负责人确认的角色视觉素材")
    ref=next((r for r in c.execute("SELECT * FROM martial_motion_refs WHERE move_id=? AND status='locked' ORDER BY version DESC",(move["id"],))
              if not martial_product.historical(c,"motion_ref",r["id"])),None)
    if not ref:blockers.append("真人动作参考尚未锁定")
    plans=_video_plans(c,move["id"])
    if ref and any(plans[kind] is None for kind in VIDEO_TYPES):
        blockers.append("请分别规划讲解演示与跟教练跟练视频")
    latest=next((p for p in packages if
        p["facts"].get("move",{}).get("version")==move["current_version"] and
        p["facts"].get("master",{}).get("version")==master_status["version"] and
        p["facts"].get("motion",{}).get("id")== (ref["id"] if ref else None) and
        p["facts"].get("art",{}).get("version")==art["version"] and
        p["facts"].get("video_plans")==_plan_facts(plans) and
        p["facts"].get("package_prompt_version")==PACKAGE_PROMPT_VERSION),None)
    if not latest or latest["status"]!="complete":blockers.append("当前确认版本的 AI Production Package 尚未完成")
    elif latest.get("result",{}).get("missing_inputs"):
        blockers.append("AI Production Package 缺少输入："+"；".join(map(str,latest["result"]["missing_inputs"])))
    if not move["task_id"] or martial_product.historical(c,"task",move["task_id"]):
        blockers.append("Work OS 生产任务尚未形成")
    elif ref and master_status["visual_asset_id"]:
        error=_task_lock_error(c,move["task_id"],master_status["visual_asset_id"],master_status["version"],dict(ref))
        if error:blockers.append(error)
    return blockers


def art_detail(art_id: str, user: dict) -> dict:
    allow(user)
    with store.connect() as c:
        art=_row(c,"martial_arts",art_id)
        art["style_traits"]=store.parse(art["style_traits"],[])
        versions=[dict(r) for r in c.execute("SELECT * FROM martial_art_versions WHERE art_id=? ORDER BY version DESC",(art_id,))]
        for version in versions:version["payload"]=store.parse(version["payload"],{})
        current_map=c.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='martial_business_moves'").fetchone()[0]
        if current_map:
            rows=c.execute("SELECT m.id,b.display_order,b.business_label FROM martial_business_moves b "
                           "JOIN martial_moves m ON m.id=b.move_id WHERE b.martial_art_id=? AND b.is_current=1 "
                           "ORDER BY b.display_order",(art_id,)).fetchall()
            art["planned_moves_source"]=art["planned_moves"]
            art["planned_moves"]=len(rows)
        else:
            rows=c.execute("SELECT id,ordinal AS display_order,'' AS business_label FROM martial_moves WHERE martial_art_id=? ORDER BY ordinal",(art_id,)).fetchall()
        move_ids=[r["id"] for r in rows]
    # Reuse the same business summary that powers the home page, so all status
    # filters agree on the one current production mapping.
    summary={m["id"]:m for m in overview(user)["moves"] if m["martial_art_id"]==art_id}
    moves=[summary[mid] for mid in move_ids if mid in summary]
    counts={"moves":len(moves),"fact_missing":sum(not m["facts_complete"] for m in moves),
            "motion_missing":sum(m["facts_complete"] and not m["motion_locked"] for m in moves),
            "motion_locked":sum(bool(m["motion_locked"]) for m in moves),
            "candidate_count":sum(m["candidate_count"] for m in moves),
            "awaiting_qc":sum(m["awaiting_qc"] for m in moves),
            "revision":sum(bool(m["revision_required"]) for m in moves),
            "finals":sum(m["final_count"] for m in moves)}
    return {"art":art,"versions":versions,"moves":moves,"counts":counts}


def save_art(user: dict, art_id: str, data: dict) -> dict:
    allow(user,True)
    fields=("chinese_name","english_name","volume","category","description","master_id")
    payload={key:str(data.get(key) or "").strip() for key in fields}
    if not all(payload[key] for key in ("chinese_name","english_name","volume","category","master_id")):
        raise ValueError("武学名称、卷、分类和老师不可为空")
    traits=data.get("style_traits") or []
    if not isinstance(traits,list) or len(traits)>20:raise ValueError("武学特点格式无效")
    payload["style_traits"]=[str(x).strip()[:100] for x in traits if str(x).strip()]
    planned=data.get("planned_moves")
    payload["planned_moves"]=None if planned in (None,"") else int(planned)
    if payload["planned_moves"] is not None and not 0<=payload["planned_moves"]<=999:raise ValueError("规划招式数无效")
    if any(len(payload[key])>2000 for key in fields):raise ValueError("武学字段过长")
    source_ref=str(data.get("source_ref") or "").strip()
    if not source_ref or len(source_ref)>1000:raise ValueError("需填写武学资料来源")
    with store.connect() as c:
        art=_row(c,"martial_arts",art_id);_row(c,"martial_masters",payload["master_id"])
        version=max(art["version"],art["draft_version"])+1
        if art["draft_version"]:
            c.execute("UPDATE martial_art_versions SET status='superseded' WHERE art_id=? AND version=? AND status='needs_review'",
                      (art_id,art["draft_version"]))
        c.execute("INSERT INTO martial_art_versions(art_id,version,payload,status,source_ref,created_by,created_at) VALUES(?,?,?,?,?,?,?)",
                  (art_id,version,store.dumps(payload),"needs_review",source_ref,user["id"],store.now()))
        c.execute("UPDATE martial_arts SET draft_version=?,updated_at=? WHERE id=?",(version,store.now(),art_id))
        store.audit(c,user["id"],"martial.art.draft",None,{"art_id":art_id,"version":version})
    return art_detail(art_id,user)


def approve_art(user: dict, art_id: str) -> dict:
    if user["role"] not in {"manager","founder"}:raise PermissionError("武学事实需负责人确认")
    with store.connect() as c:
        art=_row(c,"martial_arts",art_id)
        if not art["draft_version"]:raise ValueError("没有待确认的武学草稿")
        version=art["draft_version"]
        row=_version(c,"martial_art_versions","art_id",art_id,version)
        p=row["payload"]
        c.execute("UPDATE martial_art_versions SET status='active',approved_by=?,approved_at=? WHERE art_id=? AND version=?",
                  (user["id"],store.now(),art_id,version))
        c.execute("UPDATE martial_arts SET chinese_name=?,english_name=?,volume=?,category=?,description=?,style_traits=?,master_id=?,planned_moves=?,source_ref=?,status='active',version=?,draft_version=0,updated_at=? WHERE id=?",
                  (p["chinese_name"],p["english_name"],p["volume"],p["category"],p["description"],store.dumps(p["style_traits"]),
                   p["master_id"],p["planned_moves"],row["source_ref"],version,store.now(),art_id))
        store.audit(c,user["id"],"martial.art.approve",None,{"art_id":art_id,"version":version})
    return art_detail(art_id,user)


def move_detail(move_id: str, user: dict) -> dict:
    allow(user)
    with store.connect() as c:
        move=_row(c,"martial_moves",move_id)
        mapping=martial_product.current_mapping(c,move_id)
        if mapping and not mapping["is_current"] and user["role"]=="employee":raise KeyError(move_id)
        art=_row(c,"martial_arts",move["martial_art_id"])
        version=move["draft_version"] or move["current_version"]
        current=_version(c,"martial_move_versions","move_id",move_id,version) if version else None
        effective=_version(c,"martial_move_versions","move_id",move_id,move["current_version"]) if move["current_version"] else None
        for row in (current,effective):
            if row:
                _move_review(c,move,row,art["status"])
                row["import_confirmation"]=_import_confirmation(c,"move",move_id,row["version"])
        versions=[_move_review(c,move,_version(c,"martial_move_versions","move_id",move_id,r["version"]),art["status"])
                  for r in c.execute("SELECT version FROM martial_move_versions WHERE move_id=? ORDER BY version DESC",(move_id,))]
        for version_row in versions:version_row["import_confirmation"]=_import_confirmation(c,"move",move_id,version_row["version"])
        # The current business label may differ from the imported source name.
        # Project it only into the normal view; versions retain exact source.
        if mapping and mapping["business_label"]:
            for row in (current,effective):
                if row:row["payload"]["chinese_name"]=mapping["business_label"]
        motions=[dict(r) for r in c.execute("SELECT * FROM martial_motion_refs WHERE move_id=? ORDER BY version DESC",(move_id,))
                 if not martial_product.historical(c,"motion_ref",r["id"])]
        for m in motions:
            m["key_moments"]=store.parse(m["key_moments"],[])
            asset=store.record(c,"assets",m["video_asset_id"])
            m["asset_name"]=asset["name"] if asset else m["video_asset_id"]
        locked=next((m for m in motions if m["status"]=="locked"),None)
        video_plans=_video_plans(c,move_id)
        packages=[dict(r) for r in c.execute("SELECT * FROM martial_packages WHERE move_id=? ORDER BY created_at DESC,rowid DESC",(move_id,))
                  if not martial_product.historical(c,"package",r["id"])][:8]
        for p in packages:
            p["result"]=store.parse(p["result"],{})
            p["facts"]=store.parse(p["facts"],{})
            if user["role"]=="employee":p.pop("provider_job_id",None)
        newest=packages[0] if packages else None
        pf=newest["facts"] if newest else {}
        package_facts_current=_package_facts_current(c,move,pf,locked,video_plans) if newest else False
        package_current=bool(package_facts_current and newest["status"]=="complete" and
                             not newest["result"].get("missing_inputs"))
        package_pending_current=bool(package_facts_current and newest["status"] in {"queued","dispatching","unknown_submission"})
        media=[dict(r) for r in c.execute("SELECT * FROM martial_media_jobs WHERE move_id=? ORDER BY created_at DESC",(move_id,))
               if not martial_product.historical(c,"media_job",r["id"])][:30]
        for m in media:
            m["current_context"]=_current_job_error(c,m) is None
            m["technical_report"]=store.parse(m["technical_report"],{})
            m["segments"]=store.parse(m.pop("segments_json",None),[])
            m["edit_refs"]=store.parse(m.pop("edit_refs_json",None),{})
            m["background_ref"]=store.parse(m.pop("background_ref_json",None),{})
            m["segment_progress"]=store.parse(m["segment_progress"],[])
            m.pop("prompt",None)  # a production prompt is shown through the approved package
            if user["role"]=="employee":
                for private in ("provider","model","provider_job_id","local_job_id","local_media_id",
                                "idempotency_key","request_key","quote_source","prompt_hash"):
                    m.pop(private,None)
            selection=c.execute("SELECT reason,selected_by,selected_at FROM martial_selections WHERE media_job_id=?",(m["id"],)).fetchone()
            m["selection"]=dict(selection) if selection else None
            m["qc"]=[dict(r) for r in c.execute("SELECT stage,result,findings,reference_comparison,checks,issue_ranges,major_dispute,reviewer_id,created_at FROM martial_qc WHERE media_job_id=? ORDER BY created_at",(m["id"],))]
            for qc in m["qc"]:
                qc["checks"]=store.parse(qc["checks"],{})
                qc["issue_ranges"]=store.parse(qc["issue_ranges"],[])
            martial_review=next((q for q in reversed(m["qc"]) if q["stage"]=="martial"),None)
            revision=c.execute("SELECT id,payload,created_at FROM martial_revision_packages WHERE media_job_id=?",(m["id"],)).fetchone()
            m["revision_package"]={"id":revision["id"],"payload":store.parse(revision["payload"],{}),"created_at":revision["created_at"]} if revision else None
            if martial_review:
                m["martial_status"]="revision_required" if martial_review["result"]=="fail" else martial_review["result"]
            else:m["martial_status"]="qc_pending" if m["status"]=="succeeded" else m["status"]
            existing_final=c.execute("SELECT id,status FROM martial_final_assets WHERE media_job_id=?",(m["id"],)).fetchone()
            m["final_asset_id"]=existing_final["id"] if existing_final else None
            m["finalization_blocker"]=_finalization_blocker(c,m,martial_review,user) if martial_review and martial_review["result"]=="pass" and not existing_final else None
        finals=[dict(r) for r in c.execute("SELECT * FROM martial_final_assets WHERE move_id=? ORDER BY created_at DESC",(move_id,))
                if not martial_product.historical(c,"final_asset",r["id"])]
        for final in finals:
            original=_row(c,"martial_media_jobs",final["media_job_id"])
            final["current"]=final["status"]=="active" and _current_final_error(c,original) is None
        final_types=sorted({final["asset_type"] for final in finals if final["current"]})
        master_status=_master_status(c,art["master_id"])
        task=dict(store.record(c,"tasks",move["task_id"])) if move["task_id"] and not martial_product.historical(c,"task",move["task_id"]) else None
        if task:
            task={key:task[key] for key in ("id","status","budget_cap","assignee_id")}
            task["budget_unlimited"]=_budget_unlimited(c,task["id"])
        return {"move":move,"art":art,"business":mapping,"business_label":mapping["business_label"] if mapping else "",
                "version":current,"effective_version":effective,"versions":versions,"motions":motions,
                "video_plans":video_plans,
                "packages":packages,"package_current":package_current,"package_pending_current":package_pending_current,
                "media":media,"finals":finals,
                "final_types":final_types,"master_status":master_status,
                "production_blockers":_production_blockers(c,move,art,master_status,packages),"task":task}


def master_detail(master_id: str, user: dict) -> dict:
    allow(user)
    with store.connect() as c:
        master=_row(c,"martial_masters",master_id)
        versions=[dict(r) for r in c.execute("SELECT * FROM martial_master_versions WHERE master_id=? ORDER BY version DESC",(master_id,))]
        for v in versions:v["payload"]=store.parse(v["payload"],{})
        pending=[{"id":r["id"],"chinese_name":r["chinese_name"],"draft_version":r["draft_version"]}
                 for r in c.execute("SELECT id,chinese_name,draft_version FROM martial_arts WHERE draft_version>0")
                 if _version(c,"martial_art_versions","art_id",r["id"],r["draft_version"])["payload"].get("master_id")==master_id]
        available_actions=[dict(r) for r in c.execute("""SELECT f.id AS final_id,f.move_id,f.asset_id,j.master_version,
          m.ordinal,a.chinese_name AS art_name FROM martial_final_assets f
          JOIN martial_media_jobs j ON j.id=f.media_job_id
          JOIN martial_moves m ON m.id=f.move_id JOIN martial_arts a ON a.id=m.martial_art_id
          WHERE a.master_id=? AND f.asset_type='teaching' AND f.status='active'
            AND j.master_version=? ORDER BY a.chinese_name,m.ordinal""",(master_id,master["current_version"]))
          if not martial_product.historical(c,"final_asset",r["final_id"])]
        return {"master":master,"versions":versions,"master_status":_master_status(c,master_id),
                "asset_imports":martial_assets.master_imports(c,master_id),
                "available_actions":available_actions,
                "martial_arts":[dict(r) for r in c.execute("SELECT id,chinese_name,english_name FROM martial_arts WHERE master_id=?",(master_id,))],
                "pending_martial_arts":pending}


def overview(user: dict) -> dict:
    allow(user)
    with store.connect() as c:
        art_columns={r[1] for r in c.execute("PRAGMA table_info(martial_arts)")}
        art_order="display_order,chinese_name" if "display_order" in art_columns else "CASE id WHEN 'beginner' THEN 0 ELSE 1 END,chinese_name"
        arts=[dict(r) for r in c.execute("SELECT * FROM martial_arts ORDER BY "+art_order)]
        masters=[dict(r) for r in c.execute("SELECT id,name,species,current_version,draft_version FROM martial_masters ORDER BY name")]
        for master in masters:
            master.update(_master_status(c,master["id"]))
            current=_version(c,"martial_master_versions","master_id",master["id"],master["current_version"])
            master["chinese_name"]=current["payload"].get("chinese_name") or ""
            master["english_name"]=current["payload"].get("english_name") or master["name"]
        has_mapping=c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='martial_business_moves'").fetchone() is not None
        if has_mapping:
            mapped={r["move_id"]:dict(r) for r in c.execute("SELECT * FROM martial_business_moves WHERE is_current=1")}
            for art in arts:
                art["planned_moves_source"]=art["planned_moves"]
                art["planned_moves"]=sum(x["martial_art_id"]==art["id"] for x in mapped.values())
        else:mapped=None
        moves=[]
        for r in c.execute("SELECT * FROM martial_moves ORDER BY martial_art_id,ordinal"):
            m=dict(r);v=m["draft_version"] or m["current_version"]
            if mapped is not None and m["id"] not in mapped:continue
            art_status=next(a["status"] for a in arts if a["id"]==m["martial_art_id"])
            m["version"]=_move_review(c,m,_version(c,"martial_move_versions","move_id",m["id"],v),art_status) if v else None
            m["effective_version"]=_version(c,"martial_move_versions","move_id",m["id"],m["current_version"]) if m["current_version"] else None
            effective_payload=m["effective_version"]["payload"] if m["effective_version"] else {}
            m["facts_complete"]=bool(effective_payload.get("chinese_action") and effective_payload.get("english_action"))
            m["import_confirmation"]=_import_confirmation(c,"move",m["id"],m["current_version"]) if m["current_version"] else None
            m["business"]=mapped.get(m["id"]) if mapped is not None else None
            m["display_order"]=m["business"]["display_order"] if m["business"] else m["ordinal"]
            m["business_label"]=m["business"]["business_label"] if m["business"] else ""
            if m["business_label"]:
                if m["version"]:m["version"]["payload"]["chinese_name"]=m["business_label"]
                if m["effective_version"]:m["effective_version"]["payload"]["chinese_name"]=m["business_label"]
            motions=[row for row in c.execute("SELECT id,status,version FROM martial_motion_refs WHERE move_id=? ORDER BY version DESC",(m["id"],))
                     if not martial_product.historical(c,"motion_ref",row["id"])]
            locked_ref=next((dict(row) for row in motions if row["status"]=="locked"),None)
            plans=_video_plans(c,m["id"])
            packages=[row for row in c.execute("SELECT id,status,facts,result FROM martial_packages WHERE move_id=? ORDER BY created_at DESC,rowid DESC",(m["id"],))
                      if not martial_product.historical(c,"package",row["id"])]
            latest_package=packages[0] if packages else None
            package_matches=bool(latest_package and _package_facts_current(c,m,store.parse(latest_package["facts"],{}),locked_ref,plans))
            jobs=[row for row in c.execute("SELECT * FROM martial_media_jobs WHERE move_id=? ORDER BY created_at DESC,rowid DESC",(m["id"],))
                  if not martial_product.historical(c,"media_job",row["id"]) and
                  _current_job_error(c,dict(row)) is None]
            m["motion_locked"]=any(row["status"]=="locked" for row in motions)
            m["package_ready"]=bool(package_matches and latest_package["status"]=="complete" and
                                    not store.parse(latest_package["result"],{}).get("missing_inputs"))
            m["package_pending_current"]=bool(package_matches and latest_package["status"] in {"queued","dispatching","unknown_submission"})
            m["media_counts"]={}
            for job in jobs:
                key=job["asset_type"]+":"+job["status"]
                m["media_counts"][key]=m["media_counts"].get(key,0)+1
            m["candidate_count"]=sum(bool(job["candidate_asset_id"]) for job in jobs)
            m["awaiting_qc"]=sum(job["status"]=="succeeded" and not c.execute(
                "SELECT 1 FROM martial_qc WHERE media_job_id=? AND stage='martial'",(job["id"],)).fetchone() for job in jobs)
            latest_by_type={}
            for job in jobs:latest_by_type.setdefault(job["asset_type"],job)
            m["unresolved_revision"]=any((qc and qc["result"]=="fail") for job in latest_by_type.values()
                for qc in [c.execute("SELECT result FROM martial_qc WHERE media_job_id=? AND stage='martial' ORDER BY created_at DESC LIMIT 1",(job["id"],)).fetchone()])
            m["qc_failed"]=sum(bool(qc and qc["result"]=="fail") for job in latest_by_type.values()
                for qc in [c.execute("SELECT result FROM martial_qc WHERE media_job_id=? AND stage='martial' ORDER BY created_at DESC LIMIT 1",(job["id"],)).fetchone()])
            latest_qc=c.execute("SELECT result FROM martial_qc WHERE media_job_id=? AND stage='martial' ORDER BY created_at DESC LIMIT 1",
                                (jobs[0]["id"],)).fetchone() if jobs else None
            m["revision_required"]=m["unresolved_revision"]
            active_finals=[dict(row) for row in c.execute("SELECT id,asset_type,media_job_id,status FROM martial_final_assets WHERE move_id=?",(m["id"],))
                           if row["status"]=="active" and not martial_product.historical(c,"final_asset",row["id"])]
            m["final_types"]=sorted({row["asset_type"] for row in active_finals
                                      if _current_final_error(c,_row(c,"martial_media_jobs",row["media_job_id"])) is None})
            m["final_count"]=len(m["final_types"])
            m["legacy_final_count"]=len(active_finals)-m["final_count"]
            if all(kind in m["final_types"] for kind in VIDEO_TYPES):m["production_status"]="completed"
            elif m["revision_required"]:m["production_status"]="revision"
            elif jobs and jobs[0]["status"]=="succeeded" and not latest_qc:m["production_status"]="awaiting_qc"
            elif jobs and jobs[0]["status"] in {"queued","dispatching","submitted","running","download_pending","technical_check","unknown_submission"}:
                m["production_status"]="generating"
            elif latest_qc and latest_qc["result"]=="pass":m["production_status"]="awaiting_final"
            elif m["candidate_count"]:m["production_status"]="awaiting_qc"
            elif not m["facts_complete"]:m["production_status"]="missing_fact"
            elif not m["motion_locked"]:m["production_status"]="awaiting_motion"
            elif not m["package_ready"]:m["production_status"]="awaiting_ai"
            else:m["production_status"]="awaiting_video"
            moves.append(m)
        moves.sort(key=lambda m:(next((i for i,a in enumerate(arts) if a["id"]==m["martial_art_id"]),999),m["display_order"]))
        job_ids={job["id"] for move in moves for job in c.execute("SELECT id FROM martial_media_jobs WHERE move_id=?",(move["id"],))
                 if not martial_product.historical(c,"media_job",job["id"])}
        pending_packages=sum(row["status"] in {"queued","dispatching"} for move in moves
                             for row in c.execute("SELECT id,status FROM martial_packages WHERE move_id=?",(move["id"],))
                             if not martial_product.historical(c,"package",row["id"]))
        counts={
            "move_drafts":sum(bool(m["draft_version"]) for m in moves),
            "move_confirmed":sum(bool(m["current_version"]) for m in moves),
            "masters_ready":sum(bool(m["production_ready"]) for m in masters),
            "fact_missing":sum(not m["facts_complete"] for m in moves),
            "motion_missing":sum(m["facts_complete"] and not m["motion_locked"] for m in moves),
            "motion_locked":sum(m["motion_locked"] for m in moves),
            "awaiting_video":sum(m["facts_complete"] and not any(k.endswith(":succeeded") for k in m["media_counts"]) for m in moves),
            "packages_pending":pending_packages,
            "media_pending":sum(row["status"] in {"queued","dispatching","submitted","running","download_pending"}
                                 for job_id in job_ids for row in c.execute("SELECT status FROM martial_media_jobs WHERE id=?",(job_id,))),
            "qc_pending":sum(m["awaiting_qc"] for m in moves),
            "revision":sum(bool(m["revision_required"]) for m in moves),
            "finals":sum(m["final_count"] for m in moves),
        }
        return {"arts":arts,"masters":masters,"moves":moves,"counts":counts,"price_profiles":{k:v["reservation"] for k,v in PRICE.items()},
                "price_source":PRICE_SOURCE}


def available_assets(user: dict) -> dict:
    allow(user)
    with store.connect() as c:
        rows=c.execute("""SELECT id,type,name,version,sha256,storage_ref FROM assets a
            WHERE project_id='wuxiang' AND status='active'
              AND type IN ('image','character','motion_reference','video','audio')
              AND (type NOT IN ('motion_reference','video') OR
                   (created_by!='local-connector' AND NOT EXISTS (
                       SELECT 1 FROM martial_media_jobs WHERE candidate_asset_id=a.id)))
            ORDER BY created_at DESC LIMIT 100""")
        roots=store.asset_roots("wuxiang")
        assets=[]
        for row in rows:
            item=dict(row)
            if martial_product.historical(c,"asset",item["id"]):continue
            if item["type"] in {"motion_reference","video"}:
                refs=c.execute("SELECT id,move_id FROM martial_motion_refs WHERE video_asset_id=?",(item["id"],)).fetchall()
                if refs and not any(martial_product.current_move(c,ref["move_id"]) and
                                    not martial_product.historical(c,"motion_ref",ref["id"]) for ref in refs):continue
            try:path=Path(item.pop("storage_ref")).resolve(strict=True)
            except (OSError,RuntimeError):continue
            if any(path.is_relative_to(root) for root in roots):assets.append(item)
        return {"assets":assets}


def save_move(user: dict, data: dict) -> dict:
    allow(user,True)
    art_id=str(data.get("martial_art_id") or "")
    source_ref=str(data.get("source_ref") or "").strip()
    if not source_ref or len(source_ref)>1000:raise ValueError("需填写本招式原文来源")
    payload={k:str(data.get(k) or "").strip() for k in MOVE_FIELDS}
    if not payload["chinese_name"] or len(payload["chinese_name"])>120:
        raise ValueError("招式中文名称无效")
    if any(len(v)>3000 for v in payload.values()):raise ValueError("招式字段过长")
    with store.connect() as c:
        _row(c,"martial_arts",art_id)
        existing=data.get("move_id")
        if existing:
            move=_row(c,"martial_moves",str(existing))
            if move["martial_art_id"]!=art_id:raise ValueError("不能改变招式所属武学")
            move_id=move["id"];version=max(move["current_version"],move["draft_version"])+1
            if move["draft_version"]:
                c.execute("UPDATE martial_move_versions SET status='superseded' WHERE move_id=? AND version=? AND status IN ('needs_review','ready_for_approval','returned')",
                          (move_id,move["draft_version"]))
        else:
            ordinal=int(data.get("order") or 0)
            if ordinal<1 or ordinal>999:raise ValueError("招式顺序无效")
            move_id="mv_"+secrets.token_hex(8);version=1
            c.execute("INSERT INTO martial_moves(id,martial_art_id,ordinal,draft_version,created_at,updated_at) VALUES(?,?,?,?,?,?)",
                      (move_id,art_id,ordinal,1,store.now(),store.now()))
        c.execute("INSERT INTO martial_move_versions(move_id,version,payload,status,source_ref,created_by,created_at) VALUES(?,?,?,?,?,?,?)",
                  (move_id,version,store.dumps(payload),"needs_review",source_ref,user["id"],store.now()))
        c.execute("UPDATE martial_moves SET draft_version=?,updated_at=? WHERE id=?",(version,store.now(),move_id))
        store.audit(c,user["id"],"martial.move.draft",None,{"move_id":move_id,"version":version,"source_ref":source_ref})
    return move_detail(move_id,user)


def submit_move(user: dict, move_id: str, data: dict) -> dict:
    allow(user,True)
    if user["role"]!="employee":raise PermissionError("只有武学岗位员工可提交已核对的招式事实")
    note=str(data.get("note") or "").strip()
    if len(note)>1000:raise ValueError("核对说明过长")
    flags=data.get("review_flags") or []
    if not isinstance(flags,list) or any(not isinstance(x,str) or x not in {"question","conflict"} for x in flags):
        raise ValueError("疑问或冲突标记无效")
    flags=sorted(set(flags))
    with store.connect() as c:
        move=_row(c,"martial_moves",move_id)
        if not move["draft_version"]:raise ValueError("没有可提交的招式草稿")
        version=_version(c,"martial_move_versions","move_id",move_id,move["draft_version"])
        if version["status"]=="returned":raise ValueError("招式已退回，请先保存修订草稿再重新提交")
        if version["status"]!="needs_review":raise ValueError("招式已提交，请等待负责人处理")
        payload=version["payload"]
        if not payload.get("chinese_action") or not payload.get("english_action"):
            raise ValueError("请先补齐中文和英文动作事实，再提交确认")
        c.execute("UPDATE martial_move_versions SET status='ready_for_approval',submitted_by=?,submitted_at=?,submission_note=?,review_flags=?,rejection_reason='' WHERE move_id=? AND version=?",
                  (user["id"],store.now(),note,store.dumps(flags),move_id,version["version"]))
        store.audit(c,user["id"],"martial.move.submit",None,{"move_id":move_id,"version":version["version"],"review_flags":flags})
    return move_detail(move_id,user)


def reject_move(user: dict, move_id: str, reason: str) -> dict:
    if user["role"] not in {"manager","founder"}:raise PermissionError("只有负责人可退回招式")
    reason=str(reason or "").strip()
    if len(reason)<4 or len(reason)>1000:raise ValueError("请填写具体退回原因")
    with store.connect() as c:
        move=_row(c,"martial_moves",move_id)
        if not move["draft_version"]:raise ValueError("没有待确认招式")
        version=_version(c,"martial_move_versions","move_id",move_id,move["draft_version"])
        if version["status"]!="ready_for_approval":raise ValueError("招式尚未提交负责人确认")
        c.execute("UPDATE martial_move_versions SET status='returned',rejected_by=?,rejected_at=?,rejection_reason=? WHERE move_id=? AND version=?",
                  (user["id"],store.now(),reason,move_id,version["version"]))
        store.audit(c,user["id"],"martial.move.reject",None,{"move_id":move_id,"version":version["version"],"reason":reason})
    return move_detail(move_id,user)


def _approve_move(c,user: dict,move: dict) -> int:
    if not move["draft_version"]:raise ValueError("没有待确认草稿")
    version=move["draft_version"]
    row=_version(c,"martial_move_versions","move_id",move["id"],version)
    if row["status"]!="ready_for_approval" or not row.get("submitted_by"):
        raise ValueError("请先由武学岗位员工核对并提交招式事实，再由负责人确认")
    if not row["payload"].get("chinese_action") or not row["payload"].get("english_action"):
        raise ValueError("中文或英文动作事实缺失，不可确认为标准招式")
    c.execute("UPDATE martial_move_versions SET status='active',approved_by=?,approved_at=? WHERE move_id=? AND version=?",
              (user["id"],store.now(),move["id"],version))
    c.execute("UPDATE martial_moves SET current_version=?,draft_version=0,updated_at=? WHERE id=?",(version,store.now(),move["id"]))
    store.audit(c,user["id"],"martial.move.approve",None,{"move_id":move["id"],"version":version})
    return version


def approve_move(user: dict, move_id: str) -> dict:
    if user["role"] not in {"manager","founder"}:raise PermissionError("招式事实需负责人确认")
    with store.connect() as c:
        move=_row(c,"martial_moves",move_id)
        _approve_move(c,user,move)
    return move_detail(move_id,user)


def batch_approve_moves(user: dict, art_id: str, move_ids: list[str]) -> dict:
    if user["role"] not in {"manager","founder"}:raise PermissionError("批量确认需负责人操作")
    if (not isinstance(move_ids,list) or not 1<=len(move_ids)<=100 or
        any(not isinstance(x,str) or not re.fullmatch(r"mv_[a-z0-9_]+",x) for x in move_ids) or
        len(set(move_ids))!=len(move_ids)):
        raise ValueError("请选择 1 至 100 条不重复招式")
    with store.connect() as c:
        art=_row(c,"martial_arts",art_id)
        if art["status"]!="active" or art["draft_version"]:raise ValueError("武学事实尚未确认，不可批量确认招式")
        moves=[]
        for move_id in move_ids:
            move=_row(c,"martial_moves",str(move_id))
            if move["martial_art_id"]!=art_id or not move["draft_version"]:
                raise ValueError(f"招式 {move_id} 不属于该武学或没有待确认版本")
            version=_version(c,"martial_move_versions","move_id",move_id,move["draft_version"])
            review=_move_review(c,move,version,art["status"])
            if not review["batch_eligible"]:
                raise ValueError(f"招式 {move_id} 有内容变化、疑问、冲突或未经员工核对；请逐条查看确认")
            moves.append(move)
        for move in moves:_approve_move(c,user,move)
        store.audit(c,user["id"],"martial.move.batch_approve",None,{"art_id":art_id,"move_ids":move_ids})
    return {"approved_move_ids":move_ids,"count":len(move_ids)}


def upload_master_asset(user: dict, master_id: str, data: dict) -> dict:
    allow(user,True)
    field=str(data.get("field") or "")
    if field not in {"portrait","front_view","side_view","back_view","turnaround","costume","digital_model","voice_preview"}:
        raise ValueError("无效老师素材类型")
    upload=data.get("upload") or {}
    try:content=base64.b64decode(upload["base64"],validate=True)
    except (KeyError,ValueError):raise ValueError("上传素材无效")
    if len(content)>MARTIAL_FILE_LIMIT:raise ValueError("武学素材超过 40MB 上限")
    name=str(upload.get("name") or "")
    if field=="voice_preview" and Path(name).suffix.lower() not in {".wav",".mp3"}:
        raise ValueError("语音样音需要 WAV 或 MP3")
    if field=="digital_model" and (Path(name).suffix.lower()!=".glb" or not _glb_header_ok(content)):
        raise ValueError("数字人模型需要有效 GLB 文件")
    if field not in {"voice_preview","digital_model"} and Path(name).suffix.lower() not in {".png",".jpg",".jpeg",".webp"}:
        raise ValueError("老师形象需要图片文件")
    if field not in {"voice_preview","digital_model"} and not _image_header_ok(content,Path(name).suffix):
        raise ValueError("老师形象文件不是可识别的图片")
    asset=store.register_production_asset("wuxiang","character",name,content,user["id"])
    return attach_master_asset(user,master_id,{"field":field,"asset_id":asset["id"],"source_ref":data.get("source_ref")})


def attach_master_asset(user: dict, master_id: str, data: dict) -> dict:
    allow(user,True)
    field=str(data.get("field") or "")
    if field not in {"portrait","front_view","side_view","back_view","turnaround","costume","digital_model","voice_preview"}:
        raise ValueError("无效老师素材类型")
    asset_id=str(data.get("asset_id") or "")
    with store.connect() as c:
        asset=store.record(c,"assets",asset_id)
        expected=({"audio"} if field=="voice_preview" else {"character"} if field=="digital_model" else {"image","character"})
        if asset["project_id"]!="wuxiang" or asset["status"]!="active" or asset["type"] not in expected:
            raise ValueError("只能关联本项目已登记的同类型素材")
        path=Path(asset["storage_ref"]).resolve(strict=True)
        roots=store.asset_roots("wuxiang")
        if not any(path.is_relative_to(root) for root in roots):raise ValueError("老师素材需要在受控工作区")
        master=_row(c,"martial_masters",master_id)
        if master["draft_version"]:
            version=master["draft_version"]
            row=_version(c,"martial_master_versions","master_id",master_id,version)
            payload=row["payload"]
            payload[field]=asset_id
            c.execute("UPDATE martial_master_versions SET payload=?,source_ref=? WHERE master_id=? AND version=? AND status='needs_review'",
                      (store.dumps(payload),str(data.get("source_ref") or row["source_ref"]),master_id,version))
        else:
            current=_version(c,"martial_master_versions","master_id",master_id,master["current_version"])
            version=master["current_version"]+1
            payload=dict(current["payload"]);payload[field]=asset_id
            c.execute("INSERT INTO martial_master_versions(master_id,version,payload,status,source_ref,created_by,created_at) VALUES(?,?,?,?,?,?,?)",
                      (master_id,version,store.dumps(payload),"needs_review",str(data.get("source_ref") or current["source_ref"]),user["id"],store.now()))
            c.execute("UPDATE martial_masters SET draft_version=?,updated_at=? WHERE id=?",(version,store.now(),master_id))
        store.audit(c,user["id"],"martial.master.asset_draft",None,{"master_id":master_id,"version":version,"field":field,"asset_id":asset_id})
    return master_detail(master_id,user)


def save_master_profile(user: dict, master_id: str, data: dict) -> dict:
    allow(user,True)
    fields={k:data.get(k) for k in MASTER_FIELDS if k in data}
    if not fields:raise ValueError("没有需要修改的资料")
    with store.connect() as c:
        master=_row(c,"martial_masters",master_id)
        version=master["draft_version"] or master["current_version"]+1
        if master["draft_version"]:
            current=_version(c,"martial_master_versions","master_id",master_id,version)
        else:
            current=_version(c,"martial_master_versions","master_id",master_id,master["current_version"])
        payload=dict(current["payload"])
        for key,value in fields.items():
            if key in {"portrait","front_view","side_view","back_view","turnaround","costume","digital_model","voice_preview"}:
                raise ValueError("素材只能通过上传新版本修改")
            if key=="forbidden_changes":
                if not isinstance(value,list) or len(value)>30:raise ValueError("禁止变化项无效")
                payload[key]=[str(x)[:250] for x in value]
            else:
                if not isinstance(value,str) or len(value)>5000:raise ValueError("老师资料无效")
                payload[key]=value.strip()
        source_ref=str(data.get("source_ref") or current["source_ref"])
        if master["draft_version"]:
            c.execute("UPDATE martial_master_versions SET payload=?,source_ref=? WHERE master_id=? AND version=?",
                      (store.dumps(payload),source_ref,master_id,version))
        else:
            c.execute("INSERT INTO martial_master_versions(master_id,version,payload,status,source_ref,created_by,created_at) VALUES(?,?,?,?,?,?,?)",
                      (master_id,version,store.dumps(payload),"needs_review",source_ref,user["id"],store.now()))
            c.execute("UPDATE martial_masters SET draft_version=?,updated_at=? WHERE id=?",(version,store.now(),master_id))
        store.audit(c,user["id"],"martial.master.profile_draft",None,{"master_id":master_id,"version":version})
    return master_detail(master_id,user)


def approve_master(user: dict, master_id: str) -> dict:
    if user["role"] not in {"manager","founder"}:raise PermissionError("定版角色需要负责人批准")
    with store.connect() as c:
        master=_row(c,"martial_masters",master_id)
        if not master["draft_version"]:raise ValueError("没有待批准的新版本")
        version=master["draft_version"]
        c.execute("UPDATE martial_master_versions SET status='locked',approved_by=?,approved_at=? WHERE master_id=? AND version=?",
                  (user["id"],store.now(),master_id,version))
        c.execute("UPDATE martial_masters SET current_version=?,draft_version=0,updated_at=? WHERE id=?",(version,store.now(),master_id))
        store.audit(c,user["id"],"martial.master.lock",None,{"master_id":master_id,"version":version})
    with store.connect() as c:
        waiting=[r[0] for r in c.execute("SELECT p.id FROM martial_packages p JOIN martial_moves m ON p.move_id=m.id JOIN martial_arts a ON m.martial_art_id=a.id WHERE a.master_id=? AND p.status='complete' AND p.task_id IS NULL AND p.master_version=?",(master_id,version))]
    for package_id in waiting:_materialize_task(package_id)
    return master_detail(master_id,user)


def _atoms(data: bytes, begin: int, end: int):
    """Read ISO BMFF atom boundaries without trusting user-supplied box lengths."""
    offset=begin
    while offset+8<=end:
        size=struct.unpack_from(">I",data,offset)[0]
        kind=data[offset+4:offset+8]
        header=8
        if size==1:
            if offset+16>end:raise ValueError("视频文件结构不完整")
            size=struct.unpack_from(">Q",data,offset+8)[0];header=16
        elif size==0:size=end-offset
        if size<header or offset+size>end:raise ValueError("视频文件结构无效")
        yield kind,offset+header,offset+size
        offset+=size


def _media_duration(body: bytes) -> float | None:
    if len(body)<20:return None
    version=body[0]
    if version==0 and len(body)>=20:
        scale=struct.unpack_from(">I",body,12)[0]
        ticks=struct.unpack_from(">I",body,16)[0]
    elif version==1 and len(body)>=32:
        scale=struct.unpack_from(">I",body,20)[0]
        ticks=struct.unpack_from(">Q",body,24)[0]
    else:return None
    return ticks/scale if scale else None


def _probe_video(content: bytes) -> dict:
    """Read real MP4/MOV container duration and video dimensions; FPS may remain unknown."""
    if len(content)<64:raise ValueError("真人动作需为 MP4/MOV 视频")
    top=list(_atoms(content,0,len(content)))
    if not any(k==b"mdat" and end>start for k,start,end in top):
        raise ValueError("视频缺少媒体数据")
    moov=next(((start,end) for k,start,end in top if k==b"moov"),None)
    if not moov:raise ValueError("视频缺少可读取的元数据")
    return _probe_moov(content[moov[0]:moov[1]])


def _probe_video_file(path: Path) -> dict:
    """Inspect only BMFF headers and a bounded moov atom, never the entire video."""
    size=path.stat().st_size
    if size<64 or size>store.MOTION_FILE_LIMIT:raise ValueError("真人动作视频大小无效")
    moov=None;has_media=False
    with path.open("rb") as stream:
        offset=0
        while offset+8<=size:
            stream.seek(offset)
            header=stream.read(8)
            atom_size,kind=struct.unpack(">I4s",header)
            header_size=8
            if atom_size==1:
                extended=stream.read(8)
                if len(extended)!=8:raise ValueError("视频文件结构不完整")
                atom_size=struct.unpack(">Q",extended)[0];header_size=16
            elif atom_size==0:atom_size=size-offset
            if atom_size<header_size or offset+atom_size>size:
                raise ValueError("视频文件结构无效")
            if kind==b"mdat" and atom_size>header_size:has_media=True
            if kind==b"moov" and moov is None:
                body_size=atom_size-header_size
                if body_size>32_000_000:raise ValueError("视频元数据过大")
                moov=(offset+header_size,body_size)
            offset+=atom_size
        if not has_media:raise ValueError("视频缺少媒体数据")
        if moov is None:raise ValueError("视频缺少可读取的元数据")
        stream.seek(moov[0]);body=stream.read(moov[1])
        if len(body)!=moov[1]:raise ValueError("视频文件结构不完整")
    return _probe_moov(body)


def _probe_moov(content: bytes) -> dict:
    movie_duration=None
    dimensions=[]
    for kind,start,end in _atoms(content,0,len(content)):
        if kind==b"mvhd":movie_duration=_media_duration(content[start:end])
        if kind!=b"trak":continue
        width=height=0;video_track=False;track_duration=None;rotation=0
        for tk,tstart,tend in _atoms(content,start,end):
            if tk==b"tkhd" and tend-tstart>=8:
                width=struct.unpack_from(">I",content,tend-8)[0]/65536
                height=struct.unpack_from(">I",content,tend-4)[0]/65536
                # The display matrix is the only reliable container-level
                # rotation hint.  An absent/unusual matrix leaves it unknown.
                box=content[tstart:tend]
                matrix_at=52 if box[0] else 40
                if len(box)>=matrix_at+20:
                    a,b,c,d=(struct.unpack_from(">i",box,matrix_at+offset)[0]/65536
                             for offset in (0,4,12,16))
                    if abs(a)<0.01 and abs(d)<0.01:
                        if abs(b-1)<0.01 and abs(c+1)<0.01:rotation=90
                        elif abs(b+1)<0.01 and abs(c-1)<0.01:rotation=270
                    elif abs(a+1)<0.01 and abs(d+1)<0.01:rotation=180
            if tk==b"mdia":
                for mk,mstart,mend in _atoms(content,tstart,tend):
                    if mk==b"hdlr" and mend-mstart>=12:
                        video_track=content[mstart+8:mstart+12]==b"vide"
                    if mk==b"mdhd":track_duration=_media_duration(content[mstart:mend])
        if video_track and width>0 and height>0:
            dimensions.append((round(width),round(height),track_duration,rotation))
    if not dimensions:raise ValueError("视频元数据缺少画面宽高")
    width,height,track_duration,rotation=dimensions[0]
    duration=track_duration or movie_duration
    if not duration or not 0<duration<=3600 or not 1<=width<=8192 or not 1<=height<=8192:
        raise ValueError("视频时长或画面尺寸无效")
    display_width,display_height=(height,width) if rotation in {90,270} else (width,height)
    frame_orientation=("横屏" if display_width>display_height else
                       "竖屏" if display_width<display_height else "方形")
    return {"duration":round(duration,3),"width":width,"height":height,"fps":None,
            "rotation_degrees":rotation,"frame_orientation":frame_orientation}


def _moment_time(raw) -> float:
    if isinstance(raw,(int,float)) and not isinstance(raw,bool):return float(raw)
    bits=str(raw).split(":")
    if not 1<=len(bits)<=3:raise ValueError("关键时刻时间格式无效")
    try:
        value=0.0
        for part in bits:value=value*60+float(part)
        return value
    except ValueError:raise ValueError("关键时刻时间格式无效")


def _key_moments(raw: object, start: float, end: float) -> list[dict]:
    if raw in (None,""):return []
    if not isinstance(raw,list) or len(raw)>40:raise ValueError("关键时刻格式无效")
    moments=[]
    for item in raw:
        if isinstance(item,dict):
            at=_moment_time(item.get("time"));label=str(item.get("label") or "").strip()
        elif isinstance(item,str):
            match=re.fullmatch(r"\s*([0-9:.]+)\s+(.+?)\s*",item)
            if not match:raise ValueError("关键时刻需包含时间和动作描述")
            at=_moment_time(match.group(1));label=match.group(2).strip()
        else:raise ValueError("关键时刻格式无效")
        if not (start-0.01<=at<=end+0.01) or not label or len(label)>250:
            raise ValueError("关键时刻超出工作区间或缺少动作描述")
        moments.append({"time":round(at,3),"label":label})
    if [m["time"] for m in moments]!=sorted(m["time"] for m in moments):
        raise ValueError("关键时刻须按时间排序")
    return moments


def _motion_working_range(data: dict, metadata: dict) -> tuple[float,float,list[dict]]:
    try:
        start=float(data.get("start_time") or 0)
        end=float(data["end_time"]) if data.get("end_time") not in (None,"") else metadata["duration"]
    except (TypeError,ValueError):raise ValueError("工作区间起止时间无效")
    if not math.isfinite(start) or not math.isfinite(end) or start<0 or end<=start or end>metadata["duration"]+0.01:
        raise ValueError("工作区间须位于视频实际时长以内")
    moments=_key_moments(data.get("key_moments"),start,end)
    return start,end,moments


def _motion_cover(upload: dict, content: bytes | Path, filename: str, actor: str) -> str | None:
    """Persist the browser-captured first frame, or use ffmpeg when available."""
    raw=upload.get("cover_base64")
    cover=None
    if raw:
        if not isinstance(raw,str) or len(raw)>1_500_000:raise ValueError("视频封面过大")
        try:cover=base64.b64decode(raw,validate=True)
        except (ValueError,TypeError):raise ValueError("视频封面无效")
    elif shutil.which("ffmpeg"):
        with tempfile.TemporaryDirectory(prefix="yoodun-motion-cover-") as directory:
            source=content if isinstance(content,Path) else Path(directory)/("source"+Path(filename).suffix.lower())
            target=Path(directory)/"cover.jpg"
            if isinstance(content,bytes):source.write_bytes(content)
            try:
                subprocess.run(["ffmpeg","-nostdin","-hide_banner","-loglevel","error","-y",
                                "-i",str(source),"-frames:v","1","-q:v","5",str(target)],
                               stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=15,check=True)
                cover=target.read_bytes() if target.is_file() else None
            except (OSError,subprocess.SubprocessError):pass
    if cover is None:return None
    if not cover or len(cover)>1_000_000 or not _image_header_ok(cover,".jpg"):
        raise ValueError("视频封面必须是 1MB 以内的 JPEG 图片")
    asset=store.register_submission_asset("wuxiang","motion-cover.jpg",cover,actor)
    return asset["id"]


def check_motion_upload(user: dict, move_id: str) -> None:
    allow(user,True)
    with store.connect() as c:
        if not martial_product.current_move(c,move_id):raise ValueError("该招式不在当前生产范围")
        if not _row(c,"martial_moves",move_id)["current_version"]:raise ValueError("招式事实尚未确认")


def upload_motion_file(user: dict, move_id: str, staged: Path, filename: str,
                       sha256: str | None = None) -> dict:
    """Register and link a streamed original without decoding it into memory."""
    with MOTION_UPLOAD_LOCK:
        check_motion_upload(user,move_id)
        if Path(filename).suffix.lower() not in {".mp4",".mov"}:
            raise ValueError("真人动作需为 MP4/MOV 视频")
        sha256=sha256 or store.digest_file(staged)
        with store.connect() as c:
            existing=next((row for row in c.execute(
                "SELECT r.id FROM martial_motion_refs r JOIN assets a ON a.id=r.video_asset_id "
                "WHERE r.move_id=? AND a.sha256=? AND r.status IN ('draft','locked') "
                "ORDER BY r.version DESC",(move_id,sha256))
                if not martial_product.historical(c,"motion_ref",row["id"])),None)
        if existing:return move_detail(move_id,user)
        metadata=_probe_video_file(staged)
        _motion_working_range({},metadata)
        asset=store.register_motion_file(filename,staged,user["id"],sha256)
        try:return link_motion(user,move_id,{"asset_id":asset["id"]},metadata=metadata)
        except Exception:
            with store.connect() as c:
                linked=c.execute("SELECT 1 FROM martial_motion_refs WHERE video_asset_id=? LIMIT 1",
                                 (asset["id"],)).fetchone()
                if not linked:c.execute("DELETE FROM assets WHERE id=?",(asset["id"],))
            if not linked:Path(asset["storage_ref"]).unlink(missing_ok=True)
            raise


def attach_motion_cover(user: dict, reference_id: str, data: dict) -> dict:
    """Attach a small browser-captured JPEG when server-side ffmpeg is absent."""
    allow(user,True)
    if not isinstance(data,dict) or not isinstance(data.get("cover_base64"),str) or not data["cover_base64"]:
        raise ValueError("请提供视频封面 JPEG")
    with MOTION_COVER_LOCK:
        with store.connect() as c:
            ref=_row(c,"martial_motion_refs",reference_id)
            if martial_product.historical(c,"motion_ref",reference_id) or not martial_product.current_move(c,ref["move_id"]):
                raise ValueError("技术历史真人参考不能修改封面")
            if ref["cover_asset_id"]:return move_detail(ref["move_id"],user)
            if ref["status"]!="draft":raise ValueError("已确认的真人参考不能修改封面")
            move_id=ref["move_id"]
        cover_id=_motion_cover({"cover_base64":data["cover_base64"]},b"","motion.mp4",user["id"])
        with store.connect() as c:
            ref=_row(c,"martial_motion_refs",reference_id)
            if ref["status"]!="draft" or ref["cover_asset_id"]:
                raise ValueError("真人参考状态已变化，请刷新页面")
            c.execute("UPDATE martial_motion_refs SET cover_asset_id=? WHERE id=?",(cover_id,reference_id))
            store.audit(c,user["id"],"martial.motion.cover",None,
                        {"reference_id":reference_id,"move_id":move_id,"cover_asset_id":cover_id})
    return move_detail(move_id,user)


def upload_motion(user: dict, move_id: str, data: dict) -> dict:
    check_motion_upload(user,move_id)
    upload=data.get("upload") or {}
    try:content=base64.b64decode(upload["base64"],validate=True)
    except (KeyError,ValueError):raise ValueError("视频上传无效")
    if len(content)>MARTIAL_FILE_LIMIT:raise ValueError("真人动作视频超过 40MB 上限")
    filename=str(upload.get("name") or "")
    if Path(filename).suffix.lower() not in {".mp4",".mov"}:
        raise ValueError("真人动作需为 MP4/MOV 视频")
    metadata=_probe_video(content)
    _motion_working_range(data,metadata)
    cover_asset_id=_motion_cover(upload,content,filename,user["id"])
    asset=store.register_submission_asset("wuxiang",filename,content,user["id"])
    with store.connect() as c:c.execute("UPDATE assets SET type='motion_reference' WHERE id=?",(asset["id"],))
    return link_motion(user,move_id,{**data,"asset_id":asset["id"],"cover_asset_id":cover_asset_id},metadata=metadata)


def link_motion(user: dict, move_id: str, data: dict, metadata: dict | None = None) -> dict:
    allow(user,True)
    asset_id=str(data.get("asset_id") or "")
    with store.connect() as c:
        move=_row(c,"martial_moves",move_id)
        if not martial_product.current_move(c,move_id):raise ValueError("该招式不在当前生产范围")
        if not move["current_version"]:raise ValueError("招式事实尚未确认")
        asset=store.record(c,"assets",asset_id)
        if martial_product.historical(c,"asset",asset_id):raise ValueError("技术历史素材不能作为当前真人动作参考")
        if asset["project_id"]!="wuxiang" or asset["type"] not in {"motion_reference","video"} or asset["status"]!="active":
            raise ValueError("只能关联本项目已登记的视频素材")
        if asset["created_by"]=="local-connector" or c.execute(
            "SELECT 1 FROM martial_media_jobs WHERE candidate_asset_id=?",(asset_id,)
        ).fetchone():
            raise ValueError("AI 生成候选不能作为真人动作参考")
        path=Path(asset["storage_ref"]).resolve(strict=True)
        roots=store.asset_roots("wuxiang")
        if not any(path.is_relative_to(root) for root in roots):raise ValueError("真人参考需要在受控工作区")
        if path.suffix.lower() not in {".mp4",".mov"} or not 0<path.stat().st_size<=store.MOTION_FILE_LIMIT:
            raise ValueError("真人参考文件类型或大小无效")
        if metadata is None:metadata=_probe_video_file(path)
        duration=metadata["duration"]
        start,end,moments=_motion_working_range(data,metadata)
        version=c.execute("SELECT COALESCE(MAX(version),0)+1 FROM martial_motion_refs WHERE move_id=?",(move_id,)).fetchone()[0]
        ref_id="ref_"+secrets.token_hex(8)
        orientation=str(data.get("orientation") or "正面").strip()[:120] or "正面"
        cover_asset_id=data.get("cover_asset_id")
        if cover_asset_id:
            cover=store.record(c,"assets",str(cover_asset_id))
            if cover["project_id"]!="wuxiang" or cover["type"]!="image" or cover["status"]!="active":
                raise ValueError("视频封面素材无效")
        c.execute("INSERT INTO martial_motion_refs(id,move_id,version,video_asset_id,start_time,end_time,orientation,fps,duration,width,height,frame_orientation,rotation_degrees,cover_asset_id,start_pose,end_pose,key_moments,notes,status,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                  (ref_id,move_id,version,asset_id,start,end,orientation,
                   metadata["fps"],duration,metadata["width"],metadata["height"],metadata.get("frame_orientation"),
                   metadata.get("rotation_degrees"),cover_asset_id,str(data.get("start_pose") or "")[:1000],
                   str(data.get("end_pose") or "")[:1000],store.dumps(moments),
                   str(data.get("notes") or "")[:2000],"draft",store.now()))
        store.audit(c,user["id"],"martial.motion.link",None,{"move_id":move_id,"reference_id":ref_id,"version":version,"asset_id":asset_id})
    return move_detail(move_id,user)


def update_motion_range(user: dict, reference_id: str, data: dict) -> dict:
    """Step 3 may trim an uploaded draft without uploading the video again."""
    allow(user,True)
    with store.connect() as c:
        ref=_row(c,"martial_motion_refs",reference_id)
        if martial_product.historical(c,"motion_ref",reference_id) or not martial_product.current_move(c,ref["move_id"]):
            raise ValueError("技术历史真人参考不能用于当前生产")
        if ref["status"]!="draft":raise ValueError("只能修改尚未确认的真人动作")
        metadata={"duration":ref["duration"]}
        if not metadata["duration"]:raise ValueError("视频时长不可用，请重新上传")
        merged={"start_time":data.get("start_time",ref["start_time"]),
                "end_time":data.get("end_time",ref["end_time"]),
                "key_moments":data.get("key_moments",store.parse(ref["key_moments"],[]))}
        start,end,moments=_motion_working_range(merged,metadata)
        orientation=str(data.get("orientation",ref["orientation"]) or "正面").strip()[:120] or "正面"
        start_pose=str(data.get("start_pose",ref["start_pose"]) or "")[:1000]
        end_pose=str(data.get("end_pose",ref["end_pose"]) or "")[:1000]
        notes=str(data.get("notes",ref["notes"]) or "")[:2000]
        c.execute("UPDATE martial_motion_refs SET start_time=?,end_time=?,orientation=?,start_pose=?,end_pose=?,key_moments=?,notes=? WHERE id=?",
                  (start,end,orientation,start_pose,end_pose,store.dumps(moments),notes,reference_id))
        store.audit(c,user["id"],"martial.motion.range_update",None,
                    {"reference_id":reference_id,"move_id":ref["move_id"],"start":start,"end":end})
    return move_detail(ref["move_id"],user)


def confirm_motion(user: dict, reference_id: str) -> dict:
    allow(user,True)
    with store.connect() as c:
        ref=_row(c,"martial_motion_refs",reference_id)
        if martial_product.historical(c,"motion_ref",reference_id) or not martial_product.current_move(c,ref["move_id"]):
            raise ValueError("技术历史真人参考不能用于当前生产")
        if ref["status"]!="draft":raise ValueError("只能确认未锁定参考")
        c.execute("UPDATE martial_motion_refs SET status='locked',confirmed_by=?,confirmed_at=? WHERE id=?",
                  (user["id"],store.now(),reference_id))
        store.audit(c,user["id"],"martial.motion.lock",None,{"reference_id":reference_id,"move_id":ref["move_id"]})
    return move_detail(ref["move_id"],user)


def _facts(c,move_id):
    move=_row(c,"martial_moves",move_id)
    if not martial_product.current_move(c,move_id):raise ValueError("该招式不在当前生产范围")
    if not move["current_version"]:raise ValueError("招式事实未经确认")
    art=_row(c,"martial_arts",move["martial_art_id"])
    if art["status"]!="active":
        raise ValueError("武学词典尚无有效事实版本")
    if not art["master_id"]:raise ValueError("功法尚未绑定老师")
    mv=_version(c,"martial_move_versions","move_id",move_id,move["current_version"])
    mapping=martial_product.current_mapping(c,move_id)
    if mapping and mapping["business_label"]:mv["payload"]["chinese_name"]=mapping["business_label"]
    if not mv["payload"].get("chinese_action") or not mv["payload"].get("english_action"):
        raise ValueError("招式原文动作或英文表达为空，须先补齐并经负责人确认")
    master=_row(c,"martial_masters",art["master_id"])
    mm=_version(c,"martial_master_versions","master_id",master["id"],master["current_version"])
    status=_master_status(c,master["id"])
    if not status["production_ready"]:
        raise ValueError("功法老师人物事实虽已锁定，但缺少负责人确认的 Production Visual Reference")
    ref=next((row for row in c.execute("SELECT * FROM martial_motion_refs WHERE move_id=? AND status='locked' ORDER BY version DESC",(move_id,))
              if not martial_product.historical(c,"motion_ref",row["id"])),None)
    if not ref:raise ValueError("请先确认真人标准动作参考")
    ref=dict(ref);ref["key_moments"]=store.parse(ref["key_moments"],[])
    return move,art,mv,master,mm,ref


def request_package(user: dict, move_id: str, data: dict | None = None) -> dict:
    allow(user,True)
    data=data or {}
    raw_brief=data.get("production_brief","")
    if not isinstance(raw_brief,str):raise ValueError("原片结构说明必须是文字")
    brief=raw_brief.strip()
    if len(brief)>800:raise ValueError("原片结构说明不能超过 800 字")
    force=data.get("force") is True
    with store.connect() as c:
        move,art,mv,master,mm,ref=_facts(c,move_id)
        if user["role"]!="employee":
            visual=_master_status(c,master["id"])["visual_asset_id"]
            if (not move["task_id"] or martial_product.historical(c,"task",move["task_id"]) or
                    not visual or _task_lock_error(c,move["task_id"],visual,mm["version"],ref)):
                raise PermissionError("首次 AI 准备须由武学岗位员工发起；负责人只可刷新现有任务的 AI 内容")
        plans=_video_plans(c,move_id)
        if any(plans[kind] is None for kind in VIDEO_TYPES):
            raise ValueError("请先分别保存讲解演示和跟教练跟练视频规划")
        facts={"art":{"version":art["version"],"chinese_name":art["chinese_name"],"english_name":art["english_name"],"source_ref":art["source_ref"]},
               "move":{"version":mv["version"],"source_ref":mv["source_ref"],**mv["payload"]},
               "master":{"id":master["id"],"name":master["name"],"version":mm["version"],"source_ref":mm["source_ref"],
                         "forbidden_changes":mm["payload"].get("forbidden_changes",[]),"visual_asset_id":_master_status(c,master["id"])["visual_asset_id"],
                         "voice_id":mm["payload"].get("voice_id"),"profile":mm["payload"].get("profile"),
                         "canonical_sha256":mm["payload"].get("canonical_sha256"),
                         "teaching_style":mm["payload"].get("teaching_style")},
               "motion":_motion_facts(ref),
               "motions":{kind:_motion_facts(_plan_ref(c,plans[kind])) for kind in VIDEO_TYPES},
               "production_brief":brief,
               "video_plans":_plan_facts(plans),
               "package_prompt_version":PACKAGE_PROMPT_VERSION,
               "analysis_basis":"已确认的武学/招式文字事实、员工提供的制作意图和真人动作元数据；DeepSeek 未直接观看视频画面"}
        canonical=store.dumps(facts);facts_hash=hashlib.sha256(canonical.encode()).hexdigest()
        old=c.execute("SELECT * FROM martial_packages WHERE move_id=? AND facts_hash=? AND status IN ('queued','dispatching','complete','unknown_submission') ORDER BY created_at DESC,rowid DESC LIMIT 1",(move_id,facts_hash)).fetchone()
        # A lost/ongoing DeepSeek submission must never be repeated by 'force'.
        if old and (not force or old["status"]!="complete"):return dict(old)
        pid="pkg_"+secrets.token_hex(8)
        request_key="workos:martial:package:"+move_id+":"+facts_hash[:16]+":"+pid
        c.execute("INSERT INTO martial_packages(id,move_id,motion_ref_id,master_version,request_key,status,facts_hash,facts,requested_by,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                  (pid,move_id,ref["id"],mm["version"],request_key,"queued",facts_hash,canonical,user["id"],store.now(),store.now()))
        store.audit(c,user["id"],"martial.package.queue",None,{"package_id":pid,"move_id":move_id})
        return dict(_row(c,"martial_packages",pid))


def _materialize_task(package_id: str):
    with store.connect() as c:
        pkg=_row(c,"martial_packages",package_id)
        if pkg["status"]!="complete":return None
        result=store.parse(pkg["result"],{})
        if result.get("missing_inputs"):return None
        latest=c.execute("SELECT id FROM martial_packages WHERE move_id=? AND motion_ref_id=? AND master_version=? ORDER BY created_at DESC,rowid DESC LIMIT 1",
                         (pkg["move_id"],pkg["motion_ref_id"],pkg["master_version"])).fetchone()
        if not latest or latest["id"]!=package_id:return None
        prepared={"context_summary":result.get("move_summary", ""),"teaching_structure":result.get("motion_breakdown",[]),
                  "script":"","shot_plan":result.get("shot_camera_plan",[]),"generation_prompt":result.get("teaching_prompt",result.get("seedance_prompt", "")),
                  "practice_prompt":result.get("practice_prompt", ""),
                  "negative_constraints":result.get("negative_constraints",[]),"reference_mapping":result.get("reference_mapping",[]),
                  "qc_checklist":result.get("qc_checklist",[]),"missing_inputs":result.get("missing_inputs",[])}
        try:move,art,mv,master,mm,ref=_facts(c,pkg["move_id"])
        except ValueError:return None
        if pkg["motion_ref_id"]!=ref["id"] or pkg["master_version"]!=mm["version"]:
            return None
        recorded=store.parse(pkg["facts"],{})
        if recorded.get("move",{}).get("version")!=mv["version"] or recorded.get("art",{}).get("version")!=art["version"]:
            return None
        if recorded.get("video_plans")!=_plan_facts(_video_plans(c,pkg["move_id"])):
            return None
        motions=recorded.get("motions") or {"teaching":recorded.get("motion",{})}
        referenced_assets=list(dict.fromkeys(m["asset_id"] for m in motions.values() if m.get("asset_id")))
        if len(referenced_assets)<1:return None
        visual=_master_status(c,master["id"])["visual_asset_id"]
        if not visual:return None
        previous_task_id=move["task_id"]
        if previous_task_id and not _task_lock_error(c,previous_task_id,visual,mm["version"],ref):
            c.execute("UPDATE martial_packages SET task_id=? WHERE id=?",(previous_task_id,package_id))
            c.execute("UPDATE tasks SET ai_prepared=?,input_assets=?,updated_at=? WHERE id=?",
                      (store.dumps(prepared),store.dumps([visual,*referenced_assets]),store.now(),previous_task_id))
            return previous_task_id
        asset=store.record(c,"assets",visual)
        if asset["type"] not in {"image","character"}:raise ValueError("定版角色形象资产类型错误")
        if asset["type"]=="image":c.execute("UPDATE assets SET type='character' WHERE id=?",(visual,))
        fact=pkg["facts"]
        assignee=c.execute("SELECT user_id FROM martial_specialists WHERE project_id='wuxiang' AND active=1 AND user_id=?",
                           (pkg["requested_by"],)).fetchone() if pkg["requested_by"] else None
        if not assignee and not pkg["requested_by"]:
            assignee=c.execute("SELECT user_id FROM martial_specialists WHERE project_id='wuxiang' AND active=1 ORDER BY created_at LIMIT 1").fetchone()
        if not assignee:return None
        assignee_id=assignee[0]
    spec={"project_id":"wuxiang","workflow_id":"WF-01","title":art["chinese_name"]+" / "+mv["payload"]["chinese_name"],
          "why":"制作经过真人动作与角色锁定的数字功法老师教学及演练资产",
          "input_assets":[visual,*referenced_assets],"instructions":["对照真人动作与定版老师","生成教学或演练候选","专业动作 QC 后提交正式资产"],
          "character_lock":{"asset_id":visual,"identity":master["name"],"version":mm["version"],"forbidden_changes":mm["payload"].get("forbidden_changes",[])},
          "motion_lock":{"asset_id":ref["video_asset_id"],"move":mv["payload"]["chinese_name"],"start":ref["start_time"],
                         "end":ref["end_time"],"orientation":ref["orientation"],"key_moments":ref["key_moments"],"version":ref["version"]},
          "context":{"martial_move_id":move["id"],"source_ref":mv["source_ref"]},
          "deliverable_contract":{"required":"教学或演练视频，需完成 Martial QC"},
          "qc_contract":{"criteria":["角色一致","动作与真人参考一致","招式中英文事实准确","视频可播放"]},
          # One verified 5s Seedance 2.5 candidate is the normal production
          # allowance. Further candidates require a separate budget increase.
          "budget_cap":PRICE["sd2.5"]["reservation"]}
    created=store.create_task(spec,"u_system")
    tid=created["id"]
    with store.connect() as c:
        # A newer locked REF or character visual starts a new work task. The
        # previous task, package, Candidate and QC keep their original locks.
        updated=c.execute("UPDATE martial_moves SET task_id=?,updated_at=? WHERE id=? AND task_id IS ?",(tid,store.now(),move["id"],previous_task_id))
        if updated.rowcount!=1:raise RuntimeError("招式任务在建立新版期间发生变化，请刷新后重试")
        c.execute("UPDATE martial_packages SET task_id=? WHERE id=?",(tid,package_id))
        result=store.parse(pkg["result"],{})
        c.execute("UPDATE tasks SET ai_prepared=?,updated_at=? WHERE id=?",(store.dumps(prepared),store.now(),tid))
        store.update_task_status(c,tid,{"planned"},"ready","u_system")
    store.assign(tid,assignee_id,"u_system")
    return tid


def package_jobs() -> list[dict]:
    with store.connect() as c:
        return [dict(r) for r in c.execute("SELECT id,move_id,request_key,status FROM martial_packages WHERE status IN ('queued','dispatching') ORDER BY created_at")
                if not martial_product.historical(c,"package",r["id"])][:8]


def package_claim(package_id: str) -> dict:
    with store.connect() as c:
        if martial_product.historical(c,"package",package_id):raise ValueError("技术历史工作包不能重新提交")
        pkg=_row(c,"martial_packages",package_id)
        if pkg["status"]!="queued":raise ValueError("工作包不可认领")
        c.execute("UPDATE martial_packages SET status='dispatching',updated_at=? WHERE id=?",(store.now(),package_id))
        return {"id":package_id,"request_key":pkg["request_key"],"facts":store.parse(pkg["facts"],{})}


def package_report(package_id: str, report: dict) -> dict:
    state=str(report.get("status") or "")
    if state not in {"complete","failed","unknown_submission"}:raise ValueError("无效工作包状态")
    with store.connect() as c:
        if martial_product.historical(c,"package",package_id):raise ValueError("技术历史工作包不能修改")
        pkg=_row(c,"martial_packages",package_id)
        if pkg["status"] in {"failed","unknown_submission"}:return {"status":pkg["status"]}
        if pkg["status"]=="complete":
            missing=store.parse(pkg["result"],{}).get("missing_inputs",[])
            return {"status":"complete","task_id":pkg["task_id"] or _materialize_task(package_id),"missing_inputs":missing}
        if state!="complete":
            c.execute("UPDATE martial_packages SET status=?,error=?,updated_at=? WHERE id=?",(state,str(report.get("error") or "")[:500],store.now(),package_id))
            return {"status":state}
        body=report.get("body")
        expected={"move_summary","motion_breakdown","character_constraints","shot_camera_plan","seedance_prompt",
                  "negative_constraints","reference_mapping","qc_checklist","missing_inputs"}
        if store.parse(pkg["facts"],{}).get("package_prompt_version")==PACKAGE_PROMPT_VERSION:
            expected.update(("teaching_prompt","practice_prompt"))
        if not isinstance(body,dict) or not expected.issubset(body):raise ValueError("AI 生产包字段不完整")
        arrays=("motion_breakdown","character_constraints","shot_camera_plan","negative_constraints",
                "reference_mapping","qc_checklist","missing_inputs")
        if any(not isinstance(body[key],list) or len(body[key])>40 for key in arrays):
            raise ValueError("AI 生产包列表字段无效")
        if not isinstance(body["move_summary"],str) or len(body["move_summary"])>4000 or not isinstance(body["seedance_prompt"],str) or len(body["seedance_prompt"])>8000:
            raise ValueError("AI 生产包内容格式无效")
        if any(not isinstance(body.get(key),str) or len(body[key])>8000 for key in ("teaching_prompt","practice_prompt") if key in expected):
            raise ValueError("双视频提示词格式无效")
        body["missing_inputs"]=[str(x).strip()[:500] for x in body["missing_inputs"] if str(x).strip()]
        if not body["missing_inputs"] and (not body["seedance_prompt"].strip() or
                any(not body[key].strip() for key in ("teaching_prompt","practice_prompt") if key in expected)):
            raise ValueError("AI 生产包缺少讲解演示或跟练生成提示词")
        body["facts"]=store.parse(pkg["facts"],{})
        usage=report.get("usage") or {}
        c.execute("UPDATE martial_packages SET status='complete',result=?,usage=?,provider_job_id=?,updated_at=? WHERE id=?",
                  (store.dumps(body),store.dumps(usage),str(report.get("provider_job_id") or "")[:100],store.now(),package_id))
        store.audit(c,"local-connector","martial.package.complete",pkg["task_id"],{"package_id":package_id})
    task_id=_materialize_task(package_id)
    return {"status":"complete","task_id":task_id,"missing_inputs":body["missing_inputs"],
            "blockers":body["missing_inputs"] if body["missing_inputs"] else ([] if task_id else ["生产任务未形成，请检查事实、角色和员工权限"])}


def update_budget(user: dict, move_id: str, amount: float) -> dict:
    if user["role"] not in {"manager","founder"}:raise PermissionError("只能由负责人设置任务预算")
    unlimited=amount=="unlimited"
    value=None if unlimited else float(amount)
    if value is not None and not 0<=value<=100:raise ValueError("任务预算必须在 0 至 100 元")
    with store.connect() as c:
        move=_row(c,"martial_moves",move_id)
        if not martial_product.current_move(c,move_id) or martial_product.historical(c,"task",move["task_id"]):
            raise ValueError("技术历史任务不能作为当前生产预算")
        if not move["task_id"]:raise ValueError("生产任务尚未形成")
        if unlimited:
            c.execute("INSERT OR REPLACE INTO martial_budget_policies(task_id,unlimited,updated_by,updated_at) VALUES(?,?,?,?)",
                      (move["task_id"],1,user["id"],store.now()))
        else:
            c.execute("DELETE FROM martial_budget_policies WHERE task_id=?",(move["task_id"],))
            c.execute("UPDATE tasks SET budget_cap=?,updated_at=? WHERE id=?",(value,store.now(),move["task_id"]))
        store.audit(c,user["id"],"martial.budget.set",move["task_id"],{"budget_cap":value,"unlimited":unlimited})
    return {"task_id":move["task_id"],"budget_cap":value,"budget_unlimited":unlimited}


def _budget_unlimited(c,task_id: str) -> bool:
    return bool(c.execute("SELECT 1 FROM martial_budget_policies WHERE task_id=? AND unlimited=1",(task_id,)).fetchone())


def _remaining(c,task_id):
    task=store.record(c,"tasks",task_id)
    if _budget_unlimited(c,task_id):return None,None
    # A failed or uncertain provider submission may still have been billed.
    reserved=c.execute("SELECT COALESCE(SUM(COALESCE(actual_cost,reserved_cost)),0) FROM martial_media_jobs WHERE task_id=?",(task_id,)).fetchone()[0]
    return max(0,round(task["budget_cap"]-reserved,4)),task["budget_cap"]


def route_report(routes: list[dict]) -> dict:
    if not isinstance(routes,list) or len(routes)>2:raise ValueError("路由快照无效")
    approved={"runy":{"sd2.0":"doubao-seedance-2.0","sd2.5":"doubao-seedance-2-5"},
              "wanjie":{"sd2.5":"doubao-seedance-2-5-260628"}}
    clean=[]
    for item in routes:
        alias=item.get("model_alias");provider=item.get("provider");model=item.get("model")
        if approved.get(provider,{}).get(alias)!=model:raise ValueError("路由快照含未知模型")
        clean.append((alias,provider,model,store.now()))
    if len({x[0] for x in clean})!=len(clean):raise ValueError("路由快照重复")
    with store.connect() as c:
        c.execute("DELETE FROM martial_routes")
        c.executemany("INSERT INTO martial_routes(model_alias,provider,model,checked_at) VALUES(?,?,?,?)",clean)
    return {"routes":len(clean)}


def _route(c,model_alias: str) -> dict | None:
    row=c.execute("SELECT * FROM martial_routes WHERE model_alias=?",(model_alias,)).fetchone()
    if not row:return None
    route=dict(row)
    if time.time()-datetime.fromisoformat(route["checked_at"]).timestamp()>30:return None
    return route


def _complete_segments(plan: dict) -> list[dict]:
    """Price one continuous motion unit; separate model calls cannot preserve its seam."""
    start=float(plan["source_start"]); end=float(plan["source_end"])
    target=float(plan["target_duration"])
    if end<=start or abs((end-start)-target)>0.25:
        raise ValueError("完整制作要求目标时长与所选真人片段一致，请先修正视频规划")
    if target>RUNY_VIDEO_REFERENCE_MAX_SECONDS:
        raise ValueError("严格动作复刻暂不允许自动分段拼接：独立生成会在接缝处重置姿态。请先由武术同事按完整动作单元确认不超过 30 秒的区间")
    source_duration=round(end-start,3)
    duration=math.ceil(source_duration-0.001)
    if not 4<=duration<=RUNY_VIDEO_REFERENCE_MAX_SECONDS:
        raise ValueError("所选真人片段无法按润元 4–30 秒单段要求制作")
    # 480p at 24 fps: approximately 9,585 video tokens per second.
    # The Runy account's observed ¥59.5/million-token unit price is used
    # conservatively; video-input billing has not yet been measured.
    reserve=math.ceil((source_duration+duration)*9585*59.5/1_000_000*1.2)
    return [{"index":0,"source_start":round(start,3),"source_end":round(end,3),
             "source_duration":source_duration,"duration":duration,
             "reserved_cost":reserve,"estimated_cost":reserve}]


def _edit_trial_segments(plan: dict, cut_points: str = "") -> list[dict]:
    """Only employee-marked natural cuts; never infer a seam from duration."""
    start=float(plan["source_start"]);end=float(plan["source_end"])
    if end<=start or abs((end-start)-float(plan["target_duration"]))>0.25:
        raise ValueError("动作试拍要求目标时长与所选真人片段一致")
    raw=str(cut_points or "").strip()
    try:cuts=[round(float(item.strip()),3) for item in raw.split(",") if item.strip()]
    except ValueError as exc:raise ValueError("自然切点须填写原片秒数，用逗号分隔") from exc
    if len(cuts)>10 or any(not math.isfinite(x) for x in cuts) or cuts!=sorted(set(cuts)):
        raise ValueError("自然切点须按时间递增且不能重复")
    boundaries=[start,*cuts,end]
    if any(not 4<=b-a<=RUNY_VIDEO_REFERENCE_MAX_SECONDS for a,b in zip(boundaries,boundaries[1:])):
        raise ValueError("请由武术同事标出自然切点；每段真人动作须为 4–30 秒且不得跨出所选区间")
    segments=[]
    for index,(a,b) in enumerate(zip(boundaries,boundaries[1:])):
        source_duration=round(b-a,3)
        duration=math.ceil(source_duration-0.001)
        reserve=math.ceil((source_duration+duration)*9585*59.5/1_000_000*1.2)
        segments.append({"index":index,"source_start":round(a,3),"source_end":round(b,3),
                         "source_duration":source_duration,"duration":duration,
                         "reserved_cost":reserve,"estimated_cost":reserve})
    return segments


def _edit_trial_refs(c, move_id: str) -> dict:
    """Trial image bindings stay outside every formal dependency check."""
    lesson_id="lesson_"+move_id
    refs={}
    for role in ("pilot_scene","pilot_background"):
        row=c.execute("""SELECT b.original_id,b.sha256,b.version,b.registry_asset_id,b.source_system,
                       r.status AS asset_status,r.type FROM martial_lesson_asset_versions b
                       JOIN asset_registry r ON r.asset_id=b.registry_asset_id
                       WHERE b.lesson_id=? AND b.role=? AND b.shot_id='' AND b.status='active'""",
                      (lesson_id,role)).fetchone()
        if (not row or row["source_system"]!="work_os" or row["asset_status"]!="active" or
            row["type"]!="image" or not re.fullmatch(r"a_[a-f0-9]{16}",row["original_id"])):
            raise ValueError("请先在教学包上传试拍老师场景参考和试拍纯背景参考；它们不会计入正式美术资产")
        source=store.record(c,"assets",row["original_id"])
        if source["sha256"]!=row["sha256"] or not Path(source["storage_ref"]).is_file():
            raise ValueError("试拍参考图已变化或文件不可用，请重新上传并关联")
        refs[role]={"asset_id":row["original_id"],"registry_asset_id":row["registry_asset_id"],
                    "version":row["version"],"sha256":row["sha256"]}
    return refs


def _edit_trial_profile(move_id: str, motion_sha256: str, asset_type: str, cut_points: str) -> dict | None:
    """Optional source-pinned motion notes from a reviewed visual trial."""
    catalog=json.loads(Path(__file__).with_name("martial_edit_trial_profiles.json").read_text(encoding="utf-8"))
    for profile in catalog.get("profiles",[]):
        if profile.get("move_id")!=move_id or profile.get("motion_sha256")!=motion_sha256:
            continue
        track=(profile.get("tracks") or {}).get(asset_type) or {}
        if ",".join(str(x) for x in track.get("cut_points",[]))!=cut_points.strip():
            continue
        return {"id":profile["id"],"notes":track.get("segment_notes") or []}
    return None


def _reference_aspect_ratio(ref: dict | None) -> str:
    if not ref:return "16:9"
    width=float(ref.get("width") or 0);height=float(ref.get("height") or 0)
    if int(ref.get("rotation_degrees") or 0) in {90,270}:width,height=height,width
    if width<=0 or height<=0:return "16:9"
    return "9:16" if width/height<0.8 else "1:1" if width/height<1.2 else "16:9"


def _art_background_ref(c, art_id: str) -> dict | None:
    """Resolve the art's current static background without substituting pilot assets."""
    if not c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='martial_mm_asset_links'").fetchone():
        return None
    row=c.execute("""SELECT l.version,l.asset_id,a.name,a.type,a.status,a.project_id,
                    a.storage_ref,a.sha256 FROM martial_mm_asset_links l
                    JOIN assets a ON a.id=l.asset_id
                    WHERE l.scope='art' AND l.scope_id=? AND l.role='background'
                      AND l.status='active'""",(art_id,)).fetchone()
    if not row:return None
    item=dict(row)
    path=Path(item["storage_ref"])
    try:
        with path.open("rb") as stream:header=stream.read(256)
        valid=(item["project_id"]=="wuxiang" and item["type"]=="image" and item["status"]=="active"
               and _image_header_ok(header,path.suffix.lower()) and store.digest_file(path)==item["sha256"])
    except OSError:valid=False
    if not valid:raise ValueError("功法练功背景图片不可用或校验值不符，请在功法管理核对版本")
    return {"asset_id":item["asset_id"],"version":item["version"],"name":item["name"],
            "sha256":item["sha256"],"source":"art_background"}


def quote(user: dict, move_id: str, model_alias: str, count=1, generation_mode="preview", asset_type="teaching", cut_points="") -> dict:
    allow(user)
    if model_alias not in PRICE:raise ValueError("该业务模型暂无已验证报价")
    if generation_mode not in {"preview","reproduce","complete","edit_trial"}:raise ValueError("未知视频生成方式")
    if asset_type not in VIDEO_TYPES:raise ValueError("请选择讲解演示或跟教练跟练视频")
    count=int(count)
    if count not in {1,2}:raise ValueError("候选数量只能是 1 或 2")
    with store.connect() as c:
        move=_row(c,"martial_moves",move_id)
        if not martial_product.current_move(c,move_id):raise ValueError("该招式不在当前生产范围")
        background=None; background_error=None
        if generation_mode!="edit_trial":
            try:background=_art_background_ref(c,move["martial_art_id"])
            except ValueError as exc:background_error=str(exc)
            if not background and not background_error:background_error="请先在功法管理上传本功法练功背景图片"
        usable_task=move["task_id"] and not martial_product.historical(c,"task",move["task_id"])
        remaining,budget=_remaining(c,move["task_id"]) if usable_task else (0,0)
        budget_unlimited=bool(usable_task and _budget_unlimited(c,move["task_id"]))
        route=_route(c,model_alias)
        lock_error=None;package_error=None;ref=None
        if usable_task:
            try:
                _,_,_,master,mm,ref=_facts(c,move_id)
                visual=_master_status(c,master["id"])["visual_asset_id"]
                if visual:lock_error=_task_lock_error(c,move["task_id"],visual,mm["version"],ref)
            except ValueError:
                pass  # Other production prerequisites are checked by create_media.
        if ref is None:
            locked=next((r for r in c.execute("SELECT * FROM martial_motion_refs WHERE move_id=? AND status='locked' ORDER BY version DESC",(move_id,))
                         if not martial_product.historical(c,"motion_ref",r["id"])),None)
            ref=dict(locked) if locked else None
        plans=_video_plans(c,move_id)
        plan=plans[asset_type]
        track_ref=_plan_ref(c,plan)
        if usable_task:
            latest=c.execute("SELECT status,facts FROM martial_packages WHERE move_id=? ORDER BY created_at DESC,rowid DESC LIMIT 1",(move_id,)).fetchone()
            recorded=store.parse(latest["facts"],{}) if latest else {}
            if not latest or latest["status"]!="complete" or not _package_facts_current(c,move,recorded,ref,plans):
                package_error="当前双视频规划的最新 AI 准备尚未完成"
    source_duration=float(track_ref["duration"]) if track_ref and track_ref["duration"] is not None else None
    aspect_ratio=_reference_aspect_ratio(track_ref)
    selected_start=float(plan["source_start"]) if plan else None
    selected_end=float(plan["source_end"]) if plan else None
    selected_duration=round(selected_end-selected_start,3) if plan else None
    reference_info={"reference_duration":source_duration,"selected_start":selected_start,
                    "selected_end":selected_end,"selected_duration":selected_duration,
                    "asset_type":asset_type,"source_start":selected_start,"source_end":selected_end,
                    "target_duration":plan["target_duration"] if plan else None,
                    "video_plan_version":plan["version"] if plan else None,
                    "background_name":background["name"] if background else None,
                    "background_version":background["version"] if background else None}
    if generation_mode in {"complete","edit_trial"}:
        segment_error=None;segments=[]
        if plan:
            try:segments=_edit_trial_segments(plan,cut_points) if generation_mode=="edit_trial" else _complete_segments(plan)
            except ValueError as exc:segment_error=str(exc)
        if generation_mode=="edit_trial":
            try:
                with store.connect() as art_c:_edit_trial_refs(art_c,move_id)
            except ValueError as exc:segment_error=segment_error or str(exc)
        priced=route and route["provider"]=="runy" and route["model"]==PRICE[model_alias]["model"]
        if model_alias!="sd2.5":priced=False
        cost=sum(s["reserved_cost"] for s in segments)*count if priced and segments else None
        reason=(lock_error or (background_error if generation_mode=="complete" else None) or
                ("请先保存该视频的独立制作规划" if not plan else None) or
                package_error or segment_error or
                ("当前润元 Seedance 2.5 路由未核验" if not priced else None) or
                ("任务预算不足，请负责人先调整预算" if cost is not None and remaining is not None and cost>remaining else None))
        return {"generation_mode":generation_mode,"model_alias":model_alias,
                "duration":(sum(s["duration"] for s in segments) if generation_mode=="edit_trial" and segments else
                            math.ceil(plan["target_duration"]) if plan else None),
                "target_duration":plan["target_duration"] if plan else None,
                "resolution":"480p","aspect_ratio":aspect_ratio,"candidate_count":count,
                "estimated_cost":cost,"task_budget":budget,"remaining_budget":remaining,
                "budget_unlimited":budget_unlimited,
                "blocked":bool(reason or cost is None),"block_reason":reason or ("完整制作预留未核定" if cost is None else None),
                "price_source":LONG_VIDEO_RESERVATION_SOURCE if cost is not None else None,
                "price_kind":"reservation_estimate","segments":segments,
                "scope":("视频编辑试拍候选：老师场景和纯背景为试拍输入，不可定版或满足正式依赖；仍需真人动作对照"
                         if generation_mode=="edit_trial" else
                         "按所选真人片段单段生成候选，需员工进行动作 QC；生成模型不保证逐帧复刻"),**reference_info}
    if generation_mode=="reproduce":
        trimmed=(plan is not None and source_duration is not None and
                 (selected_start>0.01 or selected_end<source_duration-0.01))
        if ref is None:
            reason="请先确认真人动作参考"
        elif plan is None:
            reason="请先保存该视频的独立制作规划"
        elif trimmed:
            reason=(f"当前锁定片段为 {selected_start:.2f}–{selected_end:.2f} 秒，原片 {source_duration:.2f} 秒；"
                    "该分段完整时长、价格和接入尚未验证")
        else:
            reason="目标时长与一比一复刻方式尚未通过当前润元接入验证，价格也未核实；不能按 5 秒预览提交"
        return {"generation_mode":generation_mode,"model_alias":model_alias,
                "duration":math.ceil(plan["target_duration"]) if plan else None,
                "resolution":"480p","aspect_ratio":aspect_ratio,"candidate_count":count,
                "estimated_cost":None,"task_budget":budget,"remaining_budget":remaining,
                "budget_unlimited":budget_unlimited,
                "blocked":True,"block_reason":reason,"price_source":None,**reference_info}
    # The connector sends ref.video_asset_id as a video URL, not a clipped
    # source_start/source_end range. Validate the actual supplier input before
    # allowing even a five-second output preview to create a paid job.
    preview_error=None
    if track_ref is not None and (source_duration is None or source_duration>RUNY_VIDEO_REFERENCE_MAX_SECONDS):
        preview_error=(f"真人参考原片为 {source_duration:.2f} 秒，超过润元 Seedance 视频参考输入"
                       f"最多 {RUNY_VIDEO_REFERENCE_MAX_SECONDS} 秒；当前会发送整段原片，不能提交 5 秒样片"
                       if source_duration is not None else
                       "真人参考原片时长未知，无法核对润元 Seedance 的 30 秒输入上限；不能提交 5 秒样片")
    elif plan is not None and source_duration is not None and (selected_start>0 or selected_end<source_duration):
        preview_error=(f"制作方案选择原片 {selected_start:.2f}–{selected_end:.2f} 秒，但当前会向润元发送"
                       f"整段 {source_duration:.2f} 秒原片，尚未按所选片段裁剪；不能提交 5 秒样片")
    priced=route and route["provider"]=="runy" and route["model"]==PRICE[model_alias]["model"]
    cost=PRICE[model_alias]["reservation"]*count if priced else None
    return {"generation_mode":generation_mode,"model_alias":model_alias,"duration":5,"resolution":"480p",
            "aspect_ratio":aspect_ratio,"candidate_count":count,"estimated_cost":cost,"task_budget":budget,
            "remaining_budget":remaining,"budget_unlimited":budget_unlimited,
            "blocked":bool(preview_error or lock_error or background_error or package_error or not plan) or cost is None or (remaining is not None and cost>remaining),
            "block_reason":preview_error or lock_error or background_error or ("请先保存该视频的独立制作规划" if not plan else None) or package_error or (None if cost is not None and (remaining is None or cost<=remaining) else "价格或路由未核验" if cost is None else "预算不足"),
            "price_source":PRICE_SOURCE if cost is not None else None,
            "scope":"仅生成 5 秒样片，不是讲解演示或跟练正式视频",**reference_info}


def create_media(user: dict, move_id: str, data: dict) -> list[dict]:
    allow(user,True)
    alias=str(data.get("model") or "sd2.5")
    count=int(data.get("candidate_count") or 1)
    generation_mode=str(data.get("generation_mode") or "preview")
    cut_points=str(data.get("cut_points") or "").strip()
    asset_type=str(data.get("asset_type") or "")
    if asset_type not in VIDEO_TYPES:raise ValueError("请选择讲解演示或跟教练跟练视频")
    estimate=quote(user,move_id,alias,count,generation_mode,asset_type,cut_points)
    if not data.get("generation_mode") and (estimate.get("target_duration") or 0)>5.01:
        raise ValueError("目标视频超过 5 秒，请明确选择完整制作或 5 秒样片")
    if estimate["blocked"]:raise ValueError(estimate["block_reason"]+"，暂不可发起付费生成")
    with store.connect() as c:
        c.execute("BEGIN IMMEDIATE")  # Serialize budget and duplicate-job checks.
        move,art,mv,master,mm,ref=_facts(c,move_id)
        plans=_video_plans(c,move_id)
        plan=plans[asset_type]
        if not plan:raise ValueError("请先保存该视频的独立制作规划")
        track_ref=_plan_ref(c,plan)
        if not move["task_id"]:raise ValueError("生产任务尚未形成；需先有已批准的老师形象")
        task=store.record(c,"tasks",move["task_id"])
        if task["assignee_id"]!=user["id"] or task["status"]!="in_progress":
            raise PermissionError("只能为本人进行中的武学任务发起生成")
        pkg=c.execute("SELECT * FROM martial_packages WHERE move_id=? AND motion_ref_id=? AND master_version=? ORDER BY created_at DESC,rowid DESC LIMIT 1",
                      (move_id,ref["id"],mm["version"])).fetchone()
        if not pkg or pkg["status"]!="complete":raise ValueError("当前锁定版本的最新 AI 准备尚未完成")
        package=store.parse(pkg["result"],{})
        recorded=store.parse(pkg["facts"],{})
        if recorded.get("package_prompt_version")!=PACKAGE_PROMPT_VERSION:
            raise ValueError("AI 准备使用旧版生成规则，请按原片结构重新准备")
        if recorded.get("video_plans")!=_plan_facts(plans):
            raise ValueError("当前双视频规划已更新，请重新准备 AI 内容")
        if recorded.get("move",{}).get("version")!=mv["version"] or recorded.get("art",{}).get("version")!=art["version"]:
            raise ValueError("AI 生产包对应旧版事实；请按当前确认版本重新生成")
        if package.get("missing_inputs"):
            raise ValueError("AI 生产包仍缺少输入："+"；".join(map(str,package["missing_inputs"])))
        visual=_master_status(c,master["id"])["visual_asset_id"]
        if not visual:raise ValueError("定版角色形象尚未登记")
        lock_error=_task_lock_error(c,task["id"],visual,mm["version"],ref)
        if lock_error:raise ValueError(lock_error)
        suggestion=str(package.get(asset_type+"_prompt") or "").strip()
        if not suggestion:raise ValueError("AI 生产包没有生成提示词")
        full=generation_mode in {"complete","edit_trial"}
        edit_trial=generation_mode=="edit_trial"
        background=None if edit_trial else _art_background_ref(c,art["id"])
        if not edit_trial and not background:raise ValueError("请先在功法管理上传本功法练功背景图片")
        edit_refs=_edit_trial_refs(c,move_id) if edit_trial else {}
        prompt=(("编辑视频 1，只替换人物与背景；" if edit_trial else "")+
                f"《{art['chinese_name']}·{mv['payload']['chinese_name']}》"
                f"{'讲解演示' if asset_type=='teaching' else '跟教练跟练'}动作复刻。"
                +(f"图片 1 是数字老师 {master['name']} V{mm['version']} 在教学场景中的试拍合成参考；"
                  "图片 2 是不含人物的同场景纯背景。保留图片 1 的角色身份、服装与场景布局，"
                  "用图片 2 替换真人视频中的原背景；全程保持同一角色和武术场景。"
                  if edit_trial else
                  f"图片 1 仅用于数字老师 {master['name']} V{mm['version']} 的身份、外观和服装；"
                  f"图片 2 是本功法练功背景 V{background['version']} 的静态原图。以图片 2 锁定练功场的建筑、"
                  "地面、山石、树木、空间布局、色彩和光照方向；不要替换为其他武术场景。"
                  "只让原图已有的流云或薄雾在远景缓慢、连续、轻微地浮动，光影可有细微自然变化；"
                  "建筑、地面和树木保持稳定，不要整张背景平移、缩放、扭曲，也不要新增抢眼特效。")+
                "视频 1 是唯一的动作与时序参考。逐时刻保留视频 1 中双手轨迹、双脚落点、朝向、"
                "重心、速度、停顿、起止姿态和完整身体取景。只替换人物外观，不新增动作、换边、"
                "改拍或改变节奏。固定全身机位，保持手脚无遮挡。"
                "若文字与参考视频画面冲突，以参考视频为准。"
                +("本次仅制作当前裁切区间，不引用原片其他秒点。"
                  if full else
                  "本次只生成 5 秒短片预览，不得表述成完整真人视频复刻或正式视频。"))
        if edit_trial:prompt+="\n这是无音轨的视觉试拍候选，不加字幕、文字、对白、配乐或音效。试拍参考图不能作为正式美术资产。"
        if asset_type=="practice":prompt+="\n输出动作演练素材，不把动态教学反馈语音烧入标准视频。"
        else:prompt+="\n输出数字功法老师教学视频；标准视频与动态 TTS 分离。"
        prompt_hash=hashlib.sha256(prompt.encode()).hexdigest()
        remaining,budget=_remaining(c,task["id"])
        if remaining is not None and estimate["estimated_cost"]>remaining:raise ValueError("生成预算已被其他任务占用")
        route=_route(c,alias)
        if not route or route["provider"]!="runy" or route["model"]!=PRICE[alias]["model"]:
            raise ValueError("当前模型路由或报价未核验，暂不可提交付费生成")
        revision_of=str(data.get("revision_of") or "").strip() or None
        if edit_trial and revision_of:raise ValueError("试拍不能使用正式动作返修入口")
        if full:
            active=c.execute("SELECT id FROM martial_media_jobs WHERE move_id=? AND asset_type=? AND video_plan_id=? "
                             "AND generation_mode=? AND status IN "
                             "('queued','dispatching','submitted','running','download_pending','technical_check','unknown_submission') LIMIT 1",
                             (move_id,asset_type,plan["id"],generation_mode)).fetchone()
            if active:raise ValueError("该视频已有制作中或提交状态待核对的任务，请勿重复付费提交")
        if revision_of:
            old=_row(c,"martial_media_jobs",str(revision_of))
            if old["move_id"]!=move_id or old["asset_type"]!=asset_type:raise ValueError("返修来源不匹配")
            qc=c.execute("SELECT 1 FROM martial_qc WHERE media_job_id=? AND stage='martial' AND result='fail'",(old["id"],)).fetchone()
            if not qc:raise ValueError("只有动作 QC 未通过的候选可发起返修")
            revision=martial_revision.ensure_for_job(c,old["id"])
            prompt+="\n"+martial_revision.render_instructions(revision["payload"])
            prompt_hash=hashlib.sha256(prompt.encode()).hexdigest()
        created=[]
        for _ in range(count):
            jid="mj_"+secrets.token_hex(8);stamp=store.now()
            key="workos:martial:media:"+jid
            segments=[dict(s) for s in (estimate.get("segments") or [])]
            if edit_trial:
                source=store.record(c,"assets",track_ref["video_asset_id"])
                profile=_edit_trial_profile(move_id,source["sha256"],asset_type,cut_points)
                if profile:
                    if len(profile["notes"])!=len(segments):raise ValueError("视频编辑试拍配置的动作段数与切点不一致")
                    for segment,note in zip(segments,profile["notes"]):
                        segment["motion_note"]=str(note)[:800]
                        segment["profile_id"]=profile["id"]
            duration=(sum(s["duration"] for s in segments) if edit_trial else math.ceil(float(plan["target_duration"]))) if full else 5
            reservation=estimate["estimated_cost"]/count if full else PRICE[alias]["reservation"]
            quote_source=LONG_VIDEO_RESERVATION_SOURCE if full else PRICE_SOURCE
            c.execute("INSERT INTO martial_media_jobs(id,move_id,task_id,package_id,motion_ref_id,master_version,asset_type,provider,model_alias,model,prompt_hash,prompt,character_asset_id,duration,aspect_ratio,resolution,status,request_key,idempotency_key,reserved_cost,quote_source,revision_of,generation_mode,video_plan_id,segments_json,edit_refs_json,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                      (jid,move_id,task["id"],pkg["id"],track_ref["id"],mm["version"],asset_type,route["provider"],alias,route["model"],
                       (hashlib.sha256((prompt+store.dumps(segments)).encode()).hexdigest() if edit_trial else prompt_hash),
                       prompt,visual,duration,estimate["aspect_ratio"],"480p","queued",key,key,reservation,quote_source,
                       revision_of,generation_mode,plan["id"],store.dumps(segments),store.dumps(edit_refs),stamp,stamp))
            if background:
                c.execute("UPDATE martial_media_jobs SET background_ref_json=? WHERE id=?",
                          (store.dumps(background),jid))
            created.append(jid)
        store.audit(c,user["id"],"martial.media.queue",task["id"],{"media_job_ids":created,"reserved_cost":estimate["estimated_cost"]})
        return [{"id":jid,"status":"queued","model_alias":alias,"asset_type":asset_type,
                 "generation_mode":generation_mode,"duration":duration,
                 "reserved_cost":estimate["estimated_cost"]/count if full else PRICE[alias]["reservation"]} for jid in created]


def _sign(asset_id: str, expires: int) -> str:
    key=os.environ.get("YOODUN_CONNECTOR_TOKEN","")
    if not key:raise RuntimeError("素材签名服务未配置")
    return hmac.new(key.encode(),f"{asset_id}:{expires}".encode(),hashlib.sha256).hexdigest()


def source_url(asset_id: str, expires: int) -> str:
    return PUBLIC_ORIGIN+"/api/martial/source/"+asset_id+"?"+urlencode({"exp":expires,"sig":_sign(asset_id,expires)})


def source_path(asset_id: str, exp: str, sig: str) -> Path:
    if not re.fullmatch(r"a_[a-f0-9]{16}",asset_id) or not re.fullmatch(r"[0-9]{10,11}",exp):
        raise PermissionError("素材链接无效")
    expiry=int(exp)
    if expiry<int(time.time()) or expiry>int(time.time())+6*3600+60 or not hmac.compare_digest(sig,_sign(asset_id,expiry)):
        raise PermissionError("素材链接已过期或无效")
    with store.connect() as c:
        asset=store.record(c,"assets",asset_id)
        if asset["project_id"]!="wuxiang" or asset["type"] not in {"image","character","motion_reference","video"}:
            raise PermissionError("素材不可用于模型参考")
        if asset["type"]=="video" and not c.execute(
            "SELECT 1 FROM martial_motion_refs WHERE video_asset_id=? AND status='locked' LIMIT 1",
            (asset_id,),
        ).fetchone():
            raise PermissionError("视频尚未确认为标准动作参考")
        path=Path(asset["storage_ref"]).resolve(strict=True)
        roots=store.asset_roots("wuxiang")
        if not any(path.is_relative_to(root) for root in roots):raise PermissionError("素材不在受控工作区")
        limit=store.MOTION_FILE_LIMIT if asset["type"] in {"motion_reference","video"} else 50_000_000
        if path.stat().st_size>limit:raise PermissionError("素材过大")
        return path


def _clip_path(job_id: str, index: int) -> Path:
    if not re.fullmatch(r"mj_[a-f0-9]{16}",job_id) or not 0<=index<20:
        raise ValueError("裁切片段编号无效")
    return store.DATA/"connector_staging"/"martial"/"reference_clips"/job_id/(f"{index}.mp4")


def clip_url(job_id: str, index: int, expires: int) -> str:
    identity=f"clip:{job_id}:{index}"
    return PUBLIC_ORIGIN+f"/api/martial/clip/{job_id}/{index}?"+urlencode(
        {"exp":expires,"sig":_sign(identity,expires)})


def clip_source_path(job_id: str, index: int, exp: str, sig: str) -> Path:
    path=_clip_path(job_id,index)
    if not re.fullmatch(r"[0-9]{10,11}",exp):raise PermissionError("片段链接无效")
    expiry=int(exp)
    if expiry<int(time.time()) or expiry>int(time.time())+6*3600+60 or not hmac.compare_digest(
            sig,_sign(f"clip:{job_id}:{index}",expiry)):
        raise PermissionError("片段链接已过期或无效")
    with store.connect() as c:
        job=_row(c,"martial_media_jobs",job_id)
        segments=store.parse(job["segments_json"],[])
        if job["generation_mode"] not in {"complete","edit_trial"} or index>=len(segments):
            raise PermissionError("片段不属于完整制作任务")
    if not path.is_file() or path.stat().st_size>200_000_000:
        raise ValueError("参考片段尚未准备好")
    return path


def stage_reference_clip(job_id: str, index: int, uploaded: Path, sha256: str) -> dict:
    """Store only a bounded, checked clip produced by the local connector."""
    target=_clip_path(job_id,index)
    if not re.fullmatch(r"[a-f0-9]{64}",sha256):raise ValueError("片段校验值无效")
    with store.connect() as c:
        job=_row(c,"martial_media_jobs",job_id)
        segments=store.parse(job["segments_json"],[])
        if job["generation_mode"] not in {"complete","edit_trial"} or index>=len(segments):
            raise ValueError("视频作业没有该参考片段")
        if job["status"] not in {"queued","dispatching","submitted","running","download_pending","technical_check"}:
            raise ValueError("视频作业状态不允许上传片段")
        expected=segments[index]
    size=uploaded.stat().st_size
    if size<=0 or size>200_000_000 or store.digest_file(uploaded)!=sha256:
        raise ValueError("参考片段大小或校验值无效")
    probe=_probe_video_file(uploaded)
    if abs(probe["duration"]-expected["source_duration"])>0.35:
        raise ValueError("参考片段时长与已确认区间不一致")
    if min(probe["width"],probe["height"])<300:
        raise ValueError("参考片段分辨率不足")
    target.parent.mkdir(parents=True,exist_ok=True)
    if target.exists():
        if store.digest_file(target)!=sha256:raise ValueError("该片段已有不同内容，不允许覆盖")
    else:
        try:os.link(uploaded,target)
        except FileExistsError:
            if store.digest_file(target)!=sha256:raise ValueError("该片段已有不同内容，不允许覆盖")
    expires=int(time.time())+6*3600
    return {"video_url":clip_url(job_id,index,expires),"sha256":sha256,
            "duration":probe["duration"],"size":size,"expires_at":expires}


def media_jobs() -> list[dict]:
    with store.connect() as c:
        return [dict(r) for r in c.execute("SELECT id,request_key,status,local_media_id FROM martial_media_jobs WHERE status IN ('queued','dispatching','submitted','running','download_pending','technical_check') ORDER BY created_at")
                if not martial_product.historical(c,"media_job",r["id"])][:12]


def media_claim(job_id: str) -> dict:
    with store.connect() as c:
        if martial_product.historical(c,"media_job",job_id):raise ValueError("技术历史视频任务不能重新提交")
        job=_row(c,"martial_media_jobs",job_id)
        if job["status"] not in {"queued","dispatching","submitted","running","download_pending","technical_check"}:
            raise ValueError("视频任务不可认领")
        if job["status"]=="queued":
            c.execute("UPDATE martial_media_jobs SET status='dispatching',submitted_at=?,updated_at=? WHERE id=?",
                      (store.now(),store.now(),job_id))
        ref=_row(c,"martial_motion_refs",job["motion_ref_id"])
        plan=c.execute("SELECT * FROM martial_video_plans WHERE id=?",(job["video_plan_id"],)).fetchone() if job["video_plan_id"] else None
        asset=store.record(c,"assets",ref["video_asset_id"])
        segments=store.parse(job["segments_json"],[])
        edit_refs=store.parse(job["edit_refs_json"],{}) if job["generation_mode"]=="edit_trial" else {}
        background_ref=store.parse(job["background_ref_json"],{})
        if background_ref:
            background_asset=store.record(c,"assets",background_ref["asset_id"])
            background_path=Path(background_asset["storage_ref"])
            if (background_asset["type"]!="image" or background_asset["status"]!="active" or
                background_asset["sha256"]!=background_ref["sha256"] or
                not background_path.is_file() or store.digest_file(background_path)!=background_ref["sha256"]):
                raise ValueError("视频作业锁定的练功背景图片不可用，不能提交生成")
        if job["generation_mode"]=="edit_trial":
            if set(edit_refs)!={"pilot_scene","pilot_background"}:
                raise ValueError("视频编辑任务缺少锁定的试拍图版本")
            if job["status"]=="queued" and _edit_trial_refs(c,job["move_id"])!=edit_refs:
                raise ValueError("试拍参考图版本已变化；旧任务不会用新图提交")
        for s in segments:
            s["request_key"]=job["request_key"]+f":segment:{s['index']}"
            if job["generation_mode"]=="edit_trial":s["video_edit"]=True
            s["prompt"]=(job["prompt"]+f"\n视频 1 已从真人原片裁出 {s['source_start']:.3f}–"
                         f"{s['source_end']:.3f} 秒，视频 1 的本地 0 秒即原片 {s['source_start']:.3f} 秒。"
                         f"本次只生成这一段约 {s['source_duration']} 秒动作单元，不引用原片其他区间。"
                         +(f"\n经本片试拍对照的动作阶段提示：{s['motion_note']}" if s.get("motion_note") else ""))
            s["prompt_sha256"]=hashlib.sha256(s["prompt"].encode()).hexdigest()
        expires=int(time.time())+6*3600
        return {"id":job_id,"status":job["status"],"request_key":job["request_key"],"task_id":job["task_id"],
                "model_alias":job["model_alias"],"model":job["model"],"provider":job["provider"],
                "prompt":job["prompt"],"prompt_hash":job["prompt_hash"],"duration":job["duration"],
                "resolution":job["resolution"],"ratio":job["aspect_ratio"],"reserved_cost":job["reserved_cost"],
                "generation_mode":job["generation_mode"],"asset_type":job["asset_type"],
                "video_plan":dict(plan) if plan else None,
                "segments":segments,"segment_progress":store.parse(job["segment_progress"],[]),
                "budget_cny":job["reserved_cost"] if _budget_unlimited(c,job["task_id"]) else store.record(c,"tasks",job["task_id"])["budget_cap"],
                "budget_unlimited":_budget_unlimited(c,job["task_id"]),"quote_source":job["quote_source"],
                "reference_expires_at":expires,
                "reference_source_url":source_url(ref["video_asset_id"],expires),
                "reference_sha256":asset["sha256"],
                "background_ref":background_ref,
                "image_urls":([source_url(edit_refs[role]["asset_id"],expires)
                               for role in ("pilot_scene","pilot_background")] if edit_refs else
                              [source_url(job["character_asset_id"],expires)]+
                              ([source_url(background_ref["asset_id"],expires)] if background_ref else [])),
                "video_urls":[] if job["generation_mode"] in {"complete","edit_trial"} else [source_url(ref["video_asset_id"],expires)],
                "local_job_id":job["local_job_id"],"local_media_id":job["local_media_id"]}


def _candidate_stage_path(job_id: str) -> Path:
    if not re.fullmatch(r"mj_[a-f0-9]{16}",job_id):raise ValueError("视频作业编号无效")
    return store.DATA/"connector_staging"/"martial"/(job_id+".mp4")


def candidate_upload_state(job_id: str, sha256: str) -> dict:
    """Check an authenticated connector upload before reading its large body."""
    if not re.fullmatch(r"[a-f0-9]{64}",sha256):raise ValueError("视频校验值无效")
    with CANDIDATE_UPLOAD_LOCK:
        with store.connect() as c:
            if martial_product.historical(c,"media_job",job_id):raise ValueError("技术历史视频任务不能修改")
            job=_row(c,"martial_media_jobs",job_id)
            if job["status"]=="succeeded":
                asset=store.record(c,"assets",job["candidate_asset_id"])
                if asset["sha256"]!=sha256:raise ValueError("该任务已有不同的候选视频")
                return {"status":"already_saved","sha256":sha256,"candidate_asset_id":asset["id"]}
            if job["status"] not in {"dispatching","submitted","running","download_pending","technical_check"}:
                raise ValueError("视频作业状态不允许回传文件")
        staged=_candidate_stage_path(job_id)
        if staged.exists():
            if not staged.is_file() or store.digest_file(staged)!=sha256:
                raise ValueError("该任务已有不同的暂存视频")
            return {"status":"already_staged","sha256":sha256,"size":staged.stat().st_size}
        return {"status":"ready","sha256":sha256}


def stage_candidate_video(job_id: str, uploaded: Path, sha256: str) -> dict:
    """Keep one immutable, retryable video per paid media job."""
    with CANDIDATE_UPLOAD_LOCK:
        state=candidate_upload_state(job_id,sha256)
        if state["status"]!="ready":return state
        uploaded=Path(uploaded)
        stage_dir=store.DATA/"connector_staging"/"martial"
        if uploaded.is_symlink() or not uploaded.is_file() or uploaded.resolve().parent!=stage_dir.resolve():
            raise ValueError("候选视频上传路径无效")
        size=uploaded.stat().st_size
        if not 0<size<=store.MOTION_FILE_LIMIT or store.digest_file(uploaded)!=sha256:
            raise ValueError("候选视频大小或校验值无效")
        target=_candidate_stage_path(job_id)
        try:os.link(uploaded,target)
        except FileExistsError:return candidate_upload_state(job_id,sha256)
        return {"status":"staged","sha256":sha256,"size":size}


def media_report(job_id: str, report: dict) -> dict:
    state=str(report.get("status") or "")
    if state not in MEDIA_STATES-{"queued","dispatching"}:raise ValueError("视频状态无效")
    with store.connect() as c:
        if martial_product.historical(c,"media_job",job_id):raise ValueError("技术历史视频任务不能修改")
        job=_row(c,"martial_media_jobs",job_id)
        if job["status"] in {"succeeded","failed","unknown_submission"}:return {"status":job["status"]}
        progress=report.get("segment_progress")
        if progress is not None:
            segments=store.parse(job["segments_json"],[])
            if not isinstance(progress,list) or len(progress)>len(segments) or any(
                    not isinstance(p,dict) or p.get("index")!=i or
                    p.get("status") not in {"queued","clip_ready","submitted","running","downloaded","complete","failed","unknown_submission"}
                    for i,p in enumerate(progress)):
                raise ValueError("分段进度格式无效")
            progress=store.dumps([{k:p.get(k) for k in ("index","status","source_start","source_end","error") if k in p}
                                  for p in progress])
        else:progress=job["segment_progress"]
        if state in {"submitted","running","download_pending","technical_check"}:
            c.execute("UPDATE martial_media_jobs SET status=?,local_job_id=?,local_media_id=?,provider_job_id=?,segment_progress=?,updated_at=? WHERE id=?",
                      (state,str(report.get("local_job_id") or job["local_job_id"] or "")[:100],
                       str(report.get("local_media_id") or job["local_media_id"] or "")[:100],
                       str(report.get("provider_job_id") or job["provider_job_id"] or "")[:150],progress,store.now(),job_id))
            return {"status":state}
        if state in {"failed","unknown_submission"}:
            c.execute("UPDATE martial_media_jobs SET status=?,error=?,local_job_id=?,local_media_id=?,provider_job_id=?,actual_cost=?,segment_progress=?,updated_at=? WHERE id=?",
                      (state,str(report.get("error") or state)[:600],
                       str(report.get("local_job_id") or job["local_job_id"] or "")[:100],
                       str(report.get("local_media_id") or job["local_media_id"] or "")[:100],
                       str(report.get("provider_job_id") or job["provider_job_id"] or "")[:150],
                       float(report["actual_cost"]) if report.get("actual_cost") is not None else job["actual_cost"],
                       progress,store.now(),job_id))
            store.audit(c,"local-connector","martial.media."+state,job["task_id"],{"job_id":job_id})
            return {"status":state}
    with CANDIDATE_UPLOAD_LOCK:
        with store.connect() as c:
            latest=_row(c,"martial_media_jobs",job_id)
            if latest["status"] in {"succeeded","failed","unknown_submission"}:return {"status":latest["status"]}
        staged=None
        sha256=report.get("video_sha256")
        if sha256 is not None:
            if not isinstance(sha256,str) or not re.fullmatch(r"[a-f0-9]{64}",sha256):
                raise ValueError("候选视频校验值无效")
            staged=_candidate_stage_path(job_id)
            if not staged.is_file() or store.digest_file(staged)!=sha256:
                raise ValueError("已下载的视频尚未完整回传")
            probe=lambda:_probe_video_file(staged)
        else:
            content=report.get("video_base64")
            if not isinstance(content,str):raise ValueError("缺少已下载的视频文件")
            try:video=base64.b64decode(content,validate=True)
            except ValueError:raise ValueError("生成文件编码无效")
            if not video or len(video)>MARTIAL_FILE_LIMIT:raise ValueError("生成结果为空或超过 40MB")
            probe=lambda:_probe_video(video)
        technical=dict(report["technical"]) if isinstance(report.get("technical"),dict) else {"result":"unverified","notes":"未返回完整技术元数据"}
        try:technical["server_probe"]=probe()
        except ValueError as exc:
            # Keep the downloaded file for inspection, but never accept a claimed
            # technical PASS when the server cannot read its video metadata.
            technical["result"]="unverified"
            technical["server_probe_error"]=str(exc)[:200]
        asset=(store.register_candidate_file(job_id,staged,sha256) if staged else
               store.register_production_asset("wuxiang","candidate_video",job_id+".mp4",video,"local-connector"))
        with store.connect() as c:
            job=_row(c,"martial_media_jobs",job_id)
            if job["status"] in {"succeeded","failed","unknown_submission"}:
                if staged is None:
                    c.execute("DELETE FROM assets WHERE id=?",(asset["id"],))
                    Path(asset["storage_ref"]).unlink(missing_ok=True)
                return {"status":job["status"]}
            c.execute("UPDATE martial_media_jobs SET status='succeeded',candidate_asset_id=?,actual_cost=?,technical_report=?,local_job_id=?,local_media_id=?,provider_job_id=?,completed_at=?,updated_at=? WHERE id=?",
                      (asset["id"],float(report["actual_cost"]) if report.get("actual_cost") is not None else job["actual_cost"],
                       store.dumps(technical),str(report.get("local_job_id") or job["local_job_id"] or "")[:100],
                       str(report.get("local_media_id") or job["local_media_id"] or "")[:100],
                       str(report.get("provider_job_id") or job["provider_job_id"] or "")[:150],store.now(),store.now(),job_id))
            c.execute("INSERT INTO martial_qc(id,media_job_id,move_id,stage,result,findings,reference_comparison,created_at) VALUES(?,?,?,?,?,?,?,?)",
                      ("mqc_"+secrets.token_hex(8),job_id,job["move_id"],"technical",str(technical.get("result") or "unverified"),
                       str(technical.get("notes") or ""),"仅做文件技术核验，未判断动作质量",store.now()))
            store.audit(c,"local-connector","martial.media.candidate",job["task_id"],{"job_id":job_id,"asset_id":asset["id"]})
        if staged is not None:staged.unlink(missing_ok=True)
        return {"status":"succeeded","candidate_asset_id":asset["id"]}


def select_candidate(user: dict, media_job_id: str, reason: str) -> dict:
    allow(user,True)
    reason=str(reason or "").strip()
    if len(reason)<8 or len(reason)>2000:raise ValueError("请写明选择该候选的理由（至少 8 字）")
    with store.connect() as c:
        job=_row(c,"martial_media_jobs",media_job_id)
        if job["generation_mode"]=="edit_trial":
            raise ValueError("视频编辑试拍仅供动作对照，不得选用或定版为正式作品")
        if martial_product.historical(c,"media_job",media_job_id):raise ValueError("技术历史 Candidate 不可参与当前生产")
        context_error=_current_job_error(c,job)
        if context_error:raise ValueError(context_error)
        task=store.record(c,"tasks",job["task_id"])
        if task["assignee_id"]!=user["id"] or job["status"]!="succeeded":
            raise PermissionError("只能选择本人任务中已完成的候选")
        technical=store.parse(job["technical_report"],{})
        if technical.get("result")!="pass":raise ValueError("候选未通过文件技术检查，不可选用")
        c.execute("INSERT OR REPLACE INTO martial_selections(media_job_id,reason,selected_by,selected_at) VALUES(?,?,?,?)",
                  (media_job_id,reason,user["id"],store.now()))
        store.audit(c,user["id"],"martial.candidate.select",job["task_id"],{"media_job_id":media_job_id})
    return {"selected":True}


def martial_qc(user: dict, media_job_id: str, data: dict) -> dict:
    allow(user,True)
    dispute=data.get("major_dispute",False)
    if not isinstance(dispute,bool):raise ValueError("重大质量争议标记无效")
    checks=data.get("checks")
    if not isinstance(checks,dict) or any(not isinstance(checks.get(key),str) or checks.get(key) not in {"pass","fail","unsure"} for key in QC_CHECKS):
        raise ValueError("请逐项填写八项专业动作检查：PASS、FAIL 或 UNSURE")
    if any(key not in QC_CHECKS for key in checks):raise ValueError("专业动作检查包含未知项目")
    ranges=data.get("issue_ranges",data.get("intervals",[]))
    if not isinstance(ranges,list) or len(ranges)>20:raise ValueError("问题区间格式无效")
    issues=[]
    for item in ranges:
        if not isinstance(item,dict):raise ValueError("问题区间格式无效")
        start=_moment_time(item.get("start"));end=_moment_time(item.get("end"))
        severity=str(item.get("severity") or "").lower()
        issue=str(item.get("issue") or "").strip()
        move_id=str(item.get("move_id") or item.get("move") or "").strip()
        body_part=str(item.get("body_part") or "未标注").strip()
        issue_type=str(item.get("issue_type") or "other").strip().lower()
        comment=str(item.get("comment") or issue).strip()
        if not 0<=start<end or end>3600 or severity not in {"minor","major","critical"} or not 1<=len(issue)<=500:
            raise ValueError("问题区间需要有效起止时间、问题及严重度")
        if len(move_id)>100 or not 1<=len(body_part)<=80 or issue_type not in {"path","timing","pose","direction","balance","contact","framing","other"} or not 1<=len(comment)<=1000:
            raise ValueError("问题区间的招式、身体部位、类型或意见无效")
        issues.append({"start":round(start,3),"end":round(end,3),"move_id":move_id,
                       "body_part":body_part,"issue_type":issue_type,"severity":severity,
                       "issue":issue,"comment":comment})
    allowed_pass=(all(checks[key]!="fail" for key in QC_CHECKS) and
                  checks["teaching_suitability"]=="pass" and
                  not any(item["severity"] in {"major","critical"} for item in issues))
    requested=str(data.get("verdict") or "").lower()
    if requested and requested not in {"pass","fail","revision_required"}:
        raise ValueError("专业验收结论无效")
    if requested=="pass" and not allowed_pass:
        raise ValueError("必检项存在 FAIL、教学适宜性未 PASS 或有重大问题区间，不可通过")
    verdict="fail" if requested in {"fail","revision_required"} or not allowed_pass else "pass"
    findings=str(data.get("findings") or "").strip()
    comparison=str(data.get("reference_comparison") or "").strip()
    if not findings or not comparison:raise ValueError("需写明真人参考对照和问题结论")
    with store.connect() as c:
        job=_row(c,"martial_media_jobs",media_job_id)
        if any(item["move_id"] and item["move_id"]!=job["move_id"] for item in issues):
            raise ValueError("问题区间的招式与候选不一致")
        for item in issues:item["move_id"]=job["move_id"]
        if martial_product.historical(c,"media_job",media_job_id):raise ValueError("技术历史 Candidate 不可修改 QC")
        context_error=_current_job_error(c,job)
        if context_error:raise ValueError(context_error)
        if job["status"]!="succeeded":raise ValueError("候选视频尚未完成")
        if any(item["end"]>job["duration"]+0.5 for item in issues):
            raise ValueError("问题区间超出本次候选视频时长")
        task=store.record(c,"tasks",job["task_id"])
        if task["assignee_id"]!=user["id"]:raise PermissionError("只能验收本人任务候选")
        if not c.execute("SELECT 1 FROM martial_selections WHERE media_job_id=?",(media_job_id,)).fetchone():
            raise ValueError("请先选择候选并记录理由")
        if c.execute("SELECT 1 FROM martial_qc WHERE media_job_id=? AND stage='martial'",(media_job_id,)).fetchone():
            raise ValueError("此候选已有专业 QC；返修请建立新候选")
        qid="mqc_"+secrets.token_hex(8)
        c.execute("INSERT INTO martial_qc(id,media_job_id,move_id,stage,result,findings,reference_comparison,checks,issue_ranges,major_dispute,reviewer_id,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                  (qid,media_job_id,job["move_id"],"martial",verdict,findings[:4000],comparison[:4000],
                   store.dumps({key:checks[key] for key in QC_CHECKS}),store.dumps(issues),int(dispute),user["id"],store.now()))
        revision=martial_revision.ensure_for_job(c,media_job_id) if verdict=="fail" else None
        store.audit(c,user["id"],"martial.qc",job["task_id"],{"job_id":media_job_id,"verdict":verdict})
        return {"id":qid,"result":verdict,"status":"revision_required" if verdict=="fail" else "pass",
                "checks":{key:checks[key] for key in QC_CHECKS},"issue_ranges":issues,"major_dispute":dispute,
                "revision_package_id":revision["id"] if revision else None}


def _finalization_blocker(c, job: dict, qc: dict, user: dict) -> str | None:
    """Only the first sample, an explicit escalation, or uncertain QC needs a lead."""
    context_error=_current_job_error(c,job)
    if context_error:return context_error
    source_error=_current_final_error(c,job)
    if source_error:return source_error
    if user["role"] in {"founder","manager"}:return None
    if user["role"]!="employee":return "当前岗位不能定版武学资产"
    task=store.record(c,"tasks",job["task_id"])
    if task["assignee_id"]!=user["id"]:return "仅任务指派员工可定版"
    if not c.execute("SELECT 1 FROM martial_final_assets LIMIT 1").fetchone():
        return "首个重要正式样板需负责人最终验收"
    if task["founder_required"]:return "此任务已标记需负责人验收"
    if qc["major_dispute"]:return "重大质量争议需负责人验收"
    checks=store.parse(qc["checks"],{})
    if any(checks.get(key)!="pass" for key in QC_CHECKS):
        return "专业动作检查仍有待核实项，需负责人验收"
    return None


def finalize(user: dict, media_job_id: str) -> dict:
    allow(user,True)
    with store.connect() as c:
        job=_row(c,"martial_media_jobs",media_job_id)
        if martial_product.historical(c,"media_job",media_job_id):raise ValueError("技术历史 Candidate 不可成为正式资产")
        context_error=_current_job_error(c,job)
        if context_error:raise ValueError(context_error)
        source_error=_current_final_error(c,job)
        if source_error:raise ValueError(source_error)
        qc=c.execute("SELECT * FROM martial_qc WHERE media_job_id=? AND stage='martial' ORDER BY created_at DESC LIMIT 1",(media_job_id,)).fetchone()
        selected=c.execute("SELECT 1 FROM martial_selections WHERE media_job_id=?",(media_job_id,)).fetchone()
        if job["status"]!="succeeded" or not job["candidate_asset_id"] or not selected or not qc or qc["result"]!="pass":
            raise ValueError("专业动作 QC 尚未通过")
        previous_same=c.execute("SELECT id,status FROM martial_final_assets WHERE media_job_id=?",(media_job_id,)).fetchone()
        if previous_same:
            if previous_same["status"]=="active":return {"id":previous_same["id"],"status":"active"}
            raise ValueError("此候选的正式资产已被新版取代，不可重新定版")
        blocker=_finalization_blocker(c,job,qc,user)
        if blocker:raise PermissionError(blocker)
        previous=c.execute("SELECT id FROM martial_final_assets WHERE move_id=? AND asset_type=? AND status='active'",(job["move_id"],job["asset_type"])).fetchall()
        for row in previous:c.execute("UPDATE martial_final_assets SET status='superseded' WHERE id=?",(row["id"],))
        fid="mf_"+secrets.token_hex(8)
        c.execute("INSERT INTO martial_final_assets(id,move_id,asset_type,media_job_id,asset_id,status,approved_by,created_at) VALUES(?,?,?,?,?,?,?,?)",
                  (fid,job["move_id"],job["asset_type"],media_job_id,job["candidate_asset_id"],"active",user["id"],store.now()))
        store.audit(c,user["id"],"martial.final.approve" if user["role"] in {"founder","manager"} else "martial.final.publish",job["task_id"],{"final_id":fid,"media_job_id":media_job_id})
        return {"id":fid,"status":"active"}
