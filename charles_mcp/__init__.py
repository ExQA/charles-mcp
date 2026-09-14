"""
Charles MCP Server - connects Charles Proxy to MCP clients.

This package provides:
- CharlesClient: async Charles API client
- MCP Server: capture, analysis, throttling, mock and reverse-analysis tools

Example:
    >>> from charles_mcp.server import create_server
    >>> server = create_server()
    >>> server.run(transport="stdio")
"""

__version__ = "3.1.0rc1"
__author__ = "heizaheiza"

from charles_mcp.client import CharlesClient
from charles_mcp.config import Config

__all__ = ["Config", "CharlesClient", "__version__"]

