import React, {useEffect, useMemo, useState} from 'react';
import {Circle, CircleMarker, MapContainer, Marker, Pane, Polygon, Polyline, TileLayer, Tooltip, useMap} from 'react-leaflet';
import L from 'leaflet';
import {ALTITUDE_BANDS, DISTANCE_FORMAT, UI} from '../config';

function altitudeBand(aircraft, bands) {
  if (aircraft.on_ground === true) return 'ground';
  const altitude = aircraft.baro_altitude_m ?? aircraft.geo_altitude_m;
  if (altitude == null) return 'medium';
  if (altitude < bands.low_max) return 'low';
  if (altitude < bands.medium_max) return 'medium';
  return 'high';
}

function isReducedQuality(aircraft) {
  return aircraft.quality_flags?.includes('LOW_QUALITY') || aircraft.quality_flags?.includes('stale_position');
}

function AircraftIcon({aircraft, selected, labelMode, bands, zoom, labelZoom}) {
  const [hovered, setHovered] = useState(false);
  const band = altitudeBand(aircraft, bands);
  const reduced = isReducedQuality(aircraft);
  const shape = band === 'ground'
    ? '<circle class="ground-shape" cx="18" cy="18" r="10"/>'
    : '<path class="aircraft-shape" d="M18 1.8 22 14l10.8 5.4v3L22 20l-1.2 8.2 4.1 2.3v2L18 30.2l-6.9 2.3v-2l4.1-2.3L14 20 3.2 22.4v-3L14 14z"/>';
  const icon = useMemo(() => L.divIcon({
    className: 'aircraft-icon-wrap',
    html: `<div class="aircraft-symbol band-${band}${selected?' selected':''}${reduced?' quality-reduced':''}" style="--track-angle:${Number(aircraft.track_deg||0)}deg"><svg viewBox="0 0 36 36" aria-hidden="true">${shape}</svg>${reduced?'<span class="quality-cue" aria-label="Reduced quality">!</span>':''}</div>`,
    iconSize:[36,36], iconAnchor:[18,18],
  }), [aircraft.icao24, aircraft.track_deg, band, reduced, selected, shape]);
  const labelVisible = labelMode !== 'off' && (selected || hovered || zoom >= labelZoom);
  const showDetails = labelMode === 'detailed' && (selected || hovered);
  const altitudeFt = aircraft.baro_altitude_m == null ? null : aircraft.baro_altitude_m / DISTANCE_FORMAT.metersPerFoot;
  const speedKt = aircraft.velocity_mps == null ? null : aircraft.velocity_mps * DISTANCE_FORMAT.metersPerSecondToKnots;
  return <Marker position={[aircraft.latitude,aircraft.longitude]} icon={icon}
    eventHandlers={{click:aircraft.onSelect,mouseover:()=>setHovered(true),mouseout:()=>setHovered(false)}}>
    {labelVisible && aircraft.callsign && <Tooltip permanent direction="right" offset={[12,0]}>
      <span className="map-callsign">{aircraft.callsign.trim()}</span>
      {showDetails && <span className="map-detail">{altitudeFt==null?'—':`${Math.round(altitudeFt)} ft`} · {speedKt==null?'—':`${Math.round(speedKt)} kt`}</span>}
    </Tooltip>}
  </Marker>;
}

function TrailLayer({aircraft, color, oldestOpacity, newestOpacity}) {
  const points = aircraft.history || [];
  if (points.length < 2) return null;
  const n = points.length - 1;
  return <>{points.slice(1).map((point,index)=>{
    const opacity = n === 0 ? newestOpacity : oldestOpacity + (newestOpacity-oldestOpacity)*(index/n);
    return <Polyline key={`${aircraft.icao24}-trail-${index}`} pane="trails"
      positions={[[points[index].latitude,points[index].longitude],[point.latitude,point.longitude]]}
      pathOptions={{color,weight:UI.trailWidth,opacity,lineCap:'round',lineJoin:'round'}}/>;
  })}</>;
}

