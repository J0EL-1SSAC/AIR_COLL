import React, {useEffect, useMemo, useState} from 'react';
import {Circle, CircleMarker, GeoJSON, MapContainer, Marker, Pane, Polygon, Polyline, Popup, TileLayer, Tooltip, useMap} from 'react-leaflet';
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

function AircraftIcon({aircraft, selected, labelMode, bands, zoom, labelZoom, labelAllowed, nearAirport, alertRisk, children}) {
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
    {children}
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

function RunwayLayer({runways, layers, onSelectRunway, activities=[], badgeLimit=3}) {
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
          onEachFeature={(_feature,layer)=>layer.bindTooltip(`Approach corridor · RWY ${end.identifier}`)}
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
          onEachFeature={(_feature,layer)=>layer.bindTooltip(`Runway extended centerline · ${runway.centerline_extension_m/DISTANCE_FORMAT.metersPerNm} NM beyond each end`)}
          style={{color:'var(--color-runway-centerline)',weight:1.2,opacity:.9,dashArray:'8 5'}}
          eventHandlers={{click:()=>onSelectRunway(runway)}}/>
        {[runway.end_a,runway.end_b].map(end=><CircleMarker key={`${runway.identifier}-end-${end.identifier}`} pane="airport-labels"
          center={[end.latitude,end.longitude]} radius={4} pathOptions={{color:'var(--color-runway-outline)',weight:1.5,fillColor:'var(--color-runway-end)',fillOpacity:1}}
          eventHandlers={{click:()=>onSelectRunway(runway)}}>
          <Tooltip permanent direction="top" className="runway-end-label">{end.identifier}</Tooltip>
          {(()=>{const recent=activities.filter(item=>item.runway_end===end.identifier&&item.inferred).slice(0,badgeLimit);return recent.length?<Tooltip permanent direction="bottom" className="inferred-runway-badge">{recent.map(item=>`${item.callsign||item.icao24} ${item.activity_type==='LIKELY_LANDED'?'landed':'departed'} ~${new Date(item.time_ts*1000).toLocaleTimeString()} · inferred`).join(' | ')}</Tooltip>:null;})()}
        </CircleMarker>)}
      </>}
    </React.Fragment>)}
  </>;
}

