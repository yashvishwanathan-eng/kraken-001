"""P22 to P24: adding cameras, roles, duty check-in (and the call fallback), activity log, overview, usage,
summary by voice, and Rumik text to speech."""

from datetime import datetime, timedelta, timezone

import cv2
import numpy as np
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.config import get_settings
from app.main import app
from app.models import AuditLog, Camera, Incident, User
from app.services import camera_registry, commands, tts
from app.services.directory import contacts_on_duty, on_shift_contacts_with_fallback
from app.services.escalation import Escalation
from app.services.permissions import sign_stream_token, sign_value
from app.services.workers import workers
from tests.conftest import check_in, login, make_incident
from tests.test_escalation import FakeScheduler, timeline

client = TestClient(app)

ADMIN, MANAGER, HEAD_GUARD, GUARD = "1111", "2222", "3333", "4444"


def user(db, name: str) -> User:
    db.expire_all()
    return db.scalar(select(User).where(User.name == name))


def grant(db, name: str, *actions: str) -> None:
    row = user(db, name)
    row.actions = sorted({*row.actions, *actions})
    db.commit()


@pytest.fixture
def video(tmp_path):
    path = tmp_path / "clip.avi"
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"MJPG"), 10, (160, 120))
    for _ in range(5):
        writer.write(np.zeros((120, 160, 3), dtype=np.uint8))
    writer.release()
    return str(path)


class FakeProcess:
    pid = 4242

    def __init__(self):
        self.returncode = None

    def poll(self):
        return self.returncode

    def terminate(self):
        self.returncode = -15

    def wait(self, timeout=None):
        return self.returncode

    def kill(self):
        self.returncode = -9


@pytest.fixture
def fake_workers(monkeypatch):
    monkeypatch.setattr(workers, "_launch", lambda camera_id: FakeProcess())
    workers._workers.clear()
    yield workers
    workers._workers.clear()


# ---------- P22: cameras ----------

def test_camera_probe_reads_a_frame_or_explains(video):
    ok = camera_registry.probe(video, timeout_s=5)
    assert ok.ok and (ok.width, ok.height) == (160, 120)
    bad = camera_registry.probe("/no/such/file.mp4", timeout_s=5)
    assert not bad.ok and bad.error


def test_seed_numbers_cameras_in_id_order(roster):
    assert {c.id: c.number for c in roster.scalars(select(Camera))} == {"cam-phone": 1, "cam-webcam": 2}


def test_admin_adds_tests_and_starts_a_camera(roster, video, fake_workers):
    grant(roster, "Anita Rao", "manage_cameras")
    headers = login(client, ADMIN)
    assert client.post("/cameras/test", json={"source": video}, headers=headers).json()["ok"] is True

    failed = client.post("/cameras", json={"name": "Gate", "source": "/no/such.mp4"}, headers=headers)
    assert failed.status_code == 422 and "camera test failed" in failed.json()["detail"]

    added = client.post("/cameras", json={"name": "Loading Bay 2", "source": video, "role_names": ["Guard"]},
                        headers=headers)
    assert added.status_code == 201, added.text
    body = added.json()
    assert (body["id"], body["number"], body["port"], body["added_by"]) == ("cam-loading-bay-2", 3, 8103, "Anita Rao")
    assert body["worker"]["running"] is True and body["worker"]["managed"] is True
    assert "cam-loading-bay-2" in user(roster, "Suresh Babu").camera_ids  # given to every Guard
    assert "cam-loading-bay-2" not in user(roster, "Ravi Kumar").camera_ids

    again = client.post("/cameras", json={"name": "Loading Bay 2", "source": video, "test_first": False},
                        headers=headers)
    assert again.status_code == 409
    taken = client.post("/cameras", json={"name": "Dock", "source": video, "number": 1, "test_first": False},
                        headers=headers)
    assert taken.status_code == 409 and "Phone Camera" in taken.json()["detail"]

    listed = client.get("/cameras", headers=login(client, GUARD)).json()
    assert [(c["id"], c["number"]) for c in listed] == [("cam-webcam", 2), ("cam-loading-bay-2", 3)]
    assert camera_registry.get_camera("cam-loading-bay-2").port == 8103  # workers read the database

    off = client.put("/cameras/cam-loading-bay-2", json={"enabled": False}, headers=headers).json()
    assert off["enabled"] is False and off["worker"]["running"] is False
    assert client.post("/cameras/cam-loading-bay-2/start", headers=headers).status_code == 409
    assert roster.scalar(select(AuditLog.id).where(AuditLog.action == "camera_add")) is not None


