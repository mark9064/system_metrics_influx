"""Common classes and methods for sharing between installer, main program and plugins"""
import os
import traceback

import yaml


class InternalConfig:
    """Stores internal metrics config"""
    config_path = "configured/main.yaml"
    def __init__(self):
        self.load_config()

    def load_config(self):
        """Loads config from file. Loads empty defaults if file not present"""
        if os.path.exists(self.config_path):
            self.main = yaml.safe_load(open(self.config_path, "r"))
        else:
            self.main = {"nvidia_cards": {}, "nvidia_seen_cardnames": {}}

    def save_value(self, value_dict):
        """Saves a value to the internal config file"""
        self.main.update(value_dict)

    def write_config(self):
        """Writes config to file"""
        yaml.safe_dump(self.main, open(self.config_path, "w"))


class BaseStat:
    """Base stats class for shared methods"""
    save_rate = 0
    target_time = 0

    @classmethod
    def set_time(cls, target_time):
        """Sets the target time of the stats collection"""
        cls.target_time = target_time

def format_error(exc_info, message=""):
    """Returns a string of formatted exception info"""
    if message:
        message = "- {0} ".format(message)
    if exc_info[1] is not None:
        trace = ": {0}".format(exc_info[1])
    else:
        trace = ""
    if exc_info[2] is not None:
        cwd = os.getcwd()
        full_tb = traceback.extract_tb(exc_info[2])
        for index, frame_summary in enumerate(full_tb):
            if index == len(full_tb) - 1:
                lineno = frame_summary.lineno
                filename = frame_summary.filename
                if not index:
                    called_by = None
                else:
                    called_by = full_tb[index - 1].name
                break
            if not cwd in full_tb[index + 1].filename:
                # next level is invalid
                lineno = frame_summary.lineno
                filename = frame_summary.filename
                if not index:
                    called_by = None
                else:
                    called_by = full_tb[index - 1].name
                break

        if called_by is not None:
            called_by = "called by {0}".format(called_by)
        else:
            called_by = "no direct caller"
        filename = filename[filename.index(cwd):]
        line = "(L{0} in {1}, {2})".format(lineno, filename, called_by)
    else:
        line = ""
    return "{0} {1}{2}{3}".format(exc_info[0].__name__, message, line, trace)
