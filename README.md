# system_metrics_influx

Saves system metrics to an influxdb database, which can be plotted with grafana.

## Context

system_metrics_influx aims to provide metrics in a compact, reliable and extensible way. It allows you to see how resources are being utilised and spot problems before they happen e.g memory leaks. This project aims to be quick and easy to install; copy a settings template, fill it in and then run the installer once, and you never should have to touch it again.

The typical usecase is using the data with grafana, but grafana is not a requirement and anything supporting influxdb can be used.

## Usage

python3 system_metrics_influx.py \<args>

Run with -h or --help to see all the available config options. Config can be specified through command line options or through a config file (--config-file \<path to file>). Config files utilise the YAML format and there are is an example config at data/example_config.yaml.

To stop the program, simply send it SIGTERM and it will shutdown gracefully.

If using systemd, the commands to manage it are `(sudo) systemctl <action> system_metrics_influx` where action is start, stop or restart (or any other systemd command).

Updating is as simple as running `git pull` and then restarting it (after checking the changelog of course!).

## Install

Using the installer (install.py) is recommended, but install can also be completed manually.

The installer can:
- Install influxdb
- Install grafana
- Install python dependencies
- Setup and configure nvidia GPUs
- Install required grafana plugins
- Setup the grafana datasource and install the dashboard to grafana
- Install the systemd service

Python dependencies are in requirements.txt. Using a python venv/virtualenv is supported by the installer (including the systemd service) and the main script.

If grafana is being used, it is recommended to set the datasource minimum interval equal to the save rate to avoid any gaps in graphs. A grafana dashboard template is in data/grafana_template.json, but this should not be used directly in grafana. Instead, the installer uses this template to generate a customised dashboard, which is written to configured/grafana_configured.json (it is necessary to run the installer first to set it up for your number of CPUs and for whether the gpu backend is enabled; currently grafana doesn't provide a flexible way to template everything [e.g](https://github.com/grafana/grafana/issues/3935))

## Developement / Adding custom modules

system_metrics_influx.py contains an architectural overview in its docstring. Plugins can be added inside the plugins folder, and there is an example plugin with a guide on how to make a plugin.

## Limitations

- Some installer features only support / are tested on ubuntu
- Requires python 3.5 or newer

## Licence

GPLV3
