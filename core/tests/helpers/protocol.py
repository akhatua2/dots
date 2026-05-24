"""Protocol doubles for tests."""

from __future__ import annotations


class RecordingConnection:
    """Minimal PeerConnection stand-in that records notifications."""

    def __init__(self):
        self.notifications: list[tuple[str, dict]] = []

    async def notify(self, kind: str, **payload):
        self.notifications.append((kind, payload))