def test_only_manage_cameras_may_add(roster):
    assert client.post("/cameras", json={"name": "X", "source": "0"}, headers=login(client, MANAGER)).status_code == 403
    assert client.get("/cameras/admin", headers=login(client, GUARD)).status_code == 403


def test_crashed_worker_restarts_with_autostart(roster, fake_workers, monkeypatch):
    monkeypatch.setattr(get_settings(), "workers_autostart", True)
    monkeypatch.setattr(get_settings(), "worker_restart_s", 0)
    workers.start("cam-webcam")
    first = workers._workers["cam-webcam"].process
    first.returncode = 1  # it crashed
    workers.check()  # notices
    workers.check()  # restarts
    assert workers._workers["cam-webcam"].process is not first
    workers.stop("cam-webcam")
    workers.check()
    assert workers.status("cam-webcam")["running"] is False  # stopped on purpose: not restarted


# ---------- P22: roles and staff IDs ----------

def test_roles_fill_new_users_and_push_changes(roster):
    headers = login(client, ADMIN)
    role = client.post("/roles", json={"name": "Patrol", "camera_ids": ["cam-phone"], "actions": ["ack"]},
                       headers=headers)
    assert role.status_code == 201, role.text
    new = client.post("/users", json={"name": "Meena", "role": "Patrol", "pin": "5555",
                                             "phone": "+919800000055"}, headers=headers).json()
    assert (new["camera_ids"], new["actions"], new["staff_id"]) == (["cam-phone"], ["ack"], f"RG-{new['id']:04d}")

    extra = user(roster, "Meena")
    extra.camera_ids = ["cam-phone", "cam-webcam"]  # her own extra camera
    roster.commit()
    changed = client.put(f"/roles/{role.json()['id']}", json={"camera_ids": [], "actions": ["ack", "resolve"]},
                         headers=headers).json()
    assert changed["users"] == 1
    meena = user(roster, "Meena")
    assert meena.camera_ids == ["cam-webcam"] and meena.actions == ["ack", "resolve"]  # extra kept

    client.put(f"/roles/{role.json()['id']}", json={"name": "Night Patrol"}, headers=headers)
    assert user(roster, "Meena").role == "Night Patrol"
    assert client.post("/roles", json={"name": "Night Patrol"}, headers=headers).status_code == 409
    assert client.get("/roles", headers=login(client, GUARD)).status_code == 403


def test_invite_carries_the_staff_id(roster, monkeypatch):
    sent = []
    from app.services.email_sender import emailer
    monkeypatch.setattr(emailer, "send", lambda to, subject, body: sent.append(body) or "simulated")
    out = client.post("/invites", json={"name": "Kiran", "email": "kiran@example.com", "role": "Guard",
                                             "phone": "+919800000099"},
                      headers=login(client, ADMIN)).json()
    assert out["staff_id"] == f"RG-{out['user_id']:04d}"
    assert out["staff_id"] in sent[0]


# ---------- P23: duty ----------

