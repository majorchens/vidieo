"""Versioned martial media links and OS copy, kept separate from canonical facts.

Only registered assets with a readable local file count as generated media in
completion.  A URL or text script remains a reference or script, never a TTS
record or finished video.  Existing martial production records remain the
authority for motion locks and formally finalized videos.
"""
from __future__ import annotations

import secrets
from pathlib import Path

import store


SCRIPT_KINDS=("intro","instruction","practice","breathing","success")
LANGUAGES=("zh","en")
ART_ROLES={"theme_music":"audio","training_bgm":"audio","logo":"image","main_visual":"image"}
MOVE_ROLES={"training_bgm":"audio","teaching_video":"video","practice_video":"video"}
MASTER_ROLES={"intro_audio":"audio","voice_audition":"audio","voice_reference":"audio"}


def initialize() -> None:
    with store.connect() as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS martial_mm_asset_links(
            id TEXT PRIMARY KEY, scope TEXT NOT NULL, scope_id TEXT NOT NULL,
            role TEXT NOT NULL, version INTEGER NOT NULL, asset_id TEXT NOT NULL REFERENCES assets(id),
            status TEXT NOT NULL, source_ref TEXT NOT NULL, created_by TEXT NOT NULL REFERENCES users(id),
            created_at TEXT NOT NULL, UNIQUE(scope,scope_id,role,version));
        CREATE UNIQUE INDEX IF NOT EXISTS idx_martial_mm_link_active
          ON martial_mm_asset_links(scope,scope_id,role) WHERE status='active';
        CREATE TABLE IF NOT EXISTS martial_mm_art_worldview(
            art_id TEXT NOT NULL REFERENCES martial_arts(id), version INTEGER NOT NULL,
            zh_text TEXT NOT NULL, en_text TEXT NOT NULL, status TEXT NOT NULL,
            source_ref TEXT NOT NULL, created_by TEXT NOT NULL REFERENCES users(id),
            created_at TEXT NOT NULL, PRIMARY KEY(art_id,version));
        CREATE UNIQUE INDEX IF NOT EXISTS idx_martial_mm_worldview_active
          ON martial_mm_art_worldview(art_id) WHERE status='active';
        CREATE TABLE IF NOT EXISTS martial_mm_voice_personas(
            master_id TEXT NOT NULL REFERENCES martial_masters(id), version INTEGER NOT NULL,
            payload TEXT NOT NULL, status TEXT NOT NULL, source_ref TEXT NOT NULL,
            created_by TEXT NOT NULL REFERENCES users(id), created_at TEXT NOT NULL,
            PRIMARY KEY(master_id,version));
        CREATE UNIQUE INDEX IF NOT EXISTS idx_martial_mm_voice_active
          ON martial_mm_voice_personas(master_id) WHERE status='active';
        CREATE TABLE IF NOT EXISTS martial_mm_os_scripts(
            move_id TEXT NOT NULL REFERENCES martial_moves(id), kind TEXT NOT NULL,
            version INTEGER NOT NULL, zh_text TEXT NOT NULL, en_text TEXT NOT NULL,
            app_text TEXT NOT NULL, status TEXT NOT NULL, source_ref TEXT NOT NULL,
            created_by TEXT NOT NULL REFERENCES users(id), created_at TEXT NOT NULL,
            PRIMARY KEY(move_id,kind,version));
        CREATE UNIQUE INDEX IF NOT EXISTS idx_martial_mm_script_active
          ON martial_mm_os_scripts(move_id,kind) WHERE status='active';
        CREATE TABLE IF NOT EXISTS martial_mm_tts_assets(
            id TEXT PRIMARY KEY, move_id TEXT NOT NULL REFERENCES martial_moves(id),
            kind TEXT NOT NULL, language TEXT NOT NULL, script_version INTEGER NOT NULL,
            asset_id TEXT NOT NULL REFERENCES assets(id), version INTEGER NOT NULL,
            status TEXT NOT NULL, source_ref TEXT NOT NULL,
            created_by TEXT NOT NULL REFERENCES users(id), created_at TEXT NOT NULL,
            UNIQUE(move_id,kind,language,version));
        CREATE UNIQUE INDEX IF NOT EXISTS idx_martial_mm_tts_active
          ON martial_mm_tts_assets(move_id,kind,language) WHERE status='active';
        """)


def _allow(user: dict, write: bool=False) -> None:
    import martial
    martial.allow(user,write)


def _clean(value, label: str, maximum: int=5000) -> str:
    if value is None:return ""
    if not isinstance(value,str):raise ValueError(label+"格式无效")
    value=value.strip()
    if len(value)>maximum:raise ValueError(label+"过长")
    return value


def _source(data: dict) -> str:
    return _clean(data.get("source_ref") or "work_os_v0.3", "来源",1000)


def _record(c,table: str,item_id: str) -> dict:
    if table not in {"martial_arts","martial_masters","martial_moves"}:raise ValueError("资产层级无效")
    row=c.execute("SELECT * FROM "+table+" WHERE id=?",(item_id,)).fetchone()
    if row is None:raise KeyError(item_id)
    return dict(row)


def _scope(c,scope: str,item_id: str) -> dict:
    tables={"art":"martial_arts","master":"martial_masters","move":"martial_moves"}
    if scope not in tables:raise ValueError("资产层级无效")
    row=_record(c,tables[scope],item_id)
    if scope=="move":
        import martial_product
        if not martial_product.current_move(c,item_id):raise ValueError("历史招式不能作为当前数字资产编辑")
    return row


def _asset(c,asset_id: str,expected_type: str) -> dict:
    row=c.execute("SELECT id,type,name,status,project_id,storage_ref FROM assets WHERE id=?",(asset_id,)).fetchone()
    if row is None:raise KeyError(asset_id)
    asset=dict(row)
    if asset["project_id"]!="wuxiang" or asset["status"]!="active" or asset["type"]!=expected_type:
        raise ValueError("素材不属于当前武境项目、类型不符或未生效")
    import martial_product
    if martial_product.historical(c,"asset",asset_id):raise ValueError("技术试点历史素材不可用于当前业务资产")
    return asset


def _asset_view(c,asset_id: str | None) -> dict | None:
    if not asset_id:return None
    row=c.execute("SELECT id,type,name,status,project_id,storage_ref FROM assets WHERE id=?",(asset_id,)).fetchone()
    if row is None:return {"asset_id":asset_id,"name":"素材已缺失","type":None,"available":False,"evidence":"missing"}
    item=dict(row)
    local=not item["storage_ref"].startswith(("http://","https://","studio://"))
    available=False
    if item["project_id"]=="wuxiang" and item["status"]=="active" and local:
        path=Path(item["storage_ref"])
        try:
            with path.open("rb") as stream:header=stream.read(256)
            suffix=path.suffix.lower()
            if item["type"] in {"image","character"}:
                import martial
                available=martial._image_header_ok(header,suffix)
            elif item["type"]=="video":
                available=suffix in {".mp4",".mov"} and len(header)>=12 and header[4:8]==b"ftyp"
            elif item["type"]=="audio":
                available=(suffix==".wav" and header[:4]==b"RIFF" and header[8:12]==b"WAVE") or (
                    suffix==".mp3" and (header[:3]==b"ID3" or len(header)>=2 and header[0]==0xff and header[1]&0xe0==0xe0))
            else:available=bool(header)
        except OSError:available=False
    evidence="local_media" if available else "external_reference" if not local else "invalid_or_missing_file"
    return {"asset_id":item["id"],"name":item["name"],"type":item["type"],"available":available,"evidence":evidence}


def _link(c,scope: str,item_id: str,role: str) -> dict | None:
    row=c.execute("SELECT asset_id,version,source_ref,created_at FROM martial_mm_asset_links WHERE scope=? AND scope_id=? AND role=? AND status='active'",
                  (scope,item_id,role)).fetchone()
    if row is None:return None
    result=dict(row);result.update(_asset_view(c,result["asset_id"]) or {})
    return result


def _voice_sample(c,master_id: str,role: str,suffix: str) -> dict | None:
    sample=_link(c,"master",master_id,role)
    if not sample:return None
    asset=c.execute("SELECT type,storage_ref FROM assets WHERE id=?",(sample["asset_id"],)).fetchone()
    if not asset or asset["type"]!="audio" or Path(asset["storage_ref"]).suffix.lower()!=suffix:
        sample["available"]=False
        sample["evidence"]="wrong_audio_format"
    return sample


def _set_link(user: dict,scope: str,item_id: str,role: str,asset_id: str | None,expected: str,source: str) -> None:
    _allow(user,True)
    with store.connect() as c:
        _scope(c,scope,item_id)
        if asset_id:_asset(c,asset_id,expected)
        old=_link(c,scope,item_id,role)
        if old and old["asset_id"]==asset_id:return
        if old:c.execute("UPDATE martial_mm_asset_links SET status='superseded' WHERE scope=? AND scope_id=? AND role=? AND status='active'",(scope,item_id,role))
        if asset_id:
            version=c.execute("SELECT COALESCE(MAX(version),0)+1 FROM martial_mm_asset_links WHERE scope=? AND scope_id=? AND role=?",(scope,item_id,role)).fetchone()[0]
            c.execute("INSERT INTO martial_mm_asset_links(id,scope,scope_id,role,version,asset_id,status,source_ref,created_by,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
                      ("mma_"+secrets.token_hex(8),scope,item_id,role,version,asset_id,"active",source,user["id"],store.now()))
        store.audit(c,user["id"],"martial.multimodal.asset_link",None,{"scope":scope,"scope_id":item_id,"role":role,"asset_id":asset_id})


def set_art_asset(user: dict,art_id: str,data: dict) -> dict:
    role=_clean(data.get("role"),"资产用途",80)
    if role not in ART_ROLES:raise ValueError("功法资产用途无效")
    asset_id=data.get("asset_id") or None
    _set_link(user,"art",art_id,role,asset_id,ART_ROLES[role],_source(data))
    return art_assets(user,art_id)


def set_move_asset(user: dict,move_id: str,data: dict) -> dict:
    role=_clean(data.get("role"),"资产用途",80)
    if role not in MOVE_ROLES:raise ValueError("招式资产用途无效")
    asset_id=data.get("asset_id") or None
    _set_link(user,"move",move_id,role,asset_id,MOVE_ROLES[role],_source(data))
    return move_assets(user,move_id)


def set_master_intro_audio(user: dict,master_id: str,data: dict) -> dict:
    asset_id=_clean(data.get("asset_id"),"介绍语音素材",100)
    if not asset_id:raise ValueError("请选择已登记的介绍语音")
    _set_link(user,"master",master_id,"intro_audio",asset_id,"audio",_source(data))
    return master_assets(user,master_id)


def set_art_worldview(user: dict,art_id: str,data: dict) -> dict:
    _allow(user,True)
    zh=_clean(data.get("zh_text"),"中文世界观说明",10000)
    en=_clean(data.get("en_text"),"英文世界观说明",10000)
    if not zh and not en:raise ValueError("请填写功法世界观说明")
    with store.connect() as c:
        _scope(c,"art",art_id)
        old=c.execute("SELECT zh_text,en_text FROM martial_mm_art_worldview WHERE art_id=? AND status='active'",(art_id,)).fetchone()
        if old and (old["zh_text"],old["en_text"])==(zh,en):return art_assets(user,art_id)
        if old and user["role"]=="employee":raise PermissionError("修改已登记世界观说明需负责人确认")
        version=c.execute("SELECT COALESCE(MAX(version),0)+1 FROM martial_mm_art_worldview WHERE art_id=?",(art_id,)).fetchone()[0]
        c.execute("UPDATE martial_mm_art_worldview SET status='superseded' WHERE art_id=? AND status='active'",(art_id,))
        c.execute("INSERT INTO martial_mm_art_worldview(art_id,version,zh_text,en_text,status,source_ref,created_by,created_at) VALUES(?,?,?,?,?,?,?,?)",
                  (art_id,version,zh,en,"active",_source(data),user["id"],store.now()))
        store.audit(c,user["id"],"martial.multimodal.worldview",None,{"art_id":art_id,"version":version})
    return art_assets(user,art_id)


def set_master_voice_persona(user: dict,master_id: str,data: dict) -> dict:
    _allow(user,True)
    updates={key:_clean(data[key],key,2000 if key=="persona" else 300)
             for key in ("voice_id","persona","language","tone","pace") if key in data}
    if not updates:raise ValueError("请填写 Voice Persona 或 Voice ID")
    with store.connect() as c:
        master=_scope(c,"master",master_id)
        current=c.execute("SELECT payload FROM martial_master_versions WHERE master_id=? AND version=?",
                          (master_id,master["current_version"])).fetchone()
        baseline=store.parse(current["payload"],{}) if current else {}
        previous=c.execute("SELECT payload FROM martial_mm_voice_personas WHERE master_id=? AND status='active'",(master_id,)).fetchone()
        existing=store.parse(previous["payload"],{}) if previous else {}
        payload={key:"" for key in ("voice_id","persona","language","tone","pace")}
        payload.update(existing);payload.update(updates)
        locked_voice=existing.get("voice_id") or baseline.get("voice_id") or ""
        if "voice_id" in updates and locked_voice and payload["voice_id"]!=locked_voice and user["role"]=="employee":
            raise PermissionError("更换定版老师声线需负责人确认")
        if previous and existing==payload:return master_assets(user,master_id)
        version=c.execute("SELECT COALESCE(MAX(version),0)+1 FROM martial_mm_voice_personas WHERE master_id=?",(master_id,)).fetchone()[0]
        c.execute("UPDATE martial_mm_voice_personas SET status='superseded' WHERE master_id=? AND status='active'",(master_id,))
        c.execute("INSERT INTO martial_mm_voice_personas(master_id,version,payload,status,source_ref,created_by,created_at) VALUES(?,?,?,?,?,?,?)",
                  (master_id,version,store.dumps(payload),"active",_source(data),user["id"],store.now()))
        store.audit(c,user["id"],"martial.multimodal.voice_persona",None,{"master_id":master_id,"version":version})
    return master_assets(user,master_id)


def save_os_script(user: dict,move_id: str,data: dict) -> dict:
    _allow(user,True)
    kind=_clean(data.get("kind"),"OS Script 类型",80)
    if kind not in SCRIPT_KINDS:raise ValueError("OS Script 类型无效")
    fields={key:_clean(data.get(key),key,10000) for key in ("zh_text","en_text","app_text")}
    if not any(fields.values()):raise ValueError("OS Script 内容不可全空")
    with store.connect() as c:
        _scope(c,"move",move_id)
        previous=c.execute("SELECT version,zh_text,en_text,app_text FROM martial_mm_os_scripts WHERE move_id=? AND kind=? AND status='active'",(move_id,kind)).fetchone()
        if previous and all(previous[key]==fields[key] for key in fields):return move_assets(user,move_id)
        version=c.execute("SELECT COALESCE(MAX(version),0)+1 FROM martial_mm_os_scripts WHERE move_id=? AND kind=?",(move_id,kind)).fetchone()[0]
        c.execute("UPDATE martial_mm_os_scripts SET status='superseded' WHERE move_id=? AND kind=? AND status='active'",(move_id,kind))
        c.execute("UPDATE martial_mm_tts_assets SET status='superseded' WHERE move_id=? AND kind=? AND status='active'",(move_id,kind))
        c.execute("INSERT INTO martial_mm_os_scripts(move_id,kind,version,zh_text,en_text,app_text,status,source_ref,created_by,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
                  (move_id,kind,version,fields["zh_text"],fields["en_text"],fields["app_text"],"active",_source(data),user["id"],store.now()))
        store.audit(c,user["id"],"martial.multimodal.os_script",None,{"move_id":move_id,"kind":kind,"version":version})
    return move_assets(user,move_id)


def link_tts_asset(user: dict,move_id: str,data: dict) -> dict:
    _allow(user,True)
    kind=_clean(data.get("kind"),"OS Script 类型",80)
    language=_clean(data.get("language"),"语种",20)
    if kind not in SCRIPT_KINDS or language not in LANGUAGES:raise ValueError("TTS 类型或语种无效")
    asset_id=_clean(data.get("asset_id"),"TTS 音频素材",100)
    if not asset_id:raise ValueError("请选择真实 TTS 音频素材")
    with store.connect() as c:
        _scope(c,"move",move_id);_asset(c,asset_id,"audio")
        script=c.execute("SELECT version,zh_text,en_text FROM martial_mm_os_scripts WHERE move_id=? AND kind=? AND status='active'",(move_id,kind)).fetchone()
        if not script or not script["zh_text" if language=="zh" else "en_text"]:
            raise ValueError("请先保存当前语种的 OS Script")
        old=c.execute("SELECT asset_id,script_version FROM martial_mm_tts_assets WHERE move_id=? AND kind=? AND language=? AND status='active'",(move_id,kind,language)).fetchone()
        if old and (old["asset_id"],old["script_version"])==(asset_id,script["version"]):return move_assets(user,move_id)
        version=c.execute("SELECT COALESCE(MAX(version),0)+1 FROM martial_mm_tts_assets WHERE move_id=? AND kind=? AND language=?",(move_id,kind,language)).fetchone()[0]
        c.execute("UPDATE martial_mm_tts_assets SET status='superseded' WHERE move_id=? AND kind=? AND language=? AND status='active'",(move_id,kind,language))
        c.execute("INSERT INTO martial_mm_tts_assets(id,move_id,kind,language,script_version,asset_id,version,status,source_ref,created_by,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                  ("mmt_"+secrets.token_hex(8),move_id,kind,language,script["version"],asset_id,version,"active",_source(data),user["id"],store.now()))
        store.audit(c,user["id"],"martial.multimodal.tts",None,{"move_id":move_id,"kind":kind,"language":language,"asset_id":asset_id,"script_version":script["version"]})
    return move_assets(user,move_id)


def _art_view(c,art_id: str) -> dict:
    art=_scope(c,"art",art_id)
    worldview=c.execute("SELECT version,zh_text,en_text,source_ref FROM martial_mm_art_worldview WHERE art_id=? AND status='active'",(art_id,)).fetchone()
    return {"art_id":art_id,"name":art["chinese_name"],"description":art["description"],
            "worldview":dict(worldview) if worldview else None,
            "assets":{role:_link(c,"art",art_id,role) for role in ART_ROLES}}


def art_assets(user: dict,art_id: str) -> dict:
    _allow(user)
    with store.connect() as c:return _art_view(c,art_id)


def _master_view(c,master_id: str) -> dict:
    master=_scope(c,"master",master_id)
    row=c.execute("SELECT payload FROM martial_master_versions WHERE master_id=? AND version=?",(master_id,master["current_version"])).fetchone()
    payload=store.parse(row["payload"],{}) if row else {}
    persona=c.execute("SELECT version,payload,source_ref FROM martial_mm_voice_personas WHERE master_id=? AND status='active'",(master_id,)).fetchone()
    voice=store.parse(persona["payload"],{}) if persona else {}
    visuals={key:_asset_view(c,payload.get(key)) for key in ("portrait","front_view","side_view","back_view","turnaround")}
    visuals["other_angles"]=[_asset_view(c,asset_id) for asset_id in payload.get("other_angles") or []]
    return {"master_id":master_id,"name":master["name"],"version":master["current_version"],
            "visuals":visuals,"profile":{key:payload.get(key) or "" for key in ("profile","biography","personality","world_identity","teaching_style","speaking_style")},
            "voice_id":voice.get("voice_id") or payload.get("voice_id") or "",
            "voice_id_source":"voice_persona" if voice.get("voice_id") else "master",
            "voice_persona":({"version":persona["version"],"source_ref":persona["source_ref"],**voice} if persona else None),
            "voice_preview":_asset_view(c,payload.get("voice_preview")),
            "voice_audition":_voice_sample(c,master_id,"voice_audition",".mp3"),
            "voice_reference":_voice_sample(c,master_id,"voice_reference",".wav"),
            "intro_audio":_link(c,"master",master_id,"intro_audio")}


def master_assets(user: dict,master_id: str) -> dict:
    _allow(user)
    with store.connect() as c:return _master_view(c,master_id)


def _script_view(c,move_id: str,kind: str) -> dict:
    row=c.execute("SELECT version,zh_text,en_text,app_text,source_ref,created_at FROM martial_mm_os_scripts WHERE move_id=? AND kind=? AND status='active'",(move_id,kind)).fetchone()
    result={"kind":kind,"version":row["version"] if row else None,"zh_text":row["zh_text"] if row else "",
            "en_text":row["en_text"] if row else "","app_text":row["app_text"] if row else "",
            "source_ref":row["source_ref"] if row else None,"tts":{}}
    for language in LANGUAGES:
        tts=c.execute("SELECT asset_id,script_version,version FROM martial_mm_tts_assets WHERE move_id=? AND kind=? AND language=? AND status='active'",(move_id,kind,language)).fetchone()
        audio=_asset_view(c,tts["asset_id"]) if tts else None
        result["tts"][language]={"asset":audio,"script_version":tts["script_version"] if tts else None,
                                  "current":bool(row and tts and tts["script_version"]==row["version"] and audio and audio["available"])}
    return result


def _effective_video(c,move_id: str,role: str) -> dict:
    asset_type="teaching" if role=="teaching_video" else "practice"
    import martial_product
    finals=[dict(r) for r in c.execute(
        "SELECT id,asset_id,status,created_at,media_job_id FROM martial_final_assets "
        "WHERE move_id=? AND asset_type=? AND status='active' ORDER BY created_at DESC",
        (move_id,asset_type)) if not martial_product.historical(c,"final_asset",r["id"])]
    retained=finals[0] if finals else None
    final=retained
    # Older isolated databases may not have video plans yet. Once the new
    # schema exists, use the production validator so this completion count
    # agrees with the move overview, including measured video duration.
    has_plans=c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='martial_video_plans'").fetchone()
    if has_plans:
        job_columns={row[1] for row in c.execute("PRAGMA table_info(martial_media_jobs)")}
        final=None
        if "video_plan_id" in job_columns:
            import martial
            for item in finals:
                job=c.execute("SELECT * FROM martial_media_jobs WHERE id=?",(item["media_job_id"],)).fetchone()
                if job and martial._current_final_error(c,dict(job)) is None:
                    final=item
                    break
    historical={"asset":_asset_view(c,retained["asset_id"]),"final_asset_id":retained["id"]} if retained and retained!=final else None
    if final:
        return {"asset":_asset_view(c,final["asset_id"]),"status":"final","final_asset_id":final["id"],
                "source":"martial_qc","historical_final":historical}
    link=_link(c,"move",move_id,role)
    return {"asset":link,"status":"linked" if link else "missing","final_asset_id":None,
            "source":"move" if link else None,"historical_final":historical}


def _completion(art: dict,master: dict | None,move: dict,motion: dict | None,bgm: dict,videos: dict,scripts: dict) -> dict:
    evidence=[]
    def add(group,key,label,ready,asset=None,source=None):
        evidence.append({"group":group,"key":key,"label":label,"ready":bool(ready),
                         "asset_id":asset.get("asset_id") if asset else None,"source":source})
    for role,label in (("theme_music","主题音乐"),("training_bgm","训练 BGM"),("logo","Logo"),("main_visual","功法主视觉")):
        asset=art["assets"][role]
        add("art",role,label,asset and asset["available"],asset,"art")
    add("art","description","功法说明",bool(art["description"]),None,"art")
    add("art","worldview","世界观说明",bool(art["worldview"] and (art["worldview"]["zh_text"] or art["worldview"]["en_text"])),None,"art")
    if master:
        visual=next((master["visuals"].get(key) for key in ("portrait","front_view","side_view","back_view","turnaround") if master["visuals"].get(key) and master["visuals"][key]["available"]),None)
        add("master","visual","老师主形象",bool(visual),visual,"master")
        add("master","voice_id","Voice ID",bool(master["voice_id"]),None,master["voice_id_source"])
        add("master","voice_persona","Voice Persona",bool(master["voice_persona"] and master["voice_persona"].get("persona")),None,"master")
        audio=master["intro_audio"]
        add("master","intro_audio","老师介绍语音",audio and audio["available"],audio,"master")
    else:
        for key,label in (("visual","老师主形象"),("voice_id","Voice ID"),("voice_persona","Voice Persona"),("intro_audio","老师介绍语音")):
            add("master",key,label,False)
    facts=move["facts"]
    add("move","action_standard","动作标准",bool(facts.get("chinese_action") and facts.get("english_action")),None,"canonical_fact")
    motion_asset=motion["asset"] if motion else None
    add("move","motion_reference","真人动作参考",bool(motion_asset and motion_asset["available"]),motion_asset,"motion_lock")
    add("move","effective_bgm","有效训练 BGM",bool(bgm["asset"] and bgm["asset"]["available"]),bgm["asset"],bgm["source"])
    for role,label in (("teaching","教学视频"),("practice","演练视频")):
        video=videos[role]["asset"]
        add("move",role+"_video",label,bool(video and video["available"]),video,videos[role]["source"])
    for kind in SCRIPT_KINDS:
        script=scripts[kind]
        add("script",kind+"_os",kind.upper()+" OS Script",bool(script["zh_text"] and script["en_text"] and script["app_text"]),None,"move")
        for language in LANGUAGES:
            tts=script["tts"][language]
            add("tts",kind+"_"+language,kind.upper()+" "+language.upper()+" TTS",tts["current"],tts["asset"],"move")
    groups={group:{"ready_count":sum(x["ready"] for x in evidence if x["group"]==group),
                   "total_count":sum(x["group"]==group for x in evidence)} for group in ("art","master","move","script","tts")}
    ready=sum(x["ready"] for x in evidence)
    return {"ready_count":ready,"total_count":len(evidence),"percent":round(ready*100/len(evidence)) if evidence else 0,
            "groups":groups,"evidence":evidence}


def move_assets(user: dict,move_id: str) -> dict:
    _allow(user)
    with store.connect() as c:
        move=_scope(c,"move",move_id)
        art=_art_view(c,move["martial_art_id"])
        art_row=_record(c,"martial_arts",move["martial_art_id"])
        master=_master_view(c,art_row["master_id"]) if art_row["master_id"] else None
        version=c.execute("SELECT payload FROM martial_move_versions WHERE move_id=? AND version=?",(move_id,move["current_version"])).fetchone()
        facts=store.parse(version["payload"],{}) if version else {}
        import martial_product
        locked=next((r for r in c.execute("SELECT id,version,video_asset_id,start_time,end_time,orientation,created_at FROM martial_motion_refs WHERE move_id=? AND status='locked' ORDER BY version DESC",(move_id,))
                     if not martial_product.historical(c,"motion_ref",r["id"])),None)
        motion={"id":locked["id"],"version":locked["version"],"asset":_asset_view(c,locked["video_asset_id"]),
                "start_time":locked["start_time"],"end_time":locked["end_time"],"orientation":locked["orientation"]} if locked else None
        override=_link(c,"move",move_id,"training_bgm")
        default=art["assets"]["training_bgm"]
        bgm={"art_default_asset_id":default["asset_id"] if default else None,
             "move_override_asset_id":override["asset_id"] if override else None,
             "effective_asset_id":(override or default)["asset_id"] if override or default else None,
             "source":"move" if override else "art" if default else None,"asset":override or default}
        videos={"teaching":_effective_video(c,move_id,"teaching_video"),"practice":_effective_video(c,move_id,"practice_video")}
        scripts={kind:_script_view(c,move_id,kind) for kind in SCRIPT_KINDS}
        move_info={"move_id":move_id,"art_id":move["martial_art_id"],"facts":facts,"motion_reference":motion}
        completion=_completion(art,master,move_info,motion,bgm,videos,scripts)
        voice_inheritance={"source":"master" if master else None,"master_id":master["master_id"] if master else None,
                           "voice_id":master["voice_id"] if master else "",
                           "voice_persona":master["voice_persona"] if master else None,
                           "voice_audition":master["voice_audition"] if master else None,
                           "voice_reference":master["voice_reference"] if master else None,
                           "intro_audio":master["intro_audio"] if master else None}
        return {"move_id":move_id,"art":art,"master":master,"voice_inheritance":voice_inheritance,"bgm":bgm,"videos":videos,
                "motion_reference":motion,"scripts":scripts,"completion":completion}
