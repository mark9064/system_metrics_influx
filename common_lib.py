"""Common classes and methods for sharing between installer, main program and plugins"""
import os
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
