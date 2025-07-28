import requests
import paho.mqtt.client as mqtt
import json
import time
import hmac
import hashlib
import sys
from typing import Dict, List, Any

# 禁用输出缓冲，确保日志实时显示
sys.stdout.reconfigure(line_buffering=True)
sys.stderr.reconfigure(line_buffering=True)

class ConfigLoader:
    """加载Add-on配置（从/data/options.json）"""
    def load(self) -> Dict[str, Any]:
        try:
            print("开始加载配置文件 /data/options.json...")
            with open("/data/options.json", "r") as f:
                config = json.load(f)
            print("配置文件加载成功")
            return config
        except Exception as e:
            print(f"配置加载失败: {str(e)}，10秒后重试...")
            time.sleep(10)
            raise  # 重试后仍失败则退出

class HATo163Gateway:
    def __init__(self):
        self.config = ConfigLoader().load()
        self.ha_entities: Dict[str, Dict] = {}
        self.device_type_handlers = {
            "sensor": self._handle_sensor,
            "switch": self._handle_switch,
            "socket": self._handle_socket,
            "breaker": self._handle_breaker
        }
        # 从配置中获取超时参数（默认10秒）
        self.request_timeout = self.config.get("request_timeout", 10)

    def discover_ha_entities(self) -> None:
        """发现HA实体（添加超时和重试机制）"""
        print("开始发现HA实体...")
        headers = {"Authorization": f"Bearer {self.config['ha_token']}"}
        ha_url = self.config['ha_url'].rstrip('/')  # 处理URL末尾斜杠
        api_url = f"{ha_url}/api/states"

        try:
            print(f"请求HA API: {api_url}（超时{self.request_timeout}秒）")
            response = requests.get(
                api_url,
                headers=headers,
                verify=self.config['use_ssl'],
                timeout=self.request_timeout  # 关键：设置超时，避免无限等待
            )
            response.raise_for_status()  # 触发HTTP错误（如401、500）
            states = response.json()
            print(f"成功获取HA实体，共{len(states)}个状态")
        except requests.exceptions.Timeout:
            print(f"HA API请求超时（超过{self.request_timeout}秒），跳过本次发现")
            return
        except Exception as e:
            print(f"HA实体发现失败: {str(e)}，跳过本次发现")
            return

        # 匹配实体逻辑
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
            print(f"设备 {device_id} 匹配到 {len(matched_entities)} 个实体（前缀: {prefix}）")

    def push_to_163_platform(self) -> None:
        """推送数据（优化MQTT连接超时）"""
        print("开始推送数据到网易平台...")
        for device_id, data in self.ha_entities.items():
            device_config = data['device_config']
            
            if not device_config['enabled']:
                print(f"设备 {device_id} 已禁用，跳过")
                continue
            
            # 三元组校验
            if not all([device_config['product_key'].strip(), 
                       device_config['device_name'].strip(), 
                       device_config['device_secret'].strip()]):
                print(f"设备 {device_id} 三元组不完整，跳过")
                continue
            
            # 调用设备处理器
            handler = self.device_type_handlers.get(device_config['device_type'])
            if handler:
                handler(device_config, data['entities'])
            else:
                print(f"设备 {device_id} 类型不支持，跳过")

    def _publish_common(self, device_config: Dict, entities: List[Dict], 
                       converters: Dict[str, Any]) -> None:
        """MQTT发布（添加连接超时和操作超时）"""
        mqtt_broker = self.config.get('wy_mqtt_broker', 'device.iot.163.com')
        mqtt_port = self.config.get('wy_mqtt_port_tcp', 1883)
        device_id = device_config['id']

        try:
            # 创建客户端（指定API版本和超时）
            client = mqtt.Client(
                client_id=device_config['device_name'],
                protocol=mqtt.MQTTv311,
                callback_api_version=mqtt.CallbackAPIVersion.VERSION2
            )
            client.username_pw_set(
                username=device_config['device_name'],
                password=self._generate_163_password(device_config['device_secret'])
            )
            client.connect_timeout = 10  # MQTT连接超时（秒）

            # 连接MQTT服务器
            print(f"设备 {device_id} 连接MQTT服务器: {mqtt_broker}:{mqtt_port}")
            client.connect(mqtt_broker, mqtt_port)
            client.loop_start()  # 启动后台循环，避免阻塞

            # 处理实体推送
            prefix = device_config['ha_entity_prefix']
            for entity in entities:
                entity_id = entity['entity_id']
                prop_name = entity_id.replace(prefix, '')
                if prop_name not in device_config['supported_properties']:
                    continue

                # 转换值并推送
                try:
                    raw_value = entity['state']
                    converted_value = converters[prop_name](raw_value)
                    topic = f"/{device_config['product_key']}/{device_config['device_name']}/property/post"
                    payload = json.dumps({
                        "id": int(time.time()),
                        "version": "1.0",
                        "params": {prop_name: converted_value}
                    })
                    client.publish(topic, payload, qos=1)  # QoS=1确保消息送达
                    print(f"设备 {device_id} 推送成功: {entity_id} → {payload}")
                except Exception as e:
                    print(f"设备 {device_id} 推送 {entity_id} 失败: {str(e)}")

            # 等待消息发送完成
            time.sleep(2)
            client.loop_stop()
            client.disconnect()

        except Exception as e:
            print(f"设备 {device_id} MQTT操作失败: {str(e)}")

    def _generate_163_password(self, secret: str) -> str:
        """生成网易平台密码"""
        timestamp = int(time.time())
        counter = timestamp // 300
        hmac_obj = hmac.new(secret.encode(), str(counter).encode(), hashlib.sha256)
        token = hmac_obj.digest()[:10].hex().upper()
        return f"v1:{token}"

    # 设备类型处理方法
    def _handle_sensor(self, device_config: Dict, entities: List[Dict]) -> None:
        self._publish_common(device_config, entities, {
            "temp": lambda v: float(v),
            "hum": lambda v: float(v),
            "batt": lambda v: int(v[:-1]) if isinstance(v, str) and v.endswith('%') else int(v)
        })

    def _handle_switch(self, device_config: Dict, entities: List[Dict]) -> None:
        self._publish_common(device_config, entities, {
            "state": lambda v: 1 if v == "on" else 0
        })

    def _handle_socket(self, device_config: Dict, entities: List[Dict]) -> None:
        self._publish_common(device_config, entities, {
            "state": lambda v: 1 if v == "on" else 0,
            "power": lambda v: float(v),
            "current": lambda v: float(v)
        })

    def _handle_breaker(self, device_config: Dict, entities: List[Dict]) -> None:
        self._publish_common(device_config, entities, {
            "state": lambda v: 1 if v == "on" else 0,
            "voltage": lambda v: float(v),
            "current": lambda v: float(v),
            "power": lambda v: float(v),
            "leakage": lambda v: float(v)
        })

    def run(self) -> None:
        """主循环（增加启动步骤日志）"""
        print("===== HA to 163 Gateway 启动 =====")
        print("开始验证依赖...")
        
        # 依赖验证（带超时）
        required_deps = ['requests', 'paho.mqtt', 'ntplib']
        missing_deps = []
        for dep in required_deps:
            try:
                __import__(dep)
                print(f"依赖 {dep} 已安装")
            except ImportError:
                missing_deps.append(dep)
        
        # 安装缺失依赖（带超时）
        if missing_deps:
            print(f"发现缺失依赖: {missing_deps}，开始安装...")
            import subprocess
            try:
                # 限制安装超时30秒
                subprocess.check_call(
                    [sys.executable, "-m", "pip", "install", *missing_deps],
                    timeout=30
                )
                print("缺失依赖安装成功")
            except subprocess.TimeoutExpired:
                print(f"依赖安装超时（超过30秒），程序可能无法正常运行")
            except Exception as e:
                print(f"依赖安装失败: {str(e)}，程序可能无法正常运行")

        # 启动主循环
        print(f"启动主循环（推送间隔: {self.config['wy_push_interval']}秒）")
        while True:
            self.discover_ha_entities()
            self.push_to_163_platform()
            # 显示等待日志，避免误以为卡住
            print(f"等待下一次循环（{self.config['wy_push_interval']}秒）...")
            time.sleep(self.config['wy_push_interval'])

if __name__ == "__main__":
    try:
        gateway = HATo163Gateway()
        gateway.run()
    except Exception as e:
        print(f"程序异常退出: {str(e)}", file=sys.stderr)
        sys.exit(1)
    
