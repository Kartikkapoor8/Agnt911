"""In-process HMAC transport: authentication is not proof a claim is true."""

from collections import deque
from copy import deepcopy
import hashlib
import hmac
import json
import secrets


def canonical(value: dict) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


class MessageBus:
    def __init__(self, vehicle_ids, run_id: str, history_limit: int = 500):
        self.run_id = run_id
        self._keys = {vid: secrets.token_bytes(32) for vid in vehicle_ids}
        self._sequence = {vid: 0 for vid in vehicle_ids}
        self._received = {vid: 0 for vid in vehicle_ids}
        self._history = deque(maxlen=history_limit)

    def publish(self, sender: str, kind: str, payload: dict, round_number: int, time_s: float,
                tick_number: int = 0) -> dict:
        if sender not in self._keys:
            raise ValueError("Unknown sender")
        if kind not in {"proposal", "counter", "bid", "vote", "claim", "commit", "status"}:
            raise ValueError("Unknown message kind")
        if not isinstance(payload, dict) or len(canonical(payload)) > 16_384:
            raise ValueError("Payload must be a JSON object of at most 16 KiB")
        sequence = self._sequence[sender] + 1
        message = {"run_id": self.run_id, "sender": sender, "sequence": sequence,
                   "round": round_number, "tick": tick_number, "time_s": time_s, "kind": kind,
                   "payload": deepcopy(payload)}
        signature = hmac.new(self._keys[sender], canonical(message), hashlib.sha256).hexdigest()
        message["signature"] = signature
        self._sequence[sender] = sequence
        self.accept(message)
        return deepcopy(message)

    def verify(self, message: dict) -> bool:
        try:
            body = {k: v for k, v in message.items() if k != "signature"}
            if set(body) != {"run_id", "sender", "sequence", "round", "tick", "time_s", "kind", "payload"}:
                return False
            sender = body["sender"]
            if body["run_id"] != self.run_id or sender not in self._keys:
                return False
            expected = hmac.new(self._keys[sender], canonical(body), hashlib.sha256).hexdigest()
            return hmac.compare_digest(expected, message["signature"])
        except (KeyError, TypeError, ValueError, OverflowError):
            return False

    def accept(self, message: dict) -> None:
        if not self.verify(message):
            raise ValueError("Invalid message signature")
        sender, sequence = message["sender"], message["sequence"]
        if type(sequence) is not int or sequence <= self._received[sender]:
            raise ValueError("Replayed or out-of-order message")
        self._received[sender] = sequence
        self._history.append(deepcopy(message))

    def transcript(self) -> list[dict]:
        return deepcopy(list(self._history))
