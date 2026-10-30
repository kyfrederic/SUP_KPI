#!/bin/sh
# Installe l'agent SUP_KPI sur ce serveur (a lancer avec sudo depuis le dossier SUP_KPI).
set -e
cd "$(dirname "$0")/.."

if [ "$(id -u)" -ne 0 ]; then echo "Lance ce script avec sudo : sudo sh agent/install_agent.sh"; exit 1; fi
command -v python3 >/dev/null || { echo "python3 est introuvable : installe-le d'abord."; exit 1; }

mkdir -p /opt/sup-kpi/agent /etc/sup-kpi /var/lib/sup-kpi-agent
cp agent/agent.py /opt/sup-kpi/agent/agent.py
if [ ! -f /etc/sup-kpi/agent.conf ]; then
  cp agent/agent.conf.example /etc/sup-kpi/agent.conf
  echo ">>> Fichier /etc/sup-kpi/agent.conf cree. MODIFIE-LE (adresse du central + tes applications),"
  echo ">>> puis relance : sudo systemctl restart sup-kpi-agent"
fi
cp agent/sup-kpi-agent.service /etc/systemd/system/sup-kpi-agent.service
systemctl daemon-reload
systemctl enable --now sup-kpi-agent
echo "Agent installe. Pour voir ce qu'il fait : sudo journalctl -u sup-kpi-agent -f"
