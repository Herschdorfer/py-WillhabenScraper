# py-WillhabenScraper

This is a simple scrapper for the Austrian WillHaben.at website.

It will access a configured URL and then use a regular expression to retrieve the desired data.
The result will be written to a configured Influx database for later use, eg. a Grafana visualization.

# Docker Releases

Publishing a GitHub release automatically builds the Docker image and pushes it to
GitHub Container Registry. Draft releases do not trigger a build.

Images are available as `ghcr.io/herschdorfer/py-willhabenscraper:<release-tag>`.
The `latest` tag tracks the most recently published non-prerelease release.
The workflow uses GitHub's built-in token; no registry secrets are required.

```sh
docker run --rm \
	-v /absolute/path/to/config.ini:/config/config.ini:ro \
	ghcr.io/herschdorfer/py-willhabenscraper:latest
```

The mounted configuration must be readable by the container's `nobody` user.
To allow unauthenticated pulls, set the package visibility to public in GitHub's
package settings after the first successful release build.

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

CI runs on pushes and pull requests using Python 3.13, matching the Docker image.
It runs the same hooks, checks dependency compatibility, runs the tests, then
builds the Docker image and smoke-tests its command-line entry point.
Release publishing runs these checks first and only pushes an image if they pass.

Dependabot checks runtime and development Python dependencies, GitHub Actions,
and the Docker base image weekly. Minor and patch updates are grouped per
ecosystem; major updates remain separate pull requests and run through CI.

# Configuration

Configuration | Explanaton                           | Example
--------------|--------------------------------------|--------
name          | Simple name, not used for processing | -
regex         | Regular Expression for lookup        | "numberOfItems":(\d+)
url           | URL to access                        | https://www.willhaben.at/
bucket        | Bucket in Influx to save the data    | MetaData_HouseData

# Audit

Ranked improvements from the code audit:

1. **Implemented: predictable startup.** Imports have no CLI side effects, the
	synchronous loop no longer uses `asyncio.run`, multiple configuration files
	merge correctly, and invalid settings fail before scraping begins.
2. **Implemented: correct aggregation.** Missing or invalid data is skipped
	instead of written as zero; legitimate zero and negative values are preserved.
	Standard-library statistics replace the hand-written aggregation loops.
3. **Implemented: bounded HTTP requests.** Requests have a 30-second timeout,
	responses are closed, and network and decoding failures skip the reading.
4. **Deferred: monotonic scheduling.** Wall-clock changes and long-running cycles
	can cause catch-up bursts; use a monotonic clock and skip missed intervals.
5. **Deferred: connection reuse.** Each measurement creates an InfluxDB client;
	reuse a client for each run to reduce connection overhead.
6. **Deferred: dependency cleanup.** Several HTML parsing packages are unused;
	remove unnecessary dependencies and audit the remaining pins for advisories.

The tests cover aggregation, configuration, HTTP failures and cleanup, InfluxDB
write arguments, scheduler error isolation, and CLI behavior. They do not verify
live WillHaben responses or a live InfluxDB instance.
