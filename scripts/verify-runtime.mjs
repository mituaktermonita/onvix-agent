import fs from 'node:fs';
import { createRequire } from 'node:module';
const root = process.env.RUNTIME_ROOT || '/opt/runtime';
const require = createRequire(`${root}/package.json`);
const semver = require('semver');
const manifest = JSON.parse(fs.readFileSync(`${root}/package.json`, 'utf8'));
if (!semver.satisfies(process.version, manifest.engines.node)) throw new Error(`Unsupported Node ${process.version}`);
for (const name of ['openclaw', 'omniroute']) {
  const pkg = JSON.parse(fs.readFileSync(`${root}/node_modules/${name}/package.json`, 'utf8'));
  if (pkg.version !== manifest.dependencies[name]) throw new Error(`${name} version mismatch`);
  if (!semver.satisfies(process.version, pkg.engines.node)) throw new Error(`${name} does not support ${process.version}`);
  console.log(`${name} ${pkg.version}; Node ${process.version}`);
}
// This is the same prebuilt entrypoint used by OmniRoute's serve command.
fs.accessSync(`${root}/node_modules/omniroute/dist/server-ws.mjs`);
