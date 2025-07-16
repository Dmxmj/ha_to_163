FROM alpine:3.18

# 安装Python和必要工具
RUN apk add --no-cache python3 py3-pip && \
    rm -rf /var/cache/apk/*

WORKDIR /app

# 复制依赖文件
COPY requirements.txt .

# 使用多源安装（优先官方源，失败自动切换）
RUN pip3 install --no-cache-dir \
    --extra-index-url https://pypi.tuna.tsinghua.edu.cn/simple \
    --extra-index-url https://pypi.org/simple \
    requests>=2.25.0 paho-mqtt>=1.5.0 ntplib>=0.3.0

# 验证安装
RUN pip3 list | grep -E "requests|paho-mqtt|ntplib"

# 复制应用文件
COPY . .
RUN chmod +x /app/run.sh

CMD ["/app/run.sh"]
