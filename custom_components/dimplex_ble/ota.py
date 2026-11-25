"""Firmware update over BLE, exactly as ConfigR does it (ESP32 path).

Every byte below was read from ConfigR's code and checked live on AM2086
(docs/ota-research.md §B and "Live handshake 2026-11-25"):

* the image goes to the heater's spare slot; the heater only boots it once the
  whole image arrived and verified, so a broken transfer leaves the old
  firmware running (ESP-IDF OTA model, INFERENCE from the image);
* frames ``01 | cmd | len LE16 | payload | cksum | 17``; replies
  ``01 | status | cksum | 17`` with status 0 = OK;
* the image checksum is CRC-16/AUG-CCITT over the whole image.
"""

from __future__ import annotations

import asyncio
import hashlib
import io
import json
import re
import zipfile
from collections.abc import Awaitable, Callable
from contextlib import suppress
from dataclasses import dataclass

ENTER = 0x38
ROW = 0x39  # non-Zigbee products (PLXE)
VERIFY = 0x35
EXIT = 0x3B
MAX_ROW = 236  # ConfigR: MTU 247 − 11
REPLY_TIMEOUT = 30.0  # ConfigR's inactivity timer
EXIT_SETTLE_S = 1.0  # ConfigR waits a second after ExitOTA
ESP_IMAGE_MAGIC = 0xE9


class FirmwareFileError(ValueError):
    """The firmware file is not a valid image for this heater."""


class UpdateError(Exception):
    """The heater refused or stopped answering during the update."""


@dataclass(frozen=True, slots=True)
class Image:
    """One checked firmware image from a Dimplex release ZIP."""

    ep: str  # e.g. "EP4213"
    version: str  # e.g. "5.0"
    data: bytes

    @property
    def crc(self) -> int:
        """Return the checksum ConfigR sends in the verify frame."""
        return crc16_aug_ccitt(self.data)


def crc16_aug_ccitt(data: bytes) -> int:
    """CRC-16/AUG-CCITT (ConfigR ``OtaHelper.CalculateFileChecksum``)."""
    crc = 0x1D0F
    for byte in data:
        crc ^= byte << 8
        for _ in range(8):
            crc = (
                ((crc << 1) ^ 0x1021) & 0xFFFF if crc & 0x8000 else (crc << 1) & 0xFFFF
            )
    return crc


def frame(cmd: int, payload: bytes = b"") -> bytes:
    """Build one command frame (``OtaHelper.CalcChecksum`` seeded with 0x17)."""
    head = bytes((0x01, cmd)) + len(payload).to_bytes(2, "little") + payload
    return head + bytes(((-(0x17 + sum(head))) & 0xFF, 0x17))


def reply_status(reply: bytes) -> int:
    """Return a reply's status byte; raise on a malformed reply."""
    if (
        len(reply) != 4
        or reply[0] != 0x01
        or reply[3] != 0x17
        or (0x17 + reply[0] + reply[1] + reply[2]) & 0xFF
    ):
        raise UpdateError(f"Malformed reply {reply.hex()}")
    return reply[1]


def enter_frame(image: Image) -> bytes:
    """EnterOTA: the EP digits, fixed bytes, then the image size (LE32)."""
    payload = (
        image.ep[2:6].encode("ascii")
        + bytes((0, 0, 0xFF, 0, 0))
        + len(image.data).to_bytes(4, "little")
    )
    return frame(ENTER, payload)


def load_release(zip_bytes: bytes, heater_ep: str) -> Image:
    """Check a Dimplex release ZIP and return its single image for this heater.

    Refuses anything ConfigR would refuse, plus a broken ESP32 image.
    """
    try:
        archive = zipfile.ZipFile(io.BytesIO(zip_bytes))
        config = json.loads(archive.read("release_config.json"))
    except (zipfile.BadZipFile, KeyError, ValueError) as err:
        raise FirmwareFileError("Not a Dimplex firmware release") from err
    files = config.get("ota_file_structure") or []
    if len(files) != 1:
        raise FirmwareFileError("Only single-image releases are supported")
    item = files[0]
    name, version = str(item.get("filename", "")), str(item.get("version", ""))
    if item.get("module") != "UI" or not re.fullmatch(r"EP\d{4}_.+\.bin", name):
        raise FirmwareFileError("Not a heater display-firmware image")
    ep = name.split("_", 1)[0]
    allowed = {ep, *map(str, item.get("productcodes") or ())}
    if heater_ep not in allowed:
        raise FirmwareFileError(f"Image is for {ep}, the heater is {heater_ep}")
    try:
        data = archive.read(name)
    except KeyError as err:
        raise FirmwareFileError("The image named in the release is missing") from err
    _check_esp_image(data)
    return Image(ep, version, data)


def _check_esp_image(data: bytes) -> None:
    """An ESP32 app image whose appended SHA-256 matches (bootloader's check)."""
    if len(data) < 64 or data[0] != ESP_IMAGE_MAGIC or data[23] != 1:
        raise FirmwareFileError("Not an ESP32 app image with a hash")
    if hashlib.sha256(data[:-32]).digest() != data[-32:]:
        raise FirmwareFileError("The image is corrupt (SHA-256 mismatch)")


@dataclass(slots=True)
class Transport:
    """How the update talks to the heater (the BLE link supplies these)."""

    set_ota_mode: Callable[[bool], Awaitable[None]]  # write 1/0x300E
    send: Callable[[bytes], Awaitable[None]]  # write C51D with response
    reply: Callable[[float], Awaitable[bytes]]  # next C51D notification


async def async_flash(
    transport: Transport,
    image: Image,
    row_size: int,
    progress: Callable[[float], None] | None = None,
) -> None:
    """Send the image; on any failure leave update mode before re-raising."""
    if not 1 <= row_size <= MAX_ROW:
        raise UpdateError(f"Row size {row_size} out of range")

    async def step(name: str, data: bytes) -> None:
        await transport.send(data)
        try:
            reply = await transport.reply(REPLY_TIMEOUT)
        except TimeoutError as err:
            raise UpdateError(f"No reply to {name}") from err
        if (status := reply_status(reply)) != 0:
            raise UpdateError(f"Heater refused {name} (status {status})")

    try:
        # Inside the cleanup: the heater may apply the write and lose the ack.
        await transport.set_ota_mode(True)
        await step("enter", enter_frame(image))
        rows = range(0, len(image.data), row_size)
        for index, offset in enumerate(rows, 1):
            await step("row", frame(ROW, image.data[offset : offset + row_size]))
            if progress is not None:
                progress(index / len(rows))
        await step("verify", frame(VERIFY, image.crc.to_bytes(2, "little")))
    except BaseException:
        # Best effort: on a lost link these fail too, and must not replace the
        # reason the update stopped (rehearsal 2026-11-25, attempt 3).
        with suppress(Exception):
            await transport.send(frame(EXIT))
        with suppress(Exception):
            await transport.set_ota_mode(False)
        raise
    # Verified: like ConfigR, exit, wait a second, then leave update mode
    # (docs/ota-research.md B.6). The heater may reboot into the new image
    # meanwhile, so both are best effort; the version read after reconnecting
    # confirms the update.
    with suppress(Exception):
        await transport.send(frame(EXIT))
    await asyncio.sleep(EXIT_SETTLE_S)
    with suppress(Exception):
        await transport.set_ota_mode(False)
