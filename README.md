# influxdb_stats

Saves various system stats to an influxdb database, which can be used with plotting software such as grafana.

Python >= 3.5 is required.

Run influxdb_stats.py with -h to see all the command line options. There is also an example config file.

Documentation is inside the python files in the docstrings.

Custom modules can be added in the stats_modules.py file if you want to add your own metrics.

It is recommended to install and configure through install.py (it handles influx, grafana and dependencies)

Note that install.py is written for ubuntu only

Dependencies are in requirements.txt and can be installed manually with (sudo) pip3 install -r requirements.txt

A template grafana dashboard is in grafana_dashboard.json
