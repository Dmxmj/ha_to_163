# 基于官方Home Assistant基础镜像
ARG BUILD_FROM=ghcr.io/home-assistant/base/aarch64:latest
FROM $BUILD_FROM

# 设置工作目录
WORKDIR /app

# 安装系统依赖
RUN apk add --no-cache \
    python3 \
    py3-pip \
    && pip3 install --upgrade pip

# 复制依赖文件并安装
COPY requirements.txt .
RUN pip3 install --no-cache-dir -r requirements.txt

# 单独确保pyyaml安装（解决依赖问题）
RUN pip3 install --no-cache-dir pyyaml>=6.0.1

# 复制项目文件
COPY . .

# 启动命令
CMD ["python3", "/app/main.py"]
