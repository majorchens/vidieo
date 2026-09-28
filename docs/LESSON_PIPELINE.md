# 功法教学生产线｜现行实现与操作

## 与现有系统的关系

本实现沿用 Work OS 的功法字典、老师 Character、真人动作参考、视频计划、Runy Seedance 作业、候选、武术 QC、AI Asset Center 与员工权限。`app/lesson_pipeline.py`负责教学包依赖、整式/镜头素材版本、成片人工复核及批准发布。没有替换原视频流程或启动 Creative Lab 批次。WF-01 仍是武学制作工作流；新教学包把 WF-01 已完成的事实与视频结果组合成产品内容。

## Production Validation：真人岗位任务

《流云掌第一式》在服务器启动时幂等登记 14 项真实 Work OS 任务；新功法由负责人在教学包点击“建立或核对岗位任务”。它们复用现有任务分派、员工提交、技术检查、人工复核和返修记录。员工从“功法生产任务”领取 Ready 任务；Waiting 逐项说明前置缺项。真人动作上传/确认和动作专业 Review 仅武术岗位可领取；美术、运营、制作岗位领取对应交付任务并在教学包绑定实际资产。负责人也可在现有任务页指派。已领取任务进入“我的任务”，可提交文件或可追溯交付物、人工复核；必需阶段对应的真实资产未具备时，人工复核不能判通过。所有必需岗位任务验收完成后，教学包才可批准。OS/BGM/SFX/字幕可以在正式教学包中显式占位，但不能伪称已有音轨。

看板并列显示任务状态 Ready / Waiting / Blocked / Review / Approved、负责人、缺项和实际资产状态。人工任务验收与资产门槛是两个独立事实；提交一个文件不会自动使动作、老师或成片通过。任务通过时锁定本阶段资产与审核指纹；随后换版会自动转为返修待验收。武术 QC 问题区间保存起止时间、招式 ID、身体部位、类型、严重度、问题和具体返修意见；失败会生成基于原候选及原标准动作的返修包，新候选保留旧版和 Review 记录。没有已确认真人 Motion REF、当前老师和专业 QC 时，生产线不会提交付费生成或升级历史样片。

本式计量显示已登记真人拍摄分钟数、动作复刻与视频作业、武术 Review、美术/教学语音版本、合成与成片 QA 次数、批准时间、已取得的 API 实扣及缺账单作业数、人工作业事件数。技术历史作业从正式制作次数中排除；未登记人时和未返回的实扣不推测。负责人可据此做单片复盘，目前不做 BI。

现场试生产发现普通参考视频生成与 2026-09-28 视频编辑试拍输入不同。视频制作页现有独立的“视频编辑试拍”入口：先在教学包将含老师的场景图和纯背景图分别关联为 `pilot_scene`、`pilot_background`；两张图只作试拍参考，不满足正式美术依赖。员工选用已锁定真人视频区间，超过 30 秒时须填真人确认的自然切点；连接器按原片区间裁为 1080 边长、30fps 的参考片，向润元 Seedance 2.5 以 `duration=-1`、`ratio=adaptive`、无音轨及两张图片参考提交 `video_edit`。每段保存原片区间、图版本与哈希、Prompt 哈希、供应商任务与费用记录。只有真人源哈希及切点匹配时，才附上本片已核对的动作阶段提示；其他功法使用通用复刻提示，不猜动作时码。该入口生成的 `edit_trial` 候选只能对照，不能选用、定版、计入 `motion_mapping` 或解除 Production Run 的 Blocked。正式教学仍须美术正式背景与场景、武术 Review、声音和最终视听验收。供应商参考生成不保证严格逐帧 1:1。

《流云掌第二式》试生产要求讲解和跟练分别上传真人视频。上传并确认两条参考后，在“参考片段”步骤的讲解、跟练卡片各自选择对应文件、核对区间并保存；两项都已保存才可进入 AI 准备。旧单参考内容继续按原版本追溯。本次现场问题与验证范围见 [Trial Issue Log](TRIAL_ISSUES.md)。

