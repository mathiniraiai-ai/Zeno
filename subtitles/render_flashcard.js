#!/usr/bin/env node
'use strict';

/**
 * Zeno Pro — Bilingual Flashcard Graphic Renderer (`render_flashcard.js`).
 *
 * Uses bundled `node-canvas` (Cairo + Pango/HarfBuzz) with curated static TTFs
 * registered ONCE per file path (zero variable `fvar` fonts, zero duplicate
 * weight aliasing) to render broadcast-grade bilingual English + Indic
 * flashcard PNGs across all 9 bundled Indic scripts.
 *
 * Usage:
 *   node subtitles/render_flashcard.js --batch <manifest.json>
 *   node subtitles/render_flashcard.js <manifest.json>
 *
 * Manifest format (JSON array or `{ "cards": [...] }`):
 *   [
 *     {
 *       "en": "Trust me",
 *       "native": "ನನ್ನನ್ನು ನಂಬು",
 *       "width": 660,
 *       "height": 290,
 *       "style": "clean",
 *       "compact": false,
 *       "out": "/abs/path/to/card.png"
 *     }
 *   ]
 */

const fs = require('fs');
const path = require('path');
const { performance } = require('perf_hooks');
const canvasPkg = require('canvas');

const { createCanvas, registerFont } = canvasPkg;
const PNG_FILTER_NONE =
  canvasPkg.Canvas && canvasPkg.Canvas.PNG_FILTER_NONE !== undefined
    ? canvasPkg.Canvas.PNG_FILTER_NONE
    : canvasPkg.PNG_FILTER_NONE;
const PNG_OPTS =
  PNG_FILTER_NONE !== undefined
    ? { compressionLevel: 1, filters: PNG_FILTER_NONE }
    : { compressionLevel: 1 };

const HERE = __dirname;
const FONTS_DIR = path.join(HERE, 'fonts');

/**
 * Static TTF registry (registered ONCE per path — NO variable `fvar` fonts and
 * NO duplicate path aliasing across weights).
 *
 * Registers curated defaults plus all static Google Fonts declared in
 * `subtitles/fonts.json` so both default flashcards and custom-font cards
 * (`spec.font` / `spec.enFont` / `spec.nativeFont`) render crisply across
 * English and all 9 Indian languages (including static bold Gurmukhi).
 */
const CURATED_STATIC_FONTS = [
  // Display / Latin — Baloo Tamma 2 (static 600/700/800)
  { file: 'BalooTamma2-600.ttf', family: 'Baloo Tamma 2', weight: '600' },
  { file: 'BalooTamma2-700.ttf', family: 'Baloo Tamma 2', weight: '700' },
  { file: 'BalooTamma2-800.ttf', family: 'Baloo Tamma 2', weight: '800' },

  // Geometric Latin — Montserrat (static 400..800)
  { file: 'Montserrat-400.ttf', family: 'Montserrat', weight: '400' },
  { file: 'Montserrat-500.ttf', family: 'Montserrat', weight: '500' },
  { file: 'Montserrat-600.ttf', family: 'Montserrat', weight: '600' },
  { file: 'Montserrat-700.ttf', family: 'Montserrat', weight: '700' },
  { file: 'Montserrat-800.ttf', family: 'Montserrat', weight: '800' },

  // UI Sans Latin — Inter (static 400/600/700)
  { file: 'Inter-400.ttf', family: 'Inter', weight: '400' },
  { file: 'Inter-600.ttf', family: 'Inter', weight: '600' },
  { file: 'Inter-700.ttf', family: 'Inter', weight: '700' },

  // Neutral Latin fallback — Noto Sans (static 400/700)
  { file: 'NotoSans-400.ttf', family: 'Noto Sans', weight: '400' },
  { file: 'NotoSans-700.ttf', family: 'Noto Sans', weight: '700' },

  // All 9 bundled Indic scripts (static TTFs only, registered ONCE each)
  { file: 'NotoSansKannada-400.ttf', family: 'Noto Sans Kannada', weight: '400' },
  { file: 'NotoSansKannada-700.ttf', family: 'Noto Sans Kannada', weight: '700' },

  { file: 'NotoSansTelugu-400.ttf', family: 'Noto Sans Telugu', weight: '400' },
  { file: 'NotoSansTelugu-700.ttf', family: 'Noto Sans Telugu', weight: '700' },

  { file: 'NotoSansDevanagari-400.ttf', family: 'Noto Sans Devanagari', weight: '400' },
  { file: 'NotoSansDevanagari-700.ttf', family: 'Noto Sans Devanagari', weight: '700' },

  { file: 'NotoSansMalayalam-400.ttf', family: 'Noto Sans Malayalam', weight: '400' },
  { file: 'NotoSansMalayalam-700.ttf', family: 'Noto Sans Malayalam', weight: '700' },

  { file: 'NotoSansTamil-400.ttf', family: 'Noto Sans Tamil', weight: '400' },
  { file: 'NotoSansTamil-700.ttf', family: 'Noto Sans Tamil', weight: '700' },

  { file: 'NotoSansBengali-400.ttf', family: 'Noto Sans Bengali', weight: '400' },
  { file: 'NotoSansBengali-700.ttf', family: 'Noto Sans Bengali', weight: '700' },

  { file: 'NotoSansGujarati-400.ttf', family: 'Noto Sans Gujarati', weight: '400' },
  { file: 'NotoSansGujarati-700.ttf', family: 'Noto Sans Gujarati', weight: '700' },

  // Gurmukhi (Punjabi): static 400 + static 700
  { file: 'NotoSansGurmukhi-400.ttf', family: 'Noto Sans Gurmukhi', weight: '400' },
  { file: 'NotoSansGurmukhi-Bold.ttf', family: 'Noto Sans Gurmukhi', weight: '700' },

  // Oriya (Odia)
  { file: 'NotoSansOriya-400.ttf', family: 'Noto Sans Oriya', weight: '400' },
  { file: 'NotoSansOriya-700.ttf', family: 'Noto Sans Oriya', weight: '700' },
];

