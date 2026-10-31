let requestToken = "";
let appState = null;
let currentPlan = null;
let currentApproval = null;
let latestReceipt = null;
if(new URLSearchParams(location.search).get("desktop")==="1")document.documentElement.classList.add("desktop-mode");
const desktopSettingsRequested=new URLSearchParams(location.search).get("desktop")==="1"&&location.hash==="#settings";
const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const date = (seconds) => seconds ? new Date(seconds * 1000).toLocaleString() : "—";

async function api(path, body) {
  const options = body === undefined ? {} : {method:"POST",headers:{"Content-Type":"application/json","X-RBA-Token":requestToken},body:JSON.stringify(body)};
  const response = await fetch(path, options);
  const data = await response.json();
  if (!response.ok) { const error = new Error(data.error || `Request failed (${response.status})`); error.data = data; throw error; }
  return data;
}

function message(id, text, error=false) { const el=$(id); el.textContent=text; el.classList.toggle("error",error); }
function selectedDocument(){return appState?.documents.find(d=>d.target_id===$("target").value)}
function normalizePlan(plan){return plan ? {...plan,version:plan.version ?? plan.current_version} : plan;}
function bindBusy(id,label,callback){
  const button=$(id);
  button.addEventListener("click",async()=>{
    const wasDisabled=button.disabled,wasBusy=button.getAttribute("aria-busy"),wasLabel=button.getAttribute("aria-label");
    button.disabled=true;button.setAttribute("aria-busy","true");button.setAttribute("aria-label",`${label}…`);
    try{await callback();}finally{
      if(wasBusy===null)button.removeAttribute("aria-busy");else button.setAttribute("aria-busy",wasBusy);
      if(wasLabel===null)button.removeAttribute("aria-label");else button.setAttribute("aria-label",wasLabel);
      button.disabled=wasDisabled;renderPlan();
    }
  });
}

function renderPlan() {
  const plan=currentPlan;
  $("charCount").textContent=`${$("payload").value.length.toLocaleString()} characters`;
  if(!plan){
    $("planState").textContent="NO PLAN"; $("approvalState").textContent="AWAITING REVIEW";
    $("versionValue").textContent="—"; $("versionTitle").textContent="Plan version"; $("fingerprint").textContent="No fingerprint yet";
    $("previewTarget").textContent="No destination selected"; $("revisionLine").textContent="Expected revision —";
    $("beforeContent").textContent="Prepare a plan to load the current destination text.";
    $("afterContent").textContent="The exact text you approve will be sent to the local fake adapter.";
    $("effectText").textContent="One document update at the reviewed revision.";
    $("approveVersion").textContent="—"; $("approve").disabled=true; $("saveRevision").disabled=true;
    $("apply").disabled=true; $("mutateAfterApproval").disabled=true;
    return;
  }
  const matches=currentApproval && currentApproval.version===plan.version && currentApproval.fingerprint===plan.fingerprint && currentApproval.expires_at>Date.now()/1000;
  const staleApproval=currentApproval && !matches;
  $("planState").textContent=`VERSION ${String(plan.version).padStart(2,"0")}`;
  $("approvalState").textContent=matches?"APPROVED · EXPIRES SOON":staleApproval?"APPROVAL STALE":"AWAITING REVIEW";
  $("approvalState").classList.toggle("muted",!matches);
  $("versionValue").textContent=plan.version; $("versionTitle").textContent=`Immutable plan version ${plan.version}`;
  $("fingerprint").textContent=`SHA-256 ${plan.fingerprint}`;
  $("previewTarget").textContent=`${plan.target_name} · ${plan.target_id}`;
  $("revisionLine").textContent=`Expected revision ${plan.expected_revision} → ${Number(plan.expected_revision)+1}`;
  $("beforeContent").textContent=plan.expected_before;
  $("afterContent").textContent=plan.payload;
  $("effectText").textContent=`Replace ${plan.target_id} content and advance revision ${plan.expected_revision} to ${Number(plan.expected_revision)+1}.`;
  $("approveVersion").textContent=plan.version;
  $("approve").disabled=!$("reviewed").checked || !appState.settings.can_approve;
  $("saveRevision").disabled=!appState.settings.can_prepare || ($("payload").value===plan.payload && $("target").value===plan.target_id);
  $("apply").disabled=!currentApproval || !appState.settings.can_execute || (!$("skipReadback").checked && !appState.settings.can_verify);
  $("apply").innerHTML=staleApproval?"Try stale approval <span aria-hidden=\"true\">→</span>":"Apply approved change <span aria-hidden=\"true\">→</span>";
  $("mutateAfterApproval").disabled=!currentApproval || !appState.settings.can_prepare;
  $("approvalExpiry").textContent=matches?`Approved ${date(currentApproval.approved_at)} · expires ${date(currentApproval.expires_at)}`:staleApproval?"This approval no longer matches the current plan version.":`Approval expires after ${Math.round(appState.settings.approval_ttl_seconds/60)} minutes.`;
}

