#!/usr/bin/env python3
"""Installs influx, grafana, dependencies and configures them"""
import copy
import json
import os
import io
import pwd
import shutil
import string
import subprocess
import sys
import time
from collections import OrderedDict
from operator import itemgetter


def find_codename():
    """Finds the ubuntu codename"""
    info = open("/etc/os-release", "r")
    for line in info.readlines():
        if "UBUNTU_CODENAME" in line:
            codename = line.split("=")[1].strip()
            break
    else:
        codename = None
    info.close()
    return codename


CODENAME = find_codename()
RUNNING_AS_ROOT = os.getuid() == 0


def main():
    """Main function"""
    if not os.path.exists("configured"):
        os.mkdir("configured")
    if RUNNING_AS_ROOT:
        print("Being run as root, not using sudo")
        pip_prefix = ""
    elif (hasattr(sys, "real_prefix") or
          (hasattr(sys, "base_prefix") and sys.base_prefix != sys.prefix)):
        print("Virtualenv detected, not using sudo for pip installs")
        pip_prefix = ""
    else:
        print("Virtualenv not detected, using sudo for pip installs")
        pip_prefix = "sudo "
    required_deps = True
    apt_installed = shutil.which("apt")
    root_available = True
    if shutil.which("sudo") is None and not RUNNING_AS_ROOT:
        print("Sudo is not installed and not being run as root, "
              "only config options are available")
        root_available = False
    for item in ["curl", ["pip3", "python3-pip"]]:
        if isinstance(item, list):
            package_name = item[1]
            item = item[0]
        else:
            package_name = item
        if shutil.which(item) is None:
            if apt_installed and root_available:
                print("{0} not installed, installing now".format(item))
                if not install_package_generic(name=package_name):
                    required_deps = False
            else:
                print("{0} not installed, only config options are available".format(item))
                required_deps = False
    print("The influxdb/grafana installs are only tested/available on ubuntu")
    if CODENAME is None:
        print("ERROR: Ubuntu codename not detected."
              "\nPackages will need to be installed manually."
              "\nOnly config options are available")
    elif required_deps and root_available:
        print("Ubuntu codename found: {0}".format(CODENAME))
        transport_installed = False
        if answer_convert(input("Install influxdb? (y/n): ")):
            install_package_generic(name="apt-transport-https")
            transport_installed = True
            ret = install_package_generic(name="influxdb",
                                          supported_archs=("x86_64", "armv7l", "arm64"),
                                          repo_link="https://repos.influxdata.com/ubuntu",
                                          repo_channel="stable",
                                          key_link="https://repos.influxdata.com/influxdb.key")
            print_status("Influxdb install", ret)
        if answer_convert(input("Install grafana? (y/n): ")):
            if not transport_installed:
                install_package_generic(name="apt-transport-https")
            ret = install_package_generic(name="grafana",
                                          supported_archs=("x86_64", "armv7l", "arm64"),
                                          repo_link="https://packages.grafana.com/oss/deb",
                                          override_codename="stable",
                                          repo_channel="main",
                                          key_link="https://packages.grafana.com/gpg.key")
            print_status("Grafana install", ret)
    if required_deps:
        if answer_convert(input("Install python dependencies? (y/n): ")):
            print("Installing python modules")
            if pip_install("-r requirements.txt", pip_prefix):
                print("Done")
            else:
                print("Error installing python modules")
        print("If a new nvidia gpu has been installed, re-setup nvidia gpu support and"
              " reconfigure grafana")
        if answer_convert(input("Setup nvidia GPU support? "
                                "(only do this if you have an nvidia GPU) (y/n): ")):
            ret = setup_nvidia(pip_prefix)
            print_status("Nvidia config", ret)
    else:
        print("Python dependencies will need to be installed manually")
    if answer_convert(input("Setup influxdb database? (y/n): ")):
        ret = setup_influxdb()
        print_status("Infuxdb config", ret)
    if answer_convert(input("Configure grafana? (y/n): ")):
        ret = setup_grafana()
        print_status("Grafana config", ret)
    if root_available:
        if os.path.isdir("/run/systemd/system/"):
            print("Systemd detected, can be installed as service")
            print("You may want to postpone installing as a service until you have "
                  "set up a config file (you must specify a path to a config file for the service, "
                  "but the file does not need to exist yet)")
            if answer_convert(input("Install as a systemd service? (y/n): ")):
                ret = systemd_install()
                print_status("Systemd install", ret)
        else:
            print("Systemd not detected, install as service unavailable")
    else:
        print("Root/sudo required for systemd install")
    print("Exit")


