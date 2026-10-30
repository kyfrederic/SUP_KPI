#!/bin/sh
# Installe le central SUP_KPI sur ce serveur (a lancer avec sudo depuis le dossier SUP_KPI).
set -e
cd "$(dirname "$0")/.."

if [ "$(id -u)" -ne 0 ]; then echo "Lance ce script avec sudo : sudo sh central/install_central.sh"; exit 1; fi
command -v python3 >/dev/null || { echo "python3 est introuvable : installe-le d'abord."; exit 1; }

id sup-kpi >/dev/null 2>&1 || useradd --system --no-create-home --shell /usr/sbin/nologin sup-kpi
mkdir -p /opt/sup-kpi /etc/sup-kpi /var/lib/sup-kpi-central
rm -rf /opt/sup-kpi/central
cp -r central /opt/sup-kpi/central
chown -R sup-kpi /var/lib/sup-kpi-central
[ -f /etc/sup-kpi/central.conf ] || cp central/central.conf.example /etc/sup-kpi/central.conf
cp central/sup-kpi-central.service /etc/systemd/system/sup-kpi-central.service
systemctl daemon-reload
systemctl enable --now sup-kpi-central
PORT=$(python3 -c "import json;print(json.load(open('/etc/sup-kpi/central.conf')).get('port',8080))")
echo "Central installe. Ouvre http://$(hostname -I | awk '{print $1}'):$PORT dans ton navigateur."
echo "Pense a ouvrir le port $PORT dans le pare-feu si besoin."
