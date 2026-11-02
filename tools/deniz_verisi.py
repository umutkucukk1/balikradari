"""Balık Radarı deniz verisi: Copernicus Marine Karadeniz/Marmara modelleri -> deniz.json

Herkese açık ARCO (zarr) deposundan okur, giriş gerektirmez. GitHub Actions'ta günde iki kez çalışır.
Her deniz merası (index.html'deki SPOTS) için 1 gün geçmiş + ~7 gün tahmin:
  * Marmara modeli (500 m, saatlik): akıntı hızı/yönü, yüzey suyu sıcaklığı, tuzluluk
  * Karadeniz modeli (2,5 km, saatlik): Marmara modelinin dışında kalan meralar için aynı değişkenler
  * Karadeniz biyokimya modeli (2,5 km, günlük): oksijen, ışık sönümlenmesi (bulanıklık, kd)
Kullanım: python tools/deniz_verisi.py index.html deniz.json
"""
import json, re, sys, time, threading, concurrent.futures as cf
import numpy as np, numcodecs, requests

STAC = 'https://stac.marine.copernicus.eu/metadata'
PHY, BGC = 'BLKSEA_ANALYSISFORECAST_PHY_007_001', 'BLKSEA_ANALYSISFORECAST_BGC_007_010'
# (ad, ürün, veri kümesi deseni, değişkenler, kıyıda en yakın deniz hücresi için arama yarıçapı [hücre])
SETS = [
    ('mrm_cur', PHY, r'cmems_mod_blk_phy-cur_anfc_mrm-500m_PT1H-i_(\d{6})', ('uo', 'vo'), 4),
    ('mrm_tem', PHY, r'cmems_mod_blk_phy-temp?_anfc_mrm-500m_PT1H-i_(\d{6})', ('thetao',), 4),
    ('mrm_sal', PHY, r'cmems_mod_blk_phy-sal_anfc_mrm-500m_PT1H-i_(\d{6})', ('so',), 4),
    ('blk_cur', PHY, r'cmems_mod_blk_phy-cur_anfc_2\.5km_PT1H-m_(\d{6})', ('uo', 'vo'), 2),
    ('blk_tem', PHY, r'cmems_mod_blk_phy-temp_anfc_2\.5km_PT1H-m_(\d{6})', ('thetao',), 2),
    ('blk_sal', PHY, r'cmems_mod_blk_phy-sal_anfc_2\.5km_PT1H-m_(\d{6})', ('so',), 2),
    ('bgc_o2', BGC, r'cmems_mod_blk_bgc-pp-o2_anfc_2\.5km_P1D-m_(\d{6})', ('o2',), 2),
    ('bgc_kd', BGC, r'cmems_mod_blk_bgc-optics_anfc_2\.5km_P1D-m_(\d{6})', ('kd',), 2),
]
HOURS = 24 * 8          # 24 saat geçmiş + 7 gün
DAYS = 9

S = requests.Session()
def get(url, ok404=False):
    for k in range(4):
        try:
            r = S.get(url, timeout=120)
            if ok404 and r.status_code in (403, 404): return None   # ARCO'da boş (tamamen kara) parça hiç yazılmamış
            r.raise_for_status(); return r.content
        except requests.RequestException:
            if k == 3: raise
            time.sleep(2 + 3 * k)

def dataset_href(product, pattern):
    """Ürünün en yeni, ARCO'su (geoChunked) yayınlanmış sürümü"""
    links = json.loads(get(f'{STAC}/{product}/product.stac.json'))['links']
    ids = sorted({m.group(0) for l in links if l.get('rel') == 'item' for m in [re.search(pattern, l['href'])] if m},
                 key=lambda s: s[-6:], reverse=True)
    for ds in ids:
        a = json.loads(get(f'{STAC}/{product}/{ds}/dataset.stac.json')).get('assets', {})
        if 'geoChunked' in a: return ds, a['geoChunked']['href']
    raise RuntimeError(f'ARCO yok: {pattern}')

