import paho.mqtt.client as mqtt
import logging
import time
import hmac
import hashlib
import json
import requests
from typing import Dict, Any


class MQTTClient:
    """网易IoT平台MQTT客户端（支持智能插座控制与状态同步）"""
    
    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.logger = logging.getLogger("mqtt_client")
        self.client: mqtt.Client = None
        self.connected = False
        self.last_time_sync = 0  # 用于时间同步
        self.reconnect_delay = 1  # 重连延迟（秒）
        # 初始化HA请求头（用于控制HA实体）
        self.ha_headers = {
            "Authorization": f"Bearer {self.config['ha_token']}",
            "Content-Type": "application/json"
        }
    
    def _init_mqtt_client(self):
        """初始化MQTT客户端（包含密码生成逻辑）"""
        try:
            # 网关身份信息
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
        """生成MQTT密码（基于设备secret和时间戳）"""
        try:
            # 同步时间（每300秒一次）
            if time.time() - self.last_time_sync > 300:
                self._sync_time()
            
            # 计算counter（每300秒递增1）
            timestamp = int(time.time())
            counter = timestamp // 300
            self.logger.debug(f"当前counter: {counter}（基于时间戳: {timestamp}）")
            
            # HMAC-SHA256计算
            counter_bytes = str(counter).encode('utf-8')
            secret_bytes = device_secret.encode('utf-8')
            hmac_obj = hmac.new(secret_bytes, counter_bytes, hashlib.sha256)
            token = hmac_obj.digest()[:10].hex().upper()
            return f"v1:{token}"
        except Exception as e:
            self.logger.error(f"生成MQTT密码失败: {e}")
            raise
    
    def _sync_time(self):
        """同步NTP时间（确保密码生成准确性）"""
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
            
            # 等待连接成功（最多10秒）
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
            self.reconnect_delay = 1  # 重置重连延迟
            self.logger.info(f"MQTT连接成功（返回码: {rc}）")
            
            # 订阅子设备的控制Topic（支持两种格式）
            for device in self.config.get("sub_devices", []):
                if not device.get("enabled", True):
                    continue
                # 标准控制Topic
                standard_set_topic = f"sys/{device['product_key']}/{device['device_name']}/thing/service/property/set"
                # 扩展控制Topic（用户指定的格式）
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
        """安排重连（指数退避策略）"""
        if self.reconnect_delay < 60:  # 最大延迟60秒
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
                self._handle_control_command(product_key, device_name, payload)
            
        except Exception as e:
            self.logger.error(f"解析消息失败: {e}，原始消息: {msg.payload.decode()}")
    
    def _handle_control_command(self, product_key: str, device_name: str, payload: dict):
        """处理控制指令（适配开关和插座）"""
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
            return
        
        # 2. 提取控制参数（兼容两种格式）
        target_params = payload.get("params", payload)
        
        # 3. 处理开关（switch）和插座（socket）的状态控制
        if target_device["type"] in ("switch", "socket") and "state" in target_params:
            self._control_switch_or_socket(target_device, target_params["state"])
    
    def _control_switch_or_socket(self, device: dict, target_state: int):
        """控制开关或插座（精准匹配实体）"""
        # 转换状态格式（1→on，0→off）
        ha_state = "on" if target_state == 1 else "off"
        device_type = device["type"]
        device_id = device["id"]
        entity_prefix = device["ha_entity_prefix"]
        self.logger.info(f"控制{device_type} {device_id} 状态为: {ha_state}（目标实体前缀: {entity_prefix}）")

        # 1. 精准匹配实体（根据日志中实体特征优化）
        matched_entity = None
        try:
            # 调用HA API获取所有实体
            resp = requests.get(
                f"{self.config['ha_url']}/api/states",
                headers=self.ha_headers,
                timeout=10
            )
            if resp.status_code != 200:
                self.logger.error(f"查询HA实体失败，状态码: {resp.status_code}")
                return

            entities = resp.json()
            # 筛选条件：前缀匹配 + 包含"on"关键词（适配日志中的实体格式） + 类型为switch
            candidate_entities = [
                e["entity_id"] for e in entities
                if e["entity_id"].startswith(entity_prefix)
                and "on" in e["entity_id"]
                and e["entity_id"].split('.')[0] == "switch"
            ]

            if candidate_entities:
                matched_entity = candidate_entities[0]  # 取第一个匹配实体
                self.logger.info(f"精准匹配到控制实体: {matched_entity}")
            else:
                self.logger.error(f"未找到符合条件的实体（前缀: {entity_prefix}，含'on'关键词）")
                return

        except Exception as e:
            self.logger.error(f"查询HA实体时异常: {e}")
            return

        # 2. 调用HA API执行控制
        try:
            service_url = f"{self.config['ha_url']}/api/services/switch/turn_{ha_state}"
            response = requests.post(
                service_url,
                headers=self.ha_headers,
                json={"entity_id": matched_entity},
                timeout=10
            )

            # 输出详细响应便于排查
            self.logger.info(
                f"HA API调用详情: URL={service_url}, 实体={matched_entity}, "
                f"状态码={response.status_code}, 响应={response.text}"
            )

            if response.status_code == 200:
                self.logger.info(f"成功控制{device_type}实体 {matched_entity} 为 {ha_state}")
                self._report_state(device, target_state)  # 控制后上报状态
            else:
                self.logger.error(f"控制失败，HA返回非200状态码: {response.status_code}")

        except Exception as e:
            self.logger.error(f"调用HA API控制设备时异常: {e}")

    def _report_state(self, device: dict, state: int):
        """控制后主动上报状态（确保平台同步）"""
        payload = {
            "id": int(time.time() * 1000),
            "version": "1.0",
            "params": {"state": state}
        }
        self.publish(device, payload)
    
    def publish(self, device: Dict[str, Any], payload: Dict[str, Any]) -> bool:
        """发布设备数据到平台"""
        if not self.connected:
            self.logger.warning("MQTT未连接，无法发布数据")
            return False
        
        # 上报Topic格式
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
    
