"""Current martial business view, separate from immutable imported and P0 history.

The verified source dictionary remains unchanged.  A current-business mapping
chooses what employees produce today; the original move/version rows and every
technical pilot row remain in the database for an administrator to inspect.
"""
from __future__ import annotations

import base64
import csv
import difflib
import io
import re
import secrets
import zipfile
from pathlib import Path
from xml.etree import ElementTree

import store


BEGINNER_CURRENT = (
    ("mv_beginner_02", 1, "马步"),
    ("mv_beginner_04", 2, "冲拳"),
    ("mv_beginner_mabu_chongquan", 3, "马步冲拳"),
)
BEGINNER_SOURCE = "user_instruction:2026-09-24:beginner-current-three"
PILOT_MOVE = "mv_bagua_01"
PILOT_MEDIA = "mj_94efec7ab99b7e95"
PILOT_CANDIDATE = "a_a63a7b008c349532"
PILOT_SOURCE = "user_instruction:2026-09-24:TECHNICAL_PILOT_HISTORY"
VISUAL_FIELDS = {"portrait", "front_view", "side_view", "back_view", "turnaround", "costume", "other_angle"}
UPLOAD_FIELDS = VISUAL_FIELDS | {"voice_preview", "digital_model"}


def _schema(c):
    if "display_order" not in {r[1] for r in c.execute("PRAGMA table_info(martial_arts)")}:
        c.execute("ALTER TABLE martial_arts ADD COLUMN display_order INTEGER NOT NULL DEFAULT 0")
    c.executescript("""
    CREATE TABLE IF NOT EXISTS martial_product_migrations(
        name TEXT PRIMARY KEY, applied_at TEXT NOT NULL, source_ref TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS martial_business_moves(
        move_id TEXT PRIMARY KEY REFERENCES martial_moves(id),
        martial_art_id TEXT NOT NULL REFERENCES martial_arts(id),
        display_order INTEGER NOT NULL,
        business_label TEXT NOT NULL,
        is_current INTEGER NOT NULL CHECK(is_current IN (0,1)),
        classification TEXT NOT NULL,
        source_ref TEXT NOT NULL,
        updated_at TEXT NOT NULL);
    CREATE UNIQUE INDEX IF NOT EXISTS idx_martial_business_current_order
      ON martial_business_moves(martial_art_id,display_order) WHERE is_current=1;
    CREATE INDEX IF NOT EXISTS idx_martial_business_current
      ON martial_business_moves(is_current,martial_art_id,display_order);
    CREATE TABLE IF NOT EXISTS martial_technical_history(
        kind TEXT NOT NULL, item_id TEXT NOT NULL, move_id TEXT NOT NULL REFERENCES martial_moves(id),
        classification TEXT NOT NULL, source_ref TEXT NOT NULL, tagged_at TEXT NOT NULL,
        PRIMARY KEY(kind,item_id));
    CREATE INDEX IF NOT EXISTS idx_martial_technical_move
      ON martial_technical_history(move_id,kind);
    """)


def _tag(c, kind: str, item_id: str | None):
    if item_id:
        c.execute("INSERT OR IGNORE INTO martial_technical_history"
                  "(kind,item_id,move_id,classification,source_ref,tagged_at) VALUES(?,?,?,?,?,?)",
                  (kind,item_id,PILOT_MOVE,"TECHNICAL_PILOT_HISTORY",PILOT_SOURCE,store.now()))


def initialize_product_migration() -> dict:
    """One-time, idempotent view correction; preserves every imported/P0 row."""
    with store.connect() as c:
        _schema(c)
        if c.execute("SELECT 1 FROM martial_product_migrations WHERE name='part_a_current_business_v1'").fetchone():
            return migration_stats(c)
        stamp=store.now()
        # This name comes from the current user instruction, not the DOCX.
        move_id=BEGINNER_CURRENT[2][0]
        c.execute("INSERT OR IGNORE INTO martial_moves"
                  "(id,martial_art_id,ordinal,current_version,draft_version,created_at,updated_at)"
                  " VALUES(?,?,?,?,?,?,?)",(move_id,"beginner",8,1,0,stamp,stamp))
        payload={k:"" for k in ("chinese_name","english_name","chinese_action","english_action",
                              "chinese_coaching","english_coaching","breathing_notes","safety_notes")}
        payload["chinese_name"]="马步冲拳"
        c.execute("INSERT OR IGNORE INTO martial_move_versions"
                  "(move_id,version,payload,status,source_ref,created_by,approved_by,created_at,approved_at)"
                  " VALUES(?,?,?,?,?,?,?,?,?)",
                  (move_id,1,store.dumps(payload),"active",BEGINNER_SOURCE,"u_system","u_system",stamp,stamp))
        rows=c.execute("SELECT m.id,m.martial_art_id,m.ordinal,v.payload,v.source_ref "
                       "FROM martial_moves m LEFT JOIN martial_move_versions v "
                       "ON v.move_id=m.id AND v.version=COALESCE(NULLIF(m.current_version,0),1)").fetchall()
        for position,art_id in enumerate(("beginner","flowing_cloud","mountain_fist","taiji","bagua"),1):
            c.execute("UPDATE martial_arts SET display_order=? WHERE id=? AND display_order=0",(position,art_id))
        beginner={x[0]:(x[1],x[2]) for x in BEGINNER_CURRENT}
        for row in rows:
            art_id=row["martial_art_id"]
            current=art_id!="beginner" or row["id"] in beginner
            if art_id=="beginner":
                order,label=beginner.get(row["id"],(row["ordinal"],""))
                classification="CURRENT" if current else "BASIC_MOVEMENT_REFERENCE"
            else:
                order=row["ordinal"]
                label="第一式" if row["id"]==PILOT_MOVE else ""
                classification="CURRENT"
            c.execute("INSERT OR IGNORE INTO martial_business_moves"
                      "(move_id,martial_art_id,display_order,business_label,is_current,classification,source_ref,updated_at)"
                      " VALUES(?,?,?,?,?,?,?,?)",
                      (row["id"],art_id,order,label,int(current),classification,
                       BEGINNER_SOURCE if art_id=="beginner" else (row["source_ref"] or ""),stamp))
        # Only the verified P0 candidate is tagged. If absent (fresh install),
        # a future legitimate first-move upload is never mistaken for the pilot.
        pilot=c.execute("SELECT id,candidate_asset_id,task_id FROM martial_media_jobs "
                        "WHERE id=? AND move_id=? AND candidate_asset_id=?",
                        (PILOT_MEDIA,PILOT_MOVE,PILOT_CANDIDATE)).fetchone()
        if pilot:
            _tag(c,"media_job",pilot["id"])
            _tag(c,"asset",pilot["candidate_asset_id"])
            _tag(c,"task",pilot["task_id"])
            for row in c.execute("SELECT id,video_asset_id FROM martial_motion_refs WHERE move_id=?",(PILOT_MOVE,)):
                _tag(c,"motion_ref",row["id"]);_tag(c,"asset",row["video_asset_id"])
            for row in c.execute("SELECT id,task_id FROM martial_packages WHERE move_id=?",(PILOT_MOVE,)):
                _tag(c,"package",row["id"]);_tag(c,"task",row["task_id"])
            for row in c.execute("SELECT id,candidate_asset_id,task_id FROM martial_media_jobs WHERE move_id=?",(PILOT_MOVE,)):
                _tag(c,"media_job",row["id"]);_tag(c,"asset",row["candidate_asset_id"]);_tag(c,"task",row["task_id"])
            for table,kind in (("martial_qc","qc"),("martial_final_assets","final_asset")):
                for row in c.execute(f"SELECT id FROM {table} WHERE move_id=?",(PILOT_MOVE,)):_tag(c,kind,row["id"])
            for row in c.execute("SELECT r.id FROM martial_revision_packages r JOIN martial_media_jobs j ON j.id=r.media_job_id WHERE j.move_id=?",(PILOT_MOVE,)):
                _tag(c,"revision_package",row["id"])
        c.execute("INSERT INTO martial_product_migrations(name,applied_at,source_ref) VALUES(?,?,?)",
                  ("part_a_current_business_v1",stamp,BEGINNER_SOURCE+";"+PILOT_SOURCE))
        store.audit(c,"u_system","martial.product.current_business_migration",None,
                    {"beginner_current":[x[0] for x in BEGINNER_CURRENT],
                     "pilot_tagged":bool(pilot),"source_ref":BEGINNER_SOURCE})
        return migration_stats(c)


