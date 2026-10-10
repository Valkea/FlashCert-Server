# flashcert-share: read-only web sharing for FlashCert courses.
FROM python:3.12-slim AS build
WORKDIR /src
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir --prefix=/install ".[postgres]"

FROM python:3.12-slim
LABEL org.opencontainers.image.title="flashcert-share" \
      org.opencontainers.image.description="Read-only web sharing for FlashCert courses" \
      org.opencontainers.image.source="https://github.com/Valkea/FlashCert"
COPY --from=build /install /usr/local
RUN useradd --system --uid 10001 --home-dir /data --create-home flashcert
USER flashcert
ENV PYTHONUNBUFFERED=1 \
    FLASHCERT_SHARE_DATA_DIR=/data
VOLUME /data
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/healthz', timeout=4)"
CMD ["flashcert-share", "serve", "--host", "0.0.0.0", "--port", "8080"]
