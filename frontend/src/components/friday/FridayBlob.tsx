/**
 * components/friday/FridayBlob.tsx
 * Friday's face: a blue wireframe sphere whose surface flows with 3D noise (from the "anomalous matter" scene
 * Kirito supplied). Changes: blue instead of white; the waves grow with her voice (getLevel, 0..1, read every
 * frame so React never re-renders for it); it spins faster while thinking; it turns red during an alarm.
 * The light follows the mouse. Falls back to a CSS glow when WebGL is not available.
 */
import { useEffect, useRef, useState } from 'react'
import * as THREE from 'three'

export type FridayState = 'idle' | 'listening' | 'thinking' | 'speaking' | 'alarm'

const COLORS: Record<FridayState, [string, string]> = {
  idle: ['#1d4ed8', '#38bdf8'],
  listening: ['#0ea5e9', '#7dd3fc'],
  thinking: ['#2563eb', '#60a5fa'],
  speaking: ['#1d4ed8', '#38bdf8'],
  alarm: ['#b91c1c', '#f87171'],
}

const VERTEX = /* glsl */ `
uniform float time;
uniform float level;
varying vec3 vNormal;
varying vec3 vPosition;
varying float vWave;

// 3D simplex noise (Ashima Arts / Stefan Gustavson, MIT)
vec3 mod289(vec3 x) { return x - floor(x * (1.0 / 289.0)) * 289.0; }
vec4 mod289(vec4 x) { return x - floor(x * (1.0 / 289.0)) * 289.0; }
vec4 permute(vec4 x) { return mod289(((x * 34.0) + 1.0) * x); }
vec4 taylorInvSqrt(vec4 r) { return 1.79284291400159 - 0.85373472095314 * r; }
float snoise(vec3 v) {
  const vec2 C = vec2(1.0 / 6.0, 1.0 / 3.0);
  const vec4 D = vec4(0.0, 0.5, 1.0, 2.0);
  vec3 i = floor(v + dot(v, C.yyy));
  vec3 x0 = v - i + dot(i, C.xxx);
  vec3 g = step(x0.yzx, x0.xyz);
  vec3 l = 1.0 - g;
  vec3 i1 = min(g.xyz, l.zxy);
  vec3 i2 = max(g.xyz, l.zxy);
  vec3 x1 = x0 - i1 + C.xxx;
  vec3 x2 = x0 - i2 + C.yyy;
  vec3 x3 = x0 - D.yyy;
  i = mod289(i);
  vec4 p = permute(permute(permute(i.z + vec4(0.0, i1.z, i2.z, 1.0)) + i.y + vec4(0.0, i1.y, i2.y, 1.0))
           + i.x + vec4(0.0, i1.x, i2.x, 1.0));
  float n_ = 0.142857142857;
  vec3 ns = n_ * D.wyz - D.xzx;
  vec4 j = p - 49.0 * floor(p * ns.z * ns.z);
  vec4 x_ = floor(j * ns.z);
  vec4 y_ = floor(j - 7.0 * x_);
  vec4 x = x_ * ns.x + ns.yyyy;
  vec4 y = y_ * ns.x + ns.yyyy;
  vec4 h = 1.0 - abs(x) - abs(y);
  vec4 b0 = vec4(x.xy, y.xy);
  vec4 b1 = vec4(x.zw, y.zw);
  vec4 s0 = floor(b0) * 2.0 + 1.0;
  vec4 s1 = floor(b1) * 2.0 + 1.0;
  vec4 sh = -step(h, vec4(0.0));
  vec4 a0 = b0.xzyw + s0.xzyw * sh.xxyy;
  vec4 a1 = b1.xzyw + s1.xzyw * sh.zzww;
  vec3 p0 = vec3(a0.xy, h.x);
  vec3 p1 = vec3(a0.zw, h.y);
  vec3 p2 = vec3(a1.xy, h.z);
  vec3 p3 = vec3(a1.zw, h.w);
  vec4 norm = taylorInvSqrt(vec4(dot(p0, p0), dot(p1, p1), dot(p2, p2), dot(p3, p3)));
  p0 *= norm.x; p1 *= norm.y; p2 *= norm.z; p3 *= norm.w;
  vec4 m = max(0.6 - vec4(dot(x0, x0), dot(x1, x1), dot(x2, x2), dot(x3, x3)), 0.0);
  m = m * m;
  return 42.0 * dot(m * m, vec4(dot(p0, x0), dot(p1, x1), dot(p2, x2), dot(p3, x3)));
}

void main() {
  vNormal = normal;
  vPosition = position;
  // calm swell at rest; the voice adds bigger, faster, finer ripples
  float slow = snoise(position * 1.6 + time * 0.45);
  float voice = snoise(position * (3.0 + level * 2.0) + time * (1.2 + level * 2.5));
  float displacement = slow * 0.12 + voice * level * 0.42;
  vWave = displacement;
  vec3 newPosition = position + normal * displacement;
  gl_Position = projectionMatrix * modelViewMatrix * vec4(newPosition, 1.0);
}
`

const FRAGMENT = /* glsl */ `
uniform vec3 colorDeep;
uniform vec3 colorBright;
uniform vec3 pointLightPos;
uniform float level;
varying vec3 vNormal;
varying vec3 vPosition;
varying float vWave;

void main() {
  vec3 normal = normalize(vNormal);
  vec3 lightDir = normalize(pointLightPos - vPosition);
  float diffuse = max(dot(normal, lightDir), 0.0);
  float fresnel = pow(1.0 - abs(dot(normal, vec3(0.0, 0.0, 1.0))), 2.0);
  vec3 base = mix(colorDeep, colorBright, clamp(0.5 + vWave * 2.5, 0.0, 1.0));
  vec3 finalColor = base * (0.35 + diffuse * 0.75) + colorBright * fresnel * (0.55 + level * 0.6);
  gl_FragColor = vec4(finalColor, 0.92);
}
`