def migration_stats(c=None) -> dict:
    if c is None:
        with store.connect() as connection:return migration_stats(connection)
    return {"current_moves":c.execute("SELECT COUNT(*) FROM martial_business_moves WHERE is_current=1").fetchone()[0],
            "beginner_current":[r[0] for r in c.execute("SELECT move_id FROM martial_business_moves WHERE martial_art_id='beginner' AND is_current=1 ORDER BY display_order")],
            "technical_history_items":c.execute("SELECT COUNT(*) FROM martial_technical_history").fetchone()[0]}


def current_mapping(c, move_id: str) -> dict | None:
    if not c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='martial_business_moves'").fetchone():
        return None
    row=c.execute("SELECT * FROM martial_business_moves WHERE move_id=?",(move_id,)).fetchone()
    return dict(row) if row else None


def current_move(c, move_id: str) -> bool:
    mapping=current_mapping(c,move_id)
    return mapping is None or bool(mapping["is_current"])


def historical(c, kind: str, item_id: str | None) -> bool:
    if not item_id or not c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='martial_technical_history'").fetchone():
        return False
    return c.execute("SELECT 1 FROM martial_technical_history WHERE kind=? AND item_id=?",(kind,item_id)).fetchone() is not None


def asset_is_technical_history(asset_id: str) -> bool:
    with store.connect() as c:return historical(c,"asset",asset_id)


def _allow(user: dict, write=False):
    import martial
    martial.allow(user,write)


def _clean_text(value, name: str, *, required=False, maximum=2000):
    if not isinstance(value,str):raise ValueError(name+"格式无效")
    clean=value.strip()
    if (required and not clean) or len(clean)>maximum:raise ValueError(name+"无效或过长")
    return clean


def _id(prefix: str) -> str:
    return prefix+"_"+secrets.token_hex(8)


