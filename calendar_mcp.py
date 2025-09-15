#!/usr/bin/env python3
"""
Google Calendar MCP Server
Provides calendar management tools for A.D.A. using Model Context Protocol
"""

import json
import os
import sys
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

import mcp.server.stdio
import mcp.types as types
from mcp.server import NotificationOptions, Server
from mcp.server.models import InitializationOptions

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

SCOPES = ['https://www.googleapis.com/auth/calendar']

class GoogleCalendarMCP:
    def __init__(self):
        self.service = None
        self.credentials_file = 'credentials.json'
        self.token_file = 'token.json'

    def authenticate(self):
        """Authenticate with Google Calendar API"""
        creds = None

        if os.path.exists(self.token_file):
            creds = Credentials.from_authorized_user_file(self.token_file, SCOPES)

        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                creds.refresh(Request())
            else:
                if not os.path.exists(self.credentials_file):
                    raise FileNotFoundError(
                        f"Google credentials file '{self.credentials_file}' not found. "
                        "Please download it from Google Cloud Console."
                    )

                flow = InstalledAppFlow.from_client_secrets_file(
                    self.credentials_file, SCOPES)
                creds = flow.run_local_server(port=0)

            with open(self.token_file, 'w') as token:
                token.write(creds.to_json())

        self.service = build('calendar', 'v3', credentials=creds)

    def list_events(self, calendar_id: str = 'primary', max_results: int = 10,
                   time_min: Optional[str] = None) -> List[Dict[str, Any]]:
        """List upcoming events"""
        if not self.service:
            self.authenticate()

        if not time_min:
            time_min = datetime.utcnow().isoformat() + 'Z'

        try:
            events_result = self.service.events().list(
                calendarId=calendar_id,
                timeMin=time_min,
                maxResults=max_results,
                singleEvents=True,
                orderBy='startTime'
            ).execute()

            events = events_result.get('items', [])
            return events

        except HttpError as error:
            raise Exception(f"Failed to list events: {error}")

    def create_event(self, summary: str, start_time: str, end_time: str,
                    description: str = '', location: str = '',
                    calendar_id: str = 'primary') -> Dict[str, Any]:
        """Create a new calendar event"""
        if not self.service:
            self.authenticate()

        event = {
            'summary': summary,
            'location': location,
            'description': description,
            'start': {
                'dateTime': start_time,
                'timeZone': 'UTC',
            },
            'end': {
                'dateTime': end_time,
                'timeZone': 'UTC',
            },
        }

        try:
            event = self.service.events().insert(
                calendarId=calendar_id, body=event).execute()
            return event

        except HttpError as error:
            raise Exception(f"Failed to create event: {error}")

    def find_events(self, query: str, calendar_id: str = 'primary',
                   max_results: int = 10) -> List[Dict[str, Any]]:
        """Search for events by query"""
        if not self.service:
            self.authenticate()

        try:
            events_result = self.service.events().list(
                calendarId=calendar_id,
                q=query,
                maxResults=max_results,
                singleEvents=True,
                orderBy='startTime'
            ).execute()

            events = events_result.get('items', [])
            return events

        except HttpError as error:
            raise Exception(f"Failed to search events: {error}")

    def delete_event(self, event_id: str, calendar_id: str = 'primary') -> bool:
        """Delete an event"""
        if not self.service:
            self.authenticate()

        try:
            self.service.events().delete(
                calendarId=calendar_id, eventId=event_id).execute()
            return True

        except HttpError as error:
            raise Exception(f"Failed to delete event: {error}")

# Initialize MCP server
server = Server("google-calendar")
calendar = GoogleCalendarMCP()

