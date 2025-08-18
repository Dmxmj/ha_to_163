#!/usr/bin/env sh

# 加载Home Assistant配置
CONFIG_PATH="/data/options.json"

# 读取配置参数（示例：读取网易IoT平台地址）
WY_MQTT_BROKER=$(jq -r '.wy_mqtt_broker' $CONFIG_PATH)
LOG_LEVEL=$(jq -r '.log_level' $CONFIG_PATH)

# 设置日志级别
echo "启动HA to 163 Gateway，日志级别：$LOG_LEVEL"
echo "连接网易IoT平台：$WY_MQTT_BROKER"

# 启动Python服务（实际插件逻辑，此处为示例）
python3 -c "
import time
while True:
    print('推送数据到网易IoT平台...')
    time.sleep(10)
"

# 若有实际Python脚本，替换为：python3 /path/to/your_script.py