const INDIC_SCRIPT_BLOCKS = [
  {
    re: /[\u0C80-\u0CFF]/,
    family: 'Noto Sans Kannada',
    winFallback: 'Tunga',
    key: 'kannada',
    supported: new Set(['noto sans kannada', 'anek kannada', 'noto serif kannada', 'tiro kannada', 'baloo tamma 2', 'nirmala ui', 'nirmala text', 'tunga']),
  },
  {
    re: /[\u0C00-\u0C7F]/,
    family: 'Noto Sans Telugu',
    winFallback: 'Gautami',
    key: 'telugu',
    supported: new Set(['noto sans telugu', 'hind guntur', 'anek telugu', 'noto serif telugu', 'baloo tamma 2', 'ramabhadra', 'gidugu', 'nirmala ui', 'nirmala text', 'gautami']),
  },
  {
    re: /[\u0900-\u097F]/,
    family: 'Noto Sans Devanagari',
    winFallback: 'Mangal',
    key: 'devanagari',
    supported: new Set(['noto sans devanagari', 'hind', 'mukta', 'teko', 'anek devanagari', 'baloo 2', 'noto serif devanagari', 'kalam', 'modak', 'rozha one', 'nirmala ui', 'nirmala text', 'mangal']),
  },
  {
    re: /[\u0D00-\u0D7F]/,
    family: 'Noto Sans Malayalam',
    winFallback: 'Kartika',
    key: 'malayalam',
    supported: new Set(['noto sans malayalam', 'manjari', 'anek malayalam', 'noto serif malayalam', 'baloo chettan 2', 'chilanka', 'nirmala ui', 'nirmala text', 'kartika']),
  },
  {
    re: /[\u0B80-\u0BFF]/,
    family: 'Noto Sans Tamil',
    winFallback: 'Latha',
    key: 'tamil',
    supported: new Set(['noto sans tamil', 'hind madurai', 'mukta malar', 'anek tamil', 'baloo thambi 2', 'catamaran', 'coiny', 'kavivanar', 'nirmala ui', 'nirmala text', 'latha']),
  },
  {
    re: /[\u0980-\u09FF]/,
    family: 'Noto Sans Bengali',
    winFallback: 'Vrinda',
    key: 'bengali',
    supported: new Set(['noto sans bengali', 'hind siliguri', 'anek bangla', 'baloo da 2', 'noto serif bengali', 'atma', 'galada', 'nirmala ui', 'nirmala text', 'vrinda']),
  },
  {
    re: /[\u0A80-\u0AFF]/,
    family: 'Noto Sans Gujarati',
    winFallback: 'Shruti',
    key: 'gujarati',
    supported: new Set(['noto sans gujarati', 'hind vadodara', 'mukta vaani', 'anek gujarati', 'baloo bhai 2', 'mogra', 'nirmala ui', 'nirmala text', 'shruti']),
  },
  {
    re: /[\u0A00-\u0A7F]/,
    family: 'Noto Sans Gurmukhi',
    winFallback: 'Raavi',
    key: 'gurmukhi',
    supported: new Set(['noto sans gurmukhi', 'mukta mahee', 'anek gurmukhi', 'baloo paaji 2', 'nirmala ui', 'nirmala text', 'raavi']),
  },
  {
    re: /[\u0B00-\u0B7F]/,
    family: 'Noto Sans Oriya',
    winFallback: 'Kalinga',
    key: 'oriya',
    supported: new Set(['noto sans oriya', 'baloo bhaina 2', 'anek odia', 'noto serif oriya', 'nirmala ui', 'nirmala text', 'kalinga']),
  },
];

const GURMUKHI_RE = /[\u0A00-\u0A7F]/;

function isVariableFont(buf) {
  if (!buf || buf.length < 12) return false;
  const numTables = buf.readUInt16BE(4);
  for (let i = 0; i < numTables; i++) {
    const off = 12 + i * 16;
    if (off + 4 > buf.length) break;
    if (buf.toString('ascii', off, off + 4) === 'fvar') return true;
  }
  return false;
}

let _fontsRegistered = false;
let _hasStaticGurmukhiBold = false;
const _registeredFamilies = new Map();

