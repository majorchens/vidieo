#!/bin/sh
# Apply only the four employee UX files to the existing Work OS release.
set -eu

if [ "$#" -ne 2 ]; then
    echo 'usage: upgrade-employee-ux-patch.sh PATCH_ARCHIVE SHA256' >&2
    exit 2
fi
archive=$1
expected=$2
case "$expected" in
    *[!0-9a-f]*|'') echo 'expected SHA-256 must be lowercase hex' >&2; exit 2 ;;
esac
[ "${#expected}" -eq 64 ] || { echo 'expected SHA-256 length is invalid' >&2; exit 2; }
[ "$(id -u)" -eq 0 ] || { echo 'run as root' >&2; exit 1; }
[ -f "$archive" ] || { echo 'patch archive is missing' >&2; exit 1; }
actual=$(sha256sum "$archive" | cut -d' ' -f1)
[ "$actual" = "$expected" ] || { echo 'patch archive checksum mismatch' >&2; exit 1; }

install_root=/opt/yoodun-work-os-employee-assets-20260924
unit=/etc/systemd/system/yoodun-work-os.service
service=yoodun-work-os.service
files='app/asset_center.py app/static/martial.js app/static/app.js app/static/ai_studio.js'
[ -d "$install_root" ] && [ -f "$unit" ] || { echo 'expected Work OS installation is missing' >&2; exit 1; }
grep -Fq "$install_root/app/server.py" "$unit" || { echo 'active service does not use expected release' >&2; exit 1; }
systemctl is-active --quiet "$service" || { echo 'Work OS is not active before patch' >&2; exit 1; }
for file in $files; do
    [ -f "$install_root/$file" ] && [ ! -L "$install_root/$file" ] || {
        echo "missing or linked installation file: $file" >&2; exit 1;
    }
done

stage=$(mktemp -d /tmp/yoodun-employee-ux-patch.XXXXXX)
backup=''
changed=0
complete=0
healthy() {
    systemctl is-active --quiet "$service" || return 1
    curl -fsS --max-time 3 http://127.0.0.1:18766/ >/dev/null || return 1
    for file in martial.js app.js ai_studio.js; do
        expected_file_hash=$(sha256sum "$install_root/app/static/$file" | cut -d' ' -f1)
        served_hash=$(curl -fsS --max-time 3 "http://127.0.0.1:18766/$file" | sha256sum | cut -d' ' -f1) || return 1
        [ "$expected_file_hash" = "$served_hash" ] || return 1
    done
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
        if [ "$restored" -eq 1 ]; then
            echo "Patch failed; four files and service restored. Backup: $backup" >&2
        else
            echo "Patch failed and rollback needs manual attention. Backup: $backup" >&2
        fi
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
expected = {"app/asset_center.py", "app/static/martial.js",
            "app/static/app.js", "app/static/ai_studio.js",
            "deploy/upgrade-employee-ux-patch.sh"}
with tarfile.open(archive, "r:gz") as bundle:
    members = bundle.getmembers()
    if {member.name for member in members} != expected or len(members) != len(expected):
        raise SystemExit("patch archive must contain four application files and this installer")
    for member in members:
        if not member.isfile() or member.size > 1_000_000:
            raise SystemExit("patch archive contains an invalid file")
        target = pathlib.Path(stage, member.name)
        target.parent.mkdir(parents=True, exist_ok=True)
        with bundle.extractfile(member) as source, target.open("wb") as output:
            output.write(source.read())
compile(pathlib.Path(stage, "app/asset_center.py").read_bytes(),
        "app/asset_center.py", "exec")
PY

backup=/var/backups/yoodun-work-os/employee-ux-patch-$(date +%Y%m%d-%H%M%S)-$$
install -d -m 0700 "$backup/app/static"
for file in $files; do cp -a "$install_root/$file" "$backup/$file"; done

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
[ "$ready" -eq 1 ] || { echo 'patched Work OS health check failed' >&2; exit 1; }
complete=1
echo "Employee UX patch healthy. Backup: $backup"
