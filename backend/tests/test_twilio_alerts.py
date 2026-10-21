"""Twilio webhooks (signed like Twilio signs them), alert everybody, roll call, broadcast, share links."""

from fastapi.testclient import TestClient

from app.main import app
from app.models import Call, Incident, RollCall
from app.services.calls import calls
from app.services.permissions import sign_share
from tests.conftest import PUBLIC_URL, login, make_incident, twilio_post

client = TestClient(app)


def alert_call(db, incident_id, name="Ravi Kumar"):
    from app.models import Responder
    responder = db.query(Responder).filter_by(name=name).one()
    call = Call(kind="alert", incident_id=incident_id, responder_id=responder.id, to_name=name,
                to_phone=responder.phone, status="in-progress")
    db.add(call)
    db.commit()
    return call.id


def incident(db, incident_id):
    db.expire_all()
    return db.get(Incident, incident_id)


# ---------- signature ----------

def test_unsigned_or_forged_webhooks_are_rejected(roster, twilio_on):
    call_id = alert_call(roster, make_incident(roster))
    assert client.post(f"/twilio/voice?call_id={call_id}", data={}).status_code == 403
    forged = twilio_post(client, f"/twilio/gather?call_id={call_id}", {"Digits": "1"}, token="wrong-token")
    assert forged.status_code == 403
    assert incident(roster, roster.get(Call, call_id).incident_id).status == "open"


def test_webhooks_refuse_when_twilio_is_not_configured(roster):
    assert client.post("/twilio/voice?call_id=1", data={}).status_code == 503


# ---------- what the guard hears ----------

def test_alert_call_reads_the_alert_and_options(roster, twilio_on):
    critical = alert_call(roster, make_incident(roster, type_="fire", severity="critical"))
    high = alert_call(roster, make_incident(roster, type_="unattended", severity="high"))
    critical_xml = twilio_post(client, f"/twilio/voice?call_id={critical}", {}).text
    high_xml = twilio_post(client, f"/twilio/voice?call_id={high}", {}).text
    assert "<Gather" in critical_xml and "Fire or smoke" in critical_xml and "Press 1" in critical_xml
    assert "Press 2" not in critical_xml  # critical: phone can only acknowledge
    assert "Press 2 if it is a false alarm" in high_xml
    assert "say alert everybody" in critical_xml  # Ravi (Head Guard) has the alert_all action
    assert f"{PUBLIC_URL}/twilio/gather?call_id={critical}" in critical_xml


# ---------- keypresses ----------

def test_press_1_acknowledges_once(roster, twilio_on):
    incident_id = make_incident(roster, severity="critical", type_="fall")
    call_id = alert_call(roster, incident_id)
    first = twilio_post(client, f"/twilio/gather?call_id={call_id}", {"Digits": "1"})
    assert "Acknowledged" in first.text
    again = twilio_post(client, f"/twilio/gather?call_id={call_id}", {"Digits": "1"})  # Twilio retry
    assert "Already acknowledged by Ravi Kumar" in again.text
    inc = incident(roster, incident_id)
    assert inc.status == "acknowledged" and inc.acknowledged_by == "Ravi Kumar"
    acks = [(e.actor, e.via) for e in inc.timeline if e.action == "acknowledged"]
    assert acks == [("Ravi Kumar", "keypad")]


def test_saying_going_acknowledges(roster, twilio_on):
    incident_id = make_incident(roster)
    call_id = alert_call(roster, incident_id)
    twilio_post(client, f"/twilio/gather?call_id={call_id}", {"SpeechResult": "Okay I'm going now"})
    inc = incident(roster, incident_id)
    assert inc.status == "acknowledged" and [e.via for e in inc.timeline if e.action == "acknowledged"] == ["voice"]


def test_critical_cannot_be_closed_by_phone(roster, twilio_on):
    incident_id = make_incident(roster, type_="fire", severity="critical")
    call_id = alert_call(roster, incident_id)
    reply = twilio_post(client, f"/twilio/gather?call_id={call_id}", {"Digits": "2"})
    assert "can only be acknowledged" in reply.text
    assert incident(roster, incident_id).status == "open"


def test_high_can_be_marked_false_alarm_by_phone(roster, twilio_on):
    incident_id = make_incident(roster, type_="crowding", severity="high")
    call_id = alert_call(roster, incident_id)
    reply = twilio_post(client, f"/twilio/gather?call_id={call_id}", {"Digits": "2"})
    assert "false alarm" in reply.text.lower()
    assert incident(roster, incident_id).status == "false_alarm"


def test_press_3_connects_the_supervisor(roster, twilio_on):
    call_id = alert_call(roster, make_incident(roster))
    reply = twilio_post(client, f"/twilio/gather?call_id={call_id}", {"Digits": "3"})
    assert "<Dial" in reply.text and "+919800000002" in reply.text  # Vikram, the manager


def test_no_answer_goes_on_the_timeline(roster, twilio_on):
    incident_id = make_incident(roster)
    call_id = alert_call(roster, incident_id)
    twilio_post(client, f"/twilio/status?call_id={call_id}", {"CallStatus": "no-answer", "CallSid": "CA1"})
    twilio_post(client, f"/twilio/status?call_id={call_id}", {"CallStatus": "no-answer", "CallSid": "CA1"})
    actions = [(e.action, e.target) for e in incident(roster, incident_id).timeline]
    assert actions.count(("call_no_answer", "Ravi Kumar")) == 1