@server.list_tools()
async def handle_list_tools() -> List[types.Tool]:
    """List available calendar tools"""
    return [
        types.Tool(
            name="list_events",
            description="List upcoming calendar events",
            inputSchema={
                "type": "object",
                "properties": {
                    "max_results": {
                        "type": "integer",
                        "description": "Maximum number of events to return (default: 10)",
                        "default": 10
                    },
                    "calendar_id": {
                        "type": "string",
                        "description": "Calendar ID (default: primary)",
                        "default": "primary"
                    }
                }
            }
        ),
        types.Tool(
            name="create_event",
            description="Create a new calendar event",
            inputSchema={
                "type": "object",
                "properties": {
                    "summary": {
                        "type": "string",
                        "description": "Event title/summary"
                    },
                    "start_time": {
                        "type": "string",
                        "description": "Start time in ISO format (e.g., 2024-01-01T10:00:00Z)"
                    },
                    "end_time": {
                        "type": "string",
                        "description": "End time in ISO format (e.g., 2024-01-01T11:00:00Z)"
                    },
                    "description": {
                        "type": "string",
                        "description": "Event description (optional)",
                        "default": ""
                    },
                    "location": {
                        "type": "string",
                        "description": "Event location (optional)",
                        "default": ""
                    },
                    "calendar_id": {
                        "type": "string",
                        "description": "Calendar ID (default: primary)",
                        "default": "primary"
                    }
                },
                "required": ["summary", "start_time", "end_time"]
            }
        ),
        types.Tool(
            name="find_events",
            description="Search for events by query",
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Search query"
                    },
                    "max_results": {
                        "type": "integer",
                        "description": "Maximum number of events to return (default: 10)",
                        "default": 10
                    },
                    "calendar_id": {
                        "type": "string",
                        "description": "Calendar ID (default: primary)",
                        "default": "primary"
                    }
                },
                "required": ["query"]
            }
        ),
        types.Tool(
            name="delete_event",
            description="Delete a calendar event",
            inputSchema={
                "type": "object",
                "properties": {
                    "event_id": {
                        "type": "string",
                        "description": "Event ID to delete"
                    },
                    "calendar_id": {
                        "type": "string",
                        "description": "Calendar ID (default: primary)",
                        "default": "primary"
                    }
                },
                "required": ["event_id"]
            }
        )
    ]

@server.call_tool()
async def handle_call_tool(name: str, arguments: Dict[str, Any]) -> List[types.TextContent]:
    """Handle tool calls"""
    try:
        if name == "list_events":
            max_results = arguments.get("max_results", 10)
            calendar_id = arguments.get("calendar_id", "primary")
            events = calendar.list_events(calendar_id, max_results)

            if not events:
                return [types.TextContent(type="text", text="No upcoming events found.")]

            result = "Upcoming events:\n"
            for event in events:
                start = event['start'].get('dateTime', event['start'].get('date'))
                result += f"- {event.get('summary', 'No title')} at {start}\n"

            return [types.TextContent(type="text", text=result)]

        elif name == "create_event":
            summary = arguments["summary"]
            start_time = arguments["start_time"]
            end_time = arguments["end_time"]
            description = arguments.get("description", "")
            location = arguments.get("location", "")
            calendar_id = arguments.get("calendar_id", "primary")

            event = calendar.create_event(summary, start_time, end_time,
                                        description, location, calendar_id)

            return [types.TextContent(
                type="text",
                text=f"Event '{summary}' created successfully. Event ID: {event['id']}"
            )]

        elif name == "find_events":
            query = arguments["query"]
            max_results = arguments.get("max_results", 10)
            calendar_id = arguments.get("calendar_id", "primary")

            events = calendar.find_events(query, calendar_id, max_results)

            if not events:
                return [types.TextContent(type="text", text=f"No events found for query: {query}")]

            result = f"Events matching '{query}':\n"
            for event in events:
                start = event['start'].get('dateTime', event['start'].get('date'))
                result += f"- {event.get('summary', 'No title')} at {start} (ID: {event['id']})\n"

            return [types.TextContent(type="text", text=result)]

        elif name == "delete_event":
            event_id = arguments["event_id"]
            calendar_id = arguments.get("calendar_id", "primary")

            success = calendar.delete_event(event_id, calendar_id)

            if success:
                return [types.TextContent(type="text", text=f"Event {event_id} deleted successfully.")]
            else:
                return [types.TextContent(type="text", text=f"Failed to delete event {event_id}.")]

        else:
            raise ValueError(f"Unknown tool: {name}")

    except Exception as e:
        return [types.TextContent(type="text", text=f"Error: {str(e)}")]

async def main():
    """Run the MCP server"""
    async with mcp.server.stdio.stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            InitializationOptions(
                server_name="google-calendar",
                server_version="1.0.0",
                capabilities=server.get_capabilities(
                    notification_options=NotificationOptions(),
                    experimental_capabilities={},
                ),
            ),
        )

if __name__ == "__main__":
    import asyncio
    asyncio.run(main())