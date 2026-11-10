import fs from 'node:fs';
import vm from 'node:vm';

const html = fs.readFileSync(new URL('../static/index.html', import.meta.url), 'utf8');
const scripts = [...html.matchAll(/<script\b([^>]*)>([\s\S]*?)<\/script>/g)]
  .filter(([, attrs]) => !/\bsrc\s*=/.test(attrs));
if (!scripts.length) throw new Error('No inline application script found');
for (const [, , source] of scripts) new vm.Script(source);
for (const name of ['recovery.js','complaints.js']) new vm.Script(fs.readFileSync(new URL(`../static/${name}`, import.meta.url),'utf8'));
console.log(`Frontend JavaScript syntax passed (${scripts.length} inline and 2 external scripts).`);
