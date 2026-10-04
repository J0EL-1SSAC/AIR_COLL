import React,{useEffect,useRef,useState} from 'react';
import {createRoot} from 'react-dom/client';
import 'leaflet/dist/leaflet.css';
import './style.css';
import {UI} from './config';
import DashboardHeader from './components/DashboardHeader';
import MapView from './components/MapView';
import ResearchSidePanel from './components/ResearchSidePanel';

const apiBase=import.meta.env.VITE_API_BASE||'http://localhost:8000';
const wsUrl=apiBase.replace(/^http/,'ws')+'/ws/live';
const defaultLayers={trails:true,predictions:true,uncertainty:true,closestApproaches:true,radius:true,runways:true,runwayBuffer:false,approachCorridors:false,estimatedRoute:true};
const storedBasemap=()=>{try{const value=localStorage.getItem('aircol-basemap');return ['dark','osm'].includes(value)?value:'light';}catch{return 'light';}};
const storedValue=(key,fallback)=>{try{const value=localStorage.getItem(key);return value?JSON.parse(value):fallback;}catch{return fallback;}};

function App(){
  const [airport,setAirport]=useState(null);
  const [runways,setRunways]=useState([]);
  const [selectedRunway,setSelectedRunway]=useState(null);
  const [runwayMessage,setRunwayMessage]=useState('Loading runway data…');
  const [aircraft,setAircraft]=useState([]);
  const [predictions,setPredictions]=useState({});
  const [pairs,setPairs]=useState([]);
  const [activeAlerts,setActiveAlerts]=useState([]);
  const [eventHistory,setEventHistory]=useState([]);
  const [riskConfig,setRiskConfig]=useState(null);
  const [weather,setWeather]=useState(null);
  const [frequencyData,setFrequencyData]=useState(null);
  const [airportActivity,setAirportActivity]=useState([]);
  const [selectedEvent,setSelectedEvent]=useState(null);
  const [alertRefresh,setAlertRefresh]=useState(0);
  const [alertsLoading,setAlertsLoading]=useState(false);
  const [alertFilters,setAlertFilters]=useState({risk:'',status:'',mode:'CURRENT',range:'24h',start:'',end:''});
  const [historyMessage,setHistoryMessage]=useState('');
  const [alertView,setAlertView]=useState('active');
  const [focusedEventId,setFocusedEventId]=useState(null);
  const [pairThresholds,setPairThresholds]=useState({near_nm:1.5,amber_nm:3});
  const [status,setStatus]=useState('CONNECTING');
  const [message,setMessage]=useState('Connecting to the live collector.');
  const [mode,setMode]=useState('LIVE');
  const [pipelineTimeSeconds,setPipelineTimeSeconds]=useState(()=>Date.now()/1000);
  const [counts,setCounts]=useState({aircraft_count:0,low_or_ground_count:0});
  const [selectedId,setSelectedId]=useState(null);
  const [highlightedIds,setHighlightedIds]=useState([]);
  const [followAircraft,setFollowAircraft]=useState(true);
  const [lastKnownById,setLastKnownById]=useState({});
  const [focusedPair,setFocusedPair]=useState(null);
  const [mapLayers,setMapLayers]=useState(()=>({...defaultLayers,...storedValue('aircol-layers',{})}));
  const [labelMode,setLabelMode]=useState(()=>storedValue('aircol-label-mode','callsign'));
  const [basemap,setBasemap]=useState(storedBasemap);
  const [mapNotice,setMapNotice]=useState('');
  const [groundOnly,setGroundOnly]=useState(false);
  const [leftCollapsed,setLeftCollapsed]=useState(false);
  const [rightCollapsed,setRightCollapsed]=useState(false);
  const [utcNow,setUtcNow]=useState('--:--:--');
  const [lastReceivedAt,setLastReceivedAt]=useState(null);
  const [ageNow,setAgeNow]=useState(Date.now());
  const [predictionError,setPredictionError]=useState('');
  const [pairsError,setPairsError]=useState('');
  const [replaySpeed,setReplaySpeed]=useState(null);
  const [replayBusy,setReplayBusy]=useState(false);
  const [replayMessage,setReplayMessage]=useState('Replay uses only observations recorded from the live feed.');
  const [alertToast,setAlertToast]=useState('');
  const refreshSequence=useRef(0);

  useEffect(()=>{
    fetch(`${apiBase}/api/airport`).then(response=>response.ok?response.json():Promise.reject(new Error('Backend unavailable.')))
      .then(data=>{setAirport(data);setPairThresholds(data.cpa_display||{near_nm:1.5,amber_nm:3});})
      .catch(error=>{setStatus('DISCONNECTED');setMessage(`${error.message} No live data.`);});
  },[]);

  useEffect(()=>{
    fetch(`${apiBase}/api/runways`).then(async response=>{
      const body=await response.json();
      if(!response.ok)throw new Error(body.detail||`Runway request failed (${response.status}).`);
      return body;
    }).then(data=>{setRunways(data.runways||[]);setRunwayMessage('');})
      .catch(error=>{setRunways([]);setRunwayMessage(`Runway layers unavailable: ${error.message}`);});
  },[]);

  useEffect(()=>{
    fetch(`${apiBase}/api/risk/config`).then(response=>response.ok?response.json():Promise.reject(new Error('Risk settings unavailable.')))
      .then(setRiskConfig).catch(()=>setRiskConfig(null));
  },[mode]);

  useEffect(()=>{
    if(!airport)return undefined;
    let stopped=false;
    const refresh=()=>fetch(`${apiBase}/api/weather`).then(response=>response.ok?response.json():Promise.reject(new Error('Weather service unavailable.')))
      .then(data=>{if(!stopped)setWeather(data);})
      .catch(()=>{if(!stopped)setWeather({status:'NO_DATA',message:'Live weather is not available from the backend.',metar:{status:'NOT_AVAILABLE'},taf:{status:'NOT_AVAILABLE'}});});
    refresh();
    const timer=window.setInterval(refresh,Number(airport.weather?.refresh_interval_s||60)*1000);
    fetch(`${apiBase}/api/frequencies`).then(response=>response.ok?response.json():Promise.reject(new Error('Frequency reference unavailable.')))
      .then(data=>{if(!stopped)setFrequencyData(data);}).catch(()=>{if(!stopped)setFrequencyData({available:false,error:'Local frequency reference is not available.'});});
    return()=>{stopped=true;window.clearInterval(timer);};
  },[airport]);

  useEffect(()=>{
    if(!airport)return undefined;
    let stopped=false;
    const refresh=()=>fetch(`${apiBase}/api/airport-activity?mode=${mode}&limit=30`).then(r=>r.ok?r.json():Promise.reject(new Error('Airport activity unavailable.')))
      .then(data=>{if(!stopped)setAirportActivity(data.activities||[]);}).catch(()=>{if(!stopped)setAirportActivity([]);});
    refresh();const timer=window.setInterval(refresh,15000);
    return()=>{stopped=true;window.clearInterval(timer);};
  },[airport,mode]);

  useEffect(()=>{
    const timer=window.setInterval(()=>{const now=new Date();setUtcNow(now.toISOString().slice(11,19));setAgeNow(Date.now());},UI.updateAgeTickMs);
    return()=>window.clearInterval(timer);
  },[]);

  useEffect(()=>{
    const onKeyDown=event=>{
      if(event.key==='Escape'&&selectedId){setSelectedId(null);return;}
      if(event.key==='/'&&!event.target?.matches?.('input,textarea,select')){
        event.preventDefault();document.querySelector('input[placeholder="Callsign or ICAO24"]')?.focus();
      }
    };
    window.addEventListener('keydown',onKeyDown);
    return()=>window.removeEventListener('keydown',onKeyDown);
  },[selectedId]);

  useEffect(()=>{
    if(!airport)return undefined;
    let socket,retryTimer,stopped=false,delay=airport.reconnect_initial_ms||1000;
    const connect=()=>{
      if(stopped)return;
      socket=new WebSocket(wsUrl);
      socket.onopen=()=>{delay=airport.reconnect_initial_ms||1000;};
      socket.onmessage=event=>{
        try{
          const payload=JSON.parse(event.data);
          if(payload.type==='alert'){
            if(payload.data?.sub_event==='opened'){
              const record=payload.data?.event;
              setAlertToast(`Potential Aircraft Conflict opened · ${record?.aircraft_1?.callsign||record?.aircraft_1_icao24||'aircraft pair'} / ${record?.aircraft_2?.callsign||record?.aircraft_2_icao24||''}`);
              window.setTimeout(()=>setAlertToast(''),7000);
            }
            setAlertRefresh(value=>value+1);
            return;
          }
          setMode(payload.mode||'LIVE');if(payload.ts)setPipelineTimeSeconds(Date.parse(payload.ts)/1000);setStatus(payload.source_status||'NO_DATA');
          setMessage(payload.data?.message||'No source status message.');
          setAircraft(payload.data?.aircraft||[]);setCounts(payload.data||{});
          setLastKnownById(previous=>({...previous,...Object.fromEntries((payload.data?.aircraft||[]).map(item=>[item.icao24,item]))}));
          setLastReceivedAt(Date.now());
          setAlertRefresh(value=>value+1);
        }catch{setMessage('Received an unreadable data update.');}
      };
      socket.onclose=()=>{
        setStatus('DISCONNECTED');setMessage('Backend disconnected. No live data. Reconnecting…');
        setAircraft([]);setPredictions({});setPairs([]);setSelectedId(null);setFocusedPair(null);setLastReceivedAt(null);
        retryTimer=window.setTimeout(connect,delay);delay=Math.min(delay*2,airport.reconnect_max_ms||15000);
      };
      socket.onerror=()=>socket.close();
    };
    connect();
    return()=>{stopped=true;window.clearTimeout(retryTimer);socket?.close();};
  },[airport]);

  useEffect(()=>{
    if(!airport)return undefined;
    let stopped=false;
    setAlertsLoading(true);
    const params=new URLSearchParams({limit:'100'});
    if(alertFilters.mode!=='CURRENT')params.set('mode',alertFilters.mode);
    if(alertFilters.risk)params.set('risk',alertFilters.risk);
    if(alertFilters.status)params.set('status',alertFilters.status);
    if(alertFilters.range==='custom'){
      if(alertFilters.start)params.set('start',String(new Date(alertFilters.start).getTime()/1000));
      if(alertFilters.end)params.set('end',String(new Date(alertFilters.end).getTime()/1000));
    } else if(alertFilters.range!=='all') {
      const hours=alertFilters.range==='1h'?1:alertFilters.range==='6h'?6:24;
      const endTime=mode==='REPLAY'?pipelineTimeSeconds:Date.now()/1000;
      params.set('start',String(endTime-hours*3600));
      params.set('end',String(endTime));
    }
    Promise.all([
      fetch(`${apiBase}/api/alerts/active?mode=${mode}`).then(r=>r.ok?r.json():Promise.reject(new Error('Active alerts unavailable.'))),
      fetch(`${apiBase}/api/events?${params}`).then(r=>r.ok?r.json():Promise.reject(new Error('Event history unavailable.'))),
    ]).then(([active,history])=>{
      if(stopped)return;
      setActiveAlerts(active.alerts||[]);setEventHistory(history.events||[]);setHistoryMessage(history.message||'');
    }).catch(()=>{if(!stopped){setActiveAlerts([]);setEventHistory([]);}})
      .finally(()=>{if(!stopped)setAlertsLoading(false);});
    return()=>{stopped=true;};
  },[airport,mode,pipelineTimeSeconds,alertRefresh,alertFilters.risk,alertFilters.status,alertFilters.mode,alertFilters.range,alertFilters.start,alertFilters.end]);

  useEffect(()=>{
    if(!airport)return undefined;
    const sequence=++refreshSequence.current;
    Promise.all([
      fetch(`${apiBase}/api/predictions`).then(response=>response.ok?response.json():Promise.reject(new Error(`Prediction request failed (${response.status}).`))),
      fetch(`${apiBase}/api/pairs`).then(response=>response.ok?response.json():Promise.reject(new Error(`Closest-approach request failed (${response.status}).`))),
    ]).then(([predictionData,pairData])=>{
      if(sequence!==refreshSequence.current)return;
      if(predictionData.mode===mode){setPredictions(Object.fromEntries(predictionData.predictions.map(item=>[item.icao24,item])));setPredictionError('');}
      if(pairData.mode===mode){setPairs(pairData.pairs);setPairsError('');}
    }).catch(error=>{
      if(sequence===refreshSequence.current){setPredictionError(error.message);setPairsError(error.message);}
    });
    return()=>{refreshSequence.current+=1;};
  },[airport,aircraft,mode]);

  const selectedAircraftRaw=selectedId?aircraft.find(item=>item.icao24===selectedId)||lastKnownById[selectedId]||null:null;
  const selectedIsReporting=Boolean(selectedId&&aircraft.some(item=>item.icao24===selectedId));
  const selectedAircraft=selectedAircraftRaw?{...selectedAircraftRaw,age_s:selectedAircraftRaw.age_s+(mode==='LIVE'&&lastReceivedAt!=null?(ageNow-lastReceivedAt)/1000:0)}:null;
  const visibleAircraft=groundOnly?aircraft.filter(item=>item.on_ground===true):aircraft;
  const predictionCounts={predicted:Object.values(predictions).filter(item=>item.status==='PREDICTED').length,
    skipped:Object.values(predictions).filter(item=>item.status==='SKIPPED').length};
  const lastUpdateAge=lastReceivedAt==null?null:Math.max(0,Math.floor((ageNow-lastReceivedAt)/1000));

  function updateLayer(name,value){setMapLayers(current=>{const next={...current,[name]:value};try{localStorage.setItem('aircol-layers',JSON.stringify(next));}catch{}return next;});}
  function updateLabelMode(value){setLabelMode(value);try{localStorage.setItem('aircol-label-mode',JSON.stringify(value));}catch{}}
  function changeBasemap(value){setBasemap(value);setMapNotice('');try{localStorage.setItem('aircol-basemap',value);}catch{}}
  function tileFailure(){
    if(basemap!=='osm'){
      setBasemap('osm');setMapNotice('Selected map tiles could not load. Switched to OpenStreetMap.');
      try{localStorage.setItem('aircol-basemap','osm');}catch{}
    } else {
      setMapNotice('OpenStreetMap tiles could not load. Map tiles require internet access; aircraft data status is independent.');
    }
  }
  async function startReplay(session=null,chosenStart=null,speed=null){
    setReplayBusy(true);
    try{
      const response=await fetch(`${apiBase}/api/replay/start`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({start_time:chosenStart??session?.start_time,end_time:session?.end_time,speed:Number(speed??replaySpeed??airport.replay.default_speed)})});
      const body=await response.json();if(!response.ok)throw new Error(body.detail||`Replay request failed (${response.status}).`);
      setReplayMessage(`Replaying ${body.cycles} recorded polls from the selected session at ${body.speed}×.`);
    }catch(error){setReplayMessage(`Replay unavailable: ${error.message}`);}
    finally{setReplayBusy(false);}
  }
  async function stopReplay(){
    setReplayBusy(true);
    try{const response=await fetch(`${apiBase}/api/replay/stop`,{method:'POST'});if(!response.ok)throw new Error('Replay stop request failed.');setReplayMessage('Replay stopped. Showing the live collector.');}
    catch(error){setReplayMessage(error.message);}
    finally{setReplayBusy(false);}
  }
  function focusPair(pair){setSelectedRunway(null);setSelectedEvent(null);setFocusedEventId(null);setFocusedPair(pair);setHighlightedIds([pair.aircraft_a.icao24,pair.aircraft_b.icao24]);setSelectedId(pair.aircraft_a.icao24);}
  function focusAlert(event,inspect=false){
    setSelectedRunway(null);
    setHighlightedIds([event.aircraft_1.icao24,event.aircraft_2.icao24]);
    setSelectedId(event.aircraft_1.icao24);setFocusedEventId(event.event_id);
    setSelectedEvent(inspect?event:null);
    setFocusedPair({pair_key:event.pair_key,cpa_position:{aircraft_a:{latitude:event.cpa_aircraft_a_lat,longitude:event.cpa_aircraft_a_lon},
      aircraft_b:{latitude:event.cpa_aircraft_b_lat,longitude:event.cpa_aircraft_b_lon}}});
  }
  function updateAlertFilter(key,value){
    if(key==='reload'){setAlertRefresh(current=>current+1);return;}
    setAlertFilters(current=>({...current,[key]:value}));
  }
  function selectAircraftId(id){setSelectedEvent(null);setFocusedEventId(null);setFocusedPair(null);setHighlightedIds([]);setSelectedId(id);}
  function focusFacility(ids){setSelectedEvent(null);setFocusedPair(null);setFocusedEventId(null);setHighlightedIds(ids);if(ids[0])setSelectedId(ids[0]);}

  if(!airport)return <main className="loading-screen"><h1>AIR_COL</h1><div className="skeleton"/><div className="skeleton"/><p>{message}</p>
    <p className="disclaimer">Research prototype. Not ATC, TCAS/ACAS or a certified safety system. Public ADS-B may be delayed, incomplete or inaccurate, especially at low altitude and on the ground.</p></main>;

  return <main className="app-shell">
    <DashboardHeader airport={airport} mode={mode} status={status} message={message} utcNow={utcNow}
      alertCounts={activeAlerts.reduce((counts,item)=>({...counts,[item.current_risk]:(counts[item.current_risk]||0)+1}),{})}
      riskProfile={riskConfig?.active_profile}
      lastUpdateAge={lastUpdateAge} onToggleLeft={()=>setLeftCollapsed(value=>!value)} onToggleRight={()=>setRightCollapsed(value=>!value)}/>
    <div className="operations-layout">
      {!leftCollapsed&&<ResearchSidePanel airport={airport} counts={counts} predictionCounts={predictionCounts} mapLayers={mapLayers} runwayMessage={runwayMessage} weather={weather} frequencyData={frequencyData}
        onLayerChange={updateLayer} labelMode={labelMode} onLabelMode={updateLabelMode} basemap={basemap} onBasemap={changeBasemap}
        groundOnly={groundOnly} onGroundOnly={setGroundOnly} aircraft={visibleAircraft} selectedAircraft={selectedAircraft}
        selectedPrediction={selectedAircraft?predictions[selectedAircraft.icao24]:null} pairs={pairs} thresholds={pairThresholds}
        onSelectAircraft={state=>selectAircraftId(state.icao24)} onSelectId={selectAircraftId} onFocusPair={focusPair}
        alerts={activeAlerts} history={eventHistory} airportActivity={airportActivity} onFocusFacility={focusFacility} riskProfile={riskConfig?.active_profile} mode={mode} alertFilters={alertFilters}
        selectedEvent={selectedEvent}
    onAlertFilterChange={updateAlertFilter} historyMessage={historyMessage} onFocusAlert={event=>focusAlert(event,false)} onInspectEvent={event=>focusAlert(event,true)}
        alertsLoading={alertsLoading} alertView={alertView} setAlertView={setAlertView} replaySpeed={replaySpeed??airport.replay.default_speed}
        onStartReplay={startReplay} onStopReplay={stopReplay} onCollapse={()=>setLeftCollapsed(true)}/>}
      <MapView airport={airport} runways={runways} aircraft={visibleAircraft} predictions={predictions} pairs={pairs}
        activeAlerts={activeAlerts} focusedEventId={focusedEventId}
        selectedId={selectedId} highlightedIds={highlightedIds} selectedAircraft={selectedAircraft} selectedIsReporting={selectedIsReporting} airportActivity={airportActivity} nowMs={ageNow} mode={mode} followAircraft={followAircraft} onFollowAircraft={setFollowAircraft} onSelect={state=>{setSelectedRunway(null);setFocusedPair(null);setFocusedEventId(null);setHighlightedIds([]);setSelectedId(state?.icao24??null);}} onSelectRunway={runway=>{setSelectedId(null);setSelectedEvent(null);setSelectedRunway(runway);}} focusPair={focusedPair}
        onFocusPair={focusPair} layers={mapLayers} labelMode={labelMode} basemap={basemap} onTileFailure={tileFailure} mapNotice={mapNotice}/>
    </div>
    {alertToast&&<div className="alert-toast" role="status">{alertToast}</div>}
    <footer className="disclaimer">Research prototype. Not ATC, TCAS/ACAS or a certified safety system. Public ADS-B data can be delayed, incomplete or inaccurate, especially at low altitude and on the ground.{pairsError&&<span> · Pair data unavailable.</span>}{predictionError&&<span> · Prediction data unavailable.</span>}</footer>
  </main>;
}

createRoot(document.getElementById('root')).render(<App/>);