function PredictionLayer({aircraft, prediction, showUncertainty, showTicks, tickZoom, selected}) {
  if (!prediction || prediction.status !== 'PREDICTED' || !prediction.points?.length) return null;
  const origin = prediction.origin;
  const path = [[origin.latitude,origin.longitude],...prediction.points.map(point=>[point.latitude,point.longitude])];
  const visibleTicks = showTicks && (selected || tickZoom);
  return <>
    <Polyline pane="prediction-halo" positions={[[aircraft.latitude,aircraft.longitude],[origin.latitude,origin.longitude]]}
    pathOptions={{color:'var(--color-prediction-halo)',weight:UI.predictionHaloWidth,opacity:.9,lineCap:'round'}}/>
    <Polyline pane="prediction-halo" positions={path}
      pathOptions={{color:'var(--color-prediction-halo)',weight:UI.predictionHaloWidth,opacity:.9,dashArray:'7 7',lineCap:'round'}}/>
    <Polyline pane="predictions" positions={[[aircraft.latitude,aircraft.longitude],[origin.latitude,origin.longitude]]}
      pathOptions={{color:'var(--color-prediction)',weight:UI.predictionWidth,opacity:.86,dashArray:'2 5',lineCap:'round'}}/>
    <Polyline pane="predictions" positions={path}
      pathOptions={{color:'var(--color-prediction)',weight:UI.predictionWidth,opacity:1,dashArray:'7 7',lineCap:'round'}}/>
    {prediction.points.map(point=><React.Fragment key={`${aircraft.icao24}-prediction-${point.t_s}`}>
      {showUncertainty && <Circle pane="uncertainty" center={[point.latitude,point.longitude]}
        radius={point.uncertainty_radius_m} pathOptions={{color:'var(--color-prediction-uncertainty)',weight:1.5,opacity:.85,fillColor:'var(--color-prediction-uncertainty)',fillOpacity:.11}}/>}
      <CircleMarker pane="predictions" center={[point.latitude,point.longitude]} radius={UI.tickRadius}
        pathOptions={{color:'var(--color-cpa-outline)',weight:2,fillColor:'var(--color-prediction)',fillOpacity:1}}>
        {visibleTicks && <Tooltip permanent direction="top" className="prediction-tick">+{point.t_s}s</Tooltip>}
      </CircleMarker>
    </React.Fragment>)}
  </>;
}

function PairLayer({pair, color, currentAircraft, focused, showLabel, onFocus}) {
  const [hovered,setHovered]=useState(false);
  const a = pair.cpa_position.aircraft_a;
  const b = pair.cpa_position.aircraft_b;
  const midpoint = pair.cpa_position.midpoint;
  const stateA = currentAircraft[pair.aircraft_a.icao24];
  const stateB = currentAircraft[pair.aircraft_b.icao24];
  const eventHandlers = {click:()=>onFocus(pair),mouseover:()=>setHovered(true),mouseout:()=>setHovered(false)};
  const distanceNm = pair.h_sep_cpa_m / DISTANCE_FORMAT.metersPerNm;
  const message = `CPA in ${Math.round(pair.t_cpa_s)} s, ${distanceNm.toFixed(1)} NM`;
  return <>
    {stateA && <Polyline pane="pairs" positions={[[stateA.latitude,stateA.longitude],[a.latitude,a.longitude]]} pathOptions={{color,weight:1.5,opacity:.75,dashArray:'4 5'}} eventHandlers={eventHandlers}/>}
    {stateB && <Polyline pane="pairs" positions={[[stateB.latitude,stateB.longitude],[b.latitude,b.longitude]]} pathOptions={{color,weight:1.5,opacity:.75,dashArray:'4 5'}} eventHandlers={eventHandlers}/>}
    <Polyline pane="pairs" positions={[[a.latitude,a.longitude],[b.latitude,b.longitude]]}
      pathOptions={{color,weight:UI.pairWidth,opacity:.96,lineCap:'round'}} eventHandlers={eventHandlers}/>
    <CircleMarker pane="pairs" center={[midpoint.latitude,midpoint.longitude]} radius={6}
      pathOptions={{color:'var(--color-marker-outline)',weight:2,fillColor:color,fillOpacity:1}} eventHandlers={eventHandlers}>
      {(focused || showLabel || hovered) && <Tooltip permanent direction="top" className="cpa-label">{message}</Tooltip>}
    </CircleMarker>
  </>;
}

function AirportMarker({airport}) {
  const icon = useMemo(()=>L.divIcon({className:'airport-icon-wrap',html:'<div class="airport-symbol"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M21 16v-2l-8-5V3.5a1.5 1.5 0 0 0-3 0V9l-8 5v2l8-2.5V19l-2 1.5V22l3.5-1 3.5 1v-1.5L13 19v-5.5z"/></svg></div>',iconSize:[30,30],iconAnchor:[15,15]}),[]);
  return <Marker position={[airport.latitude,airport.longitude]} icon={icon} pane="airports"><Tooltip permanent direction="right" className="airport-label">{airport.icao}</Tooltip></Marker>;
}

function MapController({setZoom,focusPair}) {
  const map = useMap();
  useEffect(()=>{const update=()=>setZoom(map.getZoom());map.on('zoomend',update);update();return()=>map.off('zoomend',update);},[map,setZoom]);
  useEffect(()=>{
    if (!focusPair) return;
    const {aircraft_a:a,aircraft_b:b}=focusPair.cpa_position;
    map.fitBounds([[a.latitude,a.longitude],[b.latitude,b.longitude]],{padding:[UI.mapFocusPadding,UI.mapFocusPadding],maxZoom:UI.mapFocusMaxZoom});
  },[map,focusPair]);
  return null;
}