def install_package_generic(name, supported_archs=None, override_codename=CODENAME,
                            repo_link=None, repo_channel=None, key_link=None, package_link=None):
    """Generic package install function"""
    if package_link is not None:
        install_type = "package"
    elif repo_link is not None:
        install_type = "repo"
    else:
        install_type = "simple"
    arch = os.uname().machine
    if supported_archs is not None:
        if arch not in supported_archs:
            if len(supported_archs) == 1:
                format_string = "{0} is".format(supported_archs[0])
            else:
                format_string = "{0} are".format(",".join(supported_archs))
            print("The processor architecture is not supported for this package."
                  "\nOnly {0} currently supported."
                  "\nIt may be possible to install {1} manually".format(format_string, name))
            return False
    if PYTHON3_APT:
        if not apt_search(name, install_type):
            return False
    if install_type == "package":
        os.mkdir("temp_install_debs")
        os.chdir("temp_install_debs")
        print("Downloading {0}".format(name))
        if not run_command("curl -O {0}".format(package_link), sudo=False):
            print("Failed to download package")
            return False
        deb = os.listdir()[0]
        print("Installing {0}".format(name))
        ret = run_command("dpkg -i {1}".format(deb))
        os.remove(deb)
        os.chdir("..")
        os.rmdir("temp_install_debs")
        if not ret:
            print("Package install failed")
            return False

    elif install_type == "repo":
        print("Adding key")
        if not run_command(
                "{0}curl -sL {1} | {0}apt-key add -".format(sudo_prefix(), key_link), sudo=False
        ):
            print("Failed to add key")
            return False
        print("Adding repo")
        if not run_command(
                "echo 'deb {0} {1} {2}' | {3}tee /etc/apt/sources.list.d/{4}.list"
                .format(repo_link, override_codename, repo_channel, sudo_prefix(), name), sudo=False
        ):
            print("Failed to add repo")
        if not apt_install(name):
            print("Error during apt install")
            return False
    elif install_type == "simple":
        if not apt_install(name):
            print("Error during apt install")
            return False
    return True


def check_apt_module():
    """Checks if python3-apt is installed"""
    # pylint: disable=unused-import
    try:
        import apt
    except ImportError:
        print("Please check that python3-apt is installed."
              "\nThe python3-apt module is required to detect installed packages."
              "\nAPT itself still needs to be installed."
              "\nIf running with a virtualenv, python3-apt will not be available."
              "\nPackage checking has been disabled")
        return False
    return True


