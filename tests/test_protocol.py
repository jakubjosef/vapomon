"""Tests for the pure decode/detection logic (no hardware needed)."""
from vapomon.protocol import (
    Model,
    decode_crafty_target,
    decode_simple_temp,
    decode_u16,
    decode_venty_frame,
    decode_venty_status,
    detect_model,
    u16le,
)


def test_u16le_is_little_endian():
    assert u16le(b"\x2c\x01") == 300          # 0x012C
    assert u16le(b"\x00\x00") == 0
    assert u16le(b"\xff\xff") == 0xFFFF


def test_decode_simple_temp():
    assert decode_simple_temp(b"\x2c\x01") == 30.0   # 300 -> 30.0 °C
    assert decode_simple_temp(b"\xfe\x07") == 204.6  # 0x07FE = 2046
    assert decode_simple_temp(b"\xff\xff") is None    # sentinel = no reading
    assert decode_simple_temp(b"\x05") is None        # too short


def test_decode_venty_frame():
    frame = bytearray(20)
    frame[0] = 0x01           # status response to command 0x01
    frame[2] = 0x2c           # temp low byte
    frame[3] = 0x01           # temp high byte -> 0x012C = 300 -> 30.0 °C
    assert decode_venty_frame(bytes(frame)) == 30.0

    frame[0] = 0x30           # case 48 -> bootloader frame, not a temperature
    assert decode_venty_frame(bytes(frame)) is None

    assert decode_venty_frame(b"\x01" + b"\x00" * 5) is None   # too short (<15)
    assert decode_venty_frame(b"\x00" * 20) is None            # wrong frame type


def test_decode_u16():
    assert decode_u16(b"\x0c\x00") == 12
    assert decode_u16(b"\x10\x0e") == 3600
    assert decode_u16(b"\x05") is None


def test_decode_crafty_target():
    # 0x07F4 = 2036 -> 203.6 °C (<= 210, kept as-is)
    assert decode_crafty_target(b"\xf4\x07") == 203.6
    # 0x0FA0 = 4000 -> 400.0 -> > 210, treated as °F -> (400-32)/1.8 = 204.4 °C
    assert decode_crafty_target(b"\xa0\x0f") == 204.4
    assert decode_crafty_target(b"") is None


def test_decode_venty_status():
    frame = bytearray(20)
    frame[0] = 0x01
    frame[2], frame[3] = 0x2c, 0x01     # current 0x012C = 300 -> 30.0
    frame[4], frame[5] = 0xc2, 0x07     # target  0x07C2 = 1986 -> 198.6
    frame[6] = 15                        # boost
    frame[7] = 20                        # superboost
    frame[8] = 87                        # battery %
    frame[9], frame[10] = 100, 8        # auto-off = 108 s
    frame[11] = 2                        # heat mode = boost
    frame[13] = 1                        # charging
    frame[14] = 0b10                     # BIT_VENTY_READY set
    status = decode_venty_status(bytes(frame))
    assert status["current_temp_c"] == 30.0
    assert status["target_temp_c"] == 198.6
    assert status["boost_temp_c"] == 15.0
    assert status["superboost_temp_c"] == 20.0
    assert status["battery_percent"] == 87
    assert status["auto_off_seconds"] == 108
    assert status["heat_mode"] == "boost"
    assert status["heating"] is True
    assert status["charging"] is True
    assert status["ready"] is True

    assert decode_venty_status(b"\x30" + b"\x00" * 19) is None  # bootloader frame
    assert decode_venty_status(b"\x01\x00") is None             # too short


def test_detect_model():
    assert detect_model("S&B VOLCANO H 12345") == Model.VOLCANO
    assert detect_model("S&B VY 67890") == Model.VENTY
    assert detect_model("S&B VZ 67890") == Model.VEAZY
    assert detect_model("STORZ&BICKEL 1234") == Model.CRAFTY
    assert detect_model("Storz&Bickel CY 9") == Model.CRAFTY
    assert detect_model("Some Other Device") is None
    assert detect_model("") is None
    assert detect_model(None) is None
