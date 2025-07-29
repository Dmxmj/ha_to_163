import paho.mqtt.client as mqtt
import logging
import time
import hmac
import hashlib
import json
import requests
from typing import Dict, Any


class MQTTClient:
    """完整的MQTT客户端类（确保无语法错误）"""
    
    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.logger = logging.getLogger("mqtt_client")
        self.client: mqtt.Client = None
        self.connected = False
        self.last_time_sync = 0
        self.reconnect_delay = 1
        self.ha_headers = {
            "Authorization": f"Bearer {self.config['ha_token']}",
            "Content-Type": "application/json"
        }
    
    def _init_mqtt_client(self):
        try:
            client_id = self.config["gateway_device_name"]
            username = self.config["gateway_product_key"]
            password = self._generate_mqtt_password(self.config["gateway_device_secret"])
            
            self.client = mqtt.Client(client_id=client_id, clean_session=True, protocol=mqtt.MQTTv311)
            self.client.username_pw_set(username=username, password=password)
            
            if self.config.get("use_ssl", False):
                self.client.tls_set()
                self.logger.info("已启用SSL加密连接")
            
            self.client.on_connect = self._on_connect
            self.client.on_disconnect = self._on_disconnect
            self.client.on_message = self._on_message
            self.logger.info("MQTT客户端初始化完成")
        except Exception as e:
            self.logger.error(f"MQTT客户端初始化失败: {e}")
            raise
    
    def _generate_mqtt_password(self, device_secret: str) -> str:
        try:
            if time.time() - self.last_time_sync > 300:
                self._sync_time()
            
            timestamp = int(time.time())
            counter = timestamp // 300
            self.logger.debug(f"当前counter: {counter}（时间戳: {timestamp}）")
            
            counter_bytes = str(counter).encode('utf-8')
            secret_bytes = device_secret.encode('utf-8')
            hmac_obj = hmac.new(secret_bytes, counter_bytes, hashlib.sha256)
            token = hmac_obj.digest()[:10].hex().upper()
            return f"v1:{token}"
        except Exception as e:
            self.logger.error(f"生成MQTT密码失败: {e}")
            raise
    
    def _sync_time(self):
        try:
            import ntplib
            ntp_client = ntplib.NTPClient()
            response = ntp_client.request(
                self.config.get("ntp_server", "ntp.n.netease.com"),
                version=3,
                timeout=5
            )
            self.last_time_sync = time.time()
            self.logger.info(f"NTP时间同步成功: {time.ctime(response.tx_time)}")
        except Exception as e:
            self.logger.warning(f"NTP时间同步失败（使用本地时间）: {e}")
    
    def connect(self) -> bool:
        self._init_mqtt_client()
        try:
            port = self.config["wy_mqtt_port_ssl"] if self.config.get("use_ssl") else self.config["wy_mqtt_port_tcp"]
            self.logger.info(f"连接MQTT服务器: {self.config['wy_mqtt_broker']}:{port}")
            self.client.connect(self.config["wy_mqtt_broker"], port, keepalive=60)
            self.client.loop_start()
            
            start_time = time.time()
            while not self.connected and (time.time() - start_time) < 10:
                time.sleep(0.1)
            
            return self.connected
        except Exception as e:
            self.logger.error(f"MQTT连接失败: {e}")
            return False
    
    def _on_connect(self, client: mqtt.Client, userdata: Any, flags: Dict, rc: int):
        if rc == 0:
            self.connected = True
            self.reconnect_delay = 1
            self.logger.info(f"MQTT连接成功（返回码: {rc}）")
            
            for device in self.config.get("sub_devices", []):
                if not device.get("enabled", True):
                    continue
                standard_topic = f"sys/{device['product_key']}/{device['device_name']}/thing/service/property/set"
                common_topic = f"sys/{device['product_key']}/{device['device_name']}/service/CommonService"
                client.subscribe(standard_topic, qos=1)
                client.subscribe(common_topic, qos=1)
                self.logger.info(f"订阅控制Topic: {standard_topic}")
                self.logger.info(f"订阅控制Topic: {common_topic}")
        else:
            self.connected = False
            self.logger.error(f"MQTT连接失败（返回码: {rc}）")
            self._schedule_reconnect()
    
    def _on_disconnect(self, client: mqtt.Client, userdata: Any, rc: int):
        self.connected = False
        if rc != 0:
            self.logger.warning(f"MQTT断开连接（返回码: {rc}）")
            self._schedule_reconnect()
        else:
            self.logger.info("MQTT连接正常关闭")
    
    def _schedule_reconnect(self):
        if self.reconnect_delay < 60:
            self.reconnect_delay *= 2
        self.logger.info(f"{self.reconnect_delay}秒后尝试重连...")
        time.sleep(self.reconnect_delay)
        self.connect()
    
    def _on_message(self, client: mqtt.Client, userdata: Any, msg: mqtt.MQTTMessage):
        try:
            payload = json.loads(msg.payload.decode())
            self.logger.info(f"收到消息: {msg.topic} → {json.dumps(payload, indent=2)}")
            
            topic_parts = msg.topic.split('/')
            if len(topic_parts) >= 3 and topic_parts[0] == "sys":
                product_key = topic_parts[1]
                device_name = topic_parts[2]
                command_id = payload.get("id", int(time.time() * 1000))
                self._handle_control_command(product_key, device_name, payload, command_id)
            
        except Exception as e:
            self.logger.error(f"解析消息失败: {e}，原始消息: {msg.payload.decode()}")
    
    def _handle_control_command(self, product_key: str, device_name: str, payload: dict, command_id: int):
        target_device = None
        for device in self.config.get("sub_devices", []):
            if (device.get("product_key") == product_key and 
                device.get("device_name") == device_name and 
                device.get("enabled", True)):
                target_device = device
                break
        if not target_device:
            self.logger.warning(f"未找到设备: product_key={product_key}, device_name={device_name}")
            self._send_control_reply(
                product_key, device_name, command_id,
                success=False, error_msg="设备未找到"
            )
            return
        
        target_params = payload.get("params", payload)
        
        if target_device["type"] in ("switch", "socket", "breaker") and "state" in target_params:
            self._control_device(
                target_device, target_params["state"],
                product_key, device_name, command_id
            )
        else:
            self._send_control_reply(
                product_key, device_name, command_id,
                success=False, error_msg="不支持的指令或参数"
            )
    
    def _control_device(self, device: dict, target_state: int, 
                       product_key: str, device_name: str, command_id: int):
        ha_state = "on" if target_state == 1 else "off"
        device_type = device["type"]
        entity_prefix = device["ha_entity_prefix"]
        self.logger.info(f"控制{device_type}状态为: {ha_state}，前缀: {entity_prefix}")

        matched_entity = None
        try:
            resp = requests.get(
                f"{self.config['ha_url']}/api/states",
                headers=self.ha_headers,
                timeout=10
            )
            if resp.status_code != 200:
                self.logger.error(f"查询HA实体失败，状态码: {resp.status_code}")
                self._send_control_reply(
                    product_key, device_name, command_id,
                    success=False, error_msg="查询实体失败"
                )
                return

            entities = resp.json()
            candidate_entities = [
                e["entity_id"] for e in entities
                if entity_prefix in e["entity_id"]
            ]

            if candidate_entities:
                matched_entity = candidate_entities[0]
                self.logger.info(f"匹配到控制实体: {matched_entity}")
            else:
                self.logger.error(f"未找到前缀为'{entity_prefix}'的实体")
                self._send_control_reply(
                    product_key, device_name, command_id,
                    success=False, error_msg="未找到控制实体"
                )
                return

        except Exception as e:
            self.logger.error(f"查询实体异常: {e}")
            self._send_control_reply(
                product_key, device_name, command_id,
                success=False, error_msg=f"查询异常: {str(e)}"
            )
            return

        try:
            response = requests.post(
                f"{self.config['ha_url']}/api/services/switch/turn_{ha_state}",
                headers=self.ha_headers,
                json={"entity_id": matched_entity},
                timeout=10
            )

            if response.status_code == 200:
                self.logger.info(f"控制成功: {matched_entity} → {ha_state}")
                actual_state = 1 if ha_state == "on" else 0
                self._send_control_reply(
                    product_key, device_name, command_id,
                    success=True, result_data={"state": actual_state}
                )
                self._report_state(device, actual_state)
            else:
                self.logger.error(f"控制失败，状态码: {response.status_code}")
                self._send_control_reply(
                    product_key, device_name, command_id,
                    success=False, error_msg=f"控制失败，状态码: {response.status_code}"
                )

        except Exception as e:
            self.logger.error(f"API调用异常: {e}")
            self._send_control_reply(
                product_key, device_name, command_id,
                success=False, error_msg=f"API异常: {str(e)}"
            )

    def _send_control_reply(self, product_key: str, device_name: str, command_id: int,
                           success: bool, result_data: dict = None, error_msg: str = None):
        reply_topic = f"sys/{product_key}/{device_name}/service/CommonService_reply"
        reply_payload = {
            "id": command_id,
            "version": "1.0",
            "code": 200 if success else 500,
            "message": "success" if success else error_msg,
            "data": result_data or {}
        }

        try:
            payload_str = json.dumps(reply_payload)
            result = self.client.publish(reply_topic, payload_str, qos=1)
            if result.rc == mqtt.MQTT_ERR_SUCCESS:
                self.logger.info(f"回复成功: {reply_topic} → {payload_str}")
            else:
                self.logger.error(f"回复失败（错误码: {result.rc}）")
        except Exception as e:
            self.logger.error(f"发布回复异常: {e}")

    def _report_state(self, device: dict, state: int):
        payload = {
            "id": int(time.time() * 1000),
            "version": "1.0",
            "params": {"state": state}
        }
        self.publish(device, payload)
    
    def publish(self, device: Dict[str, Any], payload: Dict[str, Any]) -> bool:
        if not self.connected:
            self.logger.warning("MQTT未连接，无法发布数据")
            return False
        
        topic = f"sys/{device['product_key']}/{device['device_name']}/event/property/post"
        try:
            payload_str = json.dumps(payload)
            result = self.client.publish(topic, payload_str, qos=1)
            return result.rc == mqtt.MQTT_ERR_SUCCESS
        except Exception as e:
            self.logger.error(f"发布数据异常: {e}")
            return False
    
    def disconnect(self):
        if self.client:
            self.client.loop_stop()
            self.client.disconnect()
            self.connected = False
            self.logger.info("MQTT连接已断开")
