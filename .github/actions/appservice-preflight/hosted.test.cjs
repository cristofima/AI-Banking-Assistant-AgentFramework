const test = require('node:test');
const assert = require('node:assert/strict');
const { readFileSync } = require('node:fs');
const { resolve } = require('node:path');
const { validateToolboxEndpoint } = require('./hosted.cjs');

const project = 'https://foundry.services.ai.azure.com/api/projects/banking';
const endpoint = `${project}/toolboxes/account-tools/versions/3/mcp?api-version=v1`;

test('accepts configured Toolbox endpoints without enforcing their format', () => {
  for (const value of [endpoint, `${project}/toolboxes/transaction-tools/mcp?api-version=v1`,
    'https://other.example/future-toolbox?api-version=v2']) {
    validateToolboxEndpoint(value);
  }
});

test('rejects missing or blank Toolbox configuration', () => {
  for (const value of [undefined, null, '', ' ', '\t\n']) {
    assert.throws(() => validateToolboxEndpoint(value), /must be set/);
  }
  assert.throws(() => validateToolboxEndpoint('', 'TOOLBOX_TRANSACTION_TOOLS_MCP_ENDPOINT'),
    /TOOLBOX_TRANSACTION_TOOLS_MCP_ENDPOINT must be set/);
});

test('hosted CD validates and exports Toolbox metadata before deploying only the agent', () => {
  const workflow = readFileSync(resolve(__dirname, '../../workflows/cd-hosted-agent.yaml'), 'utf8');
  for (const name of ['TOOLBOX_ACCOUNT_TOOLS_MCP_ENDPOINT', 'TOOLBOX_TRANSACTION_TOOLS_MCP_ENDPOINT']) {
    assert.match(workflow, new RegExp(`${name}: \\$\\{\\{ vars\\.${name} \\}\\}`));
    assert.match(workflow, new RegExp(`validateToolboxEndpoint\\(process\\.env\\.${name}`));
  }
  assert.match(workflow, /ACCOUNT_MCP_URL TRANSACTION_MCP_URL \\\r?\n\s+TOOLBOX_ACCOUNT_TOOLS_MCP_ENDPOINT TOOLBOX_TRANSACTION_TOOLS_MCP_ENDPOINT \\/);
  assert.match(workflow, /node --test .*hosted\.test\.cjs/);
  assert.ok(workflow.indexOf('validateToolboxEndpoint(process.env.') < workflow.indexOf('deploy home-banking-agent'));
  assert.doesNotMatch(workflow, /deploy (account|transaction|banking)-tools/);
  assert.doesNotMatch(workflow, /TOOLBOX_BANKING_TOOLS_MCP_ENDPOINT/);
});
