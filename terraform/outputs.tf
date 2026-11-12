output "public_ip_address" {
  description = "Public IPv4 (floating IP)"
  value       = openstack_networking_floatingip_v2.platform.address
}

output "ssh_example" {
  description = "SSH command after cloud-init finishes"
  value       = "ssh -p ${var.ssh_port} deploy@${openstack_networking_floatingip_v2.platform.address}"
}

output "ssh_port" {
  description = "SSH port configured by cloud-init"
  value       = var.ssh_port
}
