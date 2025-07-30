import logging
import time
import json
import signal
import requests
from utils.config_loader import ConfigLoader
from utils.mqtt_client import MQTTClient
from device_discovery.ha_discovery import HADiscovery


class HAto163Gateway:
    def __init__(self):
        # 加载配置
        self.config_loader = ConfigLoader()
        self.config = self.config_loader.config
        self.logger = logging.getLogger("ha_to_163")

        # 初始化HA请求头
        self.ha_headers = {
            "Authorization": f"Bearer {self.config['ha_token']}",
            "Content-Type": "application/json"
        }

        # 设备与MQTT客户端
        self.matched_devices = {}
        self.mqtt_client = MQTTClient(self.config)
        self.running = True

        # 注册退出信号
        signal.signal(signal.SIGINT, self._stop)
        signal.signal(signal.SIGTERM, self._stop)

    def _stop(self, signum, frame):
        self.logger.info("收到停止信号，正在退出...")
        self.running = False
        if hasattr(self, 'mqtt_client') and self.mqtt_client:
            self.mqtt_client.disconnect()

    def _wait_for_ha_ready(self) -> bool:
        """等待Home Assistant就绪"""
        timeout = self.config.get("entity_ready_timeout", 600)
        start_time = time.time()
        while time.time() - start_time < timeout:
            try:
                resp = requests.get(
                    f"{self.config['ha_url']}/api/",
                    headers=self.ha_headers,
                    timeout=10
                )
                if resp.status_code == 200:
                    self.logger.info("Home Assistant已就绪")
                    return True
            except Exception as e:
                self.logger.warning(f"HA未就绪: {e}")
            time.sleep(10)

        self.logger.error(f"HA超时未就绪（{timeout}秒）")
        return False

    def _discover_devices(self) -> bool:
        """执行设备发现"""
        discovery = HADiscovery(self.config, self.ha_headers)
        self.matched_devices = discovery.discover()
        return len(self.matched_devices) > 0

    def _get_entity_value(self, entity_id: str, device_type: str) -> float or int or None:
        """获取HA实体值（优化电气参数解析）"""
        try:
            # 等待实体就绪
            timeout = self.config.get("entity_ready_timeout", 600)
            start_time = time.time()
            while time.time() - start_time < timeout:
                resp = requests.get(
                    f"{self.config['ha_url']}/api/states/{entity_id}",
                    headers=self.ha_headers,
                    timeout=5
                )
                if resp.status_code == 200:
                    state = resp.json().get("state")
                    if state in ("unknown", "unavailable", ""):
                        time.sleep(5)
                        continue

                    # 处理开关/插座/断路器状态
                    if device_type in ("switch", "socket", "breaker"):
                        if state == "on":
                            return 1
                        elif state == "off":
                            return 0
                        elif state == "trip" and device_type == "breaker":
                            return 2

                    # 处理数值型（传感器/电气参数）
                    # 支持带单位的数值（如"220 V" → 220，"1.5 A" → 1.5，"500 Wh" → 500）
                    import re
                    match = re.search(r'[-+]?\d*\.\d+|\d+', state)  # 提取数字部分
                    if match:
                        return float(match.group())

                    self.logger.warning(f"实体 {entity_id} 状态无法转换为有效值: {state}")
                    return None

                time.sleep(5)

            self.logger.error(f"实体 {entity_id} 超时未就绪")
            return None
        except Exception as e:
            self.logger.error(f"获取实体 {entity_id} 失败: {e}")
            return None

    def _collect_device_data(self, device_id: str) -> dict:
        """收集设备数据（新增电气参数处理）"""
        device_data = self.matched_devices[device_id]
        device_config = device_data["config"]
        device_type = device_config["type"]
        entities = device_data["entities"]

        payload = {
            "id": int(time.time() * 1000),
            "version": "1.0",
            "params": {}
        }

        for prop, entity_id in entities.items():
            value = self._get_entity_value(entity_id, device_type)
            if value is not None:
                payload["params"][prop] = value
                self.logger.info(f"  收集到 {prop} = {value}（实体: {entity_id}）")
            else:
                self.logger.warning(f"  未获取到 {prop} 数据（实体: {entity_id}）")

        # 传感器电池默认值
        if device_type == "sensor" and "batt" in device_config["supported_properties"] and "batt" not in payload["params"]:
            self.logger.warning(f"  未获取到电池数据，使用默认值100")
            payload["params"]["batt"] = 100

        # 插座电气参数默认值处理（新增）
        if device_type == "socket":
            # 电压默认值（220V）
            if "voltage" in device_config["supported_properties"] and "voltage" not in payload["params"]:
                self.logger.warning(f"  未获取到电压数据，使用默认值220")
                payload["params"]["voltage"] = 220
            # 电流默认值（0A，未使用时）
            if "current" in device_config["supported_properties"] and "current" not in payload["params"]:
                self.logger.warning(f"  未获取到电流数据，使用默认值0")
                payload["params"]["current"] = 0
            # 功率默认值（0W，未使用时）
            if "power" in device_config["supported_properties"] and "power" not in payload["params"]:
                self.logger.warning(f"  未获取到功率数据，使用默认值0")
                payload["params"]["power"] = 0

        # 断路器默认状态
        if device_type == "breaker" and "state" in device_config["supported_properties"] and "state" not in payload["params"]:
            self.logger.warning(f"  未获取到断路器状态，使用默认值0（off）")
            payload["params"]["state"] = 0

        return payload

    def _push_device_data(self, device_id: str) -> bool:
        """推送设备数据到网易IoT平台"""
        device_data = self.matched_devices[device_id]
        device_config = device_data["config"]

        payload = self._collect_device_data(device_id)
        if not payload["params"]:
            self.logger.warning(f"设备 {device_id} 无有效数据，跳过推送")
            return False

        return self.mqtt_client.publish(device_config, payload)

    def start(self):
        """启动服务"""
        self.logger.info("===== HA to 163 Gateway 启动 =====")

        # 启动延迟
        startup_delay = self.config.get("startup_delay", 120)
        self.logger.info(f"启动延迟 {startup_delay} 秒...")
        time.sleep(startup_delay)

        # 等待HA就绪
        if not self._wait_for_ha_ready():
            return

        # 连接MQTT broker
        if not self.mqtt_client.connect():
            return

        # 初始设备发现
        if not self._discover_devices():
            self.logger.error("未匹配到任何设备，服务启动失败")
            return

        # 主循环
        self._run_loop()

    def _run_loop(self):
        """主循环（定时发现与推送）"""
        push_interval = self.config.get("wy_push_interval", 60)
        discovery_interval = self.config.get("ha_discovery_interval", 3600)
        last_discovery = time.time()
        last_push = time.time()

        while self.running:
            now = time.time()

            # 定时重新发现设备
            if now - last_discovery >= discovery_interval:
                self.logger.info("执行定时设备发现...")
                self._discover_devices()
                last_discovery = now

            # 定时推送数据
            if now - last_push >= push_interval:
                self.logger.info("开始数据推送...")
                for device_id in self.matched_devices:
                    self.logger.info(f"\n推送设备 {device_id} 数据")
                    self._push_device_data(device_id)
                last_push = now

            time.sleep(1)


if __name__ == "__main__":
    # 配置日志
    import os

    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    )
    gateway = HAto163Gateway()
    gateway.start()
    
