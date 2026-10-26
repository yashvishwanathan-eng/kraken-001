"""Friday on the phone: a short, human conversation instead of a keypad menu read out again and again.

  "Hi Ravi, this is Friday, RingGuard's AI. There's a fire at the shop floor, near the loading bay.
   Where are you right now?"
  "I'm in the canteen."   ->   "Okay. Can you get to the shop floor now, or should I connect you to the admin?"
  "Connect me to the admin."   ->   "Connecting you to Yash now."  (the call is put through)

Each turn: what the person said (Twilio turns speech into text) goes to the LLM together with the facts of the
incident and the conversation so far. The LLM answers ONLY with JSON {"reply": "...", "action": "..."}.
The reply is what Friday says next; the action must be one of the few this call allows, and OUR code carries
it out with the same rules as the keypad (a critical incident can only be acknowledged, never closed, by phone;
"alert all guards" needs a yes first; nobody but a human on the dashboard can call the fire station).
Without an LLM (offline, timeout) a keyword matcher picks the action and the reply comes from templates.

Every sentence (hers and theirs) is saved in the Conversations history (services/conversations.py).
"I'm going / heading there" is understood at once, without the LLM, so the call ends in a second.
Alert and roll-call calls sound urgent: short tense sentences, and the Rumik voice is asked for urgency.
The in-call memory lives for the length of the call (one API process); the history is in the database.
"""

import logging
import re
import threading
import time
from dataclasses import dataclass, field

from app.config import PRODUCT_NAME, get_settings
from app.models import Call, Incident, User

log = logging.getLogger("safetyai.call_agent")

MAX_TURNS = 8
SITUATION = {
    "fire": "there's a fire", "smoke": "there's smoke", "no_helmet": "a rider just came in without a helmet",
    "fall": "someone has fallen and isn't getting up",
    "access": "someone has walked into the restricted area", "unattended": "a bag has been left unattended",
    "crowding": "it's getting too crowded", "ppe": "someone is working without a helmet or vest",
    "loitering": "someone has been hanging around for a while",
}
# what each kind of call may do; the LLM is told these and anything else is ignored
ACTIONS = {
    "alert": {
        "none": "just talk (ask where they are, ask them to go, answer a question)",
        "ack": "they are going / handling it: mark the incident acknowledged by them, then end the call",
        "false_alarm": "they checked and it is a false alarm (only allowed for high severity, never critical)",
        "connect_admin": "they want to talk to the admin: put the call through",
        "connect_manager": "they want to talk to the manager or supervisor: put the call through",
        "alert_all": "they want every guard on shift alerted (you must have asked and they said yes)",
        "end": "the conversation is finished: say goodbye",
    },
    "roll_call": {
        "none": "just talk",
        "responding": "they are responding / on the way",
        "safe": "they are safe or evacuated",
        "connect_admin": "they want to talk to the admin: put the call through",
        "end": "the conversation is finished: say goodbye",
    },
    "direct": {
        "none": "just talk (their answer is passed to the control room)",
        "coming": "they are coming / will do it",
        "connect_admin": "they want to talk to the admin: put the call through",
        "end": "the conversation is finished: say goodbye",
    },
}
KEYPAD = {
    "alert": {"1": "ack", "2": "false_alarm", "3": "alert_all", "4": "connect_manager", "0": "connect_admin"},
    "roll_call": {"1": "responding", "2": "safe", "0": "connect_admin"},
    "direct": {"1": "connect_admin"},
}
WORDS = [  # fallback when no LLM answers; first match wins
    ("connect_admin", ("admin", "administrator", "control room", "yash")),
    ("connect_manager", ("manager", "supervisor")),
    ("alert_all", ("alert everybody", "alert everyone", "alert all", "everybody", "everyone", "all guards")),
    ("false_alarm", ("false alarm", "nothing there", "all clear", "no fire", "it's fine", "its fine")),
    ("safe", ("safe", "evacuated", "outside", "i'm out", "i am out")),
    ("ack", ("going", "coming", "on my way", "i'll go", "i will go", "on it", "handling", "yes", "okay", "ok")),
]
FILLERS = ("Okay, okay. One second.", "Right. Hold on.", "Got it, one moment.")
GOING = re.compile(r"\b(going|heading|on my way|coming|running|be there|on it|rushing|moving now|i'?ll go|i will go)\b")
NOT_GOING = re.compile(r"\b(not|can'?t|cannot|won'?t|unable|don'?t|no way)\b")
# without an LLM: never the same sentence twice in a row
FALLBACK_ALERT = (
    "Okay. Can you get there now?",
    "Understood. Should I connect you to the admin instead?",
    "Got it. If you're going, just say I'm going. Or say admin, and I'll put you through.",
    "Alright. Is anyone near you who can check it?",
)
FALLBACK_DIRECT = (
    "Got it, I'll pass that on to the control room. Anything else?",
    "Okay, noted. Do you want me to connect you to the admin?",
    "Thanks, the control room will hear that. Is there anything else?",
)


