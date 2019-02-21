"""Add any extra metrics here"""
from system_stats_influx import BaseStat


class CustomModule(BaseStat):
    """Dummy user module"""
    def __init__(self):
        self.out_dict = {}

    async def get_stats(self):
        """Dummy get stats function"""


USER_MODULES = [CustomModule]
