const { execFileSync } = require('node:child_process');
const { appendFileSync } = require('node:fs');
const { isIP } = require('node:net');

const services = new Set(['identity', 'account', 'transaction', 'responses-bff']);
class PreflightError extends Error {}
function requireCheck(condition, message) {
  if (!condition) throw new PreflightError(message);
}
function selectApp(service, group, name, readAzure = azureJson) {
  requireCheck(services.has(service), 'Unsupported preflight service.');
  requireCheck(Boolean(group?.trim()), 'Resource group is required.');
  requireCheck(typeof name === 'string' && /^[a-zA-Z0-9][a-zA-Z0-9-]{0,58}[a-zA-Z0-9]$/.test(name), 'A valid explicit App Service name is required.');
  const app = readAzure(['webapp', 'show', '--resource-group', group, '--name', name]);
  requireCheck(app?.name === name && typeof app.id === 'string' && app.id.toLowerCase().endsWith(`/providers/microsoft.web/sites/${name.toLowerCase()}`), 'Named App Service metadata must match the explicit deployment target.');
  return app;
}
function remoteHttps(value) {
  try {
    const url = new URL(value);
    const host = url.hostname.toLowerCase();
    return url.protocol === 'https:' && !url.username && !url.password && !url.search && !url.hash &&
      host.includes('.') && !isIP(host) && !host.startsWith('[') &&
      !host.endsWith('.') && !host.endsWith('.localhost') && !host.endsWith('.local') &&
      !host.endsWith('.internal') && !host.endsWith('.test') &&
      !host.endsWith('.invalid') && !host.endsWith('.example');
  } catch { return false; }
}
function validateOAuthConfiguration(service, values, required, secrets) {
  required.push('OAUTH_ISSUER', 'OAUTH_AUDIENCE');
  requireCheck(remoteHttps(values.OAUTH_ISSUER) && new URL(values.OAUTH_ISSUER).pathname === '/' && !values.OAUTH_ISSUER.endsWith('/'), 'OAUTH_ISSUER must be a public HTTPS Identity origin.');
  requireCheck(typeof values.OAUTH_AUDIENCE === 'string' && values.OAUTH_AUDIENCE.trim() &&
    !values.OAUTH_AUDIENCE.startsWith('@Microsoft.KeyVault('), 'OAUTH_AUDIENCE must be an explicit coordinated value.');
  if (service === 'identity') {
    required.push('OAUTH_FRONTEND_AUTHORIZE_URL', 'OAUTH_SIGNING_KEY_ID', 'OAUTH_SIGNING_BACKEND', 'OAUTH_SIGNING_KEY_URI');
    requireCheck(values.OAUTH_SIGNING_BACKEND === 'key_vault', 'Remote Identity requires explicit Key Vault signing.');
    requireCheck(!Object.hasOwn(values, 'OAUTH_SIGNING_PRIVATE_KEY'), 'Native signing must not receive a private PEM.');
    requireCheck(/^https:\/\/[a-zA-Z0-9-]+\.vault\.azure\.net\/keys\/[a-zA-Z0-9-]+\/[a-zA-Z0-9]+$/.test(values.OAUTH_SIGNING_KEY_URI), 'Native signing requires a version-pinned Key Vault key URI.');
    requireCheck(remoteHttps(values.OAUTH_FRONTEND_AUTHORIZE_URL) &&
      new URL(values.OAUTH_FRONTEND_AUTHORIZE_URL).pathname === '/oauth/consent', 'Identity OAuth consent must use the public frontend /oauth/consent URL.');
    requireCheck(typeof values.OAUTH_SIGNING_KEY_ID === 'string' && values.OAUTH_SIGNING_KEY_ID.trim() &&
      !values.OAUTH_SIGNING_KEY_ID.startsWith('@Microsoft.KeyVault('), 'OAUTH_SIGNING_KEY_ID must be explicit.');
  } else {
    requireCheck(values.MCP_IDENTITY_MODE === 'oauth', 'This deployment requires explicit MCP_IDENTITY_MODE=oauth; no HMAC fallback.');
    requireCheck(!Object.hasOwn(values, 'OAUTH_SIGNING_PRIVATE_KEY'), 'Only Identity may receive the OAuth private signing key.');
    required.push('OAUTH_JWKS');
    secrets.push('OAUTH_JWKS');
    requireCheck(values.OAUTH_ISSUER.replace(/\/$/, '') === values.AUTH_USERS_ENDPOINT.replace(/\/$/, ''), 'OAuth issuer and AUTH_USERS_ENDPOINT must identify the same Identity origin.');
    if (service === 'responses-bff') {
      required.push('OAUTH_CLIENT_ID', 'OAUTH_CLIENT_SECRET', 'OAUTH_REDIRECT_URI');
      secrets.push('OAUTH_CLIENT_SECRET');
      requireCheck(remoteHttps(values.OAUTH_REDIRECT_URI) &&
        new URL(values.OAUTH_REDIRECT_URI).pathname === '/oauth/callback', 'BFF OAuth redirect must use the public frontend /oauth/callback URL.');
      let origins;
      try { origins = JSON.parse(values.ALLOWED_ORIGINS); } catch { origins = []; }
      requireCheck(Array.isArray(origins) && origins.includes(new URL(values.OAUTH_REDIRECT_URI).origin), 'BFF OAuth callback origin must be in ALLOWED_ORIGINS.');
    } else {
      requireCheck(!Object.hasOwn(values, 'OAUTH_CLIENT_SECRET'), 'MCP services must not receive the confidential client secret.');
    }
  }
}
function validateConfiguration(service, settings, references, config) {
  const values = Object.fromEntries(settings.map(setting => [setting.name, setting.value]));
  const required = ['JWT_SECRET_KEY', 'JWT_ISSUER', 'JWT_AUDIENCE', 'AUTH_INTERNAL_SECRET'];
  const secrets = ['JWT_SECRET_KEY', 'AUTH_INTERNAL_SECRET'];
  if (service !== 'identity') {
    required.push('AUTH_USERS_ENDPOINT', 'INTERNAL_IDENTITY_SECRET');
    secrets.push('INTERNAL_IDENTITY_SECRET');
    requireCheck(remoteHttps(values.AUTH_USERS_ENDPOINT), 'AUTH_USERS_ENDPOINT must be a remote HTTPS Identity URL without credentials, query or fragment.');
  }
  if (service !== 'responses-bff') {
    required.push('DATABASE_URL');
    secrets.push('DATABASE_URL');
  } else {
    requireCheck(!Object.hasOwn(values, 'DATABASE_URL'), 'DATABASE_URL must be absent from the DB-free Responses BFF.');
  }
  if (service === 'account' || service === 'transaction') required.push('CORS_ALLOWED_ORIGINS');
  validateOAuthConfiguration(service, values, required, secrets);
  for (const name of required) {
    requireCheck(typeof values[name] === 'string' && values[name].trim().length > 0, `Required app setting ${name} is missing.`);
  }
  for (const name of ['JWT_ISSUER', 'JWT_AUDIENCE']) {
    requireCheck(!values[name].trim().startsWith('@Microsoft.KeyVault('), `${name} must be an explicit coordinated value.`);
  }
  for (const name of secrets) {
    requireCheck(/^@Microsoft\.KeyVault\(SecretUri=https:\/\/[a-zA-Z0-9-]+\.vault\.azure\.net\/secrets\/[a-zA-Z0-9-]+\/?\)$/.test(values[name]), `${name} must use a versionless Key Vault reference.`);
    const reference = references.properties?.[name] ?? references.value?.find(item =>
      item.name === name || item.id?.endsWith(`/appsettings/${name}`))?.properties;
    requireCheck(reference?.status === 'Resolved', `Key Vault reference ${name} must be Resolved before deployment.`);
  }
  if (service === 'identity') {
    requireCheck(['true', '1'].includes(String(values.SCM_DO_BUILD_DURING_DEPLOYMENT).toLowerCase()), 'Enable SCM_DO_BUILD_DURING_DEPLOYMENT for Identity Oryx installation.');
    requireCheck(config.linuxFxVersion === 'PYTHON|3.11' && config.appCommandLine ===
      'python -m uvicorn identity.main:create_app --factory --host 0.0.0.0 --port 8000', 'Identity requires Python 3.11 and the documented factory startup command.');
    if (Object.hasOwn(values, 'ACCESS_TOKEN_MINUTES')) {
      requireCheck(/^[0-9]+$/.test(values.ACCESS_TOKEN_MINUTES) && Number(values.ACCESS_TOKEN_MINUTES) >= 1 &&
        Number(values.ACCESS_TOKEN_MINUTES) <= 60, 'ACCESS_TOKEN_MINUTES must be an integer from 1 to 60.');
    }
  }
}
function azureJson(args) {
  try {
    return JSON.parse(execFileSync('az', [...args, '--output', 'json', '--only-show-errors'],
      { encoding: 'utf8', stdio: ['ignore', 'pipe', 'pipe'], maxBuffer: 8 * 1024 * 1024 }));
  } catch {
    throw new PreflightError('Unable to read App Service configuration/reference metadata. Verify OIDC permissions and provisioning.');
  }
}
function main() {
  const service = process.env.PREFLIGHT_SERVICE;
  const group = process.env.PREFLIGHT_RESOURCE_GROUP;
  const app = selectApp(service, group, process.env.PREFLIGHT_APP_NAME);
  const target = ['--resource-group', group, '--name', app.name];
  const settings = azureJson(['webapp', 'config', 'appsettings', 'list', ...target]);
  const references = azureJson(['rest', '--method', 'get', '--url',
    `https://management.azure.com${app.id}/config/configreferences/appsettings?api-version=2022-03-01`]);
  const config = service === 'identity' ? azureJson(['webapp', 'config', 'show', ...target]) : {};
  validateConfiguration(service, settings, references, config);
  appendFileSync(process.env.GITHUB_OUTPUT, `app-name=${app.name}\n`);
  console.log('App Service configuration preflight passed. No connectivity or secret-value validation performed.');
}
if (require.main === module) {
  try { main(); } catch (error) {
    // Only locally controlled messages are emitted; Azure output and setting values stay private.
    console.error(`::error::${error instanceof PreflightError ? error.message : 'Invalid App Service metadata or preflight execution failure.'}`);
    process.exitCode = 1;
  }
}
module.exports = { selectApp, remoteHttps, validateConfiguration };
