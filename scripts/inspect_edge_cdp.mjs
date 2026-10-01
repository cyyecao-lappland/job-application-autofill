#!/usr/bin/env node
import {connectEdgeCdp, DEFAULT_CDP_ENDPOINT} from '../browser/cdp_connector.mjs';
import {identifyPlaywrightSession} from '../browser/playwright_backend.mjs';

function argumentsFrom(argv) {
  const result = {endpoint: DEFAULT_CDP_ENDPOINT};
  for (let index = 0; index < argv.length; index++) {
    const flag = argv[index];
    if (!['--endpoint', '--url', '--page-id'].includes(flag))
      throw new Error(`unknown_argument:${flag}`);
    const value = argv[++index];
    if (!value) throw new Error(`missing_value:${flag}`);
    if (flag === '--endpoint') result.endpoint = value;
    if (flag === '--url') result.exactUrl = value;
    if (flag === '--page-id') result.pageId = value;
  }
  return result;
}

let connection;
try {
  connection = await connectEdgeCdp(argumentsFrom(process.argv.slice(2)));
  const identity = await identifyPlaywrightSession(connection.session);
  process.stdout.write(`${JSON.stringify({
    endpoint: connection.endpoint,
    pages: connection.pages,
    selected: identity
  }, null, 2)}\n`);
} catch (error) {
  process.stderr.write(`${JSON.stringify({error: error.message, pages: error.pages}, null, 2)}\n`);
  process.exitCode = 1;
} finally {
  await connection?.closeConnection();
}
