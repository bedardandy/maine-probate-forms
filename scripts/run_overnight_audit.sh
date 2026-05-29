#!/bin/bash
# Overnight local audit — survives Claude Code session termination.
# Use: bash scripts/run_overnight_audit.sh  (will detach and return immediately)
set -u
cd /path/to/maine-probate-forms-oss

LOG=/tmp/local_audit_overnight.log
PIDFILE=/tmp/local_audit_overnight.pid

if [ -f "$PIDFILE" ] && kill -0 "$(cat "$PIDFILE")" 2>/dev/null; then
  echo "Already running (PID $(cat "$PIDFILE")). Tail log: tail -f $LOG"
  exit 1
fi

# setsid creates a new session detached from the controlling terminal.
# nohup ignores SIGHUP. Combined → survives the parent shell exiting.
setsid nohup env \
  AUDIT_BASE_URL=http://localhost:8088/v1 \
  AUDIT_MODEL=Qwen3.6-27B-FP8 \
  AUDIT_REPORT_DIR=reports/local-alignment-fused-overnight \
  .venv/bin/python3 scripts/local_alignment_review.py \
    --root output_fused -j 4 --form "_fused" \
  >> "$LOG" 2>&1 < /dev/null &

PID=$!
echo "$PID" > "$PIDFILE"
disown -h "$PID" 2>/dev/null || true

sleep 2
if kill -0 "$PID" 2>/dev/null; then
  echo "Started PID $PID, detached. Log: $LOG"
  echo "PID file: $PIDFILE"
  echo "Stop:    kill \$(cat $PIDFILE)"
else
  echo "Process died immediately. Check log: $LOG"
  exit 1
fi
