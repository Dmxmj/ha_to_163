import logging
import requests
import time
from typing import Dict, Any


class DataCollector:
    """数据收集器，支持带转换系数的参数处理"""
    
    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.logger = logging.getLogger("data_collector")
        self.ha_headers = {
            "Authorization": f"Bearer {self.config['ha_token']}",
            "Content-Type": "application/json"
        }
    
    def collect_device_data(self, device: Dict[str, Any], matched_entities: Dict[str, str]) -> Dict[str, Any]:
        """收集设备数据并应用转换系数"""
        collected_data = {}
        device_id = device["id"]
        device_type = device["type"]
        # 获取设备配置的转换系数（默认1.0）
        conversion_factors = device.get("conversion_factors", {})
        
        self.logger.info(f"\n收集设备 {device_id} 数据（应用转换系数）")
        
        for prop, entity_id in matched_entities.items():
            try:
                # 调用HA API获取实体状态
                resp = requests.get(
                    f"{self.config['ha_url']}/api/states/{entity_id}",
                    headers=self.ha_headers,
                    timeout=10
                )
                
                if resp.status_code != 200:
                    self.logger.warning(f"获取实体 {entity_id} 数据失败，状态码: {resp.status_code}")
                    continue
                
                entity_data = resp.json()
                state = entity_data.get("state")
                
                # 转换状态为合适的类型
                if state in ("on", "off"):
                    value = 1 if state == "on" else 0
                else:
                    try:
                        # 处理数值型数据（基础值）
                        value = float(state)
                        # 应用转换系数（默认1.0）
                        factor = conversion_factors.get(prop, 1.0)
                        converted_value = value * factor
                        self.logger.debug(f"  {prop} 转换: {value} * {factor} = {converted_value}")
                        
                        # 保留小数位数
                        if prop == "current":
                            converted_value = round(converted_value, 2)
                        elif prop in ("active_power", "voltage", "frequency"):
                            converted_value = round(converted_value, 1)
                        elif prop == "energy":
                            converted_value = round(converted_value, 3)  # 耗电量保留3位小数
                        
                        value = converted_value
                    except (ValueError, TypeError):
                        self.logger.warning(f"实体 {entity_id} 状态 '{state}' 无法转换为数值")
                        continue
                
                collected_data[prop] = value
                self.logger.info(f"  收集到 {prop} = {value}（实体: {entity_id}）")
                
            except Exception as e:
                self.logger.error(f"收集实体 {entity_id} 数据异常: {e}")
        
        return collected_data
