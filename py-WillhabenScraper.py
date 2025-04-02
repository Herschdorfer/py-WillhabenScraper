import asyncio
import re
import time
import configparser
import argparse
import influxdb_client

from influxdb_client import Point
from influxdb_client.client.write_api import SYNCHRONOUS
import requests

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
                config[key]["url"], config[key]["regex"], config[key]["measurement"], config[key].get("operation", "")
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
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/109.0.0.0 Safari/537.36',
    }

    response = requests.get(url, headers=headers, timeout=10)

    matches = re.findall(regex, response.text)

    data = ""

    print(f"Got {len(matches)} matches for {url}")

    print(f"Got {operation} operation for {url}")

    if operation == "average":
        average = 0
        for match in matches:
            average += int(match)


        average = average / len(matches)
        data = str(int(average))
    else: # take the lowest value
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
