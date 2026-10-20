/**
 * Orbix Signal Engine - JS Port for Mobile APK
 * All 5 strategies running offline, no Python needed
 * Based on orbix/strategies.py and orbix/engine.py
 */

function ema(values, period) {
  const k = 2 / (period + 1);
  let ema = values[0];
  const result = [ema];
  for (let i = 1; i < values.length; i++) {
    ema = values[i] * k + ema * (1 - k);
    result.push(ema);
  }
  return result;
}

function rsi(closes, period = 14) {
  const gains = [];
  const losses = [];
  for (let i = 1; i < closes.length; i++) {
    const diff = closes[i] - closes[i-1];
    gains.push(diff > 0 ? diff : 0);
    losses.push(diff < 0 ? -diff : 0);
  }
  let avgGain = gains.slice(0, period).reduce((a,b) => a+b, 0) / period;
  let avgLoss = losses.slice(0, period).reduce((a,b) => a+b, 0) / period;
  const rsis = Array(period).fill(50);
  for (let i = period; i < gains.length; i++) {
    avgGain = (avgGain * (period-1) + gains[i]) / period;
    avgLoss = (avgLoss * (period-1) + losses[i]) / period;
    const rs = avgLoss === 0 ? 100 : avgGain / avgLoss;
    rsis.push(100 - (100 / (1 + rs)));
  }
  return rsis;
}

function adx(highs, lows, closes, period = 14) {
  // Simplified ADX - directional movement
  const tr = [];
  const plusDM = [];
  const minusDM = [];
  for (let i = 1; i < closes.length; i++) {
    const hl = highs[i] - lows[i];
    const hc = Math.abs(highs[i] - closes[i-1]);
    const lc = Math.abs(lows[i] - closes[i-1]);
    tr.push(Math.max(hl, hc, lc));
    const upMove = highs[i] - highs[i-1];
    const downMove = lows[i-1] - lows[i];
    plusDM.push(upMove > downMove && upMove > 0 ? upMove : 0);
    minusDM.push(downMove > upMove && downMove > 0 ? downMove : 0);
  }
  // Return simplified trend strength
  const avgTR = tr.slice(-period).reduce((a,b) => a+b, 0) / period;
  const avgPlus = plusDM.slice(-period).reduce((a,b) => a+b, 0) / period;
  const avgMinus = minusDM.slice(-period).reduce((a,b) => a+b, 0) / period;
  const plusDI = avgTR ? (avgPlus / avgTR) * 100 : 0;
  const minusDI = avgTR ? (avgMinus / avgTR) * 100 : 0;
  const dx = (plusDI + minusDI) ? Math.abs(plusDI - minusDI) / (plusDI + minusDI) * 100 : 0;
  return {adx: dx, plusDI, minusDI};
}

