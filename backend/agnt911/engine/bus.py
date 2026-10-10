"""HMAC transport for existing Claim/Bid/Vote models, with replay protection."""

from collections import deque
import hashlib
import hmac
import json
import secrets

from ..models import Bid, Claim, Message, Vote


def canonical(message: Message) -> bytes:
    """Encode shared message fields consistently, excluding the signature."""
    return json.dumps(message.model_dump(exclude={"sig"}), sort_keys=True,
                      separators=(",", ":"), allow_nan=False).encode()


class MessageBus:
    """Bind signatures to a run, sender, sequence, and all existing message fields."""

    def __init__(self, vehicle_ids, run_id: str, history_limit: int = 500):
        """Generate fresh keys and bounded history for one run."""
        self.run_id = run_id
        self._keys = {vid: secrets.token_bytes(32) for vid in vehicle_ids}
        self._sent = {vid: 0 for vid in vehicle_ids}
        self._received = {vid: 0 for vid in vehicle_ids}
        self._history = deque(maxlen=history_limit)

    def _digest(self, message: Message, sequence: int) -> str:
        body = self.run_id.encode() + b"\0" + str(sequence).encode() + b"\0" + canonical(message)
        return hmac.new(self._keys[message.vehicle], body, hashlib.sha256).hexdigest()

    def publish(self, message: Message) -> Message:
        """Sign and accept a model in place before the protocol verifies or broadcasts it."""
        if not isinstance(message, (Claim, Bid, Vote)) or message.vehicle not in self._keys:
            raise ValueError("Unknown message or sender")
        if len(canonical(message)) > 16_384:
            raise ValueError("Message exceeds 16 KiB")
        sequence = self._sent[message.vehicle] + 1
        message.sig = f"{sequence}:{self._digest(message, sequence)}"
        self._sent[message.vehicle] = sequence
        self.accept(message)
        return message

    def verify(self, message: Message) -> bool:
        """Check authenticity without consuming a sequence number."""
        try:
            prefix, signature = message.sig.split(":", 1)
            sequence = int(prefix)
            if sequence <= 0 or message.vehicle not in self._keys:
                return False
            return hmac.compare_digest(signature, self._digest(message, sequence))
        except (AttributeError, KeyError, TypeError, ValueError, OverflowError):
            return False

    def accept(self, message: Message) -> None:
        """Drop invalid signatures and reused or out-of-order sender sequences."""
        if not self.verify(message):
            raise ValueError("Invalid message signature")
        sequence = int(message.sig.split(":", 1)[0])
        if sequence <= self._received[message.vehicle]:
            raise ValueError("Replayed or out-of-order message")
        self._received[message.vehicle] = sequence
        self._history.append(message.model_copy(deep=True))

    def history(self) -> list[Message]:
        """Return detached copies of the latest signed messages."""
        return [m.model_copy(deep=True) for m in self._history]


class SignedHistory(list):
    """Intercept protocol's existing list.append calls without changing protocol.py."""

    def __init__(self, history: list[Message], bus: MessageBus, decision: int, on_message):
        """Keep this decision's full working transcript and an audit callback."""
        super().__init__(history)
        self.bus, self.decision, self.on_message = bus, decision, on_message

    def append(self, message: Message) -> None:
        """Make claim IDs unique across decisions, sign, and then expose the message."""
        if isinstance(message, Claim):
            message.id = f"d{self.decision}-{message.id}"
        self.bus.publish(message)
        super().append(message)
        self.on_message(message)
