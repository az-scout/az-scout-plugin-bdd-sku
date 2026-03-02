output "postgresql_fqdn" {
  description = "FQDN of the PostgreSQL Flexible Server"
  value       = azurerm_postgresql_flexible_server.main.fqdn
}

output "postgresql_database" {
  description = "PostgreSQL database name"
  value       = azurerm_postgresql_flexible_server_database.azscout.name
}

output "container_apps_environment_name" {
  description = "Name of the Container Apps Environment"
  value       = azurerm_container_app_environment.main.name
}

output "container_registry_login_server" {
  description = "Login server for Azure Container Registry"
  value       = azurerm_container_registry.main.login_server
}

output "ingestion_scheduled_job_name" {
  description = "Name of the scheduled ingestion job"
  value       = azurerm_container_app_job.ingestion_scheduled.name
}

output "ingestion_manual_job_name" {
  description = "Name of the manual ingestion job"
  value       = azurerm_container_app_job.ingestion_manual.name
}

output "spot_eviction_hourly_job_name" {
  description = "Name of the hourly spot eviction historization job"
  value       = azurerm_container_app_job.spot_eviction_hourly.name
}

output "managed_identity_client_id" {
  description = "Client ID of the managed identity for jobs"
  value       = azurerm_user_assigned_identity.ingestion_jobs.client_id
}

output "log_analytics_workspace_id" {
  description = "ID of the Log Analytics workspace"
  value       = azurerm_log_analytics_workspace.main.id
}

output "resource_group_name" {
  description = "Resource group name"
  value       = azurerm_resource_group.main.name
}
