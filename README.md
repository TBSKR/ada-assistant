TARS (A.D.A.) — Real‑Time Desktop AI Assistant
=============================================

A.D.A. now ships with a TARS‑inspired persona and a streamlined, high‑contrast UI. It runs locally with a PySide6 desktop app, speaks via ElevenLabs, listens in real time, and can see your webcam or screen on demand. A Google Calendar MCP bridge is included for standards‑based scheduling.

Highlights
----------

- Voice‑to‑voice: Low‑latency conversation using Google Gemini Live + ElevenLabs TTS.
- Visuals: Toggle Webcam, Screen, or Off; video shows in the right panel.
- TARS persona: Friendly, candid, dry wit (brief), slightly more talkative with clear next steps.
- UI polish: Resizable columns, clearer status, explicit video status pill, redesigned mic control.
- Mic mute: Dedicated MIC ON/OFF button with bold green/red states; audio is actually muted in the pipeline.
- Calendar MCP: Calls a Google Calendar MCP server over HTTP; list, find, create, quick‑add, delete.

Quick Start
-----------

1) Prerequisites

- Python 3.10+
- macOS/Linux/Windows (macOS recommended)
- PortAudio (for PyAudio)
  - macOS: `brew install portaudio`
  - Ubuntu: `sudo apt-get install portaudio19-dev`
- API keys: Gemini + ElevenLabs

2) Install

```bash
python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

3) Configure environment

Create `.env` in the repo root:

```
GEMINI_API_KEY="your_gemini_key"
ELEVENLABS_API_KEY="your_elevenlabs_key"
# Optional
# ASSISTANT_NAME=TARS
# ELEVENLABS_VOICE_ID=LDStDeG1Uv2SL9ieB8xc
# MCP_CAL_BASE_URL=http://127.0.0.1:3001
```

4) Run

```bash
python ada.py --mode none   # or: camera | screen
```

Controls
--------

- Input: type in the bottom bar (Enter to send). A Send button is also available.
- Video: WEBCAM / SCREEN / OFF toggle in the right panel.
- Mic: “MIC ON/OFF” button (green = live, red = muted).
- Status: Left panel shows system stats and tool activity.

TARS Persona
------------

The assistant is configured to be a bit more talkative with a calm, dry wit. It acknowledges, answers succinctly, offers options when requests are vague, and ends with a short next‑step prompt. Visual analysis is opt‑in and carefully qualified.

Calendar MCP (Google Calendar)
------------------------------

This app calls an external Google Calendar MCP server over HTTP. You must run that server yourself.

- Base URL: `MCP_CAL_BASE_URL` (defaults to `http://127.0.0.1:3001`).
- Tools mapped: list calendars, find events, create event, quick‑add, delete event.

Quick setup (summary)

1. Install the official Calendar MCP server globally:

```bash
npm install -g @google/calendar-mcp
```

2. Start the MCP Calendar server per its docs (auth flow may open a browser).

3. Verify A.D.A. can reach it (optional script):

```bash
node tests/calendar_bridge_test.mjs
```

For details, see `MCP_CALENDAR_INTEGRATION.md` in this repo.

Configuration
-------------

- `GEMINI_API_KEY`: Required.
- `ELEVENLABS_API_KEY`: Required.
- `ASSISTANT_NAME`: Optional; defaults to `TARS`.
- `ELEVENLABS_VOICE_ID`: Optional; pick a voice with your preferred cadence.
- `MCP_CAL_BASE_URL`: Optional; URL of the running Calendar MCP HTTP server.

Notes on Voice Speed
--------------------

The code streams to ElevenLabs without an explicit speed control. Pace is mainly set by the chosen voice/model. If your ElevenLabs setup supports a speed/prosody parameter for stream‑input, that would be configured in the initial TTS handshake.

Troubleshooting
---------------

- Missing keys: Ensure `.env` exists and the keys are non‑empty.
- Mic doesn’t work: Grant microphone permission to your terminal/IDE; confirm the correct default input device.
- Camera black: Grant camera permission; ensure no other app is using the webcam.
- PyAudio issues: Install PortAudio (see prerequisites) and reinstall PyAudio.
- Calendar MCP errors: Confirm the MCP server is running and reachable at `MCP_CAL_BASE_URL`.

Security & Privacy
------------------

- `.env` is git‑ignored; never commit keys.
- The app does not persist transcripts by default.
- Calendar MCP calls happen to your local/hosted MCP server; manage credentials there.

License
-------

See repository for license information. All trademarks and service marks belong to their respective owners.
