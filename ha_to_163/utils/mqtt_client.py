def _control_switch_or_socket(self, device: dict, target_state: int, 
                             product_key: str, device_name: str, command_id: int):
    """控制设备并回复结果（优化实体匹配逻辑）"""
    ha_state = "on" if target_state == 1 else "off"
    device_type = device["type"]
    device_id = device["id"]
    entity_prefix = device["ha_entity_prefix"]  # 从配置获取实体前缀
    self.logger.info(f"控制{device_type} {device_id} 状态为: {ha_state}，使用前缀: {entity_prefix}")

    # 1. 匹配实体（优化逻辑）
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
                success=False, error_msg="查询设备实体失败"
            )
            return

        entities = resp.json()
        self.logger.debug(f"HA返回实体总数: {len(entities)}，开始筛选包含前缀'{entity_prefix}'的实体")

        # 筛选条件：实体ID包含配置的前缀（不限制类型，增加灵活性）
        candidate_entities = [
            e["entity_id"] for e in entities
            if entity_prefix in e["entity_id"]  # 仅通过前缀匹配，不限制实体类型
        ]

        self.logger.debug(f"前缀匹配到的候选实体: {candidate_entities}")

        # 进一步筛选：优先包含"on"/"state"/设备ID等关键词的实体（提高准确性）
        keywords = ["on", "state", device_id.split('_')[-1]]  # 动态关键词
        priority_entities = []
        for entity in candidate_entities:
            for kw in keywords:
                if kw in entity.lower():
                    priority_entities.append(entity)
                    break  # 匹配到一个关键词即可

        # 确定最终实体（有优先级实体则取第一个，否则取候选实体第一个）
        if priority_entities:
            matched_entity = priority_entities[0]
            self.logger.info(f"优先级匹配到控制实体: {matched_entity}（关键词匹配）")
        elif candidate_entities:
            matched_entity = candidate_entities[0]
            self.logger.info(f"前缀匹配到控制实体: {matched_entity}（无关键词匹配）")
        else:
            self.logger.error(f"未找到包含前缀'{entity_prefix}'的实体，无法控制")
            self._send_control_reply(
                product_key, device_name, command_id,
                success=False, error_msg=f"未找到前缀为'{entity_prefix}'的实体"
            )
            return

    except Exception as e:
        self.logger.error(f"查询HA实体时异常: {e}")
        self._send_control_reply(
            product_key, device_name, command_id,
            success=False, error_msg=f"查询实体异常: {str(e)}"
        )
        return

    # 2. 调用HA API控制（保持不变）
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
            actual_state = 1 if ha_state == "on" else 0
            self._send_control_reply(
                product_key, device_name, command_id,
                success=True, 
                result_data={"state": actual_state}
            )
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