公司工作流的润元适配器须支持这一视频编辑参数组合。当前运行代码已更新；其源目录暂非 Git 仓库，恢复时应对该目录应用 `deploy/patches/company-workflow-runy-video-edit.patch`，再运行该工作流的 `tests/test_runy_motion_references.py` 并重启服务。补丁仅放行 SD2.5、单条真人参考视频、`duration=-1` 与 `ratio=adaptive` 同时满足的请求。

Creative Lab 的本地实验文件经选择后，用 `scripts/import_creative_lab_asset.py`通过 Work OS 登录上传到同一个云端 Asset Center，记录实验编号、原相对路径和 SHA-256；重复导入同源同哈希文件会复用登记。导入仅产生实验素材，正式教学包拒绝绑定未复核的实验资产。负责人审查 Learning Review 并记录具体依据后可单独批准该资产进入正式制作。实验结论还须明确适用范围和反例、人工确认后，才写入 Git 中 `app/production_rules.json`，并把对应 Prompt/Workflow 版本绑定到正式教学包。程序只读取 `active` 且具备审查来源、审查人、证据哈希、适用条件和反例的规则，批准 Manifest 锁定实际使用的规则。当前规则登记为空：Batch 001 的通用“结果变化可见”结论仍是导演参考，尚无下一批作品证明其正式跨批生产效果，不等于《流云掌》标准动作已通过验证；Batch 002 不会自动晋级。本仓库不读取或更改正在运行的 Batch 002 状态。

例：`python3 scripts/import_creative_lab_asset.py --username <Work OS账号> --project-id wuxiang --experiment-id W01 --source-ref 'Creative Lab/Wushu/W01/sample.jpg' --file '<本地素材路径>'`。脚本交互输入现有密码，不保存密码。负责人已有 Learning Review 结论时，可追加 `--learning-review-ref 'Creative Lab/Batch_001_Learning_Review.md' --review-notes '<实际审查依据>'`；仅导入不会自动批准或生成媒体。

## 数据与存储

`Martial Art → Chapter → Lesson (=当前 Move) → Shot → Asset`。一套功法的聚合入口是 `/api/martial/lesson-dashboard/{art_id}`；每一式教学包为 `/api/martial/lessons/{move_id}`。招式中文名、动作、教学话术保留在已确认的 `martial_move_versions`。一个镜头记录镜号、目的、起止状态、机位与时长，修改会生成新版本。素材可绑定到整式或镜头，`martial_lesson_asset_versions`保留每次绑定及其源 Asset Center 版本、哈希与创建人。Asset Center 支持 `lesson_id`、`shot_id` 查询。

Git 存放代码、Workflow/Prompt、Schema、审查过的 Production Rule、Manifest、试跑报告、部署脚本。公司服务器 `/var/lib/yoodun-work-os/AI Production Assets/wuxiang/`存放实际图片、音频、真人参考、候选、历史样片和成片；Work OS SQLite 记录资产与批准状态。正式 Manifest 中的 `work-os://` Asset ID、角色用途、版本和 SHA-256 对应云端文件。批准后可用 `scripts/export_lesson_manifest.py --data-dir <受控数据目录> --lesson-id <教学包ID> --out manifests/<名称>.json`导出供 Git 审查；导出脚本只接受哈希完整的已批准 Manifest。不要把 SQLite、签名 URL、密钥或媒体文件提交 Git。

部署包自动写入 `build_provenance.json`，正式 Output Manifest 锁定这次部署的 Git Commit、源视频的 Video Plan 版本、Prompt Hash、模型、相关资产版本及输入/输出校验和。业务操作留在 Work OS 数据库和审计中，不逐条 Git Commit；只有代码、规则、Schema、Workflow 等有意义的系统变更进入 Git。脏构建或缺少可验证 Git Commit 时拒绝正式批准。

## 员工生产顺序