def create_art(user: dict, data: dict) -> dict:
    _allow(user,True)
    import martial
    chinese=_clean_text(data.get("chinese_name",""),"功法中文名",required=True,maximum=120)
    english=_clean_text(data.get("english_name",""),"功法英文名",maximum=120)
    category=_clean_text(data.get("category",""),"功法类型",required=True,maximum=100)
    description=_clean_text(data.get("description",""),"功法简介",maximum=3000)
    traits=data.get("style_traits") or []
    if not isinstance(traits,list) or len(traits)>20 or any(not isinstance(x,str) or len(x)>100 for x in traits):
        raise ValueError("功法风格特点无效")
    master_id=_clean_text(data.get("master_id") or "","功法老师",maximum=100)
    status=str(data.get("status") or "active")
    if status not in {"active","draft"}:raise ValueError("功法状态无效")
    order=int(data.get("order") or 0)
    if not 0<=order<=999:raise ValueError("功法排序无效")
    volume=_clean_text(data.get("volume") or "第一卷","功法卷",maximum=100)
    source_ref=_clean_text(data.get("source_ref") or "employee_created_current_business","来源",maximum=1000)
    art_id=_id("art")
    payload={"chinese_name":chinese,"english_name":english,"volume":volume,"category":category,
             "description":description,"style_traits":[x.strip() for x in traits if x.strip()],
             "master_id":master_id,"planned_moves":None,"order":order}
    with store.connect() as c:
        if master_id:martial._row(c,"martial_masters",master_id)
        if c.execute("SELECT 1 FROM martial_arts WHERE chinese_name=? AND status='active'",(chinese,)).fetchone():
            raise ValueError("同名功法已存在")
        stamp=store.now()
        c.execute("INSERT INTO martial_arts(id,chinese_name,english_name,volume,category,description,style_traits,master_id,status,version,draft_version,source_ref,display_order,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                  (art_id,chinese,english,volume,category,description,store.dumps(payload["style_traits"]),master_id,status,1,0,source_ref,order,stamp,stamp))
        c.execute("INSERT INTO martial_art_versions(art_id,version,payload,status,source_ref,created_by,approved_by,created_at,approved_at) VALUES(?,?,?,?,?,?,?,?,?)",
                  (art_id,1,store.dumps(payload),status,source_ref,user["id"],user["id"],stamp,stamp))
        store.audit(c,user["id"],"martial.art.create",None,{"art_id":art_id,"status":status})
    return {"id":art_id,**martial.art_detail(art_id,user)}


def update_art(user: dict, art_id: str, data: dict) -> dict:
    """Changing an existing canonical art remains a reviewable draft."""
    import martial
    current=martial.art_detail(art_id,user)["art"]
    merged={key:current[key] for key in ("chinese_name","english_name","volume","category","description","master_id","planned_moves")}
    merged["style_traits"]=current["style_traits"]
    merged.update(data)
    merged["source_ref"]=str(data.get("source_ref") or current["source_ref"])
    if current["status"]=="draft":
        _allow(user,True)
        status=str(data.get("status") or "draft")
        if status not in {"draft","active"}:raise ValueError("功法状态无效")
        payload={key:_clean_text(merged.get(key) or "",key,required=key in {"chinese_name","category"},maximum=3000)
                 for key in ("chinese_name","english_name","volume","category","description","master_id")}
        traits=merged.get("style_traits") or []
        if not isinstance(traits,list) or len(traits)>20 or any(not isinstance(x,str) or len(x)>100 for x in traits):
            raise ValueError("功法风格特点无效")
        payload["style_traits"]=[x.strip() for x in traits if x.strip()]
        payload["planned_moves"]=current["planned_moves"]
        with store.connect() as c:
            art=martial._row(c,"martial_arts",art_id)
            if art["status"]!="draft" or art["draft_version"]:raise ValueError("功法状态已变化，请刷新后重试")
            creator=c.execute("SELECT created_by FROM martial_art_versions WHERE art_id=? AND version=1",(art_id,)).fetchone()
            if not creator or creator["created_by"]=="u_system":raise PermissionError("导入功法不属于可直接发布的新建草稿")
            if payload["master_id"]:martial._row(c,"martial_masters",payload["master_id"])
            source_ref=_clean_text(merged["source_ref"],"来源",required=True,maximum=1000)
            version=art["version"]+1;stamp=store.now()
            c.execute("UPDATE martial_art_versions SET status='superseded' WHERE art_id=? AND version=?",(art_id,art["version"]))
            c.execute("INSERT INTO martial_art_versions(art_id,version,payload,status,source_ref,created_by,approved_by,created_at,approved_at) VALUES(?,?,?,?,?,?,?,?,?)",
                      (art_id,version,store.dumps(payload),status,source_ref,user["id"],user["id"] if status=="active" else None,stamp,stamp if status=="active" else None))
            c.execute("UPDATE martial_arts SET chinese_name=?,english_name=?,volume=?,category=?,description=?,style_traits=?,master_id=?,status=?,version=?,source_ref=?,updated_at=? WHERE id=?",
                      (payload["chinese_name"],payload["english_name"],payload["volume"],payload["category"],payload["description"],
                       store.dumps(payload["style_traits"]),payload["master_id"],status,version,source_ref,stamp,art_id))
            store.audit(c,user["id"],"martial.art.new_draft_update",None,{"art_id":art_id,"version":version,"status":status})
        return martial.art_detail(art_id,user)
    if not current["master_id"] and merged["master_id"] and all(
        merged[key]==current[key] for key in ("chinese_name","english_name","volume","category","description","planned_moves","style_traits")):
        _allow(user,True)
        with store.connect() as c:
            art=martial._row(c,"martial_arts",art_id)
            if art["master_id"] or art["draft_version"]:raise ValueError("功法绑定状态已变化，请刷新后重试")
            martial._row(c,"martial_masters",merged["master_id"])
            version=art["version"]+1;stamp=store.now()
            payload={key:merged[key] for key in ("chinese_name","english_name","volume","category","description","style_traits","master_id","planned_moves")}
            c.execute("INSERT INTO martial_art_versions(art_id,version,payload,status,source_ref,created_by,approved_by,created_at,approved_at) VALUES(?,?,?,?,?,?,?,?,?)",
                      (art_id,version,store.dumps(payload),"active",merged["source_ref"],user["id"],user["id"],stamp,stamp))
            c.execute("UPDATE martial_arts SET master_id=?,source_ref=?,version=?,updated_at=? WHERE id=?",
                      (merged["master_id"],merged["source_ref"],version,stamp,art_id))
            store.audit(c,user["id"],"martial.art.initial_master_binding",None,{"art_id":art_id,"master_id":merged["master_id"],"version":version})
        return martial.art_detail(art_id,user)
    return martial.save_art(user,art_id,merged)


def create_move(user: dict, data: dict) -> dict:
    _allow(user,True)
    import martial
    art_id=_clean_text(data.get("martial_art_id",""),"所属功法",required=True,maximum=100)
    payload={key:_clean_text(data.get(key) or "",key,maximum=3000) for key in martial.MOVE_FIELDS}
    if not payload["chinese_name"] or len(payload["chinese_name"])>120:raise ValueError("招式中文名称无效")
    source_ref=_clean_text(data.get("source_ref") or "employee_created_current_business","来源",maximum=1000)
    with store.connect() as c:
        art=martial._row(c,"martial_arts",art_id)
        if art["status"]!="active":raise ValueError("功法尚未生效")
        if art_id=="beginner" and payload["chinese_name"] not in {x[2] for x in BEGINNER_CURRENT}:
            raise ValueError("当前入门套路仅含马步、冲拳、马步冲拳；新增需先核实业务映射")
        if c.execute("SELECT 1 FROM martial_business_moves b JOIN martial_move_versions v ON v.move_id=b.move_id JOIN martial_moves m ON m.id=b.move_id AND v.version=m.current_version WHERE b.martial_art_id=? AND b.is_current=1 AND json_extract(v.payload,'$.chinese_name')=?",(art_id,payload["chinese_name"])).fetchone():
            raise ValueError("当前功法已有同名招式")
        ordinal=int(data.get("order") or 0)
        if ordinal==0:ordinal=c.execute("SELECT COALESCE(MAX(ordinal),0)+1 FROM martial_moves WHERE martial_art_id=?",(art_id,)).fetchone()[0]
        if not 1<=ordinal<=999:raise ValueError("招式排序无效")
        if c.execute("SELECT 1 FROM martial_moves WHERE martial_art_id=? AND ordinal=?",(art_id,ordinal)).fetchone():
            raise ValueError("招式排序已占用")
        move_id=_id("mv");stamp=store.now()
        c.execute("INSERT INTO martial_moves(id,martial_art_id,ordinal,current_version,draft_version,created_at,updated_at) VALUES(?,?,?,?,?,?,?)",
                  (move_id,art_id,ordinal,1,0,stamp,stamp))
        c.execute("INSERT INTO martial_move_versions(move_id,version,payload,status,source_ref,created_by,approved_by,created_at,approved_at) VALUES(?,?,?,?,?,?,?,?,?)",
                  (move_id,1,store.dumps(payload),"active",source_ref,user["id"],user["id"],stamp,stamp))
        display_order=c.execute("SELECT COALESCE(MAX(display_order),0)+1 FROM martial_business_moves WHERE martial_art_id=? AND is_current=1",(art_id,)).fetchone()[0]
        c.execute("INSERT INTO martial_business_moves(move_id,martial_art_id,display_order,business_label,is_current,classification,source_ref,updated_at) VALUES(?,?,?,?,?,?,?,?)",
                  (move_id,art_id,display_order,"",1,"CURRENT",source_ref,stamp))
        store.audit(c,user["id"],"martial.move.create",None,{"move_id":move_id,"art_id":art_id,"source_ref":source_ref})
    return {"id":move_id,**martial.move_detail(move_id,user)}


def edit_move_standard(user: dict, move_id: str, data: dict) -> dict:
    """Routine employee corrections become effective versions; only a changed
    core fact with an active final asset needs the owner to resolve it.
    """
    _allow(user,True)
    import martial
    if not isinstance(data,dict):raise ValueError("动作标准数据无效")
    allowed=set(martial.MOVE_FIELDS)
    if not (allowed & data.keys()):raise ValueError("请填写需要修改的动作标准")
    if any(key not in allowed|{"source_ref","base_version"} for key in data):
        raise ValueError("动作标准包含未知字段")
    with store.connect() as c:
        move=martial._row(c,"martial_moves",move_id)
        if not current_move(c,move_id):raise ValueError("该招式不在当前生产范围")
        initial=not move["current_version"]
        if move["draft_version"] and not initial:raise ValueError("已有待负责人确认的动作标准，请先处理")
        base_version=move["current_version"] or move["draft_version"]
        allowed_bases=(None,0,base_version) if initial else (None,base_version)
        if data.get("base_version") not in allowed_bases:
            raise ValueError("动作标准已更新，请刷新后再保存")
        current=martial._version(c,"martial_move_versions","move_id",move_id,base_version) if base_version else None
        payload=dict(current["payload"]) if current else {key:"" for key in martial.MOVE_FIELDS}
        mapping=current_mapping(c,move_id)
        if mapping and mapping["business_label"]:
            payload["chinese_name"]=mapping["business_label"]
        old=dict(payload)
        for key in martial.MOVE_FIELDS:
            if key in data:payload[key]=_clean_text(data[key] or "",key,maximum=3000)
        if not payload["chinese_name"] or len(payload["chinese_name"])>120:
            raise ValueError("招式中文名称无效")
        if not payload["chinese_action"] or not payload["english_action"]:
            raise ValueError("请填写中文和英文动作")
        if payload==old and not initial:return {"id":move_id,"changed":False,"needs_review":False,**martial.move_detail(move_id,user)}
        source_ref=_clean_text(data.get("source_ref") or (current["source_ref"] if current else "employee_action_standard"),"来源",required=True,maximum=1000)
        has_final=c.execute("SELECT 1 FROM martial_final_assets WHERE move_id=? AND status='active' LIMIT 1",(move_id,)).fetchone() is not None
        conflict=has_final and _major_asset_conflict(old,payload)
        version=max(move["current_version"],move["draft_version"])+1
        stamp=store.now()
        status="ready_for_approval" if conflict else "active"
        c.execute("INSERT INTO martial_move_versions(move_id,version,payload,status,source_ref,created_by,approved_by,created_at,approved_at,submitted_by,submitted_at,submission_note,review_flags) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                  (move_id,version,store.dumps(payload),status,source_ref,user["id"],
                   None if conflict else user["id"],stamp,None if conflict else stamp,
                   user["id"] if conflict else None,stamp if conflict else None,
                   "与已完成正式资产的动作核心事实可能冲突" if conflict else "",
                   store.dumps(["conflict"] if conflict else [])))
        if initial and not conflict:
            c.execute("UPDATE martial_moves SET current_version=?,draft_version=0,updated_at=? WHERE id=?",(version,stamp,move_id))
        else:
            c.execute("UPDATE martial_moves SET "+("draft_version" if conflict else "current_version")+"=?,updated_at=? WHERE id=?",
                      (version,stamp,move_id))
        store.audit(c,user["id"],"martial.move.standard_review" if conflict else "martial.move.standard_update",
                    None,{"move_id":move_id,"version":version,"changed_fields":[k for k in martial.MOVE_FIELDS if old.get(k)!=payload.get(k)]})
    return {"id":move_id,"changed":True,"needs_review":conflict,**martial.move_detail(move_id,user)}


