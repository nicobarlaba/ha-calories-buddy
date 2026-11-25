/** Zero-dependency pure-JS EAN-13 / EAN-8 / UPC-A photo barcode decoder (works on plain HTTP & iOS/Android). */

const L_RUNS = [
  [3, 2, 1, 1], // 0
  [2, 2, 2, 1], // 1
  [2, 1, 2, 2], // 2
  [1, 4, 1, 1], // 3
  [1, 1, 3, 2], // 4
  [1, 2, 3, 1], // 5
  [1, 1, 1, 4], // 6
  [1, 3, 1, 2], // 7
  [1, 2, 1, 3], // 8
  [3, 1, 1, 2], // 9
];

const G_RUNS = [
  [1, 1, 2, 3], // 0
  [1, 2, 2, 2], // 1
  [2, 2, 1, 2], // 2
  [1, 1, 4, 1], // 3
  [2, 3, 1, 1], // 4
  [1, 3, 2, 1], // 5
  [4, 1, 1, 1], // 6
  [2, 1, 3, 1], // 7
  [3, 1, 2, 1], // 8
  [2, 1, 1, 3], // 9
];

// R-code run proportions match L-code run proportions (just bar/space inverted)
const R_RUNS = L_RUNS;

const FIRST_DIGIT_PARITY = [
  "LLLLLL", // 0
  "LLGLGG", // 1
  "LLGGLG", // 2
  "LLGGGL", // 3
  "LGLLGG", // 4
  "LGGLLG", // 5
  "LGGGLL", // 6
  "LGLGLG", // 7
  "LGLGGL", // 8
  "LGGLGL", // 9
];

const MAX_DECODE_DIM = 1400;

export function validateEanCheckDigit(code) {
  const digits = String(code || "").trim();
  if (!/^\d{8}$|^\d{12}$|^\d{13}$/.test(digits)) {
    return false;
  }
  const nums = digits.split("").map(Number);
  const check = nums[nums.length - 1];
  const body = nums.slice(0, -1);
  // Compute standard GS1 alternating weights from right-most payload digit (weight 3)
  let sum = 0;
  for (let i = body.length - 1, pos = 0; i >= 0; i -= 1, pos += 1) {
    sum += body[i] * (pos % 2 === 0 ? 3 : 1);
  }
  const expected = (10 - (sum % 10)) % 10;
  return check === expected;
}

function matchDigit(fourRuns, table) {
  const total = fourRuns[0] + fourRuns[1] + fourRuns[2] + fourRuns[3];
  if (total < 4) return null;
  let bestDigit = -1;
  let bestScore = Infinity;
  let secondBest = Infinity;

  for (let d = 0; d < table.length; d += 1) {
    const ref = table[d];
    let err = 0;
    for (let k = 0; k < 4; k += 1) {
      const scaled = (fourRuns[k] * 7) / total;
      const diff = Math.abs(scaled - ref[k]);
      if (diff > 0.72) {
        err = Infinity;
        break;
      }
      err += diff;
    }
    if (err < bestScore) {
      secondBest = bestScore;
      bestScore = err;
      bestDigit = d;
    } else if (err < secondBest) {
      secondBest = err;
    }
  }
  if (bestDigit < 0 || !Number.isFinite(bestScore) || bestScore > 1.45) {
    return null;
  }
  return { digit: bestDigit, score: bestScore };
}

function matchLeftDigit(fourRuns) {
  const lHit = matchDigit(fourRuns, L_RUNS);
  const gHit = matchDigit(fourRuns, G_RUNS);
  if (!lHit && !gHit) return null;
  if (lHit && (!gHit || lHit.score <= gHit.score)) {
    return { digit: lHit.digit, parity: "L", score: lHit.score };
  }
  return { digit: gHit.digit, parity: "G", score: gHit.score };
}

function binarizeAdaptive(lum, length) {
  const win = Math.max(15, Math.min(81, Math.round(length / 22) | 1));
  const half = win >> 1;
  const prefix = new Float64Array(length + 1);
  for (let i = 0; i < length; i += 1) {
    prefix[i + 1] = prefix[i] + lum[i];
  }
  const binary = new Uint8Array(length);
  let minVal = 255;
  let maxVal = 0;
  for (let i = 0; i < length; i += 1) {
    if (lum[i] < minVal) minVal = lum[i];
    if (lum[i] > maxVal) maxVal = lum[i];
  }
  if (maxVal - minVal < 22) {
    return null; // Low-contrast blank row
  }
  for (let i = 0; i < length; i += 1) {
    const lo = Math.max(0, i - half);
    const hi = Math.min(length - 1, i + half);
    const mean = (prefix[hi + 1] - prefix[lo]) / (hi - lo + 1);
    // Darker than local adaptive threshold => 1 (bar), lighter => 0 (space)
    binary[i] = lum[i] < mean * 0.96 ? 1 : 0;
  }
  return binary;
}

