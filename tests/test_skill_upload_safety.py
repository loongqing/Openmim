import io
import zipfile

import pytest

import app_config.config as config
import integrations.skill_market_client as market
import plugins.web_panel.skill_upload as upload


def _skill_md(name="demo"):
    return f"---\nname: {name}\ndescription: demo skill\n---\n# Demo\n"


def _zip(entries, compression=zipfile.ZIP_DEFLATED):
    body = io.BytesIO()
    with zipfile.ZipFile(body, "w", compression=compression) as archive:
        for name, content in entries.items():
            archive.writestr(name, content)
    return body.getvalue()


def test_zip_install_is_visible_to_market_scanner(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "LOCAL_SKILL_ROOT", str(tmp_path))
    result = upload.install_skill_zip(
        _zip({"package/SKILL.md": _skill_md(), "package/assets/info.txt": "ok"}),
        skill_root=market.get_skill_root(),
    )

    assert result["name"] == "demo"
    assert market._scan_skills()[0]["id"] == "demo"
    assert (tmp_path / "demo" / "assets" / "info.txt").read_text() == "ok"


def test_zip_limits_entry_count(monkeypatch):
    monkeypatch.setattr(upload, "MAX_ZIP_ENTRIES", 2)
    body = _zip({"SKILL.md": _skill_md(), "a": "1", "b": "2"})
    with pytest.raises(ValueError, match="too many entries"):
        upload.validate_zip_entries(io.BytesIO(body))


def test_zip_limits_single_file_total_size_and_ratio(monkeypatch):
    body = _zip({"SKILL.md": _skill_md(), "large.txt": "x" * 1000})

    monkeypatch.setattr(upload, "MAX_ZIP_FILE_BYTES", 500)
    with pytest.raises(ValueError, match="entry too large"):
        upload.validate_zip_entries(io.BytesIO(body))

    monkeypatch.setattr(upload, "MAX_ZIP_FILE_BYTES", 2000)
    monkeypatch.setattr(upload, "MAX_ZIP_TOTAL_BYTES", 500)
    with pytest.raises(ValueError, match="expands beyond"):
        upload.validate_zip_entries(io.BytesIO(body))

    monkeypatch.setattr(upload, "MAX_ZIP_TOTAL_BYTES", 5000)
    monkeypatch.setattr(upload, "MAX_ZIP_COMPRESSION_RATIO", 2)
    with pytest.raises(ValueError, match="compression ratio"):
        upload.validate_zip_entries(io.BytesIO(body))


def test_overwrite_failure_restores_existing_skill(tmp_path, monkeypatch):
    target = tmp_path / "demo"
    target.mkdir()
    (target / "SKILL.md").write_text("old", encoding="utf-8")
    real_replace = upload.os.replace
    calls = 0

    def fail_promotion(src, dst):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("promotion failed")
        return real_replace(src, dst)

    monkeypatch.setattr(upload.os, "replace", fail_promotion)
    with pytest.raises(OSError, match="promotion failed"):
        upload.install_skill_zip(
            _zip({"SKILL.md": _skill_md()}),
            skill_root=tmp_path,
            overwrite=True,
        )

    assert (target / "SKILL.md").read_text(encoding="utf-8") == "old"
    assert not (tmp_path / ".demo.backup").exists()


@pytest.mark.parametrize("name", ["../SKILL.md", "/SKILL.md", "C:/SKILL.md", "..\\SKILL.md"])
def test_zip_rejects_unsafe_paths(name):
    with pytest.raises(ValueError, match="unsafe zip entry"):
        upload.validate_zip_entries(io.BytesIO(_zip({name: _skill_md()})))
