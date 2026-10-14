# BrandverseAI — Design System

| Field | Value |
|---|---|
| Document | Design.md |
| Version | 1.0 |
| Date | 14 October 2026 |
| Depends on | `PRD.md` N4–N6, N9, N10 |

---

## 1. Note on the ThreeUI reference

The reference brief supplied with this project asks for `SylvaHero` from `@designcodeio/threeui`, reproduced byte-for-byte against pinned SHA-256 hashes, including licensed binary assets.

**That component is not reproducible here.** It is a registered package whose source and assets are not available to this project, and its own instructions say to stop rather than approximate it from appearance. Attempting a look-alike would be both a licensing problem and a worse result.

What is taken from it is the **design posture**, which is legitimate and transferable:

| Borrowed principle | How it appears in BrandverseAI |
|---|---|
| A living, organic 3D world rather than a static render | A knowledge field that drifts, breathes and reacts |
| Ambient particulate motion (pollen) | Data motes drifting through the field |
| One moment of life that rewards attention (the landing butterfly) | A retrieval pulse that travels the graph on an interval |
| Controls that feel native to the world, not bolted on | Liquid-glass surfaces over the canvas |
| The 3D sits *behind* a full, real page — not instead of it | Hero canvas is a backdrop; all content is real DOM |

The scene below is original work for this subject matter.

---

## 2. Concept — "The Brandverse"

> A brand's knowledge is not a document. It is a **living constellation** that grows denser, brighter and cheaper to traverse every time it is used.

That sentence is the product thesis and the art direction at once. The hero renders it literally:

- **Memory nodes** — points of light in 3D space. Each is one piece of brand knowledge.
- **Relations** — faint filaments between related nodes. Density grows toward the centre.
- **The brand core** — a slowly rotating central form the constellation organises around.
- **Agent orbits** — seven small bodies on inclined orbital paths. Seven agents.
- **Data motes** — fine particles drifting on a slow current. Ambient life.
- **The retrieval pulse** — every few seconds, a wave of light travels outward from the core through the filaments and returns. This is step 4 of the pipeline, made visible.

When a visitor watches the pulse travel and return, they have understood the product without reading a word. **That is the job of this hero.**

---

## 3. Colour

Dark by default. Marketing-intelligence tooling is used for long sessions, and the 3D field only reads well against darkness.

```css
:root {
  /* Base */
  --bv-void:        #05060f;   /* page background */
  --bv-deep:        #0a0d1c;   /* raised surface */
  --bv-surface:     #111528;   /* card */
  --bv-border:      #1e2440;   /* hairline */

  /* Brand */
  --bv-primary:     #5eead4;   /* aqua — retrieval, memory, "known" */
  --bv-secondary:   #8b7cf6;   /* violet — generation, creative, "made" */
  --bv-accent:      #fbbf24;   /* amber — evidence, verified, "proven" */
  --bv-danger:      #fb7185;   /* rose — risk, blocked, restricted */

  /* Text */
  --bv-text:        #eef1f8;
  --bv-text-muted:  #9aa3bd;
  --bv-text-faint:  #5c6484;

  /* Effects */
  --bv-glass:       rgba(255,255,255,0.045);
  --bv-glass-edge:  rgba(255,255,255,0.10);
  --bv-glow:        0 0 48px rgba(94,234,212,0.18);
}
```

**Semantic rule — colour carries meaning in this product, so never use it decoratively:**

| Colour | Means | Used for |
|---|---|---|
| Aqua | Retrieved from memory | D0 badges, memory hits, cost savings |
| Violet | Newly generated | Creative output, agent activity |
| Amber | Verified by evidence | Source citations, verified claims |
| Rose | Blocked or risky | Restricted claims, failed validation, cap breaches |

A light theme is defined in `tokens.css` for the report export only. The application and marketing site are dark.

---

## 4. Typography

