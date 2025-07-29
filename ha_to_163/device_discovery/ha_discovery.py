import requests
import re
import logging
import time
from typing import Dict
from .base_discovery import BaseDiscovery

# 电气参数映射（覆盖electric_power、electric_current等关键词）
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

    # 电气参数（重点适配用户实体命名）
    "voltage": "voltage",  # 电压
    "electric_current": "current",  # 电流（适配electric_current）
    "current": "current",  # 兼容直接current命名
    "electric_power": "power",  # 电功率（适配electric_power）
    "power": "power",  # 兼容直接power命名
    "power_consumption": "energy",  # 耗电量（适配power_consumption）
    "energy": "energy",  # 兼容直接energy命名
}


class HADiscovery(BaseDiscovery):
    """适配电气参数匹配的设备发现类"""

    def __init__(self, config, ha_headers):
        super().__init__(config, "ha_discovery")
        self.ha_url = config.get("ha_url")
        self.ha_headers = ha_headers
        self.entities = []  # 存储HA实体
        self.sub_devices = [d for d in config.get("sub_devices", []) if d.get("enabled", True)]

    def load_ha_entities(self) -> bool:
        """加载HA所有实体（包含sensor/switch等）"""
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
            return True
        except Exception as e:
            self.logger.error(f"加载HA实体失败: {e}")
            return False

    def match_entities_to_devices(self) -> Dict:
        """匹配实体到设备（核心逻辑：忽略实体类型前缀，仅匹配核心前缀）"""
        matched_devices = {}

        # 初始化设备容器
        for device in self.sub_devices:
            device_id = device["id"]
            device_type = device["type"]
            matched_devices[device_id] = {
                "config": device,
                "entities": {},  # {属性: 实体ID}
                "last_data": None
            }
            self.logger.info(f"开始匹配{device_type}设备: {device_id}（核心前缀: {device['ha_entity_prefix']}）")

        # 遍历HA实体匹配
        for entity in self.entities:
            entity_id = entity.get("entity_id", "")
            if "." not in entity_id:
                continue  # 无效实体ID
            # 提取实体ID的核心部分（去掉"switch."/"sensor."等类型前缀）
            entity_core = entity_id.split('.', 1)[1]  # 如"switch.iot_cn_942988692_jdls1_on" → "iot_cn_942988692_jdls1_on"
            entity_type = entity_id.split('.')[0]  # 实体类型（switch/sensor）

            # 设备属性
            attributes = entity.get("attributes", {})
            friendly_name = attributes.get("friendly_name", "").lower()
            self.logger.debug(f"处理实体: {entity_id}（核心部分: {entity_core}，名称: {friendly_name}）")

            # 匹配对应设备
            for device_id, device_data in matched_devices.items():
                device = device_data["config"]
                device_type = device["type"]
                core_prefix = device["ha_entity_prefix"]  # 设备的核心前缀（如"iot_cn_942988692_"）

                # 核心匹配条件：实体核心部分包含设备的核心前缀
                if core_prefix not in entity_core:
                    continue

                # 允许socket类型匹配switch（状态）和sensor（电气参数）实体
                if device_type == "socket" and entity_type not in ("switch", "sensor"):
                    continue
                # 其他设备（如sensor/switch）严格匹配实体类型
                if device_type != "socket" and entity_type != device_type:
                    continue

                # 提取实体核心部分的属性关键词（如"iot_cn_942988692_electric_power" → "electric_power"）
                entity_suffix = entity_core.replace(core_prefix, "").strip('_')
                if not entity_suffix:
                    continue

                # 匹配属性（优先完整匹配，再拆分匹配）
                property_name = None
                # 1. 完整后缀匹配（如"electric_power"）
                if entity_suffix in PROPERTY_MAPPING:
                    property_name = PROPERTY_MAPPING[entity_suffix]
                    self.logger.debug(f"完整后缀匹配: {entity_suffix} → {property_name}")
                # 2. 拆分后缀匹配（如"electric_current"拆分为"electric"和"current"）
                if not property_name:
                    for part in entity_suffix.split('_'):
                        if part in PROPERTY_MAPPING:
                            property_name = PROPERTY_MAPPING[part]
                            self.logger.debug(f"拆分部分匹配: {part} → {property_name}")
                            break
                # 3. 名称匹配（如名称含"电压"对应"voltage"）
                if not property_name:
                    for key, prop in PROPERTY_MAPPING.items():
                        if key in friendly_name:
                            property_name = prop
                            self.logger.debug(f"名称匹配: {key} → {property_name}")
                            break

                # 验证属性是否在设备支持列表中
                if property_name and property_name in device["supported_properties"]:
                    if property_name not in device_data["entities"]:
                        device_data["entities"][property_name] = entity_id
                        self.logger.info(f"匹配成功: {entity_id} → {property_name}（设备: {device_id}）")
                    break  # 已匹配到设备，跳出循环

        # 输出匹配结果
        for device_id, device_data in matched_devices.items():
            self.logger.info(f"设备 {device_id} 最终匹配实体: {device_data['entities']}")

        return matched_devices

    def discover(self) -> Dict:
        """执行发现流程"""
        self.logger.info("开始设备发现...")
        if not self.load_ha_entities():
            return {}
        matched_devices = self.match_entities_to_devices()
        self.logger.info(f"设备发现完成，共匹配 {len(matched_devices)} 个设备")
        return matched_devices
