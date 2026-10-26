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
    assert best_score({"hey_jarvis": 0.2, "alexa": 0.7}) == 0.7
    assert best_score({}) == 0.0


def _write_wav(path: Path, rate: int, channels: int, width: int, samples: int = 1600) -> Path:
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(channels)
        wav.setsampwidth(width)
        wav.setframerate(rate)
        wav.writeframes(b"\x00\x00" * samples * channels)
    return path


def test_ack_sound_must_be_16k_mono_16bit(tmp_path):
    good = _write_wav(tmp_path / "good.wav", SAMPLE_RATE, 1, 2)
    assert len(read_wav_samples(good)) == 1600
    with pytest.raises(ValueError):
        read_wav_samples(_write_wav(tmp_path / "rate.wav", 24000, 1, 2))
    with pytest.raises(ValueError):
        read_wav_samples(_write_wav(tmp_path / "stereo.wav", SAMPLE_RATE, 2, 2))


def test_missing_ack_sound_still_builds_the_listener(tmp_path):
    listener = WakeListener(ack_sound=tmp_path / "nope.wav")
    assert listener.ack_samples is None  # it warns and triggers without a sound


def test_the_real_model_stays_quiet_on_silence():
    from openwakeword.model import Model

    from voice.wake import ensure_model

    model_name = "hey_jarvis_v0.1"
    import openwakeword
    folder = Path(openwakeword.__file__).parent / "resources" / "models"
    if not (folder / f"{model_name}.onnx").exists():
        pytest.skip("pretrained model not downloaded yet (run once with internet)")
    ensure_model(model_name)
    model = Model(wakeword_models=[model_name], inference_framework="onnx")
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
    monkeypatch.setattr(wake, "ensure_model", lambda name: pytest.fail("must not download"))
    assert wake.resolve_model(str(model)) == str(model)


def test_a_missing_trained_model_says_where_it_looked(tmp_path):
    from voice import wake

    with pytest.raises(FileNotFoundError, match="hey_friday.onnx"):
        wake.resolve_model("models/hey_friday.onnx")


def test_a_relative_model_path_is_inside_backend(monkeypatch, tmp_path):
    from voice import wake

    (tmp_path / "models").mkdir()
    (tmp_path / "models" / "hey_friday.onnx").write_bytes(b"onnx")
    monkeypatch.setattr(wake, "BACKEND_DIR", tmp_path)
    assert wake.resolve_model("models/hey_friday.onnx") == str(tmp_path / "models" / "hey_friday.onnx")
