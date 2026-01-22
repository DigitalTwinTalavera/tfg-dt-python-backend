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


class RoadType(str, Enum):
    """Types of roads/edges in the network."""

    MOTORWAY = "motorway"
    PRIMARY = "primary"
    SECONDARY = "secondary"
    TERTIARY = "tertiary"
    RESIDENTIAL = "residential"
    SERVICE = "service"
    PEDESTRIAN = "pedestrian"
    CYCLEWAY = "cycleway"


class VehicleStatus(str, Enum):
    """Status of a vehicle in the simulation."""

    IDLE = "idle"  # Vehicle is not moving, waiting to start
    MOVING = "moving"  # Vehicle is actively moving
    STOPPED = "stopped"  # Vehicle has stopped (e.g., at traffic light)
    WAITING = "waiting"  # Vehicle is waiting (e.g., in queue)
    FINISHED = "finished"  # Vehicle has completed its route
