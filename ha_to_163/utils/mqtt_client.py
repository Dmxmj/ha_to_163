import paho.mqtt.client as mqtt
import logging
import time
import hmac
import hashlib
import json
from typing import Dict, Any

class MQTTClient:
    """网易IoT平台MQTT客户端（修正上报Topic格式）"""
    
    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.logger = logging.getLogger("mqtt_client")
        self.client: mqtt.Client = None
        self.connected = False
        self.last_time_sync = 0  # 用于时间同步
        self.reconnect_delay = 1  # 重连延迟（秒）
    
    def _init_mqtt_client(self):
        """初始化MQTT客户端（复用提供的密码生成逻辑）"""
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
        """生成MQTT密码（完全复用提供的代码）"""
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
        """同步NTP时间（复用提供的代码）"""
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
        """连接回调（订阅子设备控制Topic）"""
        if rc == 0:
            self.connected = True
            self.reconnect_delay = 1  # 重置重连延迟
            self.logger.info(f"MQTT连接成功（返回码: {rc}）")
            
            # 订阅子设备的控制Topic（格式：sys/{子设备productkey}/{子设备devicename}/thing/service/property/set）
            for device in self.config.get("sub_devices", []):
                if not device.get("enabled", True):
                    continue
                set_topic = f"sys/{device['product_key']}/{device['device_name']}/thing/service/property/set"
                client.subscribe(set_topic, qos=1)
                self.logger.info(f"订阅控制Topic: {set_topic}")
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
        """安排重连（指数退避）"""
        if self.reconnect_delay < 60:  # 最大延迟60秒
            self.reconnect_delay *= 2
        self.logger.info(f"{self.reconnect_delay}秒后尝试重连...")
        time.sleep(self.reconnect_delay)
        self.connect()
    
    def _on_message(self, client: mqtt.Client, userdata: Any, msg: mqtt.MQTTMessage):
        """处理收到的消息"""
        try:
            payload = json.loads(msg.payload.decode())
            self.logger.info(f"收到消息: {msg.topic} → {json.dumps(payload, indent=2)}")
        except Exception as e:
            self.logger.error(f"解析消息失败: {e}，原始消息: {msg.payload.decode()}")
    
    def publish(self, device: Dict[str, Any], payload: Dict[str, Any]) -> bool:
        """发布设备数据（修正上报Topic格式）"""
        if not self.connected:
            self.logger.warning("MQTT未连接，无法发布数据")
            return False
        
        # 上报Topic格式：sys/{子设备productkey}/{子设备devicename}/event/property/post
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
