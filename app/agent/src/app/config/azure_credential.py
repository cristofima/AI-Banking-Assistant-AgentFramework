from azure.identity import ManagedIdentityCredential, AzureCliCredential
from azure.identity.aio import ManagedIdentityCredential as AioManagedIdentityCredential, AzureCliCredential as AioCliCredential
from app.config.settings import settings

async def get_azure_credential_async() -> AioCliCredential | AioManagedIdentityCredential:
    """
    Returns an Azure credential asynchronously based on the application environment.

    If the environment is 'local', it uses AioCliCredential.
    Otherwise, it uses AioManagedIdentityCredential.

    Returns:
        Credential object: Either AioCliCredential or AioManagedIdentityCredential.
    """
    if settings.AGENT_RUNTIME == 'local':
        return AioCliCredential()
    else:
        return AioManagedIdentityCredential(client_id=settings.AZURE_CLIENT_ID)

def get_async_azure_credential() -> AioCliCredential | AioManagedIdentityCredential:
    """
    Returns an Azure credential asynchronously based on the application environment.

    If the environment is 'local', it uses AioCliCredential.
    Otherwise, it uses AioManagedIdentityCredential.

    Returns:
        Credential object: Either AioCliCredential or AioManagedIdentityCredential.
    """
    if settings.AGENT_RUNTIME == 'local':
        return AioCliCredential()
    else:
        return AioManagedIdentityCredential(client_id=settings.AZURE_CLIENT_ID)

def get_azure_credential() -> AzureCliCredential | ManagedIdentityCredential:
    """
    Returns an Azure credential based on the application environment.

    If the environment is 'local', it uses AzureCliCredential.
    Otherwise, it uses ManagedIdentityCredential.

    Returns:
        Credential object: Either AzureCliCredential or ManagedIdentityCredential.
    """
    if settings.AGENT_RUNTIME == 'local':
        return AzureCliCredential()
    else:
        return ManagedIdentityCredential(client_id=settings.AZURE_CLIENT_ID)
