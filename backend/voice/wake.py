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
    """Read a 16-bit WAV (the ack sound) as 16 kHz mono samples. Other rates (Rumik sends 24 kHz) and stereo
    are converted, so a file saved by tools.check_rumik --save works as is."""
    import numpy as np

    with wave.open(str(path), "rb") as wav:
        if wav.getsampwidth() != 2:
            raise ValueError(f"{path} must be 16-bit PCM")
        rate, channels = wav.getframerate(), wav.getnchannels()
        samples = np.frombuffer(wav.readframes(wav.getnframes()), dtype=np.int16).astype(np.float64)
    if channels > 1:
        samples = samples.reshape(-1, channels).mean(axis=1)
    if rate != SAMPLE_RATE and len(samples):
        n = int(round(len(samples) * SAMPLE_RATE / rate))
        samples = np.interp(np.linspace(0, len(samples) - 1, n), np.arange(len(samples)), samples)
    return np.clip(np.round(samples), -32768, 32767).astype(np.int16).tolist()


TRAINING_GUIDE = "tools/wake_training/README.md"


def ensure_feature_models() -> None:
    """openWakeWord turns sound into features with two small shared models (melspectrogram, embedding) before our
    "Hey Friday" model scores them. Download those two once (needs internet the first time); no wake words."""
    import openwakeword
    from openwakeword import utils

    folder = Path(openwakeword.__file__).parent / "resources" / "models"
    for feature in openwakeword.FEATURE_MODELS.values():
        url = feature["download_url"].replace(".tflite", ".onnx")
        if not (folder / url.split("/")[-1]).exists():
            log.info("downloading openWakeWord feature model %s (first run only)", url.split("/")[-1])
            folder.mkdir(parents=True, exist_ok=True)
            utils.download_file(url, str(folder))


def resolve_model(model: str) -> str:
    """WAKE_MODEL is the "Hey Friday" model file trained on Colab (models/hey_friday.onnx, relative to backend/).
    Returns the full path openWakeWord should load."""
    if not model.endswith((".onnx", ".tflite")):
        raise ValueError(f"WAKE_MODEL must be the trained model file (models/hey_friday.onnx), not {model!r}. "
                         f"How to train it: {TRAINING_GUIDE}")
    path = Path(model)
    if not path.is_absolute():
        path = BACKEND_DIR / path
    if not path.exists():
        raise FileNotFoundError(f"wake-word model not found: {path}. Train it on Colab ({TRAINING_GUIDE}) and "
                                "put hey_friday.onnx there. Until then, hold Space on Friday's page to talk.")
    ensure_feature_models()
    return str(path)


def score_samples(model, samples) -> tuple[float, float]:
    """Run a wake-word model over 16 kHz samples, 80 ms at a time. Returns (best score, second it happened)."""
    import numpy as np

    data = np.asarray(samples, dtype=np.int16)
    best, at = 0.0, 0.0
    for i in range(0, len(data) - CHUNK_SAMPLES + 1, CHUNK_SAMPLES):
        score = best_score(model.predict(data[i:i + CHUNK_SAMPLES]))
        if score > best:
            best, at = score, i / SAMPLE_RATE
    return best, at


def audio_report(samples) -> str:
    """Loudness, peak and clipping of a recording, in words."""
    import numpy as np

    data = np.asarray(samples, dtype=np.float64)
    if not len(data):
        return "empty recording"
    rms, peak = float(np.sqrt(np.mean(data ** 2))), float(np.max(np.abs(data)))
    clipped = float(np.mean(np.abs(data) >= 32000) * 100)
    verdict = ("too quiet" if peak < 2000 else "CLIPPED (distorted): lower the microphone level" if clipped > 0.1
               else "good")
    return f"loudness {rms:.0f}, peak {peak:.0f} of 32767, clipped {clipped:.2f}% -> {verdict}"


WAKE_WORDS = ("hey", "friday")
VOSK_GRAMMAR = ["hey friday", "hey", "friday", "[unk]"]


def heard_wake(text: str) -> bool:
    """True when the recognised words contain "hey friday" in that order (anything around it is ignored)."""
    words = text.lower().split()
    return any(words[i:i + 2] == list(WAKE_WORDS) for i in range(len(words) - 1))


class VoskWake:
    """"Hey Friday" without a trained model: Vosk, an offline speech recogniser, listens with a tiny grammar
    (only "hey", "friday" and "unknown"), so it cannot drift into other words. Same interface as openWakeWord's
    Model (predict(chunk) -> {name: score}, reset()), so the listener, --debug and --mic-test work unchanged.
    The score is 1.0 when "hey friday" was just heard, else 0.0. The speech model (about 40 MB) downloads once."""

    def __init__(self, model_name: str, recognizer_factory=None) -> None:
        import json

        self._json = json
        if recognizer_factory is None:
            from vosk import KaldiRecognizer, Model, SetLogLevel

            SetLogLevel(-1)
            log.info("loading offline speech model %s (downloads once, about 40 MB)", model_name)
            model = Model(model_name=model_name)
            grammar = json.dumps(VOSK_GRAMMAR)
            recognizer_factory = lambda: KaldiRecognizer(model, SAMPLE_RATE, grammar)  # noqa: E731
        self._new = recognizer_factory
        self.reset()

    def reset(self) -> None:
        self._rec = self._new()

    def predict(self, chunk) -> dict[str, float]:
        import numpy as np

        if self._rec.AcceptWaveform(np.asarray(chunk, dtype=np.int16).tobytes()):
            text = self._json.loads(self._rec.Result()).get("text", "")
        else:
            text = self._json.loads(self._rec.PartialResult()).get("partial", "")
        if heard_wake(text):
            self.reset()  # start fresh, so the same words never trigger twice
            return {"hey_friday": 1.0}
        return {"hey_friday": 0.0}


