{
    import requests
    import paho.mqtt.client as mqtt
    import json
    import time
    import hmac
    import hashlib
    from typing import Dict, List, Any

    class ConfigLoader:
        """加载Add-on配置（从/data/options.json）"""
        def load(self) -> Dict[str, Any]:
            try:
                with open("/data/options.json", "r") as f:
                    return json.load(f)
            except Exception as e:
                print(f"配置加载失败: {str(e)}")
                raise

    class HATo163Gateway:
        def __init__(self):
            self.config = ConfigLoader().load()
            self.ha_entities: Dict[str, Dict] = {}  # 按设备ID存储实体
            self.device_type_handlers = {
                "sensor": self._handle_sensor,
                "switch": self._handle_switch,
                "socket": self._handle_socket,
                "breaker": self._handle_breaker
            }

        def discover_ha_entities(self) -> None:
            """发现HA中符合前缀的实体（支持所有设备类型）"""
            headers = {"Authorization": f"Bearer {self.config['ha_token']}"}
            try:
                response = requests.get(
                    f"{self.config['ha_url']}/api/states",
                    headers=headers,
                    verify=self.config['use_ssl'],
                    timeout=10
                )
                response.raise_for_status()
                states = response.json()
            except Exception as e:
                print(f"HA实体发现失败: {str(e)}")
                return

            # 按设备配置匹配实体
            for device in self.config['sub_devices']:
                if not device['enabled']:
                    continue
                
                device_id = device['id']
                prefix = device['ha_entity_prefix']
                matched_entities = [
                    state for state in states
                    if state['entity_id'].startswith(prefix)
                ]
                
                self.ha_entities[device_id] = {
                    "device_config": device,
                    "entities": matched_entities
                }
                print(f"设备 {device_id} 发现 {len(matched_entities)} 个实体")

        def push_to_163_platform(self) -> None:
            """根据设备类型推送数据到网易平台"""
            for device_id, data in self.ha_entities.items():
                device_config = data['device_config']
                
                # 检查设备是否启用
                if not device_config['enabled']:
                    print(f"设备 {device_id} 已禁用，跳过推送")
                    continue
                
                # 检查三元组是否完整
                if not all([
                    device_config['product_key'].strip(),
                    device_config['device_name'].strip(),
                    device_config['device_secret'].strip()
                ]):
                    print(f"设备 {device_id} 三元组不完整，跳过推送")
                    continue
                
                # 调用对应设备类型的处理器
                handler = self.device_type_handlers.get(device_config['device_type'])
                if handler:
                    handler(device_config, data['entities'])
                else:
                    print(f"设备 {device_id} 类型 {device_config['device_type']} 不支持，跳过推送")

        def _handle_sensor(self, device_config: Dict, entities: List[Dict]) -> None:
            """处理传感器设备（温度、湿度等）"""
            self._publish_common(device_config, entities, {
                "temp": lambda v: float(v),    # 温度→浮点型
                "hum": lambda v: float(v),     # 湿度→浮点型
                "batt": lambda v: int(v[:-1])  # 电池百分比（如"85%"→85）
            })

        def _handle_switch(self, device_config: Dict, entities: List[Dict]) -> None:
            """处理开关设备（仅状态）"""
            self._publish_common(device_config, entities, {
                "state": lambda v: 1 if v == "on" else 0  # 开关状态→1/0
            })

        def _handle_socket(self, device_config: Dict, entities: List[Dict]) -> None:
            """处理智能插座（状态、功率、电流）"""
            self._publish_common(device_config, entities, {
                "state": lambda v: 1 if v == "on" else 0,  # 开关状态→1/0
                "power": lambda v: float(v),               # 功率→浮点型（单位W）
                "current": lambda v: float(v)              # 电流→浮点型（单位A）
            })

        def _handle_breaker(self, device_config: Dict, entities: List[Dict]) -> None:
            """处理智能断路器（状态、电压、电流、功率、漏电）"""
            self._publish_common(device_config, entities, {
                "state": lambda v: 1 if v == "on" else 0,   # 开关状态→1/0
                "voltage": lambda v: float(v),              # 电压→浮点型（单位V）
                "current": lambda v: float(v),              # 电流→浮点型（单位A）
                "power": lambda v: float(v),                # 功率→浮点型（单位W）
                "leakage": lambda v: float(v)               # 漏电流→浮点型（单位mA）
            })

        def _publish_common(self, device_config: Dict, entities: List[Dict], 
                           converters: Dict[str, Any]) -> None:
            """通用发布逻辑（连接MQTT并推送数据）"""
            # 构建MQTT客户端，使用最新的API版本2
            client = mqtt.Client(client_id=device_config['device_name'], protocol=mqtt.MQTTv311)
            client.username_pw_set(
                username=device_config['device_name'],
                password=self._generate_163_password(device_config['device_secret'])
            )
            
            try:
                client.connect(
                    self.config.get('wy_mqtt_broker', 'device.iot.163.com'),
                    self.config.get('wy_mqtt_port_tcp', 1883)
                )
                
                # 处理每个实体
                prefix = device_config['ha_entity_prefix']
                for entity in entities:
                    entity_id = entity['entity_id']
                    # 提取属性名（如"switch.xiaomi_socket_power"→"power"）
                    prop_name = entity_id.replace(prefix, '')
                    
                    # 只处理配置中声明的属性
                    if prop_name not in device_config['supported_properties']:
                        continue
                    
                    # 转换属性值
                    raw_value = entity['state']
                    converter = converters.get(prop_name)
                    if not converter:
                        print(f"未找到{prop_name}的转换器，跳过")
                        continue
                    
                    try:
                        converted_value = converter(raw_value)
                    except Exception as e:
                        print(f"转换{entity_id}值失败: {str(e)}")
                        continue
                    
                    # 发布到网易平台
                    topic = f"/{device_config['product_key']}/{device_config['device_name']}/property/post"
                    payload = json.dumps({
                        "id": int(time.time()),
                        "version": "1.0",
                        "params": {prop_name: converted_value}
                    })
                    client.publish(topic, payload)
                    print(f"推送 {device_config['id']} {entity_id} → {payload}")
                    
            except Exception as e:
                print(f"MQTT推送失败: {str(e)}")
            finally:
                client.disconnect()

        def _generate_163_password(self, secret: str) -> str:
            """生成网易IoT平台MQTT密码（HMAC签名）"""
            timestamp = int(time.time())
            counter = timestamp // 300  # 每5分钟更新一次签名
            hmac_obj = hmac.new(secret.encode(), str(counter).encode(), hashlib.sha256)
            token = hmac_obj.digest()[:10].hex().upper()
            return f"v1:{token}"

        def run(self) -> None:
            """主循环"""
            print("===== HA to 163 Gateway 启动 =====")
            print("验证依赖是否安装...")
            
            # 验证关键依赖
            try:
                import requests
                import paho.mqtt
                import ntplib
                print("依赖验证通过")
            except ImportError as e:
                print(f"缺失依赖: {e.name}，尝试安装...")
                import subprocess
                import sys
                subprocess.check_call([sys.executable, "-m", "pip", "install", e.name])
            
            while True:
                self.discover_ha_entities()
                self.push_to_163_platform()
                time.sleep(self.config['wy_push_interval'])

    if __name__ == "__main__":
        try:
            gateway = HATo163Gateway()
            gateway.run()
        except Exception as e:
            print(f"程序异常退出: {str(e)}")
            exit(1)
    }
    
