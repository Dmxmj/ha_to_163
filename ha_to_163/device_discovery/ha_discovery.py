import requests
import re
import logging
from typing import List, Dict
from .base_discovery import BaseDiscovery

# 扩展属性映射（完全复用提供的代码）
PROPERTY_MAPPING = {
    # 基础映射
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
    
    # 带后缀的扩展映射
    "temperature_p": "temp",
    "temp_p": "temp",
    "humidity_p": "hum",
    "hum_p": "hum",
    "battery_p": "batt",
    "batt_p": "batt"
}


class BaseDiscovery:
    """设备发现基类"""
    
    def __init__(self, config, logger_name=None):
        self.config = config
        self.logger = logging.getLogger(logger_name or __name__)
        self.devices = []
    
    def discover(self):
        """执行设备发现，返回发现的设备列表"""
        raise NotImplementedError("子类必须实现discover方法")


class HADiscovery(BaseDiscovery):
    """基于HA实体的设备发现（复用提供的匹配逻辑）"""
    
    def __init__(self, config, ha_headers):
        super().__init__(config, "ha_discovery")
        self.ha_url = config.get("ha_url")
        self.ha_headers = ha_headers
        self.entities = []  # 存储HA中的实体列表
        self.sub_devices = [d for d in config.get("sub_devices", []) if d.get("enabled", True)]
    
    def load_ha_entities(self) -> bool:
        """从HA API加载实体列表（复用提供的代码逻辑）"""
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
                    self.logger.warning(f"获取HA实体尝试 {attempt+1}/{retry_attempts} 失败: {e}")
                    if attempt < retry_attempts - 1:
                        time.sleep(retry_delay)
            
            if not resp or resp.status_code != 200:
                self.logger.error(f"HA实体获取失败，状态码: {resp.status_code if resp else '无响应'}")
                return False
            
            self.entities = resp.json()
            self.logger.info(f"HA共返回 {len(self.entities)} 个实体")
            
            # 输出传感器实体列表（便于排查）
            sensor_entities = [e.get('entity_id') for e in self.entities if e.get('entity_id', '').startswith('sensor.')]
            self.logger.debug(f"HA中的传感器实体列表: {sensor_entities}")
            return True
        except Exception as e:
            self.logger.error(f"加载HA实体失败: {e}")
            return False
    
    def match_entities_to_devices(self) -> Dict:
        """将HA实体匹配到子设备（核心逻辑，复用提供的代码）"""
        matched_devices = {}
        
        for device in self.sub_devices:
            device_id = device["id"]
            matched_devices[device_id] = {
                "config": device,
                "sensors": {},  # 存储 {属性: 实体ID} 映射
                "last_data": None
            }
            self.logger.info(f"开始匹配设备: {device_id}（前缀: {device['ha_entity_prefix']}）")
        
        # 遍历HA实体进行匹配
        for entity in self.entities:
            entity_id = entity.get("entity_id", "")
            if not entity_id.startswith("sensor."):
                continue  # 只处理传感器实体
            
            # 提取实体属性（用于多维度匹配）
            attributes = entity.get("attributes", {})
            device_class = attributes.get("device_class", "").lower()
            friendly_name = attributes.get("friendly_name", "").lower()
            self.logger.debug(f"处理实体: {entity_id} (device_class: {device_class}, friendly_name: {friendly_name})")
            
            # 匹配到对应的子设备
            for device_id, device_data in matched_devices.items():
                device = device_data["config"]
                prefix = device["ha_entity_prefix"]
                
                # 宽松匹配：前缀包含在实体ID中（解决命名偏差）
                if prefix in entity_id:
                    # 提取实体类型（如"sensor.hz2_01_temperature" → "temperature"）
                    entity_type_parts = entity_id.replace(prefix, "").strip('_').split('_')
                    entity_type = '_'.join(entity_type_parts)
                    if not entity_type:
                        continue
                    
                    # 多维度匹配属性（复用提供的逻辑）
                    property_name = None
                    
                    # 方式1：通过device_class匹配（最可靠）
                    if device_class in PROPERTY_MAPPING:
                        property_name = PROPERTY_MAPPING[device_class]
                        self.logger.debug(f"通过device_class匹配: {device_class} → {property_name}")
                    
                    # 方式2：通过实体ID部分匹配
                    if not property_name:
                        for part in entity_type_parts:
                            if part in PROPERTY_MAPPING:
                                property_name = PROPERTY_MAPPING[part]
                                self.logger.debug(f"通过实体ID部分匹配: {part} → {property_name}")
                                break
                    
                    # 方式3：通过friendly_name匹配
                    if not property_name:
                        for key in PROPERTY_MAPPING:
                            if key in friendly_name:
                                property_name = PROPERTY_MAPPING[key]
                                self.logger.debug(f"通过friendly_name匹配: {key} → {property_name}")
                                break
                    
                    # 验证属性是否在设备支持列表中
                    if property_name and property_name in device["supported_properties"]:
                        device_data["sensors"][property_name] = entity_id
                        self.logger.info(f"匹配成功: {entity_id} → {property_name}（设备: {device_id}）")
                        break  # 已匹配到设备，跳出循环
        
        # 输出匹配结果
        for device_id, device_data in matched_devices.items():
            sensors = {k: v for k, v in device_data["sensors"].items()}
            self.logger.info(f"设备 {device_id} 匹配结果: {sensors}")
        
        return matched_devices
    
    def discover(self) -> Dict:
        """执行发现流程（主入口）"""
        self.logger.info("开始基于HA实体的设备发现...")
        
        # 第一步：加载HA实体
        if not self.load_ha_entities():
            return {}
        
        # 第二步：匹配实体到设备
        matched_devices = self.match_entities_to_devices()
        self.logger.info(f"设备发现完成，共匹配 {len(matched_devices)} 个设备")
        return matched_devices
