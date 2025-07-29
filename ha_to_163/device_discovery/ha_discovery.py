import requests
import re
import logging
import time
from typing import Dict
from .base_discovery import BaseDiscovery

# 扩展属性映射：支持switch、socket、breaker的电气参数
PROPERTY_MAPPING = {
    # 基础环境传感器属性
    "temperature": "temp",
    "temp": "temp",
    "humidity": "hum",
    "hum": "hum",
    "battery": "batt",
    "batt": "batt",

    # 开关/断路器基础状态
    "state": "state",
    "on": "state",
    "off": "state",
    "trip": "state",  # 断路器跳闸状态

    # 电气参数（通用匹配）
    # 电压
    "voltage": "voltage",
    "vol": "voltage",
    "voltage_p": "voltage",
    # 电流
    "current": "current",
    "curr": "current",
    "electric_current": "current",
    "current_p": "current",
    "electric_current_p": "current",
    # 功率
    "power": "power",
    "electric_power": "power",
    "active_power": "power",
    "power_p": "power",
    "electric_power_p": "power",
    # 用电量
    "energy": "energy",
    "power_consumption": "energy",
    "electricity": "energy",
    "kwh": "energy",
    "energy_p": "energy",
    "power_consumption_p": "energy"
}


class HADiscovery(BaseDiscovery):
    """支持switch、socket、breaker类型设备的电气参数发现"""

    def __init__(self, config, ha_headers):
        super().__init__(config, "ha_discovery")
        self.ha_url = config.get("ha_url")
        self.ha_headers = ha_headers
        self.entities = []
        self.sub_devices = [d for d in config.get("sub_devices", []) if d.get("enabled", True)]
        # 定义支持电气参数的设备类型
        self.electric_device_types = {"switch", "socket", "breaker"}

    def load_ha_entities(self) -> bool:
        """加载HA所有实体（带重试机制）"""
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
        """匹配实体到设备（支持多设备类型的电气参数）"""
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
            self.logger.info(
                f"开始匹配{device_type}设备: {device_id}（核心前缀: {device['ha_entity_prefix']}, "
                f"支持属性: {device['supported_properties']}"
            )

        # 遍历HA实体进行匹配
        for entity in self.entities:
            entity_id = entity.get("entity_id", "")
            if "." not in entity_id:
                continue  # 跳过无效实体ID
            
            # 拆分实体类型和核心ID（如"sensor.iot_cn_942988692_voltage" → 类型:sensor，核心:iot_cn_942988692_voltage）
            entity_type, entity_core = entity_id.split('.', 1)
            attributes = entity.get("attributes", {})
            friendly_name = attributes.get("friendly_name", "").lower()
            self.logger.debug(
                f"处理实体: {entity_id}（类型: {entity_type}, 核心ID: {entity_core}, 名称: {friendly_name}）"
            )

            # 匹配对应的设备
            for device_id, device_data in matched_devices.items():
                device = device_data["config"]
                device_type = device["type"]
                core_prefix = device["ha_entity_prefix"]

                # 1. 核心前缀匹配（实体核心ID必须包含设备的核心前缀）
                if core_prefix not in entity_core:
                    continue

                # 2. 实体类型匹配规则
                # 电气设备（switch/socket/breaker）允许匹配switch和sensor实体
                # 其他设备（如sensor）严格匹配类型
                if device_type in self.electric_device_types:
                    if entity_type not in ("switch", "sensor"):
                        self.logger.debug(
                            f"跳过实体 {entity_id}: 电气设备仅支持switch/sensor类型，当前为{entity_type}"
                        )
                        continue
                else:
                    if entity_type != device_type:
                        self.logger.debug(
                            f"跳过实体 {entity_id}: 设备类型{device_type}需匹配{device_type}实体，当前为{entity_type}"
                        )
                        continue

                # 3. 提取属性关键词（核心ID去掉前缀后的部分）
                entity_suffix = entity_core.replace(core_prefix, "").strip('_')
                if not entity_suffix:
                    self.logger.debug(f"实体 {entity_id} 无有效后缀，跳过")
                    continue

                # 4. 多维度匹配属性
                property_name = self._match_property(entity_suffix, friendly_name)
                if not property_name:
                    self.logger.debug(f"实体 {entity_id} 未匹配到任何属性，跳过")
                    continue

                # 5. 验证属性是否在设备支持列表中
                if property_name in device["supported_properties"]:
                    # 避免重复匹配（保留第一个有效实体）
                    if property_name not in device_data["entities"]:
                        device_data["entities"][property_name] = entity_id
                        self.logger.info(
                            f"匹配成功: {entity_id} → {property_name}（设备: {device_id}, 类型: {device_type}）"
                        )
                    break  # 已匹配到设备，无需继续检查其他设备

        # 输出最终匹配结果
        for device_id, device_data in matched_devices.items():
            self.logger.info(
                f"设备 {device_id}（类型: {device_data['config']['type']}）最终匹配: "
                f"{device_data['entities']}"
            )

        return matched_devices

    def _match_property(self, entity_suffix: str, friendly_name: str) -> str:
        """多维度匹配属性（优先完整匹配，再拆分匹配）"""
        # 1. 完整后缀匹配（最高优先级）
        if entity_suffix in PROPERTY_MAPPING:
            return PROPERTY_MAPPING[entity_suffix]
        
        # 2. 拆分后缀匹配（支持下划线分割的关键词）
        for part in entity_suffix.split('_'):
            if part in PROPERTY_MAPPING:
                return PROPERTY_MAPPING[part]
        
        # 3. 名称关键词匹配（支持友好名称中的关键词）
        for key, prop in PROPERTY_MAPPING.items():
            if key in friendly_name:
                return prop
        
        return None

    def discover(self) -> Dict:
        """执行设备发现流程"""
        self.logger.info("开始设备发现（支持switch/socket/breaker电气参数）...")
        if not self.load_ha_entities():
            return {}
        matched_devices = self.match_entities_to_devices()
        self.logger.info(f"设备发现完成，共匹配 {len(matched_devices)} 个设备")
        return matched_devices
    
