FROM homeassistant/aarch64-base:3.18

WORKDIR /app

COPY requirements.txt .
RUN pip3 install --no-cache-dir --upgrade pip \
    && pip3 install --no-cache-dir -r requirements.txt

COPY . .
RUN chmod +x run.sh

CMD ["./run.sh"]