def test_alert_everybody_from_the_phone_needs_confirmation(roster, twilio_on, simulate):
    incident_id = make_incident(roster, type_="fire", severity="critical")
    call_id = alert_call(roster, incident_id)
    ask = twilio_post(client, f"/twilio/gather?call_id={call_id}", {"SpeechResult": "alert everybody"})
    assert "Press 1 to confirm" in ask.text and roster.query(RollCall).count() == 0
    done = twilio_post(client, f"/twilio/gather?call_id={call_id}&step=confirm_all", {"Digits": "1"})
    assert "Alerting 4 guards" in done.text
    roster.expire_all()
    assert roster.query(RollCall).one().started_by == "Ravi Kumar"


def test_guard_without_permission_cannot_alert_everybody(roster, twilio_on):
    call_id = alert_call(roster, make_incident(roster), name="Suresh Babu")
    reply = twilio_post(client, f"/twilio/gather?call_id={call_id}", {"SpeechResult": "alert everybody"})
    assert "Only the head guard or admin" in reply.text


# ---------- alert everybody and roll call ----------

def test_alert_all_from_dashboard_and_roll_call_board(roster, twilio_on, simulate):
    headers = login(client, "1111")
    incident_id = make_incident(roster, type_="fire", severity="critical")
    response = client.post("/alerts/all", json={"message": "Fire in Block B", "incident_id": incident_id},
                           headers=headers)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["recipients"] == 4 and body["whatsapp_sent"] == 4 and body["calls_queued"] == 4
    assert len(simulate.sent) == 4

    board = client.get("/roll-call", params={"incident_id": incident_id}, headers=headers).json()
    assert (board["total"], board["responding"], board["safe"], board["not_reached"]) == (4, 0, 0, 0)

    calls_ = roster.query(Call).filter_by(roll_call_id=body["roll_call_id"]).order_by(Call.id).all()
    twilio_post(client, f"/twilio/gather?call_id={calls_[0].id}", {"Digits": "1"})
    twilio_post(client, f"/twilio/gather?call_id={calls_[1].id}", {"SpeechResult": "I am safe"})
    twilio_post(client, f"/twilio/status?call_id={calls_[2].id}", {"CallStatus": "no-answer", "CallSid": "CA9"})
    board = client.get("/roll-call", params={"incident_id": incident_id}, headers=headers).json()
    assert (board["responding"], board["safe"], board["not_reached"]) == (1, 1, 1)
    assert {g["status"] for g in board["guards"]} == {"responding", "safe", "not_reached", "pending"}
    actions = [e.action for e in incident(roster, incident_id).timeline]
    assert "alert_all" in actions and "roll_call_responded" in actions and "roll_call_safe" in actions


def test_alert_all_needs_permission_and_valid_body(roster):
    assert client.post("/alerts/all", json={"message": "x"}, headers=login(client, "4444")).status_code == 403
    admin = login(client, "1111")
    assert client.post("/alerts/all", json={"message": ""}, headers=admin).status_code == 422
    assert client.post("/alerts/all", json={"message": "x", "incident_id": 999}, headers=admin).status_code == 404


def test_roll_call_404_when_none(roster):
    assert client.get("/roll-call", headers=login(client, "1111")).status_code == 404


# ---------- broadcast and share links ----------

def test_broadcast_checks_the_audio(roster, simulate):
    headers = login(client, "1111")
    bad = client.post("/broadcast", data={"zone": "loading-bay"}, files={"audio": ("x.txt", b"hi", "text/plain")},
                      headers=headers)
    assert bad.status_code == 422
    good = client.post("/broadcast", data={"zone": "loading-bay"},
                       files={"audio": ("msg.mp3", b"ID3fake-mp3-bytes", "audio/mpeg")}, headers=headers)
    assert good.status_code == 200, good.text
    assert good.json()["recipients"] == ["Vikram Shah"] and good.json()["queued"] == 1
    assert client.post("/broadcast", data={"zone": "x"}, files={"audio": ("m.mp3", b"x", "audio/mpeg")},
                       headers=login(client, "4444")).status_code == 403


def test_share_links_are_signed(roster):
    from app.config import media_root
    (media_root() / "share-test.jpg").write_bytes(b"\xff\xd8test")
    try:
        assert client.get(f"/share/media/share-test.jpg?st={sign_share('share-test.jpg')}").status_code == 200
        assert client.get("/share/media/share-test.jpg?st=1.bad").status_code == 403
        assert client.get(f"/share/media/other.jpg?st={sign_share('share-test.jpg')}").status_code == 403
        assert client.get(f"/share/media/..%2Fsecret.jpg?st={sign_share('../secret.jpg')}").status_code in (403, 404)
    finally:
        (media_root() / "share-test.jpg").unlink()


def test_calls_off_without_keys(roster):
    assert calls.mode == "off"  # no Twilio keys in the test settings


def test_public_address_only_reaches_webhooks(roster, twilio_on):
    public = {"host": "safety.example.test"}
    assert client.get("/health", headers=public).status_code == 404
    assert client.get("/media/1.jpg", headers=public).status_code == 404
    assert client.get("/docs", headers=public).status_code == 404
    assert client.post("/twilio/voice?call_id=1", data={}, headers=public).status_code == 403  # reached, bad signature
    assert client.get("/health").status_code == 200  # local access unchanged
