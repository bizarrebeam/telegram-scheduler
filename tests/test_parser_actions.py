from app.parsing.basic import is_overwhelm, parse_extension


def test_extension_bare_plus_minutes() -> None:
    assert parse_extension("+30") == 30
    assert parse_extension("+ 15 menit") == 15
    assert parse_extension("+45m") == 45


def test_extension_natural_language() -> None:
    assert parse_extension("butuh 20 menit lagi") == 20
    assert parse_extension("tambah 10 min") == 10
    assert parse_extension("extend 25") == 25
    assert parse_extension("perpanjang 40 menit") == 40


def test_extension_rejects_non_extension_text() -> None:
    assert parse_extension("Apply MT, butuh 1 jam") is None
    assert parse_extension("") is None
    assert parse_extension("+500") is None
    assert parse_extension("+0") is None


def test_extension_english() -> None:
    assert parse_extension("+30 minutes") == 30
    assert parse_extension("add 20 minutes") == 20
    assert parse_extension("I need 15 more minutes") == 15
    assert parse_extension("extend 45 mins") == 45


def test_overwhelm_phrases() -> None:
    assert is_overwhelm("aku bingung") is True
    assert is_overwhelm("kewalahan banget") is True
    assert is_overwhelm("bantu pilih dong") is True
    assert is_overwhelm("mulai dari mana ya") is True
    assert is_overwhelm("what should i do") is True
    assert is_overwhelm("I'm stuck") is True
    assert is_overwhelm("overwhelmed") is True
    assert is_overwhelm("I don't know where to start") is True


def test_overwhelm_false_for_normal_capture() -> None:
    assert is_overwhelm("kirim email ke dosen besok") is False
    assert is_overwhelm("") is False
