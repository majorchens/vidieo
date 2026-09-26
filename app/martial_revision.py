"""Immutable, evidence-linked revision briefs for failed Martial QC candidates.

This module prepares instructions only. It never queues a media job, calls a model,
or changes the candidate/QC verdict. A person with the martial production role must
separately decide whether to spend budget on another generation.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3

import store


CHECK_LABELS = {
    "action_order": "动作顺序",
    "hand_path": "手部／翼部路径",
    "footwork": "脚步",
    "center_of_gravity": "重心",
    "start_pose": "起始姿态",
    "end_pose": "结束姿态",
    "key_moments": "关键动作时刻",
    "teaching_suitability": "教学适宜性",
}

# The founder's 2026-09-23 instruction supplies these historical QC directions.
# It is attached only to the verified P0 Candidate A, never to other candidates.
P0_MEDIA_JOB_ID = "mj_94efec7ab99b7e95"
P0_CANDIDATE_ASSET_ID = "a_a63a7b008c349532"
P0_GUIDANCE = {
    "source": "founder_p0_followup_2026-09-23",
    "keep": ["Cryn 当前角色外观", "起势至前段粗动作顺序"],
    "fix": [
        "下肢必须完整进入画面",
        "双脚必须全程可见",
        "3.50–4.90 秒必须清晰表现下按掌势",
        "结束姿态必须匹配真人参考",
        "翼部路径需要进一步贴合真人动作",
    ],
    "do_not_change": ["Cryn 定版身份", "八卦掌招式事实", "真人 Motion REF"],
}


def ensure_schema(c: sqlite3.Connection) -> None:
    c.execute("""
        CREATE TABLE IF NOT EXISTS martial_revision_packages(
            id TEXT PRIMARY KEY,
            media_job_id TEXT NOT NULL UNIQUE REFERENCES martial_media_jobs(id),
            martial_qc_id TEXT NOT NULL UNIQUE REFERENCES martial_qc(id),
            source_hash TEXT NOT NULL,
            payload TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
    """)


def _json(value, default):
    if isinstance(value, (dict, list)):
        return value
    return store.parse(value, default)


def _append_unique(items: list[str], text: str) -> None:
    clean = str(text or "").strip()
    if clean and clean not in items:
        items.append(clean)


def _candidate_source(job: dict) -> dict:
    return {
        "media_job_id": job["id"],
        "candidate_asset_id": job["candidate_asset_id"],
        "asset_type": job["asset_type"],
        "model_alias": job["model_alias"],
        "revision_of": job.get("revision_of"),
        "duration": job["duration"],
    }


def build_revision_payload(package: dict, job: dict, qc: dict,
                           guidance: dict | None = None) -> dict:
    """Build a deterministic brief from the production snapshot and one failed QC.

    The package's locked facts are used rather than current mutable art/master state.
    Explicit founder guidance can supplement, but cannot replace, QC evidence.
    """
    if package.get("id") != job.get("package_id") or package.get("status") != "complete":
        raise ValueError("Candidate 的原 Production Package 不完整或不匹配")
    if job.get("status") != "succeeded" or not job.get("candidate_asset_id"):
        raise ValueError("Candidate 尚未回收")
    if qc.get("media_job_id") != job.get("id") or qc.get("stage") != "martial" or qc.get("result") != "fail":
        raise ValueError("只有 Martial QC 为 REVISION_REQUIRED 的候选能生成返修包")

    facts = _json(package.get("facts"), {})
    result = _json(package.get("result"), {})
    checks = _json(qc.get("checks"), {})
    ranges = _json(qc.get("issue_ranges"), [])
    if not isinstance(facts, dict) or not isinstance(result, dict) or not isinstance(checks, dict) or not isinstance(ranges, list):
        raise ValueError("Revision Package 输入格式无效")
    art, move, master, motion = (facts.get(key) or {} for key in ("art", "move", "master", "motion"))
    if not all(isinstance(value, dict) for value in (art, move, master, motion)):
        raise ValueError("原 Production Package 缺少锁定事实")
    if not master.get("id") or not move.get("version") or not motion.get("id"):
        raise ValueError("原 Production Package 缺少角色、招式或动作版本")
    if motion["id"] != job.get("motion_ref_id") or master.get("version") != job.get("master_version"):
        raise ValueError("Candidate 与原 Production Package 的锁定版本不一致")

    keep: list[str] = []
    fix: list[str] = []
    do_not_change: list[str] = []
    needs_check: list[str] = []
    target_range: list[dict] = []
    for key, label in CHECK_LABELS.items():
        verdict = checks.get(key)
        if verdict == "pass":
            _append_unique(keep, f"保持已通过 QC 的{label}")
        elif verdict == "fail":
            _append_unique(fix, f"修正{label}，对照已锁定真人动作参考")
        elif verdict == "unsure":
            _append_unique(needs_check, f"重新核对{label}；上一版 QC 未能确认")
    for item in ranges:
        if not isinstance(item, dict):
            raise ValueError("Martial QC 问题区间格式无效")
        try:
            start, end = round(float(item["start"]), 3), round(float(item["end"]), 3)
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("Martial QC 问题区间时间无效") from exc
        issue = str(item.get("issue") or "").strip()
        if not 0 <= start < end or not issue:
            raise ValueError("Martial QC 问题区间缺少有效问题")
        target_range.append({"start": start, "end": end, "issue": issue,
                             "move_id":item.get("move_id"),"body_part":item.get("body_part"),
                             "issue_type":item.get("issue_type"),"comment":item.get("comment"),
                             "severity": str(item.get("severity") or "")})
        detail=" · ".join(str(item.get(key) or "") for key in ("body_part","comment") if item.get(key))
        _append_unique(fix, f"{start:.2f}–{end:.2f} 秒：{issue}"+(f"（{detail}）" if detail else ""))

    _append_unique(do_not_change, f"{master.get('name') or master['id']} 定版身份 V{master['version']}"
                   f"（视觉资产 {master.get('visual_asset_id') or job.get('character_asset_id')}）")
    _append_unique(do_not_change, f"{art.get('chinese_name') or '武学'} V{art.get('version')} / "
                   f"{move.get('chinese_name') or '招式'} V{move['version']} 的已确认动作事实")
    _append_unique(do_not_change, f"真人 Motion REF {motion['id']} / V{motion.get('version')}"
                   f"（视频资产 {motion.get('asset_id')}；工作区间 {motion.get('start')}–{motion.get('end')} 秒）")
    for constraint in master.get("forbidden_changes") or []:
        _append_unique(do_not_change, str(constraint))

    if guidance is not None:
        if not isinstance(guidance, dict) or not guidance.get("source"):
            raise ValueError("人工补充指令缺少来源")
        for key, destination in (("keep", keep), ("fix", fix), ("do_not_change", do_not_change)):
            values = guidance.get(key, [])
            if not isinstance(values, list):
                raise ValueError("人工补充指令格式无效")
            for value in values:
                _append_unique(destination, value)

    locked_facts = {
        "art": {key: art.get(key) for key in ("version", "chinese_name", "source_ref")},
        "move": {key: move.get(key) for key in ("version", "chinese_name", "chinese_action", "source_ref")},
        "master": {key: master.get(key) for key in ("id", "name", "version", "visual_asset_id", "canonical_sha256")},
        "motion": {key: motion.get(key) for key in ("id", "version", "asset_id", "start", "end", "orientation", "start_pose", "end_pose")},
    }
    source = {
        "original_production_package_id": package["id"],
        "original_package_facts_hash": package.get("facts_hash"),
        "original_seedance_prompt": result.get("seedance_prompt"),
        "original_character_constraints": result.get("character_constraints", []),
        "original_negative_constraints": result.get("negative_constraints", []),
        "candidate": _candidate_source(job),
        "martial_qc_id": qc["id"],
        "martial_qc_result": qc["result"],
        "qc_checks": checks,
        "qc_findings": qc.get("findings"),
        "reference_comparison": qc.get("reference_comparison"),
        "locked_facts": locked_facts,
        "guidance_source": guidance.get("source") if guidance else None,
    }
    return {
        "status": "ready_for_employee_decision",
        "keep": keep,
        "fix": fix,
        "do_not_change": do_not_change,
        "target_range": target_range,
        "needs_check": needs_check,
        "qc_context": {"findings": str(qc.get("findings") or ""),
                       "reference_comparison": str(qc.get("reference_comparison") or "")},
        "source": source,
        "auto_generate_video": False,
    }


def render_instructions(payload: dict) -> str:
    """Render a stored brief for a future employee-authorized revision job."""
    if payload.get("status") != "ready_for_employee_decision" or payload.get("auto_generate_video") is not False:
        raise ValueError("Revision Package 尚不可用于返修")
    sections = (
        ("KEEP", "keep"),
        ("FIX", "fix"),
        ("DO NOT CHANGE", "do_not_change"),
    )
    lines = ["以下是上一版 Candidate 的 Martial QC 返修约束；不得改写已确认事实。"]
    for title, key in sections:
        lines.append(title + ":")
        for item in payload.get(key, []):
            lines.append("- " + str(item))
    lines.append("TARGET RANGE:")
    for item in payload.get("target_range", []):
        lines.append(f"- {float(item['start']):.2f}–{float(item['end']):.2f} 秒：{item['issue']}")
    if payload.get("needs_check"):
        lines.append("VERIFY:")
        for item in payload["needs_check"]:
            lines.append("- " + str(item))
    context = payload.get("qc_context") or {}
    if context.get("findings") or context.get("reference_comparison"):
        lines.append("QC EVIDENCE:")
        if context.get("findings"):
            lines.append("- 结论：" + str(context["findings"]))
        if context.get("reference_comparison"):
            lines.append("- 真人参考对照：" + str(context["reference_comparison"]))
    return "\n".join(lines)


def ensure_for_job(c: sqlite3.Connection, media_job_id: str) -> dict:
    """Insert once for a failed candidate; safe in the same transaction as QC."""
    ensure_schema(c)
    existing = c.execute("SELECT * FROM martial_revision_packages WHERE media_job_id=?", (media_job_id,)).fetchone()
    if existing:
        row = dict(existing)
        return {"id": row["id"], "media_job_id": row["media_job_id"],
                "martial_qc_id": row["martial_qc_id"], "source_hash": row["source_hash"],
                "created_at": row["created_at"], "payload": store.parse(row["payload"], {})}
    job_row = c.execute("SELECT * FROM martial_media_jobs WHERE id=?", (media_job_id,)).fetchone()
    if not job_row:
        raise ValueError("未找到 Candidate")
    job = dict(job_row)
    qc_row = c.execute("SELECT * FROM martial_qc WHERE media_job_id=? AND stage='martial' ORDER BY created_at DESC LIMIT 1", (media_job_id,)).fetchone()
    if not qc_row or qc_row["result"] != "fail":
        raise ValueError("Candidate 尚无 REVISION_REQUIRED 的 Martial QC")
    package_row = c.execute("SELECT * FROM martial_packages WHERE id=?", (job["package_id"],)).fetchone()
    if not package_row:
        raise ValueError("原 Production Package 不存在")
    guidance = P0_GUIDANCE if (job["id"] == P0_MEDIA_JOB_ID and
                               job["candidate_asset_id"] == P0_CANDIDATE_ASSET_ID) else None
    payload = build_revision_payload(dict(package_row), job, dict(qc_row), guidance)
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    source_hash = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    revision_id = "mrp_" + hashlib.sha256(f"{media_job_id}:{qc_row['id']}".encode()).hexdigest()[:16]
    created_at = store.now()
    c.execute("INSERT INTO martial_revision_packages(id,media_job_id,martial_qc_id,source_hash,payload,created_at) VALUES(?,?,?,?,?,?)",
              (revision_id, media_job_id, qc_row["id"], source_hash, canonical, created_at))
    return {"id": revision_id, "media_job_id": media_job_id, "martial_qc_id": qc_row["id"],
            "source_hash": source_hash, "created_at": created_at, "payload": payload}


def backfill_p0(c: sqlite3.Connection) -> dict | None:
    """Materialize the historical failed P0 candidate without queuing a V2."""
    ensure_schema(c)
    row = c.execute("SELECT candidate_asset_id FROM martial_media_jobs WHERE id=?", (P0_MEDIA_JOB_ID,)).fetchone()
    if not row or row["candidate_asset_id"] != P0_CANDIDATE_ASSET_ID:
        return None
    failed = c.execute("SELECT 1 FROM martial_qc WHERE media_job_id=? AND stage='martial' AND result='fail'", (P0_MEDIA_JOB_ID,)).fetchone()
    return ensure_for_job(c, P0_MEDIA_JOB_ID) if failed else None
