"""Local reference clipping and full-video assembly require real media probes."""
from __future__ import annotations

import json
import io
import shutil
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"app"))
import martial
import martial_connector as connector


class ReferenceClipTest(unittest.TestCase):
    JID="mj_0123456789abcdef"

    @classmethod
    def setUpClass(cls):
        cls.source=ROOT/"tests/fixtures/wuxiang/cryn-bagua-part01.mp4"
        try:cls.ffmpeg=connector._ffmpeg_binary({})
        except RuntimeError:raise unittest.SkipTest("FFmpeg is unavailable")

    def test_selected_interval_is_clipped_and_original_is_preserved(self):
        original_hash=connector._file_sha256(self.source)
        with tempfile.TemporaryDirectory() as temp:
            segment={"index":0,"source_start":1,"source_end":14.7}
            clip=connector._clip_source(self.source,segment,Path(temp),self.JID,self.ffmpeg)
            self.assertAlmostEqual(clip["duration"],13.7,delta=0.2)
            self.assertLess(clip["size"],connector.MAX_REFERENCE_CLIP_FILE)
            self.assertNotEqual(clip["sha256"],original_hash)
            self.assertEqual(connector._file_sha256(self.source),original_hash)
            self.assertEqual(connector._technical(clip["path"])["result"],"pass")

    def test_single_segment_is_trimmed_to_fractional_final_duration(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            with patch.object(connector,"WORKFLOW",root):
                folder=root/"tasks"/("a"*16)/"media";folder.mkdir(parents=True)
                generated=folder/"generated.mp4"
                shutil.copyfile(self.source,generated)
                spec={"duration":15,"video_plan":{"target_duration":13.7}}
                result=connector._assemble_segments([generated],[{"duration":15}],spec,
                                                    "a"*16,self.JID,self.ffmpeg)
                self.assertAlmostEqual(martial._probe_video_file(result)["duration"],13.7,delta=0.15)

    def test_two_segments_join_to_one_candidate(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            with patch.object(connector,"WORKFLOW",root):
                folder=root/"tasks"/("a"*16)/"media";folder.mkdir(parents=True)
                paths=[]
                for i in range(2):
                    part=connector._clip_source(self.source,{"index":i,"source_start":i*5,
                            "source_end":(i+1)*5},root/"scratch",self.JID,self.ffmpeg)
                    target=folder/f"segment-{i}.mp4";shutil.copyfile(part["path"],target);paths.append(target)
                spec={"duration":10,"video_plan":{"target_duration":10}}
                result=connector._assemble_segments(paths,[{"duration":5},{"duration":5}],spec,
                                                    "a"*16,self.JID,self.ffmpeg)
                self.assertAlmostEqual(martial._probe_video_file(result)["duration"],10,delta=0.15)

    def test_rejects_unpriced_or_duplicate_segment_specs(self):
        base={"generation_mode":"complete","duration":33,"resolution":"480p","ratio":"16:9",
              "reserved_cost":40,"quote_source":"verified estimate","video_urls":[],
              "segments":[{"index":0,"source_start":0,"source_end":20,"duration":20,
                           "reserved_cost":24,"request_key":"one"},
                          {"index":1,"source_start":20,"source_end":33,"duration":13,
                           "reserved_cost":16,"request_key":"two"}]}
        self.assertEqual(len(connector._media_segments(base)),2)
        invalid=json.loads(json.dumps(base));invalid["segments"][1]["request_key"]="one"
        with self.assertRaisesRegex(ValueError,"幂等编号"):
            connector._media_segments(invalid)
        invalid=json.loads(json.dumps(base));invalid["video_urls"]=["https://example/whole.mp4"]
        with self.assertRaisesRegex(ValueError,"禁止把原片"):
            connector._media_segments(invalid)

    def test_signed_clip_url_includes_deployment_prefix(self):
        base="https://ai.duodianqian.cn/work-os"
        valid=base+f"/api/martial/clip/{self.JID}/0?exp=9999999999&sig=abc"
        self.assertEqual(connector._signed_media_url(valid,base,
                         f"/api/martial/clip/{self.JID}/0"),valid)
        with self.assertRaises(ValueError):
            connector._signed_media_url("https://ai.duodianqian.cn/api/martial/clip/"+
                         self.JID+"/0?sig=abc",base,f"/api/martial/clip/{self.JID}/0")

    def test_upload_checks_server_hash_and_reuses_signed_clip(self):
        base="https://ai.duodianqian.cn/work-os"
        url=base+f"/api/martial/clip/{self.JID}/0?exp={int(time.time())+3600}&sig=abc"
        jid=self.JID
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/"clip.mp4";shutil.copyfile(self.source,path)
            sha=connector._file_sha256(path)
            clipped={"path":path,"duration":15,"size":path.stat().st_size,"sha256":sha}
            class Response(io.BytesIO):
                def __init__(self):
                    super().__init__(json.dumps({"video_url":url,"sha256":sha,"duration":15}).encode())
            class Opener:
                def open(self,request,timeout):
                    if request.full_url!=base+f"/api/martial/connector/clips/{jid}/0":
                        raise AssertionError("wrong upload URL")
                    return Response()
            with patch.object(connector.urllib.request,"build_opener",return_value=Opener()):
                self.assertEqual(connector._upload_reference_clip(base,"token",self.JID,
                                                                 {"index":0},clipped),url)
            with patch.object(connector.urllib.request,"build_opener",side_effect=AssertionError("duplicate upload")):
                self.assertEqual(connector._upload_reference_clip(base,"token",self.JID,
                                                                 {"index":0},clipped),url)


if __name__=="__main__":unittest.main()
