"""Register seven selected, hashed source copies and two real pilot tasks."""
from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

import store

BASE=Path(__file__).resolve().parents[1]
ASSETS={
    "wuxiang/cryn-character.jpg":("character","Cryn V2 定版形象参考"),
    "wuxiang/cryn-bagua-part01.mp4":("motion_reference","八卦掌真人动作原片第 1 段（0–15 秒）"),
    "wuxiang/cryn-v2.md":("document","Cryn 角色人物小传 V2"),
    "wuxiang/bagua-review.md":("document","八卦掌 R3 预览偏差与待修记录"),
    "diaojianghu/current-direction.md":("document","钓江湖短剧当前创作方向"),
    "diaojianghu/chapters-01-04.md":("script","钓江湖前四章阅读稿 V1"),
}


def main():
    store.initialize()
    manifest=json.loads((BASE/"seed_assets"/"manifest.json").read_text())
    ids={}
    for entry in manifest:
        rel=entry["bundle_path"]
        if rel not in ASSETS:raise SystemExit("未批准的种子文件："+rel)
        src=BASE/"seed_assets"/rel
        if not src.is_file() or store.digest_file(src)!=entry["sha256"]:
            raise SystemExit("种子文件校验失败："+rel)
        pid=rel.split("/",1)[0]
        dest=store.DATA/"inputs"/rel
        dest.parent.mkdir(parents=True,exist_ok=True)
        if not dest.exists():
            fd=os.open(dest,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
            with os.fdopen(fd,"wb") as out,src.open("rb") as inp:shutil.copyfileobj(inp,out)
        if store.digest_file(dest)!=entry["sha256"]:raise SystemExit("目标文件内容与来源不一致："+rel)
        typ,name=ASSETS[rel]
        with store.connect() as c:
            old=c.execute("SELECT id FROM assets WHERE project_id=? AND name=? AND sha256=? AND status='active'",(pid,name,entry["sha256"])).fetchone()
        if old:ids[rel]=old["id"]
        else:
            asset=store.register_asset(pid,typ,name,str(dest),"u_system",source_refs=[{"source_sha256":entry["sha256"],"source_size":entry["bytes"],"source_name":Path(entry["source_path"]).name}])
            ids[rel]=asset["id"]
    specs=[
        {
            "project_id":"wuxiang","workflow_id":"WF-01",
            "title":"Cryn 八卦掌首段：起势到首轮翻掌的动作样片",
            "why":"验证真人动作与丹顶鹤师傅形象同时锁定时，员工能否按参考制作、提交并经过真实动作与画面验收。原 R3 完整预览在 8–11 秒翼端路径及脚位有偏差，不能直接复用为通过结果。",
            "context":{"pilot_reason":"现有 Cryn V2 角色定版图、原动作参考、R3 缺陷记录齐全；0–11 秒只作为一个连续教学动作单元的工作切段名，不冒充正式招式名。后续所有师傅与功法另建任务，不在本次批量展开。","source_facts":"原片首段 0–15 秒；自然垂翼起势，再抬翼、胸前交叉、翻转、摊开、转体。既有生成在约 8–11 秒动作路径和时刻存在偏差。必须由人对照原片核验。角色当前基线是 2026-09-21 V2；原 2026-09-07 预览仅是未通过成片。"},
            "input_assets":[ids[x] for x in ("wuxiang/cryn-character.jpg","wuxiang/cryn-bagua-part01.mp4","wuxiang/cryn-v2.md","wuxiang/bagua-review.md")],
            "instructions":["先打开定版角色图、真人动作片和缺陷记录，确认只处理 0–11 秒连续动作单元。","依据 AI 工作包在已接通的创作工具做最小样片；任何新增付费生成先由任务负责人核准报价与本任务预算。","保持鹤的完整羽翼、腿爪、比例、服饰和架空武境场景，不出现真人手或现实广场。","对照原片逐段检查起势、交叉、翻掌、摊开、转体的顺序和时刻；提交视频与平台任务编号，写明未达标处。"],
            "character_lock":{"asset_id":ids["wuxiang/cryn-character.jpg"],"identity":"Cryn 丹顶鹤八卦掌师傅，2026-09-21 V2","version":1,"forbidden_changes":["新增真人手或皮肤","替换鹤原形象比例、服饰、喙与翼端","把旧人物设定当 V2"]},
            "motion_lock":{"asset_id":ids["wuxiang/cryn-bagua-part01.mp4"],"move":"起势至首轮翻掌（工作切段名）","start":0,"end":11,"orientation":"沿原片画面方向；镜像未核实，不自行规定左右","key_moments":["双翼自然下垂起势","随真人抬臂再抬翼","胸前交叉／翻转／摊开／转体次序","8–11 秒动作路径重点核对"],"version":1},
            "deliverable_contract":{"type":"video","required":"约 11 秒动作样片、平台任务编号、与真人参考差异说明"},
            "qc_contract":{"criteria":["角色形象锁定","动作顺序和时刻与原片一致","无真人手及现实广场","连续观看与听审","场景及角色连续性"],"min_width":720,"min_height":720,"min_duration":8,"max_duration":16},
            "budget_cap":0,"founder_required":True,
        },
        {
            "project_id":"diaojianghu","workflow_id":"WF-03",
            "title":"《六条鱼还不容易》短剧首段与社媒／应用市场发布候选包",
            "why":"用已确认的周禾、钓神抓吹牛者和第一章素材，形成可实际制作与发布的短剧首段；同时准备社媒和应用市场适配文案，避免继续把旧科普片当作短剧成果。",
            "context":{"draft_with_open_questions":True,"pilot_reason":"当前方向与前四章阅读稿存在且尚未做视频；E02 既有 B 站／小红书／知乎发布记录可作为渠道事实，不视作本片已发布。","business_boundary":"第一章新来客大刘及任务六尾是阅读稿提案，最终人物细节待业务复核；周禾、鱼塘、钓神抓吹牛者为用户当前明确方向。不得虚构 App 已上线功能、投放预算或媒体成效。应用市场材料只做待审候选，不自动上架或付费投放。"},
            "input_assets":[ids[x] for x in ("diaojianghu/current-direction.md","diaojianghu/chapters-01-04.md")],
            "instructions":["阅读当前方向及第一章，不沿用旧票根返场、积分兑换或未确认的 App 功能。","本次 20–30 秒只覆盖吹牛、被抓入鱼塘、任务木牌；AI 台本是较长的素材草案，鱼花、落座和后续钓鱼动作可留到下一段。","如暂无授权媒体预算，先提交可执行台本、分镜和低成本预演并标明视频未完成。","为 B 站、小红书、知乎各准备适配标题和正文，并给应用市场准备单独待审版本；不代表已发布。","提交成品或预演、文案、素材来源与实际发布链接；没有发布链接就写明仍为候选。"],
            "deliverable_contract":{"required":"短剧首段成品或预演、三端社媒文案、应用市场待审文案、素材来源与实际发布状态"},
            "qc_contract":{"criteria":["与当前角色和机制一致","鱼竿、鱼线、鱼钩、鱼获关系可信","台词、动作、声音与剪辑连续","社媒文字与实际成片一致","App 功能及投放说法有证据","发布链接或未发布状态可核"]},
            "budget_cap":0,"founder_required":True,
        },
    ]
    for spec in specs:
        with store.connect() as c:
            old=c.execute("SELECT id FROM tasks WHERE project_id=? AND workflow_id=? AND title=? ORDER BY created_at LIMIT 1",(spec["project_id"],spec["workflow_id"],spec["title"])).fetchone()
        if old:print("已存在试点任务",old["id"])
        else:print("已建立试点任务",store.create_task(spec,"u_system")["id"])


if __name__=="__main__":main()
