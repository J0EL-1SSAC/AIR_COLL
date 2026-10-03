import React,{useEffect,useRef,useState} from 'react';
import {createRoot} from 'react-dom/client';
import 'leaflet/dist/leaflet.css';
import './style.css';
import {UI} from './config';
import DashboardHeader from './components/DashboardHeader';
import FiltersPanel from './components/FiltersPanel';
import DetailsPanel from './components/DetailsPanel';
import MapView from './components/MapView';
import BottomPanel from './components/BottomPanel';

const apiBase=import.meta.env.VITE_API_BASE||'http://localhost:8000';
const wsUrl=apiBase.replace(/^http/,'ws')+'/ws/live';
const defaultLayers={trails:true,predictions:true,uncertainty:true,closestApproaches:true,radius:true};
const storedBasemap=()=>{try{return localStorage.getItem('aircol-basemap')==='light'?'light':'dark';}catch{return 'dark';}};

function App(){
  const [airport,setAirport]=useState(null);
  const [aircraft,setAircraft]=useState([]);
  const [predictions,setPredictions]=useState({});
  const [pairs,setPairs]=useState([]);
  const [pairThresholds,setPairThresholds]=useState({near_nm:1.5,amber_nm:3});
  const [status,setStatus]=useState('CONNECTING');
  const [message,setMessage]=useState('Connecting to the live collector.');
  const [mode,setMode]=useState('LIVE');
  const [counts,setCounts]=useState({aircraft_count:0,low_or_ground_count:0});
  const [selectedId,setSelectedId]=useState(null);
  const [focusedPair,setFocusedPair]=useState(null);
  const [mapLayers,setMapLayers]=useState(defaultLayers);
  const [labelMode,setLabelMode]=useState('callsign');
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
  const refreshSequence=useRef(0);

  useEffect(()=>{
    fetch(`${apiBase}/api/airport`).then(response=>response.ok?response.json():Promise.reject(new Error('Backend unavailable.')))
      .then(data=>{setAirport(data);setPairThresholds(data.cpa_display||{near_nm:1.5,amber_nm:3});})
      .catch(error=>{setStatus('DISCONNECTED');setMessage(`${error.message} No live data.`);});
  },[]);

  useEffect(()=>{
    const timer=window.setInterval(()=>{const now=new Date();setUtcNow(now.toISOString().slice(11,19));setAgeNow(Date.now());},UI.updateAgeTickMs);
    return()=>window.clearInterval(timer);
  },[]);

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
          setMode(payload.mode||'LIVE');setStatus(payload.source_status||'NO_DATA');
          setMessage(payload.data?.message||'No source status message.');
          setAircraft(payload.data?.aircraft||[]);setCounts(payload.data||{});
          setLastReceivedAt(Date.now());
          setSelectedId(current=>current&&(payload.data?.aircraft||[]).some(a=>a.icao24===current)?current:null);
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

  const selectedAircraftRaw=selectedId?aircraft.find(item=>item.icao24===selectedId)||null:null;
  const selectedAircraft=selectedAircraftRaw?{...selectedAircraftRaw,age_s:selectedAircraftRaw.age_s+(mode==='LIVE'&&lastReceivedAt!=null?(ageNow-lastReceivedAt)/1000:0)}:null;
  const visibleAircraft=groundOnly?aircraft.filter(item=>item.on_ground===true):aircraft;
  const predictionCounts={predicted:Object.values(predictions).filter(item=>item.status==='PREDICTED').length,
    skipped:Object.values(predictions).filter(item=>item.status==='SKIPPED').length};
  const lastUpdateAge=lastReceivedAt==null?null:Math.max(0,Math.floor((ageNow-lastReceivedAt)/1000));

  function updateLayer(name,value){setMapLayers(current=>({...current,[name]:value}));}
  function changeBasemap(value){setBasemap(value);setMapNotice('');try{localStorage.setItem('aircol-basemap',value);}catch{}}
  function tileFailure(){
    if(basemap==='dark'){
      setBasemap('light');setMapNotice('Dark tiles could not load. Switched to OpenStreetMap light tiles.');
      try{localStorage.setItem('aircol-basemap','light');}catch{}
    }
  }
  async function startReplay(){
    setReplayBusy(true);
    try{
      const response=await fetch(`${apiBase}/api/replay/start`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({speed:Number(replaySpeed??airport.replay.default_speed)})});
      const body=await response.json();if(!response.ok)throw new Error(body.detail||`Replay request failed (${response.status}).`);
      setReplayMessage(`Replaying ${body.cycles} recorded polls at ${body.speed}×.`);
    }catch(error){setReplayMessage(`Replay unavailable: ${error.message}`);}
    finally{setReplayBusy(false);}
  }
  async function stopReplay(){
    setReplayBusy(true);
    try{const response=await fetch(`${apiBase}/api/replay/stop`,{method:'POST'});if(!response.ok)throw new Error('Replay stop request failed.');setReplayMessage('Replay stopped. Showing the live collector.');}
    catch(error){setReplayMessage(error.message);}
    finally{setReplayBusy(false);}
  }
  function focusPair(pair){setFocusedPair(pair);setSelectedId(pair.aircraft_a.icao24);}

  if(!airport)return <main className="loading-screen"><h1>AIR_COL</h1><div className="skeleton"/><div className="skeleton"/><p>{message}</p>
    <p className="disclaimer">Research prototype. Not ATC, TCAS/ACAS or a certified safety system. Public ADS-B may be delayed, incomplete or inaccurate, especially at low altitude and on the ground.</p></main>;

  const workspaceClass=['workspace',leftCollapsed?'left-hidden':'',rightCollapsed?'right-hidden':'',leftCollapsed&&rightCollapsed?'both-hidden':''].filter(Boolean).join(' ');
  const replay={speed:replaySpeed??airport.replay.default_speed,onSpeed:setReplaySpeed,onStart:startReplay,onStop:stopReplay,busy:replayBusy,mode,message:replayMessage};
  return <main className="app-shell">
    <DashboardHeader airport={airport} mode={mode} status={status} message={message} utcNow={utcNow}
      lastUpdateAge={lastUpdateAge} onToggleLeft={()=>setLeftCollapsed(value=>!value)} onToggleRight={()=>setRightCollapsed(value=>!value)}/>
    <div className={workspaceClass}>
      {!leftCollapsed&&<FiltersPanel airport={airport} counts={counts} predictionCounts={predictionCounts} mapLayers={mapLayers}
        onLayerChange={updateLayer} labelMode={labelMode} onLabelMode={setLabelMode} basemap={basemap} onBasemap={changeBasemap}
        groundOnly={groundOnly} onGroundOnly={setGroundOnly}/>}
      <MapView airport={airport} aircraft={visibleAircraft} predictions={predictions} pairs={pairs}
        selectedId={selectedId} onSelect={state=>setSelectedId(state.icao24)} focusPair={focusedPair}
        onFocusPair={focusPair} layers={mapLayers} labelMode={labelMode} basemap={basemap} onTileFailure={tileFailure} mapNotice={mapNotice}/>
      {!rightCollapsed&&<DetailsPanel aircraft={selectedAircraft} prediction={selectedAircraft?predictions[selectedAircraft.icao24]:null}/>}
    </div>
    <BottomPanel pairs={pairs} thresholds={pairThresholds} replaySettings={airport.replay} replay={replay} onFocusPair={focusPair}/>
    <footer className="disclaimer">Research prototype. Not ATC, TCAS/ACAS or a certified safety system. Public ADS-B data can be delayed, incomplete or inaccurate, especially at low altitude and on the ground.{pairsError&&<span> · Pair data unavailable.</span>}{predictionError&&<span> · Prediction data unavailable.</span>}</footer>
  </main>;
}

createRoot(document.getElementById('root')).render(<App/>);
