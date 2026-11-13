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


# ------------------------------------------------- secrets inside locations


def _minimal(body: str, proxy: str = "") -> str:
    return f"""<?xml version='1.0' encoding='UTF-8' ?>
<configuration>
  <proxyConfiguration>{proxy}</proxyConfiguration>
  <toolConfiguration><configs>{body}</configs></toolConfiguration>
</configuration>
"""


def _write(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "charles.config"
    path.write_text(text, encoding="utf-8")
    return path


def test_secrets_typed_into_locations_never_reach_the_summary(tmp_path: Path) -> None:
    """Every place a location is returned or quoted, with a secret in each part of it."""
    loc = (
        "<protocol>https</protocol><host>{host}</host><port>443</port>"
        "<path>/v1/pay?sig=SENTINEL-PATH</path><query>access_token=SENTINEL-QUERY&amp;page=2</query>"
    )
    body = f"""
    <entry><string>Map Remote</string><map><toolEnabled>true</toolEnabled><mappings>
      <mapMapping>
        <sourceLocation>{loc.format(host="user:SENTINEL-SRC@api.example.com")}</sourceLocation>
        <destLocation><host>https://admin:SENTINEL-DEST@127.0.0.1:18080/x?key=SENTINEL-DESTQ#SENTINEL-FRAG</host>
          <query>SENTINEL-BARE</query></destLocation>
      </mapMapping>
    </mappings></map></entry>
    <entry><string>Map Local</string><mapLocal><mappings><mapLocalMapping>
      <sourceLocation>{loc.format(host="files.example.com")}</sourceLocation><dest>/tmp/mocks</dest>
    </mapLocalMapping></mappings></mapLocal></entry>
    <entry><string>Rewrite</string><rewrite><sets><rewriteSet><name>set</name>
      <hosts><locationPatterns><locationMatch><location>{loc.format(host="rw.example.com")}</location></locationMatch></locationPatterns></hosts>
    </rewriteSet></sets></rewrite></entry>
    """
    proxy = """<sslLocations><locationPatterns><locationMatch><location>
      <host>u:SENTINEL-SSL@ssl.example.com</host><port>SENTINEL-PORT</port><protocol>SENTINEL PROTOCOL</protocol>
    </location></locationMatch></locationPatterns></sslLocations>
    <port>SENTINEL-PROXYPORT</port>"""

    summary = summarize_config(_write(tmp_path, _minimal(body, proxy)), proxy_open=False)
    dumped = json.dumps(summary)

    assert "SENTINEL" not in dumped, dumped
    mapping = summary["map_remote"]["mappings"][0]
    # What is useful for diagnosis survives: hosts, paths and parameter names.
    assert mapping["from"] == "https://api.example.com:443/v1/pay?sig=…?access_token=…&page=…"
    assert mapping["to"] == "https://127.0.0.1:18080/x?key=…#…?…"
    assert "is not a host name" in mapping["problems"][0]
    assert summary["ssl_proxying"]["hosts"] == ["ssl.example.com"]
    assert summary["proxy"] == {"port": 8888}


def test_only_the_named_ssl_section_is_read(tmp_path: Path) -> None:
    """A section that merely looks like a host list is not copied."""
    proxy = """
    <sslPrivateSettings><locationPatterns><locationMatch><location>
      <host>SENTINEL-UNKNOWN.example.com</host></location></locationMatch></locationPatterns></sslPrivateSettings>
    <sslClientCertificates><locationPatterns><locationMatch><location><host>SENTINEL-CERT</host></location>
      <passphrase>SENTINEL-PASSPHRASE</passphrase></locationMatch></locationPatterns></sslClientCertificates>
    <sslLocations><locationPatterns><locationMatch><location><host>api.example.com</host></location>
      </locationMatch></locationPatterns></sslLocations>"""

    summary = summarize_config(_write(tmp_path, _minimal("", proxy)), proxy_open=False)

    assert "SENTINEL" not in json.dumps(summary)
    assert summary["ssl_proxying"] == {"hosts": ["api.example.com"]}


# ------------------------------------------------------------ SSL coverage


def _problems_for(tmp_path: Path, source_port: str, ssl_port: str, ssl_host: str = "api.example.com"):
    port_tag = f"<port>{source_port}</port>" if source_port else ""
    ssl_port_tag = f"<port>{ssl_port}</port>" if ssl_port else ""
    body = f"""<entry><string>Map Remote</string><map><mappings><mapMapping>
      <sourceLocation><protocol>https</protocol><host>API.example.com</host>{port_tag}</sourceLocation>
      <destLocation><protocol>https</protocol><host>staging.example.com</host></destLocation>
    </mapMapping></mappings></map></entry>"""
    proxy = f"""<sslLocations><locationPatterns><locationMatch><location>
      <host>{ssl_host}</host>{ssl_port_tag}</location></locationMatch></locationPatterns></sslLocations>"""
    summary = summarize_config(_write(tmp_path, _minimal(body, proxy)), proxy_open=False)
    return summary["map_remote"]["mappings"][0].get("problems", [])


@pytest.mark.parametrize(
    ("source_port", "ssl_port", "covered"),
    [
        ("443", "443", True),
        ("443", "", True),  # an SSL entry without a port covers every port
        ("443", "*", True),
        ("", "443", True),  # a mapping without a port is HTTPS on 443
        ("443", "8443", False),
        ("", "8443", False),
        ("8443", "443", False),
        ("8443", "8443", True),
    ],
)
def test_ssl_coverage_compares_ports_as_well_as_hosts(
    tmp_path: Path, source_port: str, ssl_port: str, covered: bool
) -> None:
    problems = _problems_for(tmp_path, source_port, ssl_port)

    assert (not problems) is covered, problems
    if not covered:
        assert f"api.example.com:{source_port or '443'}".lower() in problems[0].lower()


def test_ssl_coverage_follows_host_wildcards(tmp_path: Path) -> None:
    assert _problems_for(tmp_path, "443", "443", ssl_host="*.example.com") == []
    assert _problems_for(tmp_path, "443", "443", ssl_host="*.other.example") != []


# ------------------------------------------------------- hostile or broken files


def test_an_oversized_file_is_refused_before_it_is_parsed(tmp_path: Path) -> None:
    from charles_mcp.mocks.charles_config import MAX_CONFIG_BYTES, CharlesConfigError

    path = tmp_path / "charles.config"
    with path.open("wb") as handle:
        handle.write(b"<configuration>")
        handle.truncate(MAX_CONFIG_BYTES + 10)  # sparse: no real megabytes written

    with pytest.raises(CharlesConfigError, match="larger than"):
        summarize_config(path)


def test_long_lists_are_cut_and_counted(tmp_path: Path) -> None:
    from charles_mcp.mocks.config_summary import MAX_LIST_ITEMS

    hosts = "".join(
        f"<locationMatch><location><host>h{i}.example.com</host></location></locationMatch>"
        for i in range(MAX_LIST_ITEMS + 25)
    )
    proxy = f"<sslLocations><locationPatterns>{hosts}</locationPatterns></sslLocations>"

    listed = summarize_config(_write(tmp_path, _minimal("", proxy)))["ssl_proxying"]["hosts"]

    assert len(listed) == MAX_LIST_ITEMS + 1
    assert listed[-1] == "… and 25 more, not listed"


@pytest.mark.parametrize(
    "content",
    [
        b"<configuration><broken SENTINEL-CONTENT",
        b"\xff\xfe<configuration/> SENTINEL-CONTENT",
        b"<!DOCTYPE c [<!ENTITY a 'SENTINEL-CONTENT'>]><configuration>&a;</configuration>",
        b"<configuration><!DOCTYPE x [<!ENTITY a 'aaaa'><!ENTITY b '&a;&a;&a;'>]>&b;</configuration>",
    ],
    ids=["malformed", "not-utf8", "external-prolog-entity", "entity-expansion"],
)
def test_broken_and_hostile_files_fail_without_quoting_their_content(
    tmp_path: Path, content: bytes
) -> None:
    from charles_mcp.mocks.charles_config import CharlesConfigError

    path = tmp_path / "charles.config"
    path.write_bytes(content)

    with pytest.raises(CharlesConfigError) as error:
        summarize_config(path)
    assert "SENTINEL" not in str(error.value)


def test_an_unreadable_file_is_a_plain_error(tmp_path: Path) -> None:
    from charles_mcp.mocks.charles_config import CharlesConfigError

    path = _write(tmp_path, _minimal(""))
    path.chmod(0)
    try:
        if path.open("rb").readable():  # running as root: nothing to test
            pytest.skip("file permissions are not enforced for this user")
    except PermissionError:
        pass
    try:
        with pytest.raises(CharlesConfigError, match="cannot read"):
            summarize_config(path)
    finally:
        path.chmod(0o600)