function extractRuns(binary) {
  const runs = [];
  const colors = [];
  let cur = binary[0];
  let len = 1;
  for (let i = 1; i < binary.length; i += 1) {
    if (binary[i] === cur) {
      len += 1;
    } else {
      runs.push(len);
      colors.push(cur);
      cur = binary[i];
      len = 1;
    }
  }
  runs.push(len);
  colors.push(cur);
  return { runs, colors };
}

function tryDecodeEanAt(runs, colors, startIdx, halfDigits) {
  // Expected: [bar, space, bar] start guard (3 runs) + 4*halfDigits left + 5 center + 4*halfDigits right + 3 end
  const totalRunsNeeded = 3 + halfDigits * 4 + 5 + halfDigits * 4 + 3;
  if (startIdx + totalRunsNeeded > runs.length) return null;
  if (colors[startIdx] !== 1) return null;

  // Quiet zone before start guard (if leading space exists, check it is wide enough)
  const modGuess = (runs[startIdx] + runs[startIdx + 1] + runs[startIdx + 2]) / 3;
  if (modGuess < 0.9) return null;
  if (startIdx > 0 && runs[startIdx - 1] < modGuess * 2.5) return null;

  // Verify 101 start guard ratios
  for (let k = 0; k < 3; k += 1) {
    if (Math.abs(runs[startIdx + k] / modGuess - 1) > 0.7) return null;
  }

  let cursor = startIdx + 3;
  const leftDigits = [];
  const leftParity = [];
  let totalScore = 0;

  for (let i = 0; i < halfDigits; i += 1) {
    const slice = [runs[cursor], runs[cursor + 1], runs[cursor + 2], runs[cursor + 3]];
    cursor += 4;
    if (halfDigits === 6) {
      const m = matchLeftDigit(slice);
      if (!m) return null;
      leftDigits.push(m.digit);
      leftParity.push(m.parity);
      totalScore += m.score;
    } else {
      const m = matchDigit(slice, L_RUNS);
      if (!m) return null;
      leftDigits.push(m.digit);
      totalScore += m.score;
    }
  }

  // Center guard 01010 (5 runs)
  const centerMod =
    (runs[cursor] + runs[cursor + 1] + runs[cursor + 2] + runs[cursor + 3] + runs[cursor + 4]) / 5;
  if (centerMod < 0.8) return null;
  for (let k = 0; k < 5; k += 1) {
    if (Math.abs(runs[cursor + k] / centerMod - 1) > 0.75) return null;
  }
  cursor += 5;

  const rightDigits = [];
  for (let i = 0; i < halfDigits; i += 1) {
    const slice = [runs[cursor], runs[cursor + 1], runs[cursor + 2], runs[cursor + 3]];
    cursor += 4;
    const m = matchDigit(slice, R_RUNS);
    if (!m) return null;
    rightDigits.push(m.digit);
    totalScore += m.score;
  }

  // End guard 101 (3 runs)
  const endMod = (runs[cursor] + runs[cursor + 1] + runs[cursor + 2]) / 3;
  if (endMod < 0.8) return null;
  for (let k = 0; k < 3; k += 1) {
    if (Math.abs(runs[cursor + k] / endMod - 1) > 0.75) return null;
  }
  if (cursor + 3 < runs.length && runs[cursor + 3] < endMod * 2.5) return null;

  if (halfDigits === 6) {
    const pattern = leftParity.join("");
    const firstDigit = FIRST_DIGIT_PARITY.indexOf(pattern);
    if (firstDigit < 0) return null;
    const code = `${firstDigit}${leftDigits.join("")}${rightDigits.join("")}`;
    if (!validateEanCheckDigit(code)) return null;
    return { code, avgScore: totalScore / 12 };
  }

  const code = `${leftDigits.join("")}${rightDigits.join("")}`;
  if (!validateEanCheckDigit(code)) return null;
  return { code, avgScore: totalScore / 8 };
}

