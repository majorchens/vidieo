"""The image connector resumes the original Company Workflow media ID."""
from __future__ import annotations

import base64
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"app"))
import ai_studio_connector as connector


class ImageConnectorTest(unittest.TestCase):
    def test_submit_once_then_recover_original_image(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            image=base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII=")
            output=root/"tasks"/"ledger-1"/"media"/"media-1.png"
            output.parent.mkdir(parents=True);output.write_bytes(image)
            current={"status":"queued","local_job_id":None,"local_media_id":None,
                     "provider_job_id":None}
            submissions=[];reports=[]

            def claim(*_args,**_kwargs):
                return {"id":"aia_0123456789abcdef","capability":"image","request_key":"image-key",
                        "provider":"wanjie","model":"jimeng_t2i_v40","workflow_project":"万象武境编剧",
                        "reserved_cost":0.172,"quote_source":"historical-price",
                        "inputs":{"prompt":"蓝色圆形图标","requirements":"留白","width":2560,"height":1440,"count":1},
                        **current}

            def ledger(method,path,payload=None):
                if method=="POST" and path=="/api/jobs":return {"id":"ledger-1"}
                if method=="GET" and path.startswith("/api/media?"):
                    return {"id":"media-1","status":"succeeded","upstream_id":"upstream-1",
                            "response":{"local_files":[str(output)]}}
                raise AssertionError((method,path,payload))

            def submit(payload):
                submissions.append(payload)
                return {"id":"media-1","status":"submitted","upstream_id":"upstream-1","response":{}}

            def report(_url,_token,_jid,body):
                reports.append(body)
                current["status"]=body["status"]
                current["local_job_id"]=body.get("local_job_id")
                current["local_media_id"]=body.get("local_media_id")
                current["provider_job_id"]=body.get("provider_job_id")

            item={"id":"aia_0123456789abcdef","capability":"image","request_key":"image-key"}
            with patch.object(connector,"WORKFLOW",root),patch.object(connector,"request",side_effect=claim),\
                 patch.object(connector,"workflow_request",side_effect=ledger),\
                 patch.object(connector,"_submit_workflow_media",side_effect=submit),\
                 patch.object(connector,"_report",side_effect=report):
                connector._image_job("https://example.invalid","private-token",item,root)
                self.assertEqual(current["status"],"submitted")
                connector._image_job("https://example.invalid","private-token",item,root)

            self.assertEqual(len(submissions),1)
            self.assertEqual(submissions[0]["kind"],"image")
            self.assertEqual(submissions[0]["model"],"jimeng_t2i_v40")
            self.assertEqual(reports[-1]["status"],"completed")
            self.assertEqual(reports[-1]["local_media_id"],"media-1")
            self.assertEqual(base64.b64decode(reports[-1]["image_base64"]),image)


if __name__=="__main__":unittest.main()
