# SafetyAI: read this first

*The whole system in plain words. No code. About 30 minutes.*

Read this before anything else. When you finish it, you will be able to explain to anyone (a judge,
your teammate, your family) what we built, how it behaves, and what is still needed from you.
The technical details are in `TECHNICAL.md`; you do not need them yet.

---

## 1. The idea in one minute

Most factories already have CCTV cameras. But nobody watches them all day. When something goes
wrong (a fire starts, a worker collapses, a stranger walks into the server room) people usually find
out late, often the next morning from the recording.

Some companies add "AI" that shows alerts on a screen. But that screen also needs someone watching it.

**SafetyAI does not wait for someone to look.** It watches every camera, decides when something is
really wrong, and **phones the right person**. If they do not answer, it phones the next person.
It keeps a short video of every event, and you can ask it questions in plain English.

> **Our one-line pitch:** "Every factory has cameras nobody watches. Most AI adds a dashboard nobody
> watches. Ours picks up the phone."

---

## 2. One night, minute by minute

The best way to understand the system is to follow one real situation.

**21:42:05.** Smoke starts rising near the loading bay. The phone camera is filming that area.

**21:42:05 to 21:42:06.** The program watching that camera (we call it the *worker*) looks at the
picture about 10 times a second. Its AI model spots "smoke".

**21:42:06.** The worker does **not** raise the alarm yet. One picture could be steam, or a reflection.
It waits until it has seen smoke in **5 pictures in a row**. This short wait is on purpose: it is what
stops false alarms.

**21:42:07.** Five in a row. Now it is a real **incident**. In less than a second, the worker:
- writes it in the logbook (the *database*): "fire, critical, phone camera, loading bay, 21:42:07";
- saves a photo of that moment;
- tells the main program (the *API*) "incident 57 just happened".

**21:42:07.** Every dashboard screen that is allowed to see the phone camera shows incident 57 in red,
straight away. Nobody has to refresh the page.

**21:42:08.** The system phones **Ravi**, the Head Guard for that area. At the same time, **Anita**
(the Admin) gets a WhatsApp message with the photo: *"SafetyAI alert. Critical: Fire or smoke at Phone
Camera, loading bay, 9:42 PM. Incident 57."*

**21:42:12.** The 10-second video clip is ready: 5 seconds before the smoke was confirmed and 5 seconds
after. Anyone on the dashboard can play it.

**21:42:15.** Ravi's phone rings (within 15 seconds of the incident: that is our target). He picks up
and hears:
> "SafetyAI alert. Critical: Fire or smoke at Phone Camera, loading bay, 9:42 PM. Incident 57.
> Press 1 if you are going. Press 3 for the supervisor. Press 9 to repeat.
> To alert all guards, say alert everybody."

**21:42:22.** Ravi presses **1** (or simply says "I'm going"). He hears "Thank you Ravi. Acknowledged.
Stay safe." On every dashboard the incident now says **Acknowledged by Ravi Kumar**. All further calls
for this incident are cancelled.

**What if Ravi does not answer?**
- At **60 seconds**, the system phones **Anita** (Admin).
- At **2 minutes**, if still nobody has answered, it phones **Vikram** (Manager).
- The moment anyone answers and presses 1, the rest of the calls are cancelled.

**Afterwards**, someone at the dashboard looks at the clip, deals with it, and marks it **Resolved**.
Because it was a critical incident, it can only be *closed* from the dashboard, never by a phone
keypress. (If the phone mishears "stop", we do not want a real fire marked as closed.)

---

## 3. The parts of the system, as a building

Think of the system as a small security office.

| Part | In the building | What it really is | Example |
|---|---|---|---|
| **Cameras** | The eyes | Laptop webcam, a phone used as a camera, or real CCTV | The phone filming the loading bay |
| **Worker** | A watchman for each camera | A program, one per camera, that looks at every picture | "I see 3 people; person 7's feet are in the red zone" |
| **AI model** | The watchman's trained eye | A file that has learned what people, bags, (and later) helmets and fire look like | Draws a box around each person |
| **Rules** | The watchman's checklist | Small timers: "in the red zone for 3 seconds?" | Starts a stopwatch when someone steps in |
| **Database** | The logbook | Where every incident, user and zone is written | "Incident 57, fire, 21:42:07" |
| **API** | The front desk | The central program that everyone talks to | Answers the dashboard, places calls |
| **Dashboard** | The screens on the wall | The website your teammate builds | Live cameras, incident list, roll-call board |
| **Phone calls and WhatsApp** | The office phone | Outside services (Twilio, WhatsApp) | Ravi's phone ringing |
| **Assistant** | A helpful clerk | Answers questions and follows spoken orders | "What happened at the loading bay last night?" |

