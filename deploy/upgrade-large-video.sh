#!/bin/sh
# Correct the V0.3 video transport without touching the Work OS database.
set -eu

if [ "$#" -ne 2 ]; then echo 'usage: upgrade-large-video.sh CODE_ARCHIVE SHA256' >&2; exit 2; fi
archive=$1
expected=$2
case "$expected" in *[!0-9a-f]*|'') echo 'invalid SHA-256' >&2; exit 2;; esac
[ "${#expected}" -eq 64 ] && [ "$(id -u)" -eq 0 ] && [ -f "$archive" ] || exit 2
[ "$(sha256sum "$archive" | cut -d' ' -f1)" = "$expected" ] || { echo 'archive checksum mismatch' >&2; exit 1; }

old=/opt/yoodun-work-os-v03-20260924
new=/opt/yoodun-work-os-v03-large-video-20260924
unit=/etc/systemd/system/yoodun-work-os.service
nginx_conf=/etc/nginx/yoodun-work-os-location.conf
service=yoodun-work-os.service
[ -d "$old" ] && [ ! -e "$new" ] && [ -f "$unit" ] && [ -f "$nginx_conf" ] || {
    echo 'expected V0.3 release or service configuration is missing' >&2; exit 1;
}
grep -Fq "$old/app/server.py" "$unit" || { echo 'active service path differs from reviewed V0.3' >&2; exit 1; }
systemctl is-active --quiet "$service" || { echo 'Work OS was not healthy before upgrade' >&2; exit 1; }

python3 - "$archive" <<'PY'
import sys,tarfile
required={'app/server.py','app/store.py','app/martial.py','app/martial_connector.py',
          'app/ai_studio.py','app/ai_studio_connector.py','app/asset_center.py',
          'app/static/martial.js','app/static/index.html',
          'deploy/nginx-location.conf','deploy/upgrade-large-video.sh'}
with tarfile.open(sys.argv[1],'r:gz') as bundle:
    names=set()
    for member in bundle.getmembers():
        if (not member.isfile() or member.size>2_000_000 or member.name in names or
            member.name not in required or member.name.startswith('/') or '..' in member.name.split('/')):
            raise SystemExit('invalid release member')
        names.add(member.name)
    if names!=required:raise SystemExit('release manifest mismatch')
PY

backup=/var/backups/yoodun-work-os/large-video-$(date +%Y%m%d-%H%M%S)-$$
install -d -m 0700 "$backup"
cp -a "$unit" "$backup/service.before"
cp -a "$nginx_conf" "$backup/nginx.before"
# Preserve a consistent copy of live task data before changing the service.
[ -f /var/lib/yoodun-work-os/work_os.sqlite3 ] || { echo 'Work OS database missing' >&2; exit 1; }
python3 - "$backup/work_os.sqlite3" <<'PY'
import sqlite3,sys
source=sqlite3.connect('file:/var/lib/yoodun-work-os/work_os.sqlite3?mode=ro',uri=True)
target=sqlite3.connect(sys.argv[1])
source.backup(target)
assert target.execute('PRAGMA quick_check').fetchone()[0]=='ok'
target.close();source.close()
PY
chmod 0600 "$backup/work_os.sqlite3"
complete=0
changed=0
rollback() {
    result=$?
    trap - EXIT
    if [ "$complete" -eq 1 ]; then return; fi
    if [ "$changed" -eq 1 ]; then
        set +e
        cp -a "$backup/service.before" "$unit"
        cp -a "$backup/nginx.before" "$nginx_conf"
        systemctl daemon-reload
        systemctl restart "$service"
        nginx -t && systemctl reload nginx
        echo "Large-video upgrade failed; previous service and Nginx restored. Backup: $backup" >&2
    fi
    rm -rf "$new"
    [ "$result" -ne 0 ] || result=1
    exit "$result"
}
trap rollback EXIT

install -d -m 0755 "$new"
cp -a "$old/." "$new/"
tar -xzf "$archive" -C "$new"
chown -R root:root "$new"
python3 -m py_compile "$new"/app/*.py
changed=1
install -m 0644 "$new/deploy/nginx-location.conf" "$nginx_conf"
nginx -t
sed "s#${old}#${new}#g" "$backup/service.before" > "$backup/service.next"
install -m 0644 "$backup/service.next" "$unit"
systemctl daemon-reload
systemctl restart "$service"
systemctl reload nginx

healthy=0
for attempt in 1 2 3 4 5 6 7 8 9 10; do
    if systemctl is-active --quiet "$service" &&
       curl -fsS --max-time 3 http://127.0.0.1:18766/ | grep -q 'V0.3' &&
       [ "$(curl -fsS --max-time 3 http://127.0.0.1:18766/martial.js | sha256sum | cut -d' ' -f1)" = "$(sha256sum "$new/app/static/martial.js" | cut -d' ' -f1)" ]; then
        healthy=1; break
    fi
    sleep 1
done
[ "$healthy" -eq 1 ] || { echo 'Work OS health check failed' >&2; exit 1; }
nginx -t
complete=1
echo "Large-video transport deployed. Backup: $backup"
