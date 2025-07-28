import requests
import re
import logging
import time
from typing import Dict
from .base_discovery import BaseDiscovery

# 扩展属性映射（新增socket和breaker相关属性）
PROPERTY_MAPPING = {
    # 基础映射
    "temperature": "temp",
    "temp": "temp",
    "temp_c": "temp",
    "temp_f": "temp",
    "humidity": "hum",
    "hum": "hum",
    "humidity_percent": "hum",
    "battery": "batt",
    "batt": "batt",
    "battery_level": "batt",
    "battery_percent": "batt",

    # 带后缀的扩展映射
    "temperature_p": "temp",
    "temp_p": "temp",
    "humidity_p": "hum",
    "hum_p": "hum",
    "battery_p": "batt",
    "batt_p": "batt",

    # Socket（插座）相关属性
    "switch": "switch",  # 开关状态
    "power": "power",    # 功率（W）
    "current": "current",# 电流（A）
    "voltage": "voltage",# 电压（V）
    "energy": "energy",  # 能耗（kWh）

    # Breaker（断路器）相关属性
    "breaker_switch": "switch",  # 断路器开关
    "breaker_current": "current",# 断路器电流
    "breaker_power": "power",    # 断路器功率
    "leakage": "leakage"         # 漏电电流（mA）
}


class HADiscovery(BaseDiscovery):
    """基于HA实体的设备发现（支持传感器、插座、断路器）"""

    def __init__(self, config, ha_headers):
        super().__init__(config, "ha_discovery")
        self.ha_url = config.get("ha_url")
        self.ha_headers = ha_headers
        self.entities = []  # 存储HA中的实体列表
        # 筛选启用的子设备（包含传感器、插座、断路器）
        self.sub_devices = [d for d in config.get("sub_devices", []) if d.get("enabled", True)]

    def load_ha_entities(self) -> bool:
        """从HA API加载实体列表（不变）"""
        try:
            self.logger.info(f"从HA获取实体列表: {self.ha_url}/api/states")
            resp = None
            retry_attempts = self.config.get("retry_attempts", 5)
            retry_delay = self.config.get("retry_delay", 3)

            # 带重试的API调用
            for attempt in range(retry_attempts):
                try:
                    resp = requests.get(
                        f"{self.ha_url}/api/states",
                        headers=self.ha_headers,
                        timeout=10
                    )
                    resp.raise_for_status()
                    break
                except Exception as e:
                    self.logger.warning(f"获取HA实体尝试 {attempt + 1}/{retry_attempts} 失败: {e}")
                    if attempt < retry_attempts - 1:
                        time.sleep(retry_delay)

            if not resp or resp.status_code != 200:
                self.logger.error(f"HA实体获取失败，状态码: {resp.status_code if resp else '无响应'}")
                return False

            self.entities = resp.json()
            self.logger.info(f"HA共返回 {len(self.entities)} 个实体")

            # 输出所有设备类型实体列表（含插座、断路器）
            relevant_entities = [e.get('entity_id') for e in self.entities if
                               e.get('entity_id', '').startswith(('sensor.', 'switch.', 'binary_sensor.'))]
            self.logger.debug(f"HA中的相关实体列表: {relevant_entities}")
            return True
        except Exception as e:
            self.logger.error(f"加载HA实体失败: {e}")
            return False

    def match_entities_to_devices(self) -> Dict:
        """将HA实体匹配到子设备（支持插座、断路器）"""
        matched_devices = {}

        for device in self.sub_devices:
            device_id = device["id"]
            device_type = device.get("type", "sensor")  # 新增设备类型标识
            matched_devices[device_id] = {
                "config": device,
                "sensors": {},  # 存储 {属性: 实体ID} 映射
                "last_data": None
            }
            self.logger.info(f"开始匹配设备: {device_id}（类型: {device_type}, 前缀: {device['ha_entity_prefix']}）")

        # 遍历HA实体进行匹配（扩展支持switch和binary_sensor域）
        for entity in self.entities:
            entity_id = entity.get("entity_id", "")
            if not entity_id.startswith(("sensor.", "switch.", "binary_sensor.")):
                continue  # 处理传感器、开关、二进制传感器实体

            # 提取实体属性
            attributes = entity.get("attributes", {})
            device_class = attributes.get("device_class", "").lower()
            friendly_name = attributes.get("friendly_name", "").lower()
            self.logger.debug(f"处理实体: {entity_id} (device_class: {device_class}, friendly_name: {friendly_name})")

            # 匹配到对应的子设备
            for device_id, device_data in matched_devices.items():
                device = device_data["config"]
                prefix = device["ha_entity_prefix"]
                device_type = device.get("type", "sensor")

                # 按设备类型和前缀匹配
                if prefix in entity_id:
                    # 提取实体类型（如"switch.socket_01_switch" → "switch"）
                    entity_type_parts = entity_id.replace(prefix, "").strip('_').split('_')
                    entity_type = '_'.join(entity_type_parts)
                    if not entity_type:
                        continue

                    # 多维度匹配属性（适配插座、断路器）
                    property_name = None

                    # 方式1：通过device_class匹配
                    if device_class in PROPERTY_MAPPING:
                        property_name = PROPERTY_MAPPING[device_class]
                        self.logger.debug(f"通过device_class匹配: {device_class} → {property_name}")

                    # 方式2：通过实体ID部分匹配
                    if not property_name:
                        for part in entity_type_parts:
                            if part in PROPERTY_MAPPING:
                                property_name = PROPERTY_MAPPING[part]
                                self.logger.debug(f"通过实体ID部分匹配: {part} → {property_name}")
                                break

                    # 方式3：通过friendly_name匹配
                    if not property_name:
                        for key in PROPERTY_MAPPING:
                            if key in friendly_name:
                                property_name = PROPERTY_MAPPING[key]
                                self.logger.debug(f"通过friendly_name匹配: {key} → {property_name}")
                                break

                    # 验证属性是否在设备支持列表中
                    if property_name and property_name in device["supported_properties"]:
                        device_data["sensors"][property_name] = entity_id
                        self.logger.info(f"匹配成功: {entity_id} → {property_name}（设备: {device_id}, 类型: {device_type}）")
                        break  # 已匹配到设备，跳出循环

        # 输出匹配结果
        for device_id, device_data in matched_devices.items():
            sensors = {k: v for k, v in device_data["sensors"].items()}
            self.logger.info(f"设备 {device_id} 匹配结果: {sensors}")

        return matched_devices

    def discover(self) -> Dict:
        """执行发现流程（主入口）"""
        self.logger.info("开始基于HA实体的设备发现...")

        # 第一步：加载HA实体
        if not self.load_ha_entities():
            return {}

        # 第二步：匹配实体到设备
        matched_devices = self.match_entities_to_devices()
        self.logger.info(f"设备发现完成，共匹配 {len(matched_devices)} 个设备")
        return matched_devices
