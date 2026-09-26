"""Old AI Centre bridge: explicit identity, source auth and no object copy."""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))
import asset_center
import legacy_asset_bridge as bridge
import store


class LegacyAssetBridgeTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.old_data, self.old_db = store.DATA, store.DB
        store.DATA = self.root / "work"
        store.DB = store.DATA / "work_os.sqlite3"
        store.initialize()
        asset_center.initialize()
        bridge.initialize()
        self.owner_id = store.create_user("work-owner", "旧素材主人", "employee", "asset-owner-password")
        self.other_id = store.create_user("work-other", "其他员工", "employee", "asset-other-password")
        self.owner = {"id": self.owner_id, "username": "work-owner", "role": "employee"}
        self.other = {"id": self.other_id, "username": "work-other", "role": "employee"}
        self.manager = {"id": "u_system", "username": "_system", "role": "manager"}
        for uid in (self.owner_id, self.other_id):
            store.create_task({"project_id": "wuxiang", "workflow_id": "WF-02",
                               "title": "素材任务", "why": "测试", "assignee_id": uid}, "u_system")
        with store.connect() as c:
            self.private_id = asset_center._register(c, {
                "source_system": "legacy_ai_center", "original_id": "old-private",
                "project_id": "wuxiang", "type": "image", "name": "旧角色图",
                "file_ref": "legacy-oss://old-private", "status": "active",
                "metadata": {"visibility": "private", "owner_username": "old-owner"},
            })[0]
            self.team_id = asset_center._register(c, {
                "source_system": "legacy_ai_center", "original_id": "old-team",
                "project_id": "wuxiang", "type": "video", "name": "团队视频",
                "file_ref": "legacy-oss://old-team", "status": "active",
                "metadata": {"visibility": "team", "owner_username": "old-owner"},
            })[0]

    def tearDown(self):
        store.DATA, store.DB = self.old_data, self.old_db
        self.temp.cleanup()

    def test_private_requires_explicit_mapping_and_project_access(self):
        with self.assertRaises(PermissionError):
            bridge.descriptor(self.owner, self.private_id)
        with self.assertRaises(PermissionError):
            bridge.map_identity(self.owner, "old-owner", self.owner_id, "wuxiang")
        bridge.map_identity(self.manager, "old-owner", self.owner_id, "wuxiang")
        info = bridge.descriptor(self.owner, self.private_id)
        self.assertEqual(info["preview_url"], f"/api/asset-center/{self.private_id}/legacy-preview")
        self.assertNotIn("object_key", info)
        with self.assertRaises(PermissionError):
            bridge.descriptor(self.other, self.private_id)
        bridge.map_identity(self.manager, "old-owner", self.owner_id, "wuxiang", active=False)
        with self.assertRaises(PermissionError):
            bridge.descriptor(self.owner, self.private_id)

    def test_team_still_requires_work_os_project_permission(self):
        self.assertTrue(bridge.descriptor(self.other, self.team_id)["can_select"])
        outsider_id = store.create_user("outside", "外部", "employee", "outside-password")
        outsider = {"id": outsider_id, "username": "outside", "role": "employee"}
        with self.assertRaises(PermissionError):
            bridge.descriptor(outsider, self.team_id)

    def test_source_rechecks_old_login_and_returns_only_short_lived_url(self):
        bridge.map_identity(self.manager, "old-owner", self.owner_id, "wuxiang")
        signed = "https://bucket.oss-cn-hangzhou.aliyuncs.com/private.png?Expires=123&Signature=opaque"
        with patch.object(bridge, "_source_redirect", return_value=signed) as source:
            with self.assertRaises(PermissionError):
                bridge.resolve_signed_access(self.owner, self.private_id, "yoodun_session=work")
            resolved = bridge.creation_reference(
                self.owner, self.private_id, "wuxiang",
                "yoodun_session=work; wujing_studio_session=old-session")
            self.assertEqual(resolved["temporary_url"], signed)
            self.assertEqual(source.call_count, 1)
            self.assertEqual(source.call_args.args[1:], ("old-private", "old-session"))
        with patch.object(bridge, "_source_redirect", return_value="http://evil.test/a?token=x"):
            with self.assertRaises(ValueError):
                bridge.resolve_signed_access(self.owner, self.private_id,
                                             "wujing_studio_session=old-session")
        with self.assertRaises(PermissionError):
            bridge.creation_reference(self.other, self.private_id, "wuxiang",
                                      "wujing_studio_session=old-session")

    def test_old_job_without_source_asset_stays_unavailable(self):
        with store.connect() as c:
            job_id = asset_center._register(c, {
                "source_system": "legacy_ai_job", "original_id": "job-only",
                "project_id": "wuxiang", "type": "image", "name": "旧输出",
                "file_ref": "legacy-job://job-only", "status": "active",
                "metadata": {"visibility": "private", "owner_username": "old-owner"},
            })[0]
        bridge.map_identity(self.manager, "old-owner", self.owner_id, "wuxiang")
        self.assertIsNone(bridge.descriptor(self.owner, job_id)["preview_url"])
        with self.assertRaises(ValueError):
            bridge.resolve_signed_access(self.owner, job_id,
                                         "wujing_studio_session=old-session")


if __name__ == "__main__":
    unittest.main()