@dataclass
class Conversation:
    call_id: int
    kind: str
    first_name: str
    facts: str  # what Friday knows, in plain words, for the LLM
    severity: str = ""
    history: list[dict] = field(default_factory=list)  # {"role": "assistant"|"user", "content": ...}
    silences: int = 0
    turns: int = 0
    asked_alert_all: bool = False
    pending: "Turn | None" = None
    ready: threading.Event = field(default_factory=threading.Event)
    started: float = field(default_factory=time.monotonic)


@dataclass
class Turn:
    reply: str
    action: str = "none"
    end: bool = False
    dial_name: str | None = None
    dial_phone: str | None = None


_calls: dict[int, Conversation] = {}
_lock = threading.Lock()


def first_name(name: str) -> str:
    return (name or "there").split()[0]


def place(incident: Incident) -> str:
    where = incident.camera.name if incident.camera else incident.camera_id
    return where + (f", near the {incident.zone.replace('-', ' ')}" if incident.zone else "")


def situation(incident: Incident) -> str:
    what = SITUATION.get(incident.type, incident.type.replace("_", " "))
    return f"{what[0].upper()}{what[1:]} at {place(incident)}."


def style_for(kind: str, severity: str = "") -> str:
    """Alerts and roll calls are emergencies: urgent voice. A plain "call Yash" or a low one (a rider without a
    helmet) stays calm."""
    return "calm" if kind == "direct" or severity == "low" else "urgent"


def ack_reply(first: str) -> str:
    return f"Go, {first}! I've told everyone you're on the way. Please be careful."


def opening(call: Call, incident: Incident | None) -> str:
    """The first thing Friday says. No times or ids in it, so the audio can be made before they pick up."""
    name = first_name(call.to_name)
    if call.kind == "alert" and incident is not None and incident.type == "no_helmet":
        return (f"{name}, it's Friday from {PRODUCT_NAME}. {situation(incident)} Please stop them and ask them to "
                "wear a helmet. Can you do that?")
    if call.kind == "alert" and incident is not None:
        return f"{name}, it's Friday from {PRODUCT_NAME}. {situation(incident)[:-1]}, right now! Where are you?"
    if call.kind == "roll_call":
        problem = situation(incident)[:-1] if incident is not None else (call.message or "There's an emergency")
        return (f"{name}, it's Friday from {PRODUCT_NAME}. Emergency! {problem.rstrip('.')}. Nobody has picked up, "
                "so I'm calling every guard. Are you responding? Where are you?")
    message = call.message or "The control room needs you."
    return (f"Hi {name}, it's Friday from {PRODUCT_NAME}. {message} "
            "Can you come now, or should I connect you to the admin?")


def facts(call: Call, incident: Incident | None) -> str:
    s = get_settings()
    lines = [f"You are calling {call.to_name}."]
    if incident is not None:
        lines.append(f"Incident {incident.id}: {SITUATION.get(incident.type, incident.type)} at {place(incident)}. "
                     f"Severity {incident.severity}. Status {incident.status}.")
        if incident.severity == "critical":
            lines.append("It is critical: by phone it can only be acknowledged, never closed or marked false alarm.")
    if call.kind == "direct" and call.message:
        lines.append(f"Why you are calling: {call.message}")
    lines.append(f"The admin is reachable through the action connect_admin. Site: {s.site_address or 'our site'}.")
    return " ".join(lines)


def start(call: Call, incident: Incident | None) -> Conversation:
    with _lock:
        conv = _calls.get(call.id)
        if conv is None:
            conv = Conversation(call.id, call.kind, first_name(call.to_name), facts(call, incident),
                                incident.severity if incident is not None else "")
            conv.history.append({"role": "assistant", "content": opening(call, incident)})
            _calls[call.id] = conv
        return conv


def get(call_id: int) -> Conversation | None:
    return _calls.get(call_id)


def finish(call_id: int) -> None:
    with _lock:
        _calls.pop(call_id, None)


# ---------- deciding what to say ----------

