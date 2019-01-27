import re
import pandas as pd
import numpy as np
import seaborn as sns
import os
import time
import sys
import paho.mqtt.client as mqtt
import json

from urllib.request import urlopen
from bs4 import BeautifulSoup

OPENHAB = 'localhost'

# Data capture and upload interval in seconds. Every hour.
INTERVAL = 60

def getData():
  url = "https://www.willhaben.at/iad/immobilien/haus-kaufen/haus-angebote?PRICE_FROM=0&PRICE_TO=300000&ESTATE_SIZE/LIVING_AREA_FROM=95&areaId=3&areaId=900&&page=1&view=list&force=true"
  html = urlopen(url)
  soup = BeautifulSoup(html, 'lxml')
  type(soup)

  text = str(soup)

  regex = r'<span.*class=.search-count..*>([\d.]+)</span>'

  matches = re.search(regex, text)

  if matches:
    if matches.groups():
      data = matches.group(1);
      print ("{group}".format(group = data))

  return data.replace('.', '')


next_reading = time.time()
client = mqtt.Client()
client.connect(OPENHAB, 1883, 60)
client.loop_start()


try:
  while True:
    client.publish('meta/house/available', getData() , 1)

    next_reading += INTERVAL
    sleep_time = next_reading-time.time()
    if sleep_time > 0:
      time.sleep(sleep_time)
except KeyboardInterrupt:
  pass

client.loop_stop()
client.disconnect()

