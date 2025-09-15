# A.D.A. MCP Google Calendar Integration

## Overview
A.D.A. has been updated to use the official Google Calendar MCP (Model Context Protocol) tools for enhanced calendar management capabilities.

## Available MCP Calendar Tools

### Core Calendar Actions
- **mcp_google_calendar_find_events** - Find events with basic and advanced filtering
- **mcp_google_calendar_create_event** - Create detailed events
- **mcp_google_calendar_quick_add_event** - Quick-add events from natural language text
- **mcp_google_calendar_delete_event** - Delete events
- **mcp_google_calendar_list_calendars** - List all available calendars

### Advanced Features (Available in full MCP server)
- **mcp_google_calendar_create_calendar** - Create new calendars
- **mcp_google_calendar_update_event** - Update existing events
- **mcp_google_calendar_add_attendee** - Add attendees to events
- **mcp_google_calendar_check_attendee_status** - Check attendee response status
- **mcp_google_calendar_query_free_busy** - Query free/busy information
- **mcp_google_calendar_schedule_mutual** - Find mutual free slots
- **mcp_google_calendar_analyze_busyness** - Analyze daily event counts

## Current Status
✅ **Function definitions integrated** - A.D.A. knows about all MCP calendar tools
⚠️ **MCP server required** - Need to install official Google Calendar MCP server

## A.D.A. HTTP Bridge
- A.D.A. now calls the running MCP server over HTTP.
- Base URL comes from `MCP_CAL_BASE_URL` (defaults to `http://127.0.0.1:3001`).
- Endpoint mapping:
  - `mcp_google_calendar_list_calendars` → `GET /calendars`
- `mcp_google_calendar_find_events` → `GET /calendars/{calendar_id}/events`
  - Default behavior: if no `time_min`/`time_max` provided, A.D.A. sets `time_min` to the current local time so results are always upcoming (no old years).
  - `mcp_google_calendar_create_event` → `POST /calendars/{calendar_id}/events`
  - `mcp_google_calendar_quick_add_event` → `POST /calendars/{calendar_id}/events/quickAdd`
  - `mcp_google_calendar_delete_event` → `DELETE /calendars/{calendar_id}/events/{event_id}`

## Testing
- Run the automated end-to-end check:
  - `node tests/calendar_bridge_test.mjs`
- What it does:
  - Health check → List calendars → Find events
  - Create a detailed event → Verify via search → Quick-add event
  - Clean up by deleting both test events

## Next Steps to Enable Full Functionality

1. **Install Google Calendar MCP Server**
   ```bash
   npm install -g @google/calendar-mcp
   ```

2. **Configure MCP Server**
   - Set up Google Calendar API credentials
   - Configure MCP server with A.D.A.

3. **Update A.D.A. Integration**
   - Replace stub functions with actual MCP client calls
   - Configure MCP communication protocol

## Usage Examples
Once fully configured, users can ask A.D.A.:
- **"List my calendars"** → uses `mcp_google_calendar_list_calendars`
- **"Find my meetings today"** → uses `mcp_google_calendar_find_events`
- **"Create meeting tomorrow 2pm"** → uses `mcp_google_calendar_create_event`
- **"Schedule lunch tomorrow"** → uses `mcp_google_calendar_quick_add_event`
- **"Delete my 3pm meeting"** → uses `mcp_google_calendar_delete_event`

## Benefits of MCP Integration
- **Standardized** - Uses official Google Calendar MCP protocol
- **Feature-rich** - Advanced scheduling and analysis capabilities
- **Reliable** - Maintained by Google/official teams
- **Extensible** - Easy to add more calendar providers
- **Interoperable** - Works with other MCP-compatible tools

The foundation is now in place for full MCP calendar integration!
