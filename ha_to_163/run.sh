#!/bin/sh
set -e

echo "===== HA to 163 Gateway 启动 ====="

LOG_LEVEL=${LOG_LEVEL:-info}

case "$LOG_LEVEL" in
  "debug") LOG_LEVEL_PYTHON="DEBUG" ;;
  "info") LOG_LEVEL_PYTHON="INFO" ;;
  "warning") LOG_LEVEL_PYTHON="WARNING" ;;
  "error") LOG_LEVEL_PYTHON="ERROR" ;;
  "critical") LOG_LEVEL_PYTHON="CRITICAL" ;;
  *) LOG_LEVEL_PYTHON="INFO" ;;
esac

export LOG_LEVEL=$LOG_LEVEL_PYTHON

python3 /app/main.py
    
