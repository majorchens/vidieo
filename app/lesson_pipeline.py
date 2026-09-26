"""Lesson packages on top of the existing Martial and Asset Center records.

The package stores immutable asset selections.  Martial facts, motion references,
candidate generation and specialist QC remain owned by their existing modules.
"""
from __future__ import annotations

import base64
import hashlib
import json
import re
import secrets
from pathlib import Path

import asset_center
import martial
import martial_product
import store

ROLES = {
    "scene": {"image", "scene"},
    "background": {"image", "scene"},
    "teacher_model": {"character", "image"},
    "instruction_voice": {"audio"},
    "narrative_voice": {"audio"},
    "bgm": {"audio"},
    "sfx": {"audio"},
    "subtitle": {"document", "script"},
    "camera": {"document", "script"},
    "prompt": {"prompt", "document"},
    "workflow": {"document"},
    "pilot_video": {"video"},
    "pilot_motion_ref": {"video"},
    "pilot_audio": {"audio"},
    "pilot_subtitle": {"document"},
}
RULE_REGISTRY = Path(__file__).resolve().parent / "production_rules.json"
OWNERS = {
    "facts": "武术 / 运营", "teacher": "美术", "shot_plan": "AI Production OS",
    "motion_source": "武术 / 运营",
    "motion_mapping": "AI Production OS", "motion_review": "武术 / 运营",
    "scene": "美术", "background": "美术", "teacher_model": "美术", "instruction_script": "武术 / 运营",
    "instruction_voice": "AI Production OS", "narrative_voice": "AI Production OS",
    "bgm": "AI Production OS", "sfx": "AI Production OS",
    "video": "AI Production OS", "pilot_video": "AI Production OS", "pilot_motion_ref": "武术 / 运营",
    "pilot_audio": "AI Production OS",
    "pilot_subtitle": "AI Production OS", "audiovisual_review": "最终人工验收",
}
LABELS = {
    "facts": "功法与招式事实", "teacher": "数字老师", "shot_plan": "教学镜头设计",
    "motion_source": "真人标准动作",
    "motion_mapping": "数字老师动作", "motion_review": "动作准确性验收",
    "scene": "教学场景", "background": "教学背景", "teacher_model": "数字人模型",
    "instruction_script": "教学讲解",
    "instruction_voice": "老师教学语音", "narrative_voice": "OS 画外音",
    "bgm": "背景音乐", "sfx": "音效", "video": "教学视频", "pilot_video": "历史样片",
    "pilot_motion_ref": "历史参考片段", "pilot_audio": "历史语音", "pilot_subtitle": "历史字幕",
    "audiovisual_review": "成片视听验收",
}
ESSENTIAL = ("facts", "teacher", "shot_plan", "motion_source", "motion_mapping", "motion_review",
             "background", "instruction_script", "instruction_voice", "video", "audiovisual_review")


def production_rules(art_id: str) -> list[dict]:
    """Only explicitly reviewed Git rules enter a formal manifest."""
    registry = json.loads(RULE_REGISTRY.read_text(encoding="utf-8"))
    if registry.get("schema") != "MartialProductionRules/v1":
        raise ValueError("生产规则登记格式无效")
    rules = []
    for item in registry.get("rules", []):
        if item.get("status") != "active" or item.get("scope") not in {"wuxiang", art_id}:
            continue
        for key in ("id", "version", "learning_review", "reviewer", "rule", "conditions", "counterexamples", "evidence_sha256"):
            if not item.get(key):
                raise ValueError("正式生产规则缺少学习审查或反例证据")
        if not re.fullmatch(r"[0-9a-f]{64}", str(item["evidence_sha256"])):
            raise ValueError("正式生产规则的证据校验和无效")
        rules.append(item)
    return sorted(rules,key=lambda item:(item["id"],item["version"]))


