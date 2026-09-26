"""Registry integrity: privacy, content deduplication and read-only legacy import."""
from __future__ import annotations

import sqlite3
import hashlib
import sys
import tempfile
import unittest
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"app"))
import asset_center
import store


class AssetCenterTest(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.root=Path(self.temp.name)
        self.old_data,self.old_db,self.old_project,self.old_shared=store.DATA,store.DB,dict(store.PROJECT_ROOTS),store.SHARED_ROOT
        store.DATA=self.root/"work";store.DB=store.DATA/"work_os.sqlite3"
        source=self.root/"project";source.mkdir()
        store.PROJECT_ROOTS["wuxiang"]=source
        store.SHARED_ROOT=self.root/"shared";store.SHARED_ROOT.mkdir()
        store.initialize();asset_center.initialize()
        uid=store.create_user("asset-employee","素材员工","employee","asset-employee-password")
        self.employee={"id":uid,"username":"asset-employee","role":"employee"}
        self.manager={"id":"u_system","username":"_system","role":"manager"}
        store.create_task({"project_id":"wuxiang","workflow_id":"WF-02","title":"素材任务","why":"核对素材","assignee_id":uid},"u_system")

    def tearDown(self):
        store.DATA,store.DB,store.PROJECT_ROOTS,store.SHARED_ROOT=self.old_data,self.old_db,self.old_project,self.old_shared
        self.temp.cleanup()

    def test_identical_work_os_files_share_one_registry_record(self):
        a=store.register_submission_asset("wuxiang","one.png",b"same content",self.employee["id"])
        b=store.register_submission_asset("wuxiang","two.png",b"same content",self.employee["id"])
        result=asset_center.list_assets(self.employee)
        self.assertEqual(result["total"],1)
        item=result["assets"][0]
        self.assertEqual(item["category"],"图片")
        self.assertTrue(item["preview_url"].startswith("/api/asset-center/"))
        self.assertNotIn("sources",asset_center.detail(self.employee,item["asset_id"])["asset"])
        sources=asset_center.detail(self.manager,item["asset_id"])["asset"]["sources"]
        self.assertEqual({x["original_id"] for x in sources},{a["id"],b["id"]})

    def test_asset_pages_cover_all_records_without_duplicates(self):
        for index in range(5):
            store.register_submission_asset("wuxiang",f"image-{index}.png",f"image-{index}".encode(),self.employee["id"])
        first=asset_center.list_assets(self.employee,{"limit":"2"})
        second=asset_center.list_assets(self.employee,{"limit":"2","offset":first["next_offset"]})
        third=asset_center.list_assets(self.employee,{"limit":"2","offset":second["next_offset"]})
        self.assertEqual(first["total"],5)
        self.assertEqual([len(page["assets"]) for page in (first,second,third)],[2,2,1])
        self.assertIsNone(third["next_offset"])
        self.assertEqual(len({item["asset_id"] for page in (first,second,third) for item in page["assets"]}),5)
        with self.assertRaises(ValueError):asset_center.list_assets(self.employee,{"offset":"-1"})
        with self.assertRaises(ValueError):asset_center.list_assets(self.employee,{"limit":"2001"})

    def test_candidate_belongs_to_assigned_employee_not_connector(self):
        other_id=store.create_user("other-asset-worker","其他员工","employee","other-asset-password")
        other_task=store.create_task({"project_id":"wuxiang","workflow_id":"WF-02",
                                      "title":"另一员工的任务","why":"验证作品归属","assignee_id":other_id},"u_system")
        own_task=store.create_task({"project_id":"wuxiang","workflow_id":"WF-02",
                                    "title":"本人任务","why":"验证作品归属","assignee_id":self.employee["id"]},"u_system")
        own=store.register_submission_asset("wuxiang","own.mp4",b"own-candidate",self.manager["id"])
        other=store.register_submission_asset("wuxiang","other.mp4",b"other-candidate",self.manager["id"])
        with store.connect() as c:
            c.executescript("""
                CREATE TABLE martial_moves(id TEXT PRIMARY KEY,martial_art_id TEXT,current_version INTEGER);
                CREATE TABLE martial_move_versions(move_id TEXT,version INTEGER,payload TEXT);
                CREATE TABLE martial_media_jobs(candidate_asset_id TEXT,move_id TEXT,task_id TEXT,
                    provider TEXT,model TEXT,provider_job_id TEXT,duration INTEGER,created_at TEXT);
            """)
            c.execute("INSERT INTO martial_moves VALUES('own-move','art',0)")
            c.execute("INSERT INTO martial_moves VALUES('other-move','art',0)")
            c.execute("INSERT INTO martial_media_jobs VALUES(?,?,?,?,?,?,?,?)",
                      (own["id"],"own-move",own_task["id"],"runy","sd2.5","job-1",5,store.now()))
            c.execute("INSERT INTO martial_media_jobs VALUES(?,?,?,?,?,?,?,?)",
                      (other["id"],"other-move",other_task["id"],"runy","sd2.5","job-2",5,store.now()))
        items=asset_center.list_assets(self.employee)["assets"]
        by_name={item["name"]:item for item in items}
        self.assertTrue(by_name["own.mp4"]["is_my_work"])
        self.assertFalse(by_name["other.mp4"]["is_my_work"])

    def test_legacy_registry_never_copies_private_oss_content(self):
        legacy_dir=self.root/"legacy";legacy_dir.mkdir()
        legacy_db=legacy_dir/"studio.db"
        with sqlite3.connect(legacy_db) as c:
            c.executescript("""
                CREATE TABLE users(id INTEGER PRIMARY KEY,username TEXT);
                CREATE TABLE oss_profiles(id TEXT PRIMARY KEY,public_base_url TEXT,public_read INTEGER);
                CREATE TABLE assets(id TEXT PRIMARY KEY,owner_id INTEGER,name TEXT,media_type TEXT,usage_type TEXT,
                    object_key TEXT,size_bytes INTEGER,duration_ms INTEGER,visibility TEXT,source_type TEXT,
                    source_job_id TEXT,status TEXT,created_at TEXT,oss_profile_id TEXT);
                CREATE TABLE jobs(id TEXT PRIMARY KEY,owner_id INTEGER,prompt TEXT,model TEXT,provider TEXT,
                    output_kind TEXT,status TEXT,output_path TEXT,output_oss_key TEXT,created_at TEXT,upstream_id TEXT);
            """)
            c.execute("INSERT INTO users VALUES(1,'legacy-owner')")
            c.execute("INSERT INTO oss_profiles VALUES('oss-1','',0)")
            c.execute("INSERT INTO assets VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                      ("old-image",1,"角色图.png","image","reference","assets/character.png",1234,None,"team","upload",None,"active","2026-09-21T12:00:00","oss-1"))
            c.execute("INSERT INTO assets VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                      ("old-private",1,"私有生成.mp4","video","generation","outputs/old-job.mp4",1234,5000,
                       "private","job","old-job","active","2026-09-21T12:01:00","oss-1"))
            c.execute("INSERT INTO jobs VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                      ("old-job",1,"Draw a crane","sd2.5","runy","video","completed",str(legacy_dir/"outputs"/"old-job.mp4"),
                       "outputs/old-job.mp4","2026-09-21T12:01:00","upstream-1"))
        report=asset_center.import_legacy(legacy_db,legacy_dir)
        self.assertEqual(report["discovered"],4)
        self.assertEqual(report["imported"],3)
        self.assertEqual(report["deduplicated"],1)
        self.assertEqual(report["images"],1)
        self.assertEqual(report["videos"],2)
        self.assertEqual(report["prompts"],1)
        items=asset_center.list_assets(self.employee)["assets"]
        self.assertEqual(len(items),1)
        old_image=next(x for x in items if x["name"]=="角色图.png")
        self.assertEqual(old_image["preview_url"],f"/api/asset-center/{old_image['asset_id']}/legacy-preview")
        self.assertTrue(old_image["requires_old_login"])
        self.assertIsNone(old_image["source_url"])
        self.assertNotIn("file_ref",old_image)
        self.assertEqual(asset_center.list_assets(self.manager)["total"],3)
        # Old private prompts and job results must not become team assets.
        with store.connect() as c:
            prompt=c.execute("SELECT metadata FROM asset_registry WHERE source_system='legacy_ai_prompt'").fetchone()[0]
            job=c.execute("SELECT metadata FROM asset_registry WHERE original_id='old-private'").fetchone()[0]
            alias=c.execute("SELECT asset_id FROM asset_registry_sources WHERE source_system='legacy_ai_job' AND original_id='old-job'").fetchone()
        self.assertEqual(store.parse(prompt)["visibility"],"private")
        self.assertEqual(store.parse(job)["visibility"],"private")
        self.assertIsNotNone(alias)
        self.assertEqual(asset_center.import_legacy(legacy_db,legacy_dir)["imported"],0)

    def test_legacy_job_match_requires_exact_object_type_owner_and_uniqueness(self):
        def old_asset(original_id, object_key, typ, owner):
            return asset_center._register(c,{"source_system":"legacy_ai_center",
                "original_id":original_id,"project_id":"wuxiang","type":typ,
                "name":original_id,"creator":owner,
                "metadata":{"object_key":object_key,"owner_username":owner,"visibility":"private"}})[0]
        with store.connect() as c:
            video=old_asset("video","outputs/job.mp4","video","owner-a")
            old_asset("thumbnail","outputs/thumb.png","image","owner-a")
            self.assertEqual(asset_center._matching_legacy_asset(c,"outputs/job.mp4","video","owner-a"),video)
            self.assertIsNone(asset_center._matching_legacy_asset(c,"outputs/thumb.png","video","owner-a"))
            self.assertIsNone(asset_center._matching_legacy_asset(c,"outputs/job.mp4","video","owner-b"))
            self.assertIsNone(asset_center._matching_legacy_asset(c,None,"video","owner-a"))
            old_asset("ambiguous","outputs/job.mp4","video","owner-a")
            self.assertIsNone(asset_center._matching_legacy_asset(c,"outputs/job.mp4","video","owner-a"))

    def test_legacy_import_filters_credential_paths_and_skips_large_file_hash(self):
        legacy_dir=self.root/"legacy-cap";legacy_dir.mkdir()
        legacy_db=legacy_dir/"studio.db"
        big=legacy_dir/"outputs"/"large.mp4";big.parent.mkdir()
        with big.open("wb") as output:output.truncate(asset_center.MAX_LEGACY_HASH_BYTES+1)
        with sqlite3.connect(legacy_db) as c:
            c.executescript("""
                CREATE TABLE users(id INTEGER PRIMARY KEY,username TEXT);
                CREATE TABLE oss_profiles(id TEXT PRIMARY KEY,public_base_url TEXT,public_read INTEGER);
                CREATE TABLE assets(id TEXT PRIMARY KEY,owner_id INTEGER,name TEXT,media_type TEXT,usage_type TEXT,
                    object_key TEXT,size_bytes INTEGER,duration_ms INTEGER,visibility TEXT,source_type TEXT,
                    source_job_id TEXT,status TEXT,created_at TEXT,oss_profile_id TEXT);
                CREATE TABLE jobs(id TEXT PRIMARY KEY,owner_id INTEGER,prompt TEXT,model TEXT,provider TEXT,
                    output_kind TEXT,status TEXT,output_path TEXT,output_oss_key TEXT,created_at TEXT,upstream_id TEXT);
            """)
            c.execute("INSERT INTO users VALUES(1,'legacy-owner')")
            c.execute("INSERT INTO assets VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                      ("safe-image",1,"正常图片.png","image","reference","images/normal.png",100,None,
                       "team","upload",None,"active","2026-09-21T12:00:00",None))
            c.execute("INSERT INTO assets VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                      ("secret-name",1,"my password.txt","document","reference","docs/plain.txt",100,None,
                       "team","upload",None,"active","2026-09-21T12:01:00",None))
            c.execute("INSERT INTO assets VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                      ("secret-path",1,"innocuous.png","image","reference","secrets/auth.json",100,None,
                       "team","upload",None,"active","2026-09-21T12:02:00",None))
            c.execute("INSERT INTO jobs VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                      ("large-job",1,"","sd2.5","runy","video","completed",str(big),None,
                       "2026-09-21T12:03:00",None))
        report=asset_center.import_legacy(legacy_db,legacy_dir)
        self.assertEqual(report["safety_filtered"],2)
        with store.connect() as c:
            rows={r["original_id"]:dict(r) for r in c.execute(
                "SELECT original_id,sha256,metadata FROM asset_registry WHERE source_system LIKE 'legacy_ai%'")}
        self.assertEqual(set(rows),{"safe-image","large-job"})
        self.assertIsNone(rows["large-job"]["sha256"])
        self.assertTrue(store.parse(rows["large-job"]["metadata"])["local_file_available"])
        visible=asset_center.list_assets(self.employee)["assets"]
        self.assertEqual(len(visible),1)
        self.assertIsNone(visible[0]["source_url"])

    def test_private_legacy_hashes_do_not_merge_across_owners(self):
        digest=hashlib.sha256(b"same prompt").hexdigest()
        with store.connect() as c:
            first=asset_center._register(c,{"source_system":"legacy_ai_prompt","original_id":"job-a",
                "project_id":"wuxiang","type":"prompt","name":"A","sha256":digest,
                "metadata":{"visibility":"private","owner_username":"old-a"}})
            second=asset_center._register(c,{"source_system":"legacy_ai_prompt","original_id":"job-b",
                "project_id":"wuxiang","type":"prompt","name":"B","sha256":digest,
                "metadata":{"visibility":"private","owner_username":"old-b"}})
        self.assertNotEqual(first[0],second[0])
        self.assertEqual(asset_center.list_assets(self.manager)["total"],2)
        self.assertEqual(asset_center.list_assets(self.employee)["total"],0)

    def test_existing_video_linked_as_motion_is_not_a_digital_teacher(self):
        video=store.register_submission_asset("wuxiang","reference.mp4",b"test video",self.employee["id"])
        with store.connect() as c:
            c.executescript("""
                CREATE TABLE martial_moves(id TEXT PRIMARY KEY,martial_art_id TEXT,current_version INTEGER);
                CREATE TABLE martial_move_versions(move_id TEXT,version INTEGER,payload TEXT);
                CREATE TABLE martial_motion_refs(video_asset_id TEXT,move_id TEXT,cover_asset_id TEXT,
                                                 duration REAL,created_at TEXT);
            """)
            c.execute("INSERT INTO martial_moves VALUES('mv_test','test-art',0)")
            c.execute("INSERT INTO martial_motion_refs VALUES(?,?,?,?,?)",
                      (video["id"],"mv_test",None,5.0,store.now()))
        result=asset_center.list_assets(self.employee)["assets"]
        self.assertEqual(result[0]["category"],"真人动作")


if __name__=="__main__":unittest.main()
