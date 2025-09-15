#!/usr/bin/env python3
"""
Test script for A.D.A. Calendar Integration
Verifies that calendar tools are properly integrated
"""

import sys
import os

sys.path.append(os.path.dirname(os.path.abspath(__file__)))

def test_calendar_integration():
    """Test if A.D.A. calendar integration is working"""

    print("Testing A.D.A. Calendar Integration...")

    # Test 1: Import A.D.A. core
    try:
        from ada import AI_Core
        print("✓ A.D.A. AI_Core import successful")
    except ImportError as e:
        print(f"✗ Failed to import AI_Core: {e}")
        return False

    # Test 2: Check if calendar tools are in tool definitions
    try:
        ai_core = AI_Core()
        tools = ai_core.config["tools"]
        function_declarations = tools[2]["function_declarations"]

        calendar_tools = [
            "list_calendar_events",
            "create_calendar_event",
            "find_calendar_events",
            "delete_calendar_event"
        ]

        found_tools = []
        for func_def in function_declarations:
            if func_def["name"] in calendar_tools:
                found_tools.append(func_def["name"])

        if len(found_tools) == 4:
            print("✓ All 4 calendar tools found in function declarations")
            for tool in found_tools:
                print(f"  - {tool}")
        else:
            print(f"✗ Only found {len(found_tools)}/4 calendar tools: {found_tools}")
            return False

    except Exception as e:
        print(f"✗ Failed to check tool definitions: {e}")
        return False

    # Test 3: Check if calendar methods exist
    try:
        methods = [
            "_list_calendar_events",
            "_create_calendar_event",
            "_find_calendar_events",
            "_delete_calendar_event"
        ]

        for method in methods:
            if hasattr(ai_core, method):
                print(f"✓ Method {method} exists")
            else:
                print(f"✗ Method {method} missing")
                return False

    except Exception as e:
        print(f"✗ Failed to check methods: {e}")
        return False

    # Test 4: Check calendar MCP availability
    try:
        from calendar_mcp import GoogleCalendarMCP
        calendar_mcp = GoogleCalendarMCP()
        print("✓ Calendar MCP available (credentials may need setup)")
    except ImportError:
        print("⚠ Calendar MCP not available (this is ok if credentials aren't set up yet)")

    print("\n🎉 Calendar integration test completed successfully!")
    print("\nNext steps:")
    print("1. Follow CALENDAR_SETUP.md to set up Google Calendar credentials")
    print("2. Test with A.D.A. by asking: 'List my calendar events'")

    return True

if __name__ == "__main__":
    success = test_calendar_integration()
    sys.exit(0 if success else 1)