def _major_asset_conflict(before: dict, after: dict) -> bool:
    """Flag explainable structural changes against a published sample.

    Copy edits and coaching notes stay in the normal employee path.  Changes
    to the move identity, directional facts, or most of the action text need
    human resolution because the existing final video may now be inaccurate.
    """
    if before.get("chinese_name")!=after.get("chinese_name"):return True
    directional=("左","右","前","后","上","下","内","外","顺时针","逆时针",
                 "left","right","forward","backward","up","down","clockwise","counterclockwise",
                 "不得","禁止","不要","must not","do not")
    for field in ("chinese_action","english_action"):
        old=str(before.get(field) or "").strip().lower()
        new=str(after.get(field) or "").strip().lower()
        if old==new:continue
        if any((term in old)!=(term in new) for term in directional):return True
        if difflib.SequenceMatcher(None,old,new).ratio()<0.65:return True
    return False


IMPORT_COLUMNS={
    "序号":"order", "顺序":"order", "order":"order", "ordinal":"order",
    "中文名":"chinese_name", "中文名称":"chinese_name", "招式名称":"chinese_name",
    "chinese_name":"chinese_name", "英文名":"english_name", "英文名称":"english_name",
    "english_name":"english_name", "中文动作":"chinese_action", "chinese_action":"chinese_action",
    "英文动作":"english_action", "english_action":"english_action",
    "教学提示":"chinese_coaching", "中文教学提示":"chinese_coaching",
    "chinese_coaching":"chinese_coaching", "英文教学提示":"english_coaching",
    "english_coaching":"english_coaching", "呼吸提示":"breathing_notes",
    "breathing_notes":"breathing_notes", "安全提示":"safety_notes", "safety_notes":"safety_notes",
}


