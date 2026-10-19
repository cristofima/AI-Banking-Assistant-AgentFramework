const { test } = require('node:test');
const assert = require('node:assert/strict');
const { selectApp, remoteHttps, validateConfiguration } = require('./preflight.cjs');
const { validateFrontend } = require('./frontend.cjs');

test('frontend build keeps public API paths and auth/OAuth/chat on the BFF', () => {
  const values = {
    VITE_ACCOUNT_API_URL: 'https://account-demo.azurewebsites.net/api',
    VITE_TRANSACTION_API_URL: 'https://transaction-demo.azurewebsites.net/api',
    VITE_RESPONSES_BFF_URL: 'https://bff-demo.azurewebsites.net',
    VITE_RESPONSES_API_URL: 'https://bff-demo.azurewebsites.net/responses',
  };
  validateFrontend(values);
  for (const name of Object.keys(values)) {
    for (const value of [undefined, '', 'http://app.azurewebsites.net/api', 'https://localhost/api', 'https://app.azurewebsites.net/wrong']) {
      assert.throws(() => validateFrontend({ ...values, [name]: value }));
    }
  }
  assert.throws(() => validateFrontend({ ...values, VITE_RESPONSES_API_URL: 'https://agent.azurewebsites.net/responses' }), /same BFF/);
  assert.throws(() => validateFrontend({ ...values, VITE_RESPONSES_BFF_URL: `${values.VITE_RESPONSES_BFF_URL}/` }), /trailing slash/);
});

