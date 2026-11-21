function validateToolboxEndpoint(toolboxEndpoint, name = 'TOOLBOX_ACCOUNT_TOOLS_MCP_ENDPOINT') {
  if (typeof toolboxEndpoint !== 'string' || !toolboxEndpoint.trim()) {
    throw new Error(`${name} must be set.`);
  }
}

module.exports = { validateToolboxEndpoint };
