#!/bin/sh
# Démo locale : lance un central + 2 robots qui lisent de faux logs.
# Ouvre ensuite http://localhost:8099 dans ton navigateur. Ctrl+C pour arrêter.
set -e
cd "$(dirname "$0")/.."
DEMO=.demo
PORT=${PORT:-8099}
rm -rf "$DEMO" && mkdir -p "$DEMO"

python3 scripts/seed_demo.py "$DEMO/demo.db"
python3 central/server.py --config /nonexistent --port "$PORT" --db "$DEMO/demo.db" &
PIDS=$!

for SRV in srv-web-01 srv-batch-01; do
  if [ "$SRV" = srv-web-01 ]; then APPS="facturation portail"; else APPS="batch-nuit reporting export-compta"; fi
  JSON=""
  for APP in $APPS; do
    mkdir -p "$DEMO/$SRV/$APP"
    JSON="$JSON{\"name\":\"$APP\",\"log_dir\":\"$DEMO/$SRV/$APP\"},"
  done
  cat > "$DEMO/$SRV.conf" <<CONF
{"central_url":"http://127.0.0.1:$PORT","server_name":"$SRV","interval_seconds":10,
 "state_dir":"$DEMO/$SRV/.state","read_from_start":true,"applications":[${JSON%,}]}
CONF
done

# Faux programmes qui écrivent des logs
(
  i=0
  while true; do
    i=$((i+1))
    for F in srv-web-01/facturation srv-web-01/portail srv-batch-01/batch-nuit srv-batch-01/reporting srv-batch-01/export-compta; do
      NOW=$(date '+%Y-%m-%d %H:%M:%S')
      echo "$NOW INFO tout va bien $i" >> "$DEMO/$F/app.log"
      case $(( (i + ${#F}) % 7 )) in
        0) echo "$NOW ERROR Connexion refusée vers la base clients id=$i" >> "$DEMO/$F/app.log" ;;
        3) echo "$NOW Traitement du lot $i : KO" >> "$DEMO/$F/app.log" ;;
        5) echo "$NOW WARN Timeout de $i ms" >> "$DEMO/$F/app.log" ;;
      esac
    done
    sleep 3
  done
) &
PIDS="$PIDS $!"

sleep 1
python3 agent/agent.py --config "$DEMO/srv-web-01.conf" &
PIDS="$PIDS $!"
python3 agent/agent.py --config "$DEMO/srv-batch-01.conf" &
PIDS="$PIDS $!"

trap 'kill $PIDS 2>/dev/null' INT TERM EXIT
echo "==> Démo lancée : ouvre http://localhost:$PORT  (Ctrl+C pour arrêter)"
wait
