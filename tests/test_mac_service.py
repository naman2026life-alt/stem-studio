from pathlib import Path
import plistlib
import shutil
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from stem_studio import mac_service


def test_keychain_command_uses_current_account_without_password_argument(monkeypatch):
    run = Mock(return_value=SimpleNamespace(returncode=0, stdout="x" * 48 + "\n", stderr=""))
    monkeypatch.setattr(mac_service.subprocess, "run", run)
    monkeypatch.setattr(mac_service.pwd, "getpwuid", lambda _: SimpleNamespace(pw_name="test-user"))
    assert mac_service.read_keychain_secret() == "x" * 48
    args, kwargs = run.call_args
    assert args[0] == [
        "/usr/bin/security", "find-generic-password",
        "-a", "test-user", "-s", "Stem Studio Processor", "-w",
    ]
    assert kwargs["capture_output"] is True
    assert kwargs["timeout"] == 15
    assert "x" * 48 not in repr(args)


@pytest.mark.parametrize("value", ["", "short\n", "x" * 48 + "\nextra\n", "x" * 48 + "\0\n"])
def test_invalid_keychain_secret_fails_closed(monkeypatch, value):
    monkeypatch.setattr(
        mac_service.subprocess, "run",
        Mock(return_value=SimpleNamespace(returncode=0, stdout=value, stderr="")),
    )
    with pytest.raises(mac_service.KeychainError, match="at least 32"):
        mac_service.read_keychain_secret("test-user")


def test_keychain_denied_error_does_not_include_subprocess_output(monkeypatch):
    secret = "must-never-be-logged" * 3
    monkeypatch.setattr(
        mac_service.subprocess, "run",
        Mock(return_value=SimpleNamespace(returncode=44, stdout=secret, stderr=secret)),
    )
    with pytest.raises(mac_service.KeychainError) as exc:
        mac_service.read_keychain_secret("test-user")
    assert secret not in str(exc.value)


@pytest.mark.parametrize("exception", [
    FileNotFoundError("a-sensitive-subprocess-error"),
    subprocess.TimeoutExpired("security", 15, output="a-sensitive-subprocess-error"),
])
def test_keychain_subprocess_errors_are_sanitized(monkeypatch, exception):
    monkeypatch.setattr(mac_service.subprocess, "run", Mock(side_effect=exception))
    with pytest.raises(mac_service.KeychainError) as exc:
        mac_service.read_keychain_secret("test-user")
    assert "a-sensitive" not in str(exc.value)


def test_runtime_environment_is_private_and_resource_bounded():
    environment = mac_service.runtime_environment("x" * 48, Path("/project with spaces"), Path("/users/test"))
    assert environment["PROCESSOR_REQUIRE_AUTH"] == "1"
    assert environment["STEM_STUDIO_DATA_DIR"] == "/users/test/Library/Application Support/Stem Studio/processor"
    assert environment["TORCH_HOME"] == "/project with spaces/.model-cache"
    assert environment["STEM_STUDIO_MODEL_DIR"] == environment["TORCH_HOME"]
    assert environment["STEM_STUDIO_CPU_THREADS"] == "4"
    assert environment["MAX_PENDING_JOBS"] == "3"
    assert environment["MAX_PENDING_IMPORTS"] == "1"
    assert environment["JOB_TTL_SECONDS"] == "3600"
    assert all(environment[name] == "4" for name in mac_service.THREAD_VARIABLES)
    assert environment["ALLOWED_ORIGINS"].split(",") == list(mac_service.ALLOWED_ORIGINS)
    assert "*" not in environment["ALLOWED_ORIGINS"]


def test_configuration_overrides_unsafe_inherited_auth_settings(monkeypatch, tmp_path):
    monkeypatch.setattr(mac_service, "read_keychain_secret", lambda: "s" * 48)
    monkeypatch.setattr(mac_service.Path, "home", lambda: tmp_path)
    monkeypatch.setattr(mac_service.os, "umask", Mock())
    monkeypatch.setenv("PROCESSOR_REQUIRE_AUTH", "0")
    monkeypatch.setenv("PROCESSOR_SHARED_SECRET", "unsafe-inherited-secret")
    monkeypatch.setenv("ALLOWED_ORIGINS", "*")
    monkeypatch.setenv("MAX_PENDING_JOBS", "100")
    # Keep every mkdir and environment mutation isolated from real storage.
    monkeypatch.setattr(mac_service.Path, "mkdir", Mock())
    permissions = Mock()
    monkeypatch.setattr(mac_service.Path, "chmod", permissions)
    monkeypatch.setattr(mac_service.os, "environ", dict(mac_service.os.environ))
    mac_service.configure_runtime()
    assert mac_service.os.environ["PROCESSOR_SHARED_SECRET"] == "s" * 48
    assert mac_service.os.environ["PROCESSOR_REQUIRE_AUTH"] == "1"
    assert "*" not in mac_service.os.environ["ALLOWED_ORIGINS"]
    assert mac_service.os.environ["MAX_PENDING_JOBS"] == "3"
    assert permissions.call_count == 2
    assert all(call.args == (0o700,) for call in permissions.call_args_list)
    mac_service.os.umask.assert_called_once_with(0o077)


