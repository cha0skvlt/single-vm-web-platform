"""inventory.ini helpers."""

from __future__ import annotations

from pathlib import Path

import pytest

from scripts.inventory import (
    INVENTORY_HOST_ALIAS,
    ensure_inventory_hostname,
    filter_known_hosts_for_ssh,
    host_from_known_hosts_field,
    inventory_group_name,
    inventory_host_alias,
    is_ipv4_address,
    parse_deploy_host,
    parse_inventory_connection,
    prepare_ansible_inventory,
    render_inventory_ini,
    sync_inventory_group,
)


@pytest.mark.unit
def test_parse_inventory_connection():
    text = "[prod]\nvm ansible_host=203.0.113.1 ansible_user=deploy ansible_port=666\n"
    assert parse_inventory_connection(text) == ("203.0.113.1", 666, "deploy")


@pytest.mark.unit
def test_render_inventory_ini_roundtrip():
    text = render_inventory_ini(hostname="prod", ansible_host="203.0.113.1")
    assert parse_inventory_connection(text) == ("203.0.113.1", 22, "deploy")


@pytest.mark.unit
def test_parse_inventory_connection_missing_host():
    with pytest.raises(ValueError, match="ansible_host"):
        parse_inventory_connection("[x]\n")


@pytest.mark.unit
def test_render_inventory_ini_rejects_empty_hostname():
    with pytest.raises(ValueError, match="hostname"):
        render_inventory_ini(hostname="  ", ansible_host="203.0.113.1")


@pytest.mark.unit
def test_render_inventory_ini_rejects_empty_ansible_host():
    with pytest.raises(ValueError, match="ansible_host"):
        render_inventory_ini(hostname="prod", ansible_host="  ")


@pytest.mark.unit
def test_is_ipv4_address():
    assert is_ipv4_address("203.0.113.9")
    assert not is_ipv4_address("#")
    assert not is_ipv4_address("999.0.0.1")


@pytest.mark.unit
def test_host_from_known_hosts_field_bracketed():
    assert host_from_known_hosts_field("[203.0.113.9]:666") == "203.0.113.9"
    assert host_from_known_hosts_field("[203.0.113.9:666]") == "203.0.113.9"
    assert host_from_known_hosts_field("#") is None
    assert host_from_known_hosts_field("") is None


@pytest.mark.unit
def test_host_from_known_hosts_field_plain_host_port():
    assert host_from_known_hosts_field("203.0.113.9:666") == "203.0.113.9"
    assert host_from_known_hosts_field("not-an-ip:666") is None
    assert host_from_known_hosts_field("[bad-ip]") is None


@pytest.mark.unit
def test_parse_deploy_host_skips_comments_and_bad_candidate():
    known = "# prod\n[203.0.113.9]:666 ssh-ed25519 AAA\n"
    assert parse_deploy_host(candidate="#", known_hosts=known) == "203.0.113.9"
    assert parse_deploy_host(candidate="#178.0.0.1", known_hosts=known) == "203.0.113.9"
    assert parse_deploy_host(candidate=None, known_hosts=known) == "203.0.113.9"
    assert parse_deploy_host(candidate="203.0.113.9", known_hosts=known) == "203.0.113.9"


@pytest.mark.unit
def test_parse_deploy_host_raises_when_only_comments():
    with pytest.raises(ValueError, match="could not determine"):
        parse_deploy_host(candidate="#", known_hosts="# no keys\n")


@pytest.mark.unit
def test_filter_known_hosts_for_ssh():
    text = "# header\n[203.0.113.9]:666 ssh-ed25519 AAA\n\n"
    assert filter_known_hosts_for_ssh(text) == "[203.0.113.9]:666 ssh-ed25519 AAA\n"


@pytest.mark.unit
def test_parse_deploy_host_rejects_multiple_ips():
    known = (
        "[203.0.113.1]:666 ssh-ed25519 A\n"
        "[203.0.113.2]:666 ssh-ed25519 B\n"
    )
    with pytest.raises(ValueError, match="multiple IPv4"):
        parse_deploy_host(candidate=None, known_hosts=known)


@pytest.mark.unit
def test_inventory_host_alias_errors():
    with pytest.raises(ValueError, match="not found"):
        inventory_host_alias("[prod]\nvm\n")


@pytest.mark.unit
def test_inventory_hostname_consistency():
    inv = render_inventory_ini(hostname="prod", ansible_host="203.0.113.9")
    assert inventory_host_alias(inv) == INVENTORY_HOST_ALIAS
    assert inventory_group_name(inv) == "prod"
    ensure_inventory_hostname(inv, expected="prod")
    with pytest.raises(ValueError, match="does not match"):
        ensure_inventory_hostname(inv, expected="other")


@pytest.mark.unit
def test_sync_inventory_group_rewrites_mismatched_group():
    old = "[oldgroup]\nvm ansible_host=YOUR_PUBLIC_IP ansible_user=deploy ansible_port=22\n"
    new, changed = sync_inventory_group(old, expected="prod")
    assert changed is True
    assert inventory_group_name(new) == "prod"
    assert parse_inventory_connection(new) == ("YOUR_PUBLIC_IP", 22, "deploy")


@pytest.mark.unit
def test_sync_inventory_group_noop_when_match():
    inv = render_inventory_ini(hostname="prod", ansible_host="203.0.113.1")
    text, changed = sync_inventory_group(inv, expected="prod")
    assert changed is False
    assert text == inv


@pytest.mark.unit
def test_sync_inventory_group_rejects_empty_expected():
    inv = render_inventory_ini(hostname="prod", ansible_host="203.0.113.1")
    with pytest.raises(ValueError, match="expected hostname"):
        sync_inventory_group(inv, expected="  ")


@pytest.mark.unit
def test_inventory_group_name_not_found():
    with pytest.raises(ValueError, match="not found"):
        inventory_group_name("no group here")


@pytest.mark.unit
def test_prepare_ansible_inventory_syncs_group(tmp_path: Path):
    inv_path = tmp_path / "inventory.ini"
    inv_path.write_text(
        "[oldgroup]\nvm ansible_host=203.0.113.1 ansible_user=deploy ansible_port=22\n",
        encoding="utf-8",
    )
    host, port, user, message = prepare_ansible_inventory(inv_path, expected="prod")
    assert host == "203.0.113.1"
    assert port == 22
    assert user == "deploy"
    assert message == "inventory: fixed group [oldgroup] -> [prod]"
    assert inventory_group_name(inv_path.read_text(encoding="utf-8")) == "prod"
