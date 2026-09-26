"""Lesson dependency, asset version and human release gates."""
from __future__ import annotations

import base64
import io
import sys
import tempfile
import unittest
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))

import asset_center
import lesson_pipeline
import martial
import martial_initialization
import martial_product
import store


class LessonPipelineTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        store.DATA = Path(self.temp.name)
        store.DB = store.DATA / "work_os.sqlite3"
        project = store.DATA / "project"
        project.mkdir()
        store.PROJECT_ROOTS["wuxiang"] = project
        store.SHARED_ROOT = store.DATA / "shared"
        store.SHARED_ROOT.mkdir()
        store.initialize()
        martial.initialize()
        martial_initialization.initialize_confirmed_import()
        martial_product.initialize_product_migration()
        asset_center.initialize()
        lesson_pipeline.initialize()
        employee_id = store.create_user("lesson-worker", "武术同事", "employee", "fixture-password")
        self.employee = {"id": employee_id, "role": "employee"}
        self.manager = {"id": "u_system", "role": "manager"}
        with store.connect() as c:
            c.execute("INSERT INTO martial_specialists(user_id,project_id,title,created_at) VALUES(?,?,?,?)",
                      (employee_id, "wuxiang", "武术", store.now()))
        self.move_id = "mv_flowing_cloud_01"

    def tearDown(self):
        self.temp.cleanup()

    def _registered_image(self, name, append=b""):
        data = (ROOT / "tests/fixtures/wuxiang/cryn-character.jpg").read_bytes() + append
        old = store.register_submission_asset("wuxiang", name, data, self.employee["id"])
        asset_center.sync_internal()
        with store.connect() as c:
            row = c.execute("SELECT asset_id FROM asset_registry_sources WHERE source_system='work_os' AND original_id=?", (old["id"],)).fetchone()
        return row[0]

    def test_dashboard_keeps_parallel_work_open(self):
        dashboard = lesson_pipeline.art_dashboard(self.employee, "flowing_cloud")
        self.assertGreater(dashboard["total"], 0)
        item = next(x for x in dashboard["lessons"] if x["move_id"] == self.move_id)
        self.assertIn("background", item["ready_work"])
        self.assertFalse(dashboard["counts"]["video"])
        self.assertIn("武术 / 运营", [x["owner"] for x in item["missing"]])

    def test_asset_versions_remain_traceable_and_cannot_release_without_qc(self):
        first = self._registered_image("background-v1.jpg")
        second = self._registered_image("background-v2.jpg", b"\0")
        lesson_pipeline.bind_asset(self.employee, self.move_id, {"role": "background", "asset_id": first})
        detail = lesson_pipeline.bind_asset(self.employee, self.move_id, {"role": "background", "asset_id": second})
        versions = [x for x in detail["bindings"] if x["role"] == "background"]
        self.assertEqual([(x["version"], x["status"]) for x in versions], [(2, "active"), (1, "superseded")])
        self.assertEqual(versions[0]["registry_asset_id"], second)
        self.assertIn(second, [x["asset_id"] for x in asset_center.list_assets(self.employee, {"lesson_id": detail["lesson"]["id"]})["assets"]])
        with self.assertRaises(PermissionError):
            lesson_pipeline.review(self.employee, self.move_id, {"stage": "audiovisual", "verdict": "pass",
                "notes": "连续观看并听审全片后确认", "evidence": ["review-v1"]})
        with self.assertRaisesRegex(ValueError, "动作专业验收"):
            lesson_pipeline.review(self.manager, self.move_id, {"stage": "audiovisual", "verdict": "pass",
                "notes": "连续观看并听审全片后确认", "evidence": ["review-v1"]})
        with self.assertRaisesRegex(ValueError, "不能批准"):
            lesson_pipeline.approve(self.manager, self.move_id)
        self.assertIsNone(lesson_pipeline.detail(self.employee, self.move_id)["lesson"]["approved_sha256"])

    def test_shot_versions_and_asset_association(self):
        shot = lesson_pipeline.save_shot(self.employee, self.move_id, {
            "ordinal": 1, "duration": 5, "purpose": "交代起势", "camera": "全身正面固定",
            "start_state": "双手腰间抱拳", "end_state": "左掌立掌"})["shots"][0]
        changed = lesson_pipeline.save_shot(self.employee, self.move_id, {
            "shot_id": shot["shot_id"], "ordinal": 1, "duration": 6, "purpose": "交代起势与重心",
            "camera": "全身正面固定", "start_state": "双手腰间抱拳", "end_state": "左掌立掌"})
        self.assertEqual(changed["shots"][0]["version"], 2)
        self.assertTrue(changed["dependencies"]["checks"]["shot_plan"])
        asset = self._registered_image("shot-background.jpg")
        result = lesson_pipeline.bind_asset(self.employee, self.move_id, {
            "role": "background", "shot_id": shot["shot_id"], "asset_id": asset})
        self.assertEqual(result["bindings"][0]["shot_id"], shot["shot_id"])
        found = asset_center.list_assets(self.employee, {"shot_id": shot["shot_id"]})["assets"]
        self.assertIn(asset, [item["asset_id"] for item in found])
        self.assertTrue(result["dependencies"]["checks"]["background"])

    def test_creative_lab_asset_requires_review_before_formal_binding(self):
        picture=(ROOT / "tests/fixtures/wuxiang/cryn-character.jpg").read_bytes()
        imported=asset_center.import_creative_lab(self.employee, {
            "project_id": "wuxiang", "experiment_id": "W01", "source_ref": "Creative Lab/Wushu/W01/sample.jpg",
            "upload": {"name": "sample.jpg", "base64": base64.b64encode(picture).decode()}})
        asset_id=imported["asset"]["asset_id"]
        with self.assertRaisesRegex(ValueError,"Learning Review"):
            lesson_pipeline.bind_asset(self.employee,self.move_id,{"role":"background","asset_id":asset_id})
        promoted=asset_center.promote_creative_lab(self.manager,asset_id,{
            "learning_review_ref":"Creative Lab/Batch_001_Learning_Review.md",
            "notes":"仅将此图片作为候选背景复核，动作准确性仍需另审"})
        self.assertEqual(promoted["asset"]["asset_id"],asset_id)
        self.assertTrue(any(ref.startswith("production_review:") for ref in store.parse(self._asset_refs(asset_id),[])))
        result=lesson_pipeline.bind_asset(self.employee,self.move_id,{"role":"background","asset_id":asset_id})
        self.assertTrue(result["dependencies"]["checks"]["background"])

    def _asset_refs(self, asset_id):
        with store.connect() as c:
            row=c.execute("SELECT a.source_refs FROM assets a JOIN asset_registry r ON r.original_id=a.id WHERE r.asset_id=?",(asset_id,)).fetchone()
            return row[0]

    def test_composition_snapshot_requires_real_output_and_invalidates_review_on_change(self):
        picture=(ROOT / "tests/fixtures/wuxiang/cryn-character.jpg").read_bytes()
        video=(ROOT / "tests/fixtures/wuxiang/cryn-bagua-part01.mp4").read_bytes()
        visual=store.register_production_asset("wuxiang","character","teacher.jpg",picture,self.employee["id"])
        with store.connect() as c:
            row=c.execute("SELECT payload FROM martial_master_versions WHERE master_id='wongkey' AND version=2").fetchone()
            payload=store.parse(row[0],{});payload["portrait"]=visual["id"]
            c.execute("UPDATE martial_master_versions SET payload=? WHERE master_id='wongkey' AND version=2",(store.dumps(payload),))
        human=store.register_production_asset("wuxiang","motion_reference","human.mp4",video,self.employee["id"])
        martial.link_motion(self.employee,self.move_id,{"asset_id":human["id"],"start_time":0,"end_time":15})
        with store.connect() as c:
            ref=c.execute("SELECT id FROM martial_motion_refs WHERE move_id=? ORDER BY version DESC LIMIT 1",(self.move_id,)).fetchone()[0]
        martial.confirm_motion(self.employee,ref)
        task=store.create_task({"project_id":"wuxiang","workflow_id":"WF-02","title":"教学合成测试","why":"核对素材版本锁定"},"u_system")
        generated=store.register_production_asset("wuxiang","candidate_video","generated.mp4",video,self.employee["id"])
        stamp=store.now()
        with store.connect() as c:
            c.execute("""INSERT INTO martial_packages(id,move_id,motion_ref_id,master_version,task_id,request_key,status,
              facts_hash,facts,result,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
              ("pkg_lesson_test",self.move_id,ref,2,task["id"],"lesson-pkg","complete","facts", "{}","{}",stamp,stamp))
            c.execute("""INSERT INTO martial_media_jobs(id,move_id,task_id,package_id,motion_ref_id,master_version,
              asset_type,provider,model_alias,model,prompt_hash,prompt,character_asset_id,duration,aspect_ratio,
              resolution,status,request_key,idempotency_key,candidate_asset_id,reserved_cost,quote_source,created_at,updated_at)
              VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
              ("mj_lesson_test",self.move_id,task["id"],"pkg_lesson_test",ref,2,"teaching","runy","sd2.5","model",
               "prompt-sha","prompt",visual["id"],15,"9:16","480p","succeeded","lesson-media","lesson-idempotent",
               generated["id"],0,"fixture",stamp,stamp))
            c.execute("""INSERT INTO martial_qc(id,media_job_id,move_id,stage,result,findings,reference_comparison,reviewer_id,created_at)
              VALUES(?,?,?,?,?,?,?,?,?)""",("qc_lesson_test","mj_lesson_test",self.move_id,"martial","pass",
                                    "逐项符合","已对照真人参考",self.employee["id"],stamp))
            c.execute("""INSERT INTO martial_final_assets(id,move_id,asset_type,media_job_id,asset_id,status,approved_by,created_at)
              VALUES(?,?,?,?,?,?,?,?)""",("mf_lesson_test",self.move_id,"teaching","mj_lesson_test",generated["id"],"active","u_system",stamp))
        shot_state=lesson_pipeline.save_shot(self.employee,self.move_id,{"ordinal":1,"duration":15,"purpose":"讲解并演示第一式",
            "camera":"全身正面固定","start_state":"抱拳准备","end_state":"收势并步"})
        shot_id=shot_state["shots"][0]["shot_id"]
        background=self._registered_image("background.jpg")
        lesson_pipeline.bind_asset(self.employee,self.move_id,{"role":"background","asset_id":background})
        sound=io.BytesIO()
        with wave.open(sound,"wb") as wav:
            wav.setnchannels(1);wav.setsampwidth(2);wav.setframerate(8000);wav.writeframes(b"\x00\x00"*800)
        voice=store.register_production_asset("wuxiang","instruction_voice","lesson.wav",sound.getvalue(),self.employee["id"])
        output=store.register_production_asset("wuxiang","lesson_output","complete.mp4",video,self.employee["id"])
        asset_center.sync_internal()
        with store.connect() as c:
            voice_reg=c.execute("SELECT asset_id FROM asset_registry_sources WHERE original_id=?",(voice["id"],)).fetchone()[0]
            output_reg=c.execute("SELECT asset_id FROM asset_registry_sources WHERE original_id=?",(output["id"],)).fetchone()[0]
        lesson_pipeline.bind_asset(self.employee,self.move_id,{"role":"instruction_voice","asset_id":voice_reg})
        lesson_pipeline.bind_asset(self.employee,self.move_id,{"role":"bgm","asset_id":voice_reg,"shot_id":shot_id})
        pending=lesson_pipeline.bind_asset(self.employee,self.move_id,{"role":"lesson_output","asset_id":output_reg})
        self.assertFalse(pending["dependencies"]["checks"]["video"])
        locked=lesson_pipeline.lock_composition(self.employee,self.move_id,{"notes":"使用当前真人动作、背景及教学语音完成整式合成"})
        self.assertTrue(locked["dependencies"]["checks"]["video"])
        reviewed=lesson_pipeline.review(self.manager,self.move_id,{"stage":"audiovisual","verdict":"pass",
            "notes":"已连续观看完整十五秒并听审教学语音及动作节拍", "evidence":["test-review"]})
        self.assertTrue(reviewed["dependencies"]["checks"]["audiovisual_review"])
        approved=lesson_pipeline.approve(self.manager,self.move_id)
        self.assertEqual(approved["status"],"approved")
        with store.connect() as c:
            manifest=store.parse(c.execute("SELECT approved_manifest FROM martial_lesson_packages WHERE move_id=?",(self.move_id,)).fetchone()[0],{})
        self.assertNotIn("bgm",manifest["placeholders"])
        changed=self._registered_image("background-v2.jpg",b"\0")
        after=lesson_pipeline.bind_asset(self.employee,self.move_id,{"role":"background","asset_id":changed})
        self.assertFalse(after["dependencies"]["checks"]["composition"])
        self.assertFalse(after["dependencies"]["checks"]["audiovisual_review"])
        self.assertIsNone(after["lesson"]["approved_sha256"])


if __name__ == "__main__":
    unittest.main()
