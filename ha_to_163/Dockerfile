ARG BUILD_FROM=homeassistant/${TARGETARCH}-base:3.18
FROM ${BUILD_FROM}

WORKDIR /app

COPY requirements.txt .
RUN pip3 install --no-cache-dir --upgrade pip \
    && pip3 install --no-cache-dir -r requirements.txt

COPY . .
RUN chmod +x run.sh

CMD ["./run.sh"]
