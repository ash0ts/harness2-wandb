import {chromium} from 'playwright';
import fs from 'node:fs/promises';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {parseArgs} from 'node:util';

const {values} = parseArgs({
  options: {
    manifest: {type: 'string'},
    view: {type: 'string'},
    url: {type: 'string'},
    output: {type: 'string'},
    'wait-text': {type: 'string'},
    authenticated: {type: 'boolean', default: false},
    chrome: {type: 'boolean', default: false},
    'full-page': {type: 'boolean', default: false},
    help: {type: 'boolean', default: false},
  },
});
if (values.help) {
  console.log('node showcase/browser/capture.mjs --manifest showcase/pilot-v3.json --view Adaptation --authenticated --chrome --output screenshot.png\nFor signed-out checks, omit --authenticated and supply --url and --wait-text.');
  process.exit(0);
}
if (!values.output || (!values.view && !values.url) || (values.view && values.url)) {
  throw new Error('Supply --output and exactly one of --view or --url');
}
const manifestPath = path.resolve(values.manifest || fileURLToPath(new URL('../pilot-v3.json', import.meta.url)));
const manifest = JSON.parse(await fs.readFile(manifestPath, 'utf8'));
const state = path.resolve(path.dirname(manifestPath), manifest.state_dir);
const views = values.view
  ? JSON.parse(await fs.readFile(path.join(state, 'presentation/views.json'), 'utf8'))
  : {};
const url = values.url || views[values.view]?.url;
if (!url) throw new Error('Run showcase/configure_views.py before capturing a saved view');
const target = new URL(url);
if (target.protocol !== 'https:' || !['forge.coreweave.com', 'wandb.ai'].includes(target.hostname)) {
  throw new Error('Capture supports HTTPS Agent Lens and W&B pages');
}
if (values.authenticated && (
  target.hostname !== 'forge.coreweave.com'
  || !target.pathname.startsWith(`/agent-lens/${manifest.project}/`)
)) {
  throw new Error('Authenticated capture is restricted to this manifest’s Agent Lens project');
}
if (!values.view && !values['wait-text']) {
  throw new Error('Supply --wait-text to wait for actual content before capture');
}
const output = path.resolve(values.output);
await fs.mkdir(path.dirname(output), {recursive: true});
const browser = await chromium.launch({headless: true, ...(values.chrome ? {channel: 'chrome'} : {})});
try {
  const context = await browser.newContext({
    viewport: {width: 1600, height: 1000},
    reducedMotion: 'reduce',
  });
  const initialCookies = (await context.cookies()).length;
  if (values.authenticated) {
    const key = process.env.WANDB_API_KEY;
    if (!key) throw new Error('WANDB_API_KEY is required for authenticated capture');
    await context.route(
      u => u.protocol === 'https:' && ['forge.coreweave.com', 'api.wandb.ai'].includes(u.hostname),
      route => route.continue({
        headers: {
          ...route.request().headers(),
          Authorization: new URL(route.request().url()).hostname === 'api.wandb.ai'
            ? 'Basic ' + Buffer.from('api:' + key).toString('base64')
            : 'Bearer ' + key,
        },
      }),
    );
  }
  const page = await context.newPage();
  const response = await page.goto(url, {waitUntil: 'domcontentloaded', timeout: 60000});
  if (!response?.ok()) throw new Error(`Page returned HTTP ${response?.status()}`);
  if (values.view) {
    await page.getByRole('columnheader', {name: 'harness2.condition', exact: true}).waitFor({timeout: 45000});
    await page.getByRole('cell').filter({hasText: /[0-9a-f]{6}/}).first().waitFor({timeout: 45000});
  } else if (target.pathname.includes('/conversations/')) {
    await page.getByRole('tab', {name: 'Thread', exact: true}).waitFor({timeout: 45000});
    const fullscreen = page.getByRole('button', {name: 'Full screen', exact: true});
    if (await fullscreen.count()) await fullscreen.click({timeout: 5000});
  }
  if (values['wait-text']) {
    await page.getByText(values['wait-text'], {exact: false}).first().waitFor({timeout: 45000});
  }
  const cookieClose = page.locator('#onetrust-close-btn-container button');
  if (await cookieClose.isVisible().catch(() => false)) {
    await cookieClose.click({timeout: 3000, force: true});
    await cookieClose.waitFor({state: 'hidden', timeout: 3000});
  }
  await page.screenshot({path: output, fullPage: values['full-page']});
  const metadata = {
    captured_at: new Date().toISOString(),
    url: page.url(),
    http_status: response.status(),
    title: await page.title(),
    authenticated: values.authenticated,
    initial_cookies: initialCookies,
    credentials_injected: values.authenticated,
    capture: 'Playwright headless browser, native product UI',
    viewport: {width: 1600, height: 1000},
    visible_text: await page.locator('body').innerText(),
  };
  await fs.writeFile(output + '.json', JSON.stringify(metadata, null, 2) + '\n');
  console.log(JSON.stringify({output, title: metadata.title, authenticated: metadata.authenticated}));
  await context.close();
} finally {
  await browser.close();
}