function scanRunsForEan(runs, colors) {
  const hits = [];
  for (let i = 0; i < runs.length - 42; i += 1) {
    if (colors[i] !== 1) continue;
    const hit13 = tryDecodeEanAt(runs, colors, i, 6);
    if (hit13) {
      hits.push(hit13);
      continue;
    }
    const hit8 = tryDecodeEanAt(runs, colors, i, 4);
    if (hit8) {
      hits.push(hit8);
    }
  }
  return hits;
}

export function decodeScanline(lum, length) {
  const binary = binarizeAdaptive(lum, length);
  if (!binary) return [];
  const fwd = extractRuns(binary);
  const hits = scanRunsForEan(fwd.runs, fwd.colors);
  if (hits.length > 0) return hits;

  const revBinary = new Uint8Array(length);
  for (let i = 0; i < length; i += 1) {
    revBinary[i] = binary[length - 1 - i];
  }
  const rev = extractRuns(revBinary);
  return scanRunsForEan(rev.runs, rev.colors);
}

function decodeCanvasEan(canvas) {
  const ctx = canvas.getContext("2d", { willReadFrequently: true });
  if (!ctx) return null;
  const { width, height } = canvas;
  const imgData = ctx.getImageData(0, 0, width, height).data;

  const votes = new Map();
  const recordHit = (hit) => {
    const prev = votes.get(hit.code) || { count: 0, bestScore: Infinity };
    votes.set(hit.code, {
      count: prev.count + 1,
      bestScore: Math.min(prev.bestScore, hit.avgScore),
    });
  };

  // Sample 19 horizontal & 19 vertical scanlines across central 70% band
  const rowBuf = new Float32Array(width);
  for (let s = 0; s < 19; s += 1) {
    const y = Math.floor(height * (0.15 + (0.70 * s) / 18));
    const rowOff = y * width * 4;
    for (let x = 0; x < width; x += 1) {
      const idx = rowOff + x * 4;
      rowBuf[x] = 0.299 * imgData[idx] + 0.587 * imgData[idx + 1] + 0.114 * imgData[idx + 2];
    }
    for (const hit of decodeScanline(rowBuf, width)) {
      recordHit(hit);
    }
  }

  const colBuf = new Float32Array(height);
  for (let s = 0; s < 19; s += 1) {
    const x = Math.floor(width * (0.15 + (0.70 * s) / 18));
    for (let y = 0; y < height; y += 1) {
      const idx = (y * width + x) * 4;
      colBuf[y] = 0.299 * imgData[idx] + 0.587 * imgData[idx + 1] + 0.114 * imgData[idx + 2];
    }
    for (const hit of decodeScanline(colBuf, height)) {
      recordHit(hit);
    }
  }

  let winner = null;
  let winnerWeight = -1;
  for (const [code, stats] of votes.entries()) {
    if (stats.count >= 2 || (stats.count === 1 && stats.bestScore < 0.35)) {
      const weight = stats.count * 10 - stats.bestScore;
      if (weight > winnerWeight) {
        winnerWeight = weight;
        winner = code;
      }
    }
  }
  return winner;
}

export async function decodeBarcodeFromImageSource(imgSource) {
  const rawW = imgSource.videoWidth || imgSource.naturalWidth || imgSource.width || 0;
  const rawH = imgSource.videoHeight || imgSource.naturalHeight || imgSource.height || 0;
  if (!rawW || !rawH) return null;

  const scale = Math.min(1, MAX_DECODE_DIM / Math.max(rawW, rawH));
  const width = Math.max(1, Math.round(rawW * scale));
  const height = Math.max(1, Math.round(rawH * scale));

  const canvas = document.createElement("canvas");
  canvas.width = width;
  canvas.height = height;
  const ctx = canvas.getContext("2d", { willReadFrequently: true });
  if (!ctx) return null;
  ctx.drawImage(imgSource, 0, 0, width, height);

  if ("BarcodeDetector" in window) {
    try {
      const detector = new window.BarcodeDetector({
        formats: ["ean_13", "ean_8", "upc_a", "upc_e", "code_128"],
      });
      const codes = await detector.detect(canvas);
      if (codes && codes.length > 0 && codes[0].rawValue) {
        return String(codes[0].rawValue).trim();
      }
    } catch {
      // Fall through to pure-JS 1D decoder
    }
  }

  return decodeCanvasEan(canvas);
}
