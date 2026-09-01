# Load test results

Measured with `backend/tests/load/run_load_test.py` against the in-process ASGI app.
Machine: **12 cores / 15.3 GB RAM**, 11 filter workers (thread pool), job concurrency 6.

| Run | Users | Photos/user | Total photos | Wall | Throughput | /health p99 | Peak RSS | Failed |
|---|---|---|---|---|---|---|---|---|
| smoke | 4 | 60 | 240 | 21.5s | 11.2/s | <2ms (max 4.0) | 0.44 GB | 0/4 |
| volume | 8 | 125 | 1,000 | 96.4s | 10.4/s | 1.4ms (max 24.4) | 0.76 GB | 0/8 |
| **concurrency** | **20** | **150** | **3,000** | **541s** | **5.5/s** | **1.8ms (max 11.6)** | **0.79 GB** | **0/20** |

All three runs pass the gate: no failed sessions, `/health` p99 well under 200 ms, every
user receives 3 variations with 7 distinct cover photos.

## The capacity number

**On this hardware, 20 concurrent users × 1,000 photos each ≈ 61 minutes** (20,000 photos
at 5.5 photos/sec).

That is an extrapolation on photo count, not a measured run. It is justified because
throughput held nearly flat as photo count grew 4× (11.2 → 10.4 photos/sec from 240 to
1,000 photos), so per-photo cost is linear. Concurrency was measured directly at the
target of 20.

## What the numbers say

**Throughput halves as concurrency doubles past the core count.** 10.4 photos/sec at 8
users, 5.5 at 20. The pipeline is CPU-bound in Phase 1 filtering (~0.3 s of real CPU per
photo: PIL decode, OpenCV sharpness, dual pHash, face detection). With 11 workers on 12
cores, 20 simultaneous sessions simply queue. This is saturation, not a defect — but it
means throughput is bounded by cores, and more users will not go faster.

**The event loop never stalls.** `/health` p99 stayed under 2 ms in every run, including
20 concurrent sessions and 1,800 samples. Before Stage 1.4 the photo load, theme engine
and layout solver all ran directly on the loop.

**Memory and disk are not the constraint.** Peak RSS 0.79 GB against 15.3 GB available;
3,000 thumbnails occupied 0.13 GB, so 20,000 would be ~0.9 GB. Originals are excluded
here — the load harness does not upload them, and the Stage 1.3 disk guards
(3 GB/session, 60 GB global) govern that path.

## Threads vs processes — settled

| Pool | Throughput (4 users × 60) |
|---|---|
| thread | **11.2 photos/sec** |
| process | 8.9 photos/sec |

**Threads win.** Per-worker model loading (11 × ~1.5 s of MediaPipe/ONNX init) plus IPC
pickling of image payloads outweighs the GIL relief, because the heavy stages
(OpenCV, NumPy) already release the GIL. `PIXOVO_POOL=process` remains available; the
default is `thread`.

Getting the process pool to run at all required module-level worker entry points
(`scan_photo`, `finalise_scanned_batch`). Submitting a bound method pickles the engine
instance with its ctypes handles and fails with
`Can't pickle local object 'CDLL.__init__.<locals>._FuncPtr'`.

## Defect found by this stage

**The filter engine's face detector is not thread-safe.** Sharing one `Phase1FilterEngine`
across the pool crashed the interpreter outright — exit 127, no Python traceback — as soon
as ~8 concurrent sessions were filtering. Four concurrent users masked it entirely.

`Phase1FilterEngine` wraps MediaPipe/TFLite and ONNX Runtime sessions, which carry native
graph state and cannot be called concurrently. A lock around detection would have
serialised the most expensive stage, so each pool thread now builds its own engine
(`threading.local()` in `filter_engine._worker_engine`). Cost: ~0.3 GB extra RSS for 11
engines. Under a process pool it degrades naturally to one engine per process.

This hazard predated Stage 1.7 — `main.py` always shared a single module-level
`filter_engine` — but it had never been exercised above 4 concurrent sessions.

## Reproducing

```bash
cd backend
python -m tests.load.run_load_test --quick                          # 4 x 60,  ~25s
python -m tests.load.run_load_test --users 8  --photos 125           # 1,000,   ~2min
python -m tests.load.run_load_test --users 20 --photos 150           # 3,000,   ~9min
python -m tests.load.run_load_test --users 20 --photos 1000          # the full gate, ~1h
PIXOVO_POOL=process python -m tests.load.run_load_test --quick       # pool comparison
```

The harness redirects the database and uploads tree to a temp directory, so it never
touches dev data.
