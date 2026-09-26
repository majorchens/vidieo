#!/bin/sh
# Install the reference-fidelity guard without touching Work OS data.
set -eu

[ "$#" -eq 2 ] || { echo 'usage: upgrade-mabu-reference-patch.sh ARCHIVE SHA256' >&2; exit 2; }
archive=$1
expected=$2
case "$expected" in *[!0-9a-f]*|'') echo 'invalid SHA-256' >&2; exit 2 ;; esac
[ "${#expected}" -eq 64 ] || { echo 'invalid SHA-256 length' >&2; exit 2; }
[ "$(id -u)" -eq 0 ] || { echo 'root is required' >&2; exit 1; }
[ -f "$archive" ] || { echo 'archive is missing' >&2; exit 1; }
[ "$(sha256sum "$archive" | cut -d' ' -f1)" = "$expected" ] || {
    echo 'archive checksum mismatch' >&2; exit 1;
}

install_root=/opt/yoodun-work-os-employee-assets-20260924
unit=/etc/systemd/system/yoodun-work-os.service
service=yoodun-work-os.service
db=/var/lib/yoodun-work-os/work_os.sqlite3
files='app/server.py app/martial.py app/martial_connector.py app/static/martial.js'
[ -d "$install_root" ] && [ -f "$unit" ] || { echo 'expected release is missing' >&2; exit 1; }
[ -f "$db" ] && [ ! -L "$db" ] || { echo 'Work OS database is missing or linked' >&2; exit 1; }
grep -Fq "$install_root/app/server.py" "$unit" || { echo 'service points to another release' >&2; exit 1; }
systemctl is-active --quiet "$service" || { echo 'service is not active' >&2; exit 1; }
for file in $files; do
    [ -f "$install_root/$file" ] && [ ! -L "$install_root/$file" ] || {
        echo "missing or linked target: $file" >&2; exit 1;
    }
done

stage=$(mktemp -d /tmp/yoodun-mabu-reference.XXXXXX)
backup=''
changed=0
complete=0
healthy() {
    systemctl is-active --quiet "$service" || return 1
    curl -fsS --max-time 3 http://127.0.0.1:18766/ >/dev/null || return 1
    [ "$(sha256sum "$install_root/app/static/martial.js" | cut -d' ' -f1)" = \
      "$(curl -fsS --max-time 3 http://127.0.0.1:18766/martial.js | sha256sum | cut -d' ' -f1)" ] || return 1
}
cleanup() {
    result=$?
    trap - EXIT
    if [ "$complete" -ne 1 ] && [ "$changed" -eq 1 ]; then
        set +e
        restored=1
        for file in $files; do
            cp -a "$backup/$file" "$install_root/$file.rollback.$$" &&
                mv -f "$install_root/$file.rollback.$$" "$install_root/$file" || restored=0
        done
        systemctl restart "$service" || restored=0
        healthy || restored=0
        [ "$restored" -eq 1 ] || echo "rollback needs attention: $backup" >&2
        [ "$result" -ne 0 ] || result=1
    fi
    rm -rf "$stage"
    exit "$result"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
trap 'exit 129' HUP

python3 - "$archive" "$stage" <<'PY'
import pathlib, sys, tarfile
archive, stage = sys.argv[1:]
expected = {"app/server.py", "app/martial.py", "app/martial_connector.py",
            "app/static/martial.js", "deploy/upgrade-mabu-reference-patch.sh"}
with tarfile.open(archive, "r:gz") as bundle:
    members = bundle.getmembers()
    if len(members) != len(expected) or {item.name for item in members} != expected:
        raise SystemExit("unexpected archive contents")
    for item in members:
        if not item.isfile() or item.size > 1_000_000:
            raise SystemExit("invalid archive member")
        target = pathlib.Path(stage, item.name)
        target.parent.mkdir(parents=True, exist_ok=True)
        with bundle.extractfile(item) as source, target.open("wb") as output:
            output.write(source.read())
for name in ("server.py", "martial.py", "martial_connector.py"):
    compile(pathlib.Path(stage, "app", name).read_bytes(), name, "exec")
PY

backup=/var/backups/yoodun-work-os/mabu-reference-$(date +%Y%m%d-%H%M%S)-$$
install -d -m 0700 "$backup/app/static"
for file in $files; do cp -a "$install_root/$file" "$backup/$file"; done
python3 - "$db" "$backup/work_os.sqlite3" <<'PY'
import sqlite3, sys
source = sqlite3.connect("file:" + sys.argv[1] + "?mode=ro", uri=True)
target = sqlite3.connect(sys.argv[2])
source.backup(target)
if target.execute("PRAGMA quick_check").fetchone()[0] != "ok":
    raise SystemExit("database backup failed integrity check")
target.close()
source.close()
PY
chmod 0600 "$backup/work_os.sqlite3"

changed=1
for file in $files; do
    install -o root -g root -m 0644 "$stage/$file" "$install_root/$file.next.$$"
    mv -f "$install_root/$file.next.$$" "$install_root/$file"
done
systemctl restart "$service"
ready=0
for attempt in 1 2 3 4 5 6 7 8 9 10; do
    if healthy; then ready=1; break; fi
    sleep 1
done
[ "$ready" -eq 1 ] || { echo 'Work OS health check failed' >&2; exit 1; }
complete=1
echo "Reference-fidelity patch healthy. Backup: $backup"