| Role | Face | Weight | Size | Tracking |
|---|---|---|---|---|
| Display | Instrument Serif | 400 | `clamp(2.75rem, 7vw, 5.5rem)` | `-0.02em` |
| Heading | Inter | 600 | `clamp(1.5rem, 3vw, 2.25rem)` | `-0.015em` |
| Body | Inter | 400 | `1.0625rem` / 1.65 | `0` |
| UI | Inter | 500 | `0.875rem` | `0.01em` |
| Mono | JetBrains Mono | 400 | `0.8125rem` | `0` |

A serif display against a geometric sans is deliberate: it separates *the argument* from *the interface*, and keeps an AI product from looking like every other AI product. Numbers in metric displays use `font-variant-numeric: tabular-nums`.

---

## 5. Space, radius, elevation

Spacing is a 4px scale: `4 8 12 16 24 32 48 64 96 128`.

| Token | Radius | Use |
|---|---|---|
| `--r-sm` | 8px | Inputs, chips |
| `--r-md` | 14px | Cards |
| `--r-lg` | 22px | Panels, modals |
| `--r-full` | 999px | Pills, avatars |

Elevation comes from **border luminance and backdrop blur**, not from drop shadows. Shadows read as grey smudges over a 3D canvas.

```css
.glass {
  background: var(--bv-glass);
  border: 1px solid var(--bv-glass-edge);
  backdrop-filter: blur(20px) saturate(140%);
  border-radius: var(--r-lg);
}
```

---

## 6. Motion

| Token | Duration | Easing | Use |
|---|---|---|---|
| `--m-fast` | 150ms | `cubic-bezier(.4,0,.2,1)` | Hover, focus |
| `--m-base` | 280ms | `cubic-bezier(.22,1,.36,1)` | Enter, expand |
| `--m-slow` | 650ms | `cubic-bezier(.16,1,.3,1)` | Panels, step transitions |
| `--m-ambient` | 8–20s | linear | 3D drift, orbits |

Rules:

1. Motion must mean something. Drift signals life; a pulse signals retrieval; a step slide signals pipeline progress. Nothing animates for decoration.
2. **`prefers-reduced-motion: reduce` stops all ambient animation and freezes the 3D scene at a composed frame.** Not "reduces" — stops. This is requirement N10.
3. Never animate `width`, `height`, `top` or `left`. `transform` and `opacity` only.
4. Entry animations never delay interactivity. Content is clickable before it finishes arriving.

---

## 7. The 3D hero — implementation spec

### 7.1 Scene

| Element | Spec |
|---|---|
| Renderer | WebGL, `antialias: true`, `powerPreference: 'high-performance'`, DPR capped at `min(devicePixelRatio, 2)` |
| Camera | Perspective, FOV 60, positioned `z ≈ 15`, gentle parallax toward the pointer (max ±1.2 units, damped) |
| Memory nodes | 900 desktop / 350 mobile. `BufferGeometry` + `ShaderMaterial`, additive blending, depth-write off. Radial distribution, denser at the centre. |
| Filaments | ~260 `LineSegments`, nearest-neighbour pairs under a distance threshold, opacity by inverse distance |
| Brand core | Icosahedron wireframe, radius 1.6, slow rotation on two axes |
| Agent orbits | 7 small emissive points on inclined elliptical paths, independent periods (no synchronised rotation — it reads as mechanical) |
| Data motes | 400 fine particles on a slow noise-driven current |
| Retrieval pulse | Every 6s, a radius value expands 0 → 14 over 2.2s; nodes within a narrow band of that radius brighten, then decay |
| Fog | Exponential, matched to `--bv-void`, so the field fades rather than ending |

### 7.2 Performance budget

| Budget | Limit |
|---|---|
| Draw calls | ≤ 12 |
| Triangles | ≤ 60k |
| Hero JS, gzipped | ≤ 180 KB |
| Frame time, mid laptop | ≤ 16.6 ms |
| Frame time, mid mobile | ≤ 33 ms (30 fps acceptable) |
| Time to first frame | ≤ 1.2 s |

