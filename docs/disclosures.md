# Disclosures

Every model, dataset, library and external API used in SafetyAI is listed here (event rule 9).

## Models
- Ultralytics YOLO11n-pose `yolo11n-pose.pt` (AGPL-3.0, Ultralytics): person boxes, track IDs and 17 body keypoints, used by the camera worker. Downloaded once into `backend/models/` (gitignored).
- Ultralytics YOLO11n `yolo11n.pt` (AGPL-3.0, Ultralytics): backpack, handbag and suitcase detection for the unattended-bag rule, about once a second.
- ByteTrack tracker (MIT), built into Ultralytics: person tracking IDs. Configured as `bytetrack.yaml`.
- OpenVINO exports of both models (`*_openvino_model/`), made locally with `yolo export`; same weights, same licence.

## Test data
- `bus.jpg` sample image shipped inside the Ultralytics package (AGPL-3.0): converted to a short local test video to check detection. The video is not committed.

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
- APScheduler (MIT): background scheduling (declared; not yet used).
- tzdata (Apache-2.0): time zone data so IST works on Windows.
- pytest (MIT): tests.

## External services and databases (in use)
- PostgreSQL 16 (PostgreSQL licence), local on the event laptop.
- FFmpeg (LGPL-2.1+/GPL depending on build; we call the `ffmpeg` command, libx264 is GPL-2.0): re-encodes incident clips to H.264 MP4.

## Planned (not yet used, add when integrated)
- Twilio voice and WhatsApp API
- Groq API (LLM and Whisper)
- Ollama `llama3.2:3b`, faster-whisper "small"
