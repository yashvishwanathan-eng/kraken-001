# SafetyAI from zero: the guide

**Start here.** This guide assumes you know nothing about any of it. Read it top to bottom once.
Every topic has the same five parts:

- **Simple**: the idea with an everyday comparison. Read this first.
- **Deeper**: how it really works. Read this once the simple version makes sense.
- **In our project**: where it lives in the code.
- **Try it**: something to run on your laptop (PowerShell, from `backend\`, venv active).
- **Check yourself**: one question. If you can answer it out loud, you understand it well enough
  to explain it to a judge.

`docs/STUDY.md` is the next step after this guide: the same topics with real code and real outputs.

## Contents
- [Part A. The big picture](#part-a-the-big-picture)
- [Part B. Foundations: the building blocks of any backend](#part-b-foundations)
- [Part C. Video and computer vision](#part-c-video-and-computer-vision)
- [Part D. From detections to incidents](#part-d-from-detections-to-incidents)
- [Part E. Users, security and live updates](#part-e-users-security-and-live-updates)
- [Part F. Coming next: calls, WhatsApp and the AI assistant](#part-f-coming-next)
- [Part G. How to use it: your first full run](#part-g-how-to-use-it)
- [Part H. Glossary](#part-h-glossary)

---

## Part A. The big picture

### A1. What SafetyAI does, in one breath
Cameras film a factory. A program watches every camera, spots dangerous situations (someone in a
forbidden area, a fall, a fire, a bag left alone), saves a short video of each one, shows it on a
dashboard, and **phones the right guard** until someone answers.

### A2. The parts, as a restaurant
| Restaurant | SafetyAI | What it is |
|---|---|---|
| Waiters watching tables | **Workers** (`worker/`) | One program per camera. Watches video, runs the AI, applies rules. |
| Kitchen | **API** (`app/`) | The central program. Stores things, answers questions, sends alerts. |
| Order book | **Database** (PostgreSQL) | Where every incident, user and zone is written down. |
| Menu and dining room | **Dashboard** (`frontend/`) | What people see. Built by your teammate. |
| The phone on the wall | **Twilio / WhatsApp** | Outside services that make calls and send messages. |

### A3. One incident, start to finish
1. The webcam sends pictures. The **worker** grabs the newest one.
2. The **AI model** finds people in it and gives each a number (track ID).
3. The **rules** notice person 7's feet have been inside the "server-room-door" zone for 3 seconds.
4. The worker **saves** an incident in the database, saves a photo, and 5 seconds later a 10-second video.
5. The worker **tells the API**: "incident 12 just happened".
6. The API **pushes** it to every dashboard allowed to see that camera. It appears instantly.
7. (Coming next) The API **phones** the Head Guard. He presses 1 to say "I'm going". The calls stop.

**Check yourself:** why is the worker a separate program from the API?
(Answer: the AI is heavy. If it ran inside the API, the API would freeze while the AI thinks, and
the dashboard would stop responding.)

---

## Part B. Foundations

### B1. Backend and frontend
**Simple.** The frontend is the shop window; the backend is the stock room. Customers only see the
window, but everything they get comes from the stock room.
**Deeper.** The frontend (React) runs in the browser and draws screens. The backend (Python) runs on
the laptop, holds the data and does the work. They talk over HTTP using the **API contract**:
an agreed list of what the backend offers (`docs/CONTRACT.md`). Because both sides follow the same
contract, you and your teammate can build at the same time.
**In our project.** You own `backend/`. Your teammate owns `frontend/`. We never touch `frontend/`.
**Check yourself:** why do we need a written contract at all?

### B2. HTTP, API, endpoint, JSON
**Simple.** An **API** is a counter with labelled windows. Each window (**endpoint**) does one job:
"window /events: list incidents", "window /auth/pin: log in". You hand in a slip, you get a slip back.
**Deeper.**
- **HTTP** is the language of that counter. A request has a **method** (GET = read, POST = create or
  act, PUT = change, DELETE = remove), a **path** (`/events/12`), **headers** (extra info like your
  login token) and sometimes a **body**.
- **JSON** is the format of the slips: `{"pin": "4444"}`. Plain text that both Python and JavaScript read.
- The answer comes with a **status code**: 200 OK, 201 created, 204 done (nothing to return),
  400 bad request, **401 not logged in**, **403 logged in but not allowed**, 404 not found,
  409 conflict (e.g. already closed), **422 your data is wrong**, 429 too many tries,
  500 server bug (we never want this), 503 a part we depend on is down.
**In our project.** Every endpoint is listed in `docs/CONTRACT.md`.
**Try it.** Start the API (Part G) and open `http://127.0.0.1:8000/health` in a browser. That is a GET
request; the text you see is JSON.
**Check yourself:** what is the difference between 401 and 403?

### B3. FastAPI and Uvicorn
**Simple.** FastAPI is the kit for building the counter; Uvicorn is the building it stands in
(it listens on port 8000 and passes requests to FastAPI).
**Deeper.** In FastAPI you write a normal Python function and put `@router.get("/health")` above it.
FastAPI turns incoming JSON into Python objects, checks them against **Pydantic models** (typed
descriptions of what is allowed, e.g. "pin is 4 to 8 digits"), and turns your return value back into
JSON. If the input breaks the rules, FastAPI answers 422 with a clear message before your code runs.
**In our project.** `app/main.py` creates the app; each file in `app/routers/` is one group of
endpoints; `app/schemas.py` holds the Pydantic models.
**Try it.** Open `http://127.0.0.1:8000/docs`. FastAPI makes this page by itself: every endpoint,
with a "Try it out" button.
**Check yourself:** where does the 422 come from, our code or FastAPI?

### B4. Database, SQL, ORM, migrations
**Simple.** The database is a set of spreadsheets (**tables**). Each row is one thing (one incident,
one user). It never forgets, even after a restart.
**Deeper.**
- **PostgreSQL** is the database program. It runs locally on your laptop.
- **SQL** is the language for asking it things: `SELECT * FROM incidents WHERE status = 'open'`.
- An **ORM** (SQLAlchemy) lets us write Python instead of SQL: each table is a Python class
  (`Incident`, `User`...) in `app/models.py`. SQLAlchemy writes the SQL for us.
- An **index** is like the index at the back of a book: it makes searching by `started_at`, `type`,
  `camera_id`, `status` fast.
- A **migration** (Alembic) is a recipe that changes the tables, e.g. "add column max_people". Each
  recipe is a file in `alembic/versions/`. `alembic upgrade head` applies any recipes your database
  has not had yet. This is how your laptop's database and mine stay identical.
- A **transaction** groups changes so they all happen or none do (e.g. "acknowledge" + its audit row).
**In our project.** Tables: cameras, zones, users, sessions, responders, incidents, timeline_events,
audit_log. Tests use a separate copy, `safetyai_test`, so they never touch real data.
**Try it.** `psql -U <your db user> -d safetyai -c "select id, type, status from incidents;"`
**Check yourself:** you added a column to `models.py`. What else must happen before the database has it?

### B5. `.env` and secrets
**Simple.** `.env` is the locked drawer for keys and passwords. The code knows the drawer exists,
but the keys never go into git.
**Deeper.** `app/config.py` reads `.env` when the program starts. Secrets (Twilio token, Groq key,
`STREAM_SECRET`, demo PINs) live only there. `.env.example` lists the same names with empty values,
so anyone can see *what* to fill in without seeing *your* values. `.gitignore` makes git ignore `.env`.
**Check yourself:** if `.env` leaked, what would you do first? (Change every key in it.)

### B6. Python virtual environment, pip, requirements
**Simple.** A venv is a private toolbox for this project, so its tools do not clash with other projects.
**Deeper.** `py -3.11 -m venv .venv` makes the toolbox; `.venv\Scripts\activate` opens it;
`pip install -r requirements.txt` fills it with exactly the versions listed (pinned with `==`), so
everyone runs the same code.
**Check yourself:** why pin exact versions?

### B7. Git: commit, branch, push
**Simple.** Git is a save-game system for code. A **commit** is a save point. A **branch** is a separate
timeline you can experiment on. **Push** uploads your save points to GitHub.
**Deeper.** Day work happens on `claude/festive-clarke-6w890k`. `main` is kept for the version that
always runs (the roadmap's rule: merge after the 9 PM check). I commit locally and push only when you
say yes (CLAUDE.md rule 10).
**Try it.** `git log --oneline -5` shows the last five save points.
**Check yourself:** what is the difference between commit and push?

### B8. Tests (pytest)
**Simple.** A test is a small robot that uses the program and shouts if something is wrong.
**Deeper.** Each test sets something up, does one thing, and checks the result with `assert`.
Example: "a person stands in the zone for 5 s → exactly one access incident at about 3 s".
We have 55 tests. Run them before every merge. They do not need a camera; rules are tested with
**fake detections**.
**Try it.** `pytest` (all) or `pytest tests/test_rules.py -v` (one file, with names).
**Check yourself:** why can we test the fall rule without anyone falling?

### B9. Processes, threads, async
**Simple.** A **process** is a separate program (its own kitchen). A **thread** is an extra pair of hands
inside one program. **async** is one cook juggling many pots: while one boils, they stir another.
**Deeper.**
- Each camera worker is its own **process**, so one camera crashing cannot take down the other or the API.
- Inside a worker, a **thread** reads the camera nonstop while the main loop runs the AI
  (the latest-frame pattern, C1).
- The API uses **async** for things that mostly wait (WebSockets, streaming video): it can hold many
  open connections without a thread each.
**Check yourself:** why is a crashing worker not a disaster?

---

## Part C. Video and computer vision

### C1. Images, frames and FPS
**Simple.** Video is a flip-book. Each page is a **frame**. FPS = pages per second.
**Deeper.** To a computer, a frame is a grid of numbers: height × width × 3 colours (OpenCV uses
blue-green-red order). A webcam gives about 30 frames a second. Our AI analyses **every 3rd frame**
(about 10 per second), because people barely move in a tenth of a second.
The **latest-frame thread**: one thread grabs frames nonstop and keeps only the newest. The AI always
takes the newest. Without it, a slow AI falls further and further behind (our test: 2.4 s behind after
3 seconds, versus always about 0.18 s with the thread).
**In our project.** `worker/capture.py`.
**Check yourself:** what goes wrong if the AI reads frames in order from a queue?

### C2. What an AI "model" is
**Simple.** A model is a very experienced spotter who learned by looking at millions of labelled photos.
You do not program the rules for "what a person looks like"; it learned them.
**Deeper.**
- A **neural network** is a huge formula with millions of adjustable numbers (**weights**).
- **Training**: show it photos with the right answers (labels), let it guess, nudge the weights to
  make the guess less wrong. Repeat millions of times. Needs a GPU and hours (Colab gives one free).
- **Inference**: using the trained model on new pictures. This is what our worker does. A CPU is enough
  for a small ("nano") model.
- The weights are saved in a file: `yolo11n-pose.pt` (6 MB).
**Check yourself:** are we training or doing inference when the worker runs?

### C3. YOLO: object detection
**Simple.** YOLO looks at a picture once and draws boxes around things it knows, with a label and how
sure it is.
**Deeper.** Each detection = box corners `x1, y1, x2, y2` in pixels + **class** (one of 80 everyday
things: 0 = person, 24 = backpack, 26 = handbag, 28 = suitcase) + **confidence** 0 to 1. We ignore
anything under 0.5. `imgsz=480` shrinks the picture first: faster, slightly less detail.
"11n" = YOLO version 11, size **n**ano (the smallest and fastest).
**In our project.** `worker/detectors.py`. A plain YOLO11n model looks for bags about once a second.
**Check yourself:** what does confidence 0.3 mean, and what do we do with it?

### C4. Tracking: giving people numbers
**Simple.** Detection says "3 people in this picture". Tracking says "this is the same person as a
moment ago: person 7".
**Deeper.** **ByteTrack** matches each new box to the boxes from the last frame (by position and
overlap) and keeps their ID. Every timer rule needs this: "person 7 has been in the zone for 3 s".
It is **not** face recognition: it does not know who anyone is, and forgets them when they leave.
**Check yourself:** what would happen to the 3-second rule without tracking?

### C5. Pose: the skeleton
**Simple.** The pose model puts 17 dots on each person: nose, shoulders, elbows, hips, knees, ankles...
**Deeper.** Each dot (**keypoint**) is `(x, y, confidence)`. From shoulders and hips we compute the
**torso angle**: 0° standing, about 90° lying flat. One pose model gives us the box, the ID and the
dots together, so we need no separate person detector.
**In our project.** `torso_angle_deg()` in `worker/detectors.py`; used by the fall rule (D2).
**Check yourself:** why is torso angle alone not enough to say "fall"? (Bending also tilts the torso.)

### C6. OpenVINO: making it fast on your laptop
**Simple.** Same spotter, faster shoes. OpenVINO is Intel's engine for running models on Intel CPUs.
**Deeper.** `yolo export ... format=openvino` converts the model once into a folder
(`models/yolo11n-pose_openvino_model/`). The worker uses it automatically if it exists. On the sandbox:
49 ms → 25 ms per frame, same results. Measure on your laptop for the "measured speed" slide.
**Try it.** `yolo export model=models/yolo11n-pose.pt format=openvino imgsz=480`
**Check yourself:** does OpenVINO change what the model detects?

### C7. Custom models: PPE and fire (your decision this week)
**Simple.** Our spotter knows "person" and "bag", but never learned "helmet", "safety vest" or "fire".
We need a spotter that did.
**Deeper.**
- **Dataset**: thousands of photos where someone drew boxes and wrote labels ("helmet", "no-helmet",
  "fire", "smoke"). Sites like Roboflow host them.
- **Train** YOLO11n on that dataset on Google Colab (free GPU), download the resulting `.pt`, export
  to OpenVINO, plug it in.
- **Licence**: the legal permission. The dataset has one, the model has one. "Public Domain" and
  "CC BY 4.0" (credit the authors) are safe for a demo. "Research only", "non-commercial" or **no
  licence stated** are not cleared. Every one goes in `docs/disclosures.md` (event rule 9).
- Options found are in `docs/STUDY.md` section 3.9.
**Check yourself:** a model on the internet has no licence. Can we use it?

---

## Part D. From detections to incidents

### D1. Zones and the feet point
**Simple.** A zone is a shape drawn on the camera picture ("nobody inside here"). A person counts as
inside if their **feet** are inside, not their head.
**Deeper.** Zone corners are stored as **fractions** (0 = left/top, 1 = right/bottom), so a zone works
at any resolution. The feet point is the bottom-centre of the person's box. To test "is this point
inside this shape" we use **ray casting**: draw an imaginary line to the right and count how many
edges it crosses; odd = inside.
**In our project.** `config/zones.yaml` → loaded into the database by `python -m app.seed`.
Kinds: `restricted` (access rule), `ppe` (helmet rule), `work` (loitering, crowding).
**Check yourself:** someone stands next to the zone and leans an arm over it. Do they trigger it?

### D2. Rules: timers, cooldown, severity
**Simple.** A rule is a stopwatch: start it when something begins, alarm when it lasts long enough,
reset it when it stops. After an alarm, stay quiet for 60 s so one event is one incident.
**Deeper.**
| Rule | Fires when | Severity |
|---|---|---|
| access | feet in a restricted zone 3 s | high; **critical** 20:00–06:00 IST |
| loitering | same person in one zone 120 s | low |
| crowding | more people in a zone than its limit | high |
| fall | torso 60°+ **and** hips dropped, for 5 s | critical |
| unattended | bag still 60 s, nobody within reach | high |
| ppe, fire | need the custom models (C7) | low, critical |
- The delay is deliberate: it filters out false alarms from one odd frame.
- **Grace period**: if the tracker loses someone for under 1 s, their stopwatch keeps going.
- **Night window** uses IST, but every time is **stored in UTC** (one world clock).
- All the numbers live in `app/config.py`, never inside the rules.
**In our project.** `worker/rules.py` (pure logic, no camera); tests in `tests/test_rules.py`.
**Check yourself:** why does a fire not create 500 incidents?

### D3. Clips: ring buffer and FFmpeg
**Simple.** A dashcam that always keeps the last 12 seconds. When something happens, we keep 5 s
before and 5 s after.
**Deeper.** Frames go into a **ring buffer** (oldest fall out as new come in), shrunk and stored as JPEG
to save memory. After an incident we wait 5 s, cut the 10 s, and **FFmpeg** turns the JPEGs into an
MP4 with H.264 video. `yuv420p` and `faststart` make browsers play it straight away.
**In our project.** `worker/buffer.py`; files land in `backend\media\<id>.mp4` and `<id>.jpg`.
**Check yourself:** how can a clip contain the 5 seconds *before* we knew anything happened?

### D4. The incident flow and speed
**Simple.** Save it, tell everyone, then finish the video.
**Deeper.** Order: insert the row (≈20 ms) → save the photo → tell the API (≈130 ms) → API pushes to
dashboards → 5 s later the clip. Every step writes a log line with its time in ms, so a slow step is
found by numbers. If the database or API is down, the worker logs it and keeps watching.
**In our project.** `worker/incidents.py`, `app/routers/internal.py`.
**Check yourself:** why tell the dashboard before the clip is ready?

---

## Part E. Users, security and live updates

### E1. Hashing PINs (bcrypt)
**Simple.** We never write the PIN down. We write a scrambled fingerprint of it that cannot be
turned back into the PIN. To check a PIN, we scramble it again and compare fingerprints.
**Deeper.** bcrypt adds random **salt** (same PIN → different fingerprint each time) and is slow on
purpose (≈70 ms): fine for one login, painful for someone trying all 10,000 PINs. Plus: 5 wrong PINs
in a minute → 429, wait a minute.
**Check yourself:** if someone steals the database, do they get the PINs?

### E2. Login tokens and sessions
**Simple.** Log in once with your PIN, get a wristband (**token**). Show the wristband with every
request instead of typing the PIN again.
**Deeper.** Our token is **opaque**: a random string with nothing written on it. The database keeps
only its fingerprint (SHA-256), who it belongs to, and when it expires (12 h). Resetting a PIN or
deactivating a user deletes their sessions: their wristband stops working at once.
(The other common kind, **JWT**, has the user written on it and is hard to cancel early.)
**Try it.** See Part G, step 6.
**Check yourself:** why store a fingerprint of the token, not the token?

### E3. Roles, actions and camera delegation
**Simple.** The role is a name badge ("Guard"). What you may actually do is on a list of keys
(**actions**), and which cameras you see is on another list.
**Deeper.** Code never checks "is this an Admin?". It checks "does this user have the `resolve`
action?" and "is this camera in their list?". Admin sees everything because they have `all_cameras`.
Every database query is filtered by the user's cameras, so even calling the API directly, a Guard
sees only his cameras. An incident on a camera you cannot see answers 404, so its existence stays hidden.
- **401**: no or bad token ("who are you?").
- **403**: valid token, missing action ("you may not").
**Demo users** (`config/demo_users.yaml`): Anita (Admin, all), Vikram (Manager), Ravi (Head Guard),
Suresh (Guard, webcam only). PINs come from `DEMO_USER_PINS` in `.env`.
**Check yourself:** how would you add a new role "Supervisor" without changing code?

### E4. Signed stream links
**Simple.** The video picture on the dashboard cannot show a wristband, so its link carries a stamped,
dated ticket instead. Change anything on the ticket and the stamp no longer matches.
**Deeper.** The link `/stream/cam-webcam?st=4.1792479041.7d3f...` holds user 4, an expiry time, and an
**HMAC** signature made with `STREAM_SECRET`. The API checks the stamp, the date, and that user 4 still
has that camera, then passes the worker's video through.
**Check yourself:** a Guard copies his link and changes `cam-webcam` to `cam-phone`. What happens? (401)

### E5. WebSocket: live updates
**Simple.** A normal request is a phone call that hangs up after one answer. A **WebSocket** is a call
that stays open, so the server can speak the moment something happens.
**Deeper.** Each dashboard connects to `/ws/events?token=...`. New incidents and every acknowledge or
resolve are pushed to all connected dashboards that may see that camera. No polling, so the target
"incident to dashboard < 2 s" is easy.
**Check yourself:** why is the token in the link here, not in a header?

### E6. Audit log and heartbeat
**Simple.** The audit log is the CCTV of the software: who did what, when, from where. The heartbeat is
each camera worker saying "I'm alive" every 5 seconds.
**Deeper.** Every action (login, acknowledge, resolve, user and zone changes) writes an audit row in the
same transaction as the change. No heartbeat for 30 s → the camera shows offline (and later, the Admin
gets a WhatsApp).
**Check yourself:** where does `/health` get each camera's FPS from?

---

## Part F. Coming next

These are not built yet. Understand them first; then the build will make sense.

### F1. Twilio phone calls
**Simple.** Twilio is a company with phone lines you control from code. You say "call this number",
and when the guard picks up, Twilio asks our server "what should I say?".
**Deeper.**
- We start a call with Twilio's API (Account SID + Auth Token, from your Twilio console).
- When answered, Twilio fetches instructions from our server, written in **TwiML** (simple XML):
  `<Say>` speaks text (English, at the start of every call), `<Gather input="speech dtmf">` waits for a
  keypress or spoken words. Keys: **1** going (acknowledge), **2** false alarm, **3** connect
  supervisor, **9** repeat.
- When the guard presses a key, Twilio sends it to our server (`/twilio/gather`). Twilio may send the
  same keypress twice, so our handler must be **idempotent**: twice = once.
- A **trial** account can only call numbers you have verified in the Twilio console.
**Check yourself:** who decides what the guard hears, Twilio or us?

### F2. Webhooks and ngrok
**Simple.** A webhook is "call me back at this number". Twilio needs to reach our server, but our laptop
is hidden inside the venue's network. **ngrok** gives it a temporary public address.
**Deeper.** `ngrok http 8000` prints a URL like `https://abc123.ngrok-free.app` that forwards to your
laptop's port 8000. That URL goes in `.env` as `PUBLIC_BASE_URL`; we give it to Twilio for each call.
Only Twilio's webhooks need this; the dashboard and workers stay local.
**Check yourself:** what breaks if ngrok stops?

### F3. WhatsApp
**Simple.** Same idea as calls, but messages: the snapshot photo and a short text go to the Admin's phone.
**Deeper.** We reuse the WhatsApp code from your Bhoomi project (URL + token in `.env`). Typical rules
of WhatsApp business APIs: messages a business starts usually need a pre-approved **template**;
free-form messages are allowed for a while after the person last wrote to you. Exactly how depends on
the provider Bhoomi uses, so we will read that code first.
**Check yourself:** why WhatsApp as well as a call? (If Twilio fails, the alert still arrives.)

### F4. The escalation ladder (timers)
**Simple.** If the first person does not answer in 60 s, call the next. Stop as soon as anyone answers.
**Deeper.** Critical: Head Guard at 0 s (+ WhatsApp to Admin) → Admin at 60 s → Manager at 2 min.
High: Head Guard → Manager at 60 s. Low: no calls, WhatsApp + daily digest at 18:00 IST.
**APScheduler** runs a job at a set time (`add_job`), and we cancel the remaining jobs when someone
acknowledges (`remove_job`). Every step goes into the incident's timeline.
**Check yourself:** the Head Guard presses 1 after 40 s. What happens to the Admin's call at 60 s?

### F5. LLMs and the assistant
**Simple.** An LLM is a text model that reads a sentence and writes a reply. We use it only to turn a
question into a **filter**, never to answer from its imagination.
**Deeper.**
- "What happened at the loading bay last night?" → the LLM returns JSON like
  `{"types": [], "cameras": ["cam-phone"], "from": "...", "to": "..."}`.
- Pydantic checks that JSON; **our code** builds the database query. The LLM never writes SQL
  (it could write something harmful or wrong).
- The answer text is built from the rows we found ("3 incidents: ..."), so it cannot invent incidents.
- **Groq** runs LLMs in the cloud, very fast (online). **Ollama** runs a small one (`llama3.2:3b`) on the
  laptop (offline fallback). If Groq takes over 5 s or the internet is off, we switch automatically.
- **Function calling** (for the laptop assistant's commands): the LLM picks which of our functions to
  run ("call_zone", "status_report") and with what arguments; our code runs it.
**Check yourself:** why can the LLM not delete incidents, even if asked?

### F6. Speech to text
**Simple.** Turns spoken words into written words, so voice questions use the same path as typed ones.
**Deeper.** **Whisper** is a speech-recognition model. Online we use Groq's hosted Whisper; offline,
**faster-whisper** ("small") on the laptop. Then the text goes through F5.
**Check yourself:** what is the target time for a spoken answer online? (About 4 s.)

### F7. RAG: answering from our documents
**Simple.** Before answering "What is the fire procedure for Block B?", look it up in our SOP documents,
then answer only from what you found.
**Deeper.** **R**etrieval-**A**ugmented **G**eneration: split documents into small chunks, turn each into
a list of numbers (**embedding**) that captures its meaning, find the chunks closest to the question
(**cosine similarity**), give only those to the LLM with the instruction "answer only from this".
First to cut if time runs short.
**Check yourself:** what should it say if the SOP does not cover the question?

---

## Part G. How to use it

Do this once on your Windows laptop, in order. Each step says what you should see.

1. **Get the code.** `git pull` on `claude/festive-clarke-6w890k`.
2. **Toolbox.** From `backend\`:
   `py -3.11 -m venv .venv ; .venv\Scripts\activate ; pip install -r requirements.txt`
3. **Database.** Install PostgreSQL 16, create a user and two databases (`safetyai`, `safetyai_test`).
4. **Secrets.** Copy `.env.example` to `.env` and fill in at least:
   `DATABASE_URL=postgresql+psycopg://<user>:<password>@127.0.0.1:5432/safetyai`,
   `INTERNAL_KEY=<any long random text>`, `STREAM_SECRET=<another long random text>`,
   `DEMO_USER_PINS=anita=1111,vikram=2222,ravi=3333,suresh=4444`.
   Make random text with: `python -c "import secrets; print(secrets.token_urlsafe(32))"`
5. **Tables and demo data.** `alembic upgrade head` then `python -m app.seed`.
   You should see `seeded 2 cameras, 4 zones and the demo users`.
6. **Start the API** (window 1): `uvicorn app.main:app --reload --port 8000`.
   Open `http://127.0.0.1:8000/health`: you should see `"db":"ok"`. Log in from PowerShell (window 2):
   ```powershell
   $r = Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8000/auth/pin -ContentType 'application/json' -Body '{"pin":"4444"}'
   $h = @{ Authorization = "Bearer $($r.token)" }
   Invoke-RestMethod -Uri http://127.0.0.1:8000/me -Headers $h
   ```
   You should see Suresh Babu, Guard, with only `cam-webcam`.
7. **Start a camera worker** (window 3): `python -m worker.run_worker --camera cam-webcam`.
   The first run downloads the model files into `models\` (about 12 MB). Every 5 s it prints FPS.
   Open `http://127.0.0.1:8101/stream` to watch boxes, IDs and zones live.
8. **Faster models (once).** Stop the worker (Ctrl+C), then
   `yolo export model=models/yolo11n-pose.pt format=openvino imgsz=480` and
   `yolo export model=models/yolo11n.pt format=openvino imgsz=480`. Start the worker again: its log now
   says `using OpenVINO model`. Compare the FPS before and after.
9. **Make an incident.** Stand so your feet are in the right-hand lower part of the picture
   (the red `server-room-door` zone) for 3 seconds. The worker prints `INCIDENT ...`, and
   `backend\media\` gets a `.jpg` and, 5 s later, a `.mp4`.
10. **See it through the API.** `Invoke-RestMethod -Uri http://127.0.0.1:8000/events -Headers $h`
11. **Run the tests.** `pytest`: all should pass.

If something fails, the message usually says what is missing. Look in Part B for the idea behind it.

---

## Part H. Glossary
| Word | Meaning |
|---|---|
| API | The set of endpoints the backend offers. |
| Async | One worker juggling many waiting tasks instead of blocking on each. |
| bcrypt | Slow, salted scrambling for passwords and PINs. |
| ByteTrack | The tracker that keeps the same ID on the same person. |
| Confidence | How sure the model is, 0 to 1. We ignore below 0.5. |
| Cooldown | Quiet time after an incident so one event is one incident (60 s). |
| Dataset | Labelled pictures used to train a model. |
| Endpoint | One "window" of the API, e.g. `GET /events`. |
| FFmpeg | The tool that turns frames into an MP4 video. |
| FPS | Frames per second. |
| HMAC | A tamper-proof stamp made with a secret key. |
| Idempotent | Doing it twice has the same effect as once. |
| Inference | Using a trained model on new data. |
| JSON | The text format of API requests and replies. |
| Keypoint | One dot of the pose skeleton (shoulder, hip...). |
| LLM | A large language model: reads and writes text. |
| Migration | A recipe that changes database tables. |
| MJPEG | Video sent as a stream of JPEG pictures; a plain `<img>` can show it. |
| ngrok | Gives your laptop a temporary public address for webhooks. |
| OpenVINO | Intel's engine to run models fast on Intel CPUs. |
| ORM | Lets Python classes stand in for database tables (SQLAlchemy). |
| Pydantic | Typed models that check request and response data. |
| RAG | Look up documents first, then answer only from them. |
| Ring buffer | Keeps only the last N seconds; oldest drops out. |
| Session | One login, stored as a hashed token with an expiry. |
| TwiML | Twilio's XML instructions for a call (`<Say>`, `<Gather>`). |
| Track ID | The number a person keeps while they stay in view. |
| UTC / IST | World time (stored) / India time = UTC + 5:30 (shown). |
| Webhook | An address another service calls to tell you something. |
| WebSocket | A connection that stays open so the server can push updates. |
| Weights | The learned numbers inside a model file. |
