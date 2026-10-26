"""Wake-word rules (no microphone needed) and the acknowledgement sound file."""

import wave
from pathlib import Path

import numpy as np
import pytest

from voice.wake import CHUNK_SAMPLES, SAMPLE_RATE, WakeListener, WakeTrigger, best_score, device_choice, read_wav_samples


def test_triggers_once_when_the_score_passes_the_threshold():
    trigger = WakeTrigger(threshold=0.5, cooldown_s=2.0)
    assert trigger.feed(0.1, now=0.0) is False
    assert trigger.feed(0.9, now=0.1) is True


def test_cooldown_ignores_the_rest_of_the_word():
    trigger = WakeTrigger(threshold=0.5, cooldown_s=2.0)
    fired = [trigger.feed(score, now=t) for t, score in [(0.0, 0.6), (0.08, 0.9), (0.16, 0.8), (1.0, 0.7)]]
    assert fired == [True, False, False, False]  # 1.0 s is still inside the 2 s cooldown


def test_triggers_again_after_the_cooldown():
    trigger = WakeTrigger(threshold=0.5, cooldown_s=2.0)
    assert trigger.feed(0.6, now=0.0) is True
    assert trigger.feed(0.6, now=2.5) is True


def test_scores_below_threshold_never_trigger():
    trigger = WakeTrigger(threshold=0.5, cooldown_s=2.0)
    assert not any(trigger.feed(0.49, now=t / 10) for t in range(100))


def test_best_score_takes_the_highest_model_score():
    assert best_score({"hey_friday": 0.2, "other": 0.7}) == 0.7
    assert best_score({}) == 0.0


def _write_wav(path: Path, rate: int, channels: int, width: int, samples: int = 1600) -> Path:
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(channels)
        wav.setsampwidth(width)
        wav.setframerate(rate)
        wav.writeframes(b"\x00\x00" * samples * channels)
    return path


def test_ack_sound_is_converted_to_16k_mono(tmp_path):
    good = _write_wav(tmp_path / "good.wav", SAMPLE_RATE, 1, 2)
    assert len(read_wav_samples(good)) == 1600
    # Rumik sends 24 kHz: same 0.1 s, resampled to 16 kHz
    assert len(read_wav_samples(_write_wav(tmp_path / "rate.wav", 24000, 1, 2, samples=2400))) == 1600
    assert len(read_wav_samples(_write_wav(tmp_path / "stereo.wav", SAMPLE_RATE, 2, 2))) == 1600
    with pytest.raises(ValueError):
        read_wav_samples(_write_wav(tmp_path / "8bit.wav", SAMPLE_RATE, 1, 1))


def test_missing_ack_sound_still_builds_the_listener(tmp_path):
    listener = WakeListener(ack_sound=tmp_path / "nope.wav")
    assert listener.ack_samples is None  # it warns and triggers without a sound


def test_the_trained_model_stays_quiet_on_silence():
    from voice.wake import BACKEND_DIR, load_model

    if not (BACKEND_DIR / "models" / "hey_friday.onnx").exists():
        pytest.skip("models/hey_friday.onnx not trained yet (tools/wake_training/README.md)")
    model = load_model("models/hey_friday.onnx")
    silence = np.zeros(CHUNK_SAMPLES * 10, dtype=np.int16)
    scores = [best_score(model.predict(silence[i:i + CHUNK_SAMPLES])) for i in range(0, len(silence), CHUNK_SAMPLES)]
    assert max(scores) < 0.05


def test_a_number_means_a_device_index_not_a_name():
    assert device_choice("1") == 1
    assert device_choice(" 26 ") == 26
    assert device_choice("Microphone Array") == "Microphone Array"
    assert device_choice("") is None
    assert device_choice(None) is None


def test_a_trained_model_file_is_used_as_is_and_never_downloaded(tmp_path, monkeypatch):
    from voice import wake

    model = tmp_path / "hey_friday.onnx"
    model.write_bytes(b"onnx")
    monkeypatch.setattr(wake, "ensure_feature_models", lambda: None)
    assert wake.resolve_model(str(model)) == str(model)


def test_a_ready_made_model_name_is_refused():
    from voice import wake

    with pytest.raises(ValueError, match="hey_friday.onnx"):
        wake.resolve_model("hey_jarvis_v0.1")


def test_a_missing_trained_model_says_where_it_looked(tmp_path):
    from voice import wake

    with pytest.raises(FileNotFoundError, match="hey_friday.onnx"):
        wake.resolve_model("models/hey_friday.onnx")


def test_a_relative_model_path_is_inside_backend(monkeypatch, tmp_path):
    from voice import wake

    (tmp_path / "models").mkdir()
    (tmp_path / "models" / "hey_friday.onnx").write_bytes(b"onnx")
    monkeypatch.setattr(wake, "BACKEND_DIR", tmp_path)
    monkeypatch.setattr(wake, "ensure_feature_models", lambda: None)
    assert wake.resolve_model("models/hey_friday.onnx") == str(tmp_path / "models" / "hey_friday.onnx")


