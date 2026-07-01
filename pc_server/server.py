"""PC(Windows) 측 서버 (FastAPI).

역할:
  - 웹 UI 호스팅 (static/index.html)
  - 파이에서 올라오는 aligned RGB+Depth 업로드 수신 및 디스크 저장
  - 저장된 캡처 갤러리 조회 API + 파일 서빙

브라우저는 이 서버(UI/갤러리)와 파이 에이전트(제어/프리뷰)를 각각 직접 호출한다.
"""

import os
from datetime import datetime
from pathlib import Path

import cv2
import numpy as np
from fastapi import FastAPI, File, Form, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

BASE_DIR = Path(__file__).resolve().parent
CAPTURE_DIR = Path(os.environ.get("CAPTURE_DIR", BASE_DIR / "captures"))
CAPTURE_DIR.mkdir(parents=True, exist_ok=True)

app = FastAPI(title="D435i PC Server")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


def _day_dir() -> Path:
    d = CAPTURE_DIR / datetime.now().strftime("%Y-%m-%d")
    d.mkdir(parents=True, exist_ok=True)
    return d


@app.post("/upload")
async def upload(
    color: UploadFile = File(...),
    depth: UploadFile = File(...),
    stamp: str = Form(...),
    depth_scale: str = Form("0.001"),
):
    day = _day_dir()
    color_bytes = await color.read()
    depth_bytes = await depth.read()

    color_path = day / f"color_{stamp}.png"
    depth_path = day / f"depth_{stamp}.png"
    color_path.write_bytes(color_bytes)
    depth_path.write_bytes(depth_bytes)

    # 갤러리용 depth 컬러맵 썸네일 생성
    thumb_path = day / f"depthvis_{stamp}.jpg"
    try:
        depth_img = cv2.imdecode(np.frombuffer(depth_bytes, np.uint8),
                                 cv2.IMREAD_UNCHANGED)
        if depth_img is not None:
            vis = cv2.convertScaleAbs(depth_img, alpha=0.03)
            vis = cv2.applyColorMap(vis, cv2.COLORMAP_JET)
            cv2.imwrite(str(thumb_path), vis)
    except Exception:  # noqa: BLE001
        thumb_path = None

    return {
        "ok": True,
        "stamp": stamp,
        "depth_scale": depth_scale,
        "color": color_path.relative_to(CAPTURE_DIR).as_posix(),
        "depth": depth_path.relative_to(CAPTURE_DIR).as_posix(),
    }


@app.get("/api/captures")
def list_captures(limit: int = 60):
    """최근 캡처 목록 (stamp 기준 내림차순)."""
    items = {}
    for p in CAPTURE_DIR.rglob("color_*.png"):
        stamp = p.stem[len("color_"):]
        rel = p.parent.relative_to(CAPTURE_DIR).as_posix()
        depthvis = p.parent / f"depthvis_{stamp}.jpg"
        items[stamp] = {
            "stamp": stamp,
            "color": f"{rel}/color_{stamp}.png",
            "depth": f"{rel}/depth_{stamp}.png",
            "depthvis": (f"{rel}/depthvis_{stamp}.jpg"
                         if depthvis.exists() else None),
            "mtime": p.stat().st_mtime,
        }
    result = sorted(items.values(), key=lambda x: x["stamp"], reverse=True)
    return {"count": len(result), "items": result[:limit]}


@app.get("/files/{path:path}")
def get_file(path: str):
    target = (CAPTURE_DIR / path).resolve()
    # 디렉터리 탈출 방지
    if not str(target).startswith(str(CAPTURE_DIR.resolve())):
        return JSONResponse(status_code=403, content={"error": "forbidden"})
    if not target.exists():
        return JSONResponse(status_code=404, content={"error": "not found"})
    return FileResponse(target)


# 웹 UI (마지막에 마운트: API 경로가 우선하도록)
app.mount("/", StaticFiles(directory=BASE_DIR / "static", html=True),
          name="static")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=9000)
