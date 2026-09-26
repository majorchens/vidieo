#!/bin/sh
# Add Martial V0.1 to the existing Work OS service; keep V0.2 as rollback source.
set -eu
if [ "$#" -ne 2 ]; then echo 'usage: upgrade-martial.sh CODE_ARCHIVE SHA256' >&2; exit 2; fi
archive=$1
expected=$2
actual=$(sha256sum "$archive" | cut -d' ' -f1)
[ "$actual" = "$expected" ] || { echo 'archive checksum mismatch' >&2; exit 1; }
[ -f /var/lib/yoodun-work-os/work_os.sqlite3 ] || { echo 'live database missing' >&2; exit 1; }
[ -d /opt/yoodun-work-os-v0.2 ] || { echo 'V0.2 rollback source missing' >&2; exit 1; }
[ ! -e /opt/yoodun-work-os-martial-v0.1 ] || { echo 'Martial destination already exists; inspect before retry' >&2; exit 1; }
backup=/var/backups/yoodun-work-os/martial-v01-$(date +%Y%m%d-%H%M%S)
install -d -m 0700 "$backup"
cp -a /etc/systemd/system/yoodun-work-os.service "$backup/service.before"
python3 - "$backup/work_os.sqlite3" <<'PY'
import sqlite3,sys
with sqlite3.connect('/var/lib/yoodun-work-os/work_os.sqlite3') as source, sqlite3.connect(sys.argv[1]) as destination:
    source.backup(destination)
PY
chmod 0600 "$backup/work_os.sqlite3"
complete=0
rollback() {
    if [ "$complete" -ne 1 ]; then
        cp -a "$backup/service.before" /etc/systemd/system/yoodun-work-os.service
        systemctl daemon-reload
        systemctl restart yoodun-work-os.service || true
        echo "Martial upgrade failed; previous service restored. Backup: $backup" >&2
    fi
}
trap rollback EXIT
systemctl stop yoodun-work-os.service
install -d -m 0755 /opt/yoodun-work-os-martial-v0.1
tar -xzf "$archive" -C /opt/yoodun-work-os-martial-v0.1
chown -R root:root /opt/yoodun-work-os-martial-v0.1
[ -f /opt/yoodun-work-os-martial-v0.1/app/server.py ]
[ -f /opt/yoodun-work-os-martial-v0.1/registry/martial_dictionary.json ]
YOODUN_DATA_DIR=/var/lib/yoodun-work-os /usr/bin/python3 /opt/yoodun-work-os-martial-v0.1/deploy/ensure-martial-specialist.py
chown yoodun-os:yoodun-os /var/lib/yoodun-work-os/work_os.sqlite3*
install -m 0644 /opt/yoodun-work-os-martial-v0.1/deploy/yoodun-work-os-martial.service /etc/systemd/system/yoodun-work-os.service
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
[ "$healthy" -eq 1 ] || { echo 'health check failed' >&2; exit 1; }
complete=1
trap - EXIT
echo "Martial workspace service healthy. Backup: $backup"
