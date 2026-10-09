# Lekar на Railway: ffmpeg + python + шрифт; образцы и результаты на диске /data.
FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
RUN python3 -c "from lekar.core import ensure_fonts; ensure_fonts()"

ENV LEKAR_DATA=/data PYTHONUNBUFFERED=1 PORT=8080
EXPOSE 8080
CMD ["python3", "server/server.py"]
