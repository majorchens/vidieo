"""Revision package is grounded in failed QC and cannot queue paid media."""
from __future__ import annotations

import json
import sqlite3
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
import martial_revision as revision


def production_rows(media_id="mj_test", candidate_id="a_candidate"):
    facts = {
        "art": {"version": 1, "chinese_name": "八卦掌", "source_ref": "source"},
        "move": {"version": 1, "chinese_name": "起式按掌", "chinese_action": "先起再按", "source_ref": "source"},
        "master": {"id": "cryn", "name": "Cryn", "version": 2, "visual_asset_id": "a_character",
                   "canonical_sha256": "hash", "forbidden_changes": ["不可改变定版喙形"]},
        "motion": {"id": "ref_1", "version": 1, "asset_id": "a_human", "start": 5.3,
                   "end": 9.7, "orientation": "正面", "start_pose": "起势", "end_pose": "下按"},
    }
    package = {"id": "pkg_1", "status": "complete", "facts_hash": "facts-hash",
               "facts": json.dumps(facts),
               "result": json.dumps({"seedance_prompt": "原教学视频提示词",
                                     "character_constraints": ["保持 Cryn"],
                                     "negative_constraints": ["不得裁脚"]})}
    job = {"id": media_id, "package_id": "pkg_1", "status": "succeeded",
           "candidate_asset_id": candidate_id, "asset_type": "teaching", "model_alias": "sd2.5",
           "revision_of": None, "duration": 5, "motion_ref_id": "ref_1", "master_version": 2,
           "character_asset_id": "a_character"}
    qc = {"id": "mqc_1", "media_job_id": media_id, "stage": "martial", "result": "fail",
          "checks": json.dumps({"action_order": "pass", "hand_path": "fail", "footwork": "unsure",
                                "center_of_gravity": "unsure", "start_pose": "pass", "end_pose": "fail",
                                "key_moments": "fail", "teaching_suitability": "fail"}),
          "issue_ranges": json.dumps([{"start": 3.5, "end": 4.9, "issue": "下按掌势不清", "severity": "major"},
                                       {"start": 0, "end": 5, "issue": "双脚被裁掉", "severity": "major"}]),
          "findings": "下按动作失败", "reference_comparison": "对照真人 REF"}
    return package, job, qc


class RevisionPackageTest(unittest.TestCase):
    def test_structured_brief_preserves_pass_and_locks(self):
        package, job, qc = production_rows()
        brief = revision.build_revision_payload(package, job, qc)
        self.assertFalse(brief["auto_generate_video"])
        self.assertIn("保持已通过 QC 的动作顺序", brief["keep"])
        self.assertIn("保持已通过 QC 的起始姿态", brief["keep"])
        self.assertIn("修正手部／翼部路径，对照已锁定真人动作参考", brief["fix"])
        self.assertIn("3.50–4.90 秒：下按掌势不清", brief["fix"])
        self.assertEqual((brief["target_range"][0]["start"], brief["target_range"][0]["end"]), (3.5, 4.9))
        self.assertTrue(any("真人 Motion REF ref_1 / V1" in item for item in brief["do_not_change"]))
        self.assertEqual(brief["source"]["original_production_package_id"], "pkg_1")
        self.assertEqual(brief["source"]["candidate"]["candidate_asset_id"], "a_candidate")
        self.assertIn("重新核对脚步", brief["needs_check"][0])
        instructions = revision.render_instructions(brief)
        for section in ("KEEP:", "FIX:", "DO NOT CHANGE:", "TARGET RANGE:", "VERIFY:", "QC EVIDENCE:"):
            self.assertIn(section, instructions)

    def test_qc_pass_cannot_create_revision(self):
        package, job, qc = production_rows()
        qc["result"] = "pass"
        with self.assertRaisesRegex(ValueError, "REVISION_REQUIRED"):
            revision.build_revision_payload(package, job, qc)

    def test_historical_p0_is_idempotent_and_does_not_create_v2(self):
        package, job, qc = production_rows(revision.P0_MEDIA_JOB_ID, revision.P0_CANDIDATE_ASSET_ID)
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.executescript("""
            CREATE TABLE martial_packages(id TEXT PRIMARY KEY, status TEXT, facts_hash TEXT, facts TEXT, result TEXT);
            CREATE TABLE martial_media_jobs(id TEXT PRIMARY KEY, package_id TEXT, status TEXT,
                candidate_asset_id TEXT, asset_type TEXT, model_alias TEXT, revision_of TEXT,
                duration INTEGER, motion_ref_id TEXT, master_version INTEGER, character_asset_id TEXT);
            CREATE TABLE martial_qc(id TEXT PRIMARY KEY, media_job_id TEXT, stage TEXT, result TEXT,
                checks TEXT, issue_ranges TEXT, findings TEXT, reference_comparison TEXT, created_at TEXT);
        """)
        conn.execute("INSERT INTO martial_packages VALUES(?,?,?,?,?)",
                     tuple(package[key] for key in ("id", "status", "facts_hash", "facts", "result")))
        conn.execute("INSERT INTO martial_media_jobs VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                     tuple(job[key] for key in ("id", "package_id", "status", "candidate_asset_id", "asset_type",
                                                "model_alias", "revision_of", "duration", "motion_ref_id",
                                                "master_version", "character_asset_id")))
        conn.execute("INSERT INTO martial_qc VALUES(?,?,?,?,?,?,?,?,?)",
                     tuple(qc[key] for key in ("id", "media_job_id", "stage", "result", "checks",
                                               "issue_ranges", "findings", "reference_comparison")) + ("2026-09-23",))
        first = revision.backfill_p0(conn)
        second = revision.backfill_p0(conn)
        self.assertEqual(first["id"], second["id"])
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM martial_revision_packages").fetchone()[0], 1)
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM martial_media_jobs").fetchone()[0], 1)
        self.assertEqual(conn.execute("SELECT result FROM martial_qc").fetchone()[0], "fail")
        self.assertEqual(first["payload"]["source"]["guidance_source"], "founder_p0_followup_2026-09-23")
        self.assertIn("双脚必须全程可见", first["payload"]["fix"])
        self.assertIn("Cryn 当前角色外观", first["payload"]["keep"])
        self.assertIn("八卦掌招式事实", first["payload"]["do_not_change"])
        conn.close()


if __name__ == "__main__":
    unittest.main()