def test_debug_meter_prints_once_a_second_and_flags_a_silent_microphone():
    from voice.wake import DebugMeter

    meter = DebugMeter()
    quiet = np.zeros(CHUNK_SAMPLES, dtype=np.int16)
    lines = [meter.feed(quiet, 0.05, 0.5) for _ in range(12)]
    assert lines[:11] == [None] * 11 and "too quiet" in lines[11] and "wake score 0.05" in lines[11]
    loud = (np.ones(CHUNK_SAMPLES) * 3000).astype(np.int16)
    line = [meter.feed(loud, 0.7, 0.5) for _ in range(12)][-1]
    assert "too quiet" not in line and "wake score 0.70" in line


def test_score_samples_reports_the_best_score_and_when():
    from voice.wake import score_samples

    class Fake:
        def __init__(self):
            self.n = 0

        def predict(self, chunk):
            assert len(chunk) == CHUNK_SAMPLES
            self.n += 1
            return {"hey": 0.8 if self.n == 26 else 0.1}

    best, at = score_samples(Fake(), np.zeros(CHUNK_SAMPLES * 40, dtype=np.int16))
    assert best == 0.8 and at == 2.0  # chunk 26 starts at 25 x 80 ms


def test_audio_report_spots_quiet_and_clipped_recordings():
    from voice.wake import audio_report

    assert "too quiet" in audio_report(np.full(16000, 100, dtype=np.int16))
    assert "CLIPPED" in audio_report(np.full(16000, 32767, dtype=np.int16))
    wave_ = (np.sin(np.linspace(0, 400, 16000)) * 9000).astype(np.int16)
    assert audio_report(wave_).endswith("good")


def test_heard_wake_needs_hey_then_friday():
    from voice.wake import heard_wake

    assert heard_wake("hey friday") and heard_wake("[unk] hey friday [unk]") and heard_wake("HEY FRIDAY")
    assert not heard_wake("friday") and not heard_wake("friday hey") and not heard_wake("hey [unk] friday")
    # people talking nearby: the words turn up mid-sentence, after other speech -> never a wake
    assert not heard_wake("[unk] [unk] [unk] hey friday")


class FakeRecognizer:
    """Plays back a script of (final?, text) results, like Vosk's KaldiRecognizer."""

    def __init__(self, script):
        self.script = script

    def AcceptWaveform(self, data):
        assert len(data) == CHUNK_SAMPLES * 2
        self.final, self.text = self.script.pop(0) if self.script else (False, "")
        return self.final

    def Result(self):
        return f'{{"text": "{self.text}"}}'

    def PartialResult(self):
        return f'{{"partial": "{self.text}"}}'


def test_vosk_wake_scores_one_on_hey_friday_and_starts_fresh():
    from voice.wake import VoskWake

    script = [(False, "hey"), (False, "hey friday"), (True, "hey friday")]
    made = []

    def factory():
        made.append(FakeRecognizer(script))
        return made[-1]

    wake = VoskWake("any", recognizer_factory=factory)
    chunk = np.zeros(CHUNK_SAMPLES, dtype=np.int16)
    scores = [wake.predict(chunk)["hey_friday"] for _ in range(4)]
    assert scores[:2] == [0.0, 1.0]
    assert len(made) == 3  # a fresh recognizer after each trigger


def test_auto_engine_uses_the_trained_file_when_it_exists(tmp_path):
    from voice.wake import engine_for

    model = tmp_path / "hey_friday.onnx"
    assert engine_for(str(model), "auto") == "vosk"
    model.write_bytes(b"onnx")
    assert engine_for(str(model), "auto") == "onnx"
    assert engine_for(str(model), "vosk") == "vosk"


def test_yes_boss_is_made_once_in_the_rumik_voice(tmp_path):
    from voice.wake import OLD_ACK, ensure_rumik_ack

    target = tmp_path / "ack" / "yes_boss_rumik.wav"
    asked = []

    def speak(text):
        asked.append(text)
        return b"RIFF-rumik", 900

    assert ensure_rumik_ack(target, speak) == target and target.read_bytes() == b"RIFF-rumik"
    assert ensure_rumik_ack(target, speak) == target and asked == ["Yes, boss."]  # not asked again

    def broken(text):
        raise RuntimeError("no key")

    assert ensure_rumik_ack(tmp_path / "other.wav", broken) == OLD_ACK


def test_empty_dashboard_url_keeps_the_default():
    from app.config import Settings

    assert Settings(dashboard_url="").dashboard_url == "http://localhost:5173"
    assert Settings(dashboard_url="http://10.0.0.5:5173").dashboard_url == "http://10.0.0.5:5173"