export const strategies = {
  orbix_confluence: {
    name: 'Orbix Confluence Gate (OCG)',
    description: '5/6 checks: EMA regime, momentum, RSI band, ADX, candle quality, breakout',
    evaluate(candles) {
      if (candles.length < 60) return {side: 'NO_SIGNAL', reason: 'Need 60 candles', checks: 0};
      const closes = candles.map(c => c.close);
      const highs = candles.map(c => c.high);
      const lows = candles.map(c => c.low);
      const last = candles[candles.length-1];
      
      const ema20 = ema(closes, 20);
      const ema50 = ema(closes, 50);
      const rsi14 = rsi(closes, 14);
      const {adx: adxVal, plusDI, minusDI} = adx(highs, lows, closes);
      
      const lastClose = closes[closes.length-1];
      const lastEma20 = ema20[ema20.length-1];
      const lastEma50 = ema50[ema50.length-1];
      const lastRsi = rsi14[rsi14.length-1];
      
      let checks = 0;
      const reasons = [];
      
      // 1. EMA regime
      const uptrend = lastClose > lastEma20 && lastEma20 > lastEma50;
      const downtrend = lastClose < lastEma20 && lastEma20 < lastEma50;
      if (uptrend || downtrend) { checks++; reasons.push(`EMA ${uptrend?'up':'down'}trend`); }
      
      // 2. Momentum (multi-bar)
      const mom = closes.slice(-5);
      const upMom = mom[4] > mom[0] && mom[4] > mom[2];
      const downMom = mom[4] < mom[0] && mom[4] < mom[2];
      if (upMom || downMom) { checks++; reasons.push(`Momentum ${upMom?'up':'down'}`); }
      
      // 3. RSI band
      if (lastRsi >= 35 && lastRsi <= 75) { checks++; reasons.push(`RSI ${lastRsi.toFixed(1)} in band`); }
      
      // 4. ADX directional
      if (adxVal > 18 && Math.abs(plusDI - minusDI) > 5) { checks++; reasons.push(`ADX ${adxVal.toFixed(1)} directional`); }
      
      // 5. Candle quality
      const body = Math.abs(last.close - last.open);
      const range = last.high - last.low;
      if (range > 0 && body/range > 0.25) { checks++; reasons.push(`Candle quality ${(body/range).toFixed(2)}`); }
      
      // 6. 20-bar breakout
      const last20High = Math.max(...highs.slice(-20, -1));
      const last20Low = Math.min(...lows.slice(-20, -1));
      const breakoutUp = lastClose > last20High;
      const breakoutDown = lastClose < last20Low;
      if (breakoutUp || breakoutDown) { checks++; reasons.push(`20-bar breakout ${breakoutUp?'up':'down'}`); }
      
      const side = checks >= 5 ? (uptrend ? 'BUY' : downtrend ? 'SELL' : 'NO_SIGNAL') : 'NO_SIGNAL';
      // Enforce trend/ADX alignment
      if (side !== 'NO_SIGNAL') {
        if (side === 'BUY' && plusDI <= minusDI) return {side: 'NO_SIGNAL', reason: 'BUY needs +DI > -DI', checks};
        if (side === 'SELL' && minusDI <= plusDI) return {side: 'NO_SIGNAL', reason: 'SELL needs -DI > +DI', checks};
      }
      
      return {
        side,
        reason: checks >= 5 ? `${checks}/6: ${reasons.join(', ')}` : `Only ${checks}/6 checks passed`,
        checks,
        probability: side === 'NO_SIGNAL' ? 0.5 : 0.5 + (checks-5)*0.07 + (adxVal/100)*0.1,
        meta: {ema20: lastEma20, ema50: lastEma50, rsi: lastRsi, adx: adxVal}
      };
    }
  },

  ml_consensus: {
    name: 'ML + Trend Agreement',
    description: 'EMA trend + RSI momentum consensus',
    evaluate(candles) {
      if (candles.length < 30) return {side: 'NO_SIGNAL', reason: 'Need 30 candles'};
      const closes = candles.map(c => c.close);
      const ema20 = ema(closes, 20);
      const rsi14 = rsi(closes, 14);
      const lastClose = closes[closes.length-1];
      const lastEma = ema20[ema20.length-1];
      const lastRsi = rsi14[rsi14.length-1];
      
      if (lastClose > lastEma && lastRsi > 52 && lastRsi < 78) {
        return {side: 'BUY', reason: `Close above EMA20 & RSI ${lastRsi.toFixed(1)}`, probability: 0.55 + (lastRsi-50)/200};
      }
      if (lastClose < lastEma && lastRsi < 48 && lastRsi > 22) {
        return {side: 'SELL', reason: `Close below EMA20 & RSI ${lastRsi.toFixed(1)}`, probability: 0.55 + (50-lastRsi)/200};
      }
      return {side: 'NO_SIGNAL', reason: `No consensus: EMA ${lastClose>lastEma?'above':'below'}, RSI ${lastRsi.toFixed(1)}`};
    }
  },

  trend_pullback: {
    name: 'Trend Pullback',
    description: 'Uptrend pullback to EMA20',
    evaluate(candles) {
      if (candles.length < 40) return {side: 'NO_SIGNAL', reason: 'Need 40 candles'};
      const closes = candles.map(c => c.close);
      const lows = candles.map(c => c.low);
      const highs = candles.map(c => c.high);
      const ema20 = ema(closes, 20);
      const ema50 = ema(closes, 50);
      const lastClose = closes[closes.length-1];
      const lastEma20 = ema20[ema20.length-1];
      const lastEma50 = ema50[ema50.length-1];
      
      const uptrend = lastEma20 > lastEma50 && closes[closes.length-10] < ema20[ema20.length-10];
      const downtrend = lastEma20 < lastEma50 && closes[closes.length-10] > ema20[ema20.length-10];
      
      if (uptrend && Math.abs(lastClose - lastEma20)/lastClose < 0.002) {
        return {side: 'BUY', reason: 'Uptrend pullback to EMA20', probability: 0.56};
      }
      if (downtrend && Math.abs(lastClose - lastEma20)/lastClose < 0.002) {
        return {side: 'SELL', reason: 'Downtrend pullback to EMA20', probability: 0.56};
      }
      return {side: 'NO_SIGNAL', reason: 'No pullback setup'};
    }
  },

  range_reversal: {
    name: 'Range Reversal',
    description: 'RSI extremes + wick rejection',
    evaluate(candles) {
      if (candles.length < 20) return {side: 'NO_SIGNAL', reason: 'Need 20 candles'};
      const closes = candles.map(c => c.close);
      const rsi14 = rsi(closes, 14);
      const last = candles[candles.length-1];
      const lastRsi = rsi14[rsi14.length-1];
      const upperWick = last.high - Math.max(last.open, last.close);
      const lowerWick = Math.min(last.open, last.close) - last.low;
      const body = Math.abs(last.close - last.open);
      
      if (lastRsi < 32 && lowerWick > body*1.5) {
        return {side: 'BUY', reason: `Oversold RSI ${lastRsi.toFixed(1)} + long lower wick`, probability: 0.57};
      }
      if (lastRsi > 68 && upperWick > body*1.5) {
        return {side: 'SELL', reason: `Overbought RSI ${lastRsi.toFixed(1)} + long upper wick`, probability: 0.57};
      }
      return {side: 'NO_SIGNAL', reason: `RSI ${lastRsi.toFixed(1)} not extreme`};
    }
  },

  breakout: {
    name: '20-Candle Breakout',
    description: 'Pure price breakout',
    evaluate(candles) {
      if (candles.length < 25) return {side: 'NO_SIGNAL', reason: 'Need 25 candles'};
      const closes = candles.map(c => c.close);
      const highs = candles.map(c => c.high);
      const lows = candles.map(c => c.low);
      const lastClose = closes[closes.length-1];
      const high20 = Math.max(...highs.slice(-21, -1));
      const low20 = Math.min(...lows.slice(-21, -1));
      
      if (lastClose > high20) {
        return {side: 'BUY', reason: `Breakout above 20-bar high ${high20.toFixed(5)}`, probability: 0.54};
      }
      if (lastClose < low20) {
        return {side: 'SELL', reason: `Breakdown below 20-bar low ${low20.toFixed(5)}`, probability: 0.54};
      }
      return {side: 'NO_SIGNAL', reason: `Inside 20-bar range ${low20.toFixed(5)}-${high20.toFixed(5)}`};
    }
  },

  orbix_cadence: {
    name: 'Orbix Cadence (Collection)',
    description: 'Loose model read every 3 mins - for data collection',
    evaluate(candles) {
      if (candles.length < 20) return {side: 'NO_SIGNAL', reason: 'Need 20 candles'};
      const closes = candles.map(c => c.close);
      const ema10 = ema(closes, 10);
      const lastClose = closes[closes.length-1];
      const lastEma10 = ema10[ema10.length-1];
      const momentum = closes[closes.length-1] - closes[closes.length-5];
      
      // Very loose - always gives a side, but low edge
      const side = lastClose > lastEma10 ? 'BUY' : 'SELL';
      const prob = 0.5 + Math.min(0.08, Math.abs(momentum)/(lastClose*0.01));
      return {
        side,
        reason: `Cadence: ${side} EMA10 ${lastClose>lastEma10?'above':'below'} mom ${momentum.toFixed(5)}`,
        probability: side === 'BUY' ? prob : 1-prob,
        isCadence: true
      };
    }
  }
};

export function evaluateAll(candles) {
  const results = {};
  for (const [key, strat] of Object.entries(strategies)) {
    try {
      results[key] = {strategy: key, ...strat.evaluate(candles)};
    } catch (e) {
      results[key] = {strategy: key, side: 'NO_SIGNAL', reason: `Error: ${e.message}`};
    }
  }
  return results;
}

export function getBestSignal(candles, order = ['orbix_confluence','ml_consensus','trend_pullback','range_reversal','breakout','orbix_cadence']) {
  const all = evaluateAll(candles);
  for (const key of order) {
    if (all[key] && all[key].side !== 'NO_SIGNAL') {
      return all[key];
    }
  }
  return {side: 'NO_SIGNAL', reason: 'All strategies abstained', strategy: 'none', probability: 0.5};
}

export function generateSignalId() {
  return `sig_${Date.now()}_${Math.random().toString(36).slice(2,8)}`;
}