function registerCuratedFontsOnce() {
  if (_fontsRegistered) return 0;
  const t0 = performance.now();
  const seen = new Set();
  const hdrBuf = Buffer.alloc(4096);

  let allDefs = [...CURATED_STATIC_FONTS];
  try {
    const jsonPath = path.join(HERE, 'fonts.json');
    if (fs.existsSync(jsonPath)) {
      const parsed = JSON.parse(fs.readFileSync(jsonPath, 'utf8'));
      if (Array.isArray(parsed)) {
        for (const f of parsed) {
          if (f && f.file && f.family) {
            allDefs.push({ file: f.file, family: f.family, weight: String(f.weight || 400) });
          }
        }
      }
    }
  } catch (_err) {
    // Curated list remains intact
  }

  const WEIGHT_REG_ORDER = { 400: 0, 700: 1, 800: 2, 900: 3, 600: 4, 500: 5, 300: 6 };
  allDefs.sort(
    (a, b) => (WEIGHT_REG_ORDER[Number(a.weight)] ?? 9) - (WEIGHT_REG_ORDER[Number(b.weight)] ?? 9)
  );

  for (const def of allDefs) {
    const fullPath = path.join(FONTS_DIR, def.file);
    if (seen.has(fullPath) || !fs.existsSync(fullPath)) continue;
    let fd;
    let isVar = false;
    try {
      fd = fs.openSync(fullPath, 'r');
      const n = fs.readSync(fd, hdrBuf, 0, hdrBuf.length, 0);
      isVar = isVariableFont(hdrBuf.subarray(0, n));
    } catch (_err) {
      continue;
    } finally {
      if (fd !== undefined) { try { fs.closeSync(fd); } catch (_e) {} }
    }
    if (isVar) continue;
    seen.add(fullPath);
    try {
      registerFont(fullPath, { family: def.family, weight: String(def.weight) });
      const key = String(def.family).trim().toLowerCase();
      let rec = _registeredFamilies.get(key);
      if (!rec) {
        rec = { family: def.family, weights: new Set() };
        _registeredFamilies.set(key, rec);
      }
      rec.weights.add(Number(def.weight) || 400);
      if (key === 'noto sans gurmukhi' && Number(def.weight) >= 700) {
        _hasStaticGurmukhiBold = true;
      }
    } catch (_err) {
      // Ignore optional missing/unreadable font gracefully
    }
  }
  _fontsRegistered = true;
  return performance.now() - t0;
}

function resolveFlashcardFamily(name) {
  const raw = String(name || '').trim();
  if (!raw) return '';
  const rec = _registeredFamilies.get(raw.toLowerCase());
  return rec ? rec.family : raw;
}

function snapFlashcardWeight(family, requestedWeight) {
  const req = Number(requestedWeight) || 700;
  const rec = _registeredFamilies.get(String(family || '').trim().toLowerCase());
  if (!rec || rec.weights.size === 0) return req;
  if (rec.weights.has(req)) return req;
  const sorted = Array.from(rec.weights).sort((a, b) => a - b);
  let best = sorted[0];
  let bestDiff = Math.abs(best - req);
  for (const w of sorted) {
    const diff = Math.abs(w - req);
    if (diff < bestDiff || (diff === bestDiff && req >= 600 && w > best)) {
      best = w;
      bestDiff = diff;
    }
  }
  return best;
}

function detectIndicInfo(text, customFamily = '') {
  const indicFams = [];
  const winFallbacks = [];
  const s = String(text || '');
  const matched = [];
  for (const block of INDIC_SCRIPT_BLOCKS) {
    if (block.re.test(s)) {
      matched.push(block);
      if (!indicFams.includes(block.family)) indicFams.push(block.family);
      if (block.winFallback && !winFallbacks.includes(block.winFallback)) {
        winFallbacks.push(block.winFallback);
      }
    }
  }
  const famLow = String(customFamily || '').trim().toLowerCase();
  const customSupportsIndic = Boolean(famLow) && matched.length > 0 && matched.every((b) => b.supported.has(famLow));
  return { indicFams, winFallbacks, customSupportsIndic };
}

function quoteFamily(name) {
  return `"${name}"`;
}

function buildFontSpec(text, fontSize, isNativeLine, customFamily = '', targetWeightOverride = null) {
  const customFam = resolveFlashcardFamily(customFamily);
  const { indicFams, winFallbacks, customSupportsIndic } = detectIndicInfo(text, customFam);
  const hasIndic = indicFams.length > 0;
  const hasGurmukhi = GURMUKHI_RE.test(text);
  const needsSynthGurmukhi = hasGurmukhi && !_hasStaticGurmukhiBold;

  if (hasIndic) {
    const targetWeight = needsSynthGurmukhi ? 400 : 700;
    let weight = (customFam && customSupportsIndic) ? snapFlashcardWeight(customFam, targetWeight) : targetWeight;
    if (Number(weight) >= 700) weight = 700;

    const indicOrdered = [...indicFams, 'Nirmala UI', 'Nirmala Text', ...winFallbacks];
    const tail = isNativeLine
      ? ['Montserrat', 'Noto Sans']
      : ['Noto Sans', 'Montserrat', 'Inter'];
    const stack = (customFam && customSupportsIndic)
      ? [customFam, ...indicOrdered, ...tail]
      : (customFam ? [...indicOrdered, customFam, ...tail] : [...indicOrdered, ...tail]);
    const dedup = Array.from(new Set(stack));
    return {
      css: `${weight} ${fontSize}px ${dedup.map(quoteFamily).join(', ')}, sans-serif`,
      hasIndic: true,
      emboldenGurmukhi: needsSynthGurmukhi && !(customFam && customSupportsIndic),
    };
  }

  if (isNativeLine) {
    const weight = customFam ? snapFlashcardWeight(customFam, Number(targetWeightOverride) || 700) : 700;
    const stack = customFam
      ? [customFam, 'Montserrat', 'Nirmala UI', 'Nirmala Text', 'Noto Sans']
      : ['Montserrat', 'Nirmala UI', 'Nirmala Text', 'Noto Sans'];
    const dedup = Array.from(new Set(stack));
    return {
      css: `${weight} ${fontSize}px ${dedup.map(quoteFamily).join(', ')}, sans-serif`,
      hasIndic: false,
      emboldenGurmukhi: false,
    };
  }

  // English / top line: align cleanly with Zeno subtitle typography (Montserrat default,
  // or Noto Sans 700 when paired with an Indic native line and no explicit enFont).
  const reqEnWeight = Number(targetWeightOverride) || 800;
  if (customFam) {
    const weight = snapFlashcardWeight(customFam, reqEnWeight);
    const dedup = Array.from(new Set([customFam, 'Montserrat', 'Inter', 'Noto Sans']));
    return {
      css: `${weight} ${fontSize}px ${dedup.map(quoteFamily).join(', ')}, sans-serif`,
      hasIndic: false,
      emboldenGurmukhi: false,
    };
  }

  return {
    css: `${reqEnWeight} ${fontSize}px "Montserrat", "Inter", "Noto Sans", sans-serif`,
    hasIndic: false,
    emboldenGurmukhi: false,
  };
}

