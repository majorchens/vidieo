"""Current business mapping and P0 history isolation on a disposable database."""
from __future__ import annotations

import base64
import csv
import io
import struct
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"app"))
import store
import martial
import martial_product
import martial_initialization


class MartialProductTest(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        store.DATA=Path(self.temp.name)
        store.DB=store.DATA/"work_os.sqlite3"
        project=store.DATA/"project";project.mkdir()
        store.PROJECT_ROOTS["wuxiang"]=project
        store.SHARED_ROOT=store.DATA/"shared";store.SHARED_ROOT.mkdir()
        store.initialize();martial.initialize();martial_initialization.initialize_confirmed_import()
        uid=store.create_user("martial-test","武术员工","employee","martial-test-password")
        self.employee={"id":uid,"role":"employee"}
        self.manager={"id":"u_system","role":"manager"}
        with store.connect() as c:
            c.execute("INSERT INTO martial_specialists(user_id,project_id,title,created_at) VALUES(?,?,?,?)",
                      (uid,"wuxiang","武术资产",store.now()))

    def tearDown(self):self.temp.cleanup()

    def test_current_beginner_mapping_and_new_creations(self):
        result=martial_product.initialize_product_migration()
        self.assertEqual(result["current_moves"],43)
        self.assertEqual(martial_product.initialize_product_migration(),result)
        overview=martial.overview(self.employee)
        beginner=[m for m in overview["moves"] if m["martial_art_id"]=="beginner"]
        self.assertEqual([m["business_label"] for m in beginner],["马步","冲拳","马步冲拳"])
        self.assertEqual(overview["counts"]["motion_missing"],42)
        self.assertEqual(overview["counts"]["fact_missing"],1)
        with self.assertRaises(KeyError):martial.move_detail("mv_beginner_03",self.employee)
        placeholder=martial.move_detail("mv_beginner_mabu_chongquan",self.employee)
        self.assertFalse(placeholder["effective_version"]["payload"]["chinese_action"])
        self.assertEqual(next(m for m in beginner if m["id"]=="mv_beginner_mabu_chongquan")["production_status"],"missing_fact")
        with self.assertRaisesRegex(ValueError,"动作.*为空"):
            martial.request_package(self.employee,"mv_beginner_mabu_chongquan")
        with store.connect() as c:
            self.assertEqual(c.execute("SELECT COUNT(*) FROM martial_moves").fetchone()[0],48)
            self.assertEqual(c.execute("SELECT COUNT(*) FROM martial_move_versions WHERE move_id LIKE 'mv_beginner_%' AND version=1").fetchone()[0],8)

        art=martial_product.create_art(self.employee,{"chinese_name":"新功法","english_name":"New Art",
                                              "category":"拳法","description":"工作台创建","order":6})
        self.assertEqual(art["art"]["master_id"],"")
        move=martial_product.create_move(self.employee,{"martial_art_id":art["id"],"chinese_name":"新招式",
                                                 "chinese_action":"动作描述","english_action":"Action description",
                                                 "source_ref":"工作台新增"})
        self.assertEqual(move["move"]["current_version"],1)
        self.assertIn("功法尚未绑定老师",move["production_blockers"])
        master=martial_product.create_master(self.employee,{"name":"NewMaster","species":"猴",
                                                     "chinese_name":"新老师","profile":"新老师人物设定"})
        bound=martial_product.update_art(self.employee,art["id"],{"master_id":master["id"]})
        self.assertEqual(bound["art"]["master_id"],master["id"])
        self.assertEqual(bound["art"]["draft_version"],0)
        image=(ROOT/"tests/fixtures"/"wuxiang"/"cryn-character.jpg").read_bytes()
        visual=martial_product.upload_master_visual(self.employee,master["id"],{
            "field":"portrait","upload":{"name":"portrait.jpg","base64":base64.b64encode(image).decode()}})
        self.assertTrue(visual["master_status"]["production_ready"])
        self.assertEqual(visual["master"]["draft_version"],0)
        angle=martial_product.upload_master_visual(self.employee,master["id"],{
            "field":"other_angle","upload":{"name":"angle.jpg","base64":base64.b64encode(image).decode()}})
        self.assertEqual(len(angle["versions"][0]["payload"]["other_angles"]),1)
        costume=martial_product.upload_master_visual(self.employee,master["id"],{
            "field":"costume","upload":{"name":"costume.jpg","base64":base64.b64encode(image).decode()}})
        self.assertTrue(costume["master_status"]["costume_asset_id"])
        glb=b"glTF"+struct.pack("<II",2,24)+struct.pack("<I4s",4,b"JSON")+b"{}  "
        model=martial_product.upload_master_visual(self.employee,master["id"],{
            "field":"digital_model","upload":{"name":"teacher.glb","base64":base64.b64encode(glb).decode()}})
        self.assertTrue(model["master_status"]["digital_model_asset_id"])
        with store.connect() as c:
            entry=c.execute("SELECT type,storage_ref FROM assets WHERE id=?",(model["master_status"]["digital_model_asset_id"],)).fetchone()
            self.assertEqual(entry["type"],"character")
            self.assertIn("AI Production Assets",entry["storage_ref"])
        gallery=martial_product.list_assets(self.employee,{"art_id":art["id"]})["assets"]
        self.assertTrue(any(item["master_id"]==master["id"] and item["art_id"]==art["id"] for item in gallery))
        second=martial_product.create_master(self.employee,{"name":"OtherMaster","species":"熊"})
        draft=martial_product.update_art(self.employee,art["id"],{"master_id":second["id"]})
        self.assertEqual(draft["art"]["master_id"],master["id"])
        self.assertGreater(draft["art"]["draft_version"],0)
        new_draft=martial_product.create_art(self.employee,{"chinese_name":"草稿功法","category":"基础","status":"draft"})
        self.assertEqual(new_draft["art"]["status"],"draft")
        published=martial_product.update_art(self.employee,new_draft["id"],{"status":"active","description":"已准备"})
        self.assertEqual(published["art"]["status"],"active")
        self.assertEqual(published["art"]["draft_version"],0)

    def test_verified_p0_rows_are_hidden_but_auditable(self):
        ref_asset=store.register_submission_asset("wuxiang","pilot.mp4",b"technical history",self.employee["id"])
        visual=store.register_submission_asset("wuxiang","pilot.jpg",
                 (ROOT/"tests/fixtures"/"wuxiang"/"cryn-character.jpg").read_bytes(),self.employee["id"])
        candidate=store.register_submission_asset("wuxiang","candidate.mp4",b"candidate",self.employee["id"])
        task=store.create_task({"project_id":"wuxiang","workflow_id":"WF-02","title":"P0 技术试点","why":"保留历史"},"u_system")
        with store.connect() as c:
            row=c.execute("SELECT payload FROM martial_master_versions WHERE master_id='cryn' AND version=2").fetchone()
            payload=store.parse(row["payload"],{});payload["portrait"]=visual["id"]
            c.execute("UPDATE martial_master_versions SET payload=? WHERE master_id='cryn' AND version=2",(store.dumps(payload),))
            c.execute("UPDATE assets SET id=? WHERE id=?",(martial_product.PILOT_CANDIDATE,candidate["id"]))
            candidate_id=martial_product.PILOT_CANDIDATE
            c.execute("UPDATE martial_moves SET task_id=? WHERE id=?",(task["id"],martial_product.PILOT_MOVE))
            stamp=store.now()
            c.execute("INSERT INTO martial_motion_refs(id,move_id,version,video_asset_id,start_time,end_time,orientation,start_pose,end_pose,key_moments,notes,status,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                      ("ref_pilot",martial_product.PILOT_MOVE,1,ref_asset["id"],0,5,"正面","起势","收势","[]","","locked",stamp))
            c.execute("INSERT INTO martial_packages(id,move_id,motion_ref_id,master_version,task_id,request_key,status,facts_hash,facts,result,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                      ("pkg_pilot",martial_product.PILOT_MOVE,"ref_pilot",2,task["id"],"pilot-package","complete","hash","{}","{}",stamp,stamp))
            c.execute("INSERT INTO martial_media_jobs(id,move_id,task_id,package_id,motion_ref_id,master_version,asset_type,provider,model_alias,model,prompt_hash,prompt,character_asset_id,duration,aspect_ratio,resolution,status,request_key,idempotency_key,candidate_asset_id,reserved_cost,quote_source,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                      (martial_product.PILOT_MEDIA,martial_product.PILOT_MOVE,task["id"],"pkg_pilot","ref_pilot",2,"teaching","runy","sd2.5","model","hash","prompt",visual["id"],5,"16:9","480p","succeeded","pilot-media","pilot-idempotency",candidate_id,3.6,"quote",stamp,stamp))
            c.execute("INSERT INTO martial_qc(id,media_job_id,move_id,stage,result,findings,reference_comparison,reviewer_id,created_at) VALUES(?,?,?,?,?,?,?,?,?)",
                      ("qc_pilot",martial_product.PILOT_MEDIA,martial_product.PILOT_MOVE,"martial","fail","返修","对照",self.employee["id"],stamp))
        result=martial_product.initialize_product_migration()
        self.assertGreater(result["technical_history_items"],0)
        detail=martial.move_detail(martial_product.PILOT_MOVE,self.employee)
        self.assertEqual(detail["effective_version"]["payload"]["chinese_name"],"第一式")
        self.assertEqual(detail["versions"][0]["payload"]["chinese_name"],"起式按掌")
        self.assertEqual(detail["motions"],[])
        self.assertEqual(detail["packages"],[])
        self.assertEqual(detail["media"],[])
        self.assertIsNone(detail["task"])
        summary=next(m for m in martial.overview(self.employee)["moves"] if m["id"]==martial_product.PILOT_MOVE)
        self.assertFalse(summary["motion_locked"])
        self.assertEqual(summary["candidate_count"],0)
        self.assertEqual(summary["qc_failed"],0)
        self.assertTrue(martial_product.asset_is_technical_history(candidate_id))
        with self.assertRaises(PermissionError):martial_product.audit_history(self.employee,martial_product.PILOT_MOVE)
        history=martial_product.audit_history(self.manager,martial_product.PILOT_MOVE)
        self.assertEqual(history["move_versions"][0]["payload"]["chinese_name"],"起式按掌")
        self.assertEqual(history["media"][0]["id"],martial_product.PILOT_MEDIA)
        self.assertEqual(history["qc"][0]["result"],"fail")
        with self.assertRaisesRegex(ValueError,"真人标准动作参考"):
            martial.request_package(self.employee,martial_product.PILOT_MOVE)
        with self.assertRaisesRegex(ValueError,"技术历史"):
            martial.link_motion(self.employee,martial_product.PILOT_MOVE,{"asset_id":ref_asset["id"]})

    def test_employee_standard_batch_import_copy_and_optional_motion_fields(self):
        martial_product.initialize_product_migration()
        current=martial.move_detail("mv_flowing_cloud_01",self.employee)
        before=current["move"]["current_version"]
        edited=martial_product.edit_move_standard(self.employee,"mv_flowing_cloud_01",
                                                   {"chinese_coaching":"请保持掌形稳定","base_version":before})
        self.assertTrue(edited["changed"])
        self.assertFalse(edited["needs_review"])
        self.assertEqual(edited["move"]["current_version"],before+1)
        self.assertEqual(edited["versions"][1]["version"],before)
        with self.assertRaisesRegex(ValueError,"已更新"):
            martial_product.edit_move_standard(self.employee,"mv_flowing_cloud_01",
                                               {"chinese_coaching":"旧页面","base_version":before})
        art=martial_product.create_art(self.employee,{"chinese_name":"测试新功法","category":"拳法"})
        art_id=art["id"]
        rows=[{"order":1,"chinese_name":"测试起势","chinese_action":"抬手","english_action":"Raise hand"},
              {"order":2,"chinese_name":"测试收势","chinese_action":"收手","english_action":"Lower hand"}]
        batch=martial_product.create_moves_batch(self.employee,art_id,{"moves":rows})
        self.assertEqual(batch["count"],2)
        with self.assertRaisesRegex(ValueError,"同名"):
            martial_product.create_moves_batch(self.employee,art_id,{"moves":[
                {"chinese_name":"临时","chinese_action":"动作","english_action":"Motion"},rows[0]]})
        self.assertEqual(len(martial.art_detail(art_id,self.employee)["moves"]),2)
        buf=io.StringIO();writer=csv.writer(buf)
        writer.writerow(["序号","中文名","英文名","中文动作","英文动作","教学提示"])
        writer.writerow([3,"测试转身","Turn","转身","Turn around","注意落脚"])
        imported=martial_product.import_moves(self.employee,art_id,{"name":"招式.csv",
            "base64":base64.b64encode(buf.getvalue().encode("utf-8-sig")).decode()})
        self.assertEqual(imported["count"],1)
        # Excel commonly stores headers in sharedStrings and data as inline text.
        sheet='''<?xml version="1.0" encoding="UTF-8"?><worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>
        <row r="1"><c r="A1" t="s"><v>0</v></c><c r="B1" t="s"><v>1</v></c><c r="C1" t="s"><v>2</v></c><c r="D1" t="s"><v>3</v></c></row>
        <row r="2"><c r="A2"><v>4</v></c><c r="B2" t="inlineStr"><is><t>测试挥掌</t></is></c><c r="C2" t="inlineStr"><is><t>挥掌</t></is></c><c r="D2" t="inlineStr"><is><t>Swing palm</t></is></c></row>
        </sheetData></worksheet>'''
        strings='''<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><si><t>序号</t></si><si><t>中文名</t></si><si><t>中文动作</t></si><si><t>英文动作</t></si></sst>'''
        stream=io.BytesIO()
        with zipfile.ZipFile(stream,"w",zipfile.ZIP_DEFLATED) as book:
            book.writestr("xl/worksheets/sheet1.xml",sheet)
            book.writestr("xl/sharedStrings.xml",strings)
        excel=martial_product.import_moves(self.employee,art_id,{"name":"招式.xlsx",
            "base64":base64.b64encode(stream.getvalue()).decode()})
        self.assertEqual(excel["count"],1)
        copied=martial_product.copy_moves(self.employee,art_id,
                                           {"source_art_id":"flowing_cloud","move_ids":["mv_flowing_cloud_01"]})
        self.assertEqual(copied["count"],1)
        video=(ROOT/"tests/fixtures"/"wuxiang"/"cryn-bagua-part01.mp4").read_bytes()
        cover=(ROOT/"tests/fixtures"/"wuxiang"/"cryn-character.jpg").read_bytes()
        uploaded=martial.upload_motion(self.employee,batch["move_ids"][0],
            {"upload":{"name":"motion.mp4","base64":base64.b64encode(video).decode(),
                       "cover_base64":base64.b64encode(cover).decode()}})
        ref=uploaded["motions"][0]
        self.assertEqual((ref["start_time"],ref["end_time"],ref["orientation"]),(0,15,"正面"))
        self.assertEqual(ref["key_moments"],[])
        self.assertEqual(ref["frame_orientation"],"横屏")
        self.assertTrue(ref["cover_asset_id"])
        trimmed=martial.update_motion_range(self.employee,ref["id"],{"start_time":2,"end_time":4,
                    "key_moments":[{"time":3.25,"label":"转身"}]})["motions"][0]
        self.assertEqual((trimmed["start_time"],trimmed["end_time"]),(2,4))
        self.assertEqual(trimmed["key_moments"],[{"time":3.25,"label":"转身"}])
        martial.confirm_motion(self.employee,ref["id"])
        with self.assertRaisesRegex(ValueError,"尚未确认"):
            martial.update_motion_range(self.employee,ref["id"],{"start_time":0})

    def test_initial_action_standard_unblocks_current_beginner_move(self):
        martial_product.initialize_product_migration()
        move_id="mv_beginner_mabu_chongquan"
        with store.connect() as c:
            # Older installs can have an imported first version awaiting activation.
            c.execute("UPDATE martial_moves SET current_version=0,draft_version=1 WHERE id=?",(move_id,))
            before=c.execute("SELECT current_version,draft_version FROM martial_moves WHERE id=?",(move_id,)).fetchone()
        self.assertEqual(before["current_version"],0)
        result=martial_product.edit_move_standard(self.employee,move_id,{"base_version":before["draft_version"],
            "chinese_action":"马步稳定，直线冲拳","english_action":"Hold a horse stance and punch straight"})
        self.assertFalse(result["needs_review"])
        self.assertGreater(result["move"]["current_version"],0)
        self.assertEqual(result["move"]["draft_version"],0)
        self.assertEqual(result["effective_version"]["payload"]["chinese_action"],"马步稳定，直线冲拳")
        with store.connect() as c:
            self.assertIsNotNone(c.execute("SELECT 1 FROM martial_move_versions WHERE move_id=? AND version=?",
                                           (move_id,before["draft_version"])).fetchone())


if __name__=="__main__":unittest.main()
