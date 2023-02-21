# py-WillhabenScraper

This is a simple scrapper for the Austrian WillHaben.at website.

It will access a configured URL and then use a regular expression to retrieve the desired data.
The result will be written to a configured Influx database for later use, eg. a Grafana visualization.

# Configuration

Configuration | Explanaton                           | Example
--------------|--------------------------------------|--------
name          | Simple name, not used for processing | -
regex         | Regular Expression for lookup        | "numberOfItems":(\d+)
url           | URL to access                        | https://www.willhaben.at/iad/immobilien/haus-kaufen/haus-angebote?PRICE_FROM=0&PRICE_TO=300000&ESTATE_SIZE/LIVING_AREA_FROM=95&areaId=3&areaId=900&&page=1&view=list&force=true
bucket        | Bucket in Influx to save the data    | MetaData_HouseData
