import requests
import re
import logging
import time
from typing import Dict
from .base_discovery import BaseDiscovery

# 增强属性映射（覆盖所有可能的关键词变体）
PROPERTY_MAPPING = {
    # 基础属性
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
    "state": "state",
    "on": "state",
    "off": "state",

    # 电气参数
    "current": "current",
    "electric_current": "current",
    "curr": "current",
    "electric_curr": "current",
    "voltage": "voltage",
    "vol": "voltage",
    "electric_vol": "voltage",
    "power": "power",
    "electric_power": "power",
    "active_power": "power",
    "energy": "energy",
    "power_consumption": "energy",
    "kwh": "energy",
    "consumption": "energy",
}


class HADiscovery(BaseDiscovery):
    def __init__(self, config, ha_headers):
        super().__init__(config, "ha_discovery")
        self.ha_url = config.get("ha_url")
        self.ha_headers = ha_headers
        self.entities = []
        self.sub_devices = [d for d in config.get("sub_devices", []) if d.get("enabled", True)]
        # 支持电气参数的设备类型
        self.electric_device_types = {"switch", "socket", "breaker"}
        self.environment_types = {"sensor"}

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
            
            # 日志：输出环境传感器候选实体
            hz2_entities = [
                e.get('entity_id') for e in self.entities 
                if e.get('entity_id', '').startswith('sensor.') and 
                ('hz2_01' in e.get('entity_id') or 'hz2_02' in e.get('entity_id'))
            ]
            self.logger.debug(f"环境传感器候选实体: {hz2_entities}")
            return True
        except Exception as e:
            self.logger.error(f"加载HA实体失败: {e}")
            return False

    def match_entities_to_devices(self) -> Dict:
        matched_devices = {}

        for device in self.sub_devices:
            device_id = device["id"]
            device_type = device["type"]
            # 修正环境传感器的前缀（去除"sensor."）
            ha_prefix = device["ha_entity_prefix"]
            if device_type in self.environment_types and ha_prefix.startswith("sensor."):
                cleaned_prefix = ha_prefix[len("sensor."):]  # 如"sensor.hz2_01_" → "hz2_01_"
                self.logger.debug(f"环境传感器 {device_id} 前缀修正: {ha_prefix} → {cleaned_prefix}")
            else:
                cleaned_prefix = ha_prefix
                
            matched_devices[device_id] = {
                "config": device,
                "entities": {},
                "cleaned_prefix": cleaned_prefix
            }
            self.logger.info(f"开始匹配{device_type}设备: {device_id}（核心前缀: {cleaned_prefix}）")

        # 处理所有实体
        for entity in self.entities:
            entity_id = entity.get("entity_id", "")
            if "." not in entity_id:
                continue
                
            entity_type = entity_id.split('.')[0]  # 实体类型（sensor/switch）
            entity_core = entity_id.split('.', 1)[1]  # 去掉类型前缀的核心部分
            attributes = entity.get("attributes", {})
            friendly_name = attributes.get("friendly_name", "").lower()
            device_class = attributes.get("device_class", "").lower()

            self.logger.debug(f"处理实体: {entity_id}（核心: {entity_core}，名称: {friendly_name}）")

            # 匹配环境传感器
            if entity_type == "sensor":
                self._match_environment_entity(entity_id, entity_core, device_class, friendly_name, matched_devices)
            
            # 匹配电气设备
            if entity_type in ("sensor", "switch"):
                self._match_electric_entity(entity_id, entity_type, entity_core, 
                                          device_class, friendly_name, matched_devices)

        # 输出最终匹配结果
        for device_id, device_data in matched_devices.items():
            device_type = device_data["config"]["type"]
            if device_type in self.environment_types:
                temp_status = "已匹配" if "temp" in device_data["entities"] else "未匹配"
                hum_status = "已匹配" if "hum" in device_data["entities"] else "未匹配"
                batt_status = "已匹配" if "batt" in device_data["entities"] else "未匹配"
                self.logger.info(
                    f"环境传感器 {device_id} 状态: "
                    f"temp={temp_status}, hum={hum_status}, batt={batt_status} → "
                    f"匹配实体: {device_data['entities']}"
                )
            else:
                active_power_status = "已匹配" if "power" in device_data["entities"] else "未匹配"
                energy_status = "已匹配" if "energy" in device_data["entities"] else "未匹配"
                self.logger.info(
                    f"电气设备 {device_id} 状态: "
                    f"active_power={active_power_status}, energy={energy_status} → "
                    f"匹配实体: {device_data['entities']}"
                )

        return matched_devices

    def _match_environment_entity(self, entity_id, entity_core, device_class, friendly_name, matched_devices):
        """匹配环境传感器实体"""
        for device_id, device_data in matched_devices.items():
            device = device_data["config"]
            if device["type"] not in self.environment_types:
                continue
                
            prefix = device_data["cleaned_prefix"]
            if prefix not in entity_core:
                continue

            # 提取实体类型部分
            entity_type_parts = entity_core.replace(prefix, "").strip('_').split('_')
            entity_type = '_'.join(entity_type_parts)
            if not entity_type:
                continue

            # 多维度匹配
            property_name = None

            # 1. device_class匹配（最高优先级）
            if device_class in PROPERTY_MAPPING:
                property_name = PROPERTY_MAPPING[device_class]
                self.logger.debug(f"环境传感器 {device_id}: device_class={device_class} → {property_name}")
            
            # 2. 实体ID部分匹配（支持带_p后缀）
            if not property_name:
                for part in entity_type_parts:
                    if part in PROPERTY_MAPPING:
                        property_name = PROPERTY_MAPPING[part]
                        self.logger.debug(f"环境传感器 {device_id}: 实体部分={part} → {property_name}")
                        break
                if not property_name and entity_type in PROPERTY_MAPPING:
                    property_name = PROPERTY_MAPPING[entity_type]
                    self.logger.debug(f"环境传感器 {device_id}: 实体完整={entity_type} → {property_name}")
            
            # 3. friendly_name匹配
            if not property_name:
                for key in PROPERTY_MAPPING:
                    if key in friendly_name:
                        property_name = PROPERTY_MAPPING[key]
                        self.logger.debug(f"环境传感器 {device_id}: 名称包含={key} → {property_name}")
                        break

            # 验证并添加匹配
            if property_name and property_name in device["supported_properties"]:
                if property_name not in device_data["entities"]:
                    device_data["entities"][property_name] = entity_id
                    self.logger.info(f"环境传感器匹配成功: {entity_id} → {property_name}（设备: {device_id}）")
                break

    def _match_electric_entity(self, entity_id, entity_type, entity_core, device_class, friendly_name, matched_devices):
        """匹配电气设备实体"""
        for device_id, device_data in matched_devices.items():
            device = device_data["config"]
            if device["type"] not in self.electric_device_types:
                continue
                
            prefix = device_data["cleaned_prefix"]
            if prefix not in entity_core:
                continue

            # 电气设备允许匹配switch和sensor实体
            if entity_type not in ("switch", "sensor"):
                continue

            # 清理实体后缀中的额外标识
            cleaned_suffix = re.sub(r'_p\d+(_\d+)?$', '', entity_core)
            cleaned_suffix = cleaned_suffix.replace(prefix, "").strip('_')
            self.logger.debug(f"电气设备实体清洗: {entity_core} → {cleaned_suffix}")

            # 匹配属性
            property_name = None

            # 1. 完整匹配
            if cleaned_suffix in PROPERTY_MAPPING:
                property_name = PROPERTY_MAPPING[cleaned_suffix]
                self.logger.debug(f"电气设备 {device_id}: 完整匹配 {cleaned_suffix} → {property_name}")

            # 2. 拆分匹配
            if not property_name:
                for part in cleaned_suffix.split('_'):
                    if part in PROPERTY_MAPPING:
                        property_name = PROPERTY_MAPPING[part]
                        self.logger.debug(f"电气设备 {device_id}: 拆分匹配 {part} → {property_name}")
                        break

            # 3. 名称匹配
            if not property_name:
                for key, prop in PROPERTY_MAPPING.items():
                    if key in friendly_name:
                        property_name = prop
                        self.logger.debug(f"电气设备 {device_id}: 名称匹配 {key} → {property_name}")
                        break

            # 验证并添加匹配
            if property_name and property_name in device["supported_properties"]:
                if property_name not in device_data["entities"]:
                    device_data["entities"][property_name] = entity_id
                    self.logger.info(f"电气设备匹配成功: {entity_id} → {property_name}（设备: {device_id}）")
                break

    def discover(self) -> Dict:
        self.logger.info("开始设备发现...")
        if not self.load_ha_entities():
            return {}
        matched_devices = self.match_entities_to_devices()
        self.logger.info(f"设备发现完成，共匹配 {len(matched_devices)} 个设备")
        return matched_devices
