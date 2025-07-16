FROM alpine:3.18

# 安装必要工具并更新源
RUN apk update && \
    apk add --no-cache python3 py3-pip && \
    rm -rf /var/cache/apk/*  # 清理缓存

WORKDIR /app

# 单独复制依赖文件
COPY requirements.txt .

# 强制安装依赖（指定镜像源加速）
RUN pip3 install --no-cache-dir -i https://pypi.tuna.tsinghua.edu.cn/simple requests==2.31.0 paho-mqtt==1.6.1 ntplib==0.4.0

# 验证安装
RUN pip3 show requests && \
    pip3 show paho-mqtt && \
    pip3 show ntplib

# 复制应用文件
COPY . .
RUN chmod +x /app/run.sh

CMD ["/app/run.sh"]
