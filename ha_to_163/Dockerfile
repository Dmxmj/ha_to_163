ARG BUILD_FROM
FROM $BUILD_FROM

# 设置工作目录
WORKDIR /app

# 安装系统依赖（确保pip正常工作）
RUN apk add --no-cache python3 py3-pip

# 复制依赖文件
COPY requirements.txt .

# 强制安装pyyaml和其他依赖
RUN pip3 install --upgrade pip && \
    pip3 install --no-cache-dir pyyaml>=6.0.1 && \
    pip3 install --no-cache-dir -r requirements.txt

# 复制项目所有文件
COPY . .

# 启动命令
CMD ["python3", "/app/main.py"]
