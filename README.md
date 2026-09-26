# Martial Arts Lesson Production Pipeline

本仓库承接已运行的 Yoodun Work OS 武学制作与 AI Asset Center，新增正式 `MartialArtsLessonPackage`。一式对应一个可追溯的教学包；视频是其中一个输出。Creative Lab 继续独立做实验，已审定的规则才进入正式制作。原武学功能说明见 [MARTIAL.md](MARTIAL.md)。

## 当前实现

- 功法页面显示每式的生产状态、缺项、负责人和并行可做工作；点击进入教学包，查看资产、镜头、版本、历史样片及人工验收。
- 真人参考和数字老师视频沿用 Work OS 现有上传、动作复刻、Seedance 作业与武术专业 QC。新教学包不绕开现有动作锁定和人工 QC。
- 场景、背景、教学语音、OS、BGM、音效、Prompt、Workflow 等通过同一个 Asset Center 登记；可绑定到整式或具体镜头。精选 Creative Lab 素材可导入同一云端库，正式绑定仍需 Learning Review。上传文件进入公司服务器数据目录 `AI Production Assets`，新版本保留旧版。
- 完整教学 MP4 需先锁定合成输入快照，不能仅把背景和语音单独登记后声称已用于成片。正式批准生成带 SHA-256 的 Manifest；发布前复核真人、老师、合成来源、成片和绑定素材的文件校验和。OS、BGM、SFX 和字幕可暂作占位；真人动作、武术专业验收、教学语音、镜头与成片人工视听验收不能占位。
- [《流云掌》真实素材技术试跑](reports/liuyun_pilot_20260926.json)归集了真人源片、历史动作参考、场景图、语音、字幕和旧视频。该样片存在动作问题，报告明确保持未批准。

## 入口

详细数据契约、岗位操作、验收与部署边界见 [教学包生产说明](docs/LESSON_PIPELINE.md)。批准后的 Manifest 格式见 [schema](schemas/martial_lesson_package.schema.json)，使用 [导出脚本](scripts/export_lesson_manifest.py)存入 Git。媒体资产不进入普通 Git。

本地回归：

```sh
python3 -m unittest discover -s tests -q
python3 -m py_compile app/*.py scripts/*.py
node --check app/static/martial.js
node --check app/static/app.js
```
