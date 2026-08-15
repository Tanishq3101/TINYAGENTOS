// TinyAgentOS marketing site — vanilla JS, no framework, no build step required.

(function () {
  'use strict';

  var prefersReducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;

  /* ---------------------------------------------------------------------
   * Oscilloscope wave: generated from a data array, not a hardcoded path.
   * Swap `sampleWaveform` for a real array (or a fetch()'d one, see the
   * README section on wiring up live metrics) to change the trace shape
   * without touching markup.
   * ------------------------------------------------------------------- */
  function buildWavePath(points, width, height) {
    var stepX = width / (points.length - 1);
    var d = '';
    points.forEach(function (p, i) {
      var x = i * stepX;
      var y = p * height;
      d += (i === 0 ? 'M' : 'L') + x.toFixed(1) + ',' + y.toFixed(1) + ' ';
    });
    return d.trim();
  }

  function renderScope() {
    var svg = document.getElementById('wave-svg');
    var path = document.getElementById('wave-path');
    if (!svg || !path) return;

    var viewBox = svg.viewBox.baseVal;
    var width = viewBox.width || 400;
    var height = viewBox.height || 110;

    // Normalized (0-1) sample points; illustrative only.
    var sampleWaveform = [
      0.55, 0.55, 0.18, 0.82, 0.5, 0.5, 0.14, 0.86, 0.5, 0.5,
      0.27, 0.73, 0.5, 0.5, 0.09, 0.91, 0.5, 0.5, 0.23, 0.77,
      0.5, 0.5, 0.16, 0.84, 0.5, 0.5, 0.32, 0.68, 0.5, 0.5,
      0.18, 0.5
    ];

    path.setAttribute('d', buildWavePath(sampleWaveform, width, height));
  }

  /* ---------------------------------------------------------------------
   * Pipeline pulse-dot animation: only runs once the section is in view,
   * and never at all if the user has requested reduced motion.
   * ------------------------------------------------------------------- */
  function initPipelineAnimation() {
    var pipeline = document.getElementById('pipeline');
    if (!pipeline || prefersReducedMotion) return;

    if (!('IntersectionObserver' in window)) {
      pipeline.classList.add('in-view');
      return;
    }

    var observer = new IntersectionObserver(
      function (entries) {
        entries.forEach(function (entry) {
          if (entry.isIntersecting) {
            pipeline.classList.add('in-view');
          } else {
            pipeline.classList.remove('in-view');
          }
        });
      },
      { threshold: 0.4 }
    );
    observer.observe(pipeline);
  }

  /* ---------------------------------------------------------------------
   * Scroll-triggered fade-ins for spec cards and the benchmark table.
   * Skipped entirely under reduced motion (CSS also forces opacity:1
   * as a belt-and-braces fallback).
   * ------------------------------------------------------------------- */
  function initFadeIns() {
    var targets = document.querySelectorAll('.spec-card, .bench');
    if (!targets.length) return;

    if (prefersReducedMotion || !('IntersectionObserver' in window)) {
      targets.forEach(function (el) { el.classList.add('visible'); });
      return;
    }

    var observer = new IntersectionObserver(
      function (entries, obs) {
        entries.forEach(function (entry) {
          if (entry.isIntersecting) {
            entry.target.classList.add('visible');
            obs.unobserve(entry.target);
          }
        });
      },
      { threshold: 0.2 }
    );
    targets.forEach(function (el) { observer.observe(el); });
  }

  /* ---------------------------------------------------------------------
   * Copy-to-clipboard on the docker compose command.
   * ------------------------------------------------------------------- */
  function initCopyButton() {
    var btn = document.getElementById('copy-btn');
    var cmdEl = document.getElementById('cmd-text');
    if (!btn || !cmdEl) return;

    btn.addEventListener('click', function () {
      // Strip the leading "$ " prompt before copying.
      var text = cmdEl.textContent.replace(/^\s*\$\s*/, '').trim();

      function markCopied() {
        btn.classList.add('copied');
        setTimeout(function () { btn.classList.remove('copied'); }, 1600);
      }

      if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(text).then(markCopied).catch(function () {
          fallbackCopy(text, markCopied);
        });
      } else {
        fallbackCopy(text, markCopied);
      }
    });
  }

  function fallbackCopy(text, done) {
    var ta = document.createElement('textarea');
    ta.value = text;
    ta.style.position = 'fixed';
    ta.style.opacity = '0';
    document.body.appendChild(ta);
    ta.select();
    try { document.execCommand('copy'); } catch (e) { /* clipboard unavailable, no-op */ }
    document.body.removeChild(ta);
    done();
  }

  document.addEventListener('DOMContentLoaded', function () {
    renderScope();
    initPipelineAnimation();
    initFadeIns();
    initCopyButton();
  });
})();