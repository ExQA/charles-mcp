"""charles_config_summary tells an agent what Charles does without its secrets.

The Charles config file holds the license and the Web Interface passwords in
plain text, and Rewrite rules or an upstream proxy can hold tokens. An agent
that wanted to know why a mapping did not fire used to open the file and send
all of it to its model; this tool answers the same question from an allow list.
"""

from __future__ import annotations

import json
import socket
from pathlib import Path

import pytest

import charles_mcp.server as server_module
from charles_mcp.config import Config
from charles_mcp.mocks.config_summary import summarize_config
from charles_mcp.server import create_server

LICENSE_NAME = "Jane Licensee"
LICENSE_KEY = "LICENSE-KEY-1234567890abcdef"
WEB_PASSWORD = "web-pass-s3cret"
REWRITE_TOKEN = "Bearer rewrite-token-abc"
UPSTREAM_PASSWORD = "upstream-pass-xyz"
SECRETS = (LICENSE_NAME, LICENSE_KEY, WEB_PASSWORD, REWRITE_TOKEN, UPSTREAM_PASSWORD, "qa-user")


def _config_xml(dispatcher_port: int) -> str:
    return f"""<?xml version='1.0' encoding='UTF-8' ?>
<?charles serialisation-version='2.0' ?>
<configuration>
  <proxyConfiguration>
    <port>8888</port>
    <externalProxy><host>corp-proxy</host><username>u</username><password>{UPSTREAM_PASSWORD}</password></externalProxy>
    <sslLocations><locationPatterns>
      <locationMatch><location><host>api.example.com</host><port>443</port></location></locationMatch>
      <locationMatch><location><host>*.shop.example</host></location></locationMatch>
    </locationPatterns></sslLocations>
  </proxyConfiguration>
  <recordingConfiguration><ignoreHosts><locationPatterns>
    <locationMatch><location><host>tracker.example</host></location></locationMatch>
  </locationPatterns></ignoreHosts></recordingConfiguration>
  <registrationConfiguration><name>{LICENSE_NAME}</name><key>{LICENSE_KEY}</key></registrationConfiguration>
  <toolConfiguration><configs>
    <entry><string>Map Remote</string><map>
      <toolEnabled>true</toolEnabled>
      <mappings>
        <mapMapping>
          <sourceLocation><protocol>https</protocol><host>api.example.com</host><port>443</port><path>/v1/*</path></sourceLocation>
          <destLocation><host>http://127.0.0.1:18080</host></destLocation>
          <preserveHostHeader>true</preserveHostHeader>
        </mapMapping>
        <mapMapping>
          <sourceLocation><protocol>https</protocol><host>*.bank.example</host><port>443</port><path>/api/*</path></sourceLocation>
          <destLocation><protocol>http</protocol><host>127.0.0.1</host><port>{dispatcher_port}</port></destLocation>
          <preserveHostHeader>true</preserveHostHeader>
        </mapMapping>
        <mapMapping>
          <sourceLocation><protocol>https</protocol><host>pay.shop.example</host><port>443</port><path>/*</path></sourceLocation>
          <destLocation><protocol>https</protocol><host>staging.shop.example</host></destLocation>
          <enabled>false</enabled>
        </mapMapping>
      </mappings>
    </map></entry>
    <entry><string>Map Local</string><mapLocal>
      <toolEnabled>true</toolEnabled>
      <mappings><mapLocalMapping>
        <sourceLocation><protocol>https</protocol><host>api.example.com</host><port>443</port><path>/*</path></sourceLocation>
        <dest>/Users/qa/charles-mocks/api.example.com</dest>
      </mapLocalMapping></mappings>
    </mapLocal></entry>
    <entry><string>Rewrite</string><rewrite>
      <toolEnabled>true</toolEnabled>
      <sets><rewriteSet>
        <active>true</active><name>auth override</name>
        <hosts><locationPatterns><locationMatch><location><host>api.example.com</host></location></locationMatch></locationPatterns></hosts>
        <rules><rewriteRule><matchHeader>Authorization</matchHeader><newValue>{REWRITE_TOKEN}</newValue></rewriteRule></rules>
      </rewriteSet></sets>
    </rewrite></entry>
    <entry><string>Breakpoints</string><breakpoints><toolEnabled>true</toolEnabled><breakpoints><breakpoint><location><host>api.example.com</host></location></breakpoint></breakpoints></breakpoints></entry>
    <entry><string>No Caching</string><selectedHostsTool /></entry>
  </configs></toolConfiguration>
  <remoteControlConfiguration>
    <enabled>true</enabled>
    <users><remoteControlUser><username>qa-user</username><password>{WEB_PASSWORD}</password></remoteControlUser></users>
  </remoteControlConfiguration>
</configuration>
"""


