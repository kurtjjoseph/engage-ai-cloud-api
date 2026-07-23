FROM python:3.12-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
# Point imageio at the system ffmpeg installed below so it never downloads its
# own binary at runtime (set here as well as render.yaml so it holds regardless
# of blueprint sync).
ENV IMAGEIO_FFMPEG_EXE=/usr/bin/ffmpeg
# System ffmpeg (for video assembly) + a real font (for burned-in captions), so
# nothing has to be downloaded at runtime.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
