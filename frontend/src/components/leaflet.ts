type Cluster = {
  id: string;
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
 *
 * `center` is the viewer's own device location: the map opens there (with a small "you" dot).
 * Without it the map fits the clusters, or shows a world view when there are none — there is no
 * hardcoded default city.
 */
export function buildLeafletHTML(clusters: Cluster[], rtl = false, center: { lat: number; lng: number } | null = null): string {
  // Area names come from readers' devices: "<" is escaped so no value can close the <script> tag,
  // and labels are inserted with textContent below, never as HTML.
  const data = JSON.stringify(clusters).replace(/</g, "\\u003c");
  const me = JSON.stringify(center);
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
  .me-dot{width:16px;height:16px;border-radius:50%;background:#D96C4A;border:3px solid #fff;box-shadow:0 0 0 6px rgba(217,108,74,0.25);}
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
  var me = ${me};
  var map = L.map('map', { zoomControl: true, attributionControl: true }).setView([20, 0], 2);
  L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
    maxZoom: 19,
    attribution: '&copy; OpenStreetMap'
  }).addTo(map);

  function send(id){
    var payload = JSON.stringify({ cluster: id });
    if (window.ReactNativeWebView) { window.ReactNativeWebView.postMessage(payload); }
    else if (window.parent) { window.parent.postMessage(payload, '*'); }
  }

  var bounds = [];
  clusters.forEach(function(c){
    var size = 42 + Math.min(c.people_count, 8) * 4;
    var wrap = document.createElement('div');
    wrap.className = 'marker-wrap';
    var dot = document.createElement('div');
    dot.className = 'cluster-marker';
    dot.style.width = size + 'px';
    dot.style.height = size + 'px';
    dot.style.fontSize = (size * 0.34) + 'px';
    dot.textContent = String(c.people_count);
    var label = document.createElement('div');
    label.className = 'cluster-label';
    label.textContent = c.neighborhood;
    wrap.appendChild(dot);
    wrap.appendChild(label);
    var icon = L.divIcon({ html: wrap, className:'', iconSize:[size, size+22], iconAnchor:[size/2, size/2] });
    var m = L.marker([c.lat, c.lng], { icon: icon }).addTo(map);
    m.on('click', function(){ send(c.id); });
    bounds.push([c.lat, c.lng]);
  });
  if (me) {
    L.marker([me.lat, me.lng], { icon: L.divIcon({ html: '<div class="me-dot"></div>', className:'', iconSize:[22, 22], iconAnchor:[11, 11] }), interactive: false }).addTo(map);
    map.setView([me.lat, me.lng], 12);
  }
  else if (bounds.length > 1) { map.fitBounds(bounds, { padding: [50, 60] }); }
  else if (bounds.length === 1) { map.setView(bounds[0], 13); }
</script>
</body>
</html>`;
}