UNIT = {'seconds': 1, 'minutes': 60, 'hours': 3600, 'days': 86400}
class Zarr:
    def __init__(self, base):
        self.base, self.meta, self.cache, self.lock = base, json.loads(get(base + '/.zmetadata'))['metadata'], {}, threading.Lock()
        self.lat, self.lon = self.coord('latitude'), self.coord('longitude')
        el = self.coord('elevation'); self.zi = int(np.abs(el).argmin())          # yüzeye en yakın seviye
        u = self.meta['time/.zattrs']['units']; m = re.match(r'(\w+) since (\d{4}-\d\d-\d\d)(?:[ T](\d\d:\d\d(?::\d\d)?))?', u)
        epoch0 = np.datetime64(m.group(2) + 'T' + (m.group(3) or '00:00:00'), 's').astype('int64')
        self.time = epoch0 + self.coord('time').astype('float64') * UNIT[m.group(1)]
    def zarray(self, name): return self.meta[f'{name}/.zarray']
    def coord(self, name):
        za = self.zarray(name); n = -(-za['shape'][0] // za['chunks'][0]); codec = numcodecs.get_codec(za['compressor'])
        return np.concatenate([np.frombuffer(codec.decode(get(f'{self.base}/{name}/{k}')), dtype=za['dtype']) for k in range(n)])[:za['shape'][0]]
    def chunk(self, var, key):
        ck = (var, key)
        with self.lock:
            if ck in self.cache: return self.cache[ck]
        za = self.zarray(var); raw = get(f'{self.base}/{var}/{".".join(map(str, key))}', ok404=True)
        if raw is None: a = None
        else:
            a = np.frombuffer(numcodecs.get_codec(za['compressor']).decode(raw), dtype=za['dtype']).reshape(za['chunks']).astype('float32')
            fill = za.get('fill_value'); mv = self.meta.get(f'{var}/.zattrs', {}).get('missing_value')
            for f in (fill, mv):
                if isinstance(f, (int, float)) and abs(f) > 1e10: a = np.where(np.abs(a) > 1e10, np.nan, a)
        with self.lock: self.cache[ck] = a
        return a
    def series(self, var, yi, xi, ti0, ti1):
        """[ti0, ti1) zaman indeksleri için (yüzey, yi, xi) serisi"""
        za = self.zarray(var); ch = za['chunks']; nd = len(za['shape']); out = np.full(ti1 - ti0, np.nan, 'float32')
        for tc in range(ti0 // ch[0], (ti1 - 1) // ch[0] + 1):
            key = (tc, self.zi // ch[1], yi // ch[-2], xi // ch[-1]) if nd == 4 else (tc, yi // ch[-2], xi // ch[-1])
            a = self.chunk(var, key)
            if a is None: continue
            lo, hi = max(ti0, tc * ch[0]), min(ti1, (tc + 1) * ch[0])
            sub = a[lo - tc * ch[0]:hi - tc * ch[0]]
            sub = sub[:, self.zi % ch[1], yi % ch[-2], xi % ch[-1]] if nd == 4 else sub[:, yi % ch[-2], xi % ch[-1]]
            out[lo - ti0:hi - ti0] = sub
        return out

MUR = 'https://upwell.pfeg.noaa.gov/erddap/griddap/jplMURSST41'
def load_mur():
    """NASA MUR uydu su sıcaklığı (1 km, günlük, ~3-4 gün gecikmeli): modelin seviye sapmasını düzeltmek için.
    29 Eki 2026 kıyası: Marmara modeli İzmit Körfezi'ni uydudan ~2°C soğuk, Boğaz/Şile'yi ±0,5°C doğru gösteriyor."""
    das = get(MUR + '.das').decode('latin-1'); day = re.search(r'time_coverage_end "([^"]+)"', das).group(1)
    csv = get(f'{MUR}.csv?analysed_sst%5B({day})%5D%5B(39.4):(42.3)%5D%5B(25.0):(34.0)%5D').decode().splitlines()[2:]
    rows = np.array([[float(x) if x != 'NaN' else np.nan for x in r.split(',')[1:4]] for r in csv if r])
    lats, lons = np.unique(rows[:, 0]), np.unique(rows[:, 1])
    grid = np.full((len(lats), len(lons)), np.nan, 'float32')
    grid[np.searchsorted(lats, rows[:, 0]), np.searchsorted(lons, rows[:, 1])] = rows[:, 2]
    t = np.datetime64(day.replace('Z', ''), 's').astype('int64'); d0 = t // 86400 * 86400
    def at(la, lo, R=4):
        y0, x0 = int(np.abs(lats - la).argmin()), int(np.abs(lons - lo).argmin())
        for _, dy, dx in sorted((dy * dy + dx * dx, dy, dx) for dy in range(-R, R + 1) for dx in range(-R, R + 1)):
            y, x = y0 + dy, x0 + dx
            if 0 <= y < len(lats) and 0 <= x < len(lons) and np.isfinite(grid[y, x]): return float(grid[y, x])
        return None
    print(f'MUR uydu: {day} · {np.isfinite(grid).sum()} deniz hücresi', flush=True)
    return at, d0

GFW = 'https://gateway.api.globalfishingwatch.org/v3/4wings/report'
def load_gfw(token):
    """Global Fishing Watch: gırgır (purse seine) teknelerinin AIS'ten çıkarılan avcılık saatleri, 0,1° ızgara.
    Veri ~4 gün gecikmeli; iki hafta ayrı çekilir (son hafta / önceki hafta) ki sürünün yer değiştirmesi görülsün.
    Tekne kimliği alınmaz, yalnız hücre toplamı."""
    import io, zipfile, datetime as dt
    poly = {'type': 'FeatureCollection', 'features': [{'type': 'Feature', 'properties': {}, 'geometry': {'type': 'Polygon',
            'coordinates': [[[25.9, 39.9], [34.0, 39.9], [34.0, 42.3], [25.9, 42.3], [25.9, 39.9]]]}}]}
    end = dt.date.today() - dt.timedelta(days=4)
    weeks = [(end - dt.timedelta(days=7), end), (end - dt.timedelta(days=14), end - dt.timedelta(days=7))]
    out = []
    for a, b in weeks:
        params = {'spatial-resolution': 'LOW', 'temporal-resolution': 'ENTIRE', 'group-by': 'GEARTYPE', 'format': 'JSON',
                  'datasets[0]': 'public-global-fishing-effort:latest', 'date-range': f'{a},{b}', 'spatial-aggregation': 'false',
                  'filters[0]': "geartype in ('purse_seines','other_purse_seines','tuna_purse_seines')"}
        r = S.post(GFW, params=params, json={'geojson': json.dumps(poly)}, timeout=180,
                   headers={'Authorization': f'Bearer {token}', 'Content-Language': 'en-EN'})
        r.raise_for_status()
        if r.content[:2] == b'PK':
            z = zipfile.ZipFile(io.BytesIO(r.content)); data = json.loads(z.read(next(n for n in z.namelist() if n.endswith('.json'))))
        else: data = r.json()
        rows = []
        def walk(x):
            if isinstance(x, dict):
                if 'lat' in x and 'lon' in x and 'hours' in x: rows.append(x)
                else: [walk(v) for v in x.values()]
            elif isinstance(x, list): [walk(v) for v in x]
        walk(data)
        cells = {}
        for e in rows:
            k = (round(float(e['lat']), 2), round(float(e['lon']), 2)); cells[k] = cells.get(k, 0) + float(e['hours'] or 0)
        out.append({'from': str(a), 'to': str(b), 'cells': [[la, lo, round(h, 1)] for (la, lo), h in sorted(cells.items()) if h >= 0.5]})
        tot = sum(h for _, _, h in out[-1]['cells']); top = sorted(out[-1]['cells'], key=lambda c: -c[2])[:5]
        print(f'GFW {a}..{b}: {len(out[-1]["cells"])} hücre, {tot:.0f} avcılık saati, en yoğun: {top}', flush=True)
    return out

def spots_from(html):
    pat = re.compile(r"\{n:'((?:[^'\\]|\\.)*)'[^{}]*?w:'deniz'[^{}]*?lat:(-?[\d.]+), lng:(-?[\d.]+)")
    return [(m.group(1).replace("\\'", "'"), float(m.group(2)), float(m.group(3))) for m in pat.finditer(html)]

def main(src, dst):
    t_start = time.time()
    spots = spots_from(open(src, encoding='utf-8').read())
    now = int(time.time()) // 3600 * 3600; T0 = now - 24 * 3600
    hours = T0 + 3600 * np.arange(HOURS); day0 = T0 // 86400 * 86400; days = day0 + 86400 * np.arange(DAYS)
    res = {n: {} for n, _, _ in spots}; used = {}
    try: mur_at, mur_d0 = load_mur()
    except Exception as ex: mur_at = None; print('MUR alınamadı:', ex)
    def nearest_idx(z, targets, tol):
        idx = np.clip(np.searchsorted(z.time, targets), 1, len(z.time) - 1)
        idx = np.where(np.abs(z.time[idx - 1] - targets) < np.abs(z.time[idx] - targets), idx - 1, idx)
        return idx, np.abs(z.time[idx] - targets) <= tol
    for name, product, pattern, vars_, R in SETS:
        ds, href = dataset_href(product, pattern); z = Zarr(href); used[name] = ds
        daily = '_P1D' in ds; targets = days + 43200 if daily else hours   # günlük ortalamalar gün ortasına denk gelir
        # hedef saat/gün -> model zaman indeksi (en yakın, ±45 dk / ±13 sa içinde)
        idx, good = nearest_idx(z, targets, 13 * 3600 if daily else 2700)
        if not good.any(): print(name, 'zaman aralığı yok'); continue
        ti0, ti1 = int(idx[good].min()), int(idx[good].max()) + 1
        # Sıcaklıkta uydu düzeltmesi için uydunun ölçüm gününün model saatleri de okunur
        bidx = bgood = None
        if name.endswith('_tem') and mur_at:
            bidx, bgood = nearest_idx(z, mur_d0 + 3600 * np.arange(24), 2700)
            if bgood.any(): ti0 = min(ti0, int(bidx[bgood].min()))
            else: bidx = None
        inside = lambda la, lo: z.lat[0] - .01 <= la <= z.lat[-1] + .01 and z.lon[0] - .01 <= lo <= z.lon[-1] + .01
        def one(sp):
            n, la, lo = sp
            if not inside(la, lo): return n, None
            y0, x0 = int(np.abs(z.lat - la).argmin()), int(np.abs(z.lon - lo).argmin())
            cand = sorted(((dy * dy + dx * dx, dy, dx) for dy in range(-R, R + 1) for dx in range(-R, R + 1)))
            for _, dy, dx in cand:
                y, x = y0 + dy, x0 + dx
                if not (0 <= y < len(z.lat) and 0 <= x < len(z.lon)): continue
                vals = [z.series(v, y, x, ti0, ti1) for v in vars_]
                if np.isfinite(vals[0]).sum() >= 0.5 * (ti1 - ti0):
                    out = [np.where(good, v[np.clip(idx - ti0, 0, ti1 - ti0 - 1)], np.nan) for v in vals]
                    if bidx is not None:
                        m = mur_at(la, lo); md = np.nanmean(np.where(bgood, vals[0][np.clip(bidx - ti0, 0, ti1 - ti0 - 1)], np.nan))
                        bias = float(np.clip(m - md, -3, 3)) if m is not None and np.isfinite(md) else None
                        if bias is not None: out[0] = out[0] + bias
                        out.append(bias)
                    return n, out
            return n, None
        with cf.ThreadPoolExecutor(8) as ex:
            got = list(ex.map(one, spots))
        hit = 0
        for n, vals in got:
            if vals is None: continue
            hit += 1; r = res[n]
            if name.endswith('_cur'):
                u, v = vals; sp = np.hypot(u, v); dr = (np.degrees(np.arctan2(u, v)) + 360) % 360
                r.setdefault('src', name[:3]);
                if 'c' not in r: r['c'], r['cd'] = sp * 100, dr          # cm/s, akıntının gittiği yön
            elif name.endswith('_tem') and 't' not in r:
                r['t'] = vals[0] * 10
                if len(vals) > 1 and vals[1] is not None: r['tb'] = round(vals[1], 1)   # uydu düzeltmesi (°C)
            elif name.endswith('_sal') and 'sa' not in r: r['sa'] = vals[0] * 10
            elif name == 'bgc_o2': r['o2'] = vals[0]
            elif name == 'bgc_kd': r['kd'] = vals[0] * 1000
        print(f'{name}: {ds} · {hit}/{len(spots)} mera · {len(z.cache)} parça · {time.time() - t_start:.0f} sn', flush=True)
        z.cache.clear()
    ints = lambda a: [None if not np.isfinite(x) else int(round(float(x))) for x in a]
    out = {'gen': time.strftime('%Y-%m-%dT%H:%MZ', time.gmtime()), 't0': int(T0), 'd0': int(day0), 'ds': used,
           's': {n: {k: (v if k in ('src', 'tb') else ints(v)) for k, v in r.items()} for n, r in res.items() if r}}
    tok = __import__('os').environ.get('GFW_TOKEN')
    if tok:
        try: out['gfw'] = load_gfw(tok)
        except Exception as ex: print('GFW alınamadı:', str(ex)[:300])
    with open(dst, 'w', encoding='utf-8') as f: json.dump(out, f, ensure_ascii=False, separators=(',', ':'))
    print(f'yazıldı: {dst} · {len(out["s"])} mera · {time.time() - t_start:.0f} sn')

if __name__ == '__main__':
    main(sys.argv[1] if len(sys.argv) > 1 else 'index.html', sys.argv[2] if len(sys.argv) > 2 else 'deniz.json')
