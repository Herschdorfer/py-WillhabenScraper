import asyncio
import re
import time
import configparser
import argparse
import influxdb_client

from influxdb_client import Point
from influxdb_client.client.write_api import SYNCHRONOUS

from urllib.request import urlopen, Request

# Data capture and upload interval in seconds. Every hour.
INTERVAL = 60

parser = argparse.ArgumentParser(description="Simple Scrapper for willHaben data.")
parser.add_argument(
    "-c", "--conf", required="true", action="append", help="config file"
)

args = parser.parse_args()

config = configparser.ConfigParser()
config.sections()

config.read(*args.conf)


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


objects = []

for key in config:
    if key.isdigit():
        objects.append(
            ScrapingObject(
                config[key]["url"],
                config[key]["regex"],
                config[key]["measurement"],
                config[key].get("operation", ""),
            )
        )


def get_data(url, regex, operation):
    """
    Retrieves data from a given URL using a regular expression.

    Args:
        url (str): The URL to scrape data from.
        regex (str): The regular expression pattern to search for in the scraped data.
        operation (str): The operation to perform on the extracted data currently only average or min.

    Returns:
        str: The extracted data from the URL, with any dots removed.
    """
    req = Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/137.0.0.0 Safari/537.36",
        },
    )

    # Fetch the content of the URL
    try:
        response = urlopen(req)
    except Exception as e:
        print(f"Error fetching {url}: {e}")
        return ""
    if response.getcode() != 200:
        print(f"Error: {response.getcode()} for {url}")
        return ""
    if not response.readable():
        print(f"Error: No content for {url}")
        return ""
    # Read the response content
    response = response.read().decode("utf-8")

    matches = re.findall(regex, response)

    data = ""

    print(f"Got {len(matches)} matches for {url}")

    print(f"Got {operation} operation for {url}")

    if operation == "average":
        average = 0
        for match in matches:
            average += int(match)

        average = average / len(matches)
        data = str(int(average))
    elif operation == "median":
        matches = sorted([int(match) for match in matches])
        mid = len(matches) // 2
        if len(matches) % 2 == 0:
            median = (matches[mid - 1] + matches[mid]) / 2
        else:
            median = matches[mid]
        data = str(int(median))
    elif operation == "mode":  # calculate the mode with bucket size of 50
        bucket_size = 50
        matches = [int(match) for match in matches]
        matches = [
            int((match // bucket_size) * bucket_size) for match in matches
        ]  # Group by tens
        frequency = {}
        for match in matches:
            if match in frequency:
                frequency[match] += 1
            else:
                frequency[match] = 1
        mode = max(frequency, key=frequency.get)
        data = str(
            mode + bucket_size
        )  # Add the bucket_size to get the upper limit of the bucket
    elif operation == "max":  # take the highest value
        current = 0
        for match in matches:
            if current == 0:
                current = int(match)
            else:
                current = max(current, int(match))
        data = str(current)
    else:  # default take the lowest value
        current = 0
        for match in matches:
            if current == 0:
                current = int(match)
            else:
                current = min(current, int(match))
        data = str(current)

    print(f"Got data {data} for {url}")

    return data


def write_data(data, measurement):
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


def main():
    next_reading = time.time()
    try:
        while True:
            for i in objects:
                try:
                    data = get_data(i.url, i.regex, i.operation)

                    print(f"Got data {data} for {i.measurement}")

                    if data:
                        write_data(data, i.measurement)
                except Exception as err:
                    print(f"got error {err}")

            next_reading += INTERVAL
            sleep_time = next_reading - time.time()

            if sleep_time > 0:
                time.sleep(sleep_time)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    asyncio.run(main())
