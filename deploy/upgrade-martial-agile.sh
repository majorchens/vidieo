#!/bin/sh
# Deploy the source-confirmed Martial production workspace beside the P0 release.
set -eu
if [ "$#" -ne 2 ]; then echo 'usage: upgrade-martial-agile.sh CODE_ARCHIVE SHA256' >&2; exit 2; fi
archive=$1
expected=$2
actual=$(sha256sum "$archive" | cut -d' ' -f1)
[ "$actual" = "$expected" ] || { echo 'archive checksum mismatch' >&2; exit 1; }
data=/var/lib/yoodun-work-os/work_os.sqlite3
old=/opt/yoodun-work-os-martial-p0-20260923
new=/opt/yoodun-work-os-martial-agile-20260923
unit=/etc/systemd/system/yoodun-work-os.service
[ -f "$data" ] && [ -d "$old" ] && [ -f "$unit" ] || { echo 'P0 installation is missing' >&2; exit 1; }
[ ! -e "$new" ] || { echo 'Agile destination already exists; inspect before retry' >&2; exit 1; }
grep -q "$old/app/server.py" "$unit" || { echo 'unexpected active service; inspect before upgrade' >&2; exit 1; }
backup=/var/backups/yoodun-work-os/martial-agile-$(date +%Y%m%d-%H%M%S)
install -d -m 0700 "$backup"
cp -a "$unit" "$backup/service.before"
complete=0
rollback() {
    if [ "$complete" -ne 1 ]; then
        set +e
        systemctl stop yoodun-work-os.service || true
        database_restored=0
        if [ -f "$backup/work_os.sqlite3" ]; then
            python3 - "$backup/work_os.sqlite3" "$data" <<'PY'
import sqlite3,sys
with sqlite3.connect(sys.argv[1]) as source, sqlite3.connect(sys.argv[2]) as target:
    source.backup(target)
PY
            [ "$?" -eq 0 ] && database_restored=1
        fi
        cp -a "$backup/service.before" "$unit"
        unit_restored=$?
        systemctl daemon-reload
        daemon_reloaded=$?
        systemctl restart yoodun-work-os.service || true
        if [ "$database_restored" -eq 1 ] && [ "$unit_restored" -eq 0 ] && [ "$daemon_reloaded" -eq 0 ] && systemctl is-active --quiet yoodun-work-os.service; then
            echo "Agile startup failed; P0 service and database restored. Backup: $backup" >&2
        else
            echo "Agile startup failed; automatic rollback incomplete. Inspect backup: $backup" >&2
        fi
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
[ -f "$new/app/server.py" ] && [ -f "$new/app/martial_initialization.py" ] && [ -f "$new/app/martial_revision.py" ] && [ -f "$new/registry/martial_asset_manifest.json" ]
python3 -m py_compile "$new"/app/*.py
sed "s#${old}#${new}#g; s/Martial Asset Workspace P0/Martial Asset Workspace Agile/g" "$backup/service.before" > "$backup/service.next"
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
[ "$healthy" -eq 1 ] || { echo 'Agile health check failed' >&2; exit 1; }
python3 - "$data" <<'PY'
import sqlite3,sys
with sqlite3.connect(sys.argv[1]) as c:
    arts=c.execute("SELECT COUNT(*) FROM martial_arts WHERE status='active'").fetchone()[0]
    moves=c.execute("SELECT COUNT(*) FROM martial_moves WHERE current_version>0").fetchone()[0]
    qc=c.execute("SELECT result FROM martial_qc WHERE media_job_id='mj_94efec7ab99b7e95' AND stage='martial'").fetchone()
    revision=c.execute("SELECT COUNT(*) FROM martial_revision_packages WHERE media_job_id='mj_94efec7ab99b7e95'").fetchone()[0]
    if (arts,moves,revision)!=(5,47,1) or not qc or qc[0]!='fail':
        raise SystemExit(f'Agile data gate failed: arts={arts} moves={moves} revision={revision} p0_qc={qc[0] if qc else None}')
    print(f'Agile data gate: arts={arts} moves={moves} P0 revision={revision} P0 QC={qc[0]}')
PY
complete=1
trap - EXIT
echo "Martial Agile service healthy. Backup: $backup"
