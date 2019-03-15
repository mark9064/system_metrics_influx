#!/usr/bin/env python3
"""Installs influx, grafana, dependencies and configures them"""
import os
import subprocess
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
    if CODENAME is None:
        print("ERROR: Ubuntu codename not detected."
              "\nPackages will need to be installed manually."
              "\nOnly config options are available")
    else:
        if answer_convert(input("Install influxdb? (y/n): ")):
            install_package_generic(name="influxdb",
                                    supported_archs=("x86_64", "armv7l", "arm64"),
                                    repo_link="https://repos.influxdata.com/ubuntu",
                                    repo_channel="stable",
                                    key_link="https://repos.influxdata.com/influxdb.key")
            print("Influxdb install done")
        if answer_convert(input("Install grafana? (y/n): ")):
            install_package_generic(name="grafana",
                                    supported_archs=("x86_64", "armv7l", "arm64"),
                                    repo_link="https://packages.grafana.com/oss/deb",
                                    override_codename="stable",
                                    repo_channel="main",
                                    key_link="https://packages.grafana.com/gpg.key")
            print("Grafana install done")
    if answer_convert(input("Install python dependencies? (y/n): ")):
        print("Installing python modules")
        subprocess.run("sudo pip3 install -r requirements.txt", shell=True)
    if answer_convert(input("Setup influxdb database? (y/n): ")):
        setup_influxdb()
        print("Influxdb config done")
    print("Exit")


def install_package_generic(name, supported_archs=None, override_codename=CODENAME,
                            repo_link=None, repo_channel=None, key_link=None, package_link=None):
    """Generic package install function"""
    if package_link is not None:
        install_type = "package"
    else:
        install_type = "repo"
    input("Only Ubuntu is supported by this script. Press enter to continue")
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
            return
    if apt_installed:
        if not apt_search(apt, name, install_type):
            return
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


def setup_influxdb():
    """Sets up config for influxdb"""
    print("Starting influxdb")
    subprocess.run("sudo systemctl start influxdb", shell=True)
    try:
        import influxdb
    except ImportError:
        print("Influxdb module not found, please install dependencies")
        return
    time.sleep(2)
    client = influxdb.InfluxDBClient()
    print("Creating database")
    client.create_database("system_stats")
    retention_time = input("Enter data rentention time. Minimum is 1h."
                           "\nm = minute, h = hour, d = day, w = week, INF for infinity"
                           "\nOnly 1 unit can be used. Recommended is 1w: ")
    client.create_retention_policy("stats_retention", retention_time, 1,
                                   database="system_stats", default=True)


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


def answer_convert(ans):
    """Converts a y/n to true/false"""
    if ans.lower() == "y":
        return True
    return False


if __name__ == "__main__":
    main()
