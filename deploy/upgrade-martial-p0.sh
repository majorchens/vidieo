#!/bin/sh
# Install the focused Martial P0 release beside the current release.
set -eu
if [ "$#" -ne 2 ]; then echo 'usage: upgrade-martial-p0.sh CODE_ARCHIVE SHA256' >&2; exit 2; fi
archive=$1
expected=$2
actual=$(sha256sum "$archive" | cut -d' ' -f1)
[ "$actual" = "$expected" ] || { echo 'archive checksum mismatch' >&2; exit 1; }
data=/var/lib/yoodun-work-os/work_os.sqlite3
old=/opt/yoodun-work-os-martial-v0.1
new=/opt/yoodun-work-os-martial-p0-20260923
unit=/etc/systemd/system/yoodun-work-os.service
[ -f "$data" ] && [ -d "$old" ] && [ -f "$unit" ] || { echo 'Martial V0.1 installation is missing' >&2; exit 1; }
[ ! -e "$new" ] || { echo 'P0 destination already exists; inspect before retry' >&2; exit 1; }
grep -q "$old/app/server.py" "$unit" || { echo 'unexpected active service; inspect before upgrade' >&2; exit 1; }
backup=/var/backups/yoodun-work-os/martial-p0-$(date +%Y%m%d-%H%M%S)
install -d -m 0700 "$backup"
cp -a "$unit" "$backup/service.before"
complete=0
rollback() {
    if [ "$complete" -ne 1 ]; then
        cp -a "$backup/service.before" "$unit"
        systemctl daemon-reload
        systemctl restart yoodun-work-os.service || true
        echo "P0 startup failed; prior service restored. Backup: $backup" >&2
    fi
}
trap rollback EXIT
systemctl stop yoodun-work-os.service
python3 - "$data" "$backup/work_os.sqlite3" <<'PY'
import sqlite3,sys
with sqlite3.connect(sys.argv[1]) as source, sqlite3.connect(sys.argv[2]) as destination:
    source.backup(destination)
PY
chmod 0600 "$backup/work_os.sqlite3"
install -d -m 0755 "$new"
tar -xzf "$archive" -C "$new"
chown -R root:root "$new"
[ -f "$new/app/server.py" ] && [ -f "$new/app/martial.py" ] && [ -f "$new/app/static/martial.js" ]
python3 -m py_compile "$new"/app/*.py
sed "s#${old}#${new}#g; s/Martial Asset Workspace V0.1/Martial Asset Workspace P0/g" "$backup/service.before" > "$backup/service.next"
install -m 0644 "$backup/service.next" "$unit"
systemctl daemon-reload
systemctl restart yoodun-work-os.service
healthy=0
for attempt in 1 2 3 4 5 6 7 8 9 10; do
    if systemctl is-active --quiet yoodun-work-os.service && curl -fsS http://127.0.0.1:18766/martial.js | grep -q martialWorkspace; then
        healthy=1
        break
    fi
    sleep 1
done
[ "$healthy" -eq 1 ] || { echo 'P0 health check failed' >&2; exit 1; }
complete=1
trap - EXIT
echo "Martial P0 service healthy. Backup: $backup"
