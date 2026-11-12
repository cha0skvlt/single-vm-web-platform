variable "hostname" {
  type        = string
  description = "OpenStack resource name prefix. Install paths use PROJECT_HOSTNAME in app/.env."
  default     = "web"
}

variable "auth_url" {
  type        = string
  description = "OpenStack Identity (Keystone) URL"
}

variable "username" {
  type        = string
  description = "OpenStack username"
}

variable "password" {
  type        = string
  sensitive   = true
  description = "OpenStack password"
}

variable "user_domain_name" {
  type        = string
  description = "User domain name (Selectel: account ID)"
  default     = "default"
}

variable "project_id" {
  type        = string
  description = "OpenStack project / tenant ID (preferred when set)"
  default     = ""
}

variable "tenant_name" {
  type        = string
  description = "OpenStack project name (used when project_id is empty)"
  default     = ""
}

variable "region" {
  type        = string
  description = "OpenStack region"
}

variable "az" {
  type        = string
  description = "Availability zone for volume and instance"
}

variable "insecure" {
  type        = bool
  default     = false
  description = "Skip TLS verify on the OpenStack API (lab only)"
}

variable "image_name" {
  type        = string
  description = "Public image name (exact match)"
}

variable "flavor_id" {
  type        = string
  description = "Compute flavor ID"
}

variable "volume_type" {
  type        = string
  description = "Boot volume type"
}

variable "disk_size_gb" {
  type        = number
  default     = 100
  description = "Boot volume size in GB"

  validation {
    condition     = var.disk_size_gb >= 20
    error_message = "Boot volume must be at least 20 GB."
  }
}

variable "volume_metadata" {
  type        = map(string)
  default     = {}
  description = "Optional volume metadata (cloud-specific IOPS caps, etc.)"
}

variable "external_network_name" {
  type        = string
  default     = ""
  description = "External / public network name. Empty = first network with external=true."
}

variable "dns_nameservers" {
  type        = list(string)
  default     = ["1.1.1.1", "8.8.8.8"]
  description = "DNS resolvers for the private subnet"
}

variable "subnet_cidr" {
  type        = string
  default     = "192.168.0.0/24"
  description = "Private subnet CIDR"
}

variable "ssh_port" {
  type        = number
  default     = 22
  description = "SSH port written by cloud-init and opened in the security group"
}

variable "peer_host_cidr" {
  type        = string
  default     = ""
  description = "Optional CIDR allowed to push Loki ingest (:3100). Empty = rule omitted."
}

variable "enable_online_resize" {
  type        = bool
  default     = true
  description = "Allow online boot-volume resize when the cloud supports it"
}
