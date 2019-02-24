#!/usr/bin/env python3
"""Data logger to influxdb
Data:

CPU by core psutil.cpu_percent(percpu=True)
CPU by user, system, user, idle, nice, iowait, irq, softirq psutil.cpu_times_percent()
CPU frequency psutil.cpu_freq()
Memory by absolute usage and percentage psutil.virtual_memory() expressed in bytes
Disk usage per specified mountpoint; total, used, percent psutil.disk_usage()
Disk i/o in bytes and number of reads/writes psutil.disk_io_counters()
Network i/o in bytes and packets psutil.net_io_counters()
CPU temp psutil.sensors_temperatures()
System load (1, 5, 15) os.getloadavg()
Total processes psutil.pids()
System uptime psutil.boot_time()

Catergories:
    cpu
    memory
    disk
    diskio
    netio
    sensors
    misc

Timers:
target_time - targetted end time of the fetch - data saved to the db under this value
last_end_time - precise end time stored internally in each class for delta monitors

Control flow:

Initial cycle:
Call init for all delta monitors
Set target to next round second
Begin mainloop

Main loop:
Wait until target_time - save_rate or if already past this log warning
Continuous monitors start
Continuous monitors end 0.2 secs before target time
Point monitors sampled
Data pushed to database
target_time set
next iteration

TODO

check types on logfile parse
cython version
better error handling
"""
# pylint: disable=no-member, logging-format-interpolation
import argparse
import collections
import copy
import logging
import math
import os
import signal
import statistics
import sys
import time

import influxdb
import psutil
import trio
import yaml

import stats_modules


class GracefulKiller:
    """Class to deal with SIGTERM and SIGINT"""
    kill_now = False
    def __init__(self):
        signal.signal(signal.SIGINT, self.exit_gracefully)
        signal.signal(signal.SIGTERM, self.exit_gracefully)

    def exit_gracefully(self, signum, frame):
        """Sets the kill_now var upon SIGTERM/SIGINT"""
        # pylint: disable=unused-argument
        self.kill_now = True

class BaseStat:
    """Base stats class for shared methods"""
    save_rate = 0
    target_time = 0

    @classmethod
    def set_time(cls, target_time):
        """Sets the target time of the stats collection"""
        cls.target_time = target_time


class CPUStats(BaseStat):
    """All CPU related stats"""
    def __init__(self):
        self.cpu_time_fields = psutil.cpu_times_percent(interval=0.0)._fields
        self.cpu_stats_fields = psutil.cpu_stats()._fields
        self.poll_data = dict(util=[], freq=[], times=[])
        self.cpu_persistent = []
        self.out_dict = {"cpu": {}}
        self.last_end_time = 0

    def init_fetch(self):
        """Fetches stats for post-initialisation"""
        self.cpu_persistent = psutil.cpu_stats()
        self.last_end_time = time.time()

    async def poll_stats(self):
        """Fetches the polling stats"""
        self.poll_data = dict(util=[], freq=[], times=[])
        while time.time() < self.target_time - 0.2:
            self.poll_data["util"].append(psutil.cpu_percent(percpu=True))
            self.poll_data["freq"].append(int(psutil.cpu_freq().current))
            self.poll_data["times"].append(psutil.cpu_times_percent(interval=0.0))
            next_poll_time = time.time() + self.save_rate / 10
            if next_poll_time > self.target_time:
                break
            time.sleep(next_poll_time - time.time())

    async def get_stats(self):
        """Fetches the point stats and pushes to out_dict"""
        current_stats = psutil.cpu_stats()
        time_delta = time.time() - self.last_end_time
        self.last_end_time = time.time()
        stats_delta = [round((current_stats[i] - self.cpu_persistent[i]) / time_delta)
                       for i in range(len(current_stats))]
        utilisation = [round(statistics.mean(x), 2) for x in zip(*self.poll_data["util"])]
        frequency = round(statistics.mean(self.poll_data["freq"]))
        times = [round(statistics.mean(x), 2) for x in zip(*self.poll_data["times"])]
        self.out_dict = {"cpu": {}}
        for item in ["ctx_switches", "interrupts"]:
            self.out_dict["cpu"][item] = stats_delta[self.cpu_stats_fields.index(item)]
        for index, item in enumerate(utilisation):
            self.out_dict["cpu"]["cpu{0}".format(index)] = item
        self.out_dict["cpu"]["frequency"] = frequency
        for index, item in enumerate(times):
            field = self.cpu_time_fields[index]
            if field in ("user", "system", "iowait", "nice", "irq", "softirq"):
                self.out_dict["cpu"][field] = item



