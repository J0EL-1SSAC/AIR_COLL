import React, { useEffect, useMemo, useState } from 'react';
import { createRoot } from 'react-dom/client';
import { MapContainer, Marker, Polygon, Polyline, TileLayer, Tooltip, useMap } from 'react-leaflet';
import L from 'leaflet';
import 'leaflet/dist/leaflet.css';
import './style.css';

const apiBase = import.meta.env.VITE_API_BASE || 'http://localhost:8000';
const wsUrl = apiBase.replace(/^http/, 'ws') + '/ws/live';
const fmt = (v, unit, digits=0) => v == null ? '—' : `${Number(v).toFixed(digits)} ${unit}`;
function AircraftMarker({ aircraft, selected, onClick, bands }) {
  const altitude = aircraft.baro_altitude_m;
  const color = aircraft.on_ground ? '#f59e0b' : altitude == null ? '#94a3b8' : altitude < bands.low_max ? '#22c55e' : altitude < bands.medium_max ? '#38bdf8' : '#a78bfa';
  const icon = useMemo(() => L.divIcon({ className: 'aircraft-icon-wrap', html: `<div class="plane" style="--plane-color:${color};--rotation:${aircraft.track_deg || 0}deg">➤</div>`, iconSize: [30,30], iconAnchor: [15,15] }), [color, aircraft.track_deg]);
  return <Marker position={[aircraft.latitude, aircraft.longitude]} icon={icon} eventHandlers={{ click: onClick }}>
    <Tooltip permanent direction="top" offset={[0,-8]}>{aircraft.callsign || aircraft.icao24}</Tooltip>
  </Marker>;
}
function FitAirport({ center }) { const map = useMap(); useEffect(() => { map.setView(center, map.getZoom()); }, [center, map]); return null; }
function App() {
  const [airport, setAirport] = useState(null);
  const [aircraft, setAircraft] = useState([]);
  const [selected, setSelected] = useState(null);
  const [status, setStatus] = useState('CONNECTING');
  const [message, setMessage] = useState('Connecting to live collector…');
  const [counts, setCounts] = useState({aircraft_count:0, low_or_ground_count:0});
  const [connected, setConnected] = useState(false);
  const [ageTick, setAgeTick] = useState(0);
  const [groundOnly, setGroundOnly] = useState(false);
  useEffect(() => { fetch(`${apiBase}/api/airport`).then(r=>r.ok?r.json():Promise.reject()).then(setAirport).catch(()=>{setStatus('DISCONNECTED');setMessage('Backend unavailable. No live data.');}); }, []);
  useEffect(() => {
    const ageTimer = setInterval(() => setAgeTick(t => t + (airport?.age_refresh_s || 1)), (airport?.age_refresh_s || 1) * 1000);
    let socket, retryTimer, stopped=false, delay=airport?.reconnect_initial_ms || 1000;
    const connect = () => {
      if (stopped) return;
      socket = new WebSocket(wsUrl);
      socket.onopen = () => { setConnected(true); delay=airport?.reconnect_initial_ms || 1000; };
      socket.onmessage = event => { try { const m=JSON.parse(event.data); setStatus(m.source_status); setMessage(m.data?.message || ''); setAircraft(m.data?.aircraft || []); setCounts(m.data || {}); setAgeTick(0); setSelected(current => current ? (m.data?.aircraft || []).find(a=>a.icao24===current.icao24) || null : null); } catch {} };
      socket.onclose = () => { setConnected(false); setStatus('DISCONNECTED'); setMessage('Backend disconnected. Live aircraft cleared. Reconnecting…'); setAircraft([]); setSelected(null); retryTimer=setTimeout(connect, delay); delay=Math.min(delay*2, airport?.reconnect_max_ms || 15000); };
      socket.onerror = () => socket.close();
    };
    connect();
    return () => { stopped=true; clearTimeout(retryTimer); clearInterval(ageTimer); socket?.close(); };
  }, [airport]);
  if (!airport) return <main className="loading"><h1>AIR_COL</h1><p>{message}</p><p className="disclaimer">Research and educational prototype only. Public ADS-B data can be delayed or incomplete, with poor low-altitude and ground coverage. Not for operational safety decisions.</p></main>;
  const fresh = aircraft.map(a=>({...a, age_s:a.age_s+ageTick})).filter(a=>a.age_s <= airport.stale_after_s);
  const visible = groundOnly ? fresh.filter(a=>a.on_ground) : fresh;
  const selectedFresh = selected ? fresh.find(a=>a.icao24===selected.icao24) : null;
  const lowBandFt = airport.altitude_bands_m.low_max * 3.280839895;
  const mediumBandFt = airport.altitude_bands_m.medium_max * 3.280839895;
  const center = [airport.latitude, airport.longitude];
  const ring = airport.radius_ring.map(([lon,lat])=>[lat,lon]);
  const colors = {OK:'green', DEGRADED:'amber', NO_DATA:'red', DISCONNECTED:'red', CONNECTING:'amber'};
  return <div className="app">
    <header><div><h1>AIR_COL <span>LIVE RESEARCH</span></h1><p>{airport.icao} · {airport.radius_nm} NM monitoring radius</p></div><div className={`status ${colors[status] || 'amber'}`}><i/>{status.replace('_',' ')} · {message}</div></header>
    <div className="workspace">
      <aside className="left panel"><h2>Filters</h2><label><input type="checkbox" checked={groundOnly} onChange={e=>setGroundOnly(e.target.checked)}/> On-ground only</label><div className="legend"><h3>Aircraft bands</h3><p><b className="dot amberdot"/> On ground</p><p><b className="dot greendot"/> Below {Math.round(lowBandFt).toLocaleString()} ft</p><p><b className="dot bluedot"/> {Math.round(lowBandFt).toLocaleString()}–{Math.round(mediumBandFt).toLocaleString()} ft</p><p><b className="dot purpledot"/> Above {Math.round(mediumBandFt).toLocaleString()} ft</p></div><div className="coverage"><b>{counts.aircraft_count || 0}</b><span> aircraft observed</span><p>{counts.low_or_ground_count || 0} below 1,000 ft / on ground</p></div></aside>
      <section className="map-area"><MapContainer center={center} zoom={airport.map_zoom} scrollWheelZoom className="map"><FitAirport center={center}/><TileLayer attribution='&copy; OpenStreetMap contributors' url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"/><Polygon positions={ring} pathOptions={{color:'#42d6c5',weight:2,fillOpacity:0.035,dashArray:'7 7'}}/><Marker position={center}><Tooltip permanent>{airport.icao}</Tooltip></Marker>{visible.map(a=><React.Fragment key={a.icao24}>{a.history.length > 1 && <Polyline positions={a.history.map(p=>[p.latitude,p.longitude])} pathOptions={{color:'#e5f4ff',weight:2,opacity:.55}}/>}<AircraftMarker aircraft={a} selected={selected?.icao24===a.icao24} bands={airport.altitude_bands_m} onClick={()=>setSelected(a)}/></React.Fragment>)}</MapContainer><div className="map-caption">Live positions and trails · recorded from the live feed</div></section>
      <aside className="right panel"><h2>Aircraft details</h2>{selectedFresh ? <><div className="callsign">{selectedFresh.callsign || 'Unknown callsign'}</div><div className="icao">{selectedFresh.icao24}</div><dl><dt>Altitude</dt><dd>{fmt(selectedFresh.altitude_ft,'ft')}</dd><dt>Speed</dt><dd>{fmt(selectedFresh.speed_kt,'kt')}</dd><dt>Heading</dt><dd>{fmt(selectedFresh.track_deg,'°')}</dd><dt>Vertical rate</dt><dd>{fmt(selectedFresh.vertical_rate_mps == null ? null : selectedFresh.vertical_rate_mps*196.8504,'ft/min')}</dd><dt>On ground</dt><dd>{selectedFresh.on_ground ? 'Yes' : 'No'}</dd><dt>Data age</dt><dd>{fmt(selectedFresh.age_s,'s',1)}</dd><dt>Distance</dt><dd>{fmt(selectedFresh.distance_nm,'NM',1)}</dd></dl></> : <p className="muted">Select a live aircraft marker to inspect its reported state.</p>}</aside>
    </div>
    <section className="timeline panel"><h2>Timeline</h2><p>History and event timeline will be available in a later phase.</p></section>
    <footer className="disclaimer">Research and educational prototype only. Public ADS-B data may be delayed, omit aircraft, and have poor low-altitude or ground coverage. This is not ATC, TCAS/ACAS, or a certified runway-safety system.</footer>
  </div>;
}

createRoot(document.getElementById('root')).render(<App/>);