def setup_nvidia(pip_prefix):
    """Installs nvidia features"""
    from common_lib import InternalConfig
    try:
        import py3nvml
    except ImportError:
        print("Installing py3nvml")
        if not pip_install("py3nvml", pip_prefix):
            print("Py3nvml install failed")
            return False
    import py3nvml.py3nvml as py3nvml
    config = InternalConfig()
    py3nvml.nvmlInit()
    if config.main["nvidia_cards"]:
        if answer_convert(input("Clear current card list? (y/n): ")):
            for key in ["nvidia_cards", "nvidia_seen_cardnames"]:
                config.main[key] = {}
    device_count = py3nvml.nvmlDeviceGetCount()
    if device_count == 0:
        print("Nvidia driver loaded but no devices found")
    for item in range(device_count):
        handle = py3nvml.nvmlDeviceGetHandleByIndex(item)
        uuid = str(py3nvml.nvmlDeviceGetUUID(handle))
        card_name = str(py3nvml.nvmlDeviceGetName(handle))
        if uuid not in config.main["nvidia_cards"]:
            print("New nvidia card detected: {0}".format(card_name))
            if card_name in config.main["nvidia_seen_cardnames"]:
                n_cards = config.main["nvidia_seen_cardnames"][card_name]
                if n_cards == 1:
                    for ext_uuid, ext_card_name in config.main["nvidia_cards"].items():
                        if card_name == ext_card_name:
                            config.main["nvidia_cards"][ext_uuid] = "{0} 0".format(card_name)
                            config.main["nvidia_cards"][uuid] = "{0} 1".format(card_name)
                            break
                else:
                    config.main["nvidia_cards"][uuid] = "{0} {1}".format(card_name, n_cards)
                config.main["nvidia_seen_cardnames"][card_name] += 1
            else:
                config.main["nvidia_cards"][uuid] = card_name
                config.main["nvidia_seen_cardnames"][card_name] = 1
    print("All configured cards:")
    for pair in config.main["nvidia_cards"].items():
        print("UUID {0}: {1}".format(*pair))
    for uuid, name in config.main["nvidia_cards"].items():
        if answer_convert(input("Rename {0}? ({1}) (y/n): ".format(name, uuid))):
            new_name = input("Enter new name for {0}: ".format(name))
            if new_name in config.main["nvidia_cards"].values():
                print("This name is already in use")
            else:
                config.main["nvidia_cards"][uuid] = new_name
    config.write_config()
    return True


def setup_influxdb():
    """Sets up config for influxdb"""
    print("Starting influxdb")
    if not run_command("systemctl start influxdb"):
        print("Influxdb failed to start")
        return False
    try:
        import influxdb
        import requests.exceptions
    except ImportError:
        print("Influxdb module not found, please install dependencies")
        return False

    client = influxdb.InfluxDBClient()

    name = input("Enter database name (recommended is 'system_stats'): ")
    print("Creating database... (on slow systems this may take a while, 60s timeout)")
    start = time.monotonic()
    while time.monotonic() - 60 < start:
        try:
            client.create_database(name)
        except requests.exceptions.RequestException:
            time.sleep(5)
        except (influxdb.exceptions.InfluxDBClientError, influxdb.exceptions.InfluxDBServerError):
            print("Influxdb could not create the database")
            return False
        else:
            break
    else:
        print("Database creation timed out")
        return False
    retention_time = input("Enter data rentention time. Minimum is 1h."
                           "\nm = minute, h = hour, d = day, w = week, INF for infinity"
                           "\nOnly 1 unit can be used. Recommended is 1w: ")
    # no need to error check this as if the database creation succeeded the connection should be ok
    client.create_retention_policy("stats_retention", retention_time, 1,
                                   database=name, default=True)
    return True


