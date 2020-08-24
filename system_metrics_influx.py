#!/usr/bin/env python3
"""
Overall design:
- Load configuration from command line options and config file
- Create influxdb connection
- Load all .py files inside the plugins folder, and load all classes in their ACTIVATED_METRICS
- Initialise all stat classes (including async_init)
- Run the init_fetch methods of all stat classes (only if present)
    - This allows stats to get an initial reading for metrics which record the change in a value over time
- Start the influx_write and stats_handler functions
    In influx_write
    - Read data sent by stats_handler and write it to influxdb
    - Repeat until data channel closed by stats_handler
    In stats_handler
    - Initialise the target time to the next + 1 integer second
    - Set target_time on BaseStat (used by continuous stats)
    - Loop indefinitely, check exit conditions at start of loop
        - Exit conditions checked:
            - Check for SIGTERM or SIGINT
            - Check if error count is more than cumulative errors limit
        - Check current time and log any running behind situations
            Behind by:
            - 0 < t < collect_interval / 2 logged with info level
            - else logged with warning level
            - If behind by more than 5 * collect_interval data entry will be skipped (logged with critical level)
        - Wait until target_time - collect_interval
        - Call collect_stats
            In collect stats
            - Calculates start times for each stat class, checking for a time_needed attribute
                - Default is 0.2s (before target time) if not specified
            - All continuous stats are started immediately
            - Start stats when their start time is met (respecting time_needed)
            - Return when everything has finished
        - Errors are checked for and logged
        - Data is formatted for influx
        - Formatted data is sent through the data channel to influx_write
        - Target time incremented
    - When exiting, wait for the influx data channel to empty and then close it


Data:

Data comes from psutil unless otherwise stated
All values are expressed in their base units e.g bytes instead of gigabytes
Percentages are stored as 0-100 (float)
Database measurement names are in brackets

CPU (cpu):
    CPU usage and frequency by logical processor
    CPU total usage by user, system, user, idle, nice, iowait, irq
Nvidia (nvidia):
    Data from py3nvml
    Nvidia metrics per GPU
    clocks, max clocks, temperature, fanspeed, power usage, power limit,
    gpu utilisation, memory (bandwidth) utilisation and memory usage
Memory (memory):
    Memory usage
    usage, total and percentage
Disk usage (disk):
    Disk usage per specified mountpoint
    used and total
Disk I/O (diskio):
    Disk I/O per specified disk/partiton
    read/write bytes, number of reads/writes,
    number of merged reads/writes, time spent busy,
    time spent reading/writing
Network I/O (netio):
    Network I/O per nic
    sent/received bytes and sent/received packets
Sensors (sensors):
    CPU temperature (°C)
Miscellaneous (misc):
    System load (1, 5, 15 minutes) (from os)
    Total processes
    System uptime

Timers:
target_time - targetted end time of the fetch - data saved to the db under this value
last_end_time - precise end time stored internally in each class for delta monitors
"""
# pylint: disable=logging-format-interpolation
import argparse
import collections
import copy
import functools
import importlib
import logging
import math
import os
import re
import signal
import statistics
import sys
import time

import influxdb
import psutil
import trio
import yaml

from common_lib import BaseStat, InternalConfig, format_error

#
# stat classes
#

class CPUStats(BaseStat):
    """All CPU related stats"""
    name = "CPU"
    def __init__(self):
        self.cpu_time_fields = psutil.cpu_times_percent(interval=None)._fields
        self.cpu_stats_fields = psutil.cpu_stats()._fields
        self.poll_data = dict(freq=[])
        self.poll_success = False
        self.cpu_persistent = []
        self.last_end_time = 0

    async def init_fetch(self):
        """Fetches stats for post-initialisation"""
        self.cpu_persistent = psutil.cpu_stats()
        self.last_end_time = self.current_time()

    async def poll_stats(self):
        """Fetches the polling stats"""
        self.poll_data = dict(freq=[])
        self.poll_success = False
        initial = True
        while (self.current_time() < self.target_time - 0.2) or initial:
            initial = False
            self.poll_data["freq"].append(psutil.cpu_freq(percpu=True))
            next_poll_time = self.current_time() + self.collect_interval / 10
            if next_poll_time > self.target_time:
                break
            await sleep_until(next_poll_time)
        self.poll_success = True

    async def get_stats(self):
        """Fetches the point stats and pushes to out_data"""
        current_stats = psutil.cpu_stats()
        times = psutil.cpu_times_percent(interval=None)
        utilisation = psutil.cpu_percent(percpu=True)
        time_delta = self.current_time() - self.last_end_time
        self.last_end_time = self.current_time()
        stats_delta = [round((current_stats[i] - self.cpu_persistent[i]) / time_delta)
                       for i in range(len(current_stats))]
        self.cpu_persistent = current_stats
        if self.poll_success:
            frequencies = [round(statistics.mean([y.current * 1000000 for y in x]))
                           for x in zip(*self.poll_data["freq"])]
        out_data = [{"measurement": "cpu"}]
        for item in ["ctx_switches", "interrupts"]:
            out_data[0][item] = stats_delta[self.cpu_stats_fields.index(item)]
        for index, item in enumerate(utilisation):
            data_point = {"measurement": "cpu", "util": item, "tags": {"cpu": index}}
            if self.poll_success:
                data_point["freq"] = frequencies[index]
            out_data.append(data_point)
        for index, item in enumerate(times):
            field = self.cpu_time_fields[index]
            if field in ("user", "system", "iowait", "nice", "irq", "softirq"):
                out_data[0][field] = item
        return out_data


