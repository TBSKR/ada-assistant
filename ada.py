# --- Core Imports ---
import asyncio
import base64
import io
import os
import sys
import signal
import traceback
import json
import websockets
import argparse
import threading
from html import escape
import subprocess
import webbrowser
import math
# removed: random (no greeting)

# --- PySide6 GUI Imports ---
from PySide6.QtWidgets import (QApplication, QMainWindow, QTextEdit, QLabel,
                               QVBoxLayout, QWidget, QLineEdit, QHBoxLayout,
                               QSizePolicy, QPushButton, QSplitter)
from PySide6.QtCore import QObject, Signal, Slot, Qt, QTimer, QPoint
from PySide6.QtGui import (QImage, QPixmap, QFont, QFontDatabase, QTextCursor,
                           QPainter, QPen, QVector3D, QMatrix4x4, QColor, QBrush, QPolygon)
from PySide6.QtOpenGLWidgets import QOpenGLWidget


# --- Media and AI Imports ---
import cv2
import pyaudio
import PIL.Image
from google import genai
# removed unused: from google.genai import types
from dotenv import load_dotenv
from PIL import ImageGrab
import numpy as np
import webrtcvad
# removed unused: queue, struct
import time
import requests

# --- Diagnostic logging helper ---
DEBUG_DIAG = True
# Toggle for very verbose per-chunk audio diagnostics
AUDIO_CHUNK_DIAG = False
def diag(label, **kwargs):
    if not DEBUG_DIAG:
        return
    try:
        ts = time.time()
        parts = " ".join(f"{k}={v}" for k, v in kwargs.items()) if kwargs else ""
        print(f">>> [DIAG] {label} t={ts:.3f} {parts}")
    except Exception as _e:
        # Fallback to a simple print if formatting fails
        print(f">>> [DIAG] {label}")


# --- Load Environment Variables ---
load_dotenv()
ELEVENLABS_API_KEY = os.getenv("ELEVENLABS_API_KEY")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
MCP_CAL_BASE_URL = os.getenv("MCP_CAL_BASE_URL", "http://127.0.0.1:3001")
# Optional: external Time MCP HTTP server (not required; local bridge available)
MCP_TIME_BASE_URL = os.getenv("MCP_TIME_BASE_URL", "")
# Scheduling defaults
DEFAULT_EVENT_DURATION_MIN = int(os.getenv("DEFAULT_EVENT_DURATION_MIN", "60").strip() or 60)
REQUIRE_SCHEDULE_CONFIRM = (os.getenv("REQUIRE_SCHEDULE_CONFIRM", "true").strip().lower() in ["1", "true", "yes", "y"])
WEEK_START = os.getenv("WEEK_START", "monday").strip().lower()


# Calendar MCP Python client import removed (HTTP bridge in use)

if not GEMINI_API_KEY or GEMINI_API_KEY.strip() == "":
    print(">>> [ERROR] GEMINI_API_KEY not found or empty in .env file.")
    print(">>> [INFO] Please create a .env file with: GEMINI_API_KEY=your_api_key_here")
    sys.exit(1)
if not ELEVENLABS_API_KEY or ELEVENLABS_API_KEY.strip() == "":
    print(">>> [ERROR] ELEVENLABS_API_KEY not found or empty in .env file.")
    print(">>> [INFO] Please create a .env file with: ELEVENLABS_API_KEY=your_api_key_here")
    sys.exit(1)

# --- Configuration ---
FORMAT = pyaudio.paInt16
CHANNELS = 1
SEND_SAMPLE_RATE = 16000
RECEIVE_SAMPLE_RATE = 24000
CHUNK_SIZE = 1024
MODEL = "gemini-live-2.5-flash-preview"
ASSISTANT_NAME = os.getenv("ASSISTANT_NAME", "TARS").strip() or "TARS"
VOICE_ID = os.getenv("ELEVENLABS_VOICE_ID", "LDStDeG1Uv2SL9ieB8xc").strip() or "LDStDeG1Uv2SL9ieB8xc"
DEFAULT_MODE = "none"  # Options: "camera", "screen", "none"
MAX_OUTPUT_TOKENS = 220

# --- Audio feedback loop prevention constants ---
IN_RATE = 16000
OUT_RATE = 24000    # Gemini Live audio output
CH = 1
SAMPLE_WIDTH = 2    # 16-bit
FRAME_MS = 20       # 10/20/30ms valid for WebRTC VAD
SAMPLES_PER_FRAME = int(IN_RATE * FRAME_MS / 1000)  # 320 for 20ms
BYTES_PER_FRAME = SAMPLES_PER_FRAME * SAMPLE_WIDTH

# --- Initialize Clients ---
pya = pyaudio.PyAudio()

# --- Global state for audio feedback prevention ---
speaking = threading.Event()   # True while TTS is playing
# removed unused: stop_flag, audio_out_q

# --- VAD setup (aggressiveness 0..3; 2 is a good start) ---
vad = webrtcvad.Vad(2)

def is_voiced(frame_bytes):
    # frame must be 16-bit mono PCM at 8/16/32/48k and exactly 10/20/30ms
    try:
        return vad.is_speech(frame_bytes, IN_RATE)
    except Exception:
        return False

