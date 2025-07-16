#!/bin/sh
set -e

# 再次确认依赖
echo "验证依赖是否安装..."
if ! python3 -c "import requests"; then
    echo "紧急安装 requests..."
    pip3 install --no-cache-dir -i https://pypi.tuna.tsinghua.edu.cn/simple requests==2.31.0
fi

echo "===== HA to 163 Gateway 启动 ====="
python3 /app/main.py