class GPUStats(BaseStat):
    """All GPU related stats"""
    name = "GPU"
    def __init__(self):
        self.nvidia_devices = {}
        try:
            import py3nvml.py3nvml as py3nvml # pylint: disable=import-outside-toplevel
            self.py3nvml = py3nvml
            self.setup_nvidia()
        except ImportError:
            LOGGER.info("Py3nvml not found, disabling nvidia backend")

    def setup_nvidia(self):
        """Sets up nvidia backend"""
        self.py3nvml.nvmlInit()
        LOGGER.debug("Detected nvidia driver: {0}"
                     .format(self.py3nvml.nvmlSystemGetDriverVersion()))
        device_count = self.py3nvml.nvmlDeviceGetCount()
        if device_count == 0:
            LOGGER.warning("Nvidia driver loaded but no devices found")
        for item in range(device_count):
            handle = self.py3nvml.nvmlDeviceGetHandleByIndex(item)
            uuid = str(self.py3nvml.nvmlDeviceGetUUID(handle))
            if uuid not in CONFIG.main["nvidia_cards"]:
                LOGGER.warning("New nvidia card detected, please re-run install to set up"
                               " the card and grafana")
            else:
                self.nvidia_devices[uuid] = handle
        self.nvidia_metrics = dict(
            mem=[self.py3nvml.nvmlDeviceGetMemoryInfo],
            power_usage=[self.py3nvml.nvmlDeviceGetPowerUsage],
            power_limit=[self.py3nvml.nvmlDeviceGetPowerManagementLimit],
            util=[self.py3nvml.nvmlDeviceGetUtilizationRates],
            temp=[self.py3nvml.nvmlDeviceGetTemperature, self.py3nvml.NVML_TEMPERATURE_GPU],
            core_clock=[self.py3nvml.nvmlDeviceGetClockInfo, self.py3nvml.NVML_CLOCK_GRAPHICS],
            max_core_clock=[self.py3nvml.nvmlDeviceGetMaxClockInfo,
                            self.py3nvml.NVML_CLOCK_GRAPHICS],
            mem_clock=[self.py3nvml.nvmlDeviceGetClockInfo, self.py3nvml.NVML_CLOCK_MEM],
            max_mem_clock=[self.py3nvml.nvmlDeviceGetMaxClockInfo, self.py3nvml.NVML_CLOCK_MEM],
            fanspeed_percent=[self.py3nvml.nvmlDeviceGetFanSpeed],
        )
        self.device_support = {}
        for uuid, handle in self.nvidia_devices.items():
            self.device_support[uuid] = {}
            for test, args in self.nvidia_metrics.items():
                self.device_support[uuid][test] = self.test_metric(args[0], handle, *args[1:])
            LOGGER.debug("GPU {0} supports {1}".format(CONFIG.main["nvidia_cards"][uuid],
                                                       self.device_support[uuid]))

    def test_metric(self, func, *args):
        """Tests a metric to see whether it is supported"""
        try:
            res = func(*args)
            if res is None:
                return False
        except Exception:
            return False
        return True

    async def get_stats(self):
        """Fetches the point stats and pushes to out_data"""
        out_data = []
        nvidia_results = {}
        for uuid, handle in self.nvidia_devices.items():
            nvidia_results[uuid] = {}
            for metric, enabled in self.device_support[uuid].items():
                if not enabled:
                    continue
                if metric == "mem":
                    res = self.py3nvml.nvmlDeviceGetMemoryInfo(handle)
                    nvidia_results[uuid]["mem_free"] = res.free
                    nvidia_results[uuid]["mem_used"] = res.used
                    nvidia_results[uuid]["mem_total"] = res.total
                    res = None
                elif metric == "power_usage":
                    res = self.py3nvml.nvmlDeviceGetPowerUsage(handle) / 1000
                elif metric == "power_limit":
                    res = self.py3nvml.nvmlDeviceGetPowerManagementLimit(handle) / 1000
                elif metric == "util":
                    res = self.py3nvml.nvmlDeviceGetUtilizationRates(handle)
                    nvidia_results[uuid]["gpu_util"] = res.gpu
                    nvidia_results[uuid]["mem_util"] = res.memory
                    res = None
                elif metric == "temp":
                    res = self.py3nvml.nvmlDeviceGetTemperature(
                        handle, self.py3nvml.NVML_TEMPERATURE_GPU
                    )
                elif metric == "fanspeed_percent":
                    res = self.py3nvml.nvmlDeviceGetFanSpeed(handle)
                elif metric == "core_clock":
                    res = self.py3nvml.nvmlDeviceGetClockInfo(
                        handle, self.py3nvml.NVML_CLOCK_GRAPHICS
                    ) * 1000000
                elif metric == "max_core_clock":
                    res = self.py3nvml.nvmlDeviceGetMaxClockInfo(
                        handle, self.py3nvml.NVML_CLOCK_GRAPHICS
                    ) * 1000000
                elif metric == "mem_clock":
                    res = self.py3nvml.nvmlDeviceGetClockInfo(
                        handle, self.py3nvml.NVML_CLOCK_MEM
                    ) * 1000000
                elif metric == "max_mem_clock":
                    res = self.py3nvml.nvmlDeviceGetMaxClockInfo(
                        handle, self.py3nvml.NVML_CLOCK_MEM
                    ) * 1000000
                if res is not None:
                    nvidia_results[uuid][metric] = res
            out_data.append({"measurement": "nvidia", **nvidia_results[uuid],
                             "tags": {"gpu": uuid}})
        return out_data


