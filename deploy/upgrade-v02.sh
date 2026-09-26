#!/bin/sh
# Upgrade the existing independent Work OS service. Run as root on the ECS.
set -eu
if [ "$#" -ne 2 ]; then
    echo 'usage: upgrade-v02.sh CODE_ARCHIVE EXPECTED_SHA256' >&2
    exit 2
fi
archive=$1
expected=$2
[ -f "$archive" ] || { echo 'archive missing' >&2; exit 1; }
actual=$(sha256sum "$archive" | awk '{print $1}')
[ "$actual" = "$expected" ] || { echo 'archive checksum mismatch' >&2; exit 1; }
[ -d /opt/yoodun-work-os ] || { echo 'V0.1 rollback source missing' >&2; exit 1; }
[ -f /var/lib/yoodun-work-os/work_os.sqlite3 ] || { echo 'live database missing' >&2; exit 1; }
[ -f /etc/systemd/system/yoodun-work-os.service ] || { echo 'service unit missing' >&2; exit 1; }
[ ! -e /opt/yoodun-work-os-v0.2 ] || { echo 'V0.2 destination exists; inspect before retry' >&2; exit 1; }
backup=/var/backups/yoodun-work-os/v02-$(date +%Y%m%d-%H%M%S)
install -d -m 0700 "$backup"
cp -a /etc/systemd/system/yoodun-work-os.service "$backup/service.v01"
python3 - "$backup/work_os.sqlite3" <<'PY'
import sqlite3,sys
with sqlite3.connect('/var/lib/yoodun-work-os/work_os.sqlite3') as source, sqlite3.connect(sys.argv[1]) as destination:
    source.backup(destination)
PY
chmod 0600 "$backup/work_os.sqlite3"
install -d -m 0755 /opt/yoodun-work-os-v0.2
tar -xzf "$archive" -C /opt/yoodun-work-os-v0.2
chown -R root:root /opt/yoodun-work-os-v0.2
[ -f /opt/yoodun-work-os-v0.2/app/server.py ] || { echo 'archive missing server' >&2; exit 1; }
[ -f /opt/yoodun-work-os-v0.2/deploy/yoodun-work-os.service ] || { echo 'archive missing unit' >&2; exit 1; }
YOODUN_DATA_DIR=/var/lib/yoodun-work-os /usr/bin/python3 - <<'PY'
import sys
sys.path.insert(0,'/opt/yoodun-work-os-v0.2/app')
import store
store.initialize()
with store.connect() as c:
    print('migration version',c.execute('SELECT MAX(version) FROM schema_version').fetchone()[0])
    print('preserved tasks',c.execute('SELECT COUNT(*) FROM tasks').fetchone()[0])
PY
install -m 0644 /opt/yoodun-work-os-v0.2/deploy/yoodun-work-os.service /etc/systemd/system/yoodun-work-os.service
systemctl daemon-reload
systemctl restart yoodun-work-os.service
verified=0
for attempt in 1 2 3 4 5 6 7 8; do
    if systemctl is-active --quiet yoodun-work-os.service && curl -fsS http://127.0.0.1:18766/ | grep -q 'V0.2'; then
        verified=1
        break
    fi
    sleep 1
done
if [ "$verified" -ne 1 ]; then
    cp -a "$backup/service.v01" /etc/systemd/system/yoodun-work-os.service
    systemctl daemon-reload
    systemctl restart yoodun-work-os.service
    echo "V0.2 health check failed; V0.1 service restored. Backup: $backup" >&2
    exit 1
fi
echo "V0.2 service healthy. Backup: $backup"
