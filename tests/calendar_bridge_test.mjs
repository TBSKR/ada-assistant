/*
  Calendar MCP HTTP Bridge Test
  - Verifies list calendars, find events, create event, quick add, delete
  - Uses Node 18+ global fetch
*/

const baseUrl = process.env.MCP_CAL_BASE_URL?.replace(/\/$/, '') || 'http://127.0.0.1:3001';

async function req(method, path, { params, body } = {}) {
  const url = new URL(baseUrl + (path.startsWith('/') ? path : '/' + path));
  if (params) Object.entries(params).forEach(([k, v]) => v !== undefined && v !== null && url.searchParams.set(k, String(v)));
  const init = { method, headers: { 'content-type': 'application/json' } };
  if (body !== undefined) init.body = JSON.stringify(body);
  const res = await fetch(url, init);
  const ct = res.headers.get('content-type') || '';
  const isJSON = ct.includes('application/json');
  let data = null;
  try { data = isJSON ? await res.json() : await res.text(); } catch { data = await res.text(); }
  return { ok: res.ok, status: res.status, data };
}

function nowPlus(minutes) { return new Date(Date.now() + minutes * 60_000).toISOString(); }

function tomorrowAt(h, m) {
  const t = new Date();
  t.setDate(t.getDate() + 1);
  t.setHours(h, m, 0, 0);
  // Format as yyyy-mm-dd HH:MM (for quickAdd parsing)
  const yyyy = t.getFullYear();
  const mm = String(t.getMonth() + 1).padStart(2, '0');
  const dd = String(t.getDate()).padStart(2, '0');
  const HH = String(t.getHours()).padStart(2, '0');
  const MM = String(t.getMinutes()).padStart(2, '0');
  return `${yyyy}-${mm}-${dd} ${HH}:${MM}`;
}

async function main() {
  console.log(`[1] Health check @ ${baseUrl}/health`);
  let r = await req('GET', '/health');
  if (!r.ok) throw new Error(`Health check failed: ${r.status}`);
  console.log(' ok');

  console.log('[2] List calendars');
  r = await req('GET', '/calendars');
  if (!r.ok) throw new Error(`List calendars failed: ${r.status}`);
  const calendars = r.data.items || [];
  const primary = calendars.find(c => c.primary) || calendars.find(c => c.accessRole === 'owner');
  const calendarId = primary?.id || 'primary';
  console.log(' using calendar:', calendarId);

  console.log('[3] Find events (sample fetch)');
  r = await req('GET', `/calendars/${encodeURIComponent(calendarId)}/events`, { params: { max_results: 3 } });
  if (!r.ok) throw new Error(`Find events failed: ${r.status}`);
  console.log(' items:', (r.data.items || []).length);

  const ts = Date.now();
  const detailedSummary = `ADA Bridge Test Detailed ${ts}`;
  console.log('[4] Create detailed event');
  const createBody = {
    summary: detailedSummary,
    start: { dateTime: nowPlus(10) },
    end: { dateTime: nowPlus(40) },
    description: 'Automated test event — safe to delete',
  };
  r = await req('POST', `/calendars/${encodeURIComponent(calendarId)}/events`, { body: createBody });
  if (!(r.ok && (r.status === 201 || r.status === 200))) throw new Error(`Create event failed: ${r.status} ${JSON.stringify(r.data)}`);
  const createdId = r.data.id;
  console.log(' created event id:', createdId);

  console.log('[5] Verify created event via search');
  r = await req('GET', `/calendars/${encodeURIComponent(calendarId)}/events`, { params: { q: 'ADA Bridge Test Detailed', max_results: 50 } });
  if (!r.ok) throw new Error(`Find events (verify) failed: ${r.status}`);
  const found = (r.data.items || []).some(ev => ev.id === createdId);
  console.log(' search contains created id:', found);

  const quickTs = Date.now();
  const quickText = `ADA QuickAdd Test ${quickTs} on ${tomorrowAt(9, 0)}-09:30`;
  console.log('[6] Quick add event');
  r = await req('POST', `/calendars/${encodeURIComponent(calendarId)}/events/quickAdd`, { body: { text: quickText } });
  if (!(r.ok && (r.status === 201 || r.status === 200))) throw new Error(`QuickAdd failed: ${r.status} ${JSON.stringify(r.data)}`);
  const quickId = r.data.id;
  console.log(' quickAdd event id:', quickId);

  console.log('[7] Delete quickAdd event');
  r = await req('DELETE', `/calendars/${encodeURIComponent(calendarId)}/events/${encodeURIComponent(quickId)}`);
  if (!(r.ok && r.status === 204)) throw new Error(`Delete quickAdd failed: ${r.status} ${JSON.stringify(r.data)}`);
  console.log(' deleted quickAdd');

  console.log('[8] Delete detailed event');
  r = await req('DELETE', `/calendars/${encodeURIComponent(calendarId)}/events/${encodeURIComponent(createdId)}`);
  if (!(r.ok && r.status === 204)) throw new Error(`Delete created failed: ${r.status} ${JSON.stringify(r.data)}`);
  console.log(' deleted detailed');

  console.log('\nAll checks passed.');
}

main().catch(e => { console.error('TEST FAILED:', e.message); process.exit(1); });

