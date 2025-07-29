import requests
import re
import logging
import time
from typing import Dict
from .base_discovery import BaseDiscovery

# 重点更新：适配实体实际命名规则（electric_power/electric_current等）
PROPERTY_MAPPING = {
    # 基础属性（保持不变）
    "temperature": "temp",
    "temp": "temp",
    "humidity": "hum",
    "hum": "hum",
    "battery": "batt",
    "batt": "batt",
    "state": "state",
    "on": "state",
    "off": "state",

    # 电气参数（重点扩展）
    # 电压（直接匹配）
    "voltage": "voltage",
    # 电流（支持electric_current）
    "current": "current",
    "electric_current": "current",  # 新增：匹配electric_current
    "curr": "current",
    # 功率（支持electric_power）
    "power": "power",
    "electric_power": "power",  # 新增：匹配electric_power
    "active_power": "power",
    # 耗电量（支持power_consumption）
    "energy": "energy",
    "power_consumption": "energy",  # 新增：匹配power_consumption
    "electricity": "energy",
    "kwh": "energy",

    # 带前缀/后缀的扩展匹配
    "voltage_p": "voltage",
    "electric_current_p": "current",  # 新增
    "electric_power_p": "power",      # 新增
    "power_consumption_p": "energy"   # 新增
}


class HADiscovery(BaseDiscovery):
    """适配电气参数实体命名的设备发现逻辑"""

    def __init__(self, config, ha_headers):
        super().__init__(config, "ha_discovery")
        self.ha_url = config.get("ha_url")
        self.ha_headers = ha_headers
        self.entities = []
        self.sub_devices = [d for d in config.get("sub_devices", []) if d.get("enabled", True)]

    def load_ha_entities(self) -> bool:
        """加载HA实体（不变）"""
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
            return True
        except Exception as e:
            self.logger.error(f"加载HA实体失败: {e}")
            return False

    def match_entities_to_devices(self) -> Dict:
        """匹配实体到设备（优化关键词提取逻辑）"""
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
                continue
            entity_type = entity_id.split('.')[0]

            attributes = entity.get("attributes", {})
            friendly_name = attributes.get("friendly_name", "").lower()
            self.logger.debug(f"处理实体: {entity_id} (名称: {friendly_name})")

            # 匹配对应的子设备
            for device_id, device_data in matched_devices.items():
                device = device_data["config"]
                device_type = device["type"]
                prefix = device["ha_entity_prefix"]

                # 插座支持匹配switch（状态）和sensor（电气参数）
                if not (
                    (device_type == "socket" and entity_type in ("switch", "sensor")) or
                    (device_type == entity_type)
                ):
                    continue

                # 前缀匹配
                if prefix in entity_id:
                    # 提取实体ID中的关键词部分（如"sensor.prefix_electric_power" → "electric_power"）
                    entity_suffix = entity_id.replace(prefix, "").strip('_')
                    # 重点优化：保留完整后缀（不拆分），避免"electric_power"被拆分为"electric"和"power"
                    self.logger.debug(f"实体后缀分析: {entity_suffix}")

                    # 匹配属性（优先完整匹配，再拆分匹配）
                    property_name = None

                    # 1. 完整后缀匹配（优先处理electric_power等完整关键词）
                    if entity_suffix in PROPERTY_MAPPING:
                        property_name = PROPERTY_MAPPING[entity_suffix]
                        self.logger.debug(f"完整后缀匹配: {entity_suffix} → {property_name}")

                    # 2. 拆分后缀匹配（如果完整匹配失败）
                    if not property_name:
                        for part in entity_suffix.split('_'):
                            if part in PROPERTY_MAPPING:
                                property_name = PROPERTY_MAPPING[part]
                                self.logger.debug(f"拆分部分匹配: {part} → {property_name}")
                                break

                    # 3. 名称匹配（friendly_name包含关键词）
                    if not property_name:
                        for key in PROPERTY_MAPPING:
                            if key in friendly_name:
                                property_name = PROPERTY_MAPPING[key]
                                self.logger.debug(f"名称匹配: {key} → {property_name}")
                                break

                    # 验证属性是否在设备支持列表中
                    if property_name and property_name in device["supported_properties"]:
                        if property_name not in device_data["entities"]:
                            device_data["entities"][property_name] = entity_id
                            self.logger.info(f"匹配成功: {entity_id} → {property_name}（设备: {device_id}）")
                        break

        # 输出匹配结果
        for device_id, device_data in matched_devices.items():
            self.logger.info(f"设备 {device_id} 最终匹配: {device_data['entities']}")

        return matched_devices

    def discover(self) -> Dict:
        """执行发现流程"""
        self.logger.info("开始基于HA实体的设备发现...")
        if not self.load_ha_entities():
            return {}
        matched_devices = self.match_entities_to_devices()
        self.logger.info(f"设备发现完成，共匹配 {len(matched_devices)} 个设备")
        return matched_devices
    
