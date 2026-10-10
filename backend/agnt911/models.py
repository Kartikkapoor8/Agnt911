"""Pydantic models shared by the vehicle agents, protocol, and fake world."""

from typing import Literal

from pydantic import BaseModel, Field

VehicleType = Literal["ambulance", "fire", "police"]
VehicleStatus = Literal["free", "en_route", "on_scene"]
IncidentType = Literal["medical", "fire", "crash", "hazard"]
IncidentStatus = Literal["open", "assigned", "resolved"]
ClaimType = Literal[
    "on_scene",
    "en_route",
    "resolved",
    "sector_clear",
    "available",
    "capability",
    "observation",
]


class Location(BaseModel):
    """A point on the map in decimal degrees."""

    lat: float = Field(ge=-90, le=90)
    lng: float = Field(ge=-180, le=180)


class Vehicle(BaseModel):
    """One emergency vehicle and its current state."""

    id: str
    type: VehicleType
    location: Location
    home_district: str
    status: VehicleStatus = "free"
    target_incident: str | None = None
    trust: float = 1.0
    untrusted: bool = False


class Incident(BaseModel):
    """One emergency call that needs responders."""

    id: str
    type: IncidentType
    severity: int = Field(ge=1, le=5)
    location: Location
    status: IncidentStatus = "open"
    assigned: list[str] = Field(default_factory=list)


class Claim(BaseModel):
    """A statement a vehicle makes to the fleet about itself or the world."""

    id: str
    tick: int
    vehicle: str
    type: ClaimType
    incident: str | None = None
    location: Location
    value: dict | None = None
    confidence: float = Field(ge=0, le=1)
    reasoning: str
    sig: str = ""


class Bid(BaseModel):
    """A vehicle's offer to respond to one incident."""

    vehicle: str
    incident: str
    eta_seconds: int = Field(ge=0)
    capability_match: float = Field(ge=0, le=1)
    cost_to_district: float = Field(ge=0)
    confidence: float = Field(ge=0, le=1)
    reasoning: str


class Vote(BaseModel):
    """A vehicle's vote for one option in a proposed plan."""

    vehicle: str
    incident: str
    option_index: int = Field(ge=0)
    reasoning: str


class Plan(BaseModel):
    """The outcome of negotiating one incident."""

    incident: str
    assigned: list[str] = Field(default_factory=list)
    round_reached: int = Field(ge=0)
    consensus: bool
    escalated: bool


class AgentOutput(BaseModel):
    """Everything one vehicle returns in one round."""

    claims: list[Claim] = Field(default_factory=list)
    bid: Bid | None = None
    vote: Vote | None = None


class World(BaseModel):
    """The full simulated city at one tick."""

    tick: int
    vehicles: dict[str, Vehicle]
    incidents: dict[str, Incident]
    recent_messages: list[Claim] = Field(default_factory=list)
    baseline: bool = False