function renderReceipts() {
  const approvals=(appState?.approvals||[]).map(a=>({kind:"APPROVAL",id:a.approval_id,status:a.expires_at>Date.now()/1000?"APPROVED":"EXPIRED",detail:`Version ${a.version} · ${a.fingerprint.slice(0,16)}…`,time:a.approved_at,cls:a.expires_at>Date.now()/1000?"":"unverified"}));
  const actions=(appState?.receipts||[]).map(r=>({kind:r.tool_status==="not_started"?"REJECTION":r.parent_receipt_id?"READBACK":"ACTION",id:r.receipt_id,status:r.status.toUpperCase(),detail:r.detail,time:r.created_at,cls:r.status}));
  const rows=[...approvals,...actions].sort((a,b)=>b.time-a.time).slice(0,18);
  $("receiptList").innerHTML=rows.length?rows.map(r=>`<div class="receipt-item"><span class="receipt-type">${esc(r.kind)}</span><span class="receipt-time">${esc(date(r.time))}</span><div class="receipt-copy"><b>${esc(r.id)}</b><span>${esc(r.detail)}</span></div><span class="receipt-status ${esc(r.cls)}">${esc(r.status)}</span></div>`).join(""):'<div class="empty-state">No receipts yet. Prepare a synthetic document change to begin.</div>';
}

function renderResult(receipt){
  latestReceipt=receipt||latestReceipt;
  if(!latestReceipt){return;}
  const banner=$("resultBanner"); banner.classList.remove("verified","unverified","rejected"); banner.classList.add(latestReceipt.status);
  $("resultTitle").textContent=latestReceipt.status==="verified"?"Destination verified":latestReceipt.status==="unverified"?"Action ran, destination unverified":"Action rejected";
  $("resultDetail").textContent=latestReceipt.detail;
  $("verifyAction").hidden=latestReceipt.status!=="unverified" || latestReceipt.tool_status!=="success" || !appState.settings.can_verify;
  $("verifyAction").dataset.receiptId=latestReceipt.receipt_id;
}

async function refresh(){
  appState=await api("/api/state");
  $("canPrepare").checked=appState.settings.can_prepare; $("canApprove").checked=appState.settings.can_approve;
  $("canExecute").checked=appState.settings.can_execute; $("canVerify").checked=appState.settings.can_verify;
  $("ttl").value=String(appState.settings.approval_ttl_seconds);
  if(!currentPlan && appState.plans.length) currentPlan=normalizePlan(appState.plans[0]);
  if(currentPlan){
    currentApproval=appState.approvals.find(a=>a.plan_id===currentPlan.plan_id && a.version===currentPlan.version && a.fingerprint===currentPlan.fingerprint) ||
      appState.approvals.find(a=>a.plan_id===currentPlan.plan_id) || null;
    $("payload").value=currentPlan.payload; $("target").value=currentPlan.target_id;
  }
  $("target").disabled=!appState.settings.can_prepare;
  $("prepare").disabled=!appState.settings.can_prepare;
  if(appState.receipts.length && !latestReceipt) latestReceipt=appState.receipts[0];
  renderPlan(); renderReceipts(); renderResult(latestReceipt);
}

async function prepare(){
  try{const data=await api("/api/plan",{target_id:$("target").value,payload:$("payload").value});currentPlan=data.plan;currentApproval=null;latestReceipt=null;$("reviewed").checked=false;$("resultBanner").className="receipt-banner";$("resultTitle").textContent="No action taken";$("resultDetail").textContent="The local adapter has not changed a document.";$("verifyAction").hidden=true;renderPlan();await refresh();message("planMessage","Prepared a new version. Review the exact before and after text.");}
  catch(e){message("planMessage",e.message,true);}
}