def engine_for(model_name: str, engine: str | None = None) -> str:
    """auto: our trained "Hey Friday" model when its file exists, otherwise Vosk."""
    engine = (engine or get_settings().wake_engine).lower()
    if engine != "auto":
        return engine
    path = Path(model_name)
    return "onnx" if (path if path.is_absolute() else BACKEND_DIR / path).exists() else "vosk"


def load_model(model_name: str, engine: str | None = None):
    if engine_for(model_name, engine) == "vosk":
        return VoskWake(get_settings().wake_vosk_model)
    from openwakeword.model import Model

    return Model(wakeword_models=[resolve_model(model_name)], inference_framework="onnx")


def write_wav(path: Path, samples) -> None:
    import numpy as np

    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SAMPLE_RATE)
        w.writeframes(np.asarray(samples, dtype=np.int16).tobytes())


class DebugMeter:
    """For --debug: one line per second with the microphone level and the best wake-word score."""

    def __init__(self, every: int = 12) -> None:  # 12 chunks of 80 ms = about 1 s
        self.every, self.n, self.loudest, self.best, self.clipped = every, 0, 0.0, 0.0, 0

    def feed(self, chunk, score: float, threshold: float) -> str | None:
        import numpy as np

        self.n += 1
        samples = np.asarray(chunk, dtype=np.float64)
        self.loudest = max(self.loudest, float(np.sqrt(np.mean(samples ** 2))))
        self.clipped += int(np.sum(np.abs(samples) >= 32000))
        self.best = max(self.best, score)
        if self.n < self.every:
            return None
        level = min(30, int(self.loudest / 300))
        hint = ("  <- too quiet: wrong microphone?" if self.loudest < 150
                else "  <- CLIPPING: too loud, lower the microphone level" if self.clipped > 20 else "")
        line = (f"mic {'#' * level:<30} {self.loudest:6.0f}   wake score {self.best:.2f} "
                f"(needs {threshold:.2f}){hint}")
        self.n, self.loudest, self.best, self.clipped = 0, 0.0, 0.0, 0
        return line


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

    def run(self, on_trigger=None, debug: bool = False) -> None:
        """Listen until Ctrl+C. Each time the wake word is heard: play "Yes, boss", then call
        on_trigger(next_chunks), where next_chunks() yields the microphone audio that follows (the reply is
        recorded from the same microphone). When on_trigger returns, listening for the wake word resumes.
        debug=True prints, once a second, the microphone loudness and the best wake-word score heard."""
        import queue

        import numpy as np
        import sounddevice as sd
        model = load_model(self.model_name)
        chunks: "queue.Queue[np.ndarray]" = queue.Queue()

        def microphone(indata, frames, time_info, status) -> None:  # called by the audio driver, ~every 80 ms
            if status:
                log.warning("microphone status: %s", status)
            chunks.put(indata[:, 0].copy())

        engine = engine_for(self.model_name)
        log.info("listening for \"Hey Friday\" (%s, threshold %.2f); Ctrl+C to stop",
                 self.model_name if engine == "onnx" else f"offline speech model {get_settings().wake_vosk_model}",
                 self.trigger.threshold)
        with sd.InputStream(samplerate=SAMPLE_RATE, channels=1, dtype="int16", blocksize=CHUNK_SAMPLES,
                            device=self.device, callback=microphone):
            try:
                meter = DebugMeter() if debug else None
                while True:
                    chunk = chunks.get()
                    t0 = time.perf_counter()
                    score = best_score(model.predict(chunk))
                    if meter:
                        line = meter.feed(chunk, score, self.trigger.threshold)
                        if line:
                            print(line, flush=True)
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


    def record(self, seconds: float):
        """Record the microphone exactly as the listener hears it (16 kHz mono)."""
        import sounddevice as sd

        audio = sd.rec(int(seconds * SAMPLE_RATE), samplerate=SAMPLE_RATE, channels=1, dtype="int16",
                       device=self.device)
        sd.wait()
        return audio[:, 0]


def list_devices() -> str:
    import sounddevice as sd

    lines = []
    for index, dev in enumerate(sd.query_devices()):
        if dev["max_input_channels"] > 0:
            lines.append(f"{index:>3}  {dev['name']}  (inputs: {dev['max_input_channels']})")
    return "\n".join(lines)
