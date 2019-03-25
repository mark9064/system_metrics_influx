# system_metrics_influx

Saves various system metrics to an influxdb database, which can be used with plotting software such as grafana.

## Context

system_metrics_influx aims to provide metrics for a system in a compact, reliable and extensible way. It allows you to see how resources are being utilised, and spot problems before they happen e.g memory leaks. This project aims to be quick and easy to install; copy a settings template, fill it in and then run the installer once, and you never should have to touch it again.

## Usage

python3 system_metrics_influx.py \<args>

Run with -h or --help to see all the available config options. Config can be specified through command line options or through a config file (--config-file \<path to file>). The config file format is yaml and there are 3 example files, including a generic template, a debug template and a production template. It is blocking when run.

## Install

Install can be done by manually installing the dependencies or using the install.py script. It's recommended to use the installer script as it can set up everything for you. It still lets you choose which things to install / modify so you can do some parts manually if necessary. The installer can also install a systemd service so it starts automatically upon boot.

Python dependencies are in requirements.txt. Using a python venv/virtualenv is supported by the installer (including the systemd service) and the main script.

If grafana is being used to graph the data, it is recommended to set the datasource minimum interval equal to the save rate to avoid any gaps in the graph. A grafana dashboard is in data/grafana_template.json and the generated dashboard from the installer is written to configured/grafana_configured.json (it is recommended to run the installer first to set it up for your number of cpus; currently grafana doesn't provide a flexible way to template everything [e.g](https://github.com/grafana/grafana/issues/3935))

## Developement / Adding custom modules

The documentation is inside the python files in their docstrings. Custom modules can be added in the stats_modules.py.

## Limitations

- Some installer features only support / are tested on ubuntu
- Requires python 3.5 or newer

## Licence

GPLV3
