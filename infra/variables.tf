variable "subscription_id" {
  description = "Subscription containing the single resource group."
  type        = string
}

variable "environment_name" {
  description = "Single azd environment name used to derive resource names."
  type        = string
  validation {
    condition     = can(regex("^[a-z0-9-]{1,18}$", var.environment_name))
    error_message = "Use 1-18 lowercase letters, digits or hyphens."
  }
}

variable "location" {
  type = string
}

variable "app_service_location" {
  description = "Region for the shared Linux plan and its five sites only. Null retains the resource group region. Existing deployments require a reviewed replacement or parallel migration."
  type        = string
  default     = null
  nullable    = true
  validation {
    condition     = var.app_service_location == null ? true : length(trimspace(var.app_service_location)) > 0
    error_message = "App Service location must be null or a nonempty Azure region name."
  }
}

variable "resource_group_name" {
  description = "Existing single resource group name selected for this stack."
  type        = string
}

variable "plan_sku" {
  description = "Shared Linux App Service Plan SKU for five sites (four Python services and one frontend). B2 is the retained default, not verified capacity evidence; validate workload capacity before rollout."
  type        = string
  default     = "B2"
}

variable "jwt_issuer" {
  description = "Coordinated application JWT issuer for Identity and all three consumers."
  type        = string
  default     = "home-banking-api"
  validation {
    condition     = length(trimspace(var.jwt_issuer)) > 0
    error_message = "JWT issuer must be nonempty."
  }
}

variable "jwt_audience" {
  description = "Coordinated application JWT audience for Identity and all three consumers."
  type        = string
  default     = "home-banking-web"
  validation {
    condition     = length(trimspace(var.jwt_audience)) > 0
    error_message = "JWT audience must be nonempty."
  }
}

variable "access_token_minutes" {
  description = "Identity-issued application JWT lifetime in minutes."
  type        = number
  default     = 60
  validation {
    condition     = var.access_token_minutes >= 1 && var.access_token_minutes <= 60 && floor(var.access_token_minutes) == var.access_token_minutes
    error_message = "Token lifetime must be an integer between 1 and 60 minutes."
  }
}

variable "oauth_audience" {
  description = "MCP OAuth audience shared by Identity, BFF and both MCP services, independent from application JWTs."
  type        = string
  default     = "banking-mcp"
  validation {
    condition     = length(trimspace(var.oauth_audience)) > 0
    error_message = "OAuth audience must be nonempty."
  }
}

variable "oauth_client_id" {
  description = "Owner-registered confidential BFF OAuth client ID; its secret is uploaded separately to Key Vault."
  type        = string
  validation {
    condition     = length(trimspace(var.oauth_client_id)) > 0
    error_message = "OAuth client ID must be nonempty."
  }
}

variable "oauth_signing_key_id" {
  description = "Public Identity OAuth signing key ID matching the owner-supplied private key and public JWKS."
  type        = string
  validation {
    condition     = length(trimspace(var.oauth_signing_key_id)) > 0 && length(var.oauth_signing_key_id) <= 128
    error_message = "OAuth signing key ID must be nonblank and at most 128 characters."
  }
}

variable "app_names" {
  description = "Override app names to import already deployed apps without replacement."
  type        = map(string)
  default     = {}
}

variable "hostname_scope" {
  description = "Set only at creation; null keeps existing legacy hostnames when importing sites."
  type        = string
  default     = "TenantReuse"
  validation {
    condition     = var.hostname_scope == null || contains(["TenantReuse", "SubscriptionReuse", "ResourceGroupReuse", "NoReuse"], var.hostname_scope)
    error_message = "Use a supported Azure App Service unique hostname scope or null."
  }
}

variable "storage_account_name" {
  type    = string
  default = null
}

variable "storage_container_name" {
  type    = string
  default = "content"
}

variable "tags" {
  type    = map(string)
  default = {}
}

variable "foundry_account_name" {
  description = "Optional override for the dedicated AI Services (Foundry) account name."
  type        = string
  default     = null
}

variable "foundry_project_name" {
  description = "Optional override for the dedicated Foundry project name."
  type        = string
  default     = null
}

variable "postgres_location" {
  description = "Optional PostgreSQL region override; defaults to the existing resource group region."
  type        = string
  default     = null
}

variable "postgres_server_name" {
  description = "Optional override for Azure Database for PostgreSQL Flexible Server name."
  type        = string
  default     = null
}

variable "postgres_database_name" {
  description = "Primary PostgreSQL database name used by the application data pipeline."
  type        = string
  default     = "banking"
}

variable "postgres_admin_username" {
  description = "Administrator login name for PostgreSQL Flexible Server."
  type        = string
  default     = "pgadmin"
}

variable "postgres_admin_password" {
  description = "Administrator login password for PostgreSQL Flexible Server."
  type        = string
  sensitive   = true
}

variable "postgres_sku_name" {
  description = "PostgreSQL Flexible Server SKU for the single development environment."
  type        = string
  default     = "B_Standard_B1ms"
}

variable "postgres_storage_mb" {
  description = "Allocated storage in MB for PostgreSQL Flexible Server."
  type        = number
  default     = 32768
}

variable "postgres_version" {
  description = "PostgreSQL major version."
  type        = string
  default     = "15"
}

variable "postgres_backup_retention_days" {
  description = "Backup retention in days for PostgreSQL Flexible Server."
  type        = number
  default     = 7
}

variable "postgres_allowed_client_ip" {
  description = "Optional single public IP allowed to connect directly to PostgreSQL Flexible Server (for example, a developer's local IP for pgAdmin). Leave unset to keep the server reachable only from Azure-hosted resources."
  type        = string
  default     = null
}

variable "key_vault_name" {
  description = "Optional override for the shared Key Vault storing jwt-secret-key/internal-identity-secret."
  type        = string
  default     = null
}

variable "github_actions_principal_id" {
  description = "Optional object ID (not the client/app ID) of the GitHub Actions federated identity service principal, granted Key Vault Secrets User so cd-hosted-agent.yaml can resolve internal-identity-secret for the Foundry hosted agent, which has no native Key Vault reference support. Leave unset to skip this role assignment."
  type        = string
  default     = null
}

variable "azure_operations_principal_id" {
  description = "Optional object ID of the separate protected manual database-operations principal. Grants resource lookup, public OAuth key read, database-url read and scoped OAuth secret initialization; never grants key signing or ordinary CD write access."
  type        = string
  default     = null
}

variable "deployer_principal_id" {
  description = "Optional object ID (not the client/app ID) of whoever runs terraform apply/azd provision, granted Key Vault Secrets Officer and Crypto Officer so Terraform can write the database-url secret and generate the native OAuth signing key. Key Vault RBAC does not inherit from subscription Owner/Contributor. Leave unset to skip this role assignment."
  type        = string
  default     = null
}
