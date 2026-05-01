import os

OUTPUT_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "docs", "index.html")

HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>My Notes & Reminders</title>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700;800&display=swap" rel="stylesheet">
<script src="https://www.gstatic.com/firebasejs/10.12.0/firebase-app-compat.js"></script>
<script src="https://www.gstatic.com/firebasejs/10.12.0/firebase-auth-compat.js"></script>
<script src="https://www.gstatic.com/firebasejs/10.12.0/firebase-firestore-compat.js"></script>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.1/dist/chart.umd.min.js"></script>
<!-- Google Identity Services for Calendar integration -->
<script src="https://accounts.google.com/gsi/client" async defer></script>
<script>const GOOGLE_CLIENT_ID = 'GOOGLE_CLIENT_ID_PLACEHOLDER';</script>
<script>
// ── Google Calendar Integration ──────────────────────────────────────────────
let _gcalToken = null;
let _gcalTokenExpiry = 0;
// Maps reminder app id → Google Calendar event id
const _gcalEventMap = JSON.parse(localStorage.getItem('_gcalEventMap')||'{}');

function _gcalSaveMap(){ try{ localStorage.setItem('_gcalEventMap', JSON.stringify(_gcalEventMap)); }catch(e){} }

function _gcalToast(msg, type='success'){
  if(typeof toast === 'function') toast(msg, type);
  else console.log('[GCal]', msg);
}

// Get or refresh access token, then run callback(token)
let _gcalTokenPending = false; // prevent duplicate token requests
function _gcalWithToken(callback){
  if(!GOOGLE_CLIENT_ID) return;
  if(_gcalToken && Date.now() < _gcalTokenExpiry){
    callback(_gcalToken);
    return;
  }
  if(_gcalTokenPending) return; // already requesting, skip duplicate
  _gcalTokenPending = true;
  const tokenClient = google.accounts.oauth2.initTokenClient({
    client_id: GOOGLE_CLIENT_ID,
    scope: 'https://www.googleapis.com/auth/calendar.events',
    callback: (response) => {
      _gcalTokenPending = false;
      if(response.error){
        // On first-time auth, retry with explicit consent prompt
        if(response.error === 'interaction_required' || response.error === 'access_denied'){
          tokenClient.requestAccessToken({ prompt: 'consent' });
        } else {
          console.warn('GCal auth error:', response.error);
          _gcalToast('Google Calendar auth failed: ' + response.error, 'error');
        }
        return;
      }
      _gcalToken = response.access_token;
      _gcalTokenExpiry = Date.now() + 55 * 60 * 1000;
      callback(_gcalToken);
    }
  });
  tokenClient.requestAccessToken({ prompt: '' });
}

// Create a calendar event and store the event id mapped to reminder id
async function _gcalCreateEvent(accessToken, remId, title, startDateTime, endDateTime, description, remindMinutes){
  const overrides = [];
  if(remindMinutes > 0){
    overrides.push({ method: 'email', minutes: remindMinutes });
    overrides.push({ method: 'popup', minutes: Math.min(remindMinutes, 30) });
  }
  const event = {
    summary: title,
    description: description || '',
    start: { dateTime: startDateTime, timeZone: Intl.DateTimeFormat().resolvedOptions().timeZone },
    end:   { dateTime: endDateTime,   timeZone: Intl.DateTimeFormat().resolvedOptions().timeZone },
    reminders: { useDefault: false, overrides }
  };
  const res = await fetch('https://www.googleapis.com/calendar/v3/calendars/primary/events', {
    method: 'POST',
    headers: { 'Authorization': 'Bearer ' + accessToken, 'Content-Type': 'application/json' },
    body: JSON.stringify(event)
  });
  const data = await res.json();
  if(data.id){
    if(remId){ _gcalEventMap[remId] = data.id; _gcalSaveMap(); }
    return data.id;
  } else {
    console.warn('GCal create error:', data.error?.message);
    return null;
  }
}

// Delete a calendar event by reminder id
async function _gcalDeleteEvent(accessToken, remId){
  const eventId = _gcalEventMap[remId];
  if(!eventId) return; // not synced, nothing to delete
  const res = await fetch('https://www.googleapis.com/calendar/v3/calendars/primary/events/' + eventId, {
    method: 'DELETE',
    headers: { 'Authorization': 'Bearer ' + accessToken }
  });
  // 204 = success, 404 = already deleted — both are safe to clean up locally
  if(res.ok || res.status === 404){
    delete _gcalEventMap[remId];
    _gcalSaveMap();
  } else {
    console.warn('GCal delete failed with status:', res.status);
    throw new Error('GCal delete failed: ' + res.status);
  }
}

// PUBLIC: Add reminder to Google Calendar (called on save)
function addReminderToGoogleCalendar(remId, title, startDateTime, endDateTime, description, remindMinutes){
  if(!GOOGLE_CLIENT_ID) return;
  const mins = (remindMinutes !== undefined && remindMinutes !== null) ? parseInt(remindMinutes) : 30;
  _gcalWithToken(token => {
    _gcalCreateEvent(token, remId, title, startDateTime, endDateTime, description, mins)
      .then(id => { if(id) _gcalToast('📅 Synced to Google Calendar','success'); })
      .catch(console.warn);
  });
}

// PUBLIC: Delete reminder from Google Calendar (called on delete)
function deleteReminderFromGoogleCalendar(remId){
  if(!GOOGLE_CLIENT_ID) return;
  _gcalWithToken(token => {
    _gcalDeleteEvent(token, remId)
      .then(() => _gcalToast('🗑️ Removed from Google Calendar', 'success'))
      .catch(e => { console.warn('GCal delete error:', e); });
  });
}

// PUBLIC: Sync ALL existing reminders to Google Calendar at once
function syncAllRemindersToGoogleCalendar(){
  if(!GOOGLE_CLIENT_ID){ _gcalToast('Google Calendar not configured','error'); return; }
  const today = new Date().toISOString().slice(0,10);
  const all = (window.DATA?.reminders||[]).filter(r=>{
    if(!r.due) return false;
    const dueDate = r.due.split(' ')[0];
    if(dueDate < today && r.sent) return false; // past AND already notified → skip
    if(_gcalEventMap[r.id]) return false;        // already synced → skip
    return dueDate >= today;                     // only future/today reminders
  });
  if(!all.length){ _gcalToast('No active reminders to sync','error'); return; }
  _gcalWithToken(async token => {
    let count = 0;
    for(const r of all){
      try{
        const [dp,tp] = r.due.split(' ');
        const sISO = dp+'T'+(tp||'09:00')+':00';
        const eD = new Date(sISO); eD.setHours(eD.getHours()+1);
        const pd = n=>String(n).padStart(2,'0');
        const eISO = eD.getFullYear()+'-'+pd(eD.getMonth()+1)+'-'+pd(eD.getDate())+'T'+pd(eD.getHours())+':'+pd(eD.getMinutes())+':00';
        const id = await _gcalCreateEvent(token, r.id, r.title||r.text||'Reminder', sISO, eISO, r.body||'', 30);
        if(id) count++;
        await new Promise(res=>setTimeout(res,300)); // small delay to avoid rate limit
      }catch(e){ console.warn('Sync error for',r.id,e); }
    }
    _gcalToast('📅 Synced '+count+' reminder'+(count!==1?'s':'')+' to Google Calendar','success');
  });
}

// Create an all-day Google Calendar event (used for Tasks & Important Dates which have no time)
async function _gcalCreateAllDayEvent(accessToken, entryId, title, dateStr, description){
  // GCal all-day events need end = day after start
  const startD = new Date(dateStr + 'T00:00:00');
  const endD   = new Date(startD); endD.setDate(endD.getDate()+1);
  const pd = n=>String(n).padStart(2,'0');
  const endStr = endD.getFullYear()+'-'+pd(endD.getMonth()+1)+'-'+pd(endD.getDate());
  const event = {
    summary: title,
    description: description || '',
    start: { date: dateStr },
    end:   { date: endStr },
    reminders: { useDefault: false, overrides: [
      { method: 'popup', minutes: 60 },
      { method: 'email', minutes: 60 }
    ]}
  };
  const res = await fetch('https://www.googleapis.com/calendar/v3/calendars/primary/events', {
    method: 'POST',
    headers: { 'Authorization': 'Bearer ' + accessToken, 'Content-Type': 'application/json' },
    body: JSON.stringify(event)
  });
  const data = await res.json();
  if(data.id){
    if(entryId){ _gcalEventMap[entryId] = data.id; _gcalSaveMap(); }
    return data.id;
  } else {
    console.warn('GCal all-day create error:', data.error?.message);
    return null;
  }
}

// PUBLIC: Add important date to Google Calendar
function addImpDateToGoogleCalendar(entryId, title, dateStr, note){
  if(!GOOGLE_CLIENT_ID) return;
  _gcalWithToken(token => {
    _gcalCreateAllDayEvent(token, entryId, '📅 ' + title, dateStr, note || '')
      .then(id => { if(id) _gcalToast('📅 Important date synced to Google Calendar','success'); })
      .catch(console.warn);
  });
}

// PUBLIC: Delete important date from Google Calendar
function deleteImpDateFromGoogleCalendar(entryId){
  if(!GOOGLE_CLIENT_ID) return;
  _gcalWithToken(token => {
    _gcalDeleteEvent(token, entryId)
      .then(() => _gcalToast('🗑️ Important date removed from Google Calendar','success'))
      .catch(e => console.warn('GCal imp delete error:', e));
  });
}

// PUBLIC: Add task to Google Calendar as all-day event
function addTaskToGoogleCalendar(taskId, text, dateStr, priority){
  if(!GOOGLE_CLIENT_ID) return;
  const label = priority === 'high' ? '🔴 ' : priority === 'low' ? '🟢 ' : '🟡 ';
  _gcalWithToken(token => {
    _gcalCreateAllDayEvent(token, taskId, label + text, dateStr, 'Priority: ' + (priority||'medium'))
      .then(id => { if(id) _gcalToast('✅ Task synced to Google Calendar','success'); })
      .catch(console.warn);
  });
}

// PUBLIC: Delete task from Google Calendar
function deleteTaskFromGoogleCalendar(taskId){
  if(!GOOGLE_CLIENT_ID) return;
  _gcalWithToken(token => {
    _gcalDeleteEvent(token, taskId)
      .then(() => _gcalToast('🗑️ Task removed from Google Calendar','success'))
      .catch(e => console.warn('GCal task delete error:', e));
  });
}

// AUTO-SYNC: Called after Firebase data loads — syncs any existing unsynced future reminders
function _gcalAutoSyncOnLoad(){
  if(!GOOGLE_CLIENT_ID) return;
  // Wait for Firebase data to fully populate window.DATA
  setTimeout(()=>{
    const today = new Date().toISOString().slice(0,10);

    // ── Reminders ──────────────────────────────────────────
    const unsyncedRem = (window.DATA?.reminders||[]).filter(r=>{
      if(!r.due) return false;
      if(_gcalEventMap[r.id]) return false;
      const dueDate = r.due.split(' ')[0];
      return dueDate >= today;
    });

    // ── Important Dates ────────────────────────────────────
    const unsyncedImp = (window.DATA?.important_dates||[]).filter(e=>{
      if(!e.date) return false;
      if(_gcalEventMap[e.id]) return false;
      return e.date >= today;
    });

    // ── Tasks (non-done, future/today date) ────────────────
    const unsyncedTasks = (window.DATA?.tasknotes||[]).filter(t=>{
      if(!t.date) return false;
      if(t.done) return false;
      if(_gcalEventMap[t.id]) return false;
      return t.date >= today;
    });

    const totalUnsynced = unsyncedRem.length + unsyncedImp.length + unsyncedTasks.length;
    if(!totalUnsynced) return;

    _gcalWithToken(async token=>{
      let count = 0;
      const pd = n=>String(n).padStart(2,'0');

      // Sync reminders
      for(const r of unsyncedRem){
        try{
          const [dp,tp] = r.due.split(' ');
          const sISO = dp+'T'+(tp||'09:00')+':00';
          const eD = new Date(sISO); eD.setHours(eD.getHours()+1);
          const eISO = eD.getFullYear()+'-'+pd(eD.getMonth()+1)+'-'+pd(eD.getDate())+'T'+pd(eD.getHours())+':'+pd(eD.getMinutes())+':00';
          const id = await _gcalCreateEvent(token, r.id, r.title||r.text||'Reminder', sISO, eISO, r.body||'', 30);
          if(id) count++;
          await new Promise(res=>setTimeout(res,300));
        }catch(e){ console.warn('Auto-sync reminder error for',r.id,e); }
      }

      // Sync important dates
      for(const e of unsyncedImp){
        try{
          const id = await _gcalCreateAllDayEvent(token, e.id, '📅 '+e.title, e.date, e.note||'');
          if(id) count++;
          await new Promise(res=>setTimeout(res,300));
        }catch(err){ console.warn('Auto-sync imp date error for',e.id,err); }
      }

      // Sync tasks
      for(const t of unsyncedTasks){
        try{
          const label = t.priority==='high'?'🔴 ':t.priority==='low'?'🟢 ':'🟡 ';
          const id = await _gcalCreateAllDayEvent(token, t.id, label+(t.text||'Task'), t.date, 'Priority: '+(t.priority||'medium'));
          if(id) count++;
          await new Promise(res=>setTimeout(res,300));
        }catch(err){ console.warn('Auto-sync task error for',t.id,err); }
      }

      if(count>0) _gcalToast('📅 Auto-synced '+count+' item'+(count!==1?'s':'')+' to Google Calendar','success');
    });
  }, 1500); // 1.5s delay to let Firebase data settle
}
</script>
<style>
*,*::before,*::after{box-sizing:border-box;margin:0;padding:0}
html{scroll-behavior:smooth}

/* -- THEME VARIABLES --------------------------------- */
body.theme-cream {
  --bg:       #f2ede4;
  --sidebar:  #d6c9b0;
  --s2:       #d4c4a8;
  --border:   #c8b48a;
  --border2:  #b8a070;
  --text:     #1c1208;
  --text2:    #3a2810;
  --muted:    #6a5030;
  --accent:   #7a4e1e;
  --accent2:  #96641e;
  --green:    #1a6a30;
  --red:      #b03030;
  --blue:     #1a4a88;
  --over-bg:  rgba(40,20,0,.78);
}
body.theme-beige {
  --bg:       #f5f0e8;
  --sidebar:  #ede6d8;
  --s2:       #e0d6c4;
  --border:   #d4c8b0;
  --border2:  #b8a888;
  --text:     #1a1410;
  --text2:    #382a1e;
  --muted:    #685840;
  --accent:   #6a48a8;
  --accent2:  #8868c8;
  --green:    #1a7a48;
  --red:      #b03838;
  --blue:     #2a58a8;
  --over-bg:  rgba(20,10,30,.65);
}
body.theme-midnight {
  --bg:       #141920;
  --sidebar:  #1a2130;
  --s2:       #1e2838;
  --border:   #252e40;
  --border2:  #304060;
  --text:     #e0e8f4;
  --text2:    #90a8c8;
  --muted:    #506880;
  --accent:   #e8a84a;
  --accent2:  #d4724a;
  --green:    #5aaa70;
  --red:      #e05050;
  --blue:     #5a8abf;
  --over-bg:  rgba(0,0,0,.85);
}
body.theme-ember {
  --bg:       #0f0d0b;
  --sidebar:  #161210;
  --s2:       #1e1a16;
  --border:   #2a2018;
  --border2:  #3a2a1a;
  --text:     #e0c8a8;
  --text2:    #a08058;
  --muted:    #685038;
  --accent:   #d4724a;
  --accent2:  #e8a84a;
  --green:    #5a8040;
  --red:      #c05030;
  --blue:     #6080a0;
  --over-bg:  rgba(0,0,0,.88);
}
body.theme-rose {
  --bg:       #fdf8fa;
  --sidebar:  #f4ecf0;
  --s2:       #eedee6;
  --border:   #dcc8d4;
  --border2:  #c8a8b8;
  --text:     #1a0a12;
  --text2:    #3a1828;
  --muted:    #6a4858;
  --accent:   #a04878;
  --accent2:  #b86090;
  --green:    #1a7a40;
  --red:      #b03030;
  --blue:     #2a5a90;
  --over-bg:  rgba(60,10,30,.65);
}
body.theme-ocean {
  --bg:       #060e12;
  --sidebar:  #0a1a1e;
  --s2:       #0e2028;
  --border:   #153038;
  --border2:  #1a4050;
  --text:     #d8f0e8;
  --text2:    #70a898;
  --muted:    #3a7060;
  --accent:   #00d2b4;
  --accent2:  #00b4a0;
  --green:    #00dc8c;
  --red:      #ff6868;
  --blue:     #64b4ff;
  --over-bg:  rgba(0,8,12,.9);
}
body.theme-arctic {
  --bg:       #f0f2f8;
  --sidebar:  #e4e8f0;
  --s2:       #dce0ea;
  --border:   #c8ccd8;
  --border2:  #a8b0c0;
  --text:     #0e1830;
  --text2:    #283050;
  --muted:    #586888;
  --accent:   #384870;
  --accent2:  #485880;
  --green:    #186838;
  --red:      #b03030;
  --blue:     #285898;
  --over-bg:  rgba(20,24,40,.6);
}

body{
  font-family:'Inter',sans-serif;
  background:var(--bg);color:var(--text);
  height:100vh;overflow:hidden;transition:background 0.3s,color 0.3s
}

/* -- LAYOUT ---------------------------------------- */
.layout{display:flex;height:100vh;overflow:hidden}

aside{
  width:232px;flex-shrink:0;background:rgba(214,201,176,.85);
  backdrop-filter:blur(16px);-webkit-backdrop-filter:blur(16px);
  border-right:1px solid rgba(200,180,138,.3);
  display:flex;flex-direction:column;
  position:fixed;top:0;left:0;bottom:0;z-index:50;
  overflow-y:auto;transition:all 0.3s ease
}
body.theme-cream aside{
  background:rgba(214,201,176,.88);
  border-right:1px solid rgba(184,164,122,.3);
  box-shadow:3px 0 24px rgba(0,0,0,.08)
}
body.theme-beige aside{background:rgba(237,230,216,.85);border-right:1px solid rgba(212,200,176,.3)}
body.theme-midnight aside{background:rgba(26,33,48,.88);border-right:1px solid rgba(37,46,64,.4)}
body.theme-ember aside{background:rgba(22,18,16,.9);border-right:1px solid rgba(42,32,24,.4)}

/* Rose Quartz overrides */
body.theme-rose aside{background:rgba(244,236,240,.92);border-right:1px solid rgba(200,160,180,.2);box-shadow:3px 0 24px rgba(100,40,60,.06)}
body.theme-rose .topbar{background:rgba(248,240,244,.85);border-bottom:1px solid rgba(200,160,180,.2);box-shadow:0 1px 12px rgba(100,40,60,.05)}
body.theme-rose .nav-item.active{background:rgba(176,96,144,.12);color:var(--accent)}
body.theme-rose .btn{color:#fff}
body.theme-rose .page-title{color:#4a2838}
body.theme-rose .clock-block{background:rgba(0,0,0,.03);border-color:rgba(0,0,0,.04)}
body.theme-rose .clock-time{color:#4a2838}
body.theme-rose .ctitle{color:#b06090}
body.theme-rose .stat-num{color:#b06090}
body.theme-rose .db-new-btn{background:#b06090}
body.theme-rose .db-new-btn:hover{background:#984878}
body.theme-rose .db-filter-btn.active{background:rgba(176,96,144,.1);color:var(--accent)}
body.theme-rose .db-filter-btn.active .db-filter-count{background:rgba(176,96,144,.1);color:var(--accent)}
body.theme-rose .dh-day{color:var(--accent)}
body.theme-rose .dh-mon{color:var(--accent)}
body.theme-rose .dh-ecount{background:rgba(176,96,144,.1);color:var(--accent)}
body.theme-rose .db-entry-time{color:var(--accent)}
body.theme-rose .db-compose-dt{color:var(--accent);background:rgba(176,96,144,.1)}
body.theme-rose .ncard.pinned-card{border-top:3px solid #b06090}
body.theme-rose .shop-folder.active{background:rgba(176,96,144,.1);color:var(--accent)}

/* Ocean Depths overrides */
body.theme-ocean aside{background:rgba(10,26,30,.92);border-right:1px solid rgba(0,210,180,.08);backdrop-filter:blur(16px)}
body.theme-ocean .topbar{background:rgba(8,18,22,.88);border-bottom:1px solid rgba(0,210,180,.06);box-shadow:0 1px 12px rgba(0,0,0,.2)}
body.theme-ocean .nav-item.active{background:rgba(0,210,180,.1);color:var(--accent)}
body.theme-ocean .btn{color:#060e12;background:var(--accent)}
body.theme-ocean .page-title{color:#70e8d4}
body.theme-ocean .clock-block{background:rgba(255,255,255,.04);border-color:rgba(255,255,255,.06)}
body.theme-ocean .clock-block:hover{background:rgba(255,255,255,.07)}
body.theme-ocean .clock-time{color:#b8e0d8}
body.theme-ocean .topbar-sync,body.theme-ocean .topbar-sync{background:rgba(255,255,255,.04);border-color:rgba(255,255,255,.05)}
body.theme-ocean .topbar-avatar{background:rgba(0,210,180,.12);border-color:rgba(0,210,180,.15);color:#00d2b4}
body.theme-ocean .ctitle{color:#00d2b4}
body.theme-ocean .stat-num{color:#00d2b4}
body.theme-ocean .ncard,body.theme-ocean .fin-card,body.theme-ocean .tan-item,body.theme-ocean .fin-sum-card,body.theme-ocean .stat-card{background:rgba(6,14,18,.7);backdrop-filter:blur(12px);border-color:rgba(0,210,180,.1)}
body.theme-ocean .ncard:hover,body.theme-ocean .fin-card:hover,body.theme-ocean .tan-item:hover{border-color:rgba(0,210,180,.3)}
body.theme-ocean .ncard.pinned-card{border-top:3px solid #00d2b4}
body.theme-ocean .db-new-btn{background:#00b4a0}
body.theme-ocean .db-new-btn:hover{background:#009a88}
body.theme-ocean .db-filter-btn.active{background:rgba(0,210,180,.1);color:var(--accent)}
body.theme-ocean .db-filter-btn.active .db-filter-count{background:rgba(0,210,180,.1);color:var(--accent)}
body.theme-ocean .db-date-header{background:var(--bg);border-bottom-color:rgba(0,210,180,.08)}
body.theme-ocean .dh-day{color:var(--accent)}
body.theme-ocean .dh-mon{color:var(--accent)}
body.theme-ocean .dh-ecount{background:rgba(0,210,180,.1);color:var(--accent)}
body.theme-ocean .db-entry-time{color:var(--accent)}
body.theme-ocean .db-entry:hover{background:rgba(0,210,180,.03)}
body.theme-ocean .db-compose-dt{color:var(--accent);background:rgba(0,210,180,.08)}
body.theme-ocean .db-tag-trade{background:rgba(0,210,180,.12);color:#00d2b4}
body.theme-ocean .db-tag-personal{background:rgba(100,180,255,.12);color:#64b4ff}
body.theme-ocean .db-tag-idea{background:rgba(255,180,80,.12);color:#ffb450}
body.theme-ocean .db-tag-health{background:rgba(255,100,100,.12);color:#ff6868}
body.theme-ocean .db-tag-work{background:rgba(100,180,255,.12);color:#64b4ff}
body.theme-ocean .db-tag-family{background:rgba(220,140,180,.12);color:#dc8cb4}
body.theme-ocean .shop-folder.active{background:rgba(0,210,180,.08);color:var(--accent)}

/* Arctic Silver overrides */
body.theme-arctic aside{background:rgba(228,232,240,.92);border-right:1px solid rgba(160,170,200,.15);box-shadow:3px 0 24px rgba(20,30,60,.06)}
body.theme-arctic .topbar{background:rgba(236,238,246,.88);border-bottom:1px solid rgba(160,170,200,.15);box-shadow:0 1px 12px rgba(20,30,60,.05)}
body.theme-arctic .nav-item.active{background:rgba(74,90,128,.1);color:var(--accent)}
body.theme-arctic .btn{color:#fff}
body.theme-arctic .page-title{color:#2a3450}
body.theme-arctic .clock-block{background:rgba(0,0,0,.03);border-color:rgba(0,0,0,.04)}
body.theme-arctic .clock-time{color:#2a3450}
body.theme-arctic .ctitle{color:#4a5a80}
body.theme-arctic .stat-num{color:#4a5a80}
body.theme-arctic .db-new-btn{background:#4a5a80}
body.theme-arctic .db-new-btn:hover{background:#3a4a6a}
body.theme-arctic .db-filter-btn.active{background:rgba(74,90,128,.1);color:var(--accent)}
body.theme-arctic .db-filter-btn.active .db-filter-count{background:rgba(74,90,128,.1);color:var(--accent)}
body.theme-arctic .dh-day{color:var(--accent)}
body.theme-arctic .dh-mon{color:var(--accent)}
body.theme-arctic .dh-ecount{background:rgba(74,90,128,.08);color:var(--accent)}
body.theme-arctic .db-entry-time{color:var(--accent)}
body.theme-arctic .db-compose-dt{color:var(--accent);background:rgba(74,90,128,.08)}
body.theme-arctic .ncard.pinned-card{border-top:3px solid #4a5a80}
body.theme-arctic .shop-folder.active{background:rgba(74,90,128,.08);color:var(--accent)}
.sidebar-logo{
  padding:22px 20px 18px;
  font-family:'Inter',sans-serif;font-size:18px;color:var(--accent);
  display:flex;align-items:center;gap:9px;font-weight:800;
  border-bottom:1px solid rgba(200,180,138,.2);letter-spacing:-.5px
}
.sidebar-section{
  padding:20px 16px 6px;font-size:9px;
  text-transform:uppercase;letter-spacing:2.5px;color:var(--muted);font-weight:700;
  opacity:.7
}
.nav-item{
  display:flex;align-items:center;gap:9px;
  padding:9px 18px;font-size:13px;color:var(--text2);font-weight:500;
  cursor:pointer;border-radius:10px;margin:2px 8px;
  transition:all 0.2s ease;border:none;background:none;
  font-family:'Inter',sans-serif;text-align:left;
  width:calc(100% - 16px)
}
.nav-item:hover{background:rgba(255,255,255,.12);color:var(--text);transform:translateX(2px)}
.nav-item.active{background:rgba(139,94,42,.15);color:var(--accent)}
body.theme-beige .nav-item.active{background:rgba(124,92,191,.12);color:var(--accent)}
body.theme-midnight .nav-item.active{background:rgba(232,168,74,.1);color:var(--accent)}
body.theme-ember   .nav-item.active{background:rgba(212,114,74,.1);color:var(--accent)}

/* Status-specific active states */
.nav-item.nav-status-pending.active{background:rgba(59,130,246,.12);color:var(--blue);font-weight:600}
.nav-item.nav-status-pending.active .nav-count{background:rgba(59,130,246,.15);color:var(--blue)}
.nav-item.nav-status-overdue.active{background:rgba(192,64,64,.12);color:var(--red);font-weight:600}
.nav-item.nav-status-overdue.active .nav-count{background:rgba(192,64,64,.15);color:var(--red)}
.nav-item.nav-status-completed.active{background:rgba(42,122,64,.12);color:var(--green);font-weight:600}
.nav-item.nav-status-completed.active .nav-count{background:rgba(42,122,64,.15);color:var(--green)}
.nav-icon{font-size:14px;width:18px;text-align:center}
.nav-count{
  margin-left:auto;background:var(--s2);border-radius:20px;
  padding:1px 8px;font-size:11px;color:var(--text2);font-weight:600
}
.nav-item.active .nav-count{background:rgba(200,160,80,.2);color:var(--accent)}

.sidebar-footer{
  margin-top:auto;padding:14px;
  border-top:1px solid rgba(200,180,138,.2)
}
.sync-pill{
  display:flex;align-items:center;gap:8px;
  background:var(--s2);border-radius:8px;
  padding:9px 12px;font-size:12px;color:var(--text2)
}
.sdot{width:7px;height:7px;border-radius:50%;background:var(--green);flex-shrink:0}
.sdot.syncing{background:var(--accent);animation:blink 1s infinite}
.sdot.error{background:var(--red)}
@keyframes blink{0%,100%{opacity:1}50%{opacity:.2}}
/* 7. Page fade-in */
@keyframes page-fadein{
  from{opacity:0;transform:translateY(8px)}
  to{opacity:1;transform:translateY(0)}
}
.page-entering{animation:page-fadein 0.22s cubic-bezier(.4,0,.2,1) both}

/* -- MAIN ------------------------------------------ */
.main{margin-left:232px;flex:1;display:flex;flex-direction:column;min-width:0;min-height:0;overflow:hidden;height:100vh}

.topbar{
  background:rgba(214,201,176,.8);
  backdrop-filter:blur(14px);-webkit-backdrop-filter:blur(14px);
  border-bottom:1px solid rgba(200,180,138,.25);
  padding:0;height:58px;
  display:flex;align-items:stretch;justify-content:space-between;
  position:sticky;top:0;z-index:40;
  transition:all 0.3s ease
}
body.theme-cream .topbar{
  background:rgba(205,192,168,.82);
  border-bottom:1px solid rgba(184,164,122,.25);
  box-shadow:0 1px 12px rgba(100,70,30,.08)
}
body.theme-beige .topbar{
  background:rgba(232,223,200,.82);
  border-bottom:1px solid rgba(200,184,154,.25);
  box-shadow:0 1px 12px rgba(100,80,50,.06)
}
body.theme-midnight .topbar{
  background:rgba(26,33,48,.85);
  border-bottom:1px solid rgba(122,154,191,.12);
  box-shadow:0 1px 12px rgba(0,0,0,.2)
}
body.theme-ember .topbar{
  background:rgba(22,18,16,.88);
  border-bottom:1px solid rgba(212,114,74,.1);
  box-shadow:0 1px 12px rgba(0,0,0,.25)
}
.topbar-left{
  display:flex;align-items:center;gap:12px;
  padding:0 20px;flex:1
}
.page-title{
  font-family:'Inter',sans-serif;font-size:16px;font-weight:700;
  letter-spacing:-.2px;color:var(--text)
}
body.theme-beige .page-title{color:#2d2420}
body.theme-midnight .page-title{color:#e8c070}
body.theme-ember .page-title{color:#e09070}
.topbar-right{display:flex;align-items:center;gap:8px;padding:0 12px}
/* topbar context action area */
.topbar-ctx{display:flex;align-items:center;gap:8px;padding:0 8px}

/* -- CLOCK --------------------------------------- */
.clock-bar{
  display:flex;align-items:center;gap:5px;
  padding:0 12px;flex-shrink:0
}
.clock-block{
  display:flex;flex-direction:column;justify-content:center;align-items:center;
  padding:4px 12px;min-width:92px;border-radius:8px;
  background:rgba(0,0,0,.05);border:0.5px solid rgba(0,0,0,.06);
  transition:all 0.2s ease
}
.clock-block:hover{background:rgba(0,0,0,.08)}
body.theme-cream  .clock-block{background:rgba(0,0,0,.05);border-color:rgba(0,0,0,.06)}
body.theme-beige  .clock-block{background:rgba(0,0,0,.04);border-color:rgba(0,0,0,.05)}
body.theme-midnight .clock-block{background:rgba(255,255,255,.06);border-color:rgba(255,255,255,.08)}
body.theme-midnight .clock-block:hover{background:rgba(255,255,255,.1)}
body.theme-ember   .clock-block{background:rgba(255,255,255,.05);border-color:rgba(255,255,255,.06)}
body.theme-ember   .clock-block:hover{background:rgba(255,255,255,.08)}
.clock-zone{
  font-size:9px;font-weight:700;text-transform:uppercase;
  letter-spacing:1.5px;color:var(--muted);
  display:flex;align-items:center;gap:4px;margin-bottom:1px;
  opacity:.6
}
.clock-zone-flag{font-size:11px}
.clock-time{
  font-size:13px;font-weight:600;
  font-family:'Courier New',Courier,monospace;
  font-variant-numeric:tabular-nums;line-height:1.2;
  letter-spacing:0.5px;color:var(--text)
}
body.theme-cream  .clock-time{color:#3c2a14}
body.theme-beige  .clock-time{color:#2d2420}
body.theme-midnight .clock-time{color:#c8d8e8}
body.theme-ember .clock-time{color:#c8b090}
.clock-date{
  font-size:9px;font-weight:500;margin-top:1px;
  letter-spacing:0.3px;color:var(--muted);opacity:.5
}
.topbar-sync{
  display:flex;align-items:center;gap:5px;
  padding:4px 10px;border-radius:7px;font-size:11px;font-weight:500;
  color:var(--muted);background:rgba(0,0,0,.04);
  border:0.5px solid rgba(0,0,0,.05)
}
body.theme-midnight .topbar-sync,body.theme-ember .topbar-sync{
  background:rgba(255,255,255,.05);border-color:rgba(255,255,255,.06)
}
.topbar-sync-dot{width:5px;height:5px;border-radius:50%;background:var(--green);transition:background .2s}
.topbar-sync-dot.syncing{background:var(--accent);animation:blink 1s infinite}
.topbar-sync-dot.error{background:var(--red)}
.topbar-avatar{
  width:30px;height:30px;border-radius:50%;
  display:flex;align-items:center;justify-content:center;
  font-size:11px;font-weight:600;cursor:pointer;
  background:rgba(0,0,0,.06);color:var(--text2);
  border:0.5px solid rgba(0,0,0,.06);
  transition:all 0.2s ease;overflow:hidden
}
.topbar-avatar:hover{background:rgba(0,0,0,.1)}
body.theme-midnight .topbar-avatar{background:rgba(255,255,255,.08);border-color:rgba(255,255,255,.1);color:#c8d8e8}
body.theme-ember .topbar-avatar{background:rgba(255,255,255,.06);border-color:rgba(255,255,255,.08);color:#c8b090}
.topbar-avatar img{width:100%;height:100%;border-radius:50%;object-fit:cover}
.search-wrap{position:relative}
.search-wrap input{
  background:var(--s2);border:1px solid var(--border);border-radius:8px;
  padding:7px 12px 7px 32px;color:var(--text);font-size:13px;
  font-family:'Inter',sans-serif;outline:none;width:220px;transition:all 0.2s
}
.search-wrap input:focus{border-color:var(--accent);width:260px}
.search-wrap input::placeholder{color:var(--muted)}
.s-icon{position:absolute;left:9px;top:50%;transform:translateY(-50%);font-size:12px;color:var(--muted)}
.btn{
  display:inline-flex;align-items:center;gap:5px;
  background:var(--accent);color:var(--sidebar);border:none;border-radius:8px;
  padding:8px 16px;font-size:13px;font-weight:600;cursor:pointer;
  font-family:'Inter',sans-serif;transition:all 0.2s;white-space:nowrap
}
body.theme-cream .btn{color:#fff}
body.theme-beige .btn{color:#fff}
body.theme-midnight .btn{color:#141920;background:var(--accent)}
body.theme-ember .btn{color:#0f0d0b;background:var(--accent)}
.btn:hover{background:var(--accent2)}
.btn-ghost{
  background:transparent;color:var(--muted);border:1px solid var(--border2);
  border-radius:8px;padding:7px 13px;font-size:13px;cursor:pointer;
  font-family:'Inter',sans-serif;transition:all 0.2s
}
.btn-ghost:hover{border-color:var(--accent);color:var(--accent)}



aside{transition:background 0.3s,border-color 0.3s}

/* -- PAGE SCROLL AREA ----------------------------- */
#page-scroll-area{
  flex:1;min-height:0;overflow-y:auto;
  display:flex;flex-direction:column;
}

/* -- DAYBOOK --------------------------------------- */
.db-layout{display:flex;flex:1;min-height:0;overflow:hidden}
.db-left{width:220px;flex-shrink:0;background:var(--sidebar);border-right:1px solid var(--border);display:flex;flex-direction:column;overflow:hidden}
.db-left-head{padding:16px 14px 12px;border-bottom:1px solid var(--border)}
.db-left-title{font-family:'Inter',sans-serif;font-size:15px;font-weight:700;color:var(--accent);display:flex;align-items:center;gap:7px}
.db-left-sub{font-size:10px;color:var(--muted);margin-top:2px;font-weight:500;letter-spacing:.3px}
.db-left-search{padding:10px 10px 6px}
.db-left-search input{width:100%;background:var(--bg);border:1px solid var(--border);border-radius:7px;padding:6px 10px;font-size:12px;color:var(--text);font-family:'Inter',sans-serif;outline:none}
.db-left-search input::placeholder{color:var(--muted)}
.db-left-search input:focus{border-color:var(--accent)}
.db-filter-section{padding:8px 12px 3px;font-size:9px;text-transform:uppercase;letter-spacing:2px;color:var(--text2);font-weight:700}
.db-filter-btn{display:flex;align-items:center;gap:8px;padding:7px 12px;font-size:12px;color:var(--text2);cursor:pointer;border-radius:7px;margin:1px 6px;border:none;background:none;font-family:'Inter',sans-serif;width:calc(100% - 12px);text-align:left;transition:all .15s}
.db-filter-btn:hover{background:var(--s2);color:var(--text)}
.db-filter-btn.active{background:rgba(26,154,108,.12);color:#1a9a6c;font-weight:600}
body.theme-beige .db-filter-btn.active{background:rgba(124,92,191,.12);color:var(--accent)}
body.theme-midnight .db-filter-btn.active{background:rgba(232,168,74,.1);color:var(--accent)}
body.theme-ember .db-filter-btn.active{background:rgba(212,114,74,.1);color:var(--accent)}
.db-filter-dot{width:7px;height:7px;border-radius:50%;flex-shrink:0}
.db-filter-count{margin-left:auto;font-size:10px;background:var(--s2);border-radius:12px;padding:1px 7px;color:var(--text2);font-weight:600}
.db-filter-btn.active .db-filter-count{background:rgba(26,154,108,.15);color:#1a9a6c}
body.theme-beige .db-filter-btn.active .db-filter-count{background:rgba(124,92,191,.12);color:var(--accent)}
body.theme-midnight .db-filter-btn.active .db-filter-count{background:rgba(232,168,74,.1);color:var(--accent)}
body.theme-ember .db-filter-btn.active .db-filter-count{background:rgba(212,114,74,.1);color:var(--accent)}
.db-left-footer{margin-top:auto;padding:10px}
.db-new-btn{width:100%;background:var(--accent);color:#fff;border:none;border-radius:8px;padding:9px;font-size:13px;font-weight:600;cursor:pointer;font-family:'Inter',sans-serif;display:flex;align-items:center;justify-content:center;gap:6px;transition:background .2s}
.db-new-btn:hover{background:var(--accent2)}
body.theme-cream .db-new-btn{background:#1a9a6c}
body.theme-cream .db-new-btn:hover{background:#0f7a55}

.db-right{flex:1;display:flex;flex-direction:column;min-width:0;overflow:hidden}
.db-entries-wrap{flex:1;overflow-y:auto;scrollbar-width:thin;scrollbar-color:var(--border) transparent}
.db-entries-wrap::-webkit-scrollbar{width:4px}
.db-entries-wrap::-webkit-scrollbar-thumb{background:var(--border);border-radius:4px}
.db-empty{display:flex;flex-direction:column;align-items:center;justify-content:center;padding:60px 20px;color:var(--muted);gap:10px}
.db-empty-icon{font-size:40px;opacity:.5}
.db-empty-text{font-size:14px}

.db-date-group{border-bottom:1px solid var(--border)}
.db-date-header{padding:16px 22px 6px;display:flex;align-items:baseline;gap:0;position:sticky;top:0;background:var(--bg);z-index:2;border-bottom:1px solid rgba(200,180,140,.25)}
body.theme-midnight .db-date-header{background:var(--bg);border-bottom-color:rgba(37,46,64,.9)}
body.theme-ember .db-date-header{background:var(--bg);border-bottom-color:rgba(42,32,24,.9)}
.dh-day{font-size:30px;font-weight:700;color:#1a9a6c;line-height:1;font-family:'Inter',sans-serif}
body.theme-beige .dh-day{color:var(--accent)}
body.theme-midnight .dh-day{color:var(--accent)}
body.theme-ember .dh-day{color:var(--accent)}
.dh-mon{font-size:14px;font-weight:700;color:#1a9a6c;text-transform:uppercase;letter-spacing:.5px;margin-left:6px;align-self:flex-end;margin-bottom:3px}
body.theme-beige .dh-mon{color:var(--accent)}
body.theme-midnight .dh-mon{color:var(--accent)}
body.theme-ember .dh-mon{color:var(--accent)}
.dh-rest{font-size:11px;color:var(--muted);margin-left:8px;align-self:flex-end;margin-bottom:4px;font-weight:500}
.dh-ecount{margin-left:auto;font-size:10px;background:rgba(26,154,108,.1);color:#1a9a6c;border-radius:12px;padding:2px 9px;font-weight:700}
body.theme-beige .dh-ecount{background:rgba(124,92,191,.1);color:var(--accent)}
body.theme-midnight .dh-ecount{background:rgba(232,168,74,.1);color:var(--accent)}
body.theme-ember .dh-ecount{background:rgba(212,114,74,.1);color:var(--accent)}

.db-entry{padding:12px 22px;border-bottom:1px solid rgba(200,180,140,.18);cursor:pointer;transition:background .12s;display:flex;gap:14px}
.db-entry:hover{background:rgba(232,220,200,.3)}
body.theme-midnight .db-entry:hover{background:rgba(232,168,74,.04)}
body.theme-ember .db-entry:hover{background:rgba(212,114,74,.04)}
.db-entry:last-child{border-bottom:none}
.db-entry-time-col{width:60px;flex-shrink:0;padding-top:2px}
.db-entry-time{font-size:12px;font-weight:600;color:#1a9a6c;font-variant-numeric:tabular-nums}
body.theme-beige .db-entry-time{color:var(--accent)}
body.theme-midnight .db-entry-time{color:var(--accent)}
body.theme-ember .db-entry-time{color:var(--accent)}
.db-entry-ampm{font-size:10px;color:var(--muted);font-weight:500}
.db-entry-body{flex:1;min-width:0}
.db-entry-text{font-size:14px;color:var(--text);line-height:1.6;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden;white-space:pre-wrap}
.db-entry-tags{display:flex;gap:5px;margin-top:6px;flex-wrap:wrap}
.db-tag{font-size:10px;padding:2px 9px;border-radius:20px;font-weight:600}
.db-tag-trade{background:#e6f7f1;color:#0f6e56}
.db-tag-personal{background:#eeedfe;color:#534ab7}
.db-tag-idea{background:#faeeda;color:#854f0b}
.db-tag-health{background:#fce8e8;color:#a32d2d}
.db-tag-work{background:#e6f1fb;color:#185fa5}
.db-tag-family{background:#fbeaf0;color:#993556}
body.theme-midnight .db-tag-trade,body.theme-ember .db-tag-trade{background:rgba(74,154,96,.15);color:#5aaa70}
body.theme-midnight .db-tag-personal,body.theme-ember .db-tag-personal{background:rgba(122,96,191,.15);color:#9a80d4}
body.theme-midnight .db-tag-idea,body.theme-ember .db-tag-idea{background:rgba(192,144,48,.15);color:#c09030}
body.theme-midnight .db-tag-health,body.theme-ember .db-tag-health{background:rgba(192,80,64,.15);color:#e06050}
body.theme-midnight .db-tag-work,body.theme-ember .db-tag-work{background:rgba(74,128,191,.15);color:#6a9ad4}
body.theme-midnight .db-tag-family,body.theme-ember .db-tag-family{background:rgba(192,96,112,.15);color:#d47888}
.db-entry-actions{display:flex;gap:3px;align-items:flex-start;opacity:0;transition:opacity .15s;flex-shrink:0}
.db-entry:hover .db-entry-actions{opacity:1}
.db-ea-btn{background:none;border:none;font-size:12px;cursor:pointer;color:var(--muted);padding:2px 5px;border-radius:4px;line-height:1;transition:color .15s}
.db-ea-btn:hover{color:var(--text)}

/* compose bar */
.db-compose{border-top:1px solid var(--border);background:var(--sidebar);padding:12px 18px;flex-shrink:0;display:none;flex-direction:column;gap:9px}
.db-compose.open{display:flex}
.db-compose-top{display:flex;align-items:center;gap:10px;flex-wrap:wrap}
.db-compose-dt{font-size:11px;color:#1a9a6c;font-weight:600;background:rgba(26,154,108,.1);padding:4px 10px;border-radius:6px}
body.theme-beige .db-compose-dt{color:var(--accent);background:rgba(124,92,191,.1)}
body.theme-midnight .db-compose-dt{color:var(--accent);background:rgba(232,168,74,.08)}
body.theme-ember .db-compose-dt{color:var(--accent);background:rgba(212,114,74,.08)}
.db-compose-tags{display:flex;gap:5px;flex-wrap:wrap;align-items:center}
.db-compose-tag-lbl{font-size:10px;color:var(--muted);font-weight:600;text-transform:uppercase;letter-spacing:1px}
.db-ctag{font-size:10px;padding:3px 9px;border-radius:20px;border:1px solid var(--border);background:transparent;color:var(--text2);cursor:pointer;font-family:'Inter',sans-serif;transition:all .12s}
.db-ctag:hover{border-color:var(--accent);color:var(--accent)}
.db-ctag.sel{border-width:1.5px}
.db-ctag.sel-trade{background:#e6f7f1;color:#0f6e56;border-color:#0f6e56}
.db-ctag.sel-personal{background:#eeedfe;color:#534ab7;border-color:#534ab7}
.db-ctag.sel-idea{background:#faeeda;color:#854f0b;border-color:#854f0b}
.db-ctag.sel-health{background:#fce8e8;color:#a32d2d;border-color:#a32d2d}
.db-ctag.sel-work{background:#e6f1fb;color:#185fa5;border-color:#185fa5}
.db-ctag.sel-family{background:#fbeaf0;color:#993556;border-color:#993556}
.db-compose-input{width:100%;background:var(--bg);border:1px solid var(--border);border-radius:8px;padding:9px 12px;font-size:13px;color:var(--text);font-family:'Inter',sans-serif;outline:none;resize:none;line-height:1.55;min-height:72px;transition:border-color .2s}
.db-compose-input::placeholder{color:var(--muted)}
.db-compose-input:focus{border-color:var(--accent)}
.db-compose-footer{display:flex;justify-content:flex-end;gap:8px}

/* mobile daybook */
@media(max-width:640px){
  .db-layout{flex-direction:column}
  .db-left{width:100%;border-right:none;border-bottom:1px solid var(--border);max-height:none;overflow:visible}
  .db-left-search,.db-filter-section{display:none}
  .db-left-head{padding:10px 14px}
  .db-left-footer{padding:6px 10px;margin-top:0}
  .db-filters-mobile{display:flex;gap:6px;padding:8px 12px;overflow-x:auto;scrollbar-width:none;border-bottom:1px solid var(--border)}
  .db-filters-mobile::-webkit-scrollbar{display:none}
  .db-right{min-height:400px}
  .db-date-header{padding:12px 14px 4px}
  .db-entry{padding:10px 14px}
  .db-entry-actions{opacity:1}
  .db-compose{padding:10px 12px}
}
@media(min-width:641px){.db-filters-mobile{display:none}}

/* -- DAYBOOK PIN LOCK ------------------------------ */
.db-lock-overlay{
  position:absolute;inset:0;
  background:var(--bg);
  display:flex;align-items:center;justify-content:center;
  z-index:100;flex-direction:column;gap:0
}
.db-lock-box{
  background:var(--sidebar);border:1px solid var(--border);
  border-radius:16px;padding:36px 40px;
  display:flex;flex-direction:column;align-items:center;gap:18px;
  min-width:300px;max-width:360px;width:90%
}
.db-lock-icon{font-size:40px;line-height:1;margin-bottom:4px}
.db-lock-title{font-family:'Inter',sans-serif;font-size:20px;font-weight:700;color:var(--text);text-align:center}
.db-lock-sub{font-size:12px;color:var(--muted);text-align:center;line-height:1.5}
.db-pin-dots{display:flex;gap:12px;margin:6px 0}
.db-pin-dot{
  width:14px;height:14px;border-radius:50%;
  border:2px solid var(--border2);background:transparent;
  transition:all .15s
}
.db-pin-dot.filled{background:#1a9a6c;border-color:#1a9a6c}
body.theme-beige .db-pin-dot.filled{background:var(--accent);border-color:var(--accent)}
body.theme-midnight .db-pin-dot.filled{background:var(--accent);border-color:var(--accent)}
body.theme-ember .db-pin-dot.filled{background:var(--accent);border-color:var(--accent)}
.db-pin-error{font-size:12px;color:var(--red);font-weight:600;min-height:16px;text-align:center}
.db-numpad{display:grid;grid-template-columns:repeat(3,1fr);gap:10px;width:100%}
.db-num-btn{
  background:var(--bg);border:1px solid var(--border);
  border-radius:10px;padding:14px 0;
  font-size:18px;font-weight:600;color:var(--text);
  cursor:pointer;font-family:'Inter',sans-serif;
  transition:all .12s;text-align:center;line-height:1
}
.db-num-btn:hover{background:var(--s2);border-color:var(--border2)}
.db-num-btn:active{transform:scale(.94)}
.db-num-btn.del{font-size:16px;color:var(--muted)}
.db-num-btn.clear{font-size:13px;color:var(--muted)}
@keyframes db-shake{
  0%,100%{transform:translateX(0)}
  20%{transform:translateX(-8px)}
  40%{transform:translateX(8px)}
  60%{transform:translateX(-6px)}
  80%{transform:translateX(6px)}
}

/* -- INVESTMENTS PIN LOCK ------------------------------ */
.inv-lock-overlay{
  position:absolute;inset:0;
  background:var(--bg);
  display:flex;align-items:center;justify-content:center;
  z-index:100;flex-direction:column;gap:0
}
.inv-lock-box{
  background:var(--sidebar);border:1px solid var(--border);
  border-radius:16px;padding:36px 40px;
  display:flex;flex-direction:column;align-items:center;gap:18px;
  min-width:300px;max-width:360px;width:90%
}
.inv-lock-icon{font-size:40px;line-height:1;margin-bottom:4px}
.inv-lock-title{font-family:'Inter',sans-serif;font-size:20px;font-weight:700;color:var(--text);text-align:center}
.inv-lock-sub{font-size:12px;color:var(--muted);text-align:center;line-height:1.5}
.inv-pin-dots{display:flex;gap:12px;margin:6px 0}
.inv-pin-dot{
  width:14px;height:14px;border-radius:50%;
  border:2px solid var(--border2);background:transparent;
  transition:all .15s
}
.inv-pin-dot.filled{background:#1a9a6c;border-color:#1a9a6c}
body.theme-beige .inv-pin-dot.filled{background:var(--accent);border-color:var(--accent)}
body.theme-midnight .inv-pin-dot.filled{background:var(--accent);border-color:var(--accent)}
body.theme-ember .inv-pin-dot.filled{background:var(--accent);border-color:var(--accent)}
body.theme-rose .inv-pin-dot.filled{background:#b06090;border-color:#b06090}
body.theme-ocean .inv-pin-dot.filled{background:#00d2b4;border-color:#00d2b4}
.inv-pin-error{font-size:12px;color:var(--red);font-weight:600;min-height:16px;text-align:center}
.inv-numpad{display:grid;grid-template-columns:repeat(3,1fr);gap:10px;width:100%}
.inv-num-btn{
  background:var(--bg);border:1px solid var(--border);
  border-radius:10px;padding:14px 0;
  font-size:18px;font-weight:600;color:var(--text);
  cursor:pointer;font-family:'Inter',sans-serif;
  transition:all .12s;text-align:center;line-height:1
}
.inv-num-btn:hover{background:var(--s2);border-color:var(--border2)}
.inv-num-btn:active{transform:scale(.94)}
.inv-num-btn.del{font-size:16px;color:var(--muted)}
.inv-num-btn.clear{font-size:13px;color:var(--muted)}
@keyframes inv-shake{
  0%,100%{transform:translateX(0)}
  20%{transform:translateX(-8px)}
  40%{transform:translateX(8px)}
  60%{transform:translateX(-6px)}
  80%{transform:translateX(6px)}
}

/* -- IMPORTANT DATES PIN LOCK (uses same Daybook PIN) ----------- */
.imp-lock-overlay{
  position:absolute;inset:0;
  background:var(--bg);
  display:flex;align-items:center;justify-content:center;
  z-index:100;flex-direction:column;gap:0
}
.imp-lock-box{
  background:var(--sidebar);border:1px solid var(--border);
  border-radius:16px;padding:36px 40px;
  display:flex;flex-direction:column;align-items:center;gap:18px;
  min-width:300px;max-width:360px;width:90%
}
.imp-lock-icon{font-size:40px;line-height:1;margin-bottom:4px}
.imp-lock-title{font-family:'Inter',sans-serif;font-size:20px;font-weight:700;color:var(--text);text-align:center}
.imp-lock-sub{font-size:12px;color:var(--muted);text-align:center;line-height:1.5}
.imp-pin-dots{display:flex;gap:12px;margin:6px 0}
.imp-pin-dot{
  width:14px;height:14px;border-radius:50%;
  border:2px solid var(--border2);background:transparent;
  transition:all .15s
}
.imp-pin-dot.filled{background:#1a9a6c;border-color:#1a9a6c}
body.theme-beige .imp-pin-dot.filled{background:var(--accent);border-color:var(--accent)}
body.theme-midnight .imp-pin-dot.filled{background:var(--accent);border-color:var(--accent)}
body.theme-ember .imp-pin-dot.filled{background:var(--accent);border-color:var(--accent)}
body.theme-rose .imp-pin-dot.filled{background:#b06090;border-color:#b06090}
body.theme-ocean .imp-pin-dot.filled{background:#00d2b4;border-color:#00d2b4}
.imp-pin-error{font-size:12px;color:var(--red);font-weight:600;min-height:16px;text-align:center}
.imp-numpad{display:grid;grid-template-columns:repeat(3,1fr);gap:10px;width:100%}
.imp-num-btn{
  background:var(--bg);border:1px solid var(--border);
  border-radius:10px;padding:14px 0;
  font-size:18px;font-weight:600;color:var(--text);
  cursor:pointer;font-family:'Inter',sans-serif;
  transition:all .12s;text-align:center;line-height:1
}
.imp-num-btn:hover{background:var(--s2);border-color:var(--border2)}
.imp-num-btn:active{transform:scale(.94)}
.imp-num-btn.del{font-size:16px;color:var(--muted)}
.imp-num-btn.clear{font-size:13px;color:var(--muted)}
@keyframes imp-shake{
  0%,100%{transform:translateX(0)}
  20%{transform:translateX(-8px)}
  40%{transform:translateX(8px)}
  60%{transform:translateX(-6px)}
  80%{transform:translateX(6px)}
}

/* -- RICH DASHBOARD -------------------------------- */
.dash-wrap{padding:20px 28px;display:flex;flex-direction:column;gap:16px}
/* Quick Capture */
.quick-capture{
  display:flex;align-items:center;gap:8px;
  background:rgba(255,255,255,.25);
  backdrop-filter:blur(8px);-webkit-backdrop-filter:blur(8px);
  border:1px solid rgba(200,180,138,.2);
  border-radius:14px;padding:10px 14px;transition:all .25s ease;
  box-shadow:0 2px 8px rgba(0,0,0,.04)
}
.quick-capture:focus-within{border-color:var(--accent);box-shadow:0 4px 16px rgba(139,94,42,.1)}
.qc-icon{font-size:16px;flex-shrink:0}
.qc-input{
  flex:1;background:none;border:none;outline:none;
  font-size:14px;color:var(--text);font-family:'Inter',sans-serif;
  min-width:0
}
.qc-input::placeholder{color:var(--muted)}
.qc-type{
  background:var(--s2);border:1px solid var(--border);border-radius:6px;
  padding:4px 8px;font-size:11px;color:var(--text2);font-family:'Inter',sans-serif;
  cursor:pointer;outline:none;flex-shrink:0
}
.qc-type option{background:var(--sidebar)}
.qc-btn{
  background:var(--accent);color:#fff;border:none;border-radius:8px;
  padding:6px 14px;font-size:12px;font-weight:600;cursor:pointer;
  font-family:'Inter',sans-serif;flex-shrink:0;transition:background .2s
}
.qc-btn:hover{background:var(--accent2)}
@media(max-width:640px){
  .quick-capture{flex-wrap:wrap}
  .qc-input{min-width:100%;order:2;margin-top:4px}
  .qc-icon{order:0}
  .qc-type{order:1;margin-left:auto}
  .qc-btn{order:3;width:100%;margin-top:4px}
}
.stats-row{display:grid;grid-template-columns:repeat(4,1fr);gap:14px}
.stat-card{
  background:var(--sidebar);border:1px solid rgba(200,180,138,.2);
  border-radius:14px;padding:16px 18px;
  display:flex;flex-direction:column;gap:6px;
  position:relative;overflow:hidden;cursor:pointer;
  transition:all 0.25s ease;
  box-shadow:0 2px 8px rgba(0,0,0,.04)
}
.stat-card:hover{transform:translateY(-2px);box-shadow:0 6px 20px rgba(0,0,0,.08)}
.stat-card.active{box-shadow:0 0 0 2px var(--accent)}
.stat-card::before{
  content:'';position:absolute;top:0;left:0;
  width:4px;height:100%;border-radius:4px 0 0 4px;
}
.stat-card.sc-total::before{background:#7c5cbf}
.stat-card.sc-pending::before{background:#d97706}
.stat-card.sc-completed::before{background:#059669}
.stat-card.sc-missed::before{background:#dc2626}
body.theme-midnight .stat-card.sc-total::before{background:#7a60bf}
body.theme-midnight .stat-card.sc-pending::before{background:#c09030}
body.theme-midnight .stat-card.sc-completed::before{background:#5a9a60}
body.theme-midnight .stat-card.sc-missed::before{background:#c05040}
body.theme-ember .stat-card.sc-total::before{background:#a06050}
body.theme-ember .stat-card.sc-pending::before{background:#d08030}
body.theme-ember .stat-card.sc-completed::before{background:#5a7840}
body.theme-ember .stat-card.sc-missed::before{background:#c04030}
.stat-icon{font-size:20px;line-height:1}
.stat-num{font-family:'Inter',sans-serif;font-size:28px;color:var(--text);line-height:1;font-weight:800;letter-spacing:-.5px}
.stat-label{font-size:11px;font-weight:700;text-transform:uppercase;letter-spacing:1px;color:var(--text2)}
.stat-sub{font-size:11px;color:var(--muted);margin-top:1px}
.dash-progress{
  background:var(--sidebar);border:1px solid var(--border);
  border-radius:12px;padding:14px 20px;
  display:flex;align-items:center;gap:20px;
}
.dash-progress-header{display:flex;flex-direction:column;gap:4px}
.dash-progress-title{font-size:12px;font-weight:700;text-transform:uppercase;letter-spacing:1px;color:var(--text2)}
.dash-progress-val{font-size:13px;font-weight:700;color:var(--accent)}
.dash-progress-track{height:14px;background:var(--s2);border-radius:10px;overflow:hidden;display:none}
.dash-progress-fill{height:100%;border-radius:10px;background:linear-gradient(90deg,#3b82f6 0%,#06b6d4 100%);transition:width 0.6s ease}
body.theme-midnight .dash-progress-fill{background:linear-gradient(90deg,#e8a84a 0%,#d4724a 100%)}
body.theme-ember .dash-progress-fill{background:linear-gradient(90deg,#d4724a 0%,#e8a84a 100%)}
.dash-bottom{display:grid;grid-template-columns:1fr 1fr 1fr;gap:14px}
@media(max-width:1100px){.dash-bottom{grid-template-columns:1fr 1fr}}
.dash-widget{
  background:var(--sidebar);border:1px solid rgba(200,180,138,.15);
  border-radius:14px;padding:18px 20px;
  box-shadow:0 2px 10px rgba(0,0,0,.04);
  transition:box-shadow 0.25s ease
}
.dash-widget:hover{box-shadow:0 4px 16px rgba(0,0,0,.07)}
.dash-widget-title{
  font-size:11px;font-weight:700;text-transform:uppercase;letter-spacing:1.2px;
  color:var(--text2);margin-bottom:14px;display:flex;align-items:center;gap:7px;
}
.dwt-dot{width:7px;height:7px;border-radius:50%;flex-shrink:0}
.dwt-green{background:#059669}
.dwt-red{background:#dc2626}
.routine-items{display:flex;flex-direction:column;gap:8px}
.ri{
  display:flex;align-items:center;gap:10px;
  padding:9px 12px;border-radius:8px;
  background:var(--s2);transition:background 0.15s;
}
.ri.ri-next{background:rgba(124,92,191,.1);border:1px solid rgba(124,92,191,.25)}
body.theme-midnight .ri.ri-next{background:rgba(232,168,74,.08);border-color:rgba(232,168,74,.2)}
body.theme-ember .ri.ri-next{background:rgba(212,114,74,.08);border-color:rgba(212,114,74,.2)}
.ri-time{font-size:11px;font-weight:700;color:var(--muted);min-width:40px;font-variant-numeric:tabular-nums}
.ri-info{flex:1;min-width:0}
.ri-name{font-size:14px;color:var(--text);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.ri-countdown{font-size:10px;color:var(--accent);font-weight:700;margin-top:1px}
.ri-badge{font-size:10px;font-weight:700;padding:2px 8px;border-radius:20px;white-space:nowrap;flex-shrink:0}
.badge-next{background:rgba(124,92,191,.15);color:#7c5cbf}
.badge-soon{background:rgba(217,119,6,.12);color:#b45309}
.badge-done{background:rgba(5,150,105,.12);color:#047857}
body.theme-midnight .badge-next{background:rgba(232,168,74,.15);color:#e8a84a}
body.theme-midnight .badge-done{background:rgba(90,170,112,.12);color:#5aaa70}
body.theme-ember .badge-next{background:rgba(212,114,74,.15);color:#d4724a}
body.theme-ember .badge-done{background:rgba(90,128,64,.12);color:#5a8040}
.missed-items{display:flex;flex-direction:column;gap:8px}
.mi{
  display:flex;align-items:center;gap:10px;
  padding:9px 12px;border-radius:8px;
  background:var(--s2);border-left:3px solid #dc2626;border-radius:0 8px 8px 0;
}
.mi-icon{font-size:14px;flex-shrink:0}
.mi-info{flex:1;min-width:0}
.mi-name{font-size:13px;color:var(--text);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.mi-meta{font-size:11px;color:var(--muted);margin-top:1px}
.mi-age{font-size:10px;font-weight:700;color:#dc2626;background:rgba(220,38,38,.1);padding:2px 7px;border-radius:20px;white-space:nowrap;flex-shrink:0}

/* -- IMPORTANT DATES (dashboard widget) -- */
.dwt-blue{background:#2563eb}
body.theme-rose .dwt-blue{background:#b06090}
body.theme-ocean .dwt-blue{background:#00d2b4}
body.theme-midnight .dwt-blue{background:#5a8abf}
body.theme-ember .dwt-blue{background:#6080a0}
.imp-items{display:flex;flex-direction:column;gap:8px}
.ii{
  display:flex;align-items:center;gap:10px;
  padding:9px 12px;border-radius:8px;
  background:var(--s2);border-left:3px solid #2563eb;border-radius:0 8px 8px 0;
  transition:background 0.15s;
}
body.theme-rose .ii{border-left-color:#b06090}
body.theme-ocean .ii{border-left-color:#00d2b4}
body.theme-midnight .ii{border-left-color:#e8a84a}
body.theme-ember .ii{border-left-color:#d4724a}
.ii.ii-today{background:rgba(37,99,235,.1);border-left-color:#059669}
body.theme-rose .ii.ii-today{background:rgba(176,96,144,.12);border-left-color:#1a7a40}
body.theme-ocean .ii.ii-today{background:rgba(0,210,180,.12);border-left-color:#00dc8c}
.ii-date{
  min-width:44px;text-align:center;
  display:flex;flex-direction:column;align-items:center;justify-content:center;
  font-variant-numeric:tabular-nums;line-height:1;
}
.ii-day{font-size:18px;font-weight:800;color:var(--text)}
.ii-mon{font-size:9px;font-weight:700;letter-spacing:0.8px;text-transform:uppercase;color:var(--muted);margin-top:2px}
.ii-info{flex:1;min-width:0}
.ii-title{font-size:13px;font-weight:600;color:var(--text);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.ii-note{font-size:11px;color:var(--muted);margin-top:2px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.ii-badge{font-size:10px;font-weight:700;padding:2px 8px;border-radius:20px;white-space:nowrap;flex-shrink:0;background:rgba(37,99,235,.12);color:#2563eb}
.ii-badge.today{background:rgba(5,150,105,.14);color:#047857}
.ii-badge.overdue{background:rgba(220,38,38,.12);color:#dc2626}
body.theme-midnight .ii-badge{background:rgba(90,138,191,.18);color:#9bb8d8}
body.theme-ember .ii-badge{background:rgba(96,128,160,.18);color:#8aa0c0}
body.theme-rose .ii-badge{background:rgba(176,96,144,.14);color:#b06090}
body.theme-ocean .ii-badge{background:rgba(0,210,180,.14);color:#00d2b4}

/* -- IMPORTANT DATES (full page) -- */
.imp-header{
  display:flex;align-items:center;justify-content:space-between;
  padding:18px 28px;background:var(--sidebar);
  border-bottom:1px solid rgba(200,180,138,.15);
  flex-wrap:wrap;gap:10px;
}
.imp-title{font-size:15px;font-weight:700;color:var(--text);letter-spacing:0.2px}
.imp-filters{display:flex;gap:8px;flex-wrap:wrap;padding:14px 28px 0}
.imp-filter-btn{
  padding:6px 14px;border-radius:20px;cursor:pointer;
  font-size:12px;font-weight:600;
  background:var(--s2);color:var(--muted);border:1px solid transparent;
  transition:all 0.15s;
}
.imp-filter-btn:hover{background:var(--border);color:var(--text)}
.imp-filter-btn.active{background:rgba(37,99,235,.12);color:var(--accent);border-color:rgba(37,99,235,.25)}
body.theme-rose .imp-filter-btn.active{background:rgba(176,96,144,.12);color:var(--accent);border-color:rgba(176,96,144,.3)}
body.theme-ocean .imp-filter-btn.active{background:rgba(0,210,180,.12);color:var(--accent);border-color:rgba(0,210,180,.3)}
body.theme-midnight .imp-filter-btn.active{background:rgba(232,168,74,.12);color:var(--accent);border-color:rgba(232,168,74,.3)}
body.theme-ember .imp-filter-btn.active{background:rgba(212,114,74,.12);color:var(--accent);border-color:rgba(212,114,74,.3)}
.imp-list-wrap{padding:18px 28px 40px}
.imp-empty{
  padding:40px 20px;text-align:center;
  color:var(--muted);font-style:italic;font-size:14px;
  background:var(--sidebar);border:1px dashed var(--border);border-radius:12px;
}
.imp-grid{
  display:grid;
  grid-template-columns:repeat(auto-fill,minmax(240px,1fr));
  gap:12px;
}
.imp-month-section{margin-bottom:22px}
.imp-month-section:last-child{margin-bottom:0}
.imp-month-header{
  display:flex;align-items:center;justify-content:space-between;
  padding:6px 4px 10px 4px;margin-bottom:4px;
  border-bottom:1px solid var(--border);
}
.imp-month-label{
  font-size:13px;font-weight:800;color:var(--text);
  letter-spacing:0.8px;text-transform:uppercase;
}
.imp-month-header.current .imp-month-label{color:var(--accent)}
.imp-month-count{
  font-size:11px;font-weight:600;color:var(--muted);
  background:var(--s2);padding:3px 10px;border-radius:20px;
}
.imp-month-header.current .imp-month-count{
  background:rgba(37,99,235,.12);color:var(--accent);
}
body.theme-rose .imp-month-header.current .imp-month-count{background:rgba(176,96,144,.14)}
body.theme-ocean .imp-month-header.current .imp-month-count{background:rgba(0,210,180,.14)}
body.theme-midnight .imp-month-header.current .imp-month-count{background:rgba(232,168,74,.14)}
body.theme-ember .imp-month-header.current .imp-month-count{background:rgba(212,114,74,.14)}
.imp-card{
  display:flex;align-items:stretch;gap:12px;
  padding:12px 12px 12px 14px;
  background:var(--sidebar);border:1px solid rgba(200,180,138,.15);
  border-radius:12px;transition:all 0.15s;
  border-left:5px solid #7c5cbf;
  min-width:0;
  position:relative;
}
/* Category-colored left borders (#4) */
.imp-card.cat-personal{border-left-color:#3b82f6}
.imp-card.cat-official{border-left-color:#64748b}
.imp-card.cat-family{border-left-color:#ec4899}
.imp-card.cat-health{border-left-color:#ef4444}
.imp-card.cat-finance{border-left-color:#10b981}
.imp-card.cat-other{border-left-color:#a78bfa}
.imp-card:hover{box-shadow:0 4px 16px rgba(0,0,0,.06);transform:translateY(-1px)}
.imp-card.overdue{opacity:0.75}
.imp-card.today{background:rgba(5,150,105,.04)}

/* Urgency pulse for events within 3 days (#2) */
@keyframes imp-urgent-pulse{
  0%,100%{box-shadow:0 0 0 0 rgba(245,158,11,.5)}
  50%{box-shadow:0 0 0 6px rgba(245,158,11,0)}
}
.imp-card-badge.urgent{
  background:rgba(245,158,11,.18);color:#b45309;
  animation:imp-urgent-pulse 1.8s ease-in-out infinite;
}
.imp-card-badge.today{
  background:rgba(5,150,105,.18);color:#047857;
  animation:imp-urgent-pulse 1.8s ease-in-out infinite;
}
.imp-card-badge.overdue{
  background:rgba(220,38,38,.15);color:#dc2626;
}

/* Hero "Next Up" card (#3) */
.imp-hero{
  display:flex;gap:20px;align-items:center;
  padding:20px 22px;margin-bottom:22px;
  background:linear-gradient(135deg,var(--sidebar) 0%,var(--s2) 100%);
  border:1px solid var(--border);border-radius:16px;
  border-left:6px solid var(--accent);
  position:relative;overflow:hidden;
}
.imp-hero::before{
  content:'';position:absolute;top:0;right:0;width:180px;height:100%;
  background:radial-gradient(circle at right,rgba(124,92,191,.08),transparent 70%);
  pointer-events:none;
}
.imp-hero.urgent{border-left-color:#f59e0b}
.imp-hero.urgent::before{background:radial-gradient(circle at right,rgba(245,158,11,.12),transparent 70%)}
.imp-hero.today{border-left-color:#059669}
.imp-hero.today::before{background:radial-gradient(circle at right,rgba(5,150,105,.12),transparent 70%)}
.imp-hero-label{
  font-size:10px;font-weight:800;letter-spacing:1.5px;text-transform:uppercase;
  color:var(--accent);margin-bottom:6px;
}
.imp-hero.urgent .imp-hero-label{color:#b45309}
.imp-hero.today .imp-hero-label{color:#047857}
.imp-hero-date{
  min-width:78px;width:78px;text-align:center;flex-shrink:0;
  padding:10px 6px;border-radius:12px;background:var(--bg);
  border:1px solid var(--border);
}
.imp-hero-day{font-size:32px;font-weight:800;color:var(--text);line-height:1;font-variant-numeric:tabular-nums}
.imp-hero-mon{font-size:11px;font-weight:700;letter-spacing:1px;text-transform:uppercase;color:var(--muted);margin-top:3px}
.imp-hero-yr{font-size:10px;color:var(--muted);margin-top:2px}
.imp-hero-body{flex:1;min-width:0;position:relative;z-index:1}
.imp-hero-title{font-size:20px;font-weight:700;color:var(--text);margin-bottom:4px;line-height:1.25}
.imp-hero-note{font-size:13px;color:var(--muted);margin-bottom:10px;line-height:1.4}
.imp-hero-cat{
  display:inline-block;font-size:10px;font-weight:700;
  padding:3px 10px;border-radius:20px;
  background:var(--bg);color:var(--text2);border:1px solid var(--border);
}
.imp-hero-countdown{
  display:flex;gap:10px;flex-shrink:0;position:relative;z-index:1;
}
.imp-hero-cdblock{
  min-width:56px;text-align:center;
  padding:8px 10px;background:var(--bg);border:1px solid var(--border);
  border-radius:10px;
}
.imp-hero-cdnum{font-size:22px;font-weight:800;color:var(--accent);line-height:1;font-variant-numeric:tabular-nums}
.imp-hero.urgent .imp-hero-cdnum{color:#b45309}
.imp-hero.today .imp-hero-cdnum{color:#047857}
.imp-hero-cdlbl{font-size:9px;font-weight:700;letter-spacing:0.8px;text-transform:uppercase;color:var(--muted);margin-top:4px}
@media(max-width:640px){
  .imp-hero{flex-wrap:wrap;gap:14px;padding:16px}
  .imp-hero-date{min-width:64px;width:64px;padding:8px 4px}
  .imp-hero-day{font-size:26px}
  .imp-hero-title{font-size:17px}
  .imp-hero-countdown{width:100%;justify-content:flex-start}
  .imp-hero-cdblock{min-width:0;flex:1;max-width:80px}
}
.imp-card-date{
  min-width:50px;width:50px;text-align:center;
  display:flex;flex-direction:column;align-items:center;justify-content:center;
  padding:6px 4px;border-radius:8px;background:var(--s2);
  font-variant-numeric:tabular-nums;flex-shrink:0;
}
.imp-card-day{font-size:18px;font-weight:800;color:var(--text);line-height:1}
.imp-card-mon{font-size:9px;font-weight:700;letter-spacing:0.8px;text-transform:uppercase;color:var(--muted);margin-top:2px}
.imp-card-yr{font-size:9px;color:var(--muted);margin-top:1px}
.imp-card-body{flex:1;min-width:0;display:flex;flex-direction:column;justify-content:center;padding-right:40px}
.imp-card-title{font-size:13px;font-weight:700;color:var(--text);margin-bottom:2px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.imp-card-note{font-size:11px;color:var(--muted);white-space:nowrap;overflow:hidden;text-overflow:ellipsis;margin-bottom:4px}
.imp-card-meta{display:flex;align-items:center;gap:5px;flex-wrap:wrap}
.imp-card-badge{font-size:9px;font-weight:700;padding:2px 7px;border-radius:20px;background:rgba(37,99,235,.12);color:#2563eb;white-space:nowrap}
.imp-card-badge.cat{background:rgba(124,92,191,.14);color:#7c5cbf}
.imp-card-actions{
  position:absolute;top:8px;right:8px;
  display:flex;gap:3px;flex-shrink:0;
  opacity:0;transition:opacity 0.15s;
}
.imp-card:hover .imp-card-actions{opacity:1}
@media(hover:none){.imp-card-actions{opacity:1}}
.imp-card-btn{
  background:var(--bg);border:1px solid var(--border);
  color:var(--muted);padding:3px 7px;border-radius:6px;
  font-size:11px;cursor:pointer;transition:all 0.15s;
  line-height:1;
}
.imp-card-btn:hover{background:var(--s2);color:var(--text)}
.imp-card-btn.del:hover{background:rgba(220,38,38,.1);color:#dc2626;border-color:rgba(220,38,38,.3)}

/* Important Dates modal */
.imp-modal-backdrop{
  position:fixed;inset:0;background:var(--over-bg);
  z-index:1000;display:none;align-items:center;justify-content:center;
  backdrop-filter:blur(4px);
}
.imp-modal-backdrop.open{display:flex}
.imp-modal{
  background:var(--bg);border:1px solid var(--border);
  border-radius:16px;padding:24px;width:92%;max-width:460px;
  box-shadow:0 20px 60px rgba(0,0,0,.3);
}
.imp-modal-title{font-size:16px;font-weight:700;color:var(--text);margin-bottom:16px}
.imp-modal label{display:block;font-size:11px;font-weight:700;text-transform:uppercase;letter-spacing:0.8px;color:var(--muted);margin-bottom:6px;margin-top:12px}
.imp-modal label:first-of-type{margin-top:0}
.imp-modal input,.imp-modal select,.imp-modal textarea{
  width:100%;padding:10px 12px;border-radius:8px;
  background:var(--s2);border:1px solid var(--border);
  color:var(--text);font-size:14px;font-family:inherit;
}
.imp-modal textarea{resize:vertical;min-height:70px}
.imp-modal input:focus,.imp-modal select:focus,.imp-modal textarea:focus{outline:none;border-color:var(--accent)}
.imp-modal-actions{display:flex;gap:10px;margin-top:20px;justify-content:flex-end}
.imp-modal-actions .btn-ghost{background:transparent;border:1px solid var(--border);color:var(--muted);padding:9px 16px;border-radius:8px;cursor:pointer;font-weight:600}
.imp-modal-actions .btn-ghost:hover{background:var(--s2);color:var(--text)}
.imp-modal-actions .btn{padding:9px 18px}

@media(max-width:640px){
  .imp-header{padding:14px 16px}
  .imp-filters{padding:12px 16px 0}
  .imp-list-wrap{padding:14px 16px 30px}
  .imp-grid{grid-template-columns:1fr;gap:10px}
  .imp-card{padding:10px 12px}
  .imp-card-date{min-width:46px;width:46px;padding:5px 3px}
  .imp-card-day{font-size:16px}
  .imp-card-actions{opacity:1}
}
@media(min-width:641px) and (max-width:900px){
  .imp-grid{grid-template-columns:repeat(2,minmax(0,1fr))}
}

.dash-empty{font-size:13px;color:var(--muted);font-style:italic;padding:10px 0}
.dash-greeting{
  background:var(--sidebar);border:1px solid var(--border);
  border-radius:12px;padding:16px 22px;
  display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:8px;
}
.dash-greeting-left{display:flex;flex-direction:column;gap:3px}
.dash-greeting-name{font-family:'Inter',sans-serif;font-size:20px;font-weight:800;letter-spacing:-.3px;color:var(--text)}
.dash-greeting-date{font-size:12px;color:var(--muted);font-weight:500;letter-spacing:0.3px}
.dash-greeting-right{font-size:28px;line-height:1}
.dash-upcoming{
  background:var(--sidebar);border:1px solid var(--border);
  border-radius:12px;padding:16px 20px;
}
.dash-upcoming-title{
  font-size:11px;font-weight:700;text-transform:uppercase;letter-spacing:1.2px;
  color:var(--text2);margin-bottom:12px;display:flex;align-items:center;gap:7px;
}
.dash-upcoming-items{display:flex;flex-direction:column;gap:7px}

/* -- DASHBOARD CALENDAR WIDGET -- */
.dash-cal-widget{
  background:var(--sidebar);border:1px solid var(--border);
  border-radius:12px;padding:16px 20px;
  display:grid;grid-template-columns:auto 1fr;gap:20px;align-items:start;
}
.dash-cal-left{flex-shrink:0;min-width:220px}
.dash-cal-header{
  display:flex;align-items:center;justify-content:space-between;
  margin-bottom:10px;
}
.dash-cal-month{
  font-family:'Inter',sans-serif;font-size:13px;font-weight:700;color:var(--text);
}
.dash-cal-nav{
  background:none;border:none;cursor:pointer;color:var(--muted);
  font-size:16px;padding:0 4px;line-height:1;transition:color 0.15s;
}
.dash-cal-nav:hover{color:var(--accent)}
.dash-cal-grid{
  display:grid;grid-template-columns:repeat(7,1fr);gap:2px;
}
.dash-cal-day-label{
  font-size:9px;font-weight:700;text-transform:uppercase;letter-spacing:.5px;
  color:var(--muted);text-align:center;padding:2px 0 4px;
}
.dash-cal-cell{
  font-size:11px;text-align:center;padding:4px 2px;border-radius:6px;
  color:var(--text2);cursor:default;transition:background 0.12s;line-height:1.4;
  position:relative;
}
.dash-cal-cell.has-rem{
  background:rgba(139,94,42,.12);color:var(--accent);
  font-weight:700;cursor:pointer;
}
body.theme-beige .dash-cal-cell.has-rem{background:rgba(124,92,191,.12);color:var(--accent)}
body.theme-midnight .dash-cal-cell.has-rem{background:rgba(232,168,74,.12);color:var(--accent)}
body.theme-ember .dash-cal-cell.has-rem{background:rgba(212,114,74,.12);color:var(--accent)}
.dash-cal-cell.has-rem:hover{background:rgba(139,94,42,.22)}
body.theme-beige .dash-cal-cell.has-rem:hover{background:rgba(124,92,191,.22)}
body.theme-midnight .dash-cal-cell.has-rem:hover{background:rgba(232,168,74,.22)}
body.theme-ember .dash-cal-cell.has-rem:hover{background:rgba(212,114,74,.22)}
.dash-cal-cell.is-today{
  background:var(--red);color:#fff!important;font-weight:700;
}
body.theme-midnight .dash-cal-cell.is-today{background:#c05040}
body.theme-ember .dash-cal-cell.is-today{background:#b04030}
.dash-cal-cell.is-today.has-rem{background:var(--red)}
.dash-cal-cell.other-month{color:var(--border2);opacity:.4}
.dash-cal-cell.selected-day{box-shadow:0 0 0 2px var(--accent)}
.dash-cal-right{display:flex;flex-direction:column;min-width:0}

.upc-item{display:flex;align-items:center;gap:10px;padding:8px 12px;border-radius:8px;background:var(--s2)}
.upc-title{flex:1;font-size:13px;color:var(--text);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.upc-due{font-size:11px;font-weight:600;color:var(--muted);white-space:nowrap}
.upc-due.upc-due-today{color:#c2440f;background:#fee8d8;padding:2px 7px;border-radius:5px}
.upc-dot{width:7px;height:7px;border-radius:50%;flex-shrink:0}
.upc-dot-today{background:#dc2626}
.upc-dot-soon{background:#d97706}
.upc-dot-future{background:#059669}

/* -- DASHBOARD TASKS WIDGET -- */
.dash-tasks-widget{
  background:var(--sidebar);border:1px solid var(--border);
  border-radius:12px;padding:14px 18px;
}
.dash-tasks-hdr{
  display:flex;align-items:center;justify-content:space-between;margin-bottom:2px;
}
.dash-tasks-count{
  font-size:11px;font-weight:700;padding:2px 8px;border-radius:20px;
}
.dash-tasks-count.open{background:rgba(124,92,191,.12);color:#7c5cbf}
body.theme-midnight .dash-tasks-count.open{background:rgba(232,168,74,.1);color:#e8a84a}
body.theme-ember .dash-tasks-count.open{background:rgba(212,114,74,.1);color:#d4724a}
.dash-tasks-count.done{background:rgba(5,150,105,.1);color:#059669}
.dash-tasks-goto{
  background:none;border:none;cursor:pointer;font-size:11px;
  color:var(--accent);font-family:'Inter',sans-serif;font-weight:700;padding:0 2px;
}
.dash-tasks-goto:hover{text-decoration:underline}
.dash-tasks-section-label{
  font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:.8px;
  color:var(--muted);padding:4px 0;user-select:none;
}
.dash-task-row{
  display:flex;align-items:center;gap:9px;
  padding:6px 10px;border-radius:8px;
  border-bottom:1px solid var(--border);
  transition:background 0.12s;cursor:pointer;
}
.dash-task-row:last-child{border-bottom:none}
.dash-task-row:hover{background:var(--s2)}
.dash-task-row.is-done{opacity:.55}
.dash-task-cb{
  width:15px;height:15px;border-radius:50%;border:2px solid var(--border2);
  flex-shrink:0;cursor:pointer;display:flex;align-items:center;justify-content:center;
  background:transparent;transition:all .15s;font-size:9px;color:#fff;
}
.dash-task-cb.done{background:var(--green);border-color:var(--green)}
.dash-task-cb:hover{border-color:var(--accent)}
.dash-task-prio{
  font-size:9px;font-weight:700;padding:1px 5px;border-radius:3px;flex-shrink:0;white-space:nowrap;
}
.dash-task-prio.high{background:#fee2e2;color:#991b1b}
.dash-task-prio.medium{background:#fef3c7;color:#92400e}
.dash-task-prio.low{background:#d1fae5;color:#065f46}
.dash-task-text{flex:1;font-size:14px;color:var(--text);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.dash-task-row.is-done .dash-task-text{text-decoration:line-through;color:var(--muted)}
.dash-task-cat{font-size:10px;color:var(--muted);flex-shrink:0;white-space:nowrap}
.dash-task-date{font-size:10px;color:var(--muted);flex-shrink:0;white-space:nowrap}


/* -- CONTENT --------------------------------------- */
.content{padding:24px 28px;flex:1}
.sec-header{display:flex;align-items:center;justify-content:space-between;margin-bottom:16px}
.sec-title{
  font-family:'Inter',sans-serif;font-size:16px;color:var(--text);font-weight:700;
  display:flex;align-items:center;gap:8px
}
.pill{
  background:var(--s2);border:1px solid var(--border);
  border-radius:20px;padding:2px 9px;
  font-size:11px;color:var(--text2);font-weight:700;font-family:'Inter',sans-serif
}

/* -- CARDS ----------------------------------------- */
.cards-grid{
  display:grid;
  grid-template-columns:repeat(auto-fill,minmax(260px,1fr));
  gap:14px;margin-bottom:32px;width:100%
}
.ncard{
  background:var(--sidebar);
  border:1px solid transparent;
  border-radius:12px;padding:16px 18px;
  display:flex;flex-direction:column;gap:10px;
  transition:border-color 0.18s,box-shadow 0.18s,background 0.18s;
  position:relative;overflow:hidden;
  cursor:pointer;
  box-shadow:0 1px 4px rgba(0,0,0,.06);
}
.ncard:hover{
  border-color:var(--border);
  box-shadow:0 4px 16px rgba(0,0,0,.10);
}

/* remove old ::after top line */
.ncard::after{ display:none }

/* reminder card default left border = accent */
.ncard[data-type="reminder"]{border-left:4px solid var(--accent)}
.ncard[data-type="reminder"].sent{border-left-color:var(--green)}
.ncard[data-type="reminder"].overdue{border-left-color:var(--red)}
.ncard[data-type="reminder"].pending{border-left-color:var(--accent)}

/* note colour variants */
.ncard.cl-blue{border-left:4px solid var(--blue)!important;}
.ncard.cl-green{border-left:4px solid var(--green)!important;}
.ncard.cl-yellow{border-left:4px solid var(--accent)!important;}
.ncard.cl-red{border-left:4px solid var(--red)!important;}
.ncard.cl-purple{border-left:4px solid #7c3aed!important;}

/* title colours match left border */
.ncard.cl-blue .ctitle{color:var(--blue)}
.ncard.cl-green .ctitle{color:var(--green)}
.ncard.cl-yellow .ctitle{color:var(--accent)}
.ncard.cl-red .ctitle{color:var(--red)}
.ncard.cl-purple .ctitle{color:#7c3aed}

/* overdue card */
.ncard.overdue{border-left:4px solid var(--red)!important;border-color:rgba(200,60,60,.25);background:rgba(220,60,60,.04)}
body.theme-midnight .ncard.overdue{background:rgba(220,60,60,.06)}
body.theme-ember .ncard.overdue{background:rgba(220,60,60,.06)}
.ncard.overdue::after{background:var(--red)}
.ncard:hover::after{opacity:.8}

.ceyebrow{display:flex;align-items:center;justify-content:space-between}
.ctype{font-size:10px;text-transform:uppercase;letter-spacing:1.4px;color:var(--muted);font-weight:700}
.schip{font-size:10px;padding:2px 9px;border-radius:20px;font-weight:600}
.schip.pending{background:#fdf0d8;color:#8b5e2a}
.schip.sent{background:#e8f4ec;color:#2a7a40}
.schip.overdue{background:#f8eeec;color:#c04040}
.ctitle{
  font-family:'Inter',sans-serif;font-size:16px;
  color:var(--text);line-height:1.3;font-weight:700
}
.cbody{font-size:14px;line-height:1.6;color:var(--text2);flex:1;display:-webkit-box;-webkit-line-clamp:4;-webkit-box-orient:vertical;overflow:hidden}
.due-row{
  display:flex;align-items:center;gap:5px;font-size:11px;
  color:#8b5e2a;background:rgba(139,94,42,.08);
  border-radius:6px;padding:6px 10px;font-weight:600
}
.due-row strong{color:var(--text)}
.tags-row{display:flex;gap:5px;flex-wrap:wrap}
.ctag{
  background:var(--s2);color:var(--text2);
  border-radius:4px;padding:2px 8px;font-size:11px;
  border:1px solid var(--border);font-weight:600
}
.cmeta{
  display:flex;align-items:center;justify-content:space-between;
  padding-top:8px;border-top:1px solid rgba(0,0,0,.06)
}
.cdate{font-size:10px;color:var(--muted);font-weight:500}
.cbtns{display:flex;gap:5px}
.cbtn{
  background:var(--s2);border:1px solid var(--border);border-radius:6px;
  padding:4px 11px;font-size:11px;color:var(--text2);cursor:pointer;
  font-family:'Inter',sans-serif;transition:all 0.15s;font-weight:600
}
.cbtn:hover{border-color:var(--accent);color:var(--accent);background:var(--sidebar)}
.cbtn.del:hover{border-color:#c04040;color:#c04040;background:#f8eeec}
.cbtn.done-btn{background:#e8f4ec;border-color:#90c8a0;color:#2a7a40;font-weight:700}
.cbtn.done-btn:hover{background:#d0ecd8;border-color:#2a7a40;color:#1a5a2a}

.empty-state{
  grid-column:1/-1;display:flex;flex-direction:column;
  align-items:center;justify-content:center;
  padding:44px;color:var(--muted);gap:8px
}
.empty-state .ei{font-size:32px;opacity:.4}
.empty-state p{font-size:13px}

/* == APPLE-NOTES 3-COLUMN PAGE == */
#page-notes{
  flex:1;
  display:flex;
  flex-direction:column !important;
  min-height:0;
  width:100%;
  overflow:hidden
}
.notes-page-wrap{
  display:flex;flex-direction:column;
  flex:1;width:100%;min-height:0;
  overflow:hidden
}

/* notes-columns: on desktop shows all 3 panels side-by-side */
.notes-columns{
  display:flex;flex:1;overflow:hidden
}

/* Column 1 — Folders */
.notes-folders-panel{
  width:190px;flex-shrink:0;background:var(--s2);
  border-right:1px solid var(--border);
  display:flex;flex-direction:column;
  flex:0 0 190px;align-self:stretch;
  min-height:0;overflow:hidden
}
.notes-folders-hdr{
  padding:14px 14px 10px;border-bottom:1px solid var(--border);
  display:flex;align-items:center;justify-content:space-between;flex-shrink:0
}
.notes-folders-title{
  font-family:'Inter',sans-serif;font-size:14px;font-weight:700;color:var(--text)
}
.notes-new-folder-btn{
  background:none;border:none;color:var(--accent);font-size:18px;
  cursor:pointer;padding:0 2px;line-height:1;font-weight:700
}
.notes-folder-list{flex:1;min-height:0;overflow-y:auto;padding:6px 0;scrollbar-width:thin;scrollbar-color:var(--border) transparent}
.notes-folder-list::-webkit-scrollbar{width:4px}
.notes-folder-list::-webkit-scrollbar-track{background:transparent}
.notes-folder-list::-webkit-scrollbar-thumb{background:var(--border);border-radius:4px}
.notes-folder-list::-webkit-scrollbar-thumb:hover{background:var(--border2)}
.notes-folder-item{
  display:flex;align-items:center;justify-content:space-between;
  padding:9px 14px;cursor:pointer;border-radius:0;
  transition:background 0.12s;font-size:13px;font-weight:600;color:var(--text2)
}
.notes-folder-item:hover{background:var(--sidebar)}
.notes-folder-item.active{
  background:rgba(139,94,42,.15);color:var(--accent)
}
body.theme-beige .notes-folder-item.active{background:rgba(124,92,191,.12);color:var(--accent)}
body.theme-midnight .notes-folder-item.active{background:rgba(232,168,74,.08);color:var(--accent)}
body.theme-ember .notes-folder-item.active{background:rgba(212,114,74,.08);color:var(--accent)}
body.theme-rose .notes-folder-item.active{background:rgba(176,96,144,.1);color:var(--accent)}
body.theme-ocean .notes-folder-item.active{background:rgba(0,210,180,.1);color:var(--accent)}
body.theme-arctic .notes-folder-item.active{background:rgba(56,72,112,.1);color:var(--accent)}
.notes-folder-name{display:flex;align-items:center;gap:7px;flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.notes-folder-count{
  font-size:11px;background:var(--border);border-radius:10px;
  padding:1px 7px;color:var(--text2);font-weight:700;flex-shrink:0
}
.notes-folder-item.active .notes-folder-count{background:rgba(139,94,42,.2);color:var(--accent)}
body.theme-beige .notes-folder-item.active .notes-folder-count{background:rgba(124,92,191,.15);color:var(--accent)}
body.theme-midnight .notes-folder-item.active .notes-folder-count{background:rgba(232,168,74,.12);color:var(--accent)}
body.theme-ember .notes-folder-item.active .notes-folder-count{background:rgba(212,114,74,.12);color:var(--accent)}
body.theme-rose .notes-folder-item.active .notes-folder-count{background:rgba(176,96,144,.12);color:var(--accent)}
body.theme-ocean .notes-folder-item.active .notes-folder-count{background:rgba(0,210,180,.12);color:var(--accent)}
body.theme-arctic .notes-folder-item.active .notes-folder-count{background:rgba(56,72,112,.12);color:var(--accent)}
.notes-folder-item-wrap{position:relative}
.notes-folder-actions{
  display:none;align-items:center;gap:2px;margin-left:4px;flex-shrink:0
}
.notes-folder-item-wrap:hover .notes-folder-actions{display:flex}
.notes-folder-item-wrap.active .notes-folder-actions{display:flex}
.notes-folder-action-btn{
  background:none;border:none;cursor:pointer;
  font-size:11px;padding:2px 4px;border-radius:4px;
  opacity:0.6;transition:opacity .15s;line-height:1
}
.notes-folder-action-btn:hover{opacity:1;background:var(--border)}
.notes-folder-action-btn.del:hover{background:rgba(192,64,64,.15)}

/* Column 2 — Notes list */
.notes-list-panel{
  width:260px;flex-shrink:0;
  background:var(--sidebar);border-right:1px solid var(--border2);
  display:flex;flex-direction:column;
  flex:0 0 260px;align-self:stretch;
  min-height:0;overflow:hidden
}
.notes-list-hdr{
  padding:12px 14px 10px;border-bottom:1px solid var(--border);
  display:flex;flex-direction:column;gap:6px;flex-shrink:0;
  background:var(--sidebar)
}
.notes-list-hdr-top{display:flex;align-items:center;gap:8px;min-width:0}
.notes-list-hdr-title{
  font-size:13px;font-weight:700;color:var(--text2);
  text-transform:uppercase;letter-spacing:0.6px;
  flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap
}
.notes-list-hdr-count{font-size:11px;color:var(--muted);font-weight:600}
.notes-new-btn{
  background:var(--accent);color:#fff;border:none;border-radius:6px;
  padding:4px 10px;font-size:12px;font-weight:700;cursor:pointer;
  font-family:'Inter',sans-serif;transition:background 0.15s;white-space:nowrap;flex-shrink:0
}
.notes-new-btn:hover{background:var(--accent2)}
.notes-list-search{
  background:var(--s2);border:1px solid var(--border);border-radius:7px;
  padding:5px 10px;font-size:12px;color:var(--text);
  font-family:'Inter',sans-serif;outline:none;width:100%;
  transition:border-color 0.2s
}
.notes-list-search:focus{border-color:var(--accent)}
.notes-list-search::placeholder{color:var(--muted)}
/* Pinned / Recently Edited section headers */
.notes-section-label{
  font-size:9px;font-weight:800;text-transform:uppercase;
  letter-spacing:1.8px;color:var(--muted);
  padding:10px 14px 4px;display:flex;align-items:center;gap:5px;
  user-select:none;background:transparent
}

.notes-list-items{flex:1;min-height:0;overflow-y:auto;scrollbar-width:none;background:var(--sidebar)}
.notes-list-items::-webkit-scrollbar{display:none}
.notes-list-item{
  padding:11px 14px 11px 17px;border-bottom:1px solid var(--border);
  cursor:pointer;transition:background 0.12s;position:relative;
  background:var(--sidebar)
}
.notes-list-item:hover{background:var(--s2)}
.notes-list-item.active{background:rgba(139,94,42,.13)}
body.theme-beige .notes-list-item.active{background:rgba(124,92,191,.1)}
body.theme-midnight .notes-list-item.active{background:rgba(232,168,74,.08)}
body.theme-ember .notes-list-item.active{background:rgba(212,114,74,.08)}
body.theme-rose .notes-list-item.active{background:rgba(176,96,144,.1)}
body.theme-ocean .notes-list-item.active{background:rgba(0,210,180,.08)}
body.theme-arctic .notes-list-item.active{background:rgba(56,72,112,.1)}
.notes-list-item-pin{font-size:9px;color:var(--accent);font-weight:700;margin-bottom:2px}
.notes-list-item-title{
  font-size:13px;font-weight:700;color:var(--text);
  white-space:nowrap;overflow:hidden;text-overflow:ellipsis;line-height:1.3
}
.notes-list-item-date{font-size:10px;color:var(--muted);font-weight:600;margin-top:2px}
.notes-list-item-snippet{
  font-size:11px;color:var(--text2);margin-top:2px;
  white-space:nowrap;overflow:hidden;text-overflow:ellipsis;line-height:1.4
}
.notes-list-item-accent{
  position:absolute;left:0;top:0;bottom:0;width:3px
}
.notes-list-item-accent.cl-blue{background:var(--blue)}
.notes-list-item-accent.cl-green{background:var(--green)}
.notes-list-item-accent.cl-yellow{background:var(--accent)}
.notes-list-item-accent.cl-red{background:var(--red)}
.notes-list-item-accent.cl-purple{background:#7c3aed}
.notes-list-empty{
  padding:40px 16px;text-align:center;color:var(--muted);font-size:12px
}

/* Column 3 — Inline Editor */
.notes-editor-panel{
  flex:1;display:flex;flex-direction:column;overflow:hidden;background:var(--bg);
  align-self:stretch;min-height:0
}
.notes-editor-empty{
  flex:1;display:flex;flex-direction:column;align-items:center;justify-content:center;
  color:var(--muted);gap:10px;opacity:.5
}
.notes-editor-empty-icon{font-size:40px}
.notes-editor-empty-text{font-size:14px;font-style:italic}
.notes-editor-topbar{
  display:flex;align-items:center;justify-content:space-between;
  padding:10px 32px;border-bottom:1px solid var(--border);
  background:var(--sidebar);flex-shrink:0
}
.notes-editor-meta{font-size:11px;color:var(--muted);font-weight:600}
.notes-editor-actions{display:flex;gap:7px;align-items:center}
.notes-editor-save-indicator{
  font-size:11px;color:var(--green);font-weight:700;opacity:0;transition:opacity 0.3s
}
.notes-editor-save-indicator.show{opacity:1}
.notes-editor-content{
  flex:1;display:flex;flex-direction:column;overflow-y:auto;padding:28px 40px 20px
}
.notes-editor-title-input{
  font-family:'Inter',sans-serif;font-size:24px;font-weight:700;
  color:var(--text);line-height:1.3;width:100%;border:none;outline:none;
  background:transparent;resize:none;padding:0;margin-bottom:16px;
  font-variant-ligatures:none;caret-color:var(--accent)
}
.notes-editor-title-input::placeholder{color:var(--border2)}
.notes-editor-body-input{
  font-family:'Inter',sans-serif;font-size:16px;
  color:var(--text2);line-height:1.8;width:100%;
  border:none;outline:none;background:transparent;
  resize:none;padding:0;flex:1;min-height:300px;
  caret-color:var(--accent)
}
.notes-editor-body-input::placeholder{color:var(--border2)}



/* == MARKDOWN TOOLBAR == */
.md-toolbar{
  display:flex;align-items:center;gap:3px;flex-wrap:wrap;
  padding:6px 0 8px;border-bottom:1px solid var(--border);margin-bottom:8px;
}
.md-tb-btn{
  background:transparent;border:1px solid var(--border);border-radius:5px;
  padding:3px 8px;font-size:11px;font-weight:700;cursor:pointer;
  color:var(--text2);font-family:'Inter',sans-serif;transition:all 0.15s;
  line-height:1.6;
}
.md-tb-btn:hover{background:var(--s2);color:var(--accent);border-color:var(--accent)}
.md-tb-sep{width:1px;height:16px;background:var(--border);margin:0 3px;flex-shrink:0}
.md-tb-label{font-size:10px;color:var(--muted);text-transform:uppercase;
  letter-spacing:1px;font-weight:700;margin-left:4px}

/* == TEMPLATES PICKER == */
.tmpl-btn{
  background:transparent;border:1px solid var(--border);border-radius:6px;
  padding:4px 10px;font-size:11px;color:var(--muted);cursor:pointer;
  font-family:'Inter',sans-serif;transition:all 0.15s;margin-left:auto;
}
.tmpl-btn:hover{border-color:var(--accent);color:var(--accent)}
.tmpl-dropdown{
  position:absolute;right:0;top:110%;background:var(--sidebar);
  border:1px solid var(--border2);border-radius:10px;
  box-shadow:0 4px 20px rgba(0,0,0,.18);z-index:500;min-width:200px;
  display:none;overflow:hidden;
}
.tmpl-dropdown.open{display:block}
.tmpl-item{
  padding:10px 16px;font-size:13px;color:var(--text2);cursor:pointer;
  border-bottom:1px solid var(--border);transition:background 0.12s;
  font-family:'Inter',sans-serif;
}
.tmpl-item:last-child{border-bottom:none}
.tmpl-item:hover{background:var(--s2);color:var(--accent)}
.tmpl-item-icon{margin-right:8px}

/* == PRIORITY BADGES == */
.prio-badge{
  display:inline-flex;align-items:center;gap:4px;
  border-radius:12px;padding:2px 8px;font-size:10px;font-weight:700;
  letter-spacing:.3px;flex-shrink:0;
}
.prio-high{background:rgba(220,38,38,.1);color:#dc2626}
.prio-medium{background:rgba(59,130,246,.1);color:#3b82f6}
.prio-low{background:rgba(156,163,175,.12);color:#9ca3af}
.prio-badge::before{content:'';display:inline-block;width:7px;height:7px;border-radius:50%;flex-shrink:0}
.prio-high::before{background:#dc2626}
.prio-medium::before{background:#3b82f6}
.prio-low::before{background:#9ca3af}


/* == DASHBOARD WIDGETS ROW == */







/* == FULL MONTHLY CALENDAR == */
.full-cal-wrap{
  display:none;flex-direction:column;
  height:calc(100vh - 58px);background:var(--bg);overflow:hidden;
}
.full-cal-wrap.active{display:flex}
.full-cal-header{
  display:flex;align-items:center;justify-content:space-between;
  padding:12px 20px;border-bottom:1px solid var(--border);
  background:var(--sidebar);flex-shrink:0;gap:12px;
}
.full-cal-title{
  font-family:'Inter',sans-serif;font-size:18px;font-weight:700;color:var(--text);
  flex:1;text-align:center;
}
.full-cal-nav{
  background:transparent;border:1px solid var(--border2);border-radius:8px;
  padding:6px 14px;cursor:pointer;color:var(--text2);font-size:14px;
  font-family:'Inter',sans-serif;transition:all 0.15s;font-weight:700;
}
.full-cal-nav:hover{border-color:var(--accent);color:var(--accent)}
.full-cal-today-btn{
  background:var(--accent);color:#fff;border:none;border-radius:8px;
  padding:6px 14px;font-size:12px;font-weight:700;cursor:pointer;
  font-family:'Inter',sans-serif;
}
.full-cal-dow-row{
  display:grid;grid-template-columns:repeat(7,1fr);
  border-bottom:1px solid var(--border);flex-shrink:0;
}
.full-cal-dow{
  text-align:center;padding:8px 4px;font-size:10px;font-weight:700;
  text-transform:uppercase;letter-spacing:1px;color:var(--muted);
}
.full-cal-grid{
  display:grid;grid-template-columns:repeat(7,1fr);
  grid-auto-rows:1fr;flex:1;overflow:hidden;
}
.full-cal-cell{
  border-right:1px solid var(--border);border-bottom:1px solid var(--border);
  padding:4px;overflow:hidden;cursor:default;min-height:80px;
  display:flex;flex-direction:column;gap:2px;
  transition:background 0.1s;
}
.full-cal-cell:nth-child(7n){border-right:none}
.full-cal-cell:hover{background:var(--s2)}
.full-cal-cell.today .full-cal-day-num{
  background:var(--accent);color:#fff;border-radius:50%;
  width:22px;height:22px;display:flex;align-items:center;justify-content:center;
}
.full-cal-cell.other-month{opacity:.4;background:var(--s2)}
.full-cal-day-num{
  font-size:12px;font-weight:700;color:var(--text2);
  width:22px;height:22px;display:flex;align-items:center;justify-content:center;
  flex-shrink:0;border-radius:50%;
}
.full-cal-cell.weekend .full-cal-day-num{color:var(--accent)}
.full-cal-event{
  font-size:11px;font-weight:600;border-radius:4px;
  padding:1px 5px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;
  cursor:pointer;line-height:1.6;flex-shrink:0;
}
.full-cal-event:hover{opacity:.85;transform:translateX(1px)}
.full-cal-event.ev-done{opacity:.5;text-decoration:line-through}
.full-cal-event.prio-high{background:rgba(220,38,38,.18);color:#dc2626}
.full-cal-event.prio-medium{background:rgba(59,130,246,.18);color:var(--blue)}
.full-cal-event.prio-low{background:rgba(34,197,94,.18);color:var(--green)}
.full-cal-event.prio-default{background:var(--s2);color:var(--text2)}
.full-cal-more{
  font-size:10px;color:var(--muted);font-weight:700;cursor:pointer;
  padding:1px 4px;border-radius:3px;transition:background 0.1s;
}
.full-cal-more:hover{background:var(--s2);color:var(--accent)}

/* == STREAK / NOTIFICATION PERMISSION == */
.notif-prompt{
  background:rgba(var(--accent-rgb,139,94,42),.08);border:1px solid var(--border);
  border-radius:10px;padding:10px 14px;font-size:12px;color:var(--text2);
  display:flex;align-items:center;gap:10px;margin:0 20px 12px;
}
.notif-prompt button{
  background:var(--accent);color:#fff;border:none;border-radius:6px;
  padding:4px 10px;font-size:11px;font-weight:700;cursor:pointer;white-space:nowrap;
}

/* == MARKDOWN PREVIEW == */
.notes-preview-toggle{
  display:flex;gap:0;border:1px solid var(--border);border-radius:8px;overflow:hidden;flex-shrink:0
}
.notes-preview-toggle button{
  padding:4px 14px;font-size:11px;font-weight:700;font-family:'Inter',sans-serif;
  border:none;background:transparent;color:var(--muted);cursor:pointer;
  letter-spacing:.4px;transition:all 0.15s
}
.notes-preview-toggle button.active{background:var(--accent);color:#fff}
.notes-preview-toggle button:hover:not(.active){background:var(--s2);color:var(--text)}
.notes-md-preview{
  font-family:'Inter',sans-serif;font-size:16px;
  color:var(--text2);line-height:1.8;width:100%;
  flex:1;min-height:300px;display:none;overflow-y:auto
}
.notes-md-preview.active{display:block}
.notes-editor-body-input.hidden{display:none}
/* Markdown rendered styles */
.notes-md-preview h1{font-family:'Inter',sans-serif;font-size:22px;font-weight:700;color:var(--text);margin:18px 0 8px;border-bottom:2px solid var(--border);padding-bottom:6px}
.notes-md-preview h2{font-family:'Inter',sans-serif;font-size:18px;font-weight:700;color:var(--text);margin:16px 0 6px}
.notes-md-preview h3{font-family:'Inter',sans-serif;font-size:15px;font-weight:700;color:var(--accent);margin:12px 0 4px;text-transform:uppercase;letter-spacing:.5px}
.notes-md-preview strong{font-weight:700;color:var(--text)}
.notes-md-preview em{font-style:italic;color:var(--text2)}
.notes-md-preview ul{margin:6px 0 10px 20px;list-style:disc}
.notes-md-preview ol{margin:6px 0 10px 20px;list-style:decimal}
.notes-md-preview li{margin:3px 0;line-height:1.7}
.notes-md-preview hr{border:none;border-top:2px solid var(--border);margin:16px 0}
.notes-md-preview p{margin:6px 0;line-height:1.8}
.notes-md-preview code{font-family:'Courier New',monospace;font-size:13px;background:var(--s2);padding:1px 6px;border-radius:4px;color:var(--accent)}
.notes-md-preview blockquote{border-left:3px solid var(--accent);margin:10px 0;padding:4px 14px;color:var(--muted);font-style:italic;background:var(--s2);border-radius:0 6px 6px 0}
.notes-md-preview .md-tag-green{color:var(--green);font-weight:700}
.notes-md-preview .md-tag-red{color:var(--red);font-weight:700}
.notes-md-preview .md-tag-blue{color:var(--blue);font-weight:700}
/* Markdown pasted images */
.notes-md-preview .md-img-wrap{margin:12px 0;text-align:left;position:relative;display:inline-block;max-width:100%}
.notes-md-preview .md-img{
  max-width:100%;max-height:480px;border-radius:8px;
  border:1px solid var(--border);box-shadow:0 2px 12px rgba(0,0,0,.12);
  display:block;cursor:zoom-in;transition:box-shadow .2s
}
.notes-md-preview .md-img:hover{box-shadow:0 4px 24px rgba(0,0,0,.22)}
.notes-md-preview .md-img-del-btn{
  position:absolute;top:8px;right:8px;
  background:rgba(180,30,30,.82);color:#fff;border:none;border-radius:6px;
  width:30px;height:30px;font-size:15px;cursor:pointer;
  display:flex;align-items:center;justify-content:center;
  opacity:0;transition:opacity .15s;backdrop-filter:blur(4px)
}
.notes-md-preview .md-img-wrap:hover .md-img-del-btn{opacity:1}
.notes-md-preview .md-img-del-btn:hover{background:rgba(200,30,30,1)}
#md-img-lightbox{
  display:none;position:fixed;inset:0;z-index:9999;
  background:rgba(0,0,0,.88);align-items:center;justify-content:center;cursor:zoom-out
}
#md-img-lightbox.open{display:flex}
#md-img-lightbox img{max-width:92vw;max-height:92vh;border-radius:10px;box-shadow:0 8px 48px rgba(0,0,0,.5)}
/* Markdown rendered tables */
.notes-md-preview .md-table-wrap{position:relative;margin:12px 0;overflow-x:auto}
.notes-md-preview .md-table-copy-btn{
  position:absolute;top:6px;right:6px;
  background:var(--accent);color:#fff;border:none;border-radius:6px;
  padding:3px 10px;font-size:11px;font-weight:600;cursor:pointer;
  opacity:0;transition:opacity .15s;z-index:2;font-family:'Inter',sans-serif;
  display:flex;align-items:center;gap:4px
}
.notes-md-preview .md-table-wrap:hover .md-table-copy-btn{opacity:1}
.notes-md-preview .md-table-copy-btn.copied{background:var(--green)}
.notes-md-preview table{
  border-collapse:collapse;width:100%;font-size:13px;
  background:var(--s2);border-radius:8px;overflow:hidden;
  border:1px solid var(--border)
}
.notes-md-preview th{
  background:var(--sidebar);color:var(--text);font-weight:700;
  padding:9px 14px;text-align:left;border-bottom:2px solid var(--border2);
  border-right:1px solid var(--border);white-space:nowrap
}
.notes-md-preview th:last-child{border-right:none}
.notes-md-preview td{
  padding:8px 14px;border-bottom:1px solid var(--border);
  border-right:1px solid var(--border);color:var(--text2);line-height:1.5
}
.notes-md-preview td:last-child{border-right:none}
.notes-md-preview tr:last-child td{border-bottom:none}
.notes-md-preview tr:hover td{background:rgba(128,100,60,.06)}

/* == REMINDERS PAGE == */
.rem-page-wrap{display:flex;flex-direction:column;height:calc(100vh - 58px);overflow:hidden}
.rem-summary-row{
  display:flex;gap:16px;
  padding:12px 28px;border-bottom:1px solid var(--border);flex-shrink:0;
  align-items:center;font-size:13px;color:var(--text2)
}
.rem-summary-row span{margin-right:auto;font-weight:500}
.rem-summary-stat{display:flex;align-items:center;gap:8px}
.rem-stat-label{font-size:13px;color:var(--muted)}
.rem-stat-count{font-size:14px;font-weight:700;color:var(--text)}
.rem-columns{display:flex;flex:1;overflow:hidden}

/* Column 1 — Lists */
.rem-lists-panel{
  width:190px;flex-shrink:0;background:var(--s2);
  border-right:1px solid var(--border);
  display:flex;flex-direction:column;overflow:hidden
}
.rem-lists-hdr{
  padding:12px 14px 10px;border-bottom:1px solid var(--border);
  display:flex;align-items:center;justify-content:space-between;flex-shrink:0
}
.rem-lists-title{font-family:'Inter',sans-serif;font-size:14px;font-weight:700;color:var(--text)}
.rem-new-list-btn{
  background:none;border:none;color:var(--accent);
  font-size:18px;cursor:pointer;padding:0 2px;line-height:1;font-weight:700
}
.rem-list-items{flex:1;overflow-y:auto;padding:6px 0}
.rem-list-item{
  display:flex;align-items:center;justify-content:space-between;
  padding:10px 14px;cursor:pointer;transition:background 0.12s;
  font-size:14px;font-weight:600;color:var(--text2)
}
.rem-list-item:hover{background:var(--sidebar)}
.rem-list-item.active{background:rgba(139,94,42,.15);color:var(--accent)}
body.theme-beige .rem-list-item.active{background:rgba(124,92,191,.12);color:var(--accent)}
body.theme-midnight .rem-list-item.active{background:rgba(232,168,74,.08);color:var(--accent)}
body.theme-ember .rem-list-item.active{background:rgba(212,114,74,.08);color:var(--accent)}
body.theme-rose .rem-list-item.active{background:rgba(176,96,144,.1);color:var(--accent)}
body.theme-ocean .rem-list-item.active{background:rgba(0,210,180,.1);color:var(--accent)}
body.theme-arctic .rem-list-item.active{background:rgba(56,72,112,.1);color:var(--accent)}
.rem-list-name{display:flex;align-items:center;gap:7px;flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.rem-list-count{font-size:12px;background:var(--border);border-radius:10px;padding:1px 7px;color:var(--text2);font-weight:700;flex-shrink:0}
.rem-list-item.active .rem-list-count{background:rgba(139,94,42,.2);color:var(--accent)}
body.theme-beige .rem-list-item.active .rem-list-count{background:rgba(124,92,191,.15);color:var(--accent)}
body.theme-midnight .rem-list-item.active .rem-list-count{background:rgba(232,168,74,.12);color:var(--accent)}
body.theme-ember .rem-list-item.active .rem-list-count{background:rgba(212,114,74,.12);color:var(--accent)}
body.theme-rose .rem-list-item.active .rem-list-count{background:rgba(176,96,144,.12);color:var(--accent)}
body.theme-ocean .rem-list-item.active .rem-list-count{background:rgba(0,210,180,.12);color:var(--accent)}
body.theme-arctic .rem-list-item.active .rem-list-count{background:rgba(56,72,112,.12);color:var(--accent)}

/* Column 2 — Reminders checklist (minimalist timeline) */
.rem-checklist-panel{
  flex:1 1 50%;min-width:0;display:flex;flex-direction:column;overflow:hidden;background:var(--bg)
}

/* Column 3 — Right summary panel */
.rem-right-panel{
  flex:1 1 50%;min-width:0;background:var(--bg);
  border-left:1px solid var(--border);
  display:flex;flex-direction:column;overflow-y:auto;overflow-x:hidden;
  scrollbar-width:thin;scrollbar-color:var(--border) transparent
}
.rem-right-panel::-webkit-scrollbar{width:3px}
.rem-right-panel::-webkit-scrollbar-thumb{background:var(--border);border-radius:3px}
.rrp-section{padding:16px 16px 12px;border-bottom:1px solid rgba(0,0,0,.06);box-sizing:border-box;width:100%}
body.theme-midnight .rrp-section{border-bottom-color:rgba(255,255,255,.05)}
body.theme-ember .rrp-section{border-bottom-color:rgba(255,255,255,.04)}
.rrp-section:last-child{border-bottom:1px solid var(--border);padding-bottom:28px}
.rrp-title{
  font-size:9px;font-weight:700;text-transform:uppercase;letter-spacing:1.8px;
  color:var(--muted);margin-bottom:10px
}
.rrp-stats-grid{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1fr);gap:8px;margin-bottom:2px}
.rrp-stat{border-radius:10px;padding:10px 12px;box-sizing:border-box;min-width:0}
.rrp-stat.rrp-stat-total{background:rgba(42,90,154,.07);border:1px solid rgba(42,90,154,.18)}
.rrp-stat.rrp-stat-done{background:rgba(42,122,64,.07);border:1px solid rgba(42,122,64,.18)}
body.theme-midnight .rrp-stat.rrp-stat-total{background:rgba(122,154,191,.07);border-color:rgba(122,154,191,.2)}
body.theme-midnight .rrp-stat.rrp-stat-done{background:rgba(90,170,112,.07);border-color:rgba(90,170,112,.2)}
body.theme-ember .rrp-stat.rrp-stat-total{background:rgba(96,128,160,.07);border-color:rgba(96,128,160,.18)}
body.theme-ember .rrp-stat.rrp-stat-done{background:rgba(90,128,64,.07);border-color:rgba(90,128,64,.18)}
.rrp-stat-num{font-family:'Inter',sans-serif;font-size:26px;font-weight:700;line-height:1}
.rrp-stat.rrp-stat-total .rrp-stat-num{color:rgba(42,90,154,.65)}
.rrp-stat.rrp-stat-done .rrp-stat-num{color:rgba(42,122,64,.65)}
body.theme-midnight .rrp-stat.rrp-stat-total .rrp-stat-num{color:rgba(122,154,191,.8)}
body.theme-midnight .rrp-stat.rrp-stat-done .rrp-stat-num{color:rgba(90,170,112,.8)}
body.theme-ember .rrp-stat.rrp-stat-total .rrp-stat-num{color:rgba(96,128,160,.75)}
body.theme-ember .rrp-stat.rrp-stat-done .rrp-stat-num{color:rgba(90,128,64,.75)}
.rrp-stat-lbl{font-size:12px;color:var(--muted);margin-top:3px;font-weight:600;text-transform:uppercase;letter-spacing:.5px}
.rrp-pri-row{display:flex;align-items:center;justify-content:space-between;margin-bottom:2px}
.rrp-pri-label{font-size:13px;color:var(--text2)}
.rrp-pri-count{font-size:13px;font-weight:700;color:var(--text)}
.rrp-bar{height:3px;background:rgba(0,0,0,.08);border-radius:2px;margin-bottom:8px;overflow:hidden}
body.theme-midnight .rrp-bar{background:rgba(255,255,255,.08)}
body.theme-ember .rrp-bar{background:rgba(255,255,255,.06)}
.rrp-bar-fill{height:100%;border-radius:2px}
.rrp-mini-cal{
  display:grid;grid-template-columns:repeat(7,1fr);
  margin-top:6px;
  border:1px solid var(--border);
  border-radius:8px;
  overflow:hidden;
}
.rrp-cal-cell{
  height:24px;display:flex;align-items:center;justify-content:center;
  font-size:10px;color:var(--text2);
  border-right:1px solid var(--border);
  border-bottom:1px solid var(--border);
  box-sizing:border-box;
}
.rrp-cal-cell:nth-child(7n){border-right:none}
/* last 7 cells = last row — remove bottom border */
.rrp-cal-cell:nth-last-child(-n+7){border-bottom:none}
.rrp-cal-cell.hdr{
  color:var(--muted);font-size:9px;font-weight:800;
  background:var(--s2);text-transform:uppercase;letter-spacing:.5px;
  border-bottom:1px solid var(--border2);
}
.rrp-cal-cell.has-task{
  background:rgba(139,94,42,.12);color:var(--accent);font-weight:700;
  position:relative;cursor:pointer;
}
body.theme-beige .rrp-cal-cell.has-task{background:rgba(124,92,191,.12);color:var(--accent)}
body.theme-midnight .rrp-cal-cell.has-task{background:rgba(232,168,74,.12);color:var(--accent)}
body.theme-ember   .rrp-cal-cell.has-task{background:rgba(212,114,74,.12);color:var(--accent)}
.rrp-cal-cell.has-task::after{
  content:'';position:absolute;bottom:3px;left:50%;transform:translateX(-50%);
  width:4px;height:4px;border-radius:50%;background:var(--accent);
}
.rrp-cal-cell.has-task:hover{background:rgba(139,94,42,.22)}
body.theme-beige .rrp-cal-cell.has-task:hover{background:rgba(124,92,191,.22)}
body.theme-midnight .rrp-cal-cell.has-task:hover{background:rgba(232,168,74,.22)}
body.theme-ember   .rrp-cal-cell.has-task:hover{background:rgba(212,114,74,.22)}
.rrp-cal-cell.today-cell{background:#1d4ed8;color:#fff;font-weight:700}
body.theme-midnight .rrp-cal-cell.today-cell{background:#1d4ed8}
body.theme-ember .rrp-cal-cell.today-cell{background:#1d4ed8}
.rrp-cal-cell.rrp-sel-day{box-shadow:0 0 0 2px var(--accent);border-radius:4px}
.rrp-cal-legend{display:flex;align-items:center;gap:5px;margin-top:7px;flex-wrap:wrap}
.rrp-cal-legend span{font-size:11px;color:var(--muted)}
.rrp-cal-leg-dot{width:8px;height:8px;border-radius:2px;flex-shrink:0}
.rrp-upcoming-item{display:flex;align-items:flex-start;gap:8px;margin-bottom:10px}
.rrp-up-dot{width:6px;height:6px;border-radius:50%;flex-shrink:0;margin-top:3px}
.rrp-up-text{font-size:13px;color:var(--text);line-height:1.3}
.rrp-up-date{font-size:12px;color:var(--muted);margin-top:1px}
.rrp-await-item{
  display:flex;align-items:flex-start;gap:8px;margin-bottom:8px;
  padding:9px 10px;background:rgba(0,0,0,.03);
  border:1px solid rgba(0,0,0,.07);border-radius:6px
}
body.theme-midnight .rrp-await-item{background:rgba(255,255,255,.03);border-color:rgba(255,255,255,.07)}
body.theme-ember .rrp-await-item{background:rgba(255,255,255,.02);border-color:rgba(255,255,255,.05)}
.rrp-prog-wrap{margin-top:4px}
.rrp-prog-header{display:flex;align-items:center;justify-content:space-between;margin-bottom:5px}
.rrp-prog-label{font-size:12px;color:var(--text2)}
.rrp-prog-val{font-size:12px;font-weight:700;color:var(--accent)}
.rrp-divider{border:none;border-top:1px solid var(--border);margin:0}

/* == OVERVIEW colored header == */
.rrp-overview-section{padding:0 !important}
.rrp-overview-header{
  padding:10px 16px 9px;
  background:linear-gradient(135deg,var(--accent) 0%,var(--accent2) 100%);
  display:flex;align-items:center;
}
body.theme-cream  .rrp-overview-header{background:linear-gradient(135deg,#8b5e2a 0%,#a8762e 100%)}
body.theme-beige  .rrp-overview-header{background:linear-gradient(135deg,#7c5cbf 0%,#9b7de0 100%)}
body.theme-midnight .rrp-overview-header{background:linear-gradient(135deg,#1e2838 0%,#252e40 100%);border-bottom:2px solid #e8a84a}
body.theme-ember   .rrp-overview-header{background:linear-gradient(135deg,#1e1a16 0%,#2a2018 100%);border-bottom:2px solid #d4724a}
.rrp-overview-label{
  font-size:10px;font-weight:800;letter-spacing:2.2px;text-transform:uppercase;
  color:#fff;opacity:.95;
}
body.theme-midnight .rrp-overview-label{color:#e8a84a}
body.theme-ember   .rrp-overview-label{color:#d4724a}
.rrp-overview-section .rrp-stats-grid{padding:12px 14px 14px}
.rem-checklist-hdr{
  padding:18px 28px 12px;border-bottom:1px solid var(--border);flex-shrink:0;
  display:flex;align-items:center;justify-content:space-between
}
.rem-checklist-title{
  font-family:'Inter',sans-serif;font-size:28px;font-weight:300;color:var(--text);
  letter-spacing:-0.02em
}
.rem-checklist-actions{display:flex;gap:8px;align-items:center}
.rem-checklist-body{flex:1;overflow-y:auto;padding:8px 14px 24px}

/* == COMPACT REMINDERS LAYOUT == */
/* Date group headers */
.rem-date-group{margin-bottom:0}
.rem-date-header{
  display:flex;align-items:center;gap:8px;
  padding:8px 8px 3px;
  font-size:12px;font-weight:700;color:var(--muted);
  text-transform:uppercase;letter-spacing:1.2px;
  border-top:1px solid var(--border);margin-top:4px
}
.rem-date-group:first-child .rem-date-header{border-top:none;margin-top:0}
.rem-date-header.overdue{
  color:var(--red);border-top-color:rgba(192,64,64,.2)
}

/* Compact single-line item rows */
.rem-item-row{
  display:flex;align-items:center;gap:9px;
  padding:6px 8px;border-radius:6px;margin-bottom:1px;
  transition:background 0.1s;cursor:pointer;
  border:none
}
.rem-item-row:hover{background:var(--s2)}
body.theme-midnight .rem-item-row:hover{background:rgba(255,255,255,.04)}
body.theme-ember .rem-item-row:hover{background:rgba(255,255,255,.03)}
.rem-item-row.is-done{opacity:.5}
.rem-item-row.is-done .rem-item-title{text-decoration:line-through;color:var(--muted)}

/* Compact checkbox */
.rem-check{
  width:17px;height:17px;border-radius:50%;
  border:1.5px solid var(--border2);flex-shrink:0;cursor:pointer;
  transition:all 0.15s;display:flex;align-items:center;justify-content:center;
  background:transparent
}
body.theme-midnight .rem-check{border-color:var(--border2)}
body.theme-ember .rem-check{border-color:var(--border2)}
.rem-check.done{background:var(--green);border-color:var(--green);color:#fff;font-size:9px;font-weight:700}
.rem-check:hover{border-color:var(--accent)}

/* Item content — single line */
.rem-item-main{flex:1;min-width:0;display:flex;align-items:center;gap:0}
.rem-item-title{
  font-size:15px;font-weight:400;color:var(--text);
  white-space:nowrap;overflow:hidden;text-overflow:ellipsis;
  flex:1;min-width:0;
  cursor:pointer
}
/* Inline meta — right side */
.rem-item-meta{
  display:flex;align-items:center;gap:5px;flex-shrink:0;margin-left:8px
}
.rem-item-date{
  font-size:13px;color:var(--muted);white-space:nowrap
}
.rem-item-priority-dot{
  width:5px;height:5px;border-radius:50%;flex-shrink:0
}
.rem-item-priority-dot.high{background:var(--red)}
.rem-item-priority-dot.medium{background:var(--accent2)}
.rem-item-priority-dot.low{background:var(--green)}
.rem-item-prio-lbl{font-size:12px;font-weight:600}
.rem-item-prio-lbl.high{color:var(--red)}
.rem-item-prio-lbl.medium{color:var(--accent2)}
.rem-item-prio-lbl.low{color:var(--green)}
.rem-item-due.overdue{color:var(--red);font-weight:600}
.rem-item-due.today{color:#1d4ed8;font-weight:600}
.rem-item-notes{display:none} /* hidden in compact mode */

/* Today / No-date badges inline */
.rem-badge-today{
  font-size:12px;font-weight:600;padding:1px 7px;border-radius:8px;
  background:rgba(37,99,235,.12);color:#1d4ed8;flex-shrink:0
}
body.theme-midnight .rem-badge-today{background:rgba(232,168,74,.12);color:var(--accent)}
body.theme-ember .rem-badge-today{background:rgba(212,114,74,.12);color:var(--accent)}
.rem-badge-nodate{
  font-size:12px;font-weight:500;padding:1px 7px;border-radius:8px;
  background:var(--s2);color:var(--muted);flex-shrink:0
}

/* Delete button — compact */
.rem-item-del{
  background:none;border:none;color:var(--muted);cursor:pointer;
  font-size:13px;opacity:0;transition:opacity 0.1s;padding:2px 4px;flex-shrink:0
}
.rem-item-row:hover .rem-item-del{opacity:1}
.rem-item-del:hover{color:var(--red)}

/* Compact add row */
.rem-add-row{
  display:flex;align-items:center;gap:8px;
  padding:10px 12px;border-radius:10px;margin-top:8px;
  border:1px solid var(--border2);background:var(--sidebar);
  box-shadow:0 1px 3px rgba(0,0,0,.04);
  transition:all 0.15s
}
.rem-add-row:hover{background:var(--s2);border-color:var(--accent)}
.rem-add-row:focus-within{background:var(--s2);border-color:var(--accent);box-shadow:0 2px 8px rgba(0,0,0,.08)}
/* Per-theme tuning so the "new reminder" bar is clearly visible on every theme */
body.theme-rose     .rem-add-row{background:#ecd9e3;border-color:#c89ab0}
body.theme-rose     .rem-add-row:hover,body.theme-rose .rem-add-row:focus-within{background:#e5cbd8;border-color:#b06090}
body.theme-arctic   .rem-add-row{background:#d8dde8;border-color:#a8b0c0}
body.theme-arctic   .rem-add-row:hover,body.theme-arctic .rem-add-row:focus-within{background:#ced4e0;border-color:#384870}
body.theme-beige    .rem-add-row{background:#e4dcc8;border-color:#b8a888}
body.theme-cream    .rem-add-row{background:#d6c9b0;border-color:#b8a070}
body.theme-midnight .rem-add-row{background:#222c3e;border-color:#3a4a68}
body.theme-midnight .rem-add-row:hover,body.theme-midnight .rem-add-row:focus-within{background:#2a3448;border-color:var(--accent)}
body.theme-ember    .rem-add-row{background:#241d16;border-color:#4a3620}
body.theme-ember    .rem-add-row:hover,body.theme-ember .rem-add-row:focus-within{background:#2c241a;border-color:var(--accent)}
body.theme-ocean    .rem-add-row{background:#0f2630;border-color:#1a4050}
body.theme-ocean    .rem-add-row:hover,body.theme-ocean .rem-add-row:focus-within{background:#133040;border-color:#00d2b4}
.rem-add-plus{
  width:22px;height:22px;border-radius:50%;
  flex-shrink:0;display:flex;align-items:center;justify-content:center;
  font-size:16px;color:var(--accent);cursor:pointer;font-weight:700;
  background:var(--bg);border:1px solid var(--border)
}
.rem-add-input{
  flex:1;border:none;outline:none;background:transparent;
  font-size:14px;font-weight:500;color:var(--text);
  font-family:'Inter',sans-serif;caret-color:var(--accent)
}
.rem-add-input::placeholder{color:var(--muted);font-weight:400}
.rem-add-due-input{
  border:1px solid var(--border);border-radius:6px;
  background:var(--bg);
  font-size:12px;color:var(--text2);padding:5px 8px;outline:none;
  font-family:'Inter',sans-serif;cursor:pointer;transition:all 0.15s;
  font-weight:500
}
.rem-add-due-input:hover{border-color:var(--accent);background:var(--s2)}
.rem-add-due-input:focus{border-color:var(--accent);box-shadow:0 0 0 2px rgba(0,0,0,.04)}
body.theme-midnight .rem-add-due-input,body.theme-ember .rem-add-due-input,body.theme-ocean .rem-add-due-input{
  background:var(--bg);color-scheme:dark
}

/* Completed section */
.rem-completed-section{margin-top:32px;padding-top:20px;border-top:1px solid var(--border)}
.rem-completed-toggle{
  display:flex;align-items:center;justify-content:space-between;
  font-size:13px;font-weight:700;color:var(--muted);
  text-transform:uppercase;letter-spacing:0.8px;padding:0 12px 12px;
  user-select:none
}
.rem-completed-header{
  display:flex;align-items:center;gap:8px;cursor:pointer
}
.rem-completed-header:hover{color:var(--text2)}
.rem-empty{
  padding:80px 20px;text-align:center;color:var(--muted);font-size:14px;
  display:flex;flex-direction:column;align-items:center;gap:10px
}
.rem-empty-icon{font-size:42px;opacity:.25}

/* -- VIEW TOGGLE ----------------------------------- */
.view-toggle{
  display:flex;background:var(--s2);border:1px solid var(--border);
  border-radius:8px;padding:3px;gap:2px
}
.vtbtn{
  background:none;border:none;border-radius:6px;
  padding:5px 10px;cursor:pointer;color:var(--muted);
  font-size:14px;line-height:1;transition:all 0.15s
}
.vtbtn:hover{color:var(--text)}
.vtbtn.active{background:var(--accent);color:var(--sidebar)}
body.theme-cream  .vtbtn.active{color:#fff}
body.theme-beige  .vtbtn.active{color:#fff}
body.theme-midnight .vtbtn.active{color:#141920}
body.theme-ember .vtbtn.active{color:#0f0d0b}

/* -- LIST VIEW ------------------------------------- */
.list-view{display:flex;flex-direction:column;gap:0;margin-bottom:32px;width:100%}
.list-view .lrow{
  display:flex;align-items:center;gap:12px;
  padding:11px 14px;border-bottom:1px solid var(--border2);
  transition:background 0.15s;position:relative;
  cursor:pointer;
}
.list-view .lrow:first-child{border-top:1px solid var(--border2);border-radius:10px 10px 0 0}
.list-view .lrow:last-child{border-radius:0 0 10px 10px}
.list-view .lrow:hover{background:var(--s2)}
.lrow-accent{width:3px;height:36px;border-radius:2px;background:var(--border2);flex-shrink:0}
.lrow-accent.cl-blue{background:var(--blue)}
.lrow-accent.cl-green{background:var(--green)}
.lrow-accent.cl-yellow{background:var(--accent)}
.lrow-accent.cl-red{background:var(--red)}
.lrow-accent.cl-purple{background:#7c3aed}
.lrow-icon{font-size:14px;flex-shrink:0;width:20px;text-align:center}
.lrow-main{flex:1;min-width:0}
.lrow-title{
  font-family:'Inter',sans-serif;font-size:14px;color:var(--text);
  white-space:nowrap;overflow:hidden;text-overflow:ellipsis;
  font-weight:700
}
.lrow-sub{
  font-size:12px;color:var(--text2);margin-top:2px;
  white-space:nowrap;overflow:hidden;text-overflow:ellipsis;
  font-weight:500
}
.lrow-due{
  font-size:11px;color:var(--text);white-space:nowrap;
  background:var(--s2);border-radius:5px;padding:3px 8px;flex-shrink:0;
  border:1px solid var(--border2);font-weight:600
}
.lrow-tags{display:flex;gap:4px;flex-wrap:nowrap;overflow:hidden;flex-shrink:0;max-width:160px}
.lrow-tags .ctag{
  white-space:nowrap;
  background:rgba(0,0,0,.08);
  color:var(--text2);
  border:1px solid rgba(0,0,0,.1);
  font-weight:500;font-size:11px
}

.lrow-date{font-size:11px;color:var(--text2);flex-shrink:0;width:72px;text-align:right;font-weight:600}
.lrow-btns{display:flex;gap:4px;flex-shrink:0}
.list-view .empty-state{border:1px solid var(--border);border-radius:10px}

/* hide grid children in list mode and vice versa */
.cards-grid{display:grid}
.list-view-wrap{display:none}
.is-list .cards-grid{display:none}
.is-list .list-view-wrap{display:block}

/* -- OVERLAY / MODAL ------------------------------- */
.overlay{
  display:none;position:fixed;inset:0;
  background:var(--over-bg);
  z-index:200;align-items:flex-start;justify-content:center;
  padding:40px 20px 60px;overflow-y:auto;-webkit-overflow-scrolling:touch;
  touch-action:pan-y
}
.overlay.open{display:flex}
.modal{
  background:var(--sidebar);border:1px solid var(--border2);
  border-radius:16px;padding:26px;width:100%;max-width:460px;
  transition:background 0.3s;margin:auto;
  box-shadow:0 20px 60px rgba(0,0,0,.35),0 0 0 1px rgba(255,255,255,.06)
}
.modal.with-preview{max-width:860px;display:grid;grid-template-columns:1fr 1fr;gap:0}
.mhead{display:flex;align-items:center;justify-content:space-between;margin-bottom:20px}
.mhead h2{font-family:'Inter',sans-serif;font-size:19px;color:var(--text)}
.mclose{
  background:var(--s2);border:1px solid var(--border);
  border-radius:6px;color:var(--muted);font-size:13px;
  cursor:pointer;padding:4px 9px
}
/* 2. Visual tab toggle */
.type-tog{
  display:flex;border-bottom:2px solid var(--border);
  margin-bottom:18px;gap:0
}
.tt{
  flex:1;padding:10px 12px;text-align:center;border-radius:0;font-size:13px;
  color:var(--muted);cursor:pointer;transition:all 0.15s;
  font-family:'Inter',sans-serif;border:none;background:none;
  border-bottom:3px solid transparent;margin-bottom:-2px;font-weight:600
}
.tt:hover{color:var(--text)}
.tt.active{color:var(--accent);border-bottom-color:var(--accent);background:rgba(139,94,42,.05)}
body.theme-beige .tt.active{color:#fff;border-bottom-color:var(--accent)}
body.theme-midnight .tt.active{color:#141920;border-bottom-color:var(--accent)}
body.theme-ember .tt.active{color:#0f0d0b;border-bottom-color:var(--accent)}
/* Preview panel */
.modal-form-col{padding:26px}
.modal-preview-col{
  padding:26px;background:var(--bg);
  border-left:1px solid var(--border);border-radius:0 16px 16px 0;
  display:flex;flex-direction:column;gap:12px
}
.modal-preview-label{
  font-size:10px;font-weight:700;text-transform:uppercase;
  letter-spacing:1px;color:var(--muted);margin-bottom:6px
}
.preview-card{
  background:var(--sidebar);border:1.5px solid var(--border);
  border-radius:12px;padding:14px 16px;
  display:flex;flex-direction:column;gap:8px;flex:1
}
.preview-card.cl-blue{border-left:4px solid var(--blue)}
.preview-card.cl-green{border-left:4px solid var(--green)}
.preview-card.cl-yellow{border-left:4px solid var(--accent)}
.preview-card.cl-red{border-left:4px solid var(--red)}
.preview-card.cl-purple{border-left:4px solid #7c3aed}
.preview-eyebrow{font-size:10px;text-transform:uppercase;letter-spacing:1.2px;color:var(--muted);font-weight:700}
.preview-title{font-family:'Inter',sans-serif;font-size:15px;font-weight:700;color:var(--text);line-height:1.3;min-height:22px}
.preview-body{font-family:'Inter',sans-serif;font-size:13px;color:var(--text2);line-height:1.6;display:-webkit-box;-webkit-line-clamp:4;-webkit-box-orient:vertical;overflow:hidden}
.preview-tags{display:flex;gap:5px;flex-wrap:wrap}
.preview-meta{display:flex;align-items:center;justify-content:space-between;padding-top:8px;border-top:1px solid var(--border);margin-top:auto}
.preview-date{font-size:10px;color:var(--muted)}
/* Pin */
.pin-btn{
  background:none;border:1px solid var(--border);border-radius:6px;
  padding:4px 10px;font-size:12px;cursor:pointer;color:var(--muted);
  font-family:'Inter',sans-serif;font-weight:600;transition:all 0.15s
}
.pin-btn.pinned{background:#fef3c7;border-color:#d97706;color:#92400e}
.pin-btn:hover{border-color:var(--accent);color:var(--accent)}
.ncard.pinned-card{border-top:3px solid #d97706}
.pinned-badge{font-size:10px;background:#fef3c7;color:#92400e;border-radius:4px;padding:1px 6px;font-weight:700}
.frow{margin-bottom:13px}
.frow label{display:block;font-size:10px;color:var(--muted);margin-bottom:4px;text-transform:uppercase;letter-spacing:0.8px}
.frow input,.frow textarea,.frow select{
  width:100%;background:var(--bg);border:1px solid var(--border2);
  border-radius:8px;padding:9px 12px;color:var(--text);font-size:13px;
  font-family:'Inter',sans-serif;font-size:15px;outline:none;transition:border-color 0.2s
}
.frow input:focus,.frow textarea:focus,.frow select:focus{border-color:var(--accent)}
.frow textarea{resize:vertical;min-height:120px}
.frow select option{background:var(--sidebar)}
.mfoot{display:flex;gap:8px;justify-content:flex-end;margin-top:18px;padding-top:14px;border-top:1px solid var(--border);align-items:center;flex-wrap:wrap}
.mfoot .btn,.mfoot .btn-ghost{min-height:44px;padding:10px 20px;font-size:14px;touch-action:manipulation}
@media(max-width:640px){
  .mfoot{position:sticky;bottom:0;background:var(--sidebar);padding:12px 0 4px;margin-top:14px;z-index:10;}
  .mfoot .btn{flex:1;justify-content:center;font-size:15px;min-height:50px;border-radius:12px;}
  .mfoot .btn-ghost{flex:1;justify-content:center;font-size:15px;min-height:50px;}
}
.autosave-lbl{font-size:11px;color:var(--green);margin-right:auto;opacity:0;transition:opacity 0.4s;font-weight:600}
.autosave-lbl.show{opacity:1}
/* Tag chip input */
.tag-chip-wrap{
  display:flex;flex-wrap:wrap;gap:5px;align-items:center;
  background:var(--bg);border:1px solid var(--border2);border-radius:8px;
  padding:6px 10px;min-height:40px;cursor:text;transition:border-color 0.2s
}
.tag-chip-wrap:focus-within{border-color:var(--accent)}
.tag-chip{
  display:inline-flex;align-items:center;gap:4px;
  background:var(--s2);border:1px solid var(--border);
  border-radius:20px;padding:2px 8px;font-size:12px;
  color:var(--text2);font-weight:600
}
.tag-chip-x{
  background:none;border:none;cursor:pointer;color:var(--muted);
  font-size:13px;padding:0;line-height:1;transition:color 0.15s
}
.tag-chip-x:hover{color:var(--red)}
.tag-chip-input{
  border:none;outline:none;background:transparent;
  font-size:13px;color:var(--text);font-family:'Inter',sans-serif;
  min-width:80px;flex:1
}
.tag-chip-input::placeholder{color:var(--muted)}
.tag-suggestions{
  position:absolute;top:100%;left:0;right:0;z-index:10;
  background:var(--sidebar);border:1px solid var(--border2);
  border-radius:8px;margin-top:2px;overflow:hidden;display:none;
  box-shadow:0 4px 16px rgba(0,0,0,.12)
}
.tag-suggestions.open{display:block}
.tag-sug-item{
  padding:8px 12px;font-size:12px;cursor:pointer;color:var(--text2);
  transition:background 0.1s;display:flex;align-items:center;gap:6px
}
.tag-sug-item:hover{background:var(--s2);color:var(--text)}
/* Color swatches */
.color-swatches{display:flex;gap:8px;flex-wrap:wrap;margin-top:4px}
.cswatch{
  width:28px;height:28px;border-radius:50%;cursor:pointer;
  border:2px solid transparent;transition:all 0.15s;position:relative
}
.cswatch:hover{transform:scale(1.15)}
.cswatch.selected{border-color:var(--text);box-shadow:0 0 0 2px var(--bg),0 0 0 4px var(--text)}
.cswatch-default{background:var(--s2);border-color:var(--border)}

/* type description banner */
.type-desc{
  display:flex;align-items:flex-start;gap:10px;
  background:var(--s2);border:1px solid var(--border2);
  border-radius:8px;padding:10px 14px;margin-bottom:14px;
  font-size:12px;color:var(--text2);line-height:1.55
}
.type-desc strong{color:var(--text);font-size:13px}
.type-desc span{color:var(--muted)}
.type-desc-icon{font-size:20px;flex-shrink:0;margin-top:1px}

/* -- SETTINGS PANEL -------------------------------- */
#settings-panel{
  display:none;position:fixed;inset:0;
  background:var(--over-bg);
  z-index:300;align-items:flex-start;justify-content:center;
  padding:40px 20px;overflow-y:auto
}
#settings-panel.open{display:flex}
.settings-modal{
  background:var(--sidebar);border:1px solid var(--border2);
  border-radius:16px;padding:28px;width:100%;max-width:520px;
  margin:auto
}
.settings-modal h2{font-family:'Inter',sans-serif;font-size:19px;color:var(--text);margin-bottom:22px}
.settings-section-title{
  font-size:11px;text-transform:uppercase;letter-spacing:1.5px;
  color:var(--muted);margin-bottom:10px;margin-top:22px
}
.settings-section-title:first-of-type{margin-top:0}

/* THEME CARDS */
.theme-grid{display:grid;grid-template-columns:repeat(4,1fr);gap:10px;margin-bottom:4px}
.theme-card{
  border:2px solid var(--border);border-radius:10px;overflow:hidden;
  cursor:pointer;transition:border-color 0.2s
}
.theme-card.selected{border-color:var(--accent)}
.theme-preview{height:52px;display:flex;gap:0}
.tp-side{width:30%;flex-shrink:0}
.tp-main{flex:1;padding:7px;display:flex;flex-direction:column;gap:4px}
.tp-line{height:4px;border-radius:2px}
.theme-name{
  font-size:11px;font-weight:500;color:var(--text);
  padding:6px 10px;background:var(--s2);text-align:center
}

/* Neon Glassmorphism overrides */
body.theme-midnight .ncard,
body.theme-midnight .fin-card,
body.theme-midnight .tan-item,
body.theme-midnight .fin-sum-card,
body.theme-midnight .stat-card,
body.theme-ember .ncard,
body.theme-ember .fin-card,
body.theme-ember .tan-item,
body.theme-ember .fin-sum-card,
body.theme-ember .stat-card{
  background:rgba(12,16,32,.7);
  backdrop-filter:blur(12px);
  border-color:rgba(0,229,255,.15);
}
body.theme-midnight .ncard:hover,
body.theme-midnight .fin-card:hover,
body.theme-midnight .tan-item:hover,
body.theme-ember .ncard:hover,
body.theme-ember .fin-card:hover,
body.theme-ember .tan-item:hover{
  border-color:rgba(0,229,255,.4)
}
body.theme-midnight aside,
body.theme-ember aside{
  background:rgba(8,10,18,.9);
  border-right:1px solid rgba(0,229,255,.12);
  backdrop-filter:blur(16px)
}
body.theme-midnight .ncard.pinned-card{border-top:3px solid #e8a84a}
body.theme-ember .ncard.pinned-card{border-top:3px solid #d4724a}
body.theme-midnight .ctitle{color:#e8a84a}
body.theme-ember .ctitle{color:#d4724a}
body.theme-midnight .stat-num{color:#e8a84a}
body.theme-ember .stat-num{color:#d4724a}
/* Beige accent overrides */
body.theme-beige .ctitle{color:#7c5cbf}
body.theme-beige .nav-item.active{color:#7c5cbf}

/* -- STICKY PAGE ----------------------------------- */
.sp-toolbar{
  display:flex;align-items:center;justify-content:space-between;
  padding:12px 28px;border-bottom:1px solid var(--border);
  background:var(--bg);flex-shrink:0;flex-wrap:wrap;gap:10px
}
.sp-toolbar-left{display:flex;align-items:center;gap:12px;flex-wrap:wrap}
.sp-toolbar-right{display:flex;align-items:center;gap:10px}
.sp-label{font-size:12px;color:var(--muted);white-space:nowrap;font-weight:600}
.sp-count{font-size:12px;color:var(--muted)}
.sp-colors{display:flex;gap:6px;flex-wrap:wrap}
/* 1. Visual color picker — checkmark on selected */
.sp-dot{
  width:24px;height:24px;border-radius:7px;cursor:pointer;
  border:2px solid transparent;transition:transform 0.15s,border-color 0.15s,box-shadow 0.15s;
  flex-shrink:0;position:relative;display:flex;align-items:center;justify-content:center
}
.sp-dot:hover{transform:scale(1.2);box-shadow:0 2px 8px rgba(0,0,0,.25)}
.sp-dot.active{border-color:rgba(0,0,0,.55);transform:scale(1.1);box-shadow:0 0 0 2px rgba(0,0,0,.15)}
.sp-dot.active::after{
  content:'✓';font-size:11px;font-weight:700;color:rgba(0,0,0,.65);line-height:1
}
/* 2. Filter bar */
.sp-board{
  flex:1;padding:20px 28px;overflow-y:auto;
  display:flex;flex-wrap:wrap;
  gap:16px;align-content:flex-start
}
.sp-empty{
  width:100%;display:flex;flex-direction:column;
  align-items:center;justify-content:center;padding:80px 20px;
  color:var(--muted);gap:10px;text-align:center
}
.sp-empty-icon{font-size:52px;opacity:.3}
.sp-empty p{font-size:13px;line-height:1.6}
/* 7. Animations */
@keyframes sp-fadein{from{opacity:0;transform:scale(.88) translateY(10px)}to{opacity:1;transform:scale(1) translateY(0)}}
@keyframes sp-fadeout{from{opacity:1;transform:scale(1)}to{opacity:0;transform:scale(.85) translateY(8px)}}
.sticky-card{
  border-radius:12px;padding:14px;
  display:flex;flex-direction:column;gap:6px;
  box-shadow:2px 4px 14px rgba(0,0,0,.18);
  transition:box-shadow 0.15s,transform 0.15s;
  min-height:140px;min-width:190px;
  position:relative;overflow:hidden;
  box-sizing:border-box;
  animation:sp-fadein 0.22s ease
}
.sticky-card:hover{box-shadow:4px 8px 22px rgba(0,0,0,.26);transform:translateY(-2px)}
.sticky-card.removing{animation:sp-fadeout 0.2s ease forwards}
/* 3. Pin badge */
.sticky-pin-btn{
  background:rgba(0,0,0,.12);border:none;border-radius:5px;
  color:rgba(0,0,0,.45);font-size:12px;cursor:pointer;
  padding:2px 6px;line-height:1.4;transition:all 0.15s
}
.sticky-pin-btn:hover{background:rgba(0,0,0,.2)}
.sticky-pin-btn.pinned{background:rgba(0,0,0,.22);color:rgba(0,0,0,.75)}
.sticky-pinned-badge{
  position:absolute;top:-1px;left:10px;
  font-size:9px;font-weight:700;background:rgba(0,0,0,.18);
  color:rgba(0,0,0,.6);border-radius:0 0 6px 6px;
  padding:1px 7px;letter-spacing:0.5px;text-transform:uppercase
}
/* resize handle */
.sticky-resize-handle{
  position:absolute;bottom:0;right:0;
  width:22px;height:22px;cursor:nwse-resize;
  display:flex;align-items:flex-end;justify-content:flex-end;
  padding:4px;opacity:.3;transition:opacity 0.2s;z-index:5;
  border-radius:0 0 12px 0
}
.sticky-card:hover .sticky-resize-handle{opacity:.7}
.sticky-resize-handle:hover{opacity:1!important}
.sticky-resize-handle svg{width:12px;height:12px;pointer-events:none}
.sticky-card-header{display:flex;align-items:center;justify-content:space-between;gap:4px}
/* 5. timestamps */
.sticky-card-date{font-size:10px;color:rgba(0,0,0,.38);font-weight:500;line-height:1.4}
.sticky-card-del{
  background:rgba(0,0,0,.12);border:none;border-radius:5px;
  color:rgba(0,0,0,.5);font-size:12px;cursor:pointer;
  padding:2px 7px;line-height:1.4;transition:all 0.15s
}
.sticky-card-del:hover{background:rgba(180,0,0,.25);color:rgba(100,0,0,.8)}
/* 6. archive btn */
.sticky-archive-btn{
  background:rgba(0,0,0,.1);border:none;border-radius:5px;
  color:rgba(0,0,0,.45);font-size:11px;cursor:pointer;
  padding:2px 7px;line-height:1.4;transition:all 0.15s
}
.sticky-archive-btn:hover{background:rgba(0,0,0,.2)}
.sticky-card-body{
  font-size:13px;color:rgba(0,0,0,.78);line-height:1.6;
  flex:1;outline:none;min-height:60px;
  white-space:pre-wrap;word-break:break-word;cursor:text;
  border-radius:4px;padding:3px 5px
}
.sticky-card-body:focus{background:rgba(0,0,0,.05);outline:1px dashed rgba(0,0,0,.2)}
/* 4. tags row inside sticky */
.sticky-tags{display:flex;gap:4px;flex-wrap:wrap;margin-top:2px}
.sticky-tag{
  font-size:10px;font-weight:600;background:rgba(0,0,0,.12);
  color:rgba(0,0,0,.6);border-radius:10px;padding:1px 7px
}
.sticky-tag-input{
  font-size:11px;background:rgba(0,0,0,.08);border:none;outline:none;
  border-radius:10px;padding:2px 7px;color:rgba(0,0,0,.65);
  font-family:'Inter',sans-serif;min-width:60px;max-width:90px;
  cursor:text
}
.sticky-tag-input::placeholder{color:rgba(0,0,0,.35)}
.sticky-card-footer{
  font-size:10px;color:rgba(0,0,0,.35);
  border-top:1px solid rgba(0,0,0,.1);padding-top:6px;
  display:flex;justify-content:space-between;align-items:center;gap:6px
}

/* Archive panel */
.sp-archive-panel{
  display:none;flex-direction:column;
  background:var(--bg);border-top:1px solid var(--border);
  padding:16px 28px;flex-shrink:0
}
.sp-archive-panel.open{display:flex}
.sp-archive-title{font-size:11px;font-weight:700;text-transform:uppercase;letter-spacing:0.8px;color:var(--muted);margin-bottom:10px}
.sp-archive-grid{display:flex;flex-wrap:wrap;gap:12px}

/* -- TRADING JOURNAL ------------------------------- */
.tj-toolbar{
  display:flex;align-items:center;justify-content:space-between;
  padding:16px 28px;border-bottom:2px solid var(--border);
  background:#fff;flex-shrink:0;flex-wrap:wrap;gap:10px
}
.tj-toolbar-left{display:flex;gap:10px;flex-wrap:wrap}
/* Trade mode toggle */
.tj-mode-toggle{
  display:flex;background:var(--s2);border:1.5px solid var(--border);
  border-radius:8px;padding:3px;gap:2px;flex-shrink:0
}
.tj-mode-btn{
  background:none;border:none;border-radius:6px;
  padding:5px 14px;cursor:pointer;font-size:12px;font-weight:700;
  font-family:'Inter',sans-serif;transition:all 0.15s;color:var(--muted)
}
.tj-mode-btn:hover{color:var(--text)}
.tj-mode-btn#tj-mode-all.active{background:var(--accent);color:#fff}
.tj-mode-btn.actual.active{background:#059669;color:#fff}
.tj-mode-btn.dummy.active{background:#7c3aed;color:#fff}
/* Mode badge on table rows */
.tj-mode-badge{
  display:inline-block;font-size:9px;font-weight:700;
  border-radius:4px;padding:1px 6px;text-transform:uppercase;letter-spacing:0.5px
}
.tj-mode-badge.actual{background:#d1fae5;color:#065f46}
.tj-mode-badge.dummy{background:#ede9fe;color:#5b21b6}
.tj-select{
  background:#f0f4ff;border:1px solid #c8d4ee;border-radius:8px;
  padding:8px 14px;color:#1a2040;font-size:13px;font-family:'Inter',sans-serif;
  outline:none;cursor:pointer;font-weight:500
}
.tj-stats{
  display:grid;grid-template-columns:repeat(5,1fr);
  border-bottom:1px solid var(--border);flex-shrink:0;background:#fff
}
.tj-stat{
  padding:16px 22px;border-right:1px solid var(--border);
  display:flex;flex-direction:column;gap:4px;
}
.tj-stat:last-child{border-right:none}
.tj-stat-num{font-family:'Inter',sans-serif;font-size:24px;font-weight:700;line-height:1}
.tj-stat-num.g{color:#059669}
.tj-stat-num.r{color:#dc2626}
.tj-stat-num.b{color:#3b5bdb}
.tj-stat-lbl{font-size:11px;font-weight:700;text-transform:uppercase;letter-spacing:0.8px;color:#8898c0}
.tj-table-wrap{flex:1;padding:24px 28px;background:var(--bg);overflow-x:auto;-webkit-overflow-scrolling:touch}
.tj-table{
  width:100%;border-collapse:collapse;
  background:#fff;border-radius:12px;
  overflow:hidden;border:1px solid #dde4f5;
  box-shadow:0 2px 8px rgba(59,91,219,.06);
  min-width:640px
}
.tj-table th{
  background:#f0f4ff;color:#3b5bdb;font-weight:700;
  text-transform:uppercase;font-size:10px;letter-spacing:0.8px;
  padding:12px 14px;text-align:left;border-bottom:2px solid #dde4f5;
  white-space:nowrap
}
.tj-table td{
  padding:12px 14px;border-bottom:1px solid #f3f4ff;
  color:#374151;font-size:13px;vertical-align:middle
}
.tj-table tr:last-child td{border-bottom:none}
.tj-table tr:hover td{background:#f5f8ff;cursor:pointer}
.tj-symbol{font-weight:700;color:#1a2040;font-family:'Inter',sans-serif;font-size:14px}
.tj-type-buy{color:#059669;font-weight:700;font-size:11px;background:#d1fae5;border-radius:4px;padding:3px 9px}
.tj-type-sell{color:#dc2626;font-weight:700;font-size:11px;background:#fee2e2;border-radius:4px;padding:3px 9px}
.tj-pnl-g{color:#059669;font-weight:700}
.tj-pnl-r{color:#dc2626;font-weight:700}
.tj-badge{display:inline-block;border-radius:20px;padding:3px 10px;font-size:10px;font-weight:700}
.tj-badge.win{background:#d1fae5;color:#065f46}
.tj-badge.loss{background:#fee2e2;color:#991b1b}
.tj-badge.open{background:#dbeafe;color:#1e40af}
.tj-notes-cell{font-size:11px;color:#9ca3af;max-width:180px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.tj-act-btn{
  background:#f8faff;border:1px solid #dde4f5;border-radius:5px;
  padding:4px 10px;font-size:11px;cursor:pointer;color:#4a5880;
  font-family:'Inter',sans-serif;transition:all 0.15s;font-weight:600
}
.tj-act-btn:hover{border-color:#3b5bdb;color:#3b5bdb;background:#eef2ff}
.tj-act-btn.del:hover{border-color:#dc2626;color:#dc2626;background:#fff5f5}
.tj-empty{
  display:flex;flex-direction:column;align-items:center;
  justify-content:center;padding:60px 20px;color:var(--muted);
  gap:8px;text-align:center;
  background:#fff;border-radius:12px;border:2px dashed #dde4f5;
  margin-top:0
}
.tj-pnl-preview{
  background:#eef2ff;border:1px solid #c8d4ee;border-radius:8px;
  padding:11px 16px;display:flex;align-items:center;
  justify-content:space-between;font-size:13px;color:#4a5880;
  margin-top:4px;font-weight:500
}
@media(max-width:640px){
  .tj-toolbar{padding:12px 14px}
  .tj-table-wrap{padding:12px 14px}
  .tj-stats{grid-template-columns:repeat(3,1fr)}
  .tj-stat{padding:10px 12px}
}

/* -- ROUTINE PAGE ---------------------------------- */
.rt-header{
  display:flex;align-items:center;justify-content:space-between;
  padding:16px 28px;border-bottom:2px solid var(--accent);
  background:#fff;flex-wrap:wrap;gap:12px;
  box-shadow:0 2px 8px rgba(59,91,219,.06)
}
.rt-header-left{display:flex;flex-direction:column;gap:6px}
.rt-today-label{font-family:'Inter',sans-serif;font-size:16px;font-weight:700;color:#1a2040}
.rt-progress-wrap{display:flex;align-items:center;gap:10px}
.rt-progress-bar{
  width:220px;height:8px;background:#e8edf8;border-radius:4px;overflow:hidden
}
.rt-progress-fill{
  height:100%;background:#3b5bdb;border-radius:4px;transition:width 0.4s ease
}
.rt-progress-pct{font-size:12px;font-weight:700;color:#3b5bdb}
.rt-header-right{display:flex;gap:8px;align-items:center}

/* checklist groups */
.rt-group{
  background:#fff;border:1px solid #dde4f5;border-radius:12px;
  margin-bottom:16px;overflow:hidden;
  box-shadow:0 2px 8px rgba(59,91,219,.05)
}
.rt-group-header{
  display:flex;align-items:center;gap:10px;
  padding:14px 18px;cursor:pointer;
  border-bottom:1px solid #f0f4ff;
  user-select:none
}
.rt-group-icon{font-size:18px}
.rt-group-name{
  font-family:'Inter',sans-serif;font-size:15px;font-weight:700;color:#1a2040;flex:1
}
.rt-group-progress{
  font-size:11px;font-weight:700;color:#8898c0;
  background:#f0f4ff;border-radius:20px;padding:3px 10px
}
.rt-group-toggle{font-size:12px;color:#8898c0;margin-left:4px}
.rt-today-drag-handle{display:inline-flex;flex-direction:column;gap:3px;cursor:grab;padding:4px 8px;flex-shrink:0;user-select:none;opacity:.5;transition:opacity .15s;margin-right:2px}
.rt-today-drag-handle:hover{opacity:1}
.rt-today-drag-handle:active{cursor:grabbing;opacity:1}
.rt-today-drag-handle span{display:flex;gap:3px}
.rt-today-drag-handle span i{display:block;width:4px;height:4px;border-radius:50%;background:#3b5bdb;font-style:normal;pointer-events:none}
.rt-group.today-dragging{opacity:.4}
.rt-group.today-drag-over{border:2px dashed #3b5bdb;background:#f0f4ff}

/* color accent on group */
.rt-group.c-blue .rt-group-header{border-left:4px solid #3b5bdb}
.rt-group.c-green .rt-group-header{border-left:4px solid #059669}
.rt-group.c-purple .rt-group-header{border-left:4px solid #7c3aed}
.rt-group.c-yellow .rt-group-header{border-left:4px solid #d97706}
.rt-group.c-red .rt-group-header{border-left:4px solid #dc2626}

/* tasks inside group */
.rt-tasks{padding:4px 0}
.rt-task-row{
  display:flex;align-items:center;gap:12px;
  padding:11px 18px;border-bottom:1px solid #f8faff;
  transition:background 0.15s;cursor:pointer
}
.rt-task-row:last-child{border-bottom:none}
.rt-task-row:hover{background:#f8faff}
.rt-task-row.done{opacity:.55}

/* custom checkbox */
.rt-checkbox{
  width:20px;height:20px;border-radius:50%;border:2px solid #c8d4ee;
  display:flex;align-items:center;justify-content:center;
  flex-shrink:0;transition:all 0.2s;background:#fff
}
.rt-task-row.done .rt-checkbox{
  background:#3b5bdb;border-color:#3b5bdb;color:#fff;font-size:11px
}
.rt-task-info{flex:1;min-width:0}
.rt-task-name{
  font-size:14px;font-weight:600;color:#1a2040;
  transition:color 0.2s
}
.rt-task-row.done .rt-task-name{
  text-decoration:line-through;color:#9ca3af
}
.rt-task-meta{display:flex;align-items:center;gap:8px;margin-top:2px}
.rt-task-time{font-size:11px;color:#8898c0;font-weight:500}
.rt-task-freq{
  font-size:10px;font-weight:700;color:#3b5bdb;
  background:#eef2ff;border-radius:4px;padding:1px 6px
}
.rt-week-count{
  font-size:11px;font-weight:600;color:#6b7280;
  background:#f3f4f6;border-radius:4px;padding:2px 8px;
  margin-left:auto;white-space:nowrap
}

/* manage view */
.rt-manage-toolbar{
  display:flex;align-items:center;justify-content:space-between;
  margin-bottom:18px
}
.rt-manage-title{
  font-family:'Inter',sans-serif;font-size:17px;font-weight:700;color:#1a2040
}
.rt-manage-group{
  background:#fff;border:1px solid #dde4f5;border-radius:12px;
  margin-bottom:14px;overflow:hidden;transition:box-shadow 0.15s,opacity 0.15s
}
.rt-manage-group.drag-over{
  box-shadow:0 0 0 2px #3b5bdb;border-color:#3b5bdb
}
.rt-manage-group.dragging{opacity:.45}
.rt-manage-group-header{
  display:flex;align-items:center;gap:10px;padding:14px 18px;
  background:#f8faff;border-bottom:1px solid #f0f4ff
}
.rt-drag-handle{
  cursor:grab;color:#c8d4ee;font-size:16px;line-height:1;
  padding:0 4px;user-select:none;flex-shrink:0
}
.rt-drag-handle:active{cursor:grabbing}
.rt-manage-task-row.drag-over-top{border-top:2px solid #3b5bdb}
.rt-manage-task-row.dragging{opacity:.4}
.rt-manage-group-name{font-weight:700;color:#1a2040;font-size:14px;flex:1}
.rt-mg-btn{
  background:#f0f4ff;border:1px solid #dde4f5;border-radius:6px;
  padding:4px 10px;font-size:11px;color:#4a5880;cursor:pointer;
  font-family:'Inter',sans-serif;font-weight:600;transition:all 0.15s
}
.rt-mg-btn:hover{border-color:#3b5bdb;color:#3b5bdb}
.rt-mg-btn.del:hover{border-color:#dc2626;color:#dc2626}
.rt-manage-tasks{padding:8px 0}
.rt-manage-task-row{
  display:flex;align-items:center;gap:10px;
  padding:9px 18px;border-bottom:1px solid #f8faff;font-size:13px;color:#374151
}
.rt-manage-task-row:last-child{border-bottom:none}
.rt-mtr-info{flex:1}
.rt-mtr-name{font-weight:600;color:#1a2040}
.rt-mtr-meta{font-size:11px;color:#8898c0;margin-top:2px}
.rt-add-task-row{
  padding:10px 18px;display:flex;align-items:center;gap:8px;
  border-top:1px solid #f0f4ff;background:#fafbff
}
.rt-add-task-btn{
  background:none;border:1px dashed #c8d4ee;border-radius:7px;
  padding:6px 14px;font-size:12px;color:#8898c0;cursor:pointer;
  font-family:'Inter',sans-serif;transition:all 0.15s;width:100%;text-align:left
}
.rt-add-task-btn:hover{border-color:#3b5bdb;color:#3b5bdb}

/* icon picker */
.rt-icon-picker{display:flex;gap:8px;flex-wrap:wrap;margin-top:4px}
.rt-icon-opt{
  font-size:20px;cursor:pointer;padding:5px;border-radius:8px;
  border:2px solid transparent;transition:all 0.15s
}
.rt-icon-opt:hover{background:#f0f4ff}
.rt-icon-opt.selected{border-color:#3b5bdb;background:#eef2ff}

/* day picker */
.rt-day-picker{display:flex;gap:6px;flex-wrap:wrap;margin-top:4px}
.rt-day-opt{
  padding:5px 10px;border-radius:6px;border:1px solid #dde4f5;
  font-size:12px;font-weight:600;cursor:pointer;color:#6b7280;
  background:#f8faff;transition:all 0.15s
}
.rt-day-opt.selected{background:#3b5bdb;color:#fff;border-color:#3b5bdb}

@media(max-width:640px){
  .rt-header{padding:12px 14px}
  .rt-progress-bar{width:140px}
  #rt-today-view,#rt-manage-view{padding:12px 14px}
}

/* -- TASK & ACTION NOTES --------------------------- */
.tan-header{
  display:flex;align-items:center;justify-content:space-between;
  padding:16px 28px;border-bottom:2px solid var(--accent);
  background:var(--sidebar);flex-wrap:wrap;gap:12px;flex-shrink:0
}
.tan-title{font-family:'Inter',sans-serif;font-size:17px;font-weight:700;color:var(--text)}
.tan-quick-bar{
  display:flex;gap:10px;align-items:flex-start;
  padding:16px 28px;background:var(--bg);
  border-bottom:1px solid var(--border);flex-shrink:0
}
.tan-quick-bar textarea{
  flex:1;resize:none;min-height:48px;max-height:140px;
  background:var(--sidebar);border:1.5px solid var(--border2);
  border-radius:10px;padding:10px 14px;color:var(--text);
  font-family:'Inter',sans-serif;font-size:15px;outline:none;
  transition:border-color 0.2s;line-height:1.5
}
.tan-quick-bar textarea:focus{border-color:var(--accent)}
.tan-quick-bar textarea::placeholder{color:var(--muted)}
.tan-add-btn{
  display:inline-flex;align-items:center;gap:5px;
  background:var(--accent);color:#fff;border:none;border-radius:10px;
  padding:10px 18px;font-size:13px;font-weight:600;cursor:pointer;
  font-family:'Inter',sans-serif;white-space:nowrap;transition:background 0.2s;flex-shrink:0
}
.tan-add-btn:hover{background:var(--accent2)}
.tan-filters{
  display:flex;align-items:center;gap:8px;
  padding:12px 28px;background:var(--bg);
  border-bottom:1px solid var(--border);flex-shrink:0;flex-wrap:wrap
}
.tan-filter-btn{
  background:var(--sidebar);border:1px solid var(--border);border-radius:20px;
  padding:5px 14px;font-size:13px;color:var(--text2);cursor:pointer;
  font-family:'Inter',sans-serif;font-weight:600;transition:all 0.15s
}
.tan-filter-btn.active{background:var(--accent);color:#fff;border-color:var(--accent)}
.tan-filter-btn:hover:not(.active){border-color:var(--accent2);color:var(--accent)}
.tan-search{
  margin-left:auto;background:var(--sidebar);border:1px solid var(--border);
  border-radius:8px;padding:6px 12px;color:var(--text);font-size:13px;
  font-family:'Inter',sans-serif;outline:none;width:180px;transition:all 0.2s
}
.tan-search:focus{border-color:var(--accent);width:220px}
.tan-search::placeholder{color:var(--muted)}
.tan-list{flex:1;padding:10px 28px;overflow-y:auto}
.tan-list>div{max-width:920px}
.tan-empty{
  display:flex;flex-direction:column;align-items:center;justify-content:center;
  padding:60px 20px;color:var(--muted);gap:8px;text-align:center
}
.tan-empty-icon{font-size:40px;opacity:.4}
/* 3. Collapsible section headers */
.tan-section-hdr{
  display:flex;align-items:center;gap:8px;
  padding:8px 4px;cursor:pointer;user-select:none;margin-bottom:6px;margin-top:4px
}
.tan-section-hdr:hover .tan-section-title{color:var(--accent)}
.tan-section-title{font-size:12px;font-weight:700;text-transform:uppercase;letter-spacing:0.9px;color:var(--text2);transition:color 0.15s}
.tan-section-count{font-size:12px;background:var(--s2);color:var(--muted);border-radius:10px;padding:1px 8px;font-weight:600}
.tan-section-chevron{font-size:11px;color:var(--muted);transition:transform 0.2s}
.tan-section-chevron.collapsed{transform:rotate(-90deg)}
.tan-section-body{transition:opacity 0.2s}
.tan-section-body.collapsed{display:none}
/* 4. Card — left priority strip + shadow */
@keyframes tan-fadein{from{opacity:0;transform:translateY(6px)}to{opacity:1;transform:translateY(0)}}
@keyframes tan-fadeout{from{opacity:1;max-height:120px;margin-bottom:10px}to{opacity:0;max-height:0;margin-bottom:0;padding:0}}
.tan-item{
  background:var(--sidebar);border:1px solid var(--border);
  border-radius:8px;margin-bottom:4px;
  display:flex;flex-direction:column;gap:0;
  transition:border-color 0.15s;
  overflow:hidden;
  animation:tan-fadein 0.2s ease
}
/* Priority row tints for instant visual scanning */
.tan-item.is-high{background:rgba(220,38,38,.045);border-color:rgba(220,38,38,.18)}
.tan-item.is-medium{background:rgba(217,119,6,.035);border-color:rgba(217,119,6,.15)}
.tan-item.is-low{background:rgba(5,150,105,.03);border-color:rgba(5,150,105,.13)}
body.theme-midnight .tan-item.is-high{background:rgba(220,38,38,.07);border-color:rgba(220,38,38,.22)}
body.theme-midnight .tan-item.is-medium{background:rgba(217,119,6,.06);border-color:rgba(217,119,6,.2)}
body.theme-midnight .tan-item.is-low{background:rgba(5,150,105,.05);border-color:rgba(5,150,105,.18)}
body.theme-ember .tan-item.is-high{background:rgba(220,38,38,.08);border-color:rgba(220,38,38,.25)}
body.theme-ember .tan-item.is-medium{background:rgba(217,119,6,.07);border-color:rgba(217,119,6,.22)}
body.theme-ember .tan-item.is-low{background:rgba(5,150,105,.06);border-color:rgba(5,150,105,.2)}
.tan-item:hover{border-color:var(--border2)}
.tan-item.editing{border-color:var(--accent);box-shadow:0 0 0 2px rgba(139,94,42,.12)}
.tan-item.fading-out{animation:tan-fadeout 0.25s ease forwards}
.tan-item.is-done{opacity:.65}
.tan-item.is-done .tan-item-inner{padding:4px 10px;gap:2px}
.tan-item.is-done .tan-item-text{font-size:11px;line-height:1.3}
.tan-item.is-done .tan-item-priority{font-size:9px;padding:0px 5px}
.tan-item.is-done .tan-item-strip{width:3px}
.tan-item.is-done .tan-cat-badge{font-size:9px;padding:1px 6px}
.tan-item.is-done .tan-date-badge{font-size:9px;padding:1px 6px}
.tan-item.is-done .tan-item-meta{gap:4px}
.tan-item.is-done input[type=checkbox]{width:12px;height:12px}
.del-act{color:#c04040!important;border-color:rgba(192,64,64,.3)!important}
.del-act:hover{background:#f8eeec!important;border-color:#c04040!important}
/* 1. Left priority strip */
.tan-item-strip{width:4px;flex-shrink:0;border-radius:0;align-self:stretch;min-height:100%}
.tan-item-strip.high{background:#dc2626}
.tan-item-strip.medium{background:#d97706}
.tan-item-strip.low{background:#059669}
.tan-item-strip.none{background:var(--border)}
.tan-item-inner{padding:7px 12px;display:flex;flex-direction:column;gap:4px;flex:1;min-width:0}
.tan-item-top{display:flex;align-items:center;gap:8px}
.tan-item-priority{
  display:inline-flex;align-items:center;gap:3px;
  font-size:11px;font-weight:700;border-radius:4px;padding:1px 6px;
  flex-shrink:0;white-space:nowrap
}
.tan-item-priority.high{background:#fee2e2;color:#991b1b}
.tan-item-priority.medium{background:#fef3c7;color:#92400e}
.tan-item-priority.low{background:#d1fae5;color:#065f46}
.tan-item-text{
  font-family:'Inter',sans-serif;font-size:14px;
  color:var(--text);line-height:1.4;word-break:break-word;
  flex:1;min-width:0;font-weight:600
}
.tan-item-text.done{text-decoration:line-through;color:var(--muted)}
.tan-item-meta{display:flex;align-items:center;gap:5px;flex-wrap:wrap}
.tan-date-badge{
  font-size:12px;font-weight:600;color:var(--muted);
  background:var(--s2);border-radius:5px;padding:2px 8px;white-space:nowrap
}
/* 5. Tag chips */
.tan-tag-chip{
  display:inline-flex;align-items:center;gap:3px;
  background:rgba(139,94,42,.1);color:var(--accent);
  border-radius:20px;padding:1px 8px;font-size:12px;font-weight:600
}
.tan-tag-badge{
  font-size:11px;font-weight:700;text-transform:uppercase;letter-spacing:0.5px;
  background:rgba(139,94,42,.12);color:var(--accent);
  border-radius:5px;padding:2px 8px
}
.tan-cat-badge{
  font-size:11px;font-weight:700;text-transform:uppercase;letter-spacing:0.5px;
  border-radius:5px;padding:2px 9px;white-space:nowrap
}
.tan-cat-badge.personal{background:#dbeafe;color:#1e40af}
.tan-cat-badge.official{background:#f3e8ff;color:#6b21a8}
.tan-cat-sel{
  background:var(--sidebar);border:1.5px solid var(--border2);border-radius:10px;
  padding:10px 12px;color:var(--text);font-size:13px;font-family:'Inter',sans-serif;
  outline:none;cursor:pointer;flex-shrink:0;font-weight:600
}
.tan-cat-sel:focus{border-color:var(--accent)}
.tan-filters-row{display:flex;align-items:center;gap:8px;flex-wrap:wrap}
.tan-filters-divider{width:1px;height:20px;background:var(--border);flex-shrink:0;margin:0 4px}
.tan-item-actions{display:flex;gap:5px;align-items:center;flex-shrink:0;position:relative}
.tan-act{
  background:none;border:1px solid var(--border);border-radius:6px;
  padding:3px 9px;font-size:11px;cursor:pointer;color:var(--text2);
  font-family:'Inter',sans-serif;font-weight:600;transition:all 0.15s
}
.tan-act:hover{border-color:var(--accent);color:var(--accent)}
.tan-act.del:hover{border-color:#dc2626;color:#dc2626}
/* 7. 3-dot menu */
.tan-dot-btn{
  background:none;border:1px solid transparent;border-radius:6px;
  padding:3px 7px;font-size:14px;cursor:pointer;color:var(--muted);
  line-height:1;transition:all 0.15s
}
.tan-dot-btn:hover{border-color:var(--border);color:var(--text)}
.tan-dropdown{
  position:absolute;top:100%;right:0;z-index:50;margin-top:4px;
  background:var(--sidebar);border:1.5px solid var(--border2);
  border-radius:10px;min-width:160px;overflow:hidden;display:none;
  box-shadow:0 4px 16px rgba(0,0,0,.14)
}
.tan-dropdown.open{display:block}
.tan-dd-item{
  display:flex;align-items:center;gap:8px;padding:9px 14px;
  font-size:12px;font-weight:600;color:var(--text2);cursor:pointer;
  transition:background 0.12s;font-family:'Inter',sans-serif
}
.tan-dd-item:hover{background:var(--s2);color:var(--text)}
.tan-dd-item.danger:hover{background:#fee2e2;color:#dc2626}
.tan-edit-area{display:none;flex-direction:column;gap:8px;padding:0 14px 12px}
.tan-edit-area.open{display:flex}
.tan-edit-textarea{
  width:100%;resize:none;min-height:60px;
  background:var(--bg);border:1.5px solid var(--border2);
  border-radius:8px;padding:9px 12px;color:var(--text);
  font-family:'Inter',sans-serif;font-size:15px;outline:none;
  transition:border-color 0.2s
}
.tan-edit-textarea:focus{border-color:var(--accent)}
.tan-edit-row{display:flex;gap:8px;align-items:center;flex-wrap:wrap}
.tan-priority-sel{
  background:var(--bg);border:1px solid var(--border2);border-radius:7px;
  padding:5px 10px;color:var(--text);font-size:12px;font-family:'Inter',sans-serif;
  outline:none;cursor:pointer
}
.tan-tag-input{
  flex:1;background:var(--bg);border:1px solid var(--border2);border-radius:7px;
  padding:5px 10px;color:var(--text);font-size:12px;font-family:'Inter',sans-serif;
  outline:none;min-width:100px
}
.tan-tag-input::placeholder{color:var(--muted)}
.tan-save-btn{
  background:var(--accent);color:#fff;border:none;border-radius:7px;
  padding:6px 14px;font-size:12px;font-weight:600;cursor:pointer;
  font-family:'Inter',sans-serif;transition:background 0.2s
}
.tan-save-btn:hover{background:var(--accent2)}
.tan-done-cb{width:16px;height:16px;accent-color:var(--accent);cursor:pointer;flex-shrink:0;margin-top:3px}
/* 6. Sort select */
.tan-sort-sel{
  background:var(--sidebar);border:1px solid var(--border);border-radius:7px;
  padding:5px 10px;color:var(--text2);font-size:12px;font-family:'Inter',sans-serif;
  outline:none;cursor:pointer;font-weight:600;margin-left:auto
}
@media(max-width:640px){
  .tan-header,.tan-quick-bar,.tan-filters,.tan-list{padding-left:14px;padding-right:14px}
  .tan-filters{gap:6px}
}

/* -- FINANCE TRACKER ------------------------------- */
.fin-header{
  display:flex;align-items:center;justify-content:space-between;
  padding:16px 28px;border-bottom:2px solid var(--accent);
  background:var(--sidebar);flex-wrap:wrap;gap:12px;flex-shrink:0
}
.fin-title{font-family:'Inter',sans-serif;font-size:17px;font-weight:700;color:var(--text)}
.fin-summary{
  display:grid;grid-template-columns:repeat(3,1fr);
  gap:10px;padding:12px 20px;border-bottom:1px solid var(--border);
  background:var(--bg);flex-shrink:0
}
.fin-sum-card{
  background:var(--sidebar);border:1.5px solid var(--border);
  border-radius:10px;padding:10px 14px;display:flex;flex-direction:column;gap:3px
}
.fin-sum-label{font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:0.8px;color:var(--muted)}
.fin-sum-val{font-family:'Inter',sans-serif;font-size:19px;font-weight:700;line-height:1.1}
.fin-sum-val.gave{color:#059669}
.fin-sum-val.borrow{color:#dc2626}
.fin-sum-val.net-pos{color:#059669}
.fin-sum-val.net-neg{color:#dc2626}
.fin-sum-sub{font-size:11px;color:var(--muted);margin-top:2px}
.fin-people{
  padding:10px 20px 0;flex-shrink:0
}
.fin-people-title{
  font-size:11px;font-weight:700;text-transform:uppercase;letter-spacing:0.8px;
  color:var(--muted);margin-bottom:10px
}
.fin-person-chips{display:flex;gap:8px;flex-wrap:wrap;margin-bottom:4px}
.fin-person-chip{
  display:flex;align-items:center;gap:6px;
  background:var(--sidebar);border:1.5px solid var(--border);
  border-radius:20px;padding:6px 14px;cursor:pointer;transition:all 0.15s
}
.fin-person-chip:hover{border-color:var(--accent2)}
.fin-person-chip.active{border-color:var(--accent);background:rgba(139,94,42,.1)}
.fin-person-name{font-size:12px;font-weight:700;color:var(--text)}
.fin-person-bal{font-size:11px;font-weight:700}
.fin-person-bal.pos{color:#059669}
.fin-person-bal.neg{color:#dc2626}
.fin-filters{
  display:flex;align-items:center;gap:6px;flex-wrap:wrap;
  padding:8px 20px;border-bottom:1px solid var(--border);
  background:var(--bg);flex-shrink:0
}
.fin-list{flex:1;padding:12px 20px;overflow-y:auto}
.fin-grid{display:grid;grid-template-columns:repeat(6,minmax(0,1fr));gap:8px}
@media(max-width:1400px){.fin-grid{grid-template-columns:repeat(5,minmax(0,1fr))}}
@media(max-width:1200px){.fin-grid{grid-template-columns:repeat(4,minmax(0,1fr))}}
@media(max-width:900px){.fin-grid{grid-template-columns:repeat(3,minmax(0,1fr))}}
@media(max-width:600px){.fin-grid{grid-template-columns:repeat(2,minmax(0,1fr))}}
@media(max-width:400px){.fin-grid{grid-template-columns:1fr}}
/* Finance card — Notes-style */
.fin-card{
  background:var(--sidebar);border:1.5px solid var(--border);
  border-left:4px solid var(--accent);border-radius:12px;
  padding:14px 16px;display:flex;flex-direction:column;gap:8px;
  cursor:pointer;transition:border-color 0.18s,box-shadow 0.18s
}
.fin-card:hover{border-color:var(--border2);box-shadow:0 4px 14px rgba(139,94,42,.1)}
.fin-card.gave{border-left-color:#059669}
.fin-card.borrowed{border-left-color:#dc2626}
.fin-card.settled{border-left-color:#9ca3af}
.fin-card.overdue-card{border-left-color:#dc2626;border-color:rgba(220,38,38,.3);animation:fin-shake 3s ease 0.5s}
@keyframes fin-shake{0%,100%{transform:translateX(0)}10%{transform:translateX(-3px)}20%{transform:translateX(3px)}30%{transform:translateX(-2px)}40%{transform:translateX(2px)}50%,90%{transform:translateX(0)}}
/* 5. Sort select */
.fin-sort-sel{
  background:var(--sidebar);border:1px solid var(--border);border-radius:7px;
  padding:5px 10px;color:var(--text2);font-size:12px;font-family:'Inter',sans-serif;
  outline:none;cursor:pointer;font-weight:600
}
/* 2. Group by person */
.fin-group-hdr{
  display:flex;align-items:center;gap:10px;
  padding:10px 4px 6px;cursor:pointer;user-select:none
}
.fin-group-hdr:hover .fin-group-name{color:var(--accent)}
.fin-group-chevron{font-size:10px;color:var(--muted);transition:transform 0.2s}
.fin-group-chevron.collapsed{transform:rotate(-90deg)}
.fin-group-name{font-family:'Inter',sans-serif;font-size:13px;font-weight:700;color:var(--text);transition:color 0.15s}
.fin-group-bal{font-size:11px;font-weight:700}
.fin-group-bal.pos{color:#059669}
.fin-group-bal.neg{color:#dc2626}
.fin-group-count{font-size:10px;background:var(--s2);color:var(--muted);border-radius:10px;padding:1px 7px;font-weight:600}
.fin-group-body{transition:opacity 0.2s}
.fin-group-body.collapsed{display:none}
/* 7. Timeline panel */
.fin-timeline-panel{
  display:none;background:var(--bg);border-top:1px solid var(--border);
  padding:16px 28px;flex-shrink:0;max-height:260px;overflow-y:auto
}
.fin-timeline-panel.open{display:block}
.fin-tl-title{font-size:11px;font-weight:700;text-transform:uppercase;letter-spacing:0.8px;color:var(--muted);margin-bottom:12px}
.fin-tl-row{
  display:flex;align-items:center;gap:10px;
  padding:8px 0;border-bottom:1px solid var(--border);font-size:12px
}
.fin-tl-row:last-child{border-bottom:none}
.fin-tl-dot{width:10px;height:10px;border-radius:50%;flex-shrink:0}
.fin-tl-dot.overdue{background:#dc2626}
.fin-tl-dot.today{background:#d97706}
.fin-tl-dot.soon{background:#d97706}
.fin-tl-dot.ok{background:#059669}
.fin-tl-date{font-weight:700;color:var(--text);min-width:90px}
.fin-tl-person{color:var(--text2);flex:1}
.fin-tl-amt{font-weight:700;font-family:'Inter',sans-serif}
.fin-tl-amt.gave{color:#059669}
.fin-tl-amt.borrowed{color:#dc2626}
/* 8. Settled history toggle */
.fin-settled-section{margin-top:8px}
.fin-settled-hdr{
  display:flex;align-items:center;gap:8px;cursor:pointer;
  padding:8px 4px;user-select:none
}
.fin-settled-hdr:hover span{color:var(--accent)}
.fin-settled-body{transition:opacity 0.2s}
.fin-settled-body.collapsed{display:none}
.fin-card-eyebrow{display:flex;align-items:center;justify-content:space-between}
.fin-card-type{font-size:10px;text-transform:uppercase;letter-spacing:1.2px;color:var(--muted);font-weight:700}
.fin-card-person{font-family:'Inter',sans-serif;font-size:16px;font-weight:700;color:var(--text);line-height:1.3}
.fin-card-amount{font-family:'Inter',sans-serif;font-size:19px;font-weight:700}
.fin-card-amount.gave{color:#059669}
.fin-card-amount.borrowed{color:#dc2626}
.fin-card-amount.settled{color:var(--muted)}
.fin-card-note{font-family:'Inter',sans-serif;font-size:14px;color:var(--text2);line-height:1.5}
.fin-card-tags{display:flex;gap:5px;flex-wrap:wrap}
.fin-card-meta{
  display:flex;align-items:center;justify-content:space-between;
  padding-top:8px;border-top:1px solid var(--border);flex-wrap:wrap;gap:6px
}
.fin-card-date{font-size:10px;color:var(--muted);font-weight:500}
.fin-card-btns{display:flex;gap:5px;flex-wrap:wrap}
/* Finance list-row */
.fin-view-toggle{
  display:flex;background:var(--s2);border:1.5px solid var(--border);
  border-radius:8px;padding:3px;gap:2px
}
.fin-vtbtn{
  background:none;border:none;border-radius:6px;
  padding:5px 13px;cursor:pointer;color:var(--muted);
  font-size:12px;font-weight:700;line-height:1;transition:all 0.15s;
  font-family:'Inter',sans-serif;white-space:nowrap
}
.fin-vtbtn:hover{color:var(--text)}
.fin-vtbtn.active{background:var(--accent);color:#fff}
body.theme-midnight .fin-vtbtn.active{color:#141920}
body.theme-ember .fin-vtbtn.active{color:#0f0d0b}

/* List view table */
.fin-view-list .fin-grid{display:none}
.fin-view-list .fin-listbox{display:block}
.fin-view-card .fin-listbox{display:none}
.fin-listbox{
  display:none;border-radius:10px;overflow:hidden;
  border:1px solid var(--border)
}
/* table header */
.fin-list-thead{
  display:grid;
  grid-template-columns:3px 28px minmax(120px,200px) 100px 110px 90px 80px 1fr;
  align-items:center;gap:0;
  background:var(--s2);border-bottom:2px solid var(--border2);
  padding:7px 14px;
}
.fin-list-th{
  font-size:10px;font-weight:700;text-transform:uppercase;
  letter-spacing:0.8px;color:var(--muted)
}
.fin-lrow{
  display:grid;
  grid-template-columns:3px 28px minmax(120px,200px) 100px 110px 90px 80px 1fr;
  align-items:center;gap:0;
  padding:8px 14px;border-bottom:1px solid var(--border);
  background:var(--sidebar);cursor:pointer;transition:background 0.15s
}
.fin-lrow:last-child{border-bottom:none}
.fin-lrow:hover{background:var(--s2)}
.fin-lrow-accent{width:3px;height:100%;min-height:32px;border-radius:2px;flex-shrink:0}
.fin-lrow-accent.gave{background:#059669}
.fin-lrow-accent.borrowed{background:#dc2626}
.fin-lrow-accent.settled{background:#9ca3af}
.fin-lrow-main{flex:1;min-width:0;padding:0 8px}
.fin-lrow-person{font-size:13px;font-weight:700;color:var(--text)}
.fin-lrow-note{font-size:11px;color:var(--muted);white-space:nowrap;overflow:hidden;text-overflow:ellipsis;max-width:180px}
.fin-lrow-right{display:flex;align-items:center;gap:6px;flex-shrink:0}
.fin-lrow-amt{font-family:'Inter',sans-serif;font-size:14px;font-weight:700}
.fin-lrow-amt.gave{color:#059669}
.fin-lrow-amt.borrowed{color:#dc2626}
.fin-lrow-amt.settled{color:var(--muted)}
.fin-empty{
  display:flex;flex-direction:column;align-items:center;justify-content:center;
  padding:60px 20px;color:var(--muted);gap:8px;text-align:center
}
.fin-item{
  background:var(--sidebar);border:1.5px solid var(--border);
  border-radius:8px;padding:7px 10px;margin-bottom:0;
  cursor:pointer;transition:border-color 0.15s,background 0.15s
}
.fin-item:hover{border-color:var(--border2)}
.fin-item.expanded{border-color:var(--accent);background:var(--bg)}
.fin-item-row{display:flex;align-items:center;gap:8px}
.fin-item-dot{width:7px;height:7px;border-radius:50%;flex-shrink:0}
.fin-item-dot.gave{background:#059669}
.fin-item-dot.borrowed{background:#dc2626}
.fin-item-dot.settled{background:#9ca3af}
.fin-item-body{flex:1;min-width:0}
.fin-item-person{font-size:12px;font-weight:700;color:var(--text);line-height:1.2}
.fin-item-meta{display:flex;align-items:center;gap:4px;flex-wrap:wrap;margin-top:2px}
.fin-item-right{display:flex;flex-direction:column;align-items:flex-end;gap:1px;flex-shrink:0}
.fin-item-amount{font-family:'Inter',sans-serif;font-size:13px;font-weight:700}
.fin-item-amount.gave{color:#059669}
.fin-item-amount.borrowed{color:#dc2626}
.fin-item-amount.settled{color:var(--muted)}
.fin-item-remaining{font-size:10px;color:var(--muted)}
.fin-item-expand{
  display:none;margin-top:8px;padding-top:8px;
  border-top:1px dashed var(--border)
}
.fin-item.expanded .fin-item-expand{display:block}
.fin-status-badge{
  font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:0.5px;
  border-radius:5px;padding:2px 8px
}
.fin-status-badge.pending{background:#fef3c7;color:#92400e}
.fin-status-badge.partial{background:#dbeafe;color:#1e40af}
.fin-status-badge.settled{background:#d1fae5;color:#065f46}
.fin-status-badge.overdue{background:#fee2e2;color:#991b1b}
.fin-date-badge{
  font-size:11px;font-weight:600;color:var(--muted);
  background:var(--s2);border-radius:5px;padding:2px 8px
}
.fin-due-badge{font-size:11px;font-weight:600;border-radius:5px;padding:2px 8px}
.fin-due-badge.ok{background:#d1fae5;color:#065f46}
.fin-due-badge.warn{background:#fef3c7;color:#92400e}
.fin-due-badge.over{background:#fee2e2;color:#991b1b}
.fin-item-actions{display:flex;gap:6px;margin-top:10px;flex-wrap:wrap;align-items:center}
.fin-act{
  background:none;border:1px solid var(--border);border-radius:6px;
  padding:4px 10px;font-size:11px;cursor:pointer;color:var(--text2);
  font-family:'Inter',sans-serif;font-weight:600;transition:all 0.15s
}
.fin-act:hover{border-color:var(--accent);color:var(--accent)}
.fin-act.del:hover{border-color:#dc2626;color:#dc2626}
.fin-act.settle{background:var(--accent);color:#fff;border-color:var(--accent)}
.fin-act.settle:hover{background:var(--accent2)}
.fin-repay-section{
  margin-top:10px;padding-top:10px;border-top:1px dashed var(--border)
}
.fin-repay-title{
  font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:0.8px;
  color:var(--muted);margin-bottom:8px
}
.fin-repay-row{
  display:flex;align-items:center;gap:8px;
  padding:5px 0;border-bottom:1px solid var(--border);font-size:12px;color:var(--text2)
}
.fin-repay-row:last-child{border-bottom:none}
.fin-repay-amt{font-weight:700;color:#059669;min-width:80px}
.fin-repay-note{flex:1;color:var(--muted)}
.fin-pay-badge{
  font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:0.4px;
  border-radius:5px;padding:2px 7px;white-space:nowrap
}
.fin-pay-badge.cash{background:#d1fae5;color:#065f46}
.fin-pay-badge.credit_card{background:#dbeafe;color:#1e40af}
.fin-pay-badge.bank{background:#ede9fe;color:#5b21b6}
.fin-pay-badge.upi{background:#fef3c7;color:#92400e}
.fin-rtype-badge{
  font-size:10px;font-weight:700;border-radius:5px;padding:2px 7px;white-space:nowrap
}
.fin-rtype-badge.principal{background:#d1fae5;color:#065f46}
.fin-rtype-badge.interest{background:#fee2e2;color:#991b1b}
.fin-rtype-badge.both{background:#dbeafe;color:#1e40af}
.fin-history-wrap{
  margin-top:10px;padding-top:10px;border-top:1px dashed var(--border)
}
.fin-history-header{
  display:flex;align-items:center;justify-content:space-between;margin-bottom:8px
}
.fin-history-title{
  font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:0.8px;color:var(--muted)
}
.fin-history-summary{font-size:11px;color:var(--muted)}
.fin-repay-row{
  display:flex;align-items:center;gap:6px;flex-wrap:wrap;
  padding:7px 0;border-bottom:1px solid var(--border);font-size:12px
}
.fin-repay-row:last-child{border-bottom:none}
.fin-repay-input{
  background:var(--bg);border:1.5px solid var(--border2);border-radius:7px;
  padding:6px 10px;color:var(--text);font-size:13px;font-family:'Inter',sans-serif;
  outline:none;transition:border-color 0.2s
}
.fin-repay-input:focus{border-color:var(--accent)}
.fin-repay-input::placeholder{color:var(--muted)}
.fin-add-repay-btn{
  background:var(--accent);color:#fff;border:none;border-radius:7px;
  padding:7px 14px;font-size:12px;font-weight:600;cursor:pointer;
  font-family:'Inter',sans-serif;white-space:nowrap;transition:background 0.2s
}
.fin-add-repay-btn:hover{background:var(--accent2)}
.fin-progress{
  height:5px;background:var(--s2);border-radius:3px;overflow:hidden;margin-top:8px
}
.fin-progress-fill{height:100%;border-radius:3px;background:#059669;transition:width 0.4s}
@media(max-width:640px){
  .fin-header,.fin-summary,.fin-people,.fin-filters,.fin-list{padding-left:12px;padding-right:12px}
  .fin-summary{grid-template-columns:1fr 1fr}
  /* Finance cards: make buttons wrap so Delete is never cut off */
  .fin-card-btns{flex-wrap:wrap;gap:4px;justify-content:flex-start}
  .fin-card-btns .cbtn{font-size:11px;padding:4px 10px;flex-shrink:0}
  .fin-card-meta{flex-direction:column;align-items:flex-start;gap:6px}
  /* Finance grid: single column on mobile for more card width */
  .fin-grid{grid-template-columns:1fr !important}
}

/* -- TOAST ----------------------------------------- */
#toast{
  position:fixed;bottom:20px;right:20px;z-index:999;
  background:var(--sidebar);border:1px solid var(--border2);
  border-radius:10px;padding:10px 16px;font-size:13px;color:var(--text);
  transform:translateY(50px);opacity:0;transition:all 0.25s;pointer-events:none
}
#toast.show{transform:translateY(0);opacity:1}
#toast.success{border-color:var(--green);color:var(--green)}
#toast.error{border-color:var(--red);color:var(--red)}

/* -- RESPONSIVE ------------------------------------ */
@media(max-width:860px){
  .stats-row{grid-template-columns:repeat(2,1fr)}
  .sp-toolbar{flex-direction:column;align-items:flex-start}
  .sp-toolbar-right{width:100%;justify-content:space-between}
  /* hide SGT on tablet, keep IST + CST + FOCUS toggle */
  .clock-block:nth-child(3){display:none}
  .clock-block{min-width:90px;padding:0 12px}
  .clock-time{font-size:13px}
  /* Notes: compact columns on tablet */
  .notes-folders-panel{width:160px}
  .notes-list-panel{width:220px}
  /* Reminders: compact tiles */
  .rem-summary-row{grid-template-columns:repeat(2,1fr);padding:10px 14px}
  .rem-lists-panel{width:160px}
  /* Finance list: hide date column */
  .fin-list-thead,.fin-lrow{grid-template-columns:3px 28px 1fr 80px 100px 180px}
  .fin-lrow>div:nth-child(7),.fin-list-thead>div:nth-child(7){display:none}
  /* Notification prompt */
  .notif-prompt{margin:8px 14px}
  /* Full calendar: tighter on tablet */
  .full-cal-event{font-size:10px}
  .full-cal-day-num{font-size:11px}
}
@media(max-width:640px){
  body{height:auto;overflow:auto}
  .layout{height:auto;overflow:visible;flex-direction:column}
  .main{margin-left:0;overflow:visible;height:auto;min-height:100vh}
  #page-scroll-area{overflow:visible;flex:none}
  aside{
    transform:translateX(-100%);
    transition:transform 0.25s ease;
    z-index:200;
    width:260px;
    box-shadow:4px 0 24px rgba(0,0,0,.3)
  }
  aside.open{transform:translateX(0)}
  .sidebar-overlay{
    display:none;position:fixed;inset:0;
    background:rgba(0,0,0,.5);z-index:199
  }
  .sidebar-overlay.open{display:block}
  .topbar{padding:0}
  /* hide clocks entirely on mobile — remove the whole bar from layout, not just its children */
  .clock-bar{display:none!important}
  /* Compact the sync pill — show only the dot, hide the text on small screens */
  .topbar-sync{padding:4px 8px!important;gap:0!important}
  .topbar-sync-text,.topbar-sync span:not(.topbar-sync-dot){display:none!important}
  /* Hide topbar search on narrow screens — the primary action buttons matter more */
  #topbar-search{display:none!important}
  /* Shrink topbar action buttons so everything fits */
  .topbar-ctx .btn{padding:6px 10px;font-size:11px}
  #topbar-add-btn{padding:6px 10px;font-size:11px}
  .hamburger{
    display:flex!important;align-items:center;justify-content:center;
    background:var(--s2);border:1px solid var(--border2);
    border-radius:8px;width:36px;height:36px;
    font-size:16px;cursor:pointer;flex-shrink:0
  }
  .stats-row{
    grid-template-columns:repeat(2,1fr);
    gap:10px
  }
  .stat-card{padding:12px}
  .stat-num{font-size:20px!important}
  .dash-wrap{padding:12px 14px}
  .dash-bottom{grid-template-columns:1fr}
  .cards-grid{grid-template-columns:1fr}
  .sec-header{flex-wrap:wrap;gap:8px}
  .theme-grid{grid-template-columns:1fr 1fr}
  .settings-modal{padding:20px}
  .sp-board{padding:14px}
  .sp-toolbar{padding:12px 14px}
  .lrow-date{display:none}
  .lrow-tags{max-width:100px}
  /* Full calendar: compact on mobile */
  .full-cal-header{padding:8px 10px;gap:6px}
  .full-cal-title{font-size:14px}
  .full-cal-nav{padding:4px 8px;font-size:12px}
  .full-cal-cell{min-height:48px;padding:2px}
  .full-cal-event{font-size:9px;padding:1px 3px}
  .full-cal-day-num{font-size:10px;width:18px;height:18px}
  /* Markdown toolbar: scrollable on mobile */
  .md-toolbar{overflow-x:auto;flex-wrap:nowrap;padding-bottom:6px}
  .md-toolbar::-webkit-scrollbar{height:3px}
  /* Template row: align right */
  #tmpl-row{justify-content:flex-end}
  .tmpl-dropdown{min-width:160px;right:0}
  /* Calendar: compact */
  .cal-wrap{padding:8px 12px 0}
  .cal-day{font-size:11px}
  /* Reminder view toggle */
  .rem-view-toggle{padding:6px 12px 8px}
  /* Notification prompt */
  .notif-prompt{margin:8px 12px;flex-wrap:wrap;gap:6px}
  /* Priority badge: smaller */
  .prio-badge{font-size:9px;padding:1px 6px}
  .lrow-due{font-size:10px;padding:2px 6px}
  .modal{padding:16px}
  /* Finance modal: single column on mobile so Save button is always reachable */
  .fin-modal-grid{grid-template-columns:1fr !important}
  /* Overlay: proper padding on mobile — extra bottom so Save button clears the browser chrome */
  .overlay{padding:12px 8px 80px;align-items:flex-start}
  /* Dashboard calendar widget: stack on mobile so reminders panel gets full width */
  .dash-cal-widget{grid-template-columns:1fr;gap:10px}
  .dash-cal-left{min-width:unset}
  .upc-title{white-space:normal;overflow:visible;text-overflow:clip;font-size:12px}
  .upc-item{flex-wrap:wrap;gap:6px}
  .upc-due{font-size:11px}
  /* Notes page: handled by the dedicated mobile block below */
  /* Reminders page: summary tiles only — slide panel layout is handled in the second @media block below */
  .rem-summary-row{grid-template-columns:repeat(2,1fr);padding:10px 12px;gap:8px}
  .rem-tile-count{font-size:22px}
  /* ── Finance list ── */
  .fin-list-thead{display:none}
  .fin-lrow{display:flex;flex-wrap:wrap;gap:6px;align-items:flex-start;padding:12px 14px}
  .fin-lrow-accent{display:none}
  .fin-lrow-main{width:100%;order:1}
  .fin-lrow>div:nth-child(4){order:2;font-size:15px;font-weight:700}
  .fin-lrow>div:nth-child(5){order:3}
  .fin-lrow>div:nth-child(6){order:4}
  .fin-lrow>div:nth-child(7){display:none}
  .fin-lrow-right{order:5;width:100%;justify-content:flex-start}
  /* Finance summary */
  .fin-summary{grid-template-columns:1fr 1fr}

  /* Show back bar on mobile, hide on desktop */
  .notes-mobile-back{display:flex}
  .rem-mobile-back{display:flex}
}
/* Hide back bars on desktop */
@media(min-width:641px){
  .notes-mobile-back,.rem-mobile-back{display:none !important}
}
.hamburger{display:none}

/* == RESPONSIVE: NEW PAGES == */
@media(max-width:860px){
  .notes-folders-panel{width:160px}
  .notes-list-panel{width:220px}
  .rem-summary-row{grid-template-columns:repeat(2,1fr)}
  .rem-lists-panel{width:160px}
  .fin-list-thead,.fin-lrow{grid-template-columns:3px 28px 1fr 80px 80px 160px}
  .fin-list-thead>div:nth-child(6),.fin-lrow>div:nth-child(6){display:none}
}
@media(max-width:600px){
  .rem-right-panel{display:none}
  .rem-checklist-panel{flex:1 1 100%}
}

/* Mobile: single-panel navigation */
@media(max-width:640px){
  /* ── NOTES PAGE ── */
  /* Give the notes page a fixed viewport height so absolute slide panels work */
  #page-notes{
    height:calc(100vh - 58px);
    max-height:calc(100vh - 58px);
    flex:none;
    overflow:hidden;
    display:flex;
    flex-direction:column
  }
  .notes-page-wrap{
    flex-direction:column;height:100%;
    overflow:hidden;position:relative;
    flex:1;min-height:0
  }
  /* notes-columns mirrors rem-columns */
  .notes-columns{
    position:relative;flex:1;overflow:hidden
  }
  .notes-folders-panel,.notes-list-panel,.notes-editor-panel{
    position:absolute;top:0;left:0;
    width:100%;height:100%;
    transition:transform 0.25s ease;
    border-right:none !important
  }
  /* Default: folders visible */
  .notes-folders-panel{transform:translateX(0);z-index:3;background:var(--s2)}
  .notes-list-panel{transform:translateX(100%);z-index:2;background:var(--sidebar)}
  .notes-editor-panel{transform:translateX(200%);z-index:1;background:var(--bg)}
  /* State: show-list */
  .notes-columns.show-list .notes-folders-panel{transform:translateX(-100%)}
  .notes-columns.show-list .notes-list-panel{transform:translateX(0)}
  .notes-columns.show-list .notes-editor-panel{transform:translateX(100%)}
  /* State: show-editor */
  .notes-columns.show-editor .notes-folders-panel{transform:translateX(-100%)}
  .notes-columns.show-editor .notes-list-panel{transform:translateX(-100%)}
  .notes-columns.show-editor .notes-editor-panel{transform:translateX(0);z-index:4}
  /* Scrolling and layout inside panels */
  .notes-folders-panel{overflow-y:auto}
  .notes-list-panel{display:flex;flex-direction:column;overflow:hidden}
  .notes-list-items{flex:1;overflow-y:auto}
  .notes-editor-content{padding:18px 16px}
  .notes-editor-topbar{padding:10px 16px}
  /* Mobile back bar */
  .notes-mobile-back{
    display:flex;align-items:center;gap:8px;
    padding:10px 14px;border-bottom:1px solid var(--border);
    background:var(--sidebar);flex-shrink:0;cursor:pointer
  }
  .notes-mobile-back-btn{
    background:none;border:none;color:var(--accent);
    font-size:14px;font-weight:700;cursor:pointer;
    display:flex;align-items:center;gap:4px;
    font-family:'Inter',sans-serif;padding:4px 0
  }
  .notes-mobile-back-btn:active{opacity:0.6}

  /* ── REMINDERS PAGE ── */
  .rem-page-wrap{
    flex-direction:column;height:calc(100vh - 58px);
    overflow:hidden;position:relative
  }
  .rem-summary-row{
    grid-template-columns:repeat(2,1fr);
    padding:10px 12px;gap:8px;flex-shrink:0
  }
  .rem-tile-count{font-size:20px}
  .rem-tile-label{font-size:12px}
  /* Lists and checklist become slides */
  .rem-columns{
    position:relative;flex:1;overflow:hidden
  }
  .rem-lists-panel,.rem-checklist-panel{
    position:absolute;top:0;left:0;
    width:100%;height:100%;
    transition:transform 0.25s ease
  }
  .rem-right-panel{display:none}
  .rem-lists-panel{transform:translateX(0);z-index:2;background:var(--s2)}
  .rem-checklist-panel{transform:translateX(100%);z-index:1;background:var(--bg)}
  .rem-columns.show-checklist .rem-lists-panel{transform:translateX(-100%)}
  .rem-columns.show-checklist .rem-checklist-panel{transform:translateX(0)}
  /* Mobile back in checklist header */
  .rem-mobile-back{
    display:flex;align-items:center;gap:8px;
    padding:8px 14px;border-bottom:1px solid var(--border);
    background:var(--sidebar);flex-shrink:0
  }
  .rem-mobile-back-btn{
    background:none;border:none;color:var(--accent);
    font-size:13px;font-weight:700;cursor:pointer;
    font-family:'Inter',sans-serif;padding:4px 0;
    display:flex;align-items:center;gap:4px
  }
  .rem-checklist-body{padding:6px 14px 20px}
  .rem-checklist-hdr{padding:10px 14px 8px}
  .rem-lists-hdr{padding:10px 14px}
  .rem-list-items{overflow-y:auto;height:calc(100% - 50px)}

  /* ── Finance list ── */
  .fin-list-thead{display:none}
  .fin-lrow{
    grid-template-columns:3px 1fr auto;
    grid-template-rows:auto auto;gap:4px;padding:10px 12px
  }
  .fin-lrow>div:nth-child(1){grid-row:1/3}
  .fin-lrow>div:nth-child(2){display:none}
  .fin-lrow-main{grid-column:2;grid-row:1}
  .fin-lrow>div:nth-child(4){grid-column:3;grid-row:1;font-size:13px;font-weight:700}
  .fin-lrow>div:nth-child(5),.fin-lrow>div:nth-child(6),.fin-lrow>div:nth-child(7){display:none}
  .fin-lrow-right{grid-column:2/4;grid-row:2;flex-wrap:wrap}
  .fin-header{padding:12px 14px}

  /* Show back bars on mobile */
  .notes-mobile-back,.rem-mobile-back{display:flex}
}
/* Hide back bars on desktop */
@media(min-width:641px){
  .notes-mobile-back,.rem-mobile-back{display:none !important}
}


/* ====================================================
   DARK THEME OVERRIDES (midnight + ember)
   ==================================================== */

/* ── Notes editor ─────────────────────────────── */
body.theme-midnight .notes-editor-panel, body.theme-ember .notes-editor-panel {background:var(--bg)}
body.theme-midnight .notes-editor-topbar, body.theme-ember .notes-editor-topbar {background:var(--sidebar);border-bottom:1px solid var(--border)}

/* ── Trading Journal ──────────────────────────── */
body.theme-midnight .tj-toolbar, body.theme-ember .tj-toolbar {background:var(--sidebar);border-bottom:2px solid var(--border)}
body.theme-midnight .tj-stats, body.theme-ember .tj-stats {background:var(--sidebar);border-bottom:1px solid var(--border)}
body.theme-midnight .tj-stat-lbl, body.theme-ember .tj-stat-lbl {color:var(--muted)}
body.theme-midnight .tj-stat-num.b, body.theme-ember .tj-stat-num.b {color:var(--blue)}
body.theme-midnight .tj-table, body.theme-ember .tj-table {background:var(--s2);border:1px solid var(--border);box-shadow:none}
body.theme-midnight .tj-table th, body.theme-ember .tj-table th {background:var(--sidebar);color:var(--accent);border-bottom:1px solid var(--border)}
body.theme-midnight .tj-table td, body.theme-ember .tj-table td {border-bottom:1px solid var(--border);color:var(--text2)}
body.theme-midnight .tj-table tr:hover td, body.theme-ember .tj-table tr:hover td {background:rgba(255,255,255,.03)}
body.theme-midnight .tj-select, body.theme-ember .tj-select {background:var(--s2);border:1px solid var(--border);color:var(--text)}
body.theme-midnight .tj-mode-badge.actual, body.theme-ember .tj-mode-badge.actual {background:rgba(90,170,112,.15);color:#6aba88}
body.theme-midnight .tj-mode-badge.dummy, body.theme-ember .tj-mode-badge.dummy {background:rgba(160,128,220,.15);color:#a080dc}

/* ── Routine header ───────────────────────────── */
body.theme-midnight .rt-header, body.theme-ember .rt-header {background:var(--sidebar);box-shadow:none;border-bottom:2px solid var(--accent)}
body.theme-midnight .rt-today-label, body.theme-ember .rt-today-label {color:var(--text)}
body.theme-midnight .rt-progress-bar, body.theme-ember .rt-progress-bar {background:var(--border)}
body.theme-midnight .rt-progress-fill, body.theme-ember .rt-progress-fill {background:var(--accent)}
body.theme-midnight .rt-progress-pct, body.theme-ember .rt-progress-pct {color:var(--accent)}

/* ── Routine groups ───────────────────────────── */
body.theme-midnight .rt-group, body.theme-ember .rt-group {background:var(--s2);border:1px solid var(--border);box-shadow:none}
body.theme-midnight .rt-group-header, body.theme-ember .rt-group-header {background:var(--s2);border-bottom:1px solid var(--border)}
body.theme-midnight .rt-group-name, body.theme-ember .rt-group-name {color:var(--text)}
body.theme-midnight .rt-group-progress, body.theme-ember .rt-group-progress {background:var(--sidebar);color:var(--muted)}
body.theme-midnight .rt-group-toggle, body.theme-ember .rt-group-toggle {color:var(--muted)}

/* ── Routine tasks ────────────────────────────── */
body.theme-midnight .rt-task-row, body.theme-ember .rt-task-row {border-bottom:1px solid var(--border)}
body.theme-midnight .rt-task-row:hover, body.theme-ember .rt-task-row:hover {background:rgba(255,255,255,.04)}
body.theme-midnight .rt-task-name, body.theme-ember .rt-task-name {color:var(--text);font-size:14px;font-weight:500}
body.theme-midnight .rt-task-time, body.theme-ember .rt-task-time {color:var(--accent);font-weight:600}
body.theme-midnight .rt-task-freq, body.theme-ember .rt-task-freq {background:rgba(255,255,255,.08);color:var(--text2);border:1px solid var(--border)}
body.theme-midnight .rt-week-count, body.theme-ember .rt-week-count {background:var(--sidebar);color:var(--muted)}
body.theme-midnight .rt-checkbox, body.theme-ember .rt-checkbox {background:var(--s2);border:2px solid var(--border2)}
body.theme-midnight .rt-task-row.done .rt-checkbox, body.theme-ember .rt-task-row.done .rt-checkbox {background:var(--accent);border-color:var(--accent)}

/* ── Routine manage view ──────────────────────── */
body.theme-midnight .rt-manage-group, body.theme-ember .rt-manage-group {background:var(--s2);border:1px solid var(--border)}
body.theme-midnight .rt-manage-group-header, body.theme-ember .rt-manage-group-header {background:var(--sidebar);border-bottom:1px solid var(--border)}
body.theme-midnight .rt-manage-group-name, body.theme-ember .rt-manage-group-name {color:var(--text)}
body.theme-midnight .rt-manage-task-row, body.theme-ember .rt-manage-task-row {border-bottom:1px solid var(--border);color:var(--text2)}
body.theme-midnight .rt-mtr-name, body.theme-ember .rt-mtr-name {color:var(--text)}
body.theme-midnight .rt-mtr-meta, body.theme-ember .rt-mtr-meta {color:var(--muted)}
body.theme-midnight .rt-add-task-row, body.theme-ember .rt-add-task-row {background:var(--sidebar);border-top:1px solid var(--border)}
body.theme-midnight .rt-add-task-btn, body.theme-ember .rt-add-task-btn {border-color:var(--border2);color:var(--muted)}
body.theme-midnight .rt-add-task-btn:hover, body.theme-ember .rt-add-task-btn:hover {border-color:var(--accent);color:var(--accent)}
body.theme-midnight .rt-mg-btn, body.theme-ember .rt-mg-btn {background:var(--s2);border:1px solid var(--border);color:var(--text2)}
body.theme-midnight .rt-mg-btn:hover, body.theme-ember .rt-mg-btn:hover {border-color:var(--accent);color:var(--accent)}
body.theme-midnight .rt-icon-opt:hover, body.theme-ember .rt-icon-opt:hover {background:var(--s2)}
body.theme-midnight .rt-icon-opt.selected, body.theme-ember .rt-icon-opt.selected {border-color:var(--accent);background:rgba(255,255,255,.06)}
body.theme-midnight .rt-day-opt, body.theme-ember .rt-day-opt {background:var(--s2);border:1px solid var(--border);color:var(--text2)}
body.theme-midnight .rt-day-opt.selected, body.theme-ember .rt-day-opt.selected {background:var(--accent);color:var(--sidebar);border-color:var(--accent)}
body.theme-midnight .rt-group.today-drag-over, body.theme-ember .rt-group.today-drag-over {border:2px dashed var(--accent);background:rgba(255,255,255,.03)}
body.theme-midnight .rt-manage-group.drag-over, body.theme-ember .rt-manage-group.drag-over {box-shadow:0 0 0 2px var(--accent);border-color:var(--accent)}

/* ── Routine colour group accents ─────────────── */
body.theme-midnight .rt-group.c-blue .rt-group-header, body.theme-ember .rt-group.c-blue .rt-group-header {border-left:4px solid var(--blue)}
body.theme-midnight .rt-group.c-green .rt-group-header, body.theme-ember .rt-group.c-green .rt-group-header {border-left:4px solid var(--green)}
body.theme-midnight .rt-group.c-purple .rt-group-header, body.theme-ember .rt-group.c-purple .rt-group-header {border-left:4px solid #9a80d4}
body.theme-midnight .rt-group.c-yellow .rt-group-header, body.theme-ember .rt-group.c-yellow .rt-group-header {border-left:4px solid var(--accent)}
body.theme-midnight .rt-group.c-red .rt-group-header, body.theme-ember .rt-group.c-red .rt-group-header {border-left:4px solid var(--red)}

/* ── Task & Action Notes ──────────────────────── */
body.theme-midnight .tan-item-priority.high, body.theme-ember .tan-item-priority.high {background:rgba(220,80,80,.18);color:#f08080}
body.theme-midnight .tan-item-priority.medium, body.theme-ember .tan-item-priority.medium {background:rgba(217,119,6,.18);color:#e8a840}
body.theme-midnight .tan-item-priority.low, body.theme-ember .tan-item-priority.low {background:rgba(5,150,105,.18);color:#50c880}
body.theme-midnight .tan-cat-badge.personal, body.theme-ember .tan-cat-badge.personal {background:rgba(59,91,219,.18);color:#80a0f0}
body.theme-midnight .tan-cat-badge.official, body.theme-ember .tan-cat-badge.official {background:rgba(107,33,168,.18);color:#c080e0}
body.theme-midnight .tan-item-text, body.theme-ember .tan-item-text {color:var(--text)}
body.theme-midnight .tan-item-text.done, body.theme-ember .tan-item-text.done {color:var(--muted)}
body.theme-midnight .tan-section-title, body.theme-ember .tan-section-title {color:var(--text2)}
body.theme-midnight .tan-section-count, body.theme-ember .tan-section-count {background:var(--border);color:var(--text2)}
body.theme-midnight .del-act:hover, body.theme-ember .del-act:hover {background:rgba(192,64,64,.15)!important;border-color:var(--red)!important}

/* ── Misc badges and chips ────────────────────── */
body.theme-midnight .pin-btn.pinned, body.theme-ember .pin-btn.pinned {background:rgba(232,168,74,.15);border-color:var(--accent);color:var(--accent)}
body.theme-midnight .pinned-badge, body.theme-ember .pinned-badge {background:rgba(232,168,74,.15);color:var(--accent)}
body.theme-midnight .schip.pending, body.theme-ember .schip.pending {background:rgba(232,168,74,.15);color:var(--accent)}
body.theme-midnight .dash-task-prio.medium, body.theme-ember .dash-task-prio.medium {background:rgba(192,144,48,.15);color:#c09030}

/* ── Finance tracker ──────────────────────────── */
body.theme-midnight .fin-status-badge.pending, body.theme-ember .fin-status-badge.pending {background:rgba(192,144,48,.15);color:#c09030}
body.theme-midnight .fin-due-badge.warn, body.theme-ember .fin-due-badge.warn {background:rgba(192,80,64,.15);color:var(--red)}
body.theme-midnight .fin-pay-badge.upi, body.theme-ember .fin-pay-badge.upi {background:rgba(232,168,74,.15);color:var(--accent)}

/* ── Sticky notes ─────────────────────────────── */
body.theme-midnight .ncard, body.theme-ember .ncard {background:var(--s2);border:1px solid var(--border)}
body.theme-midnight .ncard:hover, body.theme-ember .ncard:hover {background:var(--sidebar)}
body.theme-midnight .ncard.pinned-card, body.theme-ember .ncard.pinned-card {border-top:3px solid var(--accent)}

/* ── Shopping ─────────────────────────────── */
.shop-layout{display:flex;flex:1;min-height:0;overflow:hidden;height:calc(100vh - 58px)}
.shop-left{
  width:200px;flex-shrink:0;background:var(--s2);
  border-right:1px solid var(--border);
  display:flex;flex-direction:column;overflow:hidden
}
.shop-left-hdr{
  padding:14px 14px 10px;border-bottom:1px solid var(--border);
  display:flex;align-items:center;justify-content:space-between;flex-shrink:0
}
.shop-left-title{font-family:'Inter',sans-serif;font-size:14px;font-weight:700;color:var(--text)}
.shop-new-btn{background:none;border:none;color:var(--accent);font-size:18px;cursor:pointer;padding:0 2px;line-height:1;font-weight:700}
.shop-list{flex:1;min-height:0;overflow-y:auto;padding:6px 0;scrollbar-width:thin;scrollbar-color:var(--border) transparent}
.shop-list::-webkit-scrollbar{width:4px}
.shop-list::-webkit-scrollbar-thumb{background:var(--border);border-radius:4px}
.shop-folder-wrap{position:relative}
.shop-folder{
  display:flex;align-items:center;gap:8px;
  padding:10px 14px;cursor:pointer;
  transition:background 0.12s;font-size:14px;font-weight:600;color:var(--text2)
}
.shop-folder:hover{background:var(--sidebar)}
.shop-folder.active{background:rgba(139,94,42,.15);color:var(--accent)}
body.theme-beige .shop-folder.active{background:rgba(124,92,191,.12);color:var(--accent)}
body.theme-midnight .shop-folder.active{background:rgba(232,168,74,.08);color:var(--accent)}
body.theme-ember .shop-folder.active{background:rgba(212,114,74,.08);color:var(--accent)}
.shop-folder-icon{font-size:16px;width:20px;text-align:center;flex-shrink:0}
.shop-folder-name{flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.shop-folder-count{font-size:12px;background:var(--border);border-radius:10px;padding:1px 7px;color:var(--text2);font-weight:700;flex-shrink:0}
.shop-folder.active .shop-folder-count{background:rgba(139,94,42,.2);color:var(--accent)}
body.theme-beige .shop-folder.active .shop-folder-count{background:rgba(124,92,191,.15);color:var(--accent)}
body.theme-midnight .shop-folder.active .shop-folder-count{background:rgba(232,168,74,.12);color:var(--accent)}
body.theme-ember .shop-folder.active .shop-folder-count{background:rgba(212,114,74,.12);color:var(--accent)}
body.theme-rose .shop-folder.active .shop-folder-count{background:rgba(176,96,144,.12);color:var(--accent)}
body.theme-ocean .shop-folder.active .shop-folder-count{background:rgba(0,210,180,.12);color:var(--accent)}
body.theme-arctic .shop-folder.active .shop-folder-count{background:rgba(56,72,112,.12);color:var(--accent)}
.shop-folder-actions{display:none;gap:2px;margin-left:4px;flex-shrink:0}
.shop-folder-wrap:hover .shop-folder-actions{display:flex}
.shop-fa-btn{background:none;border:none;cursor:pointer;font-size:11px;padding:2px 4px;border-radius:4px;opacity:.6;transition:opacity .15s;line-height:1}
.shop-fa-btn:hover{opacity:1;background:var(--border)}
.shop-fa-btn.del:hover{background:rgba(192,64,64,.15)}
.shop-left-footer{padding:10px;border-top:1px solid var(--border);flex-shrink:0}
.shop-left-footer button{width:100%;background:var(--accent);color:#fff;border:none;border-radius:8px;padding:9px;font-size:13px;font-weight:600;cursor:pointer;font-family:'Inter',sans-serif;display:flex;align-items:center;justify-content:center;gap:6px}
.shop-left-footer button:hover{opacity:.85}

.shop-right{flex:1;display:flex;flex-direction:column;min-width:0;overflow:hidden;background:var(--bg)}
.shop-right-hdr{
  padding:14px 18px 12px;border-bottom:1px solid var(--border);
  display:flex;align-items:center;gap:10px;flex-shrink:0;background:var(--sidebar)
}
.shop-right-icon{font-size:22px}
.shop-right-name{font-size:16px;font-weight:700;color:var(--text)}
.shop-right-sub{font-size:12px;color:var(--muted);font-weight:500}
.shop-items-wrap{flex:1;overflow-y:auto;padding:0;scrollbar-width:thin;scrollbar-color:var(--border) transparent}
.shop-items-wrap::-webkit-scrollbar{width:4px}
.shop-items-wrap::-webkit-scrollbar-thumb{background:var(--border);border-radius:4px}
.shop-entry{display:flex;align-items:center;gap:10px;padding:11px 18px;border-bottom:1px solid rgba(200,180,138,.1);font-size:15px;transition:background .1s}
.shop-entry:hover{background:rgba(255,255,255,.2)}
body.theme-midnight .shop-entry:hover{background:rgba(255,255,255,.03)}
.shop-entry:last-child{border-bottom:none}
.shop-entry.bought .se-name{text-decoration:line-through;opacity:.4}
.se-check{width:20px;height:20px;border-radius:5px;border:1.5px solid var(--border2);flex-shrink:0;display:flex;align-items:center;justify-content:center;cursor:pointer;font-size:11px;color:transparent;transition:all .15s}
.se-check.done{background:var(--green);border-color:var(--green);color:#fff}
.se-name{flex:1;color:var(--text);font-weight:500;min-width:0}
.se-date{font-size:12px;color:var(--muted);min-width:55px;text-align:right}
.se-del{font-size:12px;color:var(--muted);cursor:pointer;opacity:0;transition:opacity .15s;padding:2px 5px;border-radius:4px}
.se-del:hover{color:var(--red)}
.shop-entry:hover .se-del{opacity:1}
.shop-add-bar{display:flex;align-items:center;gap:8px;padding:10px 18px;border-top:1px solid var(--border);flex-shrink:0;background:var(--sidebar)}
.shop-add-input{flex:1;background:var(--bg);border:1px solid var(--border);border-radius:8px;padding:8px 12px;font-size:14px;color:var(--text);font-family:'Inter',sans-serif;outline:none}
.shop-add-input::placeholder{color:var(--muted)}
.shop-add-input:focus{border-color:var(--accent)}
.shop-add-btn{background:var(--accent);color:#fff;border:none;border-radius:8px;padding:8px 16px;font-size:14px;font-weight:600;cursor:pointer;font-family:'Inter',sans-serif}
.shop-add-btn:hover{opacity:.85}
.shop-empty{display:flex;flex-direction:column;align-items:center;justify-content:center;padding:60px 20px;color:var(--muted);gap:8px;flex:1}
.shop-empty-icon{font-size:40px;opacity:.3}
.shop-empty-text{font-size:14px}
@media(max-width:640px){
  .shop-layout{flex-direction:column;height:auto!important}
  .shop-left{width:100%;border-right:none;border-bottom:1px solid var(--border);max-height:180px;overflow-y:auto}
  .shop-left-hdr{padding:10px 14px 8px}
  .shop-left-footer{padding:6px 10px}
  .shop-left-footer button{padding:7px;font-size:12px}
  .shop-right{min-height:50vh}
  .shop-right-hdr{padding:10px 14px}
  .shop-entry{padding:10px 14px}
  .shop-entry .se-del{opacity:1}
  .shop-add-bar{padding:8px 14px}
  .shop-add-input{font-size:14px;padding:10px 12px}
  #page-shopping{height:auto!important}
}

/* -- INVESTMENTS PAGE -------------------------------- */
.inv-wrap{display:flex;flex-direction:column;height:calc(100vh - 58px);overflow:hidden}
.inv-header{display:flex;align-items:center;justify-content:space-between;padding:20px 24px 14px;flex-shrink:0}
.inv-title{font-family:'Inter',sans-serif;font-size:20px;font-weight:800;color:var(--text);display:flex;align-items:center;gap:8px}
.inv-summary{display:flex;gap:14px;padding:0 24px 16px;flex-shrink:0;flex-wrap:wrap}
.inv-sum-card{
  flex:1;min-width:180px;border-radius:14px;
  padding:16px 20px;transition:all .25s;position:relative;overflow:hidden
}
.inv-sum-card::before{content:'';position:absolute;top:0;left:0;right:0;bottom:0;opacity:.08;border-radius:14px;z-index:0}
.inv-sum-card>*{position:relative;z-index:1}
.inv-sum-card:nth-child(1){background:linear-gradient(135deg,#1a6b4a12,#2ecc7118);border:1.5px solid #2ecc7140}
.inv-sum-card:nth-child(1) .inv-sum-val{color:#1a8a5a}
body.theme-midnight .inv-sum-card:nth-child(1) .inv-sum-val,body.theme-ember .inv-sum-card:nth-child(1) .inv-sum-val{color:#5adb8a}
.inv-sum-card:nth-child(2){background:linear-gradient(135deg,#2a5a9a12,#3b82f618);border:1.5px solid #3b82f640}
.inv-sum-card:nth-child(2) .inv-sum-val{color:#2a6aba}
body.theme-midnight .inv-sum-card:nth-child(2) .inv-sum-val,body.theme-ember .inv-sum-card:nth-child(2) .inv-sum-val{color:#60a5fa}
.inv-sum-card:nth-child(3){background:linear-gradient(135deg,#9a4a2a12,#e8884818);border:1.5px solid #e8884840}
.inv-sum-card:nth-child(3) .inv-sum-val{color:#c06030}
body.theme-midnight .inv-sum-card:nth-child(3) .inv-sum-val,body.theme-ember .inv-sum-card:nth-child(3) .inv-sum-val{color:#f0a060}
.inv-sum-card:hover{transform:translateY(-3px);box-shadow:0 8px 24px rgba(0,0,0,.1)}
.inv-sum-label{font-size:10px;text-transform:uppercase;letter-spacing:2px;color:var(--muted);font-weight:700;margin-bottom:6px}
.inv-sum-val{font-size:24px;font-weight:800;font-variant-numeric:tabular-nums;letter-spacing:-.5px}
.inv-sum-sub{font-size:11px;color:var(--text2);margin-top:3px;font-weight:500}
.inv-table-wrap{flex:1;overflow-y:auto;padding:0 24px 20px;scrollbar-width:thin;scrollbar-color:var(--border) transparent}
.inv-table-wrap::-webkit-scrollbar{width:4px}
.inv-table-wrap::-webkit-scrollbar-thumb{background:var(--border);border-radius:4px}
.inv-table{width:100%;border-collapse:separate;border-spacing:0;border-radius:12px;overflow:hidden;border:1.5px solid var(--border);box-shadow:0 2px 12px rgba(0,0,0,.04)}
.inv-table thead th{
  text-align:left;padding:13px 14px;font-size:11px;font-weight:800;
  text-transform:uppercase;letter-spacing:1.8px;
  color:#fff;
  background:linear-gradient(135deg,#c0392b,#e74c3c);
  border-bottom:2px solid #a93226;
  position:sticky;top:0;z-index:2
}
body.theme-midnight .inv-table thead th{background:linear-gradient(135deg,#1a3050,#2a4070);border-bottom-color:#1a2a40;color:#c8d8e8}
body.theme-ember .inv-table thead th{background:linear-gradient(135deg,#3a1a10,#5a2a18);border-bottom-color:#2a1a10;color:#c8b090}
body.theme-beige .inv-table thead th{background:linear-gradient(135deg,#7c5cbf,#9b7de0);border-bottom-color:#6a4aaa;color:#fff}
.inv-table thead th.r{text-align:right}
.inv-table tbody{background:var(--sidebar)}
.inv-table tbody td{padding:12px 14px;font-size:14px;color:var(--text);border-bottom:1px solid rgba(200,180,138,.18);vertical-align:middle}
.inv-table tbody td.r{text-align:right}
.inv-table tbody tr{transition:all .15s}
.inv-table tbody tr:hover{background:rgba(0,0,0,.03)}
body.theme-midnight .inv-table tbody tr:hover{background:rgba(255,255,255,.04)}
body.theme-ember .inv-table tbody tr:hover{background:rgba(255,255,255,.03)}
.inv-table tbody tr:last-child td{border-bottom:none}
.inv-asset-name{font-weight:700;font-size:14px;color:var(--text);letter-spacing:-.1px}
.inv-val{font-family:'Courier New',monospace;font-weight:700;font-size:14px;color:#1a6b4a;font-variant-numeric:tabular-nums}
body.theme-midnight .inv-val{color:#5adb8a}
body.theme-ember .inv-val{color:#7aaa60}
.inv-pct{font-family:'Courier New',monospace;font-weight:600;color:var(--text);font-size:13px}
.inv-bar-wrap{width:100%;height:14px;background:rgba(0,0,0,.06);border-radius:8px;overflow:hidden;min-width:80px;position:relative}
body.theme-midnight .inv-bar-wrap,body.theme-ember .inv-bar-wrap{background:rgba(255,255,255,.06)}
.inv-bar-fill{height:100%;border-radius:8px;transition:width .5s cubic-bezier(.4,0,.2,1);position:relative;min-width:2px}
.inv-bar-fill::after{content:'';position:absolute;top:0;left:0;right:0;bottom:0;background:linear-gradient(180deg,rgba(255,255,255,.3) 0%,transparent 60%);border-radius:8px}
.inv-status{display:inline-flex;align-items:center;gap:5px;font-size:13px;font-weight:700;padding:4px 12px;border-radius:20px;white-space:nowrap}
.inv-status.on-target{color:#1a8a5a;background:rgba(26,138,90,.12)}
body.theme-midnight .inv-status.on-target{color:#5adb8a;background:rgba(90,219,138,.1)}
body.theme-ember .inv-status.on-target{color:#7aaa60;background:rgba(122,170,96,.1)}
.inv-status.over{color:#d97706;background:rgba(217,119,6,.12)}
body.theme-midnight .inv-status.over,body.theme-ember .inv-status.over{color:#fbbf24;background:rgba(251,191,36,.1)}
.inv-status.under{color:#dc2626;background:rgba(220,38,38,.1)}
body.theme-midnight .inv-status.under,body.theme-ember .inv-status.under{color:#f87171;background:rgba(248,113,113,.1)}
.inv-gap{font-family:'Courier New',monospace;font-weight:800;font-size:13px}
.inv-gap.pos{color:#d97706}
.inv-gap.neg{color:#dc2626}
.inv-gap.zero{color:#1a8a5a}
body.theme-midnight .inv-gap.pos,body.theme-ember .inv-gap.pos{color:#fbbf24}
body.theme-midnight .inv-gap.neg,body.theme-ember .inv-gap.neg{color:#f87171}
body.theme-midnight .inv-gap.zero,body.theme-ember .inv-gap.zero{color:#5adb8a}
.inv-actions{display:flex;gap:4px;justify-content:flex-end;opacity:0;transition:opacity .15s}
.inv-table tbody tr:hover .inv-actions{opacity:1}
.inv-abtn{background:none;border:none;cursor:pointer;font-size:14px;padding:5px 7px;border-radius:7px;color:var(--muted);transition:all .12s;line-height:1}
.inv-abtn:hover{color:var(--text);background:var(--s2)}
.inv-abtn.del:hover{color:#dc2626;background:rgba(220,38,38,.1)}
.inv-edit-input{background:var(--bg);border:1.5px solid var(--border);border-radius:8px;padding:7px 10px;font-size:13px;color:var(--text);font-family:'Inter',sans-serif;outline:none;width:100%;transition:all .2s}
.inv-edit-input:focus{border-color:var(--accent);box-shadow:0 0 0 3px rgba(139,94,42,.12)}
.inv-edit-input.num{text-align:right;width:110px;font-family:'Courier New',monospace;font-weight:600}
.inv-tfoot td{
  padding:14px 14px!important;font-weight:800;font-size:15px;
  background:linear-gradient(135deg,#1a8a5a,#2ecc71);color:#fff!important;
  border-top:none
}
body.theme-midnight .inv-tfoot td{background:linear-gradient(135deg,#1a3a2a,#2a5a3a);color:#c8e8d8!important}
body.theme-ember .inv-tfoot td{background:linear-gradient(135deg,#1a2a18,#2a4020);color:#a8c890!important}
body.theme-beige .inv-tfoot td{background:linear-gradient(135deg,#5a4a9a,#7c5cbf);color:#fff!important}
.inv-tfoot .inv-val{font-size:16px;color:#fff!important;font-weight:800}
body.theme-midnight .inv-tfoot .inv-val{color:#a0f0c0!important}
body.theme-ember .inv-tfoot .inv-val{color:#a8c890!important}
body.theme-beige .inv-tfoot .inv-val{color:#e8e0f8!important}
.inv-tfoot .inv-pct{color:rgba(255,255,255,.85)!important;font-weight:700}
.inv-rebalance{color:#fef3c7;font-size:12px;font-weight:700;display:flex;align-items:center;gap:5px;text-shadow:0 1px 2px rgba(0,0,0,.15)}
body.theme-midnight .inv-rebalance{color:#fbbf24}
.inv-empty{display:flex;flex-direction:column;align-items:center;justify-content:center;padding:60px 20px;color:var(--muted);gap:10px;flex:1}
.inv-empty-icon{font-size:48px;opacity:.4}
.inv-empty-text{font-size:15px}
/* Alternating row tint */
.inv-table tbody tr:nth-child(even){background:rgba(0,0,0,.015)}
body.theme-midnight .inv-table tbody tr:nth-child(even){background:rgba(255,255,255,.015)}
body.theme-ember .inv-table tbody tr:nth-child(even){background:rgba(255,255,255,.012)}
/* New theme investment overrides */
body.theme-rose .inv-table thead th{background:linear-gradient(135deg,#b06090,#c87898);border-bottom-color:#984878;color:#fff}
body.theme-rose .inv-tfoot td{background:linear-gradient(135deg,#b06090,#c87898);color:#fff!important}
body.theme-rose .inv-tfoot .inv-val{color:#fff!important}
body.theme-ocean .inv-sum-card:nth-child(1) .inv-sum-val{color:#00dc8c}
body.theme-ocean .inv-sum-card:nth-child(2) .inv-sum-val{color:#64b4ff}
body.theme-ocean .inv-sum-card:nth-child(3) .inv-sum-val{color:#ffb450}
body.theme-ocean .inv-table thead th{background:linear-gradient(135deg,#0a2830,#154050);border-bottom-color:#0a1a20;color:#b8e0d8}
body.theme-ocean .inv-table tbody tr:hover{background:rgba(0,210,180,.03)}
body.theme-ocean .inv-table tbody tr:nth-child(even){background:rgba(255,255,255,.012)}
body.theme-ocean .inv-val{color:#00dc8c}
body.theme-ocean .inv-bar-wrap{background:rgba(255,255,255,.05)}
body.theme-ocean .inv-status.on-target{color:#00dc8c;background:rgba(0,220,140,.1)}
body.theme-ocean .inv-status.over{color:#ffb450;background:rgba(255,180,80,.1)}
body.theme-ocean .inv-status.under{color:#ff6868;background:rgba(255,104,104,.1)}
body.theme-ocean .inv-gap.pos{color:#ffb450}
body.theme-ocean .inv-gap.neg{color:#ff6868}
body.theme-ocean .inv-gap.zero{color:#00dc8c}
body.theme-ocean .inv-tfoot td{background:linear-gradient(135deg,#0a2a20,#154838);color:#b8e8d8!important}
body.theme-ocean .inv-tfoot .inv-val{color:#70ffc8!important}
body.theme-ocean .inv-rebalance{color:#ffb450}
body.theme-arctic .inv-table thead th{background:linear-gradient(135deg,#4a5a80,#5a6a90);border-bottom-color:#3a4a6a;color:#fff}
body.theme-arctic .inv-tfoot td{background:linear-gradient(135deg,#4a5a80,#5a6a90);color:#fff!important}
body.theme-arctic .inv-tfoot .inv-val{color:#e0e8ff!important}
@media(max-width:860px){
  .inv-summary{gap:10px}
  .inv-sum-card{min-width:130px;padding:12px 14px}
  .inv-sum-val{font-size:20px}
  .inv-table thead th{padding:10px 10px;font-size:9px;letter-spacing:1px}
  .inv-table tbody td{padding:9px 10px;font-size:12px}
  .inv-table .inv-col-alloc,
  .inv-table .inv-col-status{display:none}
  .inv-asset-name{font-size:12px}
  .inv-val{font-size:12px}
  .inv-bar-wrap{height:10px}
}
@media(max-width:640px){
  .inv-wrap{height:auto;min-height:calc(100vh - 58px)}
  .inv-header{padding:14px 14px 10px;flex-wrap:wrap;gap:8px}
  .inv-title{font-size:16px}
  .inv-header .btn{font-size:12px;padding:7px 12px}
  .inv-summary{padding:0 14px 12px;gap:8px}
  .inv-sum-card{min-width:calc(50% - 4px);flex:0 0 calc(50% - 4px);padding:10px 12px}
  .inv-sum-card:nth-child(3){flex:0 0 100%}
  .inv-sum-val{font-size:18px}
  .inv-sum-label{font-size:8px;letter-spacing:1.5px}
  .inv-sum-sub{font-size:10px}
  /* Hide full table on mobile */
  .inv-table-wrap .inv-table{display:none}
  /* Show card view on mobile */
  .inv-cards-mobile{display:flex!important}
  .inv-actions{opacity:1}
}
/* Card layout for mobile — hidden on desktop */
.inv-cards-mobile{
  display:none;flex-direction:column;gap:10px;padding:0 0 16px
}
.inv-mcard{
  background:var(--sidebar);border:1px solid var(--border);border-radius:12px;
  padding:14px 16px;position:relative;overflow:hidden;transition:all .15s
}
.inv-mcard::before{
  content:'';position:absolute;left:0;top:0;bottom:0;width:4px;border-radius:4px 0 0 4px
}
.inv-mcard-top{display:flex;align-items:center;justify-content:space-between;margin-bottom:10px}
.inv-mcard-name{font-weight:700;font-size:15px;color:var(--text);flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.inv-mcard-actions{display:flex;gap:2px;flex-shrink:0}
.inv-mcard-actions .inv-abtn{opacity:1;font-size:15px}
.inv-mcard-grid{display:grid;grid-template-columns:1fr 1fr;gap:8px 16px}
.inv-mcard-item{display:flex;flex-direction:column;gap:1px}
.inv-mcard-lbl{font-size:9px;text-transform:uppercase;letter-spacing:1.5px;color:var(--muted);font-weight:700}
.inv-mcard-v{font-size:14px;font-weight:600;color:var(--text)}
.inv-mcard-v.money{font-family:'Courier New',monospace;color:#1a6b4a;font-weight:700}
body.theme-midnight .inv-mcard-v.money{color:#5adb8a}
body.theme-ember .inv-mcard-v.money{color:#7aaa60}
.inv-mcard-bar{margin-top:10px}
.inv-mcard-bar-wrap{width:100%;height:8px;background:rgba(0,0,0,.06);border-radius:6px;overflow:hidden}
body.theme-midnight .inv-mcard-bar-wrap,body.theme-ember .inv-mcard-bar-wrap{background:rgba(255,255,255,.06)}
.inv-mcard-bar-fill{height:100%;border-radius:6px;position:relative}
.inv-mcard-bar-fill::after{content:'';position:absolute;inset:0;background:linear-gradient(180deg,rgba(255,255,255,.3) 0%,transparent 60%);border-radius:6px}
/* Mobile total card */
.inv-mcard-total{
  background:linear-gradient(135deg,#1a8a5a,#2ecc71);border:none;
  padding:16px 18px;border-radius:12px;color:#fff
}
body.theme-midnight .inv-mcard-total{background:linear-gradient(135deg,#1a3a2a,#2a5a3a)}
body.theme-ember .inv-mcard-total{background:linear-gradient(135deg,#1a2a18,#2a4020)}
body.theme-beige .inv-mcard-total{background:linear-gradient(135deg,#5a4a9a,#7c5cbf)}
.inv-mcard-total .inv-mcard-name{color:#fff;font-size:14px;text-transform:uppercase;letter-spacing:1px}
.inv-mcard-total .inv-mcard-lbl{color:rgba(255,255,255,.65)}
.inv-mcard-total .inv-mcard-v{color:#fff}
.inv-mcard-total .inv-mcard-v.money{color:#fff}
.inv-mcard-total .inv-mcard-rebal{color:#fef3c7;font-size:11px;font-weight:700;margin-top:8px;font-style:italic}
/* Mobile edit form */
.inv-medit{background:var(--s2);border:1.5px solid var(--accent);border-radius:12px;padding:14px 16px}
.inv-medit-fields{display:flex;flex-direction:column;gap:8px;margin-bottom:10px}
.inv-medit-fields label{font-size:10px;text-transform:uppercase;letter-spacing:1px;color:var(--muted);font-weight:700;margin-bottom:2px}
.inv-medit-fields .inv-edit-input{width:100%;font-size:14px;padding:9px 12px}
.inv-medit-fields .inv-edit-input.num{width:100%;text-align:left}
.inv-medit-btns{display:flex;gap:8px;justify-content:flex-end}
.inv-medit-btns button{padding:8px 16px;border-radius:8px;font-size:13px;font-weight:600;cursor:pointer;font-family:'Inter',sans-serif;border:none}
.inv-medit-save{background:var(--green);color:#fff}
.inv-medit-cancel{background:var(--s2);color:var(--text);border:1px solid var(--border)!important}

/* -- INVESTMENT PIE CHART ---- */
.inv-chart-section{
  margin-top:24px;padding:0 0 28px;
  border-top:1.5px solid var(--border)
}
.inv-chart-heading{
  display:flex;align-items:center;gap:14px;
  padding:24px 0 18px
}
.inv-chart-icon{font-size:28px;line-height:1}
.inv-chart-title{
  font-family:'Inter',sans-serif;font-size:18px;font-weight:800;
  color:var(--text);letter-spacing:-.3px
}
.inv-chart-sub{font-size:12px;color:var(--muted);font-weight:500;margin-top:1px}
.inv-chart-wrap{
  position:relative;
  max-width:520px;
  margin:0 auto;
  padding:8px
}
@media(max-width:640px){
  .inv-chart-heading{padding:18px 0 14px;gap:10px}
  .inv-chart-icon{font-size:22px}
  .inv-chart-title{font-size:15px}
  .inv-chart-sub{font-size:11px}
  .inv-chart-wrap{max-width:340px;padding:4px}
  .inv-chart-section{margin-top:16px;padding-bottom:20px}
}

</style>
</head>
<body class="theme-rose">
<!-- mobile sidebar overlay -->
<div class="sidebar-overlay" id="sidebar-overlay" onclick="closeSidebar()"></div>
<div class="layout">

<!-- -- SIDEBAR ----------------------------------- -->
<aside>
  <div class="sidebar-logo">📓 MyNotes</div>

  <div class="sidebar-section">Home</div>
  <button class="nav-item active" id="nav-dashboard" onclick="showPage('dashboard',this)" title="Show everything">
    <span class="nav-icon">🏠</span> Dashboard
    <span class="nav-count" id="nav-all">0</span>
  </button>

  <div class="sidebar-section">Capture</div>
  <button class="nav-item" id="nav-notes-btn" onclick="showPage('notes',this)">
    <span class="nav-icon">📝</span> Notes
    <span class="nav-count" id="nav-notes">0</span>
  </button>
  <button class="nav-item" id="nav-sticky-btn" onclick="showPage('sticky',this)">
    <span class="nav-icon">📌</span> Sticky Notes
    <span class="nav-count" id="nav-sticky-count">0</span>
  </button>
  <button class="nav-item" id="nav-daybook-btn" onclick="showPage('daybook',this)">
    <span class="nav-icon">📖</span> Daybook
    <span class="nav-count" id="nav-daybook-count">0</span>
  </button>

  <div class="sidebar-section">Execution</div>
  <button class="nav-item" id="nav-reminders-btn" onclick="showPage('reminders',this)">
    <span class="nav-icon">⏰</span> Reminders
    <span class="nav-count" id="nav-reminders">0</span>
  </button>
  <button class="nav-item" id="nav-routine-btn" onclick="showPage('routine',this)">
    <span class="nav-icon">🔁</span> Routine
    <span class="nav-count" id="nav-routine-count">0</span>
  </button>
  <button class="nav-item" id="nav-tasknotes-btn" onclick="showPage('tasknotes',this)">
    <span class="nav-icon">✍️</span> Task Notes
    <span class="nav-count" id="nav-tasknotes-count">0</span>
  </button>
  <button class="nav-item" id="nav-impdates-btn" onclick="showPage('impdates',this)">
    <span class="nav-icon">🗓️</span> Important Dates
    <span class="nav-count" id="nav-impdates-count">0</span>
  </button>

  <div class="sidebar-section">Tracking</div>
  <button class="nav-item" id="nav-journal-btn" onclick="showPage('journal',this)">
    <span class="nav-icon">📈</span> Trading Journal
    <span class="nav-count" id="nav-journal-count">0</span>
  </button>
  <button class="nav-item" id="nav-finance-btn" onclick="showPage('finance',this)">
    <span class="nav-icon">💰</span> Finance Tracker
    <span class="nav-count" id="nav-finance-count">0</span>
  </button>
  <button class="nav-item" id="nav-shopping-btn" onclick="showPage('shopping',this)">
    <span class="nav-icon">🛒</span> Shopping
    <span class="nav-count" id="nav-shopping-count">0</span>
  </button>
  <button class="nav-item" id="nav-investments-btn" onclick="showPage('investments',this)">
    <span class="nav-icon">📊</span> Investments
    <span class="nav-count" id="nav-investments-count">0</span>
  </button>

  <div class="sidebar-section">Status</div>
  <button class="nav-item nav-status-pending" onclick="showPage('reminders',this);selectRemFilter('all')">
    <span class="nav-icon">🔔</span> Pending
    <span class="nav-count" id="nav-pending">0</span>
  </button>
  <button class="nav-item nav-status-overdue" onclick="showPage('reminders',this);selectRemFilter('overdue-only')">
    <span class="nav-icon">🔴</span> Overdue
    <span class="nav-count" id="nav-overdue">0</span>
  </button>
  <button class="nav-item nav-status-completed" onclick="showPage('reminders',this);selectRemFilter('completed')">
    <span class="nav-icon">✅</span> Completed
    <span class="nav-count" id="nav-sent">0</span>
  </button>

  <div class="sidebar-footer">
    <div class="sync-pill">
      <div class="sdot" id="sdot"></div>
      <span id="stext">Ready</span>
      <span id="sync-time" style="margin-left:auto;font-size:10px;color:var(--muted);font-variant-numeric:tabular-nums"></span>
    </div>
    <button class="btn-ghost" onclick="openSettings()" style="width:100%;margin-top:8px;justify-content:center;display:flex">
      ⚙️ Settings
    </button>
  </div>
</aside>

<!-- -- MAIN --------------------------------------- -->
<div class="main">
  <div class="topbar">
    <div class="topbar-left">
      <button class="hamburger" onclick="openSidebar()" title="Menu">☰</button>
      <div class="page-title" id="page-title">📋 Dashboard</div>
    </div>
    <div style="display:flex;align-items:stretch">
      <!-- 5. Context-aware actions -->
      <div class="topbar-ctx" id="topbar-ctx">
        <!-- dashboard: search + add -->
        <div id="ctx-dashboard" style="display:flex;align-items:center;gap:8px">
          <div class="topbar-right" id="topbar-search-wrap">
            <div class="search-wrap" id="topbar-search">
              <span class="s-icon">🔍</span>
              <input type="text" placeholder="Search..." oninput="searchCards(this.value)">
            </div>
            <button class="btn" id="topbar-add-btn" onclick="openModal()">+ Add New</button>
          </div>
        </div>
        <!-- sticky: new + color hint -->
        <div id="ctx-sticky" style="display:none;align-items:center;gap:8px">
          <button class="btn" onclick="addSticky()">+ New Sticky</button>
        </div>
        <!-- journal: new trade -->
        <div id="ctx-journal" style="display:none;align-items:center;gap:8px">
          <button class="btn" onclick="openTradeModal()">+ New Trade</button>
        </div>
        <!-- routine: new routine -->
        <div id="ctx-routine" style="display:none;align-items:center;gap:8px">
          <button class="btn" onclick="openRoutineGroupModal()">+ New Routine</button>
        </div>
        <!-- tasknotes: add note -->
        <div id="ctx-tasknotes" style="display:none;align-items:center;gap:8px">
          <button class="btn" onclick="document.getElementById('tan-quick-input').focus()">+ Quick Note</button>
        </div>
        <!-- impdates: add important date -->
        <div id="ctx-impdates" style="display:none;align-items:center;gap:8px">
          <button class="btn" onclick="impOpenModal()">+ Add Date</button>
        </div>
        <!-- finance: new entry -->
        <div id="ctx-finance" style="display:none;align-items:center;gap:8px">
          <button class="btn" onclick="openFinModal()">+ New Entry</button>
        </div>
        <!-- daybook: new entry -->
        <div id="ctx-daybook" style="display:none;align-items:center;gap:8px">
          <button class="btn" onclick="dbOpenCompose()">+ New Entry</button>
        </div>
        <!-- shopping: new shop -->
        <div id="ctx-shopping" style="display:none;align-items:center;gap:8px">
          <button class="btn" onclick="shopOpenModal()">+ Add Shop</button>
        </div>
        <!-- investments: add asset -->
        <div id="ctx-investments" style="display:none;align-items:center;gap:8px">
          <button class="btn" onclick="invOpenAddRow()">+ Add Asset</button>
        </div>
      </div>
      <!-- Clock -->
      <div class="clock-bar">
        <div class="clock-block">
          <div class="clock-zone"><span class="clock-zone-flag">🇮🇳</span> IST</div>
          <div class="clock-time" id="clk-ist-time">--:--:--</div>
          <div class="clock-date" id="clk-ist-date">--</div>
        </div>
        <div class="clock-block">
          <div class="clock-zone"><span class="clock-zone-flag">🇺🇸</span> CST</div>
          <div class="clock-time" id="clk-cst-time">--:--:--</div>
          <div class="clock-date" id="clk-cst-date">--</div>
        </div>
        <div class="clock-block">
          <div class="clock-zone"><span class="clock-zone-flag">🇸🇬</span> SGT</div>
          <div class="clock-time" id="clk-sgt-time">--:--:--</div>
          <div class="clock-date" id="clk-sgt-date">--</div>
        </div>
        <div class="topbar-sync" id="topbar-sync-pill">
          <div class="topbar-sync-dot" id="topbar-sdot"></div>
          <span id="topbar-stext">Synced</span>
        </div>
        <div class="topbar-avatar" id="topbar-avatar" onclick="openSettings()" title="Profile">
          <span id="topbar-initials">--</span>
        </div>
      </div>
    </div>
  </div>

  <!-- == PAGE SCROLL AREA (mobile scrolls here) == -->
  <div id="page-scroll-area">

  <!-- == DASHBOARD PAGE == -->
  <div id="page-dashboard">
    <div id="notif-prompt" class="notif-prompt" style="display:none;margin:12px 28px 0">
      🔔 <span>Enable browser notifications to get reminder alerts even when the tab is in background.</span>
      <button onclick="requestNotifPermission()">Enable</button>
      <button onclick="document.getElementById('notif-prompt').style.display='none'" style="background:transparent;color:var(--muted);border:1px solid var(--border);margin-left:4px">Dismiss</button>
    </div>
    <div class="dash-wrap">
      <!-- Greeting banner -->
      <div class="dash-greeting" id="dash-greeting">
        <div class="dash-greeting-left">
          <div class="dash-greeting-name" id="dash-greet-text">Good morning 👋</div>
          <div class="dash-greeting-date" id="dash-greet-date">—</div>
        </div>
        <div class="dash-greeting-right" id="dash-greet-emoji">🌅</div>
      </div>
      <!-- Quick Capture -->
      <div class="quick-capture" id="quick-capture">
        <span class="qc-icon">💡</span>
        <input type="text" id="qc-input" class="qc-input" placeholder="Add a thought, note, or reminder..." autocomplete="off">
        <select id="qc-type" class="qc-type">
          <option value="note">Note</option>
          <option value="reminder">Reminder</option>
          <option value="task">Task</option>
          <option value="sticky">Sticky</option>
          <option value="daybook">Daybook</option>
        </select>
        <button class="qc-btn" onclick="quickCapture()">Add</button>
      </div>
      <!-- Stat cards -->
      <div class="stats-row">
        <div class="stat-card sc-total" id="sc-notes" onclick="statFilter('note',this)">
          <div class="stat-icon">📋</div>
          <div class="stat-num" id="stat-notes">0</div>
          <div class="stat-label">Total Items</div>
          <div class="stat-sub">Notes + Reminders</div>
        </div>
        <div class="stat-card sc-pending" id="sc-pending" onclick="statFilter('pending',this)">
          <div class="stat-icon">⏳</div>
          <div class="stat-num" id="stat-pending">0</div>
          <div class="stat-label">Pending</div>
          <div class="stat-sub" id="stat-pending-sub">Due soon</div>
        </div>
        <div class="stat-card sc-completed" id="sc-all" onclick="statFilter('sent',this)">
          <div class="stat-icon">✅</div>
          <div class="stat-num" id="stat-files">0</div>
          <div class="stat-label">Completed</div>
          <div class="stat-sub" id="stat-completed-sub">Today</div>
        </div>
        <div class="stat-card sc-missed" id="sc-reminders" onclick="statFilter('reminder',this)">
          <div class="stat-icon">🚨</div>
          <div class="stat-num" id="stat-reminders">0</div>
          <div class="stat-label">Missed</div>
          <div class="stat-sub">Need attention</div>
        </div>
      </div>
      <!-- Progress Ring -->
      <div class="dash-progress">
        <div class="dash-progress-ring-wrap">
          <svg width="72" height="72" viewBox="0 0 72 72">
            <defs>
              <linearGradient id="prog-grad" x1="0%" y1="0%" x2="100%" y2="100%">
                <stop offset="0%"   stop-color="#3b82f6"/>
                <stop offset="100%" stop-color="#06b6d4"/>
              </linearGradient>
            </defs>
            <circle cx="36" cy="36" r="28" fill="none" stroke="var(--s2)" stroke-width="8"/>
            <circle id="dash-prog-ring" cx="36" cy="36" r="28" fill="none"
              stroke="url(#prog-grad)" stroke-width="8" stroke-linecap="round"
              stroke-dasharray="175.9" stroke-dashoffset="175.9"
              transform="rotate(-90 36 36)" style="transition:stroke-dashoffset 0.7s ease"/>
            <text x="36" y="40" text-anchor="middle" font-size="13" font-weight="800"
              fill="var(--accent)" font-family="Inter,sans-serif" id="dash-prog-pct-ring">0%</text>
          </svg>
        </div>
        <div class="dash-progress-header">
          <span class="dash-progress-title">Today's Progress</span>
          <span class="dash-progress-val" id="dash-prog-label">0 of 0 done</span>
        </div>
        <div class="dash-progress-track">
          <div class="dash-progress-fill" id="dash-prog-fill" style="width:0%"></div>
        </div>
      </div>
      <!-- Upcoming reminders calendar widget -->
      <div class="dash-cal-widget">
        <div class="dash-cal-left">
          <div class="dash-cal-header">
            <button class="dash-cal-nav" onclick="dashCalNav(-1)">‹</button>
            <span class="dash-cal-month" id="dash-cal-month-label"></span>
            <button class="dash-cal-nav" onclick="dashCalNav(1)">›</button>
          </div>
          <div class="dash-cal-grid" id="dash-cal-grid"></div>
        </div>
        <div class="dash-cal-right">
          <div class="dash-upcoming-title">📅 Upcoming Reminders</div>
          <div class="dash-upcoming-items" id="dash-upcoming-list"><div class="dash-empty">No upcoming reminders.</div></div>
        </div>
      </div>
      <!-- Bottom three widgets -->
      <div class="dash-bottom">
        <div class="dash-widget">
          <div class="dash-widget-title"><span class="dwt-dot dwt-green"></span>Next Routine</div>
          <div class="routine-items" id="dash-routine-list"><div class="dash-empty">No routines set up yet.</div></div>
        </div>
        <div class="dash-widget" id="dash-missed-widget">
          <div class="dash-widget-title" onclick="toggleMissedWidget()" style="cursor:pointer;user-select:none"><span class="dwt-dot dwt-red"></span>Missed &amp; Overdue <span id="missed-toggle" style="margin-left:auto;font-size:10px;color:var(--muted);transition:transform .2s">▼</span></div>
          <div class="missed-items" id="dash-missed-list"><div class="dash-empty">✨ All clear — you're on top of everything!</div></div>
        </div>
        <div class="dash-widget" id="dash-impdates-widget">
          <div class="dash-widget-title" style="cursor:pointer;user-select:none" onclick="showPage('impdates',document.getElementById('nav-impdates-btn'))"><span class="dwt-dot dwt-blue"></span>Important Dates <span style="margin-left:auto;font-size:10px;color:var(--muted)">View all →</span></div>
          <div class="imp-items" id="dash-impdates-list"><div class="dash-empty">No important dates added yet.</div></div>
        </div>
      </div>
      <!-- Tasks widget -->
      <div class="dash-tasks-widget">
        <div class="dash-tasks-hdr">
          <span class="dash-widget-title" style="margin-bottom:0"><span class="dwt-dot" style="background:#7c5cbf"></span>Tasks</span>
          <div style="display:flex;gap:6px">
            <span class="dash-tasks-count open" id="dash-tasks-open-count">0 open</span>
            <span class="dash-tasks-count done" id="dash-tasks-done-count">0 done</span>
            <button class="dash-tasks-goto" onclick="showPage('tasknotes',document.getElementById('nav-tasknotes-btn'))">View all →</button>
          </div>
        </div>
        <div id="dash-tasks-open" style="margin-top:8px"></div>
        <div id="dash-tasks-done-wrap" style="margin-top:4px">
          <div class="dash-tasks-section-label" onclick="dashToggleCompletedTasks()" id="dash-tasks-done-toggle" style="cursor:pointer">▶ Completed</div>
          <div id="dash-tasks-done" style="display:none"></div>
        </div>
      </div>
    </div>
  </div>

  <!-- == NOTES PAGE (3-panel, mirrors Reminders layout) == -->
  <div id="page-notes" style="display:none;flex-direction:column;height:calc(100vh - 58px)">
    <div class="notes-page-wrap" style="width:100%">

      <!-- Slide columns wrapper — .show-list and .show-editor classes drive mobile navigation -->
      <div class="notes-columns">

        <!-- Panel 1: Folders -->
        <div class="notes-folders-panel">
          <div class="notes-folders-hdr">
            <span class="notes-folders-title">📁 Folders</span>
            <div style="display:flex;gap:4px">
              <button class="notes-new-folder-btn" onclick="resetNoteFolders()" title="Fix folders if corrupted" style="font-size:11px;color:var(--muted)">↺</button>
              <button class="notes-new-folder-btn" onclick="createNewFolder()" title="New Folder">＋</button>
            </div>
          </div>
          <div class="notes-folder-list" id="notes-folder-list"></div>
        </div>

        <!-- Panel 2: Notes list -->
        <div class="notes-list-panel">
          <!-- Back to Folders (mobile only) -->
          <div class="notes-mobile-back" onclick="notesMobileBack('folders')">
            <button class="notes-mobile-back-btn">‹ Folders</button>
          </div>
          <div class="notes-list-hdr">
            <div class="notes-list-hdr-top">
              <span class="notes-list-hdr-title" id="notes-list-folder-name">All Notes</span>
              <button class="notes-new-btn" onclick="createNewNote()" title="New Note">＋ New</button>
            </div>
            <span class="notes-list-hdr-count" id="notes-panel-count">0 notes</span>
            <input class="notes-list-search" id="notes-panel-search" placeholder="Search…" oninput="renderNotesList()">
          </div>
          <div class="notes-list-items" id="notes-panel-list">
            <div class="notes-list-empty">No notes yet</div>
          </div>
        </div>

        <!-- Panel 3: Editor -->
        <div class="notes-editor-panel" id="notes-editor-panel">
          <div class="notes-editor-empty" id="notes-editor-empty">
            <div class="notes-editor-empty-icon">📝</div>
            <div class="notes-editor-empty-text">Select or create a note</div>
          </div>
          <div id="notes-editor-inner" style="display:none;flex:1;flex-direction:column;overflow:hidden">
            <!-- Back to Notes list (mobile only) -->
            <div class="notes-mobile-back" onclick="notesMobileBack('list')">
              <button class="notes-mobile-back-btn">‹ Notes</button>
            </div>
            <div class="notes-editor-topbar">
              <span class="notes-editor-meta" id="notes-editor-meta"></span>
              <div class="notes-editor-actions">
                <div class="notes-preview-toggle" title="Switch between editing markdown and seeing the rendered result">
                  <button id="btn-edit-mode" class="active" onclick="setNoteViewMode('edit')">✏️ Edit</button>
                  <button id="btn-preview-mode" onclick="setNoteViewMode('preview')">👁 Preview</button>
                </div>
                <span class="notes-editor-save-indicator" id="notes-editor-saved">✓ Saved</span>
                <button class="cbtn del" onclick="deleteCurrentNote()">🗑 Delete</button>
              </div>
            </div>
            <div class="notes-editor-content">
              <!-- Template picker -->
              <div style="display:flex;align-items:center;margin-bottom:8px;position:relative" id="tmpl-row">
                <button class="tmpl-btn" onclick="toggleTemplateDropdown(event)">📋 Templates ▾</button>
                <div class="tmpl-dropdown" id="tmpl-dropdown">
                  <div class="tmpl-item" onclick="applyTemplate('daily')"><span class="tmpl-item-icon">📅</span>Daily Log</div>
                  <div class="tmpl-item" onclick="applyTemplate('meeting')"><span class="tmpl-item-icon">👥</span>Meeting Notes</div>
                  <div class="tmpl-item" onclick="applyTemplate('trading')"><span class="tmpl-item-icon">📈</span>Trading Plan</div>
                  <div class="tmpl-item" onclick="applyTemplate('todo')"><span class="tmpl-item-icon">✅</span>To-Do List</div>
                </div>
              </div>
              <textarea class="notes-editor-title-input" id="notes-editor-title" rows="1" placeholder="Note title…" oninput="onNoteEditorInput()" onkeydown="noteTitleKeydown(event)"></textarea>
              <!-- Markdown toolbar -->
              <div class="md-toolbar" id="md-toolbar">
                <span class="md-tb-label">Format</span>
                <button class="md-tb-btn" onclick="mdWrap('**','**')" title="Bold">B</button>
                <button class="md-tb-btn" onclick="mdWrap('*','*')" title="Italic" style="font-style:italic">I</button>
                <div class="md-tb-sep"></div>
                <button class="md-tb-btn" onclick="mdLinePrefix('# ')" title="Heading 1">H1</button>
                <button class="md-tb-btn" onclick="mdLinePrefix('## ')" title="Heading 2">H2</button>
                <button class="md-tb-btn" onclick="mdLinePrefix('### ')" title="Heading 3">H3</button>
                <div class="md-tb-sep"></div>
                <button class="md-tb-btn" onclick="mdLinePrefix('- ')" title="Bullet list">• List</button>
                <button class="md-tb-btn" onclick="mdLinePrefix('> ')" title="Quote">❝</button>
                <button class="md-tb-btn" onclick="mdInsert('\n---\n')" title="Divider">—</button>
                <button class="md-tb-btn" onclick="mdWrap('`','`')" title="Inline code">`code`</button>
              </div>
              <textarea class="notes-editor-body-input" id="notes-editor-body" placeholder="Start writing… (supports Markdown: ## Heading, **bold**, - bullet, > quote, --- divider)" oninput="onNoteEditorInput()"></textarea>
              <div class="notes-md-preview" id="notes-md-preview"></div>
            </div>
          </div>
        </div>

      </div><!-- end .notes-columns -->
    </div>
  </div>

  <!-- == REMINDERS PAGE == -->
  <div id="page-reminders" style="display:none;flex-direction:column;height:calc(100vh - 58px)">
    <div class="rem-page-wrap" style="width:100%">
      <!-- View toggle: List vs Calendar -->
      <div class="rem-view-toggle" style="display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:8px">
        <div style="display:flex;align-items:center;gap:8px">
          <span>View</span>
          <button class="tan-filter-btn active" id="rem-view-list-btn" onclick="setRemView('list',this)">📋 List</button>
          <button class="tan-filter-btn" id="rem-view-cal-btn" onclick="setRemView('cal',this)">📅 Calendar</button>
        </div>
        <button onclick="syncAllRemindersToGoogleCalendar()" title="Sync all existing reminders to Google Calendar" style="display:flex;align-items:center;gap:6px;padding:7px 14px;background:var(--accent);color:#fff;border:none;border-radius:8px;font-size:12px;font-weight:600;cursor:pointer;font-family:'Inter',sans-serif;opacity:0.92;transition:opacity 0.2s" onmouseover="this.style.opacity='1'" onmouseout="this.style.opacity='0.92'">
          🔄 Sync All to Google Calendar
        </button>
      </div>

      <!-- Full Monthly Calendar -->
      <div class="full-cal-wrap" id="full-cal-wrap">
        <div class="full-cal-header">
          <button class="full-cal-nav" onclick="calNav(-1)">‹ Prev</button>
          <div class="full-cal-title" id="full-cal-title">March 2026</div>
          <button class="full-cal-today-btn" onclick="calGoToday()">Today</button>
          <button class="full-cal-nav" onclick="calNav(1)">Next ›</button>
        </div>
        <div class="full-cal-dow-row">
          <div class="full-cal-dow">Sun</div>
          <div class="full-cal-dow">Mon</div>
          <div class="full-cal-dow">Tue</div>
          <div class="full-cal-dow">Wed</div>
          <div class="full-cal-dow">Thu</div>
          <div class="full-cal-dow">Fri</div>
          <div class="full-cal-dow">Sat</div>
        </div>
        <div class="full-cal-grid" id="full-cal-grid"></div>
      </div>

      <!-- Summary stats row (minimalist) -->
      <div class="rem-summary-row">
        <span id="rem-summary-title">Reminders</span>
        <div class="rem-summary-stat">
          <span class="rem-stat-label">Active:</span>
          <span class="rem-stat-count" id="rem-count-active">0</span>
        </div>
        <div class="rem-summary-stat">
          <span class="rem-stat-label">Completed:</span>
          <span class="rem-stat-count" id="rem-count-completed">0</span>
        </div>
      </div>

      <!-- Two columns below the tiles -->
      <div class="rem-columns">

        <!-- Column 1: My Lists -->
        <div class="rem-lists-panel">
          <div class="rem-lists-hdr">
            <span class="rem-lists-title">My Lists</span>
            <button class="rem-new-list-btn" onclick="createRemList()" title="New List">＋</button>
          </div>
          <div class="rem-list-items" id="rem-list-items"></div>
        </div>

        <!-- Column 2: Checklist -->
        <div class="rem-checklist-panel">
          <div class="rem-mobile-back" onclick="remMobileBack()">
            <button class="rem-mobile-back-btn">‹ My Lists</button>
          </div>
          <div class="rem-checklist-hdr">
            <span class="rem-checklist-title" id="rem-checklist-title">All</span>
            <div class="rem-checklist-actions">
              <button class="cbtn del" id="rem-delete-list-btn" onclick="deleteCurrentRemList()" style="display:none">🗑 Delete List</button>
            </div>
          </div>
          <div class="rem-checklist-body" id="rem-checklist-body">
            <div class="rem-empty"><div class="rem-empty-icon">⏰</div><p>No reminders here</p></div>
          </div>
        </div>

        <!-- Column 3: Right Summary Panel -->
        <div class="rem-right-panel" id="rem-right-panel">

          <!-- Overview stats -->
          <div class="rrp-section rrp-overview-section">
            <div class="rrp-overview-header">
              <span class="rrp-overview-label">OVERVIEW</span>
            </div>
            <div class="rrp-stats-grid">
              <div class="rrp-stat rrp-stat-total"><div class="rrp-stat-num" id="rrp-total">0</div><div class="rrp-stat-lbl">Total</div></div>
              <div class="rrp-stat rrp-stat-done"><div class="rrp-stat-num" id="rrp-done">0</div><div class="rrp-stat-lbl">Done</div></div>
            </div>
          </div>

          <!-- Priority breakdown -->
          <div class="rrp-section">
            <div class="rrp-title">By priority</div>
            <div class="rrp-pri-row"><span class="rrp-pri-label">High</span><span class="rrp-pri-count" id="rrp-high">0</span></div>
            <div class="rrp-bar"><div class="rrp-bar-fill" id="rrp-high-bar" style="background:var(--red);width:0%"></div></div>
            <div class="rrp-pri-row"><span class="rrp-pri-label">Medium</span><span class="rrp-pri-count" id="rrp-med">0</span></div>
            <div class="rrp-bar"><div class="rrp-bar-fill" id="rrp-med-bar" style="background:var(--accent2);width:0%"></div></div>
            <div class="rrp-pri-row"><span class="rrp-pri-label">Low</span><span class="rrp-pri-count" id="rrp-low">0</span></div>
            <div class="rrp-bar"><div class="rrp-bar-fill" id="rrp-low-bar" style="background:var(--green);width:0%"></div></div>
          </div>

          <!-- Mini calendar -->
          <div class="rrp-section">
            <div class="rrp-title" id="rrp-cal-title">This month</div>
            <div class="rrp-mini-cal" id="rrp-mini-cal"></div>
            <div class="rrp-cal-legend">
              <div class="rrp-cal-leg-dot" style="background:var(--red)"></div><span>Today</span>
              <div class="rrp-cal-leg-dot" style="background:rgba(139,94,42,.25);margin-left:6px"></div><span>Has task</span>
            </div>
          </div>

          <!-- This week -->
          <div class="rrp-section">
            <div class="rrp-title">This week</div>
            <div id="rrp-upcoming-list"><span style="font-size:11px;color:var(--muted)">No upcoming tasks</span></div>
          </div>

          <!-- Awaiting / No date -->
          <div class="rrp-section">
            <div class="rrp-title">Awaiting / No date</div>
            <div id="rrp-nodate-list"><span style="font-size:11px;color:var(--muted)">None</span></div>
          </div>

          <!-- Progress -->
          <div class="rrp-section">
            <div class="rrp-title">Progress</div>
            <div class="rrp-prog-wrap">
              <div class="rrp-prog-header">
                <span class="rrp-prog-label">Completed</span>
                <span class="rrp-prog-val" id="rrp-prog-val">0 / 0</span>
              </div>
              <div class="rrp-bar" style="height:5px"><div class="rrp-bar-fill" id="rrp-prog-bar" style="background:var(--green);width:0%"></div></div>
              <div style="font-size:10px;color:var(--muted);margin-top:4px" id="rrp-prog-pct">0% complete</div>
            </div>
          </div>

        </div>

      </div>
    </div>
  </div>

  <!-- == STICKY NOTES PAGE == -->
  <div id="page-sticky" style="display:none;flex-direction:column;height:calc(100vh - 56px)">

    <!-- Colour picker toolbar -->
    <div class="sp-toolbar">
      <div class="sp-toolbar-left">
        <span class="sp-label">Colour:</span>
        <div class="sp-colors" id="sp-colors"></div>
      </div>
      <div class="sp-toolbar-right">
        <span class="sp-count" id="sp-count">0 stickies</span>
        <button class="btn-ghost" onclick="toggleArchivePanel()" style="font-size:12px">🗂 Archive</button>
        <button class="btn" onclick="addSticky()">+ New Sticky</button>
      </div>
    </div>

    <!-- Sticky board -->
    <div class="sp-board" id="sp-board">
      <div class="sp-empty" id="sp-empty">
        <div class="sp-empty-icon">📌</div>
        <p>No sticky notes yet</p>
        <p style="font-size:12px">Pick a colour above and click <strong>+ New Sticky</strong></p>
      </div>
    </div>

    <!-- 6. Archive panel -->
    <div class="sp-archive-panel" id="sp-archive-panel">
      <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:10px">
        <span class="sp-archive-title">🗂 Archived Stickies</span>
        <button class="btn-ghost" style="font-size:11px" onclick="toggleArchivePanel()">✕ Close</button>
      </div>
      <div class="sp-archive-grid" id="sp-archive-grid">
        <span style="font-size:12px;color:var(--muted)">No archived stickies</span>
      </div>
    </div>

  </div>

  <!-- -- TRADING JOURNAL PAGE ---------------------- -->
<div id="page-journal" style="display:none;flex-direction:column;width:100%;min-height:calc(100vh - 60px);background:var(--bg)">

  <!-- Toolbar -->
  <div class="tj-toolbar">
    <div class="tj-toolbar-left">
      <!-- Trade Mode Toggle -->
      <div class="tj-mode-toggle">
        <button class="tj-mode-btn actual active" id="tj-mode-all" onclick="setTradeMode('all')">All</button>
        <button class="tj-mode-btn actual" id="tj-mode-actual" onclick="setTradeMode('actual')">✅ Actual</button>
        <button class="tj-mode-btn dummy" id="tj-mode-dummy" onclick="setTradeMode('dummy')">🧪 Dummy</button>
      </div>
      <select id="tj-filter-month" class="tj-select" onchange="renderJournal()">
        <option value="all">📅 All Time</option>
        <option value="0">January</option><option value="1">February</option>
        <option value="2">March</option><option value="3">April</option>
        <option value="4">May</option><option value="5">June</option>
        <option value="6">July</option><option value="7">August</option>
        <option value="8">September</option><option value="9">October</option>
        <option value="10">November</option><option value="11">December</option>
      </select>
      <select id="tj-filter-status" class="tj-select" onchange="renderJournal()">
        <option value="all">All Trades</option>
        <option value="win">✅ Wins</option>
        <option value="loss">❌ Losses</option>
        <option value="open">⏳ Open</option>
      </select>
    </div>
    <button class="btn" onclick="openTradeModal()">+ New Trade</button>
  </div>

  <!-- Stats bar -->
  <div class="tj-stats" id="tj-stats">
    <div class="tj-stat"><div class="tj-stat-num b">0</div><div class="tj-stat-lbl">Total Trades</div></div>
    <div class="tj-stat"><div class="tj-stat-num b">0%</div><div class="tj-stat-lbl">Win Rate</div></div>
    <div class="tj-stat"><div class="tj-stat-num b">-</div><div class="tj-stat-lbl">Net P&amp;L</div></div>
    <div class="tj-stat"><div class="tj-stat-num g">0</div><div class="tj-stat-lbl">Wins</div></div>
    <div class="tj-stat"><div class="tj-stat-num r">0</div><div class="tj-stat-lbl">Losses</div></div>
  </div>

  <!-- Table -->
  <div class="tj-table-wrap">
    <table class="tj-table">
      <thead>
        <tr>
          <th>Date</th><th>Symbol</th><th>Type</th>
          <th>Entry</th><th>Exit</th><th>Qty</th>
          <th>P&amp;L</th><th>Status</th><th>Mode</th><th>Notes</th><th></th>
        </tr>
      </thead>
      <tbody id="tj-tbody"></tbody>
    </table>
    <div class="tj-empty" id="tj-empty">
      <div style="font-size:48px;opacity:.25">📈</div>
      <p style="font-size:16px;font-weight:700;color:var(--text)">No trades logged yet</p>
      <p style="font-size:13px;color:var(--muted);margin-top:4px">Click <strong>+ New Trade</strong> above to log your first trade</p>
      <button class="btn" style="margin-top:16px" onclick="openTradeModal()">+ Log First Trade</button>
    </div>
  </div>
</div>




<!-- -- ROUTINE PAGE ------------------------------- -->
<div id="page-routine" style="display:none;flex-direction:column;width:100%;min-height:calc(100vh - 60px);background:var(--bg)">

  <!-- Top progress bar -->
  <div class="rt-header">
    <div class="rt-header-left">
      <div class="rt-today-label" id="rt-today-label">Today · Friday, Mar 20</div>
      <div class="rt-progress-wrap">
        <div class="rt-progress-bar"><div class="rt-progress-fill" id="rt-progress-fill" style="width:0%"></div></div>
        <span class="rt-progress-pct" id="rt-progress-pct">0% done today</span>
      </div>
    </div>
    <div class="rt-header-right">
      <button class="btn-ghost" onclick="showRoutineView('manage')">⚙️ Manage Routines</button>
      <button class="btn" onclick="showRoutineView('today')" id="rt-back-btn" style="display:none">← Today's View</button>
    </div>
  </div>

  <!-- TODAY VIEW -->
  <div id="rt-today-view" style="padding:20px 28px">
    <div id="rt-checklist"></div>
  </div>

  <!-- MANAGE VIEW -->
  <div id="rt-manage-view" style="display:none;padding:20px 28px">
    <div class="rt-manage-toolbar">
      <div class="rt-manage-title">📋 Routine Templates</div>
      <button class="btn" onclick="openRoutineGroupModal()">+ New Routine</button>
    </div>
    <div id="rt-groups-list"></div>
  </div>

</div>

<!-- ── TASK & ACTION NOTES PAGE ───────────────────── -->
<div id="page-tasknotes" style="display:none;flex-direction:column;width:100%;min-height:calc(100vh - 60px);background:var(--bg)">

  <div class="tan-header">
    <span class="tan-title">✍️ Task &amp; Action Notes</span>
    <span style="font-size:12px;color:var(--muted)" id="tan-hdr-count">0 notes</span>
  </div>

  <!-- Quick-add bar -->
  <div class="tan-quick-bar">
    <textarea id="tan-quick-input" placeholder="Quick note… press Ctrl+Enter or click Add" rows="2"
      onkeydown="if((event.ctrlKey||event.metaKey)&&event.key==='Enter'){addTaskNote();event.preventDefault();}"></textarea>
    <select id="tan-quick-cat" class="tan-cat-sel" title="Category">
      <option value="personal">👤 Personal</option>
      <option value="official">💼 Official</option>
    </select>
    <button class="tan-add-btn" onclick="addTaskNote()">+ Add</button>
  </div>

  <!-- Filter / search bar -->
  <div class="tan-filters">
    <div class="tan-filters-row">
      <span style="font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:0.8px;color:var(--muted)">Category</span>
      <button class="tan-filter-btn active" id="tan-fc-all"      onclick="tanSetCat('all',this)">All</button>
      <button class="tan-filter-btn"        id="tan-fc-personal" onclick="tanSetCat('personal',this)">👤 Personal</button>
      <button class="tan-filter-btn"        id="tan-fc-official" onclick="tanSetCat('official',this)">💼 Official</button>
      <div class="tan-filters-divider"></div>
      <span style="font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:0.8px;color:var(--muted)">Status</span>
      <button class="tan-filter-btn active" id="tan-f-all"    onclick="tanSetFilter('all',this)">All</button>
      <button class="tan-filter-btn"        id="tan-f-open"   onclick="tanSetFilter('open',this)">Open</button>
      <button class="tan-filter-btn"        id="tan-f-done"   onclick="tanSetFilter('done',this)">Done</button>
      <div class="tan-filters-divider"></div>
      <span style="font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:0.8px;color:var(--muted)">Priority</span>
      <button class="tan-filter-btn" id="tan-f-high"   onclick="tanSetFilter('high',this)">🔴 High</button>
      <button class="tan-filter-btn" id="tan-f-medium" onclick="tanSetFilter('medium',this)">🟡 Medium</button>
      <button class="tan-filter-btn" id="tan-f-low"    onclick="tanSetFilter('low',this)">🟢 Low</button>
      <input class="tan-search" id="tan-search" placeholder="Search notes…" oninput="renderTaskNotes()">
      <select class="tan-sort-sel" id="tan-sort" onchange="renderTaskNotes()">
        <option value="date-desc">📅 Newest first</option>
        <option value="date-asc">📅 Oldest first</option>
        <option value="priority">🔴 By Priority</option>
        <option value="category">💼 By Category</option>
        <option value="status">✅ By Status</option>
      </select>
    </div>
  </div>

  <!-- Notes list -->
  <div class="tan-list" id="tan-list"></div>

</div>

<!-- ── IMPORTANT DATES PAGE ───────────────────────── -->
<div id="page-impdates" style="display:none;flex-direction:column;width:100%;min-height:calc(100vh - 60px);background:var(--bg);position:relative">

  <!-- PIN LOCK OVERLAY (uses same PIN as Daybook & Investments) -->
  <div class="imp-lock-overlay" id="imp-lock-overlay" style="display:none">
    <div class="imp-lock-box">
      <div class="imp-lock-icon">🔐</div>
      <div class="imp-lock-title">Important Dates is Locked</div>
      <div class="imp-lock-sub" id="imp-lock-sub">Enter your PIN to view your important dates</div>
      <div class="imp-pin-dots" id="imp-pin-dots">
        <div class="imp-pin-dot" id="imp-dot-0"></div>
        <div class="imp-pin-dot" id="imp-dot-1"></div>
        <div class="imp-pin-dot" id="imp-dot-2"></div>
        <div class="imp-pin-dot" id="imp-dot-3"></div>
      </div>
      <div class="imp-pin-error" id="imp-pin-error"></div>
      <div class="imp-numpad">
        <button class="imp-num-btn" onclick="impPinPress('1')">1</button>
        <button class="imp-num-btn" onclick="impPinPress('2')">2</button>
        <button class="imp-num-btn" onclick="impPinPress('3')">3</button>
        <button class="imp-num-btn" onclick="impPinPress('4')">4</button>
        <button class="imp-num-btn" onclick="impPinPress('5')">5</button>
        <button class="imp-num-btn" onclick="impPinPress('6')">6</button>
        <button class="imp-num-btn" onclick="impPinPress('7')">7</button>
        <button class="imp-num-btn" onclick="impPinPress('8')">8</button>
        <button class="imp-num-btn" onclick="impPinPress('9')">9</button>
        <button class="imp-num-btn clear" onclick="impPinClear()">CLR</button>
        <button class="imp-num-btn" onclick="impPinPress('0')">0</button>
        <button class="imp-num-btn del" onclick="impPinBack()">⌫</button>
      </div>
    </div>
  </div>

  <div class="imp-header">
    <span class="imp-title">🗓️ Important Dates</span>
    <div style="display:flex;align-items:center;gap:10px">
      <span style="font-size:12px;color:var(--muted)" id="imp-hdr-count">0 dates</span>
      <button class="btn" onclick="impOpenModal()">+ Add Date</button>
    </div>
  </div>

  <div class="imp-filters">
    <button class="imp-filter-btn active" id="imp-f-all"      onclick="impSetFilter('all',this)">All</button>
    <button class="imp-filter-btn"        id="imp-f-upcoming" onclick="impSetFilter('upcoming',this)">📅 Upcoming</button>
    <button class="imp-filter-btn"        id="imp-f-today"    onclick="impSetFilter('today',this)">⭐ Today</button>
    <button class="imp-filter-btn"        id="imp-f-past"     onclick="impSetFilter('past',this)">⏳ Past</button>
  </div>

  <div class="imp-list-wrap" id="imp-list-wrap">
    <div class="imp-empty">No important dates yet. Click "+ Add Date" to add your first one.</div>
  </div>

</div>

<!-- Important Dates modal -->
<div class="imp-modal-backdrop" id="imp-modal-backdrop">
  <div class="imp-modal">
    <div class="imp-modal-title" id="imp-modal-title">Add Important Date</div>
    <input type="hidden" id="imp-edit-id" value="">

    <label for="imp-input-date">Date *</label>
    <input type="date" id="imp-input-date">

    <label for="imp-input-title">Title *</label>
    <input type="text" id="imp-input-title" placeholder="e.g. Anniversary, Doctor appointment, Exam">

    <label for="imp-input-cat">Category</label>
    <select id="imp-input-cat">
      <option value="personal">👤 Personal</option>
      <option value="official">💼 Official</option>
      <option value="family">👨‍👩‍👧 Family</option>
      <option value="health">❤️ Health</option>
      <option value="finance">💰 Finance</option>
      <option value="other">📌 Other</option>
    </select>

    <label for="imp-input-note">Notes (optional)</label>
    <textarea id="imp-input-note" placeholder="Extra details..."></textarea>

    <div class="imp-modal-actions">
      <button class="btn-ghost" onclick="impCloseModal()">Cancel</button>
      <button class="btn" onclick="impSaveEntry()">Save</button>
    </div>
  </div>
</div>

<!-- ── FINANCE TRACKER PAGE ───────────────────────── -->
<div id="page-finance" style="display:none;flex-direction:column;width:100%;min-height:calc(100vh - 60px);background:var(--bg)">

  <div class="fin-header">
    <span class="fin-title">💰 Finance Tracker</span>
    <div style="display:flex;align-items:center;gap:10px">
      <div class="fin-view-toggle">
        <button class="fin-vtbtn active" id="fin-vcard" onclick="finSetView('card')" title="Card view">⊞ Cards</button>
        <button class="fin-vtbtn" id="fin-vlist" onclick="finSetView('list')" title="List view">☰ List</button>
      </div>
      <button class="btn" onclick="openFinModal()">+ New Entry</button>
    </div>
  </div>

  <!-- Summary cards -->
  <div class="fin-summary">
    <div class="fin-sum-card">
      <div class="fin-sum-label">They Owe Me</div>
      <div class="fin-sum-val gave" id="fin-sum-gave">₹0</div>
      <div class="fin-sum-sub" id="fin-sum-gave-sub">0 entries</div>
    </div>
    <div class="fin-sum-card">
      <div class="fin-sum-label">I Owe Them</div>
      <div class="fin-sum-val borrow" id="fin-sum-borrow">₹0</div>
      <div class="fin-sum-sub" id="fin-sum-borrow-sub">0 entries</div>
    </div>
    <div class="fin-sum-card">
      <div class="fin-sum-label">Net Balance</div>
      <div class="fin-sum-val" id="fin-sum-net">₹0</div>
      <div class="fin-sum-sub" id="fin-sum-net-sub">—</div>
    </div>
  </div>

  <!-- Per-person chips -->
  <div class="fin-people">
    <div class="fin-people-title">Per Person</div>
    <div class="fin-person-chips" id="fin-person-chips"></div>
  </div>

  <!-- Filters -->
  <div class="fin-filters">
    <button class="tan-filter-btn active" id="fin-f-all"      onclick="finSetFilter('all',this)">All</button>
    <button class="tan-filter-btn"        id="fin-f-gave"     onclick="finSetFilter('gave',this)">💚 I Gave</button>
    <button class="tan-filter-btn"        id="fin-f-borrowed" onclick="finSetFilter('borrowed',this)">❤️ I Borrowed</button>
    <div class="tan-filters-divider"></div>
    <button class="tan-filter-btn"        id="fin-f-pending"  onclick="finSetFilter('pending',this)">⏳ Pending</button>
    <button class="tan-filter-btn"        id="fin-f-partial"  onclick="finSetFilter('partial',this)">🔵 Partial</button>
    <button class="tan-filter-btn"        id="fin-f-overdue"  onclick="finSetFilter('overdue',this)">🔴 Overdue</button>
    <button class="tan-filter-btn"        id="fin-f-settled"  onclick="finSetFilter('settled',this)">✅ Settled</button>
    <div class="tan-filters-divider"></div>
    <!-- 5. Sort -->
    <select class="fin-sort-sel" id="fin-sort" onchange="renderFinance()">
      <option value="date-desc">📅 Newest</option>
      <option value="date-asc">📅 Oldest</option>
      <option value="amount-desc">₹ Highest</option>
      <option value="amount-asc">₹ Lowest</option>
      <option value="person">👤 Person</option>
      <option value="duedate">⏰ Due Date</option>
      <option value="status">🔵 Status</option>
    </select>
    <!-- 2. Group toggle -->
    <button class="tan-filter-btn" id="fin-group-toggle" onclick="finToggleGroup(this)">👥 Group by Person</button>
    <!-- 7. Timeline toggle -->
    <button class="tan-filter-btn" id="fin-tl-toggle" onclick="finToggleTimeline(this)">📅 Timeline</button>
    <input class="tan-search" id="fin-search" placeholder="Search person / note…" oninput="renderFinance()" style="margin-left:auto">
  </div>

  <!-- 7. Timeline panel -->
  <div class="fin-timeline-panel" id="fin-timeline-panel">
    <div class="fin-tl-title">Upcoming & Overdue Payments</div>
    <div id="fin-tl-rows"></div>
  </div>

  <!-- Entries -->
  <div class="fin-list fin-view-card" id="fin-list">
    <div class="fin-grid" id="fin-card-grid"></div>
    <div class="fin-listbox" id="fin-listbox"></div>
  </div>

</div>

  </div><!-- end #page-scroll-area -->

<!-- ── DAYBOOK PAGE ───────────────────────────────── -->
<div id="page-daybook" style="display:none;flex-direction:column;width:100%;height:calc(100vh - 58px);background:var(--bg);position:relative">

  <!-- PIN LOCK OVERLAY -->
  <div class="db-lock-overlay" id="db-lock-overlay" style="display:none">
    <div class="db-lock-box">
      <div class="db-lock-icon">🔐</div>
      <div class="db-lock-title">Daybook is Locked</div>
      <div class="db-lock-sub" id="db-lock-sub">Enter your PIN to open your private diary</div>
      <div class="db-pin-dots" id="db-pin-dots">
        <div class="db-pin-dot" id="db-dot-0"></div>
        <div class="db-pin-dot" id="db-dot-1"></div>
        <div class="db-pin-dot" id="db-dot-2"></div>
        <div class="db-pin-dot" id="db-dot-3"></div>
      </div>
      <div class="db-pin-error" id="db-pin-error"></div>
      <div class="db-numpad">
        <button class="db-num-btn" onclick="dbPinPress('1')">1</button>
        <button class="db-num-btn" onclick="dbPinPress('2')">2</button>
        <button class="db-num-btn" onclick="dbPinPress('3')">3</button>
        <button class="db-num-btn" onclick="dbPinPress('4')">4</button>
        <button class="db-num-btn" onclick="dbPinPress('5')">5</button>
        <button class="db-num-btn" onclick="dbPinPress('6')">6</button>
        <button class="db-num-btn" onclick="dbPinPress('7')">7</button>
        <button class="db-num-btn" onclick="dbPinPress('8')">8</button>
        <button class="db-num-btn" onclick="dbPinPress('9')">9</button>
        <button class="db-num-btn clear" onclick="dbPinClear()">CLR</button>
        <button class="db-num-btn" onclick="dbPinPress('0')">0</button>
        <button class="db-num-btn del" onclick="dbPinBack()">⌫</button>
      </div>
    </div>
  </div>
  <div class="db-layout">

    <!-- LEFT: filters -->
    <div class="db-left">
      <div class="db-left-head">
        <div class="db-left-title">📖 Daybook</div>
        <div class="db-left-sub">Personal diary &amp; notes</div>
      </div>
      <div class="db-left-search">
        <input id="db-search" placeholder="🔍  Search entries..." oninput="dbRender()">
      </div>
      <div class="db-filter-section">Filter by tag</div>
      <button class="db-filter-btn active" id="db-f-all" onclick="dbSetFilter('all',this)">
        <span class="db-filter-dot" style="background:#1a9a6c"></span> All entries
        <span class="db-filter-count" id="db-cnt-all">0</span>
      </button>
      <button class="db-filter-btn" id="db-f-trade" onclick="dbSetFilter('trade',this)">
        <span class="db-filter-dot" style="background:#1d9e75"></span> Trade
        <span class="db-filter-count" id="db-cnt-trade">0</span>
      </button>
      <button class="db-filter-btn" id="db-f-personal" onclick="dbSetFilter('personal',this)">
        <span class="db-filter-dot" style="background:#7f77dd"></span> Personal
        <span class="db-filter-count" id="db-cnt-personal">0</span>
      </button>
      <button class="db-filter-btn" id="db-f-idea" onclick="dbSetFilter('idea',this)">
        <span class="db-filter-dot" style="background:#ba7517"></span> Idea
        <span class="db-filter-count" id="db-cnt-idea">0</span>
      </button>
      <button class="db-filter-btn" id="db-f-health" onclick="dbSetFilter('health',this)">
        <span class="db-filter-dot" style="background:#e24b4a"></span> Health
        <span class="db-filter-count" id="db-cnt-health">0</span>
      </button>
      <button class="db-filter-btn" id="db-f-work" onclick="dbSetFilter('work',this)">
        <span class="db-filter-dot" style="background:#378add"></span> Work
        <span class="db-filter-count" id="db-cnt-work">0</span>
      </button>
      <button class="db-filter-btn" id="db-f-family" onclick="dbSetFilter('family',this)">
        <span class="db-filter-dot" style="background:#d4537e"></span> Family
        <span class="db-filter-count" id="db-cnt-family">0</span>
      </button>
      <!-- mobile filter row -->
      <div class="db-filters-mobile" id="db-filters-mobile"></div>
      <div class="db-left-footer">
        <button class="db-new-btn" onclick="dbOpenCompose()">＋ New Entry</button>
      </div>
    </div>

    <!-- RIGHT: entries + compose -->
    <div class="db-right">
      <div class="db-entries-wrap" id="db-entries-wrap">
        <div class="db-empty" id="db-empty-state">
          <div class="db-empty-icon">📖</div>
          <div class="db-empty-text">No entries yet — write your first one!</div>
        </div>
        <div id="db-entries-list"></div>
      </div>

      <!-- compose bar -->
      <div class="db-compose" id="db-compose">
        <div class="db-compose-top">
          <span class="db-compose-dt" id="db-compose-dt">📅 —</span>
          <div class="db-compose-tags">
            <span class="db-compose-tag-lbl">Tag:</span>
            <button class="db-ctag" data-tag="trade"    onclick="dbToggleTag(this)">trade</button>
            <button class="db-ctag" data-tag="personal" onclick="dbToggleTag(this)">personal</button>
            <button class="db-ctag" data-tag="idea"     onclick="dbToggleTag(this)">idea</button>
            <button class="db-ctag" data-tag="health"   onclick="dbToggleTag(this)">health</button>
            <button class="db-ctag" data-tag="work"     onclick="dbToggleTag(this)">work</button>
            <button class="db-ctag" data-tag="family"   onclick="dbToggleTag(this)">family</button>
          </div>
        </div>
        <textarea class="db-compose-input" id="db-compose-text" rows="3"
          placeholder="Write your entry… (what happened, trades, thoughts, anything)"></textarea>
        <div class="db-compose-footer">
          <button class="btn-ghost" onclick="dbCloseCompose()">Cancel</button>
          <button class="btn" onclick="dbSaveEntry()">💾 Save Entry</button>
        </div>
      </div>
    </div>

  </div>
</div>

<!-- == SHOPPING PAGE == -->
<div id="page-shopping" style="display:none;flex-direction:column;width:100%;height:calc(100vh - 58px);background:var(--bg)">
  <div class="shop-layout">
    <div class="shop-left">
      <div class="shop-left-hdr">
        <div class="shop-left-title">🛒 Shops</div>
        <button class="shop-new-btn" onclick="shopOpenModal()" title="Add Shop">＋</button>
      </div>
      <div class="shop-list" id="shop-list"></div>
      <div class="shop-left-footer">
        <button onclick="shopOpenModal()">+ Add Shop</button>
      </div>
    </div>
    <div class="shop-right" id="shop-right">
      <div class="shop-empty" id="shop-empty-state">
        <div class="shop-empty-icon">🛒</div>
        <div class="shop-empty-text">Select a shop or add one to get started</div>
      </div>
    </div>
  </div>
</div>

<!-- == INVESTMENTS PAGE == -->
<div id="page-investments" style="display:none;flex-direction:column;width:100%;height:calc(100vh - 58px);background:var(--bg)">
  <!-- PIN LOCK OVERLAY -->
  <div class="inv-lock-overlay" id="inv-lock-overlay" style="display:none">
    <div class="inv-lock-box">
      <div class="inv-lock-icon">🔐</div>
      <div class="inv-lock-title">Investments is Locked</div>
      <div class="inv-lock-sub" id="inv-lock-sub">Enter your PIN to view your portfolio</div>
      <div class="inv-pin-dots" id="inv-pin-dots">
        <div class="inv-pin-dot" id="inv-dot-0"></div>
        <div class="inv-pin-dot" id="inv-dot-1"></div>
        <div class="inv-pin-dot" id="inv-dot-2"></div>
        <div class="inv-pin-dot" id="inv-dot-3"></div>
      </div>
      <div class="inv-pin-error" id="inv-pin-error"></div>
      <div class="inv-numpad">
        <button class="inv-num-btn" onclick="invPinPress('1')">1</button>
        <button class="inv-num-btn" onclick="invPinPress('2')">2</button>
        <button class="inv-num-btn" onclick="invPinPress('3')">3</button>
        <button class="inv-num-btn" onclick="invPinPress('4')">4</button>
        <button class="inv-num-btn" onclick="invPinPress('5')">5</button>
        <button class="inv-num-btn" onclick="invPinPress('6')">6</button>
        <button class="inv-num-btn" onclick="invPinPress('7')">7</button>
        <button class="inv-num-btn" onclick="invPinPress('8')">8</button>
        <button class="inv-num-btn" onclick="invPinPress('9')">9</button>
        <button class="inv-num-btn clear" onclick="invPinClear()">CLR</button>
        <button class="inv-num-btn" onclick="invPinPress('0')">0</button>
        <button class="inv-num-btn del" onclick="invPinBack()">⌫</button>
      </div>
    </div>
  </div>
  <div class="inv-wrap">
    <div class="inv-header">
      <span class="inv-title">📊 Investment Portfolio</span>
      <button class="btn" onclick="invOpenAddRow()">+ Add Asset</button>
    </div>
    <div class="inv-summary">
      <div class="inv-sum-card">
        <div class="inv-sum-label">Total Portfolio</div>
        <div class="inv-sum-val" id="inv-sum-total">₹0</div>
        <div class="inv-sum-sub" id="inv-sum-total-sub">0 assets</div>
      </div>
      <div class="inv-sum-card">
        <div class="inv-sum-label">Target Allocation</div>
        <div class="inv-sum-val" id="inv-sum-target">0%</div>
        <div class="inv-sum-sub" id="inv-sum-target-sub">—</div>
      </div>
      <div class="inv-sum-card">
        <div class="inv-sum-label">Top Holding</div>
        <div class="inv-sum-val" id="inv-sum-top">—</div>
        <div class="inv-sum-sub" id="inv-sum-top-sub">—</div>
      </div>
    </div>
    <div class="inv-table-wrap">
      <div id="inv-table-container"></div>
      <!-- Portfolio Allocation Chart -->
      <div class="inv-chart-section" id="inv-chart-section" style="display:none">
        <div class="inv-chart-heading">
          <span class="inv-chart-icon">🥧</span>
          <div>
            <div class="inv-chart-title">Portfolio Allocation</div>
            <div class="inv-chart-sub">Visual breakdown of your investment distribution</div>
          </div>
        </div>
        <div class="inv-chart-wrap">
          <canvas id="inv-pie-chart"></canvas>
        </div>
      </div>
    </div>
  </div>
</div>

<!-- ── FINANCE ENTRY MODAL ─────────────────────────── -->
<div class="overlay" id="fin-modal-overlay">
<div class="modal" style="max-width:520px;display:flex;flex-direction:column;max-height:92vh;overflow-y:auto;-webkit-overflow-scrolling:touch">
  <div class="mhead">
    <h2 id="fin-modal-title">New Entry</h2>
    <button class="mclose" onclick="closeFinModal()">✕</button>
  </div>
  <input type="hidden" id="fin-edit-id">
  <div class="fin-modal-grid" style="display:grid;grid-template-columns:1fr 1fr;gap:12px">
    <div class="frow" style="grid-column:1/-1"><label>Type *</label>
      <select id="fin-type">
        <option value="gave">💚 I Gave (they owe me)</option>
        <option value="borrowed">❤️ I Borrowed (I owe them)</option>
      </select>
    </div>
    <div class="frow"><label>Person Name *</label><input id="fin-person" placeholder="e.g. Rahul, Priya"></div>
    <div class="frow"><label>Amount ₹ *</label><input id="fin-amount" type="number" step="0.01" placeholder="0.00"></div>
    <div class="frow"><label>Payment Method *</label>
      <select id="fin-paymethod">
        <option value="cash">💵 Liquid Cash</option>
        <option value="credit_card">💳 Credit Card</option>
        <option value="bank">🏦 Bank Transfer</option>
        <option value="upi">📱 UPI</option>
      </select>
    </div>
    <div class="frow"><label>Interest Rate % / yr</label>
      <input id="fin-interest" type="number" step="0.01" placeholder="0 = no interest">
    </div>
    <div class="frow"><label>Date *</label><input id="fin-date" type="date"></div>
    <div class="frow"><label>Due Date (optional)</label><input id="fin-duedate" type="date"></div>
  </div>
  <div class="frow"><label>Notes / Reason</label>
    <textarea id="fin-notes" placeholder="e.g. For groceries, Wedding gift, Office lunch…" style="min-height:70px"></textarea>
  </div>
  <div class="mfoot">
    <button class="btn-ghost" onclick="closeFinModal()">Cancel</button>
    <button class="btn" onclick="saveFinEntry()">💾 Save Entry</button>
  </div>
</div>
</div>

<div class="overlay" id="fin-pay-modal-overlay">
<div class="modal" style="max-width:460px">
  <div class="mhead">
    <h2 id="fin-pay-modal-title">Record Payment</h2>
    <button class="mclose" onclick="closeFinPayModal()">✕</button>
  </div>
  <input type="hidden" id="fin-pay-entry-id">
  <div style="background:var(--s2);border-radius:8px;padding:10px 14px;margin-bottom:14px;font-size:13px" id="fin-pay-summary"></div>
  <div style="display:grid;grid-template-columns:1fr 1fr;gap:12px">
    <div class="frow"><label>Amount ₹ *</label><input id="fin-pay-amt" type="number" step="0.01" placeholder="0.00"></div>
    <div class="frow"><label>Date *</label><input id="fin-pay-date" type="date"></div>
    <div class="frow"><label>Payment Type</label>
      <select id="fin-pay-type">
        <option value="principal">💰 Principal</option>
        <option value="interest">📊 Interest Only</option>
        <option value="both">💰+📊 Both</option>
      </select>
    </div>
    <div class="frow"><label>Method</label>
      <select id="fin-pay-method">
        <option value="cash">💵 Cash</option>
        <option value="credit_card">💳 Card</option>
        <option value="bank">🏦 Bank</option>
        <option value="upi">📱 UPI</option>
      </select>
    </div>
  </div>
  <div class="frow"><label>Note (optional)</label><input id="fin-pay-note" placeholder="e.g. Partial payment, Interest for March…"></div>
  <div class="mfoot">
    <button class="btn-ghost" onclick="closeFinPayModal()">Cancel</button>
    <button class="btn" onclick="submitFinPayModal()">💾 Record Payment</button>
  </div>
</div>
</div>

<div class="overlay" id="rt-group-modal">
<div class="modal" style="max-width:480px">
  <div class="mhead">
    <h2 id="rt-group-modal-title">New Routine</h2>
    <button class="mclose" onclick="closeRoutineGroupModal()">✕</button>
  </div>
  <input type="hidden" id="rt-group-edit-id">
  <div class="frow"><label>Routine Name *</label><input id="rt-group-name" placeholder="e.g. Morning Routine"></div>
  <div class="frow"><label>Icon</label>
    <div class="rt-icon-picker" id="rt-icon-picker">
      <span class="rt-icon-opt selected" onclick="selectIcon(this,'🌅')">🌅</span>
      <span class="rt-icon-opt" onclick="selectIcon(this,'📈')">📈</span>
      <span class="rt-icon-opt" onclick="selectIcon(this,'🌙')">🌙</span>
      <span class="rt-icon-opt" onclick="selectIcon(this,'💪')">💪</span>
      <span class="rt-icon-opt" onclick="selectIcon(this,'📚')">📚</span>
      <span class="rt-icon-opt" onclick="selectIcon(this,'🧘')">🧘</span>
      <span class="rt-icon-opt" onclick="selectIcon(this,'🏃')">🏃</span>
      <span class="rt-icon-opt" onclick="selectIcon(this,'🍎')">🍎</span>
      <span class="rt-icon-opt" onclick="selectIcon(this,'💼')">💼</span>
      <span class="rt-icon-opt" onclick="selectIcon(this,'🎯')">🎯</span>
    </div>
    <input type="hidden" id="rt-group-icon" value="🌅">
  </div>
  <div class="frow"><label>Color</label>
    <select id="rt-group-color">
      <option value="blue">🔵 Blue</option>
      <option value="green">🟢 Green</option>
      <option value="purple">🟣 Purple</option>
      <option value="yellow">🟡 Yellow</option>
      <option value="red">🔴 Red</option>
    </select>
  </div>
  <div class="mfoot">
    <button class="btn-ghost" onclick="closeRoutineGroupModal()">Cancel</button>
    <button class="btn" onclick="saveRoutineGroup()">Save Routine</button>
  </div>
</div>
</div>

<!-- -- ROUTINE TASK MODAL -------------------------- -->
<div class="overlay" id="rt-task-modal">
<div class="modal" style="max-width:460px">
  <div class="mhead">
    <h2 id="rt-task-modal-title">Add Task</h2>
    <button class="mclose" onclick="closeRoutineTaskModal()">✕</button>
  </div>
  <input type="hidden" id="rt-task-edit-id">
  <input type="hidden" id="rt-task-group-id">
  <div class="frow"><label>Task Name *</label><input id="rt-task-name" placeholder="e.g. Workout, Check market"></div>
  <div class="frow"><label>Time (optional)</label><input id="rt-task-time" type="time"></div>
  <div class="frow"><label>Frequency</label>
    <select id="rt-task-freq" onchange="toggleWeekdays()">
      <option value="daily">🔁 Daily</option>
      <option value="weekly">📅 Weekly (select days)</option>
    </select>
  </div>
  <div class="frow" id="rt-weekdays-row" style="display:none">
    <label>Days</label>
    <div class="rt-day-picker">
      <span class="rt-day-opt" data-day="Mon">Mon</span>
      <span class="rt-day-opt" data-day="Tue">Tue</span>
      <span class="rt-day-opt" data-day="Wed">Wed</span>
      <span class="rt-day-opt" data-day="Thu">Thu</span>
      <span class="rt-day-opt" data-day="Fri">Fri</span>
      <span class="rt-day-opt" data-day="Sat">Sat</span>
      <span class="rt-day-opt" data-day="Sun">Sun</span>
    </div>
  </div>
  <div class="mfoot">
    <button class="btn-ghost" onclick="closeRoutineTaskModal()">Cancel</button>
    <button class="btn" onclick="saveRoutineTask()">Save Task</button>
  </div>
</div>
</div>

<!-- -- TRADE MODAL -------------------------------- -->
<div class="overlay" id="trade-modal-overlay">
<div class="modal" style="max-width:580px;max-height:90vh;overflow-y:auto">
  <div class="mhead">
    <h2 id="trade-modal-heading">Log New Trade</h2>
    <button class="mclose" onclick="closeTradeModal()">✕</button>
  </div>
  <input type="hidden" id="trade-edit-id">

  <!-- Row 1: Symbol + Date + Instrument -->
  <div style="display:grid;grid-template-columns:1fr 1fr 1fr;gap:12px">
    <div class="frow"><label>Symbol *</label><input id="tj-symbol" placeholder="e.g. NIFTY"></div>
    <div class="frow"><label>Date *</label><input id="tj-date" type="date"></div>
    <div class="frow"><label>Instrument *</label>
      <select id="tj-instrument" onchange="onInstrumentChange()">
        <option value="equity">📊 Equity</option>
        <option value="futures">📉 Futures</option>
        <option value="options">🎯 Options</option>
      </select>
    </div>
  </div>

  <!-- ── EQUITY / FUTURES SECTION ── -->
  <div id="tj-eq-section">
    <div style="display:grid;grid-template-columns:1fr 1fr;gap:12px">
      <div class="frow"><label>Type *</label>
        <select id="tj-type">
          <option value="BUY">📈 BUY (Long)</option>
          <option value="SELL">📉 SELL (Short)</option>
        </select>
      </div>
      <div class="frow"><label>Quantity</label><input id="tj-qty" type="number" placeholder="e.g. 50"></div>
      <div class="frow"><label>Entry Price *</label><input id="tj-entry" type="number" step="0.01" placeholder="₹0.00"></div>
      <div class="frow"><label>Exit Price</label><input id="tj-exit" type="number" step="0.01" placeholder="₹0.00"></div>
      <div class="frow"><label>Stop Loss</label><input id="tj-sl" type="number" step="0.01" placeholder="₹0.00"></div>
      <div class="frow"><label>Target</label><input id="tj-target" type="number" step="0.01" placeholder="₹0.00"></div>
    </div>
  </div>

  <!-- ── OPTIONS MULTI-LEG SECTION ── -->
  <div id="tj-opt-section" style="display:none">
    <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:10px">
      <span style="font-size:11px;font-weight:700;text-transform:uppercase;letter-spacing:0.8px;color:var(--muted)">Option Legs</span>
      <button class="btn" style="padding:5px 12px;font-size:12px" onclick="addLeg()">+ Add Leg</button>
    </div>
    <div id="tj-legs-container"></div>
    <!-- Multi-leg P&L breakdown -->
    <div id="tj-legs-pnl" style="display:none;margin-top:8px;background:#f8faff;border:1px solid #dde4f5;border-radius:10px;padding:12px 14px">
      <div id="tj-legs-pnl-rows"></div>
      <div style="border-top:1px solid #dde4f5;margin-top:8px;padding-top:8px;display:flex;justify-content:space-between;align-items:center">
        <span style="font-size:13px;font-weight:700;color:#374151">Combined P&amp;L</span>
        <span id="tj-legs-total" style="font-size:15px;font-weight:700"></span>
      </div>
    </div>
  </div>

  <!-- Trade Mode -->
  <div class="frow" style="margin-top:4px"><label>Trade Mode</label>
    <div style="display:flex;gap:8px">
      <label style="display:flex;align-items:center;gap:6px;cursor:pointer;font-size:13px;font-weight:600">
        <input type="radio" name="tj-mode-radio" id="tj-mode-actual-radio" value="actual" checked style="accent-color:#059669;width:16px;height:16px">
        <span style="color:#059669">✅ Actual</span>
      </label>
      <label style="display:flex;align-items:center;gap:6px;cursor:pointer;font-size:13px;font-weight:600">
        <input type="radio" name="tj-mode-radio" id="tj-mode-dummy-radio" value="dummy" style="accent-color:#7c3aed;width:16px;height:16px">
        <span style="color:#7c3aed">🧪 Dummy</span>
      </label>
    </div>
  </div>

  <!-- Status -->
  <div class="frow" style="margin-top:4px"><label>Status</label>
    <select id="tj-status">
      <option value="open">⏳ Open</option>
      <option value="win">✅ Win</option>
      <option value="loss">❌ Loss</option>
    </select>
  </div>

  <div class="frow"><label>Notes / Reasoning</label>
    <textarea id="tj-notes" placeholder="Why did you take this trade? What did you learn?" style="min-height:80px"></textarea>
  </div>

  <!-- Equity/Futures single-leg P&L preview -->
  <div class="tj-pnl-preview" id="tj-pnl-preview" style="display:none">
    <span>Estimated P&amp;L:</span>
    <span id="tj-pnl-val" style="font-weight:700;font-size:15px"></span>
  </div>

  <div class="mfoot">
    <button class="btn-ghost" onclick="closeTradeModal()">Cancel</button>
    <button class="btn" onclick="saveTrade()">💾 Save Trade</button>
  </div>
</div>
</div>

<!-- -- FLOATING ACTION BUTTON -------------------- -->

<!-- -- ADD/EDIT MODAL ---------------------------- -->
<div class="overlay" id="modal-overlay">
<div class="modal with-preview" id="main-modal">
  <!-- LEFT: Form column -->
  <div class="modal-form-col">
    <div class="mhead">
      <h2 id="modal-heading">Add New</h2>
      <button class="mclose" onclick="closeModal()">✕</button>
    </div>

    <!-- 2. Visual tab toggle -->
    <div class="type-tog">
      <button class="tt active" id="tt-note" onclick="switchType('note')">📝 Note</button>
      <button class="tt" id="tt-reminder" onclick="switchType('reminder')">⏰ Reminder</button>
    </div>

    <!-- Purpose description -->
    <div id="type-desc-note" class="type-desc">
      <span class="type-desc-icon">📝</span>
      <div>
        <strong>Note</strong> - Write something down to remember later.<br>
        <span>No due date. Just stored and visible on your dashboard.</span>
      </div>
    </div>
    <div id="type-desc-reminder" class="type-desc" style="display:none">
      <span class="type-desc-icon">⏰</span>
      <div>
        <strong>Reminder</strong> - Something you need to act on by a specific time.<br>
        <span>You'll receive an <strong>email alert</strong> when it's due.</span>
      </div>
    </div>

    <input type="hidden" id="edit-id">

    <!-- Shared fields -->
    <div class="frow"><label>Title *</label><input id="f-title" placeholder="Enter a title..." oninput="scheduleAutosave();updatePreview()"></div>
    <!-- 5. Taller description box -->
    <div class="frow"><label>Description</label><textarea id="f-body" placeholder="Add details..." style="min-height:140px" oninput="scheduleAutosave();updatePreview()"></textarea></div>

    <!-- Tag chip input -->
    <div class="frow" style="position:relative">
      <label>Tags</label>
      <input type="hidden" id="f-tags">
      <div class="tag-chip-wrap" id="tag-chip-wrap" onclick="document.getElementById('tag-chip-input').focus()">
        <span id="tag-chips-display"></span>
        <input class="tag-chip-input" id="tag-chip-input" placeholder="Type a tag, press Enter…"
          onkeydown="handleTagKey(event)" oninput="showTagSuggestions(this.value)">
      </div>
      <div class="tag-suggestions" id="tag-suggestions"></div>
    </div>

    <!-- Category (shared for notes & reminders) -->
    <div class="frow"><label>Category</label>
      <select id="f-category">
        <option value="personal">👤 Personal</option>
        <option value="official">💼 Official</option>
      </select>
    </div>

    <!-- Note-only fields -->
    <div id="row-color" class="frow">
      <label>Card Colour</label>
      <input type="hidden" id="f-color" value="default">
      <div class="color-swatches" id="color-swatches">
        <div class="cswatch cswatch-default selected" data-color="default" onclick="selectSwatch(this)" title="Default"></div>
        <div class="cswatch" data-color="blue"   onclick="selectSwatch(this)" style="background:#60a5fa" title="Blue"></div>
        <div class="cswatch" data-color="green"  onclick="selectSwatch(this)" style="background:#4ade80" title="Green"></div>
        <div class="cswatch" data-color="yellow" onclick="selectSwatch(this)" style="background:#fbbf24" title="Yellow"></div>
        <div class="cswatch" data-color="red"    onclick="selectSwatch(this)" style="background:#f87171" title="Red"></div>
        <div class="cswatch" data-color="purple" onclick="selectSwatch(this)" style="background:#c084fc" title="Purple"></div>
      </div>
    </div>

    <!-- 7. Pin toggle (note only) -->
    <div id="row-pin" class="frow" style="display:flex;align-items:center;gap:10px;margin-bottom:13px">
      <label style="margin:0;flex:1">Pin to top of dashboard</label>
      <button type="button" class="pin-btn" id="pin-btn" onclick="togglePin()">📌 Pin</button>
      <input type="hidden" id="f-pinned" value="false">
    </div>

    <!-- Reminder-only fields -->
    <div id="row-due" class="frow" style="display:none">
      <label>📅 Due Date & Time *</label>
      <div style="display:grid;grid-template-columns:1fr auto auto;gap:8px;align-items:center">
        <input id="f-due-date" type="date" style="width:100%">
        <select id="f-due-hour" style="padding:9px 8px;background:var(--bg);border:1px solid var(--border2);border-radius:8px;color:var(--text);font-family:'Inter',sans-serif;font-size:13px;outline:none;cursor:pointer">
          HOUR_OPTIONS_PLACEHOLDER
        </select>
        <select id="f-due-min" style="padding:9px 8px;background:var(--bg);border:1px solid var(--border2);border-radius:8px;color:var(--text);font-family:'Inter',sans-serif;font-size:13px;outline:none;cursor:pointer">
          MIN_OPTIONS_PLACEHOLDER
        </select>
      </div>
      <div style="font-size:11px;color:var(--muted);margin-top:4px">Format: Date · Hour (00-23) · Minute (00-59)</div>
    </div>
    <div id="row-repeat" class="frow" style="display:none">
      <label>🔁 Repeat</label>
      <select id="f-repeat">
        <option value="none">No repeat</option>
        <option value="daily">Daily</option>
        <option value="weekly">Weekly</option>
        <option value="monthly">Monthly</option>
      </select>
    </div>
    <div id="row-remind-before" class="frow" style="display:none">
      <label>🔔 Remind me before</label>
      <select id="f-remind-before" style="padding:9px 8px;background:var(--bg);border:1px solid var(--border2);border-radius:8px;color:var(--text);font-family:'Inter',sans-serif;font-size:13px;outline:none;cursor:pointer;width:100%">
        <option value="10">10 minutes before</option>
        <option value="30" selected>30 minutes before</option>
        <option value="60">1 hour before</option>
        <option value="120">2 hours before</option>
        <option value="480">8 hours before</option>
        <option value="1440">1 day before</option>
        <option value="2880">2 days before</option>
        <option value="10080">1 week before</option>
      </select>
    </div>
    <div id="row-priority" class="frow" style="display:none">
      <label>⭐ Priority</label>
      <div style="display:flex;gap:8px">
        <label style="display:flex;align-items:center;gap:5px;cursor:pointer;font-size:13px;font-weight:600">
          <input type="radio" name="f-priority" value="high" style="accent-color:#dc2626">
          <span class="prio-badge prio-high">▲ High</span>
        </label>
        <label style="display:flex;align-items:center;gap:5px;cursor:pointer;font-size:13px;font-weight:600">
          <input type="radio" name="f-priority" value="medium" checked style="accent-color:#ca8a04">
          <span class="prio-badge prio-medium">— Med</span>
        </label>
        <label style="display:flex;align-items:center;gap:5px;cursor:pointer;font-size:13px;font-weight:600">
          <input type="radio" name="f-priority" value="low" style="accent-color:#16a34a">
          <span class="prio-badge prio-low">▼ Low</span>
        </label>
      </div>
    </div>

    <div class="mfoot">
      <span class="autosave-lbl" id="autosave-lbl">✓ Saved</span>
      <button class="btn-ghost" onclick="closeModal()">Cancel</button>
      <button class="btn" onclick="saveItem()" id="modal-save-btn">💾 Save Note</button>
    </div>
  </div>

  <!-- RIGHT: 6. Live preview panel (note only) -->
  <div class="modal-preview-col" id="modal-preview-col">
    <div class="modal-preview-label">✨ Live Preview</div>
    <div class="preview-card" id="preview-card">
      <div class="preview-eyebrow">📝 Note</div>
      <div class="preview-title" id="preview-title" style="color:var(--muted);font-style:italic;font-weight:400">Your title will appear here…</div>
      <div class="preview-body" id="preview-body" style="color:var(--muted);font-style:italic"></div>
      <div class="preview-tags" id="preview-tags"></div>
      <div class="preview-meta">
        <span class="preview-date" id="preview-date"></span>
        <span id="preview-pin-badge"></span>
      </div>
    </div>
    <!-- 8. timestamps info -->
    <div id="preview-timestamps" style="font-size:11px;color:var(--muted);line-height:1.8;margin-top:4px"></div>
  </div>
</div>
</div>

<!-- -- SHOP MODAL -------------------------------- -->
<div class="overlay" id="shop-modal-overlay">
<div class="modal" style="max-width:380px">
  <div class="mhead">
    <h2 id="shop-modal-title">Add Shop</h2>
    <button class="mclose" onclick="shopCloseModal()">✕</button>
  </div>
  <input type="hidden" id="shop-edit-id">
  <div class="frow"><label>Shop Name</label><input id="shop-name-input" placeholder="e.g. Reliance Fresh, DMart, Amazon..."></div>
  <div class="frow"><label>Icon (emoji)</label>
    <div style="display:flex;gap:6px;flex-wrap:wrap;margin-bottom:8px" id="shop-icon-grid"></div>
    <input id="shop-icon-input" placeholder="Or type custom emoji" maxlength="2" style="width:80px">
  </div>
  <div class="mfoot" style="display:flex;gap:8px;justify-content:flex-end;margin-top:12px">
    <button class="btn-ghost" onclick="shopCloseModal()">Cancel</button>
    <button class="btn" onclick="shopSave()">Save Shop</button>
  </div>
</div>
</div>

<!-- -- SETTINGS PANEL ---------------------------- -->
<div id="settings-panel">
<div class="settings-modal">
  <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:22px">
    <h2 style="margin:0">⚙️ Settings</h2>
    <button class="mclose" onclick="closeSettings()">✕</button>
  </div>

  <!-- THEME -->
  <div class="settings-section-title">🎨 Theme</div>
  <div class="theme-grid">

    <!-- Rose Quartz (DEFAULT) -->
    <div class="theme-card selected" id="theme-btn-rose" onclick="applyTheme('rose')">
      <div class="theme-preview">
        <div class="tp-side" style="background:#f4ecf0;border-right:1px solid #dcc8d4"></div>
        <div class="tp-main" style="background:#fdf8fa">
          <div class="tp-line" style="background:#b06090;width:60%"></div>
          <div class="tp-line" style="background:#dcc8d4;width:90%"></div>
          <div class="tp-line" style="background:#c8a8b8;width:70%"></div>
        </div>
      </div>
      <div class="theme-name">&#x1F338; Rose Quartz</div>
    </div>

    <!-- Warm Parchment -->
    <div class="theme-card" id="theme-btn-cream" onclick="applyTheme('cream')">
      <div class="theme-preview">
        <div class="tp-side" style="background:#e8dcc8;border-right:1px solid #c8b48a"></div>
        <div class="tp-main" style="background:#faf6ef">
          <div class="tp-line" style="background:#8b5e2a;width:60%"></div>
          <div class="tp-line" style="background:#c8b48a;width:90%"></div>
          <div class="tp-line" style="background:#c8b48a;width:70%"></div>
        </div>
      </div>
      <div class="theme-name">&#x1FAB5; Warm Parchment</div>
    </div>

    <!-- Soft Beige -->
    <div class="theme-card" id="theme-btn-beige" onclick="applyTheme('beige')">
      <div class="theme-preview">
        <div class="tp-side" style="background:#ede6d8;border-right:1px solid #d4c8b0"></div>
        <div class="tp-main" style="background:#f5f0e8">
          <div class="tp-line" style="background:#7c5cbf;width:60%"></div>
          <div class="tp-line" style="background:#d4c8b0;width:90%"></div>
          <div class="tp-line" style="background:#b8a888;width:70%"></div>
        </div>
      </div>
      <div class="theme-name">&#x1F338; Soft Beige</div>
    </div>

    <!-- Arctic Silver -->
    <div class="theme-card" id="theme-btn-arctic" onclick="applyTheme('arctic')">
      <div class="theme-preview">
        <div class="tp-side" style="background:#e4e8f0;border-right:1px solid #c8ccd8"></div>
        <div class="tp-main" style="background:#f0f2f8">
          <div class="tp-line" style="background:#4a5a80;width:60%"></div>
          <div class="tp-line" style="background:#c8ccd8;width:90%"></div>
          <div class="tp-line" style="background:#a8b0c0;width:70%"></div>
        </div>
      </div>
      <div class="theme-name">&#x2744;&#xFE0F; Arctic Silver</div>
    </div>

    <!-- Ocean Depths -->
    <div class="theme-card" id="theme-btn-ocean" onclick="applyTheme('ocean')">
      <div class="theme-preview" style="background:#060e12">
        <div class="tp-side" style="background:#0a1a1e;border-right:1px solid #153038"></div>
        <div class="tp-main" style="background:#060e12">
          <div class="tp-line" style="background:#00d2b4;width:60%"></div>
          <div class="tp-line" style="background:#153038;width:90%"></div>
          <div class="tp-line" style="background:#1a4050;width:70%"></div>
        </div>
      </div>
      <div class="theme-name" style="background:#0a1a1e;color:#00d2b4">&#x1F30A; Ocean Depths</div>
    </div>

    <!-- Midnight Slate -->
    <div class="theme-card" id="theme-btn-midnight" onclick="applyTheme('midnight')">
      <div class="theme-preview" style="background:#141920">
        <div class="tp-side" style="background:#1a2130;border-right:1px solid #252e40"></div>
        <div class="tp-main" style="background:#141920">
          <div class="tp-line" style="background:#e8a84a;width:60%"></div>
          <div class="tp-line" style="background:#252e40;width:90%"></div>
          <div class="tp-line" style="background:#304060;width:70%"></div>
        </div>
      </div>
      <div class="theme-name" style="background:#1a2130;color:#e8a84a">&#x1F311; Midnight Slate</div>
    </div>

    <!-- Obsidian Ember -->
    <div class="theme-card" id="theme-btn-ember" onclick="applyTheme('ember')">
      <div class="theme-preview" style="background:#0f0d0b">
        <div class="tp-side" style="background:#161210;border-right:1px solid #2a2018"></div>
        <div class="tp-main" style="background:#0f0d0b">
          <div class="tp-line" style="background:#d4724a;width:60%"></div>
          <div class="tp-line" style="background:#2a2018;width:90%"></div>
          <div class="tp-line" style="background:#3a2a1a;width:70%"></div>
        </div>
      </div>
      <div class="theme-name" style="background:#161210;color:#d4724a">&#x1F525; Obsidian Ember</div>
    </div>

  </div>

  <!-- FIREBASE AUTH -->
  <div class="settings-section-title" style="margin-top:24px">🔗 Cloud Sync</div>
  <div id="auth-section">
    <div id="auth-signed-out">
      <p style="font-size:12px;color:var(--muted);margin-bottom:14px;line-height:1.6">
        Sign in with your Google account to securely sync your notes across devices.
      </p>
      <button class="btn" onclick="firebaseSignIn()" style="display:flex;align-items:center;gap:8px;width:100%;justify-content:center">
        <span style="font-size:16px">🔐</span> Sign in with Google
      </button>
    </div>
    <div id="auth-signed-in" style="display:none">
      <div style="display:flex;align-items:center;gap:10px;background:var(--s2);border-radius:10px;padding:12px 14px;margin-bottom:12px">
        <img id="auth-avatar" src="" style="width:32px;height:32px;border-radius:50%;border:2px solid var(--accent)" onerror="this.style.display='none'">
        <div style="flex:1;min-width:0">
          <div id="auth-name" style="font-size:13px;font-weight:600;color:var(--text)"></div>
          <div id="auth-email" style="font-size:11px;color:var(--muted);overflow:hidden;text-overflow:ellipsis;white-space:nowrap"></div>
        </div>
        <span style="font-size:11px;color:var(--green);font-weight:600">● Synced</span>
      </div>
      <button class="btn-ghost" onclick="firebaseSignOut()" style="width:100%;justify-content:center;display:flex">Sign Out</button>
    </div>
  </div>

  <div style="display:flex;gap:8px;justify-content:flex-end;margin-top:18px;padding-top:14px;border-top:1px solid var(--border)">
    <button class="btn-ghost" onclick="closeSettings()">Close</button>
  </div>

  <!-- DAYBOOK PIN -->
  <div class="settings-section-title" style="margin-top:24px">🔐 Daybook, Investments & Important Dates PIN Lock</div>
  <p style="font-size:12px;color:var(--muted);margin-bottom:14px;line-height:1.6">
    Set a 4-digit PIN to lock your Daybook, Investments, and Important Dates. Leave blank to disable the lock. PIN is stored only in your browser.
  </p>
  <!-- Current PIN (only shown when a PIN is already set) -->
  <div id="cfg-db-pin-current-row" class="frow" style="display:none;margin-bottom:12px">
    <label>Current PIN (required to change or remove)</label>
    <input id="cfg-db-pin-current" type="password" maxlength="4" pattern="[0-9]*" inputmode="numeric" placeholder="enter current PIN" style="letter-spacing:6px;font-size:18px;max-width:200px">
  </div>
  <div style="display:flex;gap:10px;align-items:flex-end;flex-wrap:wrap">
    <div class="frow" style="flex:1;min-width:140px;margin-bottom:0">
      <label id="cfg-db-pin-label">New PIN (4 digits)</label>
      <input id="cfg-db-pin" type="password" maxlength="4" pattern="[0-9]*" inputmode="numeric" placeholder="e.g. 1234" style="letter-spacing:6px;font-size:18px">
    </div>
    <div class="frow" style="flex:1;min-width:140px;margin-bottom:0">
      <label>Confirm PIN</label>
      <input id="cfg-db-pin2" type="password" maxlength="4" pattern="[0-9]*" inputmode="numeric" placeholder="repeat PIN" style="letter-spacing:6px;font-size:18px">
    </div>
    <button class="btn" onclick="dbSavePin()" style="white-space:nowrap;margin-bottom:2px">Save PIN</button>
    <button class="btn-ghost" onclick="dbClearPin()" style="white-space:nowrap;margin-bottom:2px">Remove Lock</button>
  </div>
  <div id="db-pin-settings-msg" style="font-size:12px;margin-top:8px;min-height:16px"></div>
</div>
</div>

<div id="toast"></div>

<script>
let DATA={notes:[],reminders:[],stickies:[],archived:[],trades:[],routines:[],routine_logs:[],tasknotes:[],finance:[],note_folders:[],rem_lists:[],daybook:[],shopping:[],investments:[],important_dates:[]};
let dataLoaded=false; // guard: prevents any save before data is fully loaded
// ── Real-time sync state ─────────────────────────────────────────────────────
let _firestoreUnsubscribe=null;    // unsubscribe fn returned by onSnapshot
let _isSavingToFirestore=false;    // true while WE are writing (suppress echo)
let _realtimeListenerActive=false; // false on first snapshot, true thereafter
// ─────────────────────────────────────────────────────────────────────────────
let currentType='note';

/* -- THEME --------------------------------------- */
const THEMES=['rose','cream','beige','arctic','ocean','midnight','ember'];

function applyTheme(t){
  document.body.className='theme-'+t;
  localStorage.setItem('mynotes_theme',t);
  THEMES.forEach(k=>{
    const el=document.getElementById('theme-btn-'+k);
    if(el) el.classList.toggle('selected',k===t);
  });
  // Destroy pie chart so it re-renders with new theme colors
  if(typeof _invChartInstance!=='undefined' && _invChartInstance){_invChartInstance.destroy();_invChartInstance=null;}
  setTimeout(closeSettings, 300);
}

/* -- FIREBASE CONFIG ----------------------------- */
const firebaseConfig = {
  apiKey: "FIREBASE_API_KEY_PLACEHOLDER",
  authDomain: "FIREBASE_AUTH_DOMAIN_PLACEHOLDER",
  projectId: "FIREBASE_PROJECT_ID_PLACEHOLDER",
  storageBucket: "FIREBASE_STORAGE_BUCKET_PLACEHOLDER",
  messagingSenderId: "FIREBASE_MESSAGING_SENDER_ID_PLACEHOLDER",
  appId: "FIREBASE_APP_ID_PLACEHOLDER"
};
firebase.initializeApp(firebaseConfig);
const fbAuth = firebase.auth();
const fbDb   = firebase.firestore();

/* -- AUTH ---------------------------------------- */
function updateAuthUI(user){
  const tbAvatar=document.getElementById('topbar-avatar');
  const tbInitials=document.getElementById('topbar-initials');
  if(user){
    document.getElementById('auth-signed-out').style.display='none';
    document.getElementById('auth-signed-in').style.display='block';
    document.getElementById('auth-name').textContent=user.displayName||'';
    document.getElementById('auth-email').textContent=user.email||'';
    if(user.photoURL) {
      const av=document.getElementById('auth-avatar');
      av.src=user.photoURL; av.style.display='block';
      if(tbAvatar) tbAvatar.innerHTML=`<img src="${user.photoURL}" alt="">`;
    } else if(tbInitials){
      const names=(user.displayName||'').split(' ');
      tbInitials.textContent=(names[0]?names[0][0]:'')+(names[1]?names[1][0]:'');
    }
  } else {
    document.getElementById('auth-signed-out').style.display='block';
    document.getElementById('auth-signed-in').style.display='none';
    if(tbInitials) tbInitials.textContent='--';
  }
}
async function firebaseSignIn(){
  try{
    await fbAuth.signInWithPopup(new firebase.auth.GoogleAuthProvider());
  }catch(e){
    if(e.code!=='auth/popup-closed-by-user') toast('Sign-in failed: '+e.message,'error');
  }
}
async function firebaseSignOut(){
  if(!confirm('Sign out? Local unsaved changes will be lost.')) return;
  // Stop the real-time listener before signing out
  if(_firestoreUnsubscribe){ _firestoreUnsubscribe(); _firestoreUnsubscribe=null; }
  _realtimeListenerActive=false;
  _isSavingToFirestore=false;
  await fbAuth.signOut();
  DATA={notes:[],reminders:[],stickies:[],archived:[],trades:[],routines:[],routine_logs:[],tasknotes:[],finance:[],note_folders:[],rem_lists:[],daybook:[],shopping:[],investments:[],important_dates:[]};
  dataLoaded=false;
  renderAll();
  toast('Signed out','success');
}

function openSettings(){
  updateAuthUI(fbAuth.currentUser);
  document.getElementById('settings-panel').classList.add('open');
  // Refresh PIN lock UI — shows/hides the "Current PIN" field based on whether a PIN is set,
  // and clears any stale values from a previous session.
  if(typeof dbRefreshPinSettingsUI === 'function') dbRefreshPinSettingsUI();
}
function closeSettings(){document.getElementById('settings-panel').classList.remove('open')}

/* -- FIRESTORE LOAD/SAVE ---- */
/* Firestore has a 1MB doc limit. We store DATA as a single doc users/{uid}.
   Arrays are stored as sub-keys so Firestore can handle them. */

async function loadFromFirebase(){
  const user=fbAuth.currentUser;
  if(!user){openSettings();return;}

  // Tear down any previous listener (e.g. after sign-out / re-sign-in)
  if(_firestoreUnsubscribe){ _firestoreUnsubscribe(); _firestoreUnsubscribe=null; }
  _realtimeListenerActive=false;

  setSyncing(true,'Loading...');

  // onSnapshot fires immediately with current data AND every time any device
  // writes, so all open browsers update without a manual page refresh.
  _firestoreUnsubscribe = fbDb.collection('users').doc(user.uid).onSnapshot(async (doc)=>{

    // Skip the echo of our own saves to prevent an infinite loop.
    if(_isSavingToFirestore){ return; }

    setSyncing(true, _realtimeListenerActive ? 'Updating...' : 'Loading...');

    try{
    if(doc.exists){
      const remote=doc.data();
      // Restore DATA from Firestore (each key is stored as a JSON string to avoid Firestore nested-object limits)
      try{ DATA=JSON.parse(remote.payload||'{}'); }catch(e){ DATA=remote; }
    }
    dataLoaded=true;
    let needsRepair = false;
    const _beforeRepair = JSON.stringify(DATA);

    if(!DATA.trades)        DATA.trades        = [];
    if(!DATA.routines)      DATA.routines      = [];
    if(!DATA.routine_logs)  DATA.routine_logs  = [];
    if(!DATA.tasknotes)     DATA.tasknotes     = [];
    if(!DATA.finance)       DATA.finance       = [];
    if(!DATA.stickies)      DATA.stickies      = [];
    if(!DATA.archived)      DATA.archived      = [];
    if(!DATA.daybook)       DATA.daybook       = [];
    if(!DATA.shopping)      DATA.shopping      = [];
    if(!DATA.investments)   DATA.investments   = [];
    if(!DATA.notes)         DATA.notes         = [];
    if(!DATA.reminders)     DATA.reminders     = [];
    if(!DATA.important_dates) DATA.important_dates = [];

    // ── EMOJI SANITIZER ──
    function isValidStr(s){ return typeof s==='string' && !/[\u0080-\u00ff]{2,}/.test(s); }
    function safeIcon(icon, fallback){ return isValidStr(icon) ? icon : fallback; }
    function safeName(name, fallback){ return (typeof name==='string' && name.trim() && isValidStr(name)) ? name : fallback; }

    if(Array.isArray(DATA.routines)){
      DATA.routines = DATA.routines.map((g,i)=>{
        const fixed = {...g};
        if(!isValidStr(g.icon)||!g.icon){ fixed.icon='🔁'; needsRepair=true; }
        if(!isValidStr(g.name)||!g.name){ fixed.name='Routine '+(i+1); needsRepair=true; }
        return fixed;
      });
    }
    if(!Array.isArray(DATA.note_folders)||!DATA.note_folders.length||
       typeof DATA.note_folders[0]!=='object'||!DATA.note_folders[0]?.id){
      DATA.note_folders = [{id:'personal',name:'Personal',icon:'👤'},{id:'official',name:'Official',icon:'💼'}];
      needsRepair = true;
    } else {
      DATA.note_folders = DATA.note_folders.map(f=>({...f, icon: safeIcon(f.icon,'📁'), name: safeName(f.name,'Folder')}));
    }
    if(!Array.isArray(DATA.rem_lists)||!DATA.rem_lists.length||
       typeof DATA.rem_lists[0]!=='object'||!DATA.rem_lists[0]?.id){
      DATA.rem_lists = [{id:'personal',name:'Personal',icon:'👤'},{id:'official',name:'Official',icon:'💼'}];
      needsRepair = true;
    } else {
      DATA.rem_lists = DATA.rem_lists.map(l=>({...l, icon: safeIcon(l.icon,'🔵'), name: safeName(l.name,'List')}));
    }
    if(Array.isArray(DATA.stickies)){
      DATA.stickies = DATA.stickies.map(s=>({...s, text: isValidStr(s.text)?s.text:'', bg: isValidStr(s.bg)?s.bg:'#fde68a'}));
    }

    TRADES        = DATA.trades;
    ROUTINES      = DATA.routines;
    ROUTINE_LOGS  = DATA.routine_logs;
    TASKNOTES     = DATA.tasknotes;
    FINANCE       = DATA.finance;

    try{
      const finBackup = JSON.parse(localStorage.getItem('fin_backup')||'[]');
      if(finBackup.length > FINANCE.length){ FINANCE = finBackup; DATA.finance = FINANCE; }
    }catch(e){}

    renderAll();
    updateJournalCount();
    updateRoutineCount();
    renderTaskNotes();
    renderFinance();
    initSticky();
    dbUpdateCounts();
    dbRender();
    shopRender();
    invRender();
    impRenderDashboard();
    impRenderPage();
    updateImpDatesCount();

    if(needsRepair && JSON.stringify(DATA) !== _beforeRepair){
      await saveToFirebase();
      // saveToFirebase sets _isSavingToFirestore so its echo is skipped
      if(!_realtimeListenerActive) toast('Data repaired & synced ✓','success');
    } else {
      if(!_realtimeListenerActive){
        toast('Loaded ✓','success');
      } else {
        // Change came from another device
        toast('🔄 Synced from another device','success');
      }
    }
    _realtimeListenerActive=true;

    }catch(e){
      dataLoaded=true;
      initSticky();
      renderAll();
      toast('Load failed: '+e.message,'error');
    }
    setSyncing(false,'Synced');

  }, (err)=>{
    // Listener-level error (network lost, permissions revoked, etc.)
    console.error('Firestore listener error:',err);
    _isSavingToFirestore=false;
    setSyncing(false,'Error');
    toast('Sync error: '+err.message,'error');
  }); // end onSnapshot
}

async function saveToFirebase(){
  if(!dataLoaded){ console.warn('saveToFirebase blocked: data not loaded yet'); return false; }
  const user=fbAuth.currentUser;
  if(!user){toast('Sign in first','error');openSettings();return false;}
  setSyncing(true,'Saving...');
  try{
    if(typeof FINANCE !== 'undefined' && FINANCE.length > 0)       DATA.finance      = [...FINANCE];
    if(typeof TRADES  !== 'undefined' && TRADES.length  > 0)       DATA.trades       = [...TRADES];
    if(typeof TASKNOTES !== 'undefined' && TASKNOTES.length > 0)   DATA.tasknotes    = [...TASKNOTES];
    if(typeof ROUTINES !== 'undefined' && ROUTINES.length > 0)     DATA.routines     = [...ROUTINES];
    if(typeof ROUTINE_LOGS !== 'undefined' && ROUTINE_LOGS.length > 0) DATA.routine_logs = [...ROUTINE_LOGS];
    if(typeof INVESTMENTS !== 'undefined' && INVESTMENTS.length > 0)  DATA.investments  = [...INVESTMENTS];

    // Store as a single JSON string payload to avoid Firestore nested object/array depth limits
    // Suppress the onSnapshot echo of our own write
    _isSavingToFirestore=true;
    await fbDb.collection('users').doc(user.uid).set({
      payload: JSON.stringify(DATA),
      updatedAt: firebase.firestore.FieldValue.serverTimestamp(),
      email: user.email||''
    });
    // Release after 3 s — Firestore echoes back within ~1-2 s
    setTimeout(()=>{ _isSavingToFirestore=false; }, 3000);
    toast('Saved ✓','success');
    setSyncing(false,'Synced');
    return true;
  }catch(e){
    _isSavingToFirestore=false; // always release on error
    toast('Save failed: '+e.message,'error');
    setSyncing(false,'Error');
    return false;
  }
}



function setSyncing(on,label){
  document.getElementById('sdot').className='sdot'+(on?' syncing':'');
  document.getElementById('stext').textContent=label||'Synced';
  // Topbar sync pill
  const tsDot=document.getElementById('topbar-sdot');
  const tsText=document.getElementById('topbar-stext');
  if(tsDot) tsDot.className='topbar-sync-dot'+(on?' syncing':'')+(label==='Error'?' error':'');
  if(tsText) tsText.textContent=label||'Synced';
  if(!on && label!=='Error'){
    const now=new Date();
    const h=now.getHours(),m=now.getMinutes();
    const ampm=h>=12?'PM':'AM', h12=h===0?12:h>12?h-12:h;
    const ts=document.getElementById('sync-time');
    if(ts) ts.textContent=`${h12}:${String(m).padStart(2,'0')} ${ampm}`;
  }
}

/* -- VIEW TOGGLE --------------------------------- */
const viewState={rem:'card',notes:'card'};

function setView(section, mode){
  viewState[section]=mode;
  localStorage.setItem('view_'+section, mode);
  const sec=document.getElementById(section+'-section');
  if(!sec) return;
  sec.classList.toggle('is-list', mode==='list');
  const vc=document.getElementById(section+'-vcard');
  const vl=document.getElementById(section+'-vlist');
  if(vc) vc.classList.toggle('active', mode==='card');
  if(vl) vl.classList.toggle('active', mode==='list');
}

function renderAll(){
  const notes=DATA.notes||[];
  const reminders=DATA.reminders||[];
  TRADES       = DATA.trades       || [];
  ROUTINES     = DATA.routines     || [];
  ROUTINE_LOGS = DATA.routine_logs || [];
  TASKNOTES    = DATA.tasknotes    || [];
  FINANCE      = DATA.finance      || [];
  updateJournalCount();
  updateRoutineCount();
  updateTaskNotesCount();
  updateFinanceCount();
  updateInvestmentsCount();
  updateImpDatesCount();
  const now=new Date();
  const todayStr=localToday();
  const pending=reminders.filter(r=>!r.sent && !isOverdue(r)).length;
  const overdue=reminders.filter(r=>isOverdue(r)).length;
  const sent=reminders.filter(r=>r.sent).length;

  document.getElementById('stat-notes').textContent=notes.length;
  document.getElementById('stat-reminders').textContent=reminders.length;
  document.getElementById('stat-pending').textContent=pending;
  document.getElementById('stat-files').textContent=sent;
  document.getElementById('nav-all').textContent=notes.length+reminders.length;
  document.getElementById('nav-notes').textContent=notes.length;
  document.getElementById('nav-reminders').textContent=reminders.length;
  document.getElementById('nav-pending').textContent=pending;
  document.getElementById('nav-overdue').textContent=overdue;
  document.getElementById('nav-sent').textContent=sent;
  const navSticky=document.getElementById('nav-sticky-count');
  if(navSticky) navSticky.textContent=(DATA.stickies||[]).length;
  const remPill=document.getElementById('rem-pill');
  const notesPill=document.getElementById('notes-pill');
  if(remPill) remPill.textContent=reminders.length;
  if(notesPill) notesPill.textContent=notes.length;
  updateJournalCount();

  const ng=document.getElementById('notes-grid');
  const rg=document.getElementById('reminders-grid');
  const nl=document.getElementById('notes-list');
  const rl=document.getElementById('reminders-list');

  const emptyNote=`<div class="empty-state"><div class="ei">📝</div><p>Start capturing your thoughts</p></div>`;
  const emptyRem=`<div class="empty-state"><div class="ei">⏰</div><p>No reminders — your schedule is clear</p></div>`;

  const sortedNotes=[...notes].reverse().sort((a,b)=>(b.pinned?1:0)-(a.pinned?1:0));
  const sortedRems=[...reminders].reverse();

  // apply category filters
  const filteredNotes = _notesCatFilter==='all' ? sortedNotes
    : sortedNotes.filter(n=>(n.category||'personal')===_notesCatFilter);
  const filteredRems  = _remCatFilter==='all'   ? sortedRems
    : sortedRems.filter(r=>(r.category||'personal')===_remCatFilter);

  if(ng) ng.innerHTML=filteredNotes.length?filteredNotes.map(renderNoteCard).join(''):emptyNote;
  if(rg) rg.innerHTML=filteredRems.length ?filteredRems.map(renderReminderCard).join(''):emptyRem;
  if(nl) nl.innerHTML=filteredNotes.length?filteredNotes.map(renderNoteRow).join(''):emptyNote;
  if(rl) rl.innerHTML=filteredRems.length ?filteredRems.map(renderReminderRow).join(''):emptyRem;
  // refresh Notes page if open
  if(document.getElementById('page-notes')?.style.display!=='none') renderNotesPage();
  // refresh Reminders page if open
  if(document.getElementById('page-reminders')?.style.display!=='none') renderRemindersPage();
  // Update dashboard widgets
  updateDashboardWidgets();
  // Update favicon + PWA badge with latest counts
  updateBadge();
}

const esc=s=>String(s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
const fd=s=>s?String(s).slice(0,10):'';

/* -- CARD RENDERERS ------------------------------ */
function renderNoteCard(n){
  const tags=(n.tags||[]).map(t=>`<span class="ctag">#${esc(t)}</span>`).join('');
  const cl=n.color&&n.color!=='default'?` cl-${n.color}`:'';
  const pinCls=n.pinned?' pinned-card':'';
  const pinBadge=n.pinned?'<span class="pinned-badge">📌 Pinned</span>':'';
  const updatedLine=n.updated&&n.updated!==n.created
    ?`<span class="cdate" style="margin-left:8px;opacity:.7">✏️ ${fd(n.updated)}</span>`:'';
  const bodyPreview=(n.body||'')
    .replace(/!\[[^\]]*\]\((?:data:image\/[^)]+|%%IMGDATA:[^)]+%%)\)/g,'📷 image')
    .slice(0,120);
  return`<div class="ncard${cl}${pinCls}" data-type="note" data-id="${n.id}" onclick="handleCardClick(event,'${n.id}')">
    <div class="ceyebrow"><span class="ctype">📝 Note</span>${pinBadge}</div>
    <div class="ctitle">${esc(n.title)}</div>
    ${bodyPreview?`<div class="cbody" style="font-family:'Inter',sans-serif">${esc(bodyPreview)}</div>`:''}
    ${tags?`<div class="tags-row">${tags}</div>`:''}
    <div class="cmeta">
      <span style="display:flex;align-items:center;gap:4px">
        <span class="cdate">Created ${fd(n.created)}</span>${updatedLine}
      </span>
      <div class="cbtns">
        <button class="cbtn" onclick="event.stopPropagation();editItem('${n.id}')">Edit</button>
        <button class="cbtn del" onclick="event.stopPropagation();deleteItem('${n.id}')">Delete</button>
      </div>
    </div>
  </div>`;
}
function renderReminderCard(r){
  const now=new Date();
  let sc='pending',sl='🔔 Pending';
  try{if(r.sent){sc='sent';sl='✅ Done';}else if(isOverdue(r)){sc='overdue';sl='🔴 Overdue';}}catch{}
  const tags=(r.tags||[]).map(t=>`<span class="ctag">#${esc(t)}</span>`).join('');
  const rep=r.repeat&&r.repeat!=='none'?`<span class="ctag">🔁 ${r.repeat}</span>`:'';
  const prio=r.priority||'medium';
  const prioMap={high:'High',medium:'Med',low:'Low'};
  const prioBadge=`<span class="prio-badge prio-${prio}">${prioMap[prio]||''}</span>`;
  const catIcon=r.category==='official'?'💼':'🏠';
  const catDot=`<span class="cat-dot ${r.category==='official'?'cat-official':'cat-personal'}" title="${r.category||'personal'}"></span>`;
  const doneBtn = !r.sent
    ? `<button class="cbtn done-btn" onclick="event.stopPropagation();markReminderDone('${r.id}')">✅ Done</button>`
    : `<button class="cbtn" onclick="event.stopPropagation();markReminderDone('${r.id}')">↩ Reopen</button>`;
  return`<div class="ncard ${sc}" data-type="reminder" data-id="${r.id}" onclick="handleCardClick(event,'${r.id}')">
    <div class="ceyebrow"><span class="ctype">⏰ Reminder</span><span class="schip ${sc}">${sl}</span>${prioBadge}</div>
    <div class="ctitle">${catDot}<span style="font-size:13px;margin-right:3px">${catIcon}</span>${esc(r.title)}</div>
    ${r.body?`<div class="cbody">${esc(r.body)}</div>`:''}
    <div class="due-row">📅 Due: <strong>${esc(r.due||'')}</strong></div>
    ${(tags||rep)?`<div class="tags-row">${tags}${rep}</div>`:''}
    <div class="cmeta">
      <span class="cdate">${fd(r.created)}</span>
      <div class="cbtns">
        ${doneBtn}
        <button class="cbtn" onclick="event.stopPropagation();editItem('${r.id}')">Edit</button>
        <button class="cbtn del" onclick="event.stopPropagation();deleteItem('${r.id}')">Delete</button>
      </div>
    </div>
  </div>`;
}

/* -- LIST RENDERERS ------------------------------ */
function renderNoteRow(n){
  const cl=n.color&&n.color!=='default'?` cl-${n.color}`:'';
  const tags=(n.tags||[]).slice(0,3).map(t=>`<span class="ctag">#${esc(t)}</span>`).join('');
  return`<div class="lrow" data-type="note" data-id="${n.id}" onclick="handleCardClick(event,'${n.id}')">
    <div class="lrow-accent${cl}"></div>
    <div class="lrow-icon">📝</div>
    <div class="lrow-main">
      <div class="lrow-title">${esc(n.title)}</div>
      ${n.body?`<div class="lrow-sub">${esc(n.body)}</div>`:''}
    </div>
    ${tags?`<div class="lrow-tags">${tags}</div>`:''}
    <div class="lrow-date">${fd(n.created)}</div>
    <div class="lrow-btns">
      <button class="cbtn" onclick="event.stopPropagation();editItem('${n.id}')">Edit</button>
      <button class="cbtn del" onclick="event.stopPropagation();deleteItem('${n.id}')">Delete</button>
    </div>
  </div>`;
}

function renderReminderRow(r){
  const now=new Date();
  let sc='pending';
  try{if(r.sent){sc='sent';}else if(isOverdue(r)){sc='overdue';}}catch{}
  const tags=(r.tags||[]).slice(0,2).map(t=>`<span class="ctag">#${esc(t)}</span>`).join('');
  const rep=r.repeat&&r.repeat!=='none'?`<span class="ctag">🔁 ${r.repeat}</span>`:'';
  const doneBtn = !r.sent
    ? `<button class="cbtn done-btn" onclick="event.stopPropagation();markReminderDone('${r.id}')">✅ Done</button>`
    : `<button class="cbtn" onclick="event.stopPropagation();markReminderDone('${r.id}')">↩ Reopen</button>`;
  // Single priority indicator — one colour strip on the left, no duplicate emojis
  const rprio=r.priority||'medium';
  const accentCls=rprio==='high'?' cl-red':rprio==='low'?' cl-green':' cl-blue';
  const statusIcon=r.sent?'✅':sc==='overdue'?'⚠️':'🔔';
  return`<div class="lrow ${sc}" data-type="reminder" data-id="${r.id}" onclick="handleCardClick(event,'${r.id}')">
    <div class="lrow-accent${accentCls}"></div>
    <div class="lrow-icon">${statusIcon}</div>
    <div class="lrow-main">
      <div class="lrow-title">${r.category==='official'?'💼':'🏠'} ${esc(r.title)}</div>
      ${r.body?`<div class="lrow-sub">${esc(r.body)}</div>`:''}
    </div>
    ${tags?`<div class="lrow-tags">${tags}${rep}</div>`:''}
    <div class="lrow-due">📅 ${esc(r.due||'')}</div>
    <div class="lrow-date">${fd(r.created)}</div>
    <div class="lrow-btns">
      ${doneBtn}
      <button class="cbtn" onclick="event.stopPropagation();editItem('${r.id}')">Edit</button>
      <button class="cbtn del" onclick="event.stopPropagation();deleteItem('${r.id}')">Delete</button>
    </div>
  </div>`;
}

/* click anywhere on card/row = edit, except delete button */
function handleCardClick(e, id){
  if(e.target.classList.contains('del')) return;
  editItem(id);
}

/* -- MARK REMINDER DONE -------------------------- */
async function markReminderDone(id){
  const rem = (DATA.reminders||[]).find(r=>r.id===id);
  if(!rem) return;
  rem.sent = !rem.sent;
  renderAll();
  await saveToFirebase();
  toast(rem.sent ? '✅ Marked as done!' : '↩ Reopened', 'success');
}

/* -- MODAL --------------------------------------- */
function openModal(type='note'){
  document.getElementById('modal-heading').textContent='Add New';
  document.getElementById('edit-id').value='';
  document.getElementById('f-title').value='';
  document.getElementById('f-body').value='';
  document.getElementById('f-due-date').value='';
  document.getElementById('f-due-hour').value='09';
  document.getElementById('f-due-min').value='00';
  document.getElementById('f-repeat').value='none';
  const rbEl = document.getElementById('f-remind-before'); if(rbEl) rbEl.value='30';
  document.getElementById('f-pinned').value='false';
  document.getElementById('pin-btn').className='pin-btn';
  document.getElementById('pin-btn').textContent='📌 Pin';
  setTagChips([]);
  selectSwatchByValue('default');
  const catEl = document.getElementById('f-category');
  if(catEl) catEl.value = 'personal';
  const pts = document.getElementById('preview-timestamps');
  if(pts) pts.innerHTML='';
  switchType(type);
  document.getElementById('modal-overlay').classList.add('open');
  document.getElementById('autosave-lbl').classList.remove('show');
  updatePreview();
}
function closeModal(){document.getElementById('modal-overlay').classList.remove('open')}

/* ── NOTES / REMINDERS CATEGORY FILTER ── */
let _notesCatFilter = 'all';
let _remCatFilter   = 'all';

function setNotesCatFilter(cat, btn){
  _notesCatFilter = cat;
  document.querySelectorAll('[id^="notes-fc-"]').forEach(b=>b.classList.remove('active'));
  if(btn) btn.classList.add('active');
  renderAll();
}
function setRemCatFilter(cat, btn){
  _remCatFilter = cat;
  document.querySelectorAll('[id^="rem-fc-"]').forEach(b=>b.classList.remove('active'));
  if(btn) btn.classList.add('active');
  renderAll();
}

/* ── TAG CHIP SYSTEM ── */
let _tagChips = [];

function setTagChips(arr){
  _tagChips = [...arr];
  renderTagChips();
  document.getElementById('f-tags').value = _tagChips.join(', ');
}

function getTagChips(){ return [..._tagChips]; }

function renderTagChips(){
  const display = document.getElementById('tag-chips-display');
  if(!display) return;
  display.innerHTML = _tagChips.map((t,i)=>
    `<span class="tag-chip">#${esc(t)}<button class="tag-chip-x" onclick="removeTagChip(${i})">×</button></span>`
  ).join('');
}

function removeTagChip(i){
  _tagChips.splice(i,1);
  renderTagChips();
  document.getElementById('f-tags').value = _tagChips.join(', ');
}

function handleTagKey(e){
  const input = e.target;
  const val = input.value.trim().replace(/,/g,'');
  if((e.key==='Enter'||e.key===','||e.key===' ')&&val){
    e.preventDefault();
    if(!_tagChips.includes(val)) _tagChips.push(val);
    renderTagChips();
    document.getElementById('f-tags').value = _tagChips.join(', ');
    input.value='';
    hideTagSuggestions();
  } else if(e.key==='Backspace'&&!input.value&&_tagChips.length){
    _tagChips.pop();
    renderTagChips();
    document.getElementById('f-tags').value = _tagChips.join(', ');
  }
}

function showTagSuggestions(q){
  const box = document.getElementById('tag-suggestions');
  if(!box) return;
  const allTags = [...new Set([
    ...(DATA.notes||[]).flatMap(n=>n.tags||[]),
    ...(DATA.reminders||[]).flatMap(r=>r.tags||[])
  ])].filter(t=>t&&!_tagChips.includes(t)&&t.toLowerCase().includes(q.toLowerCase()));
  if(!q||!allTags.length){ box.classList.remove('open'); return; }
  box.innerHTML = allTags.slice(0,6).map(t=>
    `<div class="tag-sug-item" onclick="pickTagSuggestion('${esc(t)}')">🏷 ${esc(t)}</div>`
  ).join('');
  box.classList.add('open');
}

function hideTagSuggestions(){
  const box=document.getElementById('tag-suggestions');
  if(box) box.classList.remove('open');
}

function pickTagSuggestion(t){
  if(!_tagChips.includes(t)) _tagChips.push(t);
  renderTagChips();
  document.getElementById('f-tags').value = _tagChips.join(', ');
  document.getElementById('tag-chip-input').value='';
  hideTagSuggestions();
  updatePreview();
}

/* ── 6. LIVE PREVIEW ── */
function updatePreview(){
  const title  = document.getElementById('f-title')?.value||'';
  const body   = document.getElementById('f-body')?.value||'';
  const color  = document.getElementById('f-color')?.value||'default';
  const pinned = document.getElementById('f-pinned')?.value==='true';

  const pCard  = document.getElementById('preview-card');
  const pTitle = document.getElementById('preview-title');
  const pBody  = document.getElementById('preview-body');
  const pTags  = document.getElementById('preview-tags');
  const pDate  = document.getElementById('preview-date');
  const pPin   = document.getElementById('preview-pin-badge');

  if(!pCard) return;

  // update card colour class
  pCard.className = 'preview-card'+(color!=='default'?' cl-'+color:'');

  pTitle.textContent = title||'Your title will appear here…';
  pTitle.style.fontStyle = title?'normal':'italic';
  pTitle.style.fontWeight = title?'700':'400';
  pTitle.style.color = title?'':'var(--muted)';

  pBody.textContent = body;
  pBody.style.display = body?'':'none';

  const tagArr = getTagChips();
  pTags.innerHTML = tagArr.map(t=>`<span class="ctag">#${esc(t)}</span>`).join('');
  pTags.style.display = tagArr.length?'':'none';

  const now = localToday();
  pDate.textContent = 'Created '+now;
  pPin.innerHTML = pinned?'<span class="pinned-badge">📌 Pinned</span>':'';
}

/* ── 7. PIN ── */
function togglePin(){
  const hidden = document.getElementById('f-pinned');
  const btn    = document.getElementById('pin-btn');
  if(!hidden) return;
  const isPinned = hidden.value==='true';
  hidden.value = isPinned?'false':'true';
  btn.classList.toggle('pinned', !isPinned);
  btn.textContent = isPinned?'📌 Pin':'📌 Pinned';
  updatePreview();
}

/* ── COLOR SWATCHES ── */
function selectSwatch(el){
  document.querySelectorAll('.cswatch').forEach(s=>s.classList.remove('selected'));
  el.classList.add('selected');
  document.getElementById('f-color').value = el.dataset.color;
  updatePreview();
}

function selectSwatchByValue(val){
  document.querySelectorAll('.cswatch').forEach(s=>{
    s.classList.toggle('selected', s.dataset.color===val);
  });
  document.getElementById('f-color').value = val||'default';
  updatePreview();
}

/* ── AUTOSAVE DEBOUNCE ── */
let _autosaveTimer = null;
function scheduleAutosave(){
  clearTimeout(_autosaveTimer);
  _autosaveTimer = setTimeout(()=>{
    const title = document.getElementById('f-title').value.trim();
    if(!title) return;
    // silent background save only if editing existing item
    const id = document.getElementById('edit-id').value;
    if(!id) return;
    saveItem();
  }, 2000);
}

/* close suggestion box on outside click */
document.addEventListener('click', e=>{
  if(!e.target.closest('#tag-chip-wrap')&&!e.target.closest('#tag-suggestions')) hideTagSuggestions();
});

function switchType(t){
  currentType=t;
  document.getElementById('tt-note').classList.toggle('active',t==='note');
  document.getElementById('tt-reminder').classList.toggle('active',t==='reminder');
  document.getElementById('row-color').style.display     = t==='note'     ? '' : 'none';
  document.getElementById('row-pin').style.display       = t==='note'     ? 'flex' : 'none';
  document.getElementById('row-due').style.display       = t==='reminder' ? '' : 'none';
  document.getElementById('row-repeat').style.display    = t==='reminder' ? '' : 'none';
  document.getElementById('row-priority').style.display  = t==='reminder' ? '' : 'none';
  document.getElementById('type-desc-note').style.display     = t==='note'     ? '' : 'none';
  document.getElementById('type-desc-reminder').style.display = t==='reminder' ? '' : 'none';
  document.getElementById('modal-save-btn').textContent  = t==='note' ? '💾 Save Note' : '⏰ Save Reminder';
  const remindRow = document.getElementById('row-remind-before');
  if(remindRow) remindRow.style.display = t==='reminder' ? '' : 'none';
  const previewCol = document.getElementById('modal-preview-col');
  const modal      = document.getElementById('main-modal');
  if(previewCol) previewCol.style.display = t==='note' ? '' : 'none';
  if(modal) modal.className = t==='note' ? 'modal with-preview' : 'modal';
}

function editItem(id){
  const item=[...(DATA.notes||[]),...(DATA.reminders||[])].find(i=>i.id===id);
  if(!item)return;
  document.getElementById('modal-heading').textContent='Edit';
  document.getElementById('edit-id').value=id;
  document.getElementById('f-title').value=item.title||'';
  document.getElementById('f-body').value=item.body||'';
  setTagChips(item.tags||[]);
  selectSwatchByValue(item.color||'default');
  // 7. pin
  const isPinned=item.pinned===true;
  document.getElementById('f-pinned').value=String(isPinned);
  const pb=document.getElementById('pin-btn');
  pb.className='pin-btn'+(isPinned?' pinned':'');
  pb.textContent=isPinned?'📌 Pinned':'📌 Pin';
  // 8. timestamps
  const pts=document.getElementById('preview-timestamps');
  if(pts){
    const lines=[];
    if(item.created) lines.push('📅 Created: '+item.created.slice(0,10));
    if(item.updated) lines.push('✏️ Updated: '+item.updated.slice(0,10));
    pts.innerHTML=lines.join('<br>');
  }
  if(item.due){
    const parts=item.due.split(' ');
    document.getElementById('f-due-date').value=parts[0]||'';
    if(parts[1]){
      const tp=parts[1].split(':');
      document.getElementById('f-due-hour').value=(tp[0]||'09').padStart(2,'0');
      document.getElementById('f-due-min').value=(tp[1]||'00').padStart(2,'0');
    }
  }
  document.getElementById('f-repeat').value=item.repeat||'none';
  // restore priority radio
  const prio = item.priority||'medium';
  const prioRadio = document.querySelector(`input[name="f-priority"][value="${prio}"]`);
  if(prioRadio) prioRadio.checked = true;
  const catEl2 = document.getElementById('f-category');
  if(catEl2) catEl2.value = item.category||'personal';
  switchType(item.type==='reminder'?'reminder':'note');
  document.getElementById('modal-overlay').classList.add('open');
  document.getElementById('autosave-lbl').classList.remove('show');
  updatePreview();
}

async function saveItem(){
  const title=document.getElementById('f-title').value.trim();
  if(!title){toast('Title is required','error');return;}
  const id=document.getElementById('edit-id').value;
  const tags=getTagChips();
  const color=document.getElementById('f-color').value||'default';
  const pinned=document.getElementById('f-pinned').value==='true';
  const now=localNow();
  const ex=id?[...(DATA.notes||[]),...(DATA.reminders||[])].find(i=>i.id===id):null;
  if(currentType==='note'){
    const note={id:id||uid(),type:'note',category:document.getElementById('f-category').value||'personal',title,body:document.getElementById('f-body').value.trim(),tags,color,pinned,created:ex?ex.created:now,updated:now,attachments:ex?ex.attachments||[]:[]};
    if(id)DATA.notes=DATA.notes.map(n=>n.id===id?note:n);else DATA.notes.push(note);
  }else{
    const dueDate=document.getElementById('f-due-date').value;
    const dueHour=document.getElementById('f-due-hour').value||'00';
    const dueMin=document.getElementById('f-due-min').value||'00';
    if(!dueDate){toast('Due date required','error');return;}
    const dueStr=dueDate+' '+dueHour+':'+dueMin;
    const selPrio = document.querySelector('input[name="f-priority"]:checked');
    const priority = selPrio ? selPrio.value : 'medium';
    const rem={id:id||uid(),type:'reminder',category:document.getElementById('f-category').value||'personal',title,body:document.getElementById('f-body').value.trim(),tags,due:dueStr,repeat:document.getElementById('f-repeat').value,priority,sent:ex?ex.sent||false:false,created:ex?ex.created:now,updated:now,attachments:ex?ex.attachments||[]:[]};
    if(id)DATA.reminders=DATA.reminders.map(r=>r.id===id?rem:r);else DATA.reminders.push(rem);
    // ── Auto-sync to Google Calendar ──────────────────────────────────────
    if(dueStr){
      try{
        const [datePart2, timePart2] = dueStr.split(' ');
        const startISO2 = datePart2 + 'T' + (timePart2||'09:00') + ':00';
        const endDate2 = new Date(startISO2); endDate2.setHours(endDate2.getHours()+1);
        const pad2 = n=>String(n).padStart(2,'0');
        const endISO2 = endDate2.getFullYear()+'-'+pad2(endDate2.getMonth()+1)+'-'+pad2(endDate2.getDate())+'T'+pad2(endDate2.getHours())+':'+pad2(endDate2.getMinutes())+':00';
        const remindMins = parseInt(document.getElementById('f-remind-before')?.value||'30');
        addReminderToGoogleCalendar(rem.id, title, startISO2, endISO2, rem.body||'', remindMins);
      }catch(gcErr){ console.warn('GCal sync skipped:',gcErr); }
    }
  }
  const lbl=document.getElementById('autosave-lbl');
  if(lbl){lbl.classList.add('show');setTimeout(()=>lbl.classList.remove('show'),2500);}
  closeModal();renderAll();
  await saveToFirebase();
  toast('Saved ✓','success');
}

async function deleteItem(id){
  if(!confirm('Delete this item?'))return;
  DATA.notes=(DATA.notes||[]).filter(n=>n.id!==id);
  DATA.reminders=(DATA.reminders||[]).filter(r=>r.id!==id);
  renderAll();
  await saveToFirebase();
}

const uid=()=>Math.random().toString(36).slice(2,10);

// Returns local datetime as 'YYYY-MM-DD HH:MM' (no UTC shift)
function localNow(){
  const d=new Date();
  const pad=n=>String(n).padStart(2,'0');
  return `${d.getFullYear()}-${pad(d.getMonth()+1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
}
// Returns local date as 'YYYY-MM-DD'
function localToday(){
  const d=new Date();
  const pad=n=>String(n).padStart(2,'0');
  return `${d.getFullYear()}-${pad(d.getMonth()+1)}-${pad(d.getDate())}`;
}

/* Returns true if a reminder is past its due date+time (including today's past-time items) */
function isOverdue(r){
  if(!r || r.sent || !r.due) return false;
  try{
    const dueMs = new Date(r.due.replace(' ','T')).getTime();
    return dueMs < Date.now();
  }catch{ return false; }
}

/* -- QUICK CAPTURE ------------------------------- */
function toggleMissedWidget(){
  const list=document.getElementById('dash-missed-list');
  const arrow=document.getElementById('missed-toggle');
  if(!list) return;
  const collapsed=list.style.display==='none';
  list.style.display=collapsed?'':'none';
  if(arrow) arrow.textContent=collapsed?'▼':'▶';
}

function quickCapture(){
  const input=document.getElementById('qc-input');
  const type=document.getElementById('qc-type').value;
  const text=input.value.trim();
  if(!text){toast('Type something first','error');return;}

  const now=new Date();
  const pad=n=>String(n).padStart(2,'0');
  const dateStr=`${now.getFullYear()}-${pad(now.getMonth()+1)}-${pad(now.getDate())}`;
  const timeStr=`${pad(now.getHours())}:${pad(now.getMinutes())}`;

  if(type==='note'){
    if(!DATA.notes) DATA.notes=[];
    DATA.notes.push({id:'n_'+Date.now(),title:text,body:'',folder:'personal',created:now.toISOString()});
  } else if(type==='reminder'){
    if(!DATA.reminders) DATA.reminders=[];
    DATA.reminders.push({id:'r_'+Date.now(),text:text,date:dateStr,time:'23:59',done:false,created:now.toISOString()});
  } else if(type==='task'){
    if(!DATA.tasknotes) DATA.tasknotes=[];
    DATA.tasknotes.push({id:'tn_'+Date.now(),text:text,done:false,category:'personal',date:dateStr,created:now.toISOString()});
    if(typeof TASKNOTES!=='undefined') TASKNOTES=DATA.tasknotes;
  } else if(type==='sticky'){
    if(!DATA.stickies) DATA.stickies=[];
    DATA.stickies.push({id:'s_'+Date.now(),text:text,bg:'#fde68a',pinned:false});
  } else if(type==='daybook'){
    if(!DATA.daybook) DATA.daybook=[];
    DATA.daybook.push({id:'db_'+Date.now(),date:dateStr,time:timeStr,text:text,tags:[],created:now.toISOString()});
  }

  input.value='';
  renderAll();
  if(typeof dbRender==='function') dbRender();
  if(typeof dbUpdateCounts==='function') dbUpdateCounts();
  if(typeof renderFinance==='function') renderFinance();
  if(typeof renderTaskNotes==='function') renderTaskNotes();
  saveToFirebase();
  toast(`Added to ${type} ✓`,'success');
}
// Enter key support for quick capture
document.addEventListener('keydown',e=>{
  if(e.target.id==='qc-input' && e.key==='Enter'){e.preventDefault();quickCapture();}
});

/* -- STAT CARD FILTER ---------------------------- */
function statFilter(type, btn){
  document.querySelectorAll('.stat-card').forEach(c=>c.classList.remove('active'));
  btn.classList.add('active');

  const remSec       = document.getElementById('rem-section');
  const notesSec     = document.getElementById('notes-section');
  const remHdr       = document.getElementById('rem-sec-header');
  const remCatFil    = document.getElementById('rem-cat-filter');
  const notesHdr     = document.getElementById('notes-sec-header');
  const notesCatFil  = document.getElementById('notes-cat-filter');

  const showRem   = (type==='all'||type==='reminder'||type==='pending');
  const showNotes = (type==='all'||type==='note');

  if(remSec)      remSec.style.display      = showRem   ? '' : 'none';
  if(remHdr)      remHdr.style.display      = showRem   ? '' : 'none';
  if(remCatFil)   remCatFil.style.display   = showRem   ? 'flex' : 'none';
  if(notesSec)    notesSec.style.display    = showNotes ? '' : 'none';
  if(notesHdr)    notesHdr.style.display    = showNotes ? '' : 'none';
  if(notesCatFil) notesCatFil.style.display = showNotes ? 'flex' : 'none';

  // pending = anything not sent (includes overdue)
  if(type==='pending'){
    document.querySelectorAll('[data-type="reminder"]').forEach(el=>{
      el.style.display = !el.classList.contains('sent') ? '' : 'none';
    });
  } else {
    document.querySelectorAll('[data-type="reminder"]').forEach(el=>{
      el.style.display = '';
    });
  }
}

/* -- FILTER/SEARCH ------------------------------- */
function filterCards(type, btn){
  // update nav highlight
  document.querySelectorAll('.nav-item').forEach(b=>b.classList.remove('active'));
  if(btn) btn.classList.add('active');

  // reset stat card highlights
  document.querySelectorAll('.stat-card').forEach(c=>c.classList.remove('active'));

  const remSec      = document.getElementById('rem-section');
  const notesSec    = document.getElementById('notes-section');
  const remHdr      = document.getElementById('rem-sec-header');
  const remCatFil   = document.getElementById('rem-cat-filter');
  const notesHdr    = document.getElementById('notes-sec-header');
  const notesCatFil = document.getElementById('notes-cat-filter');

  function showRem(v){
    if(remSec)      remSec.style.display      = v ? '' : 'none';
    if(remHdr)      remHdr.style.display      = v ? '' : 'none';
    if(remCatFil)   remCatFil.style.display   = v ? 'flex' : 'none';
  }
  function showNotes(v){
    if(notesSec)    notesSec.style.display    = v ? '' : 'none';
    if(notesHdr)    notesHdr.style.display    = v ? '' : 'none';
    if(notesCatFil) notesCatFil.style.display = v ? 'flex' : 'none';
  }

  if(type==='all'){
    showRem(true); showNotes(true);
    document.querySelectorAll('.ncard,.lrow').forEach(c=>c.style.display='');
    return;
  }

  if(type==='note'){
    showRem(false); showNotes(true);
    document.querySelectorAll('.ncard,.lrow').forEach(c=>c.style.display='');
    return;
  }

  if(type==='reminder'){
    showRem(true); showNotes(false);
    document.querySelectorAll('.ncard,.lrow').forEach(c=>c.style.display='');
    return;
  }

  // pending / overdue / sent - show reminders section only, filter by class
  showRem(true); showNotes(false);

  document.querySelectorAll('[data-type="reminder"]').forEach(c=>{
    let show = false;
    if(type==='pending')       show = !c.classList.contains('sent'); // pending+overdue
    else if(type==='overdue')  show = c.classList.contains('overdue');
    else if(type==='sent')     show = c.classList.contains('sent');
    else show = c.classList.contains(type);
    c.style.display = show ? '' : 'none';
  });

  // show empty state if nothing visible
  const anyVisible = [...document.querySelectorAll('[data-type="reminder"]')]
    .some(c=>c.style.display!=='none');
  if(!anyVisible){
    // inject empty msg if not already there
    ['reminders-grid','reminders-list'].forEach(id=>{
      const el=document.getElementById(id);
      if(el && !el.querySelector('.filter-empty')){
        const d=document.createElement('div');
        d.className='empty-state filter-empty';
        d.innerHTML='<div class="ei">🔍</div><p>Nothing here</p>';
        el.appendChild(d);
      }
    });
  } else {
    document.querySelectorAll('.filter-empty').forEach(e=>e.remove());
  }
}

function searchCards(q){
  const lq=q.toLowerCase();
  document.querySelectorAll('.ncard,.lrow').forEach(c=>{
    c.style.display=c.innerText.toLowerCase().includes(lq)?'':'none';
  });
}
document.addEventListener('keydown',e=>{if(e.key==='Escape'){closeModal();closeSettings();}});

/* -- TOAST --------------------------------------- */
function toast(msg,type='success'){
  const t=document.getElementById('toast');
  t.textContent=msg;t.className='show '+type;
  setTimeout(()=>{t.className='';},3000);
}

/* -- MOBILE SIDEBAR ------------------------------ */
function openSidebar(){
  document.querySelector('aside').classList.add('open');
  document.getElementById('sidebar-overlay').classList.add('open');
}
function closeSidebar(){
  document.querySelector('aside').classList.remove('open');
  document.getElementById('sidebar-overlay').classList.remove('open');
}
// close sidebar when any nav item clicked on mobile
document.addEventListener('DOMContentLoaded',()=>{
  document.querySelectorAll('.nav-item').forEach(btn=>{
    btn.addEventListener('click',()=>{
      if(window.innerWidth<=640) closeSidebar();
    });
  });
});

/* == NOTES PAGE — 3-column Apple Notes style == */
let _selectedNoteId  = null;
let _selectedFolderId = 'all';
let _noteAutoSaveTimer = null;

const DEFAULT_FOLDERS = [{id:'personal',name:'Personal',icon:'👤'},{id:'official',name:'Official',icon:'💼'}];

function getFolders(){
  // Validate: must be a real array of objects with id+name
  if(!Array.isArray(DATA.note_folders) || !DATA.note_folders.length ||
     typeof DATA.note_folders[0] !== 'object' || !DATA.note_folders[0].id){
    DATA.note_folders = DEFAULT_FOLDERS.map(f=>({...f}));
  }
  return DATA.note_folders;
}

function renderNotesPage(){
  renderFolderPanel();
  renderNotesList();
}

function renderFolderPanel(){
  const folders = getFolders();
  const allNotes = DATA.notes || [];
  const el = document.getElementById('notes-folder-list');
  if(!el) return;

  // "All Notes" row — no delete/rename on this
  const allActive = _selectedFolderId==='all' ? ' active' : '';
  let html = `<div class="notes-folder-item${allActive}" onclick="selectFolder('all')">
    <span class="notes-folder-name">📋 All Notes</span>
    <span class="notes-folder-count">${allNotes.length}</span>
  </div>`;

  folders.forEach(f=>{
    if(!f || typeof f !== 'object' || !f.id || !f.name) return;
    const count = allNotes.filter(n=>(n.folder_id||n.category||'personal')===f.id).length;
    const active = _selectedFolderId===f.id ? ' active' : '';
    const icon = String(f.icon||'📁');
    const name = esc(String(f.name||'Folder'));
    const isDefault = f.id==='personal'||f.id==='official';
    html += `<div class="notes-folder-item${active} notes-folder-item-wrap" onclick="selectFolder('${esc(f.id)}')">
      <span class="notes-folder-name">${icon} ${name}</span>
      <span class="notes-folder-count">${count}</span>
      <span class="notes-folder-actions" onclick="event.stopPropagation()">
        <button class="notes-folder-action-btn" onclick="renameFolder('${esc(f.id)}')" title="Rename">✏️</button>
        ${isDefault ? '' : `<button class="notes-folder-action-btn del" onclick="deleteFolder('${esc(f.id)}')" title="Delete">🗑</button>`}
      </span>
    </div>`;
  });

  el.innerHTML = html;
}

function selectFolder(folderId){
  _selectedFolderId = folderId;
  _selectedNoteId = null;
  renderFolderPanel();
  renderNotesList();
  showNoteEditor(null);
  notesMobileShow('list');
}

function renderNotesList(){
  const allNotes = DATA.notes || [];
  const search   = (document.getElementById('notes-panel-search')?.value||'').toLowerCase();

  let items = _selectedFolderId==='all'
    ? [...allNotes]
    : allNotes.filter(n=>(n.folder_id||n.category||'personal')===_selectedFolderId);

  // sort by updated desc
  items = [...items].sort((a,b)=>{
    const ta = a.updated||a.created||'';
    const tb = b.updated||b.created||'';
    return tb.localeCompare(ta);
  });

  if(search) items = items.filter(n=>
    (n.title||'').toLowerCase().includes(search)||
    (n.body||'').toLowerCase().includes(search)
  );

  // update header folder name
  const folders = getFolders();
  const folderName = _selectedFolderId==='all' ? 'All Notes'
    : (folders.find(f=>f.id===_selectedFolderId)?.name||'Notes');
  const nameEl = document.getElementById('notes-list-folder-name');
  if(nameEl) nameEl.textContent = folderName;

  const countEl = document.getElementById('notes-panel-count');
  if(countEl) countEl.textContent = items.length+(items.length===1?' note':' notes');

  const list = document.getElementById('notes-panel-list');
  if(!list) return;
  if(!items.length){
    list.innerHTML='<div class="notes-list-empty">No notes here.<br>Click ＋ New to add one.</div>';
    showNoteEditor(null);
    return;
  }

  function noteItemHTML(n){
    const cl  = n.color&&n.color!=='default' ? ' cl-'+n.color : '';
    const dateStr = (n.updated||n.created||'').slice(0,10);
    const snippet = (n.body||'')
      .replace(/!\[[^\]]*\]\((?:data:image\/[^)]+|%%IMGDATA:[^)]+%%)\)/g,'📷')
      .replace(/\n/g,' ').slice(0,72);
    const isActive = n.id===_selectedNoteId ? ' active' : '';
    return `<div class="notes-list-item${isActive}" onclick="selectNote('${n.id}')" id="nli-${n.id}">
      <div class="notes-list-item-accent${cl}"></div>
      <div class="notes-list-item-title">${esc(n.title||'New Note')}</div>
      <div class="notes-list-item-date">${dateStr}</div>
      ${snippet?`<div class="notes-list-item-snippet">${esc(snippet)}</div>`:''}
    </div>`;
  }

  let html = '';

  // --- Pinned section ---
  const pinned = items.filter(n=>n.pinned);
  if(pinned.length && !search){
    html += `<div class="notes-section-label"><span>📌</span> Pinned</div>`;
    html += pinned.map(noteItemHTML).join('');
  }

  // --- Recently Edited (last 5 modified in last 7 days, not pinned) ---
  const sevenDaysAgo = new Date(Date.now() - 7*24*60*60*1000).toISOString().slice(0,10);
  const recent = items.filter(n=>!n.pinned && (n.updated||n.created||'')>=sevenDaysAgo).slice(0,5);
  if(recent.length && !search){
    html += `<div class="notes-section-label"><span>🕐</span> Recently Edited</div>`;
    html += recent.map(noteItemHTML).join('');
  }

  // --- All other notes ---
  const recentIds = new Set(recent.map(n=>n.id));
  const pinnedIds = new Set(pinned.map(n=>n.id));
  const rest = items.filter(n=> !pinnedIds.has(n.id) && !recentIds.has(n.id));

  if(rest.length || search){
    if(!search && (pinned.length||recent.length)){
      html += `<div class="notes-section-label">All Notes</div>`;
    }
    html += (search ? items : rest).map(noteItemHTML).join('');
  }

  list.innerHTML = html;

  // auto-select first if nothing is selected
  if(!_selectedNoteId && items.length) selectNote(items[0].id, false);
}

function selectNote(id, focusEditor=false){
  _selectedNoteId = id;
  document.querySelectorAll('.notes-list-item').forEach(el=>el.classList.remove('active'));
  const li = document.getElementById('nli-'+id);
  if(li) li.classList.add('active');
  showNoteEditor(id, focusEditor);
  notesMobileShow('editor');
}

function showNoteEditor(id, focusBody=false){
  const emptyEl  = document.getElementById('notes-editor-empty');
  const innerEl  = document.getElementById('notes-editor-inner');
  if(!id){
    if(emptyEl) emptyEl.style.display='flex';
    if(innerEl) innerEl.style.display='none';
    return;
  }
  const n = (DATA.notes||[]).find(x=>x.id===id);
  if(!n){
    if(emptyEl) emptyEl.style.display='flex';
    if(innerEl) innerEl.style.display='none';
    return;
  }
  if(emptyEl) emptyEl.style.display='none';
  if(innerEl) innerEl.style.display='flex';

  const titleEl = document.getElementById('notes-editor-title');
  const bodyEl  = document.getElementById('notes-editor-body');
  const metaEl  = document.getElementById('notes-editor-meta');
  if(titleEl) titleEl.value = n.title||'';
  if(bodyEl){
    // Replace any saved data:image base64 URLs with compact in-memory tokens
    // so the textarea stays clean and readable
    let bodyText = n.body||'';
    bodyText = bodyText.replace(/!\[([^\]]*)\]\((data:image\/[^)]{20,})\)/g, (match, alt, dataUrl) => {
      const token = 'img_' + (++window._imgTokenCounter);
      window._imgDataStore[token] = dataUrl;
      return `![${alt||'pasted image'}](%%IMGDATA:${token}%%)`;
    });
    bodyEl.value = bodyText;
  }
  if(metaEl)  metaEl.textContent = n.updated||n.created||'';
  hideSavedIndicator();
  if(focusBody && bodyEl) setTimeout(()=>bodyEl.focus(),50);
  else if(titleEl && !(n.title)) setTimeout(()=>titleEl.focus(),50);

  // Restore edit/preview mode preference
  const savedMode = localStorage.getItem('note_view_mode')||'edit';
  setNoteViewMode(savedMode);
}

function onNoteEditorInput(){
  clearTimeout(_noteAutoSaveTimer);
  _noteAutoSaveTimer = setTimeout(()=>saveCurrentNoteInline(), 800);
}

function noteTitleKeydown(e){
  // Tab or Enter in title → jump to body
  if(e.key==='Enter'||e.key==='Tab'){
    e.preventDefault();
    document.getElementById('notes-editor-body')?.focus();
  }
}

async function saveCurrentNoteInline(){
  if(!_selectedNoteId) return;
  const titleEl = document.getElementById('notes-editor-title');
  const bodyEl  = document.getElementById('notes-editor-body');
  const title   = titleEl?.value.trim()||'';
  const rawBody = bodyEl?.value||'';
  // Resolve any in-memory image tokens back to full data URLs before persisting
  const body = rawBody.replace(/%%IMGDATA:(img_\d+)%%/g, (match, token) => {
    return (window._imgDataStore && window._imgDataStore[token]) ? window._imgDataStore[token] : match;
  });
  const now=localNow();
  DATA.notes = (DATA.notes||[]).map(n=>{
    if(n.id!==_selectedNoteId) return n;
    return {...n, title:title||'New Note', body, updated:now};
  });
  // refresh list item without full re-render
  const li = document.getElementById('nli-'+_selectedNoteId);
  if(li){
    const n = DATA.notes.find(x=>x.id===_selectedNoteId);
    if(n){
      const snippet = (n.body||'')
        .replace(/!\[[^\]]*\]\((?:data:image\/[^)]+|%%IMGDATA:[^)]+%%)\)/g,'📷')
        .replace(/\n/g,' ').slice(0,72);
      const titleDiv = li.querySelector('.notes-list-item-title');
      const snippetDiv = li.querySelector('.notes-list-item-snippet');
      const dateDiv = li.querySelector('.notes-list-item-date');
      if(titleDiv) titleDiv.textContent = n.title||'New Note';
      if(dateDiv)  dateDiv.textContent  = now.slice(0,10);
      if(snippetDiv) snippetDiv.textContent = snippet;
    }
  }
  const metaEl = document.getElementById('notes-editor-meta');
  if(metaEl) metaEl.textContent = now;
  showSavedIndicator();
  renderAll();
  await saveToFirebase();
}

function showSavedIndicator(){
  const el = document.getElementById('notes-editor-saved');
  if(el){ el.classList.add('show'); setTimeout(()=>el.classList.remove('show'),2000); }
}
function hideSavedIndicator(){
  const el = document.getElementById('notes-editor-saved');
  if(el) el.classList.remove('show');
}

async function createNewNote(){
  const now=localNow();
  const folderId  = _selectedFolderId==='all' ? 'personal' : _selectedFolderId;
  const newNote   = {
    id: uid(), type:'note',
    title:'', body:'',
    folder_id: folderId,
    category: folderId,
    color:'default', pinned:false,
    tags:[], created:now, updated:now, attachments:[]
  };
  if(!DATA.notes) DATA.notes=[];
  DATA.notes.push(newNote);
  _selectedNoteId = newNote.id;
  renderFolderPanel();
  renderNotesList();
  // force select & focus title
  selectNote(newNote.id, false);
  setTimeout(()=>document.getElementById('notes-editor-title')?.focus(), 60);
  await saveToFirebase();
}

async function createNewFolder(){
  const name = prompt('Folder name:','');
  if(!name||!name.trim()) return;
  const folder = {id:'f'+uid(), name:name.trim(), icon:'📁'};
  if(!DATA.note_folders) DATA.note_folders=DEFAULT_FOLDERS.slice();
  DATA.note_folders.push(folder);
  renderFolderPanel();
  await saveToFirebase();
}

async function renameFolder(folderId){
  const folders = getFolders();
  const f = folders.find(x=>x.id===folderId);
  if(!f) return;
  const newName = prompt('Rename folder:', f.name);
  if(!newName||!newName.trim()||newName.trim()===f.name) return;
  f.name = newName.trim();
  renderFolderPanel();
  renderNotesList();
  await saveToFirebase();
  toast('Folder renamed ✓','success');
}

async function deleteFolder(folderId){
  const folders = getFolders();
  const f = folders.find(x=>x.id===folderId);
  if(!f) return;
  const notesInFolder = (DATA.notes||[]).filter(n=>(n.folder_id||n.category||'personal')===folderId);
  let action = 'delete';
  if(notesInFolder.length){
    const choice = confirm(
      `Folder "${f.name}" has ${notesInFolder.length} note${notesInFolder.length!==1?'s':''}.\n\n`+
      `OK → Move notes to Personal folder\nCancel → Delete folder AND all its notes`
    );
    action = choice ? 'move' : 'delete-notes';
  }
  if(action==='move'){
    // Move notes to Personal
    DATA.notes = (DATA.notes||[]).map(n=>{
      if((n.folder_id||n.category||'personal')===folderId){
        return {...n, folder_id:'personal', category:'personal'};
      }
      return n;
    });
  } else if(action==='delete-notes'){
    if(!confirm(`Are you sure? This will permanently delete the folder AND all ${notesInFolder.length} note${notesInFolder.length!==1?'s':''} inside it.`)) return;
    DATA.notes = (DATA.notes||[]).filter(n=>(n.folder_id||n.category||'personal')!==folderId);
  }
  // Remove the folder
  DATA.note_folders = DATA.note_folders.filter(x=>x.id!==folderId);
  // If deleted folder was selected, go back to All Notes
  if(_selectedFolderId===folderId){ _selectedFolderId='all'; _selectedNoteId=null; }
  renderAll();
  renderFolderPanel();
  renderNotesList();
  showNoteEditor(null);
  await saveToFirebase();
  toast('Folder deleted ✓','success');
}

async function deleteCurrentNote(){
  if(!_selectedNoteId) return;
  if(!confirm('Delete this note?')) return;
  DATA.notes = (DATA.notes||[]).filter(n=>n.id!==_selectedNoteId);
  _selectedNoteId = null;
  renderAll();
  renderFolderPanel();
  renderNotesList();
  showNoteEditor(null);
  await saveToFirebase();
}


/* == REMINDERS PAGE == */
let _remPageFilter  = 'all';  // 'all'|'today'|'scheduled'|'completed'|list-id
let _remListId      = null;    // selected My List id (null = smart filter active)

const DEFAULT_REM_LISTS = [{id:'personal',name:'Personal',icon:'🔵'},{id:'official',name:'Official',icon:'🔴'}];

function getRemLists(){
  if(!Array.isArray(DATA.rem_lists) || !DATA.rem_lists.length ||
     typeof DATA.rem_lists[0] !== 'object' || !DATA.rem_lists[0].id){
    DATA.rem_lists = DEFAULT_REM_LISTS.map(l=>({...l}));
  }
  return DATA.rem_lists;
}

function renderRemindersPage(resetPanel){
  // Reset to the lists panel when explicitly requested (navigating TO reminders page)
  // For re-renders triggered by add/toggle/delete we preserve the current panel
  if(resetPanel){
    const cols = document.querySelector('.rem-columns');
    if(cols) cols.classList.remove('show-checklist');
    _rrpSelDate = null;
  }
  _updateRemTiles();
  _renderRemListPanel();
  _renderRemChecklist();
  _renderRightPanel();
  if(_remViewMode==='cal') renderFullCal();
}

function _updateRemTiles(){
  const rems = DATA.reminders||[];
  const activeCount = rems.filter(r=>!r.sent).length;
  const doneCount   = rems.filter(r=>r.sent).length;
  const activeEl = document.getElementById('rem-count-active');
  const doneEl   = document.getElementById('rem-count-completed');
  if(activeEl) activeEl.textContent = activeCount;
  if(doneEl) doneEl.textContent = doneCount;
}

function _renderRightPanel(){
  const allRems   = DATA.reminders||[];
  const filtered  = _getFilteredRems();
  const undone    = filtered.filter(r=>!r.sent);
  const done      = filtered.filter(r=>r.sent);
  const total     = filtered.length;
  const todayStr  = localToday();
  const now       = new Date();

  // Stats
  const setTxt = (id,v) => { const el=document.getElementById(id); if(el) el.textContent=v; };
  setTxt('rrp-total', total);
  setTxt('rrp-done', done.length);

  // Priority counts (use same logic as checklist)
  const getPri = r => {
    if(!r.due) return 'low';
    const dueMs = new Date(r.due.replace(' ','T'));
    if(dueMs <= now || (r.due||'').slice(0,10) === todayStr) return 'high';
    return 'medium';
  };
  const high = undone.filter(r=>getPri(r)==='high').length;
  const med  = undone.filter(r=>getPri(r)==='medium').length;
  const low  = undone.filter(r=>getPri(r)==='low').length;
  const maxPri = Math.max(high+med+low, 1);
  setTxt('rrp-high', high);
  setTxt('rrp-med', med);
  setTxt('rrp-low', low);
  const setBar = (id,val) => { const el=document.getElementById(id); if(el) el.style.width=Math.round(val/maxPri*100)+'%'; };
  setBar('rrp-high-bar', high);
  setBar('rrp-med-bar', med);
  setBar('rrp-low-bar', low);

  // Progress
  const pct = total ? Math.round(done.length/total*100) : 0;
  setTxt('rrp-prog-val', done.length+' / '+total);
  setTxt('rrp-prog-pct', pct+'% complete');
  const pb = document.getElementById('rrp-prog-bar');
  if(pb) pb.style.width = pct+'%';

  // Mini calendar
  const calEl = document.getElementById('rrp-mini-cal');
  const calTitle = document.getElementById('rrp-cal-title');
  if(calEl){
    const d = new Date();
    const year = d.getFullYear(); const month = d.getMonth();
    if(calTitle) calTitle.textContent = d.toLocaleString('default',{month:'long'})+' '+year;
    const firstDay = new Date(year,month,1).getDay();
    const daysInMonth = new Date(year,month+1,0).getDate();
    // Task dates set
    const taskDays = new Set(
      allRems.filter(r=>!r.sent && r.due && r.due.slice(0,7)===(year+'-'+String(month+1).padStart(2,'0')))
             .map(r=>parseInt(r.due.slice(8,10)))
    );
    const todayDay = d.getDate();
    let html = ['Su','Mo','Tu','We','Th','Fr','Sa'].map(d=>`<div class="rrp-cal-cell hdr">${d}</div>`).join('');
    for(let i=0;i<firstDay;i++) html+=`<div class="rrp-cal-cell"></div>`;
    for(let day=1;day<=daysInMonth;day++){
      const isToday = day===todayDay;
      const hasTask = taskDays.has(day);
      const ds = year+'-'+String(month+1).padStart(2,'0')+'-'+String(day).padStart(2,'0');
      const isSel = _rrpSelDate === ds;
      let cls = isToday?'today-cell':hasTask?'has-task':'';
      if(isSel) cls += ' rrp-sel-day';
      const click = hasTask ? `onclick="rrpCalSelectDay('${ds}')"` : '';
      html+=`<div class="rrp-cal-cell ${cls}" ${click}>${day}</div>`;
    }
    calEl.innerHTML = html;
  }

  // This week upcoming
  const upEl = document.getElementById('rrp-upcoming-list');
  if(upEl){
    const weekEnd = new Date(); weekEnd.setDate(weekEnd.getDate()+7);
    const weekTasks = undone
      .filter(r=>r.due && (r.due.slice(0,10)>=todayStr) && new Date(r.due.slice(0,10)+'T00:00:00')<=weekEnd)
      .sort((a,b)=>(a.due||'').localeCompare(b.due||''))
      .slice(0,5);
    if(!weekTasks.length){
      upEl.innerHTML = `<span style="font-size:11px;color:var(--muted)">No tasks this week</span>`;
    } else {
      upEl.innerHTML = weekTasks.map(r=>{
        const dStr = r.due.slice(0,10);
        const label = dStr===todayStr?'Today':new Date(dStr+'T00:00:00').toLocaleDateString('en-US',{weekday:'short',month:'short',day:'numeric'});
        const dotColor = dStr===todayStr?'var(--red)':'var(--blue)';
        return `<div class="rrp-upcoming-item">
          <div class="rrp-up-dot" style="background:${dotColor}"></div>
          <div><div class="rrp-up-text">${esc(r.title||'')}</div><div class="rrp-up-date">${label}</div></div>
        </div>`;
      }).join('');
    }
  }

  // Awaiting / No date
  const ndEl = document.getElementById('rrp-nodate-list');
  if(ndEl){
    const noDate = undone.filter(r=>!r.due).slice(0,5);
    if(!noDate.length){
      ndEl.innerHTML = `<span style="font-size:11px;color:var(--muted)">None</span>`;
    } else {
      ndEl.innerHTML = noDate.map(r=>`<div class="rrp-await-item">
        <div class="rrp-up-dot" style="background:var(--accent2);margin-top:3px"></div>
        <div><div class="rrp-up-text">${esc(r.title||'')}</div><div class="rrp-up-date">No date set</div></div>
      </div>`).join('');
    }
  }
}

function rrpCalSelectDay(ds){
  // Toggle: clicking the same date again clears the filter
  _rrpSelDate = (_rrpSelDate === ds) ? null : ds;
  _renderRightPanel();
  _renderRemChecklist();
  // Update checklist title to show active date filter
  const title = document.getElementById('rem-checklist-title');
  if(title && _rrpSelDate){
    const d = new Date(_rrpSelDate+'T00:00:00');
    const label = d.toLocaleDateString('en-US',{weekday:'short',month:'short',day:'numeric'});
    title.textContent = _getListTitle() + '  —  ' + label;
  }
}

function _renderRemListPanel(){
  const lists = getRemLists();
  const rems  = DATA.reminders||[];
  const el    = document.getElementById('rem-list-items');
  if(!el) return;
  el.innerHTML = lists.map(l=>{
    const count = rems.filter(r=>!r.sent && (r.list_id||r.category||'personal')===l.id).length;
    const active = (_remListId===l.id) ? ' active' : '';
    return `<div class="rem-list-item${active}" onclick="selectRemList('${l.id}')">
      <span class="rem-list-name">${l.icon||'🔵'} ${esc(l.name)}</span>
      <span class="rem-list-count">${count}</span>
    </div>`;
  }).join('');
}

function selectRemFilter(filter){
  _remPageFilter = filter;
  _remListId     = null;
  _rrpSelDate    = null;
  _updateRemTiles();
  _renderRemListPanel();
  _renderRemChecklist();
}

function selectRemList(id){
  _remListId     = id;
  _remPageFilter = 'list';
  _rrpSelDate    = null;
  _updateRemTiles();
  _renderRemListPanel();
  _renderRemChecklist();
  remMobileShow();
}

function _getFilteredRems(){
  const rems = DATA.reminders||[];
  const now  = new Date();
  const todayStr = localToday();
  let filtered;
  if(_remListId){
    filtered = rems.filter(r=>(r.list_id||r.category||'personal')===_remListId);
  } else if(_remPageFilter==='today'){
    filtered = rems.filter(r=>!r.sent && (r.due||'').slice(0,10)===todayStr);
  } else if(_remPageFilter==='scheduled'){
    filtered = rems.filter(r=>!r.sent && r.due);
  } else if(_remPageFilter==='completed'){
    filtered = rems.filter(r=>r.sent);
  } else if(_remPageFilter==='overdue-only'){
    filtered = rems.filter(r=>isOverdue(r));
  } else {
    // "all" / pending = not sent AND not overdue (overdue has its own bucket)
    filtered = rems.filter(r=>!r.sent && !isOverdue(r));
  }
  return filtered;
}

function _getListTitle(){
  if(_remListId){
    const l = getRemLists().find(l=>l.id===_remListId);
    return l ? l.icon+' '+l.name : 'List';
  }
  const map={all:'⏰ Pending',today:'📅 Today',scheduled:'🗓 Scheduled',completed:'✅ Completed','overdue-only':'🔴 Overdue'};
  return map[_remPageFilter]||'Reminders';
}

function _renderRemChecklist(){
  const title  = document.getElementById('rem-checklist-title');
  const body   = document.getElementById('rem-checklist-body');
  const delBtn = document.getElementById('rem-delete-list-btn');
  if(title){
    if(_rrpSelDate){
      const d = new Date(_rrpSelDate+'T00:00:00');
      const label = d.toLocaleDateString('en-US',{weekday:'short',month:'short',day:'numeric'});
      title.innerHTML = _getListTitle() + ` <span style="font-size:11px;font-weight:500;color:var(--accent);cursor:pointer;border:1px solid var(--accent);border-radius:10px;padding:1px 8px" onclick="rrpCalSelectDay('${_rrpSelDate}')" title="Clear date filter">📅 ${label} ✕</span>`;
    } else {
      title.textContent = _getListTitle();
    }
  }
  if(delBtn) delBtn.style.display = _remListId ? '' : 'none';
  if(!body) return;

  let items = _getFilteredRems();
  if(_rrpSelDate){
    items = items.filter(r => r.due && r.due.slice(0,10) === _rrpSelDate);
  }
  const undone = items.filter(r=>!r.sent).sort((a,b)=>(a.due||'').localeCompare(b.due||''));
  const done   = items.filter(r=>r.sent);
  const now    = new Date();
  const todayStr = localToday();

  // Helper to get priority for an item
  const getPriority = r => {
    if(!r.due) return 'low';
    const dueDate = (r.due||'').slice(0,10);
    const dueMs = new Date(r.due.replace(' ','T'));
    if(dueMs <= now) return 'high';
    if(dueDate === todayStr) return 'high';
    return 'medium';
  };

  // Helper to format date nicely with relative labels
  const formatDate = (dateStr) => {
    if(!dateStr) return '';
    const date = new Date(dateStr+'T00:00:00');
    const todayMs = new Date(todayStr+'T00:00:00');
    const diff = Math.round((date - todayMs) / 86400000);
    const dayName = date.toLocaleDateString('en-US',{weekday:'short'});
    const monthDay = date.toLocaleDateString('en-US',{month:'short',day:'numeric'});
    if(diff === 0) return 'Today';
    if(diff === 1) return `Tomorrow \u00b7 ${dayName}`;
    if(diff > 1 && diff <= 7) return `${dayName} \u00b7 in ${diff} days`;
    if(diff < 0) return `${monthDay} (${Math.abs(diff)}d ago)`;
    return `${monthDay} \u00b7 ${dayName}`;
  };

  // Render a single compact reminder row
  const renderRow = r => {
    const dueDate = (r.due||'').slice(0,10);
    const dueMs   = r.due ? new Date(r.due.replace(' ','T')) : null;
    const isOver  = !r.sent && dueMs && dueMs <= now;
    const isToday = !isOver && dueDate === todayStr;
    const priority = r.priority || getPriority(r);
    const prioShort = {high:'High',medium:'Med',low:'Low'}[priority]||'Med';
    // date badge
    let dateBadge = '';
    if(!r.due) dateBadge = `<span class="rem-badge-nodate">No date</span>`;
    else if(isToday) dateBadge = `<span class="rem-badge-today">Today</span>`;
    else if(dueDate){
      const dMs=new Date(dueDate+'T00:00:00'),tMs=new Date(todayStr+'T00:00:00');
      const diff=Math.round((dMs-tMs)/86400000);
      const dn=dMs.toLocaleDateString('en-US',{weekday:'short'});
      const md=dMs.toLocaleDateString('en-US',{month:'short',day:'numeric'});
      let lbl;
      if(diff===1) lbl=`Tomorrow \u00b7 ${dn}`;
      else if(diff>1&&diff<=7) lbl=`${dn} \u00b7 in ${diff} days`;
      else if(diff<0) lbl=`${md} (${Math.abs(diff)}d ago)`;
      else lbl=`${md} \u00b7 ${dn}`;
      const cls=isOver?'rem-item-date overdue':'rem-item-date';
      dateBadge=`<span class="${cls}">${lbl}</span>`;
    }
    return `<div class="rem-item-row${r.sent?' is-done':''}" id="remrow-${r.id}">
      <div class="rem-check${r.sent?' done':''}" onclick="toggleRemDone('${r.id}')">${r.sent?'✓':''}</div>
      <div class="rem-item-main">
        <div class="rem-item-title" onclick="openRemInlineEdit('${r.id}')">${esc(r.title||'')}</div>
        <div class="rem-item-meta">
          ${dateBadge}
          <div class="rem-item-priority-dot ${priority}"></div>
          <span class="rem-item-prio-lbl ${priority}">${prioShort}</span>
        </div>
      </div>
      <button class="rem-item-del" onclick="deleteReminder('${r.id}')" title="Delete">✕</button>
    </div>`;
  };

  // Group by date
  const groupByDate = (items) => {
    const groups = { overdue: [], today: [], upcoming: [], nodate: [] };
    items.forEach(r => {
      if(!r.due){
        groups.nodate.push(r);
        return;
      }
      const dueDate = (r.due||'').slice(0,10);
      const dueMs = new Date(r.due.replace(' ','T'));
      if(dueMs <= now && dueDate !== todayStr){
        groups.overdue.push(r);
      } else if(dueDate === todayStr){
        groups.today.push(r);
      } else {
        groups.upcoming.push(r);
      }
    });
    return groups;
  };

  const grouped = groupByDate(undone);
  let html = '';

  // Overdue section
  if(grouped.overdue.length){
    html += `<div class="rem-date-group">
      <div class="rem-date-header overdue">⚠️ Overdue</div>
      ${grouped.overdue.map(renderRow).join('')}
    </div>`;
  }

  // Today section
  if(grouped.today.length){
    html += `<div class="rem-date-group">
      <div class="rem-date-header" style="color:#2563eb">Today</div>
      ${grouped.today.map(renderRow).join('')}
    </div>`;
  }

  // Upcoming section - group by individual dates
  if(grouped.upcoming.length){
    const upcomingByDate = {};
    grouped.upcoming.forEach(r => {
      const date = (r.due||'').slice(0,10);
      if(!upcomingByDate[date]) upcomingByDate[date] = [];
      upcomingByDate[date].push(r);
    });
    Object.keys(upcomingByDate).sort().forEach(date => {
      html += `<div class="rem-date-group">
        <div class="rem-date-header">${formatDate(date)}</div>
        ${upcomingByDate[date].map(renderRow).join('')}
      </div>`;
    });
  }

  // No date section
  if(grouped.nodate.length){
    html += `<div class="rem-date-group">
      <div class="rem-date-header">No Date</div>
      ${grouped.nodate.map(renderRow).join('')}
    </div>`;
  }

  // Add new reminder row at TOP (only when not in completed view)
  if(_remPageFilter!=='completed'){
    const listId = _remListId || 'personal';
    html = `<div class="rem-add-row" style="position:sticky;top:0;z-index:3;padding-bottom:4px">
      <div class="rem-add-plus" onclick="document.getElementById('rem-add-input').focus()">＋</div>
      <input class="rem-add-input" id="rem-add-input" placeholder="New reminder… (press Enter to add)"
        onkeydown="remAddKeydown(event,'${listId}')">
      <input type="date" class="rem-add-due-input" id="rem-add-due" title="Due date">
      <button class="cbtn" style="font-size:11px;padding:4px 12px;flex-shrink:0" onclick="remAddClick('${listId}')">Add</button>
    </div>` + html;
  }

  // Completed section
  if(done.length){
    html += `<div class="rem-completed-section">
      <div class="rem-completed-toggle">
        <div class="rem-completed-header" onclick="toggleRemCompleted(this)">
          <span id="rem-comp-chev">▼</span>
          <span>Completed (${done.length})</span>
        </div>
        <button onclick="clearCompletedReminders()" style="font-size:11px;padding:3px 10px;border-radius:20px;border:1px solid #b5d4f4;background:#e6f1fb;color:#185fa5;cursor:pointer;font-family:'Inter',sans-serif;font-weight:600">Clear all</button>
      </div>
      <div id="rem-comp-list">${done.map(renderRow).join('')}</div>
    </div>`;
  }

  // Empty state
  if(!undone.length && !done.length){
    html = `<div class="rem-empty">
      <div class="rem-empty-icon">⏰</div>
      <p>No reminders here.</p>
      <p style="font-size:12px;color:var(--muted)">Add one above to get started</p>
    </div>`;
    if(_remPageFilter!=='completed'){
      const listId = _remListId||'personal';
      html += `<div class="rem-add-row">
        <div class="rem-add-plus" onclick="document.getElementById('rem-add-input').focus()">＋</div>
        <input class="rem-add-input" id="rem-add-input" placeholder="New reminder… (press Enter to add)"
          onkeydown="remAddKeydown(event,'${listId}')">
        <input type="date" class="rem-add-due-input" id="rem-add-due" title="Due date">
        <button class="cbtn" style="font-size:11px;padding:4px 12px;flex-shrink:0" onclick="remAddClick('${listId}')">Add</button>
      </div>`;
    }
  }

  body.innerHTML = html;
}

function remAddKeydown(e, listId){
  if(e.key!=='Enter') return;
  _doAddReminder(listId);
}

function remAddClick(listId){
  _doAddReminder(listId);
}

function _doAddReminder(listId){
  const titleEl = document.getElementById('rem-add-input');
  const dueEl   = document.getElementById('rem-add-due');
  const title   = titleEl?.value.trim();
  if(!title){ titleEl?.focus(); return; }
  const now=localNow();
  const dueStr = dueEl?.value ? dueEl.value+' 09:00' : '';
  const rem = {
    id:uid(),type:'reminder',
    list_id: listId, category: listId,
    title, body:'', tags:[], color:'default',
    due: dueStr, repeat:'none', sent:false, pinned:false,
    created:now, updated:now, attachments:[]
  };
  if(!DATA.reminders) DATA.reminders=[];
  DATA.reminders.push(rem);
  // ── Auto-sync to Google Calendar (quick add, default 30 min reminder) ──
  if(rem.due){
    try{
      const [dp,tp]=rem.due.split(' ');
      const sISO=dp+'T'+(tp||'09:00')+':00';
      const eD=new Date(sISO); eD.setHours(eD.getHours()+1);
      const pd=n=>String(n).padStart(2,'0');
      const eISO=eD.getFullYear()+'-'+pd(eD.getMonth()+1)+'-'+pd(eD.getDate())+'T'+pd(eD.getHours())+':'+pd(eD.getMinutes())+':00';
      addReminderToGoogleCalendar(rem.id, rem.title, sISO, eISO, '', 30);
    }catch(gcErr){ console.warn('GCal sync skipped:',gcErr); }
  }
  renderAll();
  renderRemindersPage();
  saveToFirebase();
  // re-focus the input so user can keep adding
  setTimeout(()=>{ const inp=document.getElementById('rem-add-input'); if(inp){ inp.value=''; inp.focus(); } },50);
}

async function toggleRemDone(id){
  const rem = (DATA.reminders||[]).find(r=>r.id===id);
  if(!rem) return;

  // If it's a repeating reminder being marked done → reschedule instead of completing
  if(!rem.sent && rem.repeat && rem.repeat !== 'none' && rem.due){
    const base = new Date(rem.due.slice(0,10) + 'T00:00:00');
    let next = new Date(base);
    if(rem.repeat === 'daily'){
      next.setDate(next.getDate() + 1);
    } else if(rem.repeat === 'weekly'){
      next.setDate(next.getDate() + 7);
    } else if(rem.repeat === 'monthly'){
      next.setMonth(next.getMonth() + 1);
    }
    // Build new due string preserving original time (HH:MM)
    const timePart = rem.due.length > 10 ? rem.due.slice(10) : 'T09:00';
    const yyyy = next.getFullYear();
    const mm   = String(next.getMonth()+1).padStart(2,'0');
    const dd   = String(next.getDate()).padStart(2,'0');
    rem.due  = yyyy + '-' + mm + '-' + dd + timePart;
    rem.sent = false; // stays active, rescheduled to next occurrence
  } else {
    // Non-repeating → normal toggle done/undone
    rem.sent = !rem.sent;
  }

  renderAll();
  renderRemindersPage();
  await saveToFirebase();
}

async function deleteReminder(id){
  if(!confirm('Delete this reminder?')) return;
  // ── Delete from Google Calendar first ─────────────────────────────────────
  deleteReminderFromGoogleCalendar(id);
  DATA.reminders = (DATA.reminders||[]).filter(r=>r.id!==id);
  renderAll();
  renderRemindersPage();
  await saveToFirebase();
}

function openRemInlineEdit(id){
  editItem(id); // opens existing modal for full edit
}

function toggleRemCompleted(btn){
  const list = document.getElementById('rem-comp-list');
  const chev = document.getElementById('rem-comp-chev');
  if(!list) return;
  const hidden = list.style.display==='none';
  list.style.display = hidden ? '' : 'none';
  if(chev) chev.textContent = hidden ? '▼' : '▶';
}

async function clearCompletedReminders(){
  const count = (DATA.reminders||[]).filter(r=>r.sent).length;
  if(!count) return;
  if(!confirm(`Delete all ${count} completed reminder${count>1?'s':''}? This cannot be undone.`)) return;
  DATA.reminders = (DATA.reminders||[]).filter(r=>!r.sent);
  renderAll();
  updateDashboardWidgets();
  await saveToFirebase();
  toast('Completed reminders cleared ✓','success');
}


async function createRemList(){
  const name = prompt('List name:','');
  if(!name||!name.trim()) return;
  const icons = ['🔵','🟣','🟡','🟠','🔴','🟢','⚫'];
  const icon  = icons[Math.floor(Math.random()*icons.length)];
  const list  = {id:'rl'+uid(), name:name.trim(), icon};
  if(!DATA.rem_lists) DATA.rem_lists=DEFAULT_REM_LISTS.slice();
  DATA.rem_lists.push(list);
  renderRemindersPage();
  await saveToFirebase();
}

async function deleteCurrentRemList(){
  if(!_remListId) return;
  const l = getRemLists().find(l=>l.id===_remListId);
  if(!l) return;
  if(!confirm(`Delete list "${l.name}" and all its reminders?`)) return;
  DATA.reminders = (DATA.reminders||[]).filter(r=>(r.list_id||r.category||'personal')!==_remListId);
  DATA.rem_lists = DATA.rem_lists.filter(l=>l.id!==_remListId);
  _remListId=null; _remPageFilter='all';
  renderAll();
  renderRemindersPage();
  await saveToFirebase();
}

/* == MOBILE PANEL NAVIGATION == */
function isMobile(){ return window.innerWidth <= 640; }

function notesMobileShow(panel){
  if(!isMobile()) return;
  const cols = document.querySelector('.notes-columns');
  if(!cols) return;
  cols.classList.remove('show-list','show-editor');
  if(panel==='list')   cols.classList.add('show-list');
  if(panel==='editor') cols.classList.add('show-editor');
}

function notesMobileBack(to){
  if(!isMobile()) return;
  const cols = document.querySelector('.notes-columns');
  if(!cols) return;
  cols.classList.remove('show-list','show-editor');
  if(to==='list') cols.classList.add('show-list');
  // to==='folders' → remove both classes → shows folders
}

function remMobileShow(){
  const cols = document.querySelector('.rem-columns');
  if(cols) cols.classList.add('show-checklist');
}

function remMobileBack(){
  // Reset list selection so the user can pick a different list
  _remListId = null;
  _remPageFilter = 'all';
  const cols = document.querySelector('.rem-columns');
  if(cols) cols.classList.remove('show-checklist');
  _renderRemListPanel();
  _renderRemChecklist();
}

// When createNewNote is called on mobile, go straight to editor
const _origCreateNewNote = createNewNote;
createNewNote = async function(){
  await _origCreateNewNote();
  notesMobileShow('editor');
};

function showPage(page, btn){
  // Lock daybook when navigating away
  if(page !== 'daybook' && dbGetPin()) _dbUnlocked = false;
  // Lock investments when navigating away (uses same PIN)
  if(page !== 'investments' && dbGetPin()) _invUnlocked = false;
  // Lock important dates when navigating away (uses same PIN)
  if(page !== 'impdates' && dbGetPin()) _impUnlocked = false;
  const pages = ['dashboard','notes','reminders','sticky','journal','routine','tasknotes','finance','daybook','shopping','investments','impdates'];
  const displayMap = {dashboard:'',notes:'flex',reminders:'flex',sticky:'flex',journal:'flex',routine:'flex',tasknotes:'flex',finance:'flex',daybook:'flex',shopping:'flex',investments:'flex',impdates:'flex'};
  pages.forEach(p=>{
    const el=document.getElementById('page-'+p);
    if(el){
      const showing = p===page;
      el.style.display = showing ? (displayMap[p]||'') : 'none';
      if(showing){
        el.classList.remove('page-entering');
        void el.offsetWidth; // force reflow
        el.classList.add('page-entering');
      }
    }
  });
  // Reset scroll on page switch (desktop: scroll area, mobile: window)
  const sa = document.getElementById('page-scroll-area');
  // Hide scroll area for full-height pages (daybook, shopping are outside it)
  if(sa) sa.style.display = (page==='daybook'||page==='shopping'||page==='investments') ? 'none' : '';
  if(sa) sa.scrollTop = 0;
  window.scrollTo(0,0);

  // 6. Icons in title
  const titles = {
    dashboard:'📋 Dashboard',
    notes:'📝 Notes',
    reminders:'⏰ Reminders',
    sticky:'📌 Sticky Notes',
    journal:'📈 Trading Journal',
    routine:'🔁 Routine',
    tasknotes:'✍️ Task Notes',
    finance:'💰 Finance Tracker',
    daybook:'📖 Daybook',
    shopping:'🛒 Shopping',
    investments:'📊 Investments',
    impdates:'🗓️ Important Dates'
  };
  document.getElementById('page-title').textContent = titles[page]||'📋 Dashboard';

  // 5. Context-aware actions
  ['dashboard','notes','reminders','sticky','journal','routine','tasknotes','finance','daybook','shopping','investments','impdates'].forEach(p=>{
    const el=document.getElementById('ctx-'+p);
    if(el) el.style.display=p===page?'flex':'none';
  });
  // legacy search-wrap compat
  const sw=document.getElementById('topbar-search-wrap');
  if(sw) sw.style.display='flex';

  document.querySelectorAll('.nav-item').forEach(b=>b.classList.remove('active'));
  if(btn) btn.classList.add('active');

  if(page==='dashboard'){
    const remSec      = document.getElementById('rem-section');
    const notesSec    = document.getElementById('notes-section');
    const remHdr      = document.getElementById('rem-sec-header');
    const remCatFil   = document.getElementById('rem-cat-filter');
    const notesHdr    = document.getElementById('notes-sec-header');
    const notesCatFil = document.getElementById('notes-cat-filter');
    if(remSec)      remSec.style.display      = '';
    if(remHdr)      remHdr.style.display      = '';
    if(remCatFil)   remCatFil.style.display   = 'flex';
    if(notesSec)    notesSec.style.display    = '';
    if(notesHdr)    notesHdr.style.display    = '';
    if(notesCatFil) notesCatFil.style.display = 'flex';
    document.querySelectorAll('.ncard,.lrow').forEach(c=>c.style.display='');
    document.querySelectorAll('.filter-empty').forEach(e=>e.remove());
    document.querySelectorAll('.stat-card').forEach(c=>c.classList.remove('active'));
  }
  if(page==='notes'){
    renderNotesPage();
    // Always reset to folders panel on mobile when navigating to Notes
    if(isMobile()){
      const cols = document.querySelector('.notes-columns');
      if(cols) cols.classList.remove('show-list','show-editor');
    }
  }
  if(page==='reminders'){
    // Always reset to 'all' view when navigating to reminders, so mobile users aren't locked to one list
    _remListId = null;
    _remPageFilter = 'all';
    _rrpSelDate = null;
    renderRemindersPage(true); // true = reset mobile panel to lists view
  }
  if(page==='journal')    renderJournal();
  if(page==='routine')  showRoutineView('today');
  if(page==='finance')  renderFinance();
  if(page==='daybook'){
    const pin = dbGetPin();
    if(pin && !_dbUnlocked){
      dbShowLock();
    } else {
      dbHideLock();
      dbRender();
      dbUpdateCounts();
    }
  }
  if(page==='shopping') shopRender();
  if(page==='impdates'){
    const pin = dbGetPin();
    if(pin && !_impUnlocked){
      impShowLockScreen();
    } else {
      impHideLockScreen();
      impRenderPage();
    }
  }
  if(page==='investments'){
    const pin = dbGetPin();
    if(pin && !_invUnlocked){
      invShowLockScreen();
    } else {
      invHideLockScreen();
      invRender();
    }
  }
}

/* -- STICKY NOTES PAGE --------------------------- */
const SP_COLORS = [
  {id:'yellow',  bg:'#fde68a', label:'Yellow'},
  {id:'orange',  bg:'#fb923c', label:'Orange'},
  {id:'pink',    bg:'#f472b6', label:'Pink'},
  {id:'green',   bg:'#86efac', label:'Green'},
  {id:'blue',    bg:'#60a5fa', label:'Blue'},
  {id:'purple',  bg:'#c084fc', label:'Purple'},
  {id:'red',     bg:'#f87171', label:'Red'},
  {id:'teal',    bg:'#2dd4bf', label:'Teal'},
  {id:'white',   bg:'#f8fafc', label:'White'},
  {id:'sky',     bg:'#38bdf8', label:'Sky'},
  {id:'lime',    bg:'#a3e635', label:'Lime'},
  {id:'amber',   bg:'#fbbf24', label:'Amber'},
];
let activeSPColor = 'yellow';
let STICKIES = [];
let ARCHIVED = [];
function initSticky(){
  const wrap = document.getElementById('sp-colors');
  wrap.innerHTML = SP_COLORS.map(c=>`
    <div class="sp-dot${c.id===activeSPColor?' active':''}"
      style="background:${c.bg}"
      title="${c.label}"
      onclick="pickSPColor('${c.id}')"
      id="spdot-${c.id}">
    </div>`).join('');
  // Load from DATA (Firebase sync) — migrate from localStorage if needed
  STICKIES = DATA.stickies || [];
  ARCHIVED = DATA.archived || [];
  // One-time migration: if DATA empty but localStorage has data, migrate it
  if(!STICKIES.length){
    try{
      const lsStick = JSON.parse(localStorage.getItem('mynotes_stickies')||'[]');
      const lsArch  = JSON.parse(localStorage.getItem('mynotes_stickies_archive')||'[]');
      if(lsStick.length||lsArch.length){
        STICKIES = lsStick;
        ARCHIVED = lsArch;
        DATA.stickies = STICKIES;
        DATA.archived = ARCHIVED;
        saveToFirebase();
        localStorage.removeItem('mynotes_stickies');
        localStorage.removeItem('mynotes_stickies_archive');
        toast('Sticky notes migrated to cloud sync ✓','success');
      }
    }catch{}
  }
  renderStickyBoard();
  renderArchiveGrid();
}

/* 1. color picker */
function pickSPColor(id){
  activeSPColor=id;
  document.querySelectorAll('.sp-dot').forEach(d=>d.classList.remove('active'));
  const el=document.getElementById('spdot-'+id);
  if(el) el.classList.add('active');
}

function addSticky(){
  const colorObj = SP_COLORS.find(c=>c.id===activeSPColor)||SP_COLORS[0];
  const now=localNow();
  const s = {
    id: Math.random().toString(36).slice(2,10),
    text: '',
    bg: colorObj.bg,
    colorId: colorObj.id,
    created: now,
    updated: now,
    pinned: false,
    tags: []
  };
  STICKIES.unshift(s);
  saveStickies(true);
  renderStickyBoard();
  setTimeout(()=>{
    const el=document.getElementById('stext-'+s.id);
    if(el){ el.focus(); placeCursorAtEnd(el); }
  }, 50);
}

/* 7. animated delete */
function deleteSticky(id){
  if(!confirm('Delete this sticky?')) return;
  const card = document.getElementById('scard-'+id);
  if(card){
    card.classList.add('removing');
    setTimeout(()=>{
      STICKIES=STICKIES.filter(s=>s.id!==id);
      saveStickies(true); renderStickyBoard();
    }, 200);
  } else {
    STICKIES=STICKIES.filter(s=>s.id!==id);
    saveStickies(true); renderStickyBoard();
  }
}

/* 6. archive */
function archiveSticky(id){
  const s = STICKIES.find(s=>s.id===id);
  if(!s) return;
  const card = document.getElementById('scard-'+id);
  if(card){ card.classList.add('removing'); }
  setTimeout(()=>{
    STICKIES = STICKIES.filter(x=>x.id!==id);
    ARCHIVED.unshift({...s, archivedAt: localToday()});
    saveStickies(true);
    renderStickyBoard();
    renderArchiveGrid();
  }, 200);
}

function restoreSticky(id){
  const s = ARCHIVED.find(a=>a.id===id);
  if(!s) return;
  ARCHIVED = ARCHIVED.filter(a=>a.id!==id);
  delete s.archivedAt;
  STICKIES.unshift(s);
  saveStickies(true);
  renderStickyBoard();
  renderArchiveGrid();
  toast('Sticky restored','success');
}

function toggleArchivePanel(){
  const panel = document.getElementById('sp-archive-panel');
  if(panel) panel.classList.toggle('open');
}

function renderArchiveGrid(){
  const grid = document.getElementById('sp-archive-grid');
  if(!grid) return;
  localStorage.setItem('mynotes_stickies_archive', JSON.stringify(ARCHIVED));
  if(!ARCHIVED.length){
    grid.innerHTML='<span style="font-size:12px;color:var(--muted)">No archived stickies</span>';
    return;
  }
  grid.innerHTML = ARCHIVED.map(s=>`
    <div style="background:${s.bg};border-radius:8px;padding:10px 12px;min-width:150px;max-width:200px;opacity:.75;position:relative">
      <div style="font-size:10px;color:rgba(0,0,0,.4);margin-bottom:4px">📦 Archived ${s.archivedAt||''}</div>
      <div style="font-size:12px;color:rgba(0,0,0,.75);line-height:1.4;max-height:60px;overflow:hidden">${escHtml(s.text||'(empty)')}</div>
      <div style="display:flex;gap:5px;margin-top:8px">
        <button onclick="restoreSticky('${s.id}')" style="font-size:10px;background:rgba(0,0,0,.12);border:none;border-radius:5px;padding:2px 8px;cursor:pointer;color:rgba(0,0,0,.6);font-weight:600">↩ Restore</button>
        <button onclick="permDeleteArchived('${s.id}')" style="font-size:10px;background:rgba(180,0,0,.12);border:none;border-radius:5px;padding:2px 8px;cursor:pointer;color:rgba(100,0,0,.7);font-weight:600">✕</button>
      </div>
    </div>`).join('');
}

function permDeleteArchived(id){
  if(!confirm('Permanently delete?')) return;
  ARCHIVED = ARCHIVED.filter(a=>a.id!==id);
  renderArchiveGrid();
}

function saveStickyText(id, el, immediate=true){
  const s=STICKIES.find(s=>s.id===id);
  if(s){
    s.text=el.innerText;
    s.updated=localNow();
    const dateEl=document.getElementById('sdate-'+id);
    if(dateEl) dateEl.innerHTML=stickyDateHtml(s);
    saveStickies(immediate);
  }
}

// called oninput — debounced save (not immediate)
function onInputStickyText(id, el){
  saveStickyText(id, el, false);
}

function saveStickyTag(id, input){
  const val=input.value.trim().replace(/,/g,'');
  if(!val) return;
  const s=STICKIES.find(s=>s.id===id);
  if(!s) return;
  s.tags=s.tags||[];
  if(!s.tags.includes(val)) s.tags.push(val);
  input.value='';
  saveStickies(true);
  renderStickyBoard();
}

function removeStickyTag(id, tag){
  const s=STICKIES.find(s=>s.id===id);
  if(s){ s.tags=(s.tags||[]).filter(t=>t!==tag); saveStickies(true); renderStickyBoard(); }
}

function changeStickyColor(id, newColor, colorId){
  const s=STICKIES.find(s=>s.id===id);
  if(s){ s.bg=newColor; s.colorId=colorId||s.colorId; saveStickies(true); renderStickyBoard(); }
}

/* 3. pin */
function toggleStickyPin(id){
  const s=STICKIES.find(s=>s.id===id);
  if(!s) return;
  s.pinned=!s.pinned;
  saveStickies(true); renderStickyBoard();
}

let _stickySaveTimer = null;

function saveStickies(immediate=false){
  DATA.stickies = STICKIES;
  DATA.archived = ARCHIVED;
  const el=document.getElementById('nav-sticky-count');
  if(el) el.textContent=STICKIES.length;
  const cnt=document.getElementById('sp-count');
  if(cnt) cnt.textContent=STICKIES.length+' stick'+(STICKIES.length===1?'y':'ies');
  // debounce Firebase save — 1.5s after last change (immediate for delete/pin/archive)
  clearTimeout(_stickySaveTimer);
  if(immediate){
    saveToFirebase();
  } else {
    _stickySaveTimer = setTimeout(()=>saveToFirebase(), 1500);
  }
}

function placeCursorAtEnd(el){
  const r=document.createRange(),s=window.getSelection();
  r.selectNodeContents(el);r.collapse(false);
  s.removeAllRanges();s.addRange(r);
}

function escHtml(s){
  return String(s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
}

/* 5. timestamps helper */
function stickyDateHtml(s){
  let html=`<span>📅 ${s.created?s.created.slice(0,10):''}</span>`;
  if(s.updated&&s.updated!==s.created) html+=`<span style="margin-left:6px;opacity:.7">✏️ ${s.updated.slice(0,10)}</span>`;
  return html;
}

function renderStickyBoard(){
  const board=document.getElementById('sp-board');
  const empty=document.getElementById('sp-empty');
  if(!board) return;
  // always keep sidebar count in sync
  const navEl=document.getElementById('nav-sticky-count');
  if(navEl) navEl.textContent=STICKIES.length;
  const cntEl=document.getElementById('sp-count');
  if(cntEl) cntEl.textContent=STICKIES.length+' stick'+(STICKIES.length===1?'y':'ies');
  board.querySelectorAll('.sticky-card').forEach(el=>el.remove());

  // filter
  let visible = [...STICKIES];
  // 3. pinned first
  visible.sort((a,b)=>(b.pinned?1:0)-(a.pinned?1:0));

  if(!visible.length){
    if(empty) empty.style.display='flex';
    return;
  }
  if(empty) empty.style.display='none';

  visible.forEach(s=>{
    const card=document.createElement('div');
    card.className='sticky-card';
    card.style.background=s.bg;
    card.id='scard-'+s.id;
    card.style.width  = s.width  || '220px';
    card.style.height = s.height || '170px';

    const tagHtml=(s.tags||[]).map(t=>`
      <span class="sticky-tag">#${escHtml(t)}<span onclick="removeStickyTag('${s.id}','${escHtml(t)}')"
        style="margin-left:3px;cursor:pointer;opacity:.6">×</span></span>`).join('');

    card.innerHTML=`
      ${s.pinned?'<div class="sticky-pinned-badge">📌 PINNED</div>':''}
      <div class="sticky-card-header">
        <span class="sticky-card-date" id="sdate-${s.id}">${stickyDateHtml(s)}</span>
        <div style="display:flex;gap:4px">
          <button class="sticky-pin-btn${s.pinned?' pinned':''}" onclick="toggleStickyPin('${s.id}')" title="${s.pinned?'Unpin':'Pin to top'}">${s.pinned?'📌':'📌'}</button>
          <button class="sticky-archive-btn" onclick="archiveSticky('${s.id}')" title="Archive">🗂</button>
          <button class="sticky-card-del" onclick="deleteSticky('${s.id}')">✕</button>
        </div>
      </div>
      <div class="sticky-card-body"
        id="stext-${s.id}"
        contenteditable="true"
        oninput="onInputStickyText('${s.id}',this)"
        onblur="saveStickyText('${s.id}',this,true)"
      >${escHtml(s.text)}</div>
      <div class="sticky-tags">${tagHtml}<input class="sticky-tag-input" placeholder="+ tag"
        onkeydown="if(event.key==='Enter'||event.key===','){saveStickyTag('${s.id}',this);event.preventDefault()}"
        onblur="if(this.value.trim())saveStickyTag('${s.id}',this)"></div>
      <div class="sticky-card-footer">
        <div style="display:flex;gap:4px;flex-wrap:wrap;align-items:center">
          ${SP_COLORS.map(c=>`<div onclick="changeStickyColor('${s.id}','${c.bg}','${c.id}')"
            title="${c.label}"
            style="width:13px;height:13px;border-radius:4px;background:${c.bg};cursor:pointer;
            border:2px solid ${s.bg===c.bg?'rgba(0,0,0,.55)':'transparent'};
            flex-shrink:0;transition:transform 0.12s"
            onmouseover="this.style.transform='scale(1.35)'"
            onmouseout="this.style.transform='scale(1)'">
          </div>`).join('')}
        </div>
      </div>
      <div class="sticky-resize-handle" id="srh-${s.id}" title="Drag to resize">
        <svg viewBox="0 0 12 12" xmlns="http://www.w3.org/2000/svg">
          <line x1="10" y1="2" x2="2" y2="10" stroke="rgba(0,0,0,.55)" stroke-width="1.8" stroke-linecap="round"/>
          <line x1="10" y1="6" x2="6" y2="10" stroke="rgba(0,0,0,.55)" stroke-width="1.8" stroke-linecap="round"/>
          <line x1="10" y1="10" x2="10" y2="10" stroke="rgba(0,0,0,.55)" stroke-width="1.8" stroke-linecap="round"/>
        </svg>
      </div>`;

    board.insertBefore(card, empty||null);

    const handle = card.querySelector('.sticky-resize-handle');
    let startX, startY, startW, startH;

    function onDown(e){
      e.preventDefault(); e.stopPropagation();
      const touch = e.touches ? e.touches[0] : e;
      startX=touch.clientX; startY=touch.clientY;
      startW=card.offsetWidth; startH=card.offsetHeight;
      document.addEventListener('mousemove', onMove);
      document.addEventListener('mouseup',   onUp);
      document.addEventListener('touchmove', onMove, {passive:false});
      document.addEventListener('touchend',  onUp);
    }

    function onMove(e){
      e.preventDefault();
      const touch = e.touches ? e.touches[0] : e;
      const newW = Math.max(190, startW + (touch.clientX - startX));
      const newH = Math.max(140, startH + (touch.clientY - startY));
      card.style.width  = newW + 'px';
      card.style.height = newH + 'px';
    }

    function onUp(){
      const st = STICKIES.find(x=>x.id===s.id);
      if(st){ st.width=card.style.width; st.height=card.style.height; saveStickies(true); }
      document.removeEventListener('mousemove', onMove);
      document.removeEventListener('mouseup',   onUp);
      document.removeEventListener('touchmove', onMove);
      document.removeEventListener('touchend',  onUp);
    }

    handle.addEventListener('mousedown',  onDown);
    handle.addEventListener('touchstart', onDown, {passive:false});
  });
}

/* -- LIVE CLOCK ---------------------------------- */
function startClock(){
  function tick(){
    const now = new Date();
    const fmtTime = tz => now.toLocaleTimeString('en-US',{
      timeZone:tz, hour:'2-digit', minute:'2-digit', second:'2-digit', hour12:true
    });
    const fmtDate = tz => now.toLocaleDateString('en-US',{
      timeZone:tz, weekday:'short', month:'short', day:'numeric'
    });
    document.getElementById('clk-ist-time').textContent = fmtTime('Asia/Kolkata');
    document.getElementById('clk-cst-time').textContent = fmtTime('America/Chicago');
    document.getElementById('clk-sgt-time').textContent = fmtTime('Asia/Singapore');
    document.getElementById('clk-ist-date').textContent = fmtDate('Asia/Kolkata');
    document.getElementById('clk-cst-date').textContent = fmtDate('America/Chicago');
    document.getElementById('clk-sgt-date').textContent = fmtDate('Asia/Singapore');
  }
  tick();
  setInterval(tick, 1000);
}

/* -- TRADING JOURNAL ----------------------------- */
let TRADES = [];
let ROUTINES = [];

function updateJournalCount(){
  const el = document.getElementById('nav-journal-count');
  if(el) el.textContent = TRADES.length;
}

async function saveTrades(){
  // Save trades into DATA.trades and push to Firebase
  DATA.trades = TRADES;
  updateJournalCount();
  await saveToFirebase();
}

function uid_trade(){ return 'T'+Date.now().toString(36)+Math.random().toString(36).slice(2,5); }

function calcPnL(trade){
  if(trade.instrument === 'options' && trade.legs?.length){
    const pnls = trade.legs.map(l=>calcLegPnL(l)).filter(v=>v!==null);
    if(!pnls.length) return null;
    return pnls.reduce((a,b)=>a+b, 0);
  }
  if(!trade.exit || !trade.entry || !trade.qty) return null;
  const diff = trade.type==='BUY'
    ? (parseFloat(trade.exit) - parseFloat(trade.entry))
    : (parseFloat(trade.entry) - parseFloat(trade.exit));
  return diff * parseFloat(trade.qty);
}

function fmtPnL(v){
  if(v===null) return '-';
  const s = v>=0 ? '+' : '';
  return s+'₹'+Math.abs(v).toLocaleString('en-IN',{maximumFractionDigits:2});
}

let _tradeMode = 'all'; // 'all' | 'actual' | 'dummy'

function setTradeMode(mode){
  _tradeMode = mode;
  document.getElementById('tj-mode-all').classList.toggle('active',    mode==='all');
  document.getElementById('tj-mode-actual').classList.toggle('active', mode==='actual');
  document.getElementById('tj-mode-dummy').classList.toggle('active',  mode==='dummy');
  renderJournal();
}

function getFilteredTrades(){
  const month  = document.getElementById('tj-filter-month')?.value  || 'all';
  const status = document.getElementById('tj-filter-status')?.value || 'all';
  return TRADES.filter(t=>{
    if(month !== 'all'){
      const d = new Date(t.date);
      if(d.getMonth() !== parseInt(month)) return false;
    }
    if(status !== 'all' && t.status !== status) return false;
    // mode filter
    if(_tradeMode === 'actual' && t.mode !== 'actual') return false;
    if(_tradeMode === 'dummy'  && t.mode !== 'dummy')  return false;
    return true;
  });
}

function renderJournal(){
  const trades = getFilteredTrades();
  const tbody  = document.getElementById('tj-tbody');
  const empty  = document.getElementById('tj-empty');
  const stats  = document.getElementById('tj-stats');
  if(!tbody) return;

  // compute stats
  const wins   = trades.filter(t=>t.status==='win').length;
  const losses = trades.filter(t=>t.status==='loss').length;
  const open   = trades.filter(t=>t.status==='open').length;
  const pnls   = trades.map(t=>calcPnL(t)).filter(v=>v!==null);
  const netPnL = pnls.reduce((a,b)=>a+b, 0);
  const winRate= trades.length ? Math.round((wins/trades.length)*100) : 0;

  stats.innerHTML = `
    <div class="tj-stat">
      <div class="tj-stat-num b">${trades.length}</div>
      <div class="tj-stat-lbl">Total Trades</div>
    </div>
    <div class="tj-stat">
      <div class="tj-stat-num ${winRate>=50?'g':'r'}">${winRate}%</div>
      <div class="tj-stat-lbl">Win Rate</div>
    </div>
    <div class="tj-stat">
      <div class="tj-stat-num ${netPnL>=0?'g':'r'}">${fmtPnL(netPnL)}</div>
      <div class="tj-stat-lbl">Net P&L</div>
    </div>
    <div class="tj-stat">
      <div class="tj-stat-num g">${wins}</div>
      <div class="tj-stat-lbl">Wins</div>
    </div>
    <div class="tj-stat">
      <div class="tj-stat-num r">${losses}</div>
      <div class="tj-stat-lbl">Losses</div>
    </div>`;

  if(!trades.length){
    tbody.innerHTML='';
    empty.style.display='flex';
    return;
  }
  empty.style.display='none';

  const sorted = [...trades].sort((a,b)=>new Date(b.date)-new Date(a.date));
  tbody.innerHTML = sorted.map(t=>{
    const pnl = calcPnL(t);
    const pnlHtml = pnl===null ? '<span style="color:#9ca3af">-</span>'
      : `<span class="${pnl>=0?'tj-pnl-g':'tj-pnl-r'}">${fmtPnL(pnl)}</span>`;
    const isOpt = t.instrument === 'options';
    const instBadge = isOpt
      ? `<span style="font-size:9px;background:#f3e8ff;color:#7c3aed;border-radius:4px;padding:2px 6px;font-weight:700;margin-left:4px">OPT</span>`
      : t.instrument === 'futures'
        ? `<span style="font-size:9px;background:#fef9c3;color:#a16207;border-radius:4px;padding:2px 6px;font-weight:700;margin-left:4px">FUT</span>`
        : '';
    const typeHtml = isOpt
      ? `<span style="font-size:11px;font-weight:700;color:#7c3aed">${(t.legs||[]).length} leg${(t.legs||[]).length!==1?'s':''}</span>`
      : t.type==='BUY'
        ? `<span class="tj-type-buy">▲ BUY</span>`
        : `<span class="tj-type-sell">▼ SELL</span>`;
    const entryHtml = isOpt
      ? `<span style="color:#9ca3af;font-size:12px">${(t.legs||[]).map(l=>`${l.action} ${l.optType}`).join(', ')}</span>`
      : `₹${parseFloat(t.entry||0).toLocaleString('en-IN')}`;
    const exitHtml = isOpt
      ? `<span style="color:#9ca3af">-</span>`
      : (t.exit ? '₹'+parseFloat(t.exit).toLocaleString('en-IN') : '-');
    const badge = `<span class="tj-badge ${t.status}">${
      t.status==='win'?'✅ Win':t.status==='loss'?'❌ Loss':'⏳ Open'
    }</span>`;
    const modeBadge = t.mode==='dummy'
      ? `<span class="tj-mode-badge dummy">🧪 Dummy</span>`
      : `<span class="tj-mode-badge actual">✅ Actual</span>`;
    return `<tr onclick="openTradeModal('${t.id}')">
      <td style="color:#6b7280;font-size:12px;white-space:nowrap">${t.date||'-'}</td>
      <td><span class="tj-symbol">${esc(t.symbol)}</span>${instBadge}</td>
      <td>${typeHtml}</td>
      <td style="font-weight:600">${entryHtml}</td>
      <td style="color:#6b7280">${exitHtml}</td>
      <td style="color:#6b7280">${isOpt ? '-' : (t.qty||'-')}</td>
      <td>${pnlHtml}</td>
      <td>${badge}</td>
      <td>${modeBadge}</td>
      <td><span class="tj-notes-cell" title="${esc(t.notes||'')}">${esc(t.notes||'-')}</span></td>
      <td onclick="event.stopPropagation()" style="white-space:nowrap">
        <button class="tj-act-btn" onclick="openTradeModal('${t.id}')">Edit</button>
        <button class="tj-act-btn del" onclick="deleteTrade('${t.id}')">Delete</button>
      </td>
    </tr>`;
  }).join('');
}

/* ── INSTRUMENT TOGGLE ── */
function onInstrumentChange(){
  const inst = document.getElementById('tj-instrument').value;
  const isOpt = inst === 'options';
  document.getElementById('tj-eq-section').style.display  = isOpt ? 'none' : '';
  document.getElementById('tj-opt-section').style.display = isOpt ? '' : 'none';
  document.getElementById('tj-pnl-preview').style.display = 'none';
  if(isOpt && document.getElementById('tj-legs-container').children.length === 0) addLeg();
  else if(!isOpt) updatePnLPreview();
}

/* ── LEG BUILDER ── */
let _legCounter = 0;
function addLeg(){
  _legCounter++;
  const legId = 'leg_'+_legCounter;
  const wrap = document.createElement('div');
  wrap.id = legId;
  wrap.style.cssText = 'background:var(--sidebar);border:1px solid var(--border);border-radius:10px;padding:12px 14px;margin-bottom:10px;position:relative';
  wrap.innerHTML = `
    <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:10px">
      <span style="font-size:11px;font-weight:700;text-transform:uppercase;letter-spacing:0.8px;color:var(--accent)">Leg ${_legCounter}</span>
      <button onclick="removeLeg('${legId}')" style="background:none;border:none;cursor:pointer;font-size:14px;color:#dc2626;line-height:1">✕</button>
    </div>
    <div style="display:grid;grid-template-columns:1fr 1fr 1fr;gap:10px;margin-bottom:10px">
      <div class="frow" style="margin:0"><label>CE / PE</label>
        <select class="leg-opttype" data-leg="${legId}" onchange="updateLegsPreview()">
          <option value="CE">📈 CE (Call)</option>
          <option value="PE">📉 PE (Put)</option>
        </select>
      </div>
      <div class="frow" style="margin:0"><label>Strike</label>
        <input class="leg-strike" data-leg="${legId}" type="number" placeholder="e.g. 22000" oninput="updateLegsPreview()">
      </div>
      <div class="frow" style="margin:0"><label>Expiry</label>
        <input class="leg-expiry" data-leg="${legId}" type="text" placeholder="e.g. 27Mar">
      </div>
    </div>
    <div style="display:grid;grid-template-columns:1fr 1fr 1fr 1fr;gap:10px">
      <div class="frow" style="margin:0"><label>Action</label>
        <select class="leg-action" data-leg="${legId}" onchange="updateLegsPreview()">
          <option value="BUY">BUY</option>
          <option value="SELL">SELL</option>
        </select>
      </div>
      <div class="frow" style="margin:0"><label>Qty (lots)</label>
        <input class="leg-qty" data-leg="${legId}" type="number" placeholder="1" oninput="updateLegsPreview()">
      </div>
      <div class="frow" style="margin:0"><label>Entry ₹</label>
        <input class="leg-entry" data-leg="${legId}" type="number" step="0.01" placeholder="0.00" oninput="updateLegsPreview()">
      </div>
      <div class="frow" style="margin:0"><label>Exit ₹</label>
        <input class="leg-exit" data-leg="${legId}" type="number" step="0.01" placeholder="0.00" oninput="updateLegsPreview()">
      </div>
    </div>`;
  document.getElementById('tj-legs-container').appendChild(wrap);
  updateLegsPreview();
}

function removeLeg(legId){
  const el = document.getElementById(legId);
  if(el) el.remove();
  updateLegsPreview();
}

function getLegData(){
  const legs = [];
  document.querySelectorAll('#tj-legs-container > div').forEach(wrap=>{
    const legId = wrap.id;
    legs.push({
      legId,
      optType: wrap.querySelector('.leg-opttype')?.value || 'CE',
      strike:  wrap.querySelector('.leg-strike')?.value  || '',
      expiry:  wrap.querySelector('.leg-expiry')?.value  || '',
      action:  wrap.querySelector('.leg-action')?.value  || 'BUY',
      qty:     parseFloat(wrap.querySelector('.leg-qty')?.value)  || 0,
      entry:   parseFloat(wrap.querySelector('.leg-entry')?.value) || 0,
      exit:    parseFloat(wrap.querySelector('.leg-exit')?.value)  || 0,
    });
  });
  return legs;
}

function calcLegPnL(leg){
  if(!leg.entry || !leg.exit || !leg.qty) return null;
  const diff = leg.action === 'BUY' ? (leg.exit - leg.entry) : (leg.entry - leg.exit);
  return diff * leg.qty;
}

function updateLegsPreview(){
  const legs = getLegData();
  const pnlBox   = document.getElementById('tj-legs-pnl');
  const rowsEl   = document.getElementById('tj-legs-pnl-rows');
  const totalEl  = document.getElementById('tj-legs-total');
  const hasData  = legs.some(l=>l.entry && l.exit && l.qty);
  if(!hasData){ pnlBox.style.display='none'; return; }
  pnlBox.style.display='block';
  let total = 0;
  let html = '';
  legs.forEach((leg,i)=>{
    const pnl = calcLegPnL(leg);
    if(pnl !== null){
      total += pnl;
      const label = `${leg.action} ${leg.optType}${leg.strike ? ' '+leg.strike : ''} Leg ${i+1}`;
      const color = pnl >= 0 ? '#059669' : '#dc2626';
      html += `<div style="display:flex;justify-content:space-between;font-size:12px;padding:3px 0;color:#374151">
        <span>${label}</span>
        <span style="font-weight:700;color:${color}">${fmtPnL(pnl)}</span>
      </div>`;
    }
  });
  rowsEl.innerHTML = html;
  totalEl.textContent = fmtPnL(total);
  totalEl.style.color = total >= 0 ? '#059669' : '#dc2626';
}

function populateLegInputs(legs){
  const container = document.getElementById('tj-legs-container');
  container.innerHTML = '';
  _legCounter = 0;
  legs.forEach(leg=>{
    addLeg();
    const wrap = container.lastElementChild;
    if(wrap){
      wrap.querySelector('.leg-opttype').value = leg.optType || 'CE';
      wrap.querySelector('.leg-strike').value  = leg.strike  || '';
      wrap.querySelector('.leg-expiry').value  = leg.expiry  || '';
      wrap.querySelector('.leg-action').value  = leg.action  || 'BUY';
      wrap.querySelector('.leg-qty').value     = leg.qty     || '';
      wrap.querySelector('.leg-entry').value   = leg.entry   || '';
      wrap.querySelector('.leg-exit').value    = leg.exit    || '';
    }
  });
  updateLegsPreview();
}

/* ── OPEN / CLOSE MODAL ── */
function openTradeModal(id){
  const existing = id ? TRADES.find(t=>t.id===id) : null;
  document.getElementById('trade-modal-heading').textContent = existing ? 'Edit Trade' : 'Log New Trade';
  document.getElementById('trade-edit-id').value = existing ? existing.id : '';
  document.getElementById('tj-symbol').value     = existing?.symbol     || '';
  document.getElementById('tj-date').value       = existing?.date       || localToday();
  document.getElementById('tj-instrument').value = existing?.instrument || 'equity';
  document.getElementById('tj-status').value     = existing?.status     || 'open';
  document.getElementById('tj-notes').value      = existing?.notes      || '';
  // set mode radio
  const modeVal = existing?.mode || (_tradeMode==='dummy' ? 'dummy' : 'actual');
  document.getElementById('tj-mode-actual-radio').checked = modeVal !== 'dummy';
  document.getElementById('tj-mode-dummy-radio').checked  = modeVal === 'dummy';

  const isOpt = (existing?.instrument === 'options');
  document.getElementById('tj-eq-section').style.display  = isOpt ? 'none' : '';
  document.getElementById('tj-opt-section').style.display = isOpt ? '' : 'none';

  if(isOpt){
    const legs = existing?.legs || [];
    if(legs.length) populateLegInputs(legs);
    else { document.getElementById('tj-legs-container').innerHTML=''; _legCounter=0; addLeg(); }
  } else {
    document.getElementById('tj-type').value   = existing?.type   || 'BUY';
    document.getElementById('tj-qty').value    = existing?.qty    || '';
    document.getElementById('tj-entry').value  = existing?.entry  || '';
    document.getElementById('tj-exit').value   = existing?.exit   || '';
    document.getElementById('tj-sl').value     = existing?.sl     || '';
    document.getElementById('tj-target').value = existing?.target || '';
    updatePnLPreview();
  }
  document.getElementById('trade-modal-overlay').classList.add('open');
}
function closeTradeModal(){ document.getElementById('trade-modal-overlay').classList.remove('open'); }

function updatePnLPreview(){
  const entry = parseFloat(document.getElementById('tj-entry').value);
  const exit  = parseFloat(document.getElementById('tj-exit').value);
  const qty   = parseFloat(document.getElementById('tj-qty').value);
  const type  = document.getElementById('tj-type').value;
  const prev  = document.getElementById('tj-pnl-preview');
  const val   = document.getElementById('tj-pnl-val');
  if(entry && exit && qty){
    const pnl = type==='BUY' ? (exit-entry)*qty : (entry-exit)*qty;
    prev.style.display='flex';
    val.textContent = fmtPnL(pnl);
    val.style.color = pnl>=0 ? '#059669' : '#dc2626';
  } else {
    prev.style.display='none';
  }
}

function initJournalListeners(){
  ['tj-entry','tj-exit','tj-qty','tj-type'].forEach(id=>{
    const el=document.getElementById(id);
    if(el) el.addEventListener('input', updatePnLPreview);
  });
}

/* ── SAVE TRADE ── */
function saveTrade(){
  const symbol = document.getElementById('tj-symbol').value.trim().toUpperCase();
  if(!symbol){ toast('Symbol is required','error'); return; }
  const instrument = document.getElementById('tj-instrument').value;
  const id = document.getElementById('trade-edit-id').value;
  let trade;

  if(instrument === 'options'){
    const legs = getLegData();
    if(!legs.length){ toast('Add at least one option leg','error'); return; }
    trade = {
      id:         id || uid_trade(),
      symbol,
      instrument: 'options',
      date:       document.getElementById('tj-date').value,
      legs,
      status:     document.getElementById('tj-status').value,
      notes:      document.getElementById('tj-notes').value.trim(),
      mode:       document.getElementById('tj-mode-dummy-radio').checked ? 'dummy' : 'actual',
    };
  } else {
    const entry = document.getElementById('tj-entry').value;
    if(!entry){ toast('Entry price is required','error'); return; }
    trade = {
      id:         id || uid_trade(),
      symbol,
      instrument,
      date:       document.getElementById('tj-date').value,
      type:       document.getElementById('tj-type').value,
      qty:        document.getElementById('tj-qty').value,
      entry,
      exit:       document.getElementById('tj-exit').value,
      sl:         document.getElementById('tj-sl').value,
      target:     document.getElementById('tj-target').value,
      status:     document.getElementById('tj-status').value,
      notes:      document.getElementById('tj-notes').value.trim(),
      mode:       document.getElementById('tj-mode-dummy-radio').checked ? 'dummy' : 'actual',
    };
  }

  if(id){ TRADES = TRADES.map(t=>t.id===id ? trade : t); }
  else  { TRADES.push(trade); }

  saveTrades();
  renderJournal();
  closeTradeModal();
  toast('Trade saved ✓','success');
}

function deleteTrade(id){
  if(!confirm('Delete this trade?')) return;
  TRADES = TRADES.filter(t=>t.id!==id);
  saveTrades();
  renderJournal();
  toast('Trade deleted','success');
}

/* ── TASK & ACTION NOTES ─────────────────────────── */
/* ── TASK & ACTION NOTES ─────────────────────────── */
let TASKNOTES = [];
let _tanFilter    = 'all';
let _tanCatFilter = 'all';
let _tanSort      = 'date-desc';

function uid_tan(){ return 'TN'+Date.now().toString(36)+Math.random().toString(36).slice(2,5); }

function updateTaskNotesCount(){
  const open = TASKNOTES.filter(n=>!n.done).length;
  const el = document.getElementById('nav-tasknotes-count');
  if(el) el.textContent = TASKNOTES.length;
  const hdr = document.getElementById('tan-hdr-count');
  if(hdr) hdr.textContent = TASKNOTES.length + ' note' + (TASKNOTES.length!==1?'s':'') + (open?' · '+open+' open':'');
}

async function saveTaskNotes(){
  DATA.tasknotes = TASKNOTES;
  updateTaskNotesCount();
  await saveToFirebase();
}

function tanSetFilter(f, btn){
  _tanFilter = f;
  document.querySelectorAll('#tan-f-all,#tan-f-open,#tan-f-done,#tan-f-high,#tan-f-medium,#tan-f-low')
    .forEach(b=>b.classList.remove('active'));
  if(btn) btn.classList.add('active');
  renderTaskNotes();
}

function tanSetCat(cat, btn){
  _tanCatFilter = cat;
  document.querySelectorAll('#tan-fc-all,#tan-fc-personal,#tan-fc-official')
    .forEach(b=>b.classList.remove('active'));
  if(btn) btn.classList.add('active');
  renderTaskNotes();
}

function addTaskNote(){
  const input = document.getElementById('tan-quick-input');
  const text  = input.value.trim();
  if(!text){ toast('Write something first','error'); return; }
  const cat = document.getElementById('tan-quick-cat')?.value || 'personal';
  const note = {
    id:       uid_tan(),
    text,
    category: cat,
    priority: 'medium',
    tags:     [],
    done:     false,
    date:     localToday(),
    created:  new Date().toISOString(),
  };
  TASKNOTES.unshift(note);
  input.value = '';
  input.style.height = '';
  saveTaskNotes();
  renderTaskNotes();
  addTaskToGoogleCalendar(note.id, note.text, note.date, note.priority);
  toast('Note added \u2713','success');
}

/* 2. fade-out then toggle done */
function toggleTanDone(id){
  const n = TASKNOTES.find(n=>n.id===id);
  if(!n) return;
  n.done = !n.done;
  if(n.done){
    const el = document.getElementById('tan-item-'+id);
    if(el){
      el.classList.add('fading-out');
      setTimeout(()=>{ saveTaskNotes(); renderTaskNotes(); updateBadge(); }, 260);
      return;
    }
  }
  saveTaskNotes(); renderTaskNotes(); updateBadge();
}

function deleteTanNote(id){
  if(!confirm('Delete this note?')) return;
  deleteTaskFromGoogleCalendar(id);
  const el = document.getElementById('tan-item-'+id);
  if(el){ el.classList.add('fading-out'); setTimeout(()=>{ TASKNOTES=TASKNOTES.filter(n=>n.id!==id); saveTaskNotes(); renderTaskNotes(); },260); }
  else  { TASKNOTES=TASKNOTES.filter(n=>n.id!==id); saveTaskNotes(); renderTaskNotes(); }
  toast('Note deleted','success');
}

/* 7. Quick actions */
function tanDuplicate(id){
  const n = TASKNOTES.find(n=>n.id===id);
  if(!n) return;
  const copy = {...n, id:uid_tan(), created:new Date().toISOString(), done:false};
  TASKNOTES.unshift(copy);
  closeTanDropdown();
  saveTaskNotes(); renderTaskNotes();
  toast('Duplicated \u2713','success');
}

let _openDropdown = null;
function toggleTanDropdown(id, evt){
  evt.stopPropagation();
  const dd = document.getElementById('tan-dd-'+id);
  if(!dd) return;
  if(_openDropdown && _openDropdown!==dd){ _openDropdown.classList.remove('open'); }
  dd.classList.toggle('open');
  _openDropdown = dd.classList.contains('open') ? dd : null;
}
function closeTanDropdown(){
  if(_openDropdown){ _openDropdown.classList.remove('open'); _openDropdown=null; }
}
document.addEventListener('click', ()=>closeTanDropdown());

function saveTanEdit(id){
  const n = TASKNOTES.find(n=>n.id===id);
  if(!n) return;
  const textEl = document.getElementById('tan-edit-text-'+id);
  const prioEl = document.getElementById('tan-edit-prio-'+id);
  const tagEl  = document.getElementById('tan-edit-tag-'+id);
  const catEl  = document.getElementById('tan-edit-cat-'+id);
  const dateEl = document.getElementById('tan-edit-date-'+id);
  if(textEl) n.text     = textEl.value.trim() || n.text;
  if(prioEl) n.priority = prioEl.value;
  if(catEl)  n.category = catEl.value;
  if(dateEl && dateEl.value) n.date = dateEl.value;
  // 5. parse tags from comma-separated input into array
  if(tagEl){
    const raw = tagEl.value.trim();
    n.tags = raw ? raw.split(',').map(t=>t.trim()).filter(Boolean) : [];
  }
  saveTaskNotes(); renderTaskNotes();
  // Re-sync to GCal: delete old event and create updated one
  if(GOOGLE_CLIENT_ID){
    _gcalWithToken(async token=>{
      await _gcalDeleteEvent(token, id).catch(console.warn);
      const label = n.priority==='high'?'🔴 ':n.priority==='low'?'🟢 ':'🟡 ';
      await _gcalCreateAllDayEvent(token, n.id, label+(n.text||'Task'), n.date, 'Priority: '+(n.priority||'medium')).catch(console.warn);
      _gcalToast('✅ Task updated in Google Calendar','success');
    });
  }
  toast('Saved \u2713','success');
}

function renderTaskNotes(){
  const list = document.getElementById('tan-list');
  if(!list) return;
  const search = (document.getElementById('tan-search')?.value||'').toLowerCase();
  const sort   = document.getElementById('tan-sort')?.value || 'date-desc';
  _tanSort = sort;
  let items = [...TASKNOTES];

  // category filter
  if(_tanCatFilter==='personal') items = items.filter(n=>(n.category||'personal')==='personal');
  if(_tanCatFilter==='official') items = items.filter(n=>n.category==='official');

  // status / priority filter
  if(_tanFilter==='open')   items = items.filter(n=>!n.done);
  if(_tanFilter==='done')   items = items.filter(n=>n.done);
  if(_tanFilter==='high')   items = items.filter(n=>n.priority==='high');
  if(_tanFilter==='medium') items = items.filter(n=>n.priority==='medium');
  if(_tanFilter==='low')    items = items.filter(n=>n.priority==='low');
  if(search) items = items.filter(n=>{
    const tags = Array.isArray(n.tags) ? n.tags.join(' ') : (n.tags||'');
    return n.text.toLowerCase().includes(search) || tags.toLowerCase().includes(search);
  });

  // 6. sort
  const prioOrder = {high:0,medium:1,low:2};
  if(sort==='date-desc') items.sort((a,b)=>new Date(b.created)-new Date(a.created));
  else if(sort==='date-asc') items.sort((a,b)=>new Date(a.created)-new Date(b.created));
  else if(sort==='priority') items.sort((a,b)=>(prioOrder[a.priority||'medium']||1)-(prioOrder[b.priority||'medium']||1));
  else if(sort==='category') items.sort((a,b)=>(a.category||'personal').localeCompare(b.category||'personal'));
  else if(sort==='status') items.sort((a,b)=>(a.done?1:0)-(b.done?1:0));

  updateTaskNotesCount();

  if(!items.length){
    const isFiltered = _tanFilter!=='all' || _tanCatFilter!=='all' || (document.getElementById('tan-search')?.value||'').trim();
    list.innerHTML = `<div class="tan-empty">
      <div class="tan-empty-icon">${isFiltered?'🔍':'🎉'}</div>
      <div style="font-size:15px;font-weight:700;color:var(--text)">${isFiltered?'No matching notes':'All caught up!'}</div>
      <div style="font-size:13px">${isFiltered?'Try a different filter or search term.':'Nothing pending — take a break or plan ahead! ☕'}</div>
    </div>`;
    return;
  }

  // also show nice empty state when open section is empty
  const _openEmptyHtml = `<div class="tan-empty" style="padding:30px 20px">
    <div class="tan-empty-icon" style="font-size:28px">✅</div>
    <div style="font-size:13px;font-weight:600;color:var(--text2)">All tasks done — well done!</div>
  </div>`;

  // 3. split into open and done sections
  const open = items.filter(n=>!n.done);
  const done = items.filter(n=>n.done);

  const fmtDate = iso => {
    if(!iso) return '';
    const d = new Date(iso);
    return d.toLocaleDateString('en-IN',{day:'2-digit',month:'short',year:'numeric'});
  };

  function renderItem(n){
    const cat      = n.category || 'personal';
    const catLabel = cat==='official' ? '💼 Official' : '👤 Personal';
    const prio     = n.priority || 'medium';
    const prioLabels = {high:'🔴 High', medium:'🟡 Med', low:'🟢 Low'};
    const doneCls  = n.done ? ' done' : '';
    // 5. tags as chips
    const tagArr   = Array.isArray(n.tags) ? n.tags : (n.tags ? [n.tags] : []);
    const tagHtml  = tagArr.map(t=>`<span class="tan-tag-chip">#${esc(t)}</span>`).join('');

    return `<div class="tan-item${n.done?' is-done':''} is-${prio}" id="tan-item-${n.id}" style="display:flex;flex-direction:row">
      <div class="tan-item-strip ${prio}"></div>
      <div class="tan-item-inner">
        <div class="tan-item-top">
          <input type="checkbox" class="tan-done-cb" ${n.done?'checked':''} onchange="toggleTanDone('${n.id}')">
          <span class="tan-item-priority ${prio}">${prioLabels[prio]}</span>
          <div class="tan-item-text${doneCls}">${esc(n.text)}</div>
          <div class="tan-item-actions">
            <button class="tan-act" onclick="tanToggleEdit('${n.id}')">Edit</button>
            ${n.done?`<button class="tan-act del-act" onclick="deleteTanNote('${n.id}')">Delete</button>`:''}
            <button class="tan-dot-btn" onclick="toggleTanDropdown('${n.id}',event)" title="More">⋯</button>
            <div class="tan-dropdown" id="tan-dd-${n.id}">
              <div class="tan-dd-item" onclick="tanDuplicate('${n.id}')">📋 Duplicate</div>
              <div class="tan-dd-item" onclick="tanToggleEdit('${n.id}');closeTanDropdown()">✏️ Edit</div>
              <div class="tan-dd-item danger" onclick="deleteTanNote('${n.id}')">🗑 Delete</div>
            </div>
          </div>
        </div>
        <div class="tan-item-meta">
          <span class="tan-cat-badge ${cat}">${catLabel}</span>
          <span class="tan-date-badge">📅 ${fmtDate(n.date)}</span>
          ${tagHtml}
          <span style="font-size:11px;color:var(--muted)">${n.done?'✅ Done':'⏳ Open'}</span>
        </div>
        <div class="tan-edit-area" id="tan-edit-${n.id}">
          <textarea class="tan-edit-textarea" id="tan-edit-text-${n.id}" rows="3">${esc(n.text)}</textarea>
          <div class="tan-edit-row">
            <select class="tan-cat-sel" id="tan-edit-cat-${n.id}" style="padding:5px 10px;font-size:12px">
              <option value="personal" ${cat==='personal'?'selected':''}>👤 Personal</option>
              <option value="official" ${cat==='official'?'selected':''}>💼 Official</option>
            </select>
            <select class="tan-priority-sel" id="tan-edit-prio-${n.id}">
              <option value="high"   ${prio==='high'  ?'selected':''}>🔴 High</option>
              <option value="medium" ${prio==='medium'?'selected':''}>🟡 Medium</option>
              <option value="low"    ${prio==='low'   ?'selected':''}>🟢 Low</option>
            </select>
            <input class="tan-tag-input" id="tan-edit-tag-${n.id}"
              placeholder="Tags (comma separated)"
              value="${esc(tagArr.join(', '))}">
            <input type="date" id="tan-edit-date-${n.id}"
              style="padding:5px 10px;font-size:12px;border:1px solid var(--border);border-radius:6px;background:var(--bg);color:var(--text);cursor:pointer"
              value="${n.date||''}">
            <button class="tan-save-btn" onclick="saveTanEdit('${n.id}')">Save</button>
          </div>
        </div>
      </div>
    </div>`;
  }

  function sectionHtml(title, arr, key){
    if(!arr.length) return '';
    return `<div>
      <div class="tan-section-hdr" onclick="tanToggleSection('${key}')">
        <span class="tan-section-chevron" id="tan-chev-${key}">▼</span>
        <span class="tan-section-title">${title}</span>
        <span class="tan-section-count">${arr.length}</span>
      </div>
      <div class="tan-section-body" id="tan-sec-${key}">
        ${arr.map(renderItem).join('')}
      </div>
    </div>`;
  }

  list.innerHTML = sectionHtml('Open Tasks', open, 'open') + (open.length===0?_openEmptyHtml:'') + sectionHtml('Completed', done, 'done');
}

function tanToggleSection(key){
  const body = document.getElementById('tan-sec-'+key);
  const chev = document.getElementById('tan-chev-'+key);
  if(!body) return;
  body.classList.toggle('collapsed');
  if(chev) chev.classList.toggle('collapsed');
}

function tanToggleEdit(id){
  const el   = document.getElementById('tan-edit-'+id);
  const item = document.getElementById('tan-item-'+id);
  if(!el) return;
  const isOpen = el.classList.contains('open');
  el.classList.toggle('open', !isOpen);
  if(item) item.classList.toggle('editing', !isOpen);
}

/* ── FINANCE TRACKER ─────────────────────────────── */
let FINANCE = [];
let _finFilter     = 'all';
let _finPersonFilter = 'all';

function uid_fin(){ return 'FN'+Date.now().toString(36)+Math.random().toString(36).slice(2,5); }

function finRupee(v){
  return '₹'+Math.abs(v).toLocaleString('en-IN',{maximumFractionDigits:2});
}

function updateFinanceCount(){
  const pending = FINANCE.filter(e=>finStatus(e)!=='settled').length;
  const el = document.getElementById('nav-finance-count');
  if(el) el.textContent = pending || FINANCE.length;
}

/* derive status — only principal repayments count toward settlement */
function finStatus(e){
  const principalPaid=(e.repayments||[])
    .filter(r=>r.repayType==='principal'||r.repayType==='both'||!r.repayType)
    .reduce((s,r)=>s+r.amount,0);
  if(e.status==='settled') return 'settled';
  if(principalPaid>=e.amount) return 'settled';
  if(principalPaid>0) return 'partial';
  if(e.duedate&&new Date(e.duedate)<new Date()) return 'overdue';
  return 'pending';
}

function finRemaining(e){
  const principalPaid=(e.repayments||[])
    .filter(r=>r.repayType==='principal'||r.repayType==='both'||!r.repayType)
    .reduce((s,r)=>s+r.amount,0);
  return Math.max(0,e.amount-principalPaid);
}

function finTotalInterestPaid(e){
  return (e.repayments||[])
    .filter(r=>r.repayType==='interest'||r.repayType==='both')
    .reduce((s,r)=>s+r.amount,0);
}

async function saveFinance(){
  DATA.finance = FINANCE;
  updateFinanceCount();
  // Always persist to localStorage immediately as a safety net for mobile
  // In case Firebase sync fails or is slow, data is never lost
  try{ localStorage.setItem('fin_backup', JSON.stringify(FINANCE)); }catch(e){}
  const ok = await saveToFirebase();
  return ok;
}

let _finView = 'card';
let _finGroupBy = false;

function finSetView(v){
  _finView = v;
  const list = document.getElementById('fin-list');
  if(list){ list.className = 'fin-list fin-view-'+v; }
  document.getElementById('fin-vcard').classList.toggle('active', v==='card');
  document.getElementById('fin-vlist').classList.toggle('active', v==='list');
  renderFinance();
}

function finToggleGroup(btn){
  _finGroupBy = !_finGroupBy;
  btn.classList.toggle('active', _finGroupBy);
  renderFinance();
}

function finToggleTimeline(btn){
  const panel = document.getElementById('fin-timeline-panel');
  if(!panel) return;
  const isOpen = panel.classList.toggle('open');
  btn.classList.toggle('active', isOpen);
  if(isOpen) renderFinanceTimeline();
}

function renderFinanceTimeline(){
  const rows = document.getElementById('fin-tl-rows');
  if(!rows) return;
  const today = new Date(); today.setHours(0,0,0,0);
  const withDue = FINANCE
    .filter(e=>e.duedate && finStatus(e)!=='settled')
    .sort((a,b)=>new Date(a.duedate)-new Date(b.duedate));
  if(!withDue.length){
    rows.innerHTML='<div style="font-size:12px;color:var(--muted)">No upcoming due dates</div>';
    return;
  }
  const fmtD=iso=>{const d=new Date(iso);return d.toLocaleDateString('en-IN',{day:'2-digit',month:'short',year:'numeric'});};
  rows.innerHTML = withDue.map(e=>{
    const due=new Date(e.duedate);due.setHours(0,0,0,0);
    const diff=Math.round((due-today)/(86400000));
    let dotCls='ok', label='';
    if(diff<0){dotCls='overdue';label=`🔴 Overdue by ${Math.abs(diff)}d`;}
    else if(diff===0){dotCls='today';label='🟡 Due today';}
    else if(diff<=7){dotCls='soon';label=`🟡 ${diff}d left`;}
    else{label=`✅ ${diff}d left`;}
    const amtCls=e.type==='gave'?'gave':'borrowed';
    return `<div class="fin-tl-row">
      <div class="fin-tl-dot ${dotCls}"></div>
      <div class="fin-tl-date">${fmtD(e.duedate)}</div>
      <div class="fin-tl-person">${esc(e.person)}${e.notes?` · <span style="color:var(--muted)">${esc(e.notes.slice(0,30))}</span>`:''}</div>
      <div class="fin-tl-amt ${amtCls}">${finRupee(finRemaining(e))}</div>
      <div style="font-size:11px;white-space:nowrap">${label}</div>
    </div>`;
  }).join('');
}

/* 8. Settled section toggle */
function finToggleSettled(key){
  const body=document.getElementById('fin-settled-body-'+key);
  const chev=document.getElementById('fin-settled-chev-'+key);
  if(!body) return;
  body.classList.toggle('collapsed');
  if(chev) chev.classList.toggle('collapsed');
}

function finSetFilter(f,btn){
  _finFilter=f;
  if(f==='all') _finPersonFilter='all';
  document.querySelectorAll('#fin-f-all,#fin-f-gave,#fin-f-borrowed,#fin-f-pending,#fin-f-partial,#fin-f-overdue,#fin-f-settled')
    .forEach(b=>b.classList.remove('active'));
  if(btn) btn.classList.add('active');
  renderFinance();
}

function finSetPerson(name){
  _finPersonFilter=_finPersonFilter===name?'all':name;
  renderFinance();
}

function openFinModal(id){
  const ex=id?FINANCE.find(e=>e.id===id):null;
  document.getElementById('fin-modal-title').textContent=ex?'Edit Entry':'New Entry';
  document.getElementById('fin-edit-id').value    =ex?ex.id:'';
  document.getElementById('fin-type').value       =ex?.type      ||'gave';
  document.getElementById('fin-person').value     =ex?.person    ||'';
  document.getElementById('fin-amount').value     =ex?.amount    ||'';
  document.getElementById('fin-paymethod').value  =ex?.paymethod ||'cash';
  document.getElementById('fin-interest').value   =ex?.interestRate||'';
  document.getElementById('fin-date').value       =ex?.date      ||localToday();
  document.getElementById('fin-duedate').value    =ex?.duedate   ||'';
  document.getElementById('fin-notes').value      =ex?.notes     ||'';
  document.getElementById('fin-modal-overlay').classList.add('open');
}
function closeFinModal(){document.getElementById('fin-modal-overlay').classList.remove('open');}

async function saveFinEntry(){
  const person=document.getElementById('fin-person').value.trim();
  if(!person){toast('Person name is required','error');return;}
  const amount=parseFloat(document.getElementById('fin-amount').value);
  if(!amount||amount<=0){toast('Enter a valid amount','error');return;}
  const id=document.getElementById('fin-edit-id').value;
  const existing=id?FINANCE.find(e=>e.id===id):null;
  const entry={
    id:          id||uid_fin(),
    type:        document.getElementById('fin-type').value,
    person,
    amount,
    paymethod:   document.getElementById('fin-paymethod').value,
    interestRate:parseFloat(document.getElementById('fin-interest').value)||0,
    date:        document.getElementById('fin-date').value,
    duedate:     document.getElementById('fin-duedate').value,
    notes:       document.getElementById('fin-notes').value.trim(),
    repayments:  existing?(existing.repayments||[]):[],
    created:     existing?existing.created:new Date().toISOString(),
  };
  if(id){FINANCE=FINANCE.map(e=>e.id===id?entry:e);}
  else  {FINANCE.unshift(entry);}
  // Persist to localStorage immediately so data is never lost on mobile
  try{ localStorage.setItem('fin_backup', JSON.stringify(FINANCE)); }catch(e){}

  // Disable Save button immediately to prevent double-tap on mobile
  const saveBtn = document.querySelector('#fin-modal-overlay .btn');
  if(saveBtn){ saveBtn.disabled=true; saveBtn.textContent='⏳ Saving…'; }

  // On slow mobile networks, Firebase data may not be loaded yet — wait up to 8s
  if(!dataLoaded){
    toast('Saving\u2026 please wait','success');
    let waited=0;
    await new Promise(resolve=>{
      const check=setInterval(()=>{
        waited+=250;
        if(dataLoaded||waited>=8000){clearInterval(check);resolve();}
      },250);
    });
  }

  const ok = await saveFinance();
  // Close modal AFTER save completes (prevents iOS Safari losing async context)
  closeFinModal();
  renderFinance();
  toast(ok!==false?'Entry saved \u2713':'Entry saved locally (sync pending)','success');
}

function deleteFinEntry(id){
  if(!confirm('Delete this entry?')) return;
  FINANCE=FINANCE.filter(e=>e.id!==id);
  saveFinance();renderFinance();
  toast('Entry deleted','success');
}

function markFinSettled(id){
  const e=FINANCE.find(e=>e.id===id);
  if(!e) return;
  e.status='settled';
  const rem=finRemaining(e);
  if(rem>0) e.repayments.push({amount:rem,repayType:'principal',paymethod:'cash',note:'Settled',date:localToday()});
  saveFinance();renderFinance();
  toast('Marked as settled \u2713','success');
}

function deleteRepayment(entryId,idx){
  if(!confirm('Remove this payment?')) return;
  const e=FINANCE.find(e=>e.id===entryId);
  if(!e) return;
  e.repayments.splice(idx,1);
  saveFinance();renderFinance();
  toast('Payment removed','success');
}

function addRepayment(id){
  const amtEl   =document.getElementById('fin-repay-amt-'+id);
  const noteEl  =document.getElementById('fin-repay-note-'+id);
  const dateEl  =document.getElementById('fin-repay-date-'+id);
  const typeEl  =document.getElementById('fin-repay-type-'+id);
  const methodEl=document.getElementById('fin-repay-method-'+id);
  const amt=parseFloat(amtEl?.value);
  if(!amt||amt<=0){toast('Enter a valid amount','error');return;}
  const e=FINANCE.find(e=>e.id===id);
  if(!e) return;
  e.repayments=e.repayments||[];
  e.repayments.push({
    amount:    amt,
    repayType: typeEl?.value   ||'principal',
    paymethod: methodEl?.value ||'cash',
    note:      noteEl?.value.trim()||'',
    date:      dateEl?.value||localToday(),
  });
  saveFinance();renderFinance();
  toast('Payment recorded \u2713','success');
}

function toggleFinHistory(id){
  const el =document.getElementById('fin-history-body-'+id);
  const btn=document.getElementById('fin-history-toggle-'+id);
  if(!el) return;
  const hidden=el.style.display==='none';
  el.style.display=hidden?'':'none';
  if(btn) btn.textContent=hidden?'\u25b2 Hide':'\u25bc History';
}

function toggleFinRepay(id){
  const e=(FINANCE||[]).find(x=>x.id===id);
  if(!e) return;
  const rem=finRemaining(e);
  const summary=document.getElementById('fin-pay-summary');
  if(summary){
    summary.innerHTML=`<strong>${esc(e.person)}</strong> &nbsp;·&nbsp; Total: ${finRupee(e.amount)} &nbsp;·&nbsp; <span style="color:var(--green)">Remaining: ${finRupee(rem)}</span>`;
  }
  document.getElementById('fin-pay-entry-id').value=id;
  document.getElementById('fin-pay-amt').value='';
  document.getElementById('fin-pay-note').value='';
  document.getElementById('fin-pay-date').value=localToday();
  document.getElementById('fin-pay-type').value='principal';
  document.getElementById('fin-pay-method').value=e.paymethod||'cash';
  document.getElementById('fin-pay-modal-title').textContent='Record Payment — '+esc(e.person);
  document.getElementById('fin-pay-modal-overlay').classList.add('open');
}
function closeFinPayModal(){
  document.getElementById('fin-pay-modal-overlay').classList.remove('open');
}
async function submitFinPayModal(){
  const id=document.getElementById('fin-pay-entry-id').value;
  const amt=parseFloat(document.getElementById('fin-pay-amt').value);
  if(!amt||amt<=0){toast('Enter a valid amount','error');return;}
  const e=(FINANCE||[]).find(x=>x.id===id);
  if(!e) return;
  e.repayments=e.repayments||[];
  e.repayments.push({
    amount:amt,
    repayType:document.getElementById('fin-pay-type').value,
    paymethod:document.getElementById('fin-pay-method').value,
    note:document.getElementById('fin-pay-note').value,
    date:document.getElementById('fin-pay-date').value
  });
  closeFinPayModal();
  renderFinance();
  await saveToFirebase();
  toast('Payment recorded ✓','success');
}

function toggleFinExpand(id, evt){
  if(evt) evt.stopPropagation();
  const el = document.getElementById('fin-item-'+id);
  if(!el) return;
  const isOpen = el.classList.contains('open');
  el.classList.toggle('open', !isOpen);
  el.style.display = isOpen ? 'none' : '';
  // card view parent
  const card = document.getElementById('fin-card-'+id);
  if(card) card.classList.toggle('expanded', !isOpen);
}

function renderFinance(){
  const list = document.getElementById('fin-list');
  if(!list) return;
  const search = (document.getElementById('fin-search')?.value||'').toLowerCase();

  const personMap={};
  FINANCE.forEach(e=>{
    const p=e.person;
    if(!personMap[p]) personMap[p]={gave:0,borrowed:0};
    const rem=finRemaining(e);
    if(e.type==='gave') personMap[p].gave+=rem;
    else                personMap[p].borrowed+=rem;
  });

  const chips=document.getElementById('fin-person-chips');
  if(chips){
    if(!Object.keys(personMap).length){
      chips.innerHTML='<span style="font-size:12px;color:var(--muted)">No entries yet</span>';
    } else {
      chips.innerHTML=Object.entries(personMap).map(([name,bal])=>{
        const net=bal.gave-bal.borrowed;
        const balLabel=net>0?`+${finRupee(net)}`:net<0?`-${finRupee(Math.abs(net))}`:'✅ Settled';
        const balCls=net>0?'pos':net<0?'neg':'pos';
        const active=_finPersonFilter===name?' active':'';
        return `<div class="fin-person-chip${active}" onclick="finSetPerson('${esc(name)}')">
          <span class="fin-person-name">${esc(name)}</span>
          <span class="fin-person-bal ${balCls}">${balLabel}</span>
        </div>`;
      }).join('');
    }
  }

  const totalGave    =FINANCE.filter(e=>e.type==='gave').reduce((s,e)=>s+finRemaining(e),0);
  const totalBorrowed=FINANCE.filter(e=>e.type==='borrowed').reduce((s,e)=>s+finRemaining(e),0);
  const net=totalGave-totalBorrowed;
  const gaveCount=FINANCE.filter(e=>e.type==='gave'&&finStatus(e)!=='settled').length;
  const borCount =FINANCE.filter(e=>e.type==='borrowed'&&finStatus(e)!=='settled').length;
  const sg=document.getElementById('fin-sum-gave');
  const sb=document.getElementById('fin-sum-borrow');
  const sn=document.getElementById('fin-sum-net');
  if(sg) sg.textContent=finRupee(totalGave);
  if(sb) sb.textContent=finRupee(totalBorrowed);
  if(sn){sn.textContent=(net>=0?'+':'-')+finRupee(Math.abs(net));sn.className='fin-sum-val '+(net>=0?'net-pos':'net-neg');}
  const sgs=document.getElementById('fin-sum-gave-sub');
  const sbs=document.getElementById('fin-sum-borrow-sub');
  const sns=document.getElementById('fin-sum-net-sub');
  if(sgs) sgs.textContent=gaveCount+' pending';
  if(sbs) sbs.textContent=borCount+' pending';
  if(sns) sns.textContent=net>=0?'Overall you are ahead':'Overall you owe more';

  let items=[...FINANCE];
  if(_finPersonFilter!=='all') items=items.filter(e=>e.person===_finPersonFilter);
  if(_finFilter==='gave')      items=items.filter(e=>e.type==='gave');
  if(_finFilter==='borrowed')  items=items.filter(e=>e.type==='borrowed');
  if(_finFilter==='pending')   items=items.filter(e=>finStatus(e)==='pending');
  if(_finFilter==='partial')   items=items.filter(e=>finStatus(e)==='partial');
  if(_finFilter==='overdue')   items=items.filter(e=>finStatus(e)==='overdue');
  if(_finFilter==='settled')   items=items.filter(e=>finStatus(e)==='settled');
  if(search) items=items.filter(e=>e.person.toLowerCase().includes(search)||(e.notes||'').toLowerCase().includes(search));

  // 5. Sort
  const sort = document.getElementById('fin-sort')?.value || 'date-desc';
  if(sort==='date-desc') items.sort((a,b)=>new Date(b.created)-new Date(a.created));
  else if(sort==='date-asc') items.sort((a,b)=>new Date(a.created)-new Date(b.created));
  else if(sort==='amount-desc') items.sort((a,b)=>b.amount-a.amount);
  else if(sort==='amount-asc')  items.sort((a,b)=>a.amount-b.amount);
  else if(sort==='person') items.sort((a,b)=>a.person.localeCompare(b.person));
  else if(sort==='duedate') items.sort((a,b)=>{
    if(!a.duedate) return 1; if(!b.duedate) return -1;
    return new Date(a.duedate)-new Date(b.duedate);
  });
  else if(sort==='status'){
    const ord={overdue:0,pending:1,partial:2,settled:3};
    items.sort((a,b)=>(ord[finStatus(a)]||1)-(ord[finStatus(b)]||1));
  }

  updateFinanceCount();

  if(!items.length){
    const grid=document.getElementById('fin-card-grid');
    const lbox=document.getElementById('fin-listbox');
    const empty=`<div class="fin-empty">
      <div style="font-size:40px;opacity:.4">💰</div>
      <div style="font-size:14px;font-weight:600;color:var(--text2)">No entries found</div>
      <div style="font-size:13px">Click <strong>+ New Entry</strong> to get started.</div>
    </div>`;
    if(grid) grid.innerHTML=empty;
    if(lbox) lbox.innerHTML='';
    return;
  }

  const fmtD=iso=>{if(!iso)return'';const d=new Date(iso);return d.toLocaleDateString('en-IN',{day:'2-digit',month:'short',year:'numeric'});};
  const today=new Date();today.setHours(0,0,0,0);
  const payLabel={'cash':'💵 Cash','credit_card':'💳 Card','bank':'🏦 Bank','upi':'📱 UPI'};
  const rtypeLabel={'principal':'Principal','interest':'Interest','both':'Principal+Interest'};

  const cardGrid = document.getElementById('fin-card-grid');
  const listBox  = document.getElementById('fin-listbox');

  const rendered = items.map(e=>{
    const st=finStatus(e);
    const rem=finRemaining(e);
    const paid=e.amount-rem;
    const intPaid=finTotalInterestPaid(e);
    const pct=e.amount>0?Math.round((paid/e.amount)*100):0;
    const isGave=e.type==='gave';
    const amtCls=st==='settled'?'settled':isGave?'gave':'borrowed';
    const pmLabel=payLabel[e.paymethod||'cash'];

    // due badge
    let dueBadge='';
    if(e.duedate&&st!=='settled'){
      const due=new Date(e.duedate);due.setHours(0,0,0,0);
      const diff=Math.round((due-today)/(1000*60*60*24));
      const dueCls=diff<0?'over':diff<=7?'warn':'ok';
      const dueText=diff<0?`Overdue ${Math.abs(diff)}d`:diff===0?'Due today':`${diff}d left`;
      dueBadge=`<span class="fin-due-badge ${dueCls}">📅 ${dueText}</span>`;
    }
    const stBadge=`<span class="fin-status-badge ${st}">${
      st==='settled'?'✅ Settled':st==='partial'?'🔵 Partial':st==='overdue'?'🔴 Overdue':'⏳ Pending'
    }</span>`;
    const interestBadge=e.interestRate>0
      ?`<span style="font-size:10px;background:#fef3c7;color:#92400e;border-radius:4px;padding:1px 6px;font-weight:700">📊 ${e.interestRate}%</span>`:'';
    const pmBadge=`<span class="fin-pay-badge ${e.paymethod||'cash'}" style="font-size:10px">${pmLabel}</span>`;
    const progressBar=pct>0&&st!=='settled'
      ?`<div class="fin-progress" style="margin-top:5px"><div class="fin-progress-fill" style="width:${pct}%"></div></div>`:'';

    // shared expand section
    const repays=e.repayments||[];
    const historyRows=repays.map((r,i)=>{
      const rtype=r.repayType||'principal';
      const rmethod=r.paymethod||'cash';
      return `<div class="fin-repay-row">
        <span class="fin-repay-amt">${finRupee(r.amount)}</span>
        <span class="fin-rtype-badge ${rtype}">${rtypeLabel[rtype]}</span>
        <span class="fin-pay-badge ${rmethod}" style="font-size:10px">${payLabel[rmethod]||rmethod}</span>
        <span class="fin-repay-note">${esc(r.note||'—')}</span>
        <span style="font-size:11px;color:var(--muted);white-space:nowrap;margin-left:auto">${fmtD(r.date)}</span>
        <button onclick="event.stopPropagation();deleteRepayment('${e.id}',${i})"
          style="background:none;border:none;cursor:pointer;color:#dc2626;font-size:11px;padding:0 4px">✕</button>
      </div>`;
    }).join('');
    const historySummary=repays.length
      ?`${repays.length} payment${repays.length!==1?'s':''}${intPaid>0?' · Interest: '+finRupee(intPaid):''}`
      :'No payments yet';
    const settleBtn=st!=='settled'?`<button class="fin-act settle" onclick="event.stopPropagation();markFinSettled('${e.id}')">✅ Settle All</button>`:'';
    const editBtn=`<button class="fin-act" onclick="event.stopPropagation();openFinModal('${e.id}')">Edit</button>`;
    const delBtn=`<button class="fin-act del" onclick="event.stopPropagation();deleteFinEntry('${e.id}')">Delete</button>`;
    const repayBtn=st!=='settled'?`<button class="fin-act" onclick="event.stopPropagation();toggleFinRepay('${e.id}')">+ Payment</button>`:'';
    const histBtn=repays.length?`<button id="fin-history-toggle-${e.id}" class="fin-act" style="padding:2px 8px;font-size:10px" onclick="event.stopPropagation();toggleFinHistory('${e.id}')">▼ History</button>`:'';
    const expandBody=`
      <div id="fin-item-expand-${e.id}">
        <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:8px">
          <span style="font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:0.8px;color:var(--muted)">Payment History</span>
          <span style="display:flex;align-items:center;gap:6px">
            <span style="font-size:11px;color:var(--muted)">${historySummary}</span>
            ${histBtn}
          </span>
        </div>
        <div id="fin-history-body-${e.id}" style="display:none">${historyRows}</div>
        <div class="fin-item-actions" style="margin-top:8px">${editBtn}${settleBtn}${repayBtn}${delBtn}</div>
        <div id="fin-addrepay-${e.id}" style="display:none;margin-top:10px;padding-top:10px;border-top:1px dashed var(--border)">
          <div style="font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:0.8px;color:var(--muted);margin-bottom:8px">Record Payment</div>
          <div style="display:flex;gap:8px;flex-wrap:wrap;align-items:center">
            <input class="fin-repay-input" id="fin-repay-amt-${e.id}" type="number" step="0.01" placeholder="Amount ₹" style="width:110px" onclick="event.stopPropagation()">
            <select class="fin-repay-input" id="fin-repay-type-${e.id}" style="width:155px" onclick="event.stopPropagation()">
              <option value="principal">💰 Principal</option>
              <option value="interest">📊 Interest Only</option>
              <option value="both">💰+📊 Both</option>
            </select>
            <select class="fin-repay-input" id="fin-repay-method-${e.id}" style="width:120px" onclick="event.stopPropagation()">
              <option value="cash">💵 Cash</option>
              <option value="credit_card">💳 Card</option>
              <option value="bank">🏦 Bank</option>
              <option value="upi">📱 UPI</option>
            </select>
            <input class="fin-repay-input" id="fin-repay-date-${e.id}" type="date" style="width:130px" value="${localToday()}" onclick="event.stopPropagation()">
            <input class="fin-repay-input" id="fin-repay-note-${e.id}" placeholder="Note (optional)" style="flex:1;min-width:80px" onclick="event.stopPropagation()">
            <button class="fin-add-repay-btn" onclick="event.stopPropagation();addRepayment('${e.id}')">Add</button>
          </div>
        </div>
      </div>`;

    // ── CARD view ──
    const typeLabel=isGave?'💚 I Gave':'❤️ I Borrowed';
    const overdueCardCls=st==='overdue'?' overdue-card':'';
    const cardHtml=`<div class="fin-card ${amtCls}${overdueCardCls}" id="fin-card-${e.id}" onclick="toggleFinExpand('${e.id}',event)">
      <div class="fin-card-eyebrow">
        <span class="fin-card-type">${typeLabel}</span>
        ${stBadge}
      </div>
      <div class="fin-card-person">${esc(e.person)}</div>
      <div class="fin-card-amount ${amtCls}">${finRupee(e.amount)}</div>
      ${e.notes?`<div class="fin-card-note">${esc(e.notes)}</div>`:''}
      <div class="fin-card-tags">
        ${pmBadge}${interestBadge}${dueBadge}
        ${rem<e.amount&&st!=='settled'?`<span class="fin-date-badge" style="font-size:10px">Rem: ${finRupee(rem)}</span>`:''}
      </div>
      ${progressBar}
      <div class="fin-card-meta">
        <span class="fin-card-date">${fmtD(e.date)}</span>
        <div class="fin-card-btns">
          <button class="cbtn" onclick="event.stopPropagation();openFinModal('${e.id}')">Edit</button>
          ${st!=='settled'?`<button class="cbtn" onclick="event.stopPropagation();toggleFinRepay('${e.id}')">+ Payment</button>`:''}
          <button class="cbtn del" onclick="event.stopPropagation();deleteFinEntry('${e.id}')">Delete</button>
        </div>
      </div>
      <div class="fin-item-expand" id="fin-item-${e.id}">${expandBody}</div>
    </div>`;

    // ── LIST row ──
    const rowHtml=`<div class="fin-lrow" id="fin-lrow-${e.id}" onclick="toggleFinExpand('${e.id}',event)">
      <div class="fin-lrow-accent ${amtCls}"></div>
      <div style="padding:0 8px">${isGave?'💚':'❤️'}</div>
      <div class="fin-lrow-main">
        <div class="fin-lrow-person">${esc(e.person)}</div>
        ${e.notes?`<div class="fin-lrow-note">${esc(e.notes)}</div>`:''}
      </div>
      <div><span class="fin-lrow-amt ${amtCls}">${finRupee(e.amount)}</span></div>
      <div style="padding:0 4px">${stBadge}</div>
      <div style="padding:0 4px">${pmBadge}</div>
      <div style="font-size:11px;color:var(--muted);font-weight:600">${fmtD(e.date)}</div>
      <div class="fin-lrow-right">
        <button class="cbtn" onclick="event.stopPropagation();openFinModal('${e.id}')">Edit</button>
        ${st!=='settled'?`<button class="cbtn" onclick="event.stopPropagation();toggleFinRepay('${e.id}')">+ Payment</button>`:''}
        <button class="cbtn del" onclick="event.stopPropagation();deleteFinEntry('${e.id}')">Delete</button>
      </div>
    </div>
    <div id="fin-item-${e.id}" class="fin-item-expand" style="padding:10px 14px;background:var(--bg);border-bottom:1px solid var(--border)">${expandBody}</div>`;

    return {cardHtml, rowHtml};
  });

  if(cardGrid){
    if(_finGroupBy){
      // 2. Group by person
      const groups={};
      rendered.forEach((r,i)=>{
        const p=items[i].person;
        if(!groups[p]) groups[p]=[];
        groups[p].push({r,e:items[i]});
      });
      const active = items.filter(e=>finStatus(e)!=='settled');
      const settled = items.filter(e=>finStatus(e)==='settled');

      let html='';
      Object.entries(groups).forEach(([person,entries])=>{
        const pendingAmt = entries.filter(({e})=>finStatus(e)!=='settled').reduce((s,{e})=>s+finRemaining(e),0);
        const net = entries.reduce(({e:en})=>en.type==='gave'?finRemaining(en):-finRemaining(en),0);
        const gKey='g'+person.replace(/\W/g,'');
        const balCls=pendingAmt>0?'pos':'pos';
        html+=`<div class="fin-group-hdr" onclick="document.getElementById('fgb-${gKey}').classList.toggle('collapsed');this.querySelector('.fin-group-chevron').classList.toggle('collapsed')">
          <span class="fin-group-chevron">▼</span>
          <span class="fin-group-name">${esc(person)}</span>
          <span class="fin-group-bal ${balCls}">${pendingAmt>0?finRupee(pendingAmt)+' pending':'✅ Settled'}</span>
          <span class="fin-group-count">${entries.length}</span>
        </div>
        <div class="fin-group-body fin-grid" id="fgb-${gKey}">
          ${entries.map(({r})=>r.cardHtml).join('')}
        </div>`;
      });
      cardGrid.innerHTML=html;
    } else {
      // 8. Separate active and settled sections
      const activeItems   = rendered.filter((_,i)=>finStatus(items[i])!=='settled');
      const settledItems  = rendered.filter((_,i)=>finStatus(items[i])==='settled');
      let html = activeItems.map(r=>r.cardHtml).join('');
      if(settledItems.length){
        html+=`<div class="fin-settled-section" style="grid-column:1/-1">
          <div class="fin-settled-hdr" onclick="finToggleSettled('main')">
            <span class="fin-group-chevron" id="fin-settled-chev-main">▼</span>
            <span style="font-size:11px;font-weight:700;text-transform:uppercase;letter-spacing:0.8px;color:var(--muted)">Settled (${settledItems.length})</span>
          </div>
          <div class="fin-group-body fin-grid fin-settled-body" id="fin-settled-body-main">
            ${settledItems.map(r=>r.cardHtml).join('')}
          </div>
        </div>`;
      }
      cardGrid.innerHTML=html;
    }
  }
  if(listBox){
    const thead = `<div class="fin-list-thead">
      <div></div>
      <div></div>
      <div class="fin-list-th">Person / Note</div>
      <div class="fin-list-th">Amount</div>
      <div class="fin-list-th">Status</div>
      <div class="fin-list-th">Method</div>
      <div class="fin-list-th">Date</div>
      <div class="fin-list-th">Actions</div>
    </div>`;
    listBox.innerHTML = thead + rendered.map(r=>r.rowHtml).join('');
  }
}
let ROUTINE_LOGS = [];
const RT_COLORS = {blue:'#3b5bdb',green:'#059669',purple:'#7c3aed',yellow:'#d97706',red:'#dc2626'};

function todayStr(){
  // Use CST (America/Chicago) timezone so routine resets at 12:00 AM CST as expected
  return new Date().toLocaleDateString('en-CA',{timeZone:'America/Chicago'}); // YYYY-MM-DD
}
function todayDayName(){
  return new Date().toLocaleDateString('en-US',{timeZone:'America/Chicago',weekday:'short'}); // Mon,Tue...
}

function updateRoutineCount(){
  const el = document.getElementById('nav-routine-count');
  if(el){
    const total = ROUTINES.reduce((a,r)=>a+(r.tasks||[]).length,0);
    el.textContent = total;
  }
}

async function saveRoutines(){
  DATA.routines     = ROUTINES;
  DATA.routine_logs = ROUTINE_LOGS;
  updateRoutineCount();
  await saveToFirebase();
}

/* -- ROUTINE DRAG-TO-REORDER ---------------------- */
function initRoutineDrag(){
  // ── GROUP-LEVEL DRAG ──────────────────────────────
  const container = document.getElementById('rt-groups-list');
  if(!container) return;
  let dragSrcGroup = null;

  container.querySelectorAll('.rt-manage-group[draggable]').forEach(el=>{
    el.addEventListener('dragstart', e=>{
      if(!e.target.closest('.rt-drag-handle')||e.target.closest('.rt-manage-task-row')){e.preventDefault();return;}
      dragSrcGroup = el;
      el.classList.add('dragging');
      e.dataTransfer.effectAllowed = 'move';
      e.dataTransfer.setData('text/plain', el.dataset.groupId);
    });
    el.addEventListener('dragend', ()=>{
      el.classList.remove('dragging');
      container.querySelectorAll('.rt-manage-group').forEach(g=>g.classList.remove('drag-over'));
      dragSrcGroup = null;
    });
    el.addEventListener('dragover', e=>{
      if(!dragSrcGroup || el === dragSrcGroup) return;
      e.preventDefault();
      e.dataTransfer.dropEffect = 'move';
      container.querySelectorAll('.rt-manage-group').forEach(g=>g.classList.remove('drag-over'));
      el.classList.add('drag-over');
    });
    el.addEventListener('dragleave', ()=>el.classList.remove('drag-over'));
    el.addEventListener('drop', e=>{
      if(!dragSrcGroup || el === dragSrcGroup) return;
      e.preventDefault();
      el.classList.remove('drag-over');
      const fromId = e.dataTransfer.getData('text/plain');
      const toId   = el.dataset.groupId;
      const fromIdx = ROUTINES.findIndex(r=>r.id===fromId);
      const toIdx   = ROUTINES.findIndex(r=>r.id===toId);
      if(fromIdx<0||toIdx<0) return;
      const [moved] = ROUTINES.splice(fromIdx, 1);
      ROUTINES.splice(toIdx, 0, moved);
      renderManageView();
      saveRoutines();
      toast('Routine order saved ✓','success');
    });
  });

  // ── TASK-LEVEL DRAG (within each group) ───────────
  container.querySelectorAll('.rt-manage-tasks[data-group-id]').forEach(tasksEl=>{
    const groupId = tasksEl.dataset.groupId;
    let dragSrcTask = null;

    tasksEl.querySelectorAll('.rt-manage-task-row[draggable]').forEach(row=>{
      row.addEventListener('dragstart', e=>{
        if(!e.target.closest('.rt-drag-handle')){e.preventDefault();return;}
        e.stopPropagation();
        dragSrcTask = row;
        row.classList.add('dragging');
        e.dataTransfer.effectAllowed = 'move';
        e.dataTransfer.setData('text/plain', row.dataset.taskId+'|'+groupId);
      });
      row.addEventListener('dragend', ()=>{
        row.classList.remove('dragging');
        tasksEl.querySelectorAll('.rt-manage-task-row').forEach(r=>r.classList.remove('drag-over-top'));
        dragSrcTask = null;
      });
      row.addEventListener('dragover', e=>{
        if(!dragSrcTask || row === dragSrcTask) return;
        e.preventDefault();
        e.dataTransfer.dropEffect = 'move';
        tasksEl.querySelectorAll('.rt-manage-task-row').forEach(r=>r.classList.remove('drag-over-top'));
        row.classList.add('drag-over-top');
      });
      row.addEventListener('dragleave', ()=>row.classList.remove('drag-over-top'));
      row.addEventListener('drop', e=>{
        if(!dragSrcTask || row === dragSrcTask) return;
        e.preventDefault();
        e.stopPropagation();
        row.classList.remove('drag-over-top');
        const raw = e.dataTransfer.getData('text/plain');
        const fromTaskId = raw.split('|')[0];
        const toTaskId   = row.dataset.taskId;
        const group = ROUTINES.find(g=>g.id===groupId);
        if(!group) return;
        const fromIdx = group.tasks.findIndex(t=>t.id===fromTaskId);
        const toIdx   = group.tasks.findIndex(t=>t.id===toTaskId);
        if(fromIdx<0||toIdx<0) return;
        const [moved] = group.tasks.splice(fromIdx, 1);
        group.tasks.splice(toIdx, 0, moved);
        renderManageView();
        saveRoutines();
        toast('Task order saved ✓','success');
      });
    });
  });
}

/* -- TASK VISIBILITY ------------------------ */
function isTaskForToday(task){
  if(task.frequency === 'daily') return true;
  if(task.frequency === 'weekly'){
    const today = todayDayName(); // e.g. "Fri"
    return (task.days||[]).includes(today);
  }
  return false;
}

function isTaskDoneToday(taskId){
  const today = todayStr();
  return ROUTINE_LOGS.some(l=>l.date===today && l.task_id===taskId && l.done);
}

function getWeekCount(taskId){
  // how many days in the last 7 days was this task done (using CST for day boundaries)
  let count = 0;
  for(let i=0;i<7;i++){
    const d = new Date(Date.now() - i*86400000);
    const ds = d.toLocaleDateString('en-CA',{timeZone:'America/Chicago'}); // YYYY-MM-DD in CST
    if(ROUTINE_LOGS.some(l=>l.date===ds && l.task_id===taskId && l.done)) count++;
  }
  return count;
}

/* -- TODAY'S CHECKLIST ---------------------- */
function renderTodayChecklist(){
  const today = todayStr();
  const label = document.getElementById('rt-today-label');
  if(label){
    const fmt = new Date().toLocaleDateString('en-US',{weekday:'long',month:'long',day:'numeric',year:'numeric'});
    label.textContent = 'Today · '+fmt;
  }

  let totalToday=0, doneToday=0;
  const container = document.getElementById('rt-checklist');
  if(!container) return;

  // If not signed in, show setup message
  if(!fbAuth.currentUser){
    container.innerHTML=`<div style="text-align:center;padding:60px 20px;color:#8898c0">
      <div style="font-size:40px;opacity:.3">🔗</div>
      <p style="font-size:15px;font-weight:700;color:#1a2040;margin-top:12px">Sign in first</p>
      <p style="font-size:13px;margin-top:6px">Click <strong>⚙️ Settings</strong> in the sidebar to sign in with Google</p>
    </div>`;
    updateProgress(0,0);
    return;
  }

  if(!ROUTINES || !ROUTINES.length){
    container.innerHTML=`<div style="text-align:center;padding:60px 20px;color:#8898c0">
      <div style="font-size:48px;opacity:.25">🔁</div>
      <p style="font-size:16px;font-weight:700;color:#1a2040;margin-top:12px">No routines set up yet</p>
      <p style="font-size:13px;color:#8898c0;margin-top:6px">Click <strong>⚙️ Manage Routines</strong> above to create your first routine</p>
      <button class="btn" style="margin-top:16px" onclick="showRoutineView('manage')">⚙️ Set Up Routines</button>
    </div>`;
    updateProgress(0,0);
    return;
  }

  let html = '';
  ROUTINES.forEach(group=>{
    const todayTasks = (group.tasks||[]).filter(isTaskForToday).sort((a,b)=>{
        if(!a.time && !b.time) return 0;
        if(!a.time) return 1;
        if(!b.time) return -1;
        return a.time.localeCompare(b.time);
      });
    if(!todayTasks.length){
      // Still show the routine card but with an "add tasks" prompt
      const colorClass = 'c-'+(group.color||'blue');
      html += `<div class="rt-group ${colorClass}" id="rtg-${group.id}">
        <div class="rt-group-header" onclick="toggleGroup('${group.id}')">
          <span class="rt-group-icon">${group.icon||'🔁'}</span>
          <span class="rt-group-name">${esc(group.name)}</span>
          <span class="rt-group-progress" style="opacity:.5">No tasks</span>
        </div>
        <div class="rt-tasks" id="rttasks-${group.id}">
          <div style="padding:12px 16px;font-size:12px;color:var(--muted);text-align:center">
            No tasks scheduled for today —
            <span style="cursor:pointer;text-decoration:underline;color:var(--accent)" onclick="showRoutineView('manage')">Add tasks</span>
          </div>
        </div>
      </div>`;
      return;
    }
    const doneTasks = todayTasks.filter(t=>isTaskDoneToday(t.id));
    totalToday += todayTasks.length;
    doneToday  += doneTasks.length;

    const colorClass = 'c-'+(group.color||'blue');
    const pct = todayTasks.length ? Math.round((doneTasks.length/todayTasks.length)*100) : 0;

    html += `<div class="rt-group ${colorClass}" id="rtg-${group.id}" draggable="true" data-group-id="${group.id}">
      <div class="rt-group-header" onclick="toggleGroup('${group.id}')">
        <span class="rt-today-drag-handle" title="Drag to reorder" onclick="event.stopPropagation()"><span><i></i><i></i></span><span><i></i><i></i></span><span><i></i><i></i></span></span>
        <span class="rt-group-icon">${group.icon||'🔁'}</span>
        <span class="rt-group-name">${esc(group.name)}</span>
        <span class="rt-group-progress">${doneTasks.length}/${todayTasks.length} · ${pct}%</span>
        <span class="rt-group-toggle" id="rtgt-${group.id}">▼</span>
      </div>
      <div class="rt-tasks" id="rttasks-${group.id}">`;

    todayTasks.forEach(task=>{
      const done = isTaskDoneToday(task.id);
      const wc   = getWeekCount(task.id);
      const freq = task.frequency==='weekly'
        ? `📅 ${(task.days||[]).join(',')}` : '🔁 Daily';
      html += `<div class="rt-task-row${done?' done':''}" onclick="toggleTask('${task.id}','${group.id}')">
        <div class="rt-checkbox">${done?'✓':''}</div>
        <div class="rt-task-info">
          <div class="rt-task-name">${esc(task.name)}</div>
          <div class="rt-task-meta">
            ${task.time?`<span class="rt-task-time">⏰ ${task.time}</span>`:''}
            <span class="rt-task-freq">${freq}</span>
          </div>
        </div>
        <span class="rt-week-count">${wc}/7 this week</span>
      </div>`;
    });

    html += `</div></div>`;
  });

  container.innerHTML = html || `<div style="text-align:center;padding:40px;color:#8898c0;font-size:13px">
    No tasks scheduled for today</div>`;
  updateProgress(doneToday, totalToday);
  initTodayDrag();
}

function initTodayDrag(){
  const container = document.getElementById('rt-checklist');
  if(!container) return;
  let dragSrc = null;
  let dragAllowed = false;

  // Set draggable only when mousedown is on the handle
  container.querySelectorAll('.rt-today-drag-handle').forEach(handle=>{
    handle.addEventListener('mousedown', ()=>{ dragAllowed = true; });
  });
  document.addEventListener('mouseup', ()=>{ dragAllowed = false; }, true);

  container.querySelectorAll('.rt-group[draggable]').forEach(el=>{
    el.addEventListener('dragstart', e=>{
      if(!dragAllowed){ e.preventDefault(); return; }
      dragSrc = el;
      el.classList.add('today-dragging');
      e.dataTransfer.effectAllowed = 'move';
      e.dataTransfer.setData('text/plain', el.dataset.groupId);
    });
    el.addEventListener('dragend', ()=>{
      dragAllowed = false;
      el.classList.remove('today-dragging');
      container.querySelectorAll('.rt-group').forEach(g=>g.classList.remove('today-drag-over'));
      dragSrc = null;
    });
    el.addEventListener('dragover', e=>{
      if(!dragSrc || el===dragSrc) return;
      e.preventDefault();
      container.querySelectorAll('.rt-group').forEach(g=>g.classList.remove('today-drag-over'));
      el.classList.add('today-drag-over');
    });
    el.addEventListener('dragleave', ()=>el.classList.remove('today-drag-over'));
    el.addEventListener('drop', e=>{
      if(!dragSrc || el===dragSrc) return;
      e.preventDefault();
      el.classList.remove('today-drag-over');
      const fromId = e.dataTransfer.getData('text/plain');
      const toId   = el.dataset.groupId;
      const fromIdx = ROUTINES.findIndex(r=>r.id===fromId);
      const toIdx   = ROUTINES.findIndex(r=>r.id===toId);
      if(fromIdx<0||toIdx<0) return;
      const [moved] = ROUTINES.splice(fromIdx,1);
      ROUTINES.splice(toIdx,0,moved);
      renderTodayChecklist();
      saveRoutines();
      toast('Routine order saved ✓','success');
    });
  });
}

function updateProgress(done, total){
  const pct = total ? Math.round((done/total)*100) : 0;
  const fill = document.getElementById('rt-progress-fill');
  const label = document.getElementById('rt-progress-pct');
  if(fill)  fill.style.width = pct+'%';
  if(fill)  fill.style.background = pct===100 ? '#059669' : '#3b5bdb';
  if(label) label.textContent = pct===100 ? '🎉 All done!' : `${done}/${total} done today`;
  if(label) label.style.color = pct===100 ? '#059669' : '#3b5bdb';
}

function toggleGroup(groupId){
  const tasks = document.getElementById('rttasks-'+groupId);
  const icon  = document.getElementById('rtgt-'+groupId);
  if(!tasks) return;
  const hidden = tasks.style.display==='none';
  tasks.style.display = hidden ? '' : 'none';
  if(icon) icon.textContent = hidden ? '▼' : '▶';
}

async function toggleTask(taskId, groupId){
  const today = todayStr();
  const idx = ROUTINE_LOGS.findIndex(l=>l.date===today && l.task_id===taskId);
  if(idx>=0){
    ROUTINE_LOGS[idx].done = !ROUTINE_LOGS[idx].done;
  } else {
    ROUTINE_LOGS.push({date:today, task_id:taskId, done:true});
  }
  // trim old logs (keep last 30 days)
  const cutoff = new Date();
  cutoff.setDate(cutoff.getDate()-30);
  ROUTINE_LOGS = ROUTINE_LOGS.filter(l=>new Date(l.date)>=cutoff);
  renderTodayChecklist();
  await saveRoutines();
}

/* -- MANAGE VIEW ---------------------------- */
function renderManageView(){
  const container = document.getElementById('rt-groups-list');
  if(!container) return;
  if(!ROUTINES.length){
    container.innerHTML=`<div style="text-align:center;padding:48px;color:#8898c0;font-size:13px">
      No routines yet. Click <strong>+ New Routine</strong> to get started.</div>`;
    return;
  }
  container.innerHTML = ROUTINES.map((group,gi)=>`
    <div class="rt-manage-group" draggable="true" data-group-id="${group.id}" data-group-idx="${gi}">
      <div class="rt-manage-group-header">
        <span class="rt-drag-handle" title="Drag to reorder">⠿</span>
        <span style="font-size:18px">${group.icon||'🔁'}</span>
        <span class="rt-manage-group-name">${esc(group.name)}</span>
        <button class="rt-mg-btn" onclick="openRoutineGroupModal('${group.id}')">Edit</button>
        <button class="rt-mg-btn del" onclick="deleteRoutineGroup('${group.id}')">Delete</button>
      </div>
      <div class="rt-manage-tasks" data-group-id="${group.id}">
        ${(group.tasks||[]).slice().sort((a,b)=>{if(!a.time&&!b.time)return 0;if(!a.time)return 1;if(!b.time)return -1;return a.time.localeCompare(b.time);}).map((t,ti)=>`
        <div class="rt-manage-task-row" draggable="true" data-task-id="${t.id}" data-task-idx="${ti}" data-task-group="${group.id}">
          <span class="rt-drag-handle" title="Drag to reorder">⠿</span>
          <div class="rt-mtr-info">
            <div class="rt-mtr-name">${esc(t.name)}</div>
            <div class="rt-mtr-meta">${t.frequency==='weekly'?'📅 '+((t.days||[]).join(', ')):'🔁 Daily'}${t.time?' · ⏰ '+t.time:''}</div>
          </div>
          <button class="rt-mg-btn" onclick="openRoutineTaskModal('${group.id}','${t.id}')">Edit</button>
          <button class="rt-mg-btn del" onclick="deleteRoutineTask('${group.id}','${t.id}')">Remove</button>
        </div>`).join('')}
      </div>
      <div class="rt-add-task-row">
        <button class="rt-add-task-btn" onclick="openRoutineTaskModal('${group.id}')">+ Add task to ${esc(group.name)}</button>
      </div>
    </div>`).join('');
  initRoutineDrag();
}


function showRoutineView(view){
  document.getElementById('rt-today-view').style.display  = view==='today'   ? '' : 'none';
  document.getElementById('rt-manage-view').style.display = view==='manage'  ? '' : 'none';
  document.getElementById('rt-back-btn').style.display    = view==='manage'  ? ''  : 'none';
  if(view==='today')  renderTodayChecklist();
  if(view==='manage') renderManageView();
}

/* -- GROUP MODAL ---------------------------- */
let _editGroupId = null;
function openRoutineGroupModal(groupId){
  _editGroupId = groupId||null;
  const existing = groupId ? ROUTINES.find(r=>r.id===groupId) : null;
  document.getElementById('rt-group-modal-title').textContent = existing ? 'Edit Routine' : 'New Routine';
  document.getElementById('rt-group-edit-id').value = groupId||'';
  document.getElementById('rt-group-name').value   = existing?.name  || '';
  document.getElementById('rt-group-color').value  = existing?.color || 'blue';
  document.getElementById('rt-group-icon').value   = existing?.icon  || '🌅';
  document.querySelectorAll('.rt-icon-opt').forEach(el=>{
    el.classList.toggle('selected', el.textContent===(existing?.icon||'🌅'));
  });
  document.getElementById('rt-group-modal').classList.add('open');
}
function closeRoutineGroupModal(){ document.getElementById('rt-group-modal').classList.remove('open'); }

function selectIcon(el, icon){
  document.querySelectorAll('.rt-icon-opt').forEach(e=>e.classList.remove('selected'));
  el.classList.add('selected');
  document.getElementById('rt-group-icon').value = icon;
}

async function saveRoutineGroup(){
  const name = document.getElementById('rt-group-name').value.trim();
  if(!name){ toast('Routine name required','error'); return; }
  const id  = document.getElementById('rt-group-edit-id').value;
  const grp = {
    id:    id || 'rt'+Date.now().toString(36),
    name,
    icon:  document.getElementById('rt-group-icon').value || '🌅',
    color: document.getElementById('rt-group-color').value || 'blue',
    tasks: id ? (ROUTINES.find(r=>r.id===id)?.tasks||[]) : []
  };
  if(id){ ROUTINES = ROUTINES.map(r=>r.id===id?grp:r); }
  else  {
    // Guard: prevent duplicate if already added (e.g. double-click)
    if(!ROUTINES.find(r=>r.id===grp.id)) ROUTINES.push(grp);
  }
  closeRoutineGroupModal();
  renderManageView();
  await saveRoutines();
  toast('Routine saved ✓','success');
}

async function deleteRoutineGroup(id){
  if(!confirm('Delete this routine and all its tasks?')) return;
  ROUTINES = ROUTINES.filter(r=>r.id!==id);
  renderManageView();
  await saveRoutines();
  toast('Routine deleted','success');
}

/* -- TASK MODAL ----------------------------- */
function openRoutineTaskModal(groupId, taskId){
  const group    = ROUTINES.find(r=>r.id===groupId);
  const existing = taskId ? (group?.tasks||[]).find(t=>t.id===taskId) : null;
  document.getElementById('rt-task-modal-title').textContent = existing ? 'Edit Task' : 'Add Task';
  document.getElementById('rt-task-edit-id').value   = taskId  || '';
  document.getElementById('rt-task-group-id').value  = groupId || '';
  document.getElementById('rt-task-name').value      = existing?.name  || '';
  document.getElementById('rt-task-time').value      = existing?.time  || '';
  document.getElementById('rt-task-freq').value      = existing?.frequency || 'daily';
  // reset day selections
  document.querySelectorAll('.rt-day-opt').forEach(el=>{
    el.classList.toggle('selected', (existing?.days||[]).includes(el.dataset.day));
  });
  toggleWeekdays();
  document.getElementById('rt-task-modal').classList.add('open');
}
function closeRoutineTaskModal(){ document.getElementById('rt-task-modal').classList.remove('open'); }

function toggleWeekdays(){
  const freq = document.getElementById('rt-task-freq').value;
  document.getElementById('rt-weekdays-row').style.display = freq==='weekly' ? '' : 'none';
}

// day picker toggle
document.addEventListener('click', e=>{
  if(e.target.classList.contains('rt-day-opt')){
    e.target.classList.toggle('selected');
  }
});

async function saveRoutineTask(){
  const name = document.getElementById('rt-task-name').value.trim();
  if(!name){ toast('Task name required','error'); return; }
  const groupId  = document.getElementById('rt-task-group-id').value;
  const taskId   = document.getElementById('rt-task-edit-id').value;
  const freq     = document.getElementById('rt-task-freq').value;
  const selDays  = [...document.querySelectorAll('.rt-day-opt.selected')].map(e=>e.dataset.day);
  const task = {
    id:        taskId || 'tk'+Date.now().toString(36),
    name,
    time:      document.getElementById('rt-task-time').value,
    frequency: freq,
    days:      freq==='weekly' ? selDays : []
  };
  const group = ROUTINES.find(r=>r.id===groupId);
  if(!group){ toast('Routine not found','error'); return; }
  if(taskId){ group.tasks = group.tasks.map(t=>t.id===taskId?task:t); }
  else      { group.tasks = [...(group.tasks||[]), task]; }
  closeRoutineTaskModal();
  renderManageView();
  await saveRoutines();
  toast('Task saved ✓','success');
}

async function deleteRoutineTask(groupId, taskId){
  if(!confirm('Remove this task?')) return;
  const group = ROUTINES.find(r=>r.id===groupId);
  if(group) group.tasks = group.tasks.filter(t=>t.id!==taskId);
  renderManageView();
  await saveRoutines();
  toast('Task removed','success');
}

/* ============================================================
   DASHBOARD WIDGETS
   ============================================================ */
/* -- DASHBOARD MINI CALENDAR -------------------- */
let _dashCalYear  = new Date().getFullYear();
let _dashCalMonth = new Date().getMonth();
let _dashCalSelDay = null; // 'YYYY-MM-DD' or null = show all upcoming

function dashCalNav(dir){
  _dashCalMonth += dir;
  if(_dashCalMonth > 11){_dashCalMonth=0;_dashCalYear++;}
  if(_dashCalMonth < 0){_dashCalMonth=11;_dashCalYear--;}
  _dashCalSelDay = null;
  renderDashCal();
}

function renderDashCal(){
  const reminders = DATA.reminders||[];
  const now = new Date();
  const todayStr = localToday();
  const year  = _dashCalYear;
  const month = _dashCalMonth;

  // Build set of dates that have pending reminders
  const remDates = {};
  reminders.filter(r=>!r.sent&&r.due).forEach(r=>{
    const d = r.due.slice(0,10);
    if(!remDates[d]) remDates[d]=[];
    remDates[d].push(r);
  });

  // Calendar header
  const monthLabel = document.getElementById('dash-cal-month-label');
  if(monthLabel) monthLabel.textContent = new Date(year,month,1)
    .toLocaleDateString('en-US',{month:'long',year:'numeric'});

  // Build calendar grid
  const grid = document.getElementById('dash-cal-grid');
  if(!grid) return;
  const days=['Su','Mo','Tu','We','Th','Fr','Sa'];
  let html = days.map(d=>`<div class="dash-cal-day-label">${d}</div>`).join('');

  const firstDay = new Date(year,month,1).getDay();
  const daysInMonth = new Date(year,month+1,0).getDate();
  const daysInPrev  = new Date(year,month,0).getDate();

  // prev month filler
  for(let i=firstDay-1;i>=0;i--){
    html+=`<div class="dash-cal-cell other-month">${daysInPrev-i}</div>`;
  }
  // current month days
  for(let d=1;d<=daysInMonth;d++){
    const ds=`${year}-${String(month+1).padStart(2,'0')}-${String(d).padStart(2,'0')}`;
    const isToday=ds===todayStr;
    const hasRem=!!remDates[ds];
    const isSel=_dashCalSelDay===ds;
    let cls='dash-cal-cell';
    if(isToday) cls+=' is-today';
    if(hasRem)  cls+=' has-rem';
    if(isSel)   cls+=' selected-day';
    const click=hasRem?`onclick="dashCalSelectDay('${ds}')"` :'';
    html+=`<div class="${cls}" ${click} title="${hasRem?remDates[ds].length+' reminder(s)':''}">${d}</div>`;
  }
  // next month filler
  const totalCells = firstDay + daysInMonth;
  const remainder = totalCells%7===0?0:7-(totalCells%7);
  for(let i=1;i<=remainder;i++){
    html+=`<div class="dash-cal-cell other-month">${i}</div>`;
  }
  grid.innerHTML=html;

  // Update the list panel
  renderDashUpcomingList(remDates, todayStr, reminders);
}

function dashCalSelectDay(ds){
  _dashCalSelDay = (_dashCalSelDay===ds) ? null : ds;
  renderDashCal();
}

/* ── Dashboard click-through helpers ─────────────────── */
async function dashToggleRoutineTask(groupId, taskId){
  await toggleTask(taskId, groupId);
  updateDashboardWidgets();
}

function dashGoToImpDate(id){
  const pin = dbGetPin();
  const btn = document.querySelector('.nav-item[onclick*="impdates"]');
  showPage('impdates', btn);
  if(pin && !_impUnlocked){
    // PIN protected — just navigate, user must unlock first
    toast('Unlock Important Dates to edit this entry.', 'info');
  } else {
    setTimeout(()=>{ impOpenModal(id); }, 200);
  }
}
/* ────────────────────────────────────────────────────── */

function renderDashUpcomingList(remDates, todayStr, reminders){
  const upcomingEl = document.getElementById('dash-upcoming-list');
  if(!upcomingEl) return;

  let list;
  if(_dashCalSelDay){
    // show reminders for selected day
    list = (remDates[_dashCalSelDay]||[]).sort((a,b)=>a.due.localeCompare(b.due));
  } else {
    // show next upcoming across all days
    list = reminders
      .filter(r=>!r.sent&&r.due&&new Date(r.due.slice(0,10)+'T00:00:00')>=new Date(todayStr+'T00:00:00'))
      .sort((a,b)=>a.due.localeCompare(b.due))
      .slice(0,6);
  }

  if(!list.length){
    upcomingEl.innerHTML='<div class="dash-empty">No upcoming reminders — all clear! ✓</div>';
    return;
  }
  upcomingEl.innerHTML = list.map(r=>{
    const dueStr=r.due.slice(0,10);
    const diffDays=Math.round((new Date(dueStr+'T00:00:00')-new Date(todayStr+'T00:00:00'))/(864e5));
    let dotCls,dueLabel;
    if(diffDays===0){dotCls='upc-dot-today';dueLabel='Today';}
    else if(diffDays===1){dotCls='upc-dot-soon';dueLabel='Tomorrow';}
    else if(diffDays<=7){dotCls='upc-dot-soon';dueLabel='In '+diffDays+' days';}
    else{dotCls='upc-dot-future';dueLabel=new Date(dueStr+'T00:00:00').toLocaleDateString('en-US',{month:'short',day:'numeric'});}
    const dueCls = diffDays===0 ? 'upc-due upc-due-today' : 'upc-due';
    return `<div class="upc-item" onclick="editItem('${r.id}')" style="cursor:pointer">
      <div class="upc-dot ${dotCls}"></div>
      <div class="upc-title">${r.title||'Untitled'}</div>
      <div class="${dueCls}">${dueLabel}</div>
    </div>`;
  }).join('');
}

function updateDashboardWidgets(){
  const notes      = DATA.notes||[];
  const reminders  = DATA.reminders||[];
  const now        = new Date();
  const todayStr   = localToday();

  // -- GREETING BANNER --
  const hour = now.getHours();
  const greetText = document.getElementById('dash-greet-text');
  const greetDate = document.getElementById('dash-greet-date');
  const greetEmoji = document.getElementById('dash-greet-emoji');
  if(greetText){
    const userName = (fbAuth.currentUser?.displayName||'').split(' ')[0] || '';
    const displayName = userName ? userName.charAt(0).toUpperCase()+userName.slice(1).toLowerCase() : '';
    let greeting, emoji;
    if(hour>=5&&hour<12){greeting='Good morning';emoji='🌅';}
    else if(hour>=12&&hour<17){greeting='Good afternoon';emoji='☀️';}
    else if(hour>=17&&hour<21){greeting='Good evening';emoji='🌆';}
    else{greeting='Good night';emoji='🌙';}
    greetText.textContent = greeting+(displayName?' '+displayName:'')+' 👋';
    if(greetEmoji) greetEmoji.textContent = emoji;
  }
  if(greetDate){
    greetDate.textContent = now.toLocaleDateString('en-US',{weekday:'long',year:'numeric',month:'long',day:'numeric'});
  }

  // -- UPCOMING REMINDERS CALENDAR + LIST --
  renderDashCal();

  // -- STAT CARDS --
  const totalItems  = notes.length + reminders.length;
  const pending     = reminders.filter(r=>!r.sent && !isOverdue(r)).length;
  const completed   = reminders.filter(r=>r.sent).length;
  const missed      = reminders.filter(r=>isOverdue(r)).length;

  const elNotes   = document.getElementById('stat-notes');
  const elPend    = document.getElementById('stat-pending');
  const elFiles   = document.getElementById('stat-files');
  const elRem     = document.getElementById('stat-reminders');
  if(elNotes) elNotes.textContent = totalItems;
  if(elPend)  elPend.textContent  = pending;
  if(elFiles) elFiles.textContent = completed;
  if(elRem)   elRem.textContent   = missed;

  const pendSub = document.getElementById('stat-pending-sub');
  if(pendSub) pendSub.textContent = pending===1?'1 reminder due':''+pending+' reminders due';
  const compSub = document.getElementById('stat-completed-sub');
  if(compSub) compSub.textContent = completed===1?'1 done':''+completed+' done';

  // -- PROGRESS BAR (Routines + Reminders combined) --
  // Reminders: count total and completed (sent)
  const remTotal     = reminders.length;
  const remDone      = reminders.filter(r=>r.sent).length;
  // Routines: count today's tasks and how many are done
  let routineTotal = 0, routineDone = 0;
  (ROUTINES||[]).forEach(group=>{
    const todayTasks = (group.tasks||[]).filter(isTaskForToday);
    routineTotal += todayTasks.length;
    routineDone  += todayTasks.filter(t=>isTaskDoneToday(t.id)).length;
  });
  const total = remTotal + routineTotal;
  const done  = remDone  + routineDone;
  const pct   = total>0 ? Math.round((done/total)*100) : 0;
  const progFill  = document.getElementById('dash-prog-fill');
  const progLabel = document.getElementById('dash-prog-label');
  if(progFill)  progFill.style.width  = pct+'%';
  if(progLabel) progLabel.textContent = done+' of '+total+' done — '+pct+'%';
  // Update SVG progress ring
  const ring    = document.getElementById('dash-prog-ring');
  const ringPct = document.getElementById('dash-prog-pct-ring');
  const circ    = 175.9;
  if(ring)    ring.style.strokeDashoffset    = circ*(1-pct/100);
  if(ringPct) ringPct.textContent             = pct+'%';

  // -- NEXT ROUTINE --
  const routineEl = document.getElementById('dash-routine-list');
  if(routineEl){
    const allItems = [];
    (ROUTINES||[]).forEach(group=>{
      (group.tasks||[]).filter(isTaskForToday).forEach(task=>{
        if(task.time) allItems.push({name:task.name||'Routine',time:task.time,id:task.id,groupId:group.id,group:group.name||''});
      });
    });
    allItems.sort((a,b)=>a.time.localeCompare(b.time));

    if(!allItems.length){
      routineEl.innerHTML='<div class="dash-empty">No routines set up yet.</div>';
    } else {
      const nowMins = now.getHours()*60+now.getMinutes();
      // Find upcoming tasks that are not yet done
      const upcoming = allItems.filter(it=>{
        const [h,m] = it.time.split(':').map(Number);
        return (h*60+m) >= nowMins && !isTaskDoneToday(it.id);
      });
      // Fallback: any undone tasks today regardless of time
      const undone = allItems.filter(it=>!isTaskDoneToday(it.id));
      const toShow = (upcoming.length ? upcoming : undone).slice(0,2);

      if(!toShow.length){
        routineEl.innerHTML='<div class="dash-empty" style="color:var(--green);font-style:normal">✅ All routines done for now!</div>';
        return;
      }

      let nextMarked = false;
      const html = toShow.map(it=>{
        const [h,m] = it.time.split(':').map(Number);
        const itemMins = h*60+m;
        const diffMins = itemMins - nowMins;
        let badge='', countdown='', cls='';
        if(!nextMarked && diffMins>=0){
          nextMarked=true;
          cls=' ri-next';
          badge='<span class="ri-badge badge-next">Next</span>';
          if(diffMins<=60&&diffMins>0) countdown=`<div class="ri-countdown">▶ in ${diffMins} min</div>`;
          else if(diffMins===0) countdown='<div class="ri-countdown">▶ Now</div>';
        } else {
          badge='<span class="ri-badge badge-soon">Soon</span>';
        }
        return `<div class="ri${cls}" onclick="dashToggleRoutineTask('${it.groupId}','${it.id}')" style="cursor:pointer" title="Click to mark complete">
          <div class="ri-time">${it.time}</div>
          <div class="ri-info"><div class="ri-name">${it.name}</div>${countdown}</div>
          ${badge}
        </div>`;
      }).join('');
      routineEl.innerHTML = html;
    }
  }

  // -- MISSED & OVERDUE --
  const missedEl = document.getElementById('dash-missed-list');
  if(missedEl){
    const overdueRems = reminders.filter(r=>isOverdue(r))
      .sort((a,b)=>(b.due||'').localeCompare(a.due||''))
      .slice(0,4);

    const overdueTasks = (TASKNOTES||[])
      .filter(t=>!t.done && t.date && t.date.slice(0,10) < todayStr)
      .slice(0,3);

    const allOverdue = [
      ...overdueRems.map(r=>({type:'rem', title:r.title||'Untitled', due:r.due.slice(0,10), cat:r.category||'personal'})),
      ...overdueTasks.map(t=>({type:'task', title:t.text||'Untitled', due:new Date(t.date).toISOString().slice(0,10), cat:t.category||'personal'}))
    ];

    if(!allOverdue.length){
      missedEl.innerHTML='<div class="dash-empty">✨ All clear — you\'re on top of everything!</div>';
      // Auto-collapse when empty
      const widget=document.getElementById('dash-missed-widget');
      if(widget) widget.style.opacity='.6';
    } else {
      const widget=document.getElementById('dash-missed-widget');
      if(widget) widget.style.opacity='1';
      missedEl.innerHTML = allOverdue.map(item=>{
        const dueDate = new Date(item.due+'T00:00:00');
        const diffDays = Math.round((now-dueDate)/(1000*60*60*24));
        const ageLabel = diffDays<=0?'Today':diffDays===1?'1d ago':diffDays+'d ago';
        const icon = item.type==='task' ? '✍️' : '🔔';
        return `<div class="mi">
          <div class="mi-icon">${icon}</div>
          <div class="mi-info">
            <div class="mi-name">${item.title}</div>
            <div class="mi-meta">Due ${item.due} · ${item.cat}</div>
          </div>
          <span class="mi-age">${ageLabel}</span>
        </div>`;
      }).join('');
    }
  }

  // -- TASKS WIDGET --
  renderDashTasks();

  // -- IMPORTANT DATES --
  impRenderDashboard();
}

function renderDashTasks(){
  const tasks = TASKNOTES || [];
  const open  = tasks.filter(t=>!t.done);
  const done  = tasks.filter(t=>t.done);

  const openCountEl = document.getElementById('dash-tasks-open-count');
  const doneCountEl = document.getElementById('dash-tasks-done-count');
  if(openCountEl) openCountEl.textContent = open.length+' open';
  if(doneCountEl) doneCountEl.textContent = done.length+' done';

  const catLabel = c => c==='official'?'💼 Official':'👤 Personal';
  const prioLabel = {high:'High',medium:'Med',low:'Low'};
  const fmtD = iso => iso ? new Date(iso).toLocaleDateString('en-IN',{day:'2-digit',month:'short'}) : '';

  function taskRow(t){
    return `<div class="dash-task-row${t.done?' is-done':''}" onclick="showPage('tasknotes',document.getElementById('nav-tasknotes-btn'))">
      <div class="dash-task-cb${t.done?' done':''}" onclick="event.stopPropagation();toggleTanDone('${t.id}')">${t.done?'✓':''}</div>
      <span class="dash-task-prio ${t.priority||'medium'}">${prioLabel[t.priority||'medium']}</span>
      <span class="dash-task-text">${esc(t.text||'')}</span>
      <span class="dash-task-cat">${catLabel(t.category||'personal')}</span>
      <span class="dash-task-date">${fmtD(t.date||t.created)}</span>
    </div>`;
  }

  const openEl = document.getElementById('dash-tasks-open');
  const doneEl = document.getElementById('dash-tasks-done');

  if(openEl){
    openEl.innerHTML = open.length
      ? open.map(taskRow).join('')
      : '<div class="dash-empty" style="padding:10px 0">No open tasks 🎉</div>';
  }
  if(doneEl){
    doneEl.innerHTML = done.length ? done.slice(0,5).map(taskRow).join('') : '';
  }

  const toggle = document.getElementById('dash-tasks-done-toggle');
  if(toggle) toggle.textContent = (done.length?'▶ ':'')+`Completed (${done.length})`;
}

function dashToggleCompletedTasks(){
  const el  = document.getElementById('dash-tasks-done');
  const btn = document.getElementById('dash-tasks-done-toggle');
  if(!el) return;
  const hidden = el.style.display==='none';
  el.style.display = hidden ? '' : 'none';
  if(btn){
    const done = (TASKNOTES||[]).filter(t=>t.done).length;
    btn.textContent = (hidden?'▼ ':'▶ ')+`Completed (${done})`;
  }
}

/* ============================================================
   BROWSER NOTIFICATION ALERTS
   ============================================================ */
let _notifCheckInterval = null;

function requestNotifPermission(){
  if(!('Notification' in window)){ toast('Notifications not supported in this browser','error'); return; }
  Notification.requestPermission().then(p=>{
    if(p==='granted'){
      document.getElementById('notif-prompt').style.display='none';
      toast('Notifications enabled! ✓','success');
      startNotifChecker();
    } else {
      toast('Notification permission denied','error');
    }
  });
}

function checkNotifPermissionPrompt(){
  if(!('Notification' in window)) return;
  if(Notification.permission==='default'){
    const prompt = document.getElementById('notif-prompt');
    if(prompt) prompt.style.display='flex';
  } else if(Notification.permission==='granted'){
    startNotifChecker();
  }
}

function startNotifChecker(){
  if(_notifCheckInterval) return;
  _notifCheckInterval = setInterval(checkDueReminders, 60000);
  checkDueReminders(); // run immediately
}

function checkDueReminders(){
  if(Notification.permission!=='granted') return;
  const now = new Date();
  const notified = JSON.parse(localStorage.getItem('notified_ids')||'[]');
  (DATA.reminders||[]).forEach(r=>{
    if(r.sent || notified.includes(r.id)) return;
    try{
      const due = new Date(r.due.replace(' ','T'));
      const diff = due - now;
      // fire if within next 5 minutes or already overdue today (up to 24hr past)
      if(diff <= 5*60*1000 && diff > -24*60*60*1000){
        const n = new Notification('⏰ '+r.title,{
          body: r.body||(r.due?'Due: '+r.due:''),
          icon: 'data:image/svg+xml,<svg xmlns=%22http://www.w3.org/2000/svg%22 viewBox=%220 0 100 100%22><text y=%22.9em%22 font-size=%2290%22>⏰</text></svg>'
        });
        n.onclick=()=>{ window.focus(); showPage('reminders',document.getElementById('nav-reminders-btn')); };
        notified.push(r.id);
        localStorage.setItem('notified_ids', JSON.stringify(notified));
      }
    }catch{}
  });
  // Refresh badge count after checking reminders
  updateBadge();
}

// Clear notified list daily so recurring reminders fire again
function cleanNotifiedIds(){
  const key = 'notified_date';
  const today = localToday();
  if(localStorage.getItem(key)!==today){
    localStorage.removeItem('notified_ids');
    localStorage.setItem(key, today);
  }
}

/* ============================================================
   BADGE COUNT — browser tab favicon + PWA home screen icon
   Counts: today's incomplete tasks + pending/overdue reminders
   ============================================================ */
function getBadgeCount(){
  const today = localToday();

  // 1. Today's incomplete tasks (TASKNOTES)
  const pendingTasks = (TASKNOTES||[]).filter(t=>{
    if(t.done) return false;
    const taskDate = (t.date||t.created||'').slice(0,10);
    return taskDate === today;
  }).length;

  // 2. Overdue reminders (including today's past-time items)
  const now = new Date();
  const pendingRem = (DATA.reminders||[]).filter(r=>isOverdue(r)).length;

  return pendingTasks + pendingRem;
}

function drawFaviconBadge(count){
  // Build a 32x32 favicon; if count>0 overlay a red circle with number
  const canvas = document.createElement('canvas');
  canvas.width = 32; canvas.height = 32;
  const ctx = canvas.getContext('2d');

  // Base icon — simple bell emoji rendered as text
  ctx.font = '24px serif';
  ctx.textAlign = 'center';
  ctx.textBaseline = 'middle';
  ctx.fillText('⏰', 16, 17);

  if(count > 0){
    const label = count > 99 ? '99+' : String(count);
    const r = label.length > 1 ? 10 : 8;
    // red circle
    ctx.beginPath();
    ctx.arc(26, 6, r, 0, Math.PI*2);
    ctx.fillStyle = '#e53935';
    ctx.fill();
    // white number
    ctx.fillStyle = '#ffffff';
    ctx.font = `bold ${label.length > 1 ? 9 : 11}px Inter,sans-serif`;
    ctx.textAlign = 'center';
    ctx.textBaseline = 'middle';
    ctx.fillText(label, 26, 6);
  }

  // Update or create favicon link element
  let link = document.querySelector("link[rel~='icon']");
  if(!link){
    link = document.createElement('link');
    link.rel = 'icon';
    document.head.appendChild(link);
  }
  link.href = canvas.toDataURL('image/png');
}

function updateBadge(){
  const count = getBadgeCount();

  // 1. Favicon badge (browser tab)
  try{ drawFaviconBadge(count); }catch(e){}

  // 2. PWA home screen badge (iOS 16.4+ / Android Chrome)
  try{
    if('setAppBadge' in navigator){
      if(count > 0) navigator.setAppBadge(count);
      else          navigator.clearAppBadge();
    }
  }catch(e){}

  // 3. Update document title to show count
  const baseTitle = 'My Notes & Reminders';
  document.title = count > 0 ? `(${count}) ${baseTitle}` : baseTitle;
}

// Start badge updater — runs every 60s independently of notification permission
let _badgeInterval = null;
function startBadgeUpdater(){
  updateBadge(); // run immediately
  if(_badgeInterval) return;
  _badgeInterval = setInterval(updateBadge, 60000);
}

/* ============================================================
   MINI CALENDAR FOR REMINDERS
   ============================================================ */
let _calYear  = new Date().getFullYear();
let _calMonth = new Date().getMonth();
let _calSelDate = null;
let _rrpSelDate = null; // selected date in the right-panel mini-cal for filtering
let _remViewMode = 'list';

function setRemView(mode, btn){
  _remViewMode = mode;
  document.querySelectorAll('.rem-view-toggle .tan-filter-btn').forEach(b=>b.classList.remove('active'));
  if(btn) btn.classList.add('active');
  const calWrap  = document.getElementById('full-cal-wrap');
  const remTiles = document.querySelector('#page-reminders .rem-summary-row');
  const remCols  = document.querySelector('#page-reminders .rem-columns');
  if(mode==='cal'){
    if(calWrap)  calWrap.classList.add('active');
    if(remTiles) remTiles.style.display='none';
    if(remCols)  remCols.style.display='none';
    renderFullCal();
  } else {
    if(calWrap)  calWrap.classList.remove('active');
    if(remTiles) remTiles.style.display='';
    if(remCols)  remCols.style.display='';
  }
}

function calNav(dir){
  _calMonth += dir;
  if(_calMonth > 11){ _calMonth=0; _calYear++; }
  if(_calMonth < 0 ){ _calMonth=11; _calYear--; }
  renderFullCal();
}

function calGoToday(){
  const now = new Date();
  _calYear  = now.getFullYear();
  _calMonth = now.getMonth();
  renderFullCal();
}

function renderFullCal(){
  const titleEl = document.getElementById('full-cal-title');
  const grid    = document.getElementById('full-cal-grid');
  if(!titleEl||!grid) return;

  const monthNames=['January','February','March','April','May','June',
    'July','August','September','October','November','December'];
  titleEl.textContent = monthNames[_calMonth]+' '+_calYear;

  // Build reminder map: date string -> array of reminders
  const remMap = {};
  (DATA.reminders||[]).forEach(r=>{
    if(!r.due) return;
    const ds = r.due.slice(0,10);
    if(!remMap[ds]) remMap[ds]=[];
    remMap[ds].push(r);
  });

  const today       = localToday();
  const firstDay    = new Date(_calYear, _calMonth, 1).getDay(); // 0=Sun
  const daysInMonth = new Date(_calYear, _calMonth+1, 0).getDate();
  // prev month fill
  const prevDays    = new Date(_calYear, _calMonth, 0).getDate();

  // Colour palette for categories
  const catColor = {
    personal: 'prio-medium',
    official: 'prio-high'
  };
  const prioClass = p => p==='high'?'prio-high':p==='low'?'prio-low':'prio-medium';

  let html = '';

  // Leading days from prev month
  for(let i=firstDay-1; i>=0; i--){
    const d = prevDays - i;
    html += `<div class="full-cal-cell other-month">
      <div class="full-cal-day-num">${d}</div>
    </div>`;
  }

  // Days in current month
  for(let d=1; d<=daysInMonth; d++){
    const ds = _calYear+'-'+String(_calMonth+1).padStart(2,'0')+'-'+String(d).padStart(2,'0');
    const isToday   = ds===today;
    const isWeekend = [0,6].includes(new Date(_calYear,_calMonth,d).getDay());
    const rems      = remMap[ds]||[];

    const MAX_SHOW = 3;
    const visible  = rems.slice(0, MAX_SHOW);
    const overflow = rems.length - MAX_SHOW;

    const eventsHtml = visible.map(r=>{
      const pc = prioClass(r.priority||'medium');
      const doneClass = r.sent ? ' ev-done' : '';
      const timeStr = r.due.length>10 ? r.due.slice(11,16)+' ' : '';
      return `<div class="full-cal-event ${pc}${doneClass}" onclick="event.stopPropagation();editItem('${r.id}')" title="${r.title}">${timeStr}${r.title}</div>`;
    }).join('');

    const moreHtml = overflow>0
      ? `<div class="full-cal-more" onclick="event.stopPropagation();calDayClick('${ds}')">+${overflow} more</div>`
      : '';

    html += `<div class="full-cal-cell${isToday?' today':''}${isWeekend?' weekend':''}" onclick="calDayClick('${ds}')">
      <div class="full-cal-day-num">${d}</div>
      ${eventsHtml}${moreHtml}
    </div>`;
  }

  // Trailing days from next month
  const totalCells = firstDay + daysInMonth;
  const trailing   = totalCells % 7 === 0 ? 0 : 7 - (totalCells % 7);
  for(let d=1; d<=trailing; d++){
    html += `<div class="full-cal-cell other-month">
      <div class="full-cal-day-num">${d}</div>
    </div>`;
  }

  grid.innerHTML = html;
}

function calDayClick(ds){
  // Open add reminder modal pre-filled with that date
  openModal('reminder');
  setTimeout(()=>{
    const dateEl = document.getElementById('f-due-date');
    if(dateEl) dateEl.value = ds;
  }, 50);
}

/* ============================================================
   MARKDOWN TOOLBAR HELPERS
   ============================================================ */
function mdWrap(before, after){
  const ta = document.getElementById('notes-editor-body');
  if(!ta) return;
  const s = ta.selectionStart, e = ta.selectionEnd;
  const sel = ta.value.slice(s,e);
  if(sel){
    // Text selected: wrap it and keep it selected
    const rep = before+sel+after;
    ta.value = ta.value.slice(0,s)+rep+ta.value.slice(e);
    ta.selectionStart = s+before.length;
    ta.selectionEnd   = s+before.length+sel.length;
  } else {
    // No selection: insert markers and place cursor between them
    ta.value = ta.value.slice(0,s)+before+after+ta.value.slice(s);
    ta.selectionStart = ta.selectionEnd = s+before.length;
  }
  ta.focus();
  onNoteEditorInput();
  // Auto switch to preview after a short delay so user sees result
  clearTimeout(ta._previewTimer);
  ta._previewTimer = setTimeout(()=>{
    if(document.getElementById('btn-preview-mode')) setNoteViewMode('preview');
  }, 1200);
}

function mdLinePrefix(prefix){
  const ta = document.getElementById('notes-editor-body');
  if(!ta) return;
  const s = ta.selectionStart;
  const lineStart = ta.value.lastIndexOf('\n',s-1)+1;
  const cur = ta.value.slice(lineStart, s);
  // toggle: if already has prefix remove it, else add
  if(cur.startsWith(prefix)){
    ta.value = ta.value.slice(0,lineStart)+ta.value.slice(lineStart+prefix.length);
    ta.selectionStart = ta.selectionEnd = s - prefix.length;
  } else {
    ta.value = ta.value.slice(0,lineStart)+prefix+ta.value.slice(lineStart);
    ta.selectionStart = ta.selectionEnd = s + prefix.length;
  }
  ta.focus();
  onNoteEditorInput();
  clearTimeout(ta._previewTimer);
  ta._previewTimer = setTimeout(()=>{
    if(document.getElementById('btn-preview-mode')) setNoteViewMode('preview');
  }, 1200);
}

function mdInsert(text){
  const ta = document.getElementById('notes-editor-body');
  if(!ta) return;
  const s = ta.selectionStart;
  ta.value = ta.value.slice(0,s)+text+ta.value.slice(s);
  ta.selectionStart = ta.selectionEnd = s+text.length;
  ta.focus();
  onNoteEditorInput();
}

/* ============================================================
   NOTE TEMPLATES
   ============================================================ */
const TEMPLATES = {
  daily: {
    title: '📅 Daily Log — '+localToday(),
    body: `## Morning
- 

## Tasks
- [ ] 
- [ ] 

## Notes

## End of Day
- `
  },
  meeting: {
    title: '👥 Meeting Notes',
    body: `## Attendees
- 

## Agenda
- 

## Discussion

## Action Items
- [ ] 

## Next Meeting
`
  },
  trading: {
    title: '📈 Trading Plan — '+localToday(),
    body: `## Market Overview

## Setup
- Entry: 
- Stop Loss: 
- Target: 
- R:R: 

## Thesis

## Result
- P&L: 
- Lesson: `
  },
  todo: {
    title: '✅ To-Do List',
    body: `## High Priority
- [ ] 

## Medium Priority
- [ ] 

## Low Priority
- [ ] `
  }
};

function toggleTemplateDropdown(e){
  e.stopPropagation();
  document.getElementById('tmpl-dropdown').classList.toggle('open');
}

function applyTemplate(key){
  const t = TEMPLATES[key];
  if(!t) return;
  const titleEl = document.getElementById('notes-editor-title');
  const bodyEl  = document.getElementById('notes-editor-body');
  if(titleEl && !titleEl.value) titleEl.value = t.title;
  if(bodyEl){
    bodyEl.value = t.body;
    onNoteEditorInput();
  }
  document.getElementById('tmpl-dropdown').classList.remove('open');
  setNoteViewMode('edit');
  toast('Template applied ✓','success');
}

// Close template dropdown when clicking outside
document.addEventListener('click',()=>{
  const dd = document.getElementById('tmpl-dropdown');
  if(dd) dd.classList.remove('open');
});

/* -- MARKDOWN RENDERER ---------------------------------- */
function renderMarkdown(text){
  if(!text) return '<p style="color:var(--muted);font-style:italic">Nothing to preview yet…</p>';

  // ── PRE-PASS: extract image syntax before escaping (base64 data URLs must not be escaped) ──
  // Also resolves %%IMGDATA:token%% here so we can attach the token to the delete button
  const imgPlaceholders = [];
  text = text.replace(/!\[([^\]]*)\]\((%%IMGDATA:(img_\d+)%%|(?:data:image\/[^)]+)|(?:https?:\/\/[^)]+))\)/g, (match, alt, src, token) => {
    const safeAlt = alt.replace(/"/g,'&quot;');
    const key = `%%IMG_${imgPlaceholders.length}%%`;
    const resolvedSrc = token && window._imgDataStore && window._imgDataStore[token]
      ? window._imgDataStore[token] : src;
    const tokenAttr = token ? ` data-token="${token}"` : '';
    imgPlaceholders.push(
      `<div class="md-img-wrap"${tokenAttr}>` +
      `<img class="md-img" src="${resolvedSrc}" alt="${safeAlt}" loading="lazy" onclick="mdImgZoom(this)">` +
      `<button class="md-img-del-btn" onclick="mdImgDelete(this)" title="Remove image">🗑</button>` +
      `</div>`
    );
    return key;
  });

  // ── PRE-PASS: extract markdown table blocks before escaping ──
  // Replace table blocks with unique placeholders so they survive the escape pass
  const tablePlaceholders = [];
  text = text.replace(/^(\|.+\|\s*\n)((\|[-:| ]+\|\s*\n))((\|.+\|\s*\n?)*)/gm, (match) => {
    const rows = match.trim().split('\n').filter(r=>r.trim());
    if(rows.length < 2) return match;
    // Detect separator row (row of dashes)
    const sepIdx = rows.findIndex(r=>/^\|[\s\-:|]+\|/.test(r));
    if(sepIdx < 0) return match;
    const headerRows = rows.slice(0, sepIdx);
    const bodyRows   = rows.slice(sepIdx + 1);
    const parseRow = r => r.replace(/^\||\|$/g,'').split('|').map(c=>c.trim());
    let tableHtml = '<div class="md-table-wrap">';
    tableHtml += '<button class="md-table-copy-btn" onclick="mdCopyTable(this)" title="Copy as text">📋 Copy</button>';
    tableHtml += '<table><thead>';
    headerRows.forEach(r=>{
      tableHtml += '<tr>'+parseRow(r).map(c=>`<th>${c}</th>`).join('')+'</tr>';
    });
    tableHtml += '</thead><tbody>';
    bodyRows.forEach(r=>{
      if(!r.trim()) return;
      tableHtml += '<tr>'+parseRow(r).map(c=>`<td>${c}</td>`).join('')+'</tr>';
    });
    tableHtml += '</tbody></table></div>';
    const key = `%%TABLE_${tablePlaceholders.length}%%`;
    tablePlaceholders.push(tableHtml);
    return key;
  });

  let html = text
    // Escape HTML (tables are already extracted)
    .replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;')
    // Headings
    .replace(/^### (.+)$/gm,'<h3>$1</h3>')
    .replace(/^## (.+)$/gm,'<h2>$1</h2>')
    .replace(/^# (.+)$/gm,'<h1>$1</h1>')
    // Bold + italic
    .replace(/\*\*\*(.+?)\*\*\*/g,'<strong><em>$1</em></strong>')
    .replace(/\*\*(.+?)\*\*/g,'<strong>$1</strong>')
    .replace(/\*(.+?)\*/g,'<em>$1</em>')
    // Inline code
    .replace(/`(.+?)`/g,'<code>$1</code>')
    // Blockquote
    .replace(/^&gt; (.+)$/gm,'<blockquote>$1</blockquote>')
    // HR
    .replace(/^---+$/gm,'<hr>')
    // Bullet lists — group consecutive lines starting with - or *
    .replace(/^[\-\*] (.+)$/gm,'<li>$1</li>')
    // Ordered lists
    .replace(/^\d+\. (.+)$/gm,'<li>$1</li>')
    // Color tags: (green), (red), (blue) inline
    .replace(/\(green\)(.+?)\(\/green\)/g,'<span class="md-tag-green">$1</span>')
    .replace(/\(red\)(.+?)\(\/red\)/g,'<span class="md-tag-red">$1</span>')
    .replace(/\(blue\)(.+?)\(\/blue\)/g,'<span class="md-tag-blue">$1</span>');

  // Wrap consecutive <li> in <ul>
  html = html.replace(/(<li>.*<\/li>\n?)+/g, m=>'<ul>'+m+'</ul>');

  // Paragraphs — wrap non-block lines
  const lines = html.split('\n');
  const blocks = ['<h1','<h2','<h3','<ul','<ol','<li','<hr','<blockquote','%%TABLE_','%%IMG_'];
  const result = [];
  let buf = [];
  for(const line of lines){
    const isBlock = blocks.some(b=>line.trimStart().startsWith(b));
    if(isBlock){
      if(buf.length){ result.push('<p>'+buf.join('<br>')+'</p>'); buf=[]; }
      result.push(line);
    } else if(line.trim()===''){
      if(buf.length){ result.push('<p>'+buf.join('<br>')+'</p>'); buf=[]; }
    } else {
      buf.push(line);
    }
  }
  if(buf.length) result.push('<p>'+buf.join('<br>')+'</p>');

  // Restore table placeholders
  let final = result.join('\n');
  tablePlaceholders.forEach((tbl, i) => {
    final = final.replace(`%%TABLE_${i}%%`, tbl);
  });
  // Restore image placeholders
  imgPlaceholders.forEach((img, i) => {
    final = final.replace(`%%IMG_${i}%%`, img);
  });
  return final;
}

function mdCopyTable(btn){
  const wrap = btn.closest('.md-table-wrap');
  const table = wrap.querySelector('table');
  if(!table){ return; }
  const rows = [...table.querySelectorAll('tr')];
  const tsv = rows.map(r=>[...r.querySelectorAll('th,td')].map(c=>c.innerText.trim()).join('\t')).join('\n');
  navigator.clipboard.writeText(tsv).then(()=>{
    btn.textContent = '✓ Copied!';
    btn.classList.add('copied');
    setTimeout(()=>{ btn.textContent = '📋 Copy'; btn.classList.remove('copied'); }, 2000);
  }).catch(()=>{
    // Fallback for older browsers
    const ta = document.createElement('textarea');
    ta.value = tsv; ta.style.position='fixed'; ta.style.opacity='0';
    document.body.appendChild(ta); ta.select();
    document.execCommand('copy');
    document.body.removeChild(ta);
    btn.textContent = '✓ Copied!'; btn.classList.add('copied');
    setTimeout(()=>{ btn.textContent = '📋 Copy'; btn.classList.remove('copied'); }, 2000);
  });
}

function setNoteViewMode(mode){
  const textarea = document.getElementById('notes-editor-body');
  const preview  = document.getElementById('notes-md-preview');
  const btnEdit  = document.getElementById('btn-edit-mode');
  const btnPrev  = document.getElementById('btn-preview-mode');
  if(!textarea||!preview) return;
  if(mode==='preview'){
    preview.innerHTML = renderMarkdown(textarea.value);
    textarea.classList.add('hidden');
    preview.classList.add('active');
    btnEdit.classList.remove('active');
    btnPrev.classList.add('active');
    if(!preview._copyListenerAttached){
      preview._copyListenerAttached = true;
      preview.addEventListener('copy', mdPreviewCopyHandler);
    }
  } else {
    textarea.classList.remove('hidden');
    preview.classList.remove('active');
    btnEdit.classList.add('active');
    btnPrev.classList.remove('active');
  }
  localStorage.setItem('note_view_mode', mode);
}

function mdPreviewCopyHandler(e){
  const sel = window.getSelection();
  if(!sel || sel.isCollapsed) return;
  const range = sel.getRangeAt(0);
  const frag  = range.cloneContents();
  const hasTbl = frag.querySelector('table,tr,td,th');
  if(!hasTbl) return;
  e.preventDefault();
  function tblToText(tbl){
    const rows = [...tbl.querySelectorAll('tr')];
    return rows.map(r=>[...r.querySelectorAll('th,td')].map(c=>c.textContent.trim()).join('\t')).join('\n');
  }
  function fragToText(node){
    if(node.nodeType===Node.TEXT_NODE) return node.textContent;
    const tag=(node.tagName||'').toLowerCase();
    if(tag==='table') return tblToText(node);
    if(tag==='tr') return [...node.querySelectorAll('th,td')].map(c=>c.textContent.trim()).join('\t');
    if(tag==='td'||tag==='th') return node.textContent.trim();
    if(node.querySelector&&node.querySelector('table')) return tblToText(node.querySelector('table'));
    return [...node.childNodes].map(fragToText).join('');
  }
  const parts=[];
  frag.childNodes.forEach(n=>{
    const t=fragToText(n);
    if(t.trim()) parts.push(t.trim());
  });
  e.clipboardData.setData('text/plain', parts.join('\n'));
}



/* -- FAB (Floating Action Button) --------------- */
/* ── DAYBOOK ──────────────────────────────────────── */
let _dbFilter = 'all';
let _dbEditId = null;
let _dbUnlocked = false;   // session unlock flag
let _dbPinEntry = '';      // digits typed so far on lock screen
const DB_TAGS = ['trade','personal','idea','health','work','family'];

/* ── DAYBOOK PIN ── */
function dbGetPin(){ return localStorage.getItem('db_pin')||''; }

/* Show/hide the "Current PIN" field based on whether a PIN is set.
   Called whenever the Settings panel opens. */
function dbRefreshPinSettingsUI(){
  const hasPin = !!dbGetPin();
  const row = document.getElementById('cfg-db-pin-current-row');
  const label = document.getElementById('cfg-db-pin-label');
  const newPin = document.getElementById('cfg-db-pin');
  const newPin2 = document.getElementById('cfg-db-pin2');
  const curPin = document.getElementById('cfg-db-pin-current');
  if(row) row.style.display = hasPin ? '' : 'none';
  if(label) label.textContent = hasPin ? 'New PIN (leave blank to keep current)' : 'New PIN (4 digits)';
  // Clear any leftover values so the form starts clean every time Settings opens
  if(newPin) newPin.value='';
  if(newPin2) newPin2.value='';
  if(curPin) curPin.value='';
  const msg = document.getElementById('db-pin-settings-msg');
  if(msg) msg.textContent='';
}

function dbSavePin(){
  const p1 = document.getElementById('cfg-db-pin').value.trim();
  const p2 = document.getElementById('cfg-db-pin2').value.trim();
  const msg = document.getElementById('db-pin-settings-msg');
  const existingPin = dbGetPin();

  // If a PIN already exists, require the current PIN before allowing any change
  if(existingPin){
    const cur = document.getElementById('cfg-db-pin-current').value.trim();
    if(!cur){
      msg.style.color='var(--red)';
      msg.textContent='Enter your current PIN to change it.';
      return;
    }
    if(cur !== existingPin){
      msg.style.color='var(--red)';
      msg.textContent='Current PIN is incorrect.';
      return;
    }
  }

  if(!p1){ msg.style.color='var(--red)'; msg.textContent='Enter a new PIN.'; return; }
  if(!/^\d{4}$/.test(p1)){ msg.style.color='var(--red)'; msg.textContent='PIN must be exactly 4 digits.'; return; }
  if(p1!==p2){ msg.style.color='var(--red)'; msg.textContent='PINs do not match.'; return; }
  localStorage.setItem('db_pin', p1);
  _dbUnlocked = false; // force re-lock on next visit
  _invUnlocked = false; // force re-lock investments too
  _impUnlocked = false; // force re-lock important dates too
  document.getElementById('cfg-db-pin').value='';
  document.getElementById('cfg-db-pin2').value='';
  const curEl = document.getElementById('cfg-db-pin-current');
  if(curEl) curEl.value='';
  msg.style.color='var(--green)';
  msg.textContent='✓ PIN saved! Daybook, Investments & Important Dates will be locked next time you open them.';
  setTimeout(()=>{ msg.textContent=''; },3000);
  // Refresh UI: if we just set a PIN for the first time, show the Current PIN field next time
  dbRefreshPinSettingsUI();
}

function dbClearPin(){
  const msg = document.getElementById('db-pin-settings-msg');
  const existingPin = dbGetPin();

  // If a PIN is set, require the current PIN before removing
  if(existingPin){
    const cur = document.getElementById('cfg-db-pin-current').value.trim();
    if(!cur){
      msg.style.color='var(--red)';
      msg.textContent='Enter your current PIN to remove the lock.';
      return;
    }
    if(cur !== existingPin){
      msg.style.color='var(--red)';
      msg.textContent='Current PIN is incorrect.';
      return;
    }
  }

  localStorage.removeItem('db_pin');
  _dbUnlocked = true;
  _invUnlocked = true;
  _impUnlocked = true;
  document.getElementById('cfg-db-pin').value='';
  document.getElementById('cfg-db-pin2').value='';
  const curEl = document.getElementById('cfg-db-pin-current');
  if(curEl) curEl.value='';
  msg.style.color='var(--green)';
  msg.textContent='✓ PIN removed. Daybook, Investments & Important Dates are now unlocked.';
  setTimeout(()=>{ msg.textContent=''; },3000);
  // Refresh UI so the Current PIN field hides now that there's no PIN
  dbRefreshPinSettingsUI();
}

function dbShowLock(){
  _dbPinEntry = '';
  dbUpdateDots();
  document.getElementById('db-pin-error').textContent='';
  document.getElementById('db-lock-sub').textContent='Enter your PIN to open your private diary';
  document.getElementById('db-lock-overlay').style.display='flex';
}

function dbHideLock(){
  document.getElementById('db-lock-overlay').style.display='none';
}

function dbUpdateDots(){
  for(let i=0;i<4;i++){
    const dot = document.getElementById('db-dot-'+i);
    if(dot) dot.classList.toggle('filled', i < _dbPinEntry.length);
  }
}

function dbPinPress(digit){
  if(_dbPinEntry.length >= 4) return;
  _dbPinEntry += digit;
  dbUpdateDots();
  document.getElementById('db-pin-error').textContent='';
  if(_dbPinEntry.length === 4) setTimeout(dbCheckPin, 120);
}

function dbPinBack(){
  _dbPinEntry = _dbPinEntry.slice(0,-1);
  dbUpdateDots();
}

function dbPinClear(){
  _dbPinEntry = '';
  dbUpdateDots();
  document.getElementById('db-pin-error').textContent='';
}

function dbCheckPin(){
  const stored = dbGetPin();
  if(_dbPinEntry === stored){
    _dbUnlocked = true;
    dbHideLock();
    dbRender();
    dbUpdateCounts();
  } else {
    document.getElementById('db-pin-error').textContent='Wrong PIN. Try again.';
    // shake animation
    const box = document.querySelector('.db-lock-box');
    if(box){ box.style.animation='none'; void box.offsetWidth; box.style.animation='db-shake .35s ease'; }
    setTimeout(()=>{ _dbPinEntry=''; dbUpdateDots(); },600);
  }
}

// keyboard support on lock screen
document.addEventListener('keydown', e=>{
  const overlay = document.getElementById('db-lock-overlay');
  if(!overlay || overlay.style.display==='none') return;
  if(/^[0-9]$/.test(e.key)) dbPinPress(e.key);
  else if(e.key==='Backspace') dbPinBack();
  else if(e.key==='Escape') dbPinClear();
});

/* ── INVESTMENTS PIN LOCK (uses same Daybook PIN) ── */
let _invUnlocked = false;
let _invPinEntry = '';

function invShowLockScreen(){
  _invPinEntry = '';
  invUpdateInvDots();
  document.getElementById('inv-pin-error').textContent='';
  document.getElementById('inv-lock-sub').textContent='Enter your PIN to view your portfolio';
  document.getElementById('inv-lock-overlay').style.display='flex';
}

function invHideLockScreen(){
  document.getElementById('inv-lock-overlay').style.display='none';
}

function invUpdateInvDots(){
  for(let i=0;i<4;i++){
    const dot = document.getElementById('inv-dot-'+i);
    if(dot) dot.classList.toggle('filled', i < _invPinEntry.length);
  }
}

function invPinPress(digit){
  if(_invPinEntry.length >= 4) return;
  _invPinEntry += digit;
  invUpdateInvDots();
  document.getElementById('inv-pin-error').textContent='';
  if(_invPinEntry.length === 4) setTimeout(invCheckInvPin, 120);
}

function invPinBack(){
  _invPinEntry = _invPinEntry.slice(0,-1);
  invUpdateInvDots();
}

function invPinClear(){
  _invPinEntry = '';
  invUpdateInvDots();
  document.getElementById('inv-pin-error').textContent='';
}

function invCheckInvPin(){
  const stored = dbGetPin(); // same PIN as Daybook
  if(_invPinEntry === stored){
    _invUnlocked = true;
    invHideLockScreen();
    invRender();
  } else {
    document.getElementById('inv-pin-error').textContent='Wrong PIN. Try again.';
    const box = document.querySelector('.inv-lock-box');
    if(box){ box.style.animation='none'; void box.offsetWidth; box.style.animation='inv-shake .35s ease'; }
    setTimeout(()=>{ _invPinEntry=''; invUpdateInvDots(); },600);
  }
}

// keyboard support on investment lock screen
document.addEventListener('keydown', e=>{
  const overlay = document.getElementById('inv-lock-overlay');
  if(!overlay || overlay.style.display==='none') return;
  if(/^[0-9]$/.test(e.key)) invPinPress(e.key);
  else if(e.key==='Backspace') invPinBack();
  else if(e.key==='Escape') invPinClear();
});

/* ── IMPORTANT DATES PIN LOCK (uses same Daybook PIN) ── */
let _impUnlocked = false;
let _impPinEntry = '';

function impShowLockScreen(){
  _impPinEntry = '';
  impUpdatePinDots();
  document.getElementById('imp-pin-error').textContent='';
  document.getElementById('imp-lock-sub').textContent='Enter your PIN to view your important dates';
  document.getElementById('imp-lock-overlay').style.display='flex';
}

function impHideLockScreen(){
  document.getElementById('imp-lock-overlay').style.display='none';
}

function impUpdatePinDots(){
  for(let i=0;i<4;i++){
    const dot = document.getElementById('imp-dot-'+i);
    if(dot) dot.classList.toggle('filled', i < _impPinEntry.length);
  }
}

function impPinPress(digit){
  if(_impPinEntry.length >= 4) return;
  _impPinEntry += digit;
  impUpdatePinDots();
  document.getElementById('imp-pin-error').textContent='';
  if(_impPinEntry.length === 4) setTimeout(impCheckPin, 120);
}

function impPinBack(){
  _impPinEntry = _impPinEntry.slice(0,-1);
  impUpdatePinDots();
}

function impPinClear(){
  _impPinEntry = '';
  impUpdatePinDots();
  document.getElementById('imp-pin-error').textContent='';
}

function impCheckPin(){
  const stored = dbGetPin(); // same PIN as Daybook & Investments
  if(_impPinEntry === stored){
    _impUnlocked = true;
    impHideLockScreen();
    impRenderPage();
  } else {
    document.getElementById('imp-pin-error').textContent='Wrong PIN. Try again.';
    const box = document.querySelector('.imp-lock-box');
    if(box){ box.style.animation='none'; void box.offsetWidth; box.style.animation='imp-shake .35s ease'; }
    setTimeout(()=>{ _impPinEntry=''; impUpdatePinDots(); },600);
  }
}

// keyboard support on important dates lock screen
document.addEventListener('keydown', e=>{
  const overlay = document.getElementById('imp-lock-overlay');
  if(!overlay || overlay.style.display==='none') return;
  if(/^[0-9]$/.test(e.key)) impPinPress(e.key);
  else if(e.key==='Backspace') impPinBack();
  else if(e.key==='Escape') impPinClear();
});

function dbGetEntries(){ return DATA.daybook || []; }

function dbUpdateCounts(){
  const entries = dbGetEntries();
  const countEl = document.getElementById('nav-daybook-count');
  if(countEl) countEl.textContent = entries.length;
  document.getElementById('db-cnt-all') && (document.getElementById('db-cnt-all').textContent = entries.length);
  DB_TAGS.forEach(t=>{
    const el = document.getElementById('db-cnt-'+t);
    if(el) el.textContent = entries.filter(e=>(e.tags||[]).includes(t)).length;
  });
  // mobile filter row
  const mf = document.getElementById('db-filters-mobile');
  if(mf){
    mf.innerHTML = ['all',...DB_TAGS].map(t=>{
      const cnt = t==='all' ? entries.length : entries.filter(e=>(e.tags||[]).includes(t)).length;
      const isActive = _dbFilter===t;
      return `<button class="db-filter-btn${isActive?' active':''}" style="white-space:nowrap;border-radius:20px;padding:5px 12px;margin:0" onclick="dbSetFilter('${t}',this)">${t==='all'?'All':t} <span style="opacity:.7">${cnt}</span></button>`;
    }).join('');
  }
}

function dbSetFilter(tag, btn){
  _dbFilter = tag;
  document.querySelectorAll('.db-filter-btn').forEach(b=>b.classList.remove('active'));
  if(btn) btn.classList.add('active');
  dbRender();
  dbUpdateCounts();
}

function dbRender(){
  const entries = dbGetEntries();
  const search  = (document.getElementById('db-search')||{}).value||'';
  const sq = search.trim().toLowerCase();

  let filtered = [...entries].reverse();
  if(_dbFilter !== 'all') filtered = filtered.filter(e=>(e.tags||[]).includes(_dbFilter));
  if(sq) filtered = filtered.filter(e=>(e.text||'').toLowerCase().includes(sq)||(e.tags||[]).join(' ').includes(sq));

  const wrap = document.getElementById('db-entries-list');
  const empty = document.getElementById('db-empty-state');
  if(!wrap) return;

  if(!filtered.length){
    wrap.innerHTML='';
    if(empty) empty.style.display='flex';
    return;
  }
  if(empty) empty.style.display='none';

  // Group by date
  const groups = {};
  filtered.forEach(e=>{
    const d = (e.date||'').slice(0,10);
    if(!groups[d]) groups[d]=[];
    groups[d].push(e);
  });

  const MONTHS = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
  const DAYS   = ['Sunday','Monday','Tuesday','Wednesday','Thursday','Friday','Saturday'];

  wrap.innerHTML = Object.keys(groups).sort((a,b)=>b.localeCompare(a)).map(dateStr=>{
    const items = groups[dateStr];
    const d = new Date(dateStr+'T00:00:00');
    const dayNum  = d.getDate();
    const monStr  = MONTHS[d.getMonth()];
    const yearStr = d.getFullYear();
    const dayName = DAYS[d.getDay()];

    const entriesHtml = items.map(e=>{
      const t = e.time||'';
      const [hh,mm] = t.split(':');
      const h = parseInt(hh)||0;
      const ampm = h>=12?'PM':'AM';
      const h12  = h===0?12:h>12?h-12:h;
      const timeStr = `${String(h12).padStart(2,'0')}:${mm||'00'}`;
      const tagsHtml = (e.tags||[]).map(tg=>`<span class="db-tag db-tag-${tg}">${tg}</span>`).join('');
      const textEsc = (e.text||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
      return `
      <div class="db-entry" onclick="dbOpenEdit('${e.id}')">
        <div class="db-entry-time-col">
          <div class="db-entry-time">${timeStr}</div>
          <div class="db-entry-ampm">${ampm}</div>
        </div>
        <div class="db-entry-body">
          <div class="db-entry-text">${textEsc}</div>
          ${tagsHtml?`<div class="db-entry-tags">${tagsHtml}</div>`:''}
        </div>
        <div class="db-entry-actions">
          <button class="db-ea-btn" onclick="event.stopPropagation();dbOpenEdit('${e.id}')" title="Edit">✏️</button>
          <button class="db-ea-btn" onclick="event.stopPropagation();dbDeleteEntry('${e.id}')" title="Delete">🗑</button>
        </div>
      </div>`;
    }).join('');

    return `
    <div class="db-date-group">
      <div class="db-date-header">
        <span class="dh-day">${String(dayNum).padStart(2,'0')}</span>
        <span class="dh-mon">${monStr}</span>
        <span class="dh-rest">${yearStr}, ${dayName}</span>
        <span class="dh-ecount">${items.length} entr${items.length===1?'y':'ies'}</span>
      </div>
      ${entriesHtml}
    </div>`;
  }).join('');
}

function dbOpenCompose(){
  _dbEditId = null;
  // Reset fields
  document.getElementById('db-compose-text').value='';
  document.querySelectorAll('.db-ctag').forEach(b=>{b.className='db-ctag';});
  // Set datetime label
  const now = new Date();
  const MONTHS = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
  const h = now.getHours(), m = now.getMinutes();
  const ampm = h>=12?'PM':'AM', h12 = h===0?12:h>12?h-12:h;
  document.getElementById('db-compose-dt').textContent =
    `📅 ${String(now.getDate()).padStart(2,'0')} ${MONTHS[now.getMonth()]} ${now.getFullYear()}  🕐 ${String(h12).padStart(2,'0')}:${String(m).padStart(2,'0')} ${ampm}`;
  document.getElementById('db-compose').classList.add('open');
  document.getElementById('db-compose-text').focus();
}

function dbOpenEdit(id){
  const entry = dbGetEntries().find(e=>e.id===id);
  if(!entry) return;
  _dbEditId = id;
  document.getElementById('db-compose-text').value = entry.text||'';
  document.querySelectorAll('.db-ctag').forEach(b=>{
    const t = b.dataset.tag;
    b.className = 'db-ctag' + ((entry.tags||[]).includes(t) ? ` sel sel-${t}` : '');
  });
  const MONTHS = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
  const d = new Date((entry.date||'')+'T'+(entry.time||'00:00')+':00');
  const h=d.getHours(), m=d.getMinutes(), ampm=h>=12?'PM':'AM', h12=h===0?12:h>12?h-12:h;
  document.getElementById('db-compose-dt').textContent =
    `📅 ${String(d.getDate()).padStart(2,'0')} ${MONTHS[d.getMonth()]} ${d.getFullYear()}  🕐 ${String(h12).padStart(2,'0')}:${String(m).padStart(2,'0')} ${ampm}`;
  document.getElementById('db-compose').classList.add('open');
  document.getElementById('db-compose-text').focus();
}

function dbCloseCompose(){
  document.getElementById('db-compose').classList.remove('open');
  _dbEditId = null;
}

function dbToggleTag(btn){
  const tag = btn.dataset.tag;
  const isOn = btn.classList.contains('sel');
  if(isOn){
    btn.className='db-ctag';
  } else {
    btn.className=`db-ctag sel sel-${tag}`;
  }
}

async function dbSaveEntry(){
  const text = document.getElementById('db-compose-text').value.trim();
  if(!text){ toast('Write something first','error'); return; }
  const tags = [...document.querySelectorAll('.db-ctag.sel')].map(b=>b.dataset.tag);
  const now  = new Date();
  const pad  = n=>String(n).padStart(2,'0');
  const dateStr = `${now.getFullYear()}-${pad(now.getMonth()+1)}-${pad(now.getDate())}`;
  const timeStr = `${pad(now.getHours())}:${pad(now.getMinutes())}`;

  if(!DATA.daybook) DATA.daybook=[];

  if(_dbEditId){
    const idx = DATA.daybook.findIndex(e=>e.id===_dbEditId);
    if(idx>=0){
      DATA.daybook[idx].text = text;
      DATA.daybook[idx].tags = tags;
      DATA.daybook[idx].edited = now.toISOString();
    }
    toast('Entry updated ✓','success');
  } else {
    DATA.daybook.push({
      id: 'db_'+Date.now(),
      date: dateStr,
      time: timeStr,
      text,
      tags,
      created: now.toISOString()
    });
    toast('Entry saved ✓','success');
  }

  dbCloseCompose();
  dbRender();
  dbUpdateCounts();
  await saveToFirebase();
}

async function dbDeleteEntry(id){
  if(!confirm('Delete this entry?')) return;
  DATA.daybook = (DATA.daybook||[]).filter(e=>e.id!==id);
  dbRender();
  dbUpdateCounts();
  toast('Entry deleted','success');
  await saveToFirebase();
}

/* -- INIT ---------------------------------------- */
/* ── SMART PASTE: convert HTML tables → markdown on paste into notes editor ── */
function initNotesPasteHandler(){
  // Use event delegation on document so it works even if textarea is re-created
  document.addEventListener('paste', function(e){
    const ta = document.getElementById('notes-editor-body');
    if(!ta || document.activeElement !== ta) return;

    // ── IMAGE PASTE ──────────────────────────────────────────
    // Only treat as image if there is NO plain text on the clipboard.
    // When copying text from terminals/browsers the clipboard often also
    // carries a rendered image — we must prefer text in that case.
    const items = e.clipboardData && e.clipboardData.items;
    const hasText = e.clipboardData && (
      e.clipboardData.getData('text/plain').trim().length > 0 ||
      e.clipboardData.getData('text/html').trim().length > 0
    );
    if(items && !hasText){
      for(const item of items){
        if(item.type.startsWith('image/')){
          e.preventDefault();
          const blob = item.getAsFile();
          if(!blob) return;
          const kb = (blob.size/1024).toFixed(0);
          if(blob.size > 700*1024){
            toast(`⚠️ Image is ${kb} KB — Firestore docs cap at ~1 MB. Consider smaller images.`, 'warn');
          }
          const reader = new FileReader();
          reader.onload = function(ev){
            // Store base64 under a short token to keep the textarea readable
            const token = 'img_' + (++window._imgTokenCounter);
            window._imgDataStore[token] = ev.target.result;
            const mdImg = `\n![pasted image](%%IMGDATA:${token}%%)\n`;
            const start = ta.selectionStart;
            const end   = ta.selectionEnd;
            ta.value = ta.value.slice(0, start) + mdImg + ta.value.slice(end);
            ta.selectionStart = ta.selectionEnd = start + mdImg.length;
            ta.dispatchEvent(new Event('input'));
            // Auto-switch to preview so the image is visible immediately
            setNoteViewMode('preview');
            toast(`Image pasted (${kb} KB) ✓ — showing preview`, 'success');
          };
          reader.readAsDataURL(blob);
          return;
        }
      }
    }
    // ── END IMAGE PASTE ──────────────────────────────────────

    const html = e.clipboardData.getData('text/html');
    if(!html) return; // no HTML on clipboard, let default paste happen

    // Parse the HTML and look for a table
    const tmp = document.createElement('div');
    tmp.innerHTML = html;
    const table = tmp.querySelector('table');
    if(!table) return; // no table in clipboard HTML, let default paste happen

    e.preventDefault();

    // Convert HTML table → markdown table string
    function cellText(cell){
      // Collapse whitespace and trim
      return (cell.innerText || cell.textContent || '').replace(/\s+/g,' ').trim();
    }

    const rows = [...table.querySelectorAll('tr')];
    if(!rows.length) return;

    const mdRows = rows.map(r => {
      const cells = [...r.querySelectorAll('th,td')];
      return '| ' + cells.map(cellText).join(' | ') + ' |';
    });

    // Detect header row: if first row has <th> cells, treat as header
    const firstRowHasTh = rows[0].querySelector('th') !== null;
    let mdLines = [];
    if(firstRowHasTh){
      mdLines.push(mdRows[0]);
      // separator row
      const cols = rows[0].querySelectorAll('th,td').length;
      mdLines.push('| ' + Array(cols).fill('---').join(' | ') + ' |');
      mdLines.push(...mdRows.slice(1));
    } else {
      // No explicit header — use first row as header anyway
      mdLines.push(mdRows[0]);
      const cols = rows[0].querySelectorAll('th,td').length;
      mdLines.push('| ' + Array(cols).fill('---').join(' | ') + ' |');
      mdLines.push(...mdRows.slice(1));
    }

    const mdTable = '\n' + mdLines.join('\n') + '\n';

    // Insert at cursor position in textarea
    const start = ta.selectionStart;
    const end   = ta.selectionEnd;
    const before = ta.value.slice(0, start);
    const after  = ta.value.slice(end);
    ta.value = before + mdTable + after;
    ta.selectionStart = ta.selectionEnd = start + mdTable.length;
    ta.dispatchEvent(new Event('input')); // trigger autosave
    toast('Table pasted as Markdown ✓', 'success');
  });
}

/* ── SHOPPING ──────────────────────────────────────── */
const SHOP_ICONS = ['🏪','🏬','🛒','💊','🥬','📦','👕','🔧','🍞','🧴','🎮','🏥'];
let _activeShopId = null;

function shopGetData(){ if(!DATA.shopping) DATA.shopping=[]; return DATA.shopping; }

function shopUpdateCount(){
  const totalItems=shopGetData().reduce((s,sh)=>s+(sh.items||[]).length,0);
  const navCount=document.getElementById('nav-shopping-count');
  if(navCount) navCount.textContent=totalItems;
}

function shopRender(){
  shopRenderList();
  shopRenderItems();
  shopUpdateCount();
}

function shopRenderList(){
  const list=document.getElementById('shop-list');
  if(!list) return;
  const shops=shopGetData();

  if(!shops.length){
    list.innerHTML=`<div style="padding:20px 14px;text-align:center;color:var(--muted);font-size:12px">No shops yet</div>`;
    return;
  }

  // Auto-select first shop if none selected
  if(!_activeShopId || !shops.find(s=>s.id===_activeShopId)){
    _activeShopId=shops[0].id;
  }

  list.innerHTML=shops.map(sh=>{
    const items=sh.items||[];
    const pending=items.filter(i=>!i.done).length;
    const isActive=sh.id===_activeShopId;
    return `<div class="shop-folder-wrap${isActive?' active':''}">
      <div class="shop-folder${isActive?' active':''}" onclick="shopSelect('${sh.id}')">
        <span class="shop-folder-icon">${sh.icon||'🏪'}</span>
        <span class="shop-folder-name">${esc(sh.name)}</span>
        <span class="shop-folder-count">${pending>0?pending:items.length}</span>
        <div class="shop-folder-actions">
          <button class="shop-fa-btn" onclick="event.stopPropagation();shopOpenModal('${sh.id}')" title="Edit">✎</button>
          <button class="shop-fa-btn del" onclick="event.stopPropagation();shopDelShop('${sh.id}')" title="Delete">✕</button>
        </div>
      </div>
    </div>`;
  }).join('');
}

function shopRenderItems(){
  const right=document.getElementById('shop-right');
  const emptyState=document.getElementById('shop-empty-state');
  if(!right) return;

  const shops=shopGetData();
  const sh=shops.find(s=>s.id===_activeShopId);

  if(!sh){
    right.innerHTML=`<div class="shop-empty"><div class="shop-empty-icon">🛒</div><div class="shop-empty-text">Select a shop or add one to get started</div></div>`;
    return;
  }

  const items=sh.items||[];
  const pending=items.filter(i=>!i.done).length;
  const bought=items.filter(i=>i.done).length;

  const itemsHtml=items.length ? items.map(it=>{
    const d=it.added?(it.added.slice(5,10).replace('-','/')):'';
    return `<div class="shop-entry${it.done?' bought':''}">
      <div class="se-check${it.done?' done':''}" onclick="shopToggleItem('${sh.id}','${it.id}')">${it.done?'✓':''}</div>
      <div class="se-name">${esc(it.name)}</div>
      <div class="se-date">${d}</div>
      <div class="se-del" onclick="shopDelItem('${sh.id}','${it.id}')">✕</div>
    </div>`;
  }).join('') : `<div class="shop-empty"><div class="shop-empty-icon">📋</div><div class="shop-empty-text">No items yet — add one below</div></div>`;

  right.innerHTML=`
    <div class="shop-right-hdr">
      <div class="shop-right-icon">${sh.icon||'🏪'}</div>
      <div style="flex:1">
        <div class="shop-right-name">${esc(sh.name)}</div>
        <div class="shop-right-sub">${pending} pending · ${bought} bought</div>
      </div>
    </div>
    <div class="shop-items-wrap">${itemsHtml}</div>
    <div class="shop-add-bar">
      <input class="shop-add-input" id="shop-item-input" placeholder="Add item to ${esc(sh.name)}..." onkeydown="if(event.key==='Enter')shopAddItem()">
      <button class="shop-add-btn" onclick="shopAddItem()">Add</button>
    </div>`;

  setTimeout(()=>{const el=document.getElementById('shop-item-input');if(el)el.focus();},50);
}

function shopSelect(id){
  _activeShopId=id;
  shopRender();
}

function shopOpenModal(editId){
  const overlay=document.getElementById('shop-modal-overlay');
  document.getElementById('shop-edit-id').value=editId||'';
  document.getElementById('shop-modal-title').textContent=editId?'Edit Shop':'Add Shop';

  const grid=document.getElementById('shop-icon-grid');
  grid.innerHTML=SHOP_ICONS.map(ic=>`<div style="width:32px;height:32px;border-radius:6px;background:var(--s2);border:1px solid var(--border);display:flex;align-items:center;justify-content:center;font-size:14px;cursor:pointer" onclick="document.getElementById('shop-icon-input').value=this.textContent;document.querySelectorAll('#shop-icon-grid>div').forEach(d=>d.style.borderColor='var(--border)');this.style.borderColor='var(--accent)'">${ic}</div>`).join('');

  if(editId){
    const sh=shopGetData().find(s=>s.id===editId);
    if(sh){
      document.getElementById('shop-name-input').value=sh.name||'';
      document.getElementById('shop-icon-input').value=sh.icon||'🏪';
    }
  } else {
    document.getElementById('shop-name-input').value='';
    document.getElementById('shop-icon-input').value='🏪';
  }
  overlay.classList.add('open');
  document.getElementById('shop-name-input').focus();
}
function shopCloseModal(){document.getElementById('shop-modal-overlay').classList.remove('open');}

async function shopSave(){
  const name=document.getElementById('shop-name-input').value.trim();
  if(!name){toast('Enter a shop name','error');return;}
  const icon=document.getElementById('shop-icon-input').value.trim()||'🏪';
  const editId=document.getElementById('shop-edit-id').value;
  const shops=shopGetData();

  if(editId){
    const sh=shops.find(s=>s.id===editId);
    if(sh){sh.name=name;sh.icon=icon;}
    toast('Shop updated ✓','success');
  } else {
    const newId='shop_'+Date.now();
    shops.push({id:newId,name,icon,items:[]});
    _activeShopId=newId;
    toast('Shop added ✓','success');
  }
  shopCloseModal();
  shopRender();
  await saveToFirebase();
}

async function shopDelShop(id){
  if(!confirm('Delete this shop and all its items?')) return;
  DATA.shopping=shopGetData().filter(s=>s.id!==id);
  if(_activeShopId===id) _activeShopId=null;
  shopRender();
  toast('Shop deleted','success');
  await saveToFirebase();
}

async function shopAddItem(){
  const input=document.getElementById('shop-item-input');
  if(!input) return;
  const name=input.value.trim();
  if(!name){toast('Type an item name','error');return;}
  const sh=shopGetData().find(s=>s.id===_activeShopId);
  if(!sh) return;
  if(!sh.items) sh.items=[];
  const now=new Date();
  const pad=n=>String(n).padStart(2,'0');
  sh.items.unshift({id:'si_'+Date.now(),name,done:false,added:`${now.getFullYear()}-${pad(now.getMonth()+1)}-${pad(now.getDate())}`});
  input.value='';
  shopRender();
  await saveToFirebase();
}

async function shopToggleItem(shopId,itemId){
  const sh=shopGetData().find(s=>s.id===shopId);
  if(!sh) return;
  const it=(sh.items||[]).find(i=>i.id===itemId);
  if(!it) return;
  it.done=!it.done;
  shopRender();
  await saveToFirebase();
}

async function shopDelItem(shopId,itemId){
  const sh=shopGetData().find(s=>s.id===shopId);
  if(!sh) return;
  sh.items=(sh.items||[]).filter(i=>i.id!==itemId);
  shopRender();
  await saveToFirebase();
}

/* ═══════════════════════════════════════════════════════
   INVESTMENTS PORTFOLIO PAGE
   ═══════════════════════════════════════════════════════ */
let INVESTMENTS = [];
let _invEditId = null;
let _invAdding = false;

// Vibrant unique colors for each asset's allocation bar (matching Excel style)
const INV_BAR_COLORS = [
  '#1abc9c','#27ae60','#2ecc71','#e74c3c','#9b59b6',
  '#e67e22','#3498db','#8B4513','#f1c40f','#1abc9c',
  '#2980b9','#e91e63','#00bcd4','#ff5722','#4caf50',
  '#ff9800','#673ab7','#009688','#795548','#607d8b'
];

function invGetData(){ if(!DATA.investments) DATA.investments=[]; INVESTMENTS=DATA.investments; return INVESTMENTS; }

function invFormatINR(v){
  return new Intl.NumberFormat('en-IN',{style:'currency',currency:'INR',maximumFractionDigits:0}).format(v);
}

function invCalcPortfolio(value, total){
  return total > 0 ? ((value/total)*100).toFixed(2) : '0.00';
}

function invCalcGap(value, target, total){
  const current = parseFloat(invCalcPortfolio(value, total));
  return (current - target).toFixed(1);
}

function invGetStatus(gap){
  const g = parseFloat(gap);
  if(Math.abs(g) < 1) return {text:'On target', cls:'on-target', icon:'✓'};
  if(g > 0) return {text:Math.abs(g).toFixed(1)+'% over', cls:'over', icon:'▲'};
  return {text:Math.abs(g).toFixed(1)+'% under', cls:'under', icon:'▼'};
}

function updateInvestmentsCount(){
  const el=document.getElementById('nav-investments-count');
  if(el) el.textContent=(DATA.investments||[]).length;
}

function invRender(){
  const assets = invGetData();
  const container = document.getElementById('inv-table-container');
  if(!container) return;

  const total = assets.reduce((s,a)=>s+a.value,0);
  const totalTarget = assets.reduce((s,a)=>s+a.target,0);

  // Summary cards
  document.getElementById('inv-sum-total').textContent = invFormatINR(total);
  document.getElementById('inv-sum-total-sub').textContent = assets.length + ' asset' + (assets.length!==1?'s':'');
  document.getElementById('inv-sum-target').textContent = totalTarget + '%';
  document.getElementById('inv-sum-target-sub').textContent = totalTarget===100 ? '✓ Perfectly balanced' : (totalTarget<100?'Under-allocated by '+(100-totalTarget)+'%':'Over-allocated by '+(totalTarget-100)+'%');

  if(assets.length){
    const top = [...assets].sort((a,b)=>b.value-a.value)[0];
    document.getElementById('inv-sum-top').textContent = top.name;
    document.getElementById('inv-sum-top-sub').textContent = invFormatINR(top.value) + ' · ' + invCalcPortfolio(top.value, total) + '%';
  } else {
    document.getElementById('inv-sum-top').textContent = '—';
    document.getElementById('inv-sum-top-sub').textContent = '—';
  }

  updateInvestmentsCount();

  if(!assets.length && !_invAdding){
    container.innerHTML=`<div class="inv-empty"><div class="inv-empty-icon">📊</div><div class="inv-empty-text">No investment assets yet — click <b>+ Add Asset</b> to begin tracking</div></div>`;
    return;
  }

  let rows = '';
  let cards = '';
  assets.forEach((a,i)=>{
    const pct = invCalcPortfolio(a.value, total);
    const gap = invCalcGap(a.value, a.target, total);
    const status = invGetStatus(gap);
    const gapNum = parseFloat(gap);
    const gapCls = gapNum>0?'pos':gapNum<0?'neg':'zero';
    const barColor = INV_BAR_COLORS[i % INV_BAR_COLORS.length];
    const isEditing = _invEditId===a.id;

    if(isEditing){
      rows += `<tr>
        <td><input class="inv-edit-input" id="inv-e-name" value="${esc(a.name)}" placeholder="Asset name"></td>
        <td class="r"><input class="inv-edit-input num" id="inv-e-value" type="number" value="${a.value}" placeholder="0"></td>
        <td class="r"><span class="inv-pct">${pct}%</span></td>
        <td class="r"><input class="inv-edit-input num" id="inv-e-target" type="number" value="${a.target}" step="0.5" placeholder="0"></td>
        <td class="inv-col-alloc"><div class="inv-bar-wrap"><div class="inv-bar-fill" style="width:${Math.min(pct,100)}%;background:${barColor}"></div></div></td>
        <td class="inv-col-status"><span class="inv-status ${status.cls}">${status.icon} ${status.text}</span></td>
        <td class="r"><span class="inv-gap ${gapCls}">${gapNum>0?'+':''}${gap}%</span></td>
        <td class="r">
          <div style="display:flex;gap:4px;justify-content:flex-end">
            <button class="inv-abtn" onclick="invSaveEdit()" title="Save" style="color:#1a8a5a;font-size:16px">✓</button>
            <button class="inv-abtn" onclick="invCancelEdit()" title="Cancel" style="font-size:16px">✕</button>
          </div>
        </td>
      </tr>`;
      // Mobile edit card
      cards += `<div class="inv-medit">
        <div class="inv-medit-fields">
          <div><label>Asset Name</label><input class="inv-edit-input" id="inv-me-name" value="${esc(a.name)}" placeholder="Asset name"></div>
          <div><label>Value (INR)</label><input class="inv-edit-input" id="inv-me-value" type="number" value="${a.value}" placeholder="0"></div>
          <div><label>Target %</label><input class="inv-edit-input" id="inv-me-target" type="number" value="${a.target}" step="0.5" placeholder="0"></div>
        </div>
        <div class="inv-medit-btns">
          <button class="inv-medit-cancel" onclick="invCancelEdit()">Cancel</button>
          <button class="inv-medit-save" onclick="invSaveEdit(true)">Save</button>
        </div>
      </div>`;
    } else {
      rows += `<tr>
        <td><span class="inv-asset-name">${esc(a.name)}</span></td>
        <td class="r"><span class="inv-val">${invFormatINR(a.value)}</span></td>
        <td class="r"><span class="inv-pct">${pct}%</span></td>
        <td class="r"><span class="inv-pct" style="font-weight:700">${a.target}%</span></td>
        <td class="inv-col-alloc"><div class="inv-bar-wrap"><div class="inv-bar-fill" style="width:${Math.min(pct,100)}%;background:${barColor}"></div></div></td>
        <td class="inv-col-status"><span class="inv-status ${status.cls}">${status.icon} ${status.text}</span></td>
        <td class="r"><span class="inv-gap ${gapCls}">${gapNum>0?'+':''}${gap}%</span></td>
        <td class="r">
          <div class="inv-actions">
            <button class="inv-abtn" onclick="invStartEdit('${a.id}')" title="Edit">✎</button>
            <button class="inv-abtn del" onclick="invDelete('${a.id}')" title="Delete">✕</button>
          </div>
        </td>
      </tr>`;
      // Mobile card
      cards += `<div class="inv-mcard" style="border-left:4px solid ${barColor}">
        <div class="inv-mcard-top">
          <span class="inv-mcard-name">${esc(a.name)}</span>
          <div class="inv-mcard-actions">
            <button class="inv-abtn" onclick="invStartEdit('${a.id}')" title="Edit">✎</button>
            <button class="inv-abtn del" onclick="invDelete('${a.id}')" title="Delete">✕</button>
          </div>
        </div>
        <div class="inv-mcard-grid">
          <div class="inv-mcard-item"><span class="inv-mcard-lbl">Value</span><span class="inv-mcard-v money">${invFormatINR(a.value)}</span></div>
          <div class="inv-mcard-item"><span class="inv-mcard-lbl">% Portfolio</span><span class="inv-mcard-v">${pct}%</span></div>
          <div class="inv-mcard-item"><span class="inv-mcard-lbl">Target</span><span class="inv-mcard-v" style="font-weight:700">${a.target}%</span></div>
          <div class="inv-mcard-item"><span class="inv-mcard-lbl">Status</span><span class="inv-status ${status.cls}" style="font-size:11px;padding:2px 8px">${status.icon} ${status.text}</span></div>
        </div>
        <div class="inv-mcard-bar"><div class="inv-mcard-bar-wrap"><div class="inv-mcard-bar-fill" style="width:${Math.min(pct,100)}%;background:${barColor}"></div></div></div>
      </div>`;
    }
  });

  // Add row (inline — desktop table)
  let addRow = '';
  let addCard = '';
  if(_invAdding){
    addRow = `<tr style="background:rgba(42,122,64,.06)">
      <td><input class="inv-edit-input" id="inv-a-name" placeholder="e.g. Fixed Deposit" autofocus></td>
      <td class="r"><input class="inv-edit-input num" id="inv-a-value" type="number" placeholder="0"></td>
      <td class="r"><span class="inv-pct">—</span></td>
      <td class="r"><input class="inv-edit-input num" id="inv-a-target" type="number" placeholder="0" step="0.5"></td>
      <td colspan="3"><span style="font-size:12px;color:var(--muted);font-style:italic">Fill in details and save</span></td>
      <td class="r">
        <div style="display:flex;gap:4px;justify-content:flex-end">
          <button class="inv-abtn" onclick="invSaveNew()" title="Save" style="color:#1a8a5a;opacity:1;font-size:16px">✓</button>
          <button class="inv-abtn" onclick="invCancelAdd()" title="Cancel" style="opacity:1;font-size:16px">✕</button>
        </div>
      </td>
    </tr>`;
    // Mobile add card
    addCard = `<div class="inv-medit">
      <div style="font-weight:700;font-size:14px;color:var(--text);margin-bottom:10px">+ New Asset</div>
      <div class="inv-medit-fields">
        <div><label>Asset Name</label><input class="inv-edit-input" id="inv-ma-name" placeholder="e.g. Fixed Deposit"></div>
        <div><label>Value (INR)</label><input class="inv-edit-input" id="inv-ma-value" type="number" placeholder="0"></div>
        <div><label>Target %</label><input class="inv-edit-input" id="inv-ma-target" type="number" placeholder="0" step="0.5"></div>
      </div>
      <div class="inv-medit-btns">
        <button class="inv-medit-cancel" onclick="invCancelAdd()">Cancel</button>
        <button class="inv-medit-save" onclick="invSaveNew(true)">Save</button>
      </div>
    </div>`;
  }

  // Footer — bold green row (desktop)
  const footerHtml = `<tr class="inv-tfoot">
    <td style="font-size:15px;font-weight:800;letter-spacing:.3px">TOTAL PORTFOLIO</td>
    <td class="r"><span class="inv-val">${invFormatINR(total)}</span></td>
    <td class="r"><span class="inv-pct">100.00%</span></td>
    <td class="r"><span class="inv-pct">${totalTarget}%</span></td>
    <td colspan="4" class="inv-col-alloc inv-col-status">${totalTarget!==100?'<div class="inv-rebalance">← Rebalance needed</div>':''}</td>
  </tr>`;

  // Mobile total card
  const totalCard = `<div class="inv-mcard-total inv-mcard">
    <div class="inv-mcard-top"><span class="inv-mcard-name">Total Portfolio</span></div>
    <div class="inv-mcard-grid">
      <div class="inv-mcard-item"><span class="inv-mcard-lbl">Value</span><span class="inv-mcard-v money">${invFormatINR(total)}</span></div>
      <div class="inv-mcard-item"><span class="inv-mcard-lbl">% Portfolio</span><span class="inv-mcard-v">100.00%</span></div>
      <div class="inv-mcard-item"><span class="inv-mcard-lbl">Target</span><span class="inv-mcard-v">${totalTarget}%</span></div>
      <div class="inv-mcard-item"><span class="inv-mcard-lbl">Assets</span><span class="inv-mcard-v">${assets.length}</span></div>
    </div>
    ${totalTarget!==100?'<div class="inv-mcard-rebal">← Rebalance needed — targets sum to '+totalTarget+'%</div>':''}
  </div>`;

  container.innerHTML = `
    <table class="inv-table">
      <thead><tr>
        <th>Asset</th><th class="r">Value (INR)</th><th class="r">% Portfolio</th><th class="r">Target %</th>
        <th class="inv-col-alloc">Allocation</th><th class="inv-col-status">Status</th><th class="r">Gap vs Target</th><th class="r">Actions</th>
      </tr></thead>
      <tbody>${rows}${addRow}</tbody>
      <tfoot>${footerHtml}</tfoot>
    </table>
    <div class="inv-cards-mobile">
      ${addCard}${cards}${totalCard}
    </div>`;

  // Auto-focus: desktop table or mobile card
  if(_invAdding){
    setTimeout(()=>{
      const el=document.getElementById('inv-a-name')||document.getElementById('inv-ma-name');
      if(el)el.focus();
    },50);
  }

  // ── PIE CHART ─────────────────────────────
  invRenderChart(assets, total);
}

let _invChartInstance = null;
function invRenderChart(assets, total){
  const section = document.getElementById('inv-chart-section');
  const canvas = document.getElementById('inv-pie-chart');
  if(!section || !canvas) return;

  if(!assets.length || total <= 0){
    section.style.display = 'none';
    if(_invChartInstance){ _invChartInstance.destroy(); _invChartInstance=null; }
    return;
  }
  section.style.display = '';

  const labels = assets.map(a => a.name);
  const values = assets.map(a => a.value);
  const colors = assets.map((_,i) => INV_BAR_COLORS[i % INV_BAR_COLORS.length]);
  const pcts = assets.map(a => invCalcPortfolio(a.value, total));

  // Detect dark theme
  const isDark = document.body.classList.contains('theme-midnight') || document.body.classList.contains('theme-ember') || document.body.classList.contains('theme-ocean');
  const textColor = isDark ? '#c8c8c8' : '#3c3c3c';
  const legendColor = isDark ? '#a0a0a0' : '#5a5a5a';

  if(_invChartInstance){
    _invChartInstance.data.labels = labels;
    _invChartInstance.data.datasets[0].data = values;
    _invChartInstance.data.datasets[0].backgroundColor = colors;
    _invChartInstance.options.plugins.legend.labels.color = legendColor;
    _invChartInstance.update();
    return;
  }

  const ctx = canvas.getContext('2d');
  _invChartInstance = new Chart(ctx, {
    type: 'pie',
    data: {
      labels: labels,
      datasets: [{
        data: values,
        backgroundColor: colors,
        borderColor: isDark ? 'rgba(20,20,20,.6)' : 'rgba(255,255,255,.8)',
        borderWidth: 2.5,
        hoverBorderWidth: 3,
        hoverBorderColor: isDark ? '#fff' : '#333',
        hoverOffset: 12
      }]
    },
    options: {
      responsive: true,
      maintainAspectRatio: true,
      layout: { padding: 10 },
      plugins: {
        legend: {
          position: 'right',
          labels: {
            color: legendColor,
            font: { family: "'Inter',sans-serif", size: 12, weight: '600' },
            padding: 12,
            usePointStyle: true,
            pointStyleWidth: 14,
            generateLabels: function(chart){
              const data = chart.data;
              return data.labels.map((label, i)=>{
                const val = data.datasets[0].data[i];
                const pct = total > 0 ? ((val/total)*100).toFixed(1) : '0.0';
                const shortName = label.length > 22 ? label.substring(0,20)+'…' : label;
                return {
                  text: shortName + '  ' + pct + '%',
                  fillStyle: data.datasets[0].backgroundColor[i],
                  strokeStyle: 'transparent',
                  lineWidth: 0,
                  pointStyle: 'rectRounded',
                  hidden: false,
                  index: i
                };
              });
            }
          }
        },
        tooltip: {
          backgroundColor: isDark ? 'rgba(30,30,30,.95)' : 'rgba(255,255,255,.96)',
          titleColor: isDark ? '#e0e0e0' : '#222',
          bodyColor: isDark ? '#c0c0c0' : '#444',
          borderColor: isDark ? 'rgba(255,255,255,.15)' : 'rgba(0,0,0,.1)',
          borderWidth: 1,
          titleFont: { family: "'Inter',sans-serif", size: 13, weight: '700' },
          bodyFont: { family: "'Courier New',monospace", size: 13, weight: '600' },
          padding: 12,
          cornerRadius: 10,
          displayColors: true,
          boxWidth: 12,
          boxHeight: 12,
          boxPadding: 4,
          callbacks: {
            label: function(ctx){
              const val = ctx.raw;
              const pct = total > 0 ? ((val/total)*100).toFixed(2) : '0.00';
              return ' ' + new Intl.NumberFormat('en-IN',{style:'currency',currency:'INR',maximumFractionDigits:0}).format(val) + '  (' + pct + '%)';
            }
          }
        }
      }
    }
  });
}

function invOpenAddRow(){
  _invAdding = true;
  _invEditId = null;
  invRender();
}
function invCancelAdd(){
  _invAdding = false;
  invRender();
}

async function invSaveNew(fromMobile){
  const pfx = fromMobile ? 'inv-ma-' : 'inv-a-';
  const name = (document.getElementById(pfx+'name')?.value||'').trim();
  const value = parseFloat(document.getElementById(pfx+'value')?.value)||0;
  const target = parseFloat(document.getElementById(pfx+'target')?.value)||0;
  if(!name){toast('Enter an asset name','error');return;}
  const assets = invGetData();
  assets.push({id:'inv_'+Date.now(), name, value, target});
  _invAdding = false;
  invRender();
  toast('Asset added ✓','success');
  await saveToFirebase();
}

function invStartEdit(id){
  _invEditId = id;
  _invAdding = false;
  invRender();
}
function invCancelEdit(){
  _invEditId = null;
  invRender();
}

async function invSaveEdit(fromMobile){
  const pfx = fromMobile ? 'inv-me-' : 'inv-e-';
  const name = (document.getElementById(pfx+'name')?.value||'').trim();
  const value = parseFloat(document.getElementById(pfx+'value')?.value)||0;
  const target = parseFloat(document.getElementById(pfx+'target')?.value)||0;
  if(!name){toast('Enter an asset name','error');return;}
  const assets = invGetData();
  const a = assets.find(x=>x.id===_invEditId);
  if(a){a.name=name;a.value=value;a.target=target;}
  _invEditId = null;
  invRender();
  toast('Asset updated ✓','success');
  await saveToFirebase();
}

async function invDelete(id){
  if(!confirm('Delete this asset?')) return;
  DATA.investments = invGetData().filter(a=>a.id!==id);
  INVESTMENTS = DATA.investments;
  invRender();
  toast('Asset deleted','success');
  await saveToFirebase();
}

/* ==================== IMPORTANT DATES ==================== */
let _impFilter = 'all';
const IMP_CAT_LABEL = {
  personal:'👤 Personal',
  official:'💼 Official',
  family:'👨‍👩‍👧 Family',
  health:'❤️ Health',
  finance:'💰 Finance',
  other:'📌 Other'
};

function impGetData(){ return Array.isArray(DATA.important_dates) ? DATA.important_dates : []; }

function impTodayStr(){
  const d=new Date();
  const y=d.getFullYear(), m=String(d.getMonth()+1).padStart(2,'0'), dd=String(d.getDate()).padStart(2,'0');
  return y+'-'+m+'-'+dd;
}

function impDaysUntil(dateStr){
  if(!dateStr) return 0;
  const today=new Date(); today.setHours(0,0,0,0);
  const target=new Date(dateStr+'T00:00:00');
  return Math.round((target-today)/(1000*60*60*24));
}

function impFormatBadge(dateStr){
  const days=impDaysUntil(dateStr);
  if(days===0) return {text:'Today', cls:'today'};
  if(days===1) return {text:'Tomorrow', cls:''};
  if(days>0 && days<=7) return {text:'In '+days+' days', cls:''};
  if(days>0) return {text:'In '+days+' days', cls:''};
  if(days===-1) return {text:'Yesterday', cls:'overdue'};
  return {text:Math.abs(days)+'d ago', cls:'overdue'};
}

function impMonthShort(dateStr){
  if(!dateStr) return '';
  const d=new Date(dateStr+'T00:00:00');
  return d.toLocaleString('en-US',{month:'short'}).toUpperCase();
}

function impDayNum(dateStr){
  if(!dateStr) return '';
  const d=new Date(dateStr+'T00:00:00');
  return d.getDate();
}

function impYear(dateStr){
  if(!dateStr) return '';
  const d=new Date(dateStr+'T00:00:00');
  return d.getFullYear();
}

function impEscape(s){
  return String(s||'').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
}

function impOpenModal(id){
  const bd=document.getElementById('imp-modal-backdrop');
  const titleEl=document.getElementById('imp-modal-title');
  const idEl=document.getElementById('imp-edit-id');
  const dateEl=document.getElementById('imp-input-date');
  const titleInput=document.getElementById('imp-input-title');
  const catEl=document.getElementById('imp-input-cat');
  const noteEl=document.getElementById('imp-input-note');
  if(id){
    const entry=impGetData().find(e=>e.id===id);
    if(!entry){ toast('Entry not found','error'); return; }
    titleEl.textContent='Edit Important Date';
    idEl.value=id;
    dateEl.value=entry.date||'';
    titleInput.value=entry.title||'';
    catEl.value=entry.category||'personal';
    noteEl.value=entry.note||'';
  } else {
    titleEl.textContent='Add Important Date';
    idEl.value='';
    dateEl.value=impTodayStr();
    titleInput.value='';
    catEl.value='personal';
    noteEl.value='';
  }
  bd.classList.add('open');
  setTimeout(()=>titleInput.focus(),100);
}

function impCloseModal(){
  document.getElementById('imp-modal-backdrop').classList.remove('open');
}

async function impSaveEntry(){
  const id=document.getElementById('imp-edit-id').value;
  const date=document.getElementById('imp-input-date').value;
  const title=document.getElementById('imp-input-title').value.trim();
  const category=document.getElementById('imp-input-cat').value;
  const note=document.getElementById('imp-input-note').value.trim();

  if(!date){ toast('Please pick a date','error'); return; }
  if(!title){ toast('Please enter a title','error'); return; }

  if(!Array.isArray(DATA.important_dates)) DATA.important_dates=[];

  if(id){
    const entry=DATA.important_dates.find(e=>e.id===id);
    if(entry){
      entry.date=date; entry.title=title; entry.category=category; entry.note=note;
      entry.updatedAt=Date.now();
      // Re-sync to GCal on edit: delete old event then create updated one
      if(_gcalEventMap[id]){
        _gcalWithToken(async token=>{
          await _gcalDeleteEvent(token, id).catch(console.warn);
          await _gcalCreateAllDayEvent(token, id, '📅 '+title, date, note||'').catch(console.warn);
          _gcalToast('📅 Important date updated in Google Calendar','success');
        });
      } else {
        addImpDateToGoogleCalendar(id, title, date, note);
      }
    }
    toast('Important date updated ✓','success');
  } else {
    const newId='imp_'+Date.now()+'_'+Math.random().toString(36).slice(2,7);
    DATA.important_dates.push({
      id:newId,
      date, title, category, note,
      createdAt:Date.now(), updatedAt:Date.now()
    });
    addImpDateToGoogleCalendar(newId, title, date, note);
    toast('Important date added ✓','success');
  }

  impCloseModal();
  impRenderPage();
  impRenderDashboard();
  updateImpDatesCount();
  await saveToFirebase();
}

async function impDelete(id){
  if(!confirm('Delete this important date?')) return;
  deleteImpDateFromGoogleCalendar(id);
  DATA.important_dates=impGetData().filter(e=>e.id!==id);
  impRenderPage();
  impRenderDashboard();
  updateImpDatesCount();
  toast('Deleted','success');
  await saveToFirebase();
}

function impSetFilter(f, btn){
  _impFilter=f;
  document.querySelectorAll('.imp-filter-btn').forEach(b=>b.classList.remove('active'));
  if(btn) btn.classList.add('active');
  impRenderPage();
}

function impRenderPage(){
  const wrap=document.getElementById('imp-list-wrap');
  const hdrCount=document.getElementById('imp-hdr-count');
  if(!wrap) return;

  const all=impGetData().slice();
  const todayStr=impTodayStr();

  let filtered=all;
  if(_impFilter==='upcoming')     filtered=all.filter(e=>e.date >  todayStr);
  else if(_impFilter==='today')   filtered=all.filter(e=>e.date === todayStr);
  else if(_impFilter==='past')    filtered=all.filter(e=>e.date <  todayStr);

  // Sort month-wise: chronological (oldest to newest).
  // Entries with same date stay together; grouping happens after sort.
  filtered.sort((a,b)=>(a.date||'').localeCompare(b.date||''));

  if(hdrCount) hdrCount.textContent = all.length===1 ? '1 date' : all.length+' dates';

  if(!filtered.length){
    const msg = _impFilter==='all'
      ? 'No important dates yet. Click "+ Add Date" to add your first one.'
      : 'No dates match this filter.';
    wrap.innerHTML='<div class="imp-empty">'+msg+'</div>';
    return;
  }

  // Group by YYYY-MM
  const groups={}; // key -> { label, items: [] }
  const order=[];
  filtered.forEach(e=>{
    if(!e.date){ return; }
    const key = e.date.slice(0,7); // YYYY-MM
    if(!groups[key]){
      const d=new Date(e.date+'T00:00:00');
      const monthName = d.toLocaleString('en-US',{month:'long'}).toUpperCase();
      groups[key] = { label: monthName + ' ' + d.getFullYear(), items: [] };
      order.push(key);
    }
    groups[key].items.push(e);
  });

  const renderCard = (e)=>{
    const days=impDaysUntil(e.date);
    const badge=impFormatBadge(e.date);
    let cardCls='imp-card';
    // Category-colored border (#4)
    cardCls += ' cat-' + (e.category || 'other');
    if(days===0) cardCls+=' today';
    else if(days<0) cardCls+=' overdue';

    // Urgent pulse badge for 1-3 days out (#2). Today/overdue already pulse via their own classes.
    let badgeCls = badge.cls;
    if(days>0 && days<=3) badgeCls += ' urgent';

    return `<div class="${cardCls}">
      <div class="imp-card-date">
        <div class="imp-card-day">${impDayNum(e.date)}</div>
        <div class="imp-card-mon">${impMonthShort(e.date)}</div>
        <div class="imp-card-yr">${impYear(e.date)}</div>
      </div>
      <div class="imp-card-body">
        <div class="imp-card-title">${impEscape(e.title)}</div>
        ${e.note?`<div class="imp-card-note">${impEscape(e.note)}</div>`:''}
        <div class="imp-card-meta">
          <span class="imp-card-badge ${badgeCls}">${badge.text}</span>
          <span class="imp-card-badge cat">${IMP_CAT_LABEL[e.category]||'📌 Other'}</span>
        </div>
      </div>
      <div class="imp-card-actions">
        <button class="imp-card-btn" onclick="impOpenModal('${e.id}')" title="Edit">✎</button>
        <button class="imp-card-btn del" onclick="impDelete('${e.id}')" title="Delete">🗑</button>
      </div>
    </div>`;
  };

  // Hero "Next Up" card (#3) — the single next upcoming event (not overdue)
  const nextUp = filtered.filter(e=>e.date && e.date>=todayStr)
                         .sort((a,b)=>a.date.localeCompare(b.date))[0];
  let heroHtml = '';
  if(nextUp){
    const d=impDaysUntil(nextUp.date);
    let heroCls='imp-hero';
    let heroLabel='Next Up';
    if(d===0){ heroCls+=' today'; heroLabel='Today'; }
    else if(d>0 && d<=3){ heroCls+=' urgent'; heroLabel='Coming Up Soon'; }

    // Live countdown: if today, show hours + minutes until end of day; otherwise days + hours
    const now=new Date();
    const target=new Date(nextUp.date+'T00:00:00');
    const msLeft = target - now;
    const totalHours = Math.max(0, Math.floor(msLeft/(1000*60*60)));
    const totalMins  = Math.max(0, Math.floor((msLeft%(1000*60*60))/(1000*60)));

    let cdBlocks = '';
    if(d>0){
      cdBlocks = `
        <div class="imp-hero-cdblock"><div class="imp-hero-cdnum">${d}</div><div class="imp-hero-cdlbl">${d===1?'Day':'Days'}</div></div>
        <div class="imp-hero-cdblock"><div class="imp-hero-cdnum">${totalHours % 24}</div><div class="imp-hero-cdlbl">Hours</div></div>
        <div class="imp-hero-cdblock"><div class="imp-hero-cdnum">${totalMins}</div><div class="imp-hero-cdlbl">Mins</div></div>`;
    } else if(d===0){
      cdBlocks = `<div class="imp-hero-cdblock"><div class="imp-hero-cdnum">🎉</div><div class="imp-hero-cdlbl">Today</div></div>`;
    }

    heroHtml = `<div class="${heroCls}">
      <div class="imp-hero-date">
        <div class="imp-hero-day">${impDayNum(nextUp.date)}</div>
        <div class="imp-hero-mon">${impMonthShort(nextUp.date)}</div>
        <div class="imp-hero-yr">${impYear(nextUp.date)}</div>
      </div>
      <div class="imp-hero-body">
        <div class="imp-hero-label">${heroLabel}</div>
        <div class="imp-hero-title">${impEscape(nextUp.title)}</div>
        ${nextUp.note?`<div class="imp-hero-note">${impEscape(nextUp.note)}</div>`:''}
        <span class="imp-hero-cat">${IMP_CAT_LABEL[nextUp.category]||'📌 Other'}</span>
      </div>
      ${cdBlocks?`<div class="imp-hero-countdown">${cdBlocks}</div>`:''}
    </div>`;
  }

  // Determine current month for highlighting
  const curMonthKey = todayStr.slice(0,7);

  wrap.innerHTML = heroHtml + order.map(key=>{
    const g = groups[key];
    const isCurrent = key === curMonthKey;
    const cntText = g.items.length===1 ? '1 date' : g.items.length+' dates';
    return `<div class="imp-month-section">
      <div class="imp-month-header${isCurrent?' current':''}">
        <span class="imp-month-label">📅 ${g.label}</span>
        <span class="imp-month-count">${cntText}</span>
      </div>
      <div class="imp-grid">${g.items.map(renderCard).join('')}</div>
    </div>`;
  }).join('');
}

function impRenderDashboard(){
  const el=document.getElementById('dash-impdates-list');
  if(!el) return;
  const all=impGetData().slice();
  const todayStr=impTodayStr();

  // Compute current month + determine if we're in the last 7 days (then include next month too)
  const today = new Date(); today.setHours(0,0,0,0);
  const curYear = today.getFullYear();
  const curMonth = today.getMonth(); // 0-indexed
  const lastDayOfMonth = new Date(curYear, curMonth+1, 0).getDate();
  const daysLeftInMonth = lastDayOfMonth - today.getDate();
  const includeNextMonth = daysLeftInMonth <= 7;

  // Build allowed YYYY-MM keys
  const curKey = curYear + '-' + String(curMonth+1).padStart(2,'0');
  let nextKey = null;
  if(includeNextMonth){
    const nd = new Date(curYear, curMonth+1, 1);
    nextKey = nd.getFullYear() + '-' + String(nd.getMonth()+1).padStart(2,'0');
  }

  // Filter: only dates in current month (and next month if rolling over), and not in the past
  const relevant = all.filter(e=>{
    if(!e.date || e.date < todayStr) return false;
    const mk = e.date.slice(0,7);
    return mk === curKey || (nextKey && mk === nextKey);
  }).sort((a,b)=>a.date.localeCompare(b.date))
    .slice(0,4);

  if(!relevant.length){
    el.innerHTML='<div class="dash-empty">No important dates this month.</div>';
    return;
  }

  el.innerHTML=relevant.map(e=>{
    const days=impDaysUntil(e.date);
    const badge=impFormatBadge(e.date);
    const cls = days===0 ? ' ii-today' : '';
    return `<div class="ii${cls}" onclick="dashGoToImpDate('${e.id}')" style="cursor:pointer" title="Click to edit">
      <div class="ii-date">
        <div class="ii-day">${impDayNum(e.date)}</div>
        <div class="ii-mon">${impMonthShort(e.date)}</div>
      </div>
      <div class="ii-info">
        <div class="ii-title">${impEscape(e.title)}</div>
        ${e.note?`<div class="ii-note">${impEscape(e.note)}</div>`:''}
      </div>
      <span class="ii-badge ${badge.cls}">${badge.text}</span>
    </div>`;
  }).join('');
}

function updateImpDatesCount(){
  const n=impGetData().length;
  const el=document.getElementById('nav-impdates-count');
  if(el) el.textContent=n;
}

// Close modal on backdrop click
document.addEventListener('click', function(ev){
  const bd=document.getElementById('imp-modal-backdrop');
  if(bd && ev.target===bd) impCloseModal();
});

// Close modal on Escape key
document.addEventListener('keydown', function(ev){
  if(ev.key==='Escape'){
    const bd=document.getElementById('imp-modal-backdrop');
    if(bd && bd.classList.contains('open')) impCloseModal();
  }
});

// ── Global in-memory store for pasted image data URLs (keyed by short token) ──
window._imgDataStore = {};
window._imgTokenCounter = 0;

window.addEventListener('DOMContentLoaded',()=>{
  const savedTheme=localStorage.getItem('mynotes_theme')||'rose';
  applyTheme(savedTheme);

  // restore saved view preferences
  ['rem','notes'].forEach(sec=>{
    const saved=localStorage.getItem('view_'+sec)||'card';
    setView(sec, saved);
  });

  // NOTE: initSticky() is intentionally NOT called here.
  // It is called inside loadFromFirebase() AFTER real data is loaded from Firebase.
  // Calling it here with empty DATA triggers saveToFirebase() which wipes everything.
  initJournalListeners();
  startClock();
  initNotesPasteHandler();

  // Auto-refresh: not needed with Firestore (no SHA conflicts)

  // Notification permission check
  checkNotifPermissionPrompt();
  cleanNotifiedIds();
  // Badge updater — works without notification permission (favicon + PWA icon)
  startBadgeUpdater();

  // Firebase auth state listener
  fbAuth.onAuthStateChanged(user=>{
    updateAuthUI(user);
    if(user){
      closeSettings();
      loadFromFirebase();
      _gcalAutoSyncOnLoad(); // ← auto-sync existing reminders after login
    } else {
      dataLoaded=true;
      initSticky();
      openSettings();
      renderAll();
    }
  });
});
</script>

<!-- Image lightbox -->
<div id="md-img-lightbox" onclick="this.classList.remove('open')">
  <img id="md-img-lightbox-img" src="" alt="">
</div>
<script>
function mdImgZoom(img){
  const lb=document.getElementById('md-img-lightbox');
  const li=document.getElementById('md-img-lightbox-img');
  if(!lb||!li) return;
  li.src=img.src; li.alt=img.alt;
  lb.classList.add('open');
}
function mdImgDelete(btn){
  const wrap = btn.closest('.md-img-wrap');
  const token = wrap ? wrap.getAttribute('data-token') : null;
  const ta = document.getElementById('notes-editor-body');
  if(ta){
    if(token){
      // Remove the token-based markdown line
      ta.value = ta.value.replace(new RegExp('\\n?!\\[[^\\]]*\\]\\(%%IMGDATA:'+token+'%%\\)\\n?','g'), '\n').trim();
      // Clean up memory store
      if(window._imgDataStore) delete window._imgDataStore[token];
    } else {
      // Fallback: remove any data:image line matching this img src (external URLs etc.)
      const src = wrap ? (wrap.querySelector('img')||{}).src : '';
      if(src) ta.value = ta.value.replace(new RegExp('\\n?!\\[[^\\]]*\\]\\('+src.replace(/[.*+?^${}()|[\]\\]/g,'\\$&')+'\\)\\n?','g'),'\n').trim();
    }
    ta.dispatchEvent(new Event('input'));
  }
  // Remove from preview immediately
  if(wrap) wrap.remove();
}
document.addEventListener('keydown',function(e){
  if(e.key==='Escape'){
    const lb=document.getElementById('md-img-lightbox');
    if(lb) lb.classList.remove('open');
  }
});
</script>
</body>
</html>"""

def main():
    hour_opts = ''.join(f'<option value="{i:02d}">{i:02d}</option>' for i in range(24))
    min_opts  = ''.join(f'<option value="{i:02d}">{i:02d}</option>' for i in range(60))
    html = HTML.replace('HOUR_OPTIONS_PLACEHOLDER', hour_opts)
    html = html.replace('MIN_OPTIONS_PLACEHOLDER',  min_opts)

    # Inject Firebase config and Google credentials from environment variables
    firebase_replacements = {
        'FIREBASE_API_KEY_PLACEHOLDER':              os.environ.get('FIREBASE_API_KEY', ''),
        'FIREBASE_AUTH_DOMAIN_PLACEHOLDER':           os.environ.get('FIREBASE_AUTH_DOMAIN', ''),
        'FIREBASE_PROJECT_ID_PLACEHOLDER':            os.environ.get('FIREBASE_PROJECT_ID', ''),
        'FIREBASE_STORAGE_BUCKET_PLACEHOLDER':        os.environ.get('FIREBASE_STORAGE_BUCKET', ''),
        'FIREBASE_MESSAGING_SENDER_ID_PLACEHOLDER':   os.environ.get('FIREBASE_MESSAGING_SENDER_ID', ''),
        'FIREBASE_APP_ID_PLACEHOLDER':                os.environ.get('FIREBASE_APP_ID', ''),
        'GOOGLE_CLIENT_ID_PLACEHOLDER':               os.environ.get('GOOGLE_CLIENT_ID', ''),
    }
    for placeholder, value in firebase_replacements.items():
        html = html.replace(placeholder, value)

    os.makedirs(os.path.dirname(OUTPUT_FILE), exist_ok=True)
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"✅ HTML generated → {OUTPUT_FILE}")

if __name__ == "__main__":
    main()
