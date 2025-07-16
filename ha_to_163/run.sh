#!/bin/sh
set -e

# 检查关键模块是否安装
echo "检查依赖安装情况..."
python3 -c "import requests; import paho.mqtt; import ntplib; print('依赖检查通过')"

# 启动主程序
echo "===== HA to 163 Gateway 启动 ====="
python3 /app/main.py
