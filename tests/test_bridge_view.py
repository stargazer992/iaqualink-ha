"""Tests for the Jandy Bridge card's pass-through to the bridge."""

from unittest.mock import AsyncMock, MagicMock, patch

from aiohttp import web
from aiohttp.test_utils import make_mocked_request
import httpx
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.iaqualink.bridge_view import JandyBridgeView
from custom_components.iaqualink.const import CONF_AQUALINKD_URL, DOMAIN
from homeassistant.components.http import KEY_AUTHENTICATED, KEY_HASS
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant

from .conftest import MOCK_DATA

BRIDGE = "http://192.168.105.24:8080"


def _entry(hass: HomeAssistant, url: str | None = BRIDGE + "/") -> None:
    options = {CONF_AQUALINKD_URL: url} if url else {}
    MockConfigEntry(
        domain=DOMAIN, data=MOCK_DATA, options=options, state=ConfigEntryState.LOADED
    ).add_to_hass(hass)


def _request(
    hass: HomeAssistant, method: str, path: str, *, auth: bool = True, body=b""
) -> web.Request:
    app = web.Application()
    app[KEY_HASS] = hass
    req = make_mocked_request(
        method,
        "/api/iaqualink/bridge/" + path,
        headers={"Content-Type": "application/json"},
        app=app,
        payload=None,
    )
    req[KEY_AUTHENTICATED] = auth
    req.read = AsyncMock(return_value=body)
    return req


def _client(response: httpx.Response | Exception) -> MagicMock:
    client = MagicMock()
    if isinstance(response, Exception):
        client.request = AsyncMock(side_effect=response)
    else:
        client.request = AsyncMock(return_value=response)
    return client


async def test_page_and_state_forwarded(hass: HomeAssistant) -> None:
    """A logged-in user reads the bridge page and its state through HA."""
    _entry(hass)
    client = _client(
        httpx.Response(
            200, content=b'{"spa":false}', headers={"content-type": "application/json"}
        )
    )
    with patch(
        "custom_components.iaqualink.bridge_view.get_async_client", return_value=client
    ):
        resp = await JandyBridgeView().get(
            _request(hass, "GET", "api/state"), "api/state"
        )
    assert resp.status == 200
    assert resp.body == b'{"spa":false}'
    assert resp.content_type == "application/json"
    assert resp.headers["Cache-Control"] == "no-store"
    method, url = client.request.await_args.args
    assert (method, url) == ("GET", BRIDGE + "/api/state")


async def test_command_forwarded(hass: HomeAssistant) -> None:
    """The card's commands go to the bridge's POST api/cmd unchanged."""
    _entry(hass)
    client = _client(httpx.Response(200, json={"ok": True}))
    body = b'{"circuit":"aux3","on":true}'
    with patch(
        "custom_components.iaqualink.bridge_view.get_async_client", return_value=client
    ):
        resp = await JandyBridgeView().post(
            _request(hass, "POST", "api/cmd", body=body), "api/cmd"
        )
    assert resp.status == 200
    assert client.request.await_args.args == ("POST", BRIDGE + "/api/cmd")
    assert client.request.await_args.kwargs["content"] == body
    assert client.request.await_args.kwargs["headers"] == {
        "Content-Type": "application/json"
    }


async def test_logged_out_refused(hass: HomeAssistant) -> None:
    """Without an HA login only the page's images load."""
    _entry(hass)
    client = _client(httpx.Response(200, content=b"png"))
    view = JandyBridgeView()
    with patch(
        "custom_components.iaqualink.bridge_view.get_async_client", return_value=client
    ):
        with pytest.raises(web.HTTPUnauthorized):
            await view.get(_request(hass, "GET", "", auth=False), "")
        with pytest.raises(web.HTTPUnauthorized):
            await view.post(_request(hass, "POST", "api/cmd", auth=False), "api/cmd")
        resp = await view.get(
            _request(hass, "GET", "brand.png", auth=False), "brand.png"
        )
    assert resp.status == 200
    client.request.assert_awaited_once()


async def test_other_writes_refused(hass: HomeAssistant) -> None:
    """Settings saves and the GET .../set API stay off the card's path."""
    _entry(hass)
    client = _client(httpx.Response(200))
    view = JandyBridgeView()
    with patch(
        "custom_components.iaqualink.bridge_view.get_async_client", return_value=client
    ):
        with pytest.raises(web.HTTPForbidden):
            await view.get(_request(hass, "GET", "api/aux_3/set"), "api/aux_3/set")
        with pytest.raises(web.HTTPForbidden):
            await view.get(
                _request(hass, "GET", "api/SWG/Percent/set"), "api/SWG/Percent/set"
            )
        with pytest.raises(web.HTTPForbidden):
            await view.post(_request(hass, "POST", "api/config"), "api/config")
    client.request.assert_not_called()


async def test_no_bridge_address(hass: HomeAssistant) -> None:
    """Without a Jandy Bridge address the card gets a clear error."""
    _entry(hass, None)
    resp = await JandyBridgeView().get(_request(hass, "GET", ""), "")
    assert resp.status == 503


async def test_bridge_unreachable(hass: HomeAssistant) -> None:
    """A bridge that doesn't answer is a 502 the card can show."""
    _entry(hass)
    client = _client(httpx.ConnectError("refused"))
    with patch(
        "custom_components.iaqualink.bridge_view.get_async_client", return_value=client
    ):
        resp = await JandyBridgeView().get(
            _request(hass, "GET", "api/state"), "api/state"
        )
    assert resp.status == 502
    assert b"ConnectError" in resp.body