function MapSizeHandler(){
  const map=useMap();
  useEffect(()=>{
    const observer=new ResizeObserver(()=>map.invalidateSize({pan:false}));
    observer.observe(map.getContainer());
    return()=>observer.disconnect();
  },[map]);
  return null;
}

export default function MapView({airport,aircraft,predictions,pairs,selectedId,onSelect,focusPair,onFocusPair,layers,labelMode,basemap,onTileFailure,mapNotice}) {
  const bandColors = ALTITUDE_BANDS;
  const stateById = Object.fromEntries(aircraft.map(state=>[state.icao24,state]));
  const [zoom,setZoom] = useState(airport.map_zoom);
  const labelZoom = airport.labels_min_zoom ?? UI.labelZoomFallback;
  const tickZoom = zoom >= (airport.prediction_ticks_min_zoom ?? UI.predictionTickZoomFallback);
  const thresholds = airport.cpa_display || {near_nm:1.5,amber_nm:3};
  const ring = airport.radius_ring.map(([longitude,latitude])=>[latitude,longitude]);
  const eligiblePairs = pairs.filter(pair=>pair.converging&&!pair.cpa_in_past);
  const pairColor = pair=>{
    const distance = pair.h_sep_cpa_m / DISTANCE_FORMAT.metersPerNm;
    if(distance<=thresholds.near_nm)return 'var(--color-cpa-red)';
    if(distance<=thresholds.amber_nm)return 'var(--color-cpa-amber)';
    return 'var(--color-neutral-cpa)';
  };
  return <div className="map-frame">
    <MapContainer center={[airport.latitude,airport.longitude]} zoom={airport.map_zoom} scrollWheelZoom className="map">
      <MapController setZoom={setZoom} focusPair={focusPair}/>
      <MapSizeHandler/>
      <Pane name="trails" style={{zIndex:UI.panes.trails}}/><Pane name="pairs" style={{zIndex:UI.panes.pairs}}/>
      <Pane name="prediction-halo" style={{zIndex:UI.panes.predictionHalo}}/><Pane name="predictions" style={{zIndex:UI.panes.predictions}}/>
      <Pane name="uncertainty" style={{zIndex:UI.panes.uncertainty}}/><Pane name="airports" style={{zIndex:UI.panes.airports}}/>
      <TileLayer key={basemap} url={UI.tileUrls[basemap]} attribution={UI.tileAttribution[basemap]}
        eventHandlers={{tileerror:onTileFailure}}/>
      {layers.radius&&<Polygon positions={ring} pane="trails" pathOptions={{color:'var(--color-accent)',weight:1.5,opacity:.7,fillOpacity:.015,dashArray:'6 7'}}/>}
      {/* Phase 8 runway geometries will be drawn in this map layer group. */}
      {layers.trails&&aircraft.map(state=><TrailLayer key={`trail-${state.icao24}`} aircraft={state}
        color={bandColors[altitudeBand(state,airport.altitude_bands_m)]}
        oldestOpacity={airport.trail_oldest_opacity??UI.oldestTrailOpacityFallback}
        newestOpacity={airport.trail_newest_opacity??UI.newestTrailOpacityFallback}/>) }
      {layers.closestApproaches&&eligiblePairs.map(pair=><PairLayer key={pair.pair_key} pair={pair}
        color={pairColor(pair)} currentAircraft={stateById} focused={focusPair?.pair_key===pair.pair_key}
        showLabel={zoom>=labelZoom} onFocus={onFocusPair}/>) }
      {layers.predictions&&aircraft.map(state=><PredictionLayer key={`prediction-${state.icao24}`}
        aircraft={state} prediction={predictions[state.icao24]} showUncertainty={layers.uncertainty}
        showTicks={layers.predictions} tickZoom={tickZoom} selected={selectedId===state.icao24}/>) }
      <AirportMarker airport={airport}/>
      {aircraft.map(state=><AircraftIcon key={state.icao24} aircraft={{...state,onSelect:()=>onSelect(state)}}
        selected={selectedId===state.icao24} labelMode={labelMode} bands={airport.altitude_bands_m}
        zoom={zoom} labelZoom={labelZoom}/>) }
    </MapContainer>
    {mapNotice&&<div className="map-notice" role="status">{mapNotice}</div>}
    <div className="map-caption">{basemap==='dark'?'CARTO Dark Matter':'OpenStreetMap'} · {airport.icao} · {aircraft.length} active aircraft</div>
  </div>;
}
