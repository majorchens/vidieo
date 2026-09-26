"""Source confirmation must not reapprove the P0 pilot or employee edits."""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))

import martial
import martial_initialization as initialization
import store


class SourceInitializationTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        store.DATA = Path(self.temp.name)
        store.DB = store.DATA / "work_os.sqlite3"
        root = store.DATA / "project"
        root.mkdir()
        store.PROJECT_ROOTS["wuxiang"] = root
        store.SHARED_ROOT = store.DATA / "shared"
        store.SHARED_ROOT.mkdir()
        store.initialize()
        martial.initialize()

    def tearDown(self):
        self.temp.cleanup()

    def test_exact_import_activates_once_and_marks_incomplete_source(self):
        first = initialization.initialize_confirmed_import()
        self.assertEqual((len(first["activated_arts"]), len(first["activated_moves"])), (5, 47))
        self.assertEqual((first["skipped_arts"], first["skipped_moves"]), ({}, {}))
        self.assertEqual(first["provenance_created"], 52)
        self.assertEqual(first["normal_production_approval_count"], 0)
        with store.connect() as connection:
            art = connection.execute("SELECT status,version,draft_version FROM martial_arts WHERE id='beginner'").fetchone()
            move = connection.execute("SELECT current_version,draft_version FROM martial_moves WHERE id='mv_beginner_01'").fetchone()
            source = connection.execute("SELECT status,source_sha256,version,imported_at,missing_fields "
                                        "FROM martial_import_confirmations WHERE kind='move' AND record_id='mv_beginner_01'").fetchone()
            original_created = connection.execute("SELECT created_at FROM martial_move_versions "
                                                  "WHERE move_id='mv_beginner_01' AND version=1").fetchone()[0]
            before_audit = connection.execute("SELECT COUNT(*) FROM audit WHERE action='martial.import.source_confirm'").fetchone()[0]
        self.assertEqual(tuple(art), ("active", 1, 0))
        self.assertEqual(tuple(move), (1, 0))
        self.assertEqual(source["status"], "confirmed_with_missing_fields")
        self.assertEqual(source["source_sha256"], initialization.SOURCE_SHA256)
        self.assertEqual(source["version"], 1)
        self.assertEqual(source["imported_at"], original_created)
        self.assertEqual(json.loads(source["missing_fields"]), list(initialization.REQUIRED_SOURCE_FIELDS))
        self.assertEqual(before_audit, 1)
        stats = initialization.initialization_stats()
        self.assertEqual(stats["arts"]["confirmed"], 5)
        self.assertEqual(stats["moves"]["confirmed"], 46)
        self.assertEqual(stats["moves"]["confirmed_with_missing_fields"], 1)
        self.assertEqual(stats["incomplete_move_ids"], ["mv_beginner_01"])
        second = initialization.initialize_confirmed_import()
        self.assertEqual((len(second["activated_arts"]), len(second["activated_moves"])), (0, 0))
        self.assertEqual((len(second["preserved_arts"]), len(second["preserved_moves"])), (5, 47))
        self.assertEqual(second["provenance_created"], 0)
        with store.connect() as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM audit WHERE action='martial.import.source_confirm'").fetchone()[0], 1)

    def test_preserves_existing_p0_active_rows_and_skips_modified_drafts(self):
        editor = store.create_user("editor", "编辑员工", "employee", "employee-password-test")
        with store.connect() as connection:
            connection.execute("UPDATE martial_arts SET status='active',draft_version=0 WHERE id='bagua'")
            connection.execute("UPDATE martial_art_versions SET status='active',approved_by=?,approved_at='P0-time' "
                               "WHERE art_id='bagua' AND version=1", (editor,))
            connection.execute("UPDATE martial_moves SET current_version=1,draft_version=0 WHERE id='mv_bagua_01'")
            connection.execute("UPDATE martial_move_versions SET status='active',submitted_by=?,approved_by=?,"
                               "submitted_at='P0-time',approved_at='P0-time' WHERE move_id='mv_bagua_01' AND version=1",
                               (editor, editor))
            p0_art = tuple(connection.execute("SELECT * FROM martial_arts WHERE id='bagua'").fetchone())
            p0_move = tuple(connection.execute("SELECT * FROM martial_moves WHERE id='mv_bagua_01'").fetchone())
            p0_art_v1 = tuple(connection.execute("SELECT * FROM martial_art_versions WHERE art_id='bagua' AND version=1").fetchone())
            p0_move_v1 = tuple(connection.execute("SELECT * FROM martial_move_versions WHERE move_id='mv_bagua_01' AND version=1").fetchone())
            old_art = connection.execute("SELECT payload,source_ref FROM martial_art_versions WHERE art_id='flowing_cloud' AND version=1").fetchone()
            edited_art = json.loads(old_art["payload"])
            edited_art["description"] += "（员工待核对）"
            connection.execute("UPDATE martial_art_versions SET status='superseded' WHERE art_id='flowing_cloud' AND version=1")
            connection.execute("INSERT INTO martial_art_versions(art_id,version,payload,status,source_ref,created_by,created_at) "
                               "VALUES('flowing_cloud',2,?,'needs_review',?,?,?)",
                               (store.dumps(edited_art), old_art["source_ref"], editor, store.now()))
            connection.execute("UPDATE martial_arts SET draft_version=2 WHERE id='flowing_cloud'")
            old_move = connection.execute("SELECT payload,source_ref FROM martial_move_versions "
                                          "WHERE move_id='mv_mountain_fist_02' AND version=1").fetchone()
            edited_move = json.loads(old_move["payload"])
            edited_move["chinese_action"] += "（待核对）"
            connection.execute("UPDATE martial_move_versions SET status='superseded' "
                               "WHERE move_id='mv_mountain_fist_02' AND version=1")
            connection.execute("INSERT INTO martial_move_versions(move_id,version,payload,status,source_ref,created_by,created_at) "
                               "VALUES('mv_mountain_fist_02',2,?,'needs_review',?,?,?)",
                               (store.dumps(edited_move), old_move["source_ref"], editor, store.now()))
            connection.execute("UPDATE martial_moves SET draft_version=2 WHERE id='mv_mountain_fist_02'")
            connection.execute("UPDATE martial_move_versions SET payload='{}' WHERE move_id='mv_taiji_03' AND version=1")
        result = initialization.initialize_confirmed_import()
        self.assertIn("bagua", result["preserved_arts"])
        self.assertIn("mv_bagua_01", result["preserved_moves"])
        self.assertEqual(result["skipped_arts"]["flowing_cloud"], "later_version_exists")
        self.assertEqual(result["skipped_moves"]["mv_mountain_fist_02"], "later_version_exists")
        self.assertEqual(result["skipped_moves"]["mv_taiji_03"], "source_mismatch")
        with store.connect() as connection:
            self.assertEqual(tuple(connection.execute("SELECT * FROM martial_arts WHERE id='bagua'").fetchone()), p0_art)
            self.assertEqual(tuple(connection.execute("SELECT * FROM martial_moves WHERE id='mv_bagua_01'").fetchone()), p0_move)
            self.assertEqual(tuple(connection.execute("SELECT * FROM martial_art_versions WHERE art_id='bagua' AND version=1").fetchone()), p0_art_v1)
            self.assertEqual(tuple(connection.execute("SELECT * FROM martial_move_versions WHERE move_id='mv_bagua_01' AND version=1").fetchone()), p0_move_v1)
            self.assertEqual(connection.execute("SELECT status,draft_version FROM martial_arts WHERE id='flowing_cloud'").fetchone()["draft_version"], 2)
            self.assertEqual(connection.execute("SELECT current_version,draft_version FROM martial_moves WHERE id='mv_mountain_fist_02'").fetchone()["draft_version"], 2)
            self.assertEqual(connection.execute("SELECT current_version FROM martial_moves WHERE id='mv_taiji_03'").fetchone()[0], 0)

    def test_rejects_registry_byte_change_before_any_database_write(self):
        copy = Path(self.temp.name) / "modified-dictionary.json"
        raw = initialization.REGISTRY.read_text()
        copy.write_text(raw.replace("呼吸法", "呼吸法已改", 1))
        with self.assertRaisesRegex(ValueError, "原始 DOCX 提取结果不一致"):
            initialization.initialize_confirmed_import(copy)
        with store.connect() as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM martial_arts WHERE status='active'").fetchone()[0], 0)
            self.assertIsNone(connection.execute("SELECT name FROM sqlite_master WHERE name='martial_import_confirmations'").fetchone())


if __name__ == "__main__":
    unittest.main()
