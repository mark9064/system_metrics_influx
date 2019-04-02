#!/usr/bin/env python3
"""Installs influx, grafana, dependencies and configures them"""
import json
import os
import pwd
import subprocess
import sys
import time


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


def main():
    """Main function"""
    os.mkdir("configured")
    if (hasattr(sys, "real_prefix") or
            (hasattr(sys, "base_prefix") and sys.base_prefix != sys.prefix)):
        print("Virtualenv detected, not using sudo for pip installs")
        pip_prefix = ""
    else:
        print("Virtualenv not detected, using sudo for pip installs")
        pip_prefix = "sudo "
    print("The influx/grafana installs are only tested/available on ubuntu")
    if CODENAME is None:
        print("ERROR: Ubuntu codename not detected."
              "\nPackages will need to be installed manually."
              "\nOnly config options are available")
    else:
        print("Ubuntu codename found: {0}".format(CODENAME))
        transport_installed = False
        if answer_convert(input("Install InfluxDB? (y/n): ")):
            apt_install("apt-transport-https")
            transport_installed = True
            ret = install_package_generic(name="influxdb",
                                          supported_archs=("x86_64", "armv7l", "arm64"),
                                          repo_link="https://repos.influxdata.com/ubuntu",
                                          repo_channel="stable",
                                          key_link="https://repos.influxdata.com/influxdb.key")
            print_status("InfluxDB install", ret)
        if answer_convert(input("Install Grafana? (y/n): ")):
            if not transport_installed:
                apt_install("apt-transport-https")
            ret = install_package_generic(name="grafana",
                                          supported_archs=("x86_64", "armv7l", "arm64"),
                                          repo_link="https://packages.grafana.com/oss/deb",
                                          override_codename="stable",
                                          repo_channel="main",
                                          key_link="https://packages.grafana.com/gpg.key")
            print_status("Grafana install", ret)
    if answer_convert(input("Install python dependencies? (y/n): ")):
        print("Installing python modules")
        subprocess.run("{0}{1} -m pip install -r requirements.txt"
                       .format(pip_prefix, sys.executable), shell=True)
    if answer_convert(input("Setup InfluxDB database? (y/n): ")):
        ret = setup_influxdb()
        print_status("InfluxDB config", ret)
    if answer_convert(input("Configure Grafana? (y/n): ")):
        ret = setup_grafana()
        print_status("Grafana config", ret)
    if os.path.isdir("/run/systemd/system/"):
        print("systemd detected, can be installed as service")
        print("You may want to postpone installing as a service until you have "
              "set up a config file (you must specify a path to a config file for the service, "
              "but the file does not need to exist yet)")
        if answer_convert(input("Install as a systemd service? (y/n): ")):
            ret = systemd_install()
            print_status("systemd install", ret)
    else:
        print("systemd not detected, install as service unavailable")
    print("Exit")


def install_package_generic(name, supported_archs=None, override_codename=CODENAME,
                            repo_link=None, repo_channel=None, key_link=None, package_link=None):
    """Generic package install function"""
    if package_link is not None:
        install_type = "package"
    else:
        install_type = "repo"
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
    apt_installed = True
    try:
        import apt
    except ImportError:
        print("Please check that python3-apt is installed."
              "\nThe python3-apt module is required to detect installed packages."
              "\nAPT itself still needs to be installed.")
        if answer_convert(input("Continue without the apt module? (y/n): ")):
            apt_installed = False
        else:
            return False
    if apt_installed:
        if not apt_search(apt, name, install_type):
            return False
    if install_type == "package":
        os.mkdir("temp_install_debs")
        os.chdir("temp_install_debs")
        print("Downloading {0}".format(name))
        subprocess.run("wget {0}".format(package_link), shell=True)
        deb = os.listdir()[0]
        print("Installing {0}".format(name))
        subprocess.run("sudo dpkg -i {0}".format(deb), shell=True)
        os.remove(deb)
        os.chdir("..")
        os.rmdir("temp_install_debs")
    else:
        print("Adding key")
        subprocess.run("sudo curl -sL {0} | sudo apt-key add -".format(key_link), shell=True)
        print("Adding repo")
        subprocess.run("echo 'deb {0} {1} {2}' | sudo tee /etc/apt/sources.list.d/{3}.list"
                       .format(repo_link, override_codename, repo_channel, name), shell=True)
        apt_install(name)
    return True


