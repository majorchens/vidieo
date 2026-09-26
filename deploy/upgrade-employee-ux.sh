#!/bin/sh
# Upgrade the existing Work OS with a database backup and read-only legacy scan.
set -eu
if [ "$#" -ne 2 ]; then echo 'usage: upgrade-employee-ux.sh CODE_ARCHIVE SHA256' >&2; exit 2; fi
archive=$1
expected=$2
actual=$(sha256sum "$archive" | cut -d' ' -f1)
[ "$actual" = "$expected" ] || { echo 'archive checksum mismatch' >&2; exit 1; }

data_root=/var/lib/yoodun-work-os
data=$data_root/work_os.sqlite3
legacy_root=/var/lib/wujing-ai-studio
legacy_db=$legacy_root/studio.db
old=/opt/yoodun-work-os-martial-ai-studio-20260924
new=/opt/yoodun-work-os-employee-assets-20260924
unit=/etc/systemd/system/yoodun-work-os.service
[ -f "$data" ] && [ -f "$legacy_db" ] && [ -d "$old" ] && [ -f "$unit" ] || {
    echo 'expected Work OS or legacy AI installation is missing' >&2; exit 1;
}
[ ! -e "$new" ] || { echo 'release destination already exists' >&2; exit 1; }
grep -q "$old/app/server.py" "$unit" || { echo 'unexpected active service path' >&2; exit 1; }

backup=/var/backups/yoodun-work-os/employee-assets-$(date +%Y%m%d-%H%M%S)
install -d -m 0700 "$backup"
cp -a "$unit" "$backup/service.before"
if [ -f "$data_root/reports/Legacy_AI_Asset_Migration_Report.md" ]; then
    cp -a "$data_root/reports/Legacy_AI_Asset_Migration_Report.md" "$backup/report.before.md"
fi
if [ -f "$data_root/reports/Legacy_AI_Asset_Migration_Report.json" ]; then
    cp -a "$data_root/reports/Legacy_AI_Asset_Migration_Report.json" "$backup/report.before.json"
fi
complete=0
rollback() {
    failure=$?
    trap - EXIT
    if [ "$complete" -eq 1 ]; then return; fi
    set +e
    restored=1
    systemctl stop yoodun-work-os.service || restored=0
    if [ -f "$backup/work_os.sqlite3" ]; then
        python3 - "$backup/work_os.sqlite3" "$data" <<'PY'
import sqlite3, sys
with sqlite3.connect(sys.argv[1]) as source, sqlite3.connect(sys.argv[2]) as target:
    source.backup(target)
PY
        [ "$?" -eq 0 ] || restored=0
    fi
    if [ -f "$backup/report.before.md" ]; then
        cp -a "$backup/report.before.md" "$data_root/reports/Legacy_AI_Asset_Migration_Report.md" || restored=0
    else
        rm -f "$data_root/reports/Legacy_AI_Asset_Migration_Report.md" || restored=0
    fi
    if [ -f "$backup/report.before.json" ]; then
        cp -a "$backup/report.before.json" "$data_root/reports/Legacy_AI_Asset_Migration_Report.json" || restored=0
    else
        rm -f "$data_root/reports/Legacy_AI_Asset_Migration_Report.json" || restored=0
    fi
    cp -a "$backup/service.before" "$unit" || restored=0
    systemctl daemon-reload || restored=0
    systemctl restart yoodun-work-os.service || restored=0
    systemctl is-active --quiet yoodun-work-os.service || restored=0
    if [ "$restored" -eq 1 ]; then
        echo "Upgrade failed; previous service and database restored. Backup: $backup" >&2
    else
        echo "Upgrade failed AND rollback needs manual attention. Backup: $backup" >&2
    fi
    [ "$failure" -ne 0 ] || failure=1
    exit "$failure"
}
trap rollback EXIT

systemctl stop yoodun-work-os.service
python3 - "$data" "$backup/work_os.sqlite3" <<'PY'
import sqlite3, sys
with sqlite3.connect(sys.argv[1]) as source, sqlite3.connect(sys.argv[2]) as destination:
    source.backup(destination)
PY
chmod 0600 "$backup/work_os.sqlite3"
install -d -m 0755 "$new"
tar -xzf "$archive" -C "$new"
chown -R root:root "$new"
[ -f "$new/app/server.py" ] && [ -f "$new/app/asset_center.py" ] && [ -f "$new/scripts/sync_legacy_assets.py" ]
python3 -m py_compile "$new"/app/*.py "$new"/scripts/sync_legacy_assets.py

export YOODUN_DATA_DIR=$data_root
export YOODUN_LEGACY_DATA_DIR=$legacy_root
export PYTHONPATH=$new/app
python3 - <<'PY'
import store, martial, martial_initialization, martial_product, ai_studio, asset_center
store.initialize()
martial.initialize()
martial_initialization.initialize_confirmed_import()
martial_product.initialize_product_migration()
ai_studio.initialize()
asset_center.initialize()
PY
install -d -m 0750 "$data_root/reports"
timeout 180s python3 "$new/scripts/sync_legacy_assets.py" \
    --legacy-db "$legacy_db" \
    --legacy-data-dir "$legacy_root" \
    --work-os-data-dir "$data_root" \
    --report "$data_root/reports/Legacy_AI_Asset_Migration_Report.md"

sed "s#${old}#${new}#g" "$backup/service.before" > "$backup/service.next"
install -m 0644 "$backup/service.next" "$unit"
systemctl daemon-reload
systemctl restart yoodun-work-os.service
healthy=0
for attempt in 1 2 3 4 5 6 7 8 9 10; do
    if systemctl is-active --quiet yoodun-work-os.service &&
       curl -fsS http://127.0.0.1:18766/ >/dev/null &&
       curl -fsS http://127.0.0.1:18766/martial.js | grep -q martialWorkspace; then
        healthy=1
        break
    fi
    sleep 1
done
[ "$healthy" -eq 1 ] || { echo 'Work OS health check failed' >&2; exit 1; }
python3 - "$data" <<'PY'
import sqlite3, sys
with sqlite3.connect(sys.argv[1]) as c:
    count=c.execute("SELECT COUNT(*) FROM asset_registry WHERE source_system LIKE 'legacy_ai%'").fetchone()[0]
    qc=c.execute("SELECT result FROM martial_qc WHERE media_job_id='mj_94efec7ab99b7e95' AND stage='martial'").fetchone()
    if count < 1 or not qc or qc[0] != 'fail':
        raise SystemExit('data gate failed: legacy assets or P0 Candidate A missing')
    print(f'legacy registry entries={count}; P0 Candidate A retained as {qc[0]}')
PY
complete=1
trap - EXIT
echo "Work OS employee assets release healthy. Backup: $backup"