function roundedRectPath(ctx, x, y, w, h, r) {
  const rad = Math.max(0, Math.min(r, w / 2, h / 2));
  ctx.beginPath();
  ctx.moveTo(x + rad, y);
  ctx.arcTo(x + w, y, x + w, y + h, rad);
  ctx.arcTo(x + w, y + h, x, y + h, rad);
  ctx.arcTo(x, y + h, x, y, rad);
  ctx.arcTo(x, y, x + w, y, rad);
  ctx.closePath();
}

function computeFrameInnerBounds(W, H, style, compact) {
  const styleNorm = String(style || 'clean').trim().toLowerCase();
  const isMinimal = styleNorm === 'minimal' || styleNorm === 'pill' || styleNorm === 'flat';

  const baseH = compact ? 200 : 290;
  const scale = Math.max(0.55, Math.min(1.85, Math.min(W / 660, H / baseH)));

  const shadowBlur = Math.round((compact ? 9 : 13) * scale);
  const shadowOffsetY = Math.round((compact ? 4 : 6) * scale);
  const padX = Math.max(8, shadowBlur + 2);
  const padTop = Math.max(4, Math.round(shadowBlur - shadowOffsetY + 2));
  const padBot = Math.max(10, shadowBlur + shadowOffsetY + 2);

  const cx = padX;
  const cy = padTop;
  const cw = Math.max(32, W - padX * 2);
  const ch = Math.max(32, H - padTop - padBot);

  if (isMinimal) {
    const borderW = Math.max(2, Math.round((compact ? 3.5 : 4) * scale));
    const inset = borderW + Math.round(2 * scale);
    return {
      ix: cx + inset,
      iy: cy + inset,
      iw: cw - inset * 2,
      ih: ch - inset * 2,
      scale,
    };
  }

  const frameW = Math.max(7, Math.round((compact ? 9 : 13) * scale));
  const trimW = Math.max(3, Math.round((compact ? 3.5 : 4.5) * scale));
  const ix = cx + frameW + trimW;
  const iy = cy + frameW + trimW;
  const iw = cw - (frameW + trimW) * 2;
  const ih = ch - (frameW + trimW) * 2;
  return { ix, iy, iw, ih, scale };
}

/**
 * Draw the outer chrome & inner `#FFFFFF` text box, returning inner safe bounds.
 */