def setup_grafana():
    """Installs grafana plugins and generates a source file"""
    from common_lib import InternalConfig
    template_name = "data/grafana_template.json"
    out_name = "configured/grafana_configured.json"
    if answer_convert(input("Install required plugins? (y/n): ")):
        print("Installing plugins")
        if not run_command("grafana-cli plugins install grafana-clock-panel"):
            print("Error installing plugins")
            return False
        print("Restarting grafana")
        run_command("systemctl restart grafana-server")
    if answer_convert(input("Enable starting grafana at boot (using systemd) ? (y/n): ")):
        run_command("systemctl daemon-reload")
        run_command("systemctl enable grafana-server")
    print("Creating configured grafana dashboard")
    try:
        import psutil
    except ImportError:
        print("Psutil module not found, please install dependencies")
        return False
    template = json.load(open(template_name, "r"))
    config = InternalConfig()
    out_config = copy.deepcopy(template)
    index_shift = 0
    current_y = 0
    current_y_shift_performed = False
    y_shift_next = 0
    current_y_shift = 0
    nvidia_cardlist = OrderedDict(sorted(config.main["nvidia_cards"].items(), key=itemgetter(1)))
    if len(nvidia_cardlist) > 6:
        raise NotImplementedError("Having more than 6 cards is currently not implemented")
    for index, item in enumerate(template["panels"]):
        new_y = item["gridPos"]["y"]
        if new_y != current_y:
            current_y_shift_performed = False
            current_y = new_y
            current_y_shift = y_shift_next
        out_config["panels"][index - index_shift]["gridPos"]["y"] = new_y - current_y_shift
        if item["title"] == "CPU (%)":
            out_config["panels"][index - index_shift]["yaxes"][0]["max"] = psutil.cpu_count() * 100
        if item["title"] in ("GPU utilisation", "GPU memory usage",
                             "GPU temperature / fanspeed", "GPU frequencies",
                             "GPU power usage"):
            if not nvidia_cardlist:
                del out_config["panels"][index - index_shift]
                index_shift += 1
                if not current_y_shift_performed:
                    current_y_shift_performed = True
                    y_shift_next += item["gridPos"]["h"]
                continue
            target_templates = item["targets"]
            out_config["panels"][index - index_shift]["targets"] = []
            query_letter_index = 0
            for uuid, name in nvidia_cardlist.items():
                for target in target_templates:
                    target = copy.deepcopy(target)
                    target["alias"] = "{0} {1}".format(name, target["alias"])
                    target["refId"] = string.ascii_uppercase[query_letter_index]
                    query_letter_index += 1
                    target["tags"][0]["value"] = uuid
                    out_config["panels"][index - index_shift]["targets"].append(target)
    json.dump(out_config, open(out_name, "w"), indent=2)
    print("Dashboard written to {0}".format(out_name))
    requests = None
    try:
        import requests
    except ImportError:
        print("The requests package must be installed to automatically install dashboards")
        print("Please install dependencies")
        print("Dashboard auto install skipped as requests is not present")
        return False
    if answer_convert(input("Install/update dashboard now? (y/n): ")):
        print("Starting grafana")
        run_command("systemctl restart grafana-server")
        username = input("Enter grafana username (default 'admin'): ")
        password = input("Enter grafana password (default 'admin'): ")
        if answer_convert(input("Create datasource? This only needs to be done the "
                                "the first time the dashboard is installed (y/n): ")):
            datasource_config = copy.deepcopy(INFLUX_DATASOURCE)
            datasource_config["user"] = input("Enter influxdb username (default 'root'): ")
            datasource_config["password"] = input("Enter influxdb password (default 'root'): ")
            datasource_config["jsonData"]["timeInterval"] = (
                input("Enter desired data collection interval (default '1s'). "
                      "Remember to include the unit: ")
            )
            datasource_config["database"] = input("Enter influxdb database name "
                                                  "(default 'system_stats'): ")
            response = requests.post("http://{0}:{1}@localhost:3000/api/datasources"
                                     .format(username, password), json=datasource_config)
            if response.status_code == 200:
                print("Datasource setup successfully")
            else:
                print("Datasource setup failed (grafana returned HTTP code {0})"
                      .format(response.status_code))
                print_response_error(response)
                return False
        datasource_list = requests.get("http://{0}:{1}@localhost:3000/api/datasources"
                                       .format(username, password))
        if datasource_list.status_code == 200:
            print("Current datasources:")
            for item in datasource_list.json():
                print(" - {0}".format(item["name"]))
        else:
            print("Failed to fetch a list of the current datasources")
            print_response_error(datasource_list)
        datasource = input("Enter datasource name (default is 'InfluxDB'): ")
        for panel in out_config["panels"]:
            panel["datasource"] = datasource
        out_config["__inputs"][0]["name"] = datasource
        dashboard = dict(dashboard=out_config, folderId=0, overwrite=True)
        response = requests.post("http://{0}:{1}@localhost:3000/api/dashboards/db"
                                 .format(username, password), json=dashboard)
        if response.status_code == 200:
            print("Install successful\nThe dashboard is located at "
                  "http://localhost:3000{0}".format(response.json()["url"]))
        else:
            print("Dashboard install failed (grafana returned HTTP code {0})"
                  .format(response.status_code))
            print_response_error(response)
            return False
    return True