const config = {
  linuxFxVersion: 'PYTHON|3.11',
  appCommandLine: 'python -m uvicorn identity.main:create_app --factory --host 0.0.0.0 --port 8000',
};
function fixture(service) {
  const values = {
    JWT_SECRET_KEY: '@Microsoft.KeyVault(SecretUri=https://demo-vault.vault.azure.net/secrets/jwt)',
    AUTH_INTERNAL_SECRET: '@Microsoft.KeyVault(SecretUri=https://demo-vault.vault.azure.net/secrets/auth)',
    JWT_ISSUER: 'banking', JWT_AUDIENCE: 'banking-web',
    SCM_DO_BUILD_DURING_DEPLOYMENT: 'true', ACCESS_TOKEN_MINUTES: '60',
    OAUTH_ISSUER: 'https://identity-demo.azurewebsites.net', OAUTH_AUDIENCE: 'banking-mcp',
  };
  if (service === 'identity') {
    values.OAUTH_FRONTEND_AUTHORIZE_URL = 'https://web-demo.azurewebsites.net/oauth/consent';
    values.OAUTH_SIGNING_KEY_ID = 'banking-key';
    values.OAUTH_SIGNING_BACKEND = 'key_vault';
    values.OAUTH_SIGNING_KEY_URI = 'https://demo-vault.vault.azure.net/keys/oauth-signing/0123456789abcdef';
  } else {
    values.MCP_IDENTITY_MODE = 'oauth';
    values.OAUTH_JWKS = '@Microsoft.KeyVault(SecretUri=https://demo-vault.vault.azure.net/secrets/oauth-jwks)';
  }
  if (service === 'responses-bff') {
    values.OAUTH_CLIENT_ID = 'banking-web';
    values.OAUTH_CLIENT_SECRET = '@Microsoft.KeyVault(SecretUri=https://demo-vault.vault.azure.net/secrets/oauth-client-secret)';
    values.OAUTH_REDIRECT_URI = 'https://web-demo.azurewebsites.net/oauth/callback';
    values.ALLOWED_ORIGINS = JSON.stringify(['https://web-demo.azurewebsites.net']);
  }
  if (service !== 'responses-bff') values.DATABASE_URL = '@Microsoft.KeyVault(SecretUri=https://demo-vault.vault.azure.net/secrets/database)';
  if (service !== 'identity') {
    values.AUTH_USERS_ENDPOINT = 'https://identity-demo.azurewebsites.net';
    values.INTERNAL_IDENTITY_SECRET = '@Microsoft.KeyVault(SecretUri=https://demo-vault.vault.azure.net/secrets/transport)';
  }
  if (service === 'account' || service === 'transaction') values.CORS_ALLOWED_ORIGINS = 'https://web-demo.azurewebsites.net';
  const properties = Object.fromEntries(Object.keys(values).filter(name => values[name].startsWith('@Microsoft')).map(name => [name, { status: 'Resolved' }]));
  return { values, references: { properties } };
}
function check(service, fixture, runtime = config) {
  validateConfiguration(service, Object.entries(fixture.values).map(([name, value]) => ({ name, value })), fixture.references, runtime);
}
for (const service of ['identity', 'account', 'transaction', 'responses-bff']) {
  test(`${service}: valid configuration and both reference response shapes`, () => {
    const f = fixture(service);
    check(service, f);
    for (const name of Object.keys(f.references.properties)) f.values[name] = f.values[name].replace(')', '/)');
    check(service, f);
    f.references = { value: Object.entries(f.references.properties).map(([name, properties]) => ({ id: `/appsettings/${name}`, properties })) };
    check(service, f);
  });
}
test('explicit target uses named lookup regardless of missing or conflicting tags', () => {
  for (const tags of [undefined, { 'azd-service-name': 'account', 'azd-env-name': 'production' }]) {
    const app = { name: 'identity-demo', id: '/subscriptions/demo/resourceGroups/demo/providers/Microsoft.Web/sites/identity-demo', tags };
    assert.equal(selectApp('identity', 'demo-group', app.name, args => {
      assert.deepEqual(args, ['webapp', 'show', '--resource-group', 'demo-group', '--name', app.name]);
      return app;
    }), app);
  }
});
test('explicit target rejects missing/invalid names before querying Azure', () => {
  const noQuery = () => assert.fail('Azure must not be called');
  for (const name of [undefined, '', ' ', '-invalid', 'invalid/', 'x'.repeat(61)]) {
    assert.throws(() => selectApp('identity', 'demo-group', name, noQuery));
  }
  assert.throws(() => selectApp('web', 'demo-group', 'web-demo', noQuery));
  assert.throws(() => selectApp('identity', '', 'identity-demo', noQuery));
});
test('explicit target rejects missing resource or mismatched metadata', () => {
  for (const app of [null, {}, { name: 'identity-other' }, { name: 'identity-demo', id: '/providers/Microsoft.Web/sites/identity-other' }]) {
    assert.throws(() => selectApp('identity', 'demo-group', 'identity-demo', () => app));
  }
  assert.throws(() => selectApp('identity', 'demo-group', 'identity-demo', () => { throw new Error('Named App Service not found.'); }));
});
test('remote HTTPS requires nonlocal host and no embedded auth/query/fragment', () => {
  assert.equal(remoteHttps('https://identity-demo.azurewebsites.net/'), true);
  for (const value of ['http://identity-demo.azurewebsites.net', 'https://localhost', 'https://127.0.0.1', 'https://[::1]', 'https://app.localhost', 'https://app.internal', 'https://app.test', 'https://user:password@app.azurewebsites.net', 'https://app.azurewebsites.net?q=1', 'https://app.azurewebsites.net/#test', 'not-url']) assert.equal(remoteHttps(value), false);
});
test('required settings and explicit issuer/audience reject missing or reference values', () => {
  for (const name of ['DATABASE_URL', 'JWT_SECRET_KEY', 'JWT_ISSUER', 'JWT_AUDIENCE', 'AUTH_INTERNAL_SECRET']) {
    const f = fixture('identity');
    delete f.values[name];
    assert.throws(() => check('identity', f));
  }
  for (const name of ['JWT_ISSUER', 'JWT_AUDIENCE']) {
    const f = fixture('identity');
    f.values[name] = f.values.JWT_SECRET_KEY;
    assert.throws(() => check('identity', f));
  }
});
test('secret references must be versionless and resolved', () => {
  for (const value of ['plaintext-sensitive-value', '@Microsoft.KeyVault(SecretUri=https://demo-vault.vault.azure.net/secrets/jwt/version)', '@Microsoft.KeyVault(SecretUri=https://demo-vault.vault.azure.net/secrets/jwt?query=1)']) {
    const f = fixture('identity'); f.values.JWT_SECRET_KEY = value;
    assert.throws(() => check('identity', f), error => !error.message.includes(value));
  }
  for (const status of ['AccessToKeyVaultDenied', 'SecretNotFound', undefined]) {
    const f = fixture('identity'); f.references.properties.AUTH_INTERNAL_SECRET.status = status;
    assert.throws(() => check('identity', f));
  }
});
test('OAuth rejects missing mode, HMAC fallback and unknown mode', () => {
  for (const service of ['account', 'transaction', 'responses-bff']) {
    for (const mode of [undefined, '', 'hmac', 'OAuth', 'unknown']) {
      const f = fixture(service); f.values.MCP_IDENTITY_MODE = mode;
      assert.throws(() => check(service, f), /explicit MCP_IDENTITY_MODE=oauth/);
    }
  }
});
test('OAuth requires provider and consumer settings', () => {
  for (const service of ['identity', 'account', 'transaction', 'responses-bff']) {
    const baseline = fixture(service);
    for (const name of Object.keys(baseline.values).filter(name => name.startsWith('OAUTH_'))) {
      const f = fixture(service); delete f.values[name];
      assert.throws(() => check(service, f));
    }
  }
});
test('OAuth issuer is the coordinated public Identity origin without trailing slash', () => {
  for (const issuer of ['http://identity-demo.azurewebsites.net', 'https://localhost', 'https://identity-demo.azurewebsites.net/', 'https://identity-demo.azurewebsites.net/oauth', 'https://other.azurewebsites.net']) {
    const f = fixture('account'); f.values.OAUTH_ISSUER = issuer;
    assert.throws(() => check('account', f));
  }
});
test('OAuth callbacks use the frontend consent/callback routes and allowed BFF origin', () => {
  for (const [service, name] of [['identity', 'OAUTH_FRONTEND_AUTHORIZE_URL'], ['responses-bff', 'OAUTH_REDIRECT_URI']]) {
    for (const value of ['http://web-demo.azurewebsites.net/oauth/callback', 'https://localhost/oauth/callback', 'https://web-demo.azurewebsites.net/wrong', 'https://web-demo.azurewebsites.net/oauth/callback?q=1']) {
      const f = fixture(service); f.values[name] = value;
      assert.throws(() => check(service, f));
    }
  }
  for (const origins of ['invalid-json', '{}', '[]', '["https://other.azurewebsites.net"]']) {
    const f = fixture('responses-bff'); f.values.ALLOWED_ORIGINS = origins;
    assert.throws(() => check('responses-bff', f));
  }
});
test('OAuth key custody and versionless resolved references are enforced without leaking values', () => {
  for (const [service, name] of [['account', 'OAUTH_JWKS'], ['transaction', 'OAUTH_JWKS'], ['responses-bff', 'OAUTH_CLIENT_SECRET'], ['responses-bff', 'OAUTH_JWKS']]) {
    for (const value of ['sensitive-fixture', '@Microsoft.KeyVault(SecretUri=https://demo-vault.vault.azure.net/secrets/oauth/version)']) {
      const f = fixture(service); f.values[name] = value;
      assert.throws(() => check(service, f), error => !error.message.includes(value));
    }
    const f = fixture(service); f.references.properties[name].status = 'SecretNotFound';
    assert.throws(() => check(service, f));
  }
  for (const service of ['account', 'transaction', 'responses-bff']) {
    const f = fixture(service); f.values.OAUTH_SIGNING_PRIVATE_KEY = '';
    assert.throws(() => check(service, f), /Only Identity/);
  }
  for (const service of ['account', 'transaction']) {
    const f = fixture(service); f.values.OAUTH_CLIENT_SECRET = '';
    assert.throws(() => check(service, f), /confidential client secret/);
  }
});
test('Native Identity requires pinned Azure RSA signing configuration and no PEM fallback', () => {
  for (const value of ['', 'pem', 'unknown']) {
    const f = fixture('identity'); f.values.OAUTH_SIGNING_BACKEND = value;
    assert.throws(() => check('identity', f));
  }
  for (const value of ['https://demo-vault.vault.azure.net/keys/oauth-signing', 'http://demo-vault.vault.azure.net/keys/oauth/version', 'https://untrusted.example/keys/oauth/version']) {
    const f = fixture('identity'); f.values.OAUTH_SIGNING_KEY_URI = value;
    assert.throws(() => check('identity', f));
  }
  const f = fixture('identity'); f.values.OAUTH_SIGNING_PRIVATE_KEY = '';
  assert.throws(() => check('identity', f), /private PEM/);
});
test('OAuth audience and signing key ID must not be references', () => {
  for (const name of ['OAUTH_AUDIENCE', 'OAUTH_SIGNING_KEY_ID']) {
    const f = fixture('identity'); f.values[name] = f.values.JWT_SECRET_KEY;
    assert.throws(() => check('identity', f));
  }
});
test('BFF rejects any database setting including empty', () => {
  const f = fixture('responses-bff'); f.values.DATABASE_URL = '';
  assert.throws(() => check('responses-bff', f));
});
test('Identity requires documented runtime/Oryx and bounded token minutes', () => {
  const f = fixture('identity');
  assert.throws(() => check('identity', f, { ...config, linuxFxVersion: 'PYTHON|3.12' }));
  assert.throws(() => check('identity', f, { ...config, appCommandLine: 'uvicorn identity.main:app' }));
  f.values.SCM_DO_BUILD_DURING_DEPLOYMENT = 'false';
  assert.throws(() => check('identity', f));
  f.values.SCM_DO_BUILD_DURING_DEPLOYMENT = '1';
  for (const value of ['0', '61', '1.5', '-1', '']) { f.values.ACCESS_TOKEN_MINUTES = value; assert.throws(() => check('identity', f)); }
  delete f.values.ACCESS_TOKEN_MINUTES;
  check('identity', f);
});
