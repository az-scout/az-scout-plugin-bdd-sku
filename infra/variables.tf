variable "subscription_id" {
  description = "Azure subscription ID"
  type        = string
}

variable "resource_group_name" {
  description = "Name of the Azure resource group"
  type        = string
  default     = "rg-azure-scout-bdd"
}

variable "location" {
  description = "Azure region for resources"
  type        = string
  default     = "swedencentral"
}

variable "prefix" {
  description = "Prefix for resource names"
  type        = string
  default     = "az-scout"
}

variable "environment" {
  description = "Environment tag"
  type        = string
  default     = "production"
}

# ---------------------------------------------------------------------
# PostgreSQL
# ---------------------------------------------------------------------

variable "postgres_admin_user" {
  description = "PostgreSQL administrator login"
  type        = string
  default     = "azscout"
}

variable "postgres_admin_password" {
  description = "PostgreSQL administrator password"
  type        = string
  sensitive   = true
}

variable "postgres_db_name" {
  description = "PostgreSQL database name"
  type        = string
  default     = "azscout"
}

variable "postgres_sku" {
  description = "PostgreSQL Flexible Server SKU (e.g. B_Standard_B1ms, GP_Standard_D2s_v3)"
  type        = string
  default     = "B_Standard_B1ms"
}

variable "postgres_storage_mb" {
  description = "PostgreSQL storage in MB"
  type        = number
  default     = 32768 # 32 GB
}

# ---------------------------------------------------------------------
# Ingestion Jobs
# ---------------------------------------------------------------------

variable "max_pricing_items" {
  description = "Maximum pricing items to collect per job (-1 = unlimited)"
  type        = string
  default     = "-1"
}

variable "enable_spot_collector" {
  description = "Enable the Azure Spot pricing & eviction collector"
  type        = bool
  default     = true
}

variable "max_spot_items" {
  description = "Maximum spot items to collect per job (-1 = unlimited)"
  type        = string
  default     = "-1"
}

variable "cron_expression" {
  description = "Cron expression for the main scheduled ingestion job (UTC)"
  type        = string
  default     = "0 2 * * *" # Daily at 02:00 UTC
}

variable "spot_eviction_cron" {
  description = "Cron expression for the hourly spot eviction historization job (UTC)"
  type        = string
  default     = "0 * * * *" # Every hour
}

variable "log_level" {
  description = "Log level for ingestion jobs"
  type        = string
  default     = "INFO"
}