def initialize() -> None:
    with store.connect() as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS martial_lesson_packages(
          id TEXT PRIMARY KEY, art_id TEXT NOT NULL REFERENCES martial_arts(id),
          move_id TEXT NOT NULL UNIQUE REFERENCES martial_moves(id), chapter TEXT NOT NULL DEFAULT '第一章',
          status TEXT NOT NULL DEFAULT 'planning', current_version INTEGER NOT NULL DEFAULT 0,
          approved_manifest TEXT, approved_sha256 TEXT, approved_by TEXT REFERENCES users(id),
          approved_at TEXT, published_at TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS martial_lesson_asset_versions(
          lesson_id TEXT NOT NULL REFERENCES martial_lesson_packages(id), role TEXT NOT NULL,
          shot_id TEXT NOT NULL DEFAULT '',
          version INTEGER NOT NULL, registry_asset_id TEXT NOT NULL REFERENCES asset_registry(asset_id),
          registry_version INTEGER NOT NULL, source_system TEXT NOT NULL, original_id TEXT NOT NULL,
          sha256 TEXT NOT NULL, file_ref TEXT NOT NULL, status TEXT NOT NULL,
          created_by TEXT NOT NULL REFERENCES users(id), created_at TEXT NOT NULL,
          PRIMARY KEY(lesson_id,role,shot_id,version));
        CREATE UNIQUE INDEX IF NOT EXISTS idx_lesson_asset_active
          ON martial_lesson_asset_versions(lesson_id,role,shot_id) WHERE status='active';
        CREATE TABLE IF NOT EXISTS martial_lesson_shots(
          lesson_id TEXT NOT NULL REFERENCES martial_lesson_packages(id),
          shot_id TEXT NOT NULL, version INTEGER NOT NULL, ordinal INTEGER NOT NULL,
          purpose TEXT NOT NULL, start_state TEXT NOT NULL, end_state TEXT NOT NULL,
          camera TEXT NOT NULL, duration REAL NOT NULL,
          status TEXT NOT NULL, created_by TEXT NOT NULL REFERENCES users(id),
          created_at TEXT NOT NULL, PRIMARY KEY(lesson_id,shot_id,version));
        CREATE UNIQUE INDEX IF NOT EXISTS idx_lesson_shot_active
          ON martial_lesson_shots(lesson_id,shot_id) WHERE status='active';
        CREATE TABLE IF NOT EXISTS martial_lesson_reviews(
          lesson_id TEXT NOT NULL REFERENCES martial_lesson_packages(id),
          version INTEGER NOT NULL, stage TEXT NOT NULL, verdict TEXT NOT NULL,
          media_job_id TEXT, evidence TEXT NOT NULL, notes TEXT NOT NULL,
          reviewer_id TEXT NOT NULL REFERENCES users(id), created_at TEXT NOT NULL,
          PRIMARY KEY(lesson_id,version,stage));
        CREATE TABLE IF NOT EXISTS martial_lesson_manifests(
          lesson_id TEXT NOT NULL REFERENCES martial_lesson_packages(id), version INTEGER NOT NULL,
          manifest TEXT NOT NULL, sha256 TEXT NOT NULL, status TEXT NOT NULL,
          created_by TEXT NOT NULL REFERENCES users(id), created_at TEXT NOT NULL,
          PRIMARY KEY(lesson_id,version));
        """)


def _ensure(c, move_id: str) -> dict:
    move = c.execute("SELECT id,martial_art_id,current_version FROM martial_moves WHERE id=?", (move_id,)).fetchone()
    if not move or not martial_product.current_move(c, move_id):
        raise KeyError(move_id)
    lesson_id = "lesson_" + move_id
    stamp = store.now()
    c.execute("""INSERT OR IGNORE INTO martial_lesson_packages
      (id,art_id,move_id,created_at,updated_at) VALUES(?,?,?,?,?)""",
      (lesson_id, move["martial_art_id"], move_id, stamp, stamp))
    return dict(c.execute("SELECT * FROM martial_lesson_packages WHERE id=?", (lesson_id,)).fetchone())


def _binding(c, lesson_id: str, role: str, shot_id: str = "") -> dict | None:
    row = c.execute("""SELECT b.*,r.name,r.type,r.status AS asset_status FROM martial_lesson_asset_versions b
       JOIN asset_registry r ON r.asset_id=b.registry_asset_id
       WHERE b.lesson_id=? AND b.role=? AND b.shot_id=? AND b.status='active'""", (lesson_id, role, shot_id)).fetchone()
    return dict(row) if row else None


def bind_asset(user: dict, move_id: str, data: dict) -> dict:
    martial.allow(user, True)
    role = str(data.get("role") or "")
    if role not in ROLES:
        raise ValueError("未知教学资产用途")
    shot_id = str(data.get("shot_id") or "")
    asset_id = str(data.get("asset_id") or "")
    asset_center.sync_internal()
    # Resolve through the existing access policy. Legacy references without a
    # readable production file cannot silently become a ready lesson asset.
    file = asset_center.media_path(user, asset_id)
    if not file.is_file():
        raise FileNotFoundError(file)
    with store.connect() as c:
        lesson = _ensure(c, move_id)
        if shot_id and not c.execute("SELECT 1 FROM martial_lesson_shots WHERE lesson_id=? AND shot_id=? AND status='active'",(lesson["id"],shot_id)).fetchone():
            raise ValueError("镜头不存在或不是当前版本")
        row = c.execute("SELECT * FROM asset_registry WHERE asset_id=?", (asset_id,)).fetchone()
        if not row or row["project_id"] != "wuxiang" or row["status"] != "active" or row["type"] not in ROLES[role]:
            raise ValueError("素材不属于当前功法项目、类型不符或尚未生效")
        if role in {"scene", "background"} and row["type"] == "image" and file.suffix.lower() not in {".png", ".jpg", ".jpeg", ".webp"}:
            raise ValueError("背景需要可读取的图片")
        if role == "teacher_model" and row["type"] == "character":
            with file.open("rb") as source:
                if file.suffix.lower() != ".glb" or not martial._glb_header_ok(source.read(20), file.stat().st_size):
                    raise ValueError("数字人模型需要有效 GLB 文件")
        sha = store.digest_file(file)
        if row["sha256"] and sha != row["sha256"]:
            raise ValueError("素材文件与资产中心登记的校验和不一致")
        current = _binding(c, lesson["id"], role, shot_id)
        if current and current["registry_asset_id"] == asset_id and current["sha256"] == sha:
            return detail(user, move_id)
        next_version = c.execute("SELECT COALESCE(MAX(version),0)+1 FROM martial_lesson_asset_versions WHERE lesson_id=? AND role=? AND shot_id=?", (lesson["id"], role,shot_id)).fetchone()[0]
        c.execute("UPDATE martial_lesson_asset_versions SET status='superseded' WHERE lesson_id=? AND role=? AND shot_id=? AND status='active'", (lesson["id"], role,shot_id))
        c.execute("""INSERT INTO martial_lesson_asset_versions
          (lesson_id,role,shot_id,version,registry_asset_id,registry_version,source_system,original_id,sha256,file_ref,status,created_by,created_at)
          VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
          (lesson["id"], role, shot_id, next_version, asset_id, row["version"], row["source_system"],
           row["original_id"], sha, row["file_ref"], "active", user["id"], store.now()))
        c.execute("UPDATE martial_lesson_packages SET status='in_production',approved_manifest=NULL,approved_sha256=NULL,approved_by=NULL,approved_at=NULL,published_at=NULL,updated_at=? WHERE id=?", (store.now(), lesson["id"]))
        store.audit(c, user["id"], "lesson.asset.bind", None,
                    {"lesson_id": lesson["id"], "role": role, "shot_id": shot_id, "asset_id": asset_id, "version": next_version})
    return detail(user, move_id)


