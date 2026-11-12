/* ══════════════════════════════════════════════════════════════════════════
 * EditFlow AI — Client-side OffscreenCanvas PNG Renderer
 * (caption-renderer.js)
 *
 * Renders transparent caption PNG frames directly in the CEP panel browser
 * using OffscreenCanvas, then writes them to disk via Node.js fs (available
 * in CEP panels with --enable-nodejs). Eliminates the need for server-side
 * node-canvas rendering.
 *
 * Architecture cloned from Kalakar's OG/rG pipeline.
 * ══════════════════════════════════════════════════════════════════════════ */

const CaptionRenderer = (() => {
  // Node.js modules available in CEP
  const getFs = () => (typeof require !== 'undefined' ? require('fs') : (typeof window !== 'undefined' && window.require ? window.require('fs') : null));
  const getPath = () => (typeof require !== 'undefined' ? require('path') : (typeof window !== 'undefined' && window.require ? window.require('path') : null));
  const createCanvasNode = (typeof require !== 'undefined' || (typeof window !== 'undefined' && window.require)) ? (function() {
    try {
      const req = typeof require !== 'undefined' ? require : window.require;
      return req('canvas').createCanvas;
    } catch (e) { return null; }
  })() : null;

  // Module-scoped reusable OffscreenCanvas pool (eliminates per-frame GC churn)
  let _sharedCanvas = null;
  let _sharedCtx = null;
  let _sharedIsOffscreen = false;
  let _renderLock = Promise.resolve();

  function createRenderCanvas(width, height) {
    if (!_sharedCanvas) {
      if (typeof OffscreenCanvas !== 'undefined') {
        _sharedCanvas = new OffscreenCanvas(width, height);
        _sharedIsOffscreen = true;
      } else if (createCanvasNode) {
        _sharedCanvas = createCanvasNode(width, height);
        _sharedIsOffscreen = false;
      } else if (typeof document !== 'undefined' && document.createElement) {
        _sharedCanvas = document.createElement('canvas');
        _sharedCanvas.width = width;
        _sharedCanvas.height = height;
        _sharedIsOffscreen = false;
      } else {
        throw new Error('No canvas implementation available (OffscreenCanvas or node-canvas)');
      }
      _sharedCtx = _sharedCanvas.getContext('2d');
    } else if (_sharedCanvas.width !== width || _sharedCanvas.height !== height) {
      _sharedCanvas.width = width;
      _sharedCanvas.height = height;
    }
    return {
      canvas: _sharedCanvas,
      ctx: _sharedCtx,
      isOffscreen: _sharedIsOffscreen,
    };
  }

  let _pngFilterNone = undefined;
  try {
    const req = typeof require !== 'undefined' ? require : (typeof window !== 'undefined' && window.require ? window.require : null);
    if (req) {
      const canvasPkg = req('canvas');
      _pngFilterNone = canvasPkg && canvasPkg.Canvas ? canvasPkg.Canvas.PNG_FILTER_NONE : canvasPkg.PNG_FILTER_NONE;
    }
  } catch (e) {}

  async function canvasToBuffer(canvasObj) {
    if (canvasObj.isOffscreen || typeof canvasObj.canvas.convertToBlob === 'function') {
      const blob = await canvasObj.canvas.convertToBlob({ type: 'image/png' });
      return await blob.arrayBuffer();
    } else if (typeof canvasObj.canvas.toBuffer === 'function') {
      try {
        const opts = { compressionLevel: 1 };
        if (_pngFilterNone !== undefined) opts.filters = _pngFilterNone;
        return canvasObj.canvas.toBuffer('image/png', opts);
      } catch (e) {
        return canvasObj.canvas.toBuffer('image/png');
      }
    } else if (typeof canvasObj.canvas.toBlob === 'function') {
      const blob = await new Promise((resolve) => canvasObj.canvas.toBlob(resolve, 'image/png'));
      return await blob.arrayBuffer();
    }
    throw new Error('Unable to convert canvas to PNG buffer');
  }

  // Fallback font stack for Indic + international scripts
  const FALLBACK_FONTS = '"Noto Sans Telugu", "Noto Sans Tamil", "Noto Sans Malayalam", "Noto Sans Kannada", "Noto Sans Devanagari", "Noto Sans Bengali", "Noto Sans Gujarati", "Noto Sans Gurmukhi", "Anek Odia", "Noto Sans", "Arial", sans-serif';

  const ANIMATION_PRESETS = [
    { id: 'bottom-to-top', name: 'Bottom-to-Top Keyframe Rise', description: 'Smooth keyframed rise from bottom to top as character speaks' },
    { id: 'spring-pop', name: 'Spring Pop (Hormozi / MrBeast)', description: 'Snappy scale bounce with elastic damping' },
    { id: 'rise-fade', name: 'Smooth Rise & Fade', description: 'Upward slide with buttery cubic-bezier easing' },
    { id: 'marker-swipe', name: 'Marker Swipe', description: 'Energetic highlight wipe with dynamic rotation' },
    { id: 'glow-pulse', name: 'Neon Glow Pulse', description: 'Breathing neon bloom effect' },
    { id: 'karaoke-wipe', name: 'Karaoke Wipe', description: 'Horizontal fill reveal timed to speech' },
    { id: 'light-sheen', name: 'Gloss Light Sheen', description: 'High-end glass diagonal shimmer' },
    { id: 'char-wave', name: 'Character Kinetic Wave', description: 'Staggered character bounce wave' },
    { id: 'hud-snap', name: 'HUD Target Snap', description: 'Cyberpunk lock-on bracket snap' },
    { id: 'sticker-slam', name: 'Sticker Slam', description: 'Aggressive impact scale drop' },
    { id: 'card-zoom', name: 'Card Dynamic Zoom', description: 'Subtle cinematic push-in' },
  ];

  function isIndicWord(w) {
    return /[\u0900-\u0D7F]/.test(String(w ?? ''));
  }

  /**
   * Formats a word according to style.textTransform ('uppercase', 'lowercase', 'sentence', 'capitalize', 'preserve', 'none').
   * Preserves Indic Unicode codepoints, matras, and ZWJ/ZWNJ intact on case transforms.
   */
  function formatWordCase(w, textTransform, isFirstWord = false) {
    const str = String(w ?? '');
    if (!str || !textTransform || textTransform === 'preserve' || textTransform === 'none') return str;
    if (textTransform === 'uppercase') return str.replace(/[a-z]/g, ch => ch.toUpperCase());
    if (textTransform === 'lowercase') return str.replace(/[A-Z]/g, ch => ch.toLowerCase());
    if (textTransform === 'sentence' || textTransform === 'capitalize') {
      const trimmed = str.trim();
      if (!trimmed || isIndicWord(trimmed)) return str;
      if (isFirstWord || textTransform === 'capitalize') {
        return trimmed.charAt(0).toUpperCase() + trimmed.slice(1);
      }
      if (trimmed === 'I' || /^[A-Z]/.test(trimmed)) {
        return trimmed;
      }
      return trimmed;
    }
    return str;
  }

  /**
   * Strips punctuation (. , - _ •) while preserving ? and ! at full font size.
   */
  function formatSpotlightWord(w) {
    if (!w) return '';
    return String(w).replace(/^[.,\-_•·—–\s]+|[.,\-_•·—–\s]+$/g, '');
  }

  /**
   * Picks the hero focal word for the Spotlight style.
   * Prefers the most impactful content word in the middle region to create a balanced 3-line layout:
   * Line 1 (Top-Left Prefix), Line 2 (Center Hero), Line 3 (Bottom-Right Suffix).
   */
  function getSpotlightHeroIndex(words) {
    if (!words || words.length === 0) return 0;
    if (words.length === 1) return 0;
    if (words.length === 2) {
      const c0 = String(words[0]).replace(/[^\w\u0900-\u0D7F]/g, '');
      const c1 = String(words[1]).replace(/[^\w\u0900-\u0D7F]/g, '');
      if (c0.length === 0 && c1.length > 0) return 1;
      if (c1.length === 0 && c0.length > 0) return 0;
      const isStop0 = /^(a|an|the|of|in|to|is|it|at|by|for|on|and|or|as|do|not|you|we|he|she|they|i|my|your|his|her|its|our|their)$/i.test(c0);
      return isStop0 ? 1 : 0;
    }
    let bestIdx = Math.floor(words.length / 2);
    let maxScore = -999;
    for (let i = 0; i < words.length; i++) {
      const raw = String(words[i]);
      const clean = raw.replace(/[^\w\u0900-\u0D7F]/g, '');
      if (clean.length === 0) {
        const score = -999;
        if (score > maxScore) {
          maxScore = score;
          bestIdx = i;
        }
        continue;
      }
      const isStopword = /^(a|an|the|of|in|to|is|it|at|by|for|on|and|or|as|do|not|you|we|he|she|they|i|my|your|his|her|its|our|their|aur|apni|apna|kar|karo|ko|ke|ki|ka|hai|hain|tha|thi|the|se|mein|par|yeh|woh|ismein|isme|usmein|usme|kuch|sirf|bhi|toh|ho|raha|rahi|rahe)$/i.test(clean);
      let score = clean.length;
      if (!isStopword) score += 6;
      if (/^[A-Z]/.test(raw) && !/^(A|An|The|I)$/.test(raw)) score += 3;
      // Structural 3-line balance bonus: prefer middle words so we get prefix, hero, and suffix
      if (i > 0 && i < words.length - 1) {
        score += 3.5;
      }
      const distFromCenter = Math.abs(i - (words.length - 1) / 2);
      score -= distFromCenter * 0.4;

      if (score > maxScore) {
        maxScore = score;
        bestIdx = i;
      }
    }
    return bestIdx;
  }

  /**
   * Picks the single most important emphasis keyword for the Luxe Italic style.
   */
  function getLuxeHeroIndex(words) {
    if (!words || words.length === 0) return 0;
    if (words.length === 1) return 0;
    let bestIdx = 0;
    let maxScore = -999;
    for (let i = 0; i < words.length; i++) {
      const raw = String(words[i]).trim();
      const clean = raw.replace(/[^\w\u0900-\u0D7F]/g, '').toLowerCase();
      if (clean.length === 0) {
        const score = -999;
        if (score > maxScore) {
          maxScore = score;
          bestIdx = i;
        }
        continue;
      }
      const isStopword = /^(a|an|the|of|in|to|is|it|at|by|for|on|and|or|as|do|not|you|we|he|she|they|i|my|your|his|her|its|our|their|aur|apni|apna|kar|karo|ko|ke|ki|ka|hai|hain|tha|thi|the|se|mein|par|yeh|woh|ismein|isme|usmein|usme|kuch|sirf|bhi|toh|ho|raha|rahi|rahe|na|kya|kyun|kab|kaise|with|from|this|that|these|those|will|would|can|could|should|have|has|had|been)$/i.test(clean);
      
      let score = clean.length;
      if (isStopword) {
        score -= 10;
      } else {
        score += 8;
      }
      // High priority: numbers, symbols, terms like "24*7", "100k", "10x", ages
      if (/\d+/.test(raw) || /[\*\/\$\%\#\@]/.test(raw)) {
        score += 15;
      }
      // High priority: Proper nouns, names, brands like "Supernova", "AI", "English"
      if (/^[A-Z]/.test(raw) && !/^(A|An|The|I|In|On|At|To|And|Or|If|Of|He|She|It|We|My)$/.test(raw)) {
        score += 10;
      }
      // All-caps acronyms (AI, CEO, NASA, etc.)
      if (/^[A-Z0-9]{2,}$/.test(raw.replace(/[^\w]/g, ''))) {
        score += 12;
      }
      // Slight climax boost for later words in the card
      score += (i / words.length) * 1.5;
      if (score > maxScore) {
        maxScore = score;
        bestIdx = i;
      }
    }
    return bestIdx;
  }

  /**
   * Render a single caption cue to a transparent PNG ArrayBuffer.
   *
   * @param {Object} opts
   * @param {Object} opts.cue        - { text, words, highlightIndex }
   * @param {number} opts.width      - Canvas width (px)
   * @param {number} opts.height     - Canvas height (px)
   * @param {Object} opts.style      - Style settings from the project
   * @returns {Promise<ArrayBuffer>}  PNG image as ArrayBuffer
   */
  async function renderCueToPng(opts) {
    const run = async () => _renderCueToPngInternal(opts);
    const resultPromise = _renderLock.then(run, run);
    _renderLock = resultPromise.catch(() => {});
    return resultPromise;
  }

  async function _renderCueToPngInternal({
    cue,
    width,
    height,
    fullHeight,
    bandY0 = 0,
    style = {},
    animProgress = 1.0,
    wordScale = 1.0,
  }) {
    const canvasObj = createRenderCanvas(width, height);
    const canvas = canvasObj.canvas;
    const ctx = canvasObj.ctx || canvas.getContext('2d');

    // Reset full canvas context state and clear to fully transparent
    ctx.setTransform(1, 0, 0, 1, 0, 0);
    ctx.clearRect(0, 0, width, height);
    if (typeof ctx.filter !== 'undefined') ctx.filter = 'none';
    ctx.globalAlpha = 1.0;
    ctx.shadowColor = 'transparent';
    ctx.shadowBlur = 0;
    ctx.shadowOffsetX = 0;
    ctx.shadowOffsetY = 0;

    const frameHeight = (typeof fullHeight === 'number' && fullHeight > 0) ? fullHeight : height;
    const offsetY = (typeof bandY0 === 'number' && bandY0 > 0) ? bandY0 : 0;

    const text = String(cue.text || '');
    let highlightIdx = cue.highlightIndex ?? 0;

    const rawWords = (cue.words && cue.words.length > 0)
      ? cue.words.map(w => typeof w === 'string' ? w : String(w.text ?? w.word ?? '')).filter(w => w.trim().length > 0)
      : String(cue.text || '').split(/\s+/).filter(Boolean);

    // Adjust highlightIdx if cue.words had empty/cleared words before cue.highlightIndex
    if (cue.words && cue.words.length > 0 && highlightIdx >= 0 && rawWords.length !== cue.words.length) {
      let mappedIdx = 0;
      for (let idx = 0; idx < cue.words.length && idx < highlightIdx; idx++) {
        const w = cue.words[idx];
        const s = typeof w === 'string' ? w : String(w?.text ?? w?.word ?? '');
        if (s.trim().length > 0) mappedIdx++;
      }
      highlightIdx = Math.min(mappedIdx, Math.max(0, rawWords.length - 1));
    }

    let words = rawWords.slice();

    if (style.textTransform === 'uppercase' || style.textTransform === 'lowercase' || style.textTransform === 'sentence' || style.textTransform === 'capitalize') {
      words = words.map((w, idx) => formatWordCase(w, style.textTransform, idx === 0));
    }

    if (words.length === 0) {
      return await canvasToBuffer(canvasObj);
    }

    // ── Font Setup (Width-anchored 1080 scale + 0.78x 1:1 Square adaptation) ──
    const fontFamily = style.fontFamily || 'Montserrat';
    const baseFontSize = style.fontSize || 68;
    const isSquare = Math.abs(frameHeight - width) <= width * 0.15;
    const isLandscape = width > frameHeight * 1.15;
    const aspectScale = isSquare ? 0.78 : (isLandscape ? Math.min(0.75, frameHeight / width) : 1.0);
    const resScale = (width / 1080) * aspectScale;
    const fontSize = Math.max(12, Math.round(baseFontSize * resScale));

    const maxAllowedWidth = width * 0.86;
    let effectiveFontSize = fontSize;
    const fw = style.fontWeight || 800;
    const fst = style.fontStyle || 'normal';

    // Accumulate bundled Indic fonts for ALL scripts detected in mixed-language text (never returning early on first script)
    function getIndicFallbackFonts(family = 'Noto Sans', sampleText = '') {
      const isTamil = /[\u0B80-\u0BFF]/.test(sampleText);
      const isTelugu = /[\u0C00-\u0C7F]/.test(sampleText);
      const isMalayalam = /[\u0D00-\u0D7F]/.test(sampleText);
      const isKannada = /[\u0C80-\u0CFF]/.test(sampleText);
      const isDevanagari = /[\u0900-\u097F]/.test(sampleText);
      const isBengali = /[\u0980-\u09FF]/.test(sampleText);
      const isGujarati = /[\u0A80-\u0AFF]/.test(sampleText);
      const isGurmukhi = /[\u0A00-\u0A7F]/.test(sampleText);
      const isOdia = /[\u0B00-\u0B7F]/.test(sampleText);

      const boldFonts = [
        'Anton', 'Bebas Neue', 'Archivo Black', 'Montserrat', 'Plus Jakarta Sans', 'Outfit',
        'Noto Sans', 'Poppins', 'Oswald', 'Impact', 'Acme Gothic Condensed Semibold', 'Acme',
      ];
      const retroFonts = ['VCR OSD Mono', 'Cooper Black', 'Cooper Std Black', 'Bungee', 'Righteous', 'Chunk Five'];
      const displayFonts = ['Luckiest Guy', 'Fredoka', 'Bangers', 'Bagel Fat One'];

      const isBold = boldFonts.includes(family) || Number(fw) >= 700;
      const isRetro = retroFonts.includes(family);
      const isDisplay = displayFonts.includes(family);

      const acc = [];
      const add = (...fams) => {
        for (const f of fams) {
          if (f && !acc.includes(f)) acc.push(f);
        }
      };

      if (isKannada) {
        if (isDisplay) add('"Baloo Tamma 2"', '"Anek Kannada"', '"Noto Sans Kannada"');
        else if (isBold) add('"Anek Kannada"', '"Noto Sans Kannada"');
        else add('"Noto Sans Kannada"');
      }
      if (isTamil) {
        if (isBold) add('"Anek Tamil"', '"Catamaran"', '"Noto Sans Tamil"');
        else add('"Noto Sans Tamil"');
      }
      if (isTelugu) {
        if (isDisplay) add('"Baloo Tamma 2"', '"Anek Telugu"', '"Noto Sans Telugu"');
        else if (isRetro) add('"Ramabhadra"', '"Anek Telugu"', '"Noto Sans Telugu"');
        else if (isBold) add('"Anek Telugu"', '"Ramabhadra"', '"Noto Sans Telugu"');
        else add('"Noto Sans Telugu"');
      }
      if (isMalayalam) {
        if (isBold) add('"Anek Malayalam"', '"Noto Sans Malayalam"');
        else add('"Noto Sans Malayalam"');
      }
      if (isDevanagari) {
        if (isBold) add('"Anek Devanagari"', '"Noto Sans Devanagari"');
        else add('"Noto Sans Devanagari"');
      }
      if (isBengali) {
        if (isBold) add('"Anek Bangla"', '"Noto Sans Bengali"');
        else add('"Noto Sans Bengali"');
      }
      if (isGujarati) {
        if (isBold) add('"Anek Gujarati"', '"Noto Sans Gujarati"');
        else add('"Noto Sans Gujarati"');
      }
      if (isGurmukhi) {
        if (isBold) add('"Anek Gurmukhi"', '"Noto Sans Gurmukhi"');
        else add('"Noto Sans Gurmukhi"');
      }
      if (isOdia) {
        add('"Anek Odia"');
      }

      if (acc.length === 0) return FALLBACK_FONTS;
      add('"Noto Sans Devanagari"', '"Noto Sans Kannada"', '"Noto Sans Tamil"', '"Noto Sans Telugu"', '"Noto Sans Malayalam"', '"Noto Sans"', '"Arial"', 'sans-serif');
      return acc.join(', ');
    }

    const sampleStr = words.join(' ') || text;
    const hasRegional = /[\u0900-\u0D7F]/.test(sampleStr);
    const scriptFallbacks = getIndicFallbackFonts(fontFamily, sampleStr);

    function makeFontStr(pxSize, isIndic = false) {
      const sz = isIndic ? Math.max(12, Math.round(pxSize * 1.06)) : pxSize;
      return `${fst} ${fw} ${sz}px "${fontFamily}", ${scriptFallbacks}`;
    }

    let fontString = makeFontStr(effectiveFontSize, false);
    ctx.font = fontString;

    let spaceW = ctx.measureText(' ').width * 0.75;
    const highlightMode = style.highlightMode || 'color-swap';
    const isSpecialTierLayout = (
      highlightMode === 'spotlight' || style.id === 6 || style.name === 'Spotlight' ||
      highlightMode === 'gadzhi-split' || style.id === 23 || style.name === 'Gadzhi Split'
    );

    function getHorizontalBleed(effSize) {
      const unitSc = resScale * (effSize / fontSize);
      const strokeBleed = (style.strokeWidth ?? 0) * unitSc * 1.15;
      let leftBleed = strokeBleed;
      let rightBleed = strokeBleed;

      if (highlightMode === 'spotlight' || style.id === 6 || style.name === 'Spotlight') {
        return { leftBleed: 0, rightBleed: 0, extraPadW: 0 };
      }
      if (highlightMode === 'gadzhi-split' || style.id === 23 || style.name === 'Gadzhi Split') {
        const gPad = Math.max(strokeBleed, effSize * 0.08);
        return { leftBleed: gPad, rightBleed: gPad, extraPadW: gPad * 2 };
      }
      if (highlightMode === 'wireframe-glass' || style.id === 17 || style.name === 'Neon Wireframe Glass') {
        const padX = style.paddingX !== undefined ? style.paddingX * resScale : effSize * 0.55;
        return { leftBleed: padX, rightBleed: padX, extraPadW: padX * 2 };
      }
      if (highlightMode === 'frosted-lyric' || style.id === 25 || style.name === 'Frosted Lyric') {
        const padX = (style.paddingX !== undefined ? style.paddingX : 24) * resScale;
        return { leftBleed: padX, rightBleed: padX, extraPadW: padX * 2 };
      }
      if (highlightMode === 'docu-bar' || style.id === 28 || style.name === 'Docu Split-Bar') {
        const padX = (style.paddingX !== undefined ? style.paddingX : 20) * resScale;
        const barW = Math.max(4, effSize * 0.12);
        return { leftBleed: padX + barW, rightBleed: padX, extraPadW: padX * 2 + barW };
      }
      if (highlightMode !== 'word-box' && (style.isGlossy || style.id === 3 || style.name === 'Glass Grey' || (style.bgColor && style.bgColor !== 'transparent'))) {
        const padX = (style.paddingX !== undefined ? style.paddingX : 18) * resScale;
        return { leftBleed: padX, rightBleed: padX, extraPadW: padX * 2 };
      }

      // Word-pill styles where active pill extends horizontally beyond startX / startX + totalW
      if (highlightMode === 'word-box' || style.id === 4) {
        leftBleed = Math.max(leftBleed, effSize * 0.18);
        rightBleed = Math.max(rightBleed, effSize * 0.18);
      } else if (highlightMode === 'hormozi' || style.id === 9) {
        leftBleed = Math.max(leftBleed, effSize * 0.18);
        rightBleed = Math.max(rightBleed, effSize * 0.18);
      } else if (highlightMode === 'highlighter' || style.id === 10) {
        leftBleed = Math.max(leftBleed, effSize * 0.24);
        rightBleed = Math.max(rightBleed, effSize * 0.24);
      } else if (highlightMode === 'hud-target' || style.id === 18) {
        leftBleed = Math.max(leftBleed, effSize * 0.14);
        rightBleed = Math.max(rightBleed, effSize * 0.14);
      } else if (highlightMode === 'vox-highlighter' || style.id === 21) {
        leftBleed = Math.max(leftBleed, effSize * 0.28);
        rightBleed = Math.max(rightBleed, effSize * 0.28);
      } else if (highlightMode === 'gradient-badge' || style.id === 27) {
        leftBleed = Math.max(leftBleed, effSize * 0.22);
        rightBleed = Math.max(rightBleed, effSize * 0.22);
      } else if (highlightMode === 'comic-slap' || style.id === 24) {
        leftBleed = Math.max(leftBleed, effSize * 0.10);
        rightBleed = Math.max(rightBleed, effSize * 0.20);
      } else if (highlightMode === 'cyber-slice' || style.id === 29) {
        leftBleed = Math.max(leftBleed, effSize * 0.16);
        rightBleed = Math.max(rightBleed, effSize * 0.16);
      } else if (highlightMode === 'cinema' || style.id === 13) {
        leftBleed = Math.max(leftBleed, effSize * 0.16);
        rightBleed = Math.max(rightBleed, effSize * 0.16);
      }

      return { leftBleed, rightBleed, extraPadW: leftBleed + rightBleed };
    }

    function measureLineMetrics(wordsList, currentFontStr, effSize, heroBaseWords = null, lineWordOffset = 0) {
      ctx.font = currentFontStr;
      const curSpaceW = ctx.measureText(' ').width * 0.75;
      const indicFont = makeFontStr(effSize, true);
      let maxAscent = effSize * (hasRegional ? 0.78 : 0.74);
      let maxDescent = effSize * (hasRegional ? 0.28 : 0.18);
      let lineW = 0;

      if (style.highlightMode === 'spotlight' || style.id === 6 || style.name === 'Spotlight') {
        const heroIdx = getSpotlightHeroIndex(wordsList);
        const prefixWords = wordsList.slice(0, heroIdx).map(formatSpotlightWord);
        const heroWord = formatWordCase(
          formatSpotlightWord(wordsList[heroIdx] || wordsList[0]),
          style.textTransform || 'lowercase'
        );
        const suffixWords = wordsList.slice(heroIdx + 1).map(formatSpotlightWord);

        const heroFontSize = Math.round(effSize * 1.25);
        const subFontSize = Math.round(effSize * 0.52);

        ctx.font = `${fst} ${fw || 900} ${heroFontSize}px "${fontFamily}", ${scriptFallbacks}`;
        const heroW = ctx.measureText(heroWord).width;

        ctx.font = `${fst} 700 ${subFontSize}px "${fontFamily}", ${scriptFallbacks}`;
        const subSpaceW = ctx.measureText(' ').width * 0.75;
        const prefixW = prefixWords.reduce((acc, w) => acc + ctx.measureText(w).width, 0) + subSpaceW * Math.max(0, prefixWords.length - 1);
        const suffixW = suffixWords.reduce((acc, w) => acc + ctx.measureText(w).width, 0) + subSpaceW * Math.max(0, suffixWords.length - 1);

        ctx.font = currentFontStr;
        lineW = Math.max(heroW * 1.15, prefixW * 1.15, suffixW * 1.15);
      } else if (style.highlightMode === 'gadzhi-split' || style.id === 23 || style.name === 'Gadzhi Split') {
        if (wordsList.length === 1) {
          const botFontSize = Math.round(effSize * 1.08);
          ctx.font = `italic 700 ${botFontSize}px "Playfair Display", ${scriptFallbacks}, serif`;
          lineW = ctx.measureText(wordsList[0]).width;
          ctx.font = currentFontStr;
        } else {
          const splitIdx = wordsList.length === 2 ? 1 : Math.ceil(wordsList.length / 2);
          const topWords = wordsList.slice(0, splitIdx).map(w => style.textTransform === 'lowercase' ? w.toLowerCase() : formatWordCase(w, 'uppercase'));
          const botWords = wordsList.slice(splitIdx);

          const topFontSize = Math.round(effSize * 0.65);
          const botFontSize = Math.round(effSize * 1.08);

          ctx.font = `normal 700 ${topFontSize}px "Montserrat", ${scriptFallbacks}, sans-serif`;
          const topSpW = ctx.measureText(' ').width * 0.75;
          const topW = topWords.reduce((acc, w) => acc + ctx.measureText(w).width, 0) + topSpW * Math.max(0, topWords.length - 1);

          ctx.font = `italic 700 ${botFontSize}px "Playfair Display", ${scriptFallbacks}, serif`;
          const botSpW = ctx.measureText(' ').width * 0.75;
          const botW = botWords.reduce((acc, w) => acc + ctx.measureText(w).width, 0) + botSpW * Math.max(0, botWords.length - 1);

          ctx.font = currentFontStr;
          lineW = Math.max(topW, botW);
        }
      } else if (style.highlightMode === 'luxe-italic') {
        const heroIdx = getLuxeHeroIndex(heroBaseWords || wordsList);
        const italicFont = `italic 700 ${Math.round(effSize * 1.10)}px "Playfair Display", ${scriptFallbacks}, serif`;
        for (let idx = 0; idx < wordsList.length; idx++) {
          const w = wordsList[idx];
          if (lineWordOffset + idx === heroIdx) {
            ctx.font = italicFont;
            const m = ctx.measureText(w);
            lineW += m.width + effSize * 0.14;
            if (m.actualBoundingBoxAscent > maxAscent) maxAscent = m.actualBoundingBoxAscent;
            if (m.actualBoundingBoxDescent > maxDescent) maxDescent = m.actualBoundingBoxDescent;
          } else {
            ctx.font = isIndicWord(w) ? indicFont : currentFontStr;
            const m = ctx.measureText(w);
            lineW += m.width;
            if (m.actualBoundingBoxAscent > maxAscent) maxAscent = m.actualBoundingBoxAscent;
            if (m.actualBoundingBoxDescent > maxDescent) maxDescent = m.actualBoundingBoxDescent;
          }
        }
        lineW += curSpaceW * Math.max(0, wordsList.length - 1);
        ctx.font = currentFontStr;
      } else {
        let sumW = 0;
        for (let idx = 0; idx < wordsList.length; idx++) {
          const w = wordsList[idx];
          ctx.font = isIndicWord(w) ? indicFont : currentFontStr;
          const m = ctx.measureText(w);
          sumW += m.width;
          if (m.actualBoundingBoxAscent > maxAscent) maxAscent = m.actualBoundingBoxAscent;
          if (m.actualBoundingBoxDescent > maxDescent) maxDescent = m.actualBoundingBoxDescent;
        }
        ctx.font = currentFontStr;
        if (style.highlightMode === 'sticker' || style.id === 12 || style.name === 'Sticker Cutout') {
          lineW = sumW + (curSpaceW + effSize * 0.52) * Math.max(0, wordsList.length - 1) + effSize * 0.48;
        } else {
          lineW = sumW + curSpaceW * Math.max(0, wordsList.length - 1);
        }
      }

      const inkH = maxAscent + maxDescent;
      const contentH = Math.max((hasRegional ? 1.08 : 0.94) * effSize, inkH);
      const baselineOffset = (maxAscent - maxDescent) / 2;
      return { width: lineW, maxAscent, maxDescent, inkH, contentH, baselineOffset, spaceW: curSpaceW };
    }

    function buildWrappedLines(effSize) {
      const fStr = makeFontStr(effSize, false);
      const bleed = getHorizontalBleed(effSize);
      const single = measureLineMetrics(words, fStr, effSize, words, 0);
      // Zeno: captions are always ONE row. A caption that is too wide is made slightly smaller, never wrapped to two rows.
      if (style.singleLine || isSpecialTierLayout || words.length < 2 || (single.width + bleed.extraPadW) <= maxAllowedWidth) {
        return {
          fontStr: fStr,
          bleed,
          lines: [{ words, startIdx: 0, metrics: single }],
          maxLineW: single.width,
        };
      }
      // Balanced 2-line wrap fallback before shrinking font size
      let bestSplit = 1;
      let bestScore = Infinity;
      let bestL1 = null;
      let bestL2 = null;
      for (let s = 1; s < words.length; s++) {
        const w1 = words.slice(0, s);
        const w2 = words.slice(s);
        const m1 = measureLineMetrics(w1, fStr, effSize, words, 0);
        const m2 = measureLineMetrics(w2, fStr, effSize, words, s);
        const score = Math.max(m1.width, m2.width) + 0.25 * Math.abs(m1.width - m2.width);
        if (score < bestScore) {
          bestScore = score;
          bestSplit = s;
          bestL1 = m1;
          bestL2 = m2;
        }
      }
      return {
        fontStr: fStr,
        bleed,
        lines: [
          { words: words.slice(0, bestSplit), startIdx: 0, metrics: bestL1 },
          { words: words.slice(bestSplit), startIdx: bestSplit, metrics: bestL2 },
        ],
        maxLineW: Math.max(bestL1.width, bestL2.width),
      };
    }

    let layoutState = buildWrappedLines(effectiveFontSize);
    let iterations = 0;
    while ((layoutState.maxLineW + layoutState.bleed.extraPadW) > maxAllowedWidth && effectiveFontSize > 12 && iterations < 6) {
      const shrinkRatio = maxAllowedWidth / (layoutState.maxLineW + layoutState.bleed.extraPadW);
      effectiveFontSize = Math.max(12, Math.floor(effectiveFontSize * shrinkRatio * 0.96));
      layoutState = buildWrappedLines(effectiveFontSize);
      iterations++;
    }

    fontString = layoutState.fontStr;
    ctx.font = fontString;
    spaceW = layoutState.lines[0].metrics.spaceW;
    const { leftBleed, rightBleed, extraPadW } = layoutState.bleed;
    const wrappedLines = layoutState.lines;
    const totalW = layoutState.maxLineW;

    // ── Positioning (supports cropped band via frameHeight - offsetY) ──
    const posX = (style.positionX !== undefined ? style.positionX : 50) / 100;
    const posY = (style.positionY !== undefined ? style.positionY : 72) / 100;
    const textY = posY * frameHeight - offsetY;
    ctx.textBaseline = 'alphabetic';

    const isTwoLine = wrappedLines.length === 2;
    const mLine0 = wrappedLines[0].metrics;
    const mLine1 = isTwoLine ? wrappedLines[1].metrics : null;
    const rowGap = isTwoLine
      ? Math.max((mLine0.contentH + mLine1.contentH) * 0.62, effectiveFontSize * (hasRegional ? 1.34 : 1.20))
      : 0;
    const lineCenterYs = isTwoLine ? [textY - rowGap / 2, textY + rowGap / 2] : [textY];
    const contentH = isTwoLine
      ? (rowGap + (mLine0.contentH + mLine1.contentH) * 0.52)
      : mLine0.contentH;
    const baselineY = lineCenterYs[0] + mLine0.baselineOffset;

    function computeLineStartX(lineW) {
      let anchor = posX * width;
      if (style.textAlign === 'left') {
        // left-aligned at anchor
      } else if (style.textAlign === 'right') {
        anchor -= lineW;
      } else {
        anchor -= lineW / 2;
      }
      const minSX = width * 0.05 + leftBleed;
      const maxSX = Math.max(minSX, width * 0.95 - lineW - rightBleed);
      return Math.max(minSX, Math.min(maxSX, anchor));
    }

    const startX = computeLineStartX(totalW);

    // ── Spotlight Hero Layout ─────────────────────────────────────────
    if (highlightMode === 'spotlight' || style.id === 6 || style.name === 'Spotlight') {
      const heroIdx = getSpotlightHeroIndex(words);
      const prefixWords = words.slice(0, heroIdx);
      const heroWord = words[heroIdx] || words[0];
      const suffixWords = words.slice(heroIdx + 1);

      const heroFontSize = Math.round(effectiveFontSize * 1.25);
      const subFontSize = Math.round(effectiveFontSize * 0.52);

      const heroFont = `${fst} ${fw || 900} ${heroFontSize}px "${fontFamily}", ${scriptFallbacks}`;
      const subFont = `${fst} 700 ${subFontSize}px "${fontFamily}", ${scriptFallbacks}`;

      const heroCleaned = formatWordCase(
        formatSpotlightWord(heroWord),
        style.textTransform || 'lowercase'
      );
      const prefixWordsCleaned = prefixWords.map(formatSpotlightWord);
      const suffixWordsCleaned = suffixWords.map(formatSpotlightWord);

      ctx.font = heroFont;
      const heroW = ctx.measureText(heroCleaned).width;

      ctx.font = subFont;
      const subSpaceW = ctx.measureText(' ').width * 0.75;
      const prefixFormatted = prefixWordsCleaned;
      const prefixTotalW = prefixFormatted.reduce((acc, w) => acc + ctx.measureText(w).width, 0)
                          + subSpaceW * Math.max(0, prefixFormatted.length - 1);

      const suffixTotalW = suffixWordsCleaned.reduce((acc, w) => acc + ctx.measureText(w).width, 0)
                          + subSpaceW * Math.max(0, suffixWordsCleaned.length - 1);

      // Block width accommodates left-aligned prefix, centered hero, and right-aligned suffix
      const maxBlockW = Math.max(heroW * 1.15, prefixTotalW * 1.15, suffixTotalW * 1.15);

      let blockCenterX = posX * width;
      const halfBlock = maxBlockW / 2;
      const minCenter = width * 0.05 + halfBlock;
      const maxCenter = width * 0.95 - halfBlock;
      if (minCenter <= maxCenter) {
        blockCenterX = Math.min(Math.max(blockCenterX, minCenter), maxCenter);
      } else {
        blockCenterX = width / 2;
      }

      const blockLeftX = blockCenterX - maxBlockW / 2;
      const blockRightX = blockCenterX + maxBlockW / 2;

      const heroY = textY;
      const tierSpacing = hasRegional ? 0.58 : 0.50;
      const prefixY = heroY - heroFontSize * tierSpacing;
      const suffixY = heroY + heroFontSize * tierSpacing;

      const dropShadow = (style.dropShadow ?? 12) * (effectiveFontSize / 40);

      let animEased = 1.0;
      if (typeof animProgress === 'number' && animProgress < 1.0) {
        const p = Math.max(0, Math.min(1, animProgress));
        animEased = 1 - Math.pow(1 - p, 2.5);
      }

      /** Helper for multi-tier cinematic ambient shadow + crisp foreground pass */
      function fillWithDepthShadow(itemText, tx, ty, font, opacity, scale = 1.0) {
        if (opacity <= 0.005) return;
        ctx.save();
        ctx.font = font;
        ctx.textBaseline = 'alphabetic';
        ctx.fillStyle = style.color || style.fontColor || '#FFFFFF';
        ctx.globalAlpha = opacity;

        if (scale !== 1.0) {
          ctx.translate(tx, ty);
          ctx.scale(scale, scale);
          ctx.translate(-tx, -ty);
        }

        // Ambient diffusion pass 1 (deep wide shadow)
        if (dropShadow > 0) {
          ctx.shadowColor = 'rgba(0, 0, 0, 0.80)';
          ctx.shadowBlur = dropShadow * 2.8;
          ctx.shadowOffsetY = dropShadow * 1.0;
          ctx.fillText(itemText, tx, ty);

          // Sharp contrast pass 2 (tight contact shadow)
          ctx.shadowColor = 'rgba(0, 0, 0, 0.95)';
          ctx.shadowBlur = dropShadow * 1.2;
          ctx.shadowOffsetY = dropShadow * 0.4;
          ctx.fillText(itemText, tx, ty);
        }

        // Crisp foreground fill
        ctx.shadowColor = 'transparent';
        ctx.shadowBlur = 0;
        ctx.shadowOffsetY = 0;
        ctx.fillText(itemText, tx, ty);
        ctx.restore();
      }

      // 1. Draw Top Prefix Row (LEFT-ALIGNED, moves bottom-to-top on word 0, stays in place)
      if (prefixFormatted.length > 0 && highlightIdx >= 0) {
        ctx.font = subFont;
        ctx.textBaseline = 'alphabetic';
        let px = blockLeftX;

        // Bottom-to-top entry motion on first word
        const entryOffset = highlightIdx === 0 ? (16 * (1 - animEased) * (effectiveFontSize / 40)) : 0;
        const topOp = highlightIdx === 0 ? animEased : 1.0;

        for (let pi = 0; pi < prefixFormatted.length; pi++) {
          const pw = prefixFormatted[pi];
          const pwW = ctx.measureText(pw).width;
          const isSpoken = highlightIdx >= pi;
          const wordOp = isSpoken ? topOp : (topOp * 0.35);

          fillWithDepthShadow(pw, px, prefixY + entryOffset, subFont, wordOp);
          px += pwW + subSpaceW;
        }
      }

      // 2. Draw Middle Hero Word (CENTER-ALIGNED, larger font, moves bottom-to-top on heroIdx, stays in place)
      if (highlightIdx >= heroIdx) {
        const isCurrentHero = highlightIdx === heroIdx;
        const entryOffset = isCurrentHero ? (22 * (1 - animEased) * (effectiveFontSize / 40)) : 0;
        const heroOp = isCurrentHero ? animEased : 1.0;
        const heroScale = isCurrentHero ? (0.95 + 0.05 * animEased) : 1.0;

        const heroX = blockCenterX - heroW / 2;
        const heroYPos = heroY + heroFontSize * 0.28 + entryOffset;
        fillWithDepthShadow(heroCleaned, heroX, heroYPos, heroFont, heroOp, heroScale);
      }

      // 3. Draw Bottom Suffix Row (RIGHT-ALIGNED, moves bottom-to-top on suffix words, stays in place)
      if (suffixWordsCleaned.length > 0 && highlightIdx > heroIdx) {
        ctx.font = subFont;
        ctx.textBaseline = 'alphabetic';
        let sx = blockRightX - suffixTotalW;

        const isFirstSuffixWord = highlightIdx === heroIdx + 1;
        const entryOffset = isFirstSuffixWord ? (16 * (1 - animEased) * (effectiveFontSize / 40)) : 0;
        const btmOp = isFirstSuffixWord ? animEased : 1.0;

        for (let si = 0; si < suffixWordsCleaned.length; si++) {
          const sw = suffixWordsCleaned[si];
          const swW = ctx.measureText(sw).width;
          const overallIdx = heroIdx + 1 + si;
          const isSpoken = highlightIdx >= overallIdx;
          const wordOp = isSpoken ? btmOp : (btmOp * 0.35);

          fillWithDepthShadow(sw, sx, suffixY + subFontSize * 0.25 + entryOffset, subFont, wordOp);
          sx += swW + subSpaceW;
        }
      }

      return await canvasToBuffer(canvasObj);
    }

    // ── Style 23: Gadzhi Split (2-Tier Stacked Lockup) ────────────────
    if (highlightMode === 'gadzhi-split' || style.id === 23 || style.name === 'Gadzhi Split') {
      const fontScaleRatio = effectiveFontSize / 40;
      const strokeW = (style.strokeWidth ?? 0) * fontScaleRatio;
      const dropShadow = (style.dropShadow ?? 10) * fontScaleRatio;
      const glowBlur = (style.glowBlur ?? 12) * fontScaleRatio;
      const activeCol = style.activeColor || '#10B981';
      const baseBotCol = style.color || style.fontColor || '#FAFAF9';
      const inactiveTopCol = style.inactiveColor || '#A1A1AA';
      const strokeCol = style.strokeColor || '#000000';

      let centerX = posX * width;
      const halfW = totalW / 2;
      centerX = Math.max(width * 0.05 + halfW, Math.min(width * 0.95 - halfW, centerX));

      if (words.length === 1) {
        const botFontSize = Math.round(effectiveFontSize * 1.08);
        const botFont = `italic 700 ${botFontSize}px "Playfair Display", ${scriptFallbacks}, serif`;
        ctx.save();
        ctx.font = botFont;
        ctx.textBaseline = 'alphabetic';
        const w = words[0];
        const wW = ctx.measureText(w).width;
        const wx = centerX - wW / 2;
        const isCurrent = highlightIdx === 0;
        if (dropShadow > 0) {
          ctx.shadowColor = 'rgba(0, 0, 0, 0.90)';
          ctx.shadowBlur = dropShadow * 1.8;
          ctx.shadowOffsetY = dropShadow * 0.5;
          ctx.fillStyle = isCurrent ? activeCol : baseBotCol;
          ctx.fillText(w, wx, baselineY);
        }
        if (strokeW > 0) {
          ctx.shadowColor = 'transparent';
          ctx.strokeStyle = strokeCol;
          ctx.lineWidth = strokeW * 2;
          ctx.strokeText(w, wx, baselineY);
        }
        if (isCurrent && glowBlur > 0) {
          ctx.shadowColor = activeCol;
          ctx.shadowBlur = glowBlur;
        } else {
          ctx.shadowColor = 'transparent';
          ctx.shadowBlur = 0;
        }
        ctx.fillStyle = isCurrent ? activeCol : baseBotCol;
        ctx.fillText(w, wx, baselineY);
        ctx.restore();
        return await canvasToBuffer(canvasObj);
      }

      const splitIdx = words.length === 2 ? 1 : Math.ceil(words.length / 2);
      const topWords = words.slice(0, splitIdx).map(w => style.textTransform === 'lowercase' ? w.toLowerCase() : w.toUpperCase());
      const botWords = words.slice(splitIdx);

      const topFontSize = Math.round(effectiveFontSize * 0.65);
      const botFontSize = Math.round(effectiveFontSize * 1.08);
      const topFont = `normal 700 ${topFontSize}px "Montserrat", ${scriptFallbacks}, sans-serif`;
      const botFont = `italic 700 ${botFontSize}px "Playfair Display", ${scriptFallbacks}, serif`;

      const topCenterY = textY - effectiveFontSize * (hasRegional ? 0.52 : 0.44);
      const botCenterY = textY + effectiveFontSize * (hasRegional ? 0.48 : 0.40);
      const topBaselineY = topCenterY + topFontSize * 0.34;
      const botBaselineY = botCenterY + botFontSize * 0.32;

      // Tier 1: Setup words (Montserrat 700 Uppercase)
      ctx.save();
      ctx.font = topFont;
      ctx.textBaseline = 'alphabetic';
      const topSpW = ctx.measureText(' ').width * 0.75;
      const topTotalW = topWords.reduce((acc, w) => acc + ctx.measureText(w).width, 0) + topSpW * Math.max(0, topWords.length - 1);
      let tx = centerX - topTotalW / 2;

      for (let ti = 0; ti < topWords.length; ti++) {
        const tw = topWords[ti];
        const twW = ctx.measureText(tw).width;
        const isCurrent = highlightIdx === ti;
        const tCol = isCurrent ? '#FFFFFF' : inactiveTopCol;

        if (dropShadow > 0) {
          ctx.save();
          ctx.shadowColor = 'rgba(0, 0, 0, 0.90)';
          ctx.shadowBlur = dropShadow * 1.5;
          ctx.shadowOffsetY = Math.max(2, dropShadow * 0.4);
          ctx.fillStyle = tCol;
          ctx.fillText(tw, tx, topBaselineY);
          ctx.restore();
        }
        if (strokeW > 0) {
          ctx.save();
          ctx.strokeStyle = strokeCol;
          ctx.lineWidth = strokeW * 1.5;
          ctx.strokeText(tw, tx, topBaselineY);
          ctx.restore();
        }
        ctx.save();
        ctx.fillStyle = tCol;
        ctx.fillText(tw, tx, topBaselineY);
        ctx.restore();
        tx += twW + topSpW;
      }
      ctx.restore();

      // Tier 2: Punchline words (Playfair Display Bold Italic)
      ctx.save();
      ctx.font = botFont;
      ctx.textBaseline = 'alphabetic';
      const botSpW = ctx.measureText(' ').width * 0.75;
      const botTotalW = botWords.reduce((acc, w) => acc + ctx.measureText(w).width, 0) + botSpW * Math.max(0, botWords.length - 1);
      let bx = centerX - botTotalW / 2;

      for (let bi = 0; bi < botWords.length; bi++) {
        const bw = botWords[bi];
        const bwW = ctx.measureText(bw).width;
        const overallIdx = splitIdx + bi;
        const isCurrent = highlightIdx === overallIdx;
        const bCol = isCurrent ? activeCol : baseBotCol;

        if (dropShadow > 0) {
          ctx.save();
          ctx.shadowColor = 'rgba(0, 0, 0, 0.92)';
          ctx.shadowBlur = dropShadow * 2.0;
          ctx.shadowOffsetY = Math.max(2, dropShadow * 0.5);
          ctx.fillStyle = bCol;
          ctx.fillText(bw, bx, botBaselineY);
          ctx.restore();
        }
        if (strokeW > 0) {
          ctx.save();
          ctx.strokeStyle = strokeCol;
          ctx.lineWidth = strokeW * 2;
          ctx.strokeText(bw, bx, botBaselineY);
          ctx.restore();
        }
        ctx.save();
        if (isCurrent && glowBlur > 0) {
          ctx.shadowColor = activeCol;
          ctx.shadowBlur = glowBlur;
        }
        ctx.fillStyle = bCol;
        ctx.fillText(bw, bx, botBaselineY);
        ctx.restore();
        bx += bwW + botSpW;
      }
      ctx.restore();

      return await canvasToBuffer(canvasObj);
    }

    // ── Background Container ─────────────────────────────────────────
    if (highlightMode === 'wireframe-glass' || style.id === 17 || style.name === 'Neon Wireframe Glass') {
      const padX = style.paddingX !== undefined ? style.paddingX * resScale : effectiveFontSize * 0.55;
      const padY = style.paddingY !== undefined ? style.paddingY * resScale : effectiveFontSize * 0.25;
      const boxW = totalW + padX * 2;
      const boxH = contentH + padY * 2;
      const boxX = startX - padX;
      const boxY = textY - boxH / 2;
      const br = style.borderRadius !== undefined ? style.borderRadius * resScale : effectiveFontSize * 0.35;
      const dsBlur = style.dropShadow !== undefined ? style.dropShadow * resScale * 1.4 : effectiveFontSize * 0.35;

      ctx.save();
      ctx.fillStyle = style.bgColor && style.bgColor !== 'transparent' ? style.bgColor : 'rgba(12, 10, 24, 0.88)';
      ctx.shadowColor = 'rgba(168, 85, 247, 0.45)';
      ctx.shadowBlur = dsBlur;
      ctx.beginPath();
      ctx.roundRect(boxX, boxY, boxW, boxH, br);
      ctx.fill();

      // Border gradient
      const borderGrad = ctx.createLinearGradient(boxX, 0, boxX + boxW, 0);
      borderGrad.addColorStop(0, '#A855F7');
      borderGrad.addColorStop(0.5, '#EC4899');
      borderGrad.addColorStop(1, '#3B82F6');
      ctx.strokeStyle = borderGrad;
      ctx.lineWidth = Math.max(1.5, effectiveFontSize * 0.04);
      ctx.stroke();
      ctx.restore();
    } else if (highlightMode === 'frosted-lyric' || style.id === 25 || style.name === 'Frosted Lyric') {
      const padX = (style.paddingX !== undefined ? style.paddingX : 24) * resScale;
      const padY = (style.paddingY !== undefined ? style.paddingY : 12) * resScale;
      const boxW = totalW + padX * 2;
      const boxH = contentH + padY * 2;
      const boxX = startX - padX;
      const boxY = textY - boxH / 2;
      const br = isTwoLine ? Math.min(28 * resScale, boxH * 0.28) : Math.min(boxW, boxH) / 2;
      const dsBlur = (style.dropShadow !== undefined ? style.dropShadow : 14) * resScale;

      ctx.save();
      ctx.fillStyle = (style.bgColor && style.bgColor !== 'transparent') ? style.bgColor : 'rgba(15, 15, 20, 0.75)';
      if (dsBlur > 0) {
        ctx.shadowColor = 'rgba(0, 0, 0, 0.48)';
        ctx.shadowBlur = dsBlur;
        ctx.shadowOffsetY = Math.max(2, dsBlur * 0.3);
      }
      ctx.beginPath();
      ctx.roundRect(boxX, boxY, boxW, boxH, br);
      ctx.fill();

      // 1.5px top-edge specular gradient border
      ctx.shadowColor = 'transparent';
      ctx.shadowBlur = 0;
      ctx.shadowOffsetY = 0;
      const specGrad = ctx.createLinearGradient(0, boxY, 0, boxY + boxH);
      specGrad.addColorStop(0, 'rgba(255, 255, 255, 0.40)');
      specGrad.addColorStop(1, 'rgba(255, 255, 255, 0.06)');
      ctx.strokeStyle = specGrad;
      ctx.lineWidth = Math.max(1.2, 1.5 * resScale);
      ctx.stroke();
      ctx.restore();
    } else if (highlightMode === 'docu-bar' || style.id === 28 || style.name === 'Docu Split-Bar') {
      const padX = (style.paddingX !== undefined ? style.paddingX : 20) * resScale;
      const padY = (style.paddingY !== undefined ? style.paddingY : 10) * resScale;
      const barW = Math.max(4, effectiveFontSize * 0.12);
      const boxW = totalW + padX * 2 + barW;
      const boxH = contentH + padY * 2;
      const boxX = startX - padX - barW;
      const boxY = textY - boxH / 2;
      const br = (style.borderRadius !== undefined ? style.borderRadius : 8) * resScale;
      const dsBlur = (style.dropShadow !== undefined ? style.dropShadow : 12) * resScale;

      ctx.save();
      ctx.fillStyle = (style.bgColor && style.bgColor !== 'transparent') ? style.bgColor : 'rgba(10, 12, 16, 0.88)';
      if (dsBlur > 0) {
        ctx.shadowColor = 'rgba(0, 0, 0, 0.50)';
        ctx.shadowBlur = dsBlur;
        ctx.shadowOffsetY = Math.max(2, dsBlur * 0.3);
      }
      ctx.beginPath();
      ctx.roundRect(boxX, boxY, boxW, boxH, br);
      ctx.fill();

      // Left-edge vertical accent bar
      ctx.shadowColor = 'transparent';
      ctx.shadowBlur = 0;
      ctx.shadowOffsetY = 0;
      ctx.save();
      ctx.beginPath();
      ctx.roundRect(boxX, boxY, boxW, boxH, br);
      ctx.clip();
      ctx.fillStyle = style.activeColor || style.wordBoxColor || '#FACC15';
      ctx.fillRect(boxX, boxY, barW, boxH);
      ctx.restore();
      ctx.restore();
    } else if (highlightMode !== 'word-box' && (style.isGlossy || style.id === 3 || style.name === 'Glass Grey' || (style.bgColor && style.bgColor !== 'transparent'))) {
      const padX = (style.paddingX !== undefined ? style.paddingX : 18) * resScale;
      const padY = (style.paddingY !== undefined ? style.paddingY : 8) * resScale;
      const boxW = totalW + padX * 2;
      const boxH = contentH + padY * 2;
      const boxX = startX - padX;
      const boxY = textY - boxH / 2;
      const isGlassGrey = Boolean(style.isGlossy || style.id === 3 || style.name === 'Glass Grey');
      const br = isGlassGrey
        ? Math.max((style.borderRadius ?? 12) * resScale, effectiveFontSize * 0.30)
        : (style.borderRadius !== undefined ? style.borderRadius : 8) * resScale;
      const boxDs = (style.dropShadow !== undefined ? style.dropShadow : (isGlassGrey ? 14 : 12)) * resScale;

      ctx.save();
      if (isGlassGrey) {
        // Deep dark glossy glass container
        const grad = ctx.createLinearGradient(boxX, boxY, boxX, boxY + boxH);
        grad.addColorStop(0, 'rgba(45, 45, 52, 0.94)');
        grad.addColorStop(0.5, 'rgba(22, 22, 26, 0.96)');
        grad.addColorStop(1, 'rgba(10, 10, 14, 0.98)');
        ctx.fillStyle = grad;
        if (boxDs > 0) {
          ctx.shadowColor = 'rgba(0, 0, 0, 0.50)';
          ctx.shadowBlur = boxDs;
          ctx.shadowOffsetY = Math.max(2 * resScale, boxDs * 0.3);
        }
        ctx.beginPath();
        ctx.roundRect(boxX, boxY, boxW, boxH, br);
        ctx.fill();
        ctx.shadowColor = 'transparent';
        ctx.shadowBlur = 0;
        ctx.shadowOffsetY = 0;
        ctx.strokeStyle = 'rgba(255, 255, 255, 0.18)';
        ctx.lineWidth = Math.max(1, 1.5 * resScale);
        ctx.stroke();
      } else {
        // Shadow pass (respects style.dropShadow)
        if (boxDs > 0) {
          ctx.shadowColor = 'rgba(0, 0, 0, 0.40)';
          ctx.shadowBlur = boxDs;
          ctx.shadowOffsetY = Math.max(2 * resScale, boxDs * 0.33);
          ctx.fillStyle = style.bgColor;
          ctx.beginPath();
          ctx.roundRect(boxX, boxY, boxW, boxH, br);
          ctx.fill();
        }

        // Exact clean alpha/color pass
        ctx.shadowColor = 'transparent';
        ctx.shadowBlur = 0;
        ctx.shadowOffsetY = 0;
        ctx.fillStyle = style.bgColor;
        ctx.beginPath();
        ctx.roundRect(boxX, boxY, boxW, boxH, br);
        ctx.fill();
      }
      ctx.restore();
    }

    // ── Draw Words (1 or 2 balanced lines) ───────────────────────────
    for (let li = 0; li < wrappedLines.length; li++) {
      const ln = wrappedLines[li];
      const lineStartX = computeLineStartX(ln.metrics.width);
      const lineCenterY = lineCenterYs[li];
      const lineBaselineY = lineCenterY + ln.metrics.baselineOffset;
      drawWords(
        ctx,
        ln.words,
        highlightIdx,
        fontString,
        lineStartX,
        lineBaselineY,
        lineCenterY,
        style,
        resScale,
        effectiveFontSize,
        hasRegional,
        animProgress,
        scriptFallbacks,
        ln.startIdx,
        ln.metrics,
        wordScale,
        makeFontStr,
        words
      );
    }

    // ── Convert to PNG ───────────────────────────────────────────────
    return await canvasToBuffer(canvasObj);
  }

  function hexToRgba(col, alpha = 0.25) {
    const s = String(col || '').trim();
    if (!s.startsWith('#')) return s || `rgba(255, 255, 255, ${alpha})`;
    const h = s.slice(1);
    const full = h.length === 3 ? h.split('').map(c => c + c).join('') : h;
    const num = parseInt(full, 16);
    if (!Number.isFinite(num)) return `rgba(255, 255, 255, ${alpha})`;
    const r = (num >> 16) & 255;
    const g = (num >> 8) & 255;
    const b = num & 255;
    return `rgba(${r}, ${g}, ${b}, ${alpha})`;
  }

  /**
   * Draw words with highlighting, stroke, active-word scale pop, and shadow effects.
   */
  function drawWords(
    ctx,
    words,
    highlightIdx,
    font,
    startX,
    baselineY,
    textY,
    style,
    resScale,
    fontSize,
    hasRegional = false,
    animProgress = 1.0,
    scriptFallbacks = FALLBACK_FONTS,
    lineWordOffset = 0,
    lineMetrics = null,
    wordScale = 1.0,
    makeFontStr = null,
    allCueWords = null
  ) {
    ctx.font = font;
    ctx.textBaseline = 'alphabetic';
    ctx.lineJoin = 'round';
    ctx.lineCap = 'round';
    const spaceW = (lineMetrics && lineMetrics.spaceW) ? lineMetrics.spaceW : ctx.measureText(' ').width * 0.75;
    let x = startX;

    const fontColor = style.color || style.fontColor || '#FFFFFF';
    const activeColor = style.activeColor || fontColor;
    const highlightMode = style.highlightMode || 'color-swap';
    const wordBoxColor = style.wordBoxColor || 'transparent';
    const borderRadius = style.borderRadius || 0;
    const strokeCol = style.strokeColor || '#000000';

    // Exact 1080-normalized unit scale (avoids legacy fontSize/40 1.8x stroke inflation while shrinking cleanly when wrapped)
    const baseStyleFs = Math.max(12, Number(style.fontSize) || 68);
    const unitScale = fontSize / baseStyleFs;
    const fontScaleRatio = unitScale;
    const strokeW = (style.strokeWidth ?? 0) * unitScale;
    const dropShadow = (style.dropShadow ?? 0) * unitScale;
    const contentH = (lineMetrics && lineMetrics.contentH) ? lineMetrics.contentH : ((hasRegional ? 1.08 : 0.94) * fontSize);
    const inkH = (lineMetrics && lineMetrics.inkH) ? lineMetrics.inkH : contentH;
    const isStickerStyle = (highlightMode === 'sticker' || style.id === 12 || style.name === 'Sticker Cutout');
    const isGlassGreyStyle = Boolean(style.isGlossy || style.id === 3 || style.name === 'Glass Grey');

    for (let i = 0; i < words.length; i++) {
      const globalIdx = lineWordOffset + i;
      const word = words[i];
      const wordIsIndic = isIndicWord(word);
      const wordFont = makeFontStr ? makeFontStr(fontSize, wordIsIndic) : font;
      ctx.font = wordFont;
      const wordW = ctx.measureText(word).width;
      const wStroke = wordIsIndic ? strokeW * 0.82 : strokeW;
      const isHighlighted = globalIdx === highlightIdx;

      // Center-anchored active-word karaoke scale pop (neighbors NEVER shift X positions)
      const popCx = isStickerStyle ? (x + fontSize * 0.24 + wordW / 2) : (x + wordW / 2);
      const popCy = textY;
      const activeScale = (isHighlighted && typeof wordScale === 'number' && Math.abs(wordScale - 1.0) > 0.001) ? wordScale : 1.0;
      let didPopSave = false;
      if (activeScale !== 1.0) {
        ctx.save();
        ctx.translate(popCx, popCy);
        ctx.scale(activeScale, activeScale);
        ctx.translate(-popCx, -popCy);
        didPopSave = true;
      }

      try {
        // Style 4 (Blue Highlight / word-box): real Indic glyph ascent/descent rounded pill + subtle specular border
        if (highlightMode === 'word-box' && isHighlighted && wordBoxColor !== 'transparent') {
          const padX = fontSize * 0.18;
          const padY = fontSize * (wordIsIndic ? 0.18 : 0.13);
          const wordBoxH = contentH + padY * 2;
          const wordBoxY = textY - wordBoxH / 2;
          const wordRadius = Math.max((borderRadius || 14) * unitScale, fontSize * 0.20);

          let boxAlpha = 1.0;
          let expansion = 1.0;
          if (typeof animProgress === 'number' && animProgress < 1.0) {
            const p = Math.max(0, Math.min(1, animProgress));
            const eased = 1 - Math.pow(1 - p, 2.5);
            boxAlpha = 0.65 + (0.35 * eased);
            expansion = 0.96 + (0.04 * eased);
          }

          ctx.save();
          ctx.globalAlpha = (ctx.globalAlpha || 1.0) * boxAlpha;
          if (dropShadow > 0) {
            ctx.shadowColor = 'rgba(0, 0, 0, 0.55)';
            ctx.shadowBlur = Math.max(8 * unitScale, dropShadow * 1.15);
            ctx.shadowOffsetY = Math.max(2.5 * unitScale, dropShadow * 0.32);
          } else {
            ctx.shadowColor = 'rgba(0, 0, 0, 0.45)';
            ctx.shadowBlur = 10 * unitScale;
            ctx.shadowOffsetY = 3 * unitScale;
          }
          ctx.fillStyle = wordBoxColor;
          ctx.beginPath();
          const curW = wordW * expansion;
          const curX = x + (wordW - curW) / 2;
          ctx.roundRect(curX - padX, wordBoxY, curW + padX * 2, wordBoxH, wordRadius);
          ctx.fill();

          ctx.shadowColor = 'transparent';
          ctx.shadowBlur = 0;
          ctx.shadowOffsetY = 0;
          ctx.strokeStyle = 'rgba(255, 255, 255, 0.28)';
          ctx.lineWidth = Math.max(1.2, 1.6 * resScale);
          ctx.stroke();
          ctx.restore();
        } else if (isGlassGreyStyle && isHighlighted) {
          // Style 3 (Glass Grey): gold karaoke highlight pill inside the glossy glass bar
          const padX = fontSize * 0.16;
          const padY = fontSize * (wordIsIndic ? 0.16 : 0.11);
          const pillH = contentH + padY * 2;
          const pillY = textY - pillH / 2;
          const pillR = Math.max(8 * resScale, fontSize * 0.18);
          ctx.save();
          ctx.fillStyle = (wordBoxColor && wordBoxColor !== 'transparent') ? wordBoxColor : hexToRgba(activeColor, 0.22);
          ctx.beginPath();
          ctx.roundRect(x - padX, pillY, wordW + padX * 2, pillH, pillR);
          ctx.fill();
          ctx.strokeStyle = hexToRgba(activeColor, 0.52);
          ctx.lineWidth = Math.max(1.0, 1.4 * resScale);
          ctx.stroke();
          ctx.restore();
        }

        // Style 2 (White Motion / bottom-to-top)
        if (highlightMode === 'bottom-to-top' || highlightMode === 'clean-motion' || highlightMode === 'white-motion' || style.id === 2 || style.name === 'White Motion' || style.name === 'Clean White' || style.name === 'Clean Motion' || style.animationPreset === 'bottom-to-top') {
          const isSpoken = highlightIdx >= 0 && globalIdx < highlightIdx;
          const isCurrent = isHighlighted;
          let opacity = 0.0;
          let offsetY = fontSize * 0.36;
          let sX = 0;
          let sY = 0;
          let sBlur = 0;
          let shadowAlpha = 0;

          const ds = Number(style.dropShadow ?? 0);
          const wFillCol = isCurrent ? (style.activeColor || style.color || style.fontColor || '#FFFFFF') : (style.color || style.fontColor || '#FFFFFF');

          if (ds > 0) {
            sX = 0;
            sY = Math.max(2, dropShadow * 0.35);
            sBlur = Math.max(4, dropShadow * 1.1);
            shadowAlpha = 0.90;

            if (isCurrent) {
              const p = Math.max(0, Math.min(1, typeof animProgress === 'number' ? animProgress : 1.0));
              const eased = 1 - Math.pow(1 - p, 2.2);
              opacity = Math.min(1.0, Math.pow(p, 0.5) * 1.05);
              offsetY = p >= 0.99 ? (-fontSize * 0.05) : ((1 - eased) * (fontSize * 0.28) - eased * (fontSize * 0.05));
              sY *= eased;
              sBlur *= eased;
              shadowAlpha = Math.min(0.95, opacity);
            } else if (isSpoken) {
              opacity = 1.0;
              offsetY = 0;
            } else {
              opacity = 0.0;
              offsetY = fontSize * 0.36;
            }
          } else {
            if (isCurrent) {
              const p = Math.max(0, Math.min(1, typeof animProgress === 'number' ? animProgress : 1.0));
              const eased = 1 - Math.pow(1 - p, 2.2);
              opacity = Math.min(1.0, Math.pow(p, 0.5) * 1.05);
              offsetY = p >= 0.99 ? (-fontSize * 0.05) : ((1 - eased) * (fontSize * 0.28) - eased * (fontSize * 0.05));
            } else if (isSpoken) {
              opacity = 1.0;
              offsetY = 0;
            } else {
              opacity = 0.0;
              offsetY = fontSize * 0.36;
            }
          }

          if (opacity > 0.001) {
            ctx.save();
            ctx.globalAlpha = opacity;
            if (shadowAlpha > 0.01 && (sY > 0 || sBlur > 0)) {
              ctx.save();
              ctx.shadowColor = `rgba(0, 0, 0, ${shadowAlpha.toFixed(2)})`;
              ctx.shadowOffsetX = sX;
              ctx.shadowOffsetY = sY;
              ctx.shadowBlur = sBlur;
              ctx.fillStyle = wFillCol;
              ctx.fillText(word, x, baselineY + offsetY);
              ctx.restore();
            }
            if (isCurrent) {
              ctx.save();
              ctx.shadowColor = 'rgba(255, 255, 255, 0.55)';
              ctx.shadowBlur = Math.max(6, fontSize * 0.20);
              ctx.shadowOffsetY = Math.max(1.5, fontSize * 0.04);
              ctx.fillStyle = wFillCol;
              ctx.fillText(word, x, baselineY + offsetY);
              ctx.restore();
            }
            if (wStroke > 0) {
              ctx.save();
              ctx.strokeStyle = strokeCol;
              ctx.lineWidth = wStroke * 2;
              ctx.strokeText(word, x, baselineY + offsetY);
              ctx.restore();
            }
            ctx.fillStyle = wFillCol;
            ctx.fillText(word, x, baselineY + offsetY);
            ctx.restore();
          }

          x += wordW + spaceW;
          continue;
        }

        // Style 1 (Heavy Black / blur-focus)
        if (highlightMode === 'blur-focus' || style.id === 1 || style.name === 'Heavy Black' || style.animationPreset === 'blur-focus') {
          const isSpoken = highlightIdx >= 0 && globalIdx < highlightIdx;
          const isCurrent = isHighlighted;
          const p = Math.max(0, Math.min(1, typeof animProgress === 'number' ? animProgress : 1.0));
          let opacity = 0.0;
          let blurPx = 0;
          let scale = 1.0;

          if (isCurrent) {
            const eased = 1 - Math.pow(1 - p, 1.8);
            opacity = Math.min(1.0, Math.pow(p, 0.6) * 1.05);
            blurPx = (1 - eased) * 8.0 * unitScale;
            scale = 0.92 + (eased * 0.08);
          } else if (isSpoken) {
            opacity = 1.0;
            blurPx = 0;
            scale = 1.0;
          } else if (highlightIdx >= 0 && p >= 0.99) {
            opacity = 0.45;
            blurPx = Math.max(1.5, 2.5 * unitScale);
            scale = 0.96;
          } else {
            opacity = 0.0;
            blurPx = 8.0 * unitScale;
            scale = 0.90;
          }

          if (opacity > 0.001) {
            ctx.save();
            ctx.globalAlpha = opacity;

            if (Math.abs(scale - 1.0) > 0.001) {
              const centerX = x + wordW / 2;
              const centerY = textY;
              ctx.translate(centerX, centerY);
              ctx.scale(scale, scale);
              ctx.translate(-centerX, -centerY);
            }

            if (blurPx > 0.1 && typeof ctx.filter !== 'undefined') {
              ctx.filter = `blur(${blurPx.toFixed(1)}px)`;
            }

            const fillCol = style.color || style.fontColor || '#000000';

            if (dropShadow > 0) {
              ctx.save();
              ctx.shadowColor = 'rgba(0, 0, 0, 0.85)';
              ctx.shadowOffsetX = 0;
              ctx.shadowOffsetY = Math.max(2, dropShadow * 0.35);
              ctx.shadowBlur = Math.max(4, dropShadow * 1.15);
              ctx.fillStyle = fillCol;
              ctx.fillText(word, x, baselineY);
              ctx.restore();
            }

            if (wStroke > 0) {
              ctx.save();
              ctx.shadowColor = 'transparent';
              ctx.shadowBlur = 0;
              ctx.strokeStyle = strokeCol;
              ctx.lineWidth = wStroke * 2;
              ctx.lineJoin = 'round';
              ctx.lineCap = 'round';
              ctx.strokeText(word, x, baselineY);
              ctx.restore();
            }

            ctx.save();
            ctx.shadowColor = 'transparent';
            ctx.shadowBlur = 0;
            ctx.fillStyle = fillCol;
            ctx.fillText(word, x, baselineY);
            ctx.restore();

            if (typeof ctx.filter !== 'undefined') ctx.filter = 'none';
            ctx.restore();
          }

          x += wordW + spaceW;
          continue;
        }

        // Style 9 (Hormozi Punch)
        if (highlightMode === 'hormozi' || style.id === 9 || style.name === 'Hormozi Punch') {
          const boxCol = style.wordBoxColor || style.activeColor || '#22C55E';
          if (isHighlighted) {
            ctx.save();
            const padX = fontSize * 0.18;
            const padY = fontSize * (wordIsIndic ? 0.18 : 0.14);
            const boxH = contentH + padY * 2;
            const boxY = textY - boxH / 2;
            const br = fontSize * 0.22;

            ctx.fillStyle = boxCol;
            ctx.shadowColor = 'rgba(0,0,0,0.6)';
            ctx.shadowBlur = dropShadow > 0 ? Math.max(8, dropShadow * 1.3) : 14 * unitScale;
            ctx.shadowOffsetY = dropShadow > 0 ? Math.max(2, dropShadow * 0.38) : 4 * unitScale;
            ctx.beginPath();
            ctx.roundRect(x - padX, boxY, wordW + padX * 2, boxH, br);
            ctx.fill();

            ctx.shadowColor = 'transparent';
            ctx.shadowBlur = 0;
            ctx.shadowOffsetY = 0;
            if (wStroke > 0) {
              ctx.strokeStyle = strokeCol;
              ctx.lineWidth = wStroke * 1.5;
              ctx.strokeText(word, x, baselineY);
            }
            ctx.fillStyle = '#000000';
            ctx.fillText(word, x, baselineY);
            ctx.restore();
          } else {
            const inactiveCol = style.color || style.fontColor || '#FFFFFF';
            if (dropShadow > 0) {
              ctx.save();
              ctx.shadowColor = 'rgba(0,0,0,0.90)';
              ctx.shadowBlur = dropShadow * 1.5;
              ctx.shadowOffsetY = dropShadow * 0.4;
              ctx.fillStyle = inactiveCol;
              ctx.fillText(word, x, baselineY);
              ctx.restore();
            }
            if (wStroke > 0) {
              ctx.save();
              ctx.strokeStyle = strokeCol;
              ctx.lineWidth = wStroke * 2;
              ctx.strokeText(word, x, baselineY);
              ctx.restore();
            }
            ctx.save();
            ctx.fillStyle = inactiveCol;
            ctx.fillText(word, x, baselineY);
            ctx.restore();
          }
          x += wordW + spaceW;
          continue;
        }

        // Style 10 (Highlighter Marker)
        if (highlightMode === 'highlighter' || style.id === 10 || style.name === 'Highlighter Marker') {
          const boxCol = style.wordBoxColor || '#FEF08A';
          if (isHighlighted) {
            ctx.save();
            const padX = fontSize * 0.24;
            const padY = fontSize * (wordIsIndic ? 0.16 : 0.12);
            const boxH = contentH + padY * 2;
            const centerX = x + wordW / 2;
            const centerY = textY;

            ctx.translate(centerX, centerY);
            ctx.rotate(-0.038);
            ctx.fillStyle = boxCol;
            ctx.shadowColor = 'rgba(0,0,0,0.25)';
            ctx.shadowBlur = dropShadow > 0 ? Math.max(4, dropShadow) : 8 * unitScale;
            ctx.beginPath();
            ctx.roundRect(-wordW / 2 - padX, -boxH / 2, wordW + padX * 2, boxH, fontSize * 0.14);
            ctx.fill();

            ctx.shadowColor = 'transparent';
            ctx.shadowBlur = 0;
            ctx.fillStyle = '#18181B';
            ctx.fillText(word, -wordW / 2, baselineY - textY);
            ctx.restore();
          } else {
            const inactiveCol = style.color || style.fontColor || '#FFFFFF';
            ctx.save();
            ctx.shadowColor = 'rgba(0,0,0,0.85)';
            ctx.shadowBlur = dropShadow > 0 ? dropShadow * 1.5 : 8 * unitScale;
            ctx.shadowOffsetY = dropShadow > 0 ? dropShadow * 0.45 : 3 * unitScale;
            ctx.fillStyle = inactiveCol;
            ctx.fillText(word, x, baselineY);
            ctx.restore();
            if (wStroke > 0) {
              ctx.save();
              ctx.strokeStyle = strokeCol;
              ctx.lineWidth = wStroke * 2;
              ctx.strokeText(word, x, baselineY);
              ctx.restore();
            }
            ctx.save();
            ctx.fillStyle = inactiveCol;
            ctx.fillText(word, x, baselineY);
            ctx.restore();
          }
          x += wordW + spaceW;
          continue;
        }

        // Style 11 (Editorial Serif)
        if (highlightMode === 'editorial' || style.id === 11 || style.name === 'Editorial Serif') {
          const isSpoken = highlightIdx >= 0 && globalIdx <= highlightIdx;
          const totalCueLen = allCueWords ? allCueWords.length : words.length;
          const isHero = isHighlighted || (highlightIdx === -1 && globalIdx === Math.floor(totalCueLen / 2));
          const goldCol = style.wordBoxColor || style.activeColor || '#EAB308';
          const baseEdCol = style.color || style.fontColor || '#FAF7F2';
          if (isHero) {
            ctx.save();
            if (dropShadow > 0) {
              ctx.shadowColor = 'rgba(0, 0, 0, 0.85)';
              ctx.shadowBlur = dropShadow * 1.5;
              ctx.shadowOffsetY = dropShadow * 0.42;
              ctx.fillStyle = baseEdCol;
              ctx.fillText(word, x, baselineY);
            }
            if (wStroke > 0) {
              ctx.shadowColor = 'transparent';
              ctx.strokeStyle = strokeCol;
              ctx.lineWidth = wStroke * 2;
              ctx.strokeText(word, x, baselineY);
            }
            ctx.shadowColor = goldCol;
            ctx.shadowBlur = fontSize * 0.36;
            ctx.shadowOffsetY = 0;
            ctx.fillStyle = baseEdCol;
            ctx.fillText(word, x, baselineY);

            const lineY = baselineY + fontSize * (wordIsIndic ? 0.28 : 0.20);
            const lineH = Math.max(2, fontSize * 0.08);
            ctx.fillStyle = goldCol;
            ctx.shadowColor = goldCol;
            ctx.shadowBlur = 10 * unitScale;
            ctx.beginPath();
            ctx.roundRect(x, lineY, wordW, lineH, lineH / 2);
            ctx.fill();
            ctx.restore();
          } else {
            ctx.save();
            ctx.shadowColor = 'rgba(0,0,0,0.88)';
            ctx.shadowBlur = dropShadow > 0 ? dropShadow * 1.5 : 8 * unitScale;
            ctx.shadowOffsetY = dropShadow > 0 ? dropShadow * 0.45 : 3 * unitScale;
            if (wStroke > 0) {
              ctx.save();
              ctx.shadowColor = 'transparent';
              ctx.strokeStyle = strokeCol;
              ctx.lineWidth = wStroke * 2;
              ctx.strokeText(word, x, baselineY);
              ctx.restore();
            }
            ctx.fillStyle = isSpoken ? baseEdCol : 'rgba(250, 247, 242, 0.58)';
            ctx.fillText(word, x, baselineY);
            ctx.restore();
          }
          x += wordW + spaceW;
          continue;
        }

        // Style 12 (Sticker Cutout)
        if (isStickerStyle) {
          const colors = ['#FFFFFF', '#FEF08A', '#FB7185'];
          const rots = [-0.04, 0.03, -0.025];
          const textCols = ['#000000', '#000000', '#FFFFFF'];
          const bgCol = colors[globalIdx % colors.length];
          const rot = rots[globalIdx % rots.length];
          const tCol = textCols[globalIdx % textCols.length];

          const padX = fontSize * 0.24;
          const padY = fontSize * (wordIsIndic ? 0.16 : 0.13);
          const boxW = wordW + padX * 2;
          const boxH = contentH + padY * 2;
          const centerX = x + padX + wordW / 2;

          ctx.save();
          ctx.translate(centerX, textY - (isHighlighted ? fontSize * 0.05 : 0));
          if (isHighlighted) {
            ctx.scale(1.03, 1.03);
          }
          ctx.rotate(rot);

          ctx.fillStyle = bgCol;
          ctx.shadowColor = isHighlighted ? 'rgba(0,0,0,0.82)' : 'rgba(0,0,0,0.58)';
          ctx.shadowBlur = dropShadow > 0 ? dropShadow * (isHighlighted ? 1.5 : 1.1) : 12 * unitScale;
          ctx.shadowOffsetY = dropShadow > 0 ? dropShadow * (isHighlighted ? 0.55 : 0.38) : 5 * unitScale;
          ctx.beginPath();
          ctx.roundRect(-boxW / 2, -boxH / 2, boxW, boxH, fontSize * 0.10);
          ctx.fill();

          ctx.shadowColor = 'transparent';
          ctx.shadowBlur = 0;
          ctx.shadowOffsetY = 0;
          ctx.strokeStyle = '#000000';
          ctx.lineWidth = Math.max(2, (isHighlighted ? 3.2 : 2.0) * resScale);
          ctx.stroke();

          if (wStroke > 0 && tCol === '#FFFFFF') {
            ctx.strokeStyle = strokeCol;
            ctx.lineWidth = wStroke * 1.4;
            ctx.strokeText(word, -wordW / 2, baselineY - textY);
          }
          ctx.fillStyle = tCol;
          ctx.fillText(word, -wordW / 2, baselineY - textY);
          ctx.restore();

          x += wordW + spaceW + fontSize * 0.52;
          continue;
        }

        // Style 13 (Cinema 35mm)
        if (highlightMode === 'cinema' || style.id === 13 || style.name === 'Cinema 35mm') {
          const baseCinemaCol = style.color || style.fontColor || '#FFE600';
          const actCinemaCol = (style.activeColor && style.activeColor.toUpperCase() !== baseCinemaCol.toUpperCase())
            ? style.activeColor
            : (baseCinemaCol.toUpperCase() === '#FFFFFF' ? '#FFE600' : '#FFFFFF');
          const cinemaCol = isHighlighted ? actCinemaCol : baseCinemaCol;
          const cinemaStroke = style.strokeColor || '#000000';

          if (isHighlighted) {
            const padX = fontSize * 0.15;
            const padY = fontSize * (wordIsIndic ? 0.15 : 0.10);
            const pillH = contentH + padY * 2;
            const pillY = textY - pillH / 2;
            const pillR = Math.max(8 * resScale, fontSize * 0.16);
            ctx.save();
            ctx.fillStyle = (wordBoxColor && wordBoxColor !== 'transparent') ? wordBoxColor : 'rgba(0, 0, 0, 0.58)';
            ctx.shadowColor = 'rgba(0, 0, 0, 0.65)';
            ctx.shadowBlur = Math.max(6, dropShadow * 1.1);
            ctx.shadowOffsetY = Math.max(2, dropShadow * 0.32);
            ctx.beginPath();
            ctx.roundRect(x - padX, pillY, wordW + padX * 2, pillH, pillR);
            ctx.fill();
            ctx.restore();
          }

          if (dropShadow > 0) {
            ctx.save();
            ctx.shadowColor = 'rgba(0, 0, 0, 0.92)';
            ctx.shadowBlur = Math.max(4, dropShadow * 1.35);
            ctx.shadowOffsetY = Math.max(2, dropShadow * 0.40);
            ctx.fillStyle = cinemaCol;
            ctx.fillText(word, x, baselineY);
            ctx.restore();
          }
          if (wStroke > 0) {
            ctx.save();
            ctx.strokeStyle = cinemaStroke;
            ctx.lineWidth = wStroke * 2.0;
            ctx.strokeText(word, x, baselineY);
            ctx.restore();
          }
          ctx.save();
          ctx.fillStyle = cinemaCol;
          ctx.fillText(word, x, baselineY);
          ctx.restore();
          x += wordW + spaceW;
          continue;
        }

        // Style 14 (Typewriter Chapter)
        if (highlightMode === 'typewriter' || style.id === 14 || style.name === 'Typewriter Chapter') {
          const isSpoken = highlightIdx >= 0 && globalIdx <= highlightIdx;
          const activeTwCol = style.activeColor || '#A3E635';
          const spokenTwCol = style.color || style.fontColor || '#FFFFFF';
          const textCol = isHighlighted ? activeTwCol : (isSpoken ? spokenTwCol : '#A1A1AA');
          ctx.save();
          if (dropShadow > 0 && !isHighlighted) {
            ctx.shadowColor = 'rgba(0, 0, 0, 0.85)';
            ctx.shadowBlur = dropShadow * 1.4;
            ctx.shadowOffsetY = dropShadow * 0.4;
            ctx.fillStyle = textCol;
            ctx.fillText(word, x, baselineY);
          }
          if (wStroke > 0) {
            ctx.shadowColor = 'transparent';
            ctx.shadowBlur = 0;
            ctx.strokeStyle = strokeCol;
            ctx.lineWidth = wStroke * 2;
            ctx.strokeText(word, x, baselineY);
          }
          if (isHighlighted) {
            ctx.shadowColor = activeTwCol;
            ctx.shadowBlur = fontSize * 0.3;
            ctx.shadowOffsetY = 0;
          } else {
            ctx.shadowColor = 'transparent';
            ctx.shadowBlur = 0;
          }
          ctx.fillStyle = textCol;
          ctx.fillText(word, x, baselineY);
          ctx.restore();
          x += wordW + spaceW;
          continue;
        }

        // Style 15 (Molten Magma)
        if (highlightMode === 'magma' || style.id === 15 || style.name === 'Molten Magma') {
          ctx.save();
          const magmaGrad = ctx.createLinearGradient(0, textY - contentH / 2, 0, textY + contentH / 2);
          magmaGrad.addColorStop(0, '#FEF08A');
          magmaGrad.addColorStop(0.5, '#F97316');
          magmaGrad.addColorStop(1, '#DC2626');

          const magmaBlur = dropShadow > 0 ? Math.max(fontSize * 0.3, dropShadow * 2.0) : fontSize * 0.45;
          ctx.shadowColor = 'rgba(249, 115, 22, 0.95)';
          ctx.shadowBlur = isHighlighted ? magmaBlur * 1.25 : magmaBlur;
          if (dropShadow > 0) {
            ctx.shadowOffsetY = dropShadow * 0.4;
          }

          if (wStroke > 0) {
            ctx.strokeStyle = style.strokeColor || '#000000';
            ctx.lineWidth = wStroke * 2;
            ctx.strokeText(word, x, baselineY);
          }
          ctx.fillStyle = magmaGrad;
          ctx.fillText(word, x, baselineY);
          ctx.restore();
          x += wordW + spaceW;
          continue;
        }

        // Style 17 (Neon Wireframe Glass)
        if (highlightMode === 'wireframe-glass' || style.id === 17 || style.name === 'Neon Wireframe Glass') {
          const isSpoken = highlightIdx >= 0 && globalIdx <= highlightIdx;
          const baseWfCol = style.color || style.fontColor || '#FFFFFF';
          ctx.save();
          if (dropShadow > 0 && !isHighlighted) {
            ctx.shadowColor = 'rgba(0, 0, 0, 0.80)';
            ctx.shadowBlur = dropShadow * 1.4;
            ctx.shadowOffsetY = dropShadow * 0.4;
            ctx.fillStyle = isSpoken ? baseWfCol : 'rgba(255, 255, 255, 0.65)';
            ctx.fillText(word, x, baselineY);
          }
          if (wStroke > 0) {
            ctx.shadowColor = 'transparent';
            ctx.shadowBlur = 0;
            ctx.strokeStyle = strokeCol;
            ctx.lineWidth = wStroke * 2;
            ctx.strokeText(word, x, baselineY);
          }
          if (isHighlighted) {
            ctx.shadowColor = style.activeColor || '#EC4899';
            ctx.shadowBlur = fontSize * 0.4;
            ctx.shadowOffsetY = 0;
            ctx.fillStyle = baseWfCol;
          } else {
            ctx.shadowColor = 'transparent';
            ctx.shadowBlur = 0;
            ctx.fillStyle = isSpoken ? baseWfCol : 'rgba(255, 255, 255, 0.65)';
          }
          ctx.fillText(word, x, baselineY);
          ctx.restore();
          x += wordW + spaceW;
          continue;
        }

        // Style 18 (HUD Target Viewfinder)
        if (highlightMode === 'hud-target' || style.id === 18 || style.name === 'HUD Target Viewfinder') {
          const hudCol = style.activeColor || '#4ADE80';
          const hudInactive = style.color || style.fontColor || '#E2E8F0';
          if (isHighlighted) {
            ctx.save();
            const padY = fontSize * (wordIsIndic ? 0.12 : 0.08);
            const bw = wordW + fontSize * 0.28;
            const bh = contentH + padY * 2;
            const bx = x - fontSize * 0.14;
            const by = textY - bh / 2;
            const clen = fontSize * 0.18;

            ctx.strokeStyle = hudCol;
            ctx.lineWidth = Math.max(1.5, fontSize * 0.04);
            ctx.shadowColor = hudCol;
            ctx.shadowBlur = dropShadow > 0 ? Math.max(8, dropShadow * 1.4) : 12 * unitScale;

            ctx.beginPath();
            ctx.moveTo(bx, by + clen); ctx.lineTo(bx, by); ctx.lineTo(bx + clen, by);
            ctx.moveTo(bx + bw - clen, by); ctx.lineTo(bx + bw, by); ctx.lineTo(bx + bw, by + clen);
            ctx.moveTo(bx, by + bh - clen); ctx.lineTo(bx, by + bh); ctx.lineTo(bx + clen, by + bh);
            ctx.moveTo(bx + bw - clen, by + bh); ctx.lineTo(bx + bw, by + bh); ctx.lineTo(bx + bw, by + bh - clen);
            ctx.stroke();

            if (wStroke > 0) {
              ctx.save();
              ctx.shadowColor = 'transparent';
              ctx.strokeStyle = strokeCol;
              ctx.lineWidth = wStroke * 2;
              ctx.strokeText(word, x, baselineY);
              ctx.restore();
            }
            ctx.fillStyle = hudCol;
            ctx.fillText(word, x, baselineY);
            ctx.restore();
          } else {
            ctx.save();
            if (dropShadow > 0) {
              ctx.shadowColor = 'rgba(0, 0, 0, 0.85)';
              ctx.shadowBlur = dropShadow * 1.5;
              ctx.shadowOffsetY = dropShadow * 0.42;
              ctx.fillStyle = hudInactive;
              ctx.fillText(word, x, baselineY);
            }
            if (wStroke > 0) {
              ctx.shadowColor = 'transparent';
              ctx.strokeStyle = strokeCol;
              ctx.lineWidth = wStroke * 2;
              ctx.strokeText(word, x, baselineY);
            }
            ctx.shadowColor = 'transparent';
            ctx.fillStyle = hudInactive;
            ctx.fillText(word, x, baselineY);
            ctx.restore();
          }
          x += wordW + spaceW;
          continue;
        }

        // Style 19 (Beast Pop)
        if (highlightMode === 'beast-pop' || style.id === 19 || style.name === 'Beast Pop') {
          const actCol = style.activeColor || '#CCFF00';
          const baseBeastCol = style.color || style.fontColor || '#FFFFFF';
          const beastDrop = Math.max(3, (style.dropShadow ?? 12) * resScale * 0.60);
          ctx.save();
          if (isHighlighted) {
            const centerX = x + wordW / 2;
            const centerY = textY;
            ctx.translate(centerX, centerY);
            ctx.rotate(-2 * Math.PI / 180);
            ctx.translate(-centerX, -centerY);
          }
          ctx.shadowColor = '#000000';
          ctx.shadowBlur = 0;
          ctx.shadowOffsetY = beastDrop;
          ctx.fillStyle = isHighlighted ? actCol : baseBeastCol;
          ctx.fillText(word, x, baselineY);

          ctx.shadowColor = 'rgba(0,0,0,0.95)';
          ctx.shadowBlur = 12 * unitScale;
          ctx.shadowOffsetY = beastDrop * 1.4;
          ctx.strokeStyle = strokeCol;
          ctx.lineWidth = wStroke > 0 ? Math.max(wStroke * 2, fontSize * 0.07) : Math.max(2, fontSize * 0.07);
          ctx.strokeText(word, x, baselineY);
          ctx.fillStyle = isHighlighted ? actCol : baseBeastCol;
          ctx.fillText(word, x, baselineY);
          ctx.restore();
          x += wordW + spaceW;
          continue;
        }

        // Style 20 (Retro VHS 1995)
        if (highlightMode === 'retro-vhs' || style.id === 20 || style.name === 'Retro VHS 1995') {
          const vhsOff = Math.max(2, fontSize * 0.045);
          const baseVhsCol = style.color || style.fontColor || '#F8FAFC';
          ctx.save();
          if (dropShadow > 0) {
            ctx.save();
            ctx.shadowColor = 'rgba(0, 0, 0, 0.90)';
            ctx.shadowBlur = dropShadow * 1.5;
            ctx.shadowOffsetY = dropShadow * 0.45;
            ctx.fillStyle = baseVhsCol;
            ctx.fillText(word, x, baselineY);
            ctx.restore();
          }
          if (isHighlighted) {
            ctx.fillStyle = '#00FFFF';
            ctx.fillText(word, x - vhsOff, baselineY);
            ctx.fillStyle = '#FF0055';
            ctx.fillText(word, x + vhsOff, baselineY);
            if (wStroke > 0) {
              ctx.strokeStyle = strokeCol;
              ctx.lineWidth = wStroke * 2;
              ctx.strokeText(word, x, baselineY);
            }
            ctx.fillStyle = '#FFFFFF';
            ctx.fillText(word, x, baselineY);
          } else {
            ctx.fillStyle = 'rgba(0,255,255,0.35)';
            ctx.fillText(word, x - vhsOff * 0.5, baselineY);
            ctx.fillStyle = 'rgba(255,0,85,0.35)';
            ctx.fillText(word, x + vhsOff * 0.5, baselineY);
            if (wStroke > 0) {
              ctx.strokeStyle = strokeCol;
              ctx.lineWidth = wStroke * 2;
              ctx.strokeText(word, x, baselineY);
            }
            ctx.fillStyle = baseVhsCol;
            ctx.fillText(word, x, baselineY);
          }
          ctx.restore();
          x += wordW + spaceW;
          continue;
        }

        // Style 21 (Vox Fluid Highlighter)
        if (highlightMode === 'vox-highlighter' || style.id === 21 || style.name === 'Vox Fluid Highlighter') {
          const brushCol = style.wordBoxColor || style.activeColor || '#F59E0B';
          const isSpoken = highlightIdx >= 0 && globalIdx <= highlightIdx;
          if (isHighlighted) {
            ctx.save();
            const padX = fontSize * 0.26;
            const padY = fontSize * (wordIsIndic ? 0.16 : 0.12);
            const boxW = wordW + padX * 2;
            const boxH = contentH + padY * 2;
            ctx.fillStyle = brushCol;
            ctx.shadowColor = 'rgba(0,0,0,0.50)';
            ctx.shadowBlur = dropShadow > 0 ? Math.max(6, dropShadow * 1.2) : 12 * unitScale;
            ctx.beginPath();
            ctx.roundRect(x - padX, textY - boxH / 2, boxW, boxH, fontSize * 0.10);
            ctx.fill();
            ctx.restore();

            ctx.save();
            if (wStroke > 0) {
              ctx.strokeStyle = strokeCol;
              ctx.lineWidth = wStroke * 2;
              ctx.strokeText(word, x, baselineY);
            }
            ctx.fillStyle = '#FFFFFF';
            ctx.shadowColor = 'rgba(0,0,0,0.35)';
            ctx.shadowBlur = 2 * unitScale;
            ctx.fillText(word, x, baselineY);
            ctx.restore();
          } else {
            ctx.save();
            ctx.fillStyle = isSpoken ? '#FFFFFF' : 'rgba(255, 255, 255, 0.50)';
            if (isSpoken) {
              ctx.shadowColor = 'rgba(0,0,0,0.80)';
              ctx.shadowBlur = dropShadow > 0 ? Math.max(3, dropShadow) : 3 * unitScale;
            } else {
              ctx.shadowColor = 'rgba(0,0,0,0.60)';
              ctx.shadowBlur = 2 * unitScale;
            }
            if (wStroke > 0) {
              ctx.strokeStyle = strokeCol;
              ctx.lineWidth = wStroke * 2;
              ctx.strokeText(word, x, baselineY);
            }
            ctx.fillText(word, x, baselineY);
            ctx.restore();
          }
          x += wordW + spaceW;
          continue;
        }

        // Style 22 (Velocity Focus)
        if (highlightMode === 'velocity-focus' || style.id === 22 || style.name === 'Velocity Focus') {
          const isCurrent = isHighlighted;
          const isSpoken = highlightIdx >= 0 && globalIdx < highlightIdx;
          const vfCol = style.color || style.fontColor || '#FFFFFF';
          ctx.save();
          if (isCurrent) {
            if (wStroke > 0) {
              ctx.strokeStyle = strokeCol;
              ctx.lineWidth = wStroke * 2;
              ctx.strokeText(word, x, baselineY);
            }
            ctx.shadowColor = 'rgba(255, 255, 255, 0.9)';
            ctx.shadowBlur = fontSize * 0.36;
            ctx.fillStyle = vfCol;
            ctx.fillText(word, x, baselineY);
          } else if (isSpoken) {
            if (dropShadow > 0) {
              ctx.shadowColor = 'rgba(0, 0, 0, 0.85)';
              ctx.shadowBlur = dropShadow * 1.4;
              ctx.shadowOffsetY = dropShadow * 0.4;
            }
            if (wStroke > 0) {
              ctx.strokeStyle = strokeCol;
              ctx.lineWidth = wStroke * 2;
              ctx.strokeText(word, x, baselineY);
            }
            ctx.fillStyle = vfCol;
            ctx.fillText(word, x, baselineY);
          } else {
            ctx.globalAlpha = 0.38;
            if (typeof ctx.filter !== 'undefined') {
              ctx.filter = `blur(${Math.max(1.5, fontSize * 0.04)}px)`;
            }
            ctx.fillStyle = vfCol;
            ctx.fillText(word, x, baselineY);
            if (typeof ctx.filter !== 'undefined') {
              ctx.filter = 'none';
            }
          }
          ctx.restore();
          x += wordW + spaceW;
          continue;
        }

        // Style 24 (3D Comic Slap)
        if (highlightMode === 'comic-slap' || style.id === 24 || style.name === '3D Comic Slap') {
          const isCurrent = isHighlighted;
          const comicOutlineW = Math.max(wStroke * 2, (wordIsIndic ? 0.105 : 0.13) * fontSize);
          ctx.save();
          if (isCurrent) {
            const slapY = baselineY - 0.06 * fontSize;
            const ext1 = 0.12 * fontSize;
            const ext2 = 0.06 * fontSize;
            const accentCol = style.accentColor || '#00E5FF';
            const actFill = style.activeColor || '#FACC15';

            ctx.save();
            ctx.strokeStyle = '#000000';
            ctx.lineWidth = comicOutlineW;
            ctx.strokeText(word, x + ext1, slapY + ext1);
            ctx.fillStyle = accentCol;
            ctx.fillText(word, x + ext1, slapY + ext1);
            ctx.restore();

            ctx.save();
            ctx.strokeStyle = '#000000';
            ctx.lineWidth = comicOutlineW;
            ctx.strokeText(word, x + ext2, slapY + ext2);
            ctx.fillStyle = '#000000';
            ctx.fillText(word, x + ext2, slapY + ext2);
            ctx.restore();

            ctx.strokeStyle = '#000000';
            ctx.lineWidth = comicOutlineW;
            ctx.strokeText(word, x, slapY);
            ctx.fillStyle = actFill;
            ctx.fillText(word, x, slapY);
          } else {
            const blockOff = 0.065 * fontSize;
            const inactFill = style.color || style.fontColor || '#FFFFFF';

            ctx.save();
            ctx.strokeStyle = '#000000';
            ctx.lineWidth = comicOutlineW;
            ctx.strokeText(word, x + blockOff, baselineY + blockOff);
            ctx.fillStyle = '#000000';
            ctx.fillText(word, x + blockOff, baselineY + blockOff);
            ctx.restore();

            ctx.strokeStyle = '#000000';
            ctx.lineWidth = comicOutlineW;
            ctx.strokeText(word, x, baselineY);
            ctx.fillStyle = inactFill;
            ctx.fillText(word, x, baselineY);
          }
          ctx.restore();
          x += wordW + spaceW;
          continue;
        }

        // Style 25 (Frosted Lyric)
        if (highlightMode === 'frosted-lyric' || style.id === 25 || style.name === 'Frosted Lyric') {
          const isCurrent = isHighlighted;
          const isSpoken = highlightIdx >= 0 && globalIdx < highlightIdx;
          const lyricCol = style.color || style.fontColor || '#FFFFFF';
          const actLyricCol = style.activeColor || '#38BDF8';
          ctx.save();
          if (isCurrent) {
            const pillPadX = fontSize * 0.20;
            const pillPadY = fontSize * (wordIsIndic ? 0.16 : 0.12);
            const pillW = wordW + pillPadX * 2;
            const pillH = contentH + pillPadY * 2;
            const pillX = x - pillPadX;
            const pillY = textY - pillH / 2;
            const pillR = pillH / 2;

            ctx.fillStyle = (wordBoxColor && wordBoxColor !== 'transparent') ? wordBoxColor : hexToRgba(actLyricCol, 0.26);
            ctx.beginPath();
            ctx.roundRect(pillX, pillY, pillW, pillH, pillR);
            ctx.fill();
            ctx.strokeStyle = hexToRgba(actLyricCol, 0.68);
            ctx.lineWidth = Math.max(1.2, 1.6 * resScale);
            ctx.stroke();

            if (wStroke > 0) {
              ctx.save();
              ctx.strokeStyle = strokeCol;
              ctx.lineWidth = wStroke * 1.7;
              ctx.strokeText(word, x, baselineY);
              ctx.restore();
            }

            ctx.shadowColor = hexToRgba(actLyricCol, 0.85);
            ctx.shadowBlur = 12 * unitScale;
            ctx.fillStyle = '#FFFFFF';
            ctx.fillText(word, x, baselineY);
          } else {
            ctx.globalAlpha = isSpoken ? 0.94 : 0.68;
            if (wStroke > 0) {
              ctx.save();
              ctx.strokeStyle = strokeCol;
              ctx.lineWidth = wStroke * 1.5;
              ctx.strokeText(word, x, baselineY);
              ctx.restore();
            }
            ctx.fillStyle = lyricCol;
            ctx.fillText(word, x, baselineY);
          }
          ctx.restore();
          x += wordW + spaceW;
          continue;
        }

        // Style 26 (Kinetic Knockout)
        if (highlightMode === 'split-knockout' || style.id === 26 || style.name === 'Kinetic Knockout') {
          const isCurrent = isHighlighted;
          ctx.save();
          if (isCurrent) {
            const voltCol = style.activeColor || '#CCFF00';
            const koStrokeW = Math.max(wStroke * 2, 0.10 * fontSize);
            if (dropShadow > 0) {
              ctx.shadowColor = 'rgba(0, 0, 0, 0.90)';
              ctx.shadowBlur = dropShadow * 1.6;
              ctx.shadowOffsetY = dropShadow * 0.45;
            }
            ctx.strokeStyle = '#000000';
            ctx.lineWidth = koStrokeW;
            ctx.strokeText(word, x, baselineY);

            ctx.shadowColor = 'transparent';
            ctx.shadowBlur = 0;
            ctx.shadowOffsetY = 0;
            ctx.fillStyle = voltCol;
            ctx.fillText(word, x, baselineY);

            const barH = Math.max(3, 0.08 * fontSize);
            const barY = baselineY + fontSize * (wordIsIndic ? 0.24 : 0.14);
            ctx.fillStyle = voltCol;
            ctx.strokeStyle = '#000000';
            ctx.lineWidth = Math.max(1.5, fontSize * 0.03);
            ctx.beginPath();
            ctx.roundRect(x, barY, wordW, barH, barH / 2);
            ctx.stroke();
            ctx.fill();
          } else {
            if (dropShadow > 0) {
              ctx.shadowColor = 'rgba(0, 0, 0, 0.75)';
              ctx.shadowBlur = dropShadow * 1.2;
              ctx.shadowOffsetY = dropShadow * 0.35;
            }
            ctx.fillStyle = 'rgba(255, 255, 255, 0.08)';
            ctx.fillText(word, x, baselineY);
            ctx.strokeStyle = style.color || style.fontColor || '#FFFFFF';
            ctx.lineWidth = Math.max(2, fontSize * 0.055);
            ctx.strokeText(word, x, baselineY);
          }
          ctx.restore();
          x += wordW + spaceW;
          continue;
        }

        // Style 27 (Gradient Badge)
        if (highlightMode === 'gradient-badge' || style.id === 27 || style.name === 'Gradient Badge') {
          const isCurrent = isHighlighted;
          ctx.save();
          if (isCurrent) {
            const padX = fontSize * 0.20;
            const padY = fontSize * (wordIsIndic ? 0.18 : 0.13);
            const boxW = wordW + padX * 2;
            const boxH = contentH + padY * 2;
            const boxX = x - padX;
            const boxY = textY - boxH / 2;
            const br = Math.min(boxH / 2, 0.30 * fontSize);

            const grad = ctx.createLinearGradient(boxX, boxY, boxX + boxW, boxY + boxH);
            grad.addColorStop(0, '#6366F1');
            grad.addColorStop(0.5, style.activeColor || style.wordBoxColor || '#EC4899');
            grad.addColorStop(1, '#F59E0B');

            ctx.shadowColor = 'rgba(0, 0, 0, 0.58)';
            ctx.shadowBlur = dropShadow > 0 ? Math.max(8, dropShadow * 1.35) : 12 * unitScale;
            ctx.shadowOffsetY = Math.max(2, 4 * unitScale);
            ctx.fillStyle = grad;
            ctx.beginPath();
            ctx.roundRect(boxX, boxY, boxW, boxH, br);
            ctx.fill();

            ctx.shadowColor = 'transparent';
            ctx.shadowBlur = 0;
            ctx.shadowOffsetY = 0;
            ctx.strokeStyle = '#FFFFFF';
            ctx.lineWidth = Math.max(1.5, 2.2 * resScale);
            ctx.stroke();

            if (wStroke > 0) {
              ctx.save();
              ctx.strokeStyle = strokeCol;
              ctx.lineWidth = wStroke * 1.6;
              ctx.strokeText(word, x, baselineY);
              ctx.restore();
            }

            ctx.shadowColor = 'rgba(0, 0, 0, 0.45)';
            ctx.shadowBlur = 3 * unitScale;
            ctx.shadowOffsetY = 1.5 * unitScale;
            ctx.fillStyle = '#FFFFFF';
            ctx.fillText(word, x, baselineY);
          } else {
            const inactCol = style.color || style.fontColor || '#FFFFFF';
            ctx.shadowColor = 'rgba(0, 0, 0, 0.85)';
            ctx.shadowBlur = dropShadow > 0 ? dropShadow * 1.45 : 10 * unitScale;
            ctx.shadowOffsetY = dropShadow > 0 ? dropShadow * 0.42 : 3 * unitScale;
            ctx.fillStyle = inactCol;
            ctx.fillText(word, x, baselineY);

            ctx.shadowColor = 'transparent';
            ctx.shadowBlur = 0;
            ctx.shadowOffsetY = 0;
            ctx.strokeStyle = strokeCol;
            ctx.lineWidth = Math.max(wStroke * 2, fontSize * 0.06);
            ctx.strokeText(word, x, baselineY);
            ctx.fillStyle = inactCol;
            ctx.fillText(word, x, baselineY);
          }
          ctx.restore();
          x += wordW + spaceW;
          continue;
        }

        // Style 28 (Docu Split-Bar)
        if (highlightMode === 'docu-bar' || style.id === 28 || style.name === 'Docu Split-Bar') {
          const isCurrent = isHighlighted;
          const isSpoken = highlightIdx >= 0 && globalIdx < highlightIdx;
          const markerCol = style.activeColor || '#FACC15';
          const spokenDocCol = style.color || style.fontColor || '#F8FAFC';
          const futureDocCol = style.inactiveColor || '#CBD5E1';

          ctx.save();
          if (isCurrent) {
            const pillPadX = fontSize * 0.14;
            const pillPadY = fontSize * (wordIsIndic ? 0.14 : 0.10);
            const pillW = wordW + pillPadX * 2;
            const pillH = Math.max(inkH + pillPadY * 2, contentH + pillPadY * 1.4);
            const pillX = x - pillPadX;
            const pillY = textY - pillH / 2;
            const pillR = Math.max(6 * resScale, fontSize * 0.14);

            ctx.save();
            ctx.fillStyle = hexToRgba(markerCol, 0.20);
            ctx.beginPath();
            ctx.roundRect(pillX, pillY, pillW, pillH, pillR);
            ctx.fill();

            const barH = Math.max(2.5, fontSize * 0.06);
            ctx.fillStyle = markerCol;
            ctx.beginPath();
            ctx.roundRect(x, pillY + pillH - barH, wordW, barH, barH / 2);
            ctx.fill();
            ctx.restore();

            if (wStroke > 0) {
              ctx.strokeStyle = strokeCol;
              ctx.lineWidth = wStroke * 2;
              ctx.strokeText(word, x, baselineY);
            }
            ctx.fillStyle = markerCol;
            ctx.fillText(word, x, baselineY);
          } else {
            if (wStroke > 0) {
              ctx.strokeStyle = strokeCol;
              ctx.lineWidth = wStroke * 1.8;
              ctx.strokeText(word, x, baselineY);
            }
            ctx.fillStyle = isSpoken ? spokenDocCol : futureDocCol;
            ctx.fillText(word, x, baselineY);
          }
          ctx.restore();
          x += wordW + spaceW;
          continue;
        }

        // Style 29 (Cyber Glitch)
        if (highlightMode === 'cyber-slice' || style.id === 29 || style.name === 'Cyber Glitch') {
          const isCurrent = isHighlighted;
          const coreCol = isCurrent ? (style.activeColor || '#FFFFFF') : (style.color || style.fontColor || '#FFFFFF');
          const magentaGhost = style.accentColor || '#FF0055';
          ctx.save();
          if (isCurrent) {
            const padX = fontSize * 0.16;
            const padY = fontSize * (wordIsIndic ? 0.16 : 0.12);
            const plateW = wordW + padX * 2;
            const plateH = contentH + padY * 2;
            const plateX = x - padX;
            const plateY = textY - plateH / 2;
            const tickW = Math.max(2, fontSize * 0.045);

            ctx.fillStyle = 'rgba(5, 10, 20, 0.75)';
            ctx.fillRect(plateX, plateY, plateW, plateH);
            ctx.fillStyle = '#00F0FF';
            ctx.fillRect(plateX, plateY, tickW, plateH);
            ctx.fillRect(plateX + plateW - tickW, plateY, tickW, plateH);

            const topClipY = textY - contentH;
            const botClipY = textY + contentH;
            const splitY = baselineY - contentH * 0.45;
            const sliceShift = 0.04 * fontSize;
            const ghostShift = 0.055 * fontSize;

            ctx.save();
            ctx.beginPath();
            ctx.rect(x - fontSize, topClipY, wordW + fontSize * 2, splitY - topClipY);
            ctx.clip();
            ctx.fillStyle = '#00F0FF';
            ctx.fillText(word, x - ghostShift, baselineY);
            if (wStroke > 0) {
              ctx.strokeStyle = strokeCol;
              ctx.lineWidth = wStroke * 1.5;
              ctx.strokeText(word, x + sliceShift, baselineY);
            }
            ctx.fillStyle = coreCol;
            ctx.fillText(word, x + sliceShift, baselineY);
            ctx.restore();

            ctx.save();
            ctx.beginPath();
            ctx.rect(x - fontSize, splitY, wordW + fontSize * 2, botClipY - splitY);
            ctx.clip();
            ctx.fillStyle = magentaGhost;
            ctx.fillText(word, x + ghostShift, baselineY);
            if (wStroke > 0) {
              ctx.strokeStyle = strokeCol;
              ctx.lineWidth = wStroke * 1.5;
              ctx.strokeText(word, x - sliceShift, baselineY);
            }
            ctx.fillStyle = coreCol;
            ctx.fillText(word, x - sliceShift, baselineY);
            ctx.restore();
          } else {
            if (dropShadow > 0) {
              ctx.shadowColor = 'rgba(0, 0, 0, 0.85)';
              ctx.shadowBlur = dropShadow * 1.5;
              ctx.shadowOffsetY = dropShadow * 0.42;
              ctx.fillStyle = coreCol;
              ctx.fillText(word, x, baselineY);
            }
            ctx.shadowColor = 'transparent';
            ctx.strokeStyle = strokeCol;
            ctx.lineWidth = Math.max(wStroke * 2, fontSize * 0.06);
            ctx.strokeText(word, x, baselineY);
            ctx.fillStyle = coreCol;
            ctx.fillText(word, x, baselineY);
          }
          ctx.restore();
          x += wordW + spaceW;
          continue;
        }

        // Style 8 (Luxe Italic)
        if (highlightMode === 'luxe-italic' || style.id === 8 || style.name === 'Luxe Italic') {
          const heroIdx = getLuxeHeroIndex(allCueWords || words);
          const isHero = globalIdx === heroIdx;
          const isCurrent = isHighlighted;
          const glowCol = style.glowColor || style.activeColor || '#FFFFFF';
          const glowBlur = (style.glowBlur ?? 6) * unitScale;

          let animEased = 1.0;
          if (typeof animProgress === 'number' && animProgress < 1.0) {
            const p = Math.max(0, Math.min(1, animProgress));
            animEased = 1 - Math.pow(1 - p, 2.5);
          }

          if (isHero) {
            ctx.save();
            const italicFontSize = Math.round(fontSize * 1.10);
            const heroItalicFont = `italic 700 ${italicFontSize}px "Playfair Display", ${scriptFallbacks}, serif`;
            ctx.font = heroItalicFont;
            ctx.textBaseline = 'alphabetic';

            let heroAlpha = 0.35;
            let glowIntensity = 0.0;
            if (highlightIdx >= 0 && globalIdx < highlightIdx) {
              heroAlpha = 1.0;
              glowIntensity = 1.0;
            } else if (isCurrent) {
              heroAlpha = 0.35 + (0.65 * animEased);
              glowIntensity = animEased;
            }

            if (glowIntensity > 0 && glowBlur > 0) {
              ctx.save();
              ctx.fillStyle = '#FFFFFF';
              ctx.globalAlpha = heroAlpha * 0.40;
              ctx.shadowColor = glowCol;
              ctx.shadowBlur = glowBlur * glowIntensity * 0.75;
              ctx.fillText(word, x + fontSize * 0.06, baselineY);
              ctx.restore();
            }

            ctx.save();
            ctx.font = heroItalicFont;
            ctx.textBaseline = 'alphabetic';
            ctx.shadowColor = 'rgba(0, 0, 0, 0.85)';
            ctx.shadowBlur = 3 * unitScale;
            ctx.shadowOffsetY = 1 * unitScale;
            ctx.fillStyle = '#FFFFFF';
            ctx.globalAlpha = heroAlpha;
            ctx.fillText(word, x + fontSize * 0.06, baselineY);
            ctx.restore();

            ctx.restore();

            ctx.font = heroItalicFont;
            const italicW = ctx.measureText(word).width + fontSize * 0.14;
            ctx.font = font;
            x += italicW + spaceW;
            continue;
          }

          let wordAlpha = 0.35;
          if (highlightIdx >= 0 && globalIdx < highlightIdx) {
            wordAlpha = 1.0;
          } else if (isCurrent) {
            wordAlpha = 0.35 + (0.65 * animEased);
          }

          ctx.save();
          ctx.font = wordFont;
          ctx.textBaseline = 'alphabetic';
          ctx.fillStyle = '#FFFFFF';
          ctx.globalAlpha = wordAlpha;

          if (dropShadow > 0 && wordAlpha > 0.5) {
            ctx.shadowColor = 'rgba(0, 0, 0, 0.90)';
            ctx.shadowBlur = dropShadow * 1.5;
            ctx.shadowOffsetY = dropShadow * 0.45;
          }
          ctx.fillText(word, x, baselineY);
          ctx.restore();

          x += wordW + spaceW;
          continue;
        }

        let textFillColor;
        if (highlightMode === 'progressive' || style.id === 5 || style.name === 'White Box') {
          const isCurrent = isHighlighted;
          const isSpoken = highlightIdx >= 0 && globalIdx < highlightIdx;
          // Zeno: the spoken colour follows the style (black on a light box, white on a dark box).
          const progHex = /^#[0-9a-fA-F]{6}$/.test(String(style.color || '')) ? String(style.color) : '#000000';
          const progRgb = [1, 3, 5].map(o => parseInt(progHex.slice(o, o + 2), 16));
          const progLight = (progRgb[0] + progRgb[1] + progRgb[2]) / 3 > 127;
          const progFrom = progLight ? 150 : 102;
          if (isSpoken) {
            textFillColor = progHex;
          } else if (isCurrent) {
            if (typeof animProgress === 'number' && animProgress < 1.0) {
              const p = Math.max(0, Math.min(1, animProgress));
              const eased = 1 - Math.pow(1 - p, 2.5);
              const mix = progRgb.map(v => Math.round(progFrom + (v - progFrom) * eased));
              textFillColor = `rgb(${mix[0]}, ${mix[1]}, ${mix[2]})`;
            } else {
              textFillColor = progHex;
            }
          } else {
            textFillColor = style.inactiveColor || 'rgba(0, 0, 0, 0.40)';
          }
        } else if (style.id === 7 || style.name === 'Capsule Box') {
          const rawBg = String(style.bg || style.bgColor || '#1C1C1E').trim().toUpperCase();
          const isDarkTheme = rawBg !== '#FFFFFF' && !rawBg.startsWith('RGBA(255, 255, 255') && !rawBg.startsWith('RGBA(255,255,255');
          const baseCol = isDarkTheme ? '#FFFFFF' : '#000000';
          if (isHighlighted) {
            if (typeof animProgress === 'number' && animProgress < 1.0) {
              const p = Math.max(0, Math.min(1, animProgress));
              const eased = 1 - Math.pow(1 - p, 2.5);
              const hex = (activeColor || '#00E5FF').replace('#', '');
              const num = parseInt(hex.length === 3 ? hex.split('').map(c => c + c).join('') : hex, 16) || 0;
              const targetR = (num >> 16) & 255, targetG = (num >> 8) & 255, targetB = num & 255;
              const startR = isDarkTheme ? 255 : 0, startG = isDarkTheme ? 255 : 0, startB = isDarkTheme ? 255 : 0;
              const curR = Math.round(startR + (targetR - startR) * eased);
              const curG = Math.round(startG + (targetG - startG) * eased);
              const curB = Math.round(startB + (targetB - startB) * eased);
              textFillColor = `rgb(${curR}, ${curG}, ${curB})`;
            } else {
              textFillColor = activeColor || '#00E5FF';
            }
          } else {
            textFillColor = baseCol;
          }
        } else if (highlightMode === 'word-box') {
          const isLight = ['#FFFFFF', '#FEF08A', '#FACC15', '#4ADE80'].includes((wordBoxColor || '').toUpperCase());
          textFillColor = (isHighlighted && isLight ? '#000000' : fontColor);
        } else {
          textFillColor = isHighlighted ? activeColor : fontColor;
        }

        // Pass 1: Crisp 2-tier Broadcast Drop Shadow (ambient diffusion + tight contact occlusion)
        if (dropShadow > 0) {
          ctx.save();
          ctx.shadowColor = 'rgba(0, 0, 0, 0.78)';
          ctx.shadowBlur = Math.max(4, dropShadow * 1.35);
          ctx.shadowOffsetX = 0;
          ctx.shadowOffsetY = Math.max(2, dropShadow * 0.38);
          ctx.fillStyle = textFillColor;
          ctx.fillText(word, x, baselineY);

          ctx.shadowColor = 'rgba(0, 0, 0, 0.92)';
          ctx.shadowBlur = Math.max(2, dropShadow * 0.45);
          ctx.shadowOffsetY = Math.max(1.5, dropShadow * 0.20);
          ctx.fillText(word, x, baselineY);
          ctx.restore();
        }

        // Pass 2: Stroke Outline (crisp outer stroke with round joins)
        if (wStroke > 0) {
          ctx.save();
          ctx.shadowColor = 'transparent';
          ctx.shadowBlur = 0;
          ctx.strokeStyle = strokeCol;
          ctx.lineWidth = wStroke * 2;
          ctx.lineJoin = 'round';
          ctx.lineCap = 'round';
          ctx.strokeText(word, x, baselineY);
          ctx.restore();
        }

        // Pass 3: Foreground Fill
        ctx.save();
        ctx.shadowColor = 'transparent';
        ctx.shadowBlur = 0;
        ctx.fillStyle = textFillColor;
        ctx.fillText(word, x, baselineY);
        ctx.restore();

        x += wordW + spaceW;
      } finally {
        if (didPopSave) ctx.restore();
        ctx.font = font;
      }
    }
  }

  /**
   * Write a PNG ArrayBuffer to disk. Uses CEP's Node.js fs module.
   */
  async function writePngToDisk(outputPath, arrayBuffer) {
    const fsMod = getFs();
    if (!fsMod) throw new Error('Node.js fs not available (not running in CEP)');
    const buffer = Buffer.from(arrayBuffer);
    await fsMod.promises.writeFile(outputPath, buffer);
  }

  /**
   * Ensure an output directory exists.
   */
  function ensureDir(dirPath) {
    const fsMod = getFs();
    if (!fsMod) throw new Error('Node.js fs not available');
    if (!fsMod.existsSync(dirPath)) {
      fsMod.mkdirSync(dirPath, { recursive: true });
    }
    return dirPath;
  }

  /**
   * Parse a time value to seconds.
   */
  function parseTimeToSeconds(t) {
    if (typeof t === 'number') return t;
    if (typeof t !== 'string') return 0;
    const parts = t.split(':');
    if (parts.length === 3) return parseFloat(parts[0]) * 3600 + parseFloat(parts[1]) * 60 + parseFloat(parts[2]);
    if (parts.length === 2) return parseFloat(parts[0]) * 60 + parseFloat(parts[1]);
    return parseFloat(t) || 0;
  }

  /**
   * High-level: Render all caption cues to PNG files on disk.
   * Replaces the server's /api/render-pngs endpoint entirely.
   */
  async function renderAllCuesToPngs({
    cues,
    width = 1080,
    height = 1920,
    fullHeight,
    bandY0 = 0,
    fps = 25,
    style = {},
    outputDir,
    onProgress,
  }) {
    if (!cues || !Array.isArray(cues) || cues.length === 0) {
      throw new Error('No caption cues to render');
    }

    if (typeof document !== 'undefined' && document.fonts && document.fonts.ready) {
      try { await document.fonts.ready; } catch (e) {}
    }

    ensureDir(outputDir);

    const frameStep = 1.0 / Math.max(24, Number(fps) || 25);
    const minWordDur = Math.min(frameStep, 0.025);
    const enablePop = style.wordPop !== false;

    const frames = [];
    let rendered = 0;
    const BATCH_SIZE = 6;

    for (let i = 0; i < cues.length; i++) {
      const cue = cues[i];
      const cueStart = parseTimeToSeconds(cue.startTime);
      let cueEnd = parseTimeToSeconds(cue.endTime);
      if (cueEnd <= cueStart) cueEnd = cueStart + Math.max(frameStep * 2, 0.20);

      // Skip empty/whitespace-only words while preserving cue index mapping cleanly
      const rawWordList = Array.isArray(cue.words) && cue.words.length > 0 ? cue.words : [];
      const wordList = rawWordList.filter(w => {
        const wText = typeof w === 'string' ? w : String(w?.text ?? w?.word ?? '');
        return wText.trim().length > 0;
      });
      const totalWords = wordList.length;

      if (totalWords > 0 && typeof wordList[0] === 'object' && wordList[0].startTime !== undefined) {
        const rawFirstStart = parseTimeToSeconds(wordList[0].startTime);
        const maxLeadEnd = Math.max(cueStart, cueEnd - totalWords * minWordDur);
        const firstWordStart = Math.min(maxLeadEnd, Math.max(cueStart, rawFirstStart));
        const wordTasks = [];

        // Pre-speech lead-in frame (hidden before word 0 is spoken)
        let currentStart = cueStart;
        if (firstWordStart > cueStart + Math.max(0.04, frameStep)) {
          wordTasks.push({
            cue: { ...cue, words: wordList },
            cueIndex: i,
            wIdx: -1,
            wObj: { text: cue.text || '' },
            wStart: cueStart,
            wEnd: firstWordStart,
            animProgress: 0.0,
            wordScale: 1.0,
            frameTag: `cue_${i}_pre.png`,
            subIndex: 0,
            totalWords,
          });
          currentStart = firstWordStart;
        }

        for (let wIdx = 0; wIdx < totalWords; wIdx++) {
          const wObj = wordList[wIdx];
          const wText = typeof wObj === 'string' ? wObj : String(wObj?.text ?? wObj?.word ?? '');
          if (!wText.trim()) continue;

          const isLast = wIdx === totalWords - 1;
          const remAfter = Math.max(0, totalWords - 1 - wIdx);
          const maxAllowedEnd = Math.max(currentStart + 0.012, cueEnd - remAfter * minWordDur);

          let wEnd;
          if (isLast) {
            wEnd = Math.max(currentStart + minWordDur, cueEnd);
          } else {
            const nextWord = wordList[wIdx + 1];
            const nextStart = parseTimeToSeconds(nextWord ? nextWord.startTime : 0);
            const explicitEnd = parseTimeToSeconds(wObj?.endTime ?? 0);
            const rawEnd = nextStart > currentStart
              ? nextStart
              : (explicitEnd > currentStart ? explicitEnd : currentStart + frameStep);
            wEnd = Math.min(maxAllowedEnd, Math.max(currentStart + minWordDur, rawEnd));
          }

          const wStart = currentStart;
          currentStart = wEnd;
          const wDur = Math.max(0.012, wEnd - wStart);

          // 3-subframe active-word karaoke pop (1.05 -> 1.09 -> 1.00 over first ~110ms)
          if (enablePop && wDur >= 0.14 && wDur >= frameStep * 3) {
            const d0 = Math.max(frameStep, Math.min(0.044, wDur * 0.26));
            const d1 = Math.max(frameStep, Math.min(0.064, wDur * 0.34));
            if (wDur - (d0 + d1) >= frameStep * 0.85) {
              const t1 = wStart + d0;
              const t2 = t1 + d1;
              wordTasks.push(
                {
                  cue: { ...cue, words: wordList },
                  cueIndex: i,
                  wIdx,
                  wObj,
                  wStart,
                  wEnd: t1,
                  animProgress: 0.55,
                  wordScale: 1.03,
                  frameTag: `cue_${i}_w${wIdx}_s0.png`,
                  subIndex: 0,
                  totalWords,
                },
                {
                  cue: { ...cue, words: wordList },
                  cueIndex: i,
                  wIdx,
                  wObj,
                  wStart: t1,
                  wEnd: t2,
                  animProgress: 0.92,
                  wordScale: 1.05,
                  frameTag: `cue_${i}_w${wIdx}_s1.png`,
                  subIndex: 1,
                  totalWords,
                },
                {
                  cue: { ...cue, words: wordList },
                  cueIndex: i,
                  wIdx,
                  wObj,
                  wStart: t2,
                  wEnd,
                  animProgress: 1.0,
                  wordScale: 1.00,
                  frameTag: `cue_${i}_w${wIdx}_s2.png`,
                  subIndex: 2,
                  totalWords,
                }
              );
              continue;
            }
          }

          if (enablePop && wDur >= frameStep * 2) {
            const d0 = Math.min(0.055, Math.max(frameStep, wDur * 0.45));
            const t1 = wStart + d0;
            wordTasks.push(
              {
                cue: { ...cue, words: wordList },
                cueIndex: i,
                wIdx,
                wObj,
                wStart,
                wEnd: t1,
                animProgress: 0.85,
                wordScale: 1.08,
                frameTag: `cue_${i}_w${wIdx}_s0.png`,
                subIndex: 0,
                totalWords,
              },
              {
                cue: { ...cue, words: wordList },
                cueIndex: i,
                wIdx,
                wObj,
                wStart: t1,
                wEnd,
                animProgress: 1.0,
                wordScale: 1.00,
                frameTag: `cue_${i}_w${wIdx}_s1.png`,
                subIndex: 1,
                totalWords,
              }
            );
          } else {
            wordTasks.push({
              cue: { ...cue, words: wordList },
              cueIndex: i,
              wIdx,
              wObj,
              wStart,
              wEnd,
              animProgress: 1.0,
              wordScale: enablePop ? 1.06 : 1.00,
              frameTag: `cue_${i}_w${wIdx}_s0.png`,
              subIndex: 0,
              totalWords,
            });
          }
        }

        for (let batchStart = 0; batchStart < wordTasks.length; batchStart += BATCH_SIZE) {
          const batch = wordTasks.slice(batchStart, batchStart + BATCH_SIZE);
          await Promise.all(batch.map(async (task) => {
            const pngData = await renderCueToPng({
              cue: { ...task.cue, highlightIndex: task.wIdx },
              width,
              height,
              fullHeight,
              bandY0,
              style,
              animProgress: task.animProgress,
              wordScale: task.wordScale ?? 1.0,
            });

            const frameName = task.frameTag || `cue_${task.cueIndex}_w${task.wIdx}.png`;
            const pathMod = getPath();
            const framePath = pathMod ? pathMod.join(outputDir, frameName) : `${outputDir}/${frameName}`;
            await writePngToDisk(framePath, pngData);

            const taskWordText = typeof task.wObj === 'string'
              ? task.wObj
              : String(task.wObj?.text ?? task.wObj?.word ?? task.cue.text ?? '');

            frames.push({
              path: framePath,
              startSec: Number(task.wStart.toFixed(4)),
              endSec: Number(task.wEnd.toFixed(4)),
              startMs: Math.round(task.wStart * 1000),
              durationMs: Math.max(1, Math.round((task.wEnd - task.wStart) * 1000)),
              text: taskWordText,
              captionIndex: task.cueIndex,
              stepIndex: task.wIdx,
              subIndex: task.subIndex || 0,
              wordScale: task.wordScale ?? 1.0,
            });

            rendered++;
            if (onProgress) onProgress(rendered, wordTasks.length);
          }));
        }
      } else {
        // No word-level timestamps — one PNG per cue (skip if cue has no non-empty words)
        const fallbackWords = wordList.length > 0
          ? wordList
          : String(cue.text || '').split(/\s+/).filter(Boolean);
        if (fallbackWords.length === 0) {
          rendered++;
          if (onProgress) onProgress(rendered, cues.length);
          continue;
        }

        const pngData = await renderCueToPng({
          cue: { ...cue, words: wordList.length > 0 ? wordList : undefined, highlightIndex: 0 },
          width,
          height,
          fullHeight,
          bandY0,
          style,
          animProgress: 1.0,
          wordScale: 1.0,
        });

        const frameName = `cue_${i}.png`;
        const pathMod = getPath();
        const framePath = pathMod ? pathMod.join(outputDir, frameName) : `${outputDir}/${frameName}`;
        await writePngToDisk(framePath, pngData);

        frames.push({
          path: framePath,
          startSec: Number(cueStart.toFixed(4)),
          endSec: Number(cueEnd.toFixed(4)),
          startMs: Math.round(cueStart * 1000),
          durationMs: Math.max(1, Math.round((cueEnd - cueStart) * 1000)),
          text: cue.text || '',
          captionIndex: i,
          stepIndex: 0,
          subIndex: 0,
          wordScale: 1.0,
        });

        rendered++;
        if (onProgress) onProgress(rendered, cues.length);
      }
    }

    frames.sort((a, b) => (a.startSec ?? a.startMs / 1000) - (b.startSec ?? b.startMs / 1000) || a.captionIndex - b.captionIndex || a.stepIndex - b.stepIndex || (a.subIndex || 0) - (b.subIndex || 0));
    return { frames, framesCount: frames.length, bandY0, bandHeight: height };
  }

  return {
    renderCueToPng,
    writePngToDisk,
    renderAllCuesToPngs,
    parseTimeToSeconds,
    ensureDir,
    ANIMATION_PRESETS,
  };
})();

if (typeof window !== 'undefined') window.CaptionRenderer = CaptionRenderer;
if (typeof module !== 'undefined' && module.exports) module.exports = { CaptionRenderer };
