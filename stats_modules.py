"""Add any extra metrics here

Each module should have an init method that creates out_dict, and should subclass BaseStat
The init method should also be used for setting up any other variables for delta stats
The target time (end time) is available from self.target_time due to the BaseStat class
See timing/control flow in the docstring in system_metric_influx.py
The data collected by the stat must be inside the outdict in the format outdict[table][key]
So for example you can add to the cpu table in influx by having outdict["cpu"][custom_value]
All stats functions MUST be async
The poll_stats function should return within save_rate - 0.2s (earlier is fine)
Running over this limit will cause the script to lag behind and issue running behind warnings
The poll stats function can push directly to out_dict but it is recommended to this inside get_stats
The get_stats function should return instantly (no polling) and push all stats to the out_dict"""
from system_metrics_influx import BaseStat


class CustomModule(BaseStat):
    """Dummy user module"""
    name = "Custom" # human readable name for module
    def __init__(self):
        self.out_dict = {}

    async def get_stats(self):
        """Dummy get stats function"""


USER_MODULES = [CustomModule]
