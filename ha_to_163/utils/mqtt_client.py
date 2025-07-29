import paho.mqtt.client as mqtt
import logging
import json
from typing import Dict, Any

class MQTTClient:
    def __init__(self, config, device_discovery):
        self.config = config
        self.device_discovery = device_discovery  # 持有设备发现结果
        self.client = mqtt.Client()
        self.logger = logging.getLogger("mqtt_client")
        self.matched_devices = {}  # 存储设备发现的匹配结果

    def connect(self):
        # 连接逻辑保持不变
        self.client.on_connect = self._on_connect
        self.client.on_message = self._on_message
        self.client.username_pw_set(
            self.config.get("mqtt_username"),
            self.config.get("mqtt_password")
        )
        self.client.connect(
            self.config.get("mqtt_host"),
            self.config.get("mqtt_port", 1883),
            keepalive=60
        )
        self.client.loop_start()

    def _on_connect(self, client, userdata, flags, rc):
        self.logger.info(f"MQTT连接成功（返回码: {rc}）")
        # 订阅控制Topic（保持不变）
        for device in self.config.get("sub_devices", []):
            device_id = device["id"]
            product_key = device.get("product_key")
            device_name = device.get("device_name")
            control_topic = f"sys/{product_key}/{device_name}/service/CommonService"
            self.client.subscribe(control_topic)
            self.logger.info(f"订阅控制Topic: {control_topic}")

    def _on_message(self, client, userdata, msg):
        try:
            payload = json.loads(msg.payload.decode())
            self.logger.info(f"收到消息: {msg.topic} → {json.dumps(payload, indent=2)}")
            
            # 解析消息中的设备标识（从Topic中提取）
            topic_parts = msg.topic.split('/')
            if len(topic_parts) < 4:
                self.logger.warning("无效的控制Topic格式")
                return
            product_key = topic_parts[1]
            device_name = topic_parts[2]
            
            # 找到对应的设备配置
            target_device = None
            for device in self.config.get("sub_devices", []):
                if device.get("product_key") == product_key and device.get("device_name") == device_name:
                    target_device = device
                    break
            if not target_device:
                self.logger.warning(f"未找到匹配的设备: {product_key}/{device_name}")
                return
            device_id = target_device["id"]

            # 处理state控制指令
            if "params" in payload and "state" in payload["params"]:
                self._handle_state_control(
                    device_id,
                    target_device,
                    payload["id"],
                    payload["params"]["state"]
                )
        except Exception as e:
            self.logger.error(f"处理消息失败: {e}")

    def _handle_state_control(self, device_id, device, msg_id, target_state):
        """处理开关状态控制，优先使用设备发现阶段匹配的state实体"""
        try:
            # 1. 从设备发现结果中获取已匹配的state实体（关键修复）
            matched_entities = self.device_discovery.get_matched_entities(device_id)
            if not matched_entities or "state" not in matched_entities:
                self.logger.error(f"设备 {device_id} 未匹配到state实体，无法控制")
                self._reply_control_result(msg_id, 500, "未找到控制实体")
                return

            # 2. 确认实体类型为switch（确保可控制）
            state_entity = matched_entities["state"]
            if not state_entity.startswith("switch."):
                self.logger.error(f"实体 {state_entity} 不是switch类型，无法控制")
                self._reply_control_result(msg_id, 500, "实体不可控")
                return

            self.logger.info(f"准备控制设备 {device_id}，实体: {state_entity}，目标状态: {target_state}")

            # 3. 发送控制指令到HA（通过HA API控制switch实体）
            ha_url = self.config.get("ha_url")
            ha_headers = self.config.get("ha_headers")
            state_value = "on" if target_state == 1 else "off"
            
            import requests
            resp = requests.post(
                f"{ha_url}/api/services/switch/turn_{state_value}",
                headers=ha_headers,
                json={"entity_id": state_entity},
                timeout=10
            )
            resp.raise_for_status()

            # 4. 验证控制结果（可选：查询实体状态确认）
            verify_resp = requests.get(
                f"{ha_url}/api/states/{state_entity}",
                headers=ha_headers,
                timeout=10
            )
            verify_resp.raise_for_status()
            current_state = verify_resp.json().get("state")
            if current_state != state_value:
                self.logger.warning(f"控制未生效，当前状态: {current_state}")
                self._reply_control_result(msg_id, 202, "控制已发送但未生效")
                return

            # 5. 控制成功，回复结果
            self.logger.info(f"控制成功: {state_entity} → {state_value}（目标状态: {target_state}）")
            self._reply_control_result(msg_id, 200, "success", {"state": target_state})

            # 6. 主动推送状态更新
            self.publish_property_update(device, {"state": target_state})

        except Exception as e:
            self.logger.error(f"控制失败: {e}")
            self._reply_control_result(msg_id, 500, f"控制失败: {str(e)}")

    def _reply_control_result(self, msg_id, code, message, data=None):
        """回复控制结果到平台"""
        reply_topic = f"sys/{self.config.get('product_key')}/{self.config.get('device_name')}/service/CommonService_reply"
        reply_payload = {
            "id": msg_id,
            "version": "1.0",
            "code": code,
            "message": message
        }
        if data:
            reply_payload["data"] = data
        self.client.publish(reply_topic, json.dumps(reply_payload))
        self.logger.info(f"回复成功: {reply_topic} → {json.dumps(reply_payload)}")

    def publish_property_update(self, device, params):
        """主动推送属性更新"""
        product_key = device.get("product_key")
        device_name = device.get("device_name")
        topic = f"sys/{product_key}/{device_name}/event/property/post"
        payload = {
            "id": int(time.time() * 1000),
            "version": "1.0",
            "params": params
        }
        self.client.publish(topic, json.dumps(payload))
        self.logger.info(f"数据发布成功: {topic} → {json.dumps(payload)}")
    
