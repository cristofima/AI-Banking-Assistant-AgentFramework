const test = require('node:test');
const assert = require('node:assert/strict');
const { readFileSync } = require('node:fs');
const { resolve } = require('node:path');
const { validateToolboxEndpoint } = require('./hosted.cjs');

const project = 'https://foundry.services.ai.azure.com/api/projects/banking';
const endpoint = `${project}/toolboxes/banking-tools/versions/3/mcp?api-version=v1`;

test('accepts configured Toolbox endpoints without enforcing their format', () => {
  for (const value of [endpoint, `${project}/toolboxes/banking-tools/mcp?api-version=v1`,
    'https://other.example/future-toolbox?api-version=v2']) {
    validateToolboxEndpoint(value);
  }
});

test('rejects missing or blank Toolbox configuration', () => {
  for (const value of [undefined, null, '', ' ', '\t\n']) {
    assert.throws(() => validateToolboxEndpoint(value), /must be set/);
  }
});

test('hosted CD validates and exports Toolbox metadata before deploying only the agent', () => {
  const workflow = readFileSync(resolve(__dirname, '../../workflows/cd-hosted-agent.yaml'), 'utf8');
  assert.match(workflow, /TOOLBOX_BANKING_TOOLS_MCP_ENDPOINT: \$\{\{ vars\.TOOLBOX_BANKING_TOOLS_MCP_ENDPOINT \}\}/);
  assert.match(workflow, /validateToolboxEndpoint\(process\.env\.TOOLBOX_BANKING_TOOLS_MCP_ENDPOINT\)/);
  assert.match(workflow, /ACCOUNT_MCP_URL TRANSACTION_MCP_URL TOOLBOX_BANKING_TOOLS_MCP_ENDPOINT \\/);
  assert.match(workflow, /node --test .*hosted\.test\.cjs/);
  assert.ok(workflow.indexOf('validateToolboxEndpoint(process.env.') < workflow.indexOf('deploy home-banking-agent'));
  assert.doesNotMatch(workflow, /deploy banking-tools/);
});
