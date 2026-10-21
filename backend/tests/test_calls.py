"""The call queue in live mode, with a fake Twilio (nobody is dialled)."""

import time

import pytest

from app.config import get_settings
from app.models import Call
from app.services.calls import CallService, TwilioRejected


class FakeTwilio:
    def __init__(self, reject=()):
        self.dialled, self.reject = [], set(reject)

    def configured(self):
        return True

    def create_call(self, to, call_id):
        if to in self.reject:
            raise TwilioRejected("Twilio 400: number not verified")
        self.dialled.append((to, time.monotonic()))
        return f"CA{call_id}"

    def hangup(self, call_sid):
        pass


@pytest.fixture
def live(monkeypatch, roster):
    monkeypatch.setattr(get_settings(), "calls_mode", "live")
    monkeypatch.setattr(get_settings(), "twilio_cps", 20.0)  # 1 call per 50 ms, to keep the test quick
    fake = FakeTwilio(reject={"+919800000099"})
    service = CallService(get_settings(), client=fake)
    yield service, fake
    service.stop()


def wait_until(predicate, timeout=5.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if predicate():
            return True
        time.sleep(0.02)
    return False


def test_queue_dials_everyone_at_the_rate_limit(live, roster):
    service, fake = live
    ids = [service.place(kind="roll_call", to_name=f"G{i}", to_phone=f"+91980000000{i}") for i in range(4)]
    assert wait_until(lambda: len(fake.dialled) == 4)
    gaps = [b[1] - a[1] for a, b in zip(fake.dialled, fake.dialled[1:])]
    assert all(gap >= 0.045 for gap in gaps)  # never faster than twilio_cps
    roster.expire_all()
    assert {roster.get(Call, i).call_sid for i in ids} == {f"CA{i}" for i in ids}


def test_rejected_number_is_marked_failed_not_retried(live, roster):
    service, fake = live
    call_id = service.place(kind="alert", to_name="Unknown", to_phone="+919800000099")
    assert wait_until(lambda: roster.get(Call, call_id) is not None and
                      (roster.expire_all() or roster.get(Call, call_id).status == "failed"))
    assert fake.dialled == []


def test_cancelled_calls_are_never_dialled(live, roster, monkeypatch):
    service, fake = live
    monkeypatch.setattr(service, "start", lambda: None)  # hold the queue so we can cancel first
    call_id = service.place(kind="alert", to_name="Ravi", to_phone="+919800000003", incident_id=None)
    assert service.cancel_pending() == 1
    service._thread = None
    CallService.start(service)
    service._queue.put(call_id)
    time.sleep(0.3)
    assert fake.dialled == []


def test_off_mode_places_nothing(roster, monkeypatch):
    monkeypatch.setattr(get_settings(), "calls_mode", "off")
    assert CallService(get_settings()).place(kind="alert", to_name="x", to_phone="+919800000003") is None
    assert roster.query(Call).count() == 0