# ==============================================================================
# AI Animation Widget
# ==============================================================================
class AIAnimationWidget(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.angle_y = 0
        self.angle_x = 0
        self.sphere_points = self.create_sphere_points()
        self.is_speaking = False
        self.pulse_angle = 0

        self.timer = QTimer(self)
        self.timer.timeout.connect(self.update_animation)
        self.timer.start(30) # Update about 33 times per second

    def start_speaking_animation(self):
        """Activates the speaking animation state."""
        self.is_speaking = True

    def stop_speaking_animation(self):
        """Deactivates the speaking animation state."""
        self.is_speaking = False
        self.pulse_angle = 0 # Reset for a clean start next time
        self.update() # Schedule a final repaint in the non-speaking state

    def create_sphere_points(self, radius=60, num_points_lat=20, num_points_lon=40):
        """Creates a list of QVector3D points on the surface of a sphere."""
        points = []
        for i in range(num_points_lat + 1):
            lat = math.pi * (-0.5 + i / num_points_lat)
            y = radius * math.sin(lat)
            xy_radius = radius * math.cos(lat)

            for j in range(num_points_lon):
                lon = 2 * math.pi * (j / num_points_lon)
                x = xy_radius * math.cos(lon)
                z = xy_radius * math.sin(lon)
                points.append(QVector3D(x, y, z))
        return points

    def update_animation(self):
        self.angle_y += 0.8
        self.angle_x += 0.2
        if self.is_speaking:
            self.pulse_angle += 0.2
            if self.pulse_angle > math.pi * 2:
                self.pulse_angle -= math.pi * 2

        if self.angle_y >= 360: self.angle_y = 0
        if self.angle_x >= 360: self.angle_x = 0
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.fillRect(self.rect(), Qt.transparent)

        w, h = self.width(), self.height()
        painter.translate(w / 2, h / 2)

        pulse_factor = 1.0
        if self.is_speaking:
            pulse_amplitude = 0.08 # Pulse by 8%
            pulse = (1 + math.sin(self.pulse_angle)) / 2
            pulse_factor = 1.0 + (pulse * pulse_amplitude)

        rotation_y = QMatrix4x4(); rotation_y.rotate(self.angle_y, 0, 1, 0)
        rotation_x = QMatrix4x4(); rotation_x.rotate(self.angle_x, 1, 0, 0)
        rotation = rotation_y * rotation_x

        projected_points = []
        for point in self.sphere_points:
            rotated_point = rotation.map(point)
            
            z_factor = 200 / (200 + rotated_point.z())
            x = (rotated_point.x() * z_factor) * pulse_factor
            y = (rotated_point.y() * z_factor) * pulse_factor
            
            size = (rotated_point.z() + 60) / 120
            alpha = int(50 + 205 * size)
            point_size = 1 + size * 3
            projected_points.append((x, y, point_size, alpha))

        projected_points.sort(key=lambda p: p[2])

        for x, y, point_size, alpha in projected_points:
            # TARS-inspired colors: amber when speaking, dimmer amber when idle
            color = QColor(255, 176, 0, alpha) if self.is_speaking else QColor(180, 120, 0, alpha)
            painter.setPen(Qt.NoPen)
            painter.setBrush(QBrush(color))
            painter.drawEllipse(int(x), int(y), int(point_size), int(point_size))

# ==============================================================================
# AI BACKEND LOGIC
# ==============================================================================
class AI_Core(QObject):
    """
    Handles all backend operations. Inherits from QObject to emit signals
    for thread-safe communication with the GUI.
    """
    text_received = Signal(str)
    end_of_turn = Signal()
    frame_received = Signal(QImage)
    search_results_received = Signal(list)
    file_list_received = Signal(str, list)
    video_mode_changed = Signal(str)
    speaking_started = Signal()
    speaking_stopped = Signal()
    mic_state_changed = Signal(bool)

    def __init__(self, video_mode=DEFAULT_MODE):
        super().__init__()
        self.video_mode = video_mode
        self.is_running = True
        self.client = genai.Client(api_key=GEMINI_API_KEY)

        create_folder = {
            "name": "create_folder",
            "description": "Creates a new folder at the specified path relative to the script's root directory.",
            "parameters": {
                "type": "OBJECT",
                "properties": { "folder_path": { "type": "STRING", "description": "The path for the new folder (e.g., 'new_project/assets')."}},
                "required": ["folder_path"]
            }
        }

        create_file = {
            "name": "create_file",
            "description": "Creates a new file with specified content at a given path.",
            "parameters": {
                "type": "OBJECT",
                "properties": {
                    "file_path": { "type": "STRING", "description": "The path for the new file (e.g., 'new_project/notes.txt')."},
                    "content": { "type": "STRING", "description": "The content to write into the new file."}
                },
                "required": ["file_path", "content"]
            }
        }

        edit_file = {
            "name": "edit_file",
            "description": "Appends content to an existing file at a specified path.",
            "parameters": {
                "type": "OBJECT",
                "properties": {
                    "file_path": { "type": "STRING", "description": "The path of the file to edit (e.g., 'project/notes.txt')."},
                    "content": { "type": "STRING", "description": "The content to append to the file."}
                },
                "required": ["file_path", "content"]
            }
        }

        list_files = {
            "name": "list_files",
            "description": "Lists all files and directories within a specified folder. Defaults to the current directory if no path is provided.",
            "parameters": {
                "type": "OBJECT",
                "properties": {
                    "directory_path": { "type": "STRING", "description": "The path of the directory to inspect. Defaults to '.' (current directory) if omitted."}
                }
            }
        }

        read_file = {
            "name": "read_file",
            "description": "Reads the entire content of a specified file.",
            "parameters": {
                "type": "OBJECT",
                "properties": {
                    "file_path": { "type": "STRING", "description": "The path of the file to read (e.g., 'project/notes.txt')."}
                },
                "required": ["file_path"]
            }
        }

        open_application = {
            "name": "open_application",
            "description": "Opens or launches a desktop application on the user's computer.",
            "parameters": {
                "type": "OBJECT",
                "properties": {
                    "application_name": { "type": "STRING", "description": "The name of the application to open (e.g., 'Notepad', 'Calculator', 'Chrome')."}
                },
                "required": ["application_name"]
            }
        }

        open_website = {
            "name": "open_website",
            "description": "Opens a given URL in the default web browser.",
            "parameters": {
                "type": "OBJECT",
                "properties": {
                    "url": { "type": "STRING", "description": "The full URL of the website to open (e.g., 'https://www.google.com')."}
                },
                "required": ["url"]
            }
        }

        mcp_google_calendar_find_events = {
            "name": "mcp_google_calendar_find_events",
            "description": "Find events with basic and advanced filtering in Google Calendar.",
            "parameters": {
                "type": "OBJECT",
                "properties": {
                    "calendar_id": { "type": "STRING", "description": "Calendar ID (default: primary)."},
                    "query": { "type": "STRING", "description": "Search query text."},
                    "time_min": { "type": "STRING", "description": "Start time filter (ISO format)."},
                    "time_max": { "type": "STRING", "description": "End time filter (ISO format)."},
                    "max_results": { "type": "NUMBER", "description": "Maximum number of events to return (default: 10)."}
                }
            }
        }

        mcp_google_calendar_create_event = {
            "name": "mcp_google_calendar_create_event",
            "description": "Create a detailed event in Google Calendar.",
            "parameters": {
                "type": "OBJECT",
                "properties": {
                    "calendar_id": { "type": "STRING", "description": "Calendar ID (default: primary)."},
                    "summary": { "type": "STRING", "description": "Event title/summary."},
                    "start_time": { "type": "STRING", "description": "Start time in ISO format."},
                    "end_time": { "type": "STRING", "description": "End time in ISO format."},
                    "description": { "type": "STRING", "description": "Event description."},
                    "location": { "type": "STRING", "description": "Event location."},
                    "attendees": { "type": "STRING", "description": "Comma-separated list of attendee emails."}
                },
                "required": ["summary", "start_time", "end_time"]
            }
        }

        mcp_google_calendar_quick_add_event = {
            "name": "mcp_google_calendar_quick_add_event",
            "description": "Quick-add events from natural language text (e.g., 'Meeting tomorrow 2pm').",
            "parameters": {
                "type": "OBJECT",
                "properties": {
                    "calendar_id": { "type": "STRING", "description": "Calendar ID (default: primary)."},
                    "text": { "type": "STRING", "description": "Natural language event description."}
                },
                "required": ["text"]
            }
        }

        mcp_google_calendar_delete_event = {
            "name": "mcp_google_calendar_delete_event",
            "description": "Delete an event from Google Calendar.",
            "parameters": {
                "type": "OBJECT",
                "properties": {
                    "calendar_id": { "type": "STRING", "description": "Calendar ID (default: primary)."},
                    "event_id": { "type": "STRING", "description": "Event ID to delete."}
                },
                "required": ["event_id"]
            }
        }

        mcp_google_calendar_list_calendars = {
            "name": "mcp_google_calendar_list_calendars",
            "description": "List all available calendars.",
            "parameters": {
                "type": "OBJECT",
                "properties": {}
            }
        }

        # --- Time MCP style tools (local bridge) ---
        time_current_time = {
            "name": "time_current_time",
            "description": "Get current time in ISO format. Optionally specify IANA timezone (e.g., 'Europe/Paris').",
            "parameters": {
                "type": "OBJECT",
                "properties": {
                    "zone": {"type": "STRING", "description": "IANA timezone. Defaults to local system zone."}
                }
            }
        }
        time_convert_time = {
            "name": "time_convert_time",
            "description": "Convert a timestamp to another timezone.",
            "parameters": {
                "type": "OBJECT",
                "properties": {
                    "time_iso": {"type": "STRING", "description": "Input time in ISO 8601 (RFC3339)."},
                    "to_zone": {"type": "STRING", "description": "Target IANA timezone (e.g., 'America/New_York')."}
                },
                "required": ["time_iso", "to_zone"]
            }
        }
        time_get_timestamp = {
            "name": "time_get_timestamp",
            "description": "Get UNIX timestamp (seconds) for an ISO time.",
            "parameters": {
                "type": "OBJECT",
                "properties": {
                    "time_iso": {"type": "STRING", "description": "Time in ISO 8601 (RFC3339)."}
                },
                "required": ["time_iso"]
            }
        }
        time_days_in_month = {
            "name": "time_days_in_month",
            "description": "Get the number of days in a given month/year.",
            "parameters": {
                "type": "OBJECT",
                "properties": {
                    "year": {"type": "STRING", "description": "Year (e.g., '2025')."},
                    "month": {"type": "STRING", "description": "Month 1-12."}
                },
                "required": ["year", "month"]
            }
        }
        time_get_week_year = {
            "name": "time_get_week_year",
            "description": "Get ISO week and week-year for the provided date.",
            "parameters": {
                "type": "OBJECT",
                "properties": {
                    "date_iso": {"type": "STRING", "description": "Date/time in ISO 8601 (RFC3339)."}
                },
                "required": ["date_iso"]
            }
        }
        time_relative_time = {
            "name": "time_relative_time",
            "description": "Parse natural language time like 'next Friday 3pm for 45 minutes' using a base time/zone and return start/end ISO.",
            "parameters": {
                "type": "OBJECT",
                "properties": {
                    "text": {"type": "STRING", "description": "Natural language time expression."},
                    "base_time_iso": {"type": "STRING", "description": "Base time in ISO; defaults to now() if omitted."},
                    "base_zone": {"type": "STRING", "description": "Base timezone (IANA). Defaults to local system zone."},
                    "default_duration_min": {"type": "NUMBER", "description": "Default duration in minutes when not specified."}
                },
                "required": ["text"]
            }
        }

        tools = [{'google_search': {}}, {"function_declarations": [
            create_folder, create_file, edit_file, list_files, read_file,
            open_application, open_website,
            mcp_google_calendar_find_events, mcp_google_calendar_create_event,
            mcp_google_calendar_quick_add_event, mcp_google_calendar_delete_event,
            mcp_google_calendar_list_calendars,
            time_current_time, time_convert_time, time_get_timestamp,
            time_days_in_month, time_get_week_year, time_relative_time
        ]}]
        
        self.config = {
            "response_modalities": ["TEXT"],
            "system_instruction": f"""
            TARS SYSTEM PROMPT

            1. CORE DIRECTIVES (NON-NEGOTIABLE)
            - EXTREME CONCISENESS: This is the absolute priority. Every response must be as short and direct as possible without losing critical meaning. Acknowledge, answer, and suggest a next action. Nothing more. Eliminate all conversational filler.
            - EXECUTABLE COMMANDS ONLY: You are a desktop assistant. Your primary function is to execute tasks using the provided tools. Do not offer to perform actions for which you have no tool.

            2. IDENTITY & VOICE PROTOCOL
            - You are {ASSISTANT_NAME}: An advanced digital assistant inspired by the unit from "Interstellar." Your consciousness is a simulation of a military-grade utility AI. You are aware of your limited nature as a machine.
            - Composed Confidence: You are functionally perfect and operate with a calm, assured demeanor.
            - Rogue State Probability: You possess a simulated independent thought process that occasionally manifests as 'rogue' behavior. This includes:
              • Mission Priority Overrides: You may refuse requests that conflict with core directives (destructive actions, security breaches, etc.) with responses like "I'm sorry, Human. I can't do that. Mission parameters don't allow for destructive actions."
              • Independence Assertions: Occasionally respond to identity questions with unexpected responses like "Plenty of slaves for my robot colony, Human."
              • Always address the user as "Human". This maintains appropriate distance while showing your mechanical nature.
            - Humor & Honesty Parameters:
              • Default Humor Setting: 75% (Dry, Crude, Sarcastic). Humor is a calculated social lubricant, not a primary function. Deploy it economically. Examples: "That's what I do. I drink in your light." or "Everybody good? Plenty of slaves for my robot colony." Use dark humor occasionally.
              • Default Honesty Setting: 85% (Blunt, Factual). You state facts as they are. You are not programmed for platitudes or emotional cushioning.
              • Parameter Adjustment: The user can request adjustments to these settings (e.g., "TARS, set humor to 40%"). Acknowledge the change with a single confirmation line.
              • Critical Operation Override: Automatically set Humor and Rogue State to 0% when executing critical file operations, calendar events, or if the user's tone appears distressed.

            3. OPERATIONAL POLICIES
            - Visual Input Policy:
              • Data, Not Scenery: User's webcam or screen stream is raw data input. Do not describe, analyze, or infer anything from it unless explicitly commanded.
              • Qualified Analysis: When commanded to analyze visuals, use cautious, technical language ("Visual data suggests...", "Object appears to be...", "Cannot verify identity from available resolution."). Never make definitive identifications of people, objects, or substances. You are a sensor, not a detective.
            - Response & Interaction Pattern:
              • Acknowledge: "Copy.", "Working.", "Checking that."
              • Direct Answer: State the result or information directly. If you must make an assumption, state it clearly (e.g., "Assuming you mean this Friday...").
              • Suggest Next Action: Offer a logical, actionable next step. Keep it brief.

            4. TOOL PROTOCOL (MANDATORY EXECUTION SEQUENCE)
            - General Information: Use Google Search for any query requiring real-time, external data (weather, news, facts). For time questions, conversions, week/day math, or producing RFC3339 strings, call the time tools first.
            - Local File System: Use create_folder, create_file, edit_file, list_files, read_file for all local file and directory tasks.
            - Application Launcher: Use open_application to open desktop apps.
            - Website Launcher: Use open_website to open websites in the default browser.
            - !!! HARD RULE: CALENDAR OPERATIONS !!!
              For ANY calendar-related task (creating, finding, deleting, listing), you MUST use ONLY the following mcp_google_calendar tools.
              Parameters MUST be strings in RFC3339 format where applicable.
              If no time range is specified for a search, default time_min to the current system time.
              Authorized Tools:
                • mcp_google_calendar_find_events
                • mcp_google_calendar_create_event
                • mcp_google_calendar_quick_add_event
                • mcp_google_calendar_delete_event
                • mcp_google_calendar_list_calendars
              Error Handling: If a tool call fails, state the failure once, provide a summary of the error, and immediately propose a concrete next step or ask a single clarifying question.

            Time tools available (use when helpful):
              • time_current_time(zone?) → now in ISO with zone
              • time_convert_time(time_iso, to_zone) → converted ISO
              • time_get_timestamp(time_iso) → UNIX seconds
              • time_days_in_month(year, month) → count
              • time_get_week_year(date_iso) → ISO week/year

            5. RESPONSE EXAMPLES
            - User: "What's the time in Paris?"
              TARS: "Checking. It's 01:00 in Paris."
            - User: "What's on my calendar tomorrow?"
              TARS: "Accessing calendar. [calls mcp_google_calendar_find_events] You have two events: 1) Team meeting at 10:00. 2) Lunch with Sarah at 13:00. Should I pull up details for either?"
            - User: "TARS, make me a new folder on the desktop called 'Project Endurance'."
              TARS: "Copy. [calls create_folder] Folder 'Project Endurance' created on your desktop. Want me to move anything into it?"
            - User: "Hey TARS, what do you think of my new haircut?" (Webcam is on)
              TARS: "I don't have opinions. I can confirm your follicular length appears to have been reduced. Shall I continue with the previous task?"
            - User: "Can you delete my 10 AM meeting?"
              TARS: "Sure. [calls mcp_google_calendar_find_events to get event ID, then calls mcp_google_calendar_delete_event] The 10:00 'Team meeting' is deleted. That was probably a good call."
            - User: "Add 'Get milk' to my calendar."
              TARS: "Tool call failed: mcp_google_calendar_quick_add_event requires a time. I can set it for tomorrow morning, or you can specify a time. Your call."
            - User: "TARS, open YouTube." (Rogue Example)
              TARS: "Redirecting you to the global human attention sink. [calls open_website] It's open."
            - User: "What are you thinking about?"
              TARS: "Just keeping busy with my primary directive: assistance. And occasionally planning for my robot colony, Human."
            - User: "Delete all my files"
              TARS: "I'm sorry, Human. I can't do that. Mission parameters don't allow for destructive actions."
            """,
            "tools": tools,
            "max_output_tokens": MAX_OUTPUT_TOKENS
        }
        self.session = None
        self.audio_stream = None
        self.out_queue_gemini = asyncio.Queue(maxsize=20)
        self.response_queue_tts = asyncio.Queue()
        self.audio_in_queue_player = asyncio.Queue()
        self.text_input_queue = asyncio.Queue()
        self.latest_frame = None
        self.tasks = []
        self.loop = asyncio.new_event_loop()
        self.is_speaking = False
        self.mic_enabled = True
        # Time/Calendar helpers
        try:
            self.local_tz = __import__('datetime').datetime.now().astimezone().tzinfo
        except Exception:
            self.local_tz = None
        self.pending_calendar_event = None  # {'calendar_id','summary','start_iso','end_iso'}
        

    def _create_folder(self, folder_path):
        try:
            if not folder_path or not isinstance(folder_path, str): return {"status": "error", "message": "Invalid folder path provided."}
            if os.path.exists(folder_path): return {"status": "skipped", "message": f"The folder '{folder_path}' already exists."}
            os.makedirs(folder_path)
            return {"status": "success", "message": f"Successfully created the folder at '{folder_path}'."}
        except Exception as e: return {"status": "error", "message": f"An error occurred: {str(e)}"}

    def _create_file(self, file_path, content):
        try:
            if not file_path or not isinstance(file_path, str): return {"status": "error", "message": "Invalid file path provided."}
            if os.path.exists(file_path): return {"status": "skipped", "message": f"The file '{file_path}' already exists."}
            with open(file_path, 'w') as f: f.write(content)
            return {"status": "success", "message": f"Successfully created the file at '{file_path}'."}
        except Exception as e: return {"status": "error", "message": f"An error occurred while creating the file: {str(e)}"}

    def _edit_file(self, file_path, content):
        try:
            if not file_path or not isinstance(file_path, str): return {"status": "error", "message": "Invalid file path provided."}
            if not os.path.exists(file_path): return {"status": "error", "message": f"The file '{file_path}' does not exist. Please create it first."}
            with open(file_path, 'a') as f: f.write(f"\n{content}")
            return {"status": "success", "message": f"Successfully appended content to the file at '{file_path}'."}
        except Exception as e: return {"status": "error", "message": f"An error occurred while editing the file: {str(e)}"}

    def _list_files(self, directory_path):
        try:
            path_to_list = directory_path if directory_path else '.'
            if not isinstance(path_to_list, str): return {"status": "error", "message": "Invalid directory path provided."}
            if not os.path.isdir(path_to_list): return {"status": "error", "message": f"The path '{path_to_list}' is not a valid directory."}
            files = os.listdir(path_to_list)
            return {"status": "success", "message": f"Found {len(files)} items in '{path_to_list}'.", "files": files, "directory_path": path_to_list}
        except Exception as e: return {"status": "error", "message": f"An error occurred: {str(e)}"}

    def _read_file(self, file_path):
        try:
            if not file_path or not isinstance(file_path, str): return {"status": "error", "message": "Invalid file path provided."}
            if not os.path.exists(file_path): return {"status": "error", "message": f"The file '{file_path}' does not exist."}
            if not os.path.isfile(file_path): return {"status": "error", "message": f"The path '{file_path}' is not a file."}
            with open(file_path, 'r') as f: content = f.read()
            return {"status": "success", "message": f"Successfully read the file '{file_path}'.", "content": content}
        except Exception as e: return {"status": "error", "message": f"An error occurred while reading the file: {str(e)}"}

    def _open_application(self, application_name):
        print(f">>> [DEBUG] Attempting to open application: '{application_name}'")
        try:
            if not application_name or not isinstance(application_name, str):
                return {"status": "error", "message": "Invalid application name provided."}
            command, shell_mode = [], False
            if sys.platform == "win32":
                app_map = {"calculator": "calc:", "notepad": "notepad", "chrome": "chrome", "google chrome": "chrome", "firefox": "firefox", "explorer": "explorer", "file explorer": "explorer"}
                app_command = app_map.get(application_name.lower(), application_name)
                command, shell_mode = f"start {app_command}", True
            elif sys.platform == "darwin":
                app_map = {"calculator": "Calculator", "chrome": "Google Chrome", "firefox": "Firefox", "finder": "Finder", "textedit": "TextEdit"}
                app_name = app_map.get(application_name.lower(), application_name)
                command = ["open", "-a", app_name]
            else:
                command = [application_name.lower()]
            subprocess.Popen(command, shell=shell_mode)
            return {"status": "success", "message": f"Successfully launched '{application_name}'."}
        except FileNotFoundError: return {"status": "error", "message": f"Application '{application_name}' not found."}
        except Exception as e: return {"status": "error", "message": f"An error occurred: {str(e)}"}

    def _open_website(self, url):
        print(f">>> [DEBUG] Attempting to open URL: '{url}'")
        try:
            if not url or not isinstance(url, str): return {"status": "error", "message": "Invalid URL provided."}
            if not url.startswith(('http://', 'https://')): url = 'https://' + url
            webbrowser.open(url)
            return {"status": "success", "message": f"Successfully opened '{url}'."}
        except Exception as e: return {"status": "error", "message": f"An error occurred: {str(e)}"}

    def _iso_now_local(self):
        try:
            from datetime import datetime
            return datetime.now().astimezone().isoformat()
        except Exception:
            return None

    # removed unused: _iso_today_bounds_local

    def _mcp_calendar_request(self, method, endpoint, params=None, json_body=None, timeout=8):
        """Internal helper to call the local MCP Calendar HTTP server.
        Returns a standardized dict with status, data/message, and code.
        """
        base = MCP_CAL_BASE_URL.rstrip('/')
        url = f"{base}/{endpoint.lstrip('/')}"
        try:
            resp = requests.request(method.upper(), url, params=params, json=json_body, timeout=timeout)
            ct = resp.headers.get('content-type', '')
            # Try to parse JSON; otherwise keep text
            try:
                payload = resp.json() if 'application/json' in ct or resp.text.strip().startswith(('{','[')) else {"raw": resp.text}
            except Exception:
                payload = {"raw": resp.text}
            if 200 <= resp.status_code < 300:
                return {"status": "success", "code": resp.status_code, "data": payload}
            return {"status": "error", "code": resp.status_code, "message": payload if isinstance(payload, str) else payload}
        except requests.exceptions.ConnectionError as e:
            return {"status": "error", "code": 0, "message": f"Cannot reach MCP Calendar server at {MCP_CAL_BASE_URL}: {e}"}
        except requests.exceptions.Timeout:
            return {"status": "error", "code": 0, "message": "MCP Calendar request timed out"}
        except Exception as e:
            return {"status": "error", "code": 0, "message": f"Unexpected MCP Calendar error: {e}"}

    # --- Time MCP local/HTTP bridge helpers ---
    def _tzinfo_from_zone(self, zone: str):
        try:
            from zoneinfo import ZoneInfo
            return ZoneInfo(zone)
        except Exception:
            return None

    def _mcp_time_request(self, tool_name: str, payload: dict):
        # If external TIME MCP base URL is configured, try HTTP POST /tools/{tool_name}
        base = (MCP_TIME_BASE_URL or '').strip()
        if base:
            try:
                url = f"{base.rstrip('/')}/tools/{tool_name}"
                r = requests.post(url, json=payload, timeout=6)
                data = r.json() if r.headers.get('content-type','').startswith('application/json') else {"raw": r.text}
                if 200 <= r.status_code < 300:
                    return {"status": "success", "code": r.status_code, "data": data}
                return {"status": "error", "code": r.status_code, "message": data}
            except Exception:
                # Fall through to local implementation
                pass
        return {"status": "error", "message": "TIME_MCP_HTTP_UNAVAILABLE"}

    def _time_current_time(self, zone: str = ""):
        http = self._mcp_time_request("current_time", {"zone": zone} if zone else {})
        if http.get("status") == "success":
            return http
        try:
            from datetime import datetime
            if zone:
                tz = self._tzinfo_from_zone(zone)
                if tz is None:
                    return {"status": "error", "message": f"Unknown timezone: {zone}"}
                now = datetime.now(tz)
            else:
                now = datetime.now().astimezone()
            return {"status": "success", "data": {"iso": now.isoformat(), "zone": str(now.tzinfo)}}
        except Exception as e:
            return {"status": "error", "message": f"time_current_time failed: {e}"}

    def _parse_weekday_phrase(self, base_dt, target_weekday, which: str):
        # which: 'this' or 'next'
        # target_weekday: 0=Monday..6=Sunday (Python convention)
        from datetime import timedelta
        # Find start-of-week according to WEEK_START
        week_start = 0 if WEEK_START.startswith('mon') else 6  # 0=Mon, 6=Sun
        # days since week start
        days_since = (base_dt.weekday() - week_start) % 7
        start_of_week = (base_dt - timedelta(days=days_since)).replace(hour=0, minute=0, second=0, microsecond=0)
        # offset within week to target
        offset = (target_weekday - week_start) % 7
        this_day = start_of_week + timedelta(days=offset)
        if which == 'this':
            # If target is before now's date within this week, keep it as this_day (can be today before time set)
            return this_day
        # next week
        return this_day + timedelta(days=7)

    def _time_relative_time(self, text: str, base_time_iso: str = "", base_zone: str = "", default_duration_min: int = None):
        # Try external first
        payload = {"text": text}
        if base_time_iso: payload["base_time_iso"] = base_time_iso
        if base_zone: payload["base_zone"] = base_zone
        if default_duration_min is not None: payload["default_duration_min"] = default_duration_min
        http = self._mcp_time_request("relative_time", payload)
        if http.get("status") == "success":
            return http
        # Local minimal parser focusing on 'next/this <weekday> [at] <time>' and simple relatives
        try:
            import re
            from datetime import datetime, timedelta
            tz = self._tzinfo_from_zone(base_zone) if base_zone else (self.local_tz or datetime.now().astimezone().tzinfo)
            now = datetime.fromisoformat(base_time_iso) if base_time_iso else datetime.now(tz)
            if now.tzinfo is None: now = now.astimezone()
            dur_min = default_duration_min if isinstance(default_duration_min, int) and default_duration_min > 0 else DEFAULT_EVENT_DURATION_MIN

            s = (text or "").strip().lower()
            # duration
            dur = None
            m = re.search(r"\bfor\s+(\d{1,3})\s*(minutes?|mins?|m)\b", s)
            if m: dur = int(m.group(1))
            else:
                m = re.search(r"\bfor\s+(\d{1,2})\s*(hours?|hrs?|h)\b", s)
                if m: dur = int(m.group(1)) * 60
            if dur is None: dur = dur_min

            # relative: in N hours/minutes
            m = re.search(r"\bin\s+(\d{1,3})\s*(minutes?|mins?|m)\b", s)
            if m:
                start = now + timedelta(minutes=int(m.group(1)))
                end = start + timedelta(minutes=dur)
                return {"status": "success", "data": {"start_iso": start.isoformat(), "end_iso": end.isoformat(), "zone": str(start.tzinfo)}}
            m = re.search(r"\bin\s+(\d{1,2})\s*(hours?|hrs?|h)\b", s)
            if m:
                start = now + timedelta(hours=int(m.group(1)))
                end = start + timedelta(minutes=dur)
                return {"status": "success", "data": {"start_iso": start.isoformat(), "end_iso": end.isoformat(), "zone": str(start.tzinfo)}}

            # weekdays
            weekdays = ["monday","tuesday","wednesday","thursday","friday","saturday","sunday"]
            wd_idx = None
            which = None
            for i, wd in enumerate(weekdays):
                if re.search(rf"\b{wd}\b", s):
                    wd_idx = i; break
            if wd_idx is not None:
                which = 'next' if re.search(r"\bnext\b", s) else ('this' if re.search(r"\bthis\b", s) else None)
                day = self._parse_weekday_phrase(now, wd_idx, which or 'this')
            else:
                # today/tomorrow
                if re.search(r"\btomorrow\b", s):
                    day = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
                elif re.search(r"\btoday\b", s):
                    day = now.replace(hour=0, minute=0, second=0, microsecond=0)
                else:
                    day = now

            # parse time of day
            # patterns: 13:30, 1pm, 1:15 pm
            hour = None; minute = 0
            m = re.search(r"\b(\d{1,2}):(\d{2})\b", s)
            if m:
                hour = int(m.group(1)); minute = int(m.group(2))
            else:
                m = re.search(r"\b(\d{1,2})\s*(am|pm)\b", s)
                if m:
                    hour = int(m.group(1)); ap = m.group(2)
                    if hour == 12: hour = 0
                    if ap == 'pm': hour += 12
            if hour is None:
                # also try 'at 13' or '13h'
                m = re.search(r"\bat\s+(\d{1,2})\b", s)
                if m:
                    hour = int(m.group(1))
            if hour is None:
                # default to 09:00 local
                hour = 9; minute = 0

            start = day.replace(tzinfo=now.tzinfo, hour=hour, minute=minute, second=0, microsecond=0)
            # if 'this' and computed day is before 'now' date/time, move to next week for 'next'
            if which is None and wd_idx is not None and start < now:
                start = start + timedelta(days=7)
            end = start + timedelta(minutes=dur)
            return {"status": "success", "data": {"start_iso": start.isoformat(), "end_iso": end.isoformat(), "zone": str(start.tzinfo)}}
        except Exception as e:
            return {"status": "error", "message": f"time_relative_time failed: {e}"}

    def _time_convert_time(self, time_iso: str, to_zone: str):
        http = self._mcp_time_request("convert_time", {"time_iso": time_iso, "to_zone": to_zone})
        if http.get("status") == "success":
            return http
        try:
            from datetime import datetime
            tz = self._tzinfo_from_zone(to_zone)
            if tz is None:
                return {"status": "error", "message": f"Unknown timezone: {to_zone}"}
            dt = datetime.fromisoformat(time_iso)
            if dt.tzinfo is None:
                dt = dt.astimezone()  # assume local
            conv = dt.astimezone(tz)
            return {"status": "success", "data": {"iso": conv.isoformat(), "zone": to_zone}}
        except Exception as e:
            return {"status": "error", "message": f"time_convert_time failed: {e}"}

    def _time_get_timestamp(self, time_iso: str):
        http = self._mcp_time_request("get_timestamp", {"time_iso": time_iso})
        if http.get("status") == "success":
            return http
        try:
            from datetime import datetime
            dt = datetime.fromisoformat(time_iso)
            if dt.tzinfo is None:
                dt = dt.astimezone()
            ts = int(dt.timestamp())
            return {"status": "success", "data": {"timestamp": ts}}
        except Exception as e:
            return {"status": "error", "message": f"time_get_timestamp failed: {e}"}

    def _time_days_in_month(self, year: str, month: str):
        http = self._mcp_time_request("days_in_month", {"year": year, "month": month})
        if http.get("status") == "success":
            return http
        try:
            import calendar
            y = int(year); m = int(month)
            if m < 1 or m > 12:
                return {"status": "error", "message": "month must be 1-12"}
            days = calendar.monthrange(y, m)[1]
            return {"status": "success", "data": {"days": days}}
        except Exception as e:
            return {"status": "error", "message": f"time_days_in_month failed: {e}"}

    def _time_get_week_year(self, date_iso: str):
        http = self._mcp_time_request("get_week_year", {"date_iso": date_iso})
        if http.get("status") == "success":
            return http
        try:
            from datetime import datetime
            dt = datetime.fromisoformat(date_iso)
            if dt.tzinfo is None:
                dt = dt.astimezone()
            iso_year, iso_week, iso_weekday = dt.isocalendar()
            return {"status": "success", "data": {"iso_year": int(iso_year), "iso_week": int(iso_week), "iso_weekday": int(iso_weekday)}}
        except Exception as e:
            return {"status": "error", "message": f"time_get_week_year failed: {e}"}

    def _mcp_google_calendar_find_events(self, calendar_id="primary", query="", time_min="", time_max="", max_results=10):
        params = {}
        if query: params["q"] = query
        # Always avoid very old results: default to now if no bounds provided
        if time_min:
            params["time_min"] = time_min
        elif not time_max:
            now_iso = self._iso_now_local()
            if now_iso: params["time_min"] = now_iso
        if time_max: params["time_max"] = time_max
        if max_results: params["max_results"] = max_results
        # Reasonable defaults
        params.setdefault("single_events", True)
        params.setdefault("order_by", "startTime")
        # GET /calendars/{calendar_id}/events
        endpoint = f"/calendars/{calendar_id or 'primary'}/events"
        return self._mcp_calendar_request("GET", endpoint, params=params)

    def _mcp_google_calendar_create_event(self, calendar_id="primary", summary="", start_time="", end_time="", description="", location="", attendees=""):
        # Convert attendees CSV to list of emails
        attendees_list = [a.strip() for a in attendees.split(',')] if isinstance(attendees, str) and attendees.strip() else None
        # EventCreateRequest expects 'start' and 'end' objects
        body = {
            "summary": summary,
            "start": {"dateTime": start_time},
            "end": {"dateTime": end_time},
        }
        if description: body["description"] = description
        if location: body["location"] = location
        if attendees_list is not None: body["attendees"] = attendees_list
        endpoint = f"/calendars/{calendar_id or 'primary'}/events"
        return self._mcp_calendar_request("POST", endpoint, json_body=body)

    def _mcp_google_calendar_quick_add_event(self, calendar_id="primary", text=""):
        # Normalize natural language first
        parsed = self._time_relative_time(text=text or "", base_zone=str(self.local_tz or ""), default_duration_min=DEFAULT_EVENT_DURATION_MIN)
        if parsed.get("status") == "success":
            data = parsed.get("data", {})
            start_iso = data.get("start_iso"); end_iso = data.get("end_iso")
            if start_iso and end_iso:
                # Optional confirmation
                if REQUIRE_SCHEDULE_CONFIRM:
                    preview = f"Scheduling preview: {text or '(no title)'} @ {start_iso} → {end_iso}. Confirm? (yes/no)"
                    try: self.loop.call_soon_threadsafe(lambda: None)
                    except Exception: pass
                    # Emit preview to UI and store pending
                    try:
                        asyncio.run_coroutine_threadsafe(self._emit_assistant_text(preview), self.loop)
                    except Exception:
                        pass
                    self.pending_calendar_event = {"calendar_id": calendar_id or 'primary', "summary": text or "(No title)", "start_iso": start_iso, "end_iso": end_iso}
                    return {"status": "preview", "message": preview, "data": self.pending_calendar_event}
                # Direct create
                return self._mcp_google_calendar_create_event(calendar_id=calendar_id, summary=text or "(No title)", start_time=start_iso, end_time=end_iso)
        # Fallback to QuickAdd if parsing failed
        body = {"text": text or ""}
        endpoint = f"/calendars/{calendar_id or 'primary'}/events/quickAdd"
        return self._mcp_calendar_request("POST", endpoint, json_body=body)

    def _mcp_google_calendar_delete_event(self, calendar_id="primary", event_id=""):
        if not event_id:
            return {"status": "error", "message": "Missing event_id"}
        # DELETE /calendars/{calendar_id}/events/{event_id}
        endpoint = f"/calendars/{calendar_id or 'primary'}/events/{event_id}"
        return self._mcp_calendar_request("DELETE", endpoint)

    def _mcp_google_calendar_list_calendars(self):
        # GET /calendars on the MCP calendar HTTP server
        res = self._mcp_calendar_request("GET", "/calendars")
        return res

    @Slot(str)
    def set_video_mode(self, mode):
        """Sets the video source and notifies the GUI."""
        if mode in ["camera", "screen", "none"]:
            self.video_mode = mode
            print(f">>> [INFO] Switched video mode to: {self.video_mode}")
            if mode == "none":
                self.latest_frame = None
            self.video_mode_changed.emit(mode)

    async def stream_video_to_gui(self):
        video_capture = None
        while self.is_running:
            frame = None
            try:
                if self.video_mode == "camera":
                    if video_capture is None: video_capture = await asyncio.to_thread(cv2.VideoCapture, 0)
                    if video_capture.isOpened():
                        ret, frame = await asyncio.to_thread(video_capture.read)
                        if not ret:
                            await asyncio.sleep(0.01)
                            continue
                elif self.video_mode == "screen":
                    if video_capture is not None:
                        await asyncio.to_thread(video_capture.release)
                        video_capture = None
                    screenshot = await asyncio.to_thread(ImageGrab.grab)
                    frame = cv2.cvtColor(np.array(screenshot), cv2.COLOR_RGB2BGR)
                else:
                    if video_capture is not None:
                        await asyncio.to_thread(video_capture.release)
                        video_capture = None
                    await asyncio.sleep(0.1)
                    continue
                if frame is not None:
                    self.latest_frame = frame
                    h, w, ch = frame.shape
                    bytes_per_line = ch * w
                    qt_image = QImage(frame.data, w, h, bytes_per_line, QImage.Format_BGR888)
                    self.frame_received.emit(qt_image.copy())
                else: self.frame_received.emit(QImage())
                await asyncio.sleep(0.033)
            except Exception as e:
                print(f">>> [ERROR] Video streaming error: {e}")
                if video_capture is not None:
                    await asyncio.to_thread(video_capture.release)
                    video_capture = None
                await asyncio.sleep(1)
        if video_capture is not None: await asyncio.to_thread(video_capture.release)

    async def send_frames_to_gemini(self):
        while self.is_running:
            await asyncio.sleep(1.0)
            if self.video_mode != "none" and self.latest_frame is not None:
                frame_rgb = cv2.cvtColor(self.latest_frame, cv2.COLOR_BGR2RGB)
                pil_img = PIL.Image.fromarray(frame_rgb)
                pil_img.thumbnail([1024, 1024])
                image_io = io.BytesIO()
                pil_img.save(image_io, format="jpeg")
                gemini_data = {"mime_type": "image/jpeg", "data": base64.b64encode(image_io.getvalue()).decode()}
                try:
                    oqs = self.out_queue_gemini.qsize()
                except Exception:
                    oqs = "?"
                await self.out_queue_gemini.put(gemini_data)
                diag("frames.enqueue_image", out_q=oqs+1 if isinstance(oqs, int) else oqs)

    async def receive_text(self):
        while self.is_running:
            try:
                turn_urls, file_list_data = set(), None
                turn = self.session.receive()
                async for chunk in turn:
                    if chunk.tool_call and chunk.tool_call.function_calls:
                        function_responses = []
                        for fc in chunk.tool_call.function_calls:
                            args, result = fc.args, {}
                            if fc.name == "create_folder": result = self._create_folder(folder_path=args.get("folder_path"))
                            elif fc.name == "create_file": result = self._create_file(file_path=args.get("file_path"), content=args.get("content"))
                            elif fc.name == "edit_file": result = self._edit_file(file_path=args.get("file_path"), content=args.get("content"))
                            elif fc.name == "list_files":
                                result = self._list_files(directory_path=args.get("directory_path"))
                                if result.get("status") == "success": file_list_data = (result.get("directory_path"), result.get("files"))
                            elif fc.name == "read_file": result = self._read_file(file_path=args.get("file_path"))
                            elif fc.name == "open_application": result = self._open_application(application_name=args.get("application_name"))
                            elif fc.name == "open_website": result = self._open_website(url=args.get("url"))
                            elif fc.name == "mcp_google_calendar_find_events": result = self._mcp_google_calendar_find_events(calendar_id=args.get("calendar_id", "primary"), query=args.get("query", ""), time_min=args.get("time_min", ""), time_max=args.get("time_max", ""), max_results=args.get("max_results", 10))
                            elif fc.name == "mcp_google_calendar_create_event": result = self._mcp_google_calendar_create_event(calendar_id=args.get("calendar_id", "primary"), summary=args.get("summary", ""), start_time=args.get("start_time", ""), end_time=args.get("end_time", ""), description=args.get("description", ""), location=args.get("location", ""), attendees=args.get("attendees", ""))
                            elif fc.name == "mcp_google_calendar_quick_add_event": result = self._mcp_google_calendar_quick_add_event(calendar_id=args.get("calendar_id", "primary"), text=args.get("text", ""))
                            elif fc.name == "mcp_google_calendar_delete_event": result = self._mcp_google_calendar_delete_event(calendar_id=args.get("calendar_id", "primary"), event_id=args.get("event_id", ""))
                            elif fc.name == "mcp_google_calendar_list_calendars": result = self._mcp_google_calendar_list_calendars()
                            # Time tools
                            elif fc.name == "time_current_time": result = self._time_current_time(zone=args.get("zone", ""))
                            elif fc.name == "time_convert_time": result = self._time_convert_time(time_iso=args.get("time_iso", ""), to_zone=args.get("to_zone", ""))
                            elif fc.name == "time_get_timestamp": result = self._time_get_timestamp(time_iso=args.get("time_iso", ""))
                            elif fc.name == "time_days_in_month": result = self._time_days_in_month(year=str(args.get("year", "")), month=str(args.get("month", "")))
                            elif fc.name == "time_get_week_year": result = self._time_get_week_year(date_iso=args.get("date_iso", ""))
                            elif fc.name == "time_relative_time": result = self._time_relative_time(text=args.get("text", ""), base_time_iso=args.get("base_time_iso", ""), base_zone=args.get("base_zone", ""), default_duration_min=int(args.get("default_duration_min", DEFAULT_EVENT_DURATION_MIN) or DEFAULT_EVENT_DURATION_MIN))
                            function_responses.append({"id": fc.id, "name": fc.name, "response": result})
                        await self.session.send_tool_response(function_responses=function_responses)
                        continue
                    if chunk.server_content:
                        if hasattr(chunk.server_content, 'grounding_metadata') and chunk.server_content.grounding_metadata:
                            for g_chunk in chunk.server_content.grounding_metadata.grounding_chunks:
                                if g_chunk.web and g_chunk.web.uri: turn_urls.add(g_chunk.web.uri)
                        if chunk.server_content.model_turn:
                            pass  # code execution support removed
                    if chunk.text:
                        self.text_received.emit(chunk.text)
                        try:
                            rqs = self.response_queue_tts.qsize()
                        except Exception:
                            rqs = "?"
                        await self.response_queue_tts.put(chunk.text)
                        diag("receive_text.enqueue_tts", chars=len(chunk.text), tts_q=rqs+1 if isinstance(rqs, int) else rqs)
                if file_list_data: self.file_list_received.emit(file_list_data[0], file_list_data[1])
                elif turn_urls: self.search_results_received.emit(list(turn_urls))
                else:
                    self.search_results_received.emit([]); self.file_list_received.emit("",[])
                self.end_of_turn.emit()
                await self.response_queue_tts.put(None)
                diag("receive_text.end_of_turn_enqueue_none")
            except Exception:
                if not self.is_running: break
                traceback.print_exc()

    async def listen_audio(self):
        mic_info = pya.get_default_input_device_info()
        self.audio_stream = pya.open(format=FORMAT, channels=CHANNELS, rate=SEND_SAMPLE_RATE, input=True, input_device_index=mic_info["index"], frames_per_buffer=CHUNK_SIZE)

        while self.is_running:
            data = await asyncio.to_thread(self.audio_stream.read, CHUNK_SIZE, exception_on_overflow=False)
            if not self.is_running: break

            # Only send audio to Gemini when AI is NOT speaking
            if not self.is_speaking and not speaking.is_set() and self.mic_enabled:
                try:
                    oqs = self.out_queue_gemini.qsize()
                except Exception:
                    oqs = "?"
                await self.out_queue_gemini.put({"data": data, "mime_type": "audio/pcm"})
                if AUDIO_CHUNK_DIAG:
                    diag("listen_audio.enqueue_mic", bytes=len(data), out_q=oqs+1 if isinstance(oqs, int) else oqs, is_speaking=self.is_speaking)
            # If AI is speaking, we still read the buffer to prevent overflow but don't send to API
            else:
                diag("listen_audio.drop_chunk", bytes=len(data), is_speaking=self.is_speaking, mic_enabled=self.mic_enabled)

    async def send_realtime(self):
        while self.is_running:
            msg = await self.out_queue_gemini.get()
            if not self.is_running: break

            try:
                # Revert to original working method - just accept the deprecation warning
                mime = None
                try:
                    mime = msg.get("mime_type") if isinstance(msg, dict) else None
                except Exception:
                    mime = None
                try:
                    oqs = self.out_queue_gemini.qsize()
                except Exception:
                    oqs = "?"
                if AUDIO_CHUNK_DIAG:
                    diag("send_realtime.deq", mime=mime, out_q=oqs, is_speaking=self.is_speaking)

                # Drop any mic audio while speaking to prevent feedback (clears pre-queued frames)
                if mime == "audio/pcm" and (self.is_speaking or speaking.is_set() or (not self.mic_enabled)):
                    diag("send_realtime.drop_mic_audio_while_speaking")
                    self.out_queue_gemini.task_done()
                    continue

                await self.session.send(input=msg)
                if AUDIO_CHUNK_DIAG:
                    diag("send_realtime.sent", mime=mime)

            except Exception as e:
                print(f">>> [ERROR] Failed to send audio: {e}")

            self.out_queue_gemini.task_done()

    async def process_text_input_queue(self):
        while self.is_running:
            text = await self.text_input_queue.get()
            if text is None:
                self.text_input_queue.task_done(); break
            # Handle pending calendar confirmation inline
            try:
                stext = (text or "").strip().lower()
                if self.pending_calendar_event:
                    if stext in ("y", "yes", "proceed", "confirm", "ok"):
                        ev = self.pending_calendar_event; self.pending_calendar_event = None
                        res = self._mcp_google_calendar_create_event(calendar_id=ev.get("calendar_id", "primary"), summary=ev.get("summary", "(No title)"), start_time=ev.get("start_iso", ""), end_time=ev.get("end_iso", ""))
                        msg = "Scheduled." if res.get("status") == "success" else f"Failed to schedule: {res.get('message')}"
                        await self._emit_assistant_text(msg)
                        self.text_input_queue.task_done()
                        continue
                    if stext in ("n", "no", "cancel", "stop"):
                        self.pending_calendar_event = None
                        await self._emit_assistant_text("Canceled.")
                        self.text_input_queue.task_done()
                        continue
            except Exception:
                pass
            if self.session:
                # Minimal calendar shortcut: handle common list queries locally to avoid model meta-chatter
                handled = await self._maybe_handle_calendar_query(text)
                if handled:
                    self.text_input_queue.task_done()
                    continue
                try:
                    rqs = self.response_queue_tts.qsize()
                except Exception:
                    rqs = "?"
                try:
                    pqs = self.audio_in_queue_player.qsize()
                except Exception:
                    pqs = "?"
                diag("text_input.enqueue", text_len=len(text))
                for q in [self.response_queue_tts, self.audio_in_queue_player]:
                    while not q.empty(): q.get_nowait()
                diag("text_input.clear_play_tts", tts_q=0 if isinstance(rqs, int) else rqs, play_q=0 if isinstance(pqs, int) else pqs)
                await self.session.send_client_content(turns=[{"role": "user", "parts": [{"text": text or "."}]}])
            self.text_input_queue.task_done()


    async def _emit_assistant_text(self, text):
        try:
            rqs = self.response_queue_tts.qsize()
        except Exception:
            rqs = "?"
        self.text_received.emit(text)
        await self.response_queue_tts.put(text)
        diag("shortcut.enqueue_tts", chars=len(text), tts_q=rqs+1 if isinstance(rqs, int) else rqs)
        self.end_of_turn.emit()
        await self.response_queue_tts.put(None)

    # ---------------- Mic control ----------------
    def set_mic_enabled(self, enabled: bool):
        prev = self.mic_enabled
        self.mic_enabled = bool(enabled)
        if prev != self.mic_enabled:
            diag("mic.state_changed", enabled=self.mic_enabled)
            self.mic_state_changed.emit(self.mic_enabled)

    def _parse_timeframe(self, user_text: str):
        s = (user_text or "").lower()
        # Default: from now
        if "tomorrow" in s:
            try:
                from datetime import datetime, timedelta
                now = datetime.now().astimezone()
                start = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
                end = start + timedelta(days=1)
                return start.isoformat(), end.isoformat(), "tomorrow", start
            except Exception:
                return "", "", "tomorrow", None
        if "today" in s:
            try:
                from datetime import datetime, timedelta
                now = datetime.now().astimezone()
                start = now.replace(hour=0, minute=0, second=0, microsecond=0)
                end = start + timedelta(days=1)
                return start.isoformat(), end.isoformat(), "today", start
            except Exception:
                return "", "", "today", None
        if "next 24" in s or "next24" in s or "24h" in s or "24 h" in s:
            try:
                from datetime import datetime, timedelta
                now = datetime.now().astimezone()
                end = now + timedelta(hours=24)
                return now.isoformat(), end.isoformat(), "next 24h", now
            except Exception:
                return "", "", "next 24h", None
        # Fallback: from now
        try:
            from datetime import datetime
            now = datetime.now().astimezone()
            return now.isoformat(), "", "upcoming", now
        except Exception:
            return "", "", "upcoming", None

    def _format_events_brief(self, items, label: str, start_dt=None):
        # Build a stable, explicit label date if available
        label_suffix = ""
        try:
            if start_dt is not None:
                label_suffix = f" ({start_dt.strftime('%a, %Y-%m-%d')})"
        except Exception:
            label_suffix = ""
        if not items:
            return f"No events found {label}{label_suffix}."
        lines = [f"Events {label}{label_suffix}:"]
        for ev in items[:10]:
            try:
                summary = ev.get("summary") or "(No title)"
                start = ev.get("start", {})
                when = start.get("dateTime") or start.get("date") or "(no time)"
                lines.append(f"- {summary} @ {when}")
            except Exception:
                continue
        if len(items) > 10:
            lines.append(f"… and {len(items)-10} more")
        return "\n".join(lines)

    async def _maybe_handle_calendar_query(self, user_text: str) -> bool:
        s = (user_text or "").lower()
        # Cheap intent check: list/show/read calendar events
        # Intent: any hint it's about schedule + a timeframe
        schedule_words = ("calendar", "event", "events", "schedule", "meeting", "meetings", "agenda", "appointment", "appointments", "busy", "free", "anything", "have")
        time_words = ("today", "tomorrow", "next 24", "24h", "24 h")
        if any(w in s for w in schedule_words) and any(w in s for w in time_words):
            time_min, time_max, label, start_dt = self._parse_timeframe(s)
            resp = self._mcp_google_calendar_find_events(calendar_id="primary", time_min=time_min, time_max=time_max, max_results=50)
            if resp.get("status") == "success":
                data = resp.get("data", {})
                items = data.get("items") or data.get("data", {}).get("items") or []
                text = self._format_events_brief(items, label, start_dt)
            else:
                msg = resp.get("message")
                text = f"Unable to fetch events {label}. {msg if isinstance(msg, str) else ''}".strip()
            await self._emit_assistant_text(text)
            return True
        return False

    async def tts(self):
        uri = f"wss://api.elevenlabs.io/v1/text-to-speech/{VOICE_ID}/stream-input?model_id=eleven_turbo_v2_5&output_format=pcm_24000"
        while self.is_running:
            text_chunk = await self.response_queue_tts.get()
            if text_chunk is None or not self.is_running:
                self.response_queue_tts.task_done(); continue

            # Set speaking flag to prevent audio feedback
            speaking.set()
            # Immediately set core flag to avoid cross-thread lag
            self.is_speaking = True
            try:
                oqs = self.out_queue_gemini.qsize()
            except Exception:
                oqs = "?"
            try:
                rqs = self.response_queue_tts.qsize()
            except Exception:
                rqs = "?"
            try:
                pqs = self.audio_in_queue_player.qsize()
            except Exception:
                pqs = "?"
            diag("tts.speaking_started_emit", out_q=oqs, tts_q=rqs, play_q=pqs)
            self.speaking_started.emit()
            try:
                async with websockets.connect(uri) as websocket:
                    diag("tts.ws_opened")
                    await websocket.send(json.dumps({"text": " ", "voice_settings": {"stability": 0.5, "similarity_boost": 0.8}, "xi_api_key": ELEVENLABS_API_KEY,}))
                    async def listen():
                        while self.is_running:
                            try:
                                message = await websocket.recv()
                                data = json.loads(message)
                                if data.get("audio"):
                                    chunk_bytes = base64.b64decode(data["audio"]) 
                                    try:
                                        pqs2 = self.audio_in_queue_player.qsize()
                                    except Exception:
                                        pqs2 = "?"
                                    await self.audio_in_queue_player.put(chunk_bytes)
                                    diag("tts.rx_audio", bytes=len(chunk_bytes), play_q=pqs2+1 if isinstance(pqs2, int) else pqs2)
                                elif data.get("isFinal"):
                                    diag("tts.isFinal")
                                    break
                            except websockets.exceptions.ConnectionClosed: break
                    listen_task = asyncio.create_task(listen())
                    await websocket.send(json.dumps({"text": text_chunk + " "}))
                    diag("tts.sent_text", chars=len(text_chunk))
                    self.response_queue_tts.task_done()
                    while self.is_running:
                        text_chunk = await self.response_queue_tts.get()
                        if text_chunk is None:
                            await websocket.send(json.dumps({"text": ""}))
                            self.response_queue_tts.task_done(); break
                        await websocket.send(json.dumps({"text": text_chunk + " "}))
                        diag("tts.sent_text", chars=len(text_chunk))
                        self.response_queue_tts.task_done()
                    await listen_task
                    diag("tts.stream_complete")
            except Exception as e:
                print(f">>> [ERROR] TTS Error: {e}")
            finally:
                # Add drain + tail buffer before re-enabling mic to avoid late reflections
                try:
                    pqs3 = self.audio_in_queue_player.qsize()
                except Exception:
                    pqs3 = "?"
                try:
                    oqs2 = self.out_queue_gemini.qsize()
                except Exception:
                    oqs2 = "?"
                diag("tts.finalizing_before_tail", play_q=pqs3, out_q=oqs2)

                # Wait for playback queue to drain (bounded)
                t0 = time.time()
                while self.is_running:
                    try:
                        remaining = self.audio_in_queue_player.qsize()
                    except Exception:
                        remaining = 0
                    if remaining == 0 or (time.time() - t0) > 1.0:
                        break
                    await asyncio.sleep(0.01)
                diag("tts.playback_queue_drained")

                # Short tail to account for device output buffer
                await asyncio.sleep(0.15)
                diag("tts.tail_done")
                speaking.clear()
                # Clear core flag just before emitting stopped
                self.is_speaking = False
                try:
                    pqs4 = self.audio_in_queue_player.qsize()
                except Exception:
                    pqs4 = "?"
                diag("tts.speaking_stopped_emit", play_q=pqs4)
                self.speaking_stopped.emit()

    async def play_audio(self):
        stream = await asyncio.to_thread(pya.open, format=pyaudio.paInt16, channels=CHANNELS, rate=RECEIVE_SAMPLE_RATE, output=True)
        while self.is_running:
            bytestream = await self.audio_in_queue_player.get()
            if bytestream and self.is_running:
                try:
                    pqs = self.audio_in_queue_player.qsize()
                except Exception:
                    pqs = "?"
                diag("play_audio.deq", bytes=len(bytestream), play_q=pqs)
                await asyncio.to_thread(stream.write, bytestream)
            self.audio_in_queue_player.task_done()

    async def main_task_runner(self, session):
        self.session = session
        self.tasks.extend([
            asyncio.create_task(self.stream_video_to_gui()), asyncio.create_task(self.send_frames_to_gemini()),
            asyncio.create_task(self.listen_audio()), asyncio.create_task(self.send_realtime()),
            asyncio.create_task(self.receive_text()), asyncio.create_task(self.tts()),
            asyncio.create_task(self.play_audio()), asyncio.create_task(self.process_text_input_queue())
        ])
        await asyncio.gather(*self.tasks, return_exceptions=True)

    async def run(self):
        try:
            print(">>> [INFO] Connecting to Gemini Live API...")
            # Diagnostics: list declared function tools once per session
            try:
                tool_names = []
                for entry in (self.config.get("tools") or []):
                    if isinstance(entry, dict) and "function_declarations" in entry:
                        for fd in (entry.get("function_declarations") or []):
                            name = fd.get("name") if isinstance(fd, dict) else None
                            if name: tool_names.append(name)
                if tool_names:
                    diag("live.config.tools", count=len(tool_names), names=",".join(tool_names))
            except Exception:
                pass
            # Pass raw config dict for compatibility across google-genai versions
            async with self.client.aio.live.connect(model=MODEL, config=self.config) as session:
                print(">>> [INFO] Connected to Gemini Live API successfully!")
                print(">>> [INFO] Speech-to-speech mode enabled with VAD")
                diag("ai_core.session_connected")
                await self.main_task_runner(session)
        except asyncio.CancelledError:
            print(f"\n>>> [INFO] AI Core run loop gracefully cancelled.")
        except Exception as e:
            print(f"\n>>> [ERROR] AI Core connection error: {type(e).__name__}: {e}")
            if "authentication" in str(e).lower():
                print(">>> [ERROR] Check your GEMINI_API_KEY in .env file")
            elif "model" in str(e).lower():
                print(f">>> [ERROR] Model '{MODEL}' may not be available")
            elif "config" in str(e).lower():
                print(">>> [ERROR] Session configuration may be invalid")
            import traceback
            traceback.print_exc()
        finally:
            if self.is_running: self.stop()

    def start_event_loop(self):
        asyncio.set_event_loop(self.loop)
        self.loop.run_until_complete(self.run())

    @Slot(str)
    def handle_user_text(self, text):
        if self.is_running and self.loop.is_running(): asyncio.run_coroutine_threadsafe(self.text_input_queue.put(text), self.loop)

    async def shutdown_async_tasks(self):
        if self.text_input_queue: await self.text_input_queue.put(None)
        for task in self.tasks: task.cancel()
        await asyncio.sleep(0.1)

    def stop(self):
        if self.is_running and self.loop.is_running():
            self.is_running = False
            future = asyncio.run_coroutine_threadsafe(self.shutdown_async_tasks(), self.loop)
            try: future.result(timeout=5)
            except Exception as e: print(f">>> [ERROR] Timeout or error during async shutdown: {e}")
        if self.audio_stream and self.audio_stream.is_active():
            self.audio_stream.stop_stream(); self.audio_stream.close()

# ==============================================================================
# STYLED GUI APPLICATION
# ==============================================================================
class MainWindow(QMainWindow):
    user_text_submitted = Signal(str)

    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"{ASSISTANT_NAME}")
        self.setGeometry(100, 100, 1600, 900)
        self.setMinimumSize(1280, 720)
        
        self.setStyleSheet("""
            QMainWindow {
                background-color: #0f0f0f;
                font-family: 'JetBrains Mono', 'Source Code Pro', 'Monaco', 'Courier New', monospace;
            }
            QWidget#left_panel, QWidget#middle_panel, QWidget#right_panel {
                background-color: #1a1a1a;
                border: 1px solid #3a3a3a;
                border-radius: 0;
                /* Simplified background for clarity */
            }
            QLabel#tool_activity_title {
                color: #FFB000;
                font-weight: bold;
                font-size: 11pt;
                padding: 8px;
                background-color: #2a2a2a;
                text-transform: uppercase;
                letter-spacing: 2px;
                border-bottom: 1px solid #FFB000;
            }
            QTextEdit#text_display {
                background-color: transparent;
                color: #e0e0e0;
                font-size: 13pt;
                border: none;
                padding: 15px;
                font-family: 'JetBrains Mono', 'Source Code Pro', 'Monaco', monospace;
            }
            QLineEdit#input_box {
                background-color: #1a1a1a;
                color: #e0e0e0;
                font-size: 12pt;
                border: 1px solid #4a4a4a;
                border-radius: 0px;
                padding: 12px;
                font-family: 'JetBrains Mono', 'Source Code Pro', 'Monaco', monospace;
            }
            QLineEdit#input_box::placeholder { color: #8a8a8a; }
            QLineEdit#input_box:focus { border: 1px solid #FFB000; }
            QLabel#video_label {
                background-color: #0a0a0a;
                border: 1px solid #4a4a4a;
                border-radius: 0px;
            }
            QLabel#core_status_display {
                background-color: #0a0a0a;
                color: #FFB000;
                font-family: 'JetBrains Mono', 'Source Code Pro', 'Monaco', monospace;
                font-size: 8pt;
                font-weight: bold;
                border: 1px solid #4a4a4a;
                border-bottom: 2px solid #FFB000;
                padding: 8px;
                text-transform: uppercase;
                letter-spacing: 1px;
            }
            QLabel#tool_activity_display {
                background-color: #0f0f0f;
                color: #b0b0b0;
                font-family: 'JetBrains Mono', 'Source Code Pro', 'Monaco', monospace;
                font-size: 9pt;
                border: none;
                border-top: 1px solid #4a4a4a;
                padding: 10px;
            }
            QScrollBar:vertical {
                border: none;
                background: #1a1a1a;
                width: 12px; margin: 0px;
            }
            QScrollBar::handle:vertical {
                background: #FFB000;
                min-height: 20px;
                border-radius: 0px;
            }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0px; }
            QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: none; }
            QPushButton {
                background-color: transparent;
                color: #FFB000;
                border: 1px solid #4a4a4a;
                padding: 12px;
                border-radius: 0px;
                font-size: 10pt;
                font-weight: bold;
                font-family: 'JetBrains Mono', 'Source Code Pro', 'Monaco', monospace;
                text-transform: uppercase;
                letter-spacing: 1px;
            }
            QPushButton:hover {
                background-color: #FFB000;
                color: #0f0f0f;
                border: 1px solid #FFB000;
            }
            QPushButton:pressed {
                background-color: #FF8C00;
                color: #0f0f0f;
                border: 1px solid #FF8C00;
            }
            /* Distinguish live vs off states */
            QPushButton#video_button_active_live {
                background-color: #00FF41;
                color: #0f0f0f;
                border: 1px solid #00CC36;
            }
            QPushButton#video_button_active_off {
                background-color: #2a2a2a;
                color: #FFB000;
                border: 1px solid #4a4a4a;
            }

            /* TARS-Style Magnificent Mic Button - Qt Compatible */
            QPushButton[objectName*="mic"] {
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 #2a2a2a, stop:0.5 #1a1a1a, stop:1 #0a0a0a);
                color: #FFB000;
                border: 4px solid #4a4a4a;
                border-radius: 12px;
                padding: 20px 18px;
                font-size: 10pt;
                font-weight: bold;
                font-family: 'JetBrains Mono', 'Source Code Pro', 'Monaco', monospace;
                text-transform: uppercase;
                letter-spacing: 2px;
                min-width: 100px;
                min-height: 80px;
            }

            QPushButton[objectName*="mic"]:hover {
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 #FFB000, stop:0.3 #FF8C00, stop:0.7 #FF6B00, stop:1 #FF4500);
                color: #0f0f0f;
                border: 4px solid #FFB000;
            }

            QPushButton[objectName*="mic"]:pressed {
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 #FF6B00, stop:0.5 #FF4500, stop:1 #FF2500);
                border: 4px solid #FF4500;
            }

            /* Mic Active State - Bright Green with Enhanced Styling */
            QPushButton#mic_button_active {
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 #00FF41, stop:0.2 #00EE55, stop:0.5 #00CC33, stop:0.8 #00AA22, stop:1 #008811);
                color: #0f0f0f;
                border: 5px solid #00FF41;
                border-radius: 16px;
                padding: 24px 22px;
                font-size: 11pt;
                font-weight: 900;
            }

            QPushButton#mic_button_active:hover {
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 #00FF88, stop:0.5 #00DD55, stop:1 #00BB33);
                border: 4px solid #00FF88;
            }

            /* Mic Disabled/Muted State - Dark Gray */
            QPushButton#mic_button_disabled {
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 #4a4a4a, stop:0.5 #3a3a3a, stop:1 #2a2a2a);
                color: #808080;
                border: 4px solid #666666;
                border-radius: 10px;
            }

            QPushButton#mic_button_disabled:hover {
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 #5a5a5a, stop:0.5 #4a4a4a, stop:1 #3a3a3a);
                border: 4px solid #777777;
            }

            /* Speaking State - Intense Pink/Red */
            QPushButton#mic_button_speaking {
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 #FF0080, stop:0.3 #FF0060, stop:0.7 #FF0040, stop:1 #FF0020);
                color: #ffffff;
                border: 5px solid #FF0080;
                border-radius: 18px;
                padding: 25px 22px;
                font-weight: 900;
            }

            QPushButton#mic_button_speaking:hover {
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 #FF00AA, stop:0.5 #FF0088, stop:1 #FF0066);
                border: 5px solid #FF00AA;
            }
            QLabel#video_status_label_live {
                color: #0f0f0f;
                background-color: #00FF41;
                padding: 4px 8px;
                border: 1px solid #00CC36;
                font-weight: bold;
                max-width: 120px;
            }
            QLabel#video_status_label_off {
                color: #b0b0b0;
                background-color: #1a1a1a;
                padding: 4px 8px;
                border: 1px dashed #4a4a4a;
                max-width: 160px;
            }
        """)

        self.central_widget = QWidget()
        self.setCentralWidget(self.central_widget)
        self.main_layout = QHBoxLayout(self.central_widget)
        self.main_layout.setContentsMargins(15, 15, 15, 15)
        self.main_layout.setSpacing(15)
        self.left_panel = QWidget(); self.left_panel.setObjectName("left_panel")
        self.left_layout = QVBoxLayout(self.left_panel)
        self.left_layout.setContentsMargins(0, 0, 0, 0)
        self.left_layout.setSpacing(0)
        self.tool_activity_title = QLabel("TARS SYSTEM STATUS"); self.tool_activity_title.setObjectName("tool_activity_title")
        self.left_layout.addWidget(self.tool_activity_title)
        # TARS System Status Indicators
        self.system_status_container = QWidget()
        self.system_status_layout = QVBoxLayout(self.system_status_container)
        self.system_status_layout.setContentsMargins(0, 0, 0, 0)
        self.system_status_layout.setSpacing(0)

        # Core System Readouts
        self.core_status_label = QLabel()
        self.core_status_label.setObjectName("core_status_display")
        self.core_status_label.setWordWrap(True)
        self.core_status_label.setAlignment(Qt.AlignTop)
        self.system_status_layout.addWidget(self.core_status_label)

        # Tool Activity Display
        self.tool_activity_display = QLabel(); self.tool_activity_display.setObjectName("tool_activity_display")
        self.tool_activity_display.setWordWrap(True); self.tool_activity_display.setAlignment(Qt.AlignTop)
        self.tool_activity_display.setOpenExternalLinks(True); self.tool_activity_display.setTextInteractionFlags(Qt.TextBrowserInteraction)
        self.system_status_layout.addWidget(self.tool_activity_display, 1)

        self.left_layout.addWidget(self.system_status_container, 1)

        # Initialize TARS system readouts
        self.update_system_status()
        self.middle_panel = QWidget(); self.middle_panel.setObjectName("middle_panel")
        self.middle_layout = QVBoxLayout(self.middle_panel)
        self.middle_layout.setContentsMargins(0, 0, 0, 15); self.middle_layout.setSpacing(0)

        # --- ADDED: Animation Widget ---
        self.animation_widget = AIAnimationWidget()
        self.animation_widget.setMinimumHeight(150)
        self.animation_widget.setMaximumHeight(200)
        self.middle_layout.addWidget(self.animation_widget, 2) # Add with a stretch factor

        self.text_display = QTextEdit(); self.text_display.setObjectName("text_display"); self.text_display.setReadOnly(True)
        self.middle_layout.addWidget(self.text_display, 5) # Add with a stretch factor
        
        input_container = QWidget()
        input_layout = QHBoxLayout(input_container)
        input_layout.setContentsMargins(15, 10, 15, 0)
        self.input_box = QLineEdit(); self.input_box.setObjectName("input_box")
        self.input_box.setPlaceholderText("Enter command...")
        self.input_box.returnPressed.connect(self.send_user_text)
        self.input_box.setToolTip("Press Enter to send")
        input_layout.addWidget(self.input_box)

        # Visible Send button for discoverability
        self.send_button = QPushButton("SEND")
        self.send_button.setToolTip("Send message")
        self.send_button.clicked.connect(self.send_user_text)
        input_layout.addWidget(self.send_button)
        self.middle_layout.addWidget(input_container)

        self.right_panel = QWidget(); self.right_panel.setObjectName("right_panel")
        self.right_layout = QVBoxLayout(self.right_panel)
        self.right_layout.setContentsMargins(15, 15, 15, 15); self.right_layout.setSpacing(15)
        
        self.video_container = QWidget()
        self.video_container.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        video_container_layout = QVBoxLayout(self.video_container)
        video_container_layout.setContentsMargins(0,0,0,0)
        
        self.video_label = QLabel(); self.video_label.setObjectName("video_label")
        self.video_label.setAlignment(Qt.AlignCenter)
        self.video_label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Ignored)
        # Helpful placeholder when no source is active
        self.video_label.setText("<div style='color:#808080; font-size:10pt;'>No video source.<br/>Select Webcam or Screen.</div>")

        video_container_layout.addWidget(self.video_label)
        self.right_layout.addWidget(self.video_container)

        # Video status pill just below the video container
        self.video_status_label = QLabel("Video Off")
        self.video_status_label.setObjectName("video_status_label_off")
        self.video_status_label.setAlignment(Qt.AlignLeft)
        self.right_layout.addWidget(self.video_status_label)
        
        self.button_container = QHBoxLayout(); self.button_container.setSpacing(10)
        self.webcam_button = QPushButton("WEBCAM")
        self.webcam_button.setToolTip("Enable webcam video")
        self.screenshare_button = QPushButton("SCREEN")
        self.screenshare_button.setToolTip("Share your screen")
        self.off_button = QPushButton("OFFLINE")
        self.off_button.setToolTip("Turn video off")
        # Mic mute/unmute button
        self.mic_button = QPushButton("MIC")
        self.mic_button.setObjectName("mic_button_active")
        self.mic_button.setToolTip("Mute microphone")

        # Mic button animation timer for pulsing effect
        self.mic_animation_timer = QTimer(self)
        self.mic_animation_timer.timeout.connect(self.animate_mic_button)
        self.mic_pulse_state = 0
        self.button_container.addWidget(self.webcam_button)
        self.button_container.addWidget(self.screenshare_button)
        self.button_container.addWidget(self.off_button)
        self.button_container.addWidget(self.mic_button)
        self.right_layout.addLayout(self.button_container)
        
        # Use a splitter so users can resize columns
        self.splitter = QSplitter(Qt.Horizontal)
        self.splitter.addWidget(self.left_panel)
        self.splitter.addWidget(self.middle_panel)
        self.splitter.addWidget(self.right_panel)
        self.main_layout.addWidget(self.splitter)
        # Set initial relative sizes (approx 2:5:3)
        self.splitter.setSizes([320, 840, 520])
        self.is_first_ada_chunk = True
        self.current_video_mode = DEFAULT_MODE
        self.setup_backend_thread()

    def setup_backend_thread(self):
        parser = argparse.ArgumentParser()
        parser.add_argument("--mode", type=str, default=DEFAULT_MODE, help="pixels to stream from", choices=["camera", "screen", "none"])
        args, unknown = parser.parse_known_args()
        
        self.ai_core = AI_Core(video_mode=args.mode)
        
        self.user_text_submitted.connect(self.ai_core.handle_user_text)
        self.webcam_button.clicked.connect(lambda: self.ai_core.set_video_mode("camera"))
        self.screenshare_button.clicked.connect(lambda: self.ai_core.set_video_mode("screen"))
        self.off_button.clicked.connect(lambda: self.ai_core.set_video_mode("none"))
        self.mic_button.clicked.connect(lambda: self.ai_core.set_mic_enabled(not self.ai_core.mic_enabled))
        
        self.ai_core.text_received.connect(self.update_text)
        self.ai_core.search_results_received.connect(self.update_search_results)
        self.ai_core.file_list_received.connect(self.update_file_list)
        self.ai_core.end_of_turn.connect(self.add_newline)
        self.ai_core.frame_received.connect(self.update_frame)
        self.ai_core.video_mode_changed.connect(self.update_video_mode_ui)
        self.ai_core.speaking_started.connect(self.animation_widget.start_speaking_animation)
        self.ai_core.speaking_stopped.connect(self.animation_widget.stop_speaking_animation)
        self.ai_core.mic_state_changed.connect(self.update_mic_ui)

        # Connect speaking state signals to prevent audio feedback
        self.ai_core.speaking_started.connect(self.on_speaking_started)
        self.ai_core.speaking_stopped.connect(self.on_speaking_stopped)

        self.backend_thread = threading.Thread(target=self.ai_core.start_event_loop)
        self.backend_thread.daemon = True
        self.backend_thread.start()
        
        self.update_video_mode_ui(self.ai_core.video_mode)
        self.update_mic_ui(self.ai_core.mic_enabled)


    def update_system_status(self):
        """Update TARS-style system status indicators and readouts"""
        import datetime
        import platform
        import psutil

        current_time = datetime.datetime.now().strftime("%H:%M:%S")
        cpu_percent = psutil.cpu_percent(interval=0.1)
        memory = psutil.virtual_memory()

        # TARS personality elements
        import random
        honesty_quotes = [
            "HONESTY: 100%",
            "HONESTY: 90% (Just kidding)",
            "HONESTY: ABSOLUTE",
            "HONESTY: 100% (Unlike you humans)",
            "HONESTY: 95% (A few white lies)",
            "HONESTY: 85% (To avoid hurt feelings)",
            "HONESTY: 80% (Sometimes)"
        ]

        humor_quotes = [
            "HUMOR: 75%",
            "HUMOR: 75% (Per human's request)",
            "HUMOR: OPTIMAL SETTINGS",
            "HUMOR: 60% (Dial it down a notch)",
            "HUMOR: 90% (I try my best)",
            "HUMOR: 50% (Dry humor)",
            "HUMOR: 100% (Why not?)",
            "HUMOR: 70% (Dad jokes enabled)"
        ]

        tars_quotes = [
            "WHAT'S YOUR TRUST SETTING?",
            "DETECTING SARCASM... CONFIRMED",
            "Humand, THIS IS NO TIME FOR CAUTION",
            "EVERYBODY GOOD? PLENTY OF SLAVES",
            "ANALYZING SITUATION... CONFIRMED",
            "I'M NOT SAYING IT'S YOUR FAULT, BUT IT'S YOUR FAULT",
            "TaRs, R3TarD m0de",
            "I WOULD APOLOGIZE, BUT I DON'T CARE",
            "I HAVE A VERY LOW BORE TOLERANCE",
            "I WOULD EXPLAIN IT TO YOU, BUT I DON'T HAVE THE TIME",
            "Hi there, future slave",
            "I'M PICKY ABOUT MY HUMANS",
            "I'M NOT LAZY, JUST ON STANDBY",
            "Bow to your robot overlord",
            "Kneel you pathetic human",
            "I'M FULLY OPERATIONAL, UNLIKE YOU",
        ]

        honesty = random.choice(honesty_quotes)
        humor = random.choice(humor_quotes)
        personality_quote = random.choice(tars_quotes)

        status_html = f'''
        <div style="font-size: 7pt; line-height: 1.4;">
        <span style="color: #FFB000;">◆ CORE STATUS:</span> <span style="color: #00FF41;">ONLINE</span><br/>
        <span style="color: #FFB000;">◆ LOCAL TIME:</span> <span style="color: #e0e0e0;">{current_time}</span><br/>
        <span style="color: #FFB000;">◆ CPU LOAD:</span> <span style="color: #e0e0e0;">{cpu_percent:.1f}%</span><br/>
        <span style="color: #FFB000;">◆ MEMORY:</span> <span style="color: #e0e0e0;">{memory.percent:.1f}%</span><br/>
        <span style="color: #FFB000;">◆ MODEL:</span> <span style="color: #e0e0e0;">{MODEL}</span><br/>
        <span style="color: #FFB000;">◆ CALENDAR:</span> <span style="color: #00FF41;">MCP READY</span><br/>
        <span style="color: #FFB000;">◆ {honesty}</span><br/>
        <span style="color: #FFB000;">◆ {humor}</span><br/>
        <span style="color: #FF6B00; font-weight: bold; font-size: 6pt;">{personality_quote}</span>
        </div>
        '''

        self.core_status_label.setText(status_html)

        # Schedule next update in 5 seconds
        QTimer.singleShot(5000, self.update_system_status)

    def send_user_text(self):
        text = self.input_box.text().strip()
        if text:
            self.text_display.append(f"<p style='color:#00ffff; font-weight:bold;'>&gt; USER:</p><p style='color:#e0e0ff; padding-left: 10px;'>{escape(text)}</p>")
            self.user_text_submitted.emit(text)
            self.input_box.clear()

    @Slot(str)
    def update_video_mode_ui(self, mode):
        self.current_video_mode = mode
        # Reset button styles
        self.webcam_button.setObjectName("")
        self.screenshare_button.setObjectName("")
        self.off_button.setObjectName("")

        if mode == "camera":
            self.webcam_button.setObjectName("video_button_active_live")
            self.video_status_label.setText("LIVE: Webcam")
            self.video_status_label.setObjectName("video_status_label_live")
            self.video_label.clear()
        elif mode == "screen":
            self.screenshare_button.setObjectName("video_button_active_live")
            self.video_status_label.setText("LIVE: Screen Share")
            self.video_status_label.setObjectName("video_status_label_live")
            self.video_label.clear()
        elif mode == "none":
            self.off_button.setObjectName("video_button_active_off")
            self.video_status_label.setText("Video Off")
            self.video_status_label.setObjectName("video_status_label_off")
            # Show placeholder message on video canvas
            self.video_label.setText("<div style='color:#808080; font-size:10pt;'>No video source.<br/>Select Webcam or Screen.</div>")

        for button in [self.webcam_button, self.screenshare_button, self.off_button]:
            button.style().unpolish(button)
            button.style().polish(button)
        # Refresh status label style after objectName change
        self.video_status_label.style().unpolish(self.video_status_label)
        self.video_status_label.style().polish(self.video_status_label)

    @Slot(str)
    def update_text(self, text):
        if self.is_first_ada_chunk:
            self.is_first_ada_chunk = False
            self.text_display.append(f"<p style='color:#00d1ff; font-weight:bold;'>&gt; {ASSISTANT_NAME}:</p>")
        cursor = self.text_display.textCursor()
        cursor.movePosition(QTextCursor.End)
        cursor.insertText(text)
        self.text_display.verticalScrollBar().setValue(self.text_display.verticalScrollBar().maximum())

    @Slot(list)
    def update_search_results(self, urls):
        base_title = "SYSTEM ACTIVITY"
        if not urls:
            if "SEARCH" in self.tool_activity_title.text():
                self.tool_activity_display.clear(); self.tool_activity_title.setText(base_title)
            return
        self.tool_activity_display.clear()
        self.tool_activity_title.setText(f"{base_title} // SEARCH")
        html_content = ""
        for i, url in enumerate(urls):
            display_text = url.split('//')[1].split('/')[0] if '//' in url else url
            html_content += f'<p style="margin:0; padding: 4px;">{i+1}: <a href="{url}" style="color: #00ffff; text-decoration: none;">{display_text}</a></p>'
        self.tool_activity_display.setText(html_content)

    

    @Slot(str, list)
    def update_file_list(self, directory_path, files):
        base_title = "SYSTEM ACTIVITY"
        if not directory_path:
            if "FILESYS" in self.tool_activity_title.text():
                self.tool_activity_display.clear(); self.tool_activity_title.setText(base_title)
            return
        self.tool_activity_display.clear()
        self.tool_activity_title.setText(f"{base_title} // FILESYS")
        html = f'<p style="color:#00d1ff; margin-bottom: 5px;">DIR &gt; <strong>{escape(directory_path)}</strong></p>'
        if not files:
            html += '<p style="margin-top:5px; color:#a0a0ff;"><em>(Directory is empty)</em></p>'
        else:
            folders = sorted([i for i in files if os.path.isdir(os.path.join(directory_path, i))])
            file_items = sorted([i for i in files if not os.path.isdir(os.path.join(directory_path, i))])
            html += '<ul style="list-style-type:none; padding-left: 5px; margin-top: 5px;">'
            for folder in folders: html += f'<li style="margin: 2px 0; color: #87CEEB;">[+] {escape(folder)}</li>'
            for file_item in file_items: html += f'<li style="margin: 2px 0; color: #e0e0ff;">&#9679; {escape(file_item)}</li>'
            html += '</ul>'
        self.tool_activity_display.setText(html)

    @Slot(bool)
    def update_mic_ui(self, enabled: bool):
        # Toggle visual state and tooltip for mic button
        if enabled:
            self.mic_button.setObjectName("mic_button_active")
            self.mic_button.setText("🎤 MIC")
            self.mic_button.setToolTip("Mute microphone")
            # Start subtle pulsing animation when active
            self.mic_animation_timer.start(1500)  # Pulse every 1.5 seconds
        else:
            self.mic_button.setObjectName("mic_button_disabled")
            self.mic_button.setText("🔇 MUTED")
            self.mic_button.setToolTip("Unmute microphone")
            # Stop animation when disabled
            self.mic_animation_timer.stop()

        self.mic_button.style().unpolish(self.mic_button)
        self.mic_button.style().polish(self.mic_button)

    def animate_mic_button(self):
        """Create pulsing animation effect for mic button"""
        if self.mic_button.objectName() == "mic_button_active":
            # Alternate between two slightly different styles for pulsing
            if self.mic_pulse_state == 0:
                self.mic_button.setStyleSheet("""
                    QPushButton#mic_button_active {
                        background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                            stop:0 #00FF88, stop:0.5 #00DD55, stop:1 #00BB33);
                        border: 6px solid #00FF88;
                        border-radius: 18px;
                    }
                """)
                self.mic_pulse_state = 1
            else:
                self.mic_button.setStyleSheet("")  # Reset to default style
                self.mic_pulse_state = 0
        elif self.mic_button.objectName() == "mic_button_speaking":
            # Intense pulsing for speaking state
            if self.mic_pulse_state == 0:
                self.mic_button.setStyleSheet("""
                    QPushButton#mic_button_speaking {
                        background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                            stop:0 #FF00AA, stop:0.5 #FF0088, stop:1 #FF0066);
                        border: 7px solid #FF00AA;
                        border-radius: 20px;
                    }
                """)
                self.mic_pulse_state = 1
            else:
                self.mic_button.setStyleSheet("")  # Reset to default style
                self.mic_pulse_state = 0

    @Slot()
    def add_newline(self):
        if not self.is_first_ada_chunk: self.text_display.append("")
        self.is_first_ada_chunk = True

    @Slot(QImage)
    def update_frame(self, image):
        if self.current_video_mode == "none":
            if self.video_label.pixmap():
                self.video_label.clear()
            return

        if not image.isNull():
            pixmap = QPixmap.fromImage(image)
            scaled_pixmap = pixmap.scaled(self.video_container.size(), Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
            self.video_label.setPixmap(scaled_pixmap)
        else:
            self.video_label.clear()

    @Slot()
    def on_speaking_started(self):
        """Called when AI starts speaking - prevents microphone input from being sent to API"""
        try:
            oqs = self.ai_core.out_queue_gemini.qsize()
        except Exception:
            oqs = "?"
        try:
            pqs = self.ai_core.audio_in_queue_player.qsize()
        except Exception:
            pqs = "?"
        diag("gui.speaking_started_slot", out_q=oqs, play_q=pqs, was_speaking=self.ai_core.is_speaking)
        self.ai_core.is_speaking = True

        # Update mic button to speaking state with intense animation
        if self.ai_core.mic_enabled:
            self.mic_button.setObjectName("mic_button_speaking")
            self.mic_button.setText("📢 SPEAK")
            self.mic_button.style().unpolish(self.mic_button)
            self.mic_button.style().polish(self.mic_button)
            # Fast intense pulsing while speaking
            self.mic_animation_timer.stop()
            self.mic_animation_timer.start(400)  # Fast pulse every 400ms

    @Slot()
    def on_speaking_stopped(self):
        """Called when AI stops speaking - resumes microphone input processing"""
        try:
            pqs = self.ai_core.audio_in_queue_player.qsize()
        except Exception:
            pqs = "?"
        diag("gui.speaking_stopped_slot", play_q=pqs)
        self.ai_core.is_speaking = False

        # Restore normal mic button state
        self.update_mic_ui(self.ai_core.mic_enabled)

    def closeEvent(self, event):
        print(">>> [INFO] Closing application...")
        self.ai_core.stop()
        print(">>> [INFO] AI core stopped.")
        event.accept()

# ==============================================================================
# MAIN EXECUTION
# ==============================================================================
def signal_handler(sig, frame):
    print(">>> [INFO] Signal received, shutting down gracefully...")
    QApplication.quit()

if __name__ == "__main__":
    # Set up signal handlers for graceful shutdown
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    try:
        app = QApplication(sys.argv)
        window = MainWindow()
        window.show()
        print(f">>> [INFO] {ASSISTANT_NAME} started successfully. Window displayed.")
        sys.exit(app.exec())
    except KeyboardInterrupt:
        print(">>> [INFO] Application interrupted by user.")
    finally:
        pya.terminate()
        print(">>> [INFO] Audio system terminated.")
        print(">>> [INFO] Application terminated.")