function MapController({setZoom,focusPair,selectedAircraft,followAircraft}) {
  const map = useMap();
  useEffect(()=>{const update=()=>setZoom(map.getZoom());map.on('zoomend',update);update();return()=>map.off('zoomend',update);},[map,setZoom]);
  useEffect(()=>{
    if (!focusPair) return;
    const {aircraft_a:a,aircraft_b:b}=focusPair.cpa_position;
    map.fitBounds([[a.latitude,a.longitude],[b.latitude,b.longitude]],{padding:[UI.mapFocusPadding,UI.mapFocusPadding],maxZoom:UI.mapFocusMaxZoom});
  },[map,focusPair]);
  useEffect(()=>{
    if (!selectedAircraft?.latitude || !selectedAircraft?.longitude || focusPair || !followAircraft) return;
    const target=[selectedAircraft.latitude,selectedAircraft.longitude];
    if (!map.getBounds().contains(target)) map.panTo(target,{animate:true,duration:UI.mapFocusDurationS});
    if (map.getZoom() < UI.selectionZoom) map.setZoom(UI.selectionZoom,{animate:true});
  },[map,selectedAircraft?.icao24,selectedAircraft?.latitude,selectedAircraft?.longitude,followAircraft,focusPair]);
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

export default function MapView({airport,runways=[],aircraft,predictions,pairs,activeAlerts=[],focusedEventId,selectedId,highlightedIds=[],selectedAircraft,selectedIsReporting=true,followAircraft=true,onFollowAircraft=()=>{},airportActivity=[],nowMs=Date.now(),mode='LIVE',onSelect,onSelectRunway=()=>{},focusPair,onFocusPair,layers,labelMode,basemap,onTileFailure,mapNotice}) {
  aircraft=[...new Map(aircraft.map(state=>[state.icao24,state])).values()];
  const [enrichment,setEnrichment]=useState(null);
  const [currentApproaches,setCurrentApproaches]=useState([]);
  useEffect(()=>{
    let stopped=false;
    const refresh=()=>fetch(`${import.meta.env.VITE_API_BASE||'http://localhost:8000'}/api/approaches`).then(r=>r.ok?r.json():null).then(data=>{if(!stopped)setCurrentApproaches(data?.approaches||[]);}).catch(()=>{if(!stopped)setCurrentApproaches([]);});
    refresh();const timer=window.setInterval(refresh,10000);
    return()=>{stopped=true;window.clearInterval(timer);};
  },[mode]);
  useEffect(()=>{
    let cancelled=false;
    if(!selectedAircraft||!selectedIsReporting){setEnrichment(null);return;}
    setEnrichment(null);
    const base=import.meta.env.VITE_API_BASE||'http://localhost:8000';
    fetch(`${base}/api/aircraft/${encodeURIComponent(selectedAircraft.icao24)}/info`)
      .then(response=>response.ok?response.json():Promise.reject(new Error('Reference enrichment unavailable.')))
      .then(data=>{if(!cancelled)setEnrichment(data);})
      .catch(()=>{if(!cancelled)setEnrichment({airline:'Unknown',route:{available:false,label:'Route unavailable',reason:'Reference lookup unavailable.'},aircraft:{available:false,reason:'Reference lookup unavailable.'}});});
    return()=>{cancelled=true;};
  },[selectedAircraft?.icao24,selectedAircraft?.callsign,selectedIsReporting]);
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
  const selectedApproach=currentApproaches.find(item=>item.icao24===selectedId);
  const selectedRunwayEnd=selectedApproach?runways.flatMap(runway=>[runway.end_a,runway.end_b]).find(end=>end.identifier===selectedApproach.runway_end):null;
  return <div className="map-frame">
    <MapContainer center={[airport.latitude,airport.longitude]} zoom={airport.map_zoom} scrollWheelZoom className="map">
      <MapController setZoom={setZoom} focusPair={focusPair} selectedAircraft={selectedAircraft} followAircraft={followAircraft}/>
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
      <RunwayLayer runways={runways} layers={layers} onSelectRunway={onSelectRunway} activities={airportActivity} badgeLimit={airport.runway_badge_limit}/>
      {layers.approachCorridors&&selectedApproach&&selectedRunwayEnd?.corridor_geojson&&<GeoJSON data={selectedRunwayEnd.corridor_geojson} pane="runway-corridors" style={{color:'var(--color-accent)',weight:3,fillColor:'var(--color-accent)',fillOpacity:.12,dashArray:'5 4'}}/>}
      {selectedApproach&&selectedRunwayEnd?.landing_threshold&&selectedAircraft?.latitude!=null&&<Polyline pane="runway-corridors" positions={[[selectedAircraft.latitude,selectedAircraft.longitude],[selectedRunwayEnd.landing_threshold.latitude,selectedRunwayEnd.landing_threshold.longitude]]} pathOptions={{color:'var(--color-accent)',weight:2.5,dashArray:'4 4'}}><Tooltip sticky>Detected approach · line to RWY {selectedRunwayEnd.identifier} landing threshold</Tooltip></Polyline>}
      {airportActivity.filter(item=>item.inferred&&item.latitude!=null&&item.longitude!=null&&nowMs/1000-item.time_ts<airport.inferred_display_s).map(item=>{
        const age=Math.max(0,nowMs/1000-item.time_ts),opacity=Math.max(0.15,1-age/airport.inferred_display_s);
        return <CircleMarker key={`inferred-${item.activity_type}-${item.icao24}-${item.time_ts}`} center={[item.latitude,item.longitude]} radius={8}
          pathOptions={{color:'var(--color-warning)',weight:2,dashArray:'4 3',fillOpacity:0,opacity}}>
          <Tooltip sticky>Last seen {(item.distance_to_threshold_nm??0).toFixed(1)} NM from RWY {item.runway_end} · {item.activity_type==='LIKELY_LANDED'?'likely landed':'likely departed'} (inferred)</Tooltip>
        </CircleMarker>;
      })}
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
      {layers.estimatedRoute&&selectedIsReporting&&enrichment?.route?.destination?.latitude!=null&&selectedAircraft?.latitude!=null&&<Polyline pane="predictions" positions={[[selectedAircraft.latitude,selectedAircraft.longitude],[enrichment.route.destination.latitude,enrichment.route.destination.longitude]]} pathOptions={{color:'var(--color-estimated-route)',weight:2,dashArray:'2 6',opacity:.9}}><Tooltip sticky>Estimated route to {enrichment.route.destination.icao_code||enrichment.route.destination.iata_code||enrichment.route.destination.ident} · great-circle, not actual or filed path</Tooltip></Polyline>}
      {aircraft.map(state=><AircraftIcon key={state.icao24} aircraft={{...state,onSelect:()=>onSelect(state)}}
        selected={selectedId===state.icao24||highlightedIds.includes(state.icao24)} labelMode={labelMode} bands={airport.altitude_bands_m}
        zoom={zoom} labelZoom={labelZoom} labelAllowed={labelsAllowed.has(state.icao24)}
        nearAirport={state.distance_nm!=null&&state.distance_nm<(airport.label_exclusion_nm??2)} alertRisk={alertLevels[state.icao24]}>
        {selectedId===state.icao24&&<Popup className="aircraft-map-popup" closeButton autoPan offset={[20,-10]} eventHandlers={{remove:()=>onSelect(null)}}><AircraftMapCard aircraft={state} info={enrichment} followAircraft={followAircraft} onFollowAircraft={onFollowAircraft}/></Popup>}
      </AircraftIcon>)}
      {!selectedIsReporting&&selectedAircraft?.latitude!=null&&selectedAircraft?.longitude!=null&&<CircleMarker center={[selectedAircraft.latitude,selectedAircraft.longitude]} radius={11}
        pathOptions={{color:'var(--color-warning)',weight:2,dashArray:'4 3',fillOpacity:0}}>
        <Tooltip permanent direction="top">Last seen · not currently reporting</Tooltip>
        <Popup className="aircraft-map-popup" closeButton autoPan><AircraftMapCard aircraft={selectedAircraft} isReporting={false}/></Popup>
      </CircleMarker>}
    </MapContainer>
    {mapNotice&&<div className="map-notice" role="status">{mapNotice}</div>}
    <div className="map-legend" aria-label="Map symbol legend"><b>Map legend</b><span>✈ Solid icon · observed ADS-B aircraft</span><span>! Badge · stale or low quality</span><span>Blue ring · selected aircraft</span><span>Colored ring and line · active Potential Conflict</span><span>Dashed amber ring · last observed position, inferred activity</span><span>Dashed line · predicted path · solid line · observed trail</span><span>Dotted blue line · estimated great-circle route</span><span>Gray outline · runway · gray funnel · approach corridor</span></div>
    <div className="map-caption">{{dark:'CARTO Dark Matter',light:'CARTO Voyager',osm:'OpenStreetMap'}[basemap]} · {airport.icao} · {aircraft.length} active aircraft</div>
  </div>;
}

function AircraftMapCard({aircraft,info,isReporting=true,followAircraft=true,onFollowAircraft=()=>{}}) {
  const callsign=aircraft.callsign?.trim()||'Unknown callsign';
  const altitude=aircraft.geo_altitude_m??aircraft.baro_altitude_m;
  const route=info?.route;
  const destination=route?.destination;
  const routeDistanceM=destination?.latitude!=null&&destination?.longitude!=null&&aircraft.latitude!=null
    ?L.latLng(aircraft.latitude,aircraft.longitude).distanceTo([destination.latitude,destination.longitude]):null;
  const routeEta=routeDistanceM!=null&&Number(aircraft.velocity_mps)>0?routeDistanceM/Number(aircraft.velocity_mps):null;
  const etaMargin=Number(info?.eta_uncertainty_fraction??0.2);
  const airportLabel=value=>value?`${value.name||'Unknown airport'}, ${value.municipality||'Unknown city'} (${value.icao_code||value.ident||'Unknown ICAO'}${value.iata_code?` / ${value.iata_code}`:''})`:'Unknown · airport reference unavailable';
  return <article className="aircraft-map-card"><header><h2>{callsign}</h2><strong>{info?.airline||'Unknown airline'}</strong><code>{aircraft.icao24}</code><label><input type="checkbox" checked={followAircraft} onChange={event=>onFollowAircraft(event.target.checked)}/> Follow aircraft</label></header>
    <section><b>Reported route (callsign database)</b>{route?.available?<><span>From: {airportLabel(route.origin)}</span><span>To: {airportLabel(route.destination)}</span><span>{route.route_label} · source {route.source}</span>{routeDistanceM!=null&&<span>Estimated great-circle distance {(routeDistanceM/DISTANCE_FORMAT.metersPerNm).toFixed(1)} NM · not actual/filed path</span>}{routeEta!=null&&<span>Estimated ETA window {Math.round(routeEta*(1-etaMargin))}–{Math.round(routeEta*(1+etaMargin))} s based on current ground speed</span>}</>:<span>Route unavailable · {route?.reason||'Waiting for reference lookup.'}</span>}</section>
    <section><b>Aircraft</b><span>{info?.aircraft?.type||'Unknown type'} · {info?.aircraft?.registration||'Unknown registration'}</span><span>Operator: {info?.aircraft?.operator||'Unknown'}</span></section>
    <section><b>Live state</b><span>{altitude==null?'Unknown':`${Math.round(altitude/DISTANCE_FORMAT.metersPerFoot)} ft · ${Math.round(altitude)} m`}</span><span>{aircraft.velocity_mps==null?'Unknown':`${Math.round(aircraft.velocity_mps*DISTANCE_FORMAT.metersPerSecondToKnots)} kt`} · Track {aircraft.track_deg==null?'Unknown':`${Math.round(aircraft.track_deg)}°`}</span><span>Vertical rate {aircraft.vertical_rate_mps==null?'Unknown':`${Math.round(aircraft.vertical_rate_mps*UI.feetPerMinutePerMps)} ft/min`} · Age {Math.round(aircraft.age_s||0)} s</span><span>On ground: {aircraft.on_ground==null?'Unknown':aircraft.on_ground?'Yes':'No'} · Distance: {aircraft.distance_nm==null?'Unknown':`${aircraft.distance_nm.toFixed(1)} NM`}</span><span>Altitude basis: {aircraft.geo_altitude_m!=null?'Geometric':aircraft.baro_altitude_m!=null?'Barometric':'Unknown'} · Quality: {(aircraft.quality_flags||[]).join(', ')||'No flags reported'}</span></section>
    <section><b>Phase of flight</b><span>Not classified</span></section><section><b>Runway and radio</b><span>Unknown · not a radio observation</span></section>
    {!isReporting&&<section><b>Last seen</b><span>Aircraft no longer reporting. Last seen at {Number(aircraft.latitude).toFixed(5)}, {Number(aircraft.longitude).toFixed(5)}.</span></section>}
    <section><b>Source</b><span>Live state: ADS-B · aircraft/route reference: adsbdb. Route may be outdated or incorrect.</span><span>adsbdb credits PlaneBase, David Taylor and Jim Mason; route data is not stored in SQLite.</span></section></article>;
}
