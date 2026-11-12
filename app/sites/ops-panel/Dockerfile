FROM python:3.12.13-alpine
WORKDIR /app
RUN apk add --no-cache wget curl util-linux openssl \
    && addgroup -S panel -g 1000 \
    && adduser -S panel -G panel -u 1000 -h /app
COPY app/ /app/
RUN chown -R panel:panel /app
USER panel
ENV PROMETHEUS_URL=http://prometheus:9090
ENV PORT=8080
EXPOSE 8080
CMD ["python3", "/app/server.py"]
