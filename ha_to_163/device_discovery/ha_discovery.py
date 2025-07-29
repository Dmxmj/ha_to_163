import requests
import re
import logging
import time
from typing import Dict
from .base_discovery import BaseDiscovery

# 增强映射：覆盖更多power和energy的实体命名变体
PROPERTY_MAPPING = {
    # 基础属性（不变）
    "temperature": "temp",
    "temp": "temp",
    "humidity": "hum",
    "hum": "hum",
    "battery": "batt",
    "batt": "batt",
    "state": "state",
    "on": "state",
    "off": "state",

    # 电气参数（重点扩展power和energy的变体）
    # 电流：electric_current → current（不变）
    "current": "current",
    "electric_current": "current",
    "curr": "current",
    "electric_curr": "current",
    
    # 电功率：electric_power → active_power（扩展更多变体）
    "power": "active_power",           # 直接用power命名的实体
    "electric_power": "active_power",  # 标准命名
    "active_power": "active_power",    # 直接用目标字段命名
    "elec_power": "active_power",      # 缩写变体
    "electricity_power": "active_power", # 全称变体
    
    # 耗电量：power_consumption → energy（扩展更多变体）
    "energy": "energy",                # 直接用energy命名
    "power_consumption": "energy",     # 标准命名
    "kwh": "energy",                   # 千瓦时单位
    "consumption": "energy",           # 简化命名
    "electricity_used": "energy",      # 用电总量
    "total_energy": "energy",          # 总能耗
    
    # 电压：voltage → voltage（不变）
    "voltage": "voltage",
    "vol": "voltage",
    "electric_vol": "voltage",
    
    # 频率：frequency → frequency（不变）
    "frequency": "frequency",
    "freq": "frequency",
    "electric_freq": "frequency"
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
        # 保持原有逻辑不变
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
            entity_type = entity_id.split('.')[0]
            entity_core = entity_id.split('.', 1)[1]
            attributes = entity.get("attributes", {})
            friendly_name = attributes.get("friendly_name", "").lower()

            # 新增调试日志：打印所有含前缀的实体，方便排查
            for device in self.sub_devices:
                if device["ha_entity_prefix"] in entity_core:
                    self.logger.debug(f"发现含目标前缀的实体: {entity_id}（名称: {friendly_name}）")

            for device_id, device_data in matched_devices.items():
                device = device_data["config"]
                device_type = device["type"]
                core_prefix = device["ha_entity_prefix"]

                if core_prefix not in entity_core:
                    continue

                if device_type in self.electric_device_types:
                    if entity_type not in ("switch", "sensor"):
                        continue
                else:
                    if entity_type != device_type:
                        continue

                # 优化后缀清洗：处理更多变体（如_p3_4、_sensor等）
                cleaned_suffix = re.sub(r'_p\d+(_\d+)?$', '', entity_core)  # 去除_p3_4
                cleaned_suffix = re.sub(r'_sensor$', '', cleaned_suffix)    # 去除_sensor后缀
                cleaned_suffix = re.sub(r'_state$', '', cleaned_suffix)     # 去除_state后缀
                cleaned_suffix = cleaned_suffix.replace(core_prefix, "").strip('_')
                self.logger.debug(f"实体处理: 原始={entity_core} → 清洗后={cleaned_suffix}")

                # 增强匹配逻辑：优先全词匹配，再模糊匹配
                property_name = None

                # 1. 完整匹配（清洗后的后缀）
                if cleaned_suffix in PROPERTY_MAPPING:
                    property_name = PROPERTY_MAPPING[cleaned_suffix]
                    self.logger.debug(f"完整匹配: {cleaned_suffix} → {property_name}")

                # 2. 拆分匹配（支持中间关键词，如"power_total"拆出"power"）
                if not property_name:
                    for part in cleaned_suffix.split('_'):
                        if part in PROPERTY_MAPPING:
                            property_name = PROPERTY_MAPPING[part]
                            self.logger.debug(f"拆分匹配: {part} → {property_name}")
                            break

                # 3. 模糊匹配（关键词包含在后缀中，如"powerusage"包含"power"）
                if not property_name:
                    for key, prop in PROPERTY_MAPPING.items():
                        if key in cleaned_suffix:
                            property_name = prop
                            self.logger.debug(f"模糊匹配: {key} in {cleaned_suffix} → {property_name}")
                            break

                # 4. 名称匹配（友好名称中包含关键词）
                if not property_name:
                    for key, prop in PROPERTY_MAPPING.items():
                        if key in friendly_name:
                            property_name = prop
                            self.logger.debug(f"名称匹配: {key} in {friendly_name} → {property_name}")
                            break

                # 验证并添加匹配
                if property_name and property_name in device["supported_properties"]:
                    if property_name not in device_data["entities"]:
                        device_data["entities"][property_name] = entity_id
                        self.logger.info(f"匹配成功: {entity_id} → {property_name}（设备: {device_id}）")
                    break

        # 输出匹配结果，重点提示缺失的属性
        for device_id, device_data in matched_devices.items():
            device = device_data["config"]
            missing_props = [p for p in device["supported_properties"] if p not in device_data["entities"]]
            self.logger.info(
                f"设备 {device_id} 匹配结果: {device_data['entities']} → "
                f"缺失属性: {missing_props if missing_props else '无'}"
            )

        return matched_devices

    def discover(self) -> Dict:
        self.logger.info("开始设备发现...")
        if not self.load_ha_entities():
            return {}
        matched_devices = self.match_entities_to_devices()
        self.logger.info(f"设备发现完成，共匹配 {len(matched_devices)} 个设备")
        return matched_devices
    
