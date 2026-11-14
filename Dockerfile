FROM python:3.12-slim
# deno: runtime JS que o yt-dlp exige para ler os formatos do YouTube
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg ca-certificates curl unzip && rm -rf /var/lib/apt/lists/* \
 && curl -fsSL -o /tmp/deno.zip https://github.com/denoland/deno/releases/latest/download/deno-x86_64-unknown-linux-gnu.zip \
 && unzip -q /tmp/deno.zip -d /usr/local/bin && rm /tmp/deno.zip && deno --version \
 && useradd --system --uid 10001 --no-create-home app && mkdir /data && chown 10001:10001 /data
WORKDIR /srv
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app ./app
COPY static ./static
ARG GIT_COMMIT=dev
ENV GIT_COMMIT=${GIT_COMMIT} PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 CORTAI_DADOS=/data DENO_DIR=/tmp/deno XDG_CACHE_HOME=/tmp/cache HOME=/tmp
USER 10001
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips", "*"]
