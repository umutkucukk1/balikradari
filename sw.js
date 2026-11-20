// Balık Radarı — çevrimdışı çalışma (service worker)
// Uygulama dosyaları: önbellekten hızlı açılır, arkada güncellenir.
// Hava/deniz verisi: önce internet; bağlantı yoksa son kaydedilen yanıt.
// Harita parçaları: görülenler saklanır (en fazla ~600), internetsizken de görünür.
const V = 'br-v2', SHELL = 'br-shell-' + V, DATA = 'br-data-' + V, TILES = 'br-tiles-' + V;
const SHELL_FILES = ['./', 'index.html', 'manifest.webmanifest', 'icon-192.png', 'icon-512.png',
  'https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4/leaflet.min.css',
  'https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4/leaflet.min.js',
  'https://cdnjs.cloudflare.com/ajax/libs/suncalc/1.9.0/suncalc.min.js'];

self.addEventListener('install', ev => {
  ev.waitUntil(caches.open(SHELL).then(c => Promise.allSettled(SHELL_FILES.map(u => c.add(u)))).then(() => self.skipWaiting()));
});
self.addEventListener('activate', ev => {
  ev.waitUntil(caches.keys().then(ks => Promise.all(ks.filter(k => !k.endsWith(V)).map(k => caches.delete(k)))).then(() => self.clients.claim()));
});

const isTile = u => /tile|arcgisonline|basemaps|\/\d+\/\d+\/\d+(\.png|\.jpg|@2x)?(\?|$)/.test(u.href) && !/api|json/.test(u.pathname);
async function trim(name, max){
  const c = await caches.open(name), ks = await c.keys();
  for (let i = 0; i < ks.length - max; i++) await c.delete(ks[i]);
}

self.addEventListener('fetch', ev => {
  const req = ev.request;
  if (req.method !== 'GET') return;                       // av günlüğü gönderimi (POST) dokunulmadan geçer
  const u = new URL(req.url);
  // Sayfanın kendisi: önce internet (güncelleme hemen görünsün), yoksa son kayıtlı sürüm
  if (req.mode === 'navigate' || (u.origin === location.origin && /(\/|index\.html)$/.test(u.pathname))) {
    ev.respondWith(fetch(req).then(r => {
      if (r.ok) { const cl = r.clone(); caches.open(SHELL).then(c => c.put('index.html', cl)); }
      return r;
    }).catch(() => caches.open(SHELL).then(c => c.match('index.html')).then(hit => hit || Response.error())));
    return;
  }
  const shell = (u.origin === location.origin && !/\.json$/.test(u.pathname)) || u.hostname === 'cdnjs.cloudflare.com';
  if (shell) {
    // Önbellekten aç, arkada yenile
    ev.respondWith(caches.open(SHELL).then(async c => {
      const hit = await c.match(req, {ignoreSearch: true});
      const net = fetch(req).then(r => { if (r.ok) c.put(req, r.clone()); return r; }).catch(() => hit);
      return hit || net;
    }));
    return;
  }
  if (isTile(u)) {
    ev.respondWith(caches.open(TILES).then(async c => {
      const hit = await c.match(req);
      if (hit) return hit;
      const r = await fetch(req);
      if (r.ok || r.type === 'opaque') { c.put(req, r.clone()); trim(TILES, 600); }
      return r;
    }));
    return;
  }
  // Veri: önce ağ, olmazsa son kayıt
  ev.respondWith(fetch(req).then(r => {
    if (r.ok) { const cl = r.clone(); caches.open(DATA).then(c => { c.put(req, cl); trim(DATA, 300); }); }
    return r;
  }).catch(() => caches.open(DATA).then(c => c.match(req)).then(hit => hit || Response.error())));
});

self.addEventListener('notificationclick', ev => {
  ev.notification.close();
  ev.waitUntil(self.clients.matchAll({type: 'window'}).then(ws => ws.length ? ws[0].focus() : self.clients.openWindow('./')));
});
