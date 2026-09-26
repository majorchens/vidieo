# Work OS SSH 发布

专用 SSH 别名：`yoodun-workos-deploy`。私钥只保存在本机 `~/.ssh/yoodun_workos_deploy_ed25519`；服务器 `yoodun-deploy` 账号只有公钥。不要复制或提交私钥。

从本项目根目录运行：

```sh
python3 deploy/publish_ssh.py
```

这会打包 `app/`、用 SCP 上传并执行服务器端只读校验，不切换线上服务。确认发布时运行：

```sh
python3 deploy/publish_ssh.py --apply
```

服务器只允许该账号通过 `sudo` 执行固定的 `/usr/local/sbin/yoodun-work-os-deploy`。发布器校验 SHA-256 和归档路径，备份 SQLite 和 systemd 服务配置，切换代码并进行本机 HTTP 健康检查；失败时回滚。发布包不执行上传的脚本，也不包含凭证、运行数据或 Nginx 配置。此通道只适用于应用代码发布；数据库结构、依赖和 Nginx 变更仍需单独处理。
