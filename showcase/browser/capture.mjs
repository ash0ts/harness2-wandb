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
    'focus-text': {type: 'string'},
    viewport: {type: 'string', default: '1600x1000'},
    'expand-output': {type: 'boolean', default: false},
    authenticated: {type: 'boolean', default: false},
    chrome: {type: 'boolean', default: false},
    'full-page': {type: 'boolean', default: false},
    'video-dir': {type: 'string'},
    'hold-seconds': {type: 'string', default: '5'},
    tabs: {type: 'string'},
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
const holdSeconds = Number(values['hold-seconds']);
const tabs = values.tabs ? values.tabs.split(',') : [];
const [width, height] = values.viewport.split('x').map(Number);
if (!/^\d{3,4}x\d{3,4}$/.test(values.viewport) || width < 320 || width > 2560 || height < 400 || height > 2160) {
  throw new Error('Use a viewport such as 1600x1000, between 320x400 and 2560x2160');
}
const viewport = {width, height};
if (!Number.isFinite(holdSeconds) || holdSeconds < 1 || holdSeconds > 30
  || tabs.some(tab => !['Thread', 'Spans', 'Timeline'].includes(tab))) {
  throw new Error('Use a 1–30 second hold and Thread,Spans,Timeline tab names');
}
if (tabs.length && !values['video-dir']) throw new Error('--tabs requires --video-dir');
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
if (tabs.length && (values.view || !target.pathname.includes('/conversations/') || target.pathname.includes('/views/'))) {
  throw new Error('--tabs requires an Agent Lens conversation URL');
}
if (values['expand-output'] && !target.pathname.includes('/spans/')) {
  throw new Error('--expand-output requires an Agent Lens span URL');
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
    viewport,
    reducedMotion: 'reduce',
    ...(values['video-dir'] ? {
      recordVideo: {dir: path.resolve(values['video-dir']), size: viewport},
    } : {}),
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
  async function fitTimeline() {
    const tab = page.getByRole('tab', {name: 'Timeline', exact: true});
    if (!await tab.count() || await tab.getAttribute('aria-selected') !== 'true') return null;
    const root = page.getByRole('button', {name: /^invoke_agent /}).first();
    await root.waitFor({state: 'visible', timeout: 15000});
    const zoomOut = page.getByRole('button', {name: 'Zoom out', exact: true});
    for (let clicks = 0; clicks <= 40; clicks++) {
      const bounds = await root.boundingBox();
      if (bounds && bounds.x + bounds.width <= page.viewportSize().width - 24) return clicks;
      if (clicks === 40 || await zoomOut.isDisabled()) break;
      await zoomOut.click();
      await page.waitForTimeout(80);
    }
    throw new Error('The native timeline did not fit after bounded zoom adjustments');
  }
  const openedAt = Date.now();
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
  let expandedOutput = false;
  if (values['expand-output']) {
    const section = page.getByText('Output', {exact: true}).locator('..').locator('..');
    await section.waitFor({timeout: 15000});
    const expander = section.locator('[role="button"].hover-reveal-scope');
    if (await expander.count()) {
      await expander.click();
      await page.waitForTimeout(250);
      expandedOutput = true;
    }
  }
  if (values['focus-text']) {
    const focus = page.getByText(values['focus-text'], {exact: false}).first();
    await focus.waitFor({timeout: 15000});
    await focus.evaluate(node => node.scrollIntoView({block: 'center'}));
  }
  const timelineZoom = await fitTimeline();
  await page.screenshot({path: output, fullPage: values['full-page']});
  const readySeconds = (Date.now() - openedAt) / 1000;
  const metadata = {
    captured_at: new Date().toISOString(),
    url: page.url(),
    http_status: response.status(),
    title: await page.title(),
    authenticated: values.authenticated,
    initial_cookies: initialCookies,
    credentials_injected: values.authenticated,
    capture: 'Playwright headless browser, native product UI',
    viewport,
    expanded_output: expandedOutput,
    focused_text: values['focus-text'] || null,
    timeline_zoom_out_clicks: timelineZoom,
    visible_text: await page.locator('body').innerText(),
  };
  if (values['video-dir']) {
    const actions = [{seconds: readySeconds, action: 'Initial content ready', url: page.url()}];
    await page.waitForTimeout(holdSeconds * 1000);
    for (const tab of tabs) {
      await page.getByRole('tab', {name: tab, exact: true}).click();
      await page.getByRole('tab', {name: tab, exact: true, selected: true}).waitFor({timeout: 15000});
      const zoomClicks = await fitTimeline();
      actions.push({
        seconds: (Date.now() - openedAt) / 1000,
        action: tab,
        timeline_zoom_out_clicks: zoomClicks,
        url: page.url(),
      });
      await page.waitForTimeout(holdSeconds * 1000);
    }
    metadata.recording = {
      file: await page.video().path(),
      content_ready_seconds: readySeconds,
      actions,
      timing_reference: 'Approximate elapsed seconds since newPage completed; inspect frames when editing.',
      description: 'Continuous native browser recording; loading frames precede content_ready_seconds.',
    };
  }
  await context.close();
  await fs.writeFile(output + '.json', JSON.stringify(metadata, null, 2) + '\n');
  console.log(JSON.stringify({output, title: metadata.title, authenticated: metadata.authenticated}));
} finally {
  await browser.close();
}
