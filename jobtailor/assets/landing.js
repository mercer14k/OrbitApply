// Source for landing.bundle.js. All geometry, labels and textures are local.
import * as THREE from 'three';

export default function renderLanding({parentElement, setTriggerValue}) {
  const root = parentElement;
  const controller = new AbortController();
  const on = (element, event, listener) => element.addEventListener(event, listener, {signal: controller.signal});
  root.querySelectorAll('[data-route]').forEach(button => on(button, 'click', () => {
    setTriggerValue('route', button.dataset.route);
  }));
  const host = root.querySelector('.scene');
  const canvas = host.querySelector('canvas');
  const motion = root.querySelector('.motion');
  const media = window.matchMedia('(prefers-reduced-motion: reduce)');
  let paused = media.matches, visible = true, disposed = false, lost = false;
  let frame = null, elapsed = 0, previous = 0, rendered = 0;
  let renderer, scene, camera, group, resizeObserver, intersectionObserver;
  let pointer = {x: 0, y: 0};
  const resources = new Set();
  const own = resource => { resources.add(resource); return resource; };
  const updateMotionLabel = () => {
    motion.textContent = paused ? '▷ Play motion' : 'Ⅱ Pause motion';
    motion.setAttribute('aria-label', paused ? 'Play animation' : 'Pause animation');
    motion.setAttribute('aria-pressed', String(paused));
    host.dataset.motion = paused ? 'paused' : 'playing';
  };
  updateMotionLabel();

  function stop() { if (frame !== null) cancelAnimationFrame(frame); frame = null; previous = 0; }
  function draw() { if (renderer && !disposed && !lost) renderer.render(scene, camera); }
  function active() { return !disposed && !lost && !paused && visible && !document.hidden; }
  function tick(now) {
    frame = null;
    if (!active()) return;
    if (now - rendered >= 1000 / 24) {
      elapsed += previous ? Math.min((now - previous) / 1000, .1) : 0;
      previous = now; rendered = now;
      group.rotation.y += ((-.18 + pointer.x * .10 + Math.sin(elapsed * .26) * .08) - group.rotation.y) * .04;
      group.rotation.x += ((.06 + pointer.y * .07) - group.rotation.x) * .04;
      group.position.y = Math.sin(elapsed * .6) * .075;
      draw();
    }
    frame = requestAnimationFrame(tick);
  }
  function resume() { if (active() && frame === null) frame = requestAnimationFrame(tick); }
  function cleanup() {
    disposed = true; stop(); controller.abort();
    resizeObserver?.disconnect(); intersectionObserver?.disconnect();
    resources.forEach(resource => resource.dispose()); resources.clear();
    if (renderer) { renderer.dispose(); renderer.forceContextLoss(); }
    host.classList.remove('ready');
  }
  on(motion, 'click', () => { paused = !paused; updateMotionLabel(); paused ? stop() : resume(); });
  on(media, 'change', () => { paused = media.matches; updateMotionLabel(); paused ? stop() : resume(); });
  on(document, 'visibilitychange', () => document.hidden ? stop() : resume());

  function roundedShape(width, height, radius) {
    const s = new THREE.Shape(), x = -width / 2, y = -height / 2;
    s.moveTo(x + radius, y); s.lineTo(x + width - radius, y);
    s.quadraticCurveTo(x + width, y, x + width, y + radius);
    s.lineTo(x + width, y + height - radius);
    s.quadraticCurveTo(x + width, y + height, x + width - radius, y + height);
    s.lineTo(x + radius, y + height);
    s.quadraticCurveTo(x, y + height, x, y + height - radius);
    s.lineTo(x, y + radius); s.quadraticCurveTo(x, y, x + radius, y);
    return s;
  }
  function board(width, height, material, depth=.06) {
    const geometry = own(new THREE.ExtrudeGeometry(roundedShape(width, height, .12), {depth, bevelEnabled:true, bevelSegments:2, steps:1, bevelSize:.025, bevelThickness:.025, curveSegments:12}));
    return new THREE.Mesh(geometry, material);
  }
  function texture(width, height, painter) {
    const c = document.createElement('canvas'); c.width = width; c.height = height;
    const ctx = c.getContext('2d'); if (!ctx) throw new Error('Canvas unavailable');
    painter(ctx, width, height);
    const t = own(new THREE.CanvasTexture(c)); t.colorSpace = THREE.SRGBColorSpace;
    t.anisotropy = Math.min(4, renderer.capabilities.getMaxAnisotropy()); return t;
  }
  function face(parent, width, height, t, z=.10) {
    const plane = new THREE.Mesh(own(new THREE.PlaneGeometry(width, height)), own(new THREE.MeshBasicMaterial({map:t, transparent:true, depthWrite:false})));
    plane.position.z = z; parent.add(plane);
  }
  function line(ctx, x, y, w, color='#b3bdac', h=6) { ctx.fillStyle=color; ctx.beginPath(); ctx.roundRect(x,y,w,h,h/2); ctx.fill(); }
  try {
    renderer = new THREE.WebGLRenderer({canvas, alpha:true, antialias:true, powerPreference:'low-power'});
    renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 1.35));
    renderer.setClearColor(0x000000,0);
    scene = new THREE.Scene(); camera = new THREE.PerspectiveCamera(38,1,.1,50); camera.position.set(0,0,9);
    group = new THREE.Group(); group.rotation.set(.06,-.18,-.08); scene.add(group);
    scene.add(new THREE.HemisphereLight(0xf4f8e9,0x26362e,2.5));
    const key = new THREE.DirectionalLight(0xedffd6,3); key.position.set(-3,4,5); scene.add(key);
    const rim = new THREE.DirectionalLight(0xb8f4d2,1.8); rim.position.set(4,-1,3); scene.add(rim);
    const dark = own(new THREE.MeshStandardMaterial({color:0x28312b, metalness:.45, roughness:.38}));
    const paper = own(new THREE.MeshStandardMaterial({color:0xe3e7d7, metalness:.12, roughness:.65}));
    const lime = own(new THREE.MeshStandardMaterial({color:0xc7e999, emissive:0x465723, emissiveIntensity:.20, metalness:.36, roughness:.4}));

    // Fine orbital paths connect resume -> roles -> application package.
    for (let j=0; j<3; j++) {
      const points = Array.from({length:121}, (_,i) => {
        const a=i/120*Math.PI*2; return new THREE.Vector3(Math.cos(a)*(2.55+j*.18),Math.sin(a)*(2.00+j*.16),0);
      });
      const orbit = new THREE.Line(own(new THREE.BufferGeometry().setFromPoints(points)),own(new THREE.LineBasicMaterial({color:j===0?0x9aa878:0x546044,transparent:true,opacity:j===0?.65:.3})));
      orbit.rotation.set(j===1?.9:.42,j===2?.65:-.26,j*.35); orbit.position.z=-.7; group.add(orbit);
    }
    for(let i=0;i<8;i++) {
      const angle=i*Math.PI*.25+.18;
      const point = new THREE.Mesh(own(new THREE.SphereGeometry(i%3===0?.055:.027,12,8)),own(new THREE.MeshBasicMaterial({color:i%2?0x77856b:0xd3ee99})));
      point.position.set(Math.cos(angle)*2.65,Math.sin(angle)*1.94,-.65); group.add(point);
    }
    const back = board(2.22,3.03,dark); back.position.set(.25,.05,-.17); back.rotation.z=-.075; group.add(back);
    const sheet = board(2.12,2.94,paper); sheet.position.set(.13,.16,.04); sheet.rotation.z=.07; group.add(sheet);
    face(sheet,2.02,2.83,texture(606,849,(ctx,w,h)=>{
      ctx.clearRect(0,0,w,h);
      ctx.fillStyle='#5c6e45'; ctx.font='20px monospace'; ctx.fillText('YOUR EXPERIENCE',52,66);
      ctx.strokeStyle='#566842';ctx.lineWidth=3;ctx.strokeRect(507,38,37,37);ctx.beginPath();ctx.moveTo(515,68);ctx.lineTo(537,47);ctx.stroke();
      ctx.fillStyle='#283c25';ctx.font='bold 49px sans-serif';ctx.fillText('A story worth',50,151);ctx.fillText('building on.',50,207);
      line(ctx,52,251,200,'#acb99f',4);
      ctx.font='19px monospace';ctx.fillStyle='#586f42';ctx.fillText('EXPERIENCE & IMPACT',52,326);
      [432,466,361].forEach((v,i)=>line(ctx,52,355+i*25,v));
      ctx.fillStyle='#586f42';ctx.fillText('SKILLS & STRENGTHS',52,489);
      [463,399,438].forEach((v,i)=>line(ctx,52,518+i*25,v));
      ctx.fillStyle='#c2d9a5';ctx.beginPath();ctx.roundRect(50,664,506,108,16);ctx.fill();
      ctx.fillStyle='#425e30';ctx.font='19px monospace';ctx.fillText('THE NEXT CHAPTER',73,704);
      ctx.font='bold 25px sans-serif';ctx.fillText('Starts with you.',73,740);ctx.font='45px sans-serif';ctx.fillText('↗',489,737);
    }));
    const role = board(1.94,.97,dark); role.position.set(-1.46,.77,.74);role.rotation.set(0,.15,.13);group.add(role);
    face(role,1.88,.9,texture(640,300,(ctx)=>{
      ctx.fillStyle='#d3ee99';ctx.font='23px monospace';ctx.fillText('ROLE DISCOVERY',31,62);
      ctx.fillStyle='#e4eadb';ctx.font='bold 38px sans-serif';ctx.fillText('Your next role',31,123);
      ctx.fillStyle='#a3b093';ctx.font='23px sans-serif';ctx.fillText('Skills + experience',31,175);
      line(ctx,31,222,344,'#45533e',10);line(ctx,31,222,267,'#bedc8a',10);
      ctx.fillStyle='#cee997';ctx.font='55px sans-serif';ctx.fillText('↗',535,114);
    }));
    const folder = new THREE.Group();folder.position.set(1.37,-1.12,.85);folder.rotation.set(.03,-.12,-.03);group.add(folder);
    const folderBack=board(1.68,1.12,lime);folder.add(folderBack);
    const tab=board(.68,.20,lime,.03);tab.position.set(-.42,.57,-.01);folder.add(tab);
    const folderPaper=board(1.40,.89,paper,.02);folderPaper.position.set(0,.17,.10);folder.add(folderPaper);
    const folderFront=board(1.75,.86,lime,.04);folderFront.position.set(0,-.12,.20);folderFront.rotation.x=-.10;folder.add(folderFront);
    face(folderFront,1.65,.76,texture(660,304,(ctx)=>{
      ctx.fillStyle='#3d5630';ctx.font='20px monospace';ctx.fillText('YOUR APPLICATION',38,63);
      ctx.font='bold 37px sans-serif';ctx.fillText('Ready for review.',38,127);
      ctx.font='21px monospace';ctx.fillText('PDF  /  DOCX  /  JD',38,220);
    }),.08);
    const resize = () => {
      const {width,height}=host.getBoundingClientRect();
      if(width<=0||height<=0||disposed) return;
      renderer.setSize(width,height,false);camera.aspect=width/height;
      camera.position.z = camera.aspect < 1 ? 10 : 9;
      camera.updateProjectionMatrix();draw();
    };
    on(host,'pointermove',event=>{const rect=host.getBoundingClientRect();pointer={x:(event.clientX-rect.left)/rect.width-.5,y:(event.clientY-rect.top)/rect.height-.5};if(paused)pointer={x:0,y:0};});
    on(host,'pointerleave',()=>{pointer={x:0,y:0};});
    on(canvas,'webglcontextlost',()=>{lost=true;stop();host.classList.remove('ready');motion.textContent='Static preview';motion.disabled=true;});
    resizeObserver = new ResizeObserver(resize);resizeObserver.observe(host);resize();
    intersectionObserver = new IntersectionObserver(entries=>{visible=entries[0].isIntersecting;visible?resume():stop();});intersectionObserver.observe(host);
    host.classList.add('ready');resume();
  } catch(error) {
    // WebGL is an enhancement. The static CSS illustration and routing survive.
    stop(); resources.forEach(resource=>resource.dispose());resources.clear();
    if(renderer){renderer.dispose();renderer.forceContextLoss();renderer=null;}
    host.classList.remove('ready');motion.textContent='Static preview';motion.disabled=true;
  }
  return cleanup;
}
