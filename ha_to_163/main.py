import logging
import time
import json
from typing import Dict, Any
from device_discovery.ha_discovery import HADiscovery
from mqtt_client import MQTTClient
from config_loader import load_config, validate_config

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger("ha_to_163")

class HAto163Gateway:
    """HA到163平台的网关主类"""
    
    def __init__(self):
        # 加载并验证配置
        self.config = load_config()
        validate_config(self.config)
        self.ha_headers = {
            "Authorization": f"Bearer {self.config.get('ha_token')}",
            "Content-Type": "application/json"
        }
        
        # 初始化设备发现
        self.device_discovery = HADiscovery(self.config, self.ha_headers)
        
        # 初始化MQTT客户端（传入设备发现实例）
        self.mqtt_client = MQTTClient(self.config, self.device_discovery)
        
        # 存储设备匹配结果
        self.matched_devices = {}
        
        # 启动延迟（确保依赖服务就绪）
        self.startup_delay = self.config.get("startup_delay", 30)

    def start(self):
        """启动网关服务"""
        logger.info("===== HA to 163 Gateway 启动 =====")
        
        # 启动延迟
        if self.startup_delay > 0:
            logger.info(f"启动延迟 {self.startup_delay} 秒...")
            time.sleep(self.startup_delay)
        
        # 验证Home Assistant连接
        if not self._verify_ha_connection():
            logger.error("Home Assistant连接验证失败，无法启动网关")
            return
        
        logger.info("Home Assistant已就绪")
        
        # 连接MQTT服务器
        self.mqtt_client.connect()
        
        # 执行设备发现
        self._run_device_discovery()
        
        # 启动数据推送循环
        self._start_data_pushing_loop()

    def _verify_ha_connection(self) -> bool:
        """验证与Home Assistant的连接"""
        try:
            import requests
            ha_url = self.config.get("ha_url")
            resp = requests.get(
                f"{ha_url}/api/",
                headers=self.ha_headers,
                timeout=10
            )
            return resp.status_code == 200
        except Exception as e:
            logger.error(f"Home Assistant连接验证失败: {e}")
            return False

    def _run_device_discovery(self):
        """执行设备发现并存储结果"""
        self.matched_devices = self.device_discovery.discover()
        # 将匹配结果同步给MQTT客户端
        self.mqtt_client.set_matched_devices(self.matched_devices)

    def _start_data_pushing_loop(self):
        """启动数据推送循环"""
        push_interval = self.config.get("push_interval", 60)  # 默认60秒推送一次
        while True:
            try:
                logger.info("开始数据推送...")
                self._push_all_devices_data()
                logger.info("本轮数据推送完成")
            except Exception as e:
                logger.error(f"数据推送失败: {e}")
            
            # 等待下一次推送
            time.sleep(push_interval)

    def _push_all_devices_data(self):
        """推送所有设备的数据"""
        for device_id, device_data in self.matched_devices.items():
            device_config = device_data["config"]
            logger.info(f"\n推送设备 {device_id} 数据")
            
            # 收集设备属性数据
            properties = self._collect_device_properties(device_data)
            
            # 推送数据到MQTT
            self.mqtt_client.publish_property_update(device_config, properties)

    def _collect_device_properties(self, device_data: Dict) -> Dict[str, Any]:
        """收集设备的属性数据"""
        properties = {}
        entities = device_data["entities"]
        device_type = device_data["config"]["type"]
        
        try:
            import requests
            ha_url = self.config.get("ha_url")
            
            for prop_name, entity_id in entities.items():
                # 从HA获取实体状态
                resp = requests.get(
                    f"{ha_url}/api/states/{entity_id}",
                    headers=self.ha_headers,
                    timeout=10
                )
                resp.raise_for_status()
                entity_data = resp.json()
                state = entity_data.get("state")
                
                # 转换状态为合适的类型
                if state is not None:
                    # 数值类型转换
                    if prop_name in ["temp", "hum", "batt", "energy", "current", "voltage", "active_power"]:
                        try:
                            properties[prop_name] = float(state)
                        except ValueError:
                            properties[prop_name] = state
                    # 开关状态转换
                    elif prop_name == "state":
                        properties[prop_name] = 1 if state.lower() == "on" else 0
                    else:
                        properties[prop_name] = state
                
                logger.info(f"  收集到 {prop_name} = {properties[prop_name]}（实体: {entity_id}）")
        
        except Exception as e:
            logger.error(f"收集设备属性失败: {e}")
        
        # 处理缺失的电池数据（环境传感器）
        if device_type in ["sensor"] and "batt" not in properties:
            logger.warning("  未获取到电池数据，使用默认值100")
            properties["batt"] = 100
        
        return properties

if __name__ == "__main__":
    try:
        gateway = HAto163Gateway()
        gateway.start()
    except Exception as e:
        logger.critical(f"网关启动失败: {e}", exc_info=True)
        exit(1)
    
