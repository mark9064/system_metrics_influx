"""Data logger to influxdb
Data:
cpu by core psutil.cpu_percent(percpu=True)
cpu by user, system, user, idle, nice, iowait psutil.cpu_times_percent()
cpu freq psutil.cpu_freq()
mem by obsoluted (pc not needed) psutil.virtual_memory() expressed in bytes
sys load (1, 5, 15) os.getloadavg()
network up/down psutil.net_io_counters() have to calc difference
disk i/o psutil.disk_io_counters() have to calc differnece
cpu temp psutil.sensors_temperatures()
disk usage (absolute) psutil.disk_usage("/")
3s import with 1s average polling
catergories:
    cpu
    mem
    diskio
    net
    misc
    temp
"""
#pylint: disable=E1101
import time
import signal
import statistics
import os
import influxdb
import psutil


class GracefulKiller:
    """Class to deal with SIGTERM and SIGINT"""
    kill_now = False
    def __init__(self):
        signal.signal(signal.SIGINT, self.exit_gracefully)
        signal.signal(signal.SIGTERM, self.exit_gracefully)

    def exit_gracefully(self, signum, frame):
        """Sets the kill_now var upon SIGTERM/SIGINT"""
        self.kill_now = True

class CpuStats:
    pass


def main():
    """Main function"""
    interrupt = GracefulKiller()
    user = "root"
    password = "root"
    database = "system_stats"
    host = "localhost"
    port = 8086
    cpu_fields = psutil.cpu_times_percent(interval=0.0)._fields
    last_disk_io = psutil.disk_io_counters()
    last_net_io = psutil.net_io_counters()
    last_ctx_int = psutil.cpu_stats()
    rate_last_time = time.time()
    client = influxdb.InfluxDBClient(host, port, user, password, database)
    time.sleep(1)
    while time.time() % 1 > 0.1:
        time.sleep(0.05)
    last_time = int(time.time())
    while True:
        try:
            if interrupt.kill_now:
                break
            cpu = dict(util=[], freq=[], times=[])
            misc = {}
            memory = {}
            disk = {}
            diskio = {}
            network = {}
            temps = {}
            while int(time.time()) < last_time + 1:
                cpu["util"].append(psutil.cpu_percent(percpu=True))
                cpu["freq"].append(int(psutil.cpu_freq().current))
                cpu["times"].append(psutil.cpu_times_percent(interval=0.0))
                #print("iter")
                time.sleep(0.2)
            temps["cpu"] = psutil.sensors_temperatures()
            misc["load"] = os.getloadavg()
            misc["processes"] = len(psutil.pids())
            misc["boot"] = psutil.boot_time()
            memory["memory"] = psutil.virtual_memory()
            disk["/"] = psutil.disk_usage("/")
            current_ctx_int = psutil.cpu_stats()
            cpu["extra_current"] = current_ctx_int
            cpu["extra_last"] = last_ctx_int
            cpu["extra_time"] = time.time() - rate_last_time
            last_ctx_int = current_ctx_int
            current_net_io = psutil.net_io_counters()
            network["current"] = current_net_io
            network["last"] = last_net_io
            network["time"] = time.time() - rate_last_time
            last_net_io = current_net_io
            current_disk_io = psutil.disk_io_counters()
            diskio["current"] = current_disk_io
            diskio["last"] = last_disk_io
            diskio["time"] = time.time() - rate_last_time
            last_disk_io = current_disk_io
            rate_last_time = time.time()
            last_time = int(time.time())
            current_time = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime())
            fields = dict(cpu={}, misc={}, memory={}, disk={}, diskio={}, network={}, temps={})
            #print(cpu)
            for key, value in cpu.items():
                if key == "util":
                    temp = [statistics.mean(x) for x in zip(*value)]
                    for index, item in enumerate(temp):
                        fields["cpu"]["cpu{0}".format(index)] = round(item, 2)
                elif key == "freq":
                    fields["cpu"]["freq"] = statistics.mean(value)
                elif key == "times":
                    temp = [statistics.mean(x) for x in zip(*value)]

                    for index, item in enumerate(temp):
                        if cpu_fields[index] in ("user", "system", "iowait", "nice"):
                            fields["cpu"][cpu_fields[index]] = round(item, 2)
                        elif cpu_fields[index] == "irq":
                            fields["cpu"][cpu_fields[index]] = round(item + temp[cpu_fields.index("softirq")], 2)
                    #fields["cpu"].update({"user": value.user, "system": value.system, "iowait": value.iowait, "nice": value.nice, "irq": value.irq + value.softirq})
                elif key == "extra_current":
                    ctx_switches = (value.ctx_switches - cpu["extra_last"].ctx_switches) / cpu["extra_time"]
                    interrupts = (value.interrupts - cpu["extra_last"].interrupts) / cpu["extra_time"]
                    fields["cpu"]["ctx_switches"] = int(round(ctx_switches))
                    fields["cpu"]["interrupts"] = int(round(interrupts))
            for key, value in misc.items():
                if key == "load":
                    fields["misc"]["load_1"], fields["misc"]["load_5"], fields["misc"]["load_15"] = value
                elif key == "processes":
                    fields["misc"]["processes"] = value
                elif key == "boot":
                    fields["misc"]["uptime"] = int(time.time() - value)
            for key, value in memory.items():
                if key == "memory":
                    fields["memory"]["total"] = value.total
                    fields["memory"]["used"] = value.total - value.available
                    fields["memory"]["percent"] = value.percent
            for key, value in disk.items():
                if key == "/":
                    fields["disk"]["total"] = value.total
                    fields["disk"]["used"] = value.used
                    fields["disk"]["percent"] = value.percent
            for key, value in diskio.items():
                if key == "current":
                    read_diff = (value.read_bytes - diskio["last"].read_bytes) / diskio["time"]
                    write_diff = (value.write_bytes - diskio["last"].write_bytes) / diskio["time"]
                    fields["diskio"]["read"] = int(round(read_diff))
                    fields["diskio"]["write"] = int(round(write_diff))
            for key, value in network.items():
                if key == "current":
                    recv_diff = (value.bytes_recv - network["last"].bytes_recv) / network["time"]
                    send_diff = (value.bytes_sent - network["last"].bytes_sent) / network["time"]
                    fields["network"]["rx"] = int(round(recv_diff))
                    fields["network"]["tx"] = int(round(send_diff))
            for key, value in temps.items():
                if key == "cpu":
                    fields["temps"]["cpu"] = value["armada_thermal"][0].current
            write_data = []
            for key, value in fields.items():
                write_data.append(dict(measurement=key, time=current_time, fields=value))
            client.write_points(write_data)
        except Exception:
            continue

if __name__ == "__main__":
    PID_FILE = open("stats_daemon.pid", "w")
    PID_FILE.write(str(os.getpid()))
    PID_FILE.close()
    main()
