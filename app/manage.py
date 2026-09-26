"""Local administration. Passwords are written only to a private on-disk handoff file."""
from __future__ import annotations

import argparse
import os
import secrets
from pathlib import Path

import store


def _private_write(path: Path, content: str):
    path.parent.mkdir(parents=True,exist_ok=True)
    fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    with os.fdopen(fd,"w") as f:f.write(content)


def bootstrap():
    store.initialize()
    credentials=[]
    for username,name,role in [("majorchen","创始人","founder"),("yunying-xyq","试点员工 yunying-xyq","employee")]:
        with store.connect() as c:
            exists=c.execute("SELECT id FROM users WHERE username=?",(username,)).fetchone()
        if exists: continue
        password=secrets.token_urlsafe(24)
        uid=store.create_user(username,name,role,password)
        credentials.append((username,uid,password))
    if not credentials:
        print("账号已存在；没有重置或输出密码")
        return
    path=store.DATA/"initial-access.txt"
    if path.exists():
        path=store.DATA/("initial-access-"+secrets.token_hex(4)+".txt")
    public_url=os.environ.get("YOODUN_PUBLIC_URL","http://127.0.0.1:18766/")
    content="Yoodun Work OS v0.1 初始访问（权限600）\n登录地址："+public_url+"\n\n"+"\n".join(f"账号：{u}\n用户ID：{uid}\n初始密码：{password}\n" for u,uid,password in credentials)
    _private_write(path,content)
    print("已建立账号；初始访问文件："+str(path))


def status():
    store.initialize()
    with store.connect() as c:
        print("schema_version:",c.execute("SELECT MAX(version) FROM schema_version").fetchone()[0])
        print("users:",c.execute("SELECT COUNT(*) FROM users WHERE active=1").fetchone()[0])
        print("tasks:",c.execute("SELECT COUNT(*) FROM tasks").fetchone()[0])
        print("accepted:",c.execute("SELECT COUNT(*) FROM tasks WHERE status='accepted'").fetchone()[0])
        print("pilot_observations:",c.execute("SELECT COUNT(*) FROM pilot_observations").fetchone()[0])


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("action",choices=["bootstrap","status"])
    args=parser.parse_args()
    if args.action=="bootstrap":bootstrap()
    else:status()


if __name__=="__main__":main()
