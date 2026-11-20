// End to end: the real page (jsdom) -> the real Python server -> real SQLite files.
// Two data folders: "mac" (organized) and "new" (only the games a sync would bring). Export on one, import on the other.
//
//   cd tests/ui && npm install && npm test
const { JSDOM } = require('jsdom');
const { spawn, spawnSync } = require('child_process');
const fs = require('fs');
const os = require('os');
const path = require('path');

const ROOT = path.resolve(__dirname, '..', '..');
const PYTHON = process.env.PYTHON || [path.join(ROOT, '.venv', 'bin', 'python'), path.join(ROOT, '.venv', 'Scripts', 'python.exe')].find(fs.existsSync) || 'python3';
const tick = (ms = 30) => new Promise(r => setTimeout(r, ms));
let failures = 0;
const ok = (name, cond, extra = '') => { if (!cond) failures++; console.log((cond ? 'PASS  ' : 'FAIL  ') + name + (cond ? '' : '   ' + extra)); };

const servers = [];
async function startServer(label, organized) {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), `gl-ui-${label}-`));
  const env = { ...process.env, GAMELIBRARY_DATA_DIR: dir, PYTHONUTF8: '1' };
  const seeded = spawnSync(PYTHON, [path.join(__dirname, 'seed.py'), ...(organized ? ['--organized'] : [])], { env, encoding: 'utf8' });
  if (seeded.status !== 0) throw new Error('seed failed: ' + seeded.stderr);
  const port = 20000 + Math.floor(Math.random() * 30000);
  const child = spawn(PYTHON, ['-m', 'gamelibrary.cli', 'serve', '--no-browser', '--port', String(port)], { env, cwd: ROOT, stdio: 'ignore' });
  servers.push(child);
  const base = `http://127.0.0.1:${port}`;
  for (let i = 0; i < 100; i++) { try { if ((await fetch(base + '/api/games')).ok) return { base, dir }; } catch {} await tick(100); }
  throw new Error('server did not start');
}

async function openPage(base) {
  const calls = [], downloads = [], blobs = [], alerts = [], confirms = [], errors = [];
  let confirmAnswer = true;
  const dom = await JSDOM.fromURL(base + '/', {
    runScripts: 'dangerously', resources: 'usable', pretendToBeVisual: true,
    beforeParse(w) {
      w.fetch = (u, o) => { calls.push([String(u), o && o.body ? JSON.parse(o.body) : null]); return fetch(new URL(u, base), o); };
      w.CSS = { escape: s => s };
      w.HTMLDialogElement.prototype.showModal = function () { this.setAttribute('open', ''); };
      w.HTMLDialogElement.prototype.close = function () { if (this.hasAttribute('open')) { this.removeAttribute('open'); this.dispatchEvent(new w.Event('close')); } };
      w.URL.createObjectURL = b => { blobs.push(b); return 'blob:test'; };
      w.URL.revokeObjectURL = () => {};
      w.HTMLAnchorElement.prototype.click = function () { downloads.push(this.download); };
      w.alert = m => alerts.push(m);
      w.confirm = m => { confirms.push(m); return confirmAnswer; };
      w.addEventListener('error', e => errors.push(e.message));
    },
  });
  const w = dom.window, d = w.document;
  for (let i = 0; i < 100 && !d.querySelector('#list li, #list .card'); i++) await tick(50);
  const page = {
    w, d, calls, downloads, blobs, alerts, confirms, errors,
    setConfirm: v => { confirmAnswer = v; },
    $: id => d.getElementById(id),
    titles: () => [...d.querySelectorAll('#list .title')].map(e => e.textContent.trim()).sort(),
    text: blob => new Promise((resolve, reject) => { const r = new w.FileReader(); r.onload = () => resolve(r.result); r.onerror = reject; r.readAsText(blob); }),
    lastExport: async () => JSON.parse(await page.text(blobs.at(-1))),
    check: (name, value, on = true) => { const el = d.querySelector(`input[name="${name}"][value="${value}"]`); el.checked = on; el.dispatchEvent(new w.Event('change', { bubbles: true })); },
    chooseFile: async (text, name = 'export.json') => {
      const input = page.$('importFile');
      Object.defineProperty(input, 'files', { value: [new w.File([text], name, { type: 'application/json' })], configurable: true });
      input.dispatchEvent(new w.Event('change', { bubbles: true }));
      for (let i = 0; i < 40; i++) { await tick(25); if (!page.$('importApply').disabled || !page.$('importError').hidden) break; }
      await tick(50);
    },
    submit: async form => { form.dispatchEvent(new w.Event('submit', { cancelable: true })); await tick(200); },
  };
  return page;
}

