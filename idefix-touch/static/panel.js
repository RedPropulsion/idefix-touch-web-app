'use strict';
(() => {
const $ = id => document.getElementById(id);
const token = document.querySelector('meta[name="idefix-token"]').content;
const modes = [...document.querySelectorAll('[data-mode]')];
const NS = 'http://www.w3.org/2000/svg';
let state = null, points = [], generation = -1, instance = null, cursor = 0, pending = false, active = 'led';
let actionPending = false, pollPending = false, unavailable = false;
const number = new Intl.NumberFormat('it-IT');
function text(id, value) { $(id).textContent = value; }
function tab(name) {
  active = name;
  for (const key of ['led', 'signal']) {
    $('tab-' + key).setAttribute('aria-selected', String(key === name));
    $(key + '-panel').hidden = key !== name;
  }
  text('page-title', name === 'led' ? 'Controllo LED' : 'Come cambia il segnale?');
  if (name === 'signal') requestAnimationFrame(draw);
}
for (const key of ['led', 'signal']) $('tab-' + key).addEventListener('click', () => tab(key));
document.querySelector('nav').addEventListener('keydown', e => {
  if (e.key === 'ArrowLeft' || e.key === 'ArrowRight') {
    e.preventDefault(); tab(active === 'led' ? 'signal' : 'led'); $('tab-' + active).focus();
  }
});
async function api(path, body, timeout = 6000) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeout);
  try {
    const response = await fetch(path, {signal: controller.signal, cache: 'no-store',
      ...(body === undefined ? {} : {method: 'POST', headers: {'Content-Type':'application/json', 'X-Idefix-Token':token}, body:JSON.stringify(body)})});
    const data = await response.json();
    if (!response.ok) {
      const error = new Error(data.message || 'Operazione non riuscita');
      error.detail = data.detail;
      throw error;
    }
    return data;
  } finally { clearTimeout(timer); }
}
function selectLed(mode) { modes.forEach(b => b.setAttribute('aria-pressed', String(b.dataset.mode === mode))); }
function ledFeedback(kind, message, explanation) {
  $('feedback').dataset.state = kind; text('led-message', message); text('led-explanation', explanation);
}
modes.forEach(button => button.addEventListener('click', async () => {
  if (pending) return;
  pending = true; modes.forEach(b => b.disabled = true); selectLed(null); $('details').hidden = true;
  ledFeedback('pending', 'Invio in corso…', 'Attendo la conferma da ObelICS.');
  try {
    const result = await api('/api/led', {mode:button.dataset.mode}, 40000);
    selectLed(result.mode); ledFeedback('success', result.message, state?.demo ? 'Anteprima simulata.' : 'Comando accettato da ObelICS.');
    if (result.detail) { text('log', result.detail); $('details').hidden = false; }
  } catch(error) {
    ledFeedback('error', error.message, 'Lo stato fisico dei LED non è confermato.');
    if (error.detail) { text('log', error.detail); $('details').hidden = false; }
  }
  finally { pending = false; modes.forEach(b => b.disabled = false); poll(); }
}));
$('experiment-button').addEventListener('click', async () => {
  if (actionPending || !state || unavailable) return;
  actionPending = true; $('experiment-button').disabled = true; $('signal-error').hidden = true;
  try {
    await api('/api/lora/experiment', {action:state.recording ? 'stop' : 'start'});
    await poll();
  } catch(error) { text('signal-error', error.message); $('signal-error').hidden = false; }
  finally { actionPending = false; $('experiment-button').disabled = unavailable; }
});
$('scenario').addEventListener('change', async () => {
  try { await api('/api/demo/scenario', {scenario:$('scenario').value}); }
  catch(error) { text('signal-error', error.message); $('signal-error').hidden = false; }
});
function duration(seconds) {
  const n = Math.max(0, Math.floor(seconds));
  return n < 60 ? n + ' s' : Math.floor(n/60) + ' min ' + String(n%60).padStart(2,'0') + ' s';
}
function render() {
  if (!state) return;
  const s = state.latest;
  $('demo-banner').hidden = !state.demo; $('demo-controls').hidden = !state.demo;
  if (state.demo && document.activeElement !== $('scenario')) $('scenario').value = state.scenario;
  if (!pending) {
    selectLed(state.led_mode);
    modes.forEach(b => b.disabled = !!state.busy);
  }
  if (state.led_ack_at) text('last-ack', (state.demo ? 'Ultimo ACK simulato · ' : 'Ultimo ACK · ') + new Date(state.led_ack_at*1000).toLocaleTimeString('it-IT',{timeZone:'Europe/Rome',hour:'2-digit',minute:'2-digit',second:'2-digit'}));
  let status = 'In attesa di telemetria';
  if (unavailable) status = 'Idefix non raggiungibile';
  else if (state.telemetry_available && state.stale) status = 'Telemetria interrotta';
  else if (s) status = ({pong:'PONG ricevuto',timeout:'WL55 non risponde',unexpected:'Risposta inattesa',error:'Errore radio',unavailable:'Radio non disponibile'})[s.result];
  text('link-status', status);
  $('link-status').dataset.bad = String(unavailable || state.stale || (s && s.result !== 'pong'));
  text('rssi', s?.rssi == null ? '—' : s.rssi);
  text('snr', s?.snr == null ? '—' : (s.snr > 0 ? '+' : '') + s.snr);
  text('replies', s ? number.format(s.pong) + ' / ' + number.format(s.tx) : '— / —');
  text('success-rate', s?.tx ? Math.round(100*s.pong/s.tx) + '%' : '—');
  text('last-signal', s?.signal_age_s == null ? 'Nessun PONG ricevuto' :
    (unavailable || state.stale ? 'Ultimi valori · ' : 'Ultimo PONG · ') + duration(s.signal_age_s) + ' fa');
  text('extra-stats', s ? 'Timeout ' + number.format(s.timeouts) + ' · Errori ' + number.format(s.errors) + ' · RTT ' + (s.rtt_ms == null ? '—' : s.rtt_ms+' ms') : 'Timeout — · RTT —');
  text('experiment-state', state.recording ? 'Raccolta in corso · ' + duration(state.elapsed_s) : (state.sample_count ? 'Prova fermata · ' + state.sample_count + ' campioni' : 'Pronto per una prova'));
  text('experiment-hint', state.recording ? 'Un campione ogni ≈5 s · grafico degli ultimi 5 min' : state.sample_count ? 'Una nuova prova sostituisce il grafico. La radio resta attiva.' : 'Avvia la raccolta, poi sposta la WL55.');
  text('experiment-button', state.recording ? 'Ferma prova' : state.sample_count ? 'Nuova prova' : 'Avvia prova');
  $('experiment-button').classList.toggle('stop', state.recording);
  $('experiment-button').disabled = actionPending || unavailable;
  if (active === 'signal') draw();
}
function svgNode(tag, attrs, content) {
  const n = document.createElementNS(NS, tag);
  for (const [key,value] of Object.entries(attrs || {})) n.setAttribute(key, value);
  if (content !== undefined) n.textContent = content;
  return n;
}
function chart(id, key) {
  const svg = $(id), width = svg.clientWidth, height = svg.clientHeight;
  if (!width) return;
  svg.setAttribute('viewBox', `0 0 ${width} ${height}`); svg.replaceChildren();
  const left=43, right=12, top=13, bottom=35;
  const w=width-left-right, h=height-top-bottom;
  const elapsed = state?.elapsed_s || 0, xMax = Math.max(30, elapsed), xMin = Math.max(0,xMax-300);
  const visible=points.filter(p=>p.t>=xMin && p.t<=xMax);
  const values=visible.filter(p=>p[key]!=null).map(p=>p[key]);
  const base=key==='rssi'?[-120,-50]:[-10,15];
  let low=values.length?Math.min(...values):base[0], high=values.length?Math.max(...values):base[1];
  const pad=Math.max(key==='rssi'?6:3,(high-low)*.15);
  low=Math.floor((low-pad)/5)*5; high=Math.ceil((high+pad)/5)*5;
  const x=t=>left+(t-xMin)/(xMax-xMin)*w, y=v=>top+(high-v)/(high-low)*h;
  svg.append(svgNode('title',{}, `${key.toUpperCase()} · ${values.length} campioni validi; interruzioni per risposte mancanti`));
  for (let i=0;i<3;i++) {
    const value=low+(high-low)*i/2, yy=y(value);
    svg.append(svgNode('line',{x1:left,y1:yy,x2:width-right,y2:yy,class:'grid'}));
    svg.append(svgNode('text',{x:left-7,y:yy+4,'text-anchor':'end'},String(Math.round(value))));
  }
  svg.append(svgNode('path',{d:`M${left},${top}V${height-bottom}H${width-right}`,class:'axis'}));
  for (let i=0;i<3;i++) {
    const t=xMin+(xMax-xMin)*i/2;
    svg.append(svgNode('text',{x:x(t),y:height-bottom+16,'text-anchor':i===0?'start':i===2?'end':'middle'},String(Math.round(t))));
  }
  svg.append(svgNode('text',{x:left+w/2,y:height-2,'text-anchor':'middle'},'Tempo dalla partenza · s'));
  if (!visible.length) {
    svg.append(svgNode('text',{x:left+w/2,y:top+h/2,'text-anchor':'middle'},state?.recording?'Attendo il prossimo campione…':'Avvia una prova'));
    return;
  }
  let path='', previous=null;
  for (const point of visible) {
    if (point[key] == null) {
      previous=null;
      const xx=x(point.t), yy=height-bottom-4;
      const marker=svgNode('path',{d:`M${xx-3},${yy-3}l6,6m-6,0l6,-6`,class:'missing'});
      marker.append(svgNode('title',{},'Nessuna misura valida · '+duration(point.t)));
      svg.append(marker);
      continue;
    }
    path+=(previous && !point.gap?'L':'M')+x(point.t).toFixed(2)+','+y(point[key]).toFixed(2)+' ';
    previous=point;
  }
  svg.append(svgNode('path',{d:path,class:'trace'}));
  // Points remain visible even after a missing response breaks the trace.
  for (const p of visible) if(p[key]!=null) {
    const circle=svgNode('circle',{cx:x(p.t),cy:y(p[key]),r:2.5,class:'point'});
    circle.append(svgNode('title',{},`${duration(p.t)} · ${p[key]} ${key==='rssi'?'dBm':'dB'}`));
    svg.append(circle);
  }
}
function draw() { chart('rssi-chart','rssi'); chart('snr-chart','snr'); }
async function poll() {
  if (pollPending) return;
  pollPending=true;
  try {
    let fresh=await api('/api/lora/state?after='+cursor, undefined, 3500);
    if (fresh.instance!==instance || fresh.generation!==generation) {
      if(cursor) fresh=await api('/api/lora/state', undefined, 3500);
      points=[]; cursor=0; generation=fresh.generation; instance=fresh.instance;
    }
    for (const p of fresh.points) if(p.id>cursor) { points.push(p); cursor=p.id; }
    if (points.length>6000) points=points.slice(-6000);
    state=fresh; unavailable=false; render();
  } catch(error) {
    unavailable=true;
    text('link-status','Idefix non raggiungibile'); $('link-status').dataset.bad='true';
    $('experiment-button').disabled=true;
    text('last-signal','Aggiornamento interrotto · ultimi valori ricevuti');
  } finally { pollPending=false; }
}
new ResizeObserver(()=>{if(active==='signal')draw();}).observe($('signal-panel'));
api('/api/info').then(info=>text('target', info.demo?'ObelICS · simulato':'ObelICS · '+info.target)).catch(()=>{});
if(location.hash==='#signal')tab('signal');
poll(); setInterval(poll,1000);
})();
