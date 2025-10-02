"""HTTP/SSE server implementation for MCP Time Server"""
import asyncio
import os
from typing import Any

from mcp.server import Server
from mcp.server.sse import SseServerTransport
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import Response
from starlette.routing import Route

from .server import TimeServer, TimeTools, get_local_tz
from mcp.types import Tool, TextContent, ImageContent, EmbeddedResource
from typing import Sequence
import json


def create_app(local_timezone: str | None = None) -> Starlette:
    """Create the Starlette ASGI application"""
    
    server = Server("mcp-time")
    time_server = TimeServer()
    local_tz = str(get_local_tz(local_timezone))

    @server.list_tools()
    async def list_tools() -> list[Tool]:
        """List available time tools."""
        return [
            Tool(
                name=TimeTools.GET_CURRENT_TIME.value,
                description="Get current time in a specific timezones",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "timezone": {
                            "type": "string",
                            "description": f"IANA timezone name (e.g., 'America/New_York', 'Europe/London'). Use '{local_tz}' as local timezone if no timezone provided by the user.",
                        }
                    },
                    "required": ["timezone"],
                },
            ),
            Tool(
                name=TimeTools.CONVERT_TIME.value,
                description="Convert time between timezones",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "source_timezone": {
                            "type": "string",
                            "description": f"Source IANA timezone name (e.g., 'America/New_York', 'Europe/London'). Use '{local_tz}' as local timezone if no source timezone provided by the user.",
                        },
                        "time": {
                            "type": "string",
                            "description": "Time to convert in 24-hour format (HH:MM)",
                        },
                        "target_timezone": {
                            "type": "string",
                            "description": f"Target IANA timezone name (e.g., 'Asia/Tokyo', 'America/San_Francisco'). Use '{local_tz}' as local timezone if no target timezone provided by the user.",
                        },
                    },
                    "required": ["source_timezone", "time", "target_timezone"],
                },
            ),
        ]

    @server.call_tool()
    async def call_tool(
        name: str, arguments: dict
    ) -> Sequence[TextContent | ImageContent | EmbeddedResource]:
        """Handle tool calls for time queries."""
        try:
            match name:
                case TimeTools.GET_CURRENT_TIME.value:
                    timezone = arguments.get("timezone")
                    if not timezone:
                        raise ValueError("Missing required argument: timezone")

                    result = time_server.get_current_time(timezone)

                case TimeTools.CONVERT_TIME.value:
                    if not all(
                        k in arguments
                        for k in ["source_timezone", "time", "target_timezone"]
                    ):
                        raise ValueError("Missing required arguments")

                    result = time_server.convert_time(
                        arguments["source_timezone"],
                        arguments["time"],
                        arguments["target_timezone"],
                    )
                case _:
                    raise ValueError(f"Unknown tool: {name}")

            return [
                TextContent(type="text", text=json.dumps(result.model_dump(), indent=2))
            ]

        except Exception as e:
            raise ValueError(f"Error processing mcp-server-time query: {str(e)}")

    # Create SSE transport and connect server
    # Using /mcp/messages as the message endpoint
    sse_transport = SseServerTransport("/mcp/messages")
    
    async def startup():
        """Connect the server to the transport on startup"""
        await server.connect(sse_transport)
    
    async def handle_mcp(scope: dict, receive: Any, send: Any) -> None:
        """Handle MCP requests (both GET for SSE and POST for messages)"""
        if scope["method"] == "GET":
            # SSE connection
            await sse_transport.connect_sse(scope, receive, send)
        elif scope["method"] == "POST":
            # POST message
            await sse_transport.handle_post_message(scope, receive, send)

    # Create routes - using raw ASGI endpoint for /mcp
    routes = [
        Route("/mcp", endpoint=handle_mcp, methods=["GET", "POST"]),
        Route("/mcp/messages", endpoint=lambda scope, receive, send: sse_transport.handle_post_message(scope, receive, send), methods=["POST"]),
    ]

    app = Starlette(routes=routes, on_startup=[startup])
    return app


def main():
    """Run the HTTP server"""
    import argparse
    import uvicorn

    parser = argparse.ArgumentParser(
        description="MCP Time Server - HTTP/SSE version"
    )
    parser.add_argument("--local-timezone", type=str, help="Override local timezone")
    parser.add_argument("--port", type=int, default=8000, help="Port to run server on")
    parser.add_argument("--host", type=str, default="0.0.0.0", help="Host to bind to")

    args = parser.parse_args()
    
    # Get port from environment variable (for Smithery) or command line
    port = int(os.environ.get("PORT", args.port))
    
    app = create_app(args.local_timezone)
    
    uvicorn.run(app, host=args.host, port=port)


if __name__ == "__main__":
    main()

