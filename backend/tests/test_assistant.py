"""AI assistant: question parser, LLM fallback, answers from rows, commands, SOP answers, voice."""

from datetime import datetime, timezone

import httpx
import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.main import app
from app.models import Camera, Incident, RollCall
from app.services import assistant, commands, llm, stt
from app.services.knowledge import NOT_FOUND, KnowledgeBase, split_document
from tests.conftest import login, make_incident

client = TestClient(app)
CAMERAS = [Camera(id="cam-webcam", name="Laptop Webcam", source="0", port=8101),
           Camera(id="cam-phone", name="Phone Camera", source="x", port=8102)]
NOW = datetime(2026, 10, 21, 9, 0, tzinfo=timezone.utc)  # 14:30 IST


# ---------- question -> filter ----------

@pytest.mark.parametrize("question, expected", [
    ("any falls today?", {"types": ["fall"]}),
    ("fires or smoke on the phone camera", {"types": ["fire"], "cameras": ["cam-phone"]}),
    ("show critical open incidents", {"severities": ["critical"], "statuses": ["open"]}),
    ("restricted area entries in the last 2 hours", {"types": ["access"]}),
    ("helmet violations yesterday", {"types": ["ppe"]}),
])
def test_keyword_filter(question, expected):
    f = assistant.keyword_filter(question, CAMERAS, NOW, "Asia/Kolkata")
    for key, value in expected.items():
        assert getattr(f, key) == value, (question, key)


def test_keyword_times_are_ist_ranges_in_utc():
    tz = "Asia/Kolkata"
    today = assistant.keyword_filter("what happened today", CAMERAS, NOW, tz)
    assert today.from_.isoformat() == "2026-10-20T18:30:00+00:00"  # 00:00 IST
    night = assistant.keyword_filter("anything last night", CAMERAS, NOW, tz)
    assert (night.from_.isoformat(), night.to.isoformat()) == ("2026-10-20T14:30:00+00:00",
                                                              "2026-10-21T00:30:00+00:00")  # 20:00-06:00 IST
    hour = assistant.keyword_filter("last hour", CAMERAS, NOW, tz)
    assert hour.from_.isoformat() == "2026-10-21T08:00:00+00:00"


def test_nonsense_has_no_filter():
    assert assistant.keyword_filter("banana weather poem", CAMERAS, NOW, "Asia/Kolkata").is_empty()


def test_llm_filter_used_when_valid(monkeypatch):
    monkeypatch.setattr(assistant, "chat_json", lambda messages: ({"types": ["fire"], "cameras": [], "limit": 5,
                                                                    "unexpected": "ignored"}, "groq"))
    f, source = assistant.parse_question("blah", CAMERAS, NOW)
    assert source == "groq" and f.types == ["fire"] and f.limit == 5


def test_bad_llm_output_falls_back_to_keywords(monkeypatch):
    monkeypatch.setattr(assistant, "chat_json", lambda messages: ({"types": ["explosion"], "limit": 999}, "groq"))
    f, source = assistant.parse_question("any falls", CAMERAS, NOW)
    assert source == "keywords" and f.types == ["fall"]


def test_no_llm_falls_back_to_keywords():
    f, source = assistant.parse_question("any fires", CAMERAS, NOW)  # nothing configured in tests
    assert source == "keywords" and f.types == ["fire"]


# ---------- LLM layer fallback ----------

def test_groq_failure_falls_back_to_ollama(monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "groq_api_key", "k")
    monkeypatch.setattr(s, "groq_model", "m")
    monkeypatch.setattr(s, "ollama_url", "http://ollama.test")
    hits = []

    def fake_post(url, **kwargs):
        hits.append(url)
        request = httpx.Request("POST", url)
        if "groq" in url:
            return httpx.Response(503, request=request)
        return httpx.Response(200, json={"message": {"content": '{"types": ["fall"]}'}}, request=request)

    monkeypatch.setattr(llm.httpx, "post", fake_post)
    value, source = llm.chat_json([{"role": "user", "content": "x"}])
    assert source == "ollama" and value == {"types": ["fall"]}
    assert sum("groq" in h for h in hits) == 2  # one try + one retry before falling back