**Why is each camera's watchman a separate program?** Looking at pictures is hard work for a
computer. If it happened inside the front desk, the front desk would freeze while it thinks, and the
dashboards would stop updating. Separate programs also mean one broken camera never stops the others.

---

## 4. What it can spot

| What | When it becomes an incident | Example | Why the wait |
|---|---|---|---|
| **Restricted area entry** | Someone's **feet** are inside a forbidden zone for **3 seconds** | A visitor walks through the server-room door | Someone just passing the edge is not an intruder |
| **Missing helmet or vest** | A person in a helmet zone without a helmet or vest in **10 pictures in a row** | A worker takes off his helmet in the loading bay | A helmet can be hidden for a moment by an arm |
| **Fall** | Body near flat **and** hips dropped, staying down **5 seconds** | A worker slips and stays on the floor | Bending down to pick something up is not a fall |
| **Fire or smoke** | Seen in **5 pictures in a row** | Smoke near the loading bay | Orange lights, steam or a red shirt flash for a moment |
| **Unattended bag** | A bag stays still for **60 seconds** with nobody near it | A backpack left by the gate | People put bags down for a few seconds all the time |
| **Loitering** | The same person in one zone for **2 minutes** | Someone hanging around the loading bay | Walking through takes seconds, not minutes |
| **Crowding** | More people in a zone than its limit | 5 people in an area meant for 3 | (No wait: too many is too many) |

**Why the feet?** Imagine someone standing next to the red zone and reaching an arm over the line.
Their head and arm are "in the zone" on the picture, but they are not actually in the room. Their feet
tell you where they really stand.

**Why "near flat AND hips dropped" for a fall?** When you bend down to tie a shoelace, your back tilts
a lot, but your hips stay up. When you fall, your hips hit the floor. Checking both is what stops
"bending down" from becoming a false alarm.

**One event, one incident.** After an incident, the same camera will not report the same kind of
incident again for 60 seconds. Otherwise one fire would create an incident every tenth of a second.

> **Helmet and fire need special AI models** that we still have to choose (see section 11). Until
> then, those two rules are switched off. Everything else works today.

---

## 5. Who gets told, and how

Every incident has a **severity**: how serious it is.

| Severity | Which incidents | Who is phoned | Who gets WhatsApp |
|---|---|---|---|
| **Critical** | Fire, fall, and restricted entry **at night** (8 PM to 6 AM) | Head Guard now → Admin after 60 s → Manager after 2 min | Admin, straight away, with the photo |
| **High** | Restricted entry in the day, unattended bag, crowding | Head Guard now → Manager after 60 s | Admin, straight away |
| **Low** | Missing helmet/vest, loitering | Nobody (see the exception below) | The manager of that zone, plus a summary every evening at 6 PM |

**Examples**
- *2 PM, a visitor walks into the server room:* **high**. Ravi is called. If he does not answer in a
  minute, Vikram is called. Anita gets a WhatsApp but no call.
- *11 PM, same visitor:* **critical** (the night rule). Ravi, then Anita, then Vikram.
- *A worker without a helmet:* **low**. No phone rings; Vikram gets a WhatsApp, and it appears in the
  6 PM summary.
- *The exception:* if the **same zone** has **3 low incidents within an hour** (someone keeps taking
  their helmet off), the Head Guard is called once, because now it is a pattern.

**Things the system never does:** it never calls the fire service or 112 by itself. It never calls
anyone above the Admin. The fire station is only ever called when a person orders it and says "yes".

---

## 6. The phone call

**What the guard hears** depends on the incident:
> "SafetyAI alert. High: Unattended bag at Laptop Webcam, server room door, 2:10 PM. Incident 61.
> Press 1 if you are going. Press 2 if it is a false alarm. Press 3 for the supervisor. Press 9 to repeat."

**The keys**

