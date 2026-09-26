# 武学数字资产工作台 V0.1

此版本在现有 Yoodun Work OS V0.2 上增加夏润麒岗位工作区。原任务、素材、权限、QC、反馈和数据表保留，武学表为新增表。员工在工作台中维护五套武学与 47 条独立招式、老师版本和真人动作参考；负责人确认武学事实、招式事实、老师新版本与正式资产。教学和演练候选分开记录。

## 真实资料

- 来源：`/Users/majorchen/Downloads/各项目员工工作内容梳理 (1).docx`，SHA-256 `ef38507ceb692da5c03ae973ea52502e1b68f9031999b966d0a3ea577cd09c21`。
- 两份本机同名 Word 文件的五张武学表逐格哈希一致。只提取这五张表，未导入其他员工资料。`registry/martial_dictionary.json` 保留每个单元格的原文、表格行号和来源哈希。
- 7 条入门套路、流云掌 10 条、开山拳 10 条、太极功 10 条、八卦掌 10 条。原文中呼吸法的动作和旁白栏为空，继续保留空白。所有导入项初始为待确认，AI 不自动补正拼写、动作或中英文。
- 五位 NPC 使用 2026-09-21 V2 人物基线和已定声线编号。视觉素材独立登记；未上传的图片不会被假称为已有定版资产。
- `registry/martial_master_sources.json` 固定五份 V2 原文的本机路径与哈希。ECS 只保存摘要和哈希，不上传完整人物小传；本机制作包执行器按老师逐份校验并读取原文，供 DeepSeek 约束角色一致性。原文与锁定哈希不一致时停止制作包调用。

## 生产路径

武学与招式草稿 → 负责人确认 → 上传或关联真人视频 → 夏润麒确认锁定参考 → 本机万界 `deepseek-v4.1-flash` 制作 AI 包 → 形成 Work OS 任务 → 负责人设置预算、员工开始任务 → 选择 Seedance 2.0/2.5 并生成 → 本机 Company Workflow 根据保存的路由提交、续查、下载 → 视频自动回传为候选 → 技术检查 → 员工选择并写理由 → Martial QC → 负责人批准正式资产。

模型路由由本机既有 Company Workflow `video_routing.resolve` 定期上报。当前已核验的报价仅覆盖润元 5 秒、480p、16:9；其他平台或路由过期时显示“价格未知”并阻止付费提交。预算默认为 0；负责人设置预算前不发起视频。状态未知保留原平台和幂等编号，不自动重发或跨平台切换。实际费用只在账单核验后显示，未知费用不显示为 0。

角色图与真人视频通过 6 小时签名链接供已选供应商读取；链接只对应本项目受控素材。工作台不显示 Provider Key、连接令牌、内部路由或供应商任务编号。所有新媒体候选自动留存为 Work OS 资产；文件技术检查不代替真人动作验收。

历史说明：V0.3 曾把武学视频单文件限制为 40 MB，目的是让 Base64 JSON 请求适配当时的 `/work-os/` Nginx 55 MiB 限额。这个值未经实际素材尺寸验证，已被大于 40 MB 的真人动作参考证明不适用。当前真人动作原片和生成候选改用鉴权后的文件流传输，单文件技术防护上限为 512 MB；普通 JSON 接口仍保留原请求上限。

## 已验证和边界

- 隔离数据库测试：既有 5 项及武学工作流 1 项通过。HTTP 烟雾测试验证岗位登录、47 条招式、静态页面和路由上报。
- 真实万界 DeepSeek 生产包小输入调用成功，模型返回完整 JSON 字段；用量为输入 628、输出 1503 token。此调用没有提交 Seedance 视频，且发生在 V2 原文校验接入之前；接入后的完整路径仍待线上验证。
- 本机 PyAV 对现有八卦掌 MP4 解码首帧成功；动作语义仍需夏润麒观看确认。
- 工作区间、方向和关键时刻会锁在任务与生成提示中；V0.1 保存原视频，不物理裁剪视频文件。供应商会收到原视频参考及明确区间要求。
- 尚未有本任务的实际 Seedance 生成与员工真人试用。付费生成必须先由负责人明确设置该招式任务的预算。

## 上线

`deploy/upgrade-martial.sh` 在原 ECS 上备份现有 SQLite 和 service unit，安装新代码到 `/opt/yoodun-work-os-martial-v0.1`，保留 V0.2 代码和原运行数据。脚本新增或复用夏润麒账号；新账号的随机初始密码只写到 ECS 根用户可读的 `/var/lib/yoodun-work-os/private/xia-runqi.initial-password`，不会进入代码包或日志。原 Nginx `/work-os/` 路由不变。健康检查失败时回退 service unit。

本机使用单独的 `com.yoodun.workos-martial-connector.plist` 运行武学后台连接器，与原 V0.2 任务连接器并存。它复用原私有连接配置，不拷贝密钥。开启前先确认线上新版服务已健康。

### 2026-09-23 生产部署记录

- 已部署至 `https://ai.duodianqian.cn/work-os/`，原 Work OS 数据库与 V0.2 代码保留。上线前 SQLite 与 service unit 备份位于 `/var/backups/yoodun-work-os/martial-v01-20260923-182007/`；ECS `/tmp` 部署副本已清理。
- 生产数据库核验：5 套武学、5 位老师、47 条独立招式；`xia-runqi` 为启用的员工账号，且只登记为万象武境武学岗位。使用初始密码登录后，`/api/martial/overview` 实际返回 5/5/47。密码只存在 ECS 私有文件 `/var/lib/yoodun-work-os/private/xia-runqi.initial-password`，未记录在此文档。
- 公网 Work OS 页面及武学 JS/CSS 均返回 HTTP 200，生产 JS/CSS 哈希与本发布包一致；未登录访问武学 API 返回 401。Mac 后台连接器由 LaunchAgent 运行，认证连接生产站点成功，当前制作包及媒体队列均为 0。
- 本次部署没有发起 Seedance 视频生成，也没有扩大预算。完整的 V2 角色原文到 DeepSeek 制作包、Seedance 付费生成及员工真人操作尚未由实际任务验证。

本地检查命令：

```sh
python3 -m unittest discover -s tests -v
python3 -m py_compile app/*.py deploy/ensure-martial-specialist.py
node --check app/static/app.js
node --check app/static/martial.js
sh -n deploy/upgrade-martial.sh
plutil -lint deploy/com.yoodun.workos-martial-connector.plist
```