async function revise(payload=$("payload").value,targetId=$("target").value){
  if(!currentPlan)return prepare();
  try{const data=await api("/api/plan/revise",{plan_id:currentPlan.plan_id,version:currentPlan.version,target_id:targetId,payload});currentPlan=data.plan;$("payload").value=currentPlan.payload;$("target").value=currentPlan.target_id;$("reviewed").checked=false;if(currentApproval){$("resultBanner").className="receipt-banner unverified";$("resultTitle").textContent="Approval stale";$("resultDetail").textContent="The plan changed after approval. Try the old approval to record a rejection.";$("verifyAction").hidden=true;}renderPlan();await refresh();message("planMessage",`Saved version ${currentPlan.version}. Prior approvals remain in the audit trail and are stale.`);}
  catch(e){message("planMessage",e.message,true);}
}

async function approve(){
  if(!currentPlan || !$("reviewed").checked)return;
  try{const data=await api("/api/approve",{plan_id:currentPlan.plan_id,version:currentPlan.version,fingerprint:currentPlan.fingerprint});currentApproval=data.approval;$("reviewed").checked=false;await refresh();message("planMessage",`Approved version ${currentPlan.version}. This approval expires at ${date(currentApproval.expires_at)}.`);}
  catch(e){message("planMessage",e.message,true);}
}

async function applyApproved(){
  if(!currentApproval)return;
  try{const data=await api("/api/execute",{approval_id:currentApproval.approval_id,readback:!$("skipReadback").checked});renderResult(data.receipt);await refresh();renderResult(data.receipt);}
  catch(e){if(e.data?.receipt){renderResult(e.data.receipt);await refresh();renderResult(e.data.receipt);}else{$("resultTitle").textContent="Action blocked";$("resultDetail").textContent=e.message;$("resultBanner").className="receipt-banner rejected";}}
}

document.addEventListener("DOMContentLoaded",async()=>{
  try{requestToken=(await api("/api/session")).token;await refresh();}catch(e){message("planMessage",`Local service unavailable: ${e.message}`,true);}
  $("payload").addEventListener("input",()=>{renderPlan();$("afterContent").textContent=$("payload").value;$("charCount").textContent=`${$("payload").value.length.toLocaleString()} characters`;});
  $("target").addEventListener("change",renderPlan);
  $("reviewed").addEventListener("change",renderPlan);
  $("skipReadback").addEventListener("change",renderPlan);
  bindBusy("prepare","Preparing",prepare);
  bindBusy("saveRevision","Saving",()=>revise());
  bindBusy("approve","Recording approval",approve);
  bindBusy("apply","Applying",applyApproved);
  bindBusy("mutateAfterApproval","Changing plan",()=>revise(`${currentPlan.payload}\nRevised after approval.`));
  bindBusy("verifyAction","Reading destination",async()=>{try{const data=await api("/api/verify",{receipt_id:$("verifyAction").dataset.receiptId});renderResult(data.receipt);await refresh();renderResult(data.receipt);}catch(e){$("resultDetail").textContent=e.message;}});
  bindBusy("refresh","Refreshing",()=>refresh().catch(e=>message("planMessage",e.message,true)));
  const dialog=$("settingsDialog");$("settingsOpen").addEventListener("click",()=>dialog.showModal());
  $("settingsClose").addEventListener("click",()=>dialog.close());$("cancelSettings").addEventListener("click",()=>dialog.close());
  $("settingsForm").addEventListener("submit",async e=>{e.preventDefault();try{const result=await api("/api/settings",{approval_ttl_seconds:Number($("ttl").value),can_prepare:$("canPrepare").checked,can_approve:$("canApprove").checked,can_execute:$("canExecute").checked,can_verify:$("canVerify").checked});dialog.close();appState.settings=result;await refresh();}catch(err){message("settingsMessage",err.message,true);}});
  dialog.addEventListener("click",e=>{if(e.target===dialog)dialog.close();});
  if(desktopSettingsRequested)$("settingsOpen").click();
});
