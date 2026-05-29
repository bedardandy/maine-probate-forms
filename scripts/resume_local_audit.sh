#!/bin/bash
# Resume the overnight local audit on forms whose previous report had errors.
# Detects bad reports (non-zero `errors` array OR `_error` in pages), deletes
# them, then runs local_alignment_review on the remaining forms with -j 2.
set -u
cd /path/to/maine-probate-forms-oss

REPORT_DIR="reports/local-alignment-fused-overnight"
LOG=/tmp/local_audit_resume.log
PIDFILE=/tmp/local_audit_resume.pid

if [ -f "$PIDFILE" ] && kill -0 "$(cat "$PIDFILE")" 2>/dev/null; then
  echo "Already running (PID $(cat "$PIDFILE")). Tail: tail -f $LOG"
  exit 1
fi

echo "Healthcheck vLLM..."
if ! curl -sf --max-time 5 http://localhost:8088/v1/models >/dev/null; then
  echo "ERROR: vLLM at localhost:8088 not reachable. Start it first:"
  echo "  bash /path/to/qwen36-vllm/launch.sh"
  exit 2
fi
echo "  vLLM is up."

# Identify and delete reports with errors
deleted=0
for f in "$REPORT_DIR"/*.json; do
  [ -f "$f" ] || continue
  has_err=$(.venv/bin/python3 -c "
import json, sys
d = json.loads(open('$f').read())
errs = d.get('errors', [])
page_errs = sum(1 for pg in d.get('pages', []) if '_error' in pg)
print('1' if errs or page_errs else '0')
")
  if [ "$has_err" = "1" ]; then
    rm "$f"
    deleted=$((deleted + 1))
  fi
done
echo "Deleted $deleted reports with errors. Remaining intact: $(ls "$REPORT_DIR"/*.json 2>/dev/null | wc -l)"

# Launch audit (skips reports that already exist) with -j 2 detached
setsid nohup env \
  AUDIT_BASE_URL=http://localhost:8088/v1 \
  AUDIT_MODEL=Qwen3.6-27B-FP8 \
  AUDIT_REPORT_DIR="$REPORT_DIR" \
  .venv/bin/python3 scripts/local_alignment_review.py \
    --root output_fused -j 2 --form "_fused" \
  >> "$LOG" 2>&1 < /dev/null &

PID=$!
echo "$PID" > "$PIDFILE"
disown -h "$PID" 2>/dev/null || true

sleep 2
if kill -0 "$PID" 2>/dev/null; then
  echo "Resumed PID $PID, detached."
  echo "Log: $LOG  |  PID file: $PIDFILE"
  echo "Stop: kill \$(cat $PIDFILE)"
else
  echo "Process died. Check log: $LOG"
  exit 1
fi