def setup_influxdb():
    """Sets up config for influxdb"""
    print("Starting influxdb")
    subprocess.run("sudo systemctl start influxdb", shell=True)
    try:
        import influxdb
    except ImportError:
        print("influxdb module not found, please install dependencies")
        return False
    time.sleep(2)
    client = influxdb.InfluxDBClient()
    print("Creating database")
    client.create_database("system_stats")
    retention_time = input("Enter data rentention time. Minimum is 1h."
                           "\nm = minute, h = hour, d = day, w = week, INF for infinity"
                           "\nOnly 1 unit can be used. Recommended is 1w: ")
    client.create_retention_policy("stats_retention", retention_time, 1,
                                   database="system_stats", default=True)
    return True


def setup_grafana():
    """Installs grafana plugins and generates a source file"""
    template_name = "data/grafana_template.json"
    out_name = "configured/grafana_configured.json"
    print("Installing plugins")
    subprocess.run("sudo grafana-cli plugins install grafana-clock-panel", shell=True)
    print("Restarting grafana")
    subprocess.run("sudo systemctl restart grafana-server", shell=True)
    print("Creating configured grafana dashboard")
    try:
        import psutil
    except ImportError:
        print("psutil module not found, please install dependencies")
        return False
    template = json.load(open(template_name, "r"))
    for index, item in enumerate(template["panels"]):
        if item["title"] == "CPU (%)":
            template["panels"][index]["yaxes"][0]["max"] = psutil.cpu_count() * 100
    json.dump(template, open(out_name, "w"), indent=2)
    print("Dashboard written to {0}".format(out_name))
    return True


def systemd_install():
    """Installs the app as a systemd service"""
    try:
        import yaml
    except ImportError:
        print("YAML module not found, please install dependencies")
        return False
    template_name = "data/systemd_template.txt"
    out_name = "configured/system_metrics_influx.service"
    write_path = "/etc/systemd/system/"
    path = input("Input path to config file (absolute path): ")
    try:
        save_rate = yaml.safe_load(open(path, "r"))["save-rate"]
    except Exception:
        save_rate = 1
    current_user = os.getuid()
    data = open(template_name).read()
    data = data.format(current_user, sys.executable, os.path.abspath("system_metrics_influx.py"),
                       path, save_rate)
    open(out_name, "w").write(data)
    print("systemd config written to {0}".format(out_name))
    print("The systemd service will be run as user {0} ({1})"
          .format(current_user, pwd.getpwuid(current_user).pw_name))
    if answer_convert(input("Install as service to {0} ? (y/n): ".format(write_path))):
        subprocess.run("sudo cp {0} {1}".format(out_name, write_path), shell=True)
        subprocess.run("sudo systemctl daemon-reload", shell=True)
        if answer_convert(input("Enable start at boot? (y/n): ")):
            subprocess.run("sudo systemctl enable system_metrics_influx", shell=True)
        if answer_convert(input("Start service now? (y/n): ")):
            subprocess.run("sudo systemctl start system_metrics_influx", shell=True)
        return True
    return False


def apt_install(package):
    """Installs a package using apt"""
    print("Updating apt index")
    subprocess.run("sudo apt update", shell=True)
    print("Installing {0}".format(package))
    subprocess.run("sudo apt install {0}".format(package), shell=True)


def apt_search(apt, package, install_type):
    """Checks if a package is installed using apt"""
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


if __name__ == "__main__":
    main()
