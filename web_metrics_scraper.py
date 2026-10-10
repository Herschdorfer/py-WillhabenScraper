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
MAX_RESPONSE_BYTES = 10 * 1024 * 1024
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/137.0.0.0 Safari/537.36"
)
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

    def __init__(
        self,
        url,
        regex,
        measurement,
        operation,
        user_agent=DEFAULT_USER_AGENT,
        min_value=None,
        max_value=None,
    ):
        self.url = url
        self.regex = regex
        self.measurement = measurement
        self.operation = operation
        self.user_agent = user_agent
        self.min_value = min_value
        self.max_value = max_value


def validate_http_url(url, label):
    try:
        parsed = urlsplit(url)
    except ValueError as error:
        raise ValueError(f"{label} is malformed: {error}") from error
    try:
        port = parsed.port
    except ValueError as error:
        raise ValueError(f"{label} has an invalid port: {error}") from error
    if parsed.scheme not in ("http", "https"):
        raise ValueError(f"{label} must use HTTP or HTTPS")
    if not parsed.hostname:
        raise ValueError(f"{label} must include a hostname")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError(f"{label} must not include credentials")
    if port == 0:
        raise ValueError(f"{label} has an invalid port")
    if any(character.isspace() for character in url):
        raise ValueError(f"{label} must not contain whitespace")
    if re.search(r"%(?![0-9A-Fa-f]{2})", url):
        raise ValueError(f"{label} contains invalid percent-encoding")


def validate_user_agent(user_agent, label):
    user_agent = user_agent.strip()
    if not user_agent:
        raise ValueError(f"{label} must not be empty")
    if "\r" in user_agent or "\n" in user_agent:
        raise ValueError(f"{label} must not contain line breaks")
    return user_agent


def load_optional_integer(settings, section, key):
    value = settings.get(key)
    if value is None:
        return None
    try:
        return int(value)
    except ValueError as error:
        raise ValueError(f"[{section}] {key} must be an integer") from error