function drawCardFrame(ctx, W, H, style, compact) {
  const styleNorm = String(style || 'clean').trim().toLowerCase();
  const isMinimal = styleNorm === 'minimal' || styleNorm === 'pill' || styleNorm === 'flat';

  const baseH = compact ? 200 : 290;
  const scale = Math.max(0.55, Math.min(1.85, Math.min(W / 660, H / baseH)));

  const shadowBlur = Math.round((compact ? 9 : 13) * scale);
  const shadowOffsetY = Math.round((compact ? 4 : 6) * scale);
  const padX = Math.max(8, shadowBlur + 2);
  const padTop = Math.max(4, Math.round(shadowBlur - shadowOffsetY + 2));
  const padBot = Math.max(10, shadowBlur + shadowOffsetY + 2);

  const cx = padX;
  const cy = padTop;
  const cw = Math.max(32, W - padX * 2);
  const ch = Math.max(32, H - padTop - padBot);

  if (isMinimal) {
    const outerR = Math.round((compact ? 20 : 24) * scale);
    const borderW = Math.max(2, Math.round((compact ? 3.5 : 4) * scale));

    // Drop shadow + pure white fill
    ctx.save();
    ctx.shadowColor = 'rgba(0, 0, 0, 0.22)';
    ctx.shadowBlur = shadowBlur;
    ctx.shadowOffsetX = 0;
    ctx.shadowOffsetY = shadowOffsetY;
    roundedRectPath(ctx, cx, cy, cw, ch, outerR);
    ctx.fillStyle = '#FFFFFF';
    ctx.fill();
    ctx.restore();

    // Crisp #1E293B border stroke
    ctx.save();
    const halfB = borderW / 2;
    roundedRectPath(
      ctx,
      cx + halfB,
      cy + halfB,
      cw - borderW,
      ch - borderW,
      Math.max(4, outerR - halfB)
    );
    ctx.lineWidth = borderW;
    ctx.strokeStyle = '#1E293B';
    ctx.stroke();
    ctx.restore();

    const inset = borderW + Math.round(2 * scale);
    return {
      ix: cx + inset,
      iy: cy + inset,
      iw: cw - inset * 2,
      ih: ch - inset * 2,
      scale,
    };
  }

  // Default: style="clean" — warm honey-amber signboard frame modeled on the
  // approved reference look, with zero cartoon mascot/faces.
  const outerR = Math.round((compact ? 18 : 22) * scale);
  const frameW = Math.max(7, Math.round((compact ? 9 : 13) * scale));
  const trimW = Math.max(3, Math.round((compact ? 3.5 : 4.5) * scale));
  const outlineW = Math.max(1.5, Math.round(2 * scale));

  // 1) Outer rounded signboard with subtle drop shadow & warm honey-amber gradient
  ctx.save();
  ctx.shadowColor = 'rgba(0, 0, 0, 0.22)';
  ctx.shadowBlur = shadowBlur;
  ctx.shadowOffsetX = 0;
  ctx.shadowOffsetY = shadowOffsetY;
  roundedRectPath(ctx, cx, cy, cw, ch, outerR);
  const woodGrad = ctx.createLinearGradient(cx, cy, cx, cy + ch);
  woodGrad.addColorStop(0.0, '#E9A34E');
  woodGrad.addColorStop(0.45, '#D98A38');
  woodGrad.addColorStop(1.0, '#C47425');
  ctx.fillStyle = woodGrad;
  ctx.fill();
  ctx.restore();

  // Subtle sunlit inner top bevel highlight inside the amber rail
  ctx.save();
  roundedRectPath(ctx, cx + outlineW, cy + outlineW, cw - outlineW * 2, ch - outlineW * 2, Math.max(4, outerR - outlineW));
  ctx.clip();
  ctx.beginPath();
  ctx.moveTo(cx + outlineW, cy + outlineW + 0.75);
  ctx.lineTo(cx + cw - outlineW, cy + outlineW + 0.75);
  ctx.lineWidth = Math.max(1.2, 1.8 * scale);
  ctx.strokeStyle = 'rgba(255, 238, 198, 0.60)';
  ctx.stroke();
  ctx.restore();

  // Crisp #5E3714 2px outer outline
  ctx.save();
  const halfOut = outlineW / 2;
  roundedRectPath(
    ctx,
    cx + halfOut,
    cy + halfOut,
    cw - outlineW,
    ch - outlineW,
    Math.max(4, outerR - halfOut)
  );
  ctx.lineWidth = outlineW;
  ctx.strokeStyle = '#5E3714';
  ctx.stroke();
  ctx.restore();

  // 2) Clean 4px inner #2563EB / #FFFFFF / #DC2626 symmetric accent trim
  const tx = cx + frameW;
  const ty = cy + frameW;
  const tw = cw - frameW * 2;
  const th = ch - frameW * 2;
  const trimR = Math.max(7, outerR - frameW + Math.round(2 * scale));

  ctx.save();
  roundedRectPath(ctx, tx, ty, tw, th, trimR);
  ctx.clip();

  // Base white for trim ring
  ctx.fillStyle = '#FFFFFF';
  ctx.fillRect(tx, ty, tw, th);

  const stripBandH = trimW + 2;
  const stripBandW = trimW + 2;

  // Top & bottom symmetric segments: Blue corners, White gap, Red bars, White center
  const hSegs = [
    [0.0, 0.14, '#2563EB'],
    [0.24, 0.41, '#DC2626'],
    [0.59, 0.76, '#DC2626'],
    [0.86, 1.0, '#2563EB'],
  ];
  for (const [f0, f1, color] of hSegs) {
    const sx = tx + tw * f0;
    const sw = tw * (f1 - f0);
    ctx.fillStyle = color;
    ctx.fillRect(sx, ty - 1, sw, stripBandH);
    ctx.fillRect(sx, ty + th - stripBandH + 1, sw, stripBandH);
  }

  // Left & right symmetric segments: Blue corners, White gap, centered Red bar
  const vSegs = [
    [0.0, 0.24, '#2563EB'],
    [0.37, 0.63, '#DC2626'],
    [0.76, 1.0, '#2563EB'],
  ];
  for (const [f0, f1, color] of vSegs) {
    const sy = ty + th * f0;
    const sh = th * (f1 - f0);
    ctx.fillStyle = color;
    ctx.fillRect(tx - 1, sy, stripBandW, sh);
    ctx.fillRect(tx + tw - stripBandW + 1, sy, stripBandW, sh);
  }
  ctx.restore();

  // Crisp subtle #5E3714 hairline separating amber frame and inner trim
  ctx.save();
  roundedRectPath(ctx, tx, ty, tw, th, trimR);
  ctx.lineWidth = Math.max(1, Math.round(1.2 * scale));
  ctx.strokeStyle = 'rgba(94, 55, 20, 0.85)';
  ctx.stroke();
  ctx.restore();

  // 3) Pure opaque #FFFFFF inner text canvas
  const ix = tx + trimW;
  const iy = ty + trimW;
  const iw = tw - trimW * 2;
  const ih = th - trimW * 2;
  const innerR = Math.max(4, trimR - trimW);

  ctx.save();
  roundedRectPath(ctx, ix, iy, iw, ih, innerR);
  ctx.fillStyle = '#FFFFFF';
  ctx.fill();
  ctx.lineWidth = Math.max(1, 1 * scale);
  ctx.strokeStyle = 'rgba(148, 163, 184, 0.45)';
  ctx.stroke();
  ctx.restore();

  return { ix, iy, iw, ih, scale };
}

