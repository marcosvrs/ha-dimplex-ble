"""Firmware update logic: file checks, frames and the step sequence."""

from __future__ import annotations

import hashlib
import io
import json
import zipfile

import pytest

from custom_components.dimplex_ble import ota


def esp_image(size: int = 1000) -> bytes:
    body = bytearray(size)
    body[0], body[23] = ota.ESP_IMAGE_MAGIC, 1
    return bytes(body) + hashlib.sha256(bytes(body)).digest()


def release(image: bytes, **item: object) -> bytes:
    entry = {
        "module": "UI",
        "seqid": "1",
        "filename": "EP4213_IUI_5_0_PRD_240514.bin",
        "version": "5.0",
        "verification": "0xB8AB",
        **item,
    }
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(
            "release_config.json", json.dumps({"ota_file_structure": [entry]})
        )
        archive.writestr(str(entry["filename"]), image)
    return buffer.getvalue()


def test_crc_is_aug_ccitt() -> None:
    assert ota.crc16_aug_ccitt(b"123456789") == 0xE5CC


def test_frames_match_the_live_handshake() -> None:
    """Bytes sent to AM2086 on 2026-11-25 12:31 UTC."""
    image = ota.Image("EP4213", "5.0", bytes(906848))
    assert ota.enter_frame(image).hex() == "01380d00343231330000ff000060d60d009717"
    assert ota.frame(ota.EXIT).hex() == "013b0000ad17"


def test_reply_status() -> None:
    assert ota.reply_status(bytes.fromhex("0100e817")) == 0
    for bad in ("0100e818", "0100e8", "0200e717"):
        with pytest.raises(ota.UpdateError):
            ota.reply_status(bytes.fromhex(bad))


def test_load_release_accepts_a_matching_image() -> None:
    image = ota.load_release(release(esp_image()), "EP4213")
    assert (image.ep, image.version) == ("EP4213", "5.0")


@pytest.mark.parametrize(
    ("zip_bytes", "ep"),
    [
        (b"not a zip", "EP4213"),
        (release(esp_image()), "EP9999"),
        (release(esp_image(), module="ACC"), "EP4213"),
        (release(esp_image()[:-1] + b"\0"), "EP4213"),
        (release(b"\0" * 100), "EP4213"),
        (release(esp_image(), filename="other.bin"), "EP4213"),
    ],
    ids=["not-zip", "other-ep", "not-ui", "bad-hash", "not-esp", "bad-name"],
)
def test_load_release_refuses(zip_bytes: bytes, ep: str) -> None:
    with pytest.raises(ota.FirmwareFileError):
        ota.load_release(zip_bytes, ep)


def test_load_release_refuses_a_missing_or_second_image() -> None:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(
            "release_config.json",
            json.dumps(
                {
                    "ota_file_structure": [
                        {"module": "UI", "filename": "EP4213_a.bin", "version": "1"}
                    ]
                }
            ),
        )
    with pytest.raises(ota.FirmwareFileError):
        ota.load_release(buffer.getvalue(), "EP4213")
    two = json.dumps({"ota_file_structure": [{}, {}]})
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("release_config.json", two)
    with pytest.raises(ota.FirmwareFileError):
        ota.load_release(buffer.getvalue(), "EP4213")


OK = bytes.fromhex("0100e817")
BAD = bytes((0x01, 0x05, (-(0x17 + 1 + 5)) & 0xFF, 0x17))


class FakeHeater:
    def __init__(self, replies: list[bytes | None]) -> None:
        self.replies = replies
        self.sent: list[bytes] = []
        self.modes: list[bool] = []

    async def set_mode(self, on: bool) -> None:
        self.modes.append(on)

    async def send(self, data: bytes) -> None:
        self.sent.append(data)

    async def reply(self, wait_s: float) -> bytes:
        if not self.replies:
            raise TimeoutError
        reply = self.replies.pop(0)
        if reply is None:
            raise TimeoutError
        return reply

    def transport(self) -> ota.Transport:
        return ota.Transport(self.set_mode, self.send, self.reply)


async def test_a_full_update_sends_every_row_then_verifies() -> None:
    image = ota.Image("EP4213", "5.0", bytes(range(256)) * 2)  # 512 bytes
    heater = FakeHeater([OK] * (1 + 3 + 1) + [OK])
    seen: list[float] = []

    await ota.async_flash(heater.transport(), image, 236, seen.append)

    cmds = [frame[1] for frame in heater.sent]
    assert cmds == [ota.ENTER, ota.ROW, ota.ROW, ota.ROW, ota.VERIFY, ota.EXIT]
    assert heater.sent[3][2:4] == (512 - 472).to_bytes(2, "little")  # remainder
    assert heater.sent[4][4:6] == image.crc.to_bytes(2, "little")
    assert heater.modes == [True, False] and seen[-1] == 1.0


async def test_a_reboot_on_exit_is_not_a_failure() -> None:
    image = ota.Image("EP4213", "5.0", bytes(10))
    heater = FakeHeater([OK, OK, OK])  # no exit reply

    async def gone(on: bool) -> None:
        if not on:
            raise OSError("link lost")
        heater.modes.append(on)

    await ota.async_flash(ota.Transport(gone, heater.send, heater.reply), image, 236)
    assert heater.sent[-1] == ota.frame(ota.EXIT)


@pytest.mark.parametrize("failing", [0, 1, 2])  # enter, row, verify
@pytest.mark.parametrize("reply", [None, BAD], ids=["timeout", "refused"])
async def test_any_failure_exits_and_leaves_update_mode(failing, reply) -> None:
    image = ota.Image("EP4213", "5.0", bytes(10))
    heater = FakeHeater([OK] * failing + [reply])

    with pytest.raises(ota.UpdateError):
        await ota.async_flash(heater.transport(), image, 236)

    assert heater.sent[-1] == ota.frame(ota.EXIT)
    assert heater.modes == [True, False]


async def test_a_row_size_over_configrs_is_refused() -> None:
    heater = FakeHeater([])
    with pytest.raises(ota.UpdateError):
        await ota.async_flash(heater.transport(), ota.Image("EP4213", "5", b"x"), 237)
    assert heater.sent == [] and heater.modes == []


async def test_the_real_release_is_accepted_when_present() -> None:
    from pathlib import Path

    path = (
        Path.home() / ".local/share/dimplex-ota/firmware/EP4213_IUI_5_0_PRD_240514.zip"
    )
    if not path.exists():
        pytest.skip("release not downloaded")
    image = ota.load_release(path.read_bytes(), "EP4213")
    assert (image.version, len(image.data), image.crc) == ("5.0", 906848, 0x37E5)


async def test_a_failing_cleanup_never_hides_why_the_update_stopped() -> None:
    """Rehearsal 2026-11-25 attempt 3: the link was gone, cleanup failed too."""
    image = ota.Image("EP4213", "5.0", bytes(10))
    heater = FakeHeater([OK, BAD])
    modes: list[bool] = []

    async def set_mode(on: bool) -> None:
        if not on:
            raise OSError("Characteristic was not found")
        modes.append(on)

    async def send(data: bytes) -> None:
        if data == ota.frame(ota.EXIT):
            raise OSError("not connected")
        heater.sent.append(data)

    with pytest.raises(ota.UpdateError, match="refused row"):
        await ota.async_flash(ota.Transport(set_mode, send, heater.reply), image, 236)
