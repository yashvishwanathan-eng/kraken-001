"""Annotated MJPEG server for one camera worker, bound to 127.0.0.1 only.

The API's `GET /stream/{camera_id}` proxies this stream. A browser `<img>` tag can show it
directly because it is `multipart/x-mixed-replace`: each JPEG replaces the one before.
"""

import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

BOUNDARY = b"frame"


class MjpegServer:
    def __init__(self, port: int) -> None:
        self.port = port
        self._cond = threading.Condition()
        self._jpeg: bytes | None = None
        self._frame_no = 0
        self.viewers = 0  # open /stream connections; the live view only draws and encodes while someone watches
        self._httpd = ThreadingHTTPServer(("127.0.0.1", port), self._handler())

    def start(self) -> "MjpegServer":
        threading.Thread(target=self._httpd.serve_forever, name="mjpeg", daemon=True).start()
        return self

    def stop(self) -> None:
        self._httpd.shutdown()
        self._httpd.server_close()

    def publish(self, jpeg: bytes) -> None:
        with self._cond:
            self._jpeg = jpeg
            self._frame_no += 1
            self._cond.notify_all()

    def _handler(self):
        server = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802 (http.server method name)
                if self.path != "/stream":
                    self.send_error(404)
                    return
                self.send_response(200)
                self.send_header("Content-Type", f"multipart/x-mixed-replace; boundary={BOUNDARY.decode()}")
                self.send_header("Cache-Control", "no-cache")
                self.end_headers()
                last_sent = -1
                with server._cond:
                    server.viewers += 1
                try:
                    while True:
                        with server._cond:
                            server._cond.wait_for(lambda: server._frame_no != last_sent, timeout=5)
                            jpeg, last_sent = server._jpeg, server._frame_no
                        if jpeg is None:
                            continue
                        self.wfile.write(
                            b"--" + BOUNDARY + b"\r\n"
                            b"Content-Type: image/jpeg\r\n"
                            + f"Content-Length: {len(jpeg)}\r\n\r\n".encode()
                            + jpeg + b"\r\n"
                        )
                        self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError):
                    pass  # viewer closed the stream
                finally:
                    with server._cond:
                        server.viewers -= 1

            def log_message(self, format, *args) -> None:  # keep the console quiet
                pass

        return Handler
