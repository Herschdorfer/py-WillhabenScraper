import re
import time
import configparser
import argparse
import signal
import logging
from urllib.parse import urlsplit
from collections import Counter
from math import ceil
from statistics import mean, median
import influxdb_client
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from influxdb_client import Point
from influxdb_client.client.write_api import SYNCHRONOUS

HTTP_TIMEOUT = 30
LOGGER = logging.getLogger(__name__)


def source_host(url):
    try:
        return urlsplit(url).hostname or "unknown host"
    except ValueError:
        return "invalid URL"


class ScrapingObject:
    """
    Represents an object used for web scraping.

    Attributes:
        url (str): The URL of the webpage to scrape.
        regex (str): The regular expression pattern used to extract data from the webpage.
        measurement (str): The unit of measurement for the extracted data.
        operation (str): The operation to perform on the extracted data (e.g., average, min).
    """

    def __init__(self, url, regex, measurement, operation):
        self.url = url
        self.regex = regex
        self.measurement = measurement
        self.operation = operation


def load_search(section, settings):
    for key in ("url", "regex", "measurement"):
        if not settings.get(key, "").strip():
            raise ValueError(f"Missing [{section}] {key}")
    operation = settings.get("operation", "")
    if operation not in ("", "min", "max", "average", "median", "mode"):
        raise ValueError(f"Unsupported operation in [{section}]: {operation}")
    try:
        pattern = re.compile(settings["regex"])
    except re.error as error:
        raise ValueError(f"Invalid regex in [{section}]: {error}") from error
    if pattern.groups > 1:
        raise ValueError(f"Regex in [{section}] must have at most one capture group")
    return ScrapingObject(
        settings["url"], settings["regex"], settings["measurement"], operation
    )


def load_config(paths):
    config = configparser.ConfigParser(interpolation=None)
    loaded = config.read(paths, encoding="utf-8")
    missing = [path for path in paths if path not in loaded]
    if missing:
        raise ValueError(f"Cannot read config files: {', '.join(missing)}")

    if not config.has_section("InfluxDB"):
        raise ValueError("Missing [InfluxDB] section")
    for key in ("token", "org", "server", "bucket"):
        if not config["InfluxDB"].get(key, "").strip():
            raise ValueError(f"Missing [InfluxDB] {key}")

    interval = config.getint("Scraper", "interval", fallback=3600)
    if interval <= 0:
        raise ValueError("[Scraper] interval must be positive")

    objects = [
        load_search(section, config[section])
        for section in config.sections()
        if section.isdigit()
    ]
    if not objects:
        raise ValueError("At least one numbered search section is required")
    return config, objects, interval


def create_http_session():
    session = requests.Session()
    retry = Retry(
        total=2,
        connect=2,
        read=0,
        status=2,
        backoff_factor=1,
        backoff_max=4,
        status_forcelist=(429, 502, 503, 504),
        allowed_methods=frozenset({"GET"}),
        respect_retry_after_header=False,
        raise_on_status=False,
    )
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    return session


def get_data(url, regex, operation, http_client=None):
    """
    Retrieves data from a given URL using a regular expression.

    Args:
        url (str): The URL to scrape data from.
        regex (str): The regular expression pattern to search for in the scraped data.
        operation (str): The aggregation operation to perform on integer matches.
        http_client: Optional requests-compatible object with a get method.
            Defaults to the requests module when omitted.

    Returns:
        str: The integer aggregate, or an empty string when there is no usable data.
    """
    transport = http_client if http_client is not None else requests
    try:
        with transport.get(
            url,
            headers={
                "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/137.0.0.0 Safari/537.36",
            },
            timeout=HTTP_TIMEOUT,
        ) as response:
            if response.status_code != 200:
                LOGGER.warning(
                    "HTTP %s from %s", response.status_code, source_host(url)
                )
                return ""
            content = response.content.decode("utf-8")
    except (requests.RequestException, UnicodeError) as error:
        LOGGER.warning(
            "Fetch failed for %s (%s)", source_host(url), type(error).__name__
        )
        return ""

    matches = re.findall(regex, content)
    LOGGER.debug(
        "Matched %d values from %s using %r", len(matches), source_host(url), operation
    )
    if not matches:
        return ""
    try:
        values = [int(match) for match in matches]
    except (TypeError, ValueError):
        LOGGER.warning("Non-integer matches from %s", source_host(url))
        return ""
    if operation == "average":
        value = int(mean(values))
    elif operation == "median":
        value = int(median(values))
    elif operation == "mode":
        bucket_size = 50
        frequency = Counter(value // bucket_size * bucket_size for value in values)
        value = frequency.most_common(1)[0][0] + bucket_size
    elif operation == "max":
        value = max(values)
    else:
        value = min(values)
    data = str(value)
    LOGGER.debug("Aggregate from %s: %s", source_host(url), data)

    return data


def write_data(data, measurement, config):
    """
    Writes data to InfluxDB.

    Args:
        data: The data to be written.
        measurement: The measurement name for the data.

    Returns:
        None
    """
    token = config["InfluxDB"]["token"]
    org = config["InfluxDB"]["org"]
    server = config["InfluxDB"]["server"]
    bucket = config["InfluxDB"]["bucket"]

    with influxdb_client.InfluxDBClient(url=server, token=token, org=org) as client:
        write_api = client.write_api(write_options=SYNCHRONOUS)
        point = Point(measurement).field("value", int(data))
        write_api.write(bucket=bucket, record=point)


def run_scraper(config, objects, interval, http_client=None):
    next_reading = time.monotonic()
    try:
        while True:
            for scraping_object in objects:
                try:
                    data = get_data(
                        scraping_object.url,
                        scraping_object.regex,
                        scraping_object.operation,
                        http_client=http_client,
                    )

                    if data:
                        write_data(data, scraping_object.measurement, config)
                        LOGGER.info(
                            "Wrote measurement %r: %s",
                            scraping_object.measurement,
                            data,
                        )
                except Exception as err:
                    LOGGER.warning(
                        "Search %r failed (%s)",
                        scraping_object.measurement,
                        type(err).__name__,
                    )

            next_reading += interval
            now = time.monotonic()
            if now > next_reading:
                missed_intervals = ceil((now - next_reading) / interval)
                next_reading += missed_intervals * interval
            time.sleep(next_reading - now)
    except KeyboardInterrupt:
        pass


def handle_termination(_signum, _frame):
    raise KeyboardInterrupt


def main(argv=None):
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s"
    )
    parser = argparse.ArgumentParser(description="Simple scraper for willHaben data.")
    parser.add_argument(
        "-c", "--conf", required=True, action="append", help="config file"
    )
    args = parser.parse_args(argv)
    try:
        config, objects, interval = load_config(args.conf)
    except (OSError, ValueError, configparser.Error) as error:
        parser.error(str(error))
    previous_handler = signal.getsignal(signal.SIGTERM)
    try:
        signal.signal(signal.SIGTERM, handle_termination)
        with create_http_session() as http_client:
            run_scraper(config, objects, interval, http_client)
    except KeyboardInterrupt:
        pass
    finally:
        signal.signal(signal.SIGTERM, previous_handler)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
