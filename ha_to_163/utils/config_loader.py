import os
import json
import logging
from typing import Dict, Any

class ConfigLoader:
    """加载和管理Home Assistant Add-on配置"""
    
    def __init__(self, config_path: str = "/data/options.json"):
        self.config_path = config_path
        self.config: Dict[str, Any] = {}
        self.logger = logging.getLogger("config_loader")
        
        try:
            self.load_config()
            self.validate_config()
        except Exception as e:
            self.logger.critical(f"配置初始化失败: {e}")
            raise
    
    def load_config(self) -> Dict[str, Any]:
        """从文件加载配置"""
        try:
            if not os.path.exists(self.config_path):
                raise FileNotFoundError(f"配置文件不存在: {self.config_path}")
            
            with open(self.config_path, 'r') as f:
                self.config = json.load(f)
                self.logger.info("成功加载配置")
                return self.config
        except json.JSONDecodeError as e:
            self.logger.error(f"配置文件解析错误: {e}")
            raise
        except Exception as e:
            self.logger.error(f"加载配置失败: {e}")
            raise
    
    def get(self, key: str, default: Any = None) -> Any:
        """获取配置值"""
        return self.config.get(key, default)
    
    def validate_config(self) -> bool:
        """验证配置完整性"""
        # 验证网关三元组
        gateway_fields = [
            "gateway_product_key",
            "gateway_device_name",
            "gateway_device_secret"
        ]
        missing_gateway = [f for f in gateway_fields if not self.config.get(f)]
        if missing_gateway:
            raise ValueError(f"缺少网关配置: {', '.join(missing_gateway)}")
        
        # 验证HA配置
        if not self.config.get("ha_url") or not self.config.get("ha_token"):
            raise ValueError("缺少HA URL或Token配置")
        
        # 验证子设备配置
        sub_devices = self.config.get("sub_devices", [])
        for i, device in enumerate(sub_devices):
            if not device.get("enabled", True):
                continue
            device_fields = ["product_key", "device_name", "device_secret", "ha_entity_prefix"]
            missing = [f for f in device_fields if not device.get(f)]
            if missing:
                raise ValueError(f"子设备 {i+1} 缺少配置: {', '.join(missing)}")
        
        self.logger.info("配置验证通过")
        return True
    