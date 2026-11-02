"""Tek seferlik: her deniz merasının ~150 m çevresindeki aydınlatma (OpenStreetMap) -> tools/isik.json
Sokak lambası düğümleri (highway=street_lamp), aydınlatmalı yollar/iskeleler (lit=yes|24/7|automatic) ve
aydınlatmasız işaretlenenler (lit=no) sayılır. OSM API'ye saniyede bir istek (kullanım kuralı).
Kullanım: python tools/isik_tara.py index.html tools/isik.json"""
import json, math, sys, time
import xml.etree.ElementTree as ET
import requests
sys.path.insert(0, __import__('os').path.dirname(__file__))
from deniz_verisi import spots_from

UA = {'User-Agent': 'BalikRadari/1.0 (umutkucukk1.github.io/balikradari; one-off lighting scan)'}

def main(src, dst):
    out = {}
    for n, la, lo in spots_from(open(src, encoding='utf-8').read()):
        dla = 0.00135; dlo = dla / math.cos(math.radians(la))
        bbox = f'{lo - dlo:.6f},{la - dla:.6f},{lo + dlo:.6f},{la + dla:.6f}'
        for k in range(3):
            try:
                r = requests.get(f'https://api.openstreetmap.org/api/0.6/map?bbox={bbox}', headers=UA, timeout=60)
                r.raise_for_status(); break
            except requests.RequestException as ex:
                if k == 2: print('HATA', n, ex); r = None
                time.sleep(3)
        if r is None: continue
        root = ET.fromstring(r.content); lamps = lit = dark = 0; litpier = False
        for el in root:
            tags = {t.get('k'): t.get('v') for t in el.findall('tag')}
            if el.tag == 'node' and tags.get('highway') == 'street_lamp': lamps += 1
            if tags.get('lit') in ('yes', '24/7', 'automatic', 'sunset-sunrise'):
                lit += 1
                if tags.get('man_made') in ('pier', 'quay', 'breakwater') or tags.get('highway') == 'footway' and 'pier' in str(tags): litpier = True
            elif tags.get('lit') == 'no': dark += 1
        out[n] = {'lamba': lamps, 'lit': lit, 'karanlik': dark, 'iskele': litpier}
        time.sleep(1.1)
    json.dump(out, open(dst, 'w', encoding='utf-8'), ensure_ascii=False, indent=0)
    known = {n: v for n, v in out.items() if v['lamba'] or v['lit'] or v['karanlik']}
    print(f'{len(out)} mera tarandı; aydınlatma bilgisi olan: {len(known)}')
    for n, v in sorted(known.items(), key=lambda x: -(x[1]['lamba'] + x[1]['lit'])): print(f"  {n}: lamba {v['lamba']}, lit=yes {v['lit']}, lit=no {v['karanlik']}{' · ışıklı iskele' if v['iskele'] else ''}")

if __name__ == '__main__':
    main(sys.argv[1], sys.argv[2])
