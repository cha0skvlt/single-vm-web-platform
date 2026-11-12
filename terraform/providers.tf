provider "openstack" {
  auth_url         = var.auth_url
  region           = var.region
  user_name        = var.username
  password         = var.password
  user_domain_name = var.user_domain_name
  tenant_id        = var.project_id != "" ? var.project_id : null
  tenant_name      = var.tenant_name != "" ? var.tenant_name : null
  insecure         = var.insecure
}
