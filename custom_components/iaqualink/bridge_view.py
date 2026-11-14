"""Pass the Jandy Bridge page through Home Assistant for the Jandy Bridge card."""

from __future__ import annotations

import re

from aiohttp import web
import httpx

from homeassistant.components.http import KEY_AUTHENTICATED, KEY_HASS, HomeAssistantView
from homeassistant.core import HomeAssistant
from homeassistant.helpers.httpx_client import get_async_client

from .const import CONF_AQUALINKD_URL, DOMAIN

BRIDGE_PROXY_URL = "/api/iaqualink/bridge"
# Images the page links with <img>/<link>, which can't carry HA's login.
PUBLIC = {"brand.png", "icon.png", "wiring.svg"}
# AqualinkD-compatible GET /api/<id>/set changes state; the card never uses it.
GET_SET = re.compile(r"^api/(.*/)?set$")
POSTS = {"api/cmd", "api/ha/link/request"}
TIMEOUT = httpx.Timeout(10.0)


def _bridge_url(hass: HomeAssistant) -> str | None:
    for entry in hass.config_entries.async_loaded_entries(DOMAIN):
        if url := entry.options.get(CONF_AQUALINKD_URL):
            return str(url).rstrip("/")
    return None


class JandyBridgeView(HomeAssistantView):
    """Read the bridge page and its API, and send its commands, for logged-in users."""

    url = BRIDGE_PROXY_URL + "/{path:.*}"
    name = "api:iaqualink:bridge"
    requires_auth = False

    async def get(self, request: web.Request, path: str) -> web.Response:
        """Return a bridge page, image or API read."""
        if path not in PUBLIC and not request[KEY_AUTHENTICATED]:
            raise web.HTTPUnauthorized
        if GET_SET.match(path):
            raise web.HTTPForbidden
        return await self._forward(request, path, "GET")

    async def post(self, request: web.Request, path: str) -> web.Response:
        """Send a command to the bridge."""
        if not request[KEY_AUTHENTICATED]:
            raise web.HTTPUnauthorized
        if path not in POSTS:
            raise web.HTTPForbidden
        return await self._forward(request, path, "POST")

    async def _forward(
        self, request: web.Request, path: str, method: str
    ) -> web.Response:
        hass = request.app[KEY_HASS]
        base = _bridge_url(hass)
        if base is None:
            return web.json_response(
                {"error": "no Jandy Bridge address set"}, status=503
            )
        try:
            resp = await get_async_client(hass).request(
                method,
                f"{base}/{path}",
                params=dict(request.query),
                content=await request.read() if method == "POST" else None,
                headers={"Content-Type": request.content_type}
                if method == "POST"
                else None,
                timeout=TIMEOUT,
            )
        except httpx.HTTPError as err:
            return web.json_response(
                {"error": f"Jandy Bridge not reachable: {type(err).__name__}"},
                status=502,
            )
        return web.Response(
            body=resp.content,
            status=resp.status_code,
            content_type=resp.headers.get("content-type", "application/octet-stream")
            .split(";")[0]
            .strip(),
            headers={"Cache-Control": "no-store"},
        )
