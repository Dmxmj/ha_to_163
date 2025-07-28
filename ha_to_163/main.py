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
        self.request_timeout = self.config.get("request_timeout", 10)
        # 网易平台主题前缀（与历史成功日志一致）
        self.mqtt_topic_prefix = "sys"
        # MQTT QoS级别（设置为0）
        self.mqtt_qos = 0

    def discover_ha_entities(self) -> None:
        """发现HA实体并预览属性"""
        print("开始发现HA实体...")
        headers = {"Authorization": f"Bearer {self.config['ha_token']}"}
        ha_url = self.config['ha_url'].rstrip('/')
        api_url = f"{ha_url}/api/states"

        try:
            print(f"请求HA API: {api_url}（超时{self.request_timeout}秒）")
            response = requests.get(
                api_url,
                headers=headers,
                verify=self.config['use_ssl'],
                timeout=self.request_timeout
            )
            response.raise_for_status()
            states = response.json()
            print(f"成功获取HA实体，共{len(states)}个状态")
        except requests.exceptions.Timeout:
            print(f"HA API请求超时，跳过本次发现")
            return
        except Exception as e:
            print(f"HA实体发现失败: {str(e)}，跳过本次发现")
            return

        # 匹配实体并预览属性
        for device in self.config['sub_devices']:
            if not device['enabled']:
                continue
            
            device_id = device['id']
            prefix = device['ha_entity_prefix']
            matched_entities = [
                state for state in states
                if state['entity_id'].startswith(prefix)
            ]
            
            # 预览匹配到的实体属性
            if matched_entities:
                preview = [f"{e['entity_id']}（状态: {e['state']}）" for e in matched_entities[:2]]
                print(f"设备 {device_id} 匹配到 {len(matched_entities)} 个实体，预览: {preview}")
            
            self.ha_entities[device_id] = {
                "device_config": device,
                "entities": matched_entities
            }

    def push_to_163_platform(self) -> None:
        """推送数据到网易平台"""
        print("开始推送数据到网易平台...")
        for device_id, data in self.ha_entities.items():
            device_config = data['device_config']
            
            if not device_config['enabled']:
                print(f"设备 {device_id} 已禁用，跳过")
                continue
            
            if not all([device_config['product_key'].strip(), 
                       device_config['device_name'].strip(), 
                       device_config['device_secret'].strip()]):
                print(f"设备 {device_id} 三元组不完整，跳过")
                continue
            
            # 检查是否有可推送的实体
            if not data['entities']:
                print(f"设备 {device_id} 无匹配实体，跳过")
                continue
            
            handler = self.device_type_handlers.get(device_config['device_type'])
            if handler:
                handler(device_config, data['entities'])
            else:
                print(f"设备 {device_id} 类型不支持，跳过")

    def _publish_common(self, device_config: Dict, entities: List[Dict], 
                       converters: Dict[str, Any]) -> None:
        """通用MQTT发布逻辑（QoS=0）"""
        device_id = device_config['id']
        product_key = device_config['product_key']
        device_name = device_config['device_name']
        mqtt_broker = self.config.get('wy_mqtt_broker', 'device.iot.163.com')
        mqtt_port = self.config.get('wy_mqtt_port_tcp', 1883)

        # 构建正确的MQTT主题（与历史成功日志一致）
        topic = f"{self.mqtt_topic_prefix}/{product_key}/{device_name}/event/property/post"
        print(f"设备 {device_id} 准备发布到主题: {topic}（QoS={self.mqtt_qos}）")

        try:
            # 创建MQTT客户端
            client = mqtt.Client(
                client_id=device_name,
                protocol=mqtt.MQTTv311,
                callback_api_version=mqtt.CallbackAPIVersion.VERSION2
            )
            client.username_pw_set(username=device_name, password=self._generate_163_password(device_config['device_secret']))
            client.connect_timeout = 10

            # 连接并检查返回码（0表示成功）
            connect_result = client.connect(mqtt_broker, mqtt_port)
            if connect_result != 0:
                print(f"设备 {device_id} MQTT连接失败（返回码: {connect_result}），请检查三元组")
                return
            print(f"设备 {device_id} MQTT连接成功")
            client.loop_start()

            # 提取并转换属性
            properties = {}
            prefix = device_config['ha_entity_prefix']
            for entity in entities:
                entity_id = entity['entity_id']
                prop_name = entity_id.replace(prefix, '')  # 提取属性名
                
                if prop_name not in converters:
                    print(f"设备 {device_id} 忽略不支持的属性: {prop_name}（实体: {entity_id}）")
                    continue
                
                # 转换属性值并记录
                try:
                    raw_value = entity['state']
                    converted_value = converters[prop_name](raw_value)
                    properties[prop_name] = converted_value
                    print(f"设备 {device_id} 收集到 {prop_name} = {converted_value}（实体: {entity_id}）")
                except Exception as e:
                    print(f"设备 {device_id} 转换 {prop_name} 失败: {str(e)}（原始值: {raw_value}）")

            # 发布数据（如果有属性）
            if not properties:
                print(f"设备 {device_id} 无有效属性可推送")
                client.loop_stop()
                client.disconnect()
                return

            payload = json.dumps({
                "id": int(time.time()),
                "version": "1.0",
                "params": properties
            })
            print(f"设备 {device_id} 准备发布: {payload}")

            # 发布数据（QoS=0，不等待确认）
            client.publish(topic, payload, qos=self.mqtt_qos)
            print(f"设备 {device_id} 数据已发送（QoS=0，不保证送达）")

            # 清理连接
            time.sleep(1)  # 短暂等待确保消息发送
            client.loop_stop()
            client.disconnect()

        except Exception as e:
            print(f"设备 {device_id} 发布失败: {str(e)}")

    def _generate_163_password(self, secret: str) -> str:
        """生成网易平台MQTT密码"""
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
        """主循环"""
        print("===== HA to 163 Gateway 启动 =====")
        print("开始验证依赖...")
        
        required_deps = ['requests', 'paho.mqtt', 'ntplib']
        missing_deps = [dep for dep in required_deps if not self._check_dep(dep)]
        
        if missing_deps:
            print(f"安装缺失依赖: {missing_deps}")
            self._install_deps(missing_deps)

        print(f"启动主循环（推送间隔: {self.config['wy_push_interval']}秒）")
        while True:
            self.discover_ha_entities()
            self.push_to_163_platform()
            print(f"等待下一次循环（{self.config['wy_push_interval']}秒）...")
            time.sleep(self.config['wy_push_interval'])

    # 辅助函数：检查/安装依赖
    def _check_dep(self, dep: str) -> bool:
        try:
            __import__(dep)
            print(f"依赖 {dep} 已安装")
            return True
        except ImportError:
            return False

    def _install_deps(self, deps: List[str]) -> None:
        import subprocess
        try:
            subprocess.check_call(
                [sys.executable, "-m", "pip", "install", *deps],
                timeout=30
            )
            print("依赖安装成功")
        except Exception as e:
            print(f"依赖安装失败: {str(e)}")

if __name__ == "__main__":
    try:
        gateway = HATo163Gateway()
        gateway.run()
    except Exception as e:
        print(f"程序异常退出: {str(e)}", file=sys.stderr)
        sys.exit(1)
    
