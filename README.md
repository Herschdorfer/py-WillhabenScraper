# web-metrics-scraper

A configurable HTTP-to-InfluxDB scraper. Extract integer values from web pages
or HTTP responses with regular expressions, aggregate them, and store metrics
in InfluxDB 2.x. WillHaben is an example source, not a restriction.

## Container Quick Start

Copy [.config.example](.config.example) to `config.ini` and set your InfluxDB
connection, token, bucket, source URLs, and extraction patterns. Create the file
before starting the container, then run:

```sh
docker run -d --name web-metrics-scraper \
   --restart unless-stopped \
   --user "$(id -u):$(id -g)" \
   --mount type=bind,src="$(pwd)/config.ini",dst=/config/config.ini,readonly \
   ghcr.io/herschdorfer/web-metrics-scraper:latest

docker logs -f web-metrics-scraper
```

The host UID/GID allows a private configuration file to remain readable without
making its token world-readable. The image otherwise defaults to non-root
`nobody`. Configuration is mounted read-only and never baked into the image.

No inbound ports are needed. Sources and InfluxDB must be reachable from the
container. `localhost` inside a container is not your host; replace the example
server address with your InfluxDB service's reachable URL.

### Docker Compose

```yaml
services:
   scraper:
      image: ghcr.io/herschdorfer/web-metrics-scraper:latest
      restart: unless-stopped
      volumes:
         - ./config.ini:/config/config.ini:ro
```

This Compose example uses `nobody`; set `user` to the appropriate numeric UID/GID
if needed for configuration permissions. View logs with
`docker compose logs -f scraper`. An existing InfluxDB instance can be used;
no additional database container is required.

# Docker Releases

Publishing a GitHub release automatically builds the Docker image and pushes it to
GitHub Container Registry. Draft releases do not trigger a build.

Images are available as `ghcr.io/herschdorfer/web-metrics-scraper:<release-tag>`.
Use an explicit release tag instead of `latest` for reproducible deployments.
The `latest` tag tracks the most recently published non-prerelease release.
The workflow uses GitHub's built-in token; no registry secrets are required.

```sh
docker run --rm \
	-v /absolute/path/to/config.ini:/config/config.ini:ro \
   ghcr.io/herschdorfer/web-metrics-scraper:latest
```

The mounted configuration must be readable by the container's `nobody` user.
Public GHCR packages support unauthenticated pulls; private packages require
registry authentication.

The [GHCR package page](https://github.com/Herschdorfer/web-metrics-scraper/pkgs/container/web-metrics-scraper)
renders this README from the repository's `master` branch. Images include a
description and source/documentation metadata.
This is a GHCR image; no Docker Hub publishing is configured.

## Running From Source

```sh
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements.txt
python web_metrics_scraper.py -c config.ini
```

Edit installation/configuration paths in
[web-metrics-scraper.service](web-metrics-scraper.service) before installing it.

# Development

```sh
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements-dev.txt
pre-commit install
pre-commit run --all-files
python -m unittest discover -s tests -v
```

Pre-commit checks Python linting and formatting with Ruff, YAML syntax, workflow
syntax with actionlint, whitespace, merge conflicts, large files, and private keys.
Bandit scans Python code for security issues, and detect-secrets scans files for
credentials and high-entropy secrets. These scans also run in CI.
Tests mock HTTP requests and InfluxDB writes; no live services or credentials are
needed.
The tests cover aggregation, configuration, HTTP failures and cleanup, InfluxDB
write arguments, scheduler error isolation, and CLI behavior. They do not verify
live website responses or a live InfluxDB instance.

CI runs on pushes and pull requests using Python 3.13, matching the Docker image.
It runs the same hooks, checks dependency compatibility, runs the tests, then
builds the Docker image and smoke-tests its command-line entry point.
Release publishing runs these checks first and only pushes an image if they pass.
CI also audits the runtime dependency tree with pip-audit for known security advisories.

Dependabot checks runtime and development Python dependencies, GitHub Actions,
and the Docker base image weekly. Minor and patch updates are grouped per
ecosystem; major updates remain separate pull requests and run through CI.

# Configuration

```ini
[InfluxDB]
server = http://influxdb.example:8086
token = replace-with-your-token
org = example-org
bucket = example-bucket

[Scraper]
interval = 3600

[1]
url = https://example.org/metrics
regex = "count":\s*(\d+)
measurement = website_count
operation = min
```

Each numbered section defines a source. Required keys are `url`, `regex`, and
`measurement`; `operation` defaults to `min`. The regex must produce integer
values and may contain zero or one capture group. `name` is optional descriptive
text, not used for processing. The bucket belongs in `[InfluxDB]`, not in a
numbered search section.

| Operation | Result |
| --- | --- |
| `min` | Smallest integer; also the default |
| `max` | Largest integer |
| `average` | Arithmetic mean, truncated toward zero |
| `median` | Median, truncated toward zero |
| `mode` | Upper edge of the most common 50-unit bucket; first bucket wins ties |

The interval is a positive number of seconds, defaulting to 3600. Multiple `-c`
arguments merge configuration files in order; later files override earlier ones.
URLs must be absolute HTTP(S) addresses without inline credentials and with
valid percent escapes. Prefer HTTPS; HTTP remains supported for local services.
Keep InfluxDB tokens in private configuration files.

Missing or invalid matches skip the reading instead of fabricating a zero;
real zeros are stored normally. HTTP requests use a 30-second timeout and up to
two transient GET retries with bounded backoff. Identity-encoded response bodies
are streamed and limited to 10 MiB; encoded responses are skipped before
automatic decompression. Missed intervals are skipped rather than replayed.
SIGTERM and Ctrl+C close pooled resources cleanly.
