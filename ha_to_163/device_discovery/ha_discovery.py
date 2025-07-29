import requests
import re
import logging
import time
from typing import Dict
from .base_discovery import BaseDiscovery

# 扩展属性映射（新增电气参数）
PROPERTY_MAPPING = {
    # 基础传感器属性
    "temperature": "temp",
    "temp": "temp",
    "humidity": "hum",
    "hum": "hum",
    "battery": "batt",
    "batt": "batt",

    # 开关/插座基础属性
    "state": "state",
    "on": "state",
    "off": "state",

    # 电气参数（新增）
    "voltage": "voltage",       # 电压
    "vol": "voltage",           # 电压简写
    "current": "current",       # 电流
    "curr": "current",          # 电流简写
    "power": "power",           # 功率（即时功率）
    "active_power": "power",    # 有功功率
    "energy": "energy",         # 用电量（累计）
    "electricity": "energy",    # 电量简写
    "kwh": "energy",            # 千瓦时（用电量单位）

    # 带后缀的扩展映射
    "voltage_p": "voltage",
    "current_p": "current",
    "power_p": "power",
    "energy_p": "energy",
    "temp_p": "temp",
    "hum_p": "hum",
    "batt_p": "batt"
}


class HADiscovery(BaseDiscovery):
    """支持电气参数匹配的设备发现逻辑"""

    def __init__(self, config, ha_headers):
        super().__init__(config, "ha_discovery")
        self.ha_url = config.get("ha_url")
        self.ha_headers = ha_headers
        self.entities = []  # 存储HA中的实体列表
        self.sub_devices = [d for d in config.get("sub_devices", []) if d.get("enabled", True)]

    def load_ha_entities(self) -> bool:
        """从HA API加载所有实体（不变）"""
        try:
            self.logger.info(f"从HA获取实体列表: {self.ha_url}/api/states")
            resp = None
            retry_attempts = self.config.get("retry_attempts", 5)
            retry_delay = self.config.get("retry_delay", 3)

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
        """匹配实体到设备（优化电气参数匹配，支持跨类型实体）"""
        matched_devices = {}

        # 初始化设备匹配容器
        for device in self.sub_devices:
            device_id = device["id"]
            device_type = device["type"]
            matched_devices[device_id] = {
                "config": device,
                "entities": {},  # {属性: 实体ID}
                "last_data": None
            }
            self.logger.info(f"开始匹配{device_type}设备: {device_id}（前缀: {device['ha_entity_prefix']}）")

        # 遍历HA实体进行匹配
        for entity in self.entities:
            entity_id = entity.get("entity_id", "")
            if "." not in entity_id:
                continue  # 无效实体ID跳过
            entity_type = entity_id.split('.')[0]  # 实体类型（sensor/switch等）

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
                device_type = device["type"]
                prefix = device["ha_entity_prefix"]

                # 核心优化：允许插座（socket）匹配sensor类型的电气参数实体
                # 规则：插座设备可匹配switch（状态）和sensor（电气参数）类型实体
                if not (
                    (device_type == "socket" and entity_type in ("switch", "sensor")) or  # 插座特殊处理
                    (device_type == entity_type)  # 其他设备（如switch/breaker）严格匹配类型
                ):
                    continue

                # 前缀匹配（实体ID包含设备的ha_entity_prefix）
                if prefix in entity_id:
                    # 提取实体属性部分（如"sensor.iot_cn_942988692_voltage" → "voltage"）
                    entity_type_parts = entity_id.replace(prefix, "").strip('_').split('_')
                    entity_prop = '_'.join(entity_type_parts)
                    if not entity_prop:
                        continue

                    # 多维度匹配属性（优先device_class，再关键词）
                    property_name = None

                    # 1. 通过device_class匹配（HA标准设备类）
                    if device_class in PROPERTY_MAPPING:
                        property_name = PROPERTY_MAPPING[device_class]
                        self.logger.debug(f"通过device_class匹配: {device_class} → {property_name}")

                    # 2. 通过实体ID部分匹配（关键词）
                    if not property_name:
                        for part in entity_type_parts:
                            if part in PROPERTY_MAPPING:
                                property_name = PROPERTY_MAPPING[part]
                                self.logger.debug(f"通过实体ID部分匹配: {part} → {property_name}")
                                break

                    # 3. 通过friendly_name匹配（名称包含关键词）
                    if not property_name:
                        for key in PROPERTY_MAPPING:
                            if key in friendly_name:
                                property_name = PROPERTY_MAPPING[key]
                                self.logger.debug(f"通过friendly_name匹配: {key} → {property_name}")
                                break

                    # 验证属性是否在设备支持列表中
                    if property_name and property_name in device["supported_properties"]:
                        # 避免重复匹配（同一属性保留第一个匹配的实体）
                        if property_name not in device_data["entities"]:
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
        self.logger.info("开始基于HA实体的设备发现...")

        if not self.load_ha_entities():
            return {}

        matched_devices = self.match_entities_to_devices()
        self.logger.info(f"设备发现完成，共匹配 {len(matched_devices)} 个设备")
        return matched_devices
    