class BatteryStats(BaseStat):
    """Battery related stats"""
    name = "Battery"
    def __init__(self):
        try:
            battery = psutil.sensors_battery()
        except Exception:
            LOGGER.info("Unknown exception when querying battery information")
            self.battery_available = False
            return
        else:
            self.battery_available = True
        self.current_battery_presence = battery is not None
        if self.current_battery_presence:
            LOGGER.info("Battery detected")


    async def get_stats(self):
        """Fetched the point stats and pushes to out_data"""
        if not self.battery_available:
            return None
        battery = psutil.sensors_battery()
        battery_present = battery is not None
        if battery_present != self.current_battery_presence:
            if battery_present:
                LOGGER.info("Battery detected")
            else:
                LOGGER.warning("Battery no longer detected, removed? Battery stats disabled.")
            self.current_battery_presence = battery_present
        if not battery_present:
            return None
        out_data = {"measurement": "battery"}
        out_data["percent"] = battery.percent
        out_data["plugged"] = battery.power_plugged
        if battery.power_plugged is False:
            out_data["secsleft"] = battery.secsleft
        return out_data


class MemoryStats(BaseStat):
    """All memory related stats"""
    name = "Memory"
    async def get_stats(self):
        """Fetches the point stats and pushes to out_data"""
        mem_data = psutil.virtual_memory()
        out_data = {"measurement": "memory", "total": mem_data.total,
                    "used": mem_data.total - mem_data.available, "percent": mem_data.percent}
        return out_data


class DiskBase(BaseStat):
    """Shared methods between the two disk classes"""
    def __init__(self, disk_filters, filter_mode):
        self.filter_mode = filter_mode
        self.regex_matches = []
        self.filed_disks = {}
        self.regex_compile_list(disk_filters)

    def regex_compile_list(self, list_to_check):
        """Compiles all regex entries"""
        for item in list_to_check:
            try:
                expr = re.compile(item)
                self.regex_matches.append(expr)
            except re.error:
                raise ValueError("Disk filter specified is not valid regex")

    def check_disk_valid(self, disk):
        """Checks if a disk is valid"""
        if disk in self.filed_disks:
            return self.filed_disks[disk]
        return_mode = False
        if self.filter_mode == "include":
            return_mode = True
        for item in self.regex_matches:
            if item.fullmatch(disk):
                self.filed_disks[disk] = return_mode
                return return_mode
        self.filed_disks[disk] = not return_mode
        return not return_mode


class DiskStorageStats(DiskBase):
    """All stats related to storage space on disks"""
    name = "Disk"
    async def get_stats(self):
        """Fetches the point stats and pushes to out_data"""
        out_data = []
        for item in [device.mountpoint for device in psutil.disk_partitions()]:
            if not self.check_disk_valid(item):
                continue
            disk_data = psutil.disk_usage(item)
            results = {}
            results["total"] = disk_data.total
            results["used"] = disk_data.used
            results["percent"] = disk_data.percent
            out_data.append({"measurement": "disk", **results, "tags": {"disk": item}})
        return out_data


