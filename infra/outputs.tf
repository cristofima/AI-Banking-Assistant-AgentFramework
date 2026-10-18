output "AZURE_LOCATION" { value = var.location }
output "AZURE_TENANT_ID" { value = data.azurerm_client_config.current.tenant_id }
output "AZURE_RESOURCE_GROUP" { value = data.azurerm_resource_group.main.name }

output "AZURE_ACCOUNT_APP_NAME" { value = azapi_resource.app["account"].name }
output "AZURE_TRANSACTION_APP_NAME" { value = azapi_resource.app["transaction"].name }
output "AZURE_WEB_APP_NAME" { value = azapi_resource.web.name }
output "AZURE_RESPONSES_BFF_APP_NAME" { value = azapi_resource.responses_bff.name }
output "AZURE_IDENTITY_APP_NAME" { value = azapi_resource.identity.name }
output "IDENTITY_URI" { value = "https://${azapi_resource.identity.output.properties.defaultHostName}" }

output "OAUTH_ISSUER" { value = local.oauth_issuer }
output "OAUTH_AUDIENCE" { value = var.oauth_audience }
output "OAUTH_CLIENT_ID" { value = var.oauth_client_id }
output "OAUTH_SIGNING_KEY_ID" { value = var.oauth_signing_key_id }
output "OAUTH_SIGNING_KEY_URI" { value = azurerm_key_vault_key.oauth.id }
output "OAUTH_FRONTEND_AUTHORIZE_URL" { value = local.oauth_frontend_authorize_url }
output "OAUTH_REDIRECT_URI" { value = local.oauth_redirect_uri }

output "ACCOUNT_URI" { value = "https://${azapi_resource.app["account"].output.properties.defaultHostName}" }
output "TRANSACTION_URI" { value = "https://${azapi_resource.app["transaction"].output.properties.defaultHostName}" }
output "WEB_URI" { value = "https://${azapi_resource.web.output.properties.defaultHostName}" }
output "RESPONSES_BFF_URI" { value = "https://${azapi_resource.responses_bff.output.properties.defaultHostName}" }

output "AZURE_STORAGE_ACCOUNT" { value = azurerm_storage_account.content.name }
output "AZURE_STORAGE_CONTAINER" { value = azurerm_storage_container.content.name }
output "AZURE_STORAGE_RESOURCE_GROUP" { value = data.azurerm_resource_group.main.name }

output "AZURE_AI_FOUNDRY_ACCOUNT_NAME" { value = azapi_resource.foundry_account_dedicated.name }
output "AZURE_AI_FOUNDRY_PROJECT_NAME" { value = azapi_resource.foundry_project.name }
output "AZURE_OPENAI_ENDPOINT" { value = azapi_resource.foundry_account_dedicated.output.properties.endpoint }
output "FOUNDRY_PROJECT_ENDPOINT" { value = "https://${azapi_resource.foundry_account_dedicated.name}.services.ai.azure.com/api/projects/${azapi_resource.foundry_project.name}" }

output "AZURE_POSTGRES_SERVER_NAME" { value = azapi_resource.postgres_server.name }
output "AZURE_POSTGRES_DATABASE_NAME" { value = azapi_resource.postgres_database.name }
output "AZURE_POSTGRES_HOST" { value = azapi_resource.postgres_server.output.properties.fullyQualifiedDomainName }
output "AZURE_POSTGRES_PORT" { value = 5432 }
output "AZURE_POSTGRES_ADMIN_USERNAME" { value = azapi_resource.postgres_server.output.properties.administratorLogin }

output "AZURE_KEY_VAULT_NAME" { value = azurerm_key_vault.secrets.name }
output "AZURE_KEY_VAULT_URI" { value = azurerm_key_vault.secrets.vault_uri }