def test_offline_mode_skips_groq(monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "groq_api_key", "k")
    monkeypatch.setattr(s, "groq_model", "m")
    monkeypatch.setattr(s, "offline_mode", True)
    assert not llm.groq_ready()
    with pytest.raises(llm.LlmUnavailable):
        llm.chat([{"role": "user", "content": "x"}])


# ---------- /assistant/query ----------

def test_query_answers_from_rows_and_respects_cameras(roster):
    make_incident(roster, camera_id="cam-webcam", type_="fall", severity="critical")
    make_incident(roster, camera_id="cam-phone", type_="fall", severity="critical", zone="loading-bay")
    admin = client.post("/assistant/query", json={"question": "any falls today?"}, headers=login(client, "1111"))
    assert admin.status_code == 200, admin.text
    body = admin.json()
    assert body["source"] == "keywords" and len(body["incidents"]) == 2
    assert body["answer"].startswith("Found 2 incidents: 2 person down")
    guard = client.post("/assistant/query", json={"question": "any falls today?"}, headers=login(client, "4444"))
    assert guard.status_code == 403  # Suresh has no assistant action
    manager = client.post("/assistant/query", json={"question": "falls today on the phone camera"},
                          headers=login(client, "2222")).json()
    assert [i["camera_id"] for i in manager["incidents"]] == ["cam-phone"]


def test_nonsense_question_gets_a_polite_answer(roster):
    body = client.post("/assistant/query", json={"question": "banana weather poem"},
                       headers=login(client, "1111")).json()
    assert body["answer"].startswith("I couldn't find that") and body["incidents"] == []


def test_query_validates_input(roster):
    assert client.post("/assistant/query", json={"question": ""}, headers=login(client, "1111")).status_code == 422


# ---------- commands ----------

@pytest.mark.parametrize("text, intent", [
    ("call everybody", "alert_all"), ("Evacuate the building", "evacuate"), ("call the fire station", "call_fire_station"),
    ("stop all calls", "stop_calls"), ("who hasn't responded?", "not_responded"), ("status report", "status_report"),
    ("show camera 2", "show_camera"), ("acknowledge incident 12", "ack"), ("resolve incident 4", "resolve"),
    ("call zone loading bay", "call_zone"), ("broadcast: gate 2 is closed", "broadcast_message"),
    ("what happened in the last hour", "query"), ("call ravi", "call_person"),
    ("what is the fire procedure for block b", "knowledge"), ("purple monkey", "unknown"),
])
def test_command_detection(text, intent):
    assert commands.detect(text)[0] == intent


def test_big_action_needs_a_spoken_yes(roster, simulate):
    headers = login(client, "1111")
    first = client.post("/assistant/command", json={"text": "call everybody"}, headers=headers).json()
    assert first["status"] == "needs_confirmation" and first["confirm_token"]
    assert roster.query(RollCall).count() == 0  # nothing happened yet
    other = client.post("/assistant/confirm", json={"confirm_token": first["confirm_token"], "confirmed": True},
                        headers=login(client, "2222"))
    assert other.status_code == 404  # someone else cannot confirm it
    second = client.post("/assistant/command", json={"text": "call everybody"}, headers=headers).json()
    done = client.post("/assistant/confirm", json={"confirm_token": second["confirm_token"], "confirmed": True},
                       headers=headers).json()
    assert done["status"] == "done" and "Alerting 4 guards" in done["answer"]
    reused = client.post("/assistant/confirm", json={"confirm_token": second["confirm_token"], "confirmed": True},
                         headers=headers)
    assert reused.status_code == 404  # a token works once


def test_saying_no_cancels(roster, simulate):
    headers = login(client, "1111")
    token = client.post("/assistant/command", json={"text": "evacuate"}, headers=headers).json()["confirm_token"]
    done = client.post("/assistant/confirm", json={"confirm_token": token, "confirmed": False}, headers=headers).json()
    assert "Cancelled" in done["answer"] and roster.query(RollCall).count() == 0


def test_fire_station_number_must_be_set(roster):
    headers = login(client, "1111")
    token = client.post("/assistant/command", json={"text": "call the fire station"},
                        headers=headers).json()["confirm_token"]
    done = client.post("/assistant/confirm", json={"confirm_token": token, "confirmed": True}, headers=headers).json()
    assert done["status"] == "refused" and "not set" in done["answer"]