class DiskIOStats(DiskBase):
    """All stats related to IO on disks"""
    name = "DiskIO"
    def __init__(self, disk_filters, filter_mode):
        self.diskio_persistent = []
        self.last_end_time = 0
        self.remap = dict(read_time=dict(mult=10 ** -1), write_time=dict(mult=10 ** -1),
                          busy_time=dict(mult=10 ** -1), read_count=dict(name="disk_reads"),
                          write_count=dict(name="disk_writes"),
                          read_merged_count=dict(name="merged_reads"),
                          write_merged_count=dict(name="merged_writes"))
        super().__init__(disk_filters, filter_mode)

    async def init_fetch(self):
        """Fetches stats for post-initialisation"""
        self.diskio_persistent = {k: v._asdict()
                                  for k, v in psutil.disk_io_counters(perdisk=True).items()}
        self.last_end_time = self.current_time()

    async def get_stats(self):
        """Fetches the point stats and pushes to out_data"""
        current_stats = {k: v._asdict() for k, v in psutil.disk_io_counters(perdisk=True).items()}

        time_delta = self.current_time() - self.last_end_time
        self.last_end_time = self.current_time()
        stats_delta = {}
        for disk, previous_value in self.diskio_persistent.items():
            if not self.check_disk_valid(disk):
                continue
            if disk in current_stats:
                new_value = current_stats[disk]
                stats_delta[disk] = {key: round((new_value[key] - previous_value[key]) / time_delta)
                                     for key in new_value}
            else:
                LOGGER.info("Disk {0} no longer found. Unplugged?"
                            .format(disk))
        self.diskio_persistent = current_stats
        out_data = []
        for disk, data in stats_delta.items():
            # iterate over what the keys are now - they may be changed during iterations
            for key in list(data.keys()):
                if key in self.remap:
                    if "mult" in self.remap[key]:
                        data[key] *= self.remap[key]["mult"]
                    if "name" in self.remap[key]:
                        new_name = self.remap[key]["name"]
                        data[new_name] = data.pop(key)
                        key = new_name
            out_data.append({"measurement": "diskio", **data, "tags": {"disk": disk}})
        return out_data


class NetIOStats(BaseStat):
    """All network related stats"""
    name = "NetIO"
    def __init__(self):
        self.netio_persistent = {}
        self.last_end_time = 0
        self.netio_fields = psutil.net_io_counters()._fields
        self.remap = dict(bytes_sent="tx_bytes", bytes_recv="rx_bytes",
                          packets_sent="tx_packets", packets_recv="rx_packets")

    async def init_fetch(self):
        """Fetches stats for post-initialisation"""
        self.netio_persistent = psutil.net_io_counters(pernic=True)
        self.last_end_time = self.current_time()

    async def get_stats(self):
        """Fetches the point stats and pushes to out_data"""
        current_stats = psutil.net_io_counters(pernic=True)
        time_delta = self.current_time() - self.last_end_time
        self.last_end_time = self.current_time()
        stats_delta = {}
        for nic, previous_value in self.netio_persistent.items():
            if nic in current_stats:
                new_value = current_stats[nic]
                stats_delta[nic] = [round((new_value[i] - previous_value[i]) / time_delta)
                                    for i in range(len(new_value))]
            else:
                LOGGER.info("Network interface {0} no longer found. Unplugged/disabled?"
                            .format(nic))
        self.netio_persistent = current_stats
        out_data = []
        for nic, value in stats_delta.items():
            results = {}
            for item in ("bytes_sent", "bytes_recv", "packets_sent", "packets_recv"):
                results[self.remap[item]] = value[self.netio_fields.index(item)]
            out_data.append({"measurement": "netio", **results, "tags": {"nic": nic}})
        return out_data


class SensorStats(BaseStat):
    """All sensor related stats"""
    name = "Sensors"
    async def async_init(self):
        """Async stat initialisation"""
        if "cpu_temp" not in await self.get_stats():
            LOGGER.info("CPU thermal sensor not found")

    async def get_stats(self):
        """Fetches the point stats and pushes to out_data"""
        temperature_data = psutil.sensors_temperatures()
        cpu_temperature = None
        for item in temperature_data.get("coretemp", []):
            if item.label == "Package id 0":
                cpu_temperature = item.current
                break
        else:
            for item in temperature_data.get("k10temp", []):
                if item.label == "Tdie":
                    cpu_temperature = item.current
                    break
            else:
                if "armada_thermal" in temperature_data:
                    cpu_temperature = temperature_data["armada_thermal"][0].current
        if cpu_temperature is not None:
            return {"measurement": "sensors", "cpu_temp": cpu_temperature}



