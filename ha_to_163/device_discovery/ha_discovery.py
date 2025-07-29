import requests
import re
import logging
import time
from typing import Dict
from .base_discovery import BaseDiscovery

PROPERTY_MAPPING = {
    # 环境传感器核心属性（确保保留）
    "temperature": "temp",
    "temp": "temp",
    "humidity": "hum",
    "hum": "hum",
    "battery": "batt",
    "batt": "batt",
    "battery_level": "batt",  # 新增：适配sensor.hz2_01_battery_level...实体
    
    # 基础开关属性
    "state": "state",
    "on": "state",
    "off": "state",

    # 电气设备属性
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
        self.environment_types = {"sensor"}  # 明确环境传感器类型

    def load_ha_entities(self) -> bool:
        # 保持原有逻辑
        try:
            self.logger.info(f"从HA获取实体列表: {self.ha_url}/api/states")
            resp = None
            retry_attempts = 5
            retry_delay = 3

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
            self.logger.info(f"开始匹配{device_type}设备: {device_id}（前缀: {device['ha_entity_prefix']}）")

        for entity in self.entities:
            entity_id = entity.get("entity_id", "")
            if "." not in entity_id:
                continue
            entity_type = entity_id.split('.')[0]
            entity_core = entity_id.split('.', 1)[1]
            friendly_name = entity.get("attributes", {}).get("friendly_name", "").lower()

            # 重点日志：追踪环境传感器实体（含temperature/humidity/battery）
            if any(key in entity_core for key in ["temperature", "humidity", "battery"]) and "hz2_" in entity_core:
                self.logger.debug(f"发现环境传感器实体: {entity_id}（原始核心: {entity_core}）")

            for device_id, device_data in matched_devices.items():
                device = device_data["config"]
                device_type = device["type"]
                core_prefix = device["ha_entity_prefix"]

                # 前缀匹配是基础（必须满足）
                if core_prefix not in entity_core:
                    continue

                # 设备类型匹配规则（区分环境传感器和电气设备）
                if device_type in self.environment_types:
                    # 环境传感器（sensor）仅匹配entity_type为sensor的实体
                    if entity_type != "sensor":
                        continue
                elif device_type in self.electric_device_types:
                    # 电气设备匹配sensor/switch实体
                    if entity_type not in ("sensor", "switch"):
                        continue
                else:
                    # 其他设备严格匹配类型
                    if entity_type != device_type:
                        continue

                # 关键修复：区分设备类型的后缀清洗规则
                if device_type in self.electric_device_types:
                    # 电气设备：处理_p_3_2等格式
                    cleaned_suffix = re.sub(r'_p[_\d]+$', '', entity_core)
                else:
                    # 环境传感器：仅去除_p后的数字（保留temperature等关键词）
                    cleaned_suffix = re.sub(r'_p\d+(_\d+)?$', '', entity_core)  # 恢复原规则，避免过度清洗

                cleaned_suffix = cleaned_suffix.replace(core_prefix, "").strip('_')
                self.logger.debug(f"实体清洗（{device_type}）: 原始={entity_core} → 清洗后={cleaned_suffix}")

                # 匹配逻辑：优先环境传感器属性（避免被电气规则覆盖）
                property_name = None

                # 1. 优先匹配环境传感器核心属性
                if device_type in self.environment_types:
                    if "temperature" in cleaned_suffix:
                        property_name = "temp"
                        self.logger.debug(f"环境传感器匹配: temperature → temp（实体: {entity_id}）")
                    elif "humidity" in cleaned_suffix:
                        property_name = "hum"
                        self.logger.debug(f"环境传感器匹配: humidity → hum（实体: {entity_id}）")
                    elif "battery" in cleaned_suffix:
                        property_name = "batt"
                        self.logger.debug(f"环境传感器匹配: battery → batt（实体: {entity_id}）")

                # 2. 电气设备核心匹配
                if not property_name and device_type in self.electric_device_types:
                    if "electric_power" in cleaned_suffix:
                        property_name = "active_power"
                        self.logger.debug(f"电气设备匹配: electric_power → active_power（实体: {entity_id}）")
                    elif "power_consumption" in cleaned_suffix:
                        property_name = "energy"
                        self.logger.debug(f"电气设备匹配: power_consumption → energy（实体: {entity_id}）")

                # 3. 常规完整匹配（适用于所有设备）
                if not property_name and cleaned_suffix in PROPERTY_MAPPING:
                    property_name = PROPERTY_MAPPING[cleaned_suffix]
                    self.logger.debug(f"完整匹配: {cleaned_suffix} → {property_name}")

                # 4. 拆分匹配
                if not property_name:
                    for part in cleaned_suffix.split('_'):
                        if part in PROPERTY_MAPPING:
                            property_name = PROPERTY_MAPPING[part]
                            self.logger.debug(f"拆分匹配: {part} → {property_name}")
                            break

                # 5. 名称匹配
                if not property_name:
                    for key, prop in PROPERTY_MAPPING.items():
                        if key in friendly_name:
                            property_name = prop
                            self.logger.debug(f"名称匹配: {key} → {property_name}")
                            break

                # 验证匹配
                if property_name and property_name in device["supported_properties"]:
                    if property_name not in device_data["entities"]:
                        device_data["entities"][property_name] = entity_id
                        self.logger.info(f"匹配成功: {entity_id} → {property_name}（设备: {device_id}）")
                    break

        # 输出匹配结果，区分环境传感器和电气设备
        for device_id, device_data in matched_devices.items():
            device_type = device_data["config"]["type"]
            if device_type in self.environment_types:
                # 环境传感器重点检查temp/hum/batt
                temp_status = "已匹配" if "temp" in device_data["entities"] else "未匹配"
                hum_status = "已匹配" if "hum" in device_data["entities"] else "未匹配"
                batt_status = "已匹配" if "batt" in device_data["entities"] else "未匹配"
                self.logger.info(
                    f"环境传感器 {device_id} 状态: "
                    f"temp={temp_status}, hum={hum_status}, batt={batt_status} → "
                    f"所有匹配: {device_data['entities']}"
                )
            else:
                # 电气设备检查active_power/energy
                active_power_status = "已匹配" if "active_power" in device_data["entities"] else "未匹配"
                energy_status = "已匹配" if "energy" in device_data["entities"] else "未匹配"
                self.logger.info(
                    f"电气设备 {device_id} 状态: "
                    f"active_power={active_power_status}, energy={energy_status} → "
                    f"所有匹配: {device_data['entities']}"
                )

        return matched_devices

    def discover(self) -> Dict:
        self.logger.info("开始设备发现...")
        if not self.load_ha_entities():
            return {}
        matched_devices = self.match_entities_to_devices()
        self.logger.info(f"设备发现完成，共匹配 {len(matched_devices)} 个设备")
        return matched_devices
    
