import logging
from typing import Dict

class BaseDiscovery:
    """设备发现基类"""
    
    def __init__(self, config: Dict, logger_name: str = None):
        self.config = config
        self.logger = logging.getLogger(logger_name or __name__)
        self.devices = []
    
    def discover(self) -> Dict:
        """执行设备发现，返回发现的设备列表"""
        raise NotImplementedError("子类必须实现discover方法")
