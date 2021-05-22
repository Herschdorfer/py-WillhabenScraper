import re
import time
import paho.mqtt.client as mqtt

from urllib.request import urlopen
from bs4 import BeautifulSoup

OPENHAB = 'openhabianpi'

# Data capture and upload interval in seconds. Every hour.
INTERVAL = 60

def getData():
  url = "https://www.willhaben.at/iad/immobilien/haus-kaufen/haus-angebote?PRICE_FROM=0&PRICE_TO=300000&ESTATE_SIZE/LIVING_AREA_FROM=95&areaId=3&areaId=900&&page=1&view=list&force=true"
  html = urlopen(url)
  soup = BeautifulSoup(html, 'html5lib')
  type(soup)

  text = str(soup)

  regex = r'"numberOfItems":(\d+)'

  matches = re.search(regex, text)

  data = ""

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
    data = getData()
    if (data != ""):
      client.publish('meta/house/available', data , 1)

    next_reading += INTERVAL
    sleep_time = next_reading-time.time()
    if sleep_time > 0:
      time.sleep(sleep_time)
except KeyboardInterrupt:
  pass

client.loop_stop()
client.disconnect()

