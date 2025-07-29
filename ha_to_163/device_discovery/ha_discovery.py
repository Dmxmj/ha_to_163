import requests
import re
import logging
import time
from typing import Dict
from .base_discovery import BaseDiscovery

# 调整后的属性映射关系
PROPERTY_MAPPING = {
    # 基础属性
    "temperature": "temp",
    "temp": "temp",
    "humidity": "hum",
    "hum": "hum",
    "battery": "batt",
    "batt": "batt",
    "state": "state",
    "on": "state",
    "off": "state",

    # 电气参数（按需求调整）
    # 电流：electric_current → current
    "current": "current",
    "electric_current": "current",
    "curr": "current",
    "electric_curr": "current",
    
    # 电功率：electric_power → active_power
    "power": "active_power",
    "electric_power": "active_power",
    "active_power": "active_power",
    
    # 耗电量：power_consumption → energy
    "energy": "energy",
    "power_consumption": "energy",
    "kwh": "energy",
    "consumption": "energy",
    
    # 电压：voltage → voltage
    "voltage": "voltage",
    "vol": "voltage",
    "electric_vol": "voltage",
    
    # 新增频率：frequency → frequency
    "frequency": "frequency",
    "freq": "frequency",
    "electric_freq": "frequency"
}


class HADiscovery(BaseDiscovery):
    """调整实体映射关系，支持频率发现"""

    def __init__(self, config, ha_headers):
        super().__init__(config, "ha_discovery")
        self.ha_url = config.get("ha_url")
        self.ha_headers = ha_headers
        self.entities = []
        self.sub_devices = [d for d in config.get("sub_devices", []) if d.get("enabled", True)]
        # 支持电气参数的设备类型
        self.electric_device_types = {"switch", "socket", "breaker"}

    def load_ha_entities(self) -> bool:
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
        matched_devices = {}

        for device in self.sub_devices:
            device_id = device["id"]
            device_type = device["type"]
            matched_devices[device_id] = {
                "config": device,
                "entities": {},
                "last_data": None
            }
            self.logger.info(f"开始匹配{device_type}设备: {device_id}（核心前缀: {device['ha_entity_prefix']}）")

        for entity in self.entities:
            entity_id = entity.get("entity_id", "")
            if "." not in entity_id:
                continue
            entity_type = entity_id.split('.')[0]  # 实体类型（sensor/switch）
            entity_core = entity_id.split('.', 1)[1]  # 去掉类型前缀的核心部分
            attributes = entity.get("attributes", {})
            friendly_name = attributes.get("friendly_name", "").lower()

            self.logger.debug(f"处理实体: {entity_id}（核心: {entity_core}，名称: {friendly_name}）")

            for device_id, device_data in matched_devices.items():
                device = device_data["config"]
                device_type = device["type"]
                core_prefix = device["ha_entity_prefix"]

                # 核心前缀匹配
                if core_prefix not in entity_core:
                    continue

                # 电气设备允许匹配switch和sensor实体
                if device_type in self.electric_device_types:
                    if entity_type not in ("switch", "sensor"):
                        continue
                # 非电气设备严格匹配类型
                else:
                    if entity_type != device_type:
                        continue

                # 移除实体后缀中的额外标识（如_p_2_1、_p3等）
                cleaned_suffix = re.sub(r'_p\d+(_\d+)?$', '', entity_core)  # 去除_p_2_1等后缀
                cleaned_suffix = cleaned_suffix.replace(core_prefix, "").strip('_')
                self.logger.debug(f"实体核心处理后: {cleaned_suffix}（原始: {entity_core}）")

                # 三级匹配逻辑
                property_name = None

                # 1. 完整匹配（优先处理cleaned_suffix）
                if cleaned_suffix in PROPERTY_MAPPING:
                    property_name = PROPERTY_MAPPING[cleaned_suffix]
                    self.logger.debug(f"完整匹配: {cleaned_suffix} → {property_name}")

                # 2. 拆分匹配（处理多词组合）
                if not property_name:
                    for part in cleaned_suffix.split('_'):
                        if part in PROPERTY_MAPPING:
                            property_name = PROPERTY_MAPPING[part]
                            self.logger.debug(f"拆分匹配: {part} → {property_name}")
                            break

                # 3. 名称匹配（友好名称含关键词）
                if not property_name:
                    for key, prop in PROPERTY_MAPPING.items():
                        if key in friendly_name:
                            property_name = prop
                            self.logger.debug(f"名称匹配: {key} → {property_name}")
                            break

                # 验证并添加匹配
                if property_name and property_name in device["supported_properties"]:
                    if property_name not in device_data["entities"]:
                        device_data["entities"][property_name] = entity_id
                        self.logger.info(f"匹配成功: {entity_id} → {property_name}（设备: {device_id}）")
                    break

        # 输出最终匹配结果
        for device_id, device_data in matched_devices.items():
            self.logger.info(f"设备 {device_id} 匹配结果: {device_data['entities']}")

        return matched_devices

    def discover(self) -> Dict:
        self.logger.info("开始设备发现...")
        if not self.load_ha_entities():
            return {}
        matched_devices = self.match_entities_to_devices()
        self.logger.info(f"设备发现完成，共匹配 {len(matched_devices)} 个设备")
        return matched_devices
    