class MiscStats(BaseStat):
    """Any other miscellaneous stats"""
    name = "Misc"

    async def get_stats(self):
        """Fetches the point stats and pushes to out_data"""
        sys_load = os.getloadavg()
        processes = len(psutil.pids())
        uptime = self.target_time - int(psutil.boot_time())
        out_data = {"measurement": "misc"}
        for index, item in enumerate(("load_1", "load_5", "load_15")):
            out_data[item] = sys_load[index]
        out_data["processes"] = processes
        out_data["uptime"] = uptime
        return out_data

#
# helpers
#

def critical_exit(exc, message=""):
    """Exits with a critical error"""
    LOGGER.critical(format_error(exc, message=message))
    sys.exit(1)

def delta_current_time(time_, clamp_to_zero=False):
    """Calculate the time until a given time"""
    delta = time_ - BaseStat.current_time()
    if clamp_to_zero:
        delta = max(delta, 0)
    return delta

async def sleep_until(time_):
    """Sleep until a given time"""
    await trio.sleep(delta_current_time(time_, clamp_to_zero=True))

def create_sublogger(level, path=None):
    """Sets up a sublogger"""
    formatter = logging.Formatter("%(asctime)s %(name)s %(process)d %(levelname)s %(message)s")
    if path is None:
        logger_handler = logging.StreamHandler(sys.stdout)
    else:
        logger_handler = logging.FileHandler(path)
    logger_handler.setLevel(level)
    logger_handler.setFormatter(formatter)
    return logger_handler


#
# maih
#


