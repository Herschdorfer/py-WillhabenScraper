# web-metrics-scraper

A configurable HTTP-to-InfluxDB scraper.
Extract integer values from web pages or HTTP responses with regular expressions, aggregate them, and store metrics in InfluxDB 2.x.

## Container Quick Start

Copy [.config.example](.config.example) to `config.ini` and set your InfluxDB connection, token, bucket, source URLs, and extraction patterns.
Create the file before starting the container, then run the following command.

```sh
docker run -d --name web-metrics-scraper \
   --restart unless-stopped \
   --user "$(id -u):$(id -g)" \
   --mount type=bind,src="$(pwd)/config.ini",dst=/config/config.ini,readonly \
   ghcr.io/herschdorfer/web-metrics-scraper:latest

docker logs -f web-metrics-scraper
```

The host UID and GID let the container read your private configuration file without making the token readable by everyone.
The default container user is `nobody`.
The configuration is mounted read-only and is not included in the image.

No inbound ports are needed.
Sources and InfluxDB must be reachable from the container.
`localhost` inside a container is not your host.
Use an InfluxDB address that the container can reach.

### Docker Compose

```yaml
services:
   scraper:
      image: ghcr.io/herschdorfer/web-metrics-scraper:latest
      restart: unless-stopped
      volumes:
         - ./config.ini:/config/config.ini:ro
```

This Compose example uses `nobody`.
Set `user` to a numeric UID and GID if needed to read the configuration.
View logs with `docker compose logs -f scraper`.
You can use an existing InfluxDB instance without adding another container.

# Docker Releases

Publishing a GitHub release automatically builds the Docker image and pushes it to GitHub Container Registry.
Draft releases do not trigger a build.

Images are available as `ghcr.io/herschdorfer/web-metrics-scraper:<release-tag>`.
Use an explicit release tag instead of `latest` for reproducible deployments.
The `latest` tag tracks the most recently published non-prerelease release.
The workflow uses GitHub's built-in token.
No registry secrets are required.

```sh
docker run --rm \
	-v /absolute/path/to/config.ini:/config/config.ini:ro \
   ghcr.io/herschdorfer/web-metrics-scraper:latest
```

The mounted configuration must be readable by the container's `nobody` user.

## Running From Source

```sh
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements.txt
python web_metrics_scraper.py -c config.ini
```

Edit the installation and configuration paths in [web-metrics-scraper.service](web-metrics-scraper.service) before installing it.

# Development

```sh
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements-dev.txt
pre-commit install
pre-commit run --all-files
python -m unittest discover -s tests -v
```

Pre-commit checks Python linting and formatting with Ruff, YAML syntax, workflow syntax with actionlint, whitespace, merge conflicts, large files, and private keys.
Bandit scans Python code for security issues, and detect-secrets scans files for credentials and high-entropy secrets.
These scans also run in CI.
Tests mock HTTP requests and InfluxDB writes.
They do not need live services or credentials.
The tests cover aggregation, configuration, HTTP failures and cleanup, InfluxDB write arguments, scheduler error isolation, and CLI behavior.
They do not verify live website responses or a live InfluxDB instance.

CI runs on pushes and pull requests using Python 3.13, matching the Docker image.
It runs the same hooks, checks dependency compatibility, runs the tests, then builds the Docker image and smoke-tests its command-line entry point.
Release publishing runs these checks first and only pushes an image if they pass.
CI also audits the runtime dependency tree with pip-audit for known security advisories.

Dependabot checks runtime and development Python dependencies, GitHub Actions, and the Docker base image weekly.
Minor and patch updates are grouped per ecosystem.
Major updates use separate pull requests and run through CI.

# Configuration

```ini
[InfluxDB]
server = http://influxdb.example:8086
token = replace-with-your-token
org = example-org
bucket = example-bucket

[Scraper]
interval = 3600
user_agent = Mozilla/5.0

[1]
url = https://example.org/metrics
regex = "count":\s*(\d+)
measurement = website_count
operation = min
# Optional per-search overrides:
# user_agent = CustomScraper/1.0
# min_value = 0
# max_value = 1000000
```

Each numbered section defines a source.
Required keys are `url`, `regex`, and `measurement`.
The default `operation` is `min`.
The regex must produce integer values and may contain zero or one capture group.
`name` is optional descriptive text, not used for processing.
The bucket belongs in `[InfluxDB]`, not in a numbered search section.

| Operation | Result |
| --- | --- |
| `min` | Smallest integer (default) |
| `max` | Largest integer |
| `average` | Arithmetic mean, truncated toward zero |
| `median` | Median, truncated toward zero |
| `mode` | Upper edge of the most common 50-unit bucket. The first bucket wins ties. |

The interval is a positive number of seconds, defaulting to 3600.
The global `user_agent` is optional and defaults to the scraper's existing browser-style value.
A numbered section can override it with its own `user_agent`.
Responses with the same URL and User-Agent are fetched once per scraping cycle and reused by matching searches.
Optional `min_value` and `max_value` bounds are inclusive integers applied to the aggregated result.
Values outside the configured bounds are skipped with a warning.
Multiple `-c` arguments merge configuration files in order.
Later files override earlier ones.
URLs must be absolute HTTP(S) addresses without inline credentials and with valid percent escapes.
Configuration values are not interpolated, so write URL percent escapes once, such as `%20`, not doubled as `%%20`.
Encode a literal percent sign in a URL as `%25`.
Use HTTPS when available.
HTTP is supported for local services.
Keep InfluxDB tokens in private configuration files.

Missing or invalid matches skip the reading.
Real zeros are stored normally.
HTTP requests use a 30-second timeout and up to two transient GET retries with bounded backoff.
Identity-encoded response bodies are streamed and limited to 10 MiB.
Encoded responses are skipped before automatic decompression.
Missed intervals are skipped rather than replayed.
SIGTERM and Ctrl+C close pooled resources cleanly.