def _spreadsheet_rows(contents: bytes, suffix: str) -> list[dict]:
    if suffix==".csv":
        try:text=contents.decode("utf-8-sig")
        except UnicodeDecodeError:raise ValueError("CSV 请保存为 UTF-8 编码")
        table=list(csv.reader(io.StringIO(text)))
    else:
        try:
            with zipfile.ZipFile(io.BytesIO(contents)) as book:
                members=book.infolist()
                if (len(members)>200 or sum(item.file_size for item in members)>8_000_000 or
                    any(item.filename.startswith("xl/externalLinks/") or "vbaProject" in item.filename for item in members)):
                    raise ValueError("Excel 文件包含不支持的内容或体积过大")
                sheets=[item.filename for item in members if re.fullmatch(r"xl/worksheets/sheet\d+\.xml",item.filename)]
                if not sheets:raise ValueError("Excel 中没有可读取的工作表")
                ns="{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
                strings=[]
                if "xl/sharedStrings.xml" in book.namelist():
                    root=ElementTree.fromstring(book.read("xl/sharedStrings.xml"))
                    strings=["".join(node.text or "" for node in si.iter(ns+"t")) for si in root.iter(ns+"si")]
                root=ElementTree.fromstring(book.read(sorted(sheets,key=lambda x:int(re.search(r"(\d+)\.xml$",x).group(1)))[0]))
                table=[]
                for row in root.iter(ns+"row"):
                    cells={}
                    for cell in row.iter(ns+"c"):
                        address=cell.get("r","")
                        letters=re.match(r"[A-Z]+",address)
                        if not letters:continue
                        index=0
                        for char in letters.group():index=index*26+ord(char)-64
                        if index>30:continue
                        kind=cell.get("t")
                        if kind=="inlineStr":value="".join(node.text or "" for node in cell.iter(ns+"t"))
                        else:
                            value=cell.findtext(ns+"v") or ""
                            if kind=="s" and value:
                                try:value=strings[int(value)]
                                except (ValueError,IndexError):raise ValueError("Excel 共享文本索引无效")
                        cells[index-1]=value
                    if cells:table.append([cells.get(i,"") for i in range(max(cells)+1)])
                    if len(table)>101:raise ValueError("每次最多导入 100 条招式")
        except (zipfile.BadZipFile,ElementTree.ParseError):raise ValueError("Excel 文件格式无效")
    if not table:raise ValueError("导入文件为空")
    headers=[IMPORT_COLUMNS.get(str(value).strip().lower()) for value in table[0]]
    if not {"chinese_name","chinese_action","english_action"}.issubset(headers):
        raise ValueError("导入文件缺少中文名、中文动作或英文动作列")
    if len([h for h in headers if h])!=len(set(h for h in headers if h)):
        raise ValueError("导入文件有重复字段列")
    rows=[]
    for values in table[1:]:
        if not any(str(v).strip() for v in values):continue
        item={field:str(values[index]).strip() for index,field in enumerate(headers)
              if field and index<len(values) and str(values[index]).strip()}
        rows.append(item)
    return rows


def import_moves(user: dict, art_id: str, data: dict) -> dict:
    _allow(user,True)
    name=str(data.get("name") or "")
    suffix=Path(name).suffix.lower()
    if suffix not in {".csv",".xlsx"}:raise ValueError("仅支持 CSV 或 XLSX")
    encoded=data.get("base64")
    if not isinstance(encoded,str) or len(encoded)>4_000_000:raise ValueError("导入文件过大")
    try:contents=base64.b64decode(encoded,validate=True)
    except (TypeError,ValueError):raise ValueError("导入文件无效")
    if not contents or len(contents)>3_000_000:raise ValueError("导入文件为空或超过 3MB")
    rows=_spreadsheet_rows(contents,suffix)
    return create_moves_batch(user,art_id,{"moves":rows,"source_ref":data.get("source_ref") or "employee_import:"+suffix[1:]})


def copy_moves(user: dict, art_id: str, data: dict) -> dict:
    _allow(user,True)
    import martial
    source_id=str(data.get("source_art_id") or "")
    if not source_id or source_id==art_id:raise ValueError("请选择其他现有功法")
    selected=data.get("move_ids")
    if selected is not None and (not isinstance(selected,list) or not selected or len(selected)>100 or
                                 len(set(selected))!=len(selected) or any(not isinstance(x,str) for x in selected)):
        raise ValueError("请选择 1 至 100 条不重复招式")
    with store.connect() as c:
        martial._row(c,"martial_arts",source_id)
        rows=c.execute("SELECT m.id,m.current_version,b.business_label FROM martial_business_moves b "
                       "JOIN martial_moves m ON m.id=b.move_id WHERE b.martial_art_id=? AND b.is_current=1 "
                       "ORDER BY b.display_order",(source_id,)).fetchall()
        if selected is not None and not set(selected).issubset({r["id"] for r in rows}):
            raise ValueError("所选招式不属于源功法当前生产清单")
        moves=[]
        for row in rows:
            if selected is not None and row["id"] not in selected:continue
            if not row["current_version"]:continue
            payload=martial._version(c,"martial_move_versions","move_id",row["id"],row["current_version"])["payload"]
            item={key:payload.get(key,"") for key in martial.MOVE_FIELDS}
            if row["business_label"]:item["chinese_name"]=row["business_label"]
            moves.append(item)
    if not moves:raise ValueError("源功法没有可复制的当前招式")
    return create_moves_batch(user,art_id,{"moves":moves,
                              "source_ref":data.get("source_ref") or "copied_from_art:"+source_id})