def upload_asset(user: dict, move_id: str, data: dict) -> dict:
    martial.allow(user, True)
    role = str(data.get("role") or "")
    if role not in ROLES:raise ValueError("未知教学资产用途")
    upload = data.get("upload") or {}
    filename = str(upload.get("name") or "")
    suffix = Path(filename).suffix.lower()
    implied = ("image" if suffix in {".png",".jpg",".jpeg",".webp"} else
               "audio" if suffix in {".wav",".mp3"} else
               "character" if suffix == ".glb" else
               "video" if suffix in {".mp4",".mov"} else
               "document" if suffix in {".md",".txt",".json",".srt"} else "")
    if implied not in ROLES[role]:raise ValueError("所选文件类型不适用于该教学资产")
    try:contents=base64.b64decode(upload["base64"],validate=True)
    except (KeyError,ValueError):raise ValueError("上传文件数据无效")
    with store.connect() as c:_ensure(c,move_id)
    saved=store.register_production_asset("wuxiang",role,filename,contents,user["id"])
    asset_center.sync_internal()
    with store.connect() as c:
        registry=c.execute("SELECT asset_id FROM asset_registry_sources WHERE source_system='work_os' AND original_id=?",(saved["id"],)).fetchone()
    if not registry:raise RuntimeError("素材已保存，但资产中心尚未完成登记")
    return bind_asset(user,move_id,{"role":role,"asset_id":registry[0],"shot_id":data.get("shot_id")})


