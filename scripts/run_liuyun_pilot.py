#!/usr/bin/env python3
"""Exercise the lesson package with the existing Liuyun Palm source material.

The source film predates the current teacher and motion review. This script
archives it as a *historical pilot* in an isolated Work OS data directory;
it never confirms human motion, submits generation, or approves publication.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))

import asset_center
import lesson_pipeline
import martial
import martial_initialization
import martial_multimodal_assets
import martial_product
import store


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path,
                        default=Path("/Users/majorchen/Documents/ChatGPT/武术短剧拍摄"))
    parser.add_argument("--data-dir", type=Path, required=True,
                        help="empty directory outside Git; holds copied production assets and SQLite")
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    if args.data_dir.exists() and any(args.data_dir.iterdir()):
        parser.error("--data-dir must be empty; this pilot never alters an existing Work OS database")
    args.data_dir.mkdir(parents=True, exist_ok=True)
    store.DATA = args.data_dir.resolve()
    store.DB = store.DATA / "work_os.sqlite3"
    store.initialize()
    martial.initialize()
    martial_initialization.initialize_confirmed_import()
    martial_product.initialize_product_migration()
    martial_multimodal_assets.initialize()
    asset_center.initialize()
    lesson_pipeline.initialize()

    actor = {"id": "u_system", "role": "manager"}
    source = args.source_root.resolve()
    entries = {
        "human_source": source / "work/liuyun-palm01-sd25-480p-v1/source/original.mp4",
        "pilot_motion_ref": source / "work/liuyun-palm01-sd25-480p-v1/references/motion/LY01_motion480_20s.mp4",
        "scene": source / "work/liuyun-palm01-sd25-480p-v1/references/Wongkey_school_v1.png",
        "pilot_audio": source / "work/liuyun-palm01-sd25-480p-v1/sound/Wongkey_liuyun_EN_20s_v1.wav",
        "pilot_video": source / "work/first-forms-20260917/delivery-r2/Wongkey_Liuyun_Palm_First_Form_EN_480P.mp4",
        "pilot_subtitle": source / "work/first-forms-20260917/delivery-r2/Wongkey_Liuyun_Palm_First_Form_EN_480P.srt",
    }
    if any(not path.is_file() for path in entries.values()):
        raise FileNotFoundError("one or more existing Liuyun Palm pilot sources are missing")
    move_id = "mv_flowing_cloud_01"
    copied = {}
    for role, source_file in entries.items():
        saved = store.register_production_asset("wuxiang", role, source_file.name,
                                                source_file.read_bytes(), actor["id"])
        copied[role] = {"asset_id": saved["id"], "sha256": saved["sha256"],
                        "bytes": source_file.stat().st_size,
                        "technical_video": martial._probe_video_file(Path(saved["storage_ref"]))
                        if source_file.suffix == ".mp4" else None}
        if role == "human_source":
            martial.link_motion(actor, move_id, {"asset_id": saved["id"],
                                                     "notes": "历史真人素材待武术同事复核；未确认标准动作"})
            continue
        asset_center.sync_internal()
        with store.connect() as c:
            registry = c.execute("SELECT asset_id FROM asset_registry_sources WHERE source_system='work_os' AND original_id=?",
                                 (saved["id"],)).fetchone()
        if not registry:
            raise RuntimeError("Asset Center did not index " + role)
        lesson_pipeline.bind_asset(actor, move_id, {"role": role, "asset_id": registry[0]})
        copied[role]["registry_asset_id"] = registry[0]
    state = lesson_pipeline.detail(actor, move_id)
    if state["dependencies"]["checks"]["motion_source"] or state["dependencies"]["checks"]["video"]:
        raise AssertionError("historical pilot was mistaken for a human approved production output")
    try:
        lesson_pipeline.approve(actor, move_id)
    except ValueError as exc:
        approval_block = str(exc)
    else:
        raise AssertionError("pilot unexpectedly passed the formal release gate")
    report = {
        "schema": "MartialArtsLessonPilot/v1", "project_id": "wuxiang", "art_id": "flowing_cloud",
        "lesson_id": state["lesson"]["id"], "move_id": move_id,
        "mode": "historical_media_ingest_and_dependency_test", "formal_approval": False,
        "status": state["dependencies"]["status"],
        "checks": state["dependencies"]["checks"],
        "missing_required": [{"key": x["key"], "owner": x["owner"]}
                             for x in state["dependencies"]["missing"] if x["required_for_publish"]],
        "ready_work": state["dependencies"]["ready_work"],
        "approval_block": approval_block,
        "sources": {role: {key: value for key, value in item.items()
                            if key in {"sha256", "bytes", "technical_video"}}
                    for role, item in copied.items()},
        "quality_note": "历史 R2 有掌位与朝向问题；只验证文件、技术解析、资产归集和发布拦截。未做当前版本动作或完整视听人工验收。",
    }
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({key: report[key] for key in ("lesson_id", "status", "formal_approval", "approval_block")}, ensure_ascii=False))


if __name__ == "__main__":
    main()
