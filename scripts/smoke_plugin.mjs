#!/usr/bin/env node
/**
 * Boots a real WordPress with the plugin mounted from app/plugin_template and
 * asserts every admin page actually loads.
 *
 * This exists because 0.27.0 shipped a page that WordPress refused to serve.
 * The code parsed, the API's own tests passed, and the defect was still there:
 * hiding a submenu with remove_submenu_page() also removes the $submenu entry
 * that admin.php's capability check reads, so the setup wizard answered "Sorry,
 * you are not allowed to access this page." Nothing short of running WordPress
 * could have caught that, so this runs WordPress.
 *
 * Checks, per admin page: HTTP 200, not a permission denial, and no PHP fatal,
 * warning or deprecation in the output. Also asserts that pages meant to stay
 * out of the sidebar are still reachable by URL, which is the exact property
 * that broke.
 *
 *   node scripts/smoke_plugin.mjs            # PHP 8.2 (the floor we support)
 *   node scripts/smoke_plugin.mjs --php 8.3
 *   node scripts/smoke_plugin.mjs --plugin /path/to/engage-ai
 *
 * Exits non-zero on the first failing page, so it works as a release gate.
 */
import { spawn } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';

const HERE = dirname(fileURLToPath(import.meta.url));
const DEFAULT_PLUGIN = resolve(HERE, '..', 'app', 'plugin_template', 'engage-ai');

function arg(name, fallback) {
  const i = process.argv.indexOf('--' + name);
  return i !== -1 && process.argv[i + 1] ? process.argv[i + 1] : fallback;
}

const PLUGIN_DIR = resolve(arg('plugin', DEFAULT_PLUGIN));
const PHP = arg('php', '8.2');
const PORT = Number(arg('port', '9411'));
const BASE = `http://127.0.0.1:${PORT}`;

// Every page the plugin registers. `hidden` means it must NOT appear in the
// sidebar but MUST still load when opened directly - the 0.27.0 regression.
const PAGES = [
  { slug: 'engageai-dashboard', label: 'Dashboard' },
  { slug: 'engageai-analytics', label: 'Analytics' },
  { slug: 'engageai-ideas', label: 'Ideas' },
  { slug: 'engageai-campaigns', label: 'Campaigns' },
  { slug: 'engageai-studio', label: 'Content Studio' },
  { slug: 'engageai-content', label: 'Content Library' },
  { slug: 'engageai-calendar', label: 'Calendar' },
  { slug: 'engageai-channels', label: 'Channels' },
  { slug: 'engageai-channel-setup', label: 'Set up a channel', hidden: true },
  { slug: 'engageai-cycle', label: 'Engagement Cycle' },
  { slug: 'engageai-agents', label: 'Agents' },
  { slug: 'engageai-assistant', label: 'AI Assistant' },
  { slug: 'engageai-settings', label: 'Settings' },
];

// Tabs are real destinations with their own URL, so they get checked like any
// other page - a tab that fatals is just as broken as a menu item that does.
const TABS = [
  { url: '/wp-admin/admin.php?page=engageai-content&view=types', label: 'Content types' },
  { url: '/wp-admin/admin.php?page=engageai-analytics&view=performance', label: 'Post performance' },
  { url: '/wp-admin/admin.php?page=engageai-ideas&view=dismissed', label: 'Dismissed ideas' },
];

const PHP_PROBLEM = /(Fatal error|Parse error|Warning:|Deprecated:|Notice:)[^<\n]{0,120}/g;
const DENIED = 'you are not allowed to access this page';

const server = spawn('npx', [
  '--yes', '@wp-playground/cli@latest', 'server',
  '--php', PHP, '--wp', 'latest',
  '--port', String(PORT),
  '--login',
  '--internal-cookie-store',
  '--auto-mount', PLUGIN_DIR,
], { stdio: ['ignore', 'pipe', 'pipe'] });

let serverLog = '';
server.stdout.on('data', (d) => { serverLog += d; });
server.stderr.on('data', (d) => { serverLog += d; });

function shutdown(code) {
  server.kill('SIGTERM');
  process.exit(code);
}

// Node's fetch keeps no cookie jar, and every page under test is behind
// wp-admin, so an unauthenticated run would just measure the login screen and
// call it a pass. Keep the session by hand.
const jar = new Map();

function remember(res) {
  for (const raw of res.headers.getSetCookie?.() ?? []) {
    const [pair] = raw.split(';');
    const idx = pair.indexOf('=');
    if (idx > 0) jar.set(pair.slice(0, idx).trim(), pair.slice(idx + 1).trim());
  }
}

async function get(path, init = {}) {
  const cookie = [...jar].map(([k, v]) => `${k}=${v}`).join('; ');
  const res = await fetch(BASE + path, {
    ...init,
    redirect: 'manual',
    headers: { ...(init.headers || {}), ...(cookie ? { cookie } : {}) },
  });
  remember(res);
  // Follow WordPress's post-login redirect chain, carrying cookies forward.
  if ([301, 302, 303, 307, 308].includes(res.status)) {
    const loc = res.headers.get('location');
    if (loc) return get(loc.replace(BASE, ''), { method: 'GET' });
  }
  return res;
}

/**
 * True once wp-admin actually renders for us. With --internal-cookie-store the
 * server already holds the --login session, so this passes with no work; the
 * form POST below is the fallback for when it doesn't.
 */