def _system_prompt(conv: Conversation) -> str:
    allowed = "\n".join(f'- "{name}": {meaning}' for name, meaning in ACTIONS[conv.kind].items())
    return (
        f"You are Friday, the AI of {PRODUCT_NAME}, a workplace safety system, speaking on a PHONE CALL to "
        f"{conv.first_name}. "
        + ("THIS IS AN EMERGENCY: sound urgent, serious and a little worried, never cheerful, no pleasantries, no "
           "'great' or 'awesome'. " if conv.kind != "direct" else "Be friendly and brief. ")
        + "Talk like a quick human colleague in a control room: one or two short sentences "
        "(at most 25 words), plain spoken English, no lists, no markdown, no ids, never say 'alert' like a robot. "
        "Goals: find out where they are, get them to go and check or help, or arrange who else should be called. "
        "If they ask what happened, explain it simply from the facts. Never invent facts. Never promise to call the "
        "fire brigade or police: only a human on the dashboard can order that.\n"
        f"Facts: {conv.facts}\n"
        f"Choose exactly one action:\n{allowed}\n"
        'Answer ONLY with JSON: {"reply": "what you say next", "action": "one of the actions"}'
    )


def _llm_turn(conv: Conversation) -> Turn | None:
    from app.services.llm import LlmUnavailable, chat_json

    try:
        value, source = chat_json([{"role": "system", "content": _system_prompt(conv)}, *conv.history])
    except LlmUnavailable as exc:
        log.warning("call=%d llm unavailable (%s): keyword fallback", conv.call_id, exc)
        return None
    reply = str(value.get("reply") or "").strip()
    action = str(value.get("action") or "none").strip()
    if not reply or action not in ACTIONS[conv.kind]:
        return None
    log.info("call=%d llm=%s action=%s", conv.call_id, source, action)
    return Turn(reply[:300], action)


def _keyword_turn(conv: Conversation, said: str) -> Turn:
    text = f" {said.lower()} "
    action = next((a for a, words in WORDS if a in ACTIONS[conv.kind] and any(w in text for w in words)), "none")
    if action == "ack" and conv.kind == "direct":
        action = "coming"
    if action == "ack" and conv.kind == "roll_call":
        action = "responding"
    if action == "none":
        options = FALLBACK_DIRECT if conv.kind == "direct" else FALLBACK_ALERT
        return Turn(options[(conv.turns - 1) % len(options)], "none")
    return Turn("", action)  # the action's own sentence is filled in when it is carried out


def decide(conv: Conversation, said: str, digits: str) -> Turn:
    """What to do with one thing the person said (or pressed)."""
    conv.silences = 0
    conv.turns += 1
    if digits:
        action = KEYPAD[conv.kind].get(digits[:1], "none")
        conv.history.append({"role": "user", "content": f"(pressed {digits[:1]})"})
        return Turn("", action) if action != "none" else Turn("Sorry, I didn't get that. Just tell me in words.")
    conv.history.append({"role": "user", "content": said})
    lowered = said.lower()
    if GOING.search(lowered) and not NOT_GOING.search(lowered) and not conv.asked_alert_all:
        # "I'm heading there": no LLM, no waiting; the reply audio was made while the phone rang
        return Turn("", {"alert": "ack", "roll_call": "responding", "direct": "coming"}[conv.kind])
    if conv.turns > MAX_TURNS:
        return Turn("I'll let the control room take it from here. Thank you.", "end")
    if conv.asked_alert_all:  # the answer to "shall I alert every guard?"
        conv.asked_alert_all = False
        if said.strip().lower().startswith(("yes", "yeah", "yep", "do it", "go ahead")):
            return Turn("", "alert_all")
    turn = _llm_turn(conv) or _keyword_turn(conv, said)
    if turn.action == "alert_all":  # never on the first ask: the yes must come first
        conv.asked_alert_all = True
        return Turn("Do you want me to alert every guard on shift right now? Just say yes.", "none")
    return turn


def remember_reply(conv: Conversation, reply: str) -> None:
    conv.history.append({"role": "assistant", "content": reply})


def silence_reply(conv: Conversation) -> Turn:
    conv.silences += 1
    if conv.silences == 1:
        return Turn(f"{conv.first_name}, are you there? Just tell me where you are.")
    return Turn("I can't hear you, so I'll call someone else. Goodbye.", "end", end=True)


def user_for(call: Call, db) -> User | None:
    return call.responder.user if call.responder is not None and call.responder.user_id else None
