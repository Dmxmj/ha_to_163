import requests
import re
import logging
import time
from typing import Dict
from .base_discovery import BaseDiscovery

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

    # 核心映射（严格区分）
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

            # 重点追踪目标实体：明确打印electric_power相关实体
            if "electric_power" in entity_core:
                self.logger.debug(f"发现electric_power实体: {entity_id}（原始核心: {entity_core}）")

            for device_id, device_data in matched_devices.items():
                device = device_data["config"]
                core_prefix = device["ha_entity_prefix"]
                if core_prefix not in entity_core:
                    continue

                # 电气设备类型检查（允许sensor/switch）
                if device["type"] in self.electric_device_types and entity_type not in ("switch", "sensor"):
                    continue
                if device["type"] not in self.electric_device_types and entity_type != device["type"]:
                    continue

                # 关键修复：增强后缀清洗，处理_p_3_2等格式
                # 匹配_p后接数字/下划线的任意组合（如_p3、_p_3、_p_3_2）
                cleaned_suffix = re.sub(r'_p[_\d]+$', '', entity_core)  # 核心修正：替换原有正则
                cleaned_suffix = cleaned_suffix.replace(core_prefix, "").strip('_')
                self.logger.debug(f"实体清洗: 原始={entity_core} → 清洗后={cleaned_suffix}（目标实体专项检查）")

                # 匹配逻辑：优先核心关键词
                property_name = None

                # 1. 强制优先匹配electric_power（针对日志中的问题实体）
                if "electric_power" in cleaned_suffix:
                    property_name = "active_power"
                    self.logger.debug(f"核心匹配成功: {cleaned_suffix} → active_power（实体: {entity_id}）")
                elif "power_consumption" in cleaned_suffix:
                    property_name = "energy"
                    self.logger.debug(f"核心匹配成功: {cleaned_suffix} → energy（实体: {entity_id}）")

                # 2. 常规完整匹配
                if not property_name and cleaned_suffix in PROPERTY_MAPPING:
                    property_name = PROPERTY_MAPPING[cleaned_suffix]
                    self.logger.debug(f"完整匹配: {cleaned_suffix} → {property_name}")

                # 3. 拆分匹配
                if not property_name:
                    for part in cleaned_suffix.split('_'):
                        if part in PROPERTY_MAPPING:
                            property_name = PROPERTY_MAPPING[part]
                            self.logger.debug(f"拆分匹配: {part} → {property_name}")
                            break

                # 4. 名称匹配
                if not property_name:
                    if "electric_power" in friendly_name:
                        property_name = "active_power"
                        self.logger.debug(f"名称匹配: {friendly_name} → active_power")
                    elif "power_consumption" in friendly_name:
                        property_name = "energy"
                        self.logger.debug(f"名称匹配: {friendly_name} → energy")

                # 验证匹配
                if property_name and property_name in device["supported_properties"]:
                    if property_name not in device_data["entities"]:
                        device_data["entities"][property_name] = entity_id
                        self.logger.info(f"匹配成功: {entity_id} → {property_name}（设备: {device_id}）")
                    break

        # 输出匹配结果，重点检查active_power
        for device_id, device_data in matched_devices.items():
            active_power_status = "已匹配" if "active_power" in device_data["entities"] else "未匹配"
            energy_status = "已匹配" if "energy" in device_data["entities"] else "未匹配"
            self.logger.info(
                f"设备 {device_id} 关键属性状态: "
                f"active_power={active_power_status}（目标实体: iot_cn_942988692_jdls1_electric_power_p_3_2）, "
                f"energy={energy_status} → 所有匹配: {device_data['entities']}"
            )

        return matched_devices

    def discover(self) -> Dict:
        self.logger.info("开始设备发现...")
        if not self.load_ha_entities():
            return {}
        matched_devices = self.match_entities_to_devices()
        self.logger.info(f"设备发现完成，共匹配 {len(matched_devices)} 个设备")
        return matched_devices
    
