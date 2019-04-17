"""
Example plugin

Each plugin can contain an unlimtied number of classes
All activated classes should be added to ACTIVATED_METRICS
BaseStat should be imported from common_lib as below
Logging can be done by getting a logger WITH a name specified
Logging handlers will be inherited automatically
Each class should have an init method that creates out_data, and should subclass BaseStat
The init method should also be used for setting up any other variables for delta stats
The target time (end time) is available from self.target_time due to the BaseStat class
See timing/control flow in the docstring in system_metric_influx.py
The data collected by the stat must be inside the out_data in the format out_data[key]
You must define the measurement with out_data["measurement"], if there is no measurement
an error will be raised
So for example you can add to the cpu table in influx by having out_data["my_value"],
with the measurement set to "cpu"
out_data can also be a list of these dicts in order to support multiple sets of tagged data
All stats functions MUST be async
The poll_stats function should return within save_rate - 0.2s (earlier is fine)
Running over this limit will cause the script to lag behind and issue running behind warnings
The poll stats function can push directly to out_data,
but it is recommended to do this inside get_stats
The get_stats function should return instantly (no polling) and push all stats to out_data
"""
import logging

from common_lib import BaseStat  # pylint: disable=no-name-in-module


class CustomModule(BaseStat):
    """Dummy user module"""
    name = "Custom" # human readable name for module
    def __init__(self):
        self.out_data = {"measurement": None} # disables pushing the stat to influx
        self.times = 0

    async def get_stats(self):
        """Dummy get stats function"""
        # LOGGER.debug("test")

LOGGER = logging.getLogger("example_plugin")

ACTIVATED_METRICS = [CustomModule]
