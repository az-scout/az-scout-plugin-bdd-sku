# Container Apps Infrastructure for Ingestion Jobs
# ACR + Container Apps Environment + Scheduled & Manual Jobs

# ---------------------------------------------------------------------
# Managed Identity for Container Apps Jobs
# ---------------------------------------------------------------------
resource "azurerm_user_assigned_identity" "ingestion_jobs" {
  name                = "${var.prefix}-jobs-id"
  location            = azurerm_resource_group.main.location
  resource_group_name = azurerm_resource_group.main.name

  tags = {
    Environment = var.environment
    Project     = "bdd-sku"
  }
}

# ---------------------------------------------------------------------
# Log Analytics
# ---------------------------------------------------------------------
resource "azurerm_log_analytics_workspace" "main" {
  name                = "${var.prefix}-law"
  location            = azurerm_resource_group.main.location
  resource_group_name = azurerm_resource_group.main.name
  sku                 = "PerGB2018"
  retention_in_days   = 30

  tags = {
    Environment = var.environment
    Project     = "bdd-sku"
  }
}

# ---------------------------------------------------------------------
# Container Apps Environment
# ---------------------------------------------------------------------
resource "azurerm_container_app_environment" "main" {
  name                       = "${var.prefix}-cae"
  location                   = azurerm_resource_group.main.location
  resource_group_name        = azurerm_resource_group.main.name
  log_analytics_workspace_id = azurerm_log_analytics_workspace.main.id

  tags = {
    Environment = var.environment
    Project     = "bdd-sku"
  }
}

# Diagnostic settings
resource "azurerm_monitor_diagnostic_setting" "cae" {
  name                       = "${var.prefix}-cae-diag"
  target_resource_id         = azurerm_container_app_environment.main.id
  log_analytics_workspace_id = azurerm_log_analytics_workspace.main.id

  enabled_log {
    category = "ContainerAppConsoleLogs"
  }

  enabled_log {
    category = "ContainerAppSystemLogs"
  }

  enabled_metric {
    category = "AllMetrics"
  }
}

# ---------------------------------------------------------------------
# Azure Container Registry
# ---------------------------------------------------------------------
resource "azurerm_container_registry" "main" {
  name                = "${replace(var.prefix, "-", "")}acr"
  resource_group_name = azurerm_resource_group.main.name
  location            = azurerm_resource_group.main.location
  sku                 = "Basic"
  admin_enabled       = false

  identity {
    type = "SystemAssigned"
  }

  tags = {
    Environment = var.environment
    Project     = "bdd-sku"
  }
}

# ACR Pull for managed identity
resource "azurerm_role_assignment" "ingestion_acr_pull" {
  scope                = azurerm_container_registry.main.id
  role_definition_name = "AcrPull"
  principal_id         = azurerm_user_assigned_identity.ingestion_jobs.principal_id
}

# ---------------------------------------------------------------------
# Container Apps Job – Scheduled (daily at 02:00 UTC)
# ---------------------------------------------------------------------
resource "azurerm_container_app_job" "ingestion_scheduled" {
  name                         = "${var.prefix}-sched"
  location                     = azurerm_resource_group.main.location
  resource_group_name          = azurerm_resource_group.main.name
  container_app_environment_id = azurerm_container_app_environment.main.id

  identity {
    type         = "UserAssigned"
    identity_ids = [azurerm_user_assigned_identity.ingestion_jobs.id]
  }

  replica_timeout_in_seconds = 3600
  replica_retry_limit        = 3

  schedule_trigger_config {
    cron_expression          = var.cron_expression
    parallelism              = 1
    replica_completion_count = 1
  }

  template {
    container {
      name   = "bdd-sku-ingestion"
      image  = "${azurerm_container_registry.main.login_server}/bdd-sku-ingestion:latest"
      cpu    = 1.0
      memory = "2Gi"

      env {
        name  = "POSTGRES_HOST"
        value = azurerm_postgresql_flexible_server.main.fqdn
      }

      env {
        name  = "POSTGRES_PORT"
        value = "5432"
      }

      env {
        name  = "POSTGRES_DB"
        value = var.postgres_db_name
      }

      env {
        name  = "POSTGRES_USER"
        value = var.postgres_admin_user
      }

      env {
        name        = "POSTGRES_PASSWORD"
        secret_name = "pg-password"
      }

      env {
        name  = "POSTGRES_SSLMODE"
        value = "require"
      }

      env {
        name  = "ENABLE_AZURE_PRICING_COLLECTOR"
        value = "true"
      }

      env {
        name  = "AZURE_PRICING_MAX_ITEMS"
        value = var.max_pricing_items
      }

      env {
        name  = "JOB_TYPE"
        value = "scheduled"
      }

      env {
        name  = "PYTHONUNBUFFERED"
        value = "1"
      }

      env {
        name  = "LOG_LEVEL"
        value = var.log_level
      }
    }
  }

  secret {
    name  = "pg-password"
    value = var.postgres_admin_password
  }

  registry {
    server   = azurerm_container_registry.main.login_server
    identity = azurerm_user_assigned_identity.ingestion_jobs.id
  }

  tags = {
    Environment = var.environment
    Project     = "bdd-sku"
    JobType     = "scheduled"
  }
}

# ---------------------------------------------------------------------
# Container Apps Job – Manual (on-demand)
# ---------------------------------------------------------------------
resource "azurerm_container_app_job" "ingestion_manual" {
  name                         = "${var.prefix}-manual"
  location                     = azurerm_resource_group.main.location
  resource_group_name          = azurerm_resource_group.main.name
  container_app_environment_id = azurerm_container_app_environment.main.id

  identity {
    type         = "UserAssigned"
    identity_ids = [azurerm_user_assigned_identity.ingestion_jobs.id]
  }

  replica_timeout_in_seconds = 21600
  replica_retry_limit        = 0

  manual_trigger_config {
    parallelism              = 1
    replica_completion_count = 1
  }

  template {
    container {
      name   = "bdd-sku-ingestion"
      image  = "${azurerm_container_registry.main.login_server}/bdd-sku-ingestion:latest"
      cpu    = 1.0
      memory = "2Gi"

      env {
        name  = "POSTGRES_HOST"
        value = azurerm_postgresql_flexible_server.main.fqdn
      }

      env {
        name  = "POSTGRES_PORT"
        value = "5432"
      }

      env {
        name  = "POSTGRES_DB"
        value = var.postgres_db_name
      }

      env {
        name  = "POSTGRES_USER"
        value = var.postgres_admin_user
      }

      env {
        name        = "POSTGRES_PASSWORD"
        secret_name = "pg-password"
      }

      env {
        name  = "POSTGRES_SSLMODE"
        value = "require"
      }

      env {
        name  = "ENABLE_AZURE_PRICING_COLLECTOR"
        value = "true"
      }

      env {
        name  = "AZURE_PRICING_MAX_ITEMS"
        value = var.max_pricing_items
      }

      env {
        name  = "JOB_TYPE"
        value = "manual"
      }

      env {
        name  = "PYTHONUNBUFFERED"
        value = "1"
      }

      env {
        name  = "LOG_LEVEL"
        value = var.log_level
      }
    }
  }

  secret {
    name  = "pg-password"
    value = var.postgres_admin_password
  }

  registry {
    server   = azurerm_container_registry.main.login_server
    identity = azurerm_user_assigned_identity.ingestion_jobs.id
  }

  tags = {
    Environment = var.environment
    Project     = "bdd-sku"
    JobType     = "manual"
  }
}
