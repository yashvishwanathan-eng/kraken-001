"""Share this laptop's webcam on the Wi-Fi, like the IP Webcam app does on a phone.

Runs on the SECOND laptop (the one used as a camera). It needs only Python and OpenCV:
    pip install opencv-python
    python camera_share.py                 # webcam 0, port 8090
    python camera_share.py --source 1      # a second (USB) webcam
Then on the main laptop, cameras.yaml uses  source: "http://LAPTOP2-IP:8090/video"

The stream is MJPEG: a never-ending HTTP answer of JPEG photos, each replacing the one before.
No AI runs here; the main laptop does all the detection.

Anyone on the same Wi-Fi can open the stream, so use your own hotspot, not open venue Wi-Fi.
"""

import argparse
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import cv2

BOUNDARY = "frame"


class LatestJpeg:
    """One slot holding the newest JPEG; viewers wait for the next one instead of queueing old frames."""

    def __init__(self) -> None:
        self._cond = threading.Condition()
        self._jpeg: bytes | None = None
        self._number = 0

    def put(self, jpeg: bytes) -> None:
        with self._cond:
            self._jpeg, self._number = jpeg, self._number + 1
            self._cond.notify_all()

    def wait_newer(self, seen: int, timeout: float = 5.0) -> tuple[bytes | None, int]:
        with self._cond:
            self._cond.wait_for(lambda: self._number != seen, timeout=timeout)
            return self._jpeg, self._number


def capture_loop(source: str, latest: LatestJpeg, width: int, fps: float, quality: int) -> None:
    """Read the webcam forever; reopen it every 5 s if it is lost (unplugged, used by another app)."""
    device = int(source) if source.isdigit() else source
    is_file = not source.isdigit()  # a file would otherwise be read as fast as the CPU allows
    gap = 1.0 / fps
    while True:
        cap = cv2.VideoCapture(device)
        if not cap.isOpened():
            print(f"camera {source} not available, retrying in 5 s")
            time.sleep(5)
            continue
        print(f"camera {source} open")
        next_at = time.monotonic()
        while True:
            ok, frame = cap.read()
            if not ok:
                print("camera stopped sending frames, reopening")
                break
            now = time.monotonic()
            if now < next_at:  # keep to the chosen frame rate
                if not is_file:
                    continue  # a webcam keeps running: skip the spare frame
                time.sleep(next_at - now)  # a file waits: play it at normal speed
                now = next_at
            next_at = now + gap
            h, w = frame.shape[:2]
            if w > width:
                frame = cv2.resize(frame, (width, int(h * width / w)))
            ok, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, quality])
            if ok:
                latest.put(buf.tobytes())
        cap.release()
        time.sleep(5)


def make_handler(latest: LatestJpeg):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802 (http.server method name)
            if self.path.split("?")[0] != "/video":
                self.send_error(404, "the stream is at /video")
                return
            self.send_response(200)
            self.send_header("Content-Type", f"multipart/x-mixed-replace; boundary={BOUNDARY}")
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            seen = -1
            try:
                while True:
                    jpeg, seen = latest.wait_newer(seen)
                    if jpeg is None:
                        continue
                    self.wfile.write(f"--{BOUNDARY}\r\nContent-Type: image/jpeg\r\n"
                                     f"Content-Length: {len(jpeg)}\r\n\r\n".encode() + jpeg + b"\r\n")
            except (BrokenPipeError, ConnectionResetError):
                pass  # the viewer went away

        def log_message(self, fmt: str, *args) -> None:
            print(f"{self.client_address[0]} {fmt % args}")

    return Handler


def local_ip() -> str:
    """The address other devices on the Wi-Fi use to reach this laptop (no data is sent)."""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        try:
            s.connect(("10.255.255.255", 1))
            return s.getsockname()[0]
        except OSError:
            return "127.0.0.1"


def main() -> None:
    parser = argparse.ArgumentParser(description="Share a webcam on the Wi-Fi as an MJPEG stream.")
    parser.add_argument("--source", default="0", help="webcam number (0, 1, ...) or a video file path")
    parser.add_argument("--port", type=int, default=8090)
    parser.add_argument("--width", type=int, default=640, help="frames wider than this are shrunk")
    parser.add_argument("--fps", type=float, default=25.0, help="the worker analyses every 3rd frame: 25 gives about 8")
    parser.add_argument("--quality", type=int, default=80, help="JPEG quality 1-100")
    args = parser.parse_args()

    latest = LatestJpeg()
    threading.Thread(target=capture_loop, args=(args.source, latest, args.width, args.fps, args.quality),
                     daemon=True).start()
    server = ThreadingHTTPServer(("0.0.0.0", args.port), make_handler(latest))
    print(f"sharing camera {args.source} at  http://{local_ip()}:{args.port}/video   (Ctrl+C to stop)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("stopped")


if __name__ == "__main__":
    main()