def load_search(section, settings, default_user_agent=DEFAULT_USER_AGENT):
    for key in ("url", "regex", "measurement"):
        if not settings.get(key, "").strip():
            raise ValueError(f"Missing [{section}] {key}")
    validate_http_url(settings["url"], f"[{section}] url")
    operation = settings.get("operation", "")
    if operation not in ("", "min", "max", "average", "median", "mode"):
        raise ValueError(f"Unsupported operation in [{section}]: {operation}")
    try:
        pattern = re.compile(settings["regex"])
    except re.error as error:
        raise ValueError(f"Invalid regex in [{section}]: {error}") from error
    if pattern.groups > 1:
        raise ValueError(f"Regex in [{section}] must have at most one capture group")
    user_agent = validate_user_agent(
        settings.get("user_agent", default_user_agent), f"[{section}] user_agent"
    )
    min_value = load_optional_integer(settings, section, "min_value")
    max_value = load_optional_integer(settings, section, "max_value")
    if min_value is not None and max_value is not None and min_value > max_value:
        raise ValueError(f"[{section}] min_value must not exceed max_value")
    return ScrapingObject(
        settings["url"],
        settings["regex"],
        settings["measurement"],
        operation,
        user_agent,
        min_value,
        max_value,
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
    validate_http_url(config["InfluxDB"]["server"], "[InfluxDB] server")

    interval = config.getint("Scraper", "interval", fallback=3600)
    if interval <= 0:
        raise ValueError("[Scraper] interval must be positive")
    default_user_agent = validate_user_agent(
        config.get("Scraper", "user_agent", fallback=DEFAULT_USER_AGENT),
        "[Scraper] user_agent",
    )

    objects = [
        load_search(section, config[section], default_user_agent)
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


def read_response(response):
    encoding = response.headers.get("Content-Encoding", "identity").strip().lower()
    if encoding not in ("", "identity"):
        raise requests.RequestException("Encoded responses are not supported")
    content = bytearray()
    for chunk in response.iter_content(chunk_size=65536):
        if len(content) + len(chunk) > MAX_RESPONSE_BYTES:
            raise requests.RequestException("Response size limit exceeded")
        content.extend(chunk)
    return content.decode("utf-8")


def get_data(
    url,
    regex,
    operation,
    http_client=None,
    user_agent=DEFAULT_USER_AGENT,
    response_cache=None,
):
    """
    Retrieves data from a given URL using a regular expression.

    Args:
        url (str): The URL to scrape data from.
        regex (str): The regular expression pattern to search for in the scraped data.
        operation (str): The aggregation operation to perform on integer matches.
        http_client: Optional requests-compatible object with a get method.
            Defaults to the requests module when omitted.
        user_agent: User-Agent header value for the request.
        response_cache: Optional per-cycle cache of response bodies by URL and
            User-Agent.

    Returns:
        str: The integer aggregate, or an empty string when there is no usable data.
    """
    cache_key = (url, user_agent)
    if response_cache is not None and cache_key in response_cache:
        content = response_cache[cache_key]
    else:
        transport = http_client if http_client is not None else requests
        content = None
        try:
            with transport.get(
                url,
                headers={
                    "Accept-Encoding": "identity",
                    "User-Agent": user_agent,
                },
                timeout=HTTP_TIMEOUT,
                stream=True,
            ) as response:
                if response.status_code != 200:
                    LOGGER.warning(
                        "HTTP %s from %s", response.status_code, source_host(url)
                    )
                else:
                    content = read_response(response)
        except (requests.RequestException, UnicodeError) as error:
            LOGGER.warning(
                "Fetch failed for %s (%s)", source_host(url), type(error).__name__
            )
        if response_cache is not None:
            response_cache[cache_key] = content
    if content is None:
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


def create_influx_client(config):
    settings = config["InfluxDB"]
    return influxdb_client.InfluxDBClient(
        url=settings["server"], token=settings["token"], org=settings["org"]
    )


def write_data(data, measurement, config, write_api=None):
    """
    Writes data to InfluxDB.

    Args:
        data: The data to be written.
        measurement: The measurement name for the data.
        config: The loaded InfluxDB connection and bucket settings.
        write_api: Optional shared synchronous writer. A temporary client is used
            when omitted.

    Returns:
        None
    """
    bucket = config["InfluxDB"]["bucket"]
    point = Point(measurement).field("value", int(data))
    if write_api is not None:
        write_api.write(bucket=bucket, record=point)
        return
    with create_influx_client(config) as client:
        client.write_api(write_options=SYNCHRONOUS).write(bucket=bucket, record=point)


def run_scraper(config, objects, interval, http_client=None, write_api=None):
    next_reading = time.monotonic()
    request_counts = Counter((obj.url, obj.user_agent) for obj in objects)
    try:
        while True:
            response_cache = {}
            remaining_requests = request_counts.copy()
            for scraping_object in objects:
                request_key = (scraping_object.url, scraping_object.user_agent)
                try:
                    data = get_data(
                        scraping_object.url,
                        scraping_object.regex,
                        scraping_object.operation,
                        http_client=http_client,
                        user_agent=scraping_object.user_agent,
                        response_cache=(
                            response_cache if request_counts[request_key] > 1 else None
                        ),
                    )

                    if data:
                        value = int(data)
                        below_minimum = (
                            scraping_object.min_value is not None
                            and value < scraping_object.min_value
                        )
                        above_maximum = (
                            scraping_object.max_value is not None
                            and value > scraping_object.max_value
                        )
                        if below_minimum or above_maximum:
                            LOGGER.warning(
                                "Value %s for measurement %r is outside configured bounds",
                                value,
                                scraping_object.measurement,
                            )
                        else:
                            write_data(
                                data, scraping_object.measurement, config, write_api
                            )
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
                finally:
                    remaining_requests[request_key] -= 1
                    if remaining_requests[request_key] == 0:
                        response_cache.pop(request_key, None)

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
    parser = argparse.ArgumentParser(
        prog="web-metrics-scraper",
        description="Extract numeric HTTP metrics and write them to InfluxDB.",
    )
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
        with (
            create_http_session() as http_client,
            create_influx_client(config) as influx_client,
            influx_client.write_api(write_options=SYNCHRONOUS) as write_api,
        ):
            run_scraper(config, objects, interval, http_client, write_api)
    except KeyboardInterrupt:
        pass
    finally:
        signal.signal(signal.SIGTERM, previous_handler)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