class MemoryStats(BaseStat):
    """All memory related stats"""
    def __init__(self):
        self.out_dict = {"memory": {}}

    async def get_stats(self):
        """Fetches the point stats and pushes to out_dict"""
        mem_data = psutil.virtual_memory()
        self.out_dict = {"memory": {}}
        self.out_dict["memory"]["total"] = mem_data.total
        self.out_dict["memory"]["used"] = mem_data.total - mem_data.available
        self.out_dict["memory"]["percent"] = mem_data.percent


class DiskStorageStats(BaseStat):
    """All stats related to storage space on disks"""
    def __init__(self, disk_paths):
        self.out_dict = {"disk": {}}
        self.disk_paths = disk_paths

    async def get_stats(self):
        """Fetches the point stats and pushes to out_dict"""
        self.out_dict = {"disk": {}}
        for item in self.disk_paths:
            disk_data = psutil.disk_usage(item)
            self.out_dict["disk"]["{0}_total".format(item)] = disk_data.total
            self.out_dict["disk"]["{0}_used".format(item)] = disk_data.used
            self.out_dict["disk"]["{0}_percent".format(item)] = disk_data.percent


class DiskIOStats(BaseStat):
    """All stats related to IO on disks"""
    def __init__(self):
        self.out_dict = {"diskio": {}}
        self.diskio_persistent = []
        self.last_end_time = 0
        self.diskio_fields = psutil.disk_io_counters()._fields
        self.remap = dict(read_bytes="read_bytes", read_count="disk_reads",
                          write_bytes="write_bytes", write_count="disk_writes")

    def init_fetch(self):
        """Fetches stats for post-initialisation"""
        self.diskio_persistent = psutil.disk_io_counters()
        self.last_end_time = time.time()

    async def get_stats(self):
        """Fetches the point stats and pushes to out_dict"""
        current_stats = psutil.disk_io_counters()
        time_delta = time.time() - self.last_end_time
        self.last_end_time = time.time()
        stats_delta = [round((current_stats[i] - self.diskio_persistent[i]) / time_delta)
                       for i in range(len(current_stats))]
        self.out_dict = {"diskio": {}}
        for item in ("read_bytes", "read_count", "write_bytes", "write_count"):
            self.out_dict["diskio"][self.remap[item]] = stats_delta[self.diskio_fields.index(item)]


class NetIOStats(BaseStat):
    """All network related stats"""
    def __init__(self):
        self.out_dict = {"netio": {}}
        self.netio_persistent = []
        self.last_end_time = 0
        self.netio_fields = psutil.net_io_counters()._fields
        self.remap = dict(bytes_sent="tx_bytes", bytes_recv="rx_bytes",
                          packets_sent="tx_packets", packets_recv="rx_packets")

    def init_fetch(self):
        """Fetches stats for post-initialisation"""
        self.netio_persistent = psutil.net_io_counters()
        self.last_end_time = time.time()

    async def get_stats(self):
        """Fetches the point stats and pushes to out_dict"""
        current_stats = psutil.net_io_counters()
        time_delta = time.time() - self.last_end_time
        self.last_end_time = time.time()
        stats_delta = [round((current_stats[i] - self.netio_persistent[i]) / time_delta)
                       for i in range(len(current_stats))]
        self.out_dict = {"netio": {}}
        for item in ("bytes_sent", "bytes_recv", "packets_sent", "packets_recv"):
            self.out_dict["netio"][self.remap[item]] = stats_delta[self.netio_fields.index(item)]


class SensorStats(BaseStat):
    """All sensor related stats"""
    def __init__(self):
        self.out_dict = {"sensors": {}}
        self.thermal_nosensor = False

    async def get_stats(self):
        """Fetches the point stats and pushes to out_dict"""
        temperature_data = psutil.sensors_temperatures()
        cpu_temperature = None
        if "coretemp" in temperature_data:
            for item in temperature_data["coretemp"]:
                if item.label == "Package id 0":
                    cpu_temperature = item.current
                    break
        elif "armada_thermal" in temperature_data:
            cpu_temperature = temperature_data["armada_thermal"][0].current
        self.out_dict = {"sensors": {}}
        if cpu_temperature is not None:
            self.out_dict["sensors"]["cpu_temp"] = cpu_temperature
        else:
            if not self.thermal_nosensor:
                LOGGER.info("CPU thermal sensor not found")
                self.thermal_nosensor = True


