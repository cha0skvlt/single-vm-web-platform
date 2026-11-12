resource "openstack_networking_network_v2" "private" {
  name           = "${var.hostname}-net"
  admin_state_up = true
}

resource "openstack_networking_subnet_v2" "private" {
  name            = "${var.hostname}-subnet"
  network_id      = openstack_networking_network_v2.private.id
  cidr            = var.subnet_cidr
  ip_version      = 4
  dns_nameservers = var.dns_nameservers
}

data "openstack_networking_network_v2" "external" {
  name     = var.external_network_name != "" ? var.external_network_name : null
  external = true
}

resource "openstack_networking_router_v2" "router" {
  name                = "${var.hostname}-router"
  admin_state_up      = true
  external_network_id = data.openstack_networking_network_v2.external.id
}

resource "openstack_networking_router_interface_v2" "router_iface" {
  router_id = openstack_networking_router_v2.router.id
  subnet_id = openstack_networking_subnet_v2.private.id
}

resource "openstack_networking_secgroup_v2" "platform" {
  name        = "${var.hostname}-sg"
  description = "ingress: ssh, http, https"
}

resource "openstack_networking_secgroup_rule_v2" "ssh" {
  direction         = "ingress"
  ethertype         = "IPv4"
  protocol          = "tcp"
  port_range_min    = var.ssh_port
  port_range_max    = var.ssh_port
  remote_ip_prefix  = "0.0.0.0/0"
  security_group_id = openstack_networking_secgroup_v2.platform.id
}

resource "openstack_networking_secgroup_rule_v2" "http" {
  direction         = "ingress"
  ethertype         = "IPv4"
  protocol          = "tcp"
  port_range_min    = 80
  port_range_max    = 80
  remote_ip_prefix  = "0.0.0.0/0"
  security_group_id = openstack_networking_secgroup_v2.platform.id
}

resource "openstack_networking_secgroup_rule_v2" "https" {
  direction         = "ingress"
  ethertype         = "IPv4"
  protocol          = "tcp"
  port_range_min    = 443
  port_range_max    = 443
  remote_ip_prefix  = "0.0.0.0/0"
  security_group_id = openstack_networking_secgroup_v2.platform.id
}

resource "openstack_networking_secgroup_rule_v2" "loki_ingest_peer" {
  count             = var.peer_host_cidr != "" ? 1 : 0
  direction         = "ingress"
  ethertype         = "IPv4"
  protocol          = "tcp"
  port_range_min    = 3100
  port_range_max    = 3100
  remote_ip_prefix  = var.peer_host_cidr
  security_group_id = openstack_networking_secgroup_v2.platform.id
}

resource "openstack_networking_port_v2" "platform" {
  name               = "${var.hostname}-port"
  network_id         = openstack_networking_network_v2.private.id
  security_group_ids = [openstack_networking_secgroup_v2.platform.id]

  fixed_ip {
    subnet_id = openstack_networking_subnet_v2.private.id
  }
}

resource "openstack_networking_floatingip_v2" "platform" {
  pool = data.openstack_networking_network_v2.external.name
}

resource "openstack_networking_floatingip_associate_v2" "platform" {
  floating_ip = openstack_networking_floatingip_v2.platform.address
  port_id     = openstack_networking_port_v2.platform.id

  depends_on = [
    openstack_networking_floatingip_v2.platform,
    openstack_networking_port_v2.platform,
    openstack_networking_router_interface_v2.router_iface,
  ]
}
