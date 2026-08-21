# Pixovo v3.9 — Developer Quickstart Guide

This guide explains how to install, configure, and boot the clean Pixovo system from Base 0.

---

## 📋 Prerequisites

* **Python:** 3.10 or 3.11+
* **Node.js:** 18.0+ / npm 9.0+

---

## 🚀 1. Backend Setup & Run

1. Open a terminal and navigate to `backend/`:
   ```bash
   cd backend
   ```

2. Create and activate a Python virtual environment:
   * **Windows (PowerShell):**
     ```powershell
     python -m venv venv
     .\venv\Scripts\Activate.ps1
     ```
   * **macOS / Linux:**
     ```bash
     python3 -m venv venv
     source venv/bin/activate
     ```

3. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```

4. *(Optional)* Configure Environment Variables:
   Create a `.env` file in the `backend/` directory:
   ```env
   GEMINI_API_KEY="your_optional_gemini_api_key"
   PORT=8000
   ```
   *(Note: If `GEMINI_API_KEY` is not present, Pixovo will run seamlessly in offline/rule-based fallback mode).*

5. Start the Backend API Server:
   ```bash
   python run.py
   # Or using uvicorn directly:
   uvicorn app.main:app --reload --port 8000
   ```
   * The API server will start on **`http://localhost:8000`**.
   * Interactive OpenAPI documentation: **`http://localhost:8000/docs`**.

---

## 🎨 2. Frontend Setup & Run

1. Open a new terminal and navigate to `frontend/`:
   ```bash
   cd frontend
   ```

2. Install dependencies:
   ```bash
   npm install
   ```

3. Start the Vite development server:
   ```bash
   npm run dev
   ```
   * The frontend app will be available on **`http://localhost:5173`**.
   * The Vite server is configured with reverse proxy to automatically forward `/api`, `/uploads`, and `/exports` requests to `http://localhost:8000`.

---

## 📡 3. Key API Endpoints Reference

| HTTP Method | Endpoint | Description |
| :--- | :--- | :--- |
| `GET` | `/` | Service health status & persisted photo/job counts |
| `GET` | `/api/stats` | Real-time diagnostic telemetry, stage latencies & SQLite storage |
| `POST` | `/api/photobook/ingest` | Dual-payload ingestion (512px previews + metadata JSON) |
| `POST` | `/api/upload-originals` | Background stream for 300 DPI original print files |
| `POST` | `/api/generate-async` | Asynchronous job submission for AI themes + DSA solver (202 Accepted) |
| `GET` | `/api/jobs/{job_id}` | Polling endpoint for photobook generation status & variations |
| `POST` | `/api/spreads/reshuffle` | Dynamic single-spread reshuffle on click (~10ms) |
| `POST` | `/api/variations/reshuffle` | Regenerate 3 distinct theme variations |
| `POST` | `/api/export-pdf` | Compile 300 DPI high-resolution print PDF with 3mm bleed margins |

---

## 🔍 4. Diagnostic & Inspection Modes

* **Story Mode:** The default user journey: Upload $\rightarrow$ AI Theme Prompt $\rightarrow$ 3D Cover Carousel $\rightarrow$ Virtualized Spreads.
* **System Stats:** Click the top-bar tab **"System Stats"** to see live stage benchmark tables, timing distributions, and persistent SQLite counts.
* **Boilerplate Inspector:** Click **"Boilerplate Inspector"** to inspect registered layouts, slot coordinates, and the 20 canonical theme color swatches.
