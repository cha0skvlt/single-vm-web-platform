locals {
  deploy_ssh_public_key = trimspace(file("${path.module}/../keys/deploy.pub"))
}
