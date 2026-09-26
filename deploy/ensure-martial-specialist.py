"""Idempotently attach Xia Runqi to the martial specialty on the ECS.

Run as root after the database backup. Any newly generated login password is
written only to a root-readable file; it never enters logs or release files.
"""
from __future__ import annotations

import os
import secrets
import sys
from pathlib import Path

sys.path.insert(0,"/opt/yoodun-work-os-martial-v0.1/app")
import martial
import store


def main():
    store.initialize();martial.initialize()
    with store.connect() as c:
        users=[dict(r) for r in c.execute("SELECT id,username,display_name,role,active FROM users WHERE username='xia-runqi' OR display_name='夏润麒'")]
    if len(users)>1:raise RuntimeError("存在多个匹配员工，请人工核对后绑定")
    if users:
        user=users[0]
        if user["display_name"]!="夏润麒" or user["role"]!="employee" or not user["active"]:
            raise RuntimeError("既有账号资料不符合武术岗位要求")
        user_id=user["id"]
        print("复用既有夏润麒员工账号")
    else:
        password=secrets.token_urlsafe(24)
        private=Path("/var/lib/yoodun-work-os/private")
        private.mkdir(mode=0o700,parents=True,exist_ok=True)
        private.chmod(0o700)
        target=private/"xia-runqi.initial-password"
        fd=os.open(target,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
        with os.fdopen(fd,"w",encoding="utf-8") as file:
            file.write(password+"\n")
        try:user_id=store.create_user("xia-runqi","夏润麒","employee",password,"u_system")
        except Exception:
            target.unlink(missing_ok=True)
            raise
        print("创建夏润麒员工账号；初始密码仅保存在 ECS 私有文件")
    with store.connect() as c:
        c.execute("INSERT OR IGNORE INTO martial_specialists(user_id,project_id,title,created_at) VALUES(?,?,?,?)",
                  (user_id,"wuxiang","武术数字资产内容设计",store.now()))
        count=c.execute("SELECT COUNT(*) FROM martial_moves").fetchone()[0]
        print("武学词典招式数",count)


if __name__=="__main__":main()
