/* Deterministic runtime checks of the recovered UI, without AWS or browser credentials. */
const fs=require('fs'),vm=require('vm'),assert=require('assert');
const elements=new Map(),timers=[];
function element(id){if(!elements.has(id))elements.set(id,{id,value:'1',textContent:'',innerHTML:'',dataset:{},style:{},classList:{toggle(){},add(){}},addEventListener(){},append(){},replaceChildren(){},setAttribute(){},click(){return this.onclick?.();}});return elements.get(id);}
const context={console,Map,Set,Date,Number,String,Math,Array,JSON,Error,URL,Blob,performance:{now:()=>0},location:{href:'http://127.0.0.1:8000/',origin:'http://127.0.0.1:8000'},
 document:{getElementById:element,createElement:()=>element('made'+Math.random()),createDocumentFragment:()=>({append(){}}),querySelectorAll:()=>[],documentElement:{style:{setProperty(){}}}},
 EventSource:class {close(){this.closed=true;}},setInterval:(fn,ms)=>timers.push({fn,ms}),
 fetch:async()=>({ok:true,json:async()=>({state:'connected',tables:{t:'snapshot polling'},warnings:[]})})};
vm.createContext(context);vm.runInContext(fs.readFileSync('observer/static/app.js','utf8'),context);
const run=s=>vm.runInContext(s,context);
(async()=>{
 run("changeMode('simulation');playing=false;time=45;rebuild();render()");
 assert.equal(run('items.size'),7,'simulation generations');
 assert.equal(run("items.get('sim-g0-000').state"),'TERMINATED');
 run("pinned='sim-g2-003';render()");
 assert.equal(run('lineage(pinned).size'),3,'pinned ancestry');
 run('time=200;rebuild();render()');
 assert.equal(run('items.size'),255,'bounded fan-out');
 assert.equal(run("[...items.values()].filter(e=>e.state!=='TERMINATED').length"),128);
 run("pinned='sim-g7-127';inspect(pinned)");
 assert(element('inspector').innerHTML.includes('Ancestor median'),'ancestor timing');
 run('time=14;rebuild();render()');
 assert.equal(run('items.size'),1,'replay rewind');
 assert.equal(run("items.get('sim-g0-000').state"),'READY');
 run('apply(events[0])');assert.equal(run('items.size'),1,'dedup');
 run("changeMode('replay')");
 const sample=[{event_id:'1',timestamp:'2026-10-03T00:00:00Z',instance_id:'fixture-a',generation:0,state:'BOOTING'},{event_id:'2',timestamp:'2026-10-03T00:00:13Z',instance_id:'fixture-a',generation:0,state:'READY'}];
 element('import').files=[];
 await element('import').onchange({target:{files:[{name:'fixture.jsonl',size:100,text:async()=>sample.map(x=>JSON.stringify(x)).join('\n')}]}});
 assert(element('notice').textContent.includes('Loaded 2 events'),'JSONL import: '+element('notice').textContent);
 run("changeMode('live');$('connect').click();stream.onopen();$('disconnect').click()");
 await timers.find(t=>t.ms===3000).fn();
 assert.equal(element('source').textContent,'LIVE · DISCONNECTED','disconnect must not report connected backend as connected feed');
 run("$('connect').click();stream.onmessage({data:JSON.stringify({instances:[]})})");
 assert.equal(run('items.size'),0,'empty snapshot resets state');
 const node={...sample[1],created_at:sample[0].timestamp,ready_at:sample[1].timestamp,history:sample.map(e=>({state:e.state,timestamp:e.timestamp}))};
 context.snapshot=JSON.stringify({instances:[node]});run('stream.onmessage({data:snapshot})');
 assert.equal(run("items.get('fixture-a').created_at"),sample[0].timestamp,'reconnect lifetime');
 assert.equal(run("items.get('fixture-a').history.length"),2,'reconnect history');
 run('stream.onerror()');await timers.find(t=>t.ms===3000).fn();
 assert.equal(element('source').textContent,'LIVE · RECONNECTING','backend status cannot mask SSE reconnect');
 run("items.clear();apply({instance_id:'11:a',request_id:'11',generation:0,state:'TERMINATED',timestamp:'2026-10-03T00:00:10Z',created_at:'2026-10-03T00:00:00Z',terminated_at:'2026-10-03T00:00:10Z'})");
 assert.equal(run('runDuration([...items.values()]).elapsed'),10000,'completed clock freezes');
 assert.equal(run("items.get('11:a').terminated_at"),'2026-10-03T00:00:10Z','preserve termination snapshot clock');
 assert.equal(run('runDuration([...items.values()]).completed'),true);
 assert.equal(run("runKey(items.get('11:a'))"),'11','run identity grouping');
 console.log('PASS: simulation, bounded fan-out, pinned lineage, ancestor timing, replay rewind, deduplication, JSONL import, disconnect state, SSE snapshot replacement');
})().catch(e=>{console.error(e);process.exitCode=1;});
