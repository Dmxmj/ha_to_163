FROM alpine:3.18

# 安装Python和必要工具
RUN apk add --no-cache \
    python3 \
    py3-pip \
    gcc \
    python3-dev \
    musl-dev && \
    rm -rf /var/cache/apk/*

WORKDIR /app

# 复制依赖文件
COPY requirements.txt .

# 使用多源安装（优先国内源，提高成功率）
RUN pip3 install --no-cache-dir \
    --index-url https://pypi.tuna.tsinghua.edu.cn/simple \
    --extra-index-url https://pypi.org/simple \
    paho-mqtt>=1.6.1 \
    requests>=2.31.0 \
    pyyaml>=6.0.1 \
    ntplib>=0.3.0

# 验证关键依赖安装
RUN pip3 list | grep -E "requests|paho-mqtt|pyyaml|ntplib"

# 复制应用文件
COPY . .

# 启动命令（直接运行Python主程序）
CMD ["python3", "/app/main.py"]
    