### 7.3 Degradation ladder

```
WebGL2 + desktop + no reduced-motion  → full scene, 900 nodes
WebGL + mobile                        → 350 nodes, no filaments, no motes
prefers-reduced-motion                → scene renders one static frame, no loop
WebGL unavailable                     → CSS gradient-and-dot fallback
JS disabled                           → static background, full page content
```

The page must be fully readable and usable at every rung. **Test the bottom rung before shipping** — graceful degradation that was never run is not degradation.

### 7.4 Rules

- All Three.js inside a `'use client'` component, dynamically imported with `ssr: false`
- Dispose geometries, materials and the renderer on unmount — a leaked WebGL context will crash a soft-navigated SPA
- Pause the render loop when the canvas leaves the viewport (`IntersectionObserver`) and on `visibilitychange`
- The canvas is `aria-hidden="true"` and carries no information available nowhere else

---

## 8. Components

| Component | Notes |
|---|---|
| `PipelineStepper` | The 15 steps, live via SSE. Each step: idle → active → done → skipped. Skipped steps stay visible and greyed — showing what was *not* done is the product's argument. |
| `MemoryCard` | Content, `M` score, component breakdown bar, provenance link, freshness age |
| `DepthBadge` | D0 aqua / D1 violet / D2 amber, with the `R` score on hover |
| `ClaimChip` | Verified amber / Deduced violet / Restricted rose, source on click |
| `CostMeter` | Spend against the $2.50 cap. Turns rose past 80%. |
| `AgentGrid` | 7 agents; selected ones lit, unselected dimmed with the reason on hover |
| `EvidencePanel` | Sources with authority score and fetch date |
| `CampaignReport` | Final output; print stylesheet for PDF export |
| `ApprovalBar` | Approve / revise / reject, with rounds remaining |

### 8.1 The pipeline stepper is the demo

More than any other screen, `PipelineStepper` is what a panel will remember. It must show, live: which step is running, which agents were chosen and why, which depth was selected and why, memory hits with scores, and cost accumulating in real time. Build it well and build it early (`Phases.md` Phase 1).

---

## 9. Page structure — marketing site

1. **Hero** — 3D field, headline, one-line subhead, two actions
2. **The problem** — the four failures, with the paper's figures
3. **How it works** — the 15 steps as a visual flow
4. **Adaptive intelligence** — D0/D1/D2, memory reuse bands, model routing
5. **The seven agents** — grid with roles
6. **Evidence and trust** — claim types, validation gates, anti-patterns blocked
7. **Results** — ARR / SRR / CR / LR. *Show real measured numbers or label them as targets. Never present a target as a result.*
8. **Research** — the paper, the authors, the institution
9. **Footer**

---

## 10. Accessibility

| Requirement | Standard |
|---|---|
| Contrast | 4.5:1 body, 3:1 large text — verified against `--bv-void`, not against the 3D canvas |
| Focus | Visible 2px aqua ring on every interactive element; never `outline: none` without a replacement |
| Keyboard | Full operation without a pointer; logical tab order; skip link to main |
| Screen reader | Canvas `aria-hidden`; pipeline progress announced via `aria-live="polite"` |
| Motion | `prefers-reduced-motion` honoured absolutely |
| Targets | ≥ 44×44 px |
| Semantics | Real landmarks and heading order; never a `div` where a `button` belongs |

---

## 11. Content voice

Plain, specific, unhyped. The product's claim is that it refuses to overstate — the copy must behave the same way.

| Write | Not |
|---|---|
| "Reuses what it already knows, so the second campaign costs less." | "Revolutionary AI-powered marketing synergy." |
| "Every claim links to its source." | "Trustworthy by design." |
| "Caps spend at $2.50 per campaign." | "Cost-efficient at scale." |

Numbers from the research are attributed inline. A number with no source does not ship.
