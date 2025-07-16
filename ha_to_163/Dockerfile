# 使用Home Assistant官方基础镜像（正确的架构变量格式）
FROM ${BUILD_ARCH}/base:3.18

# 设置环境变量
ENV LANG C.UTF-8
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# 设置工作目录（必须为/app，Supervisor要求）
WORKDIR /app

# 复制依赖文件并安装Python包
COPY requirements.txt .
RUN pip3 install --no-cache-dir --upgrade pip \
    && pip3 install --no-cache-dir -r requirements.txt

# 复制Add-on所有文件（包括子目录）
COPY . .

# 赋予启动脚本执行权限
RUN chmod +x /app/run.sh

# 容器启动命令（固定执行启动脚本）
CMD ["/app/run.sh"]
 
