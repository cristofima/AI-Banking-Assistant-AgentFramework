const { remoteHttps } = require('./preflight.cjs');

function validateFrontend(values) {
  const paths = {
    VITE_ACCOUNT_API_URL: '/api',
    VITE_TRANSACTION_API_URL: '/api',
    VITE_RESPONSES_BFF_URL: '/',
    VITE_RESPONSES_API_URL: '/responses',
  };
  for (const [name, path] of Object.entries(paths)) {
    if (!remoteHttps(values[name]) || new URL(values[name]).pathname !== path) {
      throw new Error(`${name} must be a public HTTPS URL with the documented path.`);
    }
  }
  const bff = new URL(values.VITE_RESPONSES_BFF_URL);
  if (values.VITE_RESPONSES_BFF_URL.endsWith('/') || new URL(values.VITE_RESPONSES_API_URL).origin !== bff.origin) {
    throw new Error('Frontend auth/OAuth and Responses must use the same BFF origin without a trailing slash.');
  }
}
if (require.main === module) {
  try {
    validateFrontend(process.env);
    console.log('Frontend public URL preflight passed; no credentials or runtime probes used.');
  } catch (error) {
    console.error(`::error::${error.message}`);
    process.exitCode = 1;
  }
}
module.exports = { validateFrontend };
