"""Wake-word detection: the trigger rules, and a listener that runs them on the microphone.

Two parts:
  WakeTrigger  pure rules (score >= threshold, then a quiet cooldown). No audio, so it is unit-tested.
  WakeListener reads 80 ms chunks from the microphone, asks openWakeWord for a score, and calls
               WakeTrigger. It imports sounddevice only when it starts, so the tests never need audio.
"""

import logging
import time
import wave
from pathlib import Path

from app.config import BACKEND_DIR, get_settings

log = logging.getLogger("safetyai.voice")

SAMPLE_RATE = 16000  # openWakeWord wants 16 kHz, mono, 16-bit
CHUNK_SAMPLES = 1280  # 80 ms at 16 kHz


class WakeTrigger:
    """Turns a stream of scores into single trigger events.

    A score at or above the threshold triggers once. Everything is ignored for the cooldown afterwards,
    so one spoken word does not trigger several times.
    """

    def __init__(self, threshold: float, cooldown_s: float) -> None:
        self.threshold = threshold
        self.cooldown_s = cooldown_s
        self._quiet_until = float("-inf")

    def feed(self, score: float, now: float) -> bool:
        if now < self._quiet_until:
            return False
        if score >= self.threshold:
            self._quiet_until = now + self.cooldown_s
            return True
        return False


def device_choice(value: str | int | None) -> int | str | None:
    """A number means a device index ("1" -> 1). Anything else is a name, and an empty value means the default."""
    if value is None or value == "":
        return None
    if isinstance(value, int):
        return value
    text = value.strip()
    return int(text) if text.isdigit() else text


def best_score(scores: dict[str, float]) -> float:
    """openWakeWord gives one score per wake-word model; the highest one counts."""
    return max(scores.values(), default=0.0)


def read_wav_samples(path: Path) -> "list[int]":
    """Read a 16 kHz mono 16-bit WAV (the ack sound) as a list of samples. Standard library only."""
    with wave.open(str(path), "rb") as wav:
        if wav.getframerate() != SAMPLE_RATE or wav.getnchannels() != 1 or wav.getsampwidth() != 2:
            raise ValueError(f"{path} must be 16 kHz, mono, 16-bit (run the ffmpeg conversion)")
        import array
        return array.array("h", wav.readframes(wav.getnframes())).tolist()


def ensure_model(model_name: str) -> None:
    """Download the pretrained model once if it is not on this machine yet (needs internet the first time)."""
    import openwakeword
    from openwakeword import utils

    folder = Path(openwakeword.__file__).parent / "resources" / "models"
    if not (folder / f"{model_name}.onnx").exists():
        log.info("downloading wake-word model %s (first run only)", model_name)
        utils.download_models(model_names=[model_name])


def resolve_model(model: str) -> str:
    """WAKE_MODEL is either a ready-made openWakeWord name (hey_jarvis_v0.1: downloaded once) or the path of a
    model you trained (models/hey_friday.onnx, relative to backend/). Returns what openWakeWord should load."""
    if not model.endswith((".onnx", ".tflite")):
        ensure_model(model)
        return model
    path = Path(model)
    if not path.is_absolute():
        path = BACKEND_DIR / path
    if not path.exists():
        raise FileNotFoundError(f"wake-word model not found: {path}. Put the hey_friday.onnx from Colab there, "
                                "or set WAKE_MODEL=hey_jarvis_v0.1 until it is trained")
    return str(path)


class WakeListener:
    def __init__(self, *, threshold: float | None = None, device: int | str | None = None,
                 ack_sound: Path | None = None) -> None:
        s = get_settings()
        self.model_name = s.wake_model
        self.trigger = WakeTrigger(threshold if threshold is not None else s.wake_threshold, s.wake_cooldown_s)
        self.device = device_choice(device if device is not None else s.wake_device)
        self.ack_path = ack_sound or (BACKEND_DIR / s.wake_ack_sound)
        self.ack_samples = read_wav_samples(self.ack_path) if self.ack_path and self.ack_path.exists() else None
        if self.ack_samples is None:
            log.warning("ack sound not found at %s: the listener will trigger without playing it", self.ack_path)

    def run(self, on_trigger=None) -> None:
        """Listen until Ctrl+C. Each time the wake word is heard: play "Yes, boss", then call
        on_trigger(next_chunks), where next_chunks() yields the microphone audio that follows (the reply is
        recorded from the same microphone). When on_trigger returns, listening for the wake word resumes."""
        import queue

        import numpy as np
        import sounddevice as sd
        from openwakeword.model import Model

        model = Model(wakeword_models=[resolve_model(self.model_name)], inference_framework="onnx")
        chunks: "queue.Queue[np.ndarray]" = queue.Queue()

        def microphone(indata, frames, time_info, status) -> None:  # called by the audio driver, ~every 80 ms
            if status:
                log.warning("microphone status: %s", status)
            chunks.put(indata[:, 0].copy())

        log.info("listening for the wake word (model %s, threshold %.2f); Ctrl+C to stop",
                 self.model_name, self.trigger.threshold)
        with sd.InputStream(samplerate=SAMPLE_RATE, channels=1, dtype="int16", blocksize=CHUNK_SAMPLES,
                            device=self.device, callback=microphone):
            try:
                while True:
                    chunk = chunks.get()
                    t0 = time.perf_counter()
                    score = best_score(model.predict(chunk))
                    if self.trigger.feed(score, time.monotonic()):
                        ms = (time.perf_counter() - t0) * 1000
                        log.info("wake word heard (score %.2f, %.1f ms)", score, ms)
                        self.acknowledge()
                        if on_trigger:
                            ack_chunks = int(len(self.ack_samples or []) / CHUNK_SAMPLES)

                            def next_chunks():
                                for _ in range(ack_chunks):  # skip our own "Yes, boss"
                                    chunks.get()
                                while True:
                                    yield chunks.get()

                            on_trigger(next_chunks)
                            while not chunks.empty():  # drop what was heard while Friday was talking
                                chunks.get_nowait()
                            model.reset()
            except KeyboardInterrupt:
                log.info("stopped")

    def acknowledge(self) -> None:
        """Play the 'Yes, boss' sound. Non-blocking, so listening goes on at once."""
        if self.ack_samples is None:
            return
        import numpy as np
        import sounddevice as sd

        sd.play(np.array(self.ack_samples, dtype=np.int16), SAMPLE_RATE, blocking=False)


def list_devices() -> str:
    import sounddevice as sd

    lines = []
    for index, dev in enumerate(sd.query_devices()):
        if dev["max_input_channels"] > 0:
            lines.append(f"{index:>3}  {dev['name']}  (inputs: {dev['max_input_channels']})")
    return "\n".join(lines)
