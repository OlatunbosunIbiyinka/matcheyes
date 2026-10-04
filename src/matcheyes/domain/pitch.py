"""Pitch geometry.

Coordinates are metres on a 105 x 68 pitch, expressed in the *acting team's attacking frame*:
x = 0 is the acting team's own goal line, x = 105 the opponent's goal line, y = 0 the acting
team's right touchline when attacking. Analytics therefore never need to know which way a team
is kicking in a given half.
"""

from pydantic import Field

from matcheyes.domain.base import DomainModel

PITCH_LENGTH_M = 105.0
PITCH_WIDTH_M = 68.0


class Location(DomainModel):
    x: float = Field(ge=0.0, le=PITCH_LENGTH_M)
    y: float = Field(ge=0.0, le=PITCH_WIDTH_M)

    def mirrored(self) -> "Location":
        """The same physical point seen from the opponent's attacking frame."""
        return Location(x=PITCH_LENGTH_M - self.x, y=PITCH_WIDTH_M - self.y)
