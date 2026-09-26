"""Martial role, version, budget and human QC gates on an isolated database."""
from __future__ import annotations

import base64
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"app"))


class MartialTest(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        import store,martial
        self.s=store;self.m=martial
        store.DATA=Path(self.temp.name);store.DB=store.DATA/"work_os.sqlite3"
        root=store.DATA/"project";root.mkdir()
        store.PROJECT_ROOTS["wuxiang"]=root
        store.SHARED_ROOT=store.DATA/"shared";store.SHARED_ROOT.mkdir()
        store.initialize();martial.initialize()
        specialist=store.create_user("xia","夏润麒","employee","specialist-password-test")
        other=store.create_user("other","其他员工","employee","employee-password-test")
        manager=store.create_user("manager","负责人","manager","manager-password-test")
        self.x={"id":specialist,"role":"employee"};self.other={"id":other,"role":"employee"};self.manager={"id":manager,"role":"manager"}
        with store.connect() as c:
            c.execute("INSERT INTO martial_specialists(user_id,project_id,title,created_at) VALUES(?,?,?,?)",
                      (specialist,"wuxiang","武术数字资产内容设计",store.now()))
        os.environ["YOODUN_CONNECTOR_TOKEN"]="test-signing-token"
        martial.route_report([{"model_alias":"sd2.0","provider":"runy","model":"doubao-seedance-2.0"},
                              {"model_alias":"sd2.5","provider":"runy","model":"doubao-seedance-2-5"}])

    def tearDown(self):
        os.environ.pop("YOODUN_CONNECTOR_TOKEN",None)
        self.temp.cleanup()

    @staticmethod
    def upload(name,body):
        return {"name":name,"base64":base64.b64encode(body).decode()}

    def plan_both(self, move_id, ref):
        for kind,brief in (("teaching","讲解动作并演示"),("practice","跟教练跟练动作")):
            self.m.save_video_plan(self.x,move_id,{"asset_type":kind,"source_start":ref["start_time"],
                "source_end":ref["end_time"],"target_duration":45 if kind=="teaching" else 60,
                "brief":brief})

    def test_fact_version_motion_budget_candidate_and_qc(self):
        m=self.m;s=self.s
        with self.assertRaises(PermissionError):m.overview(self.other)
        spec={"martial_art_id":"flowing_cloud","order":1,"source_ref":"original-doc/row-1",
              "chinese_name":"掌御流云","english_name":"Palm Guides Clouds","chinese_action":"原文动作",
              "english_action":"Original motion","chinese_coaching":"原文旁白","english_coaching":"Original narration"}
        self.assertEqual(len(m.overview(self.x)["moves"]),47)
        art=m.art_detail("flowing_cloud",self.x)["art"]
        m.save_art(self.x,"flowing_cloud",{"chinese_name":art["chinese_name"],"english_name":art["english_name"],
                   "volume":art["volume"],"category":art["category"],"description":art["description"],
                   "style_traits":art["style_traits"],"master_id":art["master_id"],
                   "planned_moves":art["planned_moves"],"source_ref":art["source_ref"]})
        m.approve_art(self.manager,"flowing_cloud")
        mid="mv_flowing_cloud_01"
        first=m.move_detail(mid,self.x)
        self.assertEqual(first["version"]["payload"]["chinese_name"],"掌御流云")
        spec["move_id"]=mid;spec["chinese_action"]="修订动作"
        second=m.save_move(self.x,spec)
        self.assertEqual(second["version"]["version"],2)
        with s.connect() as c:
            self.assertEqual(c.execute("SELECT status FROM martial_move_versions WHERE move_id=? AND version=1",(mid,)).fetchone()[0],"superseded")
        with self.assertRaises(ValueError):m.approve_move(self.manager,mid)
        m.submit_move(self.x,mid,{"note":"已核对修订动作与原文"})
        m.approve_move(self.manager,mid)
        with self.assertRaises(PermissionError):m.approve_move(self.x,mid)
        image_bytes=(ROOT/"tests/fixtures"/"wuxiang"/"cryn-character.jpg").read_bytes()
        master=m.upload_master_asset(self.x,"wongkey",{"field":"portrait","upload":self.upload("master.jpg",image_bytes),"source_ref":"test visual fixture"})
        self.assertEqual(master["master"]["draft_version"],3)
        m.approve_master(self.manager,"wongkey")
        motion_bytes=(ROOT/"tests/fixtures"/"wuxiang"/"cryn-bagua-part01.mp4").read_bytes()
        motion=m.upload_motion(self.x,mid,{"upload":self.upload("motion.mp4",motion_bytes),
                    "start_time":0,"end_time":15,"duration":5,"orientation":"正面",
                    "start_pose":"正身起势","end_pose":"收势站定","key_moments":["0 起势","4 收势"]})
        ref=motion["motions"][0]
        self.assertEqual(ref["status"],"draft")
        self.assertEqual((ref["duration"],ref["width"],ref["height"]),(15.0,1280,720))
        m.confirm_motion(self.x,ref["id"])
        self.plan_both(mid,ref)
        pkg=m.request_package(self.x,mid)
        claim=m.package_claim(pkg["id"])
        self.assertEqual(claim["facts"]["move"]["chinese_action"],"修订动作")
        self.assertEqual(claim["facts"]["master"]["canonical_sha256"],
                         m.master_detail("wongkey",self.x)["versions"][0]["payload"]["canonical_sha256"])
        body={"move_summary":"据实制作","motion_breakdown":["起势","收势"],"character_constraints":[],
              "shot_camera_plan":["全景"],"seedance_prompt":"数字老师演示原文动作",
              "teaching_prompt":"数字老师演示原文动作","practice_prompt":"数字老师带领跟练原文动作","negative_constraints":[],
              "reference_mapping":[],"qc_checklist":["对照参考"],"missing_inputs":[],"facts":claim["facts"],"ai_suggestions":[]}
        ready=m.package_report(pkg["id"],{"status":"complete","body":body,"usage":{"prompt_tokens":5,"completion_tokens":8}})
        self.assertTrue(ready["task_id"])
        tid=ready["task_id"]
        self.assertEqual(m.move_detail(mid,self.x)["task"]["assignee_id"],self.x["id"])
        self.assertEqual(m.package_report(pkg["id"],{"status":"complete"})["task_id"],tid)
        self.assertEqual(m.quote(self.x,mid,"sd2.5",1)["task_budget"],3.6)
        self.assertFalse(m.quote(self.x,mid,"sd2.5",1)["blocked"])
        m.route_report([])
        unknown=m.quote(self.x,mid,"sd2.5",1)
        self.assertIsNone(unknown["estimated_cost"])
        self.assertTrue(unknown["blocked"])
        m.route_report([{"model_alias":"sd2.5","provider":"runy","model":"doubao-seedance-2-5"}])
        with self.assertRaises(PermissionError):m.create_media(self.x,mid,{"generation_mode":"preview","asset_type":"teaching","model":"sd2.5"})
        s.start(tid,self.x)
        jobs=m.create_media(self.x,mid,{"generation_mode":"preview","asset_type":"teaching","model":"sd2.5","candidate_count":1,"revision_of":""})
        jid=jobs[0]["id"]
        self.assertEqual(jobs[0]["status"],"queued")
        self.assertTrue(m.quote(self.x,mid,"sd2.5",1)["blocked"])
        spec=m.media_claim(jid)
        self.assertEqual(spec["provider"],"runy")
        self.assertEqual(spec["model"],"doubao-seedance-2-5")
        source=spec["image_urls"][0]
        from urllib.parse import urlparse,parse_qs
        parsed=urlparse(source);query=parse_qs(parsed.query)
        self.assertTrue(m.source_path(parsed.path.rsplit("/",1)[-1],query["exp"][0],query["sig"][0]).is_file())
        with self.assertRaises(PermissionError):m.source_path(parsed.path.rsplit("/",1)[-1],query["exp"][0],"bad")
        done=m.media_report(jid,{"status":"succeeded","video_base64":self.upload("video.mp4",motion_bytes)["base64"],
                                 "technical":{"result":"pass","notes":"container"},"local_job_id":"j1","local_media_id":"m1"})
        self.assertEqual(done["status"],"succeeded")
        available={asset["id"] for asset in m.available_assets(self.x)["assets"]}
        self.assertIn(ref["video_asset_id"],available)
        self.assertNotIn(done["candidate_asset_id"],available)
        with self.assertRaisesRegex(ValueError,"AI 生成候选不能作为真人动作参考"):
            m.link_motion(self.x,mid,{"asset_id":done["candidate_asset_id"],
                "start_time":0,"end_time":4,"orientation":"正面","start_pose":"起势",
                "end_pose":"收势","key_moments":["0 起势"]})
        self.assertEqual(len(m.move_detail(mid,self.x)["motions"]),1)
        with s.connect() as c:
            row=c.execute("SELECT technical_report,local_job_id,local_media_id FROM martial_media_jobs WHERE id=?",(jid,)).fetchone()
        self.assertEqual((row["local_job_id"],row["local_media_id"]),("j1","m1"))
        self.assertEqual(s.parse(row["technical_report"],{})["server_probe"]["width"],1280)
        self.assertEqual(m.media_report(jid,{"status":"succeeded","video_base64":"invalid"})["status"],"succeeded")
        with self.assertRaises(ValueError):m.martial_qc(self.x,jid,{"verdict":"pass","findings":"已核对","reference_comparison":"与真人相符"})
        with self.assertRaises(PermissionError):m.select_candidate(self.other,jid,"动作和角色一致")
        m.select_candidate(self.x,jid,"动作与角色已经对照真人参考")
        checks={key:"pass" for key in m.QC_CHECKS}
        with self.assertRaises(ValueError):m.martial_qc(self.x,jid,{"verdict":"pass","checks":checks|{"hand_path":"fail"},
            "issue_ranges":[{"start":1,"end":2,"issue":"手部路径错误","severity":"major"}],
            "findings":"有动作错误","reference_comparison":"逐段对照"})
        failed=m.martial_qc(self.x,jid,{"verdict":"fail","checks":checks|{"hand_path":"fail"},
            "issue_ranges":[{"start":"00:01.00","end":"00:02.00","move_id":mid,
                             "body_part":"左掌","issue_type":"path","issue":"手部路径错误",
                             "severity":"major","comment":"按真人参考降低左掌托举高度"}],
            "findings":"动作路径错误","reference_comparison":"逐段对照 1 至 2 秒"})
        self.assertEqual(failed["status"],"revision_required")
        self.assertEqual(failed["issue_ranges"][0]["body_part"],"左掌")
        with s.connect() as c:
            payload=s.parse(c.execute("SELECT payload FROM martial_revision_packages WHERE id=?",
                                      (failed["revision_package_id"],)).fetchone()[0],{})
        self.assertEqual(payload["target_range"][0]["comment"],"按真人参考降低左掌托举高度")
        with self.assertRaises(ValueError):m.finalize(self.manager,jid)
        m.update_budget(self.manager,mid,12)
        bad_job=m.create_media(self.x,mid,{"generation_mode":"preview","asset_type":"teaching","model":"sd2.5"})[0]
        m.media_claim(bad_job["id"])
        unverified=m.media_report(bad_job["id"],{"status":"succeeded",
            "video_base64":self.upload("invalid.mp4",b"\x00\x00\x00\x18ftypisom")["base64"],
            "technical":{"result":"pass","notes":"client claimed pass"},"provider_job_id":"upstream-1"})
        self.assertTrue(unverified["candidate_asset_id"])
        with s.connect() as c:
            bad_record=c.execute("SELECT technical_report,provider_job_id FROM martial_media_jobs WHERE id=?",(bad_job["id"],)).fetchone()
        self.assertEqual(bad_record["provider_job_id"],"upstream-1")
        self.assertEqual(s.parse(bad_record["technical_report"],{})["result"],"unverified")
        with self.assertRaises(ValueError):m.select_candidate(self.x,bad_job["id"],"这个文件未经服务端验证不能采用")
        revised=m.create_media(self.x,mid,{"generation_mode":"preview","asset_type":"teaching","model":"sd2.5","revision_of":jid})[0]
        new_job_id=revised["id"]
        m.media_claim(new_job_id)
        m.media_report(new_job_id,{"status":"succeeded","video_base64":self.upload("video-v2.mp4",motion_bytes)["base64"],
                                   "technical":{"result":"pass","notes":"container"},"local_job_id":"j2","local_media_id":"m2"})
        m.select_candidate(self.x,new_job_id,"返修后动作已经逐段核对真人参考")
        m.martial_qc(self.x,new_job_id,{"verdict":"pass","checks":checks,"issue_ranges":[],
                                 "findings":"真人动作与角色一致","reference_comparison":"逐段对照 0 至 4 秒"})
        with self.assertRaisesRegex(ValueError,"样片不能定版"):
            m.finalize(self.manager,new_job_id)
        with s.connect() as c:c.execute("UPDATE martial_media_jobs SET generation_mode=NULL WHERE id=?",(new_job_id,))
        with self.assertRaisesRegex(ValueError,"历史候选缺少"):
            m.finalize(self.manager,new_job_id)
        # Simulate a future verified full-length connector result; no provider call is made.
        with s.connect() as c:
            technical=s.parse(c.execute("SELECT technical_report FROM martial_media_jobs WHERE id=?",(new_job_id,)).fetchone()[0],{})
            technical["server_probe"]["duration"]=45
            c.execute("UPDATE martial_media_jobs SET generation_mode='reproduce',duration=45,technical_report=? WHERE id=?",
                      (s.dumps(technical),new_job_id))
        final=m.finalize(self.manager,new_job_id)
        self.assertEqual(final["status"],"active")
        detail=m.move_detail(mid,self.x)
        self.assertNotIn("provider",detail["media"][0])
        self.assertEqual(detail["finals"][0]["media_job_id"],new_job_id)
        old=next(job for job in detail["media"] if job["id"]==jid)
        self.assertEqual(old["qc"][-1]["issue_ranges"][0]["issue"],"手部路径错误")
        self.assertEqual(old["revision_of"],None)
        newer=m.upload_motion(self.x,mid,{"upload":self.upload("updated-motion.mp4",motion_bytes),
            "start_time":1,"end_time":3,"orientation":"正面","start_pose":"正身起势",
            "end_pose":"收势站定","key_moments":[{"time":2,"label":"掌势转折"}]})["motions"][0]
        m.confirm_motion(self.x,newer["id"])
        self.assertFalse(next(row for row in m.overview(self.x)["moves"] if row["id"]==mid)["final_types"])
        self.plan_both(mid,newer)
        fresh=m.request_package(self.x,mid)
        prepared=m.package_report(fresh["id"],{"status":"complete","body":body})
        self.assertTrue(prepared["task_id"])
        self.assertNotEqual(prepared["task_id"],tid)
        detail=m.move_detail(mid,self.x)
        self.assertEqual(detail["task"]["id"],prepared["task_id"])
        self.assertEqual(detail["task"]["budget_cap"],3.6)
        self.assertEqual(detail["finals"][0]["media_job_id"],new_job_id)
        with s.connect() as c:
            old_lock=s.parse(s.record(c,"tasks",tid)["motion_lock"],{})
            new_lock=s.parse(s.record(c,"tasks",prepared["task_id"])["motion_lock"],{})
        self.assertEqual((old_lock["asset_id"],old_lock["version"]),(ref["video_asset_id"],ref["version"]))
        self.assertEqual((new_lock["asset_id"],new_lock["version"]),(newer["video_asset_id"],newer["version"]))

    def test_employee_submission_batch_safeguards_and_rejection(self):
        m=self.m;s=self.s
        m.approve_art(self.manager,"flowing_cloud")
        a="mv_flowing_cloud_01";b="mv_flowing_cloud_02"
        with self.assertRaises(ValueError):m.batch_approve_moves(self.manager,"flowing_cloud",[a])
        first=m.submit_move(self.x,a,{"note":"逐句核对了中英文动作","review_flags":[]})
        self.assertEqual(first["version"]["status"],"ready_for_approval")
        self.assertFalse(first["version"]["batch_eligible"])  # initial needs_review needs individual approval
        second=m.submit_move(self.x,b,{"note":"动作路线有疑问","review_flags":["question"]})
        self.assertFalse(second["version"]["batch_eligible"])
        with self.assertRaises(ValueError):m.batch_approve_moves(self.manager,"flowing_cloud",[a,b])
        self.assertEqual(m.move_detail(a,self.x)["move"]["current_version"],0)
        m.approve_move(self.manager,a)
        self.assertGreater(m.move_detail(a,self.x)["move"]["current_version"],0)
        reviewed=m.move_detail(a,self.x)["version"]
        m.save_move(self.x,{"martial_art_id":"flowing_cloud","move_id":a,"source_ref":reviewed["source_ref"],**reviewed["payload"]})
        repeat=m.submit_move(self.x,a,{"note":"对照锁定版复核，无内容变更"})
        self.assertTrue(repeat["version"]["batch_eligible"])
        self.assertEqual(m.batch_approve_moves(self.manager,"flowing_cloud",[a])["count"],1)
        returned=m.reject_move(self.manager,b,"请核对手部路径与原文")
        self.assertEqual(returned["version"]["status"],"returned")
        with self.assertRaises(ValueError):m.submit_move(self.x,b,{"note":"原稿没变"})
        draft=returned["version"]["payload"]
        m.save_move(self.x,{"martial_art_id":"flowing_cloud","move_id":b,"source_ref":returned["version"]["source_ref"],**draft})
        self.assertEqual(m.move_detail(b,self.x)["version"]["status"],"needs_review")
        with s.connect() as c:
            self.assertEqual(c.execute("SELECT status FROM martial_move_versions WHERE move_id=? AND version=1",(b,)).fetchone()[0],"superseded")

    def test_package_missing_inputs_cannot_materialize_paid_task(self):
        m=self.m;s=self.s
        m.approve_art(self.manager,"flowing_cloud")
        move_id="mv_flowing_cloud_01"
        m.submit_move(self.x,move_id,{"note":"已核对动作和旁白"})
        m.approve_move(self.manager,move_id)
        image=(ROOT/"tests/fixtures"/"wuxiang"/"cryn-character.jpg").read_bytes()
        m.upload_master_asset(self.x,"wongkey",{"field":"portrait","upload":self.upload("test.jpg",image)})
        m.approve_master(self.manager,"wongkey")
        self.assertTrue(m.master_detail("wongkey",self.x)["master_status"]["production_ready"])
        video=(ROOT/"tests/fixtures"/"wuxiang"/"cryn-bagua-part01.mp4").read_bytes()
        with s.connect() as c:before=c.execute("SELECT COUNT(*) FROM assets").fetchone()[0]
        with self.assertRaises(ValueError):m.upload_motion(self.x,move_id,{"upload":self.upload("invalid-range.mp4",video),
            "start_time":0,"end_time":500,"key_moments":[]})
        with s.connect() as c:self.assertEqual(c.execute("SELECT COUNT(*) FROM assets").fetchone()[0],before)
        ref=m.upload_motion(self.x,move_id,{"upload":self.upload("motion.mov",video),"start_time":1,
            "end_time":5,"duration":500,"orientation":"正面","start_pose":"正身起势","end_pose":"收势站定",
            "key_moments":[{"time":2.1,"label":"起势"}]})["motions"][0]
        self.assertEqual(ref["duration"],15.0)
        m.confirm_motion(self.x,ref["id"])
        self.plan_both(move_id,ref)
        package=m.request_package(self.x,move_id)
        result=m.package_report(package["id"],{"status":"complete","body":{
            "move_summary":"待补资料","motion_breakdown":[],"character_constraints":[],"shot_camera_plan":[],
            "seedance_prompt":"","teaching_prompt":"","practice_prompt":"",
            "negative_constraints":[],"reference_mapping":[],"qc_checklist":[],
            "missing_inputs":["起势姿态尚未确认"]},"usage":{"prompt_tokens":10,"completion_tokens":12}})
        self.assertIsNone(result["task_id"])
        self.assertIn("起势姿态尚未确认",m.move_detail(move_id,self.x)["production_blockers"][-2])
        with s.connect() as c:
            self.assertIsNone(c.execute("SELECT task_id FROM martial_moves WHERE id=?",(move_id,)).fetchone()[0])

    def test_first_final_sample_requires_lead_then_specialist_can_finalize_clear_qc(self):
        m=self.m;s=self.s
        import martial_initialization
        martial_initialization.initialize_confirmed_import()
        mid="mv_flowing_cloud_01"
        image=(ROOT/"tests/fixtures"/"wuxiang"/"cryn-character.jpg").read_bytes()
        m.upload_master_asset(self.x,"wongkey",{"field":"portrait","upload":self.upload("master.jpg",image)})
        m.approve_master(self.manager,"wongkey")
        video=(ROOT/"tests/fixtures"/"wuxiang"/"cryn-bagua-part01.mp4").read_bytes()
        ref=m.upload_motion(self.x,mid,{"upload":self.upload("motion.mp4",video),"start_time":0,
            "end_time":15,"orientation":"正面","start_pose":"正身起势","end_pose":"收势站定",
            "key_moments":[{"time":2,"label":"动作转折"}]})["motions"][0]
        m.confirm_motion(self.x,ref["id"])
        self.plan_both(mid,ref)
        pkg=m.request_package(self.x,mid)
        body={"move_summary":"按原文制作","motion_breakdown":["起势","收势"],
              "character_constraints":[],"shot_camera_plan":["全身景别"],
              "seedance_prompt":"按锁定真人动作制作","teaching_prompt":"按锁定真人动作讲解演示",
              "practice_prompt":"按锁定真人动作带领跟练","negative_constraints":[],
              "reference_mapping":[],"qc_checklist":["动作准确"],"missing_inputs":[]}
        task_id=m.package_report(pkg["id"],{"status":"complete","body":body})["task_id"]
        self.assertTrue(task_id)
        s.start(task_id,self.x)
        m.update_budget(self.manager,mid,30)
        checks={key:"pass" for key in m.QC_CHECKS}

        def candidate(*, asset_type="teaching", major_dispute=False, unsure=False):
            job=m.create_media(self.x,mid,{"generation_mode":"preview","asset_type":asset_type,"model":"sd2.5","candidate_count":1})[0]
            m.media_claim(job["id"])
            m.media_report(job["id"],{"status":"succeeded","video_base64":self.upload("candidate.mp4",video)["base64"],
                                      "technical":{"result":"pass","notes":"container"}})
            m.select_candidate(self.x,job["id"],"已逐项对照真人参考")
            submitted=checks|({"footwork":"unsure"} if unsure else {})
            qc=m.martial_qc(self.x,job["id"],{"checks":submitted,"major_dispute":major_dispute,
                "findings":"动作已逐段检查","reference_comparison":"对照真人标准动作"})
            self.assertEqual(qc["status"],"pass")
            # Simulated verified full-length connector response for approval-gate testing.
            with s.connect() as c:
                technical=s.parse(c.execute("SELECT technical_report FROM martial_media_jobs WHERE id=?",(job["id"],)).fetchone()[0],{})
                duration=45 if asset_type=="teaching" else 60
                technical["server_probe"]["duration"]=duration
                c.execute("UPDATE martial_media_jobs SET generation_mode='reproduce',duration=?,technical_report=? WHERE id=?",
                          (duration,s.dumps(technical),job["id"]))
            return job["id"]

        first=candidate()
        self.assertIn("首个重要正式样板",m.move_detail(mid,self.x)["media"][0]["finalization_blocker"])
        with self.assertRaisesRegex(PermissionError,"首个重要正式样板"):
            m.finalize(self.x,first)
        first_final=m.finalize(self.manager,first)
        self.assertEqual(first_final["status"],"active")

        disputed=candidate(major_dispute=True)
        with self.assertRaisesRegex(PermissionError,"重大质量争议"):
            m.finalize(self.x,disputed)
        uncertain=candidate(unsure=True)
        with self.assertRaisesRegex(PermissionError,"待核实项"):
            m.finalize(self.x,uncertain)

        routine=candidate()
        routine_final=m.finalize(self.x,routine)
        self.assertEqual(m.finalize(self.x,routine),routine_final)
        summary=next(row for row in m.overview(self.x)["moves"] if row["id"]==mid)
        self.assertEqual(summary["final_types"],["teaching"])
        self.assertNotEqual(summary["production_status"],"completed")
        practice=candidate(asset_type="practice")
        m.finalize(self.x,practice)
        summary=next(row for row in m.overview(self.x)["moves"] if row["id"]==mid)
        self.assertEqual(summary["final_types"],["practice","teaching"])
        self.assertEqual(summary["production_status"],"completed")
        revised_plan=m.save_video_plan(self.x,mid,{"asset_type":"practice","source_start":0,"source_end":4,
            "target_duration":65,"brief":"跟练速度放慢"})
        self.assertFalse(revised_plan["package_current"])
        self.assertEqual(revised_plan["final_types"],["teaching"])
        with self.assertRaisesRegex(ValueError,"视频规划已不是当前版本"):
            m.finalize(self.x,practice)
        summary=next(row for row in m.overview(self.x)["moves"] if row["id"]==mid)
        self.assertEqual(summary["final_types"],["teaching"])
        self.assertNotEqual(summary["production_status"],"completed")
        with s.connect() as c:
            self.assertEqual(c.execute("SELECT status FROM martial_final_assets WHERE id=?",(first_final["id"],)).fetchone()[0],"superseded")
            row=c.execute("SELECT approved_by FROM martial_final_assets WHERE id=?",(routine_final["id"],)).fetchone()
            self.assertEqual(row[0],self.x["id"])
            self.assertEqual(c.execute("SELECT COUNT(*) FROM audit WHERE action='martial.final.publish'").fetchone()[0],2)
        import martial_product
        routine_edit=martial_product.edit_move_standard(self.x,mid,{"safety_notes":"注意落脚稳定"})
        self.assertFalse(routine_edit["needs_review"])
        self.assertEqual(routine_edit["move"]["draft_version"],0)
        self.assertEqual(next(row for row in m.overview(self.x)["moves"] if row["id"]==mid)["final_types"],[])
        proposed=martial_product.edit_move_standard(self.x,mid,{"chinese_action":"改为不同的动作路线"})
        self.assertTrue(proposed["needs_review"])
        self.assertGreater(proposed["move"]["draft_version"],0)
        self.assertEqual(proposed["effective_version"]["payload"]["chinese_action"],
                         routine_edit["effective_version"]["payload"]["chinese_action"])
        m.approve_move(self.manager,mid)
        self.assertEqual(m.move_detail(mid,self.x)["move"]["draft_version"],0)


if __name__=="__main__":unittest.main()
