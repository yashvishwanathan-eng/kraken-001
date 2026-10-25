# Disclosures

Every model, dataset, library and external API used in SafetyAI is listed here (event rule 9).

## Models
- Ultralytics YOLO11n-pose `yolo11n-pose.pt` (AGPL-3.0, Ultralytics): person boxes, track IDs and 17 body keypoints, used by the camera worker. Downloaded once into `backend/models/` (gitignored).
- Ultralytics YOLO11n `yolo11n.pt` (AGPL-3.0, Ultralytics): backpack, handbag and suitcase detection for the unattended-bag rule, about once a second.
- ByteTrack tracker (MIT), built into Ultralytics: person tracking IDs. Configured as `bytetrack.yaml`.
- OpenVINO exports of both models (`*_openvino_model/`), made locally with `yolo export`; same weights, same licence.
- Llama 3.2 3B (`llama3.2:3b`, Meta, Llama 3.2 Community License), run locally by Ollama: offline fallback for the assistant.
- Whisper "small" (OpenAI, MIT) in the faster-whisper format (`Systran/faster-whisper-small`, MIT), downloaded on first offline use: offline speech to text.
- Groq-hosted models (chosen in `.env`: `GROQ_MODEL` for questions/commands, `GROQ_STT_MODEL` for speech): fill in the exact model names and their licences once chosen.
- PPE and fire/smoke models: not chosen yet. Add the model and its training dataset, each with its licence, when chosen.

## Test data
- `bus.jpg` sample image shipped inside the Ultralytics package (AGPL-3.0): converted to a short local test video to check detection. The video is not committed.
- `backend/knowledge/SAMPLE-sop.md`: an invented sample SOP written for this project, used only to demonstrate SOP answers. Replace with the site's real SOPs.

## Libraries (backend, in use)
- FastAPI (MIT): HTTP API framework.
- Uvicorn (BSD-3-Clause): ASGI server.
- Pydantic / pydantic-settings (MIT): typed models and `.env` settings.
- SQLAlchemy 2 (MIT): database ORM.
- Alembic (MIT): database migrations.
- psycopg 3 (LGPL-3.0): PostgreSQL driver.
- PyYAML (MIT): reads `config/cameras.yaml` and `config/zones.yaml`.
- bcrypt (Apache-2.0): PIN hashing.
- websockets (BSD-3-Clause): lets Uvicorn serve `/ws/events`.
- OpenVINO (Apache-2.0, Intel): faster model inference on Intel CPUs.
- httpx (BSD-3-Clause): HTTP client.
- Ultralytics (AGPL-3.0): YOLO inference and tracking.
- lap (BSD-2-Clause): linear assignment solver used by the tracker.
- OpenCV (Apache-2.0), installed by Ultralytics: camera capture and frame handling.
- APScheduler (MIT): call-ladder timers, the 18:00 digest and the camera heartbeat check.
- python-multipart (Apache-2.0): reads form posts (Twilio webhooks) and file uploads (broadcast audio, voice questions).
- faster-whisper (MIT) with CTranslate2 (MIT): offline speech to text.
- tzdata (Apache-2.0): time zone data so IST works on Windows.
- pytest (MIT): tests.

## Email and QR codes (in use, P19)
- qrcode 8.2 (BSD licence): draws the invitation QR code as a PNG on the laptop.
- SMTP email (the provider is chosen by Kirito: a Gmail app password, Outlook, or a school mail server): sends the invitation link. Only the invitee's address and the link leave the laptop.

## External services and databases (in use)
- PostgreSQL 16 (PostgreSQL licence), local on the event laptop.
- FFmpeg (LGPL-2.1+/GPL depending on build; we call the `ffmpeg` command, libx264 is GPL-2.0): re-encodes incident clips to H.264 MP4.

- Twilio Programmable Voice (cloud API, Twilio's terms of service): alert, roll-call and broadcast phone calls. Only short call instructions and phone numbers leave the laptop; never video.
- WhatsApp Business API (Meta WhatsApp Cloud API format, to be replaced by the provider used in Kirito's Bhoomi project): alert text and a 24-hour snapshot link.
- Groq API (cloud): turns typed/spoken questions into a filter, picks commands, answers SOP questions from our documents, and speech to text. Only the question text (or voice clip) is sent.
- Ollama (MIT): runs the offline language model on the laptop.
- IP Webcam by Pavel Khlebovich (free Android app, proprietary): turns the two demo phones into network cameras. Video goes only over our own hotspot to the event laptop.
- ngrok (service): gives the laptop a public address so Twilio can reach the webhooks; only `/twilio/*` and `/share/*` answer through it.
