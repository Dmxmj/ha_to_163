# 基础镜像：使用Home Assistant官方基础镜像，通过BUILD_ARCH参数适配架构
ARG BUILD_FROM
FROM ${BUILD_FROM}

# 声明构建参数（由构建流程传入）
ARG BUILD_ARCH
ARG BUILD_VERSION
ARG BUILD_DATE

# 标签：用于Home Assistant识别镜像信息
LABEL \
  io.hass.name="HA to 163 Gateway" \
  io.hass.description="将Home Assistant数据推送到网易IoT平台" \
  io.hass.arch="${BUILD_ARCH}" \
  io.hass.version="${BUILD_VERSION}" \
  io.hass.type="addon" \
  org.opencontainers.image.created="${BUILD_DATE}" \
  org.opencontainers.image.version="${BUILD_VERSION}" \
  org.opencontainers.image.authors="Your Name"

# 安装依赖（跨架构通用，Python库自动适配）
RUN \
  apk add --no-cache \
    python3 \
    py3-pip && \
  pip3 install --no-cache-dir \
    paho-mqtt==1.6.1 \
    requests==2.31.0 \
    ntplib==0.4.0

# 复制插件代码到镜像中
COPY run.sh /
COPY requirements.txt /

# 赋予执行权限
RUN chmod a+x /run.sh

# 启动命令
CMD ["/run.sh"]