1. **武术/运营**核对已确认的招式事实，上传真人标准动作并设置工作区间；武术同事亲自确认后，真人参考才会变为 locked。未确认的 draft 不算 Motion Ready。
2. **美术**维护可复用的数字老师 Character（主形象、三视图、服装、GLB 数字人模型、Voice ID、可用正式动作、版本），同时提交教学场景和可独立使用的背景。含人物的合成画面可登记为 `scene`，不能假作纯背景。老师素材上传进入公司资产目录；更换已有素材走老师版本审查。
3. **AI Production OS**根据已有视频计划和真人参考制作数字老师候选，按现有制作页提交、选择和返修。Seedance 结果只作为候选；武术专业人员逐项核对动作顺序、手部路径、步法、重心、起止姿态和教学适用性，确认后才能定版。
4. **编排与声音**在教学包页建立镜头，关联整式或逐镜场景、背景、老师模型、镜头/Prompt/Workflow、教学语音、OS、BGM、SFX、字幕。教学语音与叙事 OS 是不同用途。无音轨时仍显示独立缺项；OS/BGM/SFX/字幕目前允许占位。
5. **合成与输出**先完成已有动作视频的武术 QC，再把实际使用老师、背景和教学语音的完整 MP4 登记为 `lesson_output`。编辑者记录合成说明并锁定输入快照；系统保存来源视频、真人参考、老师、每镜、各资产、规则及输出 SHA-256。任何输入版本变化都会令合成失效。单独上传背景、语音或 MP4 不能让 `Video Ready` 通过。
6. **最终人工验收**由负责人连续观看锁定的完整成片并实际听审，填写范围和证据。负责人批准时锁定事实、老师、真人来源、武术 QC、合成输入、各资产与成片的版本及校验和；发布前再次核验。改动任一正式素材或镜头会撤销当前批准，需重新合成和审查。

功法看板按每式显示当前卡点、缺项与负责人，同时列出可并行工作。例如 BGM 缺失不阻止动作复刻或背景制作。状态包括策划、待真人动作、待老师、待美术、动作复刻、动作验收/返修、合成、待教学语音、成片 QA、待批准、已批准、已发布。音轨占位不等于已经生成或听审。

## 发布门槛

必需项：已确认的招式事实、可生产的数字老师、至少一个有起止状态的镜头、已锁定的真人动作、同版动作候选、武术岗位真人 QC 通过、独立背景、教学话术、教学语音、锁定且输入仍一致的完整教学成片、负责人实际视听验收。文件缺失或哈希变化阻断批准/发布。历史视频、技术成功、模型自检、AI 初审不能替代动作验收。OS/BGM/SFX/字幕缺失会在 Manifest 中作为 placeholders 留痕。

## 《流云掌第一式》真实素材试跑

执行 `python3 scripts/run_liuyun_pilot.py --data-dir <新建空目录> --report reports/liuyun_pilot_20260926.json`。该脚本读取项目中既有真人源片、20 秒参考、Wongkey/练功场景合成图、历史英语语音、字幕和 R2 视频，复制到隔离 Asset Center 存储并生成绑定与技术报告。它不提交付费作业，不确认真人标准动作，不把旧声线或含人物画面升级为当前正式资产，不代替正式视频和 QA。试跑结果：素材可读、视频元数据可解析、Asset Center/教学包关系建立、缺项和负责人可见、批准被正确阻断。

旧 R2 对掌位和朝向有明确问题，未有当前标准动作与连续视听人工通过记录。把它当历史样片供返修定位。正式端到端成片仍需武术同事确认真人动作、美术交付当前老师与独立背景、镜头和音频制作、数字人动作复刻、武术 QC 与最终人工视听验收。完成这些节点后再做正式批准、Manifest 导出和产品发布。

## 验证与上线

隔离测试覆盖现有 Work OS 回归、教学包并行依赖、素材不可覆盖、镜头版本和 Asset Center 关联、专业/人工验收阻断。服务器部署脚本 `deploy/publish_ssh.py`只发布应用代码，使用服务器固定门禁做归档检查、数据库备份、健康检查和失败回滚；媒体目录与 Git 不在部署包中。部署前检查运行中的视频批次与服务状态，不把 Creative Lab 实验当作本次部署对象。
