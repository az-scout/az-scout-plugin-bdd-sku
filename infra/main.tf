# az-scout-plugin-bdd-sku Infrastructure
# Stack: Azure Database for PostgreSQL Flexible Server + Container Apps Jobs + ACR

terraform {
  required_version = ">= 1.0"
  required_providers {
    azurerm = {
      source  = "hashicorp/azurerm"
      version = ">= 4.0"
    }
  }
}

provider "azurerm" {
  features {}
  subscription_id     = var.subscription_id
  storage_use_azuread = true
}

data "azurerm_client_config" "current" {}

# ---------------------------------------------------------------------
# Resource Group
# ---------------------------------------------------------------------
resource "azurerm_resource_group" "main" {
  name     = var.resource_group_name
  location = var.location

  tags = {
    Environment = var.environment
    Project     = "bdd-sku"
  }
}

# ---------------------------------------------------------------------
# Azure Database for PostgreSQL – Flexible Server
# ---------------------------------------------------------------------
resource "azurerm_postgresql_flexible_server" "main" {
  name                          = "${var.prefix}-pg"
  resource_group_name           = azurerm_resource_group.main.name
  location                      = azurerm_resource_group.main.location
  version                       = "17"
  administrator_login           = var.postgres_admin_user
  administrator_password        = var.postgres_admin_password
  sku_name                      = var.postgres_sku
  storage_mb                    = var.postgres_storage_mb
  backup_retention_days         = 7
  geo_redundant_backup_enabled  = false
  public_network_access_enabled = true
  zone                          = "1"

  authentication {
    active_directory_auth_enabled = true
    password_auth_enabled         = true
    tenant_id                     = data.azurerm_client_config.current.tenant_id
  }

  tags = {
    Environment = var.environment
    Project     = "bdd-sku"
  }
}

# Entra ID admin – the managed identity used by Container Apps
resource "azurerm_postgresql_flexible_server_active_directory_administrator" "msi" {
  server_name         = azurerm_postgresql_flexible_server.main.name
  resource_group_name = azurerm_resource_group.main.name
  tenant_id           = data.azurerm_client_config.current.tenant_id
  object_id           = azurerm_user_assigned_identity.ingestion_jobs.principal_id
  principal_name      = azurerm_user_assigned_identity.ingestion_jobs.name
  principal_type      = "ServicePrincipal"
}

# Firewall rule: allow Azure services (Container Apps Jobs)
resource "azurerm_postgresql_flexible_server_firewall_rule" "allow_azure" {
  name             = "AllowAzureServices"
  server_id        = azurerm_postgresql_flexible_server.main.id
  start_ip_address = "0.0.0.0"
  end_ip_address   = "0.0.0.0"
}

# Database
resource "azurerm_postgresql_flexible_server_database" "azscout" {
  name      = var.postgres_db_name
  server_id = azurerm_postgresql_flexible_server.main.id
  charset   = "UTF8"
  collation = "en_US.utf8"
}

# ---------------------------------------------------------------------
# Schema application
# ---------------------------------------------------------------------
# Apply schema manually after deployment:
#   PGPASSWORD="<password>" psql \
#     -h $(terraform output -raw postgresql_fqdn) \
#     -U azscout -d azscout \
#     -f ../sql/schema.sql
