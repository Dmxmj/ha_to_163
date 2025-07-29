import requests
import re
import logging
import time
from typing import Dict
from .base_discovery import BaseDiscovery

# 保留完整的环境传感器映射
PROPERTY_MAPPING = {
    # 环境传感器核心映射
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
    "temperature_p": "temp",
    "temp_p": "temp",
    "humidity_p": "hum",
    "hum_p": "hum",
    "battery_p": "batt",
    "batt_p": "batt",

    # 电气设备映射
    "state": "state",
    "on": "state",
    "off": "state",
    "electric_power": "active_power",
    "power": "active_power",
    "active_power": "active_power",
    "elec_power": "active_power",
    "power_consumption": "energy",
    "energy": "energy",
    "kwh": "energy",
    "electricity_used": "energy",
    "current": "current",
    "electric_current": "current",
    "voltage": "voltage",
    "frequency": "frequency"
}


class HADiscovery(BaseDiscovery):
    def __init__(self, config, ha_headers):
        super().__init__(config, "ha_discovery")
        self.ha_url = config.get("ha_url")
        self.ha_headers = ha_headers
        self.entities = []
        self.sub_devices = [d for d in config.get("sub_devices", []) if d.get("enabled", True)]
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
            
            # 重点日志：输出所有含"hz2_01"或"hz2_02"的传感器实体（环境传感器候选）
            hz2_entities = [
                e.get('entity_id') for e in self.entities 
                if e.get('entity_id', '').startswith('sensor.') and 
                ('hz2_01' in e.get('entity_id') or 'hz2_02' in e.get('entity_id'))
            ]
            self.logger.debug(f"环境传感器候选实体（含hz2_01/hz2_02）: {hz2_entities}")
            return True
        except Exception as e:
            self.logger.error(f"加载HA实体失败: {e}")
            return False

    def match_entities_to_devices(self) -> Dict:
        matched_devices = {}

        for device in self.sub_devices:
            device_id = device["id"]
            device_type = device["type"]
            # 修正：环境传感器的前缀去掉"sensor."（仅保留设备标识部分）
            ha_prefix = device["ha_entity_prefix"]
            if device_type in self.environment_types and ha_prefix.startswith("sensor."):
                cleaned_prefix = ha_prefix[len("sensor."):]  # 如"sensor.hz2_01_" → "hz2_01_"
                self.logger.debug(f"环境传感器 {device_id} 前缀修正: {ha_prefix} → {cleaned_prefix}")
            else:
                cleaned_prefix = ha_prefix
            matched_devices[device_id] = {
                "config": device,
                "entities": {},
                "cleaned_prefix": cleaned_prefix  # 存储修正后的前缀用于匹配
            }
            self.logger.info(f"开始匹配{device_type}设备: {device_id}（修正后前缀: {cleaned_prefix}）")

        for entity in self.entities:
            entity_id = entity.get("entity_id", "")
            attributes = entity.get("attributes", {})
            device_class = attributes.get("device_class", "").lower()
            friendly_name = attributes.get("friendly_name", "").lower()

            # 环境传感器处理
            if entity_id.startswith("sensor."):
                self._match_environment_sensor(entity_id, device_class, friendly_name, matched_devices)
            
            # 电气设备处理
            if entity_id.startswith(("sensor.", "switch.")):
                self._match_electric_device(entity_id, device_class, friendly_name, matched_devices)

        # 输出匹配结果
        for device_id, device_data in matched_devices.items():
            device_type = device_data["config"]["type"]
            if device_type in self.environment_types:
                temp_status = "已匹配" if "temp" in device_data["entities"] else "未匹配"
                hum_status = "已匹配" if "hum" in device_data["entities"] else "未匹配"
                batt_status = "已匹配" if "batt" in device_data["entities"] else "未匹配"
                self.logger.info(
                    f"环境传感器 {device_id} 状态: "
                    f"temp={temp_status}, hum={hum_status}, batt={batt_status} → "
                    f"匹配实体: {device_data['entities']}（使用前缀: {device_data['cleaned_prefix']}）"
                )
            else:
                active_power_status = "已匹配" if "active_power" in device_data["entities"] else "未匹配"
                energy_status = "已匹配" if "energy" in device_data["entities"] else "未匹配"
                self.logger.info(
                    f"电气设备 {device_id} 状态: "
                    f"active_power={active_power_status}, energy={energy_status} → "
                    f"匹配实体: {device_data['entities']}"
                )

        return matched_devices

    def _match_environment_sensor(self, entity_id, device_class, friendly_name, matched_devices):
        entity_core = entity_id.split('.', 1)[1]  # 如"sensor.hz2_01_temperature_p_2_1" → "hz2_01_temperature_p_2_1"
        self.logger.debug(f"处理环境传感器实体: {entity_id}（核心: {entity_core}, device_class: {device_class}）")

        for device_id, device_data in matched_devices.items():
            device = device_data["config"]
            if device["type"] not in self.environment_types:
                continue
            
            # 使用修正后的前缀（不含"sensor."）进行匹配
            prefix = device_data["cleaned_prefix"]
            if prefix not in entity_core:
                self.logger.debug(f"前缀不匹配: 实体核心={entity_core}，设备前缀={prefix}（跳过）")
                continue

            # 提取实体类型部分（如"hz2_01_temperature_p_2_1" → "temperature_p_2_1"）
            entity_type_parts = entity_core.replace(prefix, "").strip('_').split('_')
            entity_type = '_'.join(entity_type_parts)
            if not entity_type:
                continue

            # 多维度匹配（恢复老代码逻辑）
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

    def _match_electric_device(self, entity_id, device_class, friendly_name, matched_devices):
        # 保持电气设备匹配逻辑不变
        entity_type = entity_id.split('.')[0]
        entity_core = entity_id.split('.', 1)[1]
        self.logger.debug(f"处理电气设备实体: {entity_id}（类型: {entity_type}）")

        for device_id, device_data in matched_devices.items():
            device = device_data["config"]
            if device["type"] not in self.electric_device_types:
                continue
            
            prefix = device_data["cleaned_prefix"]
            if prefix not in entity_core:
                continue

            if entity_type not in ("sensor", "switch"):
                continue

            cleaned_suffix = re.sub(r'_p[_\d]+$', '', entity_core)
            cleaned_suffix = cleaned_suffix.replace(prefix, "").strip('_')
            self.logger.debug(f"电气设备实体清洗: {entity_core} → {cleaned_suffix}")

            property_name = None
            if "electric_power" in cleaned_suffix:
                property_name = "active_power"
            elif "power_consumption" in cleaned_suffix:
                property_name = "energy"
            elif cleaned_suffix in PROPERTY_MAPPING:
                property_name = PROPERTY_MAPPING[cleaned_suffix]
            else:
                for part in cleaned_suffix.split('_'):
                    if part in PROPERTY_MAPPING:
                        property_name = PROPERTY_MAPPING[part]
                        break

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
    
