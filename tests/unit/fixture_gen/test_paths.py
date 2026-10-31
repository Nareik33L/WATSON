from __future__ import annotations

from watson_fixturegen.paths import fonts_dir, scenarios_dir, templates_dir


def test_package_data_dirs_exist() -> None:
    fonts = fonts_dir()
    assert (fonts / "Inter-Regular.ttf").is_file()
    assert (fonts / "Inter-SemiBold.ttf").is_file()
    assert (fonts / "OFL.txt").is_file()
    assert (scenarios_dir() / "day_acme_q4.yaml").is_file()
    assert (templates_dir() / "base.html").is_file()
    assert fonts.name == "fonts"
