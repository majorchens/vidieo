"""A long locked reference cannot silently become a paid five-second replica."""
from __future__ import annotations

import base64
import os
import shutil
import struct
import sys
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"app"))

import martial
import martial_connector
import martial_initialization
import martial_multimodal_assets
import asset_center
import lesson_pipeline
import server
import store


class ReferenceReproductionTest(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        store.DATA=Path(self.temp.name);store.DB=store.DATA/"work_os.sqlite3"
        root=store.DATA/"project";root.mkdir()
        store.PROJECT_ROOTS["wuxiang"]=root
        store.SHARED_ROOT=store.DATA/"shared";store.SHARED_ROOT.mkdir()
        store.initialize();martial.initialize();martial_multimodal_assets.initialize();asset_center.initialize()
        employee=store.create_user("xia","夏润麒","employee","fixture-password")
        manager=store.create_user("manager","负责人","manager","fixture-password")
        self.employee={"id":employee,"role":"employee"}
        self.manager={"id":manager,"role":"manager"}
        with store.connect() as c:
            c.execute("INSERT INTO martial_specialists(user_id,project_id,title,created_at) VALUES(?,?,?,?)",
                      (employee,"wuxiang","武术数字资产内容设计",store.now()))
        os.environ["YOODUN_CONNECTOR_TOKEN"]="test-signing-token"
        martial_initialization.initialize_confirmed_import()
        martial.route_report([{"model_alias":"sd2.5","provider":"runy","model":"doubao-seedance-2-5"}])
        image=(ROOT/"tests/fixtures"/"wuxiang"/"cryn-character.jpg").read_bytes()
        martial_multimodal_assets.upload_art_background(self.manager,"beginner",
            {"upload":self.upload("training-ground.jpg",image+b"\x01")})
        martial.upload_master_asset(self.employee,"pongda",{"field":"portrait","upload":self.upload("teacher.jpg",image)})
        martial.approve_master(self.manager,"pongda")
        video=(ROOT/"tests/fixtures"/"wuxiang"/"cryn-bagua-part01.mp4").read_bytes()
        self.move_id="mv_beginner_02"
        self.ref=martial.upload_motion(self.employee,self.move_id,{"upload":self.upload("horse-stance.mp4",video),
            "start_time":0,"end_time":15,"orientation":"正面","notes":"示范后跟练"})["motions"][0]
        martial.confirm_motion(self.employee,self.ref["id"])
        with self.assertRaisesRegex(ValueError,"分别保存讲解演示和跟教练跟练"):
            martial.request_package(self.employee,self.move_id)
        martial.save_video_plan(self.employee,self.move_id,{"asset_type":"teaching","source_start":0,
            "source_end":15,"target_duration":19,"brief":"讲解演示，一片独立制作"})
        martial.save_video_plan(self.employee,self.move_id,{"asset_type":"practice","source_start":0,
            "source_end":15,"target_duration":45,"brief":"跟教练跟练，独立于讲解片"})

    def tearDown(self):
        os.environ.pop("YOODUN_CONNECTOR_TOKEN",None)
        self.temp.cleanup()

    @staticmethod
    def upload(name: str, body: bytes) -> dict:
        return {"name":name,"base64":base64.b64encode(body).decode()}

    @staticmethod
    def body(prompt: str) -> dict:
        return {"move_summary":"马步按原片示范与跟练","motion_breakdown":["示范","跟练"],
                "character_constraints":[],"shot_camera_plan":[],"seedance_prompt":prompt,
                "teaching_prompt":prompt,"practice_prompt":"独立跟练："+prompt,
                "negative_constraints":[],"reference_mapping":[],"qc_checklist":[],
                "missing_inputs":[],"facts":{},"ai_suggestions":[]}

    def test_legacy_auto_budget_is_removed_without_overriding_manager_choice(self):
        package=martial.request_package(self.employee,self.move_id)
        tid=martial.package_report(package["id"],{"status":"complete","body":self.body("按参考动作")})["task_id"]
        marker="martial_default_unlimited_budget_20260928"
        with store.connect() as c:
            c.execute("UPDATE tasks SET budget_cap=3.6 WHERE id=?",(tid,))
            c.execute("DELETE FROM martial_budget_policies WHERE task_id=?",(tid,))
            c.execute("DELETE FROM martial_one_time_changes WHERE name=?",(marker,))
        martial.initialize()
        with store.connect() as c:
            self.assertEqual(store.record(c,"tasks",tid)["budget_cap"],0)
            self.assertTrue(martial._budget_unlimited(c,tid))
        martial.update_budget(self.manager,self.move_id,3.6)
        with store.connect() as c:
            c.execute("DELETE FROM martial_one_time_changes WHERE name=?",(marker,))
        martial.initialize()
        with store.connect() as c:
            self.assertEqual(store.record(c,"tasks",tid)["budget_cap"],3.6)
            self.assertFalse(martial._budget_unlimited(c,tid))

    def test_formal_generation_uses_pinned_art_background_and_cloud_motion_prompt(self):
        martial.save_video_plan(self.employee,self.move_id,{"asset_type":"teaching",
            "source_start":0,"source_end":15,"target_duration":15,"brief":"与真人原片等长"})
        package=martial.request_package(self.employee,self.move_id)
        ready=martial.package_report(package["id"],{"status":"complete","body":self.body("按真人动作示范")})
        store.start(ready["task_id"],self.employee)
        martial.update_budget(self.manager,self.move_id,"unlimited")
        priced=martial.quote(self.employee,self.move_id,"sd2.5",1,"complete","teaching")
        self.assertEqual((priced["background_name"],priced["background_version"]),("training-ground.jpg",1))
        correction="地板保持图片 2 的平整地面，不变山峰；亮度与跟练一致；手脚动作按真人参考"
        job=martial.create_media(self.employee,self.move_id,{"generation_mode":"complete",
            "asset_type":"teaching","model":"sd2.5","prompt_adjustment":correction})[0]
        with store.connect() as c:
            saved=c.execute("SELECT prompt,prompt_adjustment,background_ref_json FROM martial_media_jobs WHERE id=?",(job["id"],)).fetchone()
        locked=store.parse(saved["background_ref_json"],{})
        self.assertIn("图片 2 是本功法练功背景 V1",saved["prompt"])
        self.assertIn("流云或薄雾在远景缓慢",saved["prompt"])
        self.assertIn("地板、建筑和树木保持稳定",saved["prompt"])
        self.assertIn("不得把练功地板变成山峰",saved["prompt"])
        self.assertIn(correction,saved["prompt"])
        self.assertEqual(saved["prompt_adjustment"],correction)
        detail=martial.move_detail(self.move_id,self.employee)
        self.assertIn(correction,next(item for item in detail["media"] if item["id"]==job["id"])["prompt"])
        image=(ROOT/"tests/fixtures"/"wuxiang"/"cryn-character.jpg").read_bytes()
        newer=martial_multimodal_assets.upload_art_background(self.manager,"beginner",
            {"upload":self.upload("training-ground-v2.jpg",image+b"\x03")})["assets"]["background"]
        self.assertEqual(newer["version"],2)
        claimed=martial.media_claim(job["id"])
        self.assertEqual(claimed["background_ref"],locked)
        self.assertEqual(len(claimed["image_urls"]),2)
        self.assertIn(correction,claimed["segments"][0]["prompt"])
        self.assertEqual(len(martial_connector._media_segments(claimed)),1)
        self.assertIn(locked["asset_id"],claimed["image_urls"][1])
        self.assertNotIn(newer["asset_id"],claimed["image_urls"][1])

    def test_missing_art_background_blocks_new_formal_job(self):
        martial_multimodal_assets.set_art_asset(self.manager,"beginner",{"role":"background","asset_id":None})
        package=martial.request_package(self.employee,self.move_id)
        ready=martial.package_report(package["id"],{"status":"complete","body":self.body("按真人动作示范")})
        store.start(ready["task_id"],self.employee)
        result=martial.quote(self.employee,self.move_id,"sd2.5",1,"complete","teaching")
        self.assertTrue(result["blocked"])
        self.assertIn("上传本功法练功背景",result["block_reason"])
        with self.assertRaisesRegex(ValueError,"练功背景"):
            martial.create_media(self.employee,self.move_id,{"generation_mode":"complete",
                "asset_type":"teaching","model":"sd2.5"})
        with store.connect() as c:
            self.assertEqual(c.execute("SELECT COUNT(*) FROM martial_media_jobs").fetchone()[0],0)

    def test_separate_teaching_and_practice_uploads_reach_ai_preparation(self):
        original_id=self.ref["id"]
        video=(ROOT/"tests/fixtures"/"wuxiang"/"cryn-bagua-part01.mp4").read_bytes()
        second=martial.upload_motion(self.employee,self.move_id,{"upload":self.upload("practice.mp4",video),
            "start_time":0,"end_time":12,"orientation":"正面","notes":"独立跟练参考"})["motions"][0]
        martial.confirm_motion(self.employee,second["id"])
        detail=martial.move_detail(self.move_id,self.employee)
        self.assertEqual(detail["video_plans"]["teaching"]["motion_ref_id"],original_id)
        self.assertEqual(detail["video_plans"]["practice"]["motion_ref_id"],original_id)
        detail=martial.save_video_plan(self.employee,self.move_id,{"asset_type":"practice",
            "motion_ref_id":second["id"],"source_start":0,"source_end":12,
            "target_duration":12,"brief":"跟练使用第二条真人视频"})
        self.assertEqual(detail["video_plans"]["teaching"]["motion_ref_id"],original_id)
        self.assertEqual(detail["video_plans"]["practice"]["motion_ref_id"],second["id"])
        martial.save_video_plan(self.employee,self.move_id,{"asset_type":"teaching",
            "motion_ref_id":original_id,"source_start":0,"source_end":15,
            "target_duration":15,"brief":"讲解使用第一条真人视频"})
        package=martial.request_package(self.employee,self.move_id)
        self.assertEqual(martial.store.parse(package["facts"],{})["motions"]["teaching"]["id"],original_id)
        self.assertEqual(martial.store.parse(package["facts"],{})["motions"]["practice"]["id"],second["id"])
        ready=martial.package_report(package["id"],{"status":"complete","body":self.body("分别参照真人动作")})
        self.assertTrue(ready["task_id"])
        self.assertTrue(martial.move_detail(self.move_id,self.employee)["package_current"])
        store.start(ready["task_id"],self.employee)
        teaching=martial.quote(self.employee,self.move_id,"sd2.5",1,"complete","teaching")
        practice=martial.quote(self.employee,self.move_id,"sd2.5",1,"complete","practice")
        self.assertEqual((teaching["source_end"],practice["source_end"]),(15,12))
        martial.update_budget(self.manager,self.move_id,"unlimited")
        jobs={kind:martial.create_media(self.employee,self.move_id,{"generation_mode":"complete",
            "asset_type":kind,"model":"sd2.5"})[0]["id"] for kind in ("teaching","practice")}
        with store.connect() as c:
            refs={kind:c.execute("SELECT motion_ref_id FROM martial_media_jobs WHERE id=?",(job_id,)).fetchone()[0]
                  for kind,job_id in jobs.items()}
        self.assertEqual(refs,{"teaching":original_id,"practice":second["id"]})

    def test_reproduce_is_blocked_before_any_paid_job_and_preview_remains_explicit(self):
        package=martial.request_package(self.employee,self.move_id,{"production_brief":"保留原片示范→跟练结构"})
        report=martial.package_report(package["id"],{"status":"complete","body":self.body("按原片结构作为动作参考")})
        store.start(report["task_id"],self.employee)
        full=martial.quote(self.employee,self.move_id,"sd2.5",1,"reproduce")
        self.assertTrue(full["blocked"])
        self.assertIsNone(full["estimated_cost"])
        self.assertEqual((full["reference_duration"],full["selected_start"],full["selected_end"]),(15,0,15))
        self.assertIn("目标时长",full["block_reason"])
        practice=martial.quote(self.employee,self.move_id,"sd2.5",1,"reproduce","practice")
        self.assertEqual((practice["target_duration"],practice["source_start"],practice["source_end"]),(45,0,15))
        self.assertEqual(practice["duration"],45)
        with self.assertRaisesRegex(ValueError,"暂不可发起付费生成"):
            martial.create_media(self.employee,self.move_id,{"generation_mode":"reproduce","asset_type":"teaching","model":"sd2.5"})
        with self.assertRaisesRegex(ValueError,"明确选择"):
            martial.create_media(self.employee,self.move_id,{"asset_type":"teaching","model":"sd2.5"})
        with store.connect() as c:
            self.assertEqual(c.execute("SELECT COUNT(*) FROM martial_media_jobs").fetchone()[0],0)
        preview=martial.quote(self.employee,self.move_id,"sd2.5",1,"preview")
        self.assertEqual((preview["duration"],preview["estimated_cost"]),(5,3.6))
        self.assertFalse(preview["blocked"])
        job=martial.create_media(self.employee,self.move_id,{"generation_mode":"preview","asset_type":"teaching","model":"sd2.5"})[0]
        self.assertEqual((job["generation_mode"],job["duration"]),("preview",5))
        with store.connect() as c:
            prompt=c.execute("SELECT prompt FROM martial_media_jobs WHERE id=?",(job["id"],)).fetchone()[0]
        self.assertIn("只生成 5 秒短片预览",prompt)
        self.assertIn("视频 1 是唯一的动作与时序参考",prompt)
        self.assertNotIn("保留原片示范→跟练结构",prompt)
        self.assertIn("讲解演示",prompt)

    def test_preview_blocks_when_whole_reference_exceeds_supplier_input_limit(self):
        # Simulate a previously probed 31-second source without sending media.
        with store.connect() as c:
            c.execute("UPDATE martial_motion_refs SET duration=31,end_time=31 WHERE id=?",(self.ref["id"],))
            c.execute("UPDATE martial_video_plans SET source_end=31 WHERE move_id=?",(self.move_id,))
        pkg=martial.request_package(self.employee,self.move_id)
        ready=martial.package_report(pkg["id"],{"status":"complete","body":self.body("只做试拍")})
        store.start(ready["task_id"],self.employee)
        quote=martial.quote(self.employee,self.move_id,"sd2.5",1,"preview")
        self.assertTrue(quote["blocked"])
        self.assertIn("31.00 秒",quote["block_reason"])
        self.assertIn("最多 30 秒",quote["block_reason"])
        self.assertIn("发送整段原片",quote["block_reason"])
        with self.assertRaisesRegex(ValueError,"超过润元 Seedance 视频参考输入"):
            martial.create_media(self.employee,self.move_id,
                                 {"generation_mode":"preview","asset_type":"teaching","model":"sd2.5"})
        with store.connect() as c:
            self.assertEqual(c.execute("SELECT COUNT(*) FROM martial_media_jobs").fetchone()[0],0)

    def test_preview_blocks_when_plan_selects_only_part_of_source(self):
        martial.save_video_plan(self.employee,self.move_id,{"asset_type":"practice",
            "source_start":8,"source_end":14,"target_duration":45,"brief":"仅选跟练片段"})
        pkg=martial.request_package(self.employee,self.move_id)
        ready=martial.package_report(pkg["id"],{"status":"complete","body":self.body("只做试拍")})
        store.start(ready["task_id"],self.employee)
        quote=martial.quote(self.employee,self.move_id,"sd2.5",1,"preview","practice")
        self.assertTrue(quote["blocked"])
        self.assertIn("8.00–14.00 秒",quote["block_reason"])
        self.assertIn("尚未按所选片段裁剪",quote["block_reason"])
        with self.assertRaisesRegex(ValueError,"尚未按所选片段裁剪"):
            martial.create_media(self.employee,self.move_id,
                                 {"generation_mode":"preview","asset_type":"practice","model":"sd2.5"})
        with store.connect() as c:
            self.assertEqual(c.execute("SELECT COUNT(*) FROM martial_media_jobs").fetchone()[0],0)

    def test_complete_generation_blocks_unsafe_auto_stitch_and_keeps_single_clip(self):
        with store.connect() as c:
            c.execute("UPDATE martial_motion_refs SET duration=46.7,end_time=46.7,width=2160,height=3840 WHERE id=?",(self.ref["id"],))
        martial.save_video_plan(self.employee,self.move_id,{"asset_type":"teaching",
            "source_start":0,"source_end":33,"target_duration":33,"brief":"讲解演示"})
        martial.save_video_plan(self.employee,self.move_id,{"asset_type":"practice",
            "source_start":33,"source_end":46.7,"target_duration":13.7,"brief":"跟教练跟练"})
        pkg=martial.request_package(self.employee,self.move_id)
        ready=martial.package_report(pkg["id"],{"status":"complete","body":self.body("逐段跟随参考")})
        store.start(ready["task_id"],self.employee)
        teaching=martial.quote(self.employee,self.move_id,"sd2.5",1,"complete","teaching")
        practice=martial.quote(self.employee,self.move_id,"sd2.5",1,"complete","practice")
        self.assertTrue(teaching["blocked"])
        self.assertIn("自动分段拼接",teaching["block_reason"])
        self.assertEqual(teaching["segments"],[])
        self.assertEqual([(s["source_start"],s["source_end"],s["duration"])
                          for s in practice["segments"]],[(33,46.7,14)])
        self.assertEqual((teaching["estimated_cost"],practice["estimated_cost"]),(None,19))
        self.assertEqual((teaching["aspect_ratio"],practice["aspect_ratio"]),("9:16","9:16"))
        self.assertTrue(teaching["blocked"])
        self.assertFalse(practice["blocked"])
        self.assertTrue(practice["budget_unlimited"])
        martial.update_budget(self.manager,self.move_id,3.6)
        fixed=martial.quote(self.employee,self.move_id,"sd2.5",1,"complete","practice")
        self.assertTrue(fixed["blocked"])
        self.assertIn("预算不足",fixed["block_reason"])
        martial.update_budget(self.manager,self.move_id,"unlimited")
        unbounded=martial.quote(self.employee,self.move_id,"sd2.5",1,"complete","teaching")
        self.assertTrue(unbounded["blocked"])
        self.assertTrue(unbounded["budget_unlimited"])
        self.assertIsNone(unbounded["remaining_budget"])
        with self.assertRaisesRegex(ValueError,"自动分段拼接"):
            martial.create_media(self.employee,self.move_id,{"generation_mode":"complete",
                "asset_type":"teaching","model":"sd2.5"})
        job=martial.create_media(self.employee,self.move_id,{"generation_mode":"complete",
            "asset_type":"practice","model":"sd2.5"})[0]
        claim=martial.media_claim(job["id"])
        self.assertEqual((job["duration"],job["reserved_cost"]),(14,19))
        self.assertEqual(claim["video_urls"],[])
        self.assertTrue(claim["reference_source_url"].startswith("https://"))
        self.assertEqual(len(claim["segments"]),1)
        self.assertIn("视频 1 是唯一的动作与时序参考",claim["segments"][0]["prompt"])
        self.assertIn("本地 0 秒即原片 33.000 秒",claim["segments"][0]["prompt"])
        self.assertNotIn("合成后全片时长",claim["segments"][0]["prompt"])
        self.assertEqual(martial.move_detail(self.move_id,self.employee)["media"][0]["segments"][0]["duration"],14)

    def test_video_edit_trial_uses_two_versioned_art_refs_and_cannot_be_finalized(self):
        asset_center.initialize();lesson_pipeline.initialize()
        image=(ROOT/"tests/fixtures/wuxiang/cryn-character.jpg").read_bytes()
        for role in ("pilot_scene","pilot_background"):
            lesson_pipeline.upload_asset(self.employee,self.move_id,{"role":role,
                "upload":self.upload(role+".jpg",image)})
        martial.save_video_plan(self.employee,self.move_id,{"asset_type":"teaching","source_start":0,
            "source_end":15,"target_duration":15,"brief":"真人动作是唯一时序"})
        pkg=martial.request_package(self.employee,self.move_id)
        ready=martial.package_report(pkg["id"],{"status":"complete","body":self.body("按原片动作试拍")})
        store.start(ready["task_id"],self.employee)
        martial.update_budget(self.manager,self.move_id,"unlimited")
        quote=martial.quote(self.employee,self.move_id,"sd2.5",1,"edit_trial","teaching")
        self.assertFalse(quote["blocked"])
        self.assertEqual([(s["source_start"],s["source_end"]) for s in quote["segments"]],[(0,15)])
        job=martial.create_media(self.employee,self.move_id,{"generation_mode":"edit_trial",
            "asset_type":"teaching","model":"sd2.5"})[0]
        claim=martial.media_claim(job["id"])
        self.assertEqual((len(claim["image_urls"]),len(claim["segments"])),(2,1))
        self.assertTrue(claim["segments"][0]["video_edit"])
        with store.connect() as c:
            self.assertEqual(lesson_pipeline._check(c,lesson_pipeline._ensure(c,self.move_id))["checks"]["background"],False)
            self.assertIsNotNone(martial._current_final_error(c,martial._row(c,"martial_media_jobs",job["id"])))

    def test_video_edit_trial_requires_manual_natural_cut_for_long_motion(self):
        asset_center.initialize();lesson_pipeline.initialize()
        with store.connect() as c:
            c.execute("UPDATE martial_motion_refs SET duration=46.7,end_time=46.7 WHERE id=?",(self.ref["id"],))
        martial.save_video_plan(self.employee,self.move_id,{"asset_type":"teaching",
            "source_start":0,"source_end":33,"target_duration":33,"brief":"讲解试拍"})
        pkg=martial.request_package(self.employee,self.move_id)
        ready=martial.package_report(pkg["id"],{"status":"complete","body":self.body("按原片动作试拍")})
        store.start(ready["task_id"],self.employee)
        martial.update_budget(self.manager,self.move_id,"unlimited")
        no_cut=martial.quote(self.employee,self.move_id,"sd2.5",1,"edit_trial","teaching")
        self.assertTrue(no_cut["blocked"])
        self.assertIn("自然切点",no_cut["block_reason"])
        with_cut=martial.quote(self.employee,self.move_id,"sd2.5",1,"edit_trial","teaching","12")
        self.assertEqual([(s["source_start"],s["source_end"]) for s in with_cut["segments"]],[(0,12),(12,33)])

    def test_reviewed_trial_notes_require_exact_motion_hash_and_cut(self):
        sha="2bb081765e8fe0bb3d0761ed390f4538adfda3ac1ba76e5512829910e5463115"
        chosen=martial._edit_trial_profile("mv_flowing_cloud_01",sha,"teaching","12")
        self.assertEqual(chosen["id"],"liuyun-palm-01-20260928")
        self.assertEqual(len(chosen["notes"]),2)
        self.assertIsNone(martial._edit_trial_profile("mv_flowing_cloud_01",sha,"teaching","20"))
        self.assertIsNone(martial._edit_trial_profile("mv_flowing_cloud_01","0"*64,"teaching","12"))

    def test_reference_clip_is_immutable_and_signed(self):
        martial.save_video_plan(self.employee,self.move_id,{"asset_type":"teaching",
            "source_start":0,"source_end":15,"target_duration":15,"brief":"完整讲解"})
        pkg=martial.request_package(self.employee,self.move_id)
        ready=martial.package_report(pkg["id"],{"status":"complete","body":self.body("完整讲解")})
        store.start(ready["task_id"],self.employee)
        martial.update_budget(self.manager,self.move_id,100)
        job=martial.create_media(self.employee,self.move_id,{"generation_mode":"complete",
            "asset_type":"teaching","model":"sd2.5"})[0]
        clip=ROOT/"tests/fixtures"/"wuxiang"/"cryn-bagua-part01.mp4"
        sha=store.digest_file(clip)
        saved=martial.stage_reference_clip(job["id"],0,clip,sha)
        self.assertEqual(saved["sha256"],sha)
        self.assertAlmostEqual(saved["duration"],15,delta=.35)
        from urllib.parse import parse_qs,urlparse
        parsed=urlparse(saved["video_url"]);query=parse_qs(parsed.query)
        self.assertEqual(martial.clip_source_path(job["id"],0,query["exp"][0],query["sig"][0]).read_bytes(),clip.read_bytes())
        with self.assertRaises(PermissionError):
            martial.clip_source_path(job["id"],0,query["exp"][0],"wrong")

    def test_complete_candidate_can_enter_employee_selection_and_qc(self):
        martial.save_video_plan(self.employee,self.move_id,{"asset_type":"teaching",
            "source_start":0,"source_end":15,"target_duration":15,"brief":"完整讲解"})
        pkg=martial.request_package(self.employee,self.move_id)
        ready=martial.package_report(pkg["id"],{"status":"complete","body":self.body("完整讲解")})
        store.start(ready["task_id"],self.employee)
        martial.update_budget(self.manager,self.move_id,"unlimited")
        job=martial.create_media(self.employee,self.move_id,{"generation_mode":"complete",
            "asset_type":"teaching","model":"sd2.5"})[0]
        martial.media_claim(job["id"])
        clip=ROOT/"tests/fixtures"/"wuxiang"/"cryn-bagua-part01.mp4"
        report=martial.media_report(job["id"],{"status":"succeeded","video_base64":self.upload("candidate.mp4",clip.read_bytes())["base64"],
            "technical":{"result":"pass","notes":"容器和首帧均已核验"}})
        self.assertEqual(report["status"],"succeeded")
        self.assertTrue(martial.select_candidate(self.employee,job["id"],"对照真人动作完整片段后选择候选")["selected"])
        checks={key:"pass" for key in martial.QC_CHECKS}
        result=martial.martial_qc(self.employee,job["id"],{"verdict":"pass","checks":checks,
            "findings":"动作标准符合参考","reference_comparison":"完整对照真人参考，顺序一致"})
        self.assertEqual(result["result"],"pass")
        with store.connect() as c:
            current=martial._current_final_error(c,martial._row(c,"martial_media_jobs",job["id"]))
        self.assertIsNone(current)

    def test_replacing_teaching_source_keeps_practice_final_current(self):
        for kind in ("teaching","practice"):
            martial.save_video_plan(self.employee,self.move_id,{"asset_type":kind,
                "motion_ref_id":self.ref["id"],"source_start":0,"source_end":15,
                "target_duration":15,"brief":kind})
        original=martial.request_package(self.employee,self.move_id)
        ready=martial.package_report(original["id"],{"status":"complete","body":self.body("两条独立制作")})
        store.start(ready["task_id"],self.employee)
        teaching=martial.create_media(self.employee,self.move_id,{"generation_mode":"complete",
            "asset_type":"teaching","model":"sd2.5"})[0]
        practice=martial.create_media(self.employee,self.move_id,{"generation_mode":"complete",
            "asset_type":"practice","model":"sd2.5"})[0]
        video=(ROOT/"tests/fixtures"/"wuxiang"/"cryn-bagua-part01.mp4").read_bytes()
        final_asset=store.register_submission_asset("wuxiang","practice-final.mp4",video,self.employee["id"])
        with store.connect() as c:
            c.execute("UPDATE martial_media_jobs SET status='succeeded',candidate_asset_id=?,technical_report=? WHERE id=?",
                (final_asset["id"],store.dumps({"result":"pass","server_probe":{"duration":15}}),practice["id"]))
            c.execute("INSERT INTO martial_final_assets(id,move_id,asset_type,media_job_id,asset_id,status,approved_by,created_at) VALUES(?,?,?,?,?,?,?,?)",
                ("mf_practice_test",self.move_id,"practice",practice["id"],final_asset["id"],"active",self.manager["id"],store.now()))
        newer=martial.upload_motion(self.employee,self.move_id,{"upload":self.upload("teaching-new.mp4",video+b"\x00"),
            "start_time":0,"end_time":15,"orientation":"正面"})["motions"][0]
        martial.confirm_motion(self.employee,newer["id"])
        martial.save_video_plan(self.employee,self.move_id,{"asset_type":"teaching",
            "motion_ref_id":newer["id"],"source_start":0,"source_end":15,
            "target_duration":15,"brief":"新版讲解真人动作"})
        updated=martial.request_package(self.employee,self.move_id)
        martial.package_report(updated["id"],{"status":"complete","body":self.body("新版讲解")})
        detail=martial.move_detail(self.move_id,self.employee)
        self.assertIn("practice",detail["final_types"])
        self.assertNotIn("teaching",detail["final_types"])
        self.assertTrue(next(job for job in detail["media"] if job["id"]==practice["id"])["current_context"])
        self.assertFalse(next(job for job in detail["media"] if job["id"]==teaching["id"])["current_context"])

    def test_accepted_task_can_start_a_new_complete_video_version(self):
        martial.save_video_plan(self.employee,self.move_id,{"asset_type":"teaching",
            "motion_ref_id":self.ref["id"],"source_start":0,"source_end":15,
            "target_duration":15,"brief":"讲解完整视频"})
        package=martial.request_package(self.employee,self.move_id)
        tid=martial.package_report(package["id"],{"status":"complete","body":self.body("按真人动作")})["task_id"]
        store.start(tid,self.employee)
        with store.connect() as c:
            store.update_task_status(c,tid,{"in_progress"},"accepted",self.manager["id"])
        job=martial.create_media(self.employee,self.move_id,{"generation_mode":"complete",
            "asset_type":"teaching","model":"sd2.5","prompt_adjustment":"保持练功地面和亮度"})[0]
        with store.connect() as c:
            self.assertEqual(store.record(c,"tasks",tid)["status"],"in_progress")
            self.assertEqual(c.execute("SELECT prompt_adjustment FROM martial_media_jobs WHERE id=?",(job["id"],)).fetchone()[0],"保持练功地面和亮度")

    def test_new_brief_invalidates_cache_and_pending_package_blocks_old_prompt(self):
        old=martial.request_package(self.employee,self.move_id)
        self.assertEqual(old["id"],martial.request_package(self.employee,self.move_id)["id"])
        old_report=martial.package_report(old["id"],{"status":"complete","body":self.body("旧提示词")})
        store.start(old_report["task_id"],self.employee)
        brief="示范后跟练；切换秒点尚未标注，不要自行编造"
        revised=martial.request_package(self.employee,self.move_id,{"production_brief":brief})
        self.assertNotEqual(revised["id"],old["id"])
        self.assertEqual(revised["id"],martial.request_package(self.employee,self.move_id,{"production_brief":brief,"force":True})["id"])
        self.assertEqual(store.parse(revised["facts"],{})["production_brief"],brief)
        self.assertTrue(martial.quote(self.employee,self.move_id,"sd2.5",1,"preview")["blocked"])
        with self.assertRaisesRegex(ValueError,"最新 AI 准备尚未完成"):
            martial.create_media(self.employee,self.move_id,{"generation_mode":"preview","asset_type":"teaching","model":"sd2.5"})
        martial.package_report(revised["id"],{"status":"complete","body":self.body("新提示词：遵照示范跟练")})
        forced=martial.request_package(self.employee,self.move_id,{"production_brief":brief,"force":True})
        self.assertNotEqual(forced["id"],revised["id"])
        self.assertEqual(forced["id"],martial.request_package(self.employee,self.move_id,{"production_brief":brief,"force":True})["id"])
        with self.assertRaisesRegex(ValueError,"最新 AI 准备尚未完成"):
            martial.create_media(self.employee,self.move_id,{"generation_mode":"preview","asset_type":"teaching","model":"sd2.5"})
        martial.package_report(forced["id"],{"status":"complete","body":self.body("最终提示词：按阶段")})
        with store.connect() as c:
            prepared=store.parse(store.record(c,"tasks",old_report["task_id"])["ai_prepared"],{})
        self.assertEqual(prepared["generation_prompt"],"最终提示词：按阶段")

    def test_versioned_plan_change_invalidates_ai_package_and_does_not_queue_paid_media(self):
        first=martial.request_package(self.employee,self.move_id)
        reported=martial.package_report(first["id"],{"status":"complete","body":self.body("讲解原片")})
        self.assertTrue(reported["task_id"])
        self.assertTrue(martial.move_detail(self.move_id,self.employee)["package_current"])
        changed=martial.save_video_plan(self.manager,self.move_id,{"asset_type":"practice",
            "source_start":0,"source_end":15,"target_duration":65,"brief":"延长跟教练跟练节奏"})
        self.assertEqual(changed["video_plans"]["practice"]["version"],2)
        self.assertFalse(changed["package_current"])
        with store.connect() as c:
            rows=c.execute("SELECT version,status,target_duration FROM martial_video_plans WHERE move_id=? AND asset_type='practice' ORDER BY version",(self.move_id,)).fetchall()
            self.assertEqual([(r["version"],r["status"],r["target_duration"]) for r in rows],
                             [(1,"superseded",45),(2,"active",65)])
        with self.assertRaisesRegex(ValueError,"AI 准备尚未完成"):
            martial.create_media(self.employee,self.move_id,{"generation_mode":"preview","asset_type":"practice","model":"sd2.5"})
        with store.connect() as c:self.assertEqual(c.execute("SELECT COUNT(*) FROM martial_media_jobs").fetchone()[0],0)

    def test_manager_may_refresh_ai_for_existing_locked_task_only(self):
        with self.assertRaisesRegex(PermissionError,"首次 AI 准备"):
            martial.request_package(self.manager,self.move_id)
        original=martial.request_package(self.employee,self.move_id)
        ready=martial.package_report(original["id"],{"status":"complete","body":self.body("原方案")})
        martial.save_video_plan(self.manager,self.move_id,{"asset_type":"practice",
            "source_start":0,"source_end":15,"target_duration":13.7,"brief":"跟练按原片片段"})
        refreshed=martial.request_package(self.manager,self.move_id,{"production_brief":"保持动作顺序"})
        self.assertEqual(refreshed["requested_by"],self.manager["id"])
        result=martial.package_report(refreshed["id"],{"status":"complete","body":self.body("新版方案")})
        self.assertEqual(result["task_id"],ready["task_id"])

    def test_latest_current_track_attempt_controls_revision_flag(self):
        pkg=martial.request_package(self.employee,self.move_id)
        ready=martial.package_report(pkg["id"],{"status":"complete","body":self.body("讲解动作")})
        store.start(ready["task_id"],self.employee)
        martial.update_budget(self.manager,self.move_id,8)
        first=martial.create_media(self.employee,self.move_id,{"generation_mode":"preview","asset_type":"teaching","model":"sd2.5"})[0]
        martial.media_claim(first["id"])
        video=(ROOT/"tests/fixtures"/"wuxiang"/"cryn-bagua-part01.mp4").read_bytes()
        martial.media_report(first["id"],{"status":"succeeded","video_base64":self.upload("candidate.mp4",video)["base64"],
            "technical":{"result":"pass","notes":"container"}})
        martial.select_candidate(self.employee,first["id"],"逐段核对真人动作视频")
        checks={key:"pass" for key in martial.QC_CHECKS};checks["hand_path"]="fail"
        martial.martial_qc(self.employee,first["id"],{"verdict":"fail","checks":checks,
            "findings":"手部路径需要返修","reference_comparison":"真人参考手部路线不同"})
        summary=next(row for row in martial.overview(self.employee)["moves"] if row["id"]==self.move_id)
        self.assertTrue(summary["unresolved_revision"])
        self.assertEqual(summary["qc_failed"],1)
        martial.create_media(self.employee,self.move_id,{"generation_mode":"preview","asset_type":"teaching",
            "model":"sd2.5","revision_of":first["id"]})
        summary=next(row for row in martial.overview(self.employee)["moves"] if row["id"]==self.move_id)
        self.assertFalse(summary["unresolved_revision"])
        self.assertEqual(summary["qc_failed"],0)

    def test_large_candidate_streams_from_connector_and_reports_once(self):
        pkg=martial.request_package(self.employee,self.move_id)
        ready=martial.package_report(pkg["id"],{"status":"complete","body":self.body("讲解动作")})
        store.start(ready["task_id"],self.employee)
        job=martial.create_media(self.employee,self.move_id,
                                {"generation_mode":"preview","asset_type":"teaching","model":"sd2.5"})[0]
        martial.media_claim(job["id"])
        martial.media_report(job["id"],{"status":"technical_check"})
        source=Path(self.temp.name)/"large-candidate.mp4"
        shutil.copyfile(ROOT/"tests/fixtures"/"wuxiang"/"cryn-bagua-part01.mp4",source)
        target=52_000_000
        with source.open("ab") as output:
            output.write(struct.pack(">I4s",target-source.stat().st_size,b"free"))
            output.truncate(target)
        sha=martial_connector._file_sha256(source)
        web=ThreadingHTTPServer(("127.0.0.1",0),server.Handler)
        thread=threading.Thread(target=web.serve_forever,daemon=True);thread.start()
        try:
            url=f"http://127.0.0.1:{web.server_port}"
            first=martial_connector._upload_video_file(url,"test-signing-token",job["id"],source,sha)
            self.assertEqual(first["status"],"staged")
            duplicate=martial_connector._upload_video_file(url,"test-signing-token",job["id"],source,sha)
            self.assertEqual(duplicate["status"],"already_staged")
            report={"status":"succeeded","video_sha256":sha,
                    "technical":{"result":"pass","notes":"本地测试视频"}}
            saved=martial.media_report(job["id"],report)
            self.assertEqual(saved["status"],"succeeded")
            self.assertEqual(martial.media_report(job["id"],report)["status"],"succeeded")
            with store.connect() as c:
                asset=store.record(c,"assets",saved["candidate_asset_id"])
                row=c.execute("SELECT COUNT(*) FROM martial_qc WHERE media_job_id=? AND stage='technical'",
                              (job["id"],)).fetchone()
                self.assertEqual(row[0],1)
            self.assertEqual(asset["sha256"],sha)
            self.assertEqual(Path(asset["storage_ref"]).stat().st_size,target)
            self.assertEqual(martial.candidate_upload_state(job["id"],sha)["status"],"already_saved")
            self.assertFalse(martial._candidate_stage_path(job["id"]).exists())
        finally:
            web.shutdown();web.server_close();thread.join(timeout=5)

    def test_other_track_change_keeps_candidate_but_own_plan_change_invalidates_it(self):
        video=(ROOT/"tests/fixtures"/"wuxiang"/"cryn-bagua-part01.mp4").read_bytes()
        first_package=martial.request_package(self.employee,self.move_id)
        ready=martial.package_report(first_package["id"],{"status":"complete","body":self.body("旧讲解")})
        store.start(ready["task_id"],self.employee)
        martial.update_budget(self.manager,self.move_id,8)
        old=martial.create_media(self.employee,self.move_id,{"generation_mode":"preview","asset_type":"teaching","model":"sd2.5"})[0]
        martial.media_claim(old["id"])
        martial.media_report(old["id"],{"status":"succeeded","video_base64":self.upload("old.mp4",video)["base64"],
            "technical":{"result":"pass","notes":"container"}})
        martial.save_video_plan(self.employee,self.move_id,{"asset_type":"practice","source_start":0,
            "source_end":15,"target_duration":50,"brief":"跟练节奏调整"})
        detail=martial.move_detail(self.move_id,self.employee)
        self.assertTrue(next(job for job in detail["media"] if job["id"]==old["id"])["current_context"])
        martial.select_candidate(self.employee,old["id"],"已核对真人标准动作")
        self.assertEqual(next(row for row in martial.overview(self.employee)["moves"] if row["id"]==self.move_id)["candidate_count"],1)
        refreshed=martial.request_package(self.employee,self.move_id)
        martial.package_report(refreshed["id"],{"status":"complete","body":self.body("新版双视频")})
        current=martial.create_media(self.employee,self.move_id,{"generation_mode":"preview","asset_type":"practice","model":"sd2.5"})[0]
        martial.media_claim(current["id"])
        martial.media_report(current["id"],{"status":"succeeded","video_base64":self.upload("new.mp4",video)["base64"],
            "technical":{"result":"pass","notes":"container"}})
        martial.select_candidate(self.employee,current["id"],"已核对真人标准动作")
        martial.save_video_plan(self.employee,self.move_id,{"asset_type":"practice","source_start":0,
            "source_end":15,"target_duration":55,"brief":"跟练延长"})
        checks={key:"pass" for key in martial.QC_CHECKS}
        with self.assertRaisesRegex(ValueError,"视频规划已不是当前版本"):
            martial.martial_qc(self.employee,current["id"],{"checks":checks,"findings":"已检查",
                "reference_comparison":"对照真人标准动作"})


if __name__=="__main__":
    unittest.main()
