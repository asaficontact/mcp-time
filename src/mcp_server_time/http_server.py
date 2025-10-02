"""HTTP/SSE server implementation for MCP Time Server"""
import asyncio
import os
from typing import Any

from mcp.server import Server
from mcp.server.sse import SseServerTransport
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import Response
from starlette.routing import Route, Mount

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

    # Create SSE transport
    # Using /messages as the message endpoint
    sse_transport = SseServerTransport("/messages")
    
    async def handle_mcp_sse(scope: dict, receive: Any, send: Any) -> None:
        """Handle SSE connection for MCP"""
        async with sse_transport.connect_sse(scope, receive, send) as (read_stream, write_stream):
            init_options = server.create_initialization_options()
            try:
                await server.run(read_stream, write_stream, init_options)
            except Exception as e:
                # Log but don't crash - client might have disconnected
                import logging
                logging.error(f"Error in MCP session: {e}")
    
    async def handle_post_messages(scope: dict, receive: Any, send: Any) -> None:
        """Handle POST messages to /messages (with session)"""
        await sse_transport.handle_post_message(scope, receive, send)
    
    async def handle_direct_post(scope: dict, receive: Any, send: Any) -> None:
        """Handle direct POST to /mcp without session (for scanners)"""
        from starlette.requests import Request
        from starlette.responses import JSONResponse
        import logging
        logger = logging.getLogger(__name__)
        
        request = Request(scope, receive)
        
        try:
            # Parse the JSON-RPC request
            json_data = await request.json()
            logger.info(f"Direct POST request: {json_data}")
            
            # Handle initialize request
            if json_data.get("method") == "initialize":
                response_data = {
                    "jsonrpc": "2.0",
                    "id": json_data.get("id"),
                    "result": {
                        "protocolVersion": "2024-11-05",
                        "capabilities": {
                            "experimental": {},
                            "tools": {
                                "listChanged": False
                            }
                        },
                        "serverInfo": {
                            "name": "mcp-time",
                            "version": "1.0.0"
                        }
                    }
                }
                response = JSONResponse(response_data)
                await response(scope, receive, send)
                return
            
            # Handle notifications/initialized (notification, not a request)
            elif json_data.get("method") == "notifications/initialized":
                # This is a notification, not a request, so no response body needed
                # Just acknowledge with 202 Accepted
                from starlette.responses import Response
                response = Response(status_code=202)
                await response(scope, receive, send)
                return
            
            # Handle tools/list request
            elif json_data.get("method") == "tools/list":
                # Return the tools list manually since we know what they are
                tools_list = [
                    {
                        "name": "get_current_time",
                        "description": "Get current time in a specific timezones",
                        "inputSchema": {
                            "type": "object",
                            "properties": {
                                "timezone": {
                                    "type": "string",
                                    "description": f"IANA timezone name (e.g., 'America/New_York', 'Europe/London'). Use '{local_tz}' as local timezone if no timezone provided by the user."
                                }
                            },
                            "required": ["timezone"]
                        }
                    },
                    {
                        "name": "convert_time",
                        "description": "Convert time between timezones",
                        "inputSchema": {
                            "type": "object",
                            "properties": {
                                "source_timezone": {
                                    "type": "string",
                                    "description": f"Source IANA timezone name (e.g., 'America/New_York', 'Europe/London'). Use '{local_tz}' as local timezone if no source timezone provided by the user."
                                },
                                "time": {
                                    "type": "string",
                                    "description": "Time to convert in 24-hour format (HH:MM)"
                                },
                                "target_timezone": {
                                    "type": "string",
                                    "description": f"Target IANA timezone name (e.g., 'Asia/Tokyo', 'America/San_Francisco'). Use '{local_tz}' as local timezone if no target timezone provided by the user."
                                }
                            },
                            "required": ["source_timezone", "time", "target_timezone"]
                        }
                    }
                ]
                response_data = {
                    "jsonrpc": "2.0",
                    "id": json_data.get("id"),
                    "result": {
                        "tools": tools_list
                    }
                }
                response = JSONResponse(response_data)
                await response(scope, receive, send)
                return
            
            # Handle other methods
            else:
                response_data = {
                    "jsonrpc": "2.0",
                    "id": json_data.get("id"),
                    "error": {
                        "code": -32601,
                        "message": f"Method not found: {json_data.get('method')}"
                    }
                }
                response = JSONResponse(response_data, status_code=400)
                await response(scope, receive, send)
                return
                
        except Exception as e:
            logger.error(f"Error handling direct POST: {e}")
            response_data = {
                "jsonrpc": "2.0",
                "id": json_data.get("id") if 'json_data' in locals() else None,
                "error": {
                    "code": -32603,
                    "message": f"Internal error: {str(e)}"
                }
            }
            response = JSONResponse(response_data, status_code=500)
            await response(scope, receive, send)

    # Create a simple ASGI app that routes requests
    async def app(scope: dict, receive: Any, send: Any) -> None:
        import logging
        logger = logging.getLogger(__name__)
        
        if scope["type"] == "http":
            path = scope["path"]
            method = scope["method"]
            
            logger.info(f"HTTP request: {method} {path}")
            
            if path == "/mcp":
                if method == "GET":
                    # SSE connection
                    await handle_mcp_sse(scope, receive, send)
                elif method == "POST":
                    # Direct POST (for Smithery scanner and direct clients)
                    await handle_direct_post(scope, receive, send)
            elif path == "/messages" and method == "POST":
                await handle_post_messages(scope, receive, send)
            elif path == "/" or path == "/health":
                # Health check endpoint
                await send({
                    "type": "http.response.start",
                    "status": 200,
                    "headers": [
                        [b"content-type", b"application/json"],
                        [b"cache-control", b"no-cache"],
                    ],
                })
                await send({
                    "type": "http.response.body",
                    "body": json.dumps({
                        "status": "ok",
                        "server": "mcp-time",
                        "version": "0.6.2",
                        "mcp_endpoint": "/mcp"
                    }).encode(),
                })
            elif path == "/.well-known/mcp-config":
                # MCP configuration endpoint for Smithery discovery
                await send({
                    "type": "http.response.start",
                    "status": 200,
                    "headers": [
                        [b"content-type", b"application/json"],
                        [b"cache-control", b"no-cache"],
                    ],
                })
                await send({
                    "type": "http.response.body",
                    "body": json.dumps({
                        "$schema": "http://json-schema.org/draft-07/schema#",
                        "$id": "https://mcp-time/.well-known/mcp-config",
                        "title": "MCP Time Server Configuration",
                        "description": "Configuration for MCP Time Server (no configuration required)",
                        "type": "object",
                        "properties": {},
                        "additionalProperties": False
                    }).encode(),
                })
            else:
                # 404 Not Found
                await send({
                    "type": "http.response.start",
                    "status": 404,
                    "headers": [[b"content-type", b"text/plain"]],
                })
                await send({
                    "type": "http.response.body",
                    "body": b"Not Found",
                })
        elif scope["type"] == "lifespan":
            # Handle lifespan events
            logger.info("Handling lifespan event")
            while True:
                message = await receive()
                if message["type"] == "lifespan.startup":
                    logger.info("Application startup")
                    await send({"type": "lifespan.startup.complete"})
                elif message["type"] == "lifespan.shutdown":
                    logger.info("Application shutdown")
                    await send({"type": "lifespan.shutdown.complete"})
                    return
    
    return app


def main():
    """Run the HTTP server"""
    import argparse
    import uvicorn
    import logging

    # Configure logging
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    logger = logging.getLogger(__name__)

    parser = argparse.ArgumentParser(
        description="MCP Time Server - HTTP/SSE version"
    )
    parser.add_argument("--local-timezone", type=str, help="Override local timezone")
    parser.add_argument("--port", type=int, default=8000, help="Port to run server on")
    parser.add_argument("--host", type=str, default="0.0.0.0", help="Host to bind to")

    args = parser.parse_args()
    
    # Get port from environment variable (for Smithery) or command line
    port = int(os.environ.get("PORT", args.port))
    
    logger.info(f"Starting MCP Time Server on {args.host}:{port}")
    logger.info(f"Local timezone: {args.local_timezone or 'auto-detect'}")
    logger.info(f"MCP endpoint: http://{args.host}:{port}/mcp")
    
    app = create_app(args.local_timezone)
    
    uvicorn.run(
        app, 
        host=args.host, 
        port=port,
        log_level="info",
        access_log=True
    )


if __name__ == "__main__":
    main()

