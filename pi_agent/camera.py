"""RealSense D435i camera worker.

파이프라인을 백그라운드 스레드에서 상시 구동하면서 정렬된(aligned)
RGB + Depth 최신 프레임을 메모리에 보관한다. 라이브 프리뷰와 간격 캡처가
같은 스트림을 공유하므로 USB 대역폭/카메라 점유 충돌이 없다.
"""

import threading
import time

import cv2
import numpy as np
import pyrealsense2 as rs


class CameraWorker:
    def __init__(self, width: int = 640, height: int = 480, fps: int = 30):
        self.width = width
        self.height = height
        self.fps = fps

        self._pipeline = None
        self._align = None
        self._thread = None
        self._running = False
        self._lock = threading.Lock()

        # 최신 프레임 캐시 (lock 보호)
        self._latest_color = None      # np.ndarray (H,W,3) BGR uint8
        self._latest_depth = None      # np.ndarray (H,W)   uint16 (color 기준 정렬됨)
        self._latest_jpeg = None       # bytes, 프리뷰용
        self._latest_ts = 0.0
        self._depth_scale = 0.001      # meter/unit, start 후 갱신

        self.error = None              # 마지막 에러 메시지

    # ---- lifecycle ---------------------------------------------------
    def start(self):
        if self._running:
            return
        self._pipeline = rs.pipeline()
        config = rs.config()
        config.enable_stream(rs.stream.depth, self.width, self.height,
                             rs.format.z16, self.fps)
        config.enable_stream(rs.stream.color, self.width, self.height,
                             rs.format.bgr8, self.fps)
        profile = self._pipeline.start(config)

        depth_sensor = profile.get_device().first_depth_sensor()
        self._depth_scale = float(depth_sensor.get_depth_scale())

        # depth 를 color 프레임 좌표계로 정렬
        self._align = rs.align(rs.stream.color)

        self._running = True
        self.error = None
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self):
        self._running = False
        if self._thread:
            self._thread.join(timeout=2.0)
        if self._pipeline:
            try:
                self._pipeline.stop()
            except Exception:
                pass
        self._pipeline = None

    # ---- capture loop ------------------------------------------------
    def _loop(self):
        while self._running:
            try:
                frames = self._pipeline.wait_for_frames(timeout_ms=5000)
                aligned = self._align.process(frames)
                depth_frame = aligned.get_depth_frame()
                color_frame = aligned.get_color_frame()
                if not depth_frame or not color_frame:
                    continue

                color = np.asanyarray(color_frame.get_data())
                depth = np.asanyarray(depth_frame.get_data())

                ok, jpeg = cv2.imencode(".jpg", color,
                                        [cv2.IMWRITE_JPEG_QUALITY, 80])
                with self._lock:
                    self._latest_color = color
                    self._latest_depth = depth
                    if ok:
                        self._latest_jpeg = jpeg.tobytes()
                    self._latest_ts = time.time()
            except Exception as e:  # noqa: BLE001
                self.error = str(e)
                time.sleep(0.5)

    # ---- accessors ---------------------------------------------------
    @property
    def running(self) -> bool:
        return self._running

    def get_preview_jpeg(self):
        with self._lock:
            return self._latest_jpeg

    def get_aligned_pair(self):
        """저장/업로드용 최신 (color BGR, depth uint16) 복사본을 반환."""
        with self._lock:
            if self._latest_color is None or self._latest_depth is None:
                return None, None, 0.0
            return (self._latest_color.copy(),
                    self._latest_depth.copy(),
                    self._latest_ts)

    def depth_colormap(self, depth):
        """Depth uint16 → 시각화용 컬러맵 (프리뷰/썸네일)."""
        vis = cv2.convertScaleAbs(depth, alpha=0.03)
        return cv2.applyColorMap(vis, cv2.COLORMAP_JET)
