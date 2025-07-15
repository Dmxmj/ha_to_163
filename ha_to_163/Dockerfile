# 基于Python 3.9官方镜像
FROM python:3.9-slim

# 设置工作目录
WORKDIR /app

# 复制依赖文件并安装
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 复制应用代码
COPY . .

# 设置执行权限
RUN chmod +x run.sh

# 启动脚本
CMD ["./run.sh"]
