function validateToolboxEndpoint(toolboxEndpoint) {
  if (typeof toolboxEndpoint !== 'string' || !toolboxEndpoint.trim()) {
    throw new Error('TOOLBOX_BANKING_TOOLS_MCP_ENDPOINT must be set.');
  }
}

module.exports = { validateToolboxEndpoint };