function measureStyledLine(ctx, text, fontSize, isNativeLine, customFamily = '', targetWeight = null) {
  const spec = buildFontSpec(text, fontSize, isNativeLine, customFamily, targetWeight);
  ctx.font = spec.css;
  ctx.textBaseline = 'alphabetic';
  ctx.textAlign = 'left';

  const m = ctx.measureText(text);
  const advW = Number.isFinite(m.width) ? m.width : 0;
  const bboxLeft = Number.isFinite(m.actualBoundingBoxLeft) ? m.actualBoundingBoxLeft : 0;
  const bboxRight =
    Number.isFinite(m.actualBoundingBoxRight) && m.actualBoundingBoxRight > 0
      ? m.actualBoundingBoxRight
      : advW;
  const strokePad = spec.emboldenGurmukhi ? Math.max(1.2, fontSize * 0.035) : 0;
  const inkW = Math.max(advW, bboxLeft + bboxRight) + strokePad * 2;

  const rawAsc =
    Number.isFinite(m.actualBoundingBoxAscent) && m.actualBoundingBoxAscent > 0
      ? m.actualBoundingBoxAscent
      : fontSize * 0.78;
  const rawDesc =
    Number.isFinite(m.actualBoundingBoxDescent) && m.actualBoundingBoxDescent > 0
      ? m.actualBoundingBoxDescent
      : 0;

  // Guarantee >= 0.38em bottom clearance below baseline for Indic conjunct descenders
  const safeDescent = spec.hasIndic
    ? Math.max(rawDesc + strokePad, fontSize * 0.38)
    : Math.max(rawDesc + strokePad, fontSize * 0.18);

  return {
    text,
    fontSize,
    css: spec.css,
    hasIndic: spec.hasIndic,
    emboldenGurmukhi: spec.emboldenGurmukhi,
    width: advW,
    bboxLeft,
    bboxRight,
    inkW,
    ascent: rawAsc + strokePad,
    descent: rawDesc + strokePad,
    safeDescent,
  };
}

function fitLineToWidth(ctx, text, startSize, maxTextW, isNativeLine, minSize = 18, customFamily = '', targetWeight = null) {
  let size = Math.max(minSize, Math.round(startSize));
  let m = measureStyledLine(ctx, text, size, isNativeLine, customFamily, targetWeight);
  while (m.inkW > maxTextW && size > minSize) {
    size = Math.max(minSize, size - 2);
    m = measureStyledLine(ctx, text, size, isNativeLine, customFamily, targetWeight);
  }
  return m;
}

function drawMeasuredLine(ctx, measured, centerX, baselineY, fillColor) {
  ctx.save();
  ctx.font = measured.css;
  ctx.textBaseline = 'alphabetic';
  ctx.textAlign = 'left';

  const visualHalf =
    measured.bboxRight > 0 ? (measured.bboxRight - measured.bboxLeft) / 2 : measured.width / 2;
  const drawX = Math.round(centerX - visualHalf);
  const drawY = Math.round(baselineY);

  if (measured.emboldenGurmukhi) {
    ctx.lineJoin = 'round';
    ctx.lineCap = 'round';
    ctx.miterLimit = 2;
    ctx.lineWidth = Math.max(1.2, measured.fontSize * 0.035);
    ctx.strokeStyle = fillColor;
    ctx.strokeText(measured.text, drawX, drawY);
  }

  ctx.fillStyle = fillColor;
  ctx.fillText(measured.text, drawX, drawY);
  ctx.restore();
}

function resolveCardTypographySpec(spec) {
  const en = String(spec.en || '').replace(/\s+/g, ' ').trim();
  const native = String(spec.native || '').replace(/\s+/g, ' ').trim();
  const W = Math.max(64, Math.round(Number(spec.width) || 660));
  const H = Math.max(48, Math.round(Number(spec.height) || 290));
  const style = String(spec.style || 'clean').trim().toLowerCase();
  const compact = Boolean(spec.compact);
  const rawEnFont = String(spec.enFont || spec.font || '').trim();
  const nativeFont = String(spec.nativeFont || spec.font || '').trim();
  const hasNativeIndic = /[\u0900-\u0D7F]/.test(native);
  const enFont = rawEnFont || (hasNativeIndic ? 'Noto Sans' : '');
  const enWeight = (!rawEnFont && hasNativeIndic) ? 700 : null;
  return { en, native, W, H, style, compact, enFont, enWeight, nativeFont, hasEn: en.length > 0, hasNative: native.length > 0 };
}

