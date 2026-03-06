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
    var showClients = false;

    // ---- State ----
    var globe = null;
    var ws = null;
    var arcsData = [];
    var stats = { packets: 0, countries: new Set(), arcs: 0 };
    var MAX_ARCS = 500;
    var ARC_TTL = 35000;          // arcs live 35s (must be > ENRICH_INTERVAL=30s)
    var RECONNECT_DELAY = 3000;
    var reconnectTimer = null;

    // ---- Client tracking ----
    var CLIENT_EXPIRE = 120000;   // expire clients after 2min without new SYN
    var CONN_EXPIRE   = ARC_TTL;  // connections expire with their arcs (synced)
    var MAX_CLIENT_CONNS = 12;    // max unique connections shown per client
    var clientMap = {};            // localIP → { ip, out, in, conns{}, ts }
    var clientsDirty = false;

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
    var LABEL_TTL = 35000;  // match ARC_TTL
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
        if (cfg.showClients != null) {
            showClients = !!cfg.showClients;
            toggleClientView(showClients);
        }
        if (globe) globe.pointOfView({ lat: localLat, lng: localLon, altitude: 2.2 }, 1500);
    }

    // ---- Toggle between event list and client list ----
    function toggleClientView(enabled) {
        var $eventHud = document.getElementById('event-hud');
        var $clientHud = document.getElementById('client-hud');
        if (enabled) {
            if ($eventHud) $eventHud.style.display = 'none';
            if ($clientHud) $clientHud.style.display = '';
        } else {
            if ($eventHud) $eventHud.style.display = '';
            if ($clientHud) $clientHud.style.display = 'none';
        }
    }

    // ==============================================================
    // PROCESS PACKETS — pure data, zero rendering, zero DOM
    // ==============================================================
    function processPackets(packets) {
        if (!Array.isArray(packets)) return;
        var now = Date.now();

        for (var i = 0; i < packets.length; i++) {
            var pkt = packets[i];
            var isUpdate = !!pkt.update;

            var arcColor = pkt.color || (pkt.direction === 'outgoing' ? nextMultiColor() : '#0ff');

            // Arc stroke width: scale by connection bytes
            // 0 bytes = 0.5, up to 2.0 for heavy connections (log scale)
            var stroke = 0.5;
            if (pkt.bytes && pkt.bytes > 0) {
                stroke = Math.min(2.0, 0.5 + Math.log10(Math.max(pkt.bytes, 1)) * 0.25);
            }

            // Remote endpoint coordinates
            var remoteLat = pkt.lat;
            var remoteLon = pkt.lon;

            if (isUpdate) {
                // ---- ENRICHMENT UPDATE: refresh existing arcs or re-create if expired ----
                var rLatR = Math.round(remoteLat * 10);
                var rLonR = Math.round(remoteLon * 10);
                var arcFound = false;
                for (var u = 0; u < arcsData.length; u++) {
                    var a = arcsData[u];
                    var aRemLat, aRemLon;
                    if (a.startLat === localLat && a.startLon === localLon) {
                        aRemLat = a.endLat; aRemLon = a.endLon;
                    } else {
                        aRemLat = a.startLat; aRemLon = a.startLon;
                    }
                    if (Math.round(aRemLat * 10) === rLatR && Math.round(aRemLon * 10) === rLonR) {
                        if (stroke > a.stroke) a.stroke = stroke;
                        a.ts = now;
                        arcFound = true;
                    }
                }
                // Connection still active in pfctl but arc already expired → re-create it
                if (!arcFound && pkt.bytes > 0) {
                    var uSLat, uSLon, uELat, uELon;
                    if (pkt.direction === 'outgoing') {
                        uSLat = localLat; uSLon = localLon; uELat = remoteLat; uELon = remoteLon;
                    } else {
                        uSLat = remoteLat; uSLon = remoteLon; uELat = localLat; uELon = localLon;
                    }
                    arcsData.push({
                        startLat: uSLat, startLon: uSLon, endLat: uELat, endLon: uELon,
                        color: arcColor, stroke: stroke, ts: now
                    });
                }

                // Update labels too (keep alive or create)
                var lkeyU = (Math.round(remoteLat * 10) / 10) + ',' + (Math.round(remoteLon * 10) / 10);
                var locNameU = pkt.city || pkt.country || '';
                if (locNameU && remoteLat && remoteLon) {
                    labelMap[lkeyU] = {
                        label: locNameU, lat: remoteLat, lng: remoteLon,
                        color: arcColor, size: 1.2, ts: now
                    };
                    labelsDirty = true;
                } else if (labelMap[lkeyU]) {
                    labelMap[lkeyU].ts = now;
                    labelsDirty = true;
                }

                // Client tracking: update bytes or create new conn entry
                if (showClients && pkt.localIP) {
                    var cipU = pkt.localIP;
                    // Create client if it doesn't exist (pfctl discovered connection)
                    if (!clientMap[cipU]) {
                        clientMap[cipU] = { ip: cipU, out: 0, in_: 0, bytes: 0, conns: {}, ts: now };
                    }
                    var clientU = clientMap[cipU];
                    var connPortU = pkt.portLabel || ('Port ' + pkt.port);
                    var connRemoteU = pkt.city ? (pkt.city + ', ' + pkt.country) : (pkt.country || pkt.ip);
                    var connKeyU = connPortU + '|' + connRemoteU;
                    if (clientU.conns[connKeyU]) {
                        var oldBytes = clientU.conns[connKeyU].bytes || 0;
                        var newBytes = pkt.bytes || 0;
                        if (newBytes > oldBytes) {
                            clientU.bytes += (newBytes - oldBytes);
                            clientU.conns[connKeyU].bytes = newBytes;
                        }
                        clientU.conns[connKeyU].ts = now;
                        clientU.conns[connKeyU].lat = remoteLat;
                        clientU.conns[connKeyU].lon = remoteLon;
                    } else {
                        // Create new conn entry from enrichment data
                        clientU.conns[connKeyU] = {
                            dir: pkt.direction === 'outgoing' ? '\u2192' : '\u2190',
                            remote: connRemoteU,
                            port: connPortU,
                            color: arcColor,
                            bytes: pkt.bytes || 0,
                            count: 0,
                            ts: now,
                            lat: remoteLat,
                            lon: remoteLon
                        };
                        clientU.bytes += (pkt.bytes || 0);
                    }
                    clientsDirty = true;
                }

                // DON'T increment stats.packets, DON'T add to event ring
                continue;
            }

            // ---- NEW CONNECTION: normal processing ----
            stats.packets++;
            if (pkt.country) stats.countries.add(pkt.country);

            var sLat, sLon, eLat, eLon;
            if (pkt.direction === 'outgoing') {
                sLat = localLat; sLon = localLon; eLat = pkt.lat; eLon = pkt.lon;
            } else {
                sLat = pkt.lat; sLon = pkt.lon; eLat = localLat; eLon = localLon;
            }

            arcsData.push({
                startLat: sLat, startLon: sLon, endLat: eLat, endLon: eLon,
                color: arcColor, stroke: stroke, ts: now
            });

            // Track label at the REMOTE endpoint (deduplicated by location)
            var locName = pkt.city || pkt.country || '';
            if (locName && pkt.lat && pkt.lon) {
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

            // ---- Client tracking ----
            if (showClients && pkt.localIP) {
                var cip = pkt.localIP;
                if (!clientMap[cip]) {
                    clientMap[cip] = { ip: cip, out: 0, in_: 0, bytes: 0, conns: {}, ts: now };
                }
                var client = clientMap[cip];
                client.ts = now;
                if (pkt.direction === 'outgoing') client.out++;
                else client.in_++;
                // Deduplicate connections by port+remote (unique key)
                var connPort = pkt.portLabel || ('Port ' + pkt.port);
                var connRemote = pkt.city ? (pkt.city + ', ' + pkt.country) : (pkt.country || pkt.ip);
                var connKey = connPort + '|' + connRemote;
                if (client.conns[connKey]) {
                    client.conns[connKey].count++;
                    client.conns[connKey].ts = now;
                    client.conns[connKey].color = arcColor;
                    client.conns[connKey].lat = pkt.lat;
                    client.conns[connKey].lon = pkt.lon;
                } else {
                    client.conns[connKey] = {
                        dir: pkt.direction === 'outgoing' ? '\u2192' : '\u2190',
                        remote: connRemote,
                        port: connPort,
                        color: arcColor,
                        bytes: 0,
                        count: 1,
                        ts: now,
                        lat: pkt.lat,
                        lon: pkt.lon
                    };
                }
                clientsDirty = true;
            }
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
        if (eventsDirty && !showClients) {
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

        // Client list — rebuild when dirty (runs in idle callback, so safe)
        if (clientsDirty && showClients) {
            clientsDirty = false;
            renderClientList();
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

        // Expire individual connections + inactive clients
        // Sync connections to arc existence: no arc → remove connection
        if (showClients) {
            // Build set of active arc remote locations (rounded to 0.1°)
            var arcLocs = {};
            for (var ai = 0; ai < arcsData.length; ai++) {
                var arc = arcsData[ai];
                var arcRemLat, arcRemLon;
                if (arc.startLat === localLat && arc.startLon === localLon) {
                    arcRemLat = arc.endLat; arcRemLon = arc.endLon;
                } else {
                    arcRemLat = arc.startLat; arcRemLon = arc.startLon;
                }
                arcLocs[Math.round(arcRemLat * 10) + ',' + Math.round(arcRemLon * 10)] = true;
            }

            var ccutoff = now - CLIENT_EXPIRE;
            var ckeys = Object.keys(clientMap);
            var cchanged = false;
            for (var c = 0; c < ckeys.length; c++) {
                var cl = clientMap[ckeys[c]];
                // Prune individual connections: must have matching arc OR be within time window
                var ck = Object.keys(cl.conns);
                for (var j = 0; j < ck.length; j++) {
                    var cn = cl.conns[ck[j]];
                    var hasArc = (cn.lat != null)
                        ? !!arcLocs[Math.round(cn.lat * 10) + ',' + Math.round(cn.lon * 10)]
                        : (cn.ts >= now - CONN_EXPIRE);  // fallback for legacy entries
                    if (!hasArc) {
                        delete cl.conns[ck[j]];
                        cchanged = true;
                    }
                }
                // Remove entire client if no connections left or client expired
                if (Object.keys(cl.conns).length === 0 || cl.ts < ccutoff) {
                    delete clientMap[ckeys[c]];
                    cchanged = true;
                }
            }
            if (cchanged) clientsDirty = true;
        }
    }

    // ---- Client List Rendering ----
    function renderClientList() {
        var $list = document.getElementById('client-list');
        if (!$list) return;

        // Expire old clients + stale connections (arc-synced)
        var now = Date.now();
        var ccutoff = now - CLIENT_EXPIRE;

        // Build set of active arc locations for sync check
        var arcLocs = {};
        for (var ai = 0; ai < arcsData.length; ai++) {
            var arc = arcsData[ai];
            var arcRemLat, arcRemLon;
            if (arc.startLat === localLat && arc.startLon === localLon) {
                arcRemLat = arc.endLat; arcRemLon = arc.endLon;
            } else {
                arcRemLat = arc.startLat; arcRemLon = arc.startLon;
            }
            arcLocs[Math.round(arcRemLat * 10) + ',' + Math.round(arcRemLon * 10)] = true;
        }

        var ckeys = Object.keys(clientMap);
        for (var x = 0; x < ckeys.length; x++) {
            var cl = clientMap[ckeys[x]];
            // Prune connections without matching arc
            var ck = Object.keys(cl.conns);
            for (var cx = 0; cx < ck.length; cx++) {
                var cn = cl.conns[ck[cx]];
                var hasArc = (cn.lat != null)
                    ? !!arcLocs[Math.round(cn.lat * 10) + ',' + Math.round(cn.lon * 10)]
                    : true;  // keep legacy entries
                if (!hasArc) delete cl.conns[ck[cx]];
            }
            // Remove entire client if empty or expired
            if (Object.keys(cl.conns).length === 0 || cl.ts < ccutoff) delete clientMap[ckeys[x]];
        }

        // Sort clients by last octet of IP address (ascending)
        var clients = Object.keys(clientMap).map(function(k) { return clientMap[k]; });
        clients.sort(function(a, b) {
            var lastA = parseInt(a.ip.split('.').pop(), 10) || 0;
            var lastB = parseInt(b.ip.split('.').pop(), 10) || 0;
            return lastA - lastB;
        });

        // Build HTML (minimal DOM ops — single innerHTML write)
        var html = '';
        for (var i = 0; i < clients.length && i < 20; i++) {
            var cl = clients[i];
            var totalConns = cl.out + cl.in_;
            var bytesStr = formatBytes(cl.bytes);

            html += '<div class="client-entry">';
            html += '<div class="client-header">';
            html += '<span class="client-ip">' + escHtml(cl.ip) + '</span>';
            html += '<span class="client-stats">';
            html += '<span class="client-out" title="Ausgehend">\u2192 ' + cl.out + '</span>';
            html += '<span class="client-in" title="Eingehend">\u2190 ' + cl.in_ + '</span>';
            html += '<span class="client-bytes">' + bytesStr + '</span>';
            html += '</span></div>';

            // Show unique connections sorted by most recent, capped
            var connKeys = Object.keys(cl.conns);
            // Sort by timestamp descending
            connKeys.sort(function(a, b) { return cl.conns[b].ts - cl.conns[a].ts; });
            var shown = 0;
            for (var j = 0; j < connKeys.length && shown < MAX_CLIENT_CONNS; j++) {
                var cn = cl.conns[connKeys[j]];
                var safeColor = /^#[0-9a-fA-F]{6}$/.test(cn.color) ? cn.color : '#0ff';
                html += '<div class="client-conn">';
                html += '<span class="client-conn-dot" style="background:' + safeColor + ';box-shadow:0 0 6px ' + safeColor + '"></span>';
                html += '<span class="client-conn-dir">' + cn.dir + '</span>';
                html += '<span class="client-conn-port">' + escHtml(cn.port);
                if (cn.count > 1) html += ' \u00d7' + cn.count;
                html += '</span>';
                html += '<span class="client-conn-remote">' + escHtml(cn.remote) + '</span>';
                html += '</div>';
                shown++;
            }
            html += '</div>';
        }

        if (clients.length === 0) {
            html = '<div class="client-empty">Warte auf Verbindungen\u2026</div>';
        }

        $list.innerHTML = html;
    }

    function formatBytes(b) {
        if (b >= 1073741824) return (b / 1073741824).toFixed(1) + ' GB';
        if (b >= 1048576) return (b / 1048576).toFixed(1) + ' MB';
        if (b >= 1024) return (b / 1024).toFixed(1) + ' KB';
        return b + ' B';
    }

    function escHtml(s) {
        if (!s) return '';
        return s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
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