class MiscStats(BaseStat):
    """Any other miscellaneous stats"""
    def __init__(self):
        self.out_dict = {"misc": {}}

    async def get_stats(self):
        """Fetches the point stats and pushes to out_dict"""
        sys_load = os.getloadavg()
        processes = len(psutil.pids())
        uptime = self.target_time - int(psutil.boot_time())
        self.out_dict = {"misc": {}}
        for index, item in enumerate(("load_1", "load_5", "load_15")):
            self.out_dict["misc"][item] = sys_load[index]
        self.out_dict["misc"]["processes"] = processes
        self.out_dict["misc"]["uptime"] = uptime


def main(args):
    """Main function"""
    save_rate = args["save_rate"]
    error_limit = args["error_limit"]
    pidfile = args["pidfile"]
    interrupt = GracefulKiller()
    influx_args = {x: args[x]
                   for x in ["host", "port", "username", "password", "database"]}
    if not args["dry_run"]:
        client = influxdb.InfluxDBClient(**influx_args)
    stats_classes = [CPUStats(), MemoryStats(), DiskStorageStats(args["disk_paths"]),
                     DiskIOStats(), NetIOStats(), SensorStats(), MiscStats()]
    for module in stats_modules.USER_MODULES:
        stats_classes.append(module())
    BaseStat.save_rate = save_rate
    continous_stats = []
    for item in stats_classes:
        if callable(getattr(item, "init_fetch", None)):
            item.init_fetch()
        if callable(getattr(item, "poll_stats", None)):
            continous_stats.append(item)
    cumulative_errors = 0
    target_time = math.ceil(time.time() + 1)
    BaseStat.set_time(target_time)
    while True:
        try:
            if interrupt.kill_now or (cumulative_errors > error_limit > 0):
                break
            if time.time() > target_time - save_rate:
                LOGGER.info("Running behind by {0}s".format(time.time() - target_time))
            else:
                while time.time() < target_time - save_rate:
                    time.sleep(0.001)
            current_time = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(target_time))
            trio.run(collect_stats, continous_stats, stats_classes)
            out_dict = {}
            for item in stats_classes:
                out_dict.update(item.out_dict)
            write_data = []
            for key, value in out_dict.items():
                write_data.append(dict(measurement=key, time=current_time, fields=value))
            if not args["dry_run"]:
                client.write_points(write_data)
            else:
                print(out_dict)
            cumulative_errors = 0
        except Exception as caught_exception:
            LOGGER.warning("Caught exception: {0}".format(caught_exception))
            cumulative_errors += 1
        target_time += save_rate
        BaseStat.set_time(target_time)
    if pidfile is not None:
        os.remove(pidfile)


async def collect_stats(continuous_stats, stats_classes):
    """Asynchronously fetches the stats"""
    async with trio.open_nursery() as nursery:
        for item in continuous_stats:
            nursery.start_soon(item.poll_stats)
    async with trio.open_nursery() as nursery:
        for item in stats_classes:
            nursery.start_soon(item.get_stats)


