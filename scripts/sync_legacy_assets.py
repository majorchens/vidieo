#!/usr/bin/env python3
"""Index old AI studio assets in Work OS without copying media or reading secrets."""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path


def main() -> int:
    parser=argparse.ArgumentParser()
    parser.add_argument("--legacy-db",type=Path,required=True)
    parser.add_argument("--legacy-data-dir",type=Path,required=True)
    parser.add_argument("--work-os-data-dir",type=Path,required=True)
    parser.add_argument("--report",type=Path,required=True)
    args=parser.parse_args()
    if not args.legacy_db.is_file():parser.error("legacy database not found")
    if not args.legacy_db.resolve().is_relative_to(args.legacy_data_dir.resolve()):
        parser.error("legacy database must be within legacy data directory")
    os.environ["YOODUN_DATA_DIR"]=str(args.work_os_data_dir.resolve())
    os.environ["YOODUN_LEGACY_DATA_DIR"]=str(args.legacy_data_dir.resolve())
    sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"app"))
    import store
    import asset_center

    if not store.DB.is_file():parser.error("Work OS database not found")
    counts=asset_center.import_legacy(args.legacy_db,args.legacy_data_dir)
    lines=["# Legacy AI Asset Migration Report","",
           "旧武境 AI 创作中心 → Yoodun Work OS Asset Registry。只同步索引与来源引用；没有复制原媒体文件。","",
           "## 数量", "",
           "| 项目 | 数量 |", "| --- | ---: |",
           f"| 发现资产 | {counts['discovered']} |",
           f"| 新建统一索引 | {counts['imported']} |",
           f"| SHA / 同一作业去重 | {counts['deduplicated']} |",
           f"| 已存在的来源记录 | {counts['already_indexed']} |",
           f"| 无法识别 | {counts['unrecognized']} |",
           f"| 安全过滤 | {counts['safety_filtered']} |",
           f"| 图片 | {counts['images']} |",
           f"| 视频 | {counts['videos']} |",
           f"| 音频 | {counts['audios']} |",
           f"| 文档 | {counts['documents']} |",
           f"| Prompt | {counts['prompts']} |",
           f"| 角色资产 | {counts['character_assets']} |", "",
           "## 项目映射", ""]
    for project,number in sorted(counts["project_map"].items()):
        lines.append(f"- {project}: {number}")
    if not counts["project_map"]:lines.append("- 无可映射资产")
    lines += ["", "## 未映射/不可预览资产", ""]
    for item in counts["unmapped_assets"][:100]:
        lines.append(f"- `{item['source_system']}/{item['original_id']}`：{item['reason']}")
    if len(counts["unmapped_assets"])>100:lines.append(f"- 其余 {len(counts['unmapped_assets'])-100} 项见 JSON 统计。")
    if not counts["unmapped_assets"]:lines.append("- 无")
    lines += ["", "## 去重与访问边界", "",
              "同一来源 ID 只登记一次；可取得 SHA-256 时，再按同项目、同类型内容哈希复用索引。旧 OSS 对象没有可验证的 SHA-256 时保留原系统 ID 引用，不假装已计算哈希。",
              "旧媒体仍归原系统管理。服务器本地可读取的生成结果可以直接预览；仅存于私有 OSS、没有可用签名链路的对象在素材库中保留来源信息与状态，不显示假预览。", ""]
    args.report.parent.mkdir(parents=True,exist_ok=True)
    args.report.write_text("\n".join(lines),encoding="utf-8")
    args.report.with_suffix(".json").write_text(json.dumps(counts,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps({k:v for k,v in counts.items() if k!="unmapped_assets"},ensure_ascii=False))
    return 0


if __name__=="__main__":raise SystemExit(main())