def create_moves_batch(user: dict, art_id: str, data: dict) -> dict:
    """Validate and insert the entire employee table in one transaction."""
    _allow(user,True)
    import martial
    moves=data.get("moves")
    if not isinstance(moves,list) or not 1<=len(moves)<=100 or any(not isinstance(x,dict) for x in moves):
        raise ValueError("请提供 1 至 100 条招式")
    source_ref=_clean_text(data.get("source_ref") or "employee_batch_current_business","来源",maximum=1000)
    with store.connect() as c:
        art=martial._row(c,"martial_arts",art_id)
        if art["status"]!="active":raise ValueError("功法尚未生效")
        existing_names=set()
        for row in c.execute("SELECT b.business_label,v.payload FROM martial_business_moves b JOIN martial_moves m ON m.id=b.move_id "
                             "JOIN martial_move_versions v ON v.move_id=m.id AND v.version=m.current_version "
                             "WHERE b.martial_art_id=? AND b.is_current=1",(art_id,)):
            existing_names.add(row["business_label"] or store.parse(row["payload"],{}).get("chinese_name"))
        used_ordinals={r[0] for r in c.execute("SELECT ordinal FROM martial_moves WHERE martial_art_id=?",(art_id,))}
        next_ordinal=max(used_ordinals,default=0)+1
        display_order=c.execute("SELECT COALESCE(MAX(display_order),0) FROM martial_business_moves WHERE martial_art_id=? AND is_current=1",(art_id,)).fetchone()[0]
        prepared=[]
        for row in moves:
            payload={key:_clean_text(row.get(key) or "",key,maximum=3000) for key in martial.MOVE_FIELDS}
            name=payload["chinese_name"]
            if not name or len(name)>120:raise ValueError("招式中文名称无效")
            if name in existing_names:raise ValueError("当前功法已有同名招式："+name)
            if art_id=="beginner" and name not in {x[2] for x in BEGINNER_CURRENT}:
                raise ValueError("当前入门套路仅含马步、冲拳、马步冲拳；新增需先核实业务映射")
            existing_names.add(name)
            raw_order=row.get("order")
            if raw_order not in (None,""):
                try:ordinal=int(raw_order)
                except (TypeError,ValueError):raise ValueError("招式序号无效")
            else:
                ordinal=next_ordinal
                while ordinal in used_ordinals:ordinal+=1
            if not 1<=ordinal<=999 or ordinal in used_ordinals:raise ValueError("招式序号重复或已占用")
            used_ordinals.add(ordinal);next_ordinal=max(next_ordinal,ordinal+1)
            display_order+=1
            prepared.append(("mv_"+secrets.token_hex(8),ordinal,display_order,payload))
        stamp=store.now()
        for move_id,ordinal,order,payload in prepared:
            c.execute("INSERT INTO martial_moves(id,martial_art_id,ordinal,current_version,draft_version,created_at,updated_at) VALUES(?,?,?,?,?,?,?)",
                      (move_id,art_id,ordinal,1,0,stamp,stamp))
            c.execute("INSERT INTO martial_move_versions(move_id,version,payload,status,source_ref,created_by,approved_by,created_at,approved_at) VALUES(?,?,?,?,?,?,?,?,?)",
                      (move_id,1,store.dumps(payload),"active",source_ref,user["id"],user["id"],stamp,stamp))
            c.execute("INSERT INTO martial_business_moves(move_id,martial_art_id,display_order,business_label,is_current,classification,source_ref,updated_at) VALUES(?,?,?,?,?,?,?,?)",
                      (move_id,art_id,order,"",1,"CURRENT",source_ref,stamp))
        store.audit(c,user["id"],"martial.move.batch_create",None,
                    {"art_id":art_id,"count":len(prepared),"move_ids":[x[0] for x in prepared]})
    return {"art_id":art_id,"count":len(prepared),"move_ids":[x[0] for x in prepared],
            "art":martial.art_detail(art_id,user)}


