import paho.mqtt.client as mqtt
import logging
import time
import hmac
import hashlib
import json
import requests
from typing import Dict, Any


class MQTTClient:
    """网易IoT平台MQTT客户端（支持控制结果回传）"""
    
    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.logger = logging.getLogger("mqtt_client")
        self.client: mqtt.Client = None
        self.connected = False
        self.last_time_sync = 0  # 用于时间同步
        self.reconnect_delay = 1  # 重连延迟（秒）
        # 初始化HA请求头
        self.ha_headers = {
            "Authorization": f"Bearer {self.config['ha_token']}",
            "Content-Type": "application/json"
        }
    
    def _init_mqtt_client(self):
        """初始化MQTT客户端"""
        try:
            client_id = self.config["gateway_device_name"]
            username = self.config["gateway_product_key"]
            password = self._generate_mqtt_password(self.config["gateway_device_secret"])
            
            self.client = mqtt.Client(client_id=client_id, clean_session=True, protocol=mqtt.MQTTv311)
            self.client.username_pw_set(username=username, password=password)
            
            # SSL配置
            if self.config.get("use_ssl", False):
                self.client.tls_set()
                self.logger.info("已启用SSL加密连接")
            
            # 回调函数
            self.client.on_connect = self._on_connect
            self.client.on_disconnect = self._on_disconnect
            self.client.on_message = self._on_message
            self.logger.info("MQTT客户端初始化完成")
        except Exception as e:
            self.logger.error(f"MQTT客户端初始化失败: {e}")
            raise
    
    def _generate_mqtt_password(self, device_secret: str) -> str:
        """生成MQTT密码"""
        try:
            if time.time() - self.last_time_sync > 300:
                self._sync_time()
            
            timestamp = int(time.time())
            counter = timestamp // 300
            self.logger.debug(f"当前counter: {counter}（基于时间戳: {timestamp}）")
            
            counter_bytes = str(counter).encode('utf-8')
            secret_bytes = device_secret.encode('utf-8')
            hmac_obj = hmac.new(secret_bytes, counter_bytes, hashlib.sha256)
            token = hmac_obj.digest()[:10].hex().upper()
            return f"v1:{token}"
        except Exception as e:
            self.logger.error(f"生成MQTT密码失败: {e}")
            raise
    
    def _sync_time(self):
        """同步NTP时间"""
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
        """连接MQTT服务器"""
        self._init_mqtt_client()
        try:
            port = self.config["wy_mqtt_port_ssl"] if self.config.get("use_ssl") else self.config["wy_mqtt_port_tcp"]
            self.logger.info(f"连接MQTT服务器: {self.config['wy_mqtt_broker']}:{port}")
            self.client.connect(self.config["wy_mqtt_broker"], port, keepalive=60)
            self.client.loop_start()
            
            # 等待连接成功
            start_time = time.time()
            while not self.connected and (time.time() - start_time) < 10:
                time.sleep(0.1)
            
            return self.connected
        except Exception as e:
            self.logger.error(f"MQTT连接失败: {e}")
            return False
    
    def _on_connect(self, client: mqtt.Client, userdata: Any, flags: Dict, rc: int):
        """连接回调（订阅控制Topic）"""
        if rc == 0:
            self.connected = True
            self.reconnect_delay = 1
            self.logger.info(f"MQTT连接成功（返回码: {rc}）")
            
            # 订阅控制Topic
            for device in self.config.get("sub_devices", []):
                if not device.get("enabled", True):
                    continue
                standard_set_topic = f"sys/{device['product_key']}/{device['device_name']}/thing/service/property/set"
                common_service_topic = f"sys/{device['product_key']}/{device['device_name']}/service/CommonService"
                client.subscribe(standard_set_topic, qos=1)
                client.subscribe(common_service_topic, qos=1)
                self.logger.info(f"订阅控制Topic: {standard_set_topic}")
                self.logger.info(f"订阅控制Topic: {common_service_topic}")
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
        """安排重连"""
        if self.reconnect_delay < 60:
            self.reconnect_delay *= 2
        self.logger.info(f"{self.reconnect_delay}秒后尝试重连...")
        time.sleep(self.reconnect_delay)
        self.connect()
    
    def _on_message(self, client: mqtt.Client, userdata: Any, msg: mqtt.MQTTMessage):
        """处理收到的消息（解析控制指令）"""
        try:
            payload = json.loads(msg.payload.decode())
            self.logger.info(f"收到消息: {msg.topic} → {json.dumps(payload, indent=2)}")
            
            # 解析Topic，提取产品密钥和设备名称
            topic_parts = msg.topic.split('/')
            if len(topic_parts) >= 3 and topic_parts[0] == "sys":
                product_key = topic_parts[1]
                device_name = topic_parts[2]
                # 传递原始消息的id，用于回复时匹配
                command_id = payload.get("id", int(time.time() * 1000))
                self._handle_control_command(product_key, device_name, payload, command_id)
            
        except Exception as e:
            self.logger.error(f"解析消息失败: {e}，原始消息: {msg.payload.decode()}")
    
    def _handle_control_command(self, product_key: str, device_name: str, payload: dict, command_id: int):
        """处理控制指令（新增command_id参数用于回复）"""
        # 1. 查找匹配的子设备
        target_device = None
        for device in self.config.get("sub_devices", []):
            if (device.get("product_key") == product_key and 
                device.get("device_name") == device_name and 
                device.get("enabled", True)):
                target_device = device
                break
        if not target_device:
            self.logger.warning(f"未找到设备: product_key={product_key}, device_name={device_name}")
            # 即使设备未找到，也回复失败状态
            self._send_control_reply(
                product_key, device_name, command_id, 
                success=False, error_msg="设备未找到"
            )
            return
        
        # 2. 提取控制参数
        target_params = payload.get("params", payload)
        
        # 3. 处理开关和插座的状态控制
        if target_device["type"] in ("switch", "socket") and "state" in target_params:
            # 传递command_id用于控制成功后回复
            self._control_switch_or_socket(
                target_device, 
                target_params["state"], 
                product_key, 
                device_name, 
                command_id
            )
        else:
            # 不支持的指令类型，回复失败
            self._send_control_reply(
                product_key, device_name, command_id,
                success=False, error_msg="不支持的指令或参数"
            )
    
    def _control_switch_or_socket(self, device: dict, target_state: int, 
                                 product_key: str, device_name: str, command_id: int):
        """控制设备并回复结果"""
        ha_state = "on" if target_state == 1 else "off"
        device_type = device["type"]
        device_id = device["id"]
        entity_prefix = device["ha_entity_prefix"]
        self.logger.info(f"控制{device_type} {device_id} 状态为: {ha_state}")

        # 1. 匹配实体
        matched_entity = None
        try:
            resp = requests.get(
                f"{self.config['ha_url']}/api/states",
                headers=self.ha_headers,
                timeout=10
            )
            if resp.status_code != 200:
                self.logger.error(f"查询HA实体失败，状态码: {resp.status_code}")
                # 回复控制失败
                self._send_control_reply(
                    product_key, device_name, command_id,
                    success=False, error_msg="查询设备实体失败"
                )
                return

            entities = resp.json()
            candidate_entities = [
                e["entity_id"] for e in entities
                if e["entity_id"].startswith(entity_prefix)
                and "on" in e["entity_id"]
                and e["entity_id"].split('.')[0] == "switch"
            ]

            if candidate_entities:
                matched_entity = candidate_entities[0]
                self.logger.info(f"精准匹配到控制实体: {matched_entity}")
            else:
                self.logger.error(f"未找到符合条件的实体")
                self._send_control_reply(
                    product_key, device_name, command_id,
                    success=False, error_msg="未找到控制实体"
                )
                return

        except Exception as e:
            self.logger.error(f"查询HA实体时异常: {e}")
            self._send_control_reply(
                product_key, device_name, command_id,
                success=False, error_msg=f"查询实体异常: {str(e)}"
            )
            return

        # 2. 调用HA API控制
        try:
            service_url = f"{self.config['ha_url']}/api/services/switch/turn_{ha_state}"
            response = requests.post(
                service_url,
                headers=self.ha_headers,
                json={"entity_id": matched_entity},
                timeout=10
            )

            self.logger.info(
                f"HA API调用详情: URL={service_url}, 实体={matched_entity}, "
                f"状态码={response.status_code}, 响应={response.text}"
            )

            if response.status_code == 200:
                self.logger.info(f"控制成功，实体 {matched_entity} 已切换至 {ha_state}")
                # 控制成功：获取实际状态并回复
                actual_state = 1 if ha_state == "on" else 0
                self._send_control_reply(
                    product_key, device_name, command_id,
                    success=True, 
                    result_data={"state": actual_state}  # 回传实际状态
                )
                # 同步上报属性
                self._report_state(device, actual_state)
            else:
                self.logger.error(f"控制失败，HA返回非200状态码")
                self._send_control_reply(
                    product_key, device_name, command_id,
                    success=False, 
                    error_msg=f"控制失败，状态码: {response.status_code}"
                )

        except Exception as e:
            self.logger.error(f"调用HA API异常: {e}")
            self._send_control_reply(
                product_key, device_name, command_id,
                success=False, 
                error_msg=f"API调用异常: {str(e)}"
            )

    def _send_control_reply(self, product_key: str, device_name: str, command_id: int,
                           success: bool, result_data: dict = None, error_msg: str = None):
        """发送控制结果回复到服务器"""
        # 1. 构造回复Topic
        reply_topic = f"sys/{product_key}/{device_name}/service/CommonService_reply"
        
        # 2. 构造回复消息体（与平台协议对齐）
        reply_payload = {
            "id": command_id,  # 与原命令的id保持一致，用于匹配
            "version": "1.0",
            "code": 200 if success else 500,  # 200=成功，500=失败
            "message": "success" if success else error_msg,
            "data": result_data or {}  # 成功时返回实际状态，失败时为空
        }
        
        # 3. 发布回复消息
        try:
            payload_str = json.dumps(reply_payload)
            result = self.client.publish(reply_topic, payload_str, qos=1)
            if result.rc == mqtt.MQTT_ERR_SUCCESS:
                self.logger.info(f"控制结果回复成功: {reply_topic} → {payload_str}")
            else:
                self.logger.error(f"控制结果回复失败（错误码: {result.rc}）: {reply_topic}")
        except Exception as e:
            self.logger.error(f"发布回复消息异常: {e}")

    def _report_state(self, device: dict, state: int):
        """上报设备状态"""
        payload = {
            "id": int(time.time() * 1000),
            "version": "1.0",
            "params": {"state": state}
        }
        self.publish(device, payload)
    
    def publish(self, device: Dict[str, Any], payload: Dict[str, Any]) -> bool:
        """发布设备数据"""
        if not self.connected:
            self.logger.warning("MQTT未连接，无法发布数据")
            return False
        
        topic = f"sys/{device['product_key']}/{device['device_name']}/event/property/post"
        try:
            payload_str = json.dumps(payload)
            result = self.client.publish(topic, payload_str, qos=1)
            if result.rc == mqtt.MQTT_ERR_SUCCESS:
                self.logger.info(f"数据发布成功: {topic} → {payload_str[:50]}...")
                return True
            else:
                self.logger.error(f"数据发布失败（错误码: {result.rc}）: {topic}")
                return False
        except Exception as e:
            self.logger.error(f"发布数据异常: {e}")
            return False
    
    def disconnect(self):
        """断开MQTT连接"""
        if self.client:
            self.client.loop_stop()
            self.client.disconnect()
            self.connected = False
            self.logger.info("MQTT连接已断开")
    
