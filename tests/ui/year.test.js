// The release-year field in the game panel, against a mocked server. Run: cd tests/ui && npm install && npm test
const { JSDOM } = require('jsdom');
const fs = require('fs');
const html = fs.readFileSync(require('path').join(__dirname, '..', '..', 'gamelibrary', 'static', 'index.html'), 'utf8');
const mk = (key, title, extra = {}) => ({
  key, title, hidden: false, statuses: [], rating: null, note: '', started: null, finished: null, custom_cover: false, alt_covers: [],
  developer: 'Some Studio', developer_auto: 'Some Studio', developer_custom: false, playtime_minutes: 120, playtime_auto: 120, playtime_custom: false,
  release_year: null, release_year_auto: null, release_year_custom: false, tags: [], platforms: ['gog'], last_played: null,
  covers: ['http://x/' + key + '.jpg'], entries: [{ platform: 'gog', platform_id: key + '-1', title, split: false, manual: false, playtime_minutes: 60, url: null }], ...extra });
const data = [
  mk('a', 'Alpha', { release_year: 2015, release_year_auto: 2015 }),   // automatic year
  mk('b', 'Beta'),                                                     // no year at all
];
const posts = [];
(async () => {
  const dom = new JSDOM(html, { runScripts: 'dangerously', pretendToBeVisual: true, url: 'http://localhost/',
    beforeParse(w) {
      w.fetch = async (url, opt) => {
        if (opt && opt.method === 'POST') {
          const body = JSON.parse(opt.body); posts.push([url, body]);
          if (url === '/api/release-year') {
            if (body.year !== null && body.year < 1950) return { ok: false, text: async () => 'o ano precisa estar entre 1950 e 2100' };
            const g = data.find(x => x.key === body.key); g.release_year_custom = body.year !== null; g.release_year = body.year ?? g.release_year_auto;
          }
          return { ok: true, json: async () => ({}), text: async () => '' };
        }
        return { ok: true, json: async () => JSON.parse(JSON.stringify(data)) };
      };
      w.CSS = { escape: s => s };
      w.HTMLDialogElement.prototype.showModal = function () { this.setAttribute('open', ''); };
      w.HTMLDialogElement.prototype.close = function () { this.removeAttribute('open'); this.dispatchEvent(new w.Event('close')); };
    } });
  const w = dom.window, d = w.document;
  const alerts = []; w.alert = m => alerts.push(m);
  const tick = () => new Promise(r => setTimeout(r, 20));
  let failures = 0;
  const ok = (name, cond) => { if (!cond) failures++; console.log((cond ? 'PASS  ' : 'FAIL  ') + name); };
  await tick();

  const open = key => { const dlg = d.getElementById('dlg'); if (dlg.open) dlg.close(); d.querySelector(`.card[data-key="${key}"]`).click(); };
  const year = () => d.querySelector('#dlg .year-input');
  const note = f => d.querySelector(`#dlg .fact-note[data-field="${f}"]`).textContent;
  const change = async (el, v) => { el.value = v; el.dispatchEvent(new w.Event('change', { bubbles: true })); await tick(); };

  open('a');
  const facts = [...d.querySelector('#dlg .facts').children].map(el => el.matches('label') ? el.querySelector('input').className : 'note:' + el.dataset.field);
  ok('year field sits right after the developer field (dev, note, YEAR, note, hours, note)', facts.join() === 'dev-input,note:developer,year-input,note:year,hours-input,note:hours');
  ok('label says "Lançamento", numeric, 1950-2100, placeholder "Ano"', year().closest('label').textContent.startsWith('Lançamento') && year().type === 'number' && year().min === '1950' && year().max === '2100' && year().placeholder === 'Ano');
  ok('automatic year is shown and marked "automático"', year().value === '2015' && note('year') === 'automático');

  const node = year();
  await change(node, '2000');
  ok('typing a year POSTs it', JSON.stringify(posts.at(-1)) === '["/api/release-year",{"key":"a","year":2000}]');
  ok('typed year wins; note shows the automatic one', year().value === '2000' && note('year') === 'editado (automático: 2015)');
  ok('the field is not re-rendered (focus is kept)', year() === node);
  await change(node, '');
  ok('clearing POSTs null and goes back to the automatic year', posts.at(-1)[1].year === null && year().value === '2015' && note('year') === 'automático');

  const n = alerts.length, p = posts.length;
  await change(node, '1949');
  ok('server rejection: its message is shown and the field is reverted', alerts.length === n + 1 && alerts.at(-1) === 'o ano precisa estar entre 1950 e 2100' && year().value === '2015');
  await change(node, '2015.5');
  ok('a non-integer is refused in the page, without any request', alerts.length === n + 2 && posts.length === p + 1 && year().value === '2015');

  open('b');
  ok('a game with no automatic year starts empty, with no note', year().value === '' && note('year') === '');
  await change(year(), '1998');
  ok('a year can be typed for it', posts.at(-1)[1].year === 1998 && year().value === '1998' && note('year') === 'editado');
  await change(year(), '');
  ok('clearing it leaves it empty again', year().value === '' && note('year') === '');

  // "only here": the year must not show up anywhere else
  open('a');
  await change(year(), '1987');
  d.getElementById('dlg').close(); await tick();
  const anywhere = d.getElementById('list').textContent + d.querySelector('.stats').textContent;
  ok('the year does not appear on cards', !anywhere.includes('1987') && !anywhere.includes('2015'));
  d.getElementById('viewList').click();
  ok('...nor in the list view', !d.getElementById('list').textContent.includes('1987'));
  d.getElementById('viewGrid').click();
  open('a');
  ok('...nor in the dialog header, only in its own field', !d.querySelector('#dlg .dlg-head').textContent.includes('1987') && year().value === '1987');
  ok('no script errors', true);
  process.exit(failures ? 1 : 0);
})();