def save_shot(user: dict, move_id: str, data: dict) -> dict:
    martial.allow(user, True)
    try:
        ordinal = int(data.get("ordinal"))
        duration = float(data.get("duration"))
    except (TypeError, ValueError):
        raise ValueError("镜号和时长无效")
    if not 1 <= ordinal <= 999 or not 0 < duration <= 120:
        raise ValueError("镜号或时长超出范围")
    fields = {name: str(data.get(name) or "").strip() for name in ("purpose", "start_state", "end_state", "camera")}
    if any(not value or len(value) > 500 for value in fields.values()):
        raise ValueError("请填写镜头目的、起止状态与机位")
    with store.connect() as c:
        lesson = _ensure(c, move_id)
        shot_id = str(data.get("shot_id") or "shot_" + secrets.token_hex(6))
        if not shot_id.startswith("shot_") or len(shot_id) > 32:
            raise ValueError("镜头编号无效")
        current = c.execute("SELECT MAX(version) FROM martial_lesson_shots WHERE lesson_id=? AND shot_id=?",
                            (lesson["id"], shot_id)).fetchone()[0]
        if data.get("shot_id") and current is None:
            raise ValueError("镜头不存在")
        version = (current or 0) + 1
        c.execute("UPDATE martial_lesson_shots SET status='superseded' WHERE lesson_id=? AND shot_id=? AND status='active'",
                  (lesson["id"], shot_id))
        c.execute("""INSERT INTO martial_lesson_shots
          (lesson_id,shot_id,version,ordinal,purpose,start_state,end_state,camera,duration,status,created_by,created_at)
          VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
          (lesson["id"],shot_id,version,ordinal,fields["purpose"],fields["start_state"],fields["end_state"],
           fields["camera"],duration,"active",user["id"],store.now()))
        c.execute("UPDATE martial_lesson_packages SET status='in_production',approved_manifest=NULL,approved_sha256=NULL,approved_by=NULL,approved_at=NULL,published_at=NULL,updated_at=? WHERE id=?",
                  (store.now(),lesson["id"]))
        store.audit(c,user["id"],"lesson.shot.save",None,{"lesson_id":lesson["id"],"shot_id":shot_id,"version":version})
    return detail(user,move_id)


def _active_final(c, move_id: str, kind: str = "teaching") -> dict | None:
    row = c.execute("""SELECT f.*,j.master_version,j.motion_ref_id,j.package_id,j.prompt_hash,j.provider,j.model,
      j.character_asset_id,j.status AS job_status,j.resolution,j.duration,
      q.id AS martial_qc_id,q.result AS martial_qc_result,q.reviewer_id AS martial_reviewer
      FROM martial_final_assets f JOIN martial_media_jobs j ON j.id=f.media_job_id
      LEFT JOIN martial_qc q ON q.media_job_id=j.id AND q.stage='martial'
      WHERE f.move_id=? AND f.asset_type=? AND f.status='active'
      ORDER BY f.created_at DESC LIMIT 1""", (move_id, kind)).fetchone()
    return dict(row) if row else None


def _latest_candidate(c, move_id: str) -> dict | None:
    for row in c.execute("""SELECT j.id,j.status,j.candidate_asset_id,j.motion_ref_id,j.master_version,
       q.result AS martial_qc_result,
       q.reviewer_id AS martial_reviewer FROM martial_media_jobs j
       LEFT JOIN martial_qc q ON q.media_job_id=j.id AND q.stage='martial'
       WHERE j.move_id=? AND j.asset_type='teaching' ORDER BY j.created_at DESC,j.rowid DESC""",(move_id,)):
        if not martial_product.historical(c,"media_job",row["id"]):return dict(row)
    return None


def _check(c, lesson: dict) -> dict:
    art = c.execute("SELECT * FROM martial_arts WHERE id=?", (lesson["art_id"],)).fetchone()
    move = c.execute("SELECT * FROM martial_moves WHERE id=?", (lesson["move_id"],)).fetchone()
    facts = store.parse(c.execute("SELECT payload FROM martial_move_versions WHERE move_id=? AND version=?", (move["id"], move["current_version"])).fetchone()[0], {}) if move["current_version"] else {}
    teacher = martial._master_status(c, art["master_id"]) if art["master_id"] else {"production_ready": False, "version": 0}
    ref = c.execute("""SELECT r.*,a.sha256 AS source_sha256,a.storage_ref FROM martial_motion_refs r
      JOIN assets a ON a.id=r.video_asset_id WHERE r.move_id=? AND r.status='locked'
      ORDER BY r.version DESC LIMIT 1""", (move["id"],)).fetchone()
    ref = dict(ref) if ref and not martial_product.historical(c,"motion_ref",ref["id"]) else None
    final = _active_final(c, move["id"])
    candidate = _latest_candidate(c, move["id"])
    if candidate and (not ref or candidate["motion_ref_id"] != ref["id"] or candidate["master_version"] != teacher.get("version")):
        candidate = None
    if final and (not ref or final["motion_ref_id"] != ref["id"] or final["master_version"] != teacher.get("version")):
        final = None
    bg = _binding(c, lesson["id"], "background")
    scene = _binding(c, lesson["id"], "scene")
    teacher_model = _binding(c, lesson["id"], "teacher_model")
    voice = _binding(c, lesson["id"], "instruction_voice")
    narration = _binding(c, lesson["id"], "narrative_voice")
    bgm = _binding(c, lesson["id"], "bgm")
    sfx = _binding(c, lesson["id"], "sfx")
    pilot = _binding(c, lesson["id"], "pilot_video")
    pilot_motion_ref = _binding(c, lesson["id"], "pilot_motion_ref")
    pilot_audio = _binding(c, lesson["id"], "pilot_audio")
    pilot_subtitle = _binding(c, lesson["id"], "pilot_subtitle")
    latest_review = c.execute("""SELECT * FROM martial_lesson_reviews WHERE lesson_id=? AND stage='audiovisual'
      ORDER BY version DESC LIMIT 1""", (lesson["id"],)).fetchone()
    latest_review = dict(latest_review) if latest_review else None
    checks = {
      "facts": bool(art["status"] == "active" and move["current_version"] and (facts.get("chinese_action") or facts.get("english_action"))),
      "teacher": bool(teacher.get("production_ready")),
      "shot_plan": bool(c.execute("SELECT 1 FROM martial_lesson_shots WHERE lesson_id=? AND status='active' LIMIT 1",(lesson["id"],)).fetchone()),
      "motion_source": bool(ref and ref["source_sha256"]),
      "motion_mapping": bool(candidate and candidate["status"] == "succeeded" and candidate["candidate_asset_id"]),
      "motion_review": bool(candidate and candidate["martial_qc_result"] == "pass" and candidate["martial_reviewer"]),
      "background": bool(bg),
      "scene": bool(scene),
      "teacher_model": bool(teacher_model),
      "instruction_script": bool(facts.get("chinese_coaching") or facts.get("english_coaching")),
      "instruction_voice": bool(voice),
      "narrative_voice": bool(narration),
      "bgm": bool(bgm),
      "sfx": bool(sfx),
      "pilot_video": bool(pilot),
      "pilot_motion_ref": bool(pilot_motion_ref),
      "pilot_audio": bool(pilot_audio),
      "pilot_subtitle": bool(pilot_subtitle),
      "video": bool(final and final["asset_id"]),
      "audiovisual_review": bool(latest_review and latest_review["verdict"] == "pass" and
                                  final and latest_review["media_job_id"] == final["media_job_id"]),
    }
    stages = []
    for key, ready in checks.items():
        stages.append({"key": key, "label": LABELS[key], "ready": ready,
                       "owner": OWNERS[key], "required_for_publish": key in ESSENTIAL})
    if lesson["status"] == "published" and all(checks[k] for k in ESSENTIAL):
        status = "published"
    elif lesson["approved_manifest"] and all(checks[k] for k in ESSENTIAL):
        status = "approved"
    elif candidate and candidate["martial_qc_result"] == "fail":
        status = "motion_revision"
    elif not checks["facts"]:
        status = "planning"
    elif not checks["motion_source"]:
        status = "motion_waiting"
    elif not checks["teacher"]:
        status = "teacher_waiting"
    elif not checks["background"]:
        status = "art_waiting"
    elif not checks["motion_mapping"]:
        status = "motion_processing"
    elif not checks["motion_review"]:
        status = "motion_review"
    elif not checks["video"]:
        status = "composition"
    elif not checks["instruction_voice"]:
        status = "voice_waiting"
    elif not checks["audiovisual_review"]:
        status = "qa"
    else:
        status = "ready_for_approval"
    # Surface parallel assignments without treating optional sound as a
    # prerequisite for motion replication or art.
    ready_work = []
    if not checks["facts"]: ready_work.append("facts")
    if not checks["teacher"]: ready_work.append("teacher")
    if not checks["shot_plan"]: ready_work.append("shot_plan")
    if checks["facts"] and not checks["motion_source"]: ready_work.append("motion_source")
    if not checks["scene"]: ready_work.append("scene")
    if not checks["background"]: ready_work.append("background")
    if not checks["teacher_model"]: ready_work.append("teacher_model")
    if checks["facts"] and not checks["instruction_script"]: ready_work.append("instruction_script")
    if checks["instruction_script"] and not checks["instruction_voice"]: ready_work.append("instruction_voice")
    for sound in ("narrative_voice", "bgm", "sfx"):
        if not checks[sound]: ready_work.append(sound)
    if checks["teacher"] and checks["motion_source"] and not checks["motion_mapping"]: ready_work.append("motion_mapping")
    if checks["motion_mapping"] and not checks["motion_review"]: ready_work.append("motion_review")
    if checks["motion_review"] and checks["background"] and not checks["video"]: ready_work.append("video")
    if checks["video"] and not checks["audiovisual_review"]: ready_work.append("audiovisual_review")
    return {"status": status, "checks": checks, "stages": stages,
            "ready_work": ready_work, "missing": [s for s in stages if not s["ready"]],
            "facts": facts, "teacher": teacher, "motion_reference": ref,
            "candidate": candidate, "final_video": final, "review": latest_review}


def detail(user: dict, move_id: str) -> dict:
    martial.allow(user)
    with store.connect() as c:
        lesson = _ensure(c, move_id)
        result = _check(c, lesson)
        versions = [dict(r) for r in c.execute("SELECT b.lesson_id,b.role,b.shot_id,b.version,b.registry_asset_id,b.registry_version,b.sha256,b.status,b.created_by,b.created_at,r.name,r.type FROM martial_lesson_asset_versions b JOIN asset_registry r ON r.asset_id=b.registry_asset_id WHERE b.lesson_id=? ORDER BY b.role,b.shot_id,b.version DESC", (lesson["id"],))]
        shots = [dict(r) for r in c.execute("SELECT shot_id,version,ordinal,purpose,start_state,end_state,camera,duration FROM martial_lesson_shots WHERE lesson_id=? AND status='active' ORDER BY ordinal,shot_id",(lesson["id"],))]
        art = c.execute("SELECT chinese_name,master_id FROM martial_arts WHERE id=?", (lesson["art_id"],)).fetchone()
        move = c.execute("SELECT ordinal,current_version FROM martial_moves WHERE id=?", (move_id,)).fetchone()
        motion = result["motion_reference"]
        final = result["final_video"]
        result["motion_reference"] = ({key:motion[key] for key in ("id","version","video_asset_id","source_sha256","status")}
                                      if motion else None)
        result["final_video"] = ({key:final[key] for key in ("id","asset_id","media_job_id","martial_qc_id","martial_qc_result","martial_reviewer","status")}
                                 if final else None)
        return {"lesson": lesson, "art_name": art["chinese_name"], "move_ordinal": move["ordinal"],
                "move_name": result["facts"].get("chinese_name") or f"第{move['ordinal']}式",
                "dependencies": {k:v for k,v in result.items() if k != "facts"},
                "bindings": versions, "shots": shots, "production_rules": production_rules(lesson["art_id"]),
                "asset_roles": {k:sorted(v) for k,v in ROLES.items()}}


def art_dashboard(user: dict, art_id: str) -> dict:
    martial.allow(user)
    with store.connect() as c:
        art = c.execute("SELECT * FROM martial_arts WHERE id=?", (art_id,)).fetchone()
        if not art:raise KeyError(art_id)
        current = c.execute("SELECT 1 FROM sqlite_master WHERE name='martial_business_moves'").fetchone()
        sql = "SELECT m.id FROM martial_moves m"
        if current: sql += " JOIN martial_business_moves b ON b.move_id=m.id AND b.is_current=1"
        sql += " WHERE m.martial_art_id=? ORDER BY m.ordinal"
        lessons = []
        for row in c.execute(sql,(art_id,)).fetchall():
            lesson = _ensure(c, row["id"])
            state = _check(c, lesson)
            lessons.append({"id": lesson["id"], "move_id": row["id"], "status": state["status"],
                            "ready": [k for k,v in state["checks"].items() if v],
                            "missing": [s for s in state["missing"] if s["required_for_publish"]],
                            "ready_work": state["ready_work"],
                            "name": state["facts"].get("chinese_name") or row["id"]})
        counts = {key: sum(key in item["ready"] for item in lessons) for key in LABELS}
        return {"art": {"id": art["id"], "name": art["chinese_name"], "master_id": art["master_id"]},
                "total": len(lessons), "counts": counts, "labels": LABELS, "lessons": lessons}


def review(user: dict, move_id: str, data: dict) -> dict:
    martial.allow(user)
    if user["role"] not in {"founder", "manager"}:
        raise PermissionError("成片视听验收需负责人完成")
    if data.get("stage") != "audiovisual" or data.get("verdict") not in {"pass", "fail"}:
        raise ValueError("验收阶段或结论无效")
    notes = str(data.get("notes") or "").strip()
    evidence = data.get("evidence") or []
    if len(notes) < 12 or not isinstance(evidence,list) or not evidence:
        raise ValueError("请记录实际连续观看、听审范围与证据")
    with store.connect() as c:
        lesson = _ensure(c, move_id)
        final = _active_final(c, move_id)
        if not final or final["martial_qc_result"] != "pass":
            raise ValueError("动作专业验收及正式视频尚未通过")
        version = c.execute("SELECT COALESCE(MAX(version),0)+1 FROM martial_lesson_reviews WHERE lesson_id=? AND stage='audiovisual'", (lesson["id"],)).fetchone()[0]
        c.execute("""INSERT INTO martial_lesson_reviews
          (lesson_id,version,stage,verdict,media_job_id,evidence,notes,reviewer_id,created_at)
          VALUES(?,?,?,?,?,?,?,?,?)""", (lesson["id"], version, "audiovisual", data["verdict"],
                                  final["media_job_id"], store.dumps(evidence), notes, user["id"], store.now()))
        c.execute("UPDATE martial_lesson_packages SET approved_manifest=NULL,approved_sha256=NULL,approved_by=NULL,approved_at=NULL,published_at=NULL,updated_at=? WHERE id=?", (store.now(),lesson["id"]))
        store.audit(c,user["id"],"lesson.audiovisual.review",None,{"lesson_id":lesson["id"],"verdict":data["verdict"]})
    return detail(user,move_id)


def _manifest(c, lesson: dict, state: dict) -> dict:
    final = state["final_video"]
    ref = state["motion_reference"]
    # A historical preview may help production review, but it is not an input
    # to a formally approved teaching output.
    bindings = [dict(r) for r in c.execute("SELECT role,shot_id,version,registry_asset_id,registry_version,sha256,file_ref,source_system,original_id FROM martial_lesson_asset_versions WHERE lesson_id=? AND status='active' AND role NOT LIKE 'pilot_%' ORDER BY role,shot_id",(lesson["id"],))]
    shots = [dict(r) for r in c.execute("SELECT shot_id,version,ordinal,purpose,start_state,end_state,camera,duration FROM martial_lesson_shots WHERE lesson_id=? AND status='active' ORDER BY ordinal,shot_id",(lesson["id"],))]
    art = c.execute("SELECT master_id,version FROM martial_arts WHERE id=?",(lesson["art_id"],)).fetchone()
    move = c.execute("SELECT current_version FROM martial_moves WHERE id=?",(lesson["move_id"],)).fetchone()
    facts_text = c.execute("SELECT payload FROM martial_move_versions WHERE move_id=? AND version=?",(lesson["move_id"],move["current_version"])).fetchone()[0]
    visual_id = state["teacher"].get("visual_asset_id")
    visual = c.execute("SELECT sha256 FROM assets WHERE id=?",(visual_id,)).fetchone()
    output = c.execute("SELECT sha256 FROM assets WHERE id=?",(final["asset_id"],)).fetchone()
    martial_review = c.execute("SELECT result,reviewer_id,checks,issue_ranges,findings FROM martial_qc WHERE id=?",(final["martial_qc_id"],)).fetchone()
    audiovisual = state["review"]
    return {"schema": "MartialArtsLessonPackage/v1", "project_id": "wuxiang",
            "lesson_id": lesson["id"], "chapter": lesson["chapter"], "art_id": lesson["art_id"],
            "art_version": art["version"], "move_id": lesson["move_id"],
            "facts": {"move_version":move["current_version"],"sha256":hashlib.sha256(facts_text.encode()).hexdigest()},
            "teacher": {"master_id":art["master_id"],"master_version": state["teacher"].get("version"),
                        "visual_asset_id":visual_id,"visual_sha256":visual["sha256"] if visual else None,
                        "voice_id":state["teacher"].get("voice_id")},
            "motion_source": {"reference_id": ref["id"], "version": ref["version"], "asset_id": ref["video_asset_id"], "sha256": ref["source_sha256"]},
            "video": {"final_id": final["id"], "asset_id": final["asset_id"], "media_job_id": final["media_job_id"],
                      "package_id": final["package_id"], "provider": final["provider"], "model": final["model"],
                      "prompt_hash": final["prompt_hash"], "sha256": output["sha256"] if output else None,
                      "martial_qc_id": final["martial_qc_id"],
                      "martial_qc_sha256": hashlib.sha256(store.dumps(dict(martial_review)).encode()).hexdigest() if martial_review else None},
            "shots": shots, "assets": bindings, "production_rules": production_rules(lesson["art_id"]),
            "audiovisual_review": {"version": audiovisual["version"], "reviewer_id": audiovisual["reviewer_id"],
                                   "sha256": hashlib.sha256((audiovisual["evidence"]+audiovisual["notes"]).encode()).hexdigest()},
            "placeholders": [k for k in ("narrative_voice", "bgm", "sfx", "subtitle") if not _binding(c,lesson["id"],k)]}


def _verify_manifest_files(c, manifest: dict) -> None:
    for binding in manifest["assets"]:
        row = c.execute("SELECT * FROM asset_registry WHERE asset_id=?", (binding["registry_asset_id"],)).fetchone()
        if not row or row["status"] != "active" or row["version"] != binding["registry_version"] or row["sha256"] != binding["sha256"]:
            raise ValueError("教学包关联素材版本已变化，请重新绑定")
        if not row["file_ref"].startswith("work-os://"):
            raise ValueError("正式教学包需要公司资产库中的可读取文件")
        source = c.execute("SELECT storage_ref FROM assets WHERE id=?", (row["original_id"],)).fetchone()
        if not source or not Path(source["storage_ref"]).is_file() or store.digest_file(Path(source["storage_ref"])) != binding["sha256"]:
            raise ValueError("教学素材文件缺失或校验和变化")
    for key in ("motion_source", "video"):
        item = manifest[key]
        row = c.execute("SELECT storage_ref,sha256 FROM assets WHERE id=?", (item["asset_id"],)).fetchone()
        if not row or not Path(row["storage_ref"]).is_file() or store.digest_file(Path(row["storage_ref"])) != row["sha256"]:
            raise ValueError("动作或成片文件缺失或校验和变化")
    teacher = manifest["teacher"]
    visual = c.execute("SELECT storage_ref,sha256 FROM assets WHERE id=?",(teacher["visual_asset_id"],)).fetchone()
    if not visual or visual["sha256"] != teacher["visual_sha256"] or not Path(visual["storage_ref"]).is_file() or store.digest_file(Path(visual["storage_ref"])) != visual["sha256"]:
        raise ValueError("老师视觉资产缺失或校验和变化")


def approve(user: dict, move_id: str) -> dict:
    martial.allow(user)
    if user["role"] not in {"founder", "manager"}:
        raise PermissionError("正式教学包需负责人批准")
    with store.connect() as c:
        lesson = _ensure(c,move_id)
        state = _check(c,lesson)
        missing = [LABELS[k] for k in ESSENTIAL if not state["checks"][k]]
        if missing:raise ValueError("不能批准；缺少：" + "、".join(missing))
        manifest = _manifest(c,lesson,state)
        _verify_manifest_files(c,manifest)
        body = json.dumps(manifest,ensure_ascii=False,sort_keys=True,separators=(",",":"))
        sha = hashlib.sha256(body.encode()).hexdigest()
        if lesson["approved_sha256"] == sha:return {"version":lesson["current_version"],"sha256":sha,"status":"approved"}
        version = lesson["current_version"] + 1
        c.execute("INSERT INTO martial_lesson_manifests(lesson_id,version,manifest,sha256,status,created_by,created_at) VALUES(?,?,?,?,?,?,?)",
                  (lesson["id"],version,body,sha,"approved",user["id"],store.now()))
        c.execute("""UPDATE martial_lesson_packages SET current_version=?,status='approved',approved_manifest=?,
          approved_sha256=?,approved_by=?,approved_at=?,published_at=NULL,updated_at=? WHERE id=?""",
          (version,body,sha,user["id"],store.now(),store.now(),lesson["id"]))
        store.audit(c,user["id"],"lesson.approve",None,{"lesson_id":lesson["id"],"version":version,"sha256":sha})
        return {"version":version,"sha256":sha,"status":"approved"}


def publish(user: dict, move_id: str) -> dict:
    martial.allow(user)
    if user["role"] not in {"founder", "manager"}:raise PermissionError("发布需负责人确认")
    with store.connect() as c:
        lesson = _ensure(c,move_id)
        if not lesson["approved_sha256"]:raise ValueError("教学包尚未批准")
        state = _check(c,lesson)
        if not all(state["checks"][key] for key in ESSENTIAL):raise ValueError("批准后资产已变化，请重新验收")
        current = _manifest(c,lesson,state)
        _verify_manifest_files(c,current)
        sha = hashlib.sha256(json.dumps(current,ensure_ascii=False,sort_keys=True,separators=(",",":")).encode()).hexdigest()
        if sha != lesson["approved_sha256"]:raise ValueError("生产资产版本已变化，请重新批准")
        c.execute("UPDATE martial_lesson_packages SET status='published',published_at=?,updated_at=? WHERE id=?",(store.now(),store.now(),lesson["id"]))
        c.execute("UPDATE martial_lesson_manifests SET status='published' WHERE lesson_id=? AND version=?",(lesson["id"],lesson["current_version"]))
        store.audit(c,user["id"],"lesson.publish",None,{"lesson_id":lesson["id"],"version":lesson["current_version"]})
        return {"status":"published","version":lesson["current_version"],"sha256":sha}