def test_main_binds_loopback_only_and_disables_credential_logging(monkeypatch):
    monkeypatch.setattr(mac_service.sys, "platform", "darwin")
    configure = Mock()
    monkeypatch.setattr(mac_service, "configure_runtime", configure)
    fake_uvicorn = SimpleNamespace(run=Mock())
    monkeypatch.setitem(sys.modules, "uvicorn", fake_uvicorn)
    assert mac_service.main([]) == 0
    configure.assert_called_once()
    args, kwargs = fake_uvicorn.run.call_args
    assert args == ("processor.api:app",)
    assert kwargs["host"] == "127.0.0.1"
    assert kwargs["port"] == 8766
    assert kwargs["workers"] == 1
    assert kwargs["proxy_headers"] is False
    assert kwargs["access_log"] is False
    assert kwargs["timeout_graceful_shutdown"] == 15


def test_check_does_not_start_server(monkeypatch, capsys):
    monkeypatch.setattr(mac_service.sys, "platform", "darwin")
    monkeypatch.setattr(mac_service, "configure_runtime", Mock())
    fake_uvicorn = SimpleNamespace(run=Mock())
    monkeypatch.setitem(sys.modules, "uvicorn", fake_uvicorn)
    assert mac_service.main(["--check"]) == 0
    fake_uvicorn.run.assert_not_called()
    assert "ready" in capsys.readouterr().out


def test_startup_failure_does_not_start_server(monkeypatch, capsys):
    monkeypatch.setattr(mac_service.sys, "platform", "darwin")
    monkeypatch.setattr(
        mac_service, "configure_runtime", Mock(side_effect=mac_service.KeychainError("Unlock Keychain.")),
    )
    fake_uvicorn = SimpleNamespace(run=Mock())
    monkeypatch.setitem(sys.modules, "uvicorn", fake_uvicorn)
    assert mac_service.main([]) == 1
    fake_uvicorn.run.assert_not_called()
    assert "Unlock Keychain" in capsys.readouterr().err


def test_other_platform_fails_without_keychain_access(monkeypatch):
    monkeypatch.setattr(mac_service.sys, "platform", "linux")
    configure = Mock()
    monkeypatch.setattr(mac_service, "configure_runtime", configure)
    assert mac_service.main([]) == 1
    configure.assert_not_called()


def test_plist_template_is_login_scoped_persistent_and_contains_no_secrets():
    root = Path(__file__).resolve().parent.parent
    with (root / "scripts" / "com.stemstudio.processor.plist").open("rb") as handle:
        config = plistlib.load(handle)
    assert config["Label"] == "com.stemstudio.processor"
    assert config["ProgramArguments"][1:] == ["-m", "stem_studio.mac_service"]
    assert config["RunAtLoad"] is True
    assert config["KeepAlive"] is True
    assert config["ThrottleInterval"] == 30
    assert config["ExitTimeOut"] == 30
    assert config["AbandonProcessGroup"] is False
    assert config["Umask"] == 0o077
    assert "SECRET" not in str(config)
    assert "UserName" not in config  # A user LaunchAgent, never a privileged daemon.


@pytest.mark.skipif(sys.platform != "darwin", reason="Regression covers native macOS plutil array behavior.")
@pytest.mark.parametrize("service,module", [("processor", "stem_studio.mac_service"), ("web", "stem_studio.mac_web")])
def test_native_plutil_render_replaces_python_argument_without_leaving_placeholder(tmp_path, service, module):
    root = Path(__file__).resolve().parent.parent
    rendered = tmp_path / f"com.stemstudio.{service}.plist"
    shutil.copyfile(root / "scripts" / f"com.stemstudio.{service}.plist", rendered)
    python_path = "/Users/example/Project With Spaces/.venv/bin/python"
    subprocess.run(["/usr/bin/plutil", "-remove", "ProgramArguments.0", str(rendered)],
                   capture_output=True, check=True, timeout=10)
    subprocess.run(["/usr/bin/plutil", "-insert", "ProgramArguments.0", "-string", python_path, str(rendered)],
                   capture_output=True, check=True, timeout=10)
    with rendered.open("rb") as handle:
        config = plistlib.load(handle)
    assert config["ProgramArguments"] == [python_path, "-m", module]
    assert "PYTHON_PLACEHOLDER" not in config["ProgramArguments"]
    installer = (root / "scripts" / f"install_macos_{service}.sh").read_text()
    remove = 'plutil -remove ProgramArguments.0 "${temporary_plist}"'
    insert = 'plutil -insert ProgramArguments.0 -string "${python_path}" "${temporary_plist}"'
    assert remove in installer and insert in installer
    assert installer.index(remove) < installer.index(insert)
    assert "plutil -replace ProgramArguments.0" not in installer
