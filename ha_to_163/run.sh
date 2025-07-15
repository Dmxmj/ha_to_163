#!/bin/bash

# 设置日志级别
LOG_LEVEL=${LOG_LEVEL:-info}

# 转换日志级别为Python可用格式
case "$LOG_LEVEL" in
  "debug") LOG_LEVEL_PYTHON="DEBUG" ;;
  "info") LOG_LEVEL_PYTHON="INFO" ;;
  "warning") LOG_LEVEL_PYTHON="WARNING" ;;
  "error") LOG_LEVEL_PYTHON="ERROR" ;;
  "critical") LOG_LEVEL_PYTHON="CRITICAL" ;;
  *) LOG_LEVEL_PYTHON="INFO" ;;
esac

# 设置环境变量
export LOG_LEVEL=$LOG_LEVEL_PYTHON

# 启动应用
python main.py
