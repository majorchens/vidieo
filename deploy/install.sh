#!/bin/sh
# Install only the separate Work OS service. Add HTTPS Nginx route after review.
set -eu
if [ "$#" -ne 3 ]; then
    echo "usage: install.sh CODE_TAR RUNTIME_TAR SERVER_ENV" >&2
    exit 2
fi
code_tar=$1
runtime_tar=$2
server_env=$3
if [ -e /opt/yoodun-work-os ] || [ -e /var/lib/yoodun-work-os ] || [ -e /etc/systemd/system/yoodun-work-os.service ]; then
    echo "Work OS destination already exists; inspect before an update" >&2
    exit 1
fi
id yoodun-os >/dev/null 2>&1 || useradd --system --home-dir /var/lib/yoodun-work-os --shell /sbin/nologin yoodun-os
install -d -m 0755 /opt/yoodun-work-os
install -d -m 0700 -o yoodun-os -g yoodun-os /var/lib/yoodun-work-os
tar -xzf "$code_tar" -C /opt/yoodun-work-os
tar -xzf "$runtime_tar" -C /var/lib/yoodun-work-os
install -m 0600 "$server_env" /etc/yoodun-work-os.env
chown -R root:root /opt/yoodun-work-os
chown -R yoodun-os:yoodun-os /var/lib/yoodun-work-os
chmod 0700 /var/lib/yoodun-work-os
chmod 0600 /var/lib/yoodun-work-os/work_os.sqlite3
YOODUN_DATA_DIR=/var/lib/yoodun-work-os /usr/bin/python3 /opt/yoodun-work-os/app/rebase_runtime.py --apply
install -m 0644 /opt/yoodun-work-os/deploy/yoodun-work-os.service /etc/systemd/system/yoodun-work-os.service
systemctl daemon-reload
systemctl enable --now yoodun-work-os.service
for attempt in 1 2 3 4 5; do
    if curl --fail --silent http://127.0.0.1:18766/ >/dev/null; then
        echo "Work OS local service responds; review and add HTTPS route separately"
        exit 0
    fi
    sleep 1
done
echo "Work OS service did not respond on its local port" >&2
exit 1
