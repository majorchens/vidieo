"""AI Studio keeps provider requests idempotent and project results isolated."""
from __future__ import annotations

import base64
import http.client
import json
import os
import sys
import tempfile
import threading
import unittest
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"app"))

import ai_studio
import server
import store


class AIStudioTest(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        store.DATA=Path(self.temp.name);store.DB=store.DATA/"work_os.sqlite3"
        for project in ("wuxiang","diaojianghu","dingting"):
            store.PROJECT_ROOTS[project]=store.DATA/project
            store.PROJECT_ROOTS[project].mkdir()
        store.SHARED_ROOT=store.DATA/"shared";store.SHARED_ROOT.mkdir()
        store.initialize();ai_studio.initialize()
        self.manager_id=store.create_user("manager-ai","管理者","manager","manager-password-ai")
        self.employee_id=store.create_user("employee-ai","员工","employee","employee-password-ai")
        self.manager={"id":self.manager_id,"role":"manager"}
        self.employee={"id":self.employee_id,"role":"employee"}
        with store.connect() as c:
            c.execute("CREATE TABLE martial_packages(status TEXT)")
            c.execute("INSERT INTO martial_packages(status) VALUES('complete')")

    def tearDown(self):
        self.temp.cleanup()

    def test_text_result_save_to_project_and_turn_into_task(self):
        queued=ai_studio.create_run(self.manager,{"capability":"write","prompt":"写一段简短内容"})["artifact"]
        self.assertIsNone(queued["project_id"])
        self.assertEqual(queued["status"],"queued")
        spec=ai_studio.connector_claim(queued["id"])
        self.assertEqual(spec["model"],"deepseek-v4.1-flash")
        result=ai_studio.connector_report(queued["id"],{"status":"completed","model":"deepseek-v4.1-flash",
            "provider_job_id":"response-one","output":{"title":"草稿","text":"可使用的正文"},
            "usage":{"prompt_tokens":50,"completion_tokens":20}})
        self.assertEqual(result["status"],"completed")
        saved=ai_studio.save_to_project(self.manager,queued["id"],{"project_id":"diaojianghu"})
        self.assertEqual(saved["artifact"]["project_id"],"diaojianghu")
        task=ai_studio.turn_into_task(self.manager,queued["id"],{"title":"钓江湖文案后续制作"})
        with store.connect() as c:
            row=c.execute("SELECT workflow_id,project_id,input_assets FROM tasks WHERE id=?",(task["task_id"],)).fetchone()
            self.assertEqual((row["workflow_id"],row["project_id"]),("WF-AI","diaojianghu"))
            self.assertEqual(store.parse(row["input_assets"]),[saved["asset_id"]])

    def test_employee_cannot_use_or_save_into_unassigned_project(self):
        with self.assertRaises(PermissionError):
            ai_studio.create_run(self.employee,{"capability":"write","project_id":"diaojianghu","prompt":"写短文"})
        free=ai_studio.create_run(self.employee,{"capability":"write","prompt":"写短文"})["artifact"]
        ai_studio.connector_claim(free["id"])
        ai_studio.connector_report(free["id"],{"status":"completed","model":"deepseek-v4.1-flash",
            "output":{"text":"正文"},"usage":{}})
        with self.assertRaises(PermissionError):
            ai_studio.save_to_project(self.employee,free["id"],{"project_id":"diaojianghu"})
        with self.assertRaises(PermissionError):ai_studio.artifact_detail({"id":"other","role":"employee"},free["id"])

    def test_video_unknown_submission_never_creates_second_paid_job(self):
        with store.connect() as c:
            c.execute("CREATE TABLE martial_routes(model_alias TEXT,provider TEXT,model TEXT,checked_at TEXT)")
            c.execute("INSERT INTO martial_routes VALUES(?,?,?,?)",("sd2.5","runy","doubao-seedance-2-5",store.now()))
        data={"capability":"video","project_id":"wuxiang","prompt":"一段湖边晨景","model_alias":"sd2.5",
              "duration":5,"ratio":"16:9","count":1,"budget_cap":3.6}
        first=ai_studio.create_run(self.manager,data)["artifact"]
        again=ai_studio.create_run(self.manager,data)["artifact"]
        self.assertEqual(first["id"],again["id"])
        ai_studio.connector_claim(first["id"])
        ai_studio.connector_report(first["id"],{"status":"unknown_submission","local_media_id":"m-1",
            "local_job_id":"j-1","error":"提交待核实"})
        with self.assertRaises(ValueError):ai_studio.create_run(self.manager,data)
        fake_video=b"\x00\x00\x00\x18ftypisom"+b"fake-test-video"
        ai_studio.connector_report(first["id"],{"status":"completed","video_base64":base64.b64encode(fake_video).decode(),
            "local_media_id":"m-1","local_job_id":"j-1","provider_job_id":"upstream-1"})
        self.assertEqual(ai_studio.result_file(self.manager,first["id"]).read_bytes(),fake_video)
        self.assertEqual(ai_studio.artifact_detail(self.manager,first["id"])["artifact"]["status"],"completed")
        ai_studio.set_route(self.manager,{"business_model":"sd2.5","primary_provider":"runy","enabled":False})
        self.assertEqual(ai_studio.overview(self.manager)["capabilities"]["video"]["status"],"TESTED")
        with self.assertRaises(ValueError):ai_studio.create_run(self.manager,{**data,"prompt":"另一个视频"})

    def test_over_40mb_video_streams_back_and_saves_to_project(self):
        with store.connect() as c:
            c.execute("CREATE TABLE martial_routes(model_alias TEXT,provider TEXT,model TEXT,checked_at TEXT)")
            c.execute("INSERT INTO martial_routes VALUES(?,?,?,?)",("sd2.5","runy","doubao-seedance-2-5",store.now()))
        item=ai_studio.create_run(self.manager,{"capability":"video","project_id":"wuxiang",
            "prompt":"测试视频文件回传","model_alias":"sd2.5","duration":5,"ratio":"16:9",
            "count":1,"budget_cap":3.6})["artifact"]
        ai_studio.connector_claim(item["id"])
        source=store.DATA/"large-ai-result.mp4"
        with source.open("wb") as output:
            output.write(b"\x00\x00\x00\x18ftypisom")
            output.truncate(52_000_000)
        sha=store.digest_file(source)
        previous=os.environ.get("YOODUN_CONNECTOR_TOKEN")
        os.environ["YOODUN_CONNECTOR_TOKEN"]="ai-stream-test-token"
        httpd=server.ThreadingHTTPServer(("127.0.0.1",0),server.Handler)
        thread=threading.Thread(target=httpd.serve_forever,daemon=True);thread.start()
        try:
            conn=http.client.HTTPConnection("127.0.0.1",httpd.server_port,timeout=90)
            conn.putrequest("POST",f"/api/ai-studio/connector/jobs/{item['id']}/video-file")
            for key,value in {"Authorization":"Bearer ai-stream-test-token","Content-Type":"video/mp4",
                              "Content-Length":str(source.stat().st_size),"X-File-SHA256":sha}.items():
                conn.putheader(key,value)
            conn.endheaders()
            with source.open("rb") as stream:
                for chunk in iter(lambda:stream.read(1024*1024),b""):
                    conn.send(chunk)
            response=conn.getresponse();result=json.loads(response.read());conn.close()
            self.assertEqual(response.status,201,result)
            self.assertEqual(result["status"],"staged")
        finally:
            httpd.shutdown();httpd.server_close();thread.join(timeout=5)
            if previous is None:os.environ.pop("YOODUN_CONNECTOR_TOKEN",None)
            else:os.environ["YOODUN_CONNECTOR_TOKEN"]=previous
        completed=ai_studio.connector_report(item["id"],{"status":"completed","video_sha256":sha,
            "local_media_id":"media-one","local_job_id":"ledger-one","provider_job_id":"upstream-one"})
        self.assertEqual(completed["status"],"completed")
        result_path=ai_studio.result_file(self.manager,item["id"])
        self.assertEqual(result_path.stat().st_size,52_000_000)
        self.assertEqual(store.digest_file(result_path),sha)
        self.assertEqual(ai_studio.video_upload_state(item["id"],sha)["status"],"already_completed")
        saved=ai_studio.save_to_project(self.manager,item["id"],{"project_id":"wuxiang"})
        with store.connect() as c:asset=store.record(c,"assets",saved["asset_id"])
        self.assertEqual(Path(asset["storage_ref"]).stat().st_size,52_000_000)
        self.assertEqual(asset["sha256"],sha)

    def test_image_route_requires_activation_and_preserves_one_paid_job(self):
        data={"capability":"image","project_id":"wuxiang","prompt":"蓝色圆形图标",
              "width":2560,"height":1440,"count":1,"budget_cap":0.172}
        self.assertEqual(ai_studio.overview(self.manager)["capabilities"]["image"]["status"],"DISABLED")
        with self.assertRaises(ValueError):ai_studio.create_run(self.manager,data)
        ai_studio.set_route(self.manager,{"business_model":"image","primary_provider":"wanjie",
                                          "model":"jimeng_t2i_v40","enabled":True})
        self.assertEqual(ai_studio.overview(self.manager)["capabilities"]["image"]["status"],"CONFIGURED")
        first=ai_studio.create_run(self.manager,data)["artifact"]
        self.assertEqual(first["model"],"jimeng_t2i_v40")
        self.assertEqual(ai_studio.create_run(self.manager,data)["artifact"]["id"],first["id"])
        spec=ai_studio.connector_claim(first["id"])
        self.assertEqual(spec["workflow_project"],"万象武境编剧")
        self.assertEqual(spec["quote_source"],ai_studio.IMAGE_PRICE_SOURCE)
        ai_studio.connector_report(first["id"],{"status":"unknown_submission","local_job_id":"ledger-1",
                                                 "local_media_id":"media-1","error":"供应商结果未知"})
        with self.assertRaisesRegex(ValueError,"不能再次付费提交"):
            ai_studio.create_run(self.manager,data)
        image=base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII=")
        ai_studio.connector_report(first["id"],{"status":"completed","model":"jimeng_t2i_v40",
            "image_base64":base64.b64encode(image).decode(),"local_job_id":"ledger-1",
            "local_media_id":"media-1","provider_job_id":"upstream-1"})
        self.assertEqual(ai_studio.result_file(self.manager,first["id"]).read_bytes(),image)
        detail=ai_studio.artifact_detail(self.manager,first["id"])["artifact"]
        self.assertEqual(detail["output"]["images"][0]["format"],"png")
        self.assertEqual(ai_studio.overview(self.manager)["capabilities"]["image"]["status"],"CONFIGURED")
        with self.assertRaisesRegex(ValueError,"缺少已核对账单"):
            ai_studio.connector_report(first["id"],{"status":"completed","actual_cost":0.172,
                "local_media_id":"changed","provider_job_id":"upstream-1","billing_source":"账单"})
        ai_studio.connector_report(first["id"],{"status":"completed","actual_cost":0.172,
            "local_media_id":"media-1","provider_job_id":"upstream-1","billing_source":"万界已核账单"})
        self.assertEqual(ai_studio.overview(self.manager)["capabilities"]["image"]["status"],"PRODUCTION_READY")
        saved=ai_studio.save_to_project(self.manager,first["id"],{"project_id":"wuxiang"})
        self.assertTrue(saved["asset_id"])

    def test_image_reference_is_rejected_until_input_adapter_exists(self):
        ai_studio.set_route(self.manager,{"business_model":"image","primary_provider":"wanjie","enabled":True})
        with self.assertRaisesRegex(ValueError,"仅验证文生图"):
            ai_studio.create_run(self.manager,{"capability":"image","project_id":"wuxiang",
                                               "prompt":"角色图","asset_ids":["some-asset"]})
        with self.assertRaisesRegex(ValueError,"仅支持已报价"):
            ai_studio.create_run(self.manager,{"capability":"image","project_id":"wuxiang",
                                               "prompt":"角色图","width":1024})

    def test_secret_filter_blocks_model_submission(self):
        bad={"name":"notes.txt","base64":base64.b64encode(b"password=example-secret-value").decode()}
        with self.assertRaises(ValueError):
            ai_studio.create_run(self.manager,{"capability":"analyze","prompt":"总结","file":bad})
        with store.connect() as c:
            self.assertEqual(c.execute("SELECT COUNT(*) FROM ai_studio_artifacts").fetchone()[0],0)


if __name__=="__main__":unittest.main()