def initial_argparse():
    """Parses command line args"""
    cmd_args = collections.OrderedDict([
        ["config_file", dict(cmd_name="config-file", default=None, type=[None, str],
                             help="Specify path to config file. The command line options override "
                             "the config file. Example config file in example_config.yaml")],
        ["username", dict(cmd_name="username", default="root", type=str,
                          help="Username for influxdb. Default is root")],
        ["password", dict(cmd_name="password", default="root", type=str,
                          help="Password for influxdb. Default is root")],
        ["host", dict(cmd_name="host", default="localhost", type=str,
                      help="Host for influxdb. Default is root")],
        ["port", dict(cmd_name="port", default=8086, type=int,
                      help="Port for influxdb. Default is root")],
        ["database", dict(cmd_name="database", default="system_stats", type=str,
                          help="Database name for influxdb. Default is root")],
        ["save_rate", dict(cmd_name="save-rate", default=1, type=int,
                           help="Sets how often the stats are saved to influx, in seconds. "
                           "Default is 1, must be a non zero integer")],
        ["disk_paths", dict(cmd_name="disk-paths", default=["/"], nargs="*", type=str,
                            help="Sets the mountpoints used for disk monitoring (space used only, "
                            "io is global). Default is /, multiple args should be seperated "
                            "with a space e.g '--disk-paths / /boot/efi'. Trailing slash on "
                            "mountpoint is optional. No args disables disk monitoring.")],
        ["error_limit", dict(cmd_name="max-consecutive-errors", default=0, type=int,
                             help="Sets the max limit for consecutive errors, which the the  "
                             "program will exit at if reached. An error can occur once per save "
                             "cycle. Default is 0 (never exit)")],
        ["dry_run", dict(cmd_name="dry-run", default=False, type=bool, action="store_true",
                         help="Skips writing any data to influx and instead prints it "
                         "to stdout. Useful only for testing. A valid influx database "
                         "is not required when running in this mode.")],
        ["logfile_path", dict(cmd_name="logfile-path", default=None, type=[None, str],
                              help="Sets the path to the desired logfile. By default a logfile "
                              "is not created.")],
        ["log_stdout", dict(cmd_name="log-stdout", default=False, type=bool, action="store_true",
                            help="Enables logging to stdout")],
        ["pidfile", dict(cmd_name="pidfile", default=None, type=[None, str],
                         help="Enables writing a pidfile to the specified location. "
                         "File is removed when the program exits. "
                         "Any existing file will be overwritten.")]])
    parser = argparse.ArgumentParser()
    format_dict = copy.deepcopy(cmd_args)
    for key, value in format_dict.items():
        if isinstance(value["type"], list):
            value["type"] = value["type"][1]
        elif "action" in value:
            if value["action"] == "store_true":
                del value["type"]
        name = "--{0}".format(value["cmd_name"])
        del value["cmd_name"]
        del value["default"]
        parser.add_argument(name, dest=key, **value)

    args = vars(parser.parse_args())
    specified = {}
    for key, value in args.items():
        if value is None:
            args[key] = cmd_args[key]["default"]
            specified[key] = False
        else:
            specified[key] = True
    if args["config_file"] is not None:
        args = parse_config_file(args, cmd_args, specified)
    if args["save_rate"] <= 0:
        raise ValueError("Save rate must be a non zero positive integer")
    if args["logfile_path"] is not None:
        LOGGER.addHandler(create_sublogger(logging.DEBUG, args["log_path"]))
    if args["log_stdout"]:
        LOGGER.addHandler(create_sublogger(logging.DEBUG))
    if args["pidfile"] is not None:
        open(args["pidfile"], "w").write(str(os.getpid()))
    mountpoints = [x.mountpoint for x in psutil.disk_partitions()]
    for item in args["disk_paths"]:
        if item.endswith("/") and item != "/":
            item = item[:-1]
        if item not in mountpoints:
            raise FileNotFoundError("Invalid mountpoint specified")
    return args

def parse_config_file(args, cmd_args, specifed):
    """Parses the config file and type checks it"""
    with open(args["config_file"], "r") as stream:
        args_new = yaml.safe_load(stream)
    args_new_formatted = {}
    lookup = {v["cmd_name"]: k for k, v in cmd_args.items()}
    for key, value in args_new.items():
        args_new_formatted[lookup[key]] = value
    args_new = args_new_formatted
    for key, value in args_new.items():
        allowed_type = cmd_args[key]["type"]
        error = ""
        if isinstance(allowed_type, list):
            if (value is not None) and (not isinstance(value, allowed_type[1])):
                error = "{0} or None".format(allowed_type[1].__name__)
        elif not isinstance(value, allowed_type):
            error = allowed_type.__name__
        if "nargs" in cmd_args[key]:
            if cmd_args[key]["nargs"] == "*":
                error = ""
                if not isinstance(value, list):
                    error = "list"
                else:
                    for item in value:
                        if not isinstance(item, allowed_type):
                            error = "{0} inside list".format(allowed_type.__name__)
        if error:
            raise TypeError("Option {0} in config file is not type {1}"
                            .format(cmd_args[key]["cmd_name"], error))
        if not specifed[key]:
            args[key] = value
    return args

def create_sublogger(level, path=None):
    """Sets up a sublogger"""
    formatter = logging.Formatter("%(asctime)s %(name)s %(levelname)s %(message)s")
    if path is None:
        logger_handler = logging.StreamHandler(sys.stdout)
    else:
        logger_handler = logging.FileHandler(path)
    logger_handler.setLevel(level)
    logger_handler.setFormatter(formatter)
    return logger_handler


if __name__ == "__main__":
    LOGGER = logging.getLogger()
    main(initial_argparse())
