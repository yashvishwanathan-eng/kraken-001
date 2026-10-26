"""The listener's calls to the API (on this laptop). Header X-Internal-Key; the API acts as FRIDAY_USER."""

import httpx


class ApiClient:
    def __init__(self, base_url: str, key: str, timeout_s: float = 20.0) -> None:
        self.http = httpx.Client(base_url=base_url.rstrip("/"), headers={"X-Internal-Key": key}, timeout=timeout_s)

    def event(self, state: str) -> dict:
        response = self.http.post("/internal/friday/event", json={"state": state})
        response.raise_for_status()
        return response.json()

    def voice_command(self, wav: bytes, confirm_token: str | None = None) -> dict:
        data = {"confirm_token": confirm_token} if confirm_token else None
        response = self.http.post("/internal/friday/voice-command", files={"audio": ("order.wav", wav, "audio/wav")},
                                  data=data)
        response.raise_for_status()
        return response.json()

    def speak(self, text: str) -> bytes:
        response = self.http.post("/internal/friday/speak", json={"text": text})
        response.raise_for_status()
        return response.content