def test_ack_and_resolve_by_voice(roster):
    headers = login(client, "1111")
    critical = make_incident(roster, type_="fire", severity="critical")
    acked = client.post("/assistant/command", json={"text": f"acknowledge incident {critical}"},
                        headers=headers).json()
    assert acked["status"] == "done"
    roster.expire_all()
    inc = roster.get(Incident, critical)
    assert inc.status == "acknowledged" and [e.via for e in inc.timeline if e.action == "acknowledged"] == ["voice"]
    refused = client.post("/assistant/command", json={"text": f"resolve incident {critical}"}, headers=headers).json()
    assert refused["status"] == "refused" and "dashboard" in refused["answer"]
    low = make_incident(roster, type_="loitering", severity="low")
    resolved = client.post("/assistant/command", json={"text": f"resolve incident {low}"}, headers=headers).json()
    assert resolved["status"] == "done"


def test_status_report_and_show_camera(roster):
    headers = login(client, "1111")
    make_incident(roster, type_="fire", severity="critical")
    report = client.post("/assistant/command", json={"text": "status report"}, headers=headers).json()
    assert report["answer"].startswith("1 open incident (1 critical)")
    shown = client.post("/assistant/command", json={"text": "show camera 2"}, headers=headers).json()
    assert shown["data"] == {"camera_id": "cam-webcam"}  # cameras sorted by id: cam-phone, cam-webcam


def test_command_permissions(roster):
    vikram = login(client, "2222")
    assert client.post("/assistant/command", json={"text": "status report"},
                       headers=login(client, "4444")).status_code == 403
    ravi = login(client, "3333")
    assert client.post("/assistant/command", json={"text": "status report"}, headers=ravi).status_code == 200
    assert client.post("/assistant/confirm", json={"confirm_token": "nope", "confirmed": True},
                       headers=vikram).status_code == 404


# ---------- SOP answers ----------

def test_sop_answer_comes_from_the_documents(tmp_path):
    (tmp_path / "fire.md").write_text("# Fire in Block B\nUse stairwell 2 and gather at the main gate.\n\n"
                                      "# Chemical spill\nClose the valve and use the spill kit.\n", encoding="utf-8")
    kb = KnowledgeBase(tmp_path)
    answer, sources, how = kb.answer("what is the fire procedure for block b")
    assert how == "documents" and sources == ["fire.md"] and "stairwell 2" in answer
    assert kb.answer("what is the wifi password")[0] == NOT_FOUND


def test_split_document_keeps_headings():
    chunks = split_document("# Person down\nDo not move them.\n\nBring the first-aid kit.", "sop.md")
    assert chunks[0].text.startswith("Person down: Do not move them.")


def test_sample_sop_is_answerable(roster):
    body = client.post("/assistant/command", json={"text": "what is the fire procedure for block b"},
                       headers=login(client, "1111")).json()
    assert body["intent"] == "knowledge" and "stairwell 2" in body["answer"]


# ---------- voice ----------

def test_voice_question(roster, monkeypatch):
    make_incident(roster, type_="fall", severity="critical")
    # "in the last hour" rather than "today": the test must not depend on the time of day (midnight IST)
    monkeypatch.setattr("app.routers.assistant.transcribe", lambda data, name, ctype: ("any falls in the last hour", "groq"))
    response = client.post("/assistant/voice", files={"audio": ("q.webm", b"fake-audio", "audio/webm")},
                           headers=login(client, "1111"))
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["transcript"] == "any falls in the last hour" and len(body["incidents"]) == 1


def test_voice_rejects_non_audio_and_reports_missing_stt(roster, monkeypatch):
    headers = login(client, "1111")
    assert client.post("/assistant/voice", files={"audio": ("q.txt", b"x", "text/plain")},
                       headers=headers).status_code == 422

    def unavailable(*args):
        raise stt.SttUnavailable("nothing configured")

    monkeypatch.setattr("app.routers.assistant.transcribe", unavailable)
    assert client.post("/assistant/voice", files={"audio": ("q.webm", b"x", "audio/webm")},
                       headers=headers).status_code == 503
