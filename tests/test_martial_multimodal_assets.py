"""Evidence-based three-level martial assets on an isolated database."""
from __future__ import annotations

import base64
import io
import sys
import tempfile
import unittest
import wave
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"app"))

import martial
import martial_initialization
import martial_multimodal_assets as mm
import martial_product
import store


class MartialMultimodalTest(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        store.DATA=Path(self.temp.name);store.DB=store.DATA/"work_os.sqlite3"
        project=store.DATA/"project";project.mkdir()
        store.PROJECT_ROOTS["wuxiang"]=project
        store.SHARED_ROOT=store.DATA/"shared";store.SHARED_ROOT.mkdir()
        store.initialize();martial.initialize();martial_initialization.initialize_confirmed_import()
        martial_product.initialize_product_migration();mm.initialize();mm.initialize()
        uid=store.create_user("mm-employee","武术员工","employee","fixture-password")
        self.employee={"id":uid,"role":"employee"}
        self.manager={"id":"u_system","role":"manager"}
        with store.connect() as c:
            c.execute("INSERT INTO martial_specialists(user_id,project_id,title,created_at) VALUES(?,?,?,?)",
                      (uid,"wuxiang","武术生产",store.now()))
        self.master=martial_product.create_master(self.employee,{"name":"MMMaster","species":"鹤","voice_id":"V01","profile":"老师设定"})["id"]
        self.art=martial_product.create_art(self.employee,{"chinese_name":"测试功法","english_name":"Test Art",
                    "category":"测试","description":"功法说明","master_id":self.master})["id"]
        self.move=martial_product.create_move(self.employee,{"martial_art_id":self.art,"chinese_name":"测试招式",
                    "chinese_action":"向前推掌","english_action":"Push forward","source_ref":"fixture"})["id"]
        image=(ROOT/"tests/fixtures"/"wuxiang"/"cryn-character.jpg").read_bytes()
        video=(ROOT/"tests/fixtures"/"wuxiang"/"cryn-bagua-part01.mp4").read_bytes()
        self.image=store.register_submission_asset("wuxiang","visual.jpg",image,uid)["id"]
        self.video=store.register_submission_asset("wuxiang","teaching.mp4",video,uid)["id"]
        self.invalid_video=store.register_submission_asset("wuxiang","invalid.mp4",b"not an mp4",uid)["id"]
        stream=io.BytesIO()
        with wave.open(stream,"wb") as sound:
            sound.setnchannels(1);sound.setsampwidth(2);sound.setframerate(8000);sound.writeframes(b"\x00\x00"*80)
        self.audio=store.register_submission_asset("wuxiang","voice.wav",stream.getvalue(),uid)["id"]
        martial_product.upload_master_visual(self.employee,self.master,{"field":"portrait",
            "upload":{"name":"portrait.jpg","base64":base64.b64encode(image).decode()},"source_ref":"fixture"})

    def tearDown(self):self.temp.cleanup()

    def evidence(self,result,key):return next(row for row in result["completion"]["evidence"] if row["key"]==key)

    def test_inheritance_override_script_tts_version_and_completion(self):
        initial=mm.move_assets(self.employee,self.move)
        self.assertIsNone(initial["bgm"]["effective_asset_id"])
        self.assertFalse(self.evidence(initial,"teaching_video")["ready"])
        self.assertFalse(self.evidence(initial,"intro_zh")["ready"])
        mm.set_art_asset(self.employee,self.art,{"role":"training_bgm","asset_id":self.audio})
        mm.set_art_asset(self.employee,self.art,{"role":"theme_music","asset_id":self.audio})
        mm.set_art_asset(self.employee,self.art,{"role":"logo","asset_id":self.image})
        mm.set_art_asset(self.employee,self.art,{"role":"main_visual","asset_id":self.image})
        mm.set_art_worldview(self.employee,self.art,{"zh_text":"武学世界观","en_text":"Worldview"})
        with self.assertRaisesRegex(PermissionError,"世界观"):
            mm.set_art_worldview(self.employee,self.art,{"zh_text":"擅自修改"})
        mm.set_master_voice_persona(self.employee,self.master,{"voice_id":"V01","persona":"温和清晰","language":"zh,en","tone":"温和","pace":"中速"})
        mm.set_master_intro_audio(self.employee,self.master,{"asset_id":self.audio})
        inherited=mm.move_assets(self.employee,self.move)
        self.assertEqual(inherited["bgm"]["effective_asset_id"],self.audio)
        self.assertEqual(inherited["bgm"]["source"],"art")
        self.assertEqual(inherited["master"]["voice_id"],"V01")
        self.assertEqual(inherited["master"]["voice_persona"]["persona"],"温和清晰")
        self.assertTrue(self.evidence(inherited,"effective_bgm")["ready"])
        self.assertTrue(self.evidence(inherited,"visual")["ready"])
        self.assertTrue(self.evidence(inherited,"intro_audio")["ready"])

        second_audio=store.register_submission_asset("wuxiang","override.wav",
            (store.DATA/"uploads"/"wuxiang"/(self.audio+".wav")).read_bytes(),self.employee["id"])["id"]
        mm.set_move_asset(self.employee,self.move,{"role":"training_bgm","asset_id":second_audio})
        overridden=mm.move_assets(self.employee,self.move)
        self.assertEqual(overridden["bgm"]["effective_asset_id"],second_audio)
        self.assertEqual(overridden["bgm"]["source"],"move")
        mm.set_move_asset(self.employee,self.move,{"role":"training_bgm","asset_id":None})
        self.assertEqual(mm.move_assets(self.employee,self.move)["bgm"]["source"],"art")

        first=mm.save_os_script(self.employee,self.move,{"kind":"intro","zh_text":"开始教学",
                    "en_text":"Start lesson","app_text":"play_intro()"})
        self.assertEqual(first["scripts"]["intro"]["version"],1)
        self.assertFalse(first["scripts"]["intro"]["tts"]["zh"]["current"])
        mm.link_tts_asset(self.employee,self.move,{"kind":"intro","language":"zh","asset_id":self.audio})
        mm.link_tts_asset(self.employee,self.move,{"kind":"intro","language":"en","asset_id":self.audio})
        voiced=mm.move_assets(self.employee,self.move)
        self.assertTrue(voiced["scripts"]["intro"]["tts"]["zh"]["current"])
        self.assertTrue(self.evidence(voiced,"intro_zh")["ready"])
        self.assertTrue(self.evidence(voiced,"intro_os")["ready"])
        mm.save_os_script(self.employee,self.move,{"kind":"intro","zh_text":"新版开场",
                    "en_text":"New opening","app_text":"play_intro_v2()"})
        revised=mm.move_assets(self.employee,self.move)
        self.assertEqual(revised["scripts"]["intro"]["version"],2)
        self.assertFalse(revised["scripts"]["intro"]["tts"]["zh"]["current"])
        self.assertFalse(self.evidence(revised,"intro_zh")["ready"])
        with store.connect() as c:
            self.assertEqual(c.execute("SELECT COUNT(*) FROM martial_mm_tts_assets WHERE move_id=? AND kind='intro' AND status='superseded'",(self.move,)).fetchone()[0],2)

    def test_real_media_and_pilot_filter(self):
        mm.set_move_asset(self.employee,self.move,{"role":"teaching_video","asset_id":self.invalid_video})
        invalid=mm.move_assets(self.employee,self.move)
        self.assertFalse(invalid["videos"]["teaching"]["asset"]["available"])
        self.assertFalse(self.evidence(invalid,"teaching_video")["ready"])
        mm.set_move_asset(self.employee,self.move,{"role":"teaching_video","asset_id":self.video})
        valid=mm.move_assets(self.employee,self.move)
        self.assertTrue(valid["videos"]["teaching"]["asset"]["available"])
        self.assertEqual(valid["videos"]["teaching"]["status"],"linked")
        self.assertTrue(self.evidence(valid,"teaching_video")["ready"])
        with store.connect() as c:
            c.execute("INSERT INTO martial_technical_history(kind,item_id,move_id,classification,source_ref,tagged_at) VALUES(?,?,?,?,?,?)",
                      ("asset",self.video,martial_product.PILOT_MOVE,"TECHNICAL_PILOT_HISTORY","fixture",store.now()))
        with self.assertRaisesRegex(ValueError,"技术试点"):
            mm.set_move_asset(self.employee,self.move,{"role":"practice_video","asset_id":self.video})

    def test_old_final_is_viewable_but_does_not_complete_new_video_plan(self):
        task=store.create_task({"project_id":"wuxiang","workflow_id":"WF-02",
                                "title":"双视频完成度测试","why":"核对当前方案与历史正式视频"},"u_system")
        stamp=store.now()
        with store.connect() as c:
            c.execute("INSERT INTO martial_motion_refs(id,move_id,version,video_asset_id,start_time,end_time,orientation,"
                      "start_pose,end_pose,key_moments,notes,status,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                      ("ref_mm_v1",self.move,1,self.video,0,5,"正面","","","[]","","locked",stamp))
            c.execute("INSERT INTO martial_video_plans(id,move_id,asset_type,version,motion_ref_id,source_start,source_end,"
                      "target_duration,brief,status,created_by,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                      ("mvp_mm_v1",self.move,"teaching",1,"ref_mm_v1",0,5,5,"讲解演示","active",self.employee["id"],stamp))
            move_version=c.execute("SELECT current_version FROM martial_moves WHERE id=?",(self.move,)).fetchone()[0]
            art_version=c.execute("SELECT version FROM martial_arts WHERE id=?",(self.art,)).fetchone()[0]
            master_version=c.execute("SELECT current_version FROM martial_masters WHERE id=?",(self.master,)).fetchone()[0]
            plan=dict(c.execute("SELECT * FROM martial_video_plans WHERE id='mvp_mm_v1'").fetchone())
            plan_facts={key:plan[key] for key in
                        ("id","asset_type","version","motion_ref_id","source_start","source_end","target_duration","brief")}
            facts={"package_prompt_version":martial.PACKAGE_PROMPT_VERSION,
                   "move":{"version":move_version},"art":{"version":art_version},
                   "master":{"version":master_version},"motion":{"id":"ref_mm_v1"},
                   "video_plans":{"teaching":plan_facts,"practice":None}}
            c.execute("INSERT INTO martial_packages(id,move_id,motion_ref_id,master_version,task_id,request_key,status,"
                      "facts_hash,facts,result,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                      ("pkg_mm_v1",self.move,"ref_mm_v1",master_version,task["id"],"mm-package","complete","hash",
                       store.dumps(facts),"{}",stamp,stamp))
            c.execute("INSERT INTO martial_media_jobs(id,move_id,task_id,package_id,motion_ref_id,master_version,"
                      "asset_type,provider,model_alias,model,prompt_hash,prompt,character_asset_id,duration,aspect_ratio,"
                      "resolution,status,request_key,idempotency_key,candidate_asset_id,reserved_cost,quote_source,"
                      "generation_mode,video_plan_id,technical_report,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                      ("mj_mm_v1",self.move,task["id"],"pkg_mm_v1","ref_mm_v1",master_version,"teaching","runy","sd2.5","model",
                       "hash","prompt",self.image,5,"16:9","480p","succeeded","mm-media","mm-idempotency",
                       self.video,3.6,"fixture","reproduce","mvp_mm_v1",
                       store.dumps({"result":"pass","server_probe":{"duration":5}}),stamp,stamp))
            c.execute("INSERT INTO martial_final_assets(id,move_id,asset_type,media_job_id,asset_id,status,approved_by,created_at) "
                      "VALUES(?,?,?,?,?,?,?,?)",("mf_mm_v1",self.move,"teaching","mj_mm_v1",self.video,"active",
                                                "u_system",stamp))
        first=mm.move_assets(self.employee,self.move)
        self.assertEqual(first["videos"]["teaching"]["status"],"final")
        self.assertTrue(self.evidence(first,"teaching_video")["ready"])

        with store.connect() as c:
            c.execute("UPDATE martial_media_jobs SET technical_report=? WHERE id='mj_mm_v1'",
                      (store.dumps({"result":"pass","server_probe":{"duration":2}}),))
        wrong_length=mm.move_assets(self.employee,self.move)
        self.assertFalse(self.evidence(wrong_length,"teaching_video")["ready"])
        self.assertEqual(wrong_length["videos"]["teaching"]["historical_final"]["final_asset_id"],"mf_mm_v1")
        with store.connect() as c:
            c.execute("UPDATE martial_media_jobs SET technical_report=? WHERE id='mj_mm_v1'",
                      (store.dumps({"result":"pass","server_probe":{"duration":5}}),))

        with store.connect() as c:
            c.execute("UPDATE martial_video_plans SET status='superseded' WHERE id='mvp_mm_v1'")
            c.execute("INSERT INTO martial_video_plans(id,move_id,asset_type,version,motion_ref_id,source_start,source_end,"
                      "target_duration,brief,status,created_by,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                      ("mvp_mm_v2",self.move,"teaching",2,"ref_mm_v1",0,5,6,"新讲解节奏","active",self.employee["id"],stamp))
        revised=mm.move_assets(self.employee,self.move)
        self.assertEqual(revised["videos"]["teaching"]["status"],"missing")
        self.assertFalse(self.evidence(revised,"teaching_video")["ready"])
        self.assertEqual(revised["videos"]["teaching"]["historical_final"]["asset"]["asset_id"],self.video)

        mm.set_move_asset(self.employee,self.move,{"role":"teaching_video","asset_id":self.video})
        linked=mm.move_assets(self.employee,self.move)
        self.assertEqual(linked["videos"]["teaching"]["status"],"linked")
        self.assertTrue(self.evidence(linked,"teaching_video")["ready"])
        self.assertEqual(linked["videos"]["teaching"]["historical_final"]["final_asset_id"],"mf_mm_v1")

        mm.set_move_asset(self.employee,self.move,{"role":"teaching_video","asset_id":None})
        with store.connect() as c:
            c.execute("INSERT INTO martial_motion_refs(id,move_id,version,video_asset_id,start_time,end_time,orientation,"
                      "start_pose,end_pose,key_moments,notes,status,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                      ("ref_mm_v2",self.move,2,self.video,0,5,"正面","","","[]","","locked",stamp))
            c.execute("UPDATE martial_video_plans SET status='superseded' WHERE id='mvp_mm_v2'")
            c.execute("INSERT INTO martial_video_plans(id,move_id,asset_type,version,motion_ref_id,source_start,source_end,"
                      "target_duration,brief,status,created_by,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                      ("mvp_mm_v3",self.move,"teaching",3,"ref_mm_v2",0,5,5,"新真人动作","active",
                       self.employee["id"],stamp))
        new_reference=mm.move_assets(self.employee,self.move)
        self.assertFalse(self.evidence(new_reference,"teaching_video")["ready"])
        self.assertEqual(new_reference["videos"]["teaching"]["historical_final"]["final_asset_id"],"mf_mm_v1")

    def test_type_scope_and_voice_change_rules(self):
        with self.assertRaisesRegex(ValueError,"类型不符"):
            mm.set_art_asset(self.employee,self.art,{"role":"logo","asset_id":self.audio})
        with self.assertRaisesRegex(ValueError,"OS Script"):
            mm.link_tts_asset(self.employee,self.move,{"kind":"practice","language":"zh","asset_id":self.audio})
        with self.assertRaisesRegex(PermissionError,"声线"):
            mm.set_master_voice_persona(self.employee,self.master,{"voice_id":"different"})
        with self.assertRaisesRegex(PermissionError,"声线"):
            mm.set_master_voice_persona(self.employee,self.master,{"voice_id":""})
        manager=mm.set_master_voice_persona(self.manager,self.master,{"voice_id":"different","persona":"新声线"})
        self.assertEqual(manager["voice_id"],"different")

    def test_voice_audition_and_reference_stay_separate_from_master_version_and_intro(self):
        audition=store.register_submission_asset("wuxiang","audition.mp3",b"ID3\x04\x00\x00\x00\x00\x00\x00",self.employee["id"])["id"]
        with store.connect() as c:
            before=c.execute("SELECT current_version,draft_version FROM martial_masters WHERE id=?",(self.master,)).fetchone()
            current=c.execute("SELECT payload FROM martial_master_versions WHERE master_id=? AND version=?",
                              (self.master,before["current_version"])).fetchone()[0]
        mm._set_link(self.employee,"master",self.master,"voice_audition",audition,"audio","fixture")
        mm._set_link(self.employee,"master",self.master,"voice_reference",self.audio,"audio","fixture")
        result=mm.master_assets(self.employee,self.master)
        self.assertEqual(result["voice_audition"]["asset_id"],audition)
        self.assertTrue(result["voice_audition"]["available"])
        self.assertEqual(result["voice_reference"]["asset_id"],self.audio)
        self.assertTrue(result["voice_reference"]["available"])
        self.assertIsNone(result["intro_audio"])
        self.assertIsNone(result["voice_preview"])
        inherited=mm.move_assets(self.employee,self.move)["voice_inheritance"]
        self.assertEqual(inherited["voice_reference"]["asset_id"],self.audio)
        with store.connect() as c:
            after=c.execute("SELECT current_version,draft_version FROM martial_masters WHERE id=?",(self.master,)).fetchone()
            payload=c.execute("SELECT payload FROM martial_master_versions WHERE master_id=? AND version=?",
                              (self.master,after["current_version"])).fetchone()[0]
        self.assertEqual(tuple(before),tuple(after))
        self.assertEqual(payload,current)
        mm._set_link(self.employee,"master",self.master,"voice_audition",self.audio,"audio","wrong format fixture")
        self.assertFalse(mm.master_assets(self.employee,self.master)["voice_audition"]["available"])


if __name__=="__main__":unittest.main()
