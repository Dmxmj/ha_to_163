import logging
import yaml
import os
from typing import Dict, Any

class ConfigLoader:
    """配置加载器"""
    
    def __init__(self, config_path: str = None):
        self.logger = logging.getLogger("config_loader")
        self.config_path = config_path or os.getenv("CONFIG_PATH", "config.yaml")
        self.config = self._load_config()
        self._validate_config()
        
    def _load_config(self) -> Dict[str, Any]:
        """加载配置文件"""
        try:
            if not os.path.exists(self.config_path):
                self.logger.error(f"配置文件不存在: {self.config_path}")
                raise FileNotFoundError(f"配置文件不存在: {self.config_path}")
                
            with open(self.config_path, 'r', encoding='utf-8') as f:
                config = yaml.safe_load(f)
                
            self.logger.info("成功加载配置")
            return config or {}
        except Exception as e:
            self.logger.error(f"加载配置失败: {e}")
            raise
    
    def _validate_config(self) -> bool:
        """验证配置的完整性"""
        required_keys = [
            "ha_url", "ha_token", 
            "mqtt_host", "mqtt_port", "mqtt_username", "mqtt_password",
            "sub_devices"
        ]
        
        missing_keys = [key for key in required_keys if key not in self.config]
        
        if missing_keys:
            self.logger.error(f"配置不完整，缺少必要键: {', '.join(missing_keys)}")
            raise ValueError(f"配置不完整，缺少必要键: {', '.join(missing_keys)}")
            
        # 验证子设备配置
        for i, device in enumerate(self.config.get("sub_devices", [])):
            device_required = ["id", "type", "ha_entity_prefix", "supported_properties", 
                             "product_key", "device_name", "enabled"]
            device_missing = [key for key in device_required if key not in device]
            if device_missing:
                self.logger.error(f"子设备 {i} 配置不完整，缺少: {', '.join(device_missing)}")
                raise ValueError(f"子设备 {i} 配置不完整")
                
        self.logger.info("配置验证通过")
        return True


# 便捷函数
def load_config(config_path: str = None) -> Dict[str, Any]:
    """加载配置的便捷函数"""
    return ConfigLoader(config_path).config

def validate_config(config: Dict[str, Any]) -> bool:
    """验证配置的便捷函数"""
    required_keys = [
        "ha_url", "ha_token", 
        "mqtt_host", "mqtt_port", "mqtt_username", "mqtt_password",
        "sub_devices"
    ]
    
    missing_keys = [key for key in required_keys if key not in config]
    if missing_keys:
        raise ValueError(f"配置不完整，缺少必要键: {', '.join(missing_keys)}")
        
    return True
