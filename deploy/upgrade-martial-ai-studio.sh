#!/bin/sh
# Upgrade the existing Work OS service with a DB snapshot and automatic rollback.
set -eu
if [ "$#" -ne 2 ]; then echo 'usage: upgrade-martial-ai-studio.sh CODE_ARCHIVE SHA256' >&2; exit 2; fi
archive=$1
expected=$2
actual=$(sha256sum "$archive" | cut -d' ' -f1)
[ "$actual" = "$expected" ] || { echo 'archive checksum mismatch' >&2; exit 1; }
data=/var/lib/yoodun-work-os/work_os.sqlite3
old=/opt/yoodun-work-os-martial-agile-20260923
new=/opt/yoodun-work-os-martial-ai-studio-20260924
unit=/etc/systemd/system/yoodun-work-os.service
[ -f "$data" ] && [ -d "$old" ] && [ -f "$unit" ] || { echo 'current Work OS installation is missing' >&2; exit 1; }
[ ! -e "$new" ] || { echo 'destination already exists; inspect before retry' >&2; exit 1; }
grep -q "$old/app/server.py" "$unit" || { echo 'unexpected active service; inspect before upgrade' >&2; exit 1; }
backup=/var/backups/yoodun-work-os/martial-ai-studio-$(date +%Y%m%d-%H%M%S)
install -d -m 0700 "$backup"
cp -a "$unit" "$backup/service.before"
complete=0
rollback() {
    if [ "$complete" -ne 1 ]; then
        set +e
        systemctl stop yoodun-work-os.service || true
        restored=0
        if [ -f "$backup/work_os.sqlite3" ]; then
            python3 - "$backup/work_os.sqlite3" "$data" <<'PY'
import sqlite3,sys
with sqlite3.connect(sys.argv[1]) as source, sqlite3.connect(sys.argv[2]) as target:
    source.backup(target)
PY
            [ "$?" -eq 0 ] && restored=1
        fi
        cp -a "$backup/service.before" "$unit"
        systemctl daemon-reload
        systemctl restart yoodun-work-os.service || true
        if [ "$restored" -eq 1 ] && systemctl is-active --quiet yoodun-work-os.service; then
            echo "Upgrade failed; previous Work OS service and database restored. Backup: $backup" >&2
        else
            echo "Upgrade failed; automatic rollback incomplete. Inspect backup: $backup" >&2
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
[ -f "$new/app/server.py" ] && [ -f "$new/app/martial_product.py" ] && [ -f "$new/app/ai_studio.py" ]
python3 -m py_compile "$new"/app/*.py
PYTHONPATH="$new/app" python3 - <<'PY'
import ai_studio
if not ai_studio._pdf_available():
    raise SystemExit('PDF text extractor missing from release')
print('PDF extractor available')
PY
sed "s#${old}#${new}#g; s/Martial Asset Workspace Agile/Martial + AI Studio Product/g" "$backup/service.before" > "$backup/service.next"
install -m 0644 "$backup/service.next" "$unit"
systemctl daemon-reload
systemctl restart yoodun-work-os.service
healthy=0
for attempt in 1 2 3 4 5 6 7 8 9 10; do
    if systemctl is-active --quiet yoodun-work-os.service &&
       curl -fsS http://127.0.0.1:18766/martial.js | grep -q martialWorkspace &&
       curl -fsS http://127.0.0.1:18766/ai_studio.js | grep -q aiStudio; then
        healthy=1
        break
    fi
    sleep 1
done
[ "$healthy" -eq 1 ] || { echo 'Work OS health check failed' >&2; exit 1; }
python3 - "$data" <<'PY'
import sqlite3,sys
with sqlite3.connect(sys.argv[1]) as c:
    arts=c.execute("SELECT COUNT(*) FROM martial_arts WHERE status='active'").fetchone()[0]
    qc=c.execute("SELECT result FROM martial_qc WHERE media_job_id='mj_94efec7ab99b7e95' AND stage='martial'").fetchone()
    revision=c.execute("SELECT COUNT(*) FROM martial_revision_packages WHERE media_job_id='mj_94efec7ab99b7e95'").fetchone()[0]
    beginner=[r[0] for r in c.execute("SELECT business_label FROM martial_business_moves WHERE martial_art_id='beginner' AND is_current=1 ORDER BY display_order")]
    pilot=c.execute("SELECT COUNT(*) FROM martial_technical_history WHERE move_id='mv_bagua_01'").fetchone()[0]
    if arts<5 or not qc or qc[0]!='fail' or revision<1 or beginner!=['马步','冲拳','马步冲拳'] or pilot<1:
        raise SystemExit(f'data gate failed: arts={arts} p0_qc={qc[0] if qc else None} p0_revision={revision}')
    print(f'data gate: arts={arts} beginner={beginner} P0 QC={qc[0]} P0 revisions={revision} history={pilot}')
PY
complete=1
trap - EXIT
echo "Work OS Martial + AI Studio service healthy. Backup: $backup"
