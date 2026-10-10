FROM python:3.13-slim

LABEL org.opencontainers.image.title="web-metrics-scraper" \
	org.opencontainers.image.description="Configurable HTTP metrics scraper with InfluxDB output" \
	org.opencontainers.image.source="https://github.com/Herschdorfer/web-metrics-scraper" \
	org.opencontainers.image.documentation="https://github.com/Herschdorfer/web-metrics-scraper/blob/master/README.md"

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY web_metrics_scraper.py .

# The config (InfluxDB token, searches) is mounted, never baked into the image.
USER nobody
ENTRYPOINT ["python3", "-u", "web_metrics_scraper.py"]
CMD ["-c", "/config/config.ini"]
