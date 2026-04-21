# spcrawler

spcrawler is a full-stack live sports piracy investigation platform.

It combines:
- a Python crawler engine that searches and traverses streaming pages,
- a Go backend that manages crawler sessions and streams events,
- a React frontend that visualizes live crawl progress as an interactive graph.

The project is designed for live monitoring: you can start a session, watch page-by-page decisions, inspect nodes, and stop or remove sessions from one control room UI.

## What This Project Does

1. Accepts a keyword (for example: match name + live stream query).
2. Runs multi-turn DuckDuckGo discovery.
3. Crawls discovered URLs recursively.
4. Scores and classifies pages with rules + LLM.
5. Detects potential stream URLs and verifies likely live sources.
6. Stores crawl/session data in MongoDB.
7. Streams events to the frontend for real-time graph visualization.

## Tech Stack

- Frontend: React 19 + Vite
- Backend: Go (net/http + SSE)
- Crawler engine: Python (crawl4ai, ddgs, pymongo, requests)
- Database: MongoDB
- LLM provider: Gemini API

## Project Architecture

- frontend sends session requests to backend
- backend starts/stops Python crawler runs and exposes session APIs
- crawler emits structured events
- backend forwards events over SSE
- frontend renders live graph + inspector from event stream

## Prerequisites

- Node.js 18+
- npm 9+
- Go 1.22+
- Python 3.11+
- MongoDB running locally or remotely
- Gemini API key

## Quick Start (Recommended)

From the repository root:

```powershell
powershell -ExecutionPolicy Bypass -File .\start.ps1
```

This launches:
- Backend on http://localhost:8080
- Frontend on http://localhost:5173

## Manual Start

### 1) Backend

```powershell
cd backend
go run .\cmd\server
```

### 2) Frontend

```powershell
cd frontend
npm install
npm run dev
```

### 3) Python crawler dependencies

The backend starts the crawler via backend/scripts/run_scraper.py.
Install crawler requirements in your Python environment:

```powershell
cd backend\scripts
pip install -r requirements.txt
```

## Configuration

Frontend uses:
- VITE_API_BASE (optional, default: http://localhost:8080)

Backend uses:
- ADDR (optional, default: :8080)

Crawler session payload fields:
- keyword
- api_key
- db_name
- mongo_uri
- proxy_url (optional)

## Core API Endpoints

- POST /api/sessions
	Start a new crawler session.

- GET /api/sessions
	List all sessions.

- GET /api/sessions/{id}
	Get one session summary.

- GET /api/sessions/{id}/events
	Server-Sent Events stream for live crawler events.

- DELETE /api/sessions/{id}
	Stop a running session.

- POST /api/sessions/{id}/remove
	Remove session from runtime and clean related Mongo session records.

## Folder Structure

```text
spcrawler/
|-- README.md
|-- start.ps1
|-- backend/
|   |-- go.mod
|   |-- README.md
|   |-- cmd/
|   |   `-- server/
|   |       `-- main.go
|   |-- internal/
|   |   `-- sessions/
|   |       |-- http.go
|   |       `-- manager.go
|   `-- scripts/
|       |-- requirements.txt
|       `-- run_scraper.py
|-- frontend/
|   |-- index.html
|   |-- package.json
|   |-- README.md
|   |-- scripts/
|   |   `-- graph-logic-check.mjs
|   `-- src/
|       |-- graph-logic.js
|       |-- main.jsx
|       `-- styles.css
`-- spcrawler/
		|-- README.MD
		`-- src/
				|-- __init__.py
				|-- events.py
				|-- client/
				|   |-- llm.py
				|   |-- log.py
				|   |-- model.py
				|   `-- prompts.py
				|-- instance/
				|   |-- check.py
				|   |-- proxy_manager.py
				|   `-- scraper.py
				`-- utils/
						|-- config.py
						|-- constants.py
						`-- db.py
```

## Typical Local Workflow

1. Start backend and frontend.
2. Open frontend in browser.
3. Create a session with keyword + API key + Mongo settings.
4. Watch graph updates in real time.
5. Inspect nodes for page evidence, scoring, and stream details.
6. Stop or remove session when done.

## Notes

- The backend is intentionally lightweight and acts as an orchestration bridge.
- Most crawling intelligence and event generation lives in the Python engine.
- The frontend is optimized for live observability, not static reporting.

## Additional Docs

- backend/README.md
- frontend/README.md
- spcrawler/README.MD