def test_pass_scan_toggles_duty_and_the_board_shows_it(roster):
    suresh = user(roster, "Suresh Babu")
    pass_out = client.get(f"/duty/pass/{suresh.id}", headers=login(client, GUARD)).json()
    assert pass_out["qr_png"].startswith("data:image/png;base64,")
    assert client.get(f"/duty/pass/{user(roster, 'Ravi Kumar').id}", headers=login(client, GUARD)).status_code == 403

    scanner = login(client, HEAD_GUARD)  # has scan_badges
    first = client.post("/duty/scan", json={"pass_token": pass_out["pass_token"]}, headers=scanner).json()
    assert (first["action"], first["method"], first["by"]) == ("in", "pass_scan", "Ravi Kumar")
    board = client.get("/duty", headers=login(client, ADMIN)).json()
    assert [p["name"] for p in board["on_duty"]] == ["Suresh Babu"]
    assert [p["name"] for p in board["off_duty"]] == ["Ravi Kumar"]  # admins and managers do not check in

    second = client.post("/duty/scan", json={"pass_token": pass_out["pass_token"]}, headers=scanner).json()
    assert second["action"] == "out"
    assert client.post("/duty/scan", json={"pass_token": "1.forged"}, headers=scanner).status_code == 404


def test_guard_checks_in_by_scanning_the_post_qr(roster):
    from app.models import Area, Building, Site
    site = Site(name="Plant")
    roster.add(site)
    roster.flush()
    building = Building(site_id=site.id, name="A")
    roster.add(building)
    roster.flush()
    area = Area(building_id=building.id, name="Gate 1")
    roster.add(area)
    roster.commit()
    qr = client.get(f"/duty/post-qr?area_id={area.id}", headers=login(client, ADMIN)).json()
    assert "/check-in?post=" in qr["link"]
    done = client.post("/duty/check-in", json={"post_token": qr["post_token"], "action": "in"},
                       headers=login(client, GUARD)).json()
    assert (done["name"], done["action"], done["area_id"]) == ("Suresh Babu", "in", area.id)
    assert client.post("/duty/check-in", json={"post_token": sign_value("duty-pass", area.id)},
                       headers=login(client, GUARD)).status_code == 404  # a pass is not a post code


def test_off_duty_guards_are_not_called_but_fallback_is_never_silent(roster, simulate):
    contacts, fallback = contacts_on_duty(roster, "Head Guard", "cam-webcam")
    assert fallback is True and [c.name for c in contacts] == ["Ravi Kumar"]  # nobody on duty: shift roster

    ladder = Escalation(scheduler=FakeScheduler(), messenger=simulate, settings=get_settings())
    incident_id = make_incident(roster, type_="fire", severity="critical")
    ladder.start(incident_id)
    ladder.scheduler.run(f"ladder:{incident_id}:0")
    assert timeline(roster, incident_id)[1:] == [("duty_fallback", "Ravi Kumar"), ("whatsapp_sent", "Ravi Kumar"),
                                                 ("call_placed", "Ravi Kumar")]
    assert "nobody is checked in on duty" in simulate.sent[0][1]

    check_in(roster, "Suresh Babu")  # one guard on duty: off-duty guards drop out of alert-all
    names = [c.name for c in on_shift_contacts_with_fallback(roster)[0]]
    assert "Suresh Babu" in names and "Ravi Kumar" not in names and "Anita Rao" in names
    check_in(roster, "Ravi Kumar")
    assert contacts_on_duty(roster, "Head Guard", "cam-webcam")[1] is False


# ---------- P24: activity, overview, usage ----------

def test_activity_log_records_ai_and_camera_views(roster):
    admin = login(client, ADMIN)
    client.post("/assistant/command", json={"text": "status report"}, headers=admin)
    from app.routers.stream import _authorise, _last_view
    _last_view.clear()
    anita = user(roster, "Anita Rao")
    _authorise("cam-phone", sign_stream_token(anita.id, "cam-phone"))
    _authorise("cam-phone", sign_stream_token(anita.id, "cam-phone"))  # reconnect: not logged twice

    body = client.get("/activity", headers=login(client, MANAGER)).json()
    row = next(u for u in body["users"] if u["user"] == "Anita Rao")
    assert (row["ai_commands"], row["camera_views"], row["cameras_viewed"]) == (1, 1, ["cam-phone"])
    assert body["ai"][0]["text"] == "status report" and body["ai"][0]["intent"] == "status_report"
    assert body["ai"][0]["answer"].startswith("0 open incidents")
    assert client.get("/activity", headers=login(client, HEAD_GUARD)).status_code == 403
    audit = client.get("/audit-log?action=assistant_command", headers=admin).json()
    assert audit[0]["detail"]["text"] == "status report"