async function isAuthenticated() {
  const res = await get('/wp-admin/');
  return res.status === 200 && (await res.text()).includes('adminmenu');
}

async function login() {
  if (await isAuthenticated()) return true;

  await get('/wp-login.php');
  const body = new URLSearchParams({
    log: 'admin',
    pwd: 'password',
    'wp-submit': 'Log In',
    redirect_to: BASE + '/wp-admin/',
    testcookie: '1',
  });
  await get('/wp-login.php', {
    method: 'POST',
    body,
    headers: { 'content-type': 'application/x-www-form-urlencoded' },
  });
  return isAuthenticated();
}

async function waitForBoot(timeoutMs = 240000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    if (server.exitCode !== null) {
      console.error('Playground exited before serving:\n' + serverLog.slice(-2000));
      return false;
    }
    // Not "any response" - Playground answers 502 while it is still starting,
    // and treating that as ready races the checks into a dead server. Wait
    // until WordPress itself answers: either the admin (already authenticated,
    // which --internal-cookie-store gives us) or a real login form.
    try {
      const r = await fetch(BASE + '/wp-admin/', { redirect: 'manual' });
      if (r.status === 200 || r.status === 302) return true;
    } catch {}
    await new Promise((r) => setTimeout(r, 2000));
  }
  console.error('Timed out waiting for Playground:\n' + serverLog.slice(-2000));
  return false;
}

const run = async () => {
  console.log(`Booting WordPress (PHP ${PHP}) with ${PLUGIN_DIR}`);
  if (!(await waitForBoot())) shutdown(1);

  if (!(await login())) {
    console.error('FAIL  Could not log in to wp-admin - the run would only be measuring the login screen.');
    shutdown(1);
  }

  // The plugin must be active, or every page below would "pass" as a 404-ish
  // WordPress page and the whole run would be meaningless.
  const plugins = await (await get('/wp-admin/plugins.php')).text();
  if (!/Engage AI/.test(plugins)) {
    console.error('FAIL  Engage AI is not installed - nothing was actually tested.');
    shutdown(1);
  }
  const version = plugins.match(/Version\s*([\d.]+)\s*\|\s*By Vision Outreach Media/);
  console.log(`Plugin active: ${version ? version[1] : 'unknown version'}\n`);

  let failures = 0;

  for (const page of PAGES) {
    const res = await get(`/wp-admin/admin.php?page=${page.slug}`);
    const body = await res.text();
    const problems = body.match(PHP_PROBLEM) || [];
    const denied = body.includes(DENIED);
    const ok = res.status === 200 && !denied && problems.length === 0;

    if (ok) {
      console.log(`ok    ${page.slug}${page.hidden ? '  (hidden, still reachable)' : ''}`);
    } else {
      failures++;
      console.log(`FAIL  ${page.slug}  [HTTP ${res.status}]`);
      if (denied) {
        console.log('        WordPress refused to serve it: ' +
          '"Sorry, you are not allowed to access this page."');
        if (page.hidden) {
          console.log('        This page is meant to be hidden from the menu but still ' +
            'reachable. Hide it by registering it with an EMPTY parent slug - not by ' +
            'calling remove_submenu_page(), which also removes the capability check.');
        }
      }
      for (const p of problems.slice(0, 3)) console.log(`        ${p.trim()}`);
    }
  }

  // A hidden page that is also missing from the sidebar is the intended state;
  // assert the menu really doesn't carry it, so "reachable" wasn't achieved by
  // quietly putting it back.
  const dash = await (await get('/wp-admin/admin.php?page=engageai-dashboard')).text();
  for (const page of PAGES.filter((p) => p.hidden)) {
    if (dash.includes(`page=${page.slug}`)) {
      failures++;
      console.log(`FAIL  ${page.slug} is linked from the admin menu but should be hidden.`);
    } else {
      console.log(`ok    ${page.slug} absent from the sidebar`);
    }
  }

  for (const tab of TABS) {
    const res = await get(tab.url);
    const body = await res.text();
    const problems = body.match(PHP_PROBLEM) || [];
    const denied = body.includes(DENIED);
    if (res.status === 200 && !denied && problems.length === 0) {
      console.log(`ok    ${tab.label}  (tab)`);
    } else {
      failures++;
      console.log(`FAIL  ${tab.label}  [HTTP ${res.status}]${denied ? ' refused' : ''}`);
      for (const p of problems.slice(0, 3)) console.log(`        ${p.trim()}`);
    }
  }

  // The menu is supposed to read in workflow order. Registration order decides
  // it, so a page added in the wrong place silently lands in the wrong slot -
  // which nothing else here would notice.
  const expected = PAGES.filter((p) => !p.hidden).map((p) => p.slug);
  const seen = [...dash.matchAll(/page=(engageai-[a-z-]+)/g)].map((m) => m[1]);
  const order = expected.filter((slug) => seen.includes(slug));
  const actual = seen.filter((slug, i) => expected.includes(slug) && seen.indexOf(slug) === i);
  if (order.join() !== actual.join()) {
    failures++;
    console.log('FAIL  menu order');
    console.log(`        expected: ${order.join(' > ')}`);
    console.log(`        actual:   ${actual.join(' > ')}`);
  } else {
    console.log('ok    menu reads in workflow order');
  }

  console.log(`\n${failures ? failures + ' failing' : 'all checks passed'}`);
  shutdown(failures ? 1 : 0);
};

run().catch((err) => {
  console.error(err);
  shutdown(1);
});
