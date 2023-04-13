import asyncio
import re
import time
import configparser
import argparse
import influxdb_client

from urllib.request import urlopen
from bs4 import BeautifulSoup
from influxdb_client import Point
from influxdb_client.client.write_api import SYNCHRONOUS

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
    def __init__(self, url, regex, measurement):
        self.url = url
        self.regex = regex
        self.measurement = measurement


objects = []

for key in config:
    if key.isdigit():
        objects.append(
            ScrapingObject(
                config[key]["url"], config[key]["regex"], config[key]["measurement"]
            )
        )


def getData(url, regex):
    url = url
    html = urlopen(url)
    soup = BeautifulSoup(html, "html5lib")
    type(soup)

    text = str(soup)

    matches = re.search(regex, text)

    data = ""

    if matches:
        if matches.groups():
            data = matches.group(1)
            print("{group}".format(group=data))

    return data.replace(".", "")


def writeData(data, measurement):
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
            try:
                for i in objects:
                    data = getData(i.url, i.regex)
                    if data:
                        writeData(data, i.measurement)
            except Exception as err:
                print(f"got http error {err}")

            next_reading += INTERVAL
            sleep_time = next_reading - time.time()

            if sleep_time > 0:
                time.sleep(sleep_time)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    asyncio.run(main())
