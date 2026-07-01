# D435i → PC 이미지 캡처 파이프라인

라즈베리파이에 USB로 연결된 Intel RealSense **D435i** 카메라로 정렬된(aligned)
**RGB + Depth** 쌍을 일정 간격으로 자동 캡처하고, **Tailscale** 네트워크를 통해
**PC(Windows)** 로 전송·저장한다. PC 브라우저의 웹 UI로 제어·프리뷰·갤러리를 본다.

```
라즈베리파이(pi_agent)  ──Tailscale──►  PC(pc_server)
  D435i 상시 스트림                        웹 UI 호스팅 + 업로드 수신·저장
  간격 캡처 → POST 업로드                   갤러리 API
        ▲ 제어/프리뷰                       ▲ UI/갤러리
        └──────── PC 브라우저 ──────────────┘
```

## 구성
- `pi_agent/` — 라즈베리파이에서 실행 (FastAPI, 포트 **8000**)
- `pc_server/` — PC에서 실행 (FastAPI + 웹 UI, 포트 **9000**)

---

## 0. Tailscale 설정 (양쪽 기기)
1. 두 기기 모두 [tailscale.com](https://tailscale.com) 계정으로 로그인.
   - PC(Windows): Tailscale 설치 후 로그인.
   - 파이: `curl -fsSL https://tailscale.com/install.sh | sh` 후 `sudo tailscale up`.
2. 각 기기의 Tailscale IP 확인: `tailscale ip -4` (보통 `100.x.x.x`).
   - 예) 파이 = `100.101.1.1`, PC = `100.101.2.2`
3. 이후 UI에서 이 IP를 사용한다.

---

## 1. 라즈베리파이 (pi_agent)

### 설치
```bash
cd pi_agent
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

> **pyrealsense2 참고**: 라즈베리파이(ARM)에서는 pip 휠이 없을 수 있다.
> 그 경우 [librealsense](https://github.com/IntelRealSense/librealsense)를
> 소스 빌드하거나 `pyrealsense2` ARM 빌드를 설치해야 한다. 카메라 인식 확인:
> ```bash
> rs-enumerate-devices   # librealsense 설치 시 제공
> ```

### 실행
```bash
python agent.py          # 0.0.0.0:8000 에서 대기
```
부팅 시 자동 실행하려면 `systemd` 서비스로 등록하면 된다(아래 부록).

---

## 2. PC (pc_server) — Windows

### 설치 (PowerShell)
```powershell
cd pc_server
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

### 실행
```powershell
python server.py         # 0.0.0.0:9000 에서 대기
```
- 저장 위치: `pc_server/captures/YYYY-MM-DD/` (환경변수 `CAPTURE_DIR`로 변경 가능)
- Windows 방화벽에서 9000 포트 인바운드 허용이 필요할 수 있다(Tailscale 인터페이스).

---

## 3. 사용법
1. PC 브라우저에서 **http://localhost:9000** 접속.
2. 좌측 **연결 설정**:
   - **파이 에이전트 주소**: `http://<파이 Tailscale IP>:8000` (예 `http://100.101.1.1:8000`)
   - **PC 업로드 주소**: `http://<PC Tailscale IP>:9000/upload` (예 `http://100.101.2.2:9000/upload`)
     - ⚠️ `localhost`가 아니라 **PC의 Tailscale IP**여야 한다. 파이가 이 주소로 POST하기 때문.
   - **캡처 간격(초)** 설정.
3. **자동 캡처 시작** → 간격마다 RGB+Depth가 PC로 전송되어 갤러리에 표시됨.
   - **지금 한 장**: 간격과 무관하게 즉시 1장.
   - **정지**: 자동 캡처 중단(카메라 스트림/프리뷰는 유지).

---

## 저장 파일 형식
`pc_server/captures/<날짜>/` 에 stamp(`YYYYMMDD_HHMMSS_mmm`) 기준으로 저장:
- `color_<stamp>.png` — RGB 컬러 (BGR, 8bit)
- `depth_<stamp>.png` — Depth **16bit** 단일채널 (color 기준 정렬, z16 원본)
- `depthvis_<stamp>.jpg` — 갤러리용 depth 컬러맵 썸네일

Depth 실제 거리(m) = `픽셀값 × depth_scale`. depth_scale은 업로드 시 함께 전송된다
(D435i 기본 약 0.001 m/unit).

---

## API 요약
### 파이 (`:8000`)
| 메서드 | 경로 | 설명 |
|---|---|---|
| GET | `/status` | 상태 조회 |
| POST | `/config` | `{interval, upload_url}` 설정 |
| POST | `/start` | 자동 캡처 시작 |
| POST | `/stop` | 정지 |
| POST | `/capture_now` | 즉시 1장 |
| GET | `/preview.jpg` | 최신 프레임 1장 |
| GET | `/stream.mjpg` | MJPEG 라이브 스트림 |

### PC (`:9000`)
| 메서드 | 경로 | 설명 |
|---|---|---|
| POST | `/upload` | color/depth 멀티파트 수신·저장 |
| GET | `/api/captures?limit=N` | 최근 캡처 목록 |
| GET | `/files/<경로>` | 저장 파일 서빙 |
| GET | `/` | 웹 UI |

---

## 부록: 파이 자동 실행 (systemd)
`/etc/systemd/system/d435-agent.service`:
```ini
[Unit]
Description=D435i Pi Agent
After=network-online.target

[Service]
User=pi
WorkingDirectory=/home/pi/g/pi_agent
ExecStart=/home/pi/g/pi_agent/.venv/bin/python agent.py
Restart=always

[Install]
WantedBy=multi-user.target
```
```bash
sudo systemctl enable --now d435-agent
```
