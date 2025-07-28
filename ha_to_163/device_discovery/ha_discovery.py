import requests
import re
import logging
import time
from typing import Dict
from .base_discovery import BaseDiscovery

# 扩展属性映射（支持多设备类型）
PROPERTY_MAPPING = {
    # 传感器属性
    "temperature": "temp",
    "temp": "temp",
    "humidity": "hum",
    "hum": "hum",
    "battery": "batt",
    "batt": "batt",

    # 开关/插座属性
    "state": "state",
    "power": "power",
    "current": "current",
    "on": "state",
    "off": "state",

    # 带后缀的扩展映射
    "temperature_p": "temp",
    "humidity_p": "hum",
    "battery_p": "batt",
    "power_p": "power"
}


class HADiscovery(BaseDiscovery):
    """基于HA实体的多类型设备发现"""

    def __init__(self, config, ha_headers):
        super().__init__(config, "ha_discovery")
        self.ha_url = config.get("ha_url")
        self.ha_headers = ha_headers
        self.entities = []  # 存储HA中的实体列表
        self.sub_devices = [d for d in config.get("sub_devices", []) if d.get("enabled", True)]

    def load_ha_entities(self) -> bool:
        """从HA API加载所有实体（支持sensor/switch等）"""
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

            # 按类型输出实体列表（便于调试）
            entity_types = {}
            for e in self.entities:
                entity_id = e.get('entity_id', '')
                prefix = entity_id.split('.')[0] if '.' in entity_id else 'unknown'
                if prefix not in entity_types:
                    entity_types[prefix] = []
                entity_types[prefix].append(entity_id)
            for typ, ids in entity_types.items():
                self.logger.debug(f"HA中{typ}类型实体: {ids}")
            return True
        except Exception as e:
            self.logger.error(f"加载HA实体失败: {e}")
            return False

    def match_entities_to_devices(self) -> Dict:
        """将HA实体匹配到子设备（按设备类型过滤）"""
        matched_devices = {}

        # 初始化设备匹配容器
        for device in self.sub_devices:
            device_id = device["id"]
            device_type = device["type"]
            matched_devices[device_id] = {
                "config": device,
                "entities": {},  # 存储 {属性: 实体ID} 映射（兼容多类型）
                "last_data": None
            }
            self.logger.info(f"开始匹配{device_type}设备: {device_id}（前缀: {device['ha_entity_prefix']}）")

        # 遍历HA实体进行匹配
        for entity in self.entities:
            entity_id = entity.get("entity_id", "")
            if "." not in entity_id:
                continue  # 无效实体ID跳过
            entity_type = entity_id.split('.')[0]  # 提取实体类型（sensor/switch等）

            # 提取实体属性
            attributes = entity.get("attributes", {})
            device_class = attributes.get("device_class", "").lower()
            friendly_name = attributes.get("friendly_name", "").lower()
            self.logger.debug(
                f"处理实体: {entity_id} (类型: {entity_type}, device_class: {device_class}, 名称: {friendly_name})"
            )

            # 匹配到对应的子设备
            for device_id, device_data in matched_devices.items():
                device = device_data["config"]
                # 设备类型需与实体类型匹配（如switch设备匹配switch实体）
                if device["type"] != entity_type:
                    continue
                # 前缀匹配
                prefix = device["ha_entity_prefix"]
                if prefix in entity_id:
                    # 提取实体属性部分（如"switch.living_room_power" → "power"）
                    entity_type_parts = entity_id.replace(prefix, "").strip('_').split('_')
                    entity_prop = '_'.join(entity_type_parts)
                    if not entity_prop:
                        continue

                    # 多维度匹配属性
                    property_name = None

                    # 1. 通过device_class匹配
                    if device_class in PROPERTY_MAPPING:
                        property_name = PROPERTY_MAPPING[device_class]
                        self.logger.debug(f"通过device_class匹配: {device_class} → {property_name}")

                    # 2. 通过实体ID部分匹配
                    if not property_name:
                        for part in entity_type_parts:
                            if part in PROPERTY_MAPPING:
                                property_name = PROPERTY_MAPPING[part]
                                self.logger.debug(f"通过实体ID部分匹配: {part} → {property_name}")
                                break

                    # 3. 通过friendly_name匹配
                    if not property_name:
                        for key in PROPERTY_MAPPING:
                            if key in friendly_name:
                                property_name = PROPERTY_MAPPING[key]
                                self.logger.debug(f"通过friendly_name匹配: {key} → {property_name}")
                                break

                    # 验证属性是否在设备支持列表中
                    if property_name and property_name in device["supported_properties"]:
                        device_data["entities"][property_name] = entity_id
                        self.logger.info(f"匹配成功: {entity_id} → {property_name}（设备: {device_id}）")
                        break  # 已匹配到设备，跳出循环

        # 输出匹配结果
        for device_id, device_data in matched_devices.items():
            entities = {k: v for k, v in device_data["entities"].items()}
            self.logger.info(f"设备 {device_id} 匹配结果: {entities}")

        return matched_devices

    def discover(self) -> Dict:
        """执行发现流程（主入口）"""
        self.logger.info("开始基于HA实体的多类型设备发现...")

        # 第一步：加载HA实体
        if not self.load_ha_entities():
            return {}

        # 第二步：匹配实体到设备
        matched_devices = self.match_entities_to_devices()
        self.logger.info(f"设备发现完成，共匹配 {len(matched_devices)} 个设备")
        return matched_devices