function fitCardLines(ctx, info, targetOverrides = null) {
  const { en, native, W, H, style, compact, enFont, enWeight, nativeFont, hasEn, hasNative } = info;
  const { ix, iy, iw, ih } = computeFrameInnerBounds(W, H, style, compact);
  if (!hasEn && !hasNative) {
    return { ix, iy, iw, ih, enSize: 0, natSize: 0 };
  }

  const padX = Math.max(16, Math.round(iw * (compact ? 0.052 : 0.064)));
  const padTop = Math.max(8, Math.round(ih * (compact ? 0.075 : 0.095)));
  const padBot = Math.max(8, Math.round(ih * (compact ? 0.075 : 0.095)));
  const maxTextW = Math.max(40, iw - padX * 2);
  const availH = Math.max(24, ih - padTop - padBot);
  const centerX = ix + iw / 2;

  if (hasEn && hasNative) {
    const rawStartEn = Math.min(
      Math.round(ih * (compact ? 0.305 : 0.285)),
      Math.round(iw * 0.115)
    );
    const rawStartNat = Math.min(
      Math.round(ih * (compact ? 0.295 : 0.275)),
      Math.round(iw * 0.112)
    );
    const startEn = (targetOverrides && targetOverrides.enTargetSz > 0)
      ? Math.min(rawStartEn, Math.round(targetOverrides.enTargetSz))
      : rawStartEn;
    const startNat = (targetOverrides && targetOverrides.natTargetSz > 0)
      ? Math.min(rawStartNat, Math.round(targetOverrides.natTargetSz))
      : rawStartNat;

    let enM = fitLineToWidth(ctx, en, startEn, maxTextW, false, 18, enFont, enWeight);
    let natM = fitLineToWidth(ctx, native, startNat, maxTextW, true, 18, nativeFont);

    const maxBalancedEn = Math.max(24, Math.round(natM.fontSize * 1.38));
    if (enM.fontSize > maxBalancedEn) {
      enM = fitLineToWidth(ctx, en, maxBalancedEn, maxTextW, false, 18, enFont, enWeight);
    }

    const gapRatio = compact ? 0.24 : 0.32;
    const minGap = compact ? 8 : 12;
    const calcGap = () => Math.max(minGap, Math.round(Math.min(enM.fontSize, natM.fontSize) * gapRatio));

    let gap = calcGap();
    let enBelow = Math.max(enM.descent, enM.fontSize * 0.14);
    let totalSafeH = enM.ascent + enBelow + gap + natM.ascent + natM.safeDescent;

    while (totalSafeH > availH && (enM.fontSize > 18 || natM.fontSize > 18)) {
      const nextEn = Math.max(18, enM.fontSize - 2);
      const nextNat = Math.max(18, natM.fontSize - 2);
      enM = fitLineToWidth(ctx, en, nextEn, maxTextW, false, 18, enFont, enWeight);
      natM = fitLineToWidth(ctx, native, nextNat, maxTextW, true, 18, nativeFont);
      gap = calcGap();
      enBelow = Math.max(enM.descent, enM.fontSize * 0.14);
      totalSafeH = enM.ascent + enBelow + gap + natM.ascent + natM.safeDescent;
    }

    return {
      mode: 'bilingual',
      ix, iy, iw, ih, padTop, padBot, centerX,
      enM, natM, gap, enBelow,
      startEn: rawStartEn, startNat: rawStartNat,
      enSize: enM.fontSize, natSize: natM.fontSize,
    };
  }

  const isNative = !hasEn;
  const text = isNative ? native : en;
  const lineFont = isNative ? nativeFont : enFont;
  const lineWeight = isNative ? null : enWeight;
  const rawStartSz = Math.min(
    Math.round(ih * (compact ? 0.42 : 0.36)),
    Math.round(iw * 0.13)
  );
  const overrideSz = isNative
    ? (targetOverrides && targetOverrides.natTargetSz)
    : (targetOverrides && targetOverrides.enTargetSz);
  const startSz = (overrideSz && overrideSz > 0)
    ? Math.min(rawStartSz, Math.round(overrideSz))
    : rawStartSz;

  let m = fitLineToWidth(ctx, text, startSz, maxTextW, isNative, 18, lineFont, lineWeight);
  while (m.ascent + m.safeDescent > availH && m.fontSize > 18) {
    m = fitLineToWidth(ctx, text, m.fontSize - 2, maxTextW, isNative, 18, lineFont, lineWeight);
  }
  return {
    mode: 'single',
    isNative,
    ix, iy, iw, ih, padTop, padBot, centerX,
    m,
    startEn: isNative ? 0 : rawStartSz,
    startNat: isNative ? rawStartSz : 0,
    enSize: isNative ? 0 : m.fontSize,
    natSize: isNative ? m.fontSize : 0,
  };
}

function renderCard(spec, targetOverrides = null) {
  const info = resolveCardTypographySpec(spec);
  const { W, H, style, compact, hasEn, hasNative } = info;

  const canvas = createCanvas(W, H);
  const ctx = canvas.getContext('2d');
  ctx.antialias = 'subpixel';

  drawCardFrame(ctx, W, H, style, compact);

  if (!hasEn && !hasNative) {
    return canvas;
  }

  const fit = fitCardLines(ctx, info, targetOverrides);
  const { ix, iy, iw, ih, padTop, padBot, centerX } = fit;

  if (fit.mode === 'bilingual') {
    const { enM, natM, gap, enBelow } = fit;
    // Visually center ink block while strictly enforcing >=0.38em bottom safety clearance
    const visualNatDesc = Math.max(natM.descent, natM.fontSize * 0.20);
    const visualBlockH = enM.ascent + enBelow + gap + natM.ascent + visualNatDesc;
    let blockTop = iy + (ih - visualBlockH) / 2;

    let enBaselineY = blockTop + enM.ascent;
    let natBaselineY = enBaselineY + enBelow + gap + natM.ascent;

    // Hard anti-clipping clamps inside inner white box
    const maxNatBaseline = iy + ih - padBot - natM.safeDescent;
    if (natBaselineY > maxNatBaseline) {
      const shiftUp = natBaselineY - maxNatBaseline;
      natBaselineY -= shiftUp;
      enBaselineY -= shiftUp;
    }
    const minEnBaseline = iy + padTop + enM.ascent;
    if (enBaselineY < minEnBaseline) {
      enBaselineY = minEnBaseline;
      natBaselineY = Math.min(maxNatBaseline, enBaselineY + enBelow + gap + natM.ascent);
    }

    drawMeasuredLine(ctx, enM, centerX, enBaselineY, '#0A0A0A');
    drawMeasuredLine(ctx, natM, centerX, natBaselineY, '#111827');
  } else {
    const { isNative, m } = fit;
    const color = isNative ? '#111827' : '#0A0A0A';
    const visDesc = isNative ? Math.max(m.descent, m.fontSize * 0.20) : m.descent;
    let baselineY = iy + ih / 2 + (m.ascent - visDesc) / 2;
    const maxBaseline = iy + ih - padBot - m.safeDescent;
    const minBaseline = iy + padTop + m.ascent;
    if (baselineY > maxBaseline) baselineY = maxBaseline;
    if (baselineY < minBaseline) baselineY = minBaseline;

    drawMeasuredLine(ctx, m, centerX, baselineY, color);
  }

  return canvas;
}

