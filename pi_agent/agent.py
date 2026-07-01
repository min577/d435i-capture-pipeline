"""라즈베리파이에서 실행하는 카메라 에이전트 (FastAPI).

역할:
  - D435i 파이프라인 상시 구동 (camera.CameraWorker)
  - 라이브 프리뷰 제공 (/preview.jpg, /stream.mjpg)
  - 간격 캡처 루프 제어 (/start, /stop, /config, /status)
  - 캡처 시 aligned RGB+Depth 를 PC 로 POST 업로드 (Tailscale)

PC 브라우저에서 직접 호출하므로 CORS 를 전면 허용한다.
"""

import io
import threading
import time

import cv2
import requests
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response, StreamingResponse
from pydantic import BaseModel

from camera import CameraWorker

app = FastAPI(title="D435i Pi Agent")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

cam = CameraWorker()


class CaptureState:
    def __init__(self):
        self.enabled = False
        self.interval = 5.0            # 초
        self.upload_url = ""           # 예: http://100.y.y.y:9000/upload
        self.count = 0
        self.last_capture_ts = 0.0
        self.last_upload_ok = None     # True/False/None
        self.last_error = ""
        self._thread = None
        self._stop = threading.Event()

    def start_loop(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self):
        while not self._stop.is_set():
            if self.enabled and self.upload_url:
                self._capture_once()
            # interval 동안 잘게 나눠 대기 (stop 반응성)
            waited = 0.0
            step = 0.2
            while waited < self.interval and not self._stop.is_set():
                time.sleep(step)
                waited += step

    def _capture_once(self):
        color, depth, ts = cam.get_aligned_pair()
        if color is None:
            self.last_error = "no frame available"
            self.last_upload_ok = False
            return
        stamp = time.strftime("%Y%m%d_%H%M%S", time.localtime(ts)) + \
            f"_{int((ts % 1) * 1000):03d}"

        ok_c, color_png = cv2.imencode(".png", color)
        # depth: 16-bit 단일 채널 PNG (원본 z16 보존)
        ok_d, depth_png = cv2.imencode(".png", depth)
        if not (ok_c and ok_d):
            self.last_error = "encode failed"
            self.last_upload_ok = False
            return

        files = {
            "color": (f"color_{stamp}.png", color_png.tobytes(), "image/png"),
            "depth": (f"depth_{stamp}.png", depth_png.tobytes(), "image/png"),
        }
        data = {
            "stamp": stamp,
            "depth_scale": str(cam._depth_scale),
        }
        try:
            r = requests.post(self.upload_url, files=files, data=data,
                              timeout=10)
            r.raise_for_status()
            self.count += 1
            self.last_capture_ts = ts
            self.last_upload_ok = True
            self.last_error = ""
        except Exception as e:  # noqa: BLE001
            self.last_upload_ok = False
            self.last_error = str(e)


state = CaptureState()


# ---- models ----------------------------------------------------------
class ConfigIn(BaseModel):
    interval: float | None = None
    upload_url: str | None = None


# ---- lifecycle -------------------------------------------------------
@app.on_event("startup")
def _startup():
    try:
        cam.start()
    except Exception as e:  # noqa: BLE001
        cam.error = str(e)
    state.start_loop()


@app.on_event("shutdown")
def _shutdown():
    state.enabled = False
    state._stop.set()
    cam.stop()


# ---- control ---------------------------------------------------------
@app.get("/status")
def status():
    return {
        "camera_running": cam.running,
        "camera_error": cam.error,
        "capture_enabled": state.enabled,
        "interval": state.interval,
        "upload_url": state.upload_url,
        "count": state.count,
        "last_capture_ts": state.last_capture_ts,
        "last_upload_ok": state.last_upload_ok,
        "last_error": state.last_error,
    }


@app.post("/config")
def config(cfg: ConfigIn):
    if cfg.interval is not None:
        state.interval = max(0.5, float(cfg.interval))
    if cfg.upload_url is not None:
        state.upload_url = cfg.upload_url.strip()
    return status()


@app.post("/start")
def start(cfg: ConfigIn | None = None):
    if cfg:
        if cfg.interval is not None:
            state.interval = max(0.5, float(cfg.interval))
        if cfg.upload_url is not None:
            state.upload_url = cfg.upload_url.strip()
    if not state.upload_url:
        return JSONResponse(status_code=400,
                            content={"error": "upload_url 이 설정되지 않았습니다"})
    state.enabled = True
    return status()


@app.post("/stop")
def stop():
    state.enabled = False
    return status()


@app.post("/capture_now")
def capture_now():
    """간격과 무관하게 즉시 한 장 캡처·업로드."""
    if not state.upload_url:
        return JSONResponse(status_code=400,
                            content={"error": "upload_url 미설정"})
    state._capture_once()
    return status()


# ---- preview ---------------------------------------------------------
@app.get("/preview.jpg")
def preview():
    jpeg = cam.get_preview_jpeg()
    if jpeg is None:
        return JSONResponse(status_code=503,
                            content={"error": "no frame yet"})
    return Response(content=jpeg, media_type="image/jpeg")


@app.get("/stream.mjpg")
def stream():
    def gen():
        boundary = b"--frame"
        while True:
            jpeg = cam.get_preview_jpeg()
            if jpeg is not None:
                yield (boundary + b"\r\n"
                       b"Content-Type: image/jpeg\r\n"
                       b"Content-Length: " + str(len(jpeg)).encode() +
                       b"\r\n\r\n" + jpeg + b"\r\n")
            time.sleep(0.05)

    return StreamingResponse(
        gen(), media_type="multipart/x-mixed-replace; boundary=frame")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