| Key | Or say... | What happens |
|---|---|---|
| **1** | "I'm going", "coming", "okay", "yes" | Acknowledged. Every dashboard shows who took it. All other calls stop. |
| **2** | "false alarm", "all clear" | Marked false alarm (with the guard's name). **Only for high incidents.** |
| **3** | "supervisor", "manager" | Connects the guard straight to the Manager's phone. |
| **9** | "repeat", "again" | Reads the message again. |

**Why can't a guard close a critical incident by phone?** A phone line can be noisy and speech can be
misheard. For a fire or a fall, we only let the phone say "I'm on it". Closing it needs a person at the
dashboard who has looked at the clip.

**Pressing 1 twice** (or the phone network sending the keypress twice) does nothing extra. The system
remembers it was already acknowledged and says so.

---

## 7. "Alert everybody" and the roll call

Sometimes one guard is not enough: a real fire needs everyone.

**How it starts** (any of three ways):
- On the phone: the Head Guard or Admin says **"alert everybody"**, then presses **1** to confirm.
- On the laptop: someone says **"call everybody"**, and the assistant asks "Say yes to confirm".
- On the dashboard: the Alert Everybody button.

**What happens, in about 2 seconds:**
1. Every guard on shift gets a WhatsApp: *"SafetyAI ALERT from Ravi Kumar: Fire in Block B. You will
   get a call: press 1 if responding, 2 if safe."*
2. A **red banner** appears on every dashboard.
3. Every guard on shift is called, one new call per second (that is the phone company's normal speed,
   so 20 guards take about 20 seconds to all start ringing; say this honestly on stage).

**The roll-call board** then fills up live as people answer:
> **14 of 20 responded** · 11 responding · 3 safe · 6 not reached
> Responding: Ravi, Meena, Arun... · Safe: Priya, ... · Not reached: Suresh, ...

- **1** = "I'm responding" (going to help).
- **2** = "I'm safe" (evacuated, outside).
- Did not pick up, or hung up without pressing anything = **not reached**.

---

## 8. People, roles and what each person sees

Everyone logs in on the control-room laptop with a **PIN**. There are four demo people:

| Person | Role | Sees | Can do |
|---|---|---|---|
| **Anita Rao** | Admin | All cameras | Everything, including adding users and resetting PINs |
| **Vikram Shah** | Manager | Both cameras | Acknowledge, resolve, see the audit log, edit zones and the roster, alert everybody |
| **Ravi Kumar** | Head Guard | Both cameras | Acknowledge, resolve, alert everybody, use the assistant |
| **Suresh Babu** | Guard | **Only the webcam** | Acknowledge |

**Example:** Suresh logs in. He sees only the webcam, only the webcam's incidents, and only the
webcam's video. Even if he tried to ask the system directly for the phone camera, it would answer
"not found". This is the **camera delegation** the judges will look for.

**The role name is just a label.** What someone can actually do is a list of permissions. To create a
new role like "Supervisor", you do not change any program: you just give a new person the right list.

**The audit log** records every action: who did it, what, when, and from where (dashboard, phone or
voice). Example: *"Ravi Kumar · acknowledged incident 57 · 9:42 PM · phone"*. Only the Admin and
Manager can read it.

---

## 9. The assistant

You can **ask** questions and **give** orders, by typing or by holding the spacebar and speaking.

**Questions about incidents** (examples):
- "Any falls today?" → *"Found 2 incidents: 2 person down. Latest: person down at Phone Camera,
  loading bay, 3:12 PM on 21 Oct (resolved)."*
- "What happened at the loading bay last night?"
- "Show critical open incidents."
- A nonsense question gets a polite *"I couldn't find that."*, never an error.

**Orders** (examples): "status report", "show camera 2", "who hasn't responded?", "acknowledge
incident 12", "call Ravi", "call zone loading bay", "broadcast: gate 2 is closed", "stop all calls".

**Big orders need a spoken "yes" first:** "call everybody", "evacuate", "call the fire station". The
assistant answers *"This will message and call every guard on shift. Say yes to confirm."* Nothing
happens until the same person says yes within a minute. Calling one person or one zone does not need
a yes.

**Questions about procedures** come from your own documents, never from the AI's imagination:
- "What is the fire procedure for Block B?" → *"Press the nearest red fire alarm. Do not use the lifts.
  Leave by stairwell 2 and gather at the main gate..."*
- If the documents do not cover it: *"I couldn't find that in our documents."*

**Why the AI cannot make things up:** the AI is only allowed to turn your question into a search
("fall incidents, today"). Our own program does the searching in the logbook and writes the answer
from what it actually found. The AI never touches the logbook directly.

**Without internet:** the assistant switches by itself to a smaller AI that runs on the laptop. If no
AI is available at all, it still understands common questions using a built-in word list.

---

## 10. Privacy and safety, in plain words

- **Video never leaves the laptop.** Only short text (the alert message, a question) and a temporary
  photo link go to the phone and AI services.
- **No face recognition.** The system knows "person 7 in this camera right now", never who they are.
- **PINs are never stored**, only a scrambled fingerprint of them that cannot be turned back.
- **Every action is logged** (the audit log).
- **From the internet, only the phone-call part is reachable.** The dashboard, the video and the photos
  stay inside the laptop.
- **If something outside fails** (the phone service, WhatsApp, the AI), everything else keeps working
  and the dashboard still shows every incident.

---

## 11. What is done, and what needs you

**Done and tested:** everything that does not need your accounts or decisions. That means detection
(except helmet/fire), the rules, clips, the dashboard API, logins and permissions, the call ladder,
phone keys and speech, WhatsApp messages, alert everybody, the roll call, broadcast, the assistant,
SOP answers, the audit log and the camera "still alive" check. 140 automatic tests pass, and a full
run worked end to end in "pretend" mode (calls and messages written down instead of sent).

**Blanks only you can fill.** Until you do, that part switches itself off safely. Nothing breaks.

| What | Why it is needed | Where to get it |
|---|---|---|
| **Twilio Account SID, Auth Token, phone number** | To make real phone calls | Your Twilio console (twilio.com) |
| **Verified phone numbers** | A Twilio trial can only call numbers you have verified | Twilio console → Verified Caller IDs |
| **Public address (ngrok)** | So Twilio can reach the laptop to ask "what do I say?" | Run `ngrok http 8000`, copy the https address |
| **WhatsApp URL and token** | To send WhatsApp alerts | Your Bhoomi project (we may need to copy its message format) |
| **Phone number for each demo person** | So the ladder knows whom to call | Your team's phones |
| **Groq key and model names** | For the fast online AI and speech-to-text | console.groq.com |
| **Ollama** | For the offline AI | Install Ollama, then download `llama3.2:3b` |
| **Fire station number and site address** | Only if you want the "call the fire station" order to work | You decide |
| **Helmet/vest and fire/smoke models** | To switch on those two rules | Your choice; options are in `STUDY.md` 3.9 |
| **Your real SOPs** | So procedure answers are real, not the sample | Put them in `backend/knowledge/` |
| **Sign-off on the API details** | Your teammate's screens depend on them | `CONTRACT.md`, items P1 to P18 |

> **Twilio trial tip:** on a trial account, Twilio plays its own short notice before our message, and
> the person may have to press a key to hear it. Try it once with your phone before the demo.

---

## 12. Words you will hear

| Word | Meaning |
|---|---|
| **Incident** | One confirmed problem: type, camera, time, severity, photo, clip |
| **Severity** | How serious: critical, high or low |
| **Acknowledge** | "I've got it" (from the phone or the dashboard). Stops the calls |
| **Resolve / false alarm** | Closing an incident: dealt with, or not real |
| **Escalation / ladder** | Calling the next person when the previous one does not answer |
| **Roll call** | Everyone pressing 1 (responding) or 2 (safe) after "alert everybody" |
| **Zone** | An area drawn on a camera picture (restricted, helmet, work) |
| **Worker** | The program watching one camera |
| **API** | The central program everything talks to |
| **Dashboard** | The website on the screens |
| **Track ID** | The number the system gives a person while they stay in view |
| **Clip / snapshot** | The 10-second video / the photo of an incident |
| **Audit log** | The record of who did what, when, and how |
| **Heartbeat** | Each camera's "I'm alive" message every 5 seconds |
| **Simulate mode** | Pretend calls and messages, for testing without real phones |

---

## 13. What to read next

1. **`GUIDE.md`**: each idea from zero, simple first, then how it really works.
2. **`TECHNICAL.md`**: the full technical reference, when you are ready.
3. **`STUDY.md`**: the code-level notes with small examples you can run.
