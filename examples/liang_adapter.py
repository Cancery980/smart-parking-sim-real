"""External-module wiring example. Replace the delegated functions with Liang's code.

The shipped implementation delegates to the working reference so the exact
external import seam can be exercised without claiming Liang wrote this code.
"""
from parking_hu.reference_planner import Planner as ReferencePlanner


class Planner:
    def __init__(self):
        self._implementation = ReferencePlanner()

    def plan(self, context: dict, request: dict) -> dict:
        return self._implementation.plan(context, request)

    def find_vehicle(self, context: dict, request: dict) -> dict:
        return self._implementation.find_vehicle(context, request)
