import asyncio
import re
import time
import configparser

from influxdb_client import Point
from urllib.request import urlopen
from bs4 import BeautifulSoup
from influxdb_client.client.influxdb_client_async import InfluxDBClientAsync

# Data capture and upload interval in seconds. Every hour.
INTERVAL = 60

config = configparser.ConfigParser()
config.sections()

config.read('.config')


class ScrapingObject:
    def __init__(self, url, regex, bucket):
        self.url = url
        self.regex = regex
        self.bucket = bucket


objects = []

for key in config:
    if key.isdigit():
        objects.append(ScrapingObject(
            config[key]['url'], config[key]['regex'], config[key]['bucket']))


def getData(url, regex):
    url = url
    html = urlopen(url)
    soup = BeautifulSoup(html, 'html5lib')
    type(soup)

    text = str(soup)

    matches = re.search(regex, text)

    data = ""

    if matches:
        if matches.groups():
            data = matches.group(1)
            print("{group}".format(group=data))

    return data.replace('.', '')


async def writeData(data, bucket):
    username = config['InfluxDB']['username']
    password = config['InfluxDB']['password']
    server = config['InfluxDB']['server']

    async with InfluxDBClientAsync(url=server, token=f'{username}:{password}', org='-') as client:
        write_api = client.write_api()
        _point1 = Point(bucket).field("value", int(data))
        successfully = await write_api.write(bucket="openhab_db", record=[_point1])
        if successfully:
            print(f"successfully wrote data to InfluxDB")


async def main():
    next_reading = time.time()
    try:
        while True:
            for i in objects:
                data = getData(i.url, i.regex)
                if data:
                    await writeData(data, i.bucket)

                next_reading += INTERVAL
                sleep_time = next_reading-time.time()

            if sleep_time > 0:
                time.sleep(sleep_time)
    except KeyboardInterrupt:
        pass

if __name__ == "__main__":
    asyncio.run(main())