async def initialise(args):
    """Initialise all stats and begin collecting / sending metrics"""
    plugins_dir = "plugins"
    collect_interval = args["collect_interval"]
    pidfile = args["pidfile"]
    influx_args = {x: args[x]
                   for x in ["host", "port", "username", "password", "database"]}
    if not args["dry_run"]:
        client = influxdb.InfluxDBClient(**influx_args)
    try:
        stats_objects = [CPUStats(), MemoryStats(), DiskStorageStats(*args["mountpoint_filters"]),
                         DiskIOStats(*args["disk_filters"]), NetIOStats(), SensorStats(),
                         MiscStats(), GPUStats()]
        modules = os.listdir(plugins_dir)
        for item in modules:
            if not item.endswith(".py"):
                continue
            item = item[:-3]
            try:
                module = importlib.import_module("{0}.{1}".format(plugins_dir, item))
                if not hasattr(module, "ACTIVATED_METRICS"):
                    LOGGER.warning("Plugin {0} appears to have no ACTIVATED_METRICS array"
                                   ", skipping".format(item))
                    continue
                for stat_class in module.ACTIVATED_METRICS:
                    class_instance = stat_class()
                    if hasattr(class_instance, "async_init"):
                        await class_instance.async_init()
                    stats_objects.append(class_instance)
                    LOGGER.debug("Loaded class {0} from {1}".format(stat_class.name, item))
                if module.ACTIVATED_METRICS:
                    LOGGER.info("Loaded plugin {0} successfully".format(item))
                else:
                    LOGGER.debug("Loaded plugin {0} with no activated metrics".format(item))
            except (Exception, trio.MultiError):
                exc = sys.exc_info()
                LOGGER.error(format_error(exc, message="Failed to import plugin {0}".format(item),
                                          message_before=True))
        stats_objects = {x.name:
                         dict(obj=x, errors={}, result=None, continuous=hasattr(x, "poll_stats"))
                         for x in stats_objects}
        BaseStat.collect_interval = collect_interval
        for item in stats_objects.values():
            if hasattr(item["obj"], "init_fetch"):
                await item["obj"].init_fetch()
    except (Exception, trio.MultiError):
        exc = sys.exc_info()
        critical_exit(exc, message="Initialisation failed")
    LOGGER.info("Initialised successfully")
    # ~5 mins of history
    metrics_send_channel, metrics_receive_channel = (
        trio.open_memory_channel(max(300 // collect_interval, 1))
    )
    cumulative_errors = dict(stats=0, influx=0)
    exit_event = trio.Event()
    # current behaviour is to only catch one signal
    # switch to weak/strong nursery for continued signals
    async with trio.open_nursery() as nursery:
        nursery.start_soon(handle_signals, exit_event)
        nursery.start_soon(stats_handler, args, exit_event, stats_objects,
                           metrics_send_channel, cumulative_errors)
        if not args["dry_run"]:
            nursery.start_soon(influx_write, client, influx_args["database"],
                               metrics_receive_channel, cumulative_errors)
    if pidfile is not None:
        LOGGER.debug("Removing pidfile")
        os.remove(pidfile)
    LOGGER.info("Exiting")

async def handle_signals(exit_event):
    """Handle SIGINT / SIGTERM, setting the exit event"""
    with trio.open_signal_receiver(signal.SIGINT, signal.SIGTERM) as signal_handler:
        async for _ in signal_handler:
            exit_event.set()
            LOGGER.info("Exit signal received")
            return


async def influx_write(client, database, metrics_receive_channel, cumulative_errors):
    """Writes stats from the metrics_receive_channel to influx"""
    async with metrics_receive_channel:
        async for data in metrics_receive_channel:
            LOGGER.debug("Beginning write to influx")
            try:
                await trio.to_thread.run_sync(
                    functools.partial(client.write_points, data, database=database)
                )
            except Exception:
                cumulative_errors["influx"] += 1
                exc = sys.exc_info()
                LOGGER.error(format_error(exc, message="Caught influx exception",
                                          message_before=True))
            else:
                cumulative_errors["influx"] = 0

#
# metrics collection
#

async def stats_handler(args, exit_event, stats_objects, metrics_send_channel, cumulative_errors):
    """Handles the collections of stats"""
    collect_interval = args["collect_interval"]
    error_limit = args["error_limit"]
    target_time = math.ceil(time.time() + 1)
    BaseStat.set_time(target_time)
    while True:
        try:
            start_error_count = cumulative_errors["stats"]
            if exit_event.is_set():
                LOGGER.debug("Stats handler acknowledged signal")
                break
            if max(cumulative_errors.values()) > error_limit > 0:
                LOGGER.critical("Exiting due to cumulative errors")
                break
            if time.time() > target_time - collect_interval:
                behind_secs = time.time() - target_time + collect_interval
                if behind_secs < collect_interval / 2:
                    level = logging.INFO
                else:
                    level = logging.WARNING
                LOGGER.log(level, "Running behind by {0:.2f}s".format(behind_secs))
                if behind_secs > collect_interval * 5:
                    LOGGER.critical("Running behind by more than {0} seconds, skipping data entry"
                                    .format(collect_interval * 5))
                    target_time = math.ceil(time.time() + 1)
                    BaseStat.set_time(target_time)
            await sleep_until(target_time - collect_interval)
            LOGGER.debug("Before stats collect, currently have {0:.3f}s until iter should finish"
                         .format(delta_current_time(target_time)))
            current_time = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(target_time))
            with trio.move_on_after(collect_interval * 2) as cancel_scope:
                await collect_stats(stats_objects, target_time)
            if cancel_scope.cancelled_caught:
                LOGGER.error("Collect took >2 collect_intervals, cancelled remaining collects")
                cumulative_errors["stats"] += 1
            LOGGER.debug("Stats collect finished, currently have {0:.3f}s until iter should finish"
                         .format(delta_current_time(target_time)))
            if any(stat_entry["errors"] for stat_entry in stats_objects.values()):
                cumulative_errors["stats"] += 1
                for name, stat_entry in stats_objects.items():
                    for action, error_info in stat_entry["errors"].items():
                        LOGGER.error(format_error(
                            error_info,
                            message="Error in stats collect for {0} ({1})".format(name, action),
                            message_before=True))
            write_data = []
            for name, stat_entry in stats_objects.items():
                result = stat_entry["result"]
                if result is not None:
                    if isinstance(result, dict):
                        format_dataset = format_measurements(result, current_time, name)
                        if format_dataset is not None:
                            write_data.append(format_dataset)
                    elif isinstance(result, list):
                        for dataset in result:
                            format_dataset = format_measurements(dataset, current_time, name)
                            if format_dataset is not None:
                                write_data.append(format_dataset)
            if not args["dry_run"]:
                await metrics_send_channel.send(write_data)
            else:
                print(write_data)
        except Exception:
            exc = sys.exc_info()
            LOGGER.error(format_error(exc, message="Caught exception", message_before=True))
            cumulative_errors["stats"] += 1
        finally:
            if start_error_count == cumulative_errors["stats"]:
                cumulative_errors["stats"] = 0
            target_time += collect_interval
            BaseStat.set_time(target_time)
    current_buffer_usage = metrics_send_channel.statistics().current_buffer_used
    while current_buffer_usage > 0:
        LOGGER.info("Waiting for influx writes, {0} in queue".format(current_buffer_usage))
        await trio.sleep(0.5)
        current_buffer_usage = metrics_send_channel.statistics().current_buffer_used
    # closing it causes influx_write to also exit
    await metrics_send_channel.aclose()


async def collect_stats(stats_objects, target_time):
    """Asynchronously fetches stats"""
    async with trio.open_nursery() as nursery:
        for name, stat_entry in stats_objects.items():
            nursery.start_soon(execute_collect, name, stat_entry, target_time)


async def execute_collect(name, stat_entry, target_time):
    """Executes collection of stats for a given object"""
    stat_object = stat_entry["obj"]
    stat_entry["errors"] = {}
    stat_entry["result"] = None
    start_time = getattr(stat_object, "time_needed", 0.2)
    mode = "poll"
    if stat_entry["continuous"]:
        try:
            LOGGER.debug("Starting {0} for {1}".format(mode, name))
            await stat_object.poll_stats()
        except (Exception, trio.MultiError):
            stat_entry["errors"][mode] = sys.exc_info()
    mode = "push"
    await sleep_until(target_time - start_time)
    try:
        LOGGER.debug("Starting {0} for {1}".format(mode, name))
        stat_entry["result"] = await stat_object.get_stats()
    except (Exception, trio.MultiError):
        stat_entry["errors"][mode] = sys.exc_info()


def format_measurements(dataset, current_time, name):
    """Takes a measurement dict and formats it for influxdb"""
    if "measurement" not in dataset:
        LOGGER.error("No measurement found for {0}".format(name))
        return None
    measurement = dataset.pop("measurement")
    if measurement is None:
        return None
    tags = dataset.pop("tags", {})
    if not dataset:
        return None
    return dict(measurement=measurement, time=current_time,
                fields=dataset, tags=tags)

#
# config handling
#


def initial_argparse():
    """Parses command line args"""
    log_levels = dict(debug=logging.DEBUG, info=logging.INFO, warning=logging.WARNING,
                      error=logging.ERROR, critical=logging.CRITICAL)
    cmd_args = collections.OrderedDict([
        ["config_file", dict(cmd_name="config-file", default=None, type=[None, str],
                             help="Specify path to config file. The command line options override "
                             "the config file. Example config file in example_config.yaml")],
        ["username", dict(cmd_name="username", default="root", type=str,
                          help="Username for influxdb. Default is root")],
        ["password", dict(cmd_name="password", default="root", type=str,
                          help="Password for influxdb. Default is root")],
        ["host", dict(cmd_name="host", default="localhost", type=str,
                      help="Host for influxdb. Default is localhost")],
        ["port", dict(cmd_name="port", default=8086, type=int,
                      help="Port for influxdb. Default is 8086")],
        ["database", dict(cmd_name="database", default="system_stats", type=str,
                          help="Database name for influxdb. Default is system_stats")],
        ["collect_interval", dict(cmd_name="collect-interval", default=1, type=int,
                                  help="Sets how often the stats are collected and saved, "
                                  "in seconds. Default is 1, must be a non zero integer")],
        ["include_disks", dict(cmd_name="include-disks", default=[], nargs="*", type=str,
                               help="Disks to include for disk IO monitoring. The disks specified "
                               "can be regular expressions, but they don't need to be as you can "
                               "specify as many disks as you want. Passing this option will cause "
                               "only the specified disks to be monitored. It cannot be used at the "
                               "same time as exclude disks. The regex '[p]?\\d+' can be "
                               "used to specify disk partitions (see example config). "
                               "Default is in exclude-disks.")],
        ["exclude_disks", dict(cmd_name="exclude-disks", default=[r"loop\d+"], nargs="*", type=str,
                               help="Disks to exclude for disk IO monitoring. The disks specified "
                               "can be regular expressions, but they don't need to be as you can "
                               "specify as many disks as you want. Passing this option will "
                               "exclude the specified disks from monitoring. It cannot be used at "
                               "the same time as include disks. The regex '[p]?\\d+' can be "
                               "used to specify disk partitions (see example config). "
                               "Default is exclude all loop devices.")],
        ["include_mountpoints", dict(cmd_name="include-mountpoints", default=[], nargs="*",
                                     type=str, help="Mountpoints to include for disk storage space "
                                     "monitoring. The mountpoints specified can be regular "
                                     "expressions, but they don't need to be as you can specify "
                                     "as many mountpoints as you want. Passing this option will "
                                     "cause only the specified mountpoints to be monitored. It "
                                     "cannot be used at the same time as exclude-mountpoints. "
                                     "Default is in exclude-mountpoints.")],
        ["exclude_mountpoints", dict(cmd_name="exclude-mountpoints", default=[], nargs="*",
                                     type=str, help="Mountpoints to exclude for disk storage space "
                                     "monitoring. The mountpoints specified can be regular "
                                     "expressions, but they don't need to be as you can specify "
                                     "as many mountpoints as you want. Passing this option will "
                                     "exclude the specified mountpoints from monitoring. It "
                                     "cannot be used at the same time as include-mountpoints. "
                                     "Default is exclude no mountpoints (ie include all).")],
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
                            help="Enables logging non critical events to stdout")],
        ["log_level", dict(cmd_name="log-level", default="info", type=str,
                           help="Set the loglevel for all logging. Default is info. "
                           "Available levels are {0}".format(", ".join(log_levels.keys())))],
        ["quiet", dict(cmd_name="quiet", default=False, type=bool, action="store_true",
                       help="Disables logging critical exits to stdout; complete silence")],
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
        if value is None or (cmd_args[key]["type"] is bool and value == cmd_args[key]["default"]):
            args[key] = cmd_args[key]["default"]
            specified[key] = False
        else:
            specified[key] = True
    if args["config_file"] is not None:
        args["config_file"] = os.path.expanduser(args["config_file"])
        args = parse_config_file(args, cmd_args, specified)
    if args["log_level"] not in log_levels.keys():
        critical_exit((TypeError, None, None), message="Invalid loglevel specified")
    ROOT_LOGGER.setLevel(log_levels[args["log_level"]])
    if args["log_stdout"] and args["quiet"]:
        critical_exit((TypeError, None, None),
                      message="Log stdout and quiet cannot be specified together")
    if args["exclude_disks"] != cmd_args["exclude_disks"]["default"] and args["include_disks"]:
        critical_exit((TypeError, None, None),
                      message="Disk includes and excludes cannot be specified together")
    if (args["exclude_mountpoints"] != cmd_args["exclude_mountpoints"]["default"]
            and args["include_mountpoints"]):
        critical_exit((TypeError, None, None),
                      message="Mountpoint includes and excludes cannot be specified together")
    if args["include_disks"]:
        args["disk_filters"] = [args["include_disks"], "include"]
    else:
        args["disk_filters"] = [args["exclude_disks"], "exclude"]
    args["disk_filters"][0] = [path.replace("/dev/", "") for path in args["disk_filters"][0]]
    if args["include_mountpoints"]:
        args["mountpoint_filters"] = [args["include_mountpoints"], "include"]
    else:
        args["mountpoint_filters"] = [args["exclude_mountpoints"], "exclude"]
    if args["quiet"]:
        logging.disable(logging.CRITICAL)
    if args["log_stdout"]:
        # first handler is the stdout critical error handler
        ROOT_LOGGER.handlers[0].level = logging.DEBUG
    if args["logfile_path"] is not None:
        args["logfile_path"] = os.path.expanduser(args["logfile_path"])
        ROOT_LOGGER.addHandler(create_sublogger(logging.DEBUG, args["logfile_path"]))
    if args["collect_interval"] <= 0:
        critical_exit((TypeError, None, None),
                      message="Collect interval must be a non zero positive integer")
    if args["pidfile"] is not None:
        args["pidfile"] = os.path.expanduser(args["pidfile"])
        open(args["pidfile"], "w").write(str(os.getpid()))
    return args


def parse_config_file(args, cmd_args, specifed):
    """Parses the config file and type checks it"""
    try:
        with open(args["config_file"], "r") as stream:
            try:
                args_new = yaml.safe_load(stream)
            except Exception:
                critical_exit((ValueError, None, None),
                              message="Config file is invalid (parse failed)")
    except FileNotFoundError:
        critical_exit((FileNotFoundError, None, None),
                      message="Config file cannot be opened (file not found)")
    args_new_formatted = {}
    lookup = {v["cmd_name"]: k for k, v in cmd_args.items()}
    for key, value in args_new.items():
        if key not in lookup:
            critical_exit((ValueError, None, None), message="Key \"{0}\" in config file is invalid"
                          .format(key))
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
                            error = "{0}s inside a list".format(allowed_type.__name__)
        if error:
            critical_exit((TypeError, None, None),
                          message="TypeError: Option {0} in config file is not type {1}"
                          .format(cmd_args[key]["cmd_name"], error))
        if not specifed[key]:
            args[key] = value
    return args

#
# entrypoint
#

if __name__ == "__main__":
    os.chdir(os.path.dirname(os.path.abspath(__file__)))
    if not os.path.exists("configured"):
        os.mkdir("configured")
    CONFIG = InternalConfig()
    logging.Formatter.converter = time.gmtime
    ROOT_LOGGER = logging.getLogger()
    ROOT_LOGGER.setLevel(logging.INFO)
    ROOT_LOGGER.addHandler(create_sublogger(logging.CRITICAL))
    LOGGER = logging.getLogger("system_metrics_influx")
    trio.run(initialise, initial_argparse())
    CONFIG.write_config()
