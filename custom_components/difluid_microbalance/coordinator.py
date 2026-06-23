from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any, Optional

from bleak import BleakClient
from bleak.backends.characteristic import BleakGATTCharacteristic
from bleak.exc import BleakError

from homeassistant.components import bluetooth
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .const import (
    CHARACTERISTIC_UUID_MICROBALANCE,
    CHARACTERISTIC_UUID_MICROBALANCE_TI,
    DEVICE_STATUS_MAP,
    DOMAIN,
    SERVICE_UUID_MICROBALANCE,
    SERVICE_UUID_MICROBALANCE_TI,
    WEIGHT_UNITS,
)

_LOGGER = logging.getLogger(__name__)

_HEADER = bytes([0xDF, 0xDF])


def _build_cmd(func: int, cmd: int, data: bytes = b"") -> bytes:
    frame = bytes([func, cmd, len(data)]) + data
    full = _HEADER + frame
    return full + bytes([sum(full) & 0xFF])


_CMD_AUTO_SEND_ON = _build_cmd(0x01, 0x00, bytes([0x01]))
_CMD_GET_STATUS = _build_cmd(0x03, 0x05)

_STATUS_POLL_INTERVAL = 60

# Preferred communication characteristic per device type (from protocol docs)
_PREFERRED_CHAR = {
    SERVICE_UUID_MICROBALANCE: CHARACTERISTIC_UUID_MICROBALANCE,       # FF01
    SERVICE_UUID_MICROBALANCE_TI: CHARACTERISTIC_UUID_MICROBALANCE_TI, # AA01
}


@dataclass
class MicrobalanceData:
    weight: float = 0.0
    weight_unit: str = "g"
    flow_rate: float = 0.0
    timer: int = 0
    battery: int = 0
    charging: bool = False
    device_status: str = "Unknown"


