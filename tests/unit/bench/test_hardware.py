from __future__ import annotations

from watson_bench.hardware import detect_hardware, format_hardware


def test_detect_hardware_has_required_fields() -> None:
    info = detect_hardware()
    assert info.chip
    assert info.ram_gb >= 0
    assert info.os_name
    assert info.os_version
    assert info.arch
    assert info.python_version
    text = format_hardware(info)
    assert info.os_name in text
    assert "RAM" in text
