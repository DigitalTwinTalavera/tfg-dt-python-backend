"""
Enums for traffic simulation models.
Defines node types, road types, and vehicle statuses.
"""

from enum import Enum


class NodeType(str, Enum):
    """Types of nodes in the road network."""

    INTERSECTION = "intersection"
    ENDPOINT = "endpoint"
    TRAFFIC_LIGHT = "traffic_light"
    ROUNDABOUT = "roundabout"
    DEAD_END = "dead_end"
    ENTRY_POINT = "entry_point"
    EXIT_POINT = "exit_point"
    STOP_SIGN = "stop_sign"
    YIELD_SIGN = "yield_sign"


class RoadType(str, Enum):
    """Types of roads/edges in the network (based on OSM highway tags)."""

    # Major roads
    MOTORWAY = "motorway"
    MOTORWAY_LINK = "motorway_link"
    TRUNK = "trunk"
    TRUNK_LINK = "trunk_link"
    PRIMARY = "primary"
    PRIMARY_LINK = "primary_link"
    SECONDARY = "secondary"
    SECONDARY_LINK = "secondary_link"
    TERTIARY = "tertiary"
    TERTIARY_LINK = "tertiary_link"

    # Minor roads
    RESIDENTIAL = "residential"
    SERVICE = "service"
    UNCLASSIFIED = "unclassified"
    LIVING_STREET = "living_street"

    # Non-vehicle roads
    PEDESTRIAN = "pedestrian"
    CYCLEWAY = "cycleway"

    @classmethod
    def from_osm_highway(cls, highway: str) -> "RoadType":
        """
        Convert OSM highway tag to RoadType enum.

        Args:
            highway: OSM highway tag value

        Returns:
            Matching RoadType, defaults to UNCLASSIFIED if unknown
        """
        try:
            return cls(highway)
        except ValueError:
            return cls.UNCLASSIFIED


class VehicleStatus(str, Enum):
    """Status of a vehicle in the simulation."""

    IDLE = "idle"  # Vehicle is not moving, waiting to start
    MOVING = "moving"  # Vehicle is actively moving
    STOPPED = "stopped"  # Vehicle has stopped (e.g., at traffic light)
    WAITING = "waiting"  # Vehicle is waiting (e.g., in queue)
    FINISHED = "finished"  # Vehicle has completed its route
    COLLISION = "collision"  # Vehicle is involved in a collision
    PAUSED = "paused"  # Vehicle is manually paused