def create_master(user: dict, data: dict) -> dict:
    _allow(user,True)
    import martial
    name=_clean_text(data.get("name") or data.get("english_name") or "","老师名称",required=True,maximum=120)
    species=_clean_text(data.get("species") or "","物种",required=True,maximum=120)
    chinese=_clean_text(data.get("chinese_name") or "","中文名",maximum=120)
    english=_clean_text(data.get("english_name") or name,"英文名",maximum=120)
    forbidden=data.get("forbidden_changes") or []
    if not isinstance(forbidden,list) or len(forbidden)>30 or any(not isinstance(x,str) or len(x)>250 for x in forbidden):
        raise ValueError("禁止变化项无效")
    art_ids=data.get("martial_art_ids") or []
    if not isinstance(art_ids,list) or len(art_ids)>30 or any(not isinstance(x,str) for x in art_ids):
        raise ValueError("对应功法无效")
    payload={key:"" for key in martial.MASTER_FIELDS}
    for key in ("voice_id","profile","biography","personality","world_identity","teaching_style","speaking_style"):
        payload[key]=_clean_text(data.get(key) or "",key,maximum=5000)
    payload.update({"portrait":None,"front_view":None,"side_view":None,"back_view":None,"turnaround":None,
                    "costume":None,"digital_model":None,
                    "voice_preview":None,"forbidden_changes":[x.strip() for x in forbidden if x.strip()],
                    "chinese_name":chinese,"english_name":english,"other_angles":[],"created_in_work_os":True})
    master_id=_id("master");source_ref=_clean_text(data.get("source_ref") or "employee_created_teacher","来源",maximum=1000)
    with store.connect() as c:
        if c.execute("SELECT 1 FROM martial_masters WHERE name=?",(name,)).fetchone():raise ValueError("同名老师已存在")
        for art_id in art_ids:
            art=martial._row(c,"martial_arts",art_id)
            if art["draft_version"]:raise ValueError("对应功法已有待确认的修改，请先处理后再绑定老师")
        stamp=store.now()
        c.execute("INSERT INTO martial_masters(id,name,species,current_version,draft_version,created_at,updated_at) VALUES(?,?,?,?,?,?,?)",
                  (master_id,name,species,1,0,stamp,stamp))
        c.execute("INSERT INTO martial_master_versions(master_id,version,payload,status,source_ref,created_by,approved_by,created_at,approved_at) VALUES(?,?,?,?,?,?,?,?,?)",
                  (master_id,1,store.dumps(payload),"locked",source_ref,user["id"],user["id"],stamp,stamp))
        # Existing art/master binding is canonical.  Keep it active while
        # recording the requested new binding as a reviewable art draft.
        for art_id in dict.fromkeys(art_ids):
            art=martial._row(c,"martial_arts",art_id)
            if art["master_id"]==master_id:continue
            version=max(art["version"],art["draft_version"])+1
            initial_binding=not art["master_id"] and not art["draft_version"]
            art_payload={key:art[key] for key in ("chinese_name","english_name","volume","category","description","planned_moves")}
            art_payload["style_traits"]=store.parse(art["style_traits"],[])
            art_payload["master_id"]=master_id
            c.execute("INSERT INTO martial_art_versions(art_id,version,payload,status,source_ref,created_by,approved_by,created_at,approved_at) VALUES(?,?,?,?,?,?,?,?,?)",
                      (art_id,version,store.dumps(art_payload),"active" if initial_binding else "needs_review",source_ref,user["id"],
                       user["id"] if initial_binding else None,stamp,stamp if initial_binding else None))
            if initial_binding:
                c.execute("UPDATE martial_arts SET master_id=?,version=?,updated_at=? WHERE id=?",(master_id,version,stamp,art_id))
            else:
                c.execute("UPDATE martial_arts SET draft_version=?,updated_at=? WHERE id=?",(version,stamp,art_id))
            store.audit(c,user["id"],"martial.art.initial_master_binding" if initial_binding else "martial.art.master_rebind_draft",
                        None,{"art_id":art_id,"master_id":master_id,"version":version})
        store.audit(c,user["id"],"martial.master.create",None,{"master_id":master_id,"source_ref":source_ref})
    return {"id":master_id,**martial.master_detail(master_id,user)}


def update_master(user: dict, master_id: str, data: dict) -> dict:
    import martial
    return martial.save_master_profile(user,master_id,data)


def upload_master_visual(user: dict, master_id: str, data: dict) -> dict:
    """Initial visual of a new teacher is usable immediately; replacements draft."""
    _allow(user,True)
    import martial
    field=str(data.get("field") or "")
    if field not in UPLOAD_FIELDS:raise ValueError("无效老师素材类型")
    upload=data.get("upload") or {}
    if not isinstance(upload,dict):raise ValueError("图片数据无效")
    name=_clean_text(upload.get("name") or "","图片名称",required=True,maximum=200)
    suffix=Path(name).suffix.lower()
    if field=="voice_preview":
        if suffix not in {".wav",".mp3"}:raise ValueError("语音试听需要 WAV 或 MP3")
    elif field=="digital_model":
        if suffix!=".glb":raise ValueError("数字人模型需要 GLB 文件")
    elif suffix not in {".png",".jpg",".jpeg",".webp"}:raise ValueError("老师形象需要 PNG、JPG 或 WEBP")
    try:content=base64.b64decode(upload.get("base64") or "",validate=True)
    except (TypeError,ValueError):raise ValueError("图片数据无效")
    if not content or len(content)>martial.MARTIAL_FILE_LIMIT:raise ValueError("老师素材无效或超过 40MB 上限")
    if field=="voice_preview":
        if not (content.startswith(b"RIFF") and content[8:12]==b"WAVE" or content.startswith(b"ID3") or content[:2] in {b"\xff\xfb",b"\xff\xf3",b"\xff\xf2"}):
            raise ValueError("语音试听文件格式无效")
    elif field=="digital_model":
        if not martial._glb_header_ok(content):raise ValueError("数字人模型文件格式无效")
    elif not martial._image_header_ok(content,suffix):raise ValueError("图片文件格式无效")
    with store.connect() as c:
        master=martial._row(c,"martial_masters",master_id)
        current=martial._version(c,"martial_master_versions","master_id",master_id,master["current_version"])
        new_teacher=bool(current["payload"].get("created_in_work_os"))
        first_visual=new_teacher and not any(current["payload"].get(key) for key in VISUAL_FIELDS if key!="other_angle") and not current["payload"].get("other_angles")
        if field=="other_angle" and (not new_teacher or master["draft_version"]):
            raise ValueError("定版老师的其他角度新增需要负责人核对")
        if new_teacher and master["draft_version"] and field in {"voice_preview","other_angle"}:
            raise ValueError("老师素材正在审查，请完成当前版本确认")
    asset=store.register_production_asset("wuxiang","character",name,content,user["id"])
    direct_new_asset=new_teacher and not master["draft_version"] and (first_visual or field=="voice_preview" or field=="other_angle" or
                                                                         not current["payload"].get(field))
    if direct_new_asset:
        with store.connect() as c:
            master=martial._row(c,"martial_masters",master_id)
            current=martial._version(c,"martial_master_versions","master_id",master_id,master["current_version"])
            if master["draft_version"]:raise ValueError("老师素材正在审查，请刷新后重试")
            payload=dict(current["payload"])
            if field=="other_angle":
                angles=list(payload.get("other_angles") or [])
                if asset["id"] not in angles:angles.append(asset["id"])
                if len(angles)>20:raise ValueError("其他角度图片最多 20 张")
                payload["other_angles"]=angles
            else:
                if payload.get(field):raise ValueError("已存在该素材，替换需要负责人确认")
                payload[field]=asset["id"]
            version=master["current_version"]+1;stamp=store.now()
            c.execute("INSERT INTO martial_master_versions(master_id,version,payload,status,source_ref,created_by,approved_by,created_at,approved_at) VALUES(?,?,?,?,?,?,?,?,?)",
                      (master_id,version,store.dumps(payload),"locked",str(data.get("source_ref") or current["source_ref"]),user["id"],user["id"],stamp,stamp))
            c.execute("UPDATE martial_masters SET current_version=?,updated_at=? WHERE id=?",(version,stamp,master_id))
            store.audit(c,user["id"],"martial.master.asset_add",None,{"master_id":master_id,"version":version,"field":field,"asset_id":asset["id"]})
        return martial.master_detail(master_id,user)
    return martial.attach_master_asset(user,master_id,{"field":field,"asset_id":asset["id"],"source_ref":data.get("source_ref")})


