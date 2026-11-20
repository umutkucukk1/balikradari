// Balık Radarı – Av Günlüğü kurulumu (Google Apps Script, bir kez çalıştırılır)
// Google Drive'da bir form ve yanıtların düştüğü bir tablo oluşturur; uygulamanın kullanacağı adresleri günlüğe yazar.
function kur() {
  const f = FormApp.create('Balık Radarı – Av Günlüğü');
  f.setDescription('Balık Radarı uygulamasından otomatik gönderilir. Elle doldurmanıza gerek yok.');
  f.setCollectEmail(false);
  f.setAllowResponseEdits(false);
  f.setLimitOneResponsePerUser(false);
  f.setShowLinkToRespondAgain(false);
  const names = ['tip', 'zaman', 'mera', 'konum', 'tur', 'sonuc', 'adet', 'yem', 'not', 'genel', 'turPuan', 'cihaz', 'surum'];
  const items = names.map(n => f.addTextItem().setTitle(n));
  const ss = SpreadsheetApp.create('Balık Radarı – Av Günlüğü (kayıtlar)');
  f.setDestination(FormApp.DestinationType.SPREADSHEET, ss.getId());
  const resp = f.createResponse();
  items.forEach((it, i) => resp.withItemResponse(it.createResponse('X' + i)));
  const pre = resp.toPrefilledUrl();
  const ids = {};
  names.forEach((n, i) => { const m = pre.match(new RegExp('entry\\.(\\d+)=X' + i + '(&|$)')); if (m) ids[n] = 'entry.' + m[1]; });
  const out = {url: f.getPublishedUrl().replace(/\/viewform.*$/, '/formResponse'), f: ids};
  Logger.log('LOG_FORM = ' + JSON.stringify(out));
  Logger.log('TABLO = ' + ss.getUrl());
  Logger.log('FORM DUZENLE = ' + f.getEditUrl());
}
