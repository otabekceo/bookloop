type Cluster = {
  neighborhood: string;
  lat: number;
  lng: number;
  people_count: number;
  books_count: number;
};

/**
 * Builds a self-contained Leaflet map HTML with sage count markers.
 *
 * `rtl` mirrors the map chrome (zoom controls, attribution) and the marker
 * labels for right-to-left languages such as Arabic, so the embedded WebView
 * matches the rest of the app's layout direction.
 */
export function buildLeafletHTML(clusters: Cluster[], rtl = false): string {
  const data = JSON.stringify(clusters);
  const dir = rtl ? "rtl" : "ltr";
  return `<!DOCTYPE html>
<html lang="${rtl ? "ar" : "en"}" dir="${dir}">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=4.0, user-scalable=yes" />
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css" />
<style>
  html,body,#map{height:100%;margin:0;padding:0;background:#F7F4EE;}
  .cluster-marker{
    background:#879B7A;color:#fff;border:3px solid #fff;border-radius:50%;
    display:flex;align-items:center;justify-content:center;font-weight:700;
    font-family:-apple-system,Segoe UI,Roboto,sans-serif;
    box-shadow:0 3px 8px rgba(23,33,31,0.25);
  }
  .cluster-label{
    background:#fff;color:#17211F;border-radius:8px;padding:2px 7px;margin-top:4px;
    font-size:11px;font-weight:600;font-family:-apple-system,Segoe UI,Roboto,sans-serif;
    white-space:nowrap;text-align:center;box-shadow:0 1px 4px rgba(0,0,0,0.12);
  }
  .marker-wrap{display:flex;flex-direction:column;align-items:center;}
  .leaflet-control-attribution{font-size:9px;}
  /* Mirror Leaflet's directional chrome under RTL. */
  html[dir="rtl"] .leaflet-left{left:auto;right:0;}
  html[dir="rtl"] .leaflet-right{right:auto;left:0;}
  html[dir="rtl"] .leaflet-control-zoom{float:right;}
  html[dir="rtl"] .leaflet-control-attribution{float:left;}
</style>
</head>
<body>
<div id="map"></div>
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<script>
  var clusters = ${data};
  var map = L.map('map', { zoomControl: true, attributionControl: true }).setView([38.19, 15.55], 12);
  L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
    maxZoom: 19,
    attribution: '&copy; OpenStreetMap'
  }).addTo(map);

  function send(nb){
    var payload = JSON.stringify({ neighborhood: nb });
    if (window.ReactNativeWebView) { window.ReactNativeWebView.postMessage(payload); }
    else if (window.parent) { window.parent.postMessage(payload, '*'); }
  }

  var bounds = [];
  clusters.forEach(function(c){
    var size = 42 + Math.min(c.people_count, 8) * 4;
    var html = '<div class="marker-wrap"><div class="cluster-marker" style="width:'+size+'px;height:'+size+'px;font-size:'+(size*0.34)+'px;">'+c.people_count+'</div><div class="cluster-label">'+c.neighborhood+'</div></div>';
    var icon = L.divIcon({ html: html, className:'', iconSize:[size, size+22], iconAnchor:[size/2, size/2] });
    var m = L.marker([c.lat, c.lng], { icon: icon }).addTo(map);
    m.on('click', function(){ send(c.neighborhood); });
    bounds.push([c.lat, c.lng]);
  });
  if (bounds.length > 1) { map.fitBounds(bounds, { padding: [50, 60] }); }
  else if (bounds.length === 1) { map.setView(bounds[0], 13); }
</script>
</body>
</html>`;
}