function alarmColors(rgb: string): [string, string] {
  const [r, g, b] = rgb.split(',').map(Number)
  const deep = `rgb(${Math.round(r * 0.6)},${Math.round(g * 0.6)},${Math.round(b * 0.6)})`
  return [deep, `rgb(${r},${g},${b})`]
}

export function FridayBlob({ state, getLevel, className, alarmRgb }: { state: FridayState; getLevel: () => number; className?: string; alarmRgb?: string }) {
  const mountRef = useRef<HTMLDivElement>(null)
  const stateRef = useRef(state)
  const getLevelRef = useRef(getLevel)
  const alarmRef = useRef(alarmRgb)
  const [noWebgl, setNoWebgl] = useState(false)

  useEffect(() => {
    stateRef.current = state
    getLevelRef.current = getLevel
    alarmRef.current = alarmRgb
  }, [state, getLevel, alarmRgb])

  useEffect(() => {
    const mount = mountRef.current
    if (!mount) return
    let renderer: THREE.WebGLRenderer
    try {
      renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true })
    } catch {
      setNoWebgl(true)
      return
    }
    const reduced = window.matchMedia('(prefers-reduced-motion: reduce)').matches
    const scene = new THREE.Scene()
    const camera = new THREE.PerspectiveCamera(60, 1, 0.1, 100)
    camera.position.z = 3.4
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2))
    renderer.setClearColor(0x000000, 0)
    mount.appendChild(renderer.domElement)

    const [deep, bright] = COLORS[stateRef.current]
    const uniforms = {
      time: { value: 0 },
      level: { value: 0 },
      pointLightPos: { value: new THREE.Vector3(0, 0, 5) },
      colorDeep: { value: new THREE.Color(deep) },
      colorBright: { value: new THREE.Color(bright) },
    }
    const material = new THREE.ShaderMaterial({
      uniforms, vertexShader: VERTEX, fragmentShader: FRAGMENT, wireframe: true, transparent: true,
    })
    const geometry = new THREE.IcosahedronGeometry(1.15, 30)
    const mesh = new THREE.Mesh(geometry, material)
    scene.add(mesh)

    const resize = () => {
      const { clientWidth: w, clientHeight: h } = mount
      renderer.setSize(w, h, false)
      renderer.domElement.style.width = '100%'
      renderer.domElement.style.height = '100%'
      camera.aspect = w / Math.max(1, h)
      camera.updateProjectionMatrix()
    }
    resize()
    const observer = new ResizeObserver(resize)
    observer.observe(mount)

    const onMove = (e: PointerEvent) => {
      const x = (e.clientX / window.innerWidth) * 2 - 1
      const y = -(e.clientY / window.innerHeight) * 2 + 1
      uniforms.pointLightPos.value.set(x * 4, y * 4, 4)
    }
    window.addEventListener('pointermove', onMove)

    const targetDeep = new THREE.Color()
    const targetBright = new THREE.Color()
    let smooth = 0
    let frame = 0
    let last = performance.now()
    const animate = (now: number) => {
      const dt = Math.min(0.05, (now - last) / 1000)
      last = now
      const s = stateRef.current
      const raw = s === 'speaking' || s === 'listening' ? Math.min(1, Math.max(0, getLevelRef.current())) : 0
      // rise fast, fall slower: it follows syllables without flickering
      smooth += (raw - smooth) * (raw > smooth ? 0.45 : 0.12)
      const idleBreath = s === 'listening' ? 0.18 + Math.sin(now / 260) * 0.06 : s === 'alarm' ? 0.35 + Math.sin(now / 180) * 0.15 : 0
      uniforms.level.value = reduced ? 0 : Math.max(smooth, idleBreath)
      uniforms.time.value += dt * (reduced ? 0.1 : 1)
      const spin = s === 'thinking' ? 0.9 : 0.12 + smooth * 0.4
      mesh.rotation.y += dt * spin
      mesh.rotation.x += dt * spin * 0.35
      // an alarm takes its incident's colour: orange for fire, grey for smoke, red otherwise
      const [d, b] = s === 'alarm' && alarmRef.current ? alarmColors(alarmRef.current) : COLORS[s]
      uniforms.colorDeep.value.lerp(targetDeep.set(d), 0.08)
      uniforms.colorBright.value.lerp(targetBright.set(b), 0.08)
      renderer.render(scene, camera)
      frame = requestAnimationFrame(animate)
    }
    frame = requestAnimationFrame(animate)

    return () => {
      cancelAnimationFrame(frame)
      observer.disconnect()
      window.removeEventListener('pointermove', onMove)
      geometry.dispose()
      material.dispose()
      renderer.dispose()
      mount.removeChild(renderer.domElement)
    }
  }, [])

  return (
    <div ref={mountRef} className={className} aria-hidden="true">
      {noWebgl && (
        <div
          className="mx-auto aspect-square h-full rounded-full"
          style={{ background: `radial-gradient(circle, ${COLORS[state][1]} 0%, ${COLORS[state][0]} 45%, transparent 70%)`, opacity: 0.8 }}
        />
      )}
    </div>
  )
}
