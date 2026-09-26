#!/bin/sh
# Upgrade the existing Work OS service without changing old studio files.
set -eu

if [ "$#" -ne 2 ]; then echo 'usage: upgrade-v03.sh CODE_ARCHIVE SHA256' >&2; exit 2; fi
archive=$1
expected=$2
case "$expected" in *[!0-9a-f]*|'') echo 'invalid SHA-256' >&2; exit 2;; esac
[ "${#expected}" -eq 64 ] && [ "$(id -u)" -eq 0 ] && [ -f "$archive" ] || exit 2
[ "$(sha256sum "$archive" | cut -d' ' -f1)" = "$expected" ] || { echo 'archive checksum mismatch' >&2; exit 1; }

old=/opt/yoodun-work-os-employee-assets-20260924
new=/opt/yoodun-work-os-v03-20260924
data_root=/var/lib/yoodun-work-os
data=$data_root/work_os.sqlite3
unit=/etc/systemd/system/yoodun-work-os.service
service=yoodun-work-os.service
[ -d "$old" ] && [ ! -e "$new" ] && [ -f "$data" ] && [ -f "$unit" ] || {
    echo 'expected installed release or database is missing' >&2; exit 1;
}
grep -Fq "$old/app/server.py" "$unit" || { echo 'active service path differs from reviewed release' >&2; exit 1; }
systemctl is-active --quiet "$service" || { echo 'Work OS service was not healthy before upgrade' >&2; exit 1; }

python3 - "$archive" <<'PY'
import sys, tarfile
with tarfile.open(sys.argv[1], 'r:gz') as archive:
    names=set()
    for member in archive.getmembers():
        if not member.isfile() or member.size>2_000_000 or member.name in names or not (
            member.name.startswith('app/') or member.name in {
                'deploy/upgrade-v03.sh','deploy/import_fixed_voices.py'}
        ) or '..' in member.name.split('/') or member.name.startswith('/'):
            raise SystemExit('release archive contains invalid member')
        names.add(member.name)
    required={'app/server.py','app/ai_studio.py','app/ai_studio_connector.py',
              'app/asset_center.py','app/legacy_asset_bridge.py',
              'app/martial_multimodal_assets.py','app/martial.py','app/martial_connector.py',
              'app/static/index.html','app/static/app.js','app/static/martial.js',
              'app/static/ai_studio.js','app/static/styles.css',
              'app/static/martial.css','app/static/ai_studio.css',
              'deploy/upgrade-v03.sh','deploy/import_fixed_voices.py'}
    if names!=required:raise SystemExit('release archive manifest differs from reviewed files')
PY

backup=/var/backups/yoodun-work-os/v03-$(date +%Y%m%d-%H%M%S)-$$
install -d -m 0700 "$backup"
cp -a "$unit" "$backup/service.before"

changed=0
complete=0
new_created=0
db_backup_ready=0
rollback() {
    result=$?
    trap - EXIT
    if [ "$complete" -eq 1 ]; then return; fi
    if [ "$changed" -eq 1 ]; then
        set +e
        restored=1
        systemctl stop "$service" || restored=0
        if [ "$db_backup_ready" -eq 1 ]; then
        python3 - "$backup/work_os.sqlite3" "$data" <<'PY'
import sqlite3,sys
with sqlite3.connect(sys.argv[1]) as source, sqlite3.connect(sys.argv[2]) as target:
    source.backup(target)
PY
            [ "$?" -eq 0 ] || restored=0
        fi
        cp -a "$backup/service.before" "$unit" || restored=0
        systemctl daemon-reload || restored=0
        systemctl restart "$service" || restored=0
        systemctl is-active --quiet "$service" || restored=0
        if [ "$restored" -eq 1 ]; then
            echo "V0.3 upgrade failed; old service and database restored. Backup: $backup" >&2
            rm -rf "$new"
        else
            echo "V0.3 rollback needs manual attention. Backup: $backup" >&2
        fi
    elif [ "$new_created" -eq 1 ]; then
        rm -rf "$new"
    fi
    [ "$result" -ne 0 ] || result=1
    exit "$result"
}
trap rollback EXIT

