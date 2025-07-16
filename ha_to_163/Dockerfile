FROM alpine:3.18

RUN apk add --no-cache python3 py3-pip

WORKDIR /app

RUN touch run.sh && chmod +x run.sh

COPY . .

RUN chmod +x /app/run.sh

CMD ["/app/run.sh"]