def test_overview_shows_cameras_problems_and_duty(roster):
    make_incident(roster, type_="fire", severity="critical")
    make_incident(roster, status="resolved", minutes_ago=60 * 30)
    check_in(roster, "Suresh Babu")
    body = client.get("/overview", headers=login(client, ADMIN)).json()
    assert (body["cameras_total"], body["cameras_online"], body["open_incidents"]) == (2, 0, 1)
    assert body["open_by_severity"] == {"critical": 1}
    kinds = [p["kind"] for p in body["problems_now"]]
    assert kinds[0] == "incident" and kinds.count("camera_offline") == 2
    assert body["past_total_7d"] == 2 and len(body["past_problems"]) == 7
    assert (body["guards_on_duty"], body["guards_off_duty"]) == (["Suresh Babu"], ["Ravi Kumar"])
    assert body["ok"] is False and body["text"].startswith("0 of 2 cameras online. 1 open incident.")
    guard = client.get("/overview", headers=login(client, GUARD)).json()
    assert [c["id"] for c in guard["cameras"]] == ["cam-webcam"]


def test_usage_reports_storage_and_rows(roster):
    body = client.get("/usage", headers=login(client, ADMIN)).json()
    assert body["rows"]["users"] == 4 and body["database_bytes"] > 0
    assert len(body["incidents_by_day"]) == 30 and body["disk_total_bytes"] > 0
    assert client.get("/usage", headers=login(client, HEAD_GUARD)).status_code == 403


# ---------- summary by voice ----------

def test_summary_window_phrases():
    now = datetime(2026, 10, 26, 6, 30, tzinfo=timezone.utc)  # 12:00 IST
    start, end, label = commands.summary_window("what's the summary for today", {}, now)
    assert (start, end, label) == (datetime(2026, 10, 25, 18, 30, tzinfo=timezone.utc), now, "today")
    start, _, label = commands.summary_window("what's the summary for the past hour", {}, now)
    assert (now - start, label) == (timedelta(hours=1), "the past hour")
    start, _, label = commands.summary_window("summary of the last 3 hours", {}, now)
    assert (now - start, label) == (timedelta(hours=3), "the past 3 hours")
    start, _, label = commands.summary_window("summary for the last 30 minutes", {}, now)
    assert (now - start, label) == (timedelta(minutes=30), "the past 30 minutes")
    start, end, label = commands.summary_window("yesterday's summary", {}, now)
    assert (end - start, label) == (timedelta(days=1), "yesterday")
    assert commands.summary_cameras("summary for cameras 6, 7, and 8 for the past hour", {}) == [6, 7, 8]
    assert commands.summary_cameras("summary for camera 3", {}) == [3]
    assert commands.summary_cameras("summary for today", {"cameras": [4, 5]}) == [4, 5]


