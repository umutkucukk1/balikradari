"""Tek seferlik: her deniz merası için 12 km içindeki en büyük GloFAS akarsu hücresini seçip tools/akarsu.json'a yazar.
Günlük görev (deniz_verisi.py) yalnız bu hücreleri sorar; Open-Meteo'nun ücretsiz kotası 150 mera × 49 hücreyi kaldırmıyor.
Kullanım: python tools/akarsu_sec.py index.html tools/akarsu.json"""
import json, sys, time
import numpy as np, requests
sys.path.insert(0, __import__('os').path.dirname(__file__))
from deniz_verisi import spots_from, FLOOD

def main(src, dst):
    spots = spots_from(open(src, encoding='utf-8').read())
    snap = lambda v: round(round((v - 0.025) / 0.05) * 0.05 + 0.025, 3)        # GloFAS hücre merkezleri x.025 / x.075
    cand = {n: {(snap(la + dy * 0.05), snap(lo + dx * 0.05)) for dy in range(-3, 4) for dx in range(-3, 4)} for n, la, lo in spots}
    cells = sorted(set().union(*cand.values())); med = {}
    print(f'{len(spots)} mera, {len(cells)} benzersiz hücre')
    for k in range(0, len(cells), 50):
        b = cells[k:k + 50]
        for _ in range(5):
            r = requests.get(f'{FLOOD}?latitude={",".join(str(c[0]) for c in b)}&longitude={",".join(str(c[1]) for c in b)}'
                             '&daily=river_discharge&past_days=60&forecast_days=1', timeout=120)
            if r.status_code != 429: break
            time.sleep(65)
        r.raise_for_status(); d = r.json(); d = d if isinstance(d, list) else [d]
        for c, x in zip(b, d):
            q = [v for v in x.get('daily', {}).get('river_discharge', []) if v is not None]
            if len(q) >= 30: med[c] = (float(np.median(q)), x['latitude'], x['longitude'])
        print(f'{k + len(b)}/{len(cells)}', flush=True); time.sleep(6)
    out = {}
    for n, la, lo in spots:
        best = max(((m, cla, clo) for c in cand[n] if c in med for m, cla, clo in [med[c]]
                    if np.hypot((cla - la) * 111, (clo - lo) * 85) <= 12), default=None)
        if best and best[0] >= 3: out[n] = [round(best[1], 3), round(best[2], 3), round(best[0], 1)]
    json.dump(out, open(dst, 'w', encoding='utf-8'), ensure_ascii=False, indent=0)
    print(f'{len(out)} merada akarsu:', ', '.join(f'{n} {v[2]}' for n, v in sorted(out.items(), key=lambda x: -x[1][2])))

if __name__ == '__main__':
    main(sys.argv[1], sys.argv[2])