@pytest.fixture
def listening_port():
    """A local port with something listening on it, standing in for the dispatcher."""
    server = socket.socket()
    server.bind(("127.0.0.1", 0))
    server.listen()
    yield server.getsockname()[1]
    server.close()


@pytest.fixture
def config_file(tmp_path: Path, listening_port: int) -> Path:
    path = tmp_path / "charles.config"
    path.write_text(_config_xml(listening_port), encoding="utf-8")
    return path


def test_no_secret_reaches_the_summary(config_file: Path) -> None:
    dumped = json.dumps(summarize_config(config_file, charles_user="qa-user", proxy_open=False))

    for secret in SECRETS:
        assert secret not in dumped, secret
    assert "corp-proxy" not in dumped  # upstream proxy settings are left out whole


def test_mappings_and_host_lists_are_reported(config_file: Path) -> None:
    summary = summarize_config(config_file, proxy_open=False)

    assert summary["ssl_proxying"]["hosts"] == ["api.example.com:443", "*.shop.example"]
    assert summary["recording"]["excluded_hosts"] == ["tracker.example"]
    assert summary["map_remote"]["enabled"] is True
    assert [item["from"] for item in summary["map_remote"]["mappings"]] == [
        "https://api.example.com:443/v1/*",
        "https://*.bank.example:443/api/*",
        "https://pay.shop.example:443/*",
    ]
    assert summary["map_remote"]["mappings"][2]["enabled"] is False
    assert summary["map_local"]["mappings"] == [
        {
            "from": "https://api.example.com:443/*",
            "to": "/Users/qa/charles-mocks/api.example.com",
            "enabled": True,
        }
    ]
    assert summary["rewrite"]["sets"] == [
        {"name": "auth override", "active": True, "hosts": ["api.example.com"], "rule_count": 1}
    ]
    # Tools at their defaults are left out; the ones in use are not.
    assert summary["other_tools"] == {"breakpoints": {"enabled": True, "configured": True}}


def test_each_mapping_names_what_breaks_it(config_file: Path) -> None:
    mappings = summarize_config(config_file, proxy_open=False)["map_remote"]["mappings"]

    url_as_host, uncovered, disabled = mappings
    assert "is not a host name" in url_as_host["problems"][0]
    assert ["SSL Proxying does not cover *.bank.example" in p for p in uncovered["problems"]] == [
        True
    ]
    # A wildcard SSL entry covers the subdomain; nothing is wrong with this one.
    assert "problems" not in disabled


def test_a_local_destination_nobody_listens_on_is_flagged(tmp_path: Path) -> None:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        free_port = probe.getsockname()[1]
    path = tmp_path / "charles.config"
    path.write_text(_config_xml(free_port), encoding="utf-8")

    problems = summarize_config(path, proxy_open=False)["map_remote"]["mappings"][1]["problems"]

    assert any(f"nothing listens on 127.0.0.1:{free_port}" in problem for problem in problems)


def test_web_interface_user_is_checked_without_naming_users(config_file: Path) -> None:
    present = summarize_config(config_file, charles_user="qa-user", proxy_open=False)
    absent = summarize_config(config_file, charles_user="admin", proxy_open=False)

    assert present["web_interface"] == {
        "enabled": True,
        "user_count": 1,
        "configured_user_present": True,
    }
    assert absent["web_interface"]["configured_user_present"] is False
    assert any(w.startswith("web_interface_user_missing") for w in absent["warnings"])
    assert any(w.startswith("recording_not_excluded") for w in absent["warnings"])


def test_a_running_charles_is_flagged_as_possibly_newer_than_the_file(config_file: Path) -> None:
    assert "note" not in summarize_config(config_file, proxy_open=False)
    assert "quits" in summarize_config(config_file, proxy_open=True)["note"]


@pytest.mark.asyncio
async def test_the_tool_reads_the_configured_file(
    config_file: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(server_module, "CharlesClient", object)
    config = Config(mock_dir=str(tmp_path / "mocks"), state_dir=str(tmp_path / "state"))
    config.config_path = str(config_file)
    config.charles_user = "qa-user"
    server = create_server(config=config)

    payload = (await server.call_tool("charles_config_summary", {}))[1]
    result = payload.get("result", payload)

    assert result["config_path"] == str(config_file)
    assert result["web_interface"]["configured_user_present"] is True
    for secret in SECRETS:
        assert secret not in json.dumps(result)
