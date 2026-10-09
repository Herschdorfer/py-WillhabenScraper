FROM python:3.13-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY py-WillhabenScraper.py .

# The config (InfluxDB token, searches) is mounted, never baked into the image.
USER nobody
ENTRYPOINT ["python3", "-u", "py-WillhabenScraper.py"]
CMD ["-c", "/config/config.ini"]