const api = async (base, p, body) => (await fetch(base + p, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body || {}) })).json();
const organization = async base => {
  const groups = await (await fetch(base + '/api/games')).json();
  return Object.fromEntries(groups.filter(g => g.statuses.length || g.tags.length || g.rating || g.note || g.developer_custom || g.release_year_custom || g.playtime_custom || g.started || g.finished || g.custom_cover || g.hidden)
    .map(g => [g.key, { s: g.statuses, t: g.tags, r: g.rating, n: g.note, dc: g.developer_custom && g.developer, y: g.release_year_custom && g.release_year, p: g.playtime_custom && g.playtime_minutes, d: [g.started, g.finished], c: g.custom_cover, h: g.hidden }]));
};

(async () => {
  const mac = await startServer('mac', true);
  const fresh = await startServer('new', false);
  try {
    // ===================================================== export (on the organized computer)
    const A = await openPage(mac.base);
    const header = [...A.d.querySelectorAll('.header-actions button')].map(b => b.textContent.trim());
    ok('header has Exportar, Importar and + Adicionar jogo', header.join('|') === 'Exportar|Importar|+ Adicionar jogo', header.join('|'));

    A.$('exportBtn').click();
    ok('export dialog opens with "Tudo" selected and the filters hidden', A.$('exportDlg').open && A.d.querySelector('input[name="expScope"]:checked').value === 'all' && A.$('expFilters').hidden);
    ok('screen count matches what is listed', A.$('expScreenCount').textContent === String(A.titles().length));
    ok('store checkboxes list the stores of the library', [...A.d.querySelectorAll('input[name="expPlatform"]')].map(i => i.value).join() === 'battlenet,epic,gog,steam');
    ok('status checkboxes list all 8 statuses', A.d.querySelectorAll('input[name="expStatus"]').length === 8);
    ok('tag checkboxes list the existing tags', [...A.d.querySelectorAll('input[name="expTag"]')].map(i => i.value).join() === 'coop,horror,puzzle');

    await A.submit(A.$('exportForm'));
    ok('export all: one file named game-library-tudo-DATE.json', A.downloads.length === 1 && /^game-library-tudo-\d{4}-\d{2}-\d{2}\.json$/.test(A.downloads[0]), A.downloads.join());
    const all = await A.lastExport();
    ok('the file is a Game Library export with the organization', all.format === 'gamelibrary-export' && all.version === 1 && all.groups.length === 7, String(all.groups?.length));
    ok('...including the manual game and the alias', all.manual_games.length === 1 && all.manual_games[0].title === 'Diablo IV' && all.aliases.length === 1);
    ok('the page tells what was exported', /Arquivo gerado: \d+ jogos com organização, 1 jogos manuais/.test(A.$('exportOk').textContent), A.$('exportOk').textContent);
    ok('nothing secret or from the stores is in the file', !JSON.stringify(all).match(/token|STEAM_API|Remedy/i));

    A.check('expScope', 'filters');
    ok('choosing "filters" shows them', !A.$('expFilters').hidden);
    A.check('expPlatform', 'steam');
    await A.submit(A.$('exportForm'));
    const steamOnly = await A.lastExport();
    ok('export by store: only the Steam games', steamOnly.groups.map(g => g.title).sort().join() === 'Hades,Hollow Knight,Portal 2' && steamOnly.manual_games.length === 0, steamOnly.groups.map(g => g.title).join());
    ok('...and the file name says it was filtered', /filtrado/.test(A.downloads.at(-1)));

    A.check('expPlatform', 'steam', false); A.check('expStatus', 'backlog');
    await A.submit(A.$('exportForm'));
    ok('export by status: only "Para jogar"', (await A.lastExport()).groups.map(g => g.title).sort().join() === 'Alan Wake,Fallout');

    A.check('expStatus', 'backlog', false); A.check('expPlatform', 'steam'); A.check('expStatus', 'dropped');
    const n = A.downloads.length;
    await A.submit(A.$('exportForm'));
    ok('filters that match nothing: a message, and no file', A.downloads.length === n && /nada para exportar/i.test(A.$('exportError').textContent), A.$('exportError').textContent);

    A.check('expScope', 'all'); A.$('expAliases').checked = false;
    await A.submit(A.$('exportForm'));
    ok('"include aliases" unchecked leaves them out', (await A.lastExport()).aliases.length === 0);
    A.$('exportClose').click();

    const search = A.$('search'); search.value = 'hades'; search.dispatchEvent(new A.w.Event('input')); 
    A.$('exportBtn').click();
    ok('"what is on screen" counts the searched games', A.$('expScreenCount').textContent === '1');
    A.check('expScope', 'screen');
    await A.submit(A.$('exportForm'));
    const onScreen = await A.lastExport();
    ok('export what is on screen: only Hades', onScreen.groups.map(g => g.title).join() === 'Hades' && /tela/.test(A.downloads.at(-1)), onScreen.groups.map(g => g.title).join());
    ok('no script errors on the exporting side', A.errors.length === 0, A.errors.join('; '));

    // ===================================================== import (on the new computer)
    const exportText = JSON.stringify(all);
    const B = await openPage(fresh.base);
    ok('the new computer starts with no organization', Object.keys(await organization(fresh.base)).length === 0);
    B.$('importBtn').click();
    ok('import dialog: apply disabled, modes and report hidden', B.$('importDlg').open && B.$('importApply').disabled && B.$('importModes').hidden && B.$('importReport').hidden);

    await B.chooseFile('this is not json');
    ok('a file that is not JSON: clear message, nothing offered', /não é um JSON válido/.test(B.$('importError').textContent) && B.$('importApply').disabled);
    await B.chooseFile(JSON.stringify({ hello: 'world' }));
    ok('JSON that is not an export: the server message is shown', B.$('importError').textContent === 'Este arquivo não é uma exportação do Game Library.' && B.$('importApply').disabled, B.$('importError').textContent);
    await B.chooseFile(JSON.stringify({ format: 'gamelibrary-export', version: 2 }));
    ok('a newer file version: tells to update the program', /Atualize o programa/.test(B.$('importError').textContent), B.$('importError').textContent);

    await B.chooseFile(exportText);
    const preview = B.$('importReport').textContent;
    ok('a valid file: preview shown, apply enabled, modes offered', !B.$('importApply').disabled && !B.$('importModes').hidden && /Prévia: nada foi gravado/.test(preview), preview.slice(0, 80));
    ok('preview counts the games found here and the manual game', /Jogos encontrados aqui: \d+/.test(preview) && /Jogos manuais: 1 novos/.test(preview), preview);
    ok('"Preencher lacunas" is the default mode', B.d.querySelector('input[name="impMode"]:checked').value === 'fill');
    ok('a preview writes nothing', Object.keys(await organization(fresh.base)).length === 0 && B.calls.filter(c => c[0] === '/api/import/apply').length === 0);

    const before = B.calls.filter(c => c[0] === '/api/import/preview').length;
    B.check('impMode', 'merge');
    await tick(300);
    const previews = B.calls.filter(c => c[0] === '/api/import/preview');
    ok('changing the mode asks for a new preview with that mode', previews.length === before + 1 && previews.at(-1)[1].mode === 'merge');
    B.check('impMode', 'fill'); await tick(300);

    await B.submit(B.$('importForm'));
    const done = B.$('importReport').textContent;
    ok('apply: "Importação concluída" with the backup location', /Importação concluída/.test(done) && /Backup feito em: .*before-import-/.test(done), done.slice(0, 120));
    ok('apply button turns into a disabled "Importado" (no double import)', B.$('importApply').textContent === 'Importado' && B.$('importApply').disabled);
    const backupDir = fs.readdirSync(path.join(fresh.dir, 'backups'));
    ok('a backup folder really exists on disk with the database', backupDir.length === 1 && fs.existsSync(path.join(fresh.dir, 'backups', backupDir[0], 'library.db')));

    const orgA = await organization(mac.base), orgB = await organization(fresh.base);
    ok('THE NEW COMPUTER NOW HAS EXACTLY THE ORGANIZATION OF THE MAC', JSON.stringify(orgA) === JSON.stringify(orgB), JSON.stringify(orgA).slice(0, 150) + ' !== ' + JSON.stringify(orgB).slice(0, 150));
    ok('...including the manual game on its own platform', (await (await fetch(fresh.base + '/api/games')).json()).some(g => g.title === 'Diablo IV' && g.entries[0].manual && g.platforms.join() === 'battlenet'));
    ok('the open page already shows it (a status pill on Portal 2)', [...B.d.querySelectorAll('.card')].find(c => c.dataset.key === 'portal2')?.textContent.includes('Jogado'));

    // importing the same file again
    B.$('importBtn').click();
    await B.chooseFile(exportText);
    ok('the same file again: preview says nothing is new', /Campos: <b>0<\/b>|Campos: 0 novos/.test(B.$('importReport').innerHTML.replace(/<[^>]+>/g, m => m.includes('b>') ? m : '')) || /Campos: 0 novos/.test(B.$('importReport').textContent), B.$('importReport').textContent.match(/Campos:[^.]*\./)?.[0]);

    // conflicts and overwrite
    await api(fresh.base, '/api/rating', { key: 'portal2', rating: 4 });
    B.$('importBtn').click();
    await B.chooseFile(exportText);
    const report = B.$('importReport');
    ok('fill mode: the conflict is listed and the local value is kept', /Conflitos/.test(report.textContent) && /Nota/.test(report.textContent) && /mantido o daqui/.test(report.textContent) && /2 ★/.test(report.textContent), report.textContent.slice(0, 300));
    B.check('impMode', 'overwrite'); await tick(300);
    B.setConfirm(false);
    const applies = () => B.calls.filter(c => c[0] === '/api/import/apply').length;
    const a0 = applies();
    await B.submit(B.$('importForm'));
    ok('overwrite asks for confirmation, and "cancel" imports nothing', B.confirms.length === 1 && applies() === a0 && (await organization(fresh.base)).portal2.r === 4);
    B.setConfirm(true);
    await B.submit(B.$('importForm'));
    ok('confirmed: the file wins for that field, and nothing was deleted', applies() === a0 + 1 && (await organization(fresh.base)).portal2.r === 9 && Object.keys(await organization(fresh.base)).length === Object.keys(orgA).length);
    ok('each import made its own backup', fs.readdirSync(path.join(fresh.dir, 'backups')).length === 2);
    ok('no script errors on the importing side', B.errors.length === 0, B.errors.join('; '));
  } finally {
    servers.forEach(s => s.kill());
  }
  console.log(failures ? `\n${failures} FAILED` : '\nall passed');
  process.exit(failures ? 1 : 0);
})().catch(e => { console.error(e); servers.forEach(s => s.kill()); process.exit(1); });