class DifluidMicrobalanceCoordinator(DataUpdateCoordinator[MicrobalanceData]):

    def __init__(self, hass: HomeAssistant, address: str, is_ti: bool = False) -> None:
        super().__init__(hass, _LOGGER, name=f"{DOMAIN}_{address}", update_interval=None)
        self.address = address
        self.is_ti = is_ti
        self._preferred_char_uuid = (
            CHARACTERISTIC_UUID_MICROBALANCE_TI if is_ti else CHARACTERISTIC_UUID_MICROBALANCE
        )
        self._write_char_uuid: Optional[str] = None
        self._client: Optional[BleakClient] = None
        self._poll_task: Optional[asyncio.Task] = None
        self._reconnect_task: Optional[asyncio.Task] = None
        self.data = MicrobalanceData()

    async def async_start(self) -> None:
        await self._do_connect()

    async def async_stop(self) -> None:
        for task in (self._poll_task, self._reconnect_task):
            if task and not task.done():
                task.cancel()
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
        _LOGGER.info("Connected to Difluid Microbalance %s", self.address)

        # Log all discovered services and characteristics for diagnostics
        for svc in client.services:
            _LOGGER.info("  Service: %s", svc.uuid)
            for char in svc.characteristics:
                _LOGGER.info(
                    "    Characteristic: %s  props=%s",
                    char.uuid, char.properties,
                )

        write_uuid, notify_uuids = self._pick_characteristics(client)

        if not notify_uuids:
            raise RuntimeError(
                "No notifiable characteristics found on device — "
                "check HA logs (set log level debug for custom_components.difluid_microbalance)"
            )

        # Subscribe to all notification sources (handles both AA01 and FF01 if present)
        for uuid in notify_uuids:
            try:
                await client.start_notify(uuid, self._on_notification)
                _LOGGER.info("Subscribed to notifications on %s", uuid)
            except BleakError as err:
                _LOGGER.warning("Could not subscribe to %s: %s", uuid, err)

        self._write_char_uuid = write_uuid
        _LOGGER.info("Using %s for write commands", write_uuid)

        await client.write_gatt_char(write_uuid, _CMD_AUTO_SEND_ON, response=False)
        await client.write_gatt_char(write_uuid, _CMD_GET_STATUS, response=False)
        self._client = client

        if self._poll_task and not self._poll_task.done():
            self._poll_task.cancel()
        self._poll_task = self.hass.async_create_task(
            self._poll_status_loop(), eager_start=False
        )

    def _pick_characteristics(
        self, client: BleakClient
    ) -> tuple[str, list[str]]:
        """Return (write_uuid, [notify_uuids]) choosing the best available chars."""
        all_chars: list[BleakGATTCharacteristic] = [
            c for svc in client.services for c in svc.characteristics
        ]

        # Build sets by property
        notify_chars = [
            c for c in all_chars
            if "notify" in c.properties or "indicate" in c.properties
        ]
        write_chars = [
            c for c in all_chars
            if "write-without-response" in c.properties or "write" in c.properties
        ]

        # Prefer the documented characteristic if it exists on the device
        preferred_lower = self._preferred_char_uuid.lower()
        write_uuid = preferred_lower

        # If preferred not found among write chars, fall back to first writable char
        write_uuids = {c.uuid.lower() for c in write_chars}
        if preferred_lower not in write_uuids and write_chars:
            write_uuid = write_chars[0].uuid.lower()
            _LOGGER.warning(
                "Preferred characteristic %s not found for writing; "
                "falling back to %s",
                preferred_lower, write_uuid,
            )

        notify_uuids = [c.uuid.lower() for c in notify_chars]
        return write_uuid, notify_uuids

    async def _poll_status_loop(self) -> None:
        while True:
            await asyncio.sleep(_STATUS_POLL_INTERVAL)
            if self._client and self._client.is_connected and self._write_char_uuid:
                try:
                    await self._client.write_gatt_char(
                        self._write_char_uuid, _CMD_GET_STATUS, response=False
                    )
                except Exception as err:
                    _LOGGER.debug("Status poll write failed: %s", err)

    def _on_disconnect(self, _client: BleakClient) -> None:
        _LOGGER.warning("Difluid Microbalance %s disconnected, will retry", self.address)
        if self._poll_task and not self._poll_task.done():
            self._poll_task.cancel()
        if self._reconnect_task and not self._reconnect_task.done():
            return
        self._reconnect_task = self.hass.async_create_task(
            self._reconnect_loop(), eager_start=False
        )

    async def _reconnect_loop(self) -> None:
        for delay in (5, 15, 30, 60, 120):
            await asyncio.sleep(delay)
            try:
                await self._do_connect()
                _LOGGER.info("Reconnected to Difluid Microbalance %s", self.address)
                return
            except Exception as err:
                _LOGGER.debug("Reconnect attempt failed (%ss delay): %s", delay, err)
        _LOGGER.error("Failed to reconnect to Difluid Microbalance %s after retries", self.address)

    def _on_notification(self, sender: Any, raw: bytearray) -> None:
        # Log every raw notification so we can debug unexpected data
        _LOGGER.debug(
            "Notification from %s: %s",
            getattr(sender, "uuid", sender),
            raw.hex(),
        )

        if len(raw) < 6 or raw[0] != 0xDF or raw[1] != 0xDF:
            _LOGGER.debug("Ignoring non-Difluid packet: %s", raw.hex())
            return

        func, cmd, data_len = raw[2], raw[3], raw[4]
        if len(raw) < 5 + data_len + 1:
            _LOGGER.debug("Packet too short (expected %d bytes)", 5 + data_len + 1)
            return
        payload = raw[5 : 5 + data_len]
        updated = False

        if func == 0x03 and cmd == 0x00 and len(payload) >= 13:
            weight_raw = int.from_bytes(payload[0:4], "big")
            unit_idx = payload[12]
            self.data.weight_unit = WEIGHT_UNITS.get(unit_idx, "g")
            self.data.weight = weight_raw / (1000.0 if unit_idx == 1 else 10.0)
            self.data.flow_rate = int.from_bytes(payload[4:6], "big") / 10.0
            self.data.timer = int.from_bytes(payload[6:8], "big")
            updated = True

        elif func == 0x03 and cmd == 0x05 and len(payload) >= 3:
            self.data.device_status = DEVICE_STATUS_MAP.get(payload[0], "Unknown")
            self.data.battery = payload[1]
            self.data.charging = payload[2] == 1
            updated = True

        else:
            _LOGGER.debug("Unhandled Difluid packet func=0x%02x cmd=0x%02x", func, cmd)

        if updated:
            self.async_set_updated_data(self.data)

    async def _async_update_data(self) -> MicrobalanceData:
        return self.data
