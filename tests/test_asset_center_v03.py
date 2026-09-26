"""V0.3 asset taxonomy, context filters and legacy read authorization."""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"app"))
import asset_center
import legacy_asset_bridge
import store


class AssetCenterV03Test(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.old_data,self.old_db,self.old_project,self.old_shared=store.DATA,store.DB,dict(store.PROJECT_ROOTS),store.SHARED_ROOT
        store.DATA=Path(self.temp.name)/"work";store.DB=store.DATA/"work_os.sqlite3"
        source=Path(self.temp.name)/"project";source.mkdir()
        store.PROJECT_ROOTS["wuxiang"]=source
        store.SHARED_ROOT=Path(self.temp.name)/"shared";store.SHARED_ROOT.mkdir()
        store.initialize();asset_center.initialize();legacy_asset_bridge.initialize()
        owner_id=store.create_user("asset-v03-owner","员工","employee","asset-v03-owner-password")
        other_id=store.create_user("asset-v03-other","其他员工","employee","asset-v03-other-password")
        outsider_id=store.create_user("asset-v03-outside","项目外员工","employee","asset-v03-outside-password")
        self.owner={"id":owner_id,"username":"asset-v03-owner","role":"employee"}
        self.other={"id":other_id,"username":"asset-v03-other","role":"employee"}
        self.outsider={"id":outsider_id,"username":"asset-v03-outside","role":"employee"}
        self.manager={"id":"u_system","username":"_system","role":"manager"}
        self.owner_task=store.create_task({"project_id":"wuxiang","workflow_id":"WF-02","title":"员工任务","why":"关联资产","assignee_id":owner_id},"u_system")
        self.other_task=store.create_task({"project_id":"wuxiang","workflow_id":"WF-02","title":"其他任务","why":"隔离","assignee_id":other_id},"u_system")

    def tearDown(self):
        store.DATA,store.DB,store.PROJECT_ROOTS,store.SHARED_ROOT=self.old_data,self.old_db,self.old_project,self.old_shared
        self.temp.cleanup()

    def register(self,original: str,typ: str,subtype: str="",**kwargs) -> str:
        with store.connect() as c:
            return asset_center._register(c,{"source_system":kwargs.pop("source_system","v03_fixture"),
                "original_id":original,"project_id":kwargs.pop("project_id","wuxiang"),
                "type":typ,"subtype":subtype,"name":original,"status":"active",
                "metadata":kwargs.pop("metadata",{}),**kwargs})[0]

    def test_all_requested_categories_and_old_generic_categories(self):
        cases=[
            ("character","image","character","角色"),
            ("motion","video","martial_motion","真人动作"),
            ("ai-image","image","ai_result","AI图片"),
            ("ai-video","video","digital_teacher_video","AI视频"),
            ("teaching","video","teaching_video","教学视频"),
            ("practice","video","practice_video","演练视频"),
            ("voice","audio","voice_preview","Voice"),
            ("voice-audition","audio","voice_audition","Voice"),
            ("voice-reference","audio","voice_reference","Voice"),
            ("os","audio","os_audio","OS语音"),
            ("bgm","audio","training_bgm","BGM"),
            ("sfx","audio","sound_effect","音效"),
            ("prompt","prompt","generation_prompt","Prompt"),
            ("document","document","","文档"),
            ("scene","image","scene","场景"),
            ("prop","image","prop","道具"),
            ("plain-image","image","","图片"),
            ("plain-video","video","","视频"),
            ("plain-audio","audio","","音频"),
        ]
        for original,typ,subtype,_ in cases:self.register(original,typ,subtype)
        result=asset_center.list_assets(self.owner)
        self.assertEqual({a["name"]:a["category"] for a in result["assets"]},
                         {original:category for original,_,_,category in cases})
        for original,_,_,category in cases:
            found=asset_center.list_assets(self.owner,{"category":category})["assets"]
            self.assertIn(original,{asset["name"] for asset in found})
        self.assertEqual(asset_center.list_assets(self.owner,{"category":"数字老师视频"})["total"],3)

    def test_existing_registry_link_table_adds_campaign_column(self):
        with store.connect() as c:
            c.execute("DROP TABLE asset_registry_links")
            c.execute("""CREATE TABLE asset_registry_links(
                id TEXT PRIMARY KEY,asset_id TEXT,action TEXT,project_id TEXT,art_id TEXT,
                master_id TEXT,move_id TEXT,task_id TEXT,created_by TEXT,created_at TEXT)""")
        asset_center.initialize()
        with store.connect() as c:
            columns={row[1] for row in c.execute("PRAGMA table_info(asset_registry_links)")}
        self.assertIn("campaign_id",columns)

    def test_explicit_context_links_are_queryable_without_inventing_source_context(self):
        with store.connect() as c:
            c.executescript("""
                CREATE TABLE martial_masters(id TEXT PRIMARY KEY,name TEXT);
                CREATE TABLE martial_arts(id TEXT PRIMARY KEY,chinese_name TEXT,master_id TEXT);
                CREATE TABLE martial_moves(id TEXT PRIMARY KEY,martial_art_id TEXT,current_version INTEGER);
                CREATE TABLE martial_move_versions(move_id TEXT,version INTEGER,payload TEXT);
                INSERT INTO martial_masters VALUES('master-one','老师');
                INSERT INTO martial_arts VALUES('art-one','功法','master-one');
                INSERT INTO martial_moves VALUES('move-one','art-one',1);
                INSERT INTO martial_move_versions VALUES('move-one',1,'{"chinese_name":"招式"}');
            """)
        aid=self.register("linked-image","image")
        result=asset_center.reuse(self.manager,aid,{"action":"associate","project_id":"wuxiang",
            "art_id":"art-one","master_id":"master-one","move_id":"move-one",
            "task_id":self.owner_task["id"],"campaign_id":"campaign-autumn"})
        self.assertEqual(result["campaign_id"],"campaign-autumn")
        for key,value in (("project_id","wuxiang"),("art_id","art-one"),("master_id","master-one"),
                          ("move_id","move-one"),("task_id",self.owner_task["id"]),
                          ("campaign_id","campaign-autumn")):
            self.assertEqual(asset_center.list_assets(self.owner,{key:value})["total"],1)
        item=asset_center.detail(self.owner,aid)["asset"]
        self.assertEqual(item["associations"]["campaign_ids"],["campaign-autumn"])
        self.assertEqual(item["associations"]["move_ids"],["move-one"])
        self.assertEqual(asset_center.list_assets(self.owner,{"q":"招式"})["total"],1)
        with self.assertRaises(PermissionError):
            asset_center.reuse(self.owner,aid,{"action":"associate","project_id":"wuxiang",
                                               "task_id":self.other_task["id"]})
        with self.assertRaises(PermissionError):
            asset_center.reuse(self.owner,aid,{"action":"associate","project_id":"wuxiang",
                                               "campaign_id":"campaign-other"})
        with self.assertRaises(ValueError):
            asset_center.reuse(self.manager,aid,{"action":"associate","project_id":"wuxiang",
                                                 "art_id":"art-one","move_id":"missing-move"})

    def test_registered_bgm_and_tts_links_flow_into_registry(self):
        bgm=store.register_submission_asset("wuxiang","training.mp3",b"ID3bgm",self.owner["id"])
        tts=store.register_submission_asset("wuxiang","intro.mp3",b"ID3intro",self.owner["id"])
        with store.connect() as c:
            c.executescript("""
                CREATE TABLE martial_arts(id TEXT PRIMARY KEY,chinese_name TEXT,master_id TEXT);
                CREATE TABLE martial_moves(id TEXT PRIMARY KEY,martial_art_id TEXT,current_version INTEGER);
                CREATE TABLE martial_move_versions(move_id TEXT,version INTEGER,payload TEXT);
                CREATE TABLE martial_mm_asset_links(scope TEXT,scope_id TEXT,role TEXT,asset_id TEXT,status TEXT,created_at TEXT);
                CREATE TABLE martial_mm_tts_assets(move_id TEXT,kind TEXT,language TEXT,asset_id TEXT,status TEXT,created_at TEXT);
                INSERT INTO martial_arts VALUES('art-one','功法',NULL);
                INSERT INTO martial_moves VALUES('move-one','art-one',1);
                INSERT INTO martial_move_versions VALUES('move-one',1,'{"chinese_name":"招式"}');
            """)
            c.execute("INSERT INTO martial_mm_asset_links VALUES(?,?,?,?,?,?)",
                      ("art","art-one","training_bgm",bgm["id"],"active",store.now()))
            c.execute("INSERT INTO martial_mm_tts_assets VALUES(?,?,?,?,?,?)",
                      ("move-one","intro","zh",tts["id"],"active",store.now()))
        assets={item["name"]:item for item in asset_center.list_assets(self.owner)["assets"]}
        self.assertEqual(assets["training.mp3"]["category"],"BGM")
        self.assertEqual(assets["intro.mp3"]["category"],"OS语音")
        self.assertEqual(assets["training.mp3"]["art_id"],"art-one")
        self.assertEqual(assets["intro.mp3"]["move_id"],"move-one")
        self.assertEqual(asset_center.list_assets(self.owner,{"category":"OS语音","move_id":"move-one"})["total"],1)

    def test_bridge_visibility_requires_mapping_and_project_access(self):
        meta={"visibility":"private","owner_username":"old-owner"}
        private_id=self.register("private-old","image",source_system="legacy_ai_center",
                                 file_ref="legacy-oss://private-old",metadata=meta)
        team_id=self.register("team-old","image",source_system="legacy_ai_center",
                              file_ref="legacy-oss://team-old",
                              metadata={"visibility":"team","owner_username":"old-owner"})
        self.assertEqual(asset_center.list_assets(self.owner)["total"],1)
        self.assertEqual(asset_center.list_assets(self.outsider)["total"],0)
        self.assertIsNotNone(asset_center.detail(self.owner,team_id)["asset"]["preview_url"])
        with self.assertRaises(PermissionError):asset_center.detail(self.owner,private_id)
        legacy_asset_bridge.map_identity(self.manager,"old-owner",self.owner["id"],"wuxiang")
        item=asset_center.detail(self.owner,private_id)["asset"]
        self.assertEqual(item["preview_url"],f"/api/asset-center/{private_id}/legacy-preview")
        self.assertTrue(item["can_select"])
        self.assertTrue(item["requires_old_login"])
        self.assertEqual(asset_center.list_assets(self.owner)["total"],2)
        with self.assertRaises(PermissionError):asset_center.detail(self.other,private_id)
        with self.assertRaises(PermissionError):asset_center.detail(self.outsider,private_id)


if __name__=="__main__":unittest.main()
