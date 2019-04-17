# system_metrics_influx

Saves various system metrics to an influxdb database, which can be used with plotting software such as grafana.

## Context

system_metrics_influx aims to provide metrics for a system in a compact, reliable and extensible way. It allows you to see how resources are being utilised, and spot problems before they happen e.g memory leaks. This project aims to be quick and easy to install; copy a settings template, fill it in and then run the installer once, and you never should have to touch it again.

## Usage

python3 system_metrics_influx.py \<args>

Run with -h or --help to see all the available config options. Config can be specified through command line options or through a config file (--config-file \<path to file>). The config file format is yaml and there are 2 example files: a debug template and a production template. system_metrics_influx.py is blocking when run.

To stop the program, simply send it SIGTERM and it will shutdown gracefully.

If using systemd, the commands to manage it are `sudo systemctl <action> system_metrics_influx` where action is start, stop or restart.

Updating the program is as simple as running `git pull` and then restarting it.

## Install

Install can be done by manually installing the dependencies or using the install.py script. It's recommended to use the installer script as it can set up everything for you. It still lets you choose which things to install / modify so you can do some parts manually if necessary. The installer can also install a systemd service so it starts automatically upon boot.

Python dependencies are in requirements.txt. Using a python venv/virtualenv is supported by the installer (including the systemd service) and the main script.

If grafana is being used to graph the data, it is recommended to set the datasource minimum interval equal to the save rate to avoid any gaps in the graph. A grafana dashboard template is in data/grafana_template.json, but this should not be used directly in grafana. Instead, the installer uses this template to generate a customised dashboard, which is written to configured/grafana_configured.json (it is necessary to run the installer first to set it up for your number of cpus and for whether the gpu backend is enabled; currently grafana doesn't provide a flexible way to template everything [e.g](https://github.com/grafana/grafana/issues/3935))

## Developement / Adding custom modules

The main system_metrics_influx.py contains an architectural overview in its docstring. Plugins can be added inside the custom_plugins folder, and there is an example plugin detailing how a plugin should operate.

## Limitations

- Some installer features only support / are tested on ubuntu
- Requires python 3.5 or newer

## Licence

GPLV3
