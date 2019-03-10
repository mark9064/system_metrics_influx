# influxdb_stats

Saves various system stats to an influxdb database, which can be used with plotting software such as grafana.

Python3 is required.

Run influxdb_stats.py with -h to see all the command line options. There is also an example config file.

Documentation is inside the python files in the docstrings.

Custom modules can be added in the stats_modules.py file if you want to add your own metrics.

Dependencies are in requirements.txt and can be installed with (sudo) pip3 install -r requirements.txt
