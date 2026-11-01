"use strict";

(() => {
  const $ = (id) => document.getElementById(id);
  const canvas = $("parking-map");
  const ctx = canvas.getContext("2d");
  const ui = {state:null,busy:false,picking:false,start:null,image:null,imageKey:null,lastPointer:null,toastTimer:null,selectedSpot:null};
  const stateNames = {free:"观测空闲",occupied:"检测占用",unknown:"占用不确定"};
  const typeNames = {normal:"普通位",accessible:"无障碍位",charging:"充电位"};
  const colors = {free:"#72bda6",occupied:"#cb8b82",unknown:"#d9b76c"};
  const reasonNames = {high_confidence_center:"高置信车辆中心位于车位内",ambiguous_overlap:"车辆框与车位相交，证据不确定",no_evidence:"未观测到车辆证据",observed_free:"未观测到车辆证据",invalid_frame:"当前感知帧不可用",frame_invalid:"当前感知帧不可用",perception_unavailable:"当前感知帧不可用"};
  const actionLabels = {register_vehicle:"正在登记车辆",allocate:"正在计算推荐车位",replan:"正在重新规划",confirm_park:"正在记录停车并重新检测",find_vehicle:"正在计算寻车路线",cancel:"正在取消推荐",depart:"正在记录离场并重新检测",reset:"正在重置场景",refresh:"正在运行 YOLO 检测",change_scene:"正在生成场景并运行 YOLO 检测"};
  const escapeHTML = (value) => String(value ?? "").replace(/[&<>"']/g, (c) => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
  const registry = () => Array.isArray(ui.state?.registry) ? ui.state.registry : Object.entries(ui.state?.registry || {}).map(([id,v])=>({vehicle_id:id,...v}));
  const session = () => ui.state?.session || {state:"idle"};
  const occupancy = (id) => {const o=ui.state?.snapshot?.occupancy?.[id];return typeof o==="string"?{state:o}:o||{state:"unknown"};};
  const finitePoint = (p) => Array.isArray(p) && p.length===2 && p.every(Number.isFinite);
  const thresholds = () => ({low:.2,high:.5,overlap_fraction:.05,...ui.state?.snapshot?.thresholds});
  const retainedDetections = () => (ui.state?.snapshot?.detections||[]).filter(d=>Number(d.confidence)>=thresholds().low);

  function toast(message) {
    $("toast").textContent=message; $("toast").hidden=false;
    clearTimeout(ui.toastTimer);ui.toastTimer=setTimeout(()=>$("toast").hidden=true,5000);
  }

  function setBusy(busy, label="正在处理") {
    ui.busy=busy;document.body.classList.toggle("busy",busy);
    $("map-loading").hidden=!busy;$("loading-label").textContent=label;
    if(busy) dismissSpotDetails();
    updateControls();
  }

  function updateControls() {
    const state=session().state||"idle", parked=state==="parked", planned=state==="planned";
    document.querySelectorAll("[data-action]").forEach(button=>{
      const action=button.dataset.action;
      let allowed=!!ui.state;
      if(action==="allocate") allowed=allowed&&state==="idle";
      if(["replan","cancel","confirm_park"].includes(action)) allowed=allowed&&planned;
      if(["find_vehicle","depart"].includes(action)) allowed=allowed&&parked;
      button.disabled=ui.busy||!allowed;
    });
    $("vehicle-select").disabled=ui.busy||state!=="idle";
    $("registration-panel").hidden=state!=="idle";
    for(const id of ["registration-label","registration-power","registration-permission","register-vehicle-button"]) $(id).disabled=ui.busy||!ui.state||state!=="idle";
    for(const id of ["needs-accessible","needs-charging"]) $(id).disabled=ui.busy||parked;
    document.querySelectorAll('input[name="prefer-mode"]').forEach(el=>el.disabled=ui.busy||parked);
    ["actual-spot-select","scenario-select","seed-input","start-x","start-y","pick-start-button","start-elevator","stop-picking"].forEach(id=>$(id).disabled=ui.busy);
    $("allocate-button").hidden=state!=="idle";$("replan-button").hidden=!planned;$("cancel-button").hidden=!planned;
    $("actual-section").hidden=!planned;$("find-section").hidden=!parked;
    $("session-chip").className=`session-chip ${state}`;
    $("session-label").textContent=ui.state?(({idle:"空闲 · 可申请",planned:"已推荐 · 待确认",parked:"已停车 · 可寻车"})[state]||"状态未知"):(ui.busy?"连接中":"未连接");
    $("step-number").textContent=({idle:"01",planned:"02",parked:"03"})[state]||"01";
    ["idle","planned","parked"].forEach((s,i)=>{const current=["idle","planned","parked"].indexOf(state);$("step-"+s).className=i===current?"active":i<current?"done":"";});
    if(!parked && ui.picking) setPicking(false);
  }

  function updateVehicleDetails() {
    const v=registry().find(x=>x.vehicle_id===$("vehicle-select").value);
    $("vehicle-power").textContent=v?({fuel:"燃油车",new_energy:"新能源车"}[v.power_type]||"动力类型未知"):"动力类型 —";
    $("vehicle-permission").textContent="无障碍资格 "+(v?({true:"有",false:"无",unknown:"待确认"}[String(v.accessible_permission)]||"待确认"):"—");
  }

  function routeResult() {
    const s=ui.state;
    if(!s?.snapshot?.valid) return null;
    const candidates=[s.route_result,s.last_result,session().plan_result,session().recommendation];
    return candidates.find(r=>r?.status==="ok" && Array.isArray(r.path_m) && r.path_m.length>=1 && Number(r.scene_version)===Number(s.scene_version) && (!r.frame_id||r.frame_id===s.frame_id) && s.route_valid!==false && session().route_valid!==false) || null;
  }

  function renderResult(result) {
    const panel=document.querySelector(".result-panel"),success=result?.status==="ok",failure=result&& !success;
    panel.classList.toggle("error",!!failure);panel.classList.toggle("success",!!success);
    $("result-icon").textContent=success?"✓":failure?"!":"i";
    $("result-title").textContent=success?"操作结果":failure?"需要处理":"操作提示";
    $("result-code").textContent=result?.reason_code||result?.status||"等待操作";
    $("result-message").textContent=result?.message||"选择车辆和本次需求，申请推荐车位。也可切换图层，查看原始图像与检测证据。";
    const distances=result?.distances_m;
    const labels={drive_in:"入场车行距离",walk_to_destination:"停车后到电梯",drive_out:"驶向出口距离",walk_to_vehicle:"步行寻车距离"};
    const cells=Object.entries(distances||{}).filter(([k,v])=>Number.isFinite(v)&&labels[k]);
    $("distances").hidden=!cells.length;
    $("distances").innerHTML=cells.map(([k,v])=>`<div class="distance-cell"><span>${labels[k]}</span><strong>${v.toFixed(2)}</strong><small>m</small></div>`).join("");
    const notices=result?.alerts||result?.warnings||result?.notices||result?.violations||session().alerts||[];
    let extra=(Array.isArray(notices)?notices:[notices]).filter(Boolean).filter(n=>!(typeof n==="object"&&n.code==="deviated_from_recommendation")).map(n=>typeof n==="string"?n:(n.message||n.reason_code||n.code||JSON.stringify(n))).join("；");
    if(session().state==="parked"&&session().recommended_spot_id&&session().actual_spot_id!==session().recommended_spot_id){extra=[`实际停在 ${session().actual_spot_id}，与推荐的 ${session().recommended_spot_id} 不同。寻车以实际记录为准。`,extra].filter(Boolean).join(" ");}
    if(session().state==="parked"&&session().perception_consistent===false){extra=[extra,"当前感知与人工停车记录不一致；实际位置仍保留，不计为识别成功。"].filter(Boolean).join(" ");}
    if(session().state==="parked"&&extra) extra="人工确认记录与感知核对："+extra;
    $("result-extra").textContent=extra;$("result-extra").hidden=!extra;
    const route=routeResult();
    $("route-validity").textContent=route?`当前路线有效 · ${route.operation==="find_vehicle"?"人行":"车行"} ${route.target_spot_id||""}`:session().state==="planned"?"当前无有效路线，请重新规划或取消":"尚无当前有效路线";
    $("frame-id").textContent=`帧 ${ui.state?.frame_id||"—"}`;
  }

  function applyState(next,result=null) {
    if(!next?.map) throw new Error("服务返回的状态缺少地图数据。");
    const old=ui.state, oldSession=old?.session?.session_id, oldFrame=old?.frame_id;
    ui.state=next;
    dismissSpotDetails();
    const counts={free:0,occupied:0,unknown:0};
    for(const spot of next.map.spots||[]) counts[occupancy(spot.spot_id).state in counts?occupancy(spot.spot_id).state:"unknown"]++;
    for(const key of Object.keys(counts)) $(key+"-count").textContent=counts[key];
    $("scene-version").textContent=next.scene_version??"—";
    const valid=!!next.snapshot?.valid;
    $("frame-valid").textContent=valid?"感知可用":"感知不可用";$("frame-valid").className="frame-valid "+(valid?"valid":"invalid");
    const detections=retainedDetections();
    $("frame-summary").textContent=valid?`${detections.length} 个保留车辆框 · 原图 1664 × 1024`:(next.snapshot?.error||"等待完整有效检测结果");
    const vehicles=registry().filter(v=>v.role==="applicant");
    const previousSelection=$("vehicle-select").value;
    $("vehicle-select").innerHTML=vehicles.map(v=>`<option value="${escapeHTML(v.vehicle_id)}">${escapeHTML(v.label||v.vehicle_id)}</option>`).join("");
    const chosen=session().vehicle_id||result?.registered_vehicle?.vehicle_id||previousSelection;
    if(vehicles.some(v=>v.vehicle_id===chosen)) $("vehicle-select").value=chosen;
    const req=session().request||session().requirements||session().needs;
    if(req && (!old || oldSession!==session().session_id)) {
      $("needs-accessible").checked=!!req.needs_accessible;$("needs-charging").checked=!!req.needs_charging;
      document.querySelectorAll('input[name="prefer-mode"]').forEach(el=>el.checked=el.value===req.prefer_mode);
    }
    updateVehicleDetails();
    const prevSpot=$("actual-spot-select").value;
    $("actual-spot-select").innerHTML=(next.map.spots||[]).slice().sort((a,b)=>a.spot_id.localeCompare(b.spot_id)).map(spot=>`<option value="${escapeHTML(spot.spot_id)}">${escapeHTML(spot.spot_id)} · ${typeNames[spot.spot_type]||spot.spot_type} · ${stateNames[occupancy(spot.spot_id).state]||"不确定"}</option>`).join("");
    const target=(oldSession!==session().session_id || result?.operation==="replan")?session().recommended_spot_id:prevSpot||session().recommended_spot_id;
    if(target) $("actual-spot-select").value=target;
    $("recommended-label").textContent=`推荐 ${session().recommended_spot_id||"—"}`;
    $("actual-label").textContent=`实际 ${session().actual_spot_id||"—"}`;
    const planner=next.planner_name||"参考规划适配器";
    $("planner-label").textContent=/reference_planner|参考/.test(planner)?"参考规划适配器":"已接入规划模块";
    $("planner-label").title=`${planner}；可替换为梁可的规划模块`;
    const sceneSeed=next.seed??next.scene?.seed,sceneType=next.scenario||next.scene?.scenario;
    if(sceneSeed!==undefined) $("seed-input").value=sceneSeed;
    if(sceneType) $("scenario-select").value=sceneType;
    if(oldSession!==session().session_id&&session().state!=="parked"){ui.start=null;$("start-x").value="";$("start-y").value="";}
    renderResult(result||next.last_result);updateControls();
    if(oldFrame!==next.frame_id || !ui.image) loadFrame();
    drawMap();
  }

  function loadFrame() {
    const key=ui.state?.frame_id||String(ui.state?.scene_version||0);
    if(ui.imageKey===key && ui.image) return;
    ui.imageKey=key;ui.image=null;const img=new Image();
    img.onload=()=>{if(ui.imageKey===key){ui.image=img;drawMap();}};
    img.onerror=()=>{if(ui.imageKey===key){ui.image=null;drawMap();}};
    const base=ui.state?.image_url||"/api/frame";
    img.src=base+(base.includes("?")?"&":"?")+"frame_id="+encodeURIComponent(key);
  }

  async function api(url,options={}) {
    const controller=new AbortController(),timer=setTimeout(()=>controller.abort(),180000);
    try {
      const response=await fetch(url,{...options,signal:controller.signal,cache:"no-store"});
      const data=await response.json();
      if(!response.ok){const detail=data.detail;throw new Error(typeof detail==="string"?detail:Array.isArray(detail)?detail.map(d=>d.msg).join("；"):`本地服务返回 HTTP ${response.status}`);}
      return data;
    } catch(err) {if(err.name==="AbortError") throw new Error("本次处理超过 3 分钟。请等待后台处理结束后重新检测，或检查运行窗口。");throw err;} finally {clearTimeout(timer);}
  }

  function getRequest(operation) {
    if(operation==="register_vehicle") {
      const label=$("registration-label").value.trim();
      if(!label||label.length>40||/[\u0000-\u001f\u007f]/.test(label)) throw new Error("请填写 1 至 40 个字符的车辆名称或代号。");
      const permission=$("registration-permission").value;
      return {operation,label,power_type:$("registration-power").value,accessible_permission:permission==="unknown"?"unknown":permission==="true"};
    }
    const current=session();
    const request={operation,vehicle_id:current.vehicle_id||$("vehicle-select").value,session_id:operation==="allocate"?null:current.session_id||null};
    if(["allocate","replan"].includes(operation)) Object.assign(request,{needs_accessible:$("needs-accessible").checked,needs_charging:$("needs-charging").checked,prefer_mode:document.querySelector('input[name="prefer-mode"]:checked')?.value||"near_destination",destination_id:"elevator_1",pedestrian_start_m:null});
    if(operation==="confirm_park") Object.assign(request,{actual_spot_id:$("actual-spot-select").value,scene_version:ui.state.scene_version});
    if(operation==="find_vehicle") {
      const x=$("start-x").value,y=$("start-y").value;
      if(x===""||y===""||!Number.isFinite(Number(x))||!Number.isFinite(Number(y))) throw new Error("请先在地图上点选人行起点，或填写 X、Y 坐标。");
      request.pedestrian_start_m=[Number(x),Number(y)];ui.start=request.pedestrian_start_m;setPicking(false);
    }
    if(operation==="change_scene") {
      const seed=Number($("seed-input").value);
      if(!Number.isInteger(seed)||seed<0||seed>2147483647) throw new Error("场景种子应为 0 至 2147483647 的整数。");
      Object.assign(request,{seed,scenario:$("scenario-select").value});
    }
    return request;
  }

  async function perform(operation) {
    if(ui.busy)return;
    let request;
    try {request=getRequest(operation);}catch(err){toast(err.message);return;}
    setBusy(true,actionLabels[operation]||"正在处理");
    try {
      const response=await api("/api/action",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(request)});
      applyState(response.state,response.result);
      if(operation==="register_vehicle"&&response.result?.status==="ok") {
        $("registration-form").reset();$("registration-panel").open=false;
      }
    } catch(err) {
      toast(`操作未完成：${err.message}`);
      renderResult({status:"connection_error",reason_code:"request_failed",message:`操作未完成：${err.message}。可继续重试；已确认的停车记录以服务端状态为准。`});
      try{const fresh=await api("/api/state");applyState(fresh);}catch(_){/* Keep current state visible; never fabricate a new successful state. */}
    } finally {setBusy(false);drawMap();}
  }

  function setPicking(value) {
    if(value) dismissSpotDetails();
    ui.picking=value;$("canvas-wrap").classList.toggle("picking",value);$("pick-banner").hidden=!value;
    $("pick-start-button").textContent=value?"⌖ 点选中，点击地图设置起点":"⌖ 在地图上点选起点";
    drawMap();
  }

  function setStart(point) {
    ui.start=point;$("start-x").value=point[0].toFixed(6);$("start-y").value=point[1].toFixed(6);drawMap();
  }

  function pointerPoint(event) {
    const r=canvas.getBoundingClientRect(),m=ui.state?.map;
    return [(event.clientX-r.left)/r.width*(m?.width_m||52),(event.clientY-r.top)/r.height*(m?.height_m||32)];
  }

  function spotAt(point) {
    return ui.state?.map.spots?.find(s=>{const r=s.rect_m;return r&&point[0]>=r[0]&&point[0]<=r[2]&&point[1]>=r[1]&&point[1]<=r[3];});
  }

  function dismissSpotDetails() {
    ui.selectedSpot=null;
    const tip=$("map-tooltip");tip.hidden=true;tip.classList.remove("pinned");
  }

  function spotEvidence(spot) {
    // Explain the current perception only. Match occupancy.py's strict center
    // tolerance and intersection comparison; never infer a new occupancy state.
    const r=spot.rect_m,area=(r[2]-r[0])*(r[3]-r[1]),epsilon=1e-6,t=thresholds();
    return retainedDetections().flatMap(d=>{
      const b=d.bbox_m;
      if(!Array.isArray(b)||b.length!==4||!b.every(Number.isFinite))return [];
      const overlap=Math.max(0,Math.min(r[2],b[2])-Math.max(r[0],b[0]))*Math.max(0,Math.min(r[3],b[3])-Math.max(r[1],b[1]));
      if(overlap<=0)return [];
      const cx=(b[0]+b[2])/2,cy=(b[1]+b[3])/2;
      const centerSpot=ui.state.map.spots.find(s=>{const a=s.rect_m;return a[0]+epsilon<cx&&cx<a[2]-epsilon&&a[1]+epsilon<cy&&cy<a[3]-epsilon;});
      const nearBoundary=!centerSpot&&ui.state.map.spots.some(s=>{const a=s.rect_m;return a[0]-epsilon<=cx&&cx<=a[2]+epsilon&&a[1]-epsilon<=cy&&cy<=a[3]+epsilon;});
      return [{d,centerSpot:centerSpot?.spot_id||null,centerLabel:centerSpot?.spot_id||(nearBoundary?"车位边界附近（不计严格在位）":"车位外"),fraction:overlap/area,reachesOverlap:overlap+1e-12>=t.overlap_fraction*area,used:(occupancy(spot.spot_id).detection_ids||[]).includes(d.detection_id)}];
    }).sort((a,b)=>Number(b.used)-Number(a.used)||b.d.confidence-a.d.confidence);
  }

  function showSpotDetails(spot,event,pinned=false) {
    const tip=$("map-tooltip"),o=occupancy(spot.spot_id),t=thresholds();
    const evidence=ui.state.snapshot?.valid?spotEvidence(spot):[];
    const rows=evidence.map(e=>`<div class="evidence-box" data-detection-id="${escapeHTML(e.d.detection_id)}" data-confidence="${e.d.confidence}" data-center-spot="${escapeHTML(e.centerSpot||"")}" data-overlap-fraction="${e.fraction}" data-used="${e.used}"><div class="evidence-line"><span class="evidence-id">框 ${escapeHTML(e.d.detection_id)}</span><b>置信度 ${Number(e.d.confidence).toFixed(4)}</b></div><div class="evidence-line"><span>框中心归属</span><b>${escapeHTML(e.centerLabel)}</b></div><div class="evidence-line"><span>与本位相交 / 车位面积</span><b>${(e.fraction*100).toFixed(2)}%</b></div><small>${e.reachesOverlap?"达到":"低于"} ${(t.overlap_fraction*100).toFixed(2)}% 相交阈值 · ${e.used?"用于当前判定":"其他相交证据"}</small></div>`).join("");
    const empty=ui.state.snapshot?.valid?"当前帧没有与该车位相交的保留检测框。"+(o.state==="free"?"观测空闲仍可能存在漏检。":""):"当前感知帧不可用，请重新检测后查看证据。";
    tip.innerHTML=`<div class="evidence-heading"><strong>${escapeHTML(spot.spot_id)} · ${typeNames[spot.spot_type]||"车位"}</strong>${pinned?'<button type="button" class="evidence-close" aria-label="关闭车位详情">×</button>':""}</div><small class="evidence-reason">${stateNames[o.state]||"状态未知"}${o.reason?" · "+escapeHTML(reasonNames[o.reason]||o.reason):""}</small><div class="evidence-list">${rows||`<p class="evidence-empty">${empty}</p>`}</div><small class="evidence-thresholds">当前帧 · 保留 ≥ ${Number(t.low).toFixed(2)} · 高置信 ≥ ${Number(t.high).toFixed(2)}</small><small class="evidence-hint">${pinned?"已固定 · 可滚动查看，按 Esc 关闭":"点击车位可固定详情"}</small>`;
    ui.selectedSpot=pinned?spot.spot_id:null;
    tip.classList.toggle("pinned",pinned);tip.hidden=false;tip.scrollTop=0;
    const r=canvas.getBoundingClientRect(),x=event.clientX-r.left,y=event.clientY-r.top;
    tip.style.maxHeight=Math.max(80,r.height-16)+"px";
    const width=tip.offsetWidth,height=tip.offsetHeight;
    const preferredX=x+16+width<=r.width-8?x+16:x-width-16;
    tip.style.left=Math.max(8,Math.min(preferredX,r.width-width-8))+"px";
    tip.style.top=Math.max(8,Math.min(y+12,r.height-height-8))+"px";
  }

  function roundedRect(x,y,w,h,r) {
    ctx.beginPath();ctx.roundRect(x,y,w,h,r);
  }

  function drawArrow(x,y,angle) {
    ctx.save();ctx.translate(x,y);ctx.rotate(angle);ctx.strokeStyle="#8d9b9c";ctx.lineWidth=.07;ctx.beginPath();ctx.moveTo(-.5,0);ctx.lineTo(.55,0);ctx.moveTo(.2,-.24);ctx.lineTo(.55,0);ctx.lineTo(.2,.24);ctx.stroke();ctx.restore();
  }

  function label(text,x,y,color="#657e85",size=.46,bold=false) {
    ctx.font=`${bold?"600 ":""}${size}px "Microsoft YaHei",sans-serif`;ctx.textAlign="center";ctx.textBaseline="middle";ctx.fillStyle=color;ctx.fillText(text,x,y);
  }

  function drawPath(points,color,width=.15,dashed=false) {
    if(!Array.isArray(points)||points.length<1||!points.every(finitePoint))return;
    ctx.save();ctx.beginPath();ctx.strokeStyle=color;ctx.lineWidth=width;ctx.lineJoin="round";ctx.lineCap="round";if(dashed)ctx.setLineDash([.34,.21]);
    points.forEach((p,i)=>i?ctx.lineTo(p[0],p[1]):ctx.moveTo(p[0],p[1]));ctx.stroke();ctx.setLineDash([]);
    const last=points[points.length-1];ctx.fillStyle="white";ctx.strokeStyle=color;ctx.lineWidth=.1;ctx.beginPath();ctx.arc(last[0],last[1],.23,0,Math.PI*2);ctx.fill();ctx.stroke();ctx.restore();
  }

  function drawMarker(spot,color,text,fill="white") {
    if(!spot)return;const r=spot.rect_m;
    ctx.save();ctx.strokeStyle=color;ctx.lineWidth=.16;ctx.setLineDash([]);roundedRect(r[0]+.07,r[1]+.08,r[2]-r[0]-.14,r[3]-r[1]-.16,.1);ctx.stroke();
    const c=spot.center_m;ctx.fillStyle=color;roundedRect(c[0]-1.04,c[1]-.42,2.08,.84,.18);ctx.fill();label(text,c[0],c[1],fill,.39,true);ctx.restore();
  }

  function drawMap() {
    const r=canvas.getBoundingClientRect(),dpr=Math.min(window.devicePixelRatio||1,2),w=Math.max(1,r.width),h=w*32/52;
    if(canvas.width!==Math.round(w*dpr)||canvas.height!==Math.round(h*dpr)){canvas.width=Math.round(w*dpr);canvas.height=Math.round(h*dpr);}
    ctx.setTransform(dpr*w/52,0,0,dpr*h/32,0,0);ctx.clearRect(0,0,52,32);ctx.fillStyle="#edf0ef";ctx.fillRect(0,0,52,32);
    const map=ui.state?.map;if(!map){label("正在读取停车场地图",26,16,"#90a3aa",.7);return;}
    const showImage=$("layer-image").checked,showOccupancy=$("layer-occupancy").checked,showDetections=$("layer-detections").checked;
    if(showImage && ui.image)ctx.drawImage(ui.image,0,0,52,32);
    else {
      for(const a of map.car_areas_m||[]){ctx.fillStyle="#e0e5e4";ctx.fillRect(a[0],a[1],a[2]-a[0],a[3]-a[1]);}
      for(const a of map.ped_areas_m||[]){ctx.fillStyle="#cadbd6";ctx.fillRect(a[0],a[1],a[2]-a[0],a[3]-a[1]);}
      for(const a of map.static_obstacles_m||[]){ctx.fillStyle="#879a9b";ctx.fillRect(a[0],a[1],a[2]-a[0],a[3]-a[1]);}
    }
    // Crossing stripes remain legible in both map modes.
    for(const cy of [8.35,23.65])for(let y=cy-1.45;y<cy+1.5;y+=.48){ctx.fillStyle=showImage?"#ffffff55":"#fafcfccc";ctx.fillRect(42.04,y,1.12,.25);}
    for(const spot of map.spots||[]) {
      const box=spot.rect_m;if(!box)continue;const o=occupancy(spot.spot_id),color=colors[o.state]||colors.unknown;
      if(showOccupancy){ctx.fillStyle=color+(showImage?"55":"55");ctx.fillRect(box[0]+.08,box[1]+.08,box[2]-box[0]-.16,box[3]-box[1]-.16);ctx.strokeStyle=color;ctx.lineWidth=.065;ctx.strokeRect(box[0]+.08,box[1]+.08,box[2]-box[0]-.16,box[3]-box[1]-.16);}
      else if(!showImage){ctx.fillStyle="#f2f5f2";ctx.fillRect(box[0]+.06,box[1]+.06,box[2]-box[0]-.12,box[3]-box[1]-.12);ctx.strokeStyle="#acbcb8";ctx.lineWidth=.035;ctx.strokeRect(box[0],box[1],box[2]-box[0],box[3]-box[1]);}
      const cx=spot.center_m[0];
      // Label at the pedestrian end, outside the center of the car body.
      const upper=spot.ped_access_m[1]<spot.center_m[1];const ly=upper?box[1]+.48:box[3]-.48;
      ctx.fillStyle="#ffffffcf";roundedRect(cx-.77,ly-.3,1.54,.6,.1);ctx.fill();label(spot.spot_id,cx,ly,"#4b6773",.38,true);
      if(spot.spot_type!=="normal"){
        const ty=upper?box[1]+1.15:box[3]-1.15;
        label(spot.spot_type==="accessible"?"无障碍":"充电",cx,ty,spot.spot_type==="accessible"?"#5a71a0":"#468b7b",.33,true);
      }
    }
    if(!showImage){
      for(const d of retainedDetections()){const b=d.bbox_m||d.bbox_xyxy_px?.map(n=>n/32);if(!b)continue;ctx.fillStyle="#739096";roundedRect(b[0],b[1],b[2]-b[0],b[3]-b[1],.2);ctx.fill();}
    }
    for(const x of [10,22,34]){drawArrow(x,8.35,Math.PI);drawArrow(x,23.65,0);}
    drawArrow(2.25,12,Math.PI/2);drawArrow(2.25,20,Math.PI/2);drawArrow(45.75,12,-Math.PI/2);drawArrow(45.75,20,-Math.PI/2);
    label("入口",1.85,14.75,"#628783",.52,true);label("出口",49.6,14.75,"#628783",.52,true);drawArrow(1.9,16,0);drawArrow(49.6,16,0);
    ctx.fillStyle="#274f65";roundedRect(44.1,2.7,5.4,2.5,.3);ctx.fill();label("电梯",46.8,3.65,"white",.65,true);label("ELEVATOR",46.8,4.45,"#a4c6d2",.3);drawPath([[42.6,4],[44.1,4]],"#6a9794",.08,true);
    label("人行通道",40.5,16,"#7b9b8e",.34);label("单向车道",24,8.35,"#849798",.38);label("单向车道",24,23.65,"#849798",.38);
    const route=routeResult();
    if(route){
      const paths=route.routes||{};
      if(route.operation==="find_vehicle")drawPath(route.path_m,"#0c9c84",.17,true);
      else {drawPath(paths.drive_out,"#7d9cba",.09,true);drawPath(paths.walk_to_destination,"#0c9c84",.14,true);drawPath(route.path_m,"#2778b8",.18,false);}
    }
    if(showDetections)for(const d of retainedDetections()){
      const b=d.bbox_m||d.bbox_xyxy_px?.map(n=>n/32);if(!b)continue;
      const high=Number(d.confidence)>=thresholds().high,c=high?"#2758ae":"#c38720";ctx.save();ctx.strokeStyle=c;ctx.lineWidth=.08;if(!high)ctx.setLineDash([.2,.12]);ctx.strokeRect(b[0],b[1],b[2]-b[0],b[3]-b[1]);ctx.setLineDash([]);ctx.fillStyle=c;ctx.fillRect(b[0],b[1]-.52,1.72,.52);label(`car ${Number(d.confidence).toFixed(2)}`,b[0]+.86,b[1]-.26,"white",.3);ctx.restore();
    }
    const s=session();
    if(s.recommended_spot_id&&s.state!=="idle")drawMarker(map.spots.find(p=>p.spot_id===s.recommended_spot_id),"#2a71ac",s.state==="parked"?"原推荐":"推荐");
    if(s.actual_spot_id&&s.state==="parked")drawMarker(map.spots.find(p=>p.spot_id===s.actual_spot_id),"#8060ac","实际");
    if(s.state==="planned"&&$("actual-spot-select").value&&$("actual-spot-select").value!==s.recommended_spot_id){const spot=map.spots.find(p=>p.spot_id===$("actual-spot-select").value);if(spot){ctx.save();ctx.setLineDash([.22,.17]);ctx.strokeStyle="#8160ad";ctx.lineWidth=.12;const b=spot.rect_m;ctx.strokeRect(b[0]+.02,b[1]+.02,b[2]-b[0]-.04,b[3]-b[1]-.04);ctx.restore();}}
    if(finitePoint(ui.start)&&s.state==="parked"){
      const [x,y]=ui.start;ctx.beginPath();ctx.arc(x,y,.39,0,Math.PI*2);ctx.fillStyle="#0b9984";ctx.fill();ctx.lineWidth=.12;ctx.strokeStyle="white";ctx.stroke();label("起点",x,y-.7,"#197865",.39,true);
    }
    ctx.fillStyle="#ffffffce";roundedRect(46.1,28.8,4.8,2.1,.2);ctx.fill();ctx.strokeStyle="#768e99";ctx.lineWidth=.055;ctx.beginPath();ctx.moveTo(46.6,29.55);ctx.lineTo(50.6,29.55);ctx.moveTo(46.6,29.38);ctx.lineTo(46.6,29.72);ctx.moveTo(50.6,29.38);ctx.lineTo(50.6,29.72);ctx.stroke();label("4 m",48.6,30.2,"#778f9b",.34);
    if(!ui.state.snapshot?.valid){ctx.fillStyle="#ffffffa6";roundedRect(15,13.8,22,3.8,.3);ctx.fill();label("感知不可用 · 暂停新分配",26,15.3,"#967539",.65,true);label("请重新检测当前场景",26,16.5,"#9c8d73",.45);}
  }

  document.querySelectorAll("[data-action]").forEach(button=>button.addEventListener("click",()=>perform(button.dataset.action)));
  $("registration-form").addEventListener("submit",event=>{event.preventDefault();perform("register_vehicle");});
  ["layer-image","layer-occupancy","layer-detections"].forEach(id=>$(id).addEventListener("change",drawMap));
  $("vehicle-select").addEventListener("change",updateVehicleDetails);$("actual-spot-select").addEventListener("change",drawMap);
  $("pick-start-button").addEventListener("click",()=>setPicking(!ui.picking));$("stop-picking").addEventListener("click",()=>setPicking(false));
  $("start-elevator").addEventListener("click",()=>{setStart(ui.state?.map.destination_m||[42.6,4]);setPicking(false);});
  for(const id of ["start-x","start-y"]) $(id).addEventListener("input",()=>{const x=$("start-x").value,y=$("start-y").value;ui.start=x!==""&&y!==""?[Number(x),Number(y)]:null;drawMap();});
  canvas.addEventListener("click",event=>{
    if(ui.busy||!ui.state)return;const point=pointerPoint(event);
    if(ui.picking){setStart(point);setPicking(false);toast(`已选择起点 (${point[0].toFixed(2)}, ${point[1].toFixed(2)}) m；生成路线时将校验人行可达性。`);return;}
    const spot=spotAt(point);
    if(spot&&session().state==="planned"){$("actual-spot-select").value=spot.spot_id;drawMap();toast(`已选择实际车位 ${spot.spot_id}，请在右侧确认停车。`);}
    if(spot)showSpotDetails(spot,event,true);else dismissSpotDetails();
  });
  canvas.addEventListener("mousemove",event=>{
    const point=pointerPoint(event);$("map-coordinate").textContent=`X ${point[0].toFixed(2)} / Y ${point[1].toFixed(2)} m`;
    if(ui.busy||ui.picking){dismissSpotDetails();return;}
    if(ui.selectedSpot)return;
    const spot=spotAt(point);if(!spot){$("map-tooltip").hidden=true;return;}
    showSpotDetails(spot,event);
  });
  canvas.addEventListener("mouseleave",()=>{if(!ui.selectedSpot)$("map-tooltip").hidden=true;$("map-coordinate").textContent="X — / Y — m";});
  $("map-tooltip").addEventListener("click",event=>{if(event.target.closest(".evidence-close"))dismissSpotDetails();});
  window.addEventListener("resize",()=>{dismissSpotDetails();drawMap();});
  if(window.ResizeObserver)new ResizeObserver(drawMap).observe(canvas.parentElement);
  document.addEventListener("keydown",event=>{if(event.key==="Escape"){setPicking(false);dismissSpotDetails();}});

  async function boot() {
    setBusy(true,"正在连接本地服务");
    try{applyState(await api("/api/state"));}
    catch(err){$("session-label").textContent="连接失败";$("frame-summary").textContent="请确认本地运行窗口仍打开";$("result-message").textContent=`无法连接本地服务：${err.message}。请从项目启动器重新打开，或刷新页面。`;toast("本地服务连接失败，请检查运行窗口。");}
    finally{setBusy(false);drawMap();}
  }
  boot();
})();
