import paho.mqtt.client as mqtt
import logging
import json
import time
from typing import Dict, Any

class MQTTClient:
    def __init__(self, config, device_discovery):
        self.config = config
        self.device_discovery = device_discovery
        self.client = mqtt.Client()
        self.logger = logging.getLogger("mqtt_client")
        self.matched_devices = {}

    def set_matched_devices(self, matched_devices):
        """设置设备匹配结果"""
        self.matched_devices = matched_devices
        self.logger.info(f"已接收设备匹配结果，共{len(matched_devices)}个设备")

    def connect(self):
        """连接到MQTT服务器"""
        # 设置回调函数
        self.client.on_connect = self._on_connect
        self.client.on_message = self._on_message
        
        # 设置认证信息
        if "mqtt_username" in self.config and "mqtt_password" in self.config:
            self.client.username_pw_set(
                self.config["mqtt_username"],
                self.config["mqtt_password"]
            )
        
        # 连接服务器
        mqtt_host = self.config.get("mqtt_host", "device.iot.163.com")
        mqtt_port = self.config.get("mqtt_port", 1883)
        
        self.logger.info(f"连接MQTT服务器: {mqtt_host}:{mqtt_port}")
        self.client.connect(mqtt_host, mqtt_port, keepalive=60)
        
        # 启动网络循环
        self.client.loop_start()

    def _on_connect(self, client, userdata, flags, rc):
        """连接成功回调"""
        if rc == 0:
            self.logger.info("MQTT连接成功")
            # 订阅控制主题
            self._subscribe_control_topics()
        else:
            self.logger.error(f"MQTT连接失败，错误码: {rc}")

    def _subscribe_control_topics(self):
        """订阅设备控制主题"""
        for device_id, device_data in self.matched_devices.items():
            device_config = device_data["config"]
            product_key = device_config.get("product_key")
            device_name = device_config.get("device_name")
            
            if product_key and device_name:
                control_topic = f"sys/{product_key}/{device_name}/thing/service/property/set"
                self.client.subscribe(control_topic)
                self.logger.info(f"订阅控制Topic: {control_topic}")
                
                service_topic = f"sys/{product_key}/{device_name}/service/CommonService"
                self.client.subscribe(service_topic)
                self.logger.info(f"订阅控制Topic: {service_topic}")

    def _on_message(self, client, userdata, msg):
        """接收消息回调"""
        try:
            payload = json.loads(msg.payload.decode())
            self.logger.info(f"收到消息: {msg.topic} → {json.dumps(payload, indent=2)}")
            
            # 处理消息
            self._handle_message(msg.topic, payload)
        except Exception as e:
            self.logger.error(f"处理消息失败: {e}")

    def _handle_message(self, topic, payload):
        """处理接收到的消息"""
        # 解析主题获取设备信息
        topic_parts = topic.split('/')
        if len(topic_parts) < 4:
            self.logger.warning("无效的主题格式")
            return
            
        product_key = topic_parts[1]
        device_name = topic_parts[2]
        
        # 查找对应的设备
        target_device_id = None
        target_device_data = None
        
        for device_id, device_data in self.matched_devices.items():
            device_config = device_data["config"]
            if device_config.get("product_key") == product_key and device_config.get("device_name") == device_name:
                target_device_id = device_id
                target_device_data = device_data
                break
                
        if not target_device_id:
            self.logger.warning(f"未找到匹配的设备: {product_key}/{device_name}")
            return
            
        # 处理状态控制
        if "params" in payload and "state" in payload["params"]:
            self._handle_state_control(
                target_device_id,
                target_device_data,
                payload.get("id", int(time.time())),
                payload["params"]["state"]
            )

    def _handle_state_control(self, device_id, device_data, msg_id, target_state):
        """处理状态控制指令"""
        try:
            device_config = device_data["config"]
            entities = device_data["entities"]
            
            # 检查是否有状态实体
            if "state" not in entities:
                self.logger.error(f"设备 {device_id} 没有状态实体，无法控制")
                self._send_response(device_config, msg_id, 404, "没有状态实体")
                return
                
            state_entity = entities["state"]
            self.logger.info(f"控制设备 {device_id} 状态为: {target_state}，实体: {state_entity}")
            
            # 发送指令到Home Assistant
            ha_url = self.config.get("ha_url")
            ha_token = self.config.get("ha_token")
            
            if not ha_url or not ha_token:
                self.logger.error("Home Assistant配置不完整")
                self._send_response(device_config, msg_id, 500, "配置不完整")
                return
                
            import requests
            headers = {
                "Authorization": f"Bearer {ha_token}",
                "Content-Type": "application/json"
            }
            
            # 转换状态为HA需要的格式
            state_value = "on" if target_state == 1 else "off"
            
            # 调用HA服务切换状态
            response = requests.post(
                f"{ha_url}/api/services/switch/turn_{state_value}",
                headers=headers,
                json={"entity_id": state_entity},
                timeout=10
            )
            
            if response.status_code == 200:
                self.logger.info(f"控制成功: {state_entity} → {state_value}")
                self._send_response(device_config, msg_id, 200, "success", {"state": target_state})
                
                # 推送状态更新
                self.publish_property_update(device_config, {"state": target_state})
            else:
                self.logger.error(f"控制失败，HA返回状态码: {response.status_code}")
                self._send_response(device_config, msg_id, 500, "控制失败")
                
        except Exception as e:
            self.logger.error(f"处理控制指令失败: {e}")
            self._send_response(device_config, msg_id, 500, f"处理失败: {str(e)}")

    def _send_response(self, device_config, msg_id, code, message, data=None):
        """发送响应到平台"""
        product_key = device_config.get("product_key")
        device_name = device_config.get("device_name")
        
        if not product_key or not device_name:
            self.logger.warning("设备配置不完整，无法发送响应")
            return
            
        response_topic = f"sys/{product_key}/{device_name}/service/CommonService_reply"
        response_payload = {
            "id": msg_id,
            "version": "1.0",
            "code": code,
            "message": message
        }
        
        if data:
            response_payload["data"] = data
            
        self.client.publish(response_topic, json.dumps(response_payload))
        self.logger.info(f"发送响应: {response_topic} → {json.dumps(response_payload)}")

    def publish_property_update(self, device_config, params):
        """发布属性更新到平台"""
        product_key = device_config.get("product_key")
        device_name = device_config.get("device_name")
        
        if not product_key or not device_name:
            self.logger.warning("设备配置不完整，无法发布属性更新")
            return
            
        topic = f"sys/{product_key}/{device_name}/event/property/post"
        payload = {
            "id": int(time.time() * 1000),
            "version": "1.0",
            "params": params
        }
        
        result = self.client.publish(topic, json.dumps(payload))
        if result.rc == mqtt.MQTT_ERR_SUCCESS:
            self.logger.info(f"数据发布成功: {topic} → {json.dumps(payload)}")
        else:
            self.logger.error(f"数据发布失败，错误码: {result.rc}")
    