function parseArgs(argv) {
  let manifestPath = null;
  for (let i = 2; i < argv.length; i += 1) {
    const arg = argv[i];
    if (arg === '--batch' && i + 1 < argv.length) {
      manifestPath = argv[i + 1];
      i += 1;
    } else if (!arg.startsWith('-') && !manifestPath) {
      manifestPath = arg;
    }
  }
  return { manifestPath };
}

function main() {
  const tStart = performance.now();
  const { manifestPath } = parseArgs(process.argv);
  if (!manifestPath) {
    console.error('Usage: node subtitles/render_flashcard.js --batch <manifest.json>');
    process.exit(2);
  }

  const raw = JSON.parse(fs.readFileSync(manifestPath, 'utf8'));
  const specs = Array.isArray(raw) ? raw : Array.isArray(raw && raw.cards) ? raw.cards : [raw];

  const fontMs = registerCuratedFontsOnce();
  const tRenderStart = performance.now();
  const outputs = [];

  // Batch-uniform font size pre-pass grouped by (width, height, compact, hasEn, hasNative)
  const measureCanvas = createCanvas(64, 64);
  const measureCtx = measureCanvas.getContext('2d');
  const groupBuckets = new Map();
  for (let i = 0; i < specs.length; i += 1) {
    const spec = specs[i] || {};
    const info = resolveCardTypographySpec(spec);
    if (!info.hasEn && !info.hasNative) continue;
    const gKey = `${info.W}x${info.H}|${info.compact ? 1 : 0}|${info.hasEn ? 1 : 0}|${info.hasNative ? 1 : 0}|${info.enFont}|${info.nativeFont}`;
    let bucket = groupBuckets.get(gKey);
    if (!bucket) {
      bucket = [];
      groupBuckets.set(gKey, bucket);
    }
    bucket.push({ idx: i, info, fit: fitCardLines(measureCtx, info, null) });
  }

  const targetByIdx = new Map();
  const pickUniformTarget = (sizes, starts) => {
    const valid = sizes.filter((s) => s > 0).sort((a, b) => a - b);
    if (valid.length < 2) return 0;
    const medianSz = valid[Math.floor(valid.length / 2)];
    const outlierFloor = Math.max(24, Math.round(medianSz * 0.80));
    const normal = valid.filter((s) => s >= outlierFloor);
    const chosen = normal.length > 0 ? Math.min(...normal) : Math.max(24, medianSz);
    const maxStart = Math.max(...starts);
    return Math.max(24, Math.min(maxStart, chosen));
  };

  for (const bucket of groupBuckets.values()) {
    if (bucket.length < 2) continue;
    const enTargetSz = pickUniformTarget(
      bucket.map((b) => b.fit.enSize),
      bucket.map((b) => b.fit.startEn)
    );
    const natTargetSz = pickUniformTarget(
      bucket.map((b) => b.fit.natSize),
      bucket.map((b) => b.fit.startNat)
    );
    for (const b of bucket) {
      targetByIdx.set(b.idx, { enTargetSz, natTargetSz });
    }
  }

  for (let i = 0; i < specs.length; i += 1) {
    const spec = specs[i] || {};
    if (!spec.out) {
      throw new Error(`Manifest entry #${i} is missing required "out" path`);
    }
    const outPath = path.resolve(String(spec.out));
    fs.mkdirSync(path.dirname(outPath), { recursive: true });
    const canvas = renderCard(spec, targetByIdx.get(i) || null);
    const buf = canvas.toBuffer('image/png', PNG_OPTS);
    fs.writeFileSync(outPath, buf);
    outputs.push(outPath);
  }

  const renderMs = performance.now() - tRenderStart;
  const totalMs = performance.now() - tStart;
  process.stdout.write(
    JSON.stringify({
      ok: true,
      count: outputs.length,
      fontMs: Number(fontMs.toFixed(2)),
      renderMs: Number(renderMs.toFixed(2)),
      totalMs: Number(totalMs.toFixed(2)),
      outputs,
    }) + '\n'
  );
}

try {
  main();
} catch (err) {
  console.error((err && err.stack) || String(err));
  process.exit(1);
}
