locals {
  cloud_init = <<-EOT
#cloud-config
hostname: ${var.hostname}
manage_etc_hosts: true

users:
  - default
  - name: deploy
    gecos: ${var.hostname} deploy
    groups: [sudo]
    sudo: ALL=(ALL) NOPASSWD:ALL
    shell: /bin/bash
    ssh_authorized_keys:
      - ${local.deploy_ssh_public_key}

write_files:
  - path: /etc/hosts
    append: true
    content: |
      127.0.1.1 ${var.hostname}

  - path: /etc/ssh/sshd_config.d/99-hardening.conf
    owner: root:root
    permissions: "0644"
    content: |
      Port ${var.ssh_port}
      PermitRootLogin no
      PasswordAuthentication no
      PubkeyAuthentication yes

runcmd:
  - [systemctl, disable, --now, ssh.socket]
  - [systemctl, mask, ssh.socket]
  - [systemctl, enable, ssh.service]
  - [systemctl, restart, ssh.service]
EOT
}
