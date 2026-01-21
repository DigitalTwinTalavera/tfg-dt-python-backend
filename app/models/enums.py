"""
Enums for road network models.
Defines node and road types for the traffic simulation.
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
