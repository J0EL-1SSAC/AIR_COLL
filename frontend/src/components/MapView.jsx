import React, {useEffect, useMemo, useState} from 'react';
import {Circle, CircleMarker, GeoJSON, MapContainer, Marker, Pane, Polygon, Polyline, TileLayer, Tooltip, useMap} from 'react-leaflet';
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

function AircraftIcon({aircraft, selected, labelMode, bands, zoom, labelZoom, labelAllowed, nearAirport, alertRisk}) {
  const [hovered, setHovered] = useState(false);
  const band = altitudeBand(aircraft, bands);
  const reduced = isReducedQuality(aircraft);
  const shape = band === 'ground'
    ? '<circle class="ground-shape" cx="18" cy="18" r="10"/>'
    : '<path class="aircraft-shape" d="M18 1.8 22 14l10.8 5.4v3L22 20l-1.2 8.2 4.1 2.3v2L18 30.2l-6.9 2.3v-2l4.1-2.3L14 20 3.2 22.4v-3L14 14z"/>';
  const icon = useMemo(() => L.divIcon({
    className: 'aircraft-icon-wrap',
    html: `<div class="aircraft-symbol band-${band}${selected?' selected':''}${reduced?' quality-reduced':''}${alertRisk?` alert-risk-${alertRisk.toLowerCase()}`:''}" style="--track-angle:${Number(aircraft.track_deg||0)}deg"><svg viewBox="0 0 36 36" aria-hidden="true">${shape}</svg>${reduced?'<span class="quality-cue" aria-label="Reduced quality">!</span>':''}</div>`,
    iconSize:[36,36], iconAnchor:[18,18],
  }), [aircraft.icao24, aircraft.track_deg, band, reduced, selected, shape, alertRisk]);
  const labelVisible = labelMode !== 'off' && (selected || hovered || (zoom >= labelZoom && labelAllowed)) && (!nearAirport || selected || hovered);
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

function PredictionLayer({aircraft, prediction, showUncertainty, showTicks, selected, color}) {
  if (!prediction || prediction.status !== 'PREDICTED' || !prediction.points?.length) return null;
  const origin = prediction.origin;
  const path = [[origin.latitude,origin.longitude],...prediction.points.map(point=>[point.latitude,point.longitude])];
  const visibleTicks = showTicks && selected;
  return <>
    <Polyline pane="prediction-halo" positions={[[aircraft.latitude,aircraft.longitude],[origin.latitude,origin.longitude]]}
    pathOptions={{color:'var(--color-prediction-halo)',weight:UI.predictionHaloWidth,opacity:.9,lineCap:'round'}}/>
    <Polyline pane="prediction-halo" positions={path}
      pathOptions={{color:'var(--color-prediction-halo)',weight:UI.predictionHaloWidth,opacity:.9,dashArray:'7 7',lineCap:'round'}}/>
      <Polyline pane="predictions" positions={[[aircraft.latitude,aircraft.longitude],[origin.latitude,origin.longitude]]}
      pathOptions={{color,weight:UI.predictionWidth,opacity:1,dashArray:'2 5',lineCap:'round'}}/>
    <Polyline pane="predictions" positions={path}
      pathOptions={{color,weight:UI.predictionWidth,opacity:1,dashArray:'7 7',lineCap:'round'}}/>
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

const RISK_COLORS = {LOW:'var(--color-risk-low)',MEDIUM:'var(--color-risk-medium)',HIGH:'var(--color-risk-high)',CRITICAL:'var(--color-risk-critical)'};

function AlertLayer({event,currentAircraft,onFocus}) {
  const a=currentAircraft[event.aircraft_1.icao24], b=currentAircraft[event.aircraft_2.icao24];
  const color=RISK_COLORS[event.current_risk]||RISK_COLORS.LOW;
  const cpaA=[event.cpa_aircraft_a_lat,event.cpa_aircraft_a_lon];
  const cpaB=[event.cpa_aircraft_b_lat,event.cpa_aircraft_b_lon];
  const midpoint=[event.cpa_lat,event.cpa_lon];
  const handler={click:()=>onFocus(event)};
  if(!Number.isFinite(midpoint[0])||!Number.isFinite(midpoint[1]))return null;
  return <>
    {a&&<CircleMarker pane="alerts" center={[a.latitude,a.longitude]} radius={UI.alertRingRadius} pathOptions={{color,weight:2,fillOpacity:0,className:`alert-pulse risk-stroke-${event.current_risk.toLowerCase()}`}} eventHandlers={handler}/>}
    {b&&<CircleMarker pane="alerts" center={[b.latitude,b.longitude]} radius={UI.alertRingRadius} pathOptions={{color,weight:2,fillOpacity:0,className:`alert-pulse risk-stroke-${event.current_risk.toLowerCase()}`}} eventHandlers={handler}/>}
    {a&&b&&<Polyline pane="alerts" positions={[[a.latitude,a.longitude],[b.latitude,b.longitude]]} pathOptions={{color,weight:UI.alertLineWidth,opacity:.9}} eventHandlers={handler}/>}
    {Number.isFinite(cpaA[0])&&Number.isFinite(cpaB[0])&&<Polyline pane="alerts" positions={[cpaA,cpaB]} pathOptions={{color,weight:UI.alertLineWidth,opacity:1,dashArray:'5 4'}} eventHandlers={handler}/>}
    <CircleMarker pane="alerts" center={midpoint} radius={UI.alertCpaRadius} pathOptions={{color:'var(--color-marker-outline)',weight:2,fillColor:color,fillOpacity:1}} eventHandlers={handler}>
      <Tooltip permanent direction="top" className={`alert-map-label risk-label-${event.current_risk.toLowerCase()}`}>
        Potential Aircraft Conflict · {event.current_risk} · CPA {Math.round(event.time_to_cpa_s??0)} s
      </Tooltip>
    </CircleMarker>
  </>;
}

function AirportMarker({airport}) {
  const icon = useMemo(()=>L.divIcon({className:'airport-icon-wrap',html:'<div class="airport-symbol"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M21 16v-2l-8-5V3.5a1.5 1.5 0 0 0-3 0V9l-8 5v2l8-2.5V19l-2 1.5V22l3.5-1 3.5 1v-1.5L13 19v-5.5z"/></svg></div>',iconSize:[30,30],iconAnchor:[15,15]}),[]);
  return <Marker position={[airport.latitude,airport.longitude]} icon={icon} pane="airports"><Tooltip permanent direction="right" className="airport-label">{airport.icao}</Tooltip></Marker>;
}

function RunwayLayer({runways, layers, onSelectRunway}) {
  return <>
    {runways.map(runway=><React.Fragment key={`runway-${runway.identifier}`}>
      {layers.runwayBuffer&&runway.buffer_geojson&&<>
        <GeoJSON data={runway.buffer_geojson} pane="runway-buffers"
          style={{color:'var(--color-runway-casing)',weight:4,opacity:.8,fillOpacity:0,dashArray:'5 5'}}/>
        <GeoJSON data={runway.buffer_geojson} pane="runway-buffers"
          style={{color:'var(--color-runway-buffer)',weight:1.7,opacity:1,fillColor:'var(--color-runway-buffer)',fillOpacity:.05,dashArray:'5 5'}}/>
      </>}
      {layers.approachCorridors&&[runway.end_a,runway.end_b].map(end=><React.Fragment key={`${runway.identifier}-corridor-${end.identifier}`}>
        <GeoJSON data={end.corridor_geojson} pane="runway-corridors"
          style={{color:'var(--color-runway-casing)',weight:3.5,opacity:.8,fillColor:'var(--color-runway-casing)',fillOpacity:.025,dashArray:'3 5'}}/>
        <GeoJSON data={end.corridor_geojson} pane="runway-corridors"
          style={{color:'var(--color-runway-corridor)',weight:1,opacity:.8,fillColor:'var(--color-runway-corridor)',fillOpacity:.09,dashArray:'3 5'}}/>
        <Polyline pane="runway-corridors" positions={[
          [end.latitude,end.longitude],
          [((end.corridor_geojson.geometry.coordinates[0][2][1]+end.corridor_geojson.geometry.coordinates[0][3][1])/2),
           ((end.corridor_geojson.geometry.coordinates[0][2][0]+end.corridor_geojson.geometry.coordinates[0][3][0])/2)],
        ]} pathOptions={{color:'var(--color-runway-corridor-line)',weight:1.5,opacity:.75,dashArray:'4 5'}}/>
      </React.Fragment>)}
      {layers.runways&&<>
        <GeoJSON data={runway.core_geojson} pane="runway-core"
          style={{color:'var(--color-runway-casing)',weight:5,opacity:1,fillColor:'var(--color-runway-pavement)',fillOpacity:.48}}
          eventHandlers={{click:()=>onSelectRunway(runway)}}/>
        <GeoJSON data={runway.core_geojson} pane="runway-core"
          style={{color:'var(--color-runway-outline)',weight:2.4,opacity:1,fillOpacity:0}}
          eventHandlers={{click:()=>onSelectRunway(runway)}}/>
        <GeoJSON data={runway.centerline_geojson} pane="runway-core"
          style={{color:'var(--color-runway-centerline)',weight:1.2,opacity:.9,dashArray:'8 5'}}
          eventHandlers={{click:()=>onSelectRunway(runway)}}/>
        {[runway.end_a,runway.end_b].map(end=><CircleMarker key={`${runway.identifier}-end-${end.identifier}`} pane="airport-labels"
          center={[end.latitude,end.longitude]} radius={4} pathOptions={{color:'var(--color-runway-outline)',weight:1.5,fillColor:'var(--color-runway-end)',fillOpacity:1}}
          eventHandlers={{click:()=>onSelectRunway(runway)}}>
          <Tooltip permanent direction="top" className="runway-end-label">{end.identifier}</Tooltip>
        </CircleMarker>)}
      </>}
    </React.Fragment>)}
  </>;
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

export default function MapView({airport,runways=[],aircraft,predictions,pairs,activeAlerts=[],focusedEventId,selectedId,onSelect,onSelectRunway=()=>{},focusPair,onFocusPair,layers,labelMode,basemap,onTileFailure,mapNotice}) {
  const bandColors = ALTITUDE_BANDS;
  const stateById = Object.fromEntries(aircraft.map(state=>[state.icao24,state]));
  const [zoom,setZoom] = useState(airport.map_zoom);
  const labelZoom = airport.labels_min_zoom ?? UI.labelZoomFallback;
  const thresholds = airport.cpa_display || {near_nm:1.5,amber_nm:3};
  const ring = airport.radius_ring.map(([longitude,latitude])=>[latitude,longitude]);
  const predictionColor=basemap==='light'?'var(--color-prediction-light)':'var(--color-prediction)';
  const eligiblePairs = pairs.filter(pair=>pair.converging&&!pair.cpa_in_past);
  const liveAlerts=activeAlerts.filter(event=>event.status==='ACTIVE'||event.status==='ESCALATED');
  const alertLevels={};
  for(const event of liveAlerts){alertLevels[event.aircraft_1.icao24]=event.current_risk;alertLevels[event.aircraft_2.icao24]=event.current_risk;}
  const labelCells=new Set();
  const labelsAllowed=new Set();
  for(const state of aircraft){
    const cell=zoom<(labelZoom+2)?`${Math.floor(state.latitude/0.025)}:${Math.floor(state.longitude/0.025)}`:state.icao24;
    if(!labelCells.has(cell)){labelCells.add(cell);labelsAllowed.add(state.icao24);}
  }
  const focusEvent=event=>onFocusPair({pair_key:event.pair_key,aircraft_a:event.aircraft_1,aircraft_b:event.aircraft_2,
    cpa_position:{aircraft_a:{latitude:event.cpa_aircraft_a_lat,longitude:event.cpa_aircraft_a_lon},
      aircraft_b:{latitude:event.cpa_aircraft_b_lat,longitude:event.cpa_aircraft_b_lon}}});
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
      <Pane name="uncertainty" style={{zIndex:UI.panes.uncertainty}}/><Pane name="alerts" style={{zIndex:UI.panes.alerts}}/>
      <Pane name="runway-buffers" style={{zIndex:UI.panes.runwayBuffers}}/><Pane name="runway-corridors" style={{zIndex:UI.panes.runwayCorridors}}/>
      <Pane name="runway-core" style={{zIndex:UI.panes.runwayCore}}/><Pane name="airports" style={{zIndex:UI.panes.airports}}/>
      <Pane name="airport-labels" style={{zIndex:UI.panes.airportLabels}}/>
      <TileLayer key={basemap} url={UI.tileUrls[basemap]} attribution={UI.tileAttribution[basemap]}
        eventHandlers={{tileerror:onTileFailure}}/>
      {layers.radius&&<Polygon positions={ring} pane="trails" pathOptions={{color:'var(--color-accent)',weight:1.5,opacity:.7,fillOpacity:.015,dashArray:'6 7'}}/>}
      <RunwayLayer runways={runways} layers={layers} onSelectRunway={onSelectRunway}/>
      {layers.trails&&aircraft.map(state=><TrailLayer key={`trail-${state.icao24}`} aircraft={state}
        color={bandColors[altitudeBand(state,airport.altitude_bands_m)]}
        oldestOpacity={airport.trail_oldest_opacity??UI.oldestTrailOpacityFallback}
        newestOpacity={airport.trail_newest_opacity??UI.newestTrailOpacityFallback}/>) }
      {layers.closestApproaches&&eligiblePairs.map(pair=><PairLayer key={pair.pair_key} pair={pair}
        color={pairColor(pair)} currentAircraft={stateById} focused={focusPair?.pair_key===pair.pair_key}
        showLabel={zoom>=labelZoom} onFocus={onFocusPair}/>) }
      {layers.predictions&&aircraft.map(state=><PredictionLayer key={`prediction-${state.icao24}`}
        aircraft={state} prediction={predictions[state.icao24]} showUncertainty={layers.uncertainty}
        showTicks={layers.predictions} selected={selectedId===state.icao24} color={predictionColor}/>) }
      {liveAlerts.map(event=><AlertLayer key={event.event_id} event={event} currentAircraft={stateById}
        focused={focusedEventId===event.event_id} onFocus={focusEvent}/>) }
      <AirportMarker airport={airport}/>
      {aircraft.map(state=><AircraftIcon key={state.icao24} aircraft={{...state,onSelect:()=>onSelect(state)}}
        selected={selectedId===state.icao24} labelMode={labelMode} bands={airport.altitude_bands_m}
        zoom={zoom} labelZoom={labelZoom} labelAllowed={labelsAllowed.has(state.icao24)}
        nearAirport={state.distance_nm!=null&&state.distance_nm<(airport.label_exclusion_nm??2)} alertRisk={alertLevels[state.icao24]}/>) }
    </MapContainer>
    {mapNotice&&<div className="map-notice" role="status">{mapNotice}</div>}
    <div className="map-caption">{basemap==='dark'?'CARTO Dark Matter':'OpenStreetMap'} · {airport.icao} · {aircraft.length} active aircraft</div>
  </div>;
}
