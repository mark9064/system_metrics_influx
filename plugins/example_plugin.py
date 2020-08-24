"""
Example plugin

To create a plugin:
- Read the docstring in system_metrics_influx.py for a design overview
- Choose a measurement name to add your stats to (either a new one or one in the docstring)
- Import BaseStat from common_lib
- Optional: import logging and create a logger with an appropriate name (don't use the root logger)
- Create class which subclasses BaseStat
    - BaseStat provides access to the target time and the collect interval, as well as the current time
    - To access these you can use self.target_time, self.collect_interval and self.current_time()
    - BaseStat inheritance is technically optional if you require none of these, but it is recommended to inherit by convention
- Set a name as a class attribute, this is used as a human readable name in debug output and errors
- Optional: add a time_needed class attribute if your get_stats needs more time to run
    - The poll_stats method is always started immediately regardless of this
- Optional: create an __init__ method for any immediate initialisation
- Optional: create an async_init method; use this if you have async initialisation to do
    - async_init is always called immediately after object initialisation
- Optional: create an init_fetch method; use this to initialise a value if you are tracking how it changes over time
- Create an async method called get_stats, this is where your plugin actually collects data
- Return collected data from get_stats, all data must be returned here
- Optional: add a poll_stats method; use this if you want to poll something for data (eg CPU clocks)
    - This method must return before the target time and give your get_stats enough time to run
- Add the created class to an array called ACTIVATED_METRICS in the main scope

Things to know when creating a plugin:
- If poll_stats is present, get_stats will be run even if poll_stats errors

If you have further questions, please do open an issue on GitHub; I'm happy to answer any queries :)
"""
import logging

from common_lib import BaseStat  # pylint: disable=no-name-in-module


class CustomModule(BaseStat):
    """Dummy user module"""
    name = "Custom" # human readable name for module
    def __init__(self):
        self.times = 0

    async def async_init(self):
        # any async initialisation that needs to happen after __init__ can happen here
        # eg connect an async socket
        # called immediately after object intialisation
        pass

    async def init_fetch(self):
        # fetch initial values just before stat collection starts
        # eg reading io counters so you have an initial value to reference from
        # so you can track how much a counter changes over time
        pass

    async def get_stats(self):
        """Dummy get stats function"""
        self.times += 1
        LOGGER.debug("test")
        # measurement: "name" defines which measurements your stats will go in
        return {"measurement": "dummy", "times_called": self.times}

LOGGER = logging.getLogger("example_plugin")

# this line would be "ACTIVATED_METRICS = [CustomModule]" to enable it
ACTIVATED_METRICS = []