def test_the_three_summary_questions_answer_accurately(roster):
    make_incident(roster, camera_id="cam-webcam", minutes_ago=20)  # camera 2, in the past hour
    make_incident(roster, camera_id="cam-phone", type_="fall", severity="critical", minutes_ago=30)  # camera 1
    make_incident(roster, camera_id="cam-webcam", minutes_ago=180)  # camera 2, earlier
    headers = login(client, ADMIN)

    def say(text):
        out = client.post("/assistant/command", json={"text": text}, headers=headers).json()
        assert out["intent"] == "summary" and out["status"] == "done", out
        return out

    today = roster.scalar(select(Incident.id).where(Incident.started_at >= commands.summaries.today_window()[0]))
    hour = say("Hey Friday, what's the summary for the past hour?")
    assert "the past hour" in hour["answer"] and "2 incidents in total" in hour["answer"]
    assert sorted(hour["data"]["camera_ids"]) == ["cam-phone", "cam-webcam"]

    picked = say("What's the summary for camera 2 for the past hour?")
    assert picked["answer"].startswith("Summary for camera 2, the past hour")
    assert "1 restricted-area entry" in picked["answer"] and picked["data"]["camera_ids"] == ["cam-webcam"]

    several = say("What's the summary for cameras 1, 2 and 9 for the past hour?")
    assert several["answer"].startswith("Summary for cameras 1 and 2, the past hour")
    assert several["answer"].endswith("Camera 9 not found.")

    if today is not None:  # the 3-hour-old incident is "today" unless the test runs just after midnight IST
        assert "incident" in say("What's the summary for today?")["answer"]

    guard = client.post("/assistant/command", json={"text": "summary for camera 1"}, headers=login(client, HEAD_GUARD))
    assert guard.json()["status"] == "done"  # Ravi sees camera 1
    hidden = login(client, GUARD)
    grant(roster, "Suresh Babu", "assistant")
    out = client.post("/assistant/command", json={"text": "summary for camera 1"}, headers=hidden).json()
    assert out["status"] == "refused"  # Suresh cannot see camera 1: it is not revealed


def test_show_several_cameras_and_who_is_on_duty(roster):
    headers = login(client, ADMIN)
    shown = client.post("/assistant/command", json={"text": "open cameras 1 and 2 and show them in detail"},
                        headers=headers).json()
    assert shown["data"]["camera_ids"] == ["cam-phone", "cam-webcam"] and "couldn't" not in shown["answer"]
    check_in(roster, "Ravi Kumar")
    duty = client.post("/assistant/command", json={"text": "who is on duty"}, headers=headers).json()
    assert duty["answer"] == "1 on duty: Ravi Kumar. 1 off duty: Suresh Babu."


# ---------- Rumik text to speech ----------

class FakeResponse:
    def __init__(self, status_code, content=b"", text=""):
        self.status_code, self.content, self.text = status_code, content, text


def test_tts_calls_rumik_and_reports_failures(monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "rumik_api_key", "")
    with pytest.raises(tts.TtsUnavailable, match="RUMIK_API_KEY"):
        tts.speak("hello")
    monkeypatch.setattr(s, "rumik_api_key", "k")
    monkeypatch.setattr(s, "rumik_gateway_url", "https://gateway.example/")
    seen = []

    def ok(url, json, headers, timeout):
        seen.append((url, json, headers["Authorization"]))
        return FakeResponse(200, b"RIFF....WAVE")
    monkeypatch.setattr(tts.httpx, "post", ok)
    audio, _ = tts.speak("Yes, boss.")
    assert audio.startswith(b"RIFF")
    assert seen == [("https://gateway.example/v1/tts", {"text": "Yes, boss.", "model": "mulberry",
                                                     "description": "warm, clear Indian woman, calm and confident"},
                      "Bearer k")]

    monkeypatch.setattr(tts.httpx, "post", lambda *a, **k: FakeResponse(401, text="bad key"))
    with pytest.raises(tts.TtsUnavailable, match="refused the key"):
        tts.speak("hello")


def test_speak_endpoint_says_503_without_rumik(roster, monkeypatch):
    monkeypatch.setattr(get_settings(), "rumik_api_key", "")
    out = client.post("/assistant/speak", json={"text": "hello"}, headers=login(client, ADMIN))
    assert out.status_code == 503


def test_tts_sends_the_voice_description_for_a_female_voice(monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "rumik_api_key", "k")
    monkeypatch.setattr(s, "rumik_gateway_url", "https://gateway.example")
    monkeypatch.setattr(s, "rumik_model", "mulberry")
    monkeypatch.setattr(s, "rumik_description", "calm, clear Indian woman")
    seen = []
    monkeypatch.setattr(tts.httpx, "post", lambda url, json, headers, timeout: seen.append(json) or FakeResponse(200, b"RIFF"))
    tts.speak("Yes, boss.")
    assert seen[0] == {"text": "Yes, boss.", "model": "mulberry", "description": "calm, clear Indian woman"}
