from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any, Optional

import aiohttp
from bleak import BleakClient
from bleak.backends.characteristic import BleakGATTCharacteristic

from homeassistant.components import bluetooth
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .const import DOMAIN, R2_API_URL, R2_STATUS_MAP

_LOGGER = logging.getLogger(__name__)

_HEADER = bytes([0xDF, 0xDF])


def _build_cmd(func: int, cmd: int, data: bytes = b"") -> bytes:
    frame = bytes([func, cmd, len(data)]) + data
    full = _HEADER + frame
    return full + bytes([sum(full) & 0xFF])


_CMD_GET_FIRMWARE = _build_cmd(0x00, 0x02)


@dataclass
class R2Data:
    concentration: float = 0.0
    refractive_index: float = 0.0
    prism_temperature: float = 0.0
    sample_temperature: float = 0.0
    temperature_unit: str = "°C"
    test_status: str = "Unknown"
    authenticated: bool = False


class DifluidR2Coordinator(DataUpdateCoordinator[R2Data]):

    def __init__(
        self, hass: HomeAssistant, address: str, license_key: str
    ) -> None:
        super().__init__(hass, _LOGGER, name=f"{DOMAIN}_r2_{address}", update_interval=None)
        self.address = address
        self.license_key = license_key
        self._client: Optional[BleakClient] = None
        self._char_encrypted: Optional[BleakGATTCharacteristic] = None
        self._char_cleartext: Optional[BleakGATTCharacteristic] = None
        self._auth_response: Optional[asyncio.Future] = None
        self._reconnect_task: Optional[asyncio.Task] = None
        self._sn: str = ""
        self._mac: str = ""
        self.data = R2Data()

    async def async_start(self) -> None:
        await self._do_connect()

    async def async_stop(self) -> None:
        if self._reconnect_task and not self._reconnect_task.done():
            self._reconnect_task.cancel()
        if self._client and self._client.is_connected:
            try:
                await self._client.disconnect()
            except Exception:
                pass
        self._client = None

    async def _do_connect(self) -> None:
        ble_device = bluetooth.async_ble_device_from_address(
            self.hass, self.address, connectable=True
        )
        if ble_device is None:
            raise RuntimeError(f"BLE device {self.address} not found")

        client = BleakClient(ble_device, disconnected_callback=self._on_disconnect)
        await client.connect()
        _LOGGER.debug("Connected to Difluid R2 %s", self.address)

        # Find the two NOTIFY+WRITE characteristics of the R2 service
        chars = [
            c for c in client.services.characteristics.values()
            if "notify" in c.properties and "write-without-response" in c.properties
        ]
        if len(chars) < 2:
            # Fallback: any writable + notifiable chars
            chars = [
                c for c in client.services.characteristics.values()
                if "notify" in c.properties
            ]
        if len(chars) < 2:
            raise RuntimeError("Could not find required R2 BLE characteristics")

        # First char = encrypted auth channel, second = cleartext data channel
        self._char_encrypted = chars[0]
        self._char_cleartext = chars[1]
        self._client = client

        # Subscribe to encrypted channel for auth handshake
        await client.start_notify(self._char_encrypted.uuid, self._on_auth_notification)

        # Run server authentication
        await self._authenticate()

    async def _authenticate(self) -> None:
        """3-step handshake with DiFluid cloud to unlock cleartext channel."""
        headers = {
            "Content-Type": "application/json",
            "license": self.license_key,
        }

        try:
            async with aiohttp.ClientSession() as session:
                # Step 1: get cmd1 from server, write to device
                cmd1 = await self._server_cmd_request(session, headers, "cmd1")
                resp1 = await self._write_and_wait(bytes.fromhex(cmd1))

                # Step 2: send device response, get SN/MAC and cmd2
                result1 = await self._server_dev_respond(session, headers, resp1.hex())
                self._sn = result1.get("sn", "")
                self._mac = result1.get("mac", "")
                cmd2 = await self._server_cmd_request(
                    session, headers, "cmd2", {"sn": self._sn, "mac": self._mac}
                )
                resp2 = await self._write_and_wait(bytes.fromhex(cmd2))

                # Step 3: send device response, get cmd3 (instructContent)
                result2 = await self._server_dev_respond(
                    session, headers, resp2.hex(), sn=self._sn, mac=self._mac
                )
                cmd3 = result2.get("instructContent", "")
                resp3 = await self._write_and_wait(bytes.fromhex(cmd3))

                # Step 4: send device response, get enableCleartext cmd
                await self._server_dev_respond(
                    session, headers, resp3.hex(), sn=self._sn, mac=self._mac
                )
                enable_cmd = await self._server_cmd_request(
                    session, headers, "enableCleartext", {"sn": self._sn, "mac": self._mac}
                )
                await self._write_and_wait(bytes.fromhex(enable_cmd), wait=False)

        except Exception as err:
            raise RuntimeError(f"R2 authentication failed: {err}") from err

        # Stop listening on encrypted channel, switch to cleartext channel
        await self._client.stop_notify(self._char_encrypted.uuid)
        await self._client.start_notify(self._char_cleartext.uuid, self._on_data_notification)

        self.data.authenticated = True
        _LOGGER.info("R2 %s authenticated successfully (SN: %s)", self.address, self._sn)

        # Get firmware version as initial handshake on cleartext channel
        await self._client.write_gatt_char(
            self._char_cleartext.uuid, _CMD_GET_FIRMWARE, response=False
        )

    async def _server_cmd_request(
        self,
        session: aiohttp.ClientSession,
        headers: dict,
        cmd_type: str,
        extra: dict | None = None,
    ) -> str:
        payload = {"model": "DFT-R102", "type": cmd_type, **(extra or {})}
        async with session.post(
            f"{R2_API_URL}/sdk/cmdRequest", json=payload, headers=headers
        ) as resp:
            resp.raise_for_status()
            data = await resp.json()
            return data["data"]

    async def _server_dev_respond(
        self,
        session: aiohttp.ClientSession,
        headers: dict,
        content: str,
        sn: str = "",
        mac: str = "",
    ) -> dict:
        payload = {"model": "DFT-R102", "content": content, "sn": sn, "mac": mac}
        async with session.post(
            f"{R2_API_URL}/sdk/devRespond", json=payload, headers=headers
        ) as resp:
            resp.raise_for_status()
            data = await resp.json()
            return data["data"]

    async def _write_and_wait(
        self, cmd: bytes, wait: bool = True, timeout: float = 10.0
    ) -> bytes:
        """Write command to encrypted channel and wait for device response."""
        if wait:
            loop = asyncio.get_event_loop()
            self._auth_response = loop.create_future()

        await self._client.write_gatt_char(
            self._char_encrypted.uuid, cmd, response=False
        )

        if not wait:
            return b""

        return await asyncio.wait_for(self._auth_response, timeout=timeout)

    def _on_auth_notification(self, _sender: Any, raw: bytearray) -> None:
        if self._auth_response and not self._auth_response.done():
            self._auth_response.set_result(bytes(raw))

    def _on_data_notification(self, _sender: Any, raw: bytearray) -> None:
        if len(raw) < 6 or raw[0] != 0xDF or raw[1] != 0xDF:
            return

        func, cmd, data_len = raw[2], raw[3], raw[4]
        if len(raw) < 5 + data_len + 1:
            return
        payload = raw[5 : 5 + data_len]
        updated = False

        if func == 0x03 and cmd in (0x00, 0x01, 0x02) and data_len >= 1:
            pkg_no = payload[0]

            if pkg_no == 0x00 and data_len >= 2:
                # Test status packet
                status_code = payload[1]
                self.data.test_status = R2_STATUS_MAP.get(status_code, "Unknown")
                updated = True

            elif pkg_no == 0x01 and data_len >= 6:
                # Temperature packet
                prism_raw = int.from_bytes(payload[1:3], "big", signed=True)
                sample_raw = int.from_bytes(payload[3:5], "big", signed=True)
                temp_unit_idx = payload[5]
                self.data.temperature_unit = "°F" if temp_unit_idx == 1 else "°C"
                self.data.prism_temperature = prism_raw / 10.0
                self.data.sample_temperature = sample_raw / 10.0
                updated = True

            elif pkg_no == 0x02 and data_len >= 7:
                # Result packet: concentration + refractive index
                conc_raw = int.from_bytes(payload[1:3], "big", signed=True)
                ri_raw = int.from_bytes(payload[3:7], "big", signed=True)
                self.data.concentration = conc_raw / 100.0
                self.data.refractive_index = ri_raw / 100000.0
                updated = True

        elif func == 0x01 and cmd == 0x00 and data_len >= 1:
            # Temperature unit setting
            self.data.temperature_unit = "°F" if payload[0] == 1 else "°C"
            updated = True

        if updated:
            self.async_set_updated_data(self.data)

    def _on_disconnect(self, _client: BleakClient) -> None:
        _LOGGER.warning("Difluid R2 %s disconnected, will retry", self.address)
        if self._reconnect_task and not self._reconnect_task.done():
            return
        self._reconnect_task = self.hass.async_create_task(
            self._reconnect_loop(), eager_start=False
        )

    async def _reconnect_loop(self) -> None:
        self.data.authenticated = False
        for delay in (5, 15, 30, 60, 120):
            await asyncio.sleep(delay)
            try:
                await self._do_connect()
                _LOGGER.info("Reconnected to Difluid R2 %s", self.address)
                return
            except Exception as err:
                _LOGGER.debug("R2 reconnect attempt failed (%ss): %s", delay, err)
        _LOGGER.error("Failed to reconnect to Difluid R2 %s after retries", self.address)

    async def _async_update_data(self) -> R2Data:
        return self.data