def systemd_install():
    """Installs the app as a systemd service"""
    template_name = "data/systemd_template.txt"
    out_name = "configured/system_metrics_influx.service"
    write_path = "/etc/systemd/system/"
    path = expand_path(input("Input path to config file: "))
    current_user = os.getuid()
    data = open(template_name).read()
    data = data.format(current_user, sys.executable, expand_path("system_metrics_influx.py"), path)
    open(out_name, "w").write(data)
    print("Systemd config written to {0}".format(out_name))
    print("The systemd service will be run as user {0} ({1})"
          .format(current_user, pwd.getpwuid(current_user).pw_name))
    if answer_convert(input("Install as service to {0} ? (y/n): ".format(write_path))):
        run_command("cp {0} {1}".format(out_name, write_path))
        run_command("systemctl daemon-reload")
        if answer_convert(input("Enable start at boot? (y/n): ")):
            run_command("systemctl enable system_metrics_influx")
        if answer_convert(input("Start service now? (y/n): ")):
            run_command("systemctl start system_metrics_influx")
        print("Service name is system_metrics_influx")
        return True
    return False


def apt_install(package):
    """Installs a package using apt"""
    print("Updating apt index")
    if not run_command("apt-get update"):
        return False
    print("Installing {0}".format(package))
    return run_command("apt-get -y install {0}".format(package))


def run_command(command, sudo=not RUNNING_AS_ROOT):
    """Runs a command with the option to run it as root and check the returncode"""
    if sudo:
        command = "sudo {0}".format(command)
    rotations = r"-\|/"
    rotate_index = 0
    with subprocess.Popen(command, shell=True, stdout=subprocess.PIPE) as proc:
        for _ in io.TextIOWrapper(proc.stdout):
            print("Working {0}".format(rotations[rotate_index]), end="\r", flush=True)
            rotate_index = (rotate_index + 1) % 4
    # clear with ansi escape code
    print("\x1b[2K", end="\r")
    return check_retcode(proc)


def pip_install(args, pip_prefix):
    """Installs a pip package"""
    return run_command("{0}{1} -m pip install {2}"
                       .format(pip_prefix, sys.executable, args), sudo=False)


def check_retcode(process):
    """Checks the process return code"""
    if process.returncode == 0:
        return True
    return False

def sudo_prefix():
    """Returns whether to use sudo by checking if running as root"""
    if RUNNING_AS_ROOT:
        return ""
    return "sudo "

def apt_search(package, install_type):
    """Checks if a package is installed using apt"""
    import apt
    if install_type == "simple":
        install_type = "package"
    cache = apt.Cache()
    if package in cache:
        if cache[package].is_installed:
            print("{0} is already installed.".format(package.capitalize()))
            if not answer_convert(input("Should the {0} {1} still be installed/updated? (y/n): "
                                        .format(package, install_type))):
                return False
    return True


def print_status(message, ret):
    """Prints the status of an install using the retcode"""
    if ret:
        print("{0} done".format(message))
    else:
        print("{0} cancelled/failed".format(message))


def answer_convert(ans):
    """Converts a y/n to true/false"""
    if ans.lower() == "y":
        return True
    return False

def print_response_error(response):
    """Prints the response text for debugging"""
    print("Error debugging information: {0}".format(response.text))

def expand_path(path):
    """Expands a path fully"""
    return os.path.abspath(os.path.expanduser(path))

PYTHON3_APT = check_apt_module()
INFLUX_DATASOURCE = {"name": "InfluxDB", "type": "influxdb", "access": "proxy",
                     "url": "http://localhost:8086", "basicAuth": False, "isDefault": True,
                     "jsonData": {}, "readOnly": False}

if __name__ == "__main__":
    main()