def audit_history(user: dict, move_id: str) -> dict:
    if user.get("role") not in {"manager","founder"}:raise PermissionError("技术历史仅管理员可查看")
    import martial
    with store.connect() as c:
        martial._row(c,"martial_moves",move_id)
        if not c.execute("SELECT 1 FROM martial_technical_history WHERE move_id=?",(move_id,)).fetchone():
            return {"move_id":move_id,"classification":"NONE","move_versions":[],"motions":[],"packages":[],"media":[],"qc":[],"revision_packages":[],"finals":[]}
        result={"move_id":move_id,"classification":"TECHNICAL_PILOT_HISTORY"}
        result["move_versions"]=[dict(r) for r in c.execute("SELECT * FROM martial_move_versions WHERE move_id=? ORDER BY version",(move_id,))]
        for row in result["move_versions"]:row["payload"]=store.parse(row["payload"],{})
        for key,table in (("motions","martial_motion_refs"),("packages","martial_packages"),
                          ("media","martial_media_jobs"),("qc","martial_qc"),
                          ("finals","martial_final_assets")):
            kind={"motions":"motion_ref","packages":"package","media":"media_job","qc":"qc","finals":"final_asset"}[key]
            rows=[dict(r) for r in c.execute(
                f"SELECT x.* FROM {table} x JOIN martial_technical_history h ON h.kind=? AND h.item_id=x.id "
                "WHERE x.move_id=? ORDER BY x.created_at",(kind,move_id))]
            if key=="media":
                for row in rows:row.pop("prompt",None);row.pop("idempotency_key",None)
            if key=="packages":
                for row in rows:row["facts"]=store.parse(row["facts"],{});row["result"]=store.parse(row["result"],{})
            result[key]=rows
        result["revision_packages"]=[dict(r) for r in c.execute(
            "SELECT r.* FROM martial_revision_packages r JOIN martial_technical_history h "
            "ON h.kind='revision_package' AND h.item_id=r.id WHERE h.move_id=? ORDER BY r.created_at",(move_id,))]
        for row in result["revision_packages"]:row["payload"]=store.parse(row["payload"],{})
        return result


def list_assets(user: dict, filters: dict | None = None) -> dict:
    _allow(user)
    filters=filters or {}
    with store.connect() as c:
        assets=[]
        rows=c.execute("SELECT id,type,name,status,created_at,created_by,storage_ref FROM assets "
                       "WHERE project_id='wuxiang' AND status='active' ORDER BY created_at DESC LIMIT 500").fetchall()
        for row in rows:
            item=dict(row)
            item.pop("storage_ref",None)
            if historical(c,"asset",item["id"]):continue
            master=c.execute("SELECT v.master_id FROM martial_master_versions v JOIN martial_masters m ON m.id=v.master_id "
                             "WHERE v.version=m.current_version AND (json_extract(v.payload,'$.portrait')=? OR json_extract(v.payload,'$.front_view')=? OR json_extract(v.payload,'$.side_view')=? OR json_extract(v.payload,'$.back_view')=? OR json_extract(v.payload,'$.turnaround')=? OR json_extract(v.payload,'$.voice_preview')=? OR json_extract(v.payload,'$.costume')=? OR json_extract(v.payload,'$.digital_model')=? OR EXISTS(SELECT 1 FROM json_each(json_extract(v.payload,'$.other_angles')) WHERE value=?)) LIMIT 1",
                             (item["id"],)*9).fetchone()
            motion=c.execute("SELECT r.move_id FROM martial_motion_refs r WHERE r.video_asset_id=? AND "
                             "NOT EXISTS(SELECT 1 FROM martial_technical_history h WHERE h.kind='motion_ref' AND h.item_id=r.id) "
                             "ORDER BY r.created_at DESC LIMIT 1",(item["id"],)).fetchone()
            candidate=c.execute("SELECT j.move_id FROM martial_media_jobs j WHERE j.candidate_asset_id=? AND "
                                "NOT EXISTS(SELECT 1 FROM martial_technical_history h WHERE h.kind='media_job' AND h.item_id=j.id) "
                                "ORDER BY j.created_at DESC LIMIT 1",(item["id"],)).fetchone()
            move_id=(motion or candidate)[0] if (motion or candidate) else None
            if move_id and not current_move(c,move_id):continue
            item["master_id"]=master["master_id"] if master else None
            item["move_id"]=move_id
            art=c.execute("SELECT martial_art_id FROM martial_moves WHERE id=?",(move_id,)).fetchone() if move_id else None
            item["art_id"]=art[0] if art else None
            item["art_ids"]=[r[0] for r in c.execute("SELECT id FROM martial_arts WHERE master_id=? AND status='active'",(item["master_id"],))] if item["master_id"] else []
            if not item["art_id"] and item["art_ids"]:item["art_id"]=item["art_ids"][0]
            if filters.get("art_id") and filters["art_id"] not in (item["art_ids"] or [item["art_id"]]):continue
            if filters.get("master_id") and item["master_id"]!=filters["master_id"]:continue
            if filters.get("move_id") and item["move_id"]!=filters["move_id"]:continue
            if filters.get("type") and item["type"]!=filters["type"]:continue
            assets.append(item)
        return {"assets":assets}
