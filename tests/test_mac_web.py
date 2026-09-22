from pathlib import Path
import plistlib
from types import SimpleNamespace
from unittest.mock import Mock

from stem_studio import mac_web


def test_local_build_never_inherits_cloud_secrets(monkeypatch):
    monkeypatch.setenv("PROCESSOR_SHARED_SECRET", "a-cloud-secret")
    monkeypatch.setenv("STEM_STUDIO_PASSWORD", "a-cloud-password")
    monkeypatch.setenv("NEXT_PUBLIC_PROCESSOR_URL", "https://cloud.invalid")
    environment = mac_web.runtime_environment()
    assert environment["PROCESSOR_SHARED_SECRET"] == ""
    assert environment["STEM_STUDIO_PASSWORD"] == ""
    assert environment["NEXT_PUBLIC_PROCESSOR_URL"] == "http://127.0.0.1:8766"
    assert environment["STEM_STUDIO_LOCAL_WEB"] == "1"
    assert environment["STEM_STUDIO_PROCESSOR_LOCATION"] == "mac"
    assert mac_web.runtime_environment("new-secret")["PROCESSOR_SHARED_SECRET"] == "new-secret"


def prepare(monkeypatch):
    monkeypatch.setattr(mac_web.sys, "platform", "darwin")
    monkeypatch.setattr(mac_web.shutil, "which", lambda _: "/bin/node")
    monkeypatch.setattr(mac_web.Path, "is_file", lambda _: True)


def test_build_does_not_read_keychain(monkeypatch):
    prepare(monkeypatch)
    secret = Mock(side_effect=AssertionError("Do not access Keychain during build"))
    monkeypatch.setattr(mac_web, "load_secret", secret)
    run = Mock(return_value=SimpleNamespace(returncode=0))
    monkeypatch.setattr(mac_web.subprocess, "run", run)
    assert mac_web.main(["build"]) == 0
    assert run.call_args.args[0][-1] == "build"
    assert run.call_args.kwargs["env"]["PROCESSOR_SHARED_SECRET"] == ""
    secret.assert_not_called()


def test_start_execs_loopback_with_key_only_in_environment(monkeypatch):
    prepare(monkeypatch)
    monkeypatch.setattr(mac_web, "load_secret", lambda: "safe-key" * 6)
    monkeypatch.setattr(mac_web.os, "chdir", Mock())
    monkeypatch.setattr(mac_web.os, "umask", Mock())
    execute = Mock()
    monkeypatch.setattr(mac_web.os, "execve", execute)
    assert mac_web.main([]) == 0
    binary, argv, environment = execute.call_args.args
    assert binary == "/bin/node"
    assert argv[-5:] == ["start", "--hostname", "127.0.0.1", "--port", "3007"]
    assert environment["PROCESSOR_SHARED_SECRET"] == "safe-key" * 6
    assert "safe-key" not in repr(argv)


def test_missing_keychain_fails_closed(monkeypatch, capsys):
    prepare(monkeypatch)
    monkeypatch.setattr(mac_web, "load_secret", Mock(side_effect=mac_web.KeychainError("Unlock Keychain.")))
    execute = Mock()
    monkeypatch.setattr(mac_web.os, "execve", execute)
    assert mac_web.main([]) == 1
    execute.assert_not_called()
    assert "Unlock Keychain" in capsys.readouterr().err


def test_web_launchagent_contains_no_secrets():
    root = Path(__file__).resolve().parent.parent
    with (root / "scripts/com.stemstudio.web.plist").open("rb") as handle:
        config = plistlib.load(handle)
    assert config["Label"] == "com.stemstudio.web"
    assert config["ProgramArguments"][1:] == ["-m", "stem_studio.mac_web"]
    assert config["AbandonProcessGroup"] is False
    assert config["RunAtLoad"] is True
    assert config["Umask"] == 0o077
    assert "SECRET" not in str(config)
