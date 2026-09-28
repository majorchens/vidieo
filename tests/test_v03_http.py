"""V0.3 HTTP smoke on an isolated database; never contacts providers."""
from __future__ import annotations

import http.client
import base64
import io
import json
import sys
import tempfile
import threading
import unittest
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))
import ai_studio
import asset_center
import legacy_asset_bridge
import lesson_pipeline
import martial
import martial_initialization
import martial_multimodal_assets
import martial_product
import server
import store


class V03HttpSmokeTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.old_data, self.old_db = store.DATA, store.DB
        self.old_projects, self.old_shared = dict(store.PROJECT_ROOTS), store.SHARED_ROOT
        store.DATA = self.root / "work"
        store.DB = store.DATA / "work_os.sqlite3"
        project = self.root / "project"
        project.mkdir()
        store.PROJECT_ROOTS["wuxiang"] = project
        store.SHARED_ROOT = self.root / "shared"
        store.SHARED_ROOT.mkdir()
        store.initialize()
        martial.initialize()
        martial_initialization.initialize_confirmed_import()
        martial_product.initialize_product_migration()
        martial_multimodal_assets.initialize()
        ai_studio.initialize()
        asset_center.initialize()
        lesson_pipeline.initialize()
        legacy_asset_bridge.initialize()
        self.manager_password = "http-manager-password"
        self.worker_password = "http-worker-password"
        self.manager_id = store.create_user("http-manager", "HTTP 管理员", "manager", self.manager_password)
        self.worker_id = store.create_user("http-worker", "HTTP 员工", "employee", self.worker_password)
        self.worker = {"id": self.worker_id, "username": "http-worker", "role": "employee"}
        with store.connect() as c:
            c.execute("INSERT INTO martial_specialists(user_id,project_id,title,created_at) VALUES(?,?,?,?)",
                      (self.worker_id, "wuxiang", "武术生产", store.now()))
        self.master_id = martial_product.create_master(
            self.worker, {"name": "HTTP 老师", "species": "鹤", "voice_id": "VOICE-1"})["id"]
        self.art_id = martial_product.create_art(
            self.worker, {"chinese_name": "HTTP 功法", "category": "测试",
                          "master_id": self.master_id})["id"]
        self.move_id = martial_product.create_move(
            self.worker, {"martial_art_id": self.art_id, "chinese_name": "HTTP 招式",
                          "chinese_action": "向前推掌", "source_ref": "http-smoke"})["id"]
        sound = io.BytesIO()
        with wave.open(sound, "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(8000)
            wav.writeframes(b"\x00\x00" * 80)
        self.audio_id = store.register_submission_asset(
            "wuxiang", "test.wav", sound.getvalue(), self.worker_id)["id"]
        with store.connect() as c:
            self.legacy_id = asset_center._register(c, {
                "source_system": "legacy_ai_center", "original_id": "legacy-private",
                "project_id": "wuxiang", "type": "image", "name": "私有旧素材",
                "file_ref": "legacy-oss://legacy-private", "status": "active",
                "metadata": {"visibility": "private", "owner_username": "old-worker"},
            })[0]
        self.http = server.ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        self.thread = threading.Thread(target=self.http.serve_forever, daemon=True)
        self.thread.start()
        self.port = self.http.server_address[1]
        self.manager = self.login("http-manager", self.manager_password)
        self.employee = self.login("http-worker", self.worker_password)

    def tearDown(self):
        self.http.shutdown()
        self.http.server_close()
        self.thread.join(timeout=2)
        store.DATA, store.DB = self.old_data, self.old_db
        store.PROJECT_ROOTS, store.SHARED_ROOT = self.old_projects, self.old_shared
        self.temp.cleanup()

    def request(self, method, path, payload=None, auth=None, cookie_extra=""):
        headers = {}
        body = None
        if payload is not None:
            body = json.dumps(payload, ensure_ascii=False).encode()
            headers["Content-Type"] = "application/json"
        if auth:
            headers["Cookie"] = auth["cookie"] + cookie_extra
            if method != "GET":
                headers["X-CSRF-Token"] = auth["csrf"]
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        try:
            connection.request(method, path, body=body, headers=headers)
            response = connection.getresponse()
            data = response.read()
            content_type = response.getheader("Content-Type", "")
            result = json.loads(data) if "json" in content_type and data else data.decode()
            return response.status, dict(response.getheaders()), result
        finally:
            connection.close()

    def login(self, username, password):
        status, headers, payload = self.request("POST", "/api/login",
                                                {"username": username, "password": password})
        self.assertEqual(status, 200)
        cookie = headers["Set-Cookie"].split(";", 1)[0]
        return {"cookie": cookie, "csrf": payload["csrf"]}

    def test_static_v03_and_cache_headers(self):
        status, headers, page = self.request("GET", "/")
        self.assertEqual(status, 200)
        self.assertIn("YOODUN WORK OS · V0.3", page)
        self.assertEqual(headers["Cache-Control"], "no-store")
        self.assertIn("martial.js?v=20260928-video-optimization-1", page)
        status, headers, script = self.request("GET", "/martial.js?v=20260928-video-optimization-1")
        self.assertEqual(status, 200)
        self.assertEqual(headers["Cache-Control"], "no-store")
        self.assertIn("moveAssetSummary", script)
        self.assertIn("新建视频优化任务", script)
        status, headers, _ = self.request("GET", "/martial.css?v=20260928-video-optimization-1")
        self.assertEqual(status, 200)
        self.assertEqual(headers["Cache-Control"], "no-store")

    def test_lesson_dashboard_and_shot_route(self):
        status, _, dashboard = self.request("GET", "/api/martial/lesson-dashboard/flowing_cloud", auth=self.employee)
        self.assertEqual(status, 200)
        self.assertGreater(dashboard["total"], 0)
        move_id = "mv_flowing_cloud_01"
        status, _, lesson = self.request("GET", f"/api/martial/lessons/{move_id}", auth=self.employee)
        self.assertEqual(status, 200)
        self.assertFalse(lesson["dependencies"]["checks"]["shot_plan"])
        status, _, lesson = self.request("POST", f"/api/martial/lessons/{move_id}/shot", {
            "ordinal": 1, "duration": 6, "purpose": "展示起势与重心",
            "camera": "全身正面固定", "start_state": "双拳置于腰间", "end_state": "左掌向前推"}, self.employee)
        self.assertEqual(status, 200)
        self.assertTrue(lesson["dependencies"]["checks"]["shot_plan"])
        status, _, error = self.request("POST", f"/api/martial/lessons/{move_id}/approve", {}, self.manager)
        self.assertEqual(status, 400)
        self.assertIn("不能批准", error["error"])

    def test_lesson_stage_tasks_are_visible_and_claimable(self):
        move_id="mv_flowing_cloud_01"
        status,_,tasks=self.request("POST",f"/api/martial/lessons/{move_id}/tasks",{},self.manager)
        self.assertEqual(status,200)
        self.assertTrue(any(t["stage"]=="motion_source" for t in tasks))
        status,_,pool=self.request("GET","/api/martial/lesson-tasks",auth=self.employee)
        self.assertEqual(status,200)
        source=next(t for t in pool["tasks"] if t["move_id"]==move_id and t["stage"]=="motion_source")
        self.assertEqual(source["production_status"],"Ready")
        status,_,claimed=self.request("POST",f"/api/martial/lesson-tasks/{source['id']}/claim",{},self.employee)
        self.assertEqual(status,200)
        self.assertEqual(claimed["status"],"assigned")
        status,_,task=self.request("GET",f"/api/tasks/{source['id']}",auth=self.employee)
        self.assertEqual(status,200)
        self.assertEqual(task["context"]["lesson_stage"],"motion_source")

    def test_creative_lab_import_route_and_review_gate(self):
        picture=(ROOT / "tests/fixtures/wuxiang/cryn-character.jpg").read_bytes()
        status, _, imported = self.request("POST", "/api/asset-center/import-creative-lab", {
            "project_id": "wuxiang", "experiment_id": "W01", "source_ref": "Creative Lab/Wushu/W01/sample.jpg",
            "upload": {"name": "sample.jpg", "base64": base64.b64encode(picture).decode()}}, self.employee)
        self.assertEqual(status, 201)
        asset_id=imported["asset"]["asset_id"]
        status, _, denied = self.request("POST", "/api/martial/lessons/mv_flowing_cloud_01/bind", {
            "role": "background", "asset_id": asset_id}, self.employee)
        self.assertEqual(status, 400)
        self.assertIn("Learning Review", denied["error"])
        status, _, promoted = self.request("POST", f"/api/asset-center/{asset_id}/promote", {
            "learning_review_ref": "Creative Lab/Batch_001_Learning_Review.md",
            "notes": "复核该场景的来源与镜头用途；没有批准标准动作"}, self.manager)
        self.assertEqual(status, 200)
        self.assertEqual(promoted["asset"]["asset_id"],asset_id)

    def test_multimodal_routes_and_inheritance(self):
        art_path = f"/api/martial/arts/{self.art_id}/multimodal"
        master_path = f"/api/martial/masters/{self.master_id}/multimodal"
        move_path = f"/api/martial/moves/{self.move_id}/multimodal"
        status, _, art = self.request("GET", art_path, auth=self.employee)
        self.assertEqual(status, 200)
        self.assertEqual(art["art_id"], self.art_id)
        status, _, result = self.request("POST", f"/api/martial/arts/{self.art_id}/worldview",
                                         {"zh_text": "旧世界观"}, self.employee)
        self.assertEqual(status, 200)
        self.assertEqual(result["worldview"]["version"], 1)
        status, _, result = self.request("POST", f"/api/martial/arts/{self.art_id}/asset",
                                         {"role": "training_bgm", "asset_id": self.audio_id}, self.employee)
        self.assertEqual(status, 200)
        self.assertEqual(result["assets"]["training_bgm"]["asset_id"], self.audio_id)
        status, _, result = self.request("POST", f"/api/martial/masters/{self.master_id}/voice-persona",
                                         {"voice_id": "VOICE-1", "persona": "温和"}, self.employee)
        self.assertEqual(status, 200)
        self.assertEqual(result["voice_persona"]["persona"], "温和")
        status, _, result = self.request("POST", f"/api/martial/moves/{self.move_id}/os-script",
                                         {"kind": "intro", "zh_text": "请准备"}, self.employee)
        self.assertEqual(status, 200)
        self.assertEqual(result["scripts"]["intro"]["version"], 1)
        status, _, master = self.request("GET", master_path, auth=self.employee)
        self.assertEqual(status, 200)
        self.assertEqual(master["voice_id"], "VOICE-1")
        status, _, move = self.request("GET", move_path, auth=self.employee)
        self.assertEqual(status, 200)
        self.assertEqual(move["bgm"]["effective_asset_id"], self.audio_id)
        self.assertEqual(move["bgm"]["source"], "art")
        self.assertEqual(move["voice_inheritance"]["voice_id"], "VOICE-1")
        self.assertEqual(move["scripts"]["intro"]["zh_text"], "请准备")

    def test_private_legacy_preview_denies_unmapped_and_missing_old_login(self):
        path = f"/api/asset-center/{self.legacy_id}/legacy-preview"
        status, _, _ = self.request("GET", path, auth=self.employee)
        self.assertEqual(status, 403)
        status, _, mapped = self.request("POST", "/api/asset-center/admin/legacy-identity",
                                          {"legacy_username": "old-worker", "work_user_id": self.worker_id,
                                           "project_id": "wuxiang", "active": True}, self.manager)
        self.assertEqual(status, 200)
        self.assertTrue(mapped["active"])
        status, _, error = self.request("GET", path, auth=self.employee)
        self.assertEqual(status, 403)
        self.assertIn("旧 AI 中心", error["error"])

    def test_image_route_status_and_submission_limits_without_model_call(self):
        status, _, overview = self.request("GET", "/api/ai-studio/overview", auth=self.manager)
        self.assertEqual(status, 200)
        self.assertEqual(overview["capabilities"]["image"]["status"], "DISABLED")
        baseline = {"capability": "image", "project_id": "wuxiang", "prompt": "一只蓝色圆形"}
        status, _, error = self.request("POST", "/api/ai-studio/run", baseline, self.manager)
        self.assertEqual(status, 400)
        self.assertIn("图片路由", error["error"])
        status, _, route = self.request("POST", "/api/ai-studio/admin/route",
                                         {"business_model": "image", "primary_provider": "wanjie",
                                          "enabled": True, "model": "jimeng_t2i_v40"}, self.manager)
        self.assertEqual(status, 200)
        self.assertTrue(route["enabled"])
        status, _, overview = self.request("GET", "/api/ai-studio/overview", auth=self.manager)
        self.assertEqual(status, 200)
        self.assertEqual(overview["capabilities"]["image"]["status"], "CONFIGURED")
        for invalid in ({"count": 2}, {"width": 512}, {"budget_cap": 0.01}, {"asset_ids": ["anything"]}):
            status, _, _ = self.request("POST", "/api/ai-studio/run", baseline | invalid, self.manager)
            self.assertEqual(status, 400, invalid)
        status, _, result = self.request("POST", "/api/ai-studio/run",
                                          baseline | {"count": 1, "width": 2560, "height": 1440,
                                                      "budget_cap": 0.172}, self.manager)
        self.assertEqual(status, 201)
        self.assertEqual(result["artifact"]["status"], "queued")
        self.assertEqual(result["artifact"]["provider"], "wanjie")
        self.assertEqual(result["artifact"]["model"], "jimeng_t2i_v40")


if __name__ == "__main__":
    unittest.main()
