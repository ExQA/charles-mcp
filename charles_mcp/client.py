"""
Async client for the Charles API.

Wraps every HTTP call to the Charles Proxy Web Interface and uses the
httpx async client so the event loop is never blocked.
"""

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any, cast

import httpx

from charles_mcp.config import Config, get_config
from charles_mcp.utils import ensure_directory, get_latest_file

logger = logging.getLogger(__name__)


class CharlesClientError(Exception):
    """Base class for Charles client errors."""
    pass


class CharlesConnectionError(CharlesClientError):
    """Charles connection error."""
    pass


class CharlesAPIError(CharlesClientError):
    """Charles API call error."""
    pass


class CharlesClient:
    """
    Async API client for Charles Proxy.

    Provides full async access to the Charles Web Interface:
    - session management (clear, export)
    - recording control (start, stop)
    - network throttling (slow network simulation)
    - configuration management

    Attributes:
        config: configuration object
        _client: httpx async client instance

    Example:
        >>> async with CharlesClient() as client:
        ...     await client.clear_session()
        ...     await asyncio.sleep(30)
        ...     data = await client.export_session_json()
    """

    # Network throttling presets
    THROTTLE_PRESETS = {
        "3g": "3G",
        "4g": "4G",
        "5g": "5G",
        "fibre": "100+Mbps+Fibre",
        "100mbps": "100+Mbps+Fibre",
        "56k": "56+kbps+Modem",
        "256k": "256+kbps+ISDN/DSL",
        "deactivate": "deactivate",
        "off": "deactivate",
    }

    def __init__(self, config: Config | None = None) -> None:
        """
        Initialize the Charles client.

        Args:
            config: configuration object; the global config when None
        """
        self.config = config or get_config()
        self._client: httpx.AsyncClient | None = None

    async def __aenter__(self) -> "CharlesClient":
        """Enter the async context manager."""
        await self.connect()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        """Exit the async context manager."""
        await self.close()

    async def connect(self) -> None:
        """
        Connect to Charles (create the httpx client).

        Raises:
            CharlesConnectionError: if the connection fails
        """
        if self._client is not None:
            return

        try:
            self._client = httpx.AsyncClient(
                base_url=self.config.charles_base_url,
                auth=httpx.BasicAuth(*self.config.auth),
                proxy=self.config.proxy_url,
                timeout=httpx.Timeout(self.config.request_timeout),
                follow_redirects=True,
            )
            logger.info("Charles client connected")
        except Exception as e:
            raise CharlesConnectionError(f"cannot create the Charles client: {e}") from e

    async def close(self) -> None:
        """Close the client connection."""
        if self._client is not None:
            try:
                await self._client.aclose()
            except Exception as e:
                logger.warning(f"error while closing the client: {e}")
            finally:
                self._client = None
                logger.info("Charles client closed")

    async def _request(
        self,
        method: str,
        endpoint: str,
        **kwargs: Any,
    ) -> httpx.Response:
        """
        Send an HTTP request to the Charles API.

        Args:
            method: HTTP method (GET, POST, etc.)
            endpoint: API endpoint path
            **kwargs: extra arguments passed to httpx

        Returns:
            httpx.Response: the HTTP response

        Raises:
            CharlesConnectionError: on connection problems
            CharlesAPIError: if the API call fails
        """
        if self._client is None:
            await self.connect()
        client = self._client
        if client is None:
            raise CharlesConnectionError("Charles client is not connected")

        try:
            response = await client.request(method, endpoint, **kwargs)
            response.raise_for_status()
            return response
        except httpx.ConnectError as e:
            raise CharlesConnectionError(
                f"cannot connect to Charles ({self.config.charles_base_url}): {e}"
            ) from e
        except httpx.TimeoutException as e:
            raise CharlesConnectionError(f"request timed out: {e}") from e
        except httpx.HTTPStatusError as e:
            raise CharlesAPIError(
                f"API call failed [{e.response.status_code}]: {endpoint}"
            ) from e
        except httpx.RequestError as e:
            raise CharlesClientError(f"request error: {e}") from e

    async def _get(self, endpoint: str, **kwargs: Any) -> httpx.Response:
        """Send a GET request."""
        return await self._request("GET", endpoint, **kwargs)

    # ==================== Session management ====================

    async def clear_session(self) -> bool:
        """
        Clear the current Charles session.

        Returns:
            bool: whether it succeeded

        Example:
            >>> await client.clear_session()
            True
        """
        try:
            await self._get("/session/clear")
            logger.info("Charles session cleared")
            return True
        except CharlesClientError as e:
            logger.error(f"failed to clear the session: {e}")
            return False

    async def export_session_json(self) -> list[dict]:
        """
        Export the current session as JSON.

        Returns:
            list[dict]: session entries

        Raises:
            CharlesAPIError: if the export fails
        """
        try:
            response = await self._get("/session/export-json")
            data = cast(list[dict], response.json())
            logger.info(f"session exported: {len(data)} entries")
            return data
        except json.JSONDecodeError as e:
            raise CharlesAPIError(f"failed to parse the JSON response: {e}") from e

    async def export_session_text(self) -> str:
        """
        Export the current session as text.

        Returns:
            str: session text
        """
        response = await self._get("/session/export-text")
        return response.text

    # ==================== Recording control ====================

    async def start_recording(self) -> bool:
        """
        Start recording traffic.

        Returns:
            bool: whether it succeeded
        """
        try:
            await self._get("/recording/start")
            logger.info("recording started")
            return True
        except CharlesClientError as e:
            logger.error(f"failed to start recording: {e}")
            return False

    async def stop_recording(self) -> bool:
        """
        Stop recording traffic.

        Returns:
            bool: whether it succeeded
        """
        try:
            await self._get("/recording/stop")
            logger.info("recording stopped")
            return True
        except CharlesClientError as e:
            logger.error(f"failed to stop recording: {e}")
            return False

    # ==================== Network throttling ====================

    async def activate_throttling(self, preset: str) -> bool:
        """
        Activate network throttling.

        Args:
            preset: preset name (e.g. '3G', '4G', 'Fibre')

        Returns:
            bool: whether it succeeded

        Example:
            >>> await client.activate_throttling("3G")
            True
        """
        # Normalize the preset name
        normalized = self.THROTTLE_PRESETS.get(preset.lower(), preset)

        try:
            await self._get("/throttling/activate", params={"preset": normalized})
            logger.info(f"network throttling activated: {normalized}")
            return True
        except CharlesClientError as e:
            logger.error(f"failed to activate network throttling: {e}")
            return False

    async def deactivate_throttling(self) -> bool:
        """
        Deactivate network throttling.

        Returns:
            bool: whether it succeeded
        """
        try:
            await self._get("/throttling/deactivate")
            logger.info("network throttling deactivated")
            return True
        except CharlesClientError as e:
            logger.error(f"failed to deactivate network throttling: {e}")
            return False

    async def set_throttling(self, status: str) -> tuple[bool, str]:
        """
        Set the network throttling state.

        Single entry point for both activation and deactivation.

        Args:
            status: preset name, or 'deactivate'/'off' to turn it off

        Returns:
            tuple[bool, str]: (success, status message)

        Example:
            >>> await client.set_throttling("3G")
            (True, "Network throttling activated: 3G")
            >>> await client.set_throttling("off")
            (True, "Network throttling deactivated")
        """
        normalized = self.THROTTLE_PRESETS.get(status.lower(), status)

        if normalized == "deactivate":
            success = await self.deactivate_throttling()
            return (
                success,
                "Network throttling deactivated"
                if success
                else "Failed to deactivate network throttling",
            )
        else:
            success = await self.activate_throttling(normalized)
            return (
                success,
                f"Network throttling activated: {normalized}"
                if success
                else f"Failed to activate network throttling: {normalized}"
            )

    # ==================== Tool toggles ====================

    async def set_tool_enabled(self, tool: str, enabled: bool) -> bool:
        """
        Enable or disable a Charles tool (e.g. ``map-local``) via the web interface.

        The web interface can only toggle tools; it cannot add or edit rules.
        """
        action = "enable" if enabled else "disable"
        try:
            await self._get(f"/tools/{tool}/{action}")
            logger.info("Charles tool %s: %s", tool, action)
            return True
        except CharlesClientError as e:
            logger.error("Failed to %s Charles tool %s: %s", action, tool, e)
            return False

    # ==================== Charles control ====================

    async def quit_charles(self, timeout: float = 3.0) -> bool:
        """
        Quit Charles.

        Args:
            timeout: request timeout

        Returns:
            bool: whether the quit command was sent
        """
        if self._client is None:
            await self.connect()
        client = self._client
        if client is None:
            raise CharlesConnectionError("Charles client is not connected")

        try:
            # The quit command may never answer, so use a short timeout
            await client.get(
                "/quit",
                timeout=httpx.Timeout(timeout),
            )
            return True
        except (httpx.TimeoutException, httpx.RequestError) as e:
            # Quitting usually drops the connection; that is expected
            logger.debug(f"Charles quit command sent (connection drop expected): {e}")
            return True
        except Exception as e:
            logger.warning(f"error while sending the quit command: {e}")
            return False

    async def get_info(self) -> dict | None:
        """
        Get Charles info.

        Returns:
            Optional[dict]: Charles info, or None on failure
        """
        try:
            response = await self._get("/")
            return {"status": "connected", "response": response.text[:200]}
        except CharlesClientError as e:
            logger.error(f"failed to get Charles info: {e}")
            return None

    # ==================== Session files ====================

    async def load_latest_session(self, package_dir: str | None = None) -> list[dict]:
        """
        Load the latest session file.

        Args:
            package_dir: recordings directory; the configured one by default

        Returns:
            list[dict]: session entries

        Raises:
            FileNotFoundError: if no session file is found
        """
        directory = package_dir or self.config.package_dir
        latest_file = get_latest_file(directory, ".chlsj")

        if not latest_file:
            raise FileNotFoundError(f"no saved recording found in {directory}")

        logger.info(f"loading saved session: {latest_file}")

        with open(latest_file, encoding="utf-8") as f:
            return cast(list[dict], json.load(f))

    def generate_filename(self) -> str:
        """
        Build a timestamped file name.

        Returns:
            str: file name without the directory
        """
        timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
        return f"{timestamp}.chlsj"

    def get_full_save_path(self) -> str:
        """
        Get the full save path.

        Returns:
            str: full file path
        """
        ensure_directory(self.config.package_dir)
        return str(Path(self.config.package_dir) / self.generate_filename())
