"""The original motion upload accepts real videos beyond the old JSON limit."""
from __future__ import annotations

import base64
import http.client
import json
import os
import shutil
import struct
import sys
import tempfile
import threading
import time
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse
from unittest import mock

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"app"))

import martial
import server
import store


class MotionStreamUploadTest(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        store.DATA=Path(self.temp.name)
        store.DB=store.DATA/"work_os.sqlite3"
        self.old_connector_token=os.environ.get("YOODUN_CONNECTOR_TOKEN")
        os.environ["YOODUN_CONNECTOR_TOKEN"]="stream-upload-test-signing-key"
        project=store.DATA/"project";project.mkdir()
        store.PROJECT_ROOTS["wuxiang"]=project
        store.SHARED_ROOT=store.DATA/"shared";store.SHARED_ROOT.mkdir()
        store.initialize();martial.initialize()
        self.user_id=store.create_user("motiontester","动作测试员工","employee","test-password-123")
        with store.connect() as c:
            c.execute("INSERT INTO martial_specialists(user_id,project_id,title,created_at) VALUES(?,?,?,?)",
                      (self.user_id,"wuxiang","武术数字资产内容设计",store.now()))
        self.move_id="mv_flowing_cloud_01"
        manager_id=store.create_user("motionmanager","动作测试负责人","manager","manager-password-test")
        manager={"id":manager_id,"role":"manager"}
        employee={"id":self.user_id,"role":"employee"}
        martial.approve_art(manager,"flowing_cloud")
        martial.submit_move(employee,self.move_id,{"note":"测试动作已核对"})
        martial.approve_move(manager,self.move_id)
        self.server=ThreadingHTTPServer(("127.0.0.1",0),server.Handler)
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()

    def tearDown(self):
        self.server.shutdown();self.server.server_close();self.thread.join(timeout=5)
        if self.old_connector_token is None:os.environ.pop("YOODUN_CONNECTOR_TOKEN",None)
        else:os.environ["YOODUN_CONNECTOR_TOKEN"]=self.old_connector_token
        self.temp.cleanup()

    def request(self,method,path,body=None,headers=None):
        conn=http.client.HTTPConnection("127.0.0.1",self.server.server_port,timeout=20)
        conn.request(method,path,body=body,headers=headers or {})
        response=conn.getresponse()
        payload=response.read();status=response.status
        cookie=response.getheader("Set-Cookie")
        conn.close()
        return status,json.loads(payload),cookie

    def login(self):
        status,body,cookie=self.request("POST","/api/login",
            json.dumps({"username":"motiontester","password":"test-password-123"}),
            {"Content-Type":"application/json"})
        self.assertEqual(status,200)
        return body["csrf"],cookie.split(";",1)[0]

    def test_over_40mb_streams_to_disk_and_links_with_matching_hash(self):
        csrf,cookie=self.login()
        staged=store.DATA/"large.mp4"
        shutil.copyfile(ROOT/"tests/fixtures"/"wuxiang"/"cryn-bagua-part01.mp4",staged)
        target=52_000_000
        free_size=target-staged.stat().st_size
        with staged.open("ab") as output:
            output.write(struct.pack(">I4s",free_size,b"free"))
            output.truncate(target)
        expected=store.digest_file(staged)
        conn=http.client.HTTPConnection("127.0.0.1",self.server.server_port,timeout=60)
        conn.putrequest("POST",f"/api/martial/moves/{self.move_id}/motion-file")
        for key,value in {"Content-Type":"video/mp4","Content-Length":str(target),
                          "X-File-Name":"horse-stance.mp4","X-File-SHA256":expected,
                          "X-CSRF-Token":csrf,"Cookie":cookie}.items():
            conn.putheader(key,value)
        conn.endheaders()
        with mock.patch("martial.shutil.which",return_value=None):
            with staged.open("rb") as source:
                for chunk in iter(lambda:source.read(1024*1024),b""):
                    conn.send(chunk)
            response=conn.getresponse();body=json.loads(response.read())
        self.assertEqual(response.status,201,body)
        conn.close()
        self.assertEqual(len(body["motions"]),1)
        reference=body["motions"][0]
        self.assertEqual(reference["duration"],15.0)
        self.assertIsNone(reference["cover_asset_id"])
        cover=base64.b64encode((ROOT/"tests/fixtures"/"wuxiang"/"cryn-character.jpg").read_bytes()).decode()
        status,attached,_=self.request("POST",f"/api/martial/motions/{reference['id']}/cover",
            json.dumps({"cover_base64":cover}),
            {"Content-Type":"application/json","X-CSRF-Token":csrf,"Cookie":cookie})
        self.assertEqual(status,200,attached)
        cover_id=attached["motions"][0]["cover_asset_id"]
        self.assertTrue(cover_id)
        status,repeated,_=self.request("POST",f"/api/martial/motions/{reference['id']}/cover",
            json.dumps({"cover_base64":cover}),
            {"Content-Type":"application/json","X-CSRF-Token":csrf,"Cookie":cookie})
        self.assertEqual(status,200,repeated)
        self.assertEqual(repeated["motions"][0]["cover_asset_id"],cover_id)
        with store.connect() as c:
            before=(c.execute("SELECT COUNT(*) FROM assets").fetchone()[0],
                    c.execute("SELECT COUNT(*) FROM martial_motion_refs WHERE move_id=?",(self.move_id,)).fetchone()[0])
        conn=http.client.HTTPConnection("127.0.0.1",self.server.server_port,timeout=60)
        conn.putrequest("POST",f"/api/martial/moves/{self.move_id}/motion-file")
        for key,value in {"Content-Type":"video/mp4","Content-Length":str(target),
                          "X-File-Name":"horse-stance.mp4","X-File-SHA256":expected,
                          "X-CSRF-Token":csrf,"Cookie":cookie}.items():
            conn.putheader(key,value)
        conn.endheaders()
        with staged.open("rb") as source:
            for chunk in iter(lambda:source.read(1024*1024),b""):
                conn.send(chunk)
        duplicate=conn.getresponse();duplicate_body=json.loads(duplicate.read());conn.close()
        self.assertEqual(duplicate.status,201,duplicate_body)
        self.assertEqual(duplicate_body["motions"][0]["id"],reference["id"])
        with store.connect() as c:
            after=(c.execute("SELECT COUNT(*) FROM assets").fetchone()[0],
                   c.execute("SELECT COUNT(*) FROM martial_motion_refs WHERE move_id=?",(self.move_id,)).fetchone()[0])
        self.assertEqual(after,before)
        with store.connect() as c:
            asset=store.record(c,"assets",reference["video_asset_id"])
        self.assertEqual(asset["type"],"motion_reference")
        self.assertEqual(asset["sha256"],expected)
        self.assertEqual(Path(asset["storage_ref"]).stat().st_size,target)
        self.assertEqual(martial._probe_video_file(Path(asset["storage_ref"]))["duration"],15.0)
        self.assertFalse(list((store.DATA/"uploads"/"wuxiang").glob(".motion-*")))
        signed=martial.source_url(asset["id"],int(time.time())+60)
        parsed=urlparse(signed)
        conn=http.client.HTTPConnection("127.0.0.1",self.server.server_port,timeout=20)
        conn.request("GET",parsed.path.removeprefix("/work-os")+"?"+parsed.query,
                     headers={"Range":"bytes=10000000-10000015"})
        preview=conn.getresponse()
        self.assertEqual(preview.status,206)
        self.assertEqual(preview.getheader("Content-Range"),f"bytes 10000000-10000015/{target}")
        with staged.open("rb") as original:
            original.seek(10000000)
            self.assertEqual(preview.read(),original.read(16))
        conn.close()

    def test_auth_csrf_and_size_preflight_before_body_read(self):
        csrf,cookie=self.login()
        path=f"/api/martial/moves/{self.move_id}/motion-file"
        headers={"Content-Type":"video/mp4","Content-Length":"10",
                 "X-File-Name":"motion.mp4","Cookie":cookie}
        status,body,_=self.request("POST",path,b"0123456789",headers)
        self.assertEqual(status,403,body)
        headers["X-CSRF-Token"]=csrf
        headers["Content-Length"]=str(store.MOTION_FILE_LIMIT+1)
        status,body,_=self.request("POST",path,b"",headers)
        self.assertEqual(status,413,body)
        self.assertFalse((store.DATA/"uploads"/"wuxiang").exists())


if __name__=="__main__":unittest.main()
