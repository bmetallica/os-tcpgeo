/**
 * TCPGeo OPNsense - Globe Visualization (Native WebSocket)
 * 3D globe showing live network traffic with directional arcs.
 *
 * PERFORMANCE ARCHITECTURE:
 * - Globe rendering:  runs in its own rAF loop, never touches DOM
 * - DOM updates:      run in requestIdleCallback, never during rAF
 * - Event list:       PRE-CREATED rows, only textContent/style changes (zero reflow)
 * - Stats:            only textContent changes (zero reflow)
 */
(function () {
    'use strict';

    // ---- Configuration (overridden by server config message) ----
    var portColors = {};
    var localLat = 50.0;
    var localLon = 10.0;

    // ---- State ----
    var globe = null;
    var ws = null;
    var arcsData = [];
    var stats = { packets: 0, countries: new Set(), arcs: 0 };
    var MAX_ARCS = 500;
    var ARC_TTL = 5000;
    var RECONNECT_DELAY = 3000;
    var reconnectTimer = null;

    // ---- Performance: Globe update throttle ----
    var GLOBE_UPDATE_MS = 800;
    var globeDirty = false;
    var lastGlobeUpdate = 0;

    // ---- Event ring buffer (fixed size, no DOM mutation) ----
    var EVENT_ROWS = 10;
    var eventRing = [];
    var eventRowEls = [];
    var eventsDirty = false;
    var statsDirty = false;

    // ---- Labels at arc endpoints (GPU sprites, no DOM) ----
    var MAX_LABELS = 30;
    var LABEL_TTL = 6000;
    var labelMap = {};  // key "lat,lon" → { label, lat, lon, color, ts }
    var labelsDirty = false;

    // ---- Default multicolor palette ----
    var MULTI_PALETTE = [
        '#0ff', '#f0f', '#0f0', '#ff0', '#f60', '#00f', '#f44', '#4f4',
        '#ff00aa', '#aa00ff', '#00ffaa', '#ffaa00'
    ];
    var multiColorIdx = 0;

    function nextMultiColor() {
        var c = MULTI_PALETTE[multiColorIdx % MULTI_PALETTE.length];
        multiColorIdx++;
        return c;
    }

    // ---- DOM Elements ----
    var $wsIndicator = document.getElementById('ws-indicator');
    var $wsStatus    = document.getElementById('ws-status');
    var $statPackets   = document.getElementById('stat-packets');
    var $statCountries = document.getElementById('stat-countries');
    var $statArcs      = document.getElementById('stat-arcs');
    var $arcBar        = document.getElementById('arc-bar');
    var $legendEntries = document.getElementById('legend-entries');
    var $eventList     = document.getElementById('event-list');

    // ---- Pre-create fixed event rows (ZERO DOM changes after this) ----
    function initEventRows() {
        for (var i = 0; i < EVENT_ROWS; i++) {
            var row = document.createElement('div');
            row.className = 'event-item';
            row.style.visibility = 'hidden';

            var dot = document.createElement('span');
            dot.className = 'event-dot';

            var dir = document.createElement('span');
            dir.className = 'event-dir';

            var label = document.createElement('span');
            label.className = 'event-label';

            var loc = document.createElement('span');
            loc.className = 'event-loc';

            row.appendChild(dot);
            row.appendChild(dir);
            row.appendChild(label);
            row.appendChild(loc);
            $eventList.appendChild(row);

            eventRowEls.push({ row: row, dot: dot, dir: dir, label: label, loc: loc });
        }
    }

    // ---- Initialize Globe ----
    function initGlobe() {
        var container = document.getElementById('globeViz');

        globe = Globe()(container)
            .backgroundColor('rgba(0,0,0,0)')
            .showGlobe(true)
            .showAtmosphere(true)
            .atmosphereColor('rgba(0,170,255,0.15)')
            .atmosphereAltitude(0.18)
            .pointOfView({ lat: localLat, lng: localLon, altitude: 2.2 })
            .arcsData(arcsData)
            .arcStartLat(function (d) { return d.startLat; })
            .arcStartLng(function (d) { return d.startLon; })
            .arcEndLat(function (d) { return d.endLat; })
            .arcEndLng(function (d) { return d.endLon; })
            .arcColor(function (d) { return d.color; })
            .arcStroke(function (d) { return d.stroke; })
            .arcDashLength(0.5)
            .arcDashGap(0.25)
            .arcDashAnimateTime(1500)
            .arcAltitudeAutoScale(0.4)
            .arcsTransitionDuration(0)
            // Labels at arc endpoints (GPU sprites)
            .labelsData([])
            .labelText(function (d) { return d.label; })
            .labelSize(function (d) { return d.size || 1.2; })
            .labelDotRadius(0.3)
            .labelColor(function (d) { return d.color || 'rgba(0,255,255,0.85)'; })
            .labelResolution(1)
            .labelAltitude(0.01)
            .labelIncludeDot(true)
            .labelsTransitionDuration(0);

        // Dark Cyberpunk Globe Material
        var globeMat = globe.globeMaterial();
        globeMat.color = new THREE.Color(0x020818);
        globeMat.emissive = new THREE.Color(0x010410);
        globeMat.emissiveIntensity = 0.8;

        var scene = globe.scene();
        var GLOBE_R = 100;

        // Wireframe grid sphere
        var gridGeo = new THREE.SphereGeometry(GLOBE_R + 0.15, 48, 24);
        var gridMat = new THREE.MeshBasicMaterial({
            color: 0x0055aa, wireframe: true,
            transparent: true, opacity: 0.06, depthWrite: false
        });
        scene.add(new THREE.Mesh(gridGeo, gridMat));

        // Neon country border outlines
        fetch('/countries-110m.json')
            .then(function (r) { return r.json(); })
            .then(function (topology) {
                var countries = topojsonFeature(topology, topology.objects.countries).features;
                var allPts = [];
                for (var ci = 0; ci < countries.length; ci++) {
                    var feat = countries[ci];
                    var coords = feat.geometry.type === 'Polygon'
                        ? [feat.geometry.coordinates] : feat.geometry.coordinates;
                    for (var pi = 0; pi < coords.length; pi++) {
                        for (var ri = 0; ri < coords[pi].length; ri++) {
                            var ring = coords[pi][ri];
                            for (var i = 0; i < ring.length - 1; i++) {
                                var lon1 = ring[i][0], lat1 = ring[i][1];
                                var lon2 = ring[i+1][0], lat2 = ring[i+1][1];
                                var phi1 = (90 - lat1) * Math.PI / 180;
                                var th1  = (90 - lon1) * Math.PI / 180;
                                var phi2 = (90 - lat2) * Math.PI / 180;
                                var th2  = (90 - lon2) * Math.PI / 180;
                                var r = GLOBE_R + 0.5;
                                allPts.push(
                                    r*Math.sin(phi1)*Math.cos(th1), r*Math.cos(phi1), r*Math.sin(phi1)*Math.sin(th1),
                                    r*Math.sin(phi2)*Math.cos(th2), r*Math.cos(phi2), r*Math.sin(phi2)*Math.sin(th2)
                                );
                            }
                        }
                    }
                }
                var geo = new THREE.BufferGeometry();
                geo.setAttribute('position', new THREE.Float32BufferAttribute(allPts, 3));
                scene.add(new THREE.LineSegments(geo, new THREE.LineBasicMaterial({
                    color: 0x00ccff, transparent: true, opacity: 0.9
                })));
                var glowLines = new THREE.LineSegments(geo, new THREE.LineBasicMaterial({
                    color: 0x00aaff, transparent: true, opacity: 0.3,
                    blending: THREE.AdditiveBlending, depthWrite: false
                }));
                glowLines.scale.setScalar(1.003);
                scene.add(glowLines);
            })
            .catch(function () {});

        // Auto-Rotation
        globe.controls().autoRotate = true;
        globe.controls().autoRotateSpeed = 0.4;
        globe.controls().enableDamping = true;
        globe.controls().dampingFactor = 0.1;

        // Lighting
        var amb = scene.children.find(function (c) { return c.type === 'AmbientLight'; });
        if (amb) amb.intensity = 0.5;
        var dl = new THREE.DirectionalLight(0x3388ff, 0.4);
        dl.position.set(5, 3, 5);
        scene.add(dl);

        window.addEventListener('resize', function () {
            globe.width(container.clientWidth).height(container.clientHeight);
        });
    }

    // ---- topojson helper ----
    function topojsonFeature(topology, object) {
        var arcs = topology.arcs;
        function arcToCoords(idx) {
            var a = idx < 0 ? arcs[~idx].slice().reverse() : arcs[idx].slice();
            var x = 0, y = 0;
            return a.map(function (p) { x += p[0]; y += p[1]; return [x, y]; });
        }
        function decodeArc(idx) {
            var c = arcToCoords(idx), tf = topology.transform;
            return tf ? c.map(function (p) { return [p[0]*tf.scale[0]+tf.translate[0], p[1]*tf.scale[1]+tf.translate[1]]; }) : c;
        }
        function decodeRing(indices) {
            var c = [];
            for (var i = 0; i < indices.length; i++) c = c.concat(decodeArc(indices[i]));
            return c;
        }
        function decodeGeom(g) {
            if (g.type === 'Polygon') return { type: 'Polygon', coordinates: g.arcs.map(decodeRing) };
            if (g.type === 'MultiPolygon') return { type: 'MultiPolygon', coordinates: g.arcs.map(function (p) { return p.map(decodeRing); }) };
            return g;
        }
        return {
            type: 'FeatureCollection',
            features: (object.geometries || []).map(function (g) {
                return { type: 'Feature', id: g.id, properties: g.properties || {}, geometry: decodeGeom(g) };
            })
        };
    }

    // ---- WebSocket ----
    function connectWS() {
        var proto = location.protocol === 'https:' ? 'wss' : 'ws';
        setStatus('connecting');
        ws = new WebSocket(proto + '://' + location.host + '/ws');

        ws.onopen = function () {
            setStatus('connected');
            if (reconnectTimer) { clearTimeout(reconnectTimer); reconnectTimer = null; }
        };
        ws.onmessage = function (event) {
            try { handleMessage(JSON.parse(event.data)); } catch (e) {}
        };
        ws.onclose = function () { setStatus('disconnected'); scheduleReconnect(); };
        ws.onerror = function () { setStatus('error'); ws.close(); };
    }

    function scheduleReconnect() {
        if (reconnectTimer) return;
        reconnectTimer = setTimeout(function () { reconnectTimer = null; connectWS(); }, RECONNECT_DELAY);
    }

    function setStatus(state) {
        $wsIndicator.className = 'status-dot ' + state;
        var labels = { connecting:'VERBINDE\u2026', connected:'VERBUNDEN', disconnected:'GETRENNT', error:'FEHLER', capturing:'EMPFANGE DATEN' };
        $wsStatus.textContent = labels[state] || state;
    }

    function handleMessage(msg) {
        if (msg.type === 'config') applyConfig(msg.data);
        else if (msg.type === 'packets') processPackets(msg.data);
        else if (msg.type === 'status' && msg.data === 'capturing') setStatus('capturing');
    }

    function applyConfig(cfg) {
        if (cfg.portColors) { portColors = cfg.portColors; buildPortLegend(); }
        if (cfg.localLat != null) localLat = cfg.localLat;
        if (cfg.localLon != null) localLon = cfg.localLon;
        if (globe) globe.pointOfView({ lat: localLat, lng: localLon, altitude: 2.2 }, 1500);
    }

    // ==============================================================
    // PROCESS PACKETS — pure data, zero rendering, zero DOM
    // ==============================================================
    function processPackets(packets) {
        if (!Array.isArray(packets)) return;
        var now = Date.now();

        for (var i = 0; i < packets.length; i++) {
            var pkt = packets[i];
            stats.packets++;
            if (pkt.country) stats.countries.add(pkt.country);

            var arcColor = pkt.color || (pkt.direction === 'outgoing' ? nextMultiColor() : '#0ff');

            var sLat, sLon, eLat, eLon;
            if (pkt.direction === 'outgoing') {
                sLat = localLat; sLon = localLon; eLat = pkt.lat; eLon = pkt.lon;
            } else {
                sLat = pkt.lat; sLon = pkt.lon; eLat = localLat; eLon = localLon;
            }

            arcsData.push({
                startLat: sLat, startLon: sLon, endLat: eLat, endLon: eLon,
                color: arcColor, stroke: 0.5, ts: now
            });

            // Track label at the REMOTE endpoint (deduplicated by location)
            var locName = pkt.city || pkt.country || '';
            if (locName && pkt.lat && pkt.lon) {
                // Round to 1 decimal to merge nearby points
                var lkey = (Math.round(pkt.lat * 10) / 10) + ',' + (Math.round(pkt.lon * 10) / 10);
                labelMap[lkey] = {
                    label: locName,
                    lat: pkt.lat,
                    lng: pkt.lon,
                    color: arcColor,
                    size: 1.2,
                    ts: now
                };
                labelsDirty = true;
            }

            // Push into ring buffer (fixed size, oldest falls off)
            eventRing.unshift({
                dir: pkt.direction === 'outgoing' ? '\u2192' : '\u2190',
                label: pkt.portLabel || ('Port ' + pkt.port),
                loc: pkt.city ? (pkt.city + ', ' + pkt.country) : (pkt.country || pkt.ip),
                color: arcColor
            });
            if (eventRing.length > EVENT_ROWS) eventRing.length = EVENT_ROWS;
        }

        // Expire + cap arcs
        var cutoff = now - ARC_TTL;
        arcsData = arcsData.filter(function (a) { return a.ts > cutoff; });
        if (arcsData.length > MAX_ARCS) arcsData = arcsData.slice(-MAX_ARCS);

        globeDirty = true;
        statsDirty = true;
        eventsDirty = true;
    }

    // ==============================================================
    // GLOBE RENDER LOOP — rAF only, NEVER touches DOM
    // ==============================================================
    function globeLoop() {
        requestAnimationFrame(globeLoop);
        if (!globeDirty && !labelsDirty) return;
        var now = Date.now();
        if (now - lastGlobeUpdate < GLOBE_UPDATE_MS) return;

        if (globe) {
            if (globeDirty) globe.arcsData(arcsData);

            if (labelsDirty) {
                // Expire old labels
                var cutoff = now - LABEL_TTL;
                var keys = Object.keys(labelMap);
                for (var k = 0; k < keys.length; k++) {
                    if (labelMap[keys[k]].ts < cutoff) delete labelMap[keys[k]];
                }
                // Cap to MAX_LABELS (keep newest)
                var vals = Object.keys(labelMap).map(function (k) { return labelMap[k]; });
                vals.sort(function (a, b) { return b.ts - a.ts; });
                if (vals.length > MAX_LABELS) {
                    vals = vals.slice(0, MAX_LABELS);
                    // Rebuild map
                    labelMap = {};
                    for (var j = 0; j < vals.length; j++) {
                        var v = vals[j];
                        labelMap[(Math.round(v.lat*10)/10)+','+(Math.round(v.lng*10)/10)] = v;
                    }
                }
                globe.labelsData(vals);
                labelsDirty = false;
            }
        }

        lastGlobeUpdate = now;
        globeDirty = false;
    }

    // ==============================================================
    // DOM UPDATE — requestIdleCallback ONLY (runs when browser is idle)
    // Uses ZERO DOM tree mutations: only textContent + style changes.
    // ==============================================================
    var idleScheduled = false;

    function scheduleDomUpdate() {
        if (idleScheduled) return;
        idleScheduled = true;
        if (window.requestIdleCallback) {
            requestIdleCallback(doDomUpdate, { timeout: 2000 });
        } else {
            setTimeout(doDomUpdate, 1500);
        }
    }

    function doDomUpdate() {
        idleScheduled = false;

        // Stats — textContent only, no reflow
        if (statsDirty) {
            statsDirty = false;
            $statPackets.textContent = formatNumber(stats.packets);
            $statCountries.textContent = stats.countries.size;
            $statArcs.textContent = arcsData.length;
            if ($arcBar) $arcBar.style.width = Math.min(100, (arcsData.length / MAX_ARCS) * 100) + '%';
        }

        // Events — update pre-created rows only via textContent + style
        // NO createElement, NO removeChild, NO innerHTML => ZERO reflow
        if (eventsDirty) {
            eventsDirty = false;
            for (var i = 0; i < EVENT_ROWS; i++) {
                var el = eventRowEls[i];
                if (i < eventRing.length) {
                    var ev = eventRing[i];
                    el.dot.style.background = ev.color;
                    el.dir.textContent = ev.dir;
                    el.label.textContent = ev.label;
                    el.loc.textContent = ev.loc;
                    el.row.style.visibility = 'visible';
                } else {
                    el.row.style.visibility = 'hidden';
                }
            }
        }

        // Reschedule
        scheduleDomUpdate();
    }

    function formatNumber(n) {
        if (n >= 1000000) return (n / 1000000).toFixed(1) + 'M';
        if (n >= 1000) return (n / 1000).toFixed(1) + 'K';
        return n.toString();
    }

    // ---- Port Legend ----
    function buildPortLegend() {
        while ($legendEntries.firstChild) $legendEntries.removeChild($legendEntries.firstChild);
        var ports = Object.entries(portColors);
        if (ports.length === 0) {
            var noConf = document.createElement('span');
            noConf.style.color = 'var(--text-dim)';
            noConf.textContent = 'Keine Ports konfiguriert';
            $legendEntries.appendChild(noConf);
            return;
        }
        for (var i = 0; i < ports.length; i++) {
            var port = ports[i][0], info = ports[i][1];
            var safeColor = /^#[0-9a-fA-F]{6}$/.test(info.color) ? info.color : '#00ffff';
            var item = document.createElement('span');
            item.className = 'legend-entry';
            var dot = document.createElement('span');
            dot.className = 'legend-dot';
            dot.style.background = safeColor;
            dot.style.color = safeColor;
            item.appendChild(dot);
            item.appendChild(document.createTextNode(info.label || 'Port ' + port));
            $legendEntries.appendChild(item);
        }
    }

    // ---- Arc Cleanup ----
    function cleanArcs() {
        var now = Date.now();
        var cutoff = now - ARC_TTL;
        var before = arcsData.length;
        arcsData = arcsData.filter(function (a) { return a.ts > cutoff; });
        if (arcsData.length !== before) globeDirty = true;

        // Also expire labels
        var lcutoff = now - LABEL_TTL;
        var keys = Object.keys(labelMap);
        var changed = false;
        for (var k = 0; k < keys.length; k++) {
            if (labelMap[keys[k]].ts < lcutoff) { delete labelMap[keys[k]]; changed = true; }
        }
        if (changed) labelsDirty = true;
    }

    // ---- Boot ----
    function boot() {
        initEventRows();
        initGlobe();
        connectWS();
        globeLoop();
        scheduleDomUpdate();
        setInterval(cleanArcs, 3000);
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', boot);
    } else {
        boot();
    }
})();
