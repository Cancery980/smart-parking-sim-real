"use strict";
(() => {
  const $ = id => document.getElementById(id);
  const canvas = $("real-canvas"), context = canvas.getContext("2d");
  const ui = {photos:[],filtered:[],photo:null,image:null,result:null,model:null,busy:false,loading:false,epoch:0,selectedSpot:null,hoverSpot:null};
  const colors = {free:"#40af91",occupied:"#d17b71",unknown:"#dfad47"};
  const states = {free:"观测空闲",occupied:"检测占用",unknown:"占用不确定"};
  const reasons = {high_confidence_center:"高置信车辆框中心位于标定车位内。",ambiguous_overlap:"车辆框与车位相交，当前证据不足以判为明确占用。",incomplete_observation:"本车位未被推理区域完整覆盖，当前证据不足，保留为不确定。",no_evidence:"没有达到相交阈值的保留车辆框；观测空闲仍可能存在漏检。",observed_free:"没有达到相交阈值的保留车辆框；观测空闲仍可能存在漏检。"};
  const weatherNames = {Sunny:"晴天",Cloudy:"多云",Rainy:"雨天",SUNNY:"晴天",OVERCAST:"阴天",RAINY:"雨天",sunny:"晴天",cloudy:"多云",rainy:"雨天"};
  const splitNames = {dev:"开发集",development:"开发集",val:"开发集",validation:"开发集",test:"历史测试",external_test:"外部验证",train:"训练集"};
  const escapeHTML = value => String(value ?? "").replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
  const weather = value => weatherNames[value] || value || "未注明";
  const split = value => splitNames[value] || value || "未注明";
  const datasetName = photo => /cnrpark/i.test(photo?.dataset||"")?"CNRPark-EXT":/pklot/i.test(photo?.dataset||"")?"PKLot":photo?.dataset||"未注明";
  const filterKeys = ["dataset","view","weather","split"];
  const weatherFilterValue = value => ({sunny:"Sunny",rainy:"Rainy",cloudy:"Cloudy",overcast:"OVERCAST"}[String(value).toLowerCase()]??value);
  const filterValue = (photo,key) => key==="dataset"?datasetName(photo):key==="weather"?weatherFilterValue(photo.weather):photo[key];
  const modelName = model => String(model?.weights || model?.name || "—").split(/[\\/]/).pop();
  const photoURL = photo => photo.image_url || `/api/real/image/${encodeURIComponent(photo.photo_id)}`;
  const message = (text,isError=false) => {$("real-message").textContent=text;$("real-message").classList.toggle("error",isError);};
  function status(text,type="") {$("status-label").textContent=text;$("photo-status").className="photo-status"+(type?" "+type:"");}

  function detectionThreshold() {
    return ui.result?.thresholds?.detection ?? ui.model?.detection_threshold ?? ui.result?.thresholds?.low ?? ui.model?.conf ?? ui.model?.low ?? .2;
  }
  function accepted(detection) {
    return typeof detection.accepted==="boolean"?detection.accepted:Number(detection.confidence)>=detectionThreshold();
  }
  function detectionCounts(result) {
    const derivedAccepted=result.detections.filter(accepted).length;
    return {accepted:result.detection_counts?.accepted??derivedAccepted,evidence_only:result.detection_counts?.evidence_only??result.detections.length-derivedAccepted,retained:result.detection_counts?.retained??result.detections.length};
  }

  async function request(url,options={}) {
    const response=await fetch(url,{cache:"no-store",...options});
    let data;
    try { data=await response.json(); } catch { throw new Error("本地服务返回了无法读取的结果，请重新启动程序后再试。"); }
    if(!response.ok || data?.status==="error") {
      const detail=data?.message || (typeof data?.detail==="string"?data.detail:null);
      throw new Error(detail || `请求未完成（HTTP ${response.status}），请检查程序窗口中的信息。`);
    }
    return data;
  }

  function updateControls() {
    const i=ui.filtered.findIndex(p=>p.photo_id===ui.photo?.photo_id);
    $("previous-photo").disabled=ui.busy||i<=0;
    $("next-photo").disabled=ui.busy||i<0||i>=ui.filtered.length-1;
    $("photo-position").textContent=ui.filtered.length?`${Math.max(0,i+1)} / ${ui.filtered.length}`:"0 / 0";
    $("photo-total").textContent=`${ui.filtered.length} 张`;
    $("analyze-photo").disabled=ui.busy||ui.loading||!ui.photo||!ui.image;
    $("analyze-label").textContent=ui.busy?"正在执行模型推理…":ui.result?"重新运行真实检测":"运行真实 YOLO 检测";
    for(const key of filterKeys) $(key+"-filter").disabled=ui.busy||!ui.photos.length;
    $("photo-select").disabled=ui.busy||!ui.filtered.length;
    $("photo-loading").hidden=!(ui.busy||ui.loading);
    $("photo-loading-label").textContent=ui.busy?"正在加载模型并检测真实照片":"正在读取原始照片";
  }

  function setModel(model) {
    ui.model=model||ui.model;
    $("photo-model").textContent=modelName(ui.model);
    const resolution=ui.model?.imgsz, low=ui.result?.thresholds?.low ?? ui.model?.conf ?? ui.model?.low;
    $("photo-model-settings").textContent=[resolution?`推理尺寸 ${resolution}`:"",`检测 ≥ ${Number(detectionThreshold()).toFixed(2)}`,Number.isFinite(low)?`证据保留 ≥ ${Number(low).toFixed(2)}`:""].filter(Boolean).join(" · ");
  }

  function resetResult() {
    ui.result=null;ui.selectedSpot=null;ui.hoverSpot=null;
    for(const key of ["free","occupied","unknown"]) $("real-"+key+"-count").textContent="—";
    $("real-detection-count").textContent="—";
    $("real-evidence-summary").textContent="等待运行 · 低置信证据单独列出";
    $("real-latency").textContent="不读取占用真值";
    $("photo-time").textContent="尚未运行";
    $("photo-coordinate").textContent="X — / Y — px";
    $("real-view-hint").textContent="运行检测后点击车位查看依据";
    showSpot(null);draw();
  }

  function photoMetadata(photo) {
    $("photo-dataset").textContent=photo?.dataset||"—";
    $("photo-view").textContent=photo?.view||"—";
    $("photo-weather-date").textContent=photo?`${weather(photo.weather)} · ${photo.date||"日期未注明"}`:"—";
    $("photo-size").textContent=photo?`${photo.width} × ${photo.height} px`:"—";
    $("photo-split").textContent=photo?split(photo.split):"—";
    $("photo-id").textContent=photo?.photo_id||"—";
    $("photo-subtitle").textContent=photo?`${photo.view} · ${weather(photo.weather)} · ${photo.width} × ${photo.height} 像素`:"按照片原始像素显示";
    const link=$("photo-source-link");
    link.hidden=true;link.removeAttribute("href");
    if(photo?.source_url) {
      try {const url=new URL(photo.source_url);if(url.protocol==="https:"||url.protocol==="http:"){link.href=url.href;link.hidden=false;}} catch {}
    }
  }

  async function choosePhoto(id) {
    const epoch=++ui.epoch;
    ui.photo=ui.filtered.find(p=>p.photo_id===id)||null;
    ui.image=null;ui.loading=!!ui.photo;resetResult();photoMetadata(ui.photo);
    $("photo-placeholder").hidden=false;
    if(!ui.photo){message("没有符合当前筛选条件的照片，请更改筛选条件。");status("没有匹配照片");updateControls();return;}
    $("photo-select").value=ui.photo.photo_id;
    const photo=ui.photo;
    canvas.width=photo.width;canvas.height=photo.height;draw();
    message("正在读取原图；照片切换后需要重新运行检测。");status("正在读取照片");updateControls();
    const image=new Image();
    try {
      await new Promise((resolve,reject)=>{image.onload=resolve;image.onerror=()=>reject(new Error("原始照片未能加载，请检查演示包是否完整解压，然后重新选择照片。"));image.src=photoURL(photo);});
      if(epoch!==ui.epoch)return;
      if(image.naturalWidth!==photo.width||image.naturalHeight!==photo.height)throw new Error("照片尺寸与记录不一致，已停止显示推理结果。");
      ui.image=image;$("photo-placeholder").hidden=true;
      message("原图已就绪。点击“运行真实 YOLO 检测”，查看本次车辆检测与三态车位结果。");status("原图已就绪");draw();
    } catch(error) {
      if(epoch!==ui.epoch)return;
      ui.image=null;message(error.message,true);status("照片读取失败","error");
    } finally {
      if(epoch===ui.epoch){ui.loading=false;updateControls();}
    }
  }

  function filterPhotos(preferredID) {
    ui.filtered=ui.photos.filter(photo=>filterKeys.every(key=>!$(key+"-filter").value||String(filterValue(photo,key))===$(key+"-filter").value));
    $("photo-select").innerHTML=ui.filtered.length?ui.filtered.map(p=>`<option value="${escapeHTML(p.photo_id)}">${escapeHTML(datasetName(p))} · ${escapeHTML(p.view)} · ${escapeHTML(weather(p.weather))} · ${escapeHTML(p.date)} · ${escapeHTML(split(p.split))} · ${escapeHTML(p.photo_id)}</option>`).join(""):'<option value="">无匹配照片</option>';
    const next=ui.filtered.find(p=>p.photo_id===preferredID)||ui.filtered[0];
    choosePhoto(next?.photo_id);
  }

  async function analyze() {
    if(ui.busy||!ui.photo||!ui.image)return;
    const photo=ui.photo,epoch=ui.epoch;
    resetResult();ui.busy=true;status("正在运行真实模型");message("正在本机执行 YOLO 推理。首次加载模型可能需要更久，请保持程序窗口开启。");updateControls();
    try {
      const data=await request("/api/real/analyze",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({photo_id:photo.photo_id})});
      if(epoch!==ui.epoch)return;
      if(data.photo?.photo_id!==photo.photo_id||!Array.isArray(data.spots)||!Array.isArray(data.detections))throw new Error("检测结果与当前照片不一致，已拒绝显示。请重新运行检测。");
      ui.result=data;setModel(data.model);
      for(const key of ["free","occupied","unknown"]) $("real-"+key+"-count").textContent=data.counts?.[key]??data.spots.filter(s=>s.state===key).length;
      const detections=detectionCounts(data);
      $("real-detection-count").textContent=detections.accepted;
      $("real-evidence-summary").textContent=`另有 ${detections.evidence_only} 个低置信待确认框 · 不计入车辆数`;
      const elapsed=Number(data.elapsed_ms), timing=Number.isFinite(elapsed)?`${(elapsed/1000).toFixed(2)} 秒`:"未返回耗时";
      $("real-latency").textContent=`本次耗时 ${timing} · ${data.spots.length} 个标定车位`;
      $("photo-time").textContent=timing;
      $("real-view-hint").textContent="悬浮查看 · 点击固定车位依据";
      message(`已完成当前照片推理：${detections.accepted} 个已接受车辆检测框，另有 ${detections.evidence_only} 个低置信待确认框；${data.spots.length} 个标定车位仍使用全部保留证据判断。观测空闲可能存在漏检，具体误差见评测报告。`);
      status("本次推理已完成","ready");draw();
    } catch(error) {
      if(epoch===ui.epoch){resetResult();message(error.message,true);status("推理未完成","error");}
    } finally {ui.busy=false;updateControls();}
  }

  function polygonPath(points) {
    if(!points?.length)return;
    context.beginPath();points.forEach((p,i)=>i?context.lineTo(p[0],p[1]):context.moveTo(p[0],p[1]));context.closePath();
  }
  function drawInferenceROI(scale) {
    const roi=(ui.result?.photo||ui.photo)?.inference_roi_xywh;
    if(!Array.isArray(roi)||roi.length!==4||!roi.every(Number.isFinite)||roi[2]<=0||roi[3]<=0)return;
    const [x,y,width,height]=roi;
    context.save();
    context.setLineDash([3*scale,4*scale]);context.strokeStyle="#79c8eb";context.lineWidth=1*scale;
    context.strokeRect(x,y,width,height);context.setLineDash([]);
    context.font=`600 ${11*scale}px Arial`;context.textAlign="left";context.textBaseline="top";
    const text="推理区域",labelWidth=context.measureText(text).width+8*scale;
    const labelX=Math.min(x+3*scale,canvas.width-labelWidth),labelY=Math.max(0,y-18*scale);
    context.fillStyle="#183447dc";context.fillRect(labelX,labelY,labelWidth,16*scale);
    context.fillStyle="#a7dff4";context.fillText(text,labelX+4*scale,labelY+2*scale);
    context.restore();
  }
  function draw() {
    context.clearRect(0,0,canvas.width,canvas.height);context.fillStyle="#e5ecee";context.fillRect(0,0,canvas.width,canvas.height);
    if(ui.image&&$("real-layer-image").checked)context.drawImage(ui.image,0,0,canvas.width,canvas.height);
    const scale=Math.max(1,canvas.width/1000);
    if(!ui.result){drawInferenceROI(scale);return;}
    if($("real-layer-spots").checked) {
      for(const spot of ui.result.spots) {
        polygonPath(spot.polygon_px);context.fillStyle=(colors[spot.state]||colors.unknown)+"35";context.fill();context.strokeStyle=colors[spot.state]||colors.unknown;context.lineWidth=1.4*scale;context.stroke();
      }
    }
    if($("real-layer-boxes").checked||$("real-layer-evidence").checked) {
      for(const detection of ui.result.detections) {
        const isAccepted=accepted(detection);
        if(isAccepted?!$("real-layer-boxes").checked:!$("real-layer-evidence").checked)continue;
        const b=detection.bbox_xyxy_px;if(!Array.isArray(b)||b.length!==4)continue;
        const high=ui.result.thresholds?.high??.5,color=isAccepted?(detection.confidence>=high?"#05dcaa":"#ffd15c"):"#ffd15c";
        context.setLineDash(isAccepted?[]:[5*scale,3*scale]);
        context.strokeStyle=color;context.lineWidth=1.8*scale;context.strokeRect(b[0],b[1],b[2]-b[0],b[3]-b[1]);
        context.setLineDash([]);
        const text=(isAccepted?"":"? ")+Number(detection.confidence).toFixed(2),font=10*scale;
        context.font=`600 ${font}px Arial`;context.textAlign="left";context.textBaseline="top";
        const width=context.measureText(text).width+7*scale,x=Math.min(b[0],canvas.width-width),y=Math.max(0,b[1]-14*scale);
        context.fillStyle="#183447dc";context.fillRect(x,y,width,14*scale);context.fillStyle=color;context.fillText(text,x+3*scale,y+2*scale);
      }
    }
    drawInferenceROI(scale);
    const selected=ui.result.spots.find(s=>s.spot_id===(ui.selectedSpot||ui.hoverSpot));
    if(selected){polygonPath(selected.polygon_px);context.strokeStyle="#ffffff";context.lineWidth=4*scale;context.stroke();polygonPath(selected.polygon_px);context.strokeStyle="#173b51";context.lineWidth=2*scale;context.stroke();}
  }

  function inside(point,polygon) {
    let contained=false;
    for(let i=0,j=polygon.length-1;i<polygon.length;j=i++) {
      const a=polygon[i],b=polygon[j];
      if((a[1]>point[1])!==(b[1]>point[1])&&point[0]<(b[0]-a[0])*(point[1]-a[1])/(b[1]-a[1])+a[0])contained=!contained;
    }
    return contained;
  }
  function pointer(event) {const rect=canvas.getBoundingClientRect();return [(event.clientX-rect.left)/rect.width*canvas.width,(event.clientY-rect.top)/rect.height*canvas.height];}
  function spotAt(point) {return ui.result?.spots.find(spot=>inside(point,spot.polygon_px));}

  function showSpot(spot) {
    $("clear-spot").hidden=!ui.selectedSpot;
    if(!spot){$("spot-detail-content").innerHTML='<p class="spot-empty">'+(ui.result?'悬浮或点击照片里的车位，查看当前车辆框的证据。':'运行检测后，悬浮或点击照片里的车位，查看当前车辆框的证据。')+'</p>';return;}
    const t=ui.result.thresholds||{low:.2,high:.5,overlap_fraction:.05};
    const fraction=spot.observation_fraction,hasFraction=Number.isFinite(fraction)&&fraction>=0&&fraction<=1;
    const hasCoverage=hasFraction||typeof spot.fully_observed==="boolean";
    const coverageStatus=spot.fully_observed===true?"完整覆盖":spot.fully_observed===false?"未完整覆盖":"未提供覆盖状态";
    const coverage=hasCoverage?`<p class="spot-observation" data-fully-observed="${typeof spot.fully_observed==="boolean"?spot.fully_observed:""}" data-observation-fraction="${hasFraction?fraction:""}">推理区域覆盖本车位：${hasFraction?(fraction*100).toFixed(2)+"%":"未提供比例"}（${coverageStatus}）。覆盖比例是车位面积占比，不是识别正确率。</p>`:"";
    const evidence=Array.isArray(spot.evidence)?spot.evidence:[];
    const rows=evidence.map(e=>{
      const detection=ui.result.detections.find(d=>d.detection_id===e.detection_id)||e,isAccepted=accepted(detection);
      return `<div class="real-evidence-row" data-detection-id="${escapeHTML(e.detection_id)}" data-confidence="${e.confidence}" data-center-inside="${e.center_inside}" data-overlap-fraction="${e.overlap_fraction}" data-used="${e.used}" data-accepted="${isAccepted}"><span class="real-evidence-id">${escapeHTML(e.detection_id)}</span><div><span>检测状态</span><b class="${isAccepted?"":"pending-evidence"}">${isAccepted?"已接受检测":"低置信待确认 · 不计入车辆数"}</b></div><div><span>置信度</span><b>${Number(e.confidence).toFixed(4)}</b></div><div><span>车辆框中心</span><b>${e.center_inside?"位于本车位内":"位于本车位外"}</b></div><div><span>相交 / 本车位面积</span><b>${(Number(e.overlap_fraction)*100).toFixed(2)}%</b></div><small>${e.used?"用于当前状态判定":"其他相交证据"} · ${Number(e.overlap_fraction)+1e-12>=t.overlap_fraction?"达到":"低于"} ${(t.overlap_fraction*100).toFixed(2)}% 相交阈值</small></div>`;
    }).join("");
    $("spot-detail-content").innerHTML=`<div class="spot-summary"><strong>${escapeHTML(spot.spot_id)}</strong><span class="spot-state"><i class="dot ${escapeHTML(spot.state)}"></i>${states[spot.state]||"未知"}</span><p>${escapeHTML(reasons[spot.reason]||spot.reason||"判定依据来自当前检测结果。")}</p>${coverage}</div><div class="spot-evidence">${rows||'<p class="spot-empty">当前没有与本车位相交的保留车辆框。</p>'}</div><div class="evidence-threshold-note">车辆检测 ≥ ${Number(detectionThreshold()).toFixed(2)}<br>占用证据保留 ≥ ${Number(t.low).toFixed(2)} · 高置信占用 ≥ ${Number(t.high).toFixed(2)}<br>待确认框仍参与占用判断，不因隐藏图层而被删除。<br>${ui.selectedSpot?"详情已固定；可按 Esc 或右上角 × 取消。":"点击车位可固定详情。"}</div>`;
  }
  function clearSelection(){ui.selectedSpot=null;ui.hoverSpot=null;showSpot(null);draw();}

  canvas.addEventListener("mousemove",event=>{
    const point=pointer(event);$("photo-coordinate").textContent=`X ${point[0].toFixed(1)} / Y ${point[1].toFixed(1)} px`;
    if(ui.busy||ui.selectedSpot)return;
    const spot=spotAt(point);ui.hoverSpot=spot?.spot_id||null;showSpot(spot);draw();
  });
  canvas.addEventListener("mouseleave",()=>{$("photo-coordinate").textContent="X — / Y — px";if(!ui.selectedSpot){ui.hoverSpot=null;showSpot(null);draw();}});
  canvas.addEventListener("click",event=>{if(ui.busy)return;const spot=spotAt(pointer(event));ui.selectedSpot=spot?.spot_id||null;ui.hoverSpot=null;showSpot(spot);draw();});
  $("clear-spot").addEventListener("click",clearSelection);
  document.addEventListener("keydown",event=>{if(event.key==="Escape")clearSelection();});
  for(const id of ["real-layer-image","real-layer-spots","real-layer-boxes","real-layer-evidence"]) $(id).addEventListener("change",draw);
  for(const key of filterKeys) $(key+"-filter").addEventListener("change",()=>filterPhotos(ui.photo?.photo_id));
  $("photo-select").addEventListener("change",()=>choosePhoto($("photo-select").value));
  $("analyze-photo").addEventListener("click",analyze);
  function step(direction){const i=ui.filtered.findIndex(p=>p.photo_id===ui.photo?.photo_id);const next=ui.filtered[i+direction];if(next)choosePhoto(next.photo_id);}
  $("previous-photo").addEventListener("click",()=>step(-1));$("next-photo").addEventListener("click",()=>step(1));

  async function initialize() {
    try {
      const data=await request("/api/real/photos");
      if(!Array.isArray(data.photos)||!data.photos.length)throw new Error("当前演示包没有真实照片清单，请下载包含真实照片的完整版本。");
      ui.photos=data.photos;setModel(data.model);
      for(const key of filterKeys) {
        const selector=$(key+"-filter"),values=[...new Set(ui.photos.map(p=>filterValue(p,key)).filter(v=>v!==undefined&&v!==null))].sort();
        for(const value of values){const option=document.createElement("option");option.value=value;option.textContent=key==="weather"?weather(value):key==="split"?split(value):value;selector.append(option);}
      }
      filterPhotos(ui.photos.find(p=>p.split==="external_test")?.photo_id||ui.photos.find(p=>p.split==="test")?.photo_id);
    } catch(error){message(error.message,true);status("照片清单不可用","error");updateControls();}
  }
  initialize();
})();