install -d -m 0755 "$new"
new_created=1
cp -a "$old/." "$new/"
tar -xzf "$archive" -C "$new"
chown -R root:root "$new"
python3 -m py_compile "$new"/app/*.py

changed=1
systemctl stop "$service"
python3 - "$data" "$backup/work_os.sqlite3" <<'PY'
import sqlite3,sys
with sqlite3.connect(sys.argv[1]) as source, sqlite3.connect(sys.argv[2]) as target:
    source.backup(target)
PY
chmod 0600 "$backup/work_os.sqlite3"
db_backup_ready=1
runuser -u yoodun-os -- env YOODUN_DATA_DIR="$data_root" PYTHONPATH="$new/app" python3 - <<'PY'
import ai_studio, asset_center, legacy_asset_bridge, martial_multimodal_assets, sqlite3, store
martial_multimodal_assets.initialize()
ai_studio.initialize()
asset_center.initialize()
legacy_asset_bridge.initialize()
asset_center.sync_internal()
with store.connect() as c:
    founder=c.execute("SELECT id FROM users WHERE role='founder' AND active=1 ORDER BY created_at LIMIT 1").fetchone()
    policy=c.execute("SELECT enabled,updated_by FROM ai_studio_routes WHERE business_model='image'").fetchone()
    if not founder or not policy:raise SystemExit('founder or image route missing')
    enable=not policy['enabled'] and policy['updated_by']=='system'
if enable:
    ai_studio.set_route({'id':founder['id'],'role':'founder'},
                        {'business_model':'image','primary_provider':'wanjie',
                         'enabled':True,'model':'jimeng_t2i_v40'})
print('V0.3 migrations complete; image route', 'enabled for bounded trial' if enable else 'preserved')
PY

sed "s#${old}#${new}#g; s/Work OS V0.2/Work OS V0.3/g" "$backup/service.before" > "$backup/service.next"
install -m 0644 "$backup/service.next" "$unit"
systemctl daemon-reload
systemctl restart "$service"

healthy=0
for attempt in 1 2 3 4 5 6 7 8 9 10; do
    if systemctl is-active --quiet "$service" &&
       curl -fsS --max-time 3 http://127.0.0.1:18766/ | grep -q 'V0.3' &&
       [ "$(curl -fsS --max-time 3 http://127.0.0.1:18766/martial.js | sha256sum | cut -d' ' -f1)" = "$(sha256sum "$new/app/static/martial.js" | cut -d' ' -f1)" ]; then
        healthy=1; break
    fi
    sleep 1
done
[ "$healthy" -eq 1 ] || { echo 'V0.3 service health check failed' >&2; exit 1; }
python3 - "$data" <<'PY'
import sqlite3,sys
with sqlite3.connect(sys.argv[1]) as c:
    tables={row[0] for row in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if not {'martial_mm_os_scripts','legacy_asset_identity_map','asset_registry','martial_video_plans'} <= tables:
        raise SystemExit('V0.3 data structures missing')
    media_columns={row[1] for row in c.execute('PRAGMA table_info(martial_media_jobs)')}
    if not {'generation_mode','video_plan_id'} <= media_columns:
        raise SystemExit('dual-video media fields missing')
    row=c.execute("SELECT result FROM martial_qc WHERE media_job_id='mj_94efec7ab99b7e95' AND stage='martial'").fetchone()
    if not row or row[0]!='fail':raise SystemExit('P0 Candidate A evidence changed')
    print('P0 Candidate A remains REVISION_REQUIRED; V0.3 tables present')
PY
complete=1
trap - EXIT
echo "V0.3 Work OS healthy. Backup: $backup"
