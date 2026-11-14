import {spawn} from 'node:child_process';
import {writeFile} from 'node:fs/promises';
const [browser, url, output, userdir] = process.argv.slice(2);
const child = spawn(browser, ['--headless', '--use-angle=swiftshader', '--enable-unsafe-swiftshader', '--remote-debugging-port=0', `--user-data-dir=${userdir}`, '--window-size=1024,768', 'about:blank'], {stdio:['ignore','pipe','pipe']});
let stderr=''; child.stdout.on('data', b=>process.stdout.write(b)); child.stderr.on('data', b=>{stderr += b; process.stderr.write(b);});
const delay=ms=>new Promise(r=>setTimeout(r,ms));
try {
 let port;
 for (let i=0;i<100;i++){ const match=stderr.match(/DevTools listening on ws:\/\/127\.0\.0\.1:(\d+)\//); if(match){port=match[1];break;} if(child.exitCode!==null) throw Error('Browser exited'); await delay(100); }
 if(!port) throw Error('Browser startup timed out');
 const targets=await (await fetch(`http://127.0.0.1:${port}/json`)).json();
 const ws=new WebSocket(targets[0].webSocketDebuggerUrl); await new Promise((resolve,reject)=>{ws.onopen=resolve;ws.onerror=reject});
 let seq=0; const pending=new Map(); ws.onmessage=e=>{const m=JSON.parse(e.data);if(pending.has(m.id)){pending.get(m.id)(m);pending.delete(m.id)}};
 const send=(method,params={})=>new Promise((resolve,reject)=>{const id=++seq;pending.set(id,m=>m.error?reject(Error(m.error.message)):resolve(m.result));ws.send(JSON.stringify({id,method,params}));});
 await send('Page.enable');await send('Runtime.enable');await send('Page.navigate',{url});
 let ready=false; let metadata; 
 for(let i=0;i<600;i++){await delay(100);const v=await send('Runtime.evaluate',{expression:'window.godotReviewReady || null',returnByValue:true});if(v.result.value){ready=true;metadata=v.result.value;break;}}
 if(!ready) throw Error('Godot scene did not signal readiness');
 await delay(500);
 const shot=await send('Page.captureScreenshot',{format:'png'});await writeFile(output,Buffer.from(shot.data,'base64')); await writeFile(output + '.json', JSON.stringify(metadata)); ws.close();
} finally {child.kill('SIGTERM'); await Promise.race([new Promise(r=>child.on('exit',r)),delay(3000)]); if(child.exitCode===null){child.kill('SIGKILL');await new Promise(r=>child.on('exit',r));}}
