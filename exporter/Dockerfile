FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY exporter.py host_sync.py config.json /app/

RUN mkdir -p /host-sync \
    && chown -R 1000:1000 /app /host-sync

USER 1000:1000

EXPOSE 6556

HEALTHCHECK --interval=30s --timeout=5s --retries=3 CMD ["python", "-c", "import socket; connection = socket.create_connection(('127.0.0.1', 6556), timeout=2); connection.sendall(b'__healthcheck__\\n'); assert connection.recv(3) == b'OK\\n'; connection.close()"]

CMD ["python", "/app/exporter.py"]
