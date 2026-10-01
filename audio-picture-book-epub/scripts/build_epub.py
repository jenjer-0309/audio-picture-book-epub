#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Build a per-page narrated fixed-layout EPUB 3 picture book from a folder.

Expected folder layout (names are examples; any language works):

    cover.png   / cover.mp3      <- cover image + cover narration.
    01.png      / 01.mp3            Their stems must NOT be pure digits.
    02.png      / 02.mp3
    ...
    NN.png      / NN.mp3         <- one image + one audio per page, same stem.

The narration is one track: the in-page <audio> driven by js/reader.js. The
controller is the ONLY start path - pages carry no autoplay attribute, because
the contexts that run no scripts (Apple Books pre-rendering the whole book and
its thumbnails) must not start anything on their own. Readers that never run
JavaScript are unsupported by design; see references/playback.md for the
decision record.

The controller gives the engine the first claim on the audio session: automatic
starts are SILENT probes (muted), a start the engine kills means it owns the
session and the script yields without retrying (a retry is heard as "play, cut,
play again"), and only a probe that survives is unmuted - that survivor is the
audible track.

Every page image is normalised onto ONE shared canvas, each page carries exactly
ONE <audio> element, and the navigation document is kept out of the spine. Those
three choices are what make the result survive Apple Books; see
references/apple-books-pitfalls.md before changing them.

Usage:
    python build_epub.py --src . --title "My Book" --author "Someone"

Build artifacts land under build/ in the working directory: the finished book
defaults to build/<title>.epub (override with --out), and the package staging
tree is created there too.
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import sys
import tempfile
import uuid
import zipfile
from collections import Counter
from datetime import datetime, timezone

import hard_rules

try:
    from PIL import Image
except ImportError:  # pragma: no cover
    sys.exit("Pillow is required:  python -m pip install Pillow")


# The hard rules and their shared constants live in hard_rules.py. The very
# same predicates check_epub.py runs against the finished book are run below on
# the pages this script just rendered, so an emit-side regression (a stripped
# audio element, a drifted id) fails the build instead of shipping.
DIGITS_RE = re.compile(r"^\d+$")

# Page background. The image canvas is filled with BG_RGB and .stage is painted
# BG_HEX, so any letterbox around an odd-aspect image is invisible.
BG_RGB = (238, 246, 238)
BG_HEX = "#eef6ee"

# ---------------------------------------------------------------------------
# CSS. One <audio> per page, hidden off-canvas; when JS has NOT run the
# `controls` attribute is still present and the native bar is revealed instead.
# ---------------------------------------------------------------------------
CSS = """@charset "utf-8";

html, body {
  margin: 0;
  padding: 0;
  width: 100%;
  height: 100%;
  background-color: __BG__;
  overflow: hidden;
  -webkit-text-size-adjust: none;
}

/* absolute pinning is more robust than percentage heights in reading systems */
.stage {
  position: absolute;
  top: 0;
  right: 0;
  bottom: 0;
  left: 0;
  width: 100%;
  height: 100%;
  overflow: hidden;
}

.pg-img {
  position: absolute;
  top: 0;
  left: 0;
  width: 100%;
  height: 100%;
  max-width: 100%;
  max-height: 100%;
  object-fit: contain;
  object-position: center center;
  display: block;
  -webkit-user-select: none;
  user-select: none;
}

/* ------------------------------------------------------------------
   THE ONLY <audio> ON THIS PAGE.
   Never add a second one: pre-render contexts (Apple Books parsing the book
   and its thumbnails) do NOT run scripts, so a <noscript> fallback audio
   really does load, and two elements decoding the same file play it twice,
   about 0.5 s apart. Never add autoplay either: those same contexts start it
   without any script and without any visibility gate, one page after another.
   ------------------------------------------------------------------ */
.pg-audio {
  position: absolute;
  left: -9999px;
  top: 0;
  width: 1px;
  height: 1px;
  opacity: 0;
  overflow: hidden;
}

/* JS never ran (controls still present) -> show the native bar as fallback */
.pg-audio[controls] {
  left: 6%;
  right: 6%;
  top: auto;
  bottom: 16px;
  width: 88%;
  height: 40px;
  opacity: 0.85;
  overflow: visible;
  border-radius: 10px;
  background: rgba(0, 0, 0, 0.32);
  z-index: 30;
}

/* custom play/pause button, only enabled once JS is running */
.pg-btn {
  display: none;
  position: absolute;
  left: 50%;
  bottom: 18px;
  margin-left: -23px;
  width: 46px;
  height: 46px;
  padding: 0;
  border: 0;
  border-radius: 50%;
  background: rgba(40, 30, 20, 0.42);
  -webkit-appearance: none;
  appearance: none;
  cursor: pointer;
  z-index: 20;
  opacity: 0.62;
  -webkit-tap-highlight-color: transparent;
}

body.js .pg-btn { display: block; }
.pg-btn:active { opacity: 1; }
.pg-btn.is-playing { opacity: 0.28; }

.pg-btn .ico {
  position: absolute;
  top: 50%;
  left: 50%;
  -webkit-transform: translate(-50%, -50%);
  transform: translate(-50%, -50%);
}

.ico-play {
  width: 0;
  height: 0;
  border-left: 15px solid #ffffff;
  border-top: 9px solid transparent;
  border-bottom: 9px solid transparent;
  margin-left: 4px;
}

.ico-pause {
  width: 8px;
  height: 18px;
  border-left: 5px solid #ffffff;
  border-right: 5px solid #ffffff;
}

.pg-btn.is-playing .ico-play { display: none; }
.pg-btn .ico-pause { display: none; }
.pg-btn.is-playing .ico-pause { display: block; }
"""

# ---------------------------------------------------------------------------
# Reader-side controller. Must be the LAST thing in <body> so the DOM exists.
#
# Playing narration inside a real reading system needs a small state machine,
# not a one-shot play(): readers prefetch the next page in a hidden frame,
# suspend a page right after a page turn, and several refuse playback until the
# document has seen a user gesture. See references/playback.md.
#
# Engine first: an automatic start is a SILENT probe (muted). If the reading
# system kills it (it takes the audio session for its own narration - observed
# on Apple Books), nothing was ever heard and the controller yields instead of
# retrying. A probe that survives is seeked back to 0 and unmuted (no new
# play() call): that is the audible track taking over where the engine did
# not.
# ---------------------------------------------------------------------------
JS = """(function () {
  'use strict';

  /* One controller per document: a reading system may keep several spine
     documents in one DOM, and this script must never end up stacked twice. */
  if (document.__apbBound) { return; }
  document.__apbBound = true;

  /* NOTHING here is cached at load time. Some readers keep a pool of page
     documents and swap their content WITHOUT re-running this script, so a
     reference taken at load ends up pointing at a page that is no longer on
     screen - reported on device as narration alternating between the first two
     pages no matter which page was shown. Every trigger therefore re-resolves
     the page that is on screen right now, and the tap handler is delegated on
     `document` so that it survives a body swap. */
  function stageOf(node) {
    while (node && node.nodeType === 1) {
      if (node.classList && node.classList.contains('stage')) { return node; }
      node = node.parentNode;
    }
    return null;
  }
  function liveStage() {
    return stageOf(document.querySelector('button.pg-btn')) ||
           document.querySelector('.stage');
  }
  function liveAudio(from) {
    var root = from || liveStage();
    if (root) {
      var hit = root.querySelector('audio.pg-audio');
      if (hit) { return hit; }
    }
    return document.querySelector('audio.pg-audio');
  }

  /* The element of the page that is on screen right now, plus its container. */
  var audio = liveAudio();
  var stage = stageOf(audio);
  if (!audio) { return; }

  /* Optional on-page readout, present only in a --debug-hud build. Read through
     querySelector so the getElementById contract the validator checks stays
     untouched. A reading system gives you no console; this is the substitute. */
  var hudLines = [];
  var t0 = Date.now();
  function basename(url) {
    var s = String(url || '').split('?')[0];
    return s.substring(s.lastIndexOf('/') + 1) || '?';
  }
  function log(msg) {
    var hud = document.querySelector('#dbg');   // re-read: the body may be swapped
    if (!hud) { return; }
    hudLines.unshift('+' + ((Date.now() - t0) / 1000).toFixed(2) + 's  ' + msg);
    if (hudLines.length > 7) { hudLines.pop(); }
    hud.textContent = hudLines.join('\\n');
  }

  var started = false;     // this page has made AUDIBLE sound at least once
  var finished = false;    // it played to the very end
  var userPaused = false;  // the last stop was the reader's choice
  var wave = 0;            // retry wave, so several triggers share one budget
  var timer = 0;           // the single retry in flight, if any
  var total = 0;           // whole-page budget: a reader that says no is left alone
  var kills = 0;           // starts the engine has killed since the last user action
  var gestured = false;    // the reader has touched this page (borrowed gesture)
  var probing = false;     // a silent probe start is in flight
  var probeTimer = 0;      // the timer that would unmute a surviving probe
  var probeGen = 0;        // identifies which probe is in flight
  var pausedForHidden = false;  // we paused this page because it went hidden
  var RETRY_MS = [300, 900];   // for REJECTED starts only: those made no sound
  var MAX_KILLS = 1;           // one kill = the engine owns the session; yield
  var PROBE_MS = 550;          // how long a silent probe must survive to unmute
  /* Reading systems suspend media while the page-turn transition runs. A start
     attempted during it is killed about 0.2 s in, which the reader hears as the
     first word being cut off - with the old code that was the only remaining
     defect on Apple Books. Do not even try before the transition is over. */
  var ARMED_MS = 400;
  var armedAt = Date.now() + ARMED_MS;
  var armTimer = 0;

  /* A page the reader has sent away - backgrounded, or already turned away -
     reports hidden: never make sound there, or it stacks on top of the page
     the reader is actually looking at. A preloaded frame is the other case:
     it keeps the top-level state, see frameShown(). */
  function isVisible() {
    try { return document.visibilityState !== 'hidden'; } catch (e) { return true; }
  }

  /* visibilityState is per TOP-LEVEL document. A reading system that preloads
     the next page inside a hidden FRAME therefore reads 'visible' for it too
     (the frame's document is same-origin, scripts included), and only the
     frame's own box tells whether that page is on screen. Without this gate
     the preloaded next page unmutes itself and the reader hears the NEXT
     page's recording - the off-by-one narration reported on device.
       - no client rects (display:none / never laid out) -> not on screen
       - computed visibility:hidden on the frame element -> not on screen
       - frame box outside the parent viewport           -> not on screen
     Outside an iframe, or when the parent cannot be inspected, this is TRUE:
     never regress readers this cannot see. */
  function frameShown() {
    try {
      if (window.parent === window) { return true; }
      var fe = window.frameElement;
      if (!fe) { return true; }
      if (fe.getClientRects().length === 0) { return false; }
      var win = fe.ownerDocument.defaultView;
      if (win.getComputedStyle(fe).visibility === 'hidden') { return false; }
      var r = fe.getBoundingClientRect();
      var pw = win.innerWidth || 0, ph = win.innerHeight || 0;
      if (pw && ph &&
          (r.bottom <= 0 || r.right <= 0 || r.top >= ph || r.left >= pw)) {
        return false;
      }
      return true;
    } catch (e) { return true; }
  }
  function pageShown() { return isVisible() && frameShown(); }

  /* Arrival: only one page may be audible in this document. Reading systems
     that keep several pages in one DOM (or a pool of them) do not always fire
     a visibilitychange for the page that is swapped out, so every start first
     hushes whatever else still plays. Never called from a hidden page: that
     would hush the page the reader is looking at. */
  function hushOthers(keep) {
    var all = document.querySelectorAll('audio.pg-audio');
    for (var i = 0; i < all.length; i++) {
      var el = all[i];
      if (el === keep || el.paused) { continue; }
      try { el.pause(); } catch (e) {}
    }
  }

  /* The silent probe died (killed / paused / the page went away). Nothing was
     ever audible; stand down and restore the element so that a later explicit
     start still has sound. */
  function abortProbe() {
    if (probeTimer) { clearTimeout(probeTimer); probeTimer = 0; }
    probing = false;
    try { audio.muted = false; } catch (e) {}
  }

  /* The probe survived: no engine moved in to claim the audio session, so the
     JS fallback track takes over. Seek back to the start (everything so far
     was silent) and unmute WITHOUT calling play() again - a new start is
     exactly what the engine kills. */
  function finishProbe() {
    if (probeTimer) { clearTimeout(probeTimer); probeTimer = 0; }
    if (!probing) { return; }
    if (!pageShown()) {
      /* Off screen (a preloaded frame, or the page went away): never unmute
         here - that is exactly the next page's recording heard early. Pause
         the muted probe as OUR OWN suspension (pausedForHidden), so the
         follow-up pause event is never counted as an engine kill, then stand
         down for a later start. */
      if (!audio.paused) { pausedForHidden = true; audio.pause(); }
      abortProbe();
      return;
    }
    probing = false;
    if (audio.paused) {               // died between the timer and now
      try { audio.muted = false; } catch (e) {}
      return;
    }
    try { if (audio.currentTime > 0.05) { audio.currentTime = 0; } } catch (e) {}
    try { audio.muted = false; } catch (e) {}
    if (audio.muted) {
      /* the engine would not make this element audible: stay silent, keep
         the tap paths alive */
      log('unmute refused - tap to play');
      try { audio.pause(); } catch (e) {}
      return;
    }
    started = true; dropGestures(); setPlaying(true);
    log('unmuted ' + basename(audio.currentSrc || audio.src));
  }

  function setPlaying(on) {
    var b = stage ? stage.querySelector('button.pg-btn') : null;
    if (b) { b.className = on ? 'pg-btn is-playing' : 'pg-btn'; }
  }

  /* Re-resolve the page that is on screen. When the reader has swapped the
     document under us, start over with that page's own state. */
  function bind(from) {
    var el = liveAudio(from);
    if (!el) { return audio; }
    if (el !== audio) {
      var old = audio;
      /* A pool reader replaces the page under us without re-running this
         script. Stop the element that is leaving, then adopt the new one.
         The pointer is swapped FIRST so that the pause() below - and every
         later event of that element - is ignored by the el !== audio guards. */
      if (probeTimer) { clearTimeout(probeTimer); probeTimer = 0; }
      probing = false;
      audio = el;
      try { old.pause(); } catch (e) {}
      try { old.muted = false; } catch (e2) {}
      started = false; finished = false; userPaused = false;
      wave = 0; timer = 0; total = 0; kills = 0;
      gestured = false; pausedForHidden = false;
      armedAt = Date.now() + ARMED_MS;   // a fresh page: a fresh page turn
      log('page ' + basename(el.getAttribute('src')));
    }
    stage = stageOf(el) || liveStage();
    attach(el);
    return audio;
  }

  /* JS takes this page over: hide the native bar, reveal our own control, and
     listen to this element - once per element, however often we re-bind. */
  function attach(el) {
    try { el.removeAttribute('controls'); } catch (e) {}
    if (document.body && document.body.className.indexOf('js') < 0) {
      document.body.className =
        (document.body.className ? document.body.className + ' ' : '') + 'js';
    }
    if (el.__apb) { return; }
    el.__apb = true;

    /* Every handler ignores events from an element that is no longer the one
       on screen: a pool reader's swapped-out page keeps its listeners, and
       its events must not steer the live page's state. */
    el.addEventListener('play', function () {
      if (el !== audio || probing) { return; }
      setPlaying(true);
    });
    el.addEventListener('playing', function () {
      if (el !== audio) { return; }
      if (probing) {
        /* The silent probe is airborne: give the engine time to kill it. A
           kill now means the engine owns the audio session (its own
           narration) - and this probe stays silent. */
        if (!probeTimer) { probeTimer = setTimeout(finishProbe, PROBE_MS); }
        return;
      }
      started = true; setPlaying(true); dropGestures();
      log('playing ' + basename(el.currentSrc || el.src));
    });
    el.addEventListener('ended', function () {
      if (el !== audio) { return; }
      if (probing) { abortProbe(); }   // a file shorter than the probe window
      finished = true; setPlaying(false); log('ended');
    });
    el.addEventListener('pause', function () {
      if (el !== audio) { return; }
      if (probing && audio.paused) { abortProbe(); }
      setPlaying(false);
      if (pausedForHidden) {
        /* This is the pause we issued ourselves for a hidden page - not the
           engine talking, even if the page reads as visible again by now. */
        pausedForHidden = false;
        log('paused(hidden)');
        return;
      }
      if (userPaused || finished) { return; }
      /* Any other pause while the page is not on screen (top-level hidden, or
         the containing frame hidden / outside the parent viewport) is a
         suspension, not a refusal. */
      if (!pageShown()) { log('paused(hidden)'); return; }
      /* Stopped by something that is not the reader. Past 1.5 s it is probably
         the reading system taking the audio session for its own narration, so
         leave it alone rather than play two streams at once. */
      if (el.currentTime >= 1.5) { log('paused mid'); return; }
      /* Killed near the start while on screen: the engine owns this page's
         audio session (Apple Books was observed to kill every automatic start
         about 0.2 s in, back when the Media Overlay track shipped; re-verify
         on device now that it is gone). Yield - do NOT retry. A retry is
         heard as "play, cut, play again", and where the engine cares to, it
         is already narrating the page itself. */
      kills += 1;
      log('killed at start - yielding (engine has the session)');
    });
    el.addEventListener('canplay', function () {
      if (el !== audio) { return; }
      attempt('canplay');
    });
  }

  /* THE one place that asks for playback. Every trigger funnels through here,
     so "one start path per page" cannot be broken by a later addition.
     `explicit` marks a deliberate tap on our own button: that is exempt from the
     visibility gate, because a reading system that reports 'hidden' spuriously
     would otherwise leave the reader with a dead button - and they can only tap
     what is on screen anyway. */
  function attempt(why, explicit) {
    if (!bind()) { return; }               // re-resolve the page on screen, EVERY
                                           // time: a swapped-in page gets its own
                                           // state (its own paused/finished too)
                                           // before any guard below reads it
    if (userPaused || finished) { return; }
    if (!explicit && kills >= MAX_KILLS && !gestured) { return; }   // engine owns it; stay quiet
    if (!explicit && Date.now() < armedAt) { arm(); return; }   // page turn still running
    if (!explicit && !isVisible()) { log('skip(hidden) ' + why); return; }
    if (!explicit && !frameShown()) { log('skip(frame-hidden) ' + why); return; }
    if (total >= 16) { log('gave up ' + why); return; }
    /* Arrival: whatever else still plays in this document stops now. Never
       from an off-screen page - that would hush the page being read. */
    if (pageShown()) { hushOthers(audio); }
    total += 1;
    /* Automatic starts go in muted, as a probe: if the engine kills the start
       (it takes the audio session for its own narration), nothing was ever
       heard. Only an explicit tap - or a first gesture on a page the engine
       has NOT refused yet - asks for sound directly. Once the engine has
       killed a start here, even a gesture goes back to probing, so a stray
       touch cannot make the page blip again. */
    var probe = !explicit && !started && (!gestured || kills > 0);
    if (probe) {
      try { audio.muted = true; } catch (e) {}
      probing = true;
      probeGen += 1;
      /* measure this probe's window from THIS start */
      if (probeTimer) { clearTimeout(probeTimer); probeTimer = 0; }
      if (!audio.paused) { probeTimer = setTimeout(finishProbe, PROBE_MS); }
    } else {
      /* The audible path: it supersedes any probe still in flight - stop the
         pending unmute, restart from the silent position - and must never
         inherit a stuck mute from a refused probe. */
      if (probeTimer) { clearTimeout(probeTimer); probeTimer = 0; }
      if (probing) {
        probing = false;
        try {
          if (!audio.paused && audio.currentTime > 0.05) { audio.currentTime = 0; }
        } catch (e) {}
      } else {
        try {
          if (!started && audio.currentTime > 0.05 && audio.currentTime < 1.5) {
            audio.currentTime = 0;   // an earlier start was killed near the top
          }
        } catch (e) {}
      }
      probeGen += 1;
      try { audio.muted = false; } catch (e) {}
    }
    var gen = probeGen;
    var p;
    try { p = audio.play(); } catch (e) { p = null; log('threw ' + why); }
    if (p && typeof p.then === 'function') {
      p.then(function () { log((probe ? 'probe ok ' : 'play ok ') + why); },
             function (e) {
               log('blocked ' + ((e && e.name) || '?') + ' ' + why);
               /* A rejected start made no sound. Stand the probe down - but
                  only if it really is the probe in flight: a later attempt
                  may have started a newer one (probeGen). */
               if (probe && probing && probeGen === gen && audio.paused) { abortProbe(); }
               schedule();
             });
    } else if (p !== null) {
      /* no promise (ancient engine): assume the start was taken */
      if (probe) {
        if (!probeTimer) { probeTimer = setTimeout(finishProbe, PROBE_MS); }
      } else {
        setPlaying(!audio.paused);
      }
    } else {
      /* threw synchronously: this start never happened */
      if (probe && probing && probeGen === gen && audio.paused) { abortProbe(); }
      schedule();
    }
  }

  /* Hold an automatic start until the page turn has finished. One timer only:
     the page boots with several triggers at once and they should all wait on the
     same clock. */
  function arm() {
    if (armTimer) { return; }
    log('waiting out the page turn');
    armTimer = setTimeout(function () {
      armTimer = 0;
      attempt('armed');
    }, Math.max(50, armedAt - Date.now() + 20));
  }

  /* A REJECTED start is retried a few times: play() said no, which makes no
     sound, so a retry cannot be heard as stuttering - unlike a killed start.
     Several readers do not allow playback until THIS document has seen a
     gesture, and that gesture normally arrives after load, when the reader
     first touches the page. Only one retry is in flight at any moment - the
     page boots with several triggers at once (load, canplay, the 250/1200 ms
     probes) and if each of them queued its own retry the budget would be
     spent before the reader ever touches the page. */
  function schedule() {
    if (userPaused || finished || wave >= RETRY_MS.length || timer) { return; }
    if (!pageShown()) { return; }   // off-screen pages wait for visibility / frame watcher
    var delay = RETRY_MS[wave];
    wave += 1;
    timer = setTimeout(function () { timer = 0; attempt('retry'); }, delay);
  }

  /* The media listeners live in attach(), one set per element. */

  function buttonOf(node) {
    while (node && node.nodeType === 1) {
      if (node.classList && node.classList.contains('pg-btn')) { return node; }
      node = node.parentNode;
    }
    return null;
  }

  function toggle() {
    if (probing) {
      /* A silent probe is running: the tap means "be audible now". */
      finishProbe();
      if (!audio.paused) { return; }
    }
    if (audio.paused) {
      userPaused = false;
      finished = false;
      wave = 0;
      total = 0;             // an explicit tap always gets a fresh budget
      kills = 0;
      attempt('button', true);
    } else {
      userPaused = true;
      audio.pause();
      try { audio.currentTime = 0; } catch (e) {}
    }
  }
  /* Delegated on `document`: a reader that replaces the page body would leave a
     handler bound to a node that is gone. Resolve the tapped button, then the
     audio element inside ITS container, so the file played always belongs to the
     page the reader touched. */
  function onTap(ev) {
    var b = buttonOf(ev.target);
    if (!b) { return; }
    if (ev.preventDefault) { ev.preventDefault(); }
    if (ev.stopPropagation) { ev.stopPropagation(); }
    bind(stageOf(b));
    toggle();
  }
  document.addEventListener('click', onTap, true);
  document.addEventListener('touchend', onTap, true);

  bind();
  log('load ' + basename(audio.getAttribute('src')) + ' vis=' + isVisible() +
      ' frame=' + frameShown());
  attempt('load');
  window.addEventListener('load', function () { attempt('load-event'); });
  setTimeout(function () { attempt('t250'); }, 250);
  setTimeout(function () { attempt('t1200'); }, 1200);

  /* Frame watcher, iframes only. The top-level visibilitychange never fires
     for a preloaded frame that is later swapped onto the stage - the frame is
     reused, its document is neither reloaded nor re-scripted - so nothing else
     would tell this page that it just became the visible one. Poll the frame's
     box: hidden -> shown restarts narration (attempt('frame-shown'));
     shown -> hidden pauses it as our own suspension (pausedForHidden, NOT a
     kill). At top level there is no frame to watch. */
  if (window.parent !== window) {
    var FRAME_POLL_MS = 300;   // device-tunable
    var frameWasShown = frameShown();
    setInterval(function () {
      var now = frameShown();
      if (now === frameWasShown) { return; }
      frameWasShown = now;
      if (now) {
        log('frame shown');
        /* A promotion is a fresh page display: give the automatic start a
           fresh budget (the engine's refusal record - kills - is kept). */
        wave = 0;
        total = 0;
        attempt('frame-shown');
      } else {
        log('frame hidden');
        try {
          if (!audio.paused) { pausedForHidden = true; audio.pause(); }
        } catch (e) {}
      }
    }, FRAME_POLL_MS);
  }

  /* Mute when backgrounded; resume an interrupted reading, but never undo a
     pause the reader asked for. */
  document.addEventListener('visibilitychange', function () {
    if (!isVisible()) {
      log('hidden');
      try {
        if (!audio.paused) { pausedForHidden = true; audio.pause(); }
      } catch (e) {}
      return;
    }
    log('visible');
    if (userPaused || finished) { return; }
    wave = 0;
    attempt('visible');
  });

  /* Borrow the first gesture - for the WHOLE life of the page, not for a few
     seconds: the reader may not touch the page until long after it loaded, and
     a document that has never seen a gesture can be refused playback forever.
     Never preventDefault - page turns must keep working. */
  function onGesture(ev) {
    if (started) { dropGestures(); return; }
    if (ev && buttonOf(ev.target)) { return; }
    gestured = true;
    wave = 0;
    total = 0;
    /* kills is deliberately NOT reset here: once the engine has refused this
       page, a touch re-tries only as a silent probe - the explicit button is
       what clears the refusal. */
    /* Deliberately not starting inside the handler: the tap that turns the page
       reaches this document too, on a page that is about to be hidden, and
       starting there is heard as a split-second blip at every page turn. Let
       visibility decide instead. */
    setTimeout(function () { attempt('gesture'); }, 300);
  }
  function dropGestures() {
    document.removeEventListener('pointerdown', onGesture, true);
    document.removeEventListener('touchstart', onGesture, true);
    document.removeEventListener('mousedown', onGesture, true);
    document.removeEventListener('keydown', onGesture, true);
    document.removeEventListener('click', onGesture, true);
  }
  document.addEventListener('pointerdown', onGesture, true);
  document.addEventListener('touchstart', onGesture, true);
  document.addEventListener('mousedown', onGesture, true);
  document.addEventListener('keydown', onGesture, true);
  document.addEventListener('click', onGesture, true);
})();
"""

# ---------------------------------------------------------------------------
# Diagnostic only. A reading system gives you no console, so a build made with
# --debug-hud carries an on-page readout of what the controller did. Off by
# default: a shipped book must not show it.
#
# It goes at the END of <body>, never as the first child: some readers treat
# the first block as the chapter's content and choke on it ("chapter data
# failed to load" was reported on device for a book whose only difference was
# the readout, while the identical build without it opened fine). Keep the
# styling dull for the same reason - no shorthand, no exotic values.
# ---------------------------------------------------------------------------
HUD_HTML = ('\n  <pre id="dbg" style="position:fixed;top:0;left:0;right:0;'
            'margin:0;padding:6px 8px;z-index:99;color:#ffffff;'
            'background-color:rgba(0,0,0,0.62);font-size:12px;'
            'font-family:monospace;line-height:1.45;white-space:pre-wrap;'
            'text-align:left;"></pre>')

PAGE_TPL = """<?xml version="1.0" encoding="utf-8"?>
<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml"
      xmlns:epub="http://www.idpf.org/2007/ops"
      xml:lang="__LANG__" lang="__LANG__">
<head>
  <meta charset="utf-8"/>
  <title>__LABEL__</title>
  <meta name="viewport" content="width=__W__, height=__H__"/>
  <link rel="stylesheet" type="text/css" href="../css/style.css"/>
</head>
<body__SILENT__>
  <!-- Every id on this page is page-scoped, and the controller reads its own
       page key off the script tag below. A reading system may keep several
       spine documents in one DOM; a bare id="stage" / id="pgAudio" would then
       resolve to a neighbour's element, and the wrong recording would play.
       The <audio> carries no autoplay: pre-render / preload contexts run no
       scripts and would start it on their own, without any visibility gate. -->
  <div class="stage" id="stage-__KEY__">
    <img class="pg-img" src="../__IMG__" alt="__ALT__"/>
    <audio id="pgAudio-__KEY__" class="pg-audio" src="../__AUDIO__" preload="auto"
           controls="controls"></audio>
    <button type="button" id="pgBtn-__KEY__" class="pg-btn" aria-label="Play / pause narration">
      <span class="ico ico-play"></span>
      <span class="ico ico-pause"></span>
    </button>
  </div>__HUD__
  <script type="text/javascript" src="../js/reader.js" data-page="__KEY__"></script>
</body>
</html>
"""

NAV_TPL = """<?xml version="1.0" encoding="utf-8"?>
<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml"
      xmlns:epub="http://www.idpf.org/2007/ops"
      xml:lang="__LANG__" lang="__LANG__">
<head>
  <meta charset="utf-8"/>
  <title>__NAVTITLE__</title>
  <!-- fixed layout as well: if a reading system insists on showing this page it
       occupies one screen instead of reflowing into several -->
  <meta name="viewport" content="width=__W__, height=__H__"/>
  <style>
    /* NB: templates are rendered with str.replace, not str.format - CSS braces
       must stay single. Double braces here once shipped invalid CSS that only
       epubcheck caught (CSS-008). */
    html, body { margin: 0; padding: 0; background-color: __BG__; }
    body { font-family: sans-serif; color: #2b2b2b; padding: 60px 70px; }
    h1 { font-size: 56px; margin: 0 0 40px; }
    ol { font-size: 44px; line-height: 1.9; margin: 0; padding-left: 70px; }
    a { color: #2b2b2b; text-decoration: none; }
  </style>
</head>
<body>
  <nav epub:type="toc" id="toc">
    <h1>__NAVTITLE__</h1>
    <ol>
__ITEMS__
    </ol>
  </nav>
  <nav epub:type="landmarks" id="landmarks" hidden="hidden">
    <h2>Guide</h2>
    <ol>
      <li><a epub:type="cover" href="xhtml/cover.xhtml">__COVERLABEL__</a></li>
      <li><a epub:type="bodymatter" href="xhtml/p01.xhtml">__BODYLABEL__</a></li>
    </ol>
  </nav>
</body>
</html>
"""

NCX_TPL = """<?xml version="1.0" encoding="utf-8"?>
<!DOCTYPE ncx PUBLIC "-//NISO//DTD ncx 2005-1//EN"
  "http://www.daisy.org/z3986/2005/ncx-2005-1.dtd">
<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1"
     xml:lang="__LANG__">
  <head>
    <meta name="dtb:uid" content="__UID__"/>
    <meta name="dtb:depth" content="1"/>
    <meta name="dtb:totalPageCount" content="0"/>
    <meta name="dtb:maxPageNumber" content="0"/>
  </head>
  <docTitle><text>__TITLE__</text></docTitle>
  <navMap>
__ITEMS__
  </navMap>
</ncx>
"""

CONTAINER = """<?xml version="1.0" encoding="utf-8"?>
<container version="1.0"
           xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles>
    <rootfile full-path="OEBPS/content.opf"
              media-type="application/oebps-package+xml"/>
  </rootfiles>
</container>
"""

APPLE_OPTS = """<?xml version="1.0" encoding="UTF-8"?>
<display_options>
  <platform name="*">
    <option name="fixed-layout">true</option>
    <option name="open-to-spread">false</option>
    <option name="specified-fonts">true</option>
  </platform>
</display_options>
"""

OPF_TPL = """<?xml version="1.0" encoding="utf-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0"
         unique-identifier="bookid" xml:lang="__LANG__">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:identifier id="bookid">__UID__</dc:identifier>
    <dc:title>__TITLE__</dc:title>
__CREATOR__    <dc:language>__LANG__</dc:language>
    <dc:date>__DATE__</dc:date>
    <meta property="dcterms:modified">__MODIFIED__</meta>
    <meta name="cover" content="cover-image"/>
    <meta property="rendition:layout">pre-paginated</meta>
    <meta property="rendition:orientation">portrait</meta>
    <meta property="rendition:spread">none</meta>

    <meta property="schema:accessMode">visual</meta>
    <meta property="schema:accessMode">auditory</meta>
    <meta property="schema:accessModeSufficient">visual</meta>
    <meta property="schema:accessModeSufficient">auditory</meta>
    <meta property="schema:accessibilityFeature">alternativeText</meta>
    <meta property="schema:accessibilityHazard">none</meta>
  </metadata>
  <manifest>
__MANIFEST__
  </manifest>
  <spine toc="ncx">
__SPINE__
  </spine>
  <guide>
    <reference type="cover" title="__COVERLABEL__" href="xhtml/cover.xhtml"/>
  </guide>
</package>
"""


def xml_escape(text):
    return (str(text).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


def media_type(path):
    ext = os.path.splitext(path)[1].lower()
    if ext == ".mp3":
        return "audio/mpeg"
    if ext in (".m4a", ".aac"):
        return "audio/mp4"
    if ext in (".ogg", ".oga"):
        return "audio/ogg"
    if ext == ".wav":
        return "audio/wav"
    if ext in (".jpg", ".jpeg"):
        return "image/jpeg"
    if ext == ".png":
        return "image/png"
    if ext == ".gif":
        return "image/gif"
    if ext == ".webp":
        return "image/webp"
    return "application/octet-stream"


# ---------------------------------------------------------------------------
# discovery
# ---------------------------------------------------------------------------
def discover(src, cover=None, cover_audio=None, allow_silent=False):
    """Return (cover_img, cover_aud, pages, extras).

    pages is a list of dicts: {num, label, img, aud}. `num` is the raw stem.
    """
    entries = [f for f in os.listdir(src) if os.path.isfile(os.path.join(src, f))]
    images, audios = {}, {}
    for f in entries:
        stem, ext = os.path.splitext(f)
        ext = ext.lower()
        if ext in hard_rules.IMAGE_EXT:
            images[stem] = f
        elif ext in hard_rules.AUDIO_EXT:
            audios[stem] = f

    page_stems = sorted((s for s in images if DIGITS_RE.match(s)), key=int)
    if not page_stems:
        sys.exit("No page images found. Page images must be named with a pure "
                 "digit stem such as 01.png, 02.png, ...")

    pages, missing = [], []
    for stem in page_stems:
        aud = audios.get(stem)
        if aud is None:
            if not allow_silent:
                missing.append(stem)
                continue
        pages.append({"num": stem, "img": images[stem], "aud": aud})
    if missing:
        sys.exit("No matching audio for page(s): %s\n"
                 "Expected a file with the same stem, e.g. %s.mp3.\n"
                 "Pass --allow-silent to build those pages without narration."
                 % (", ".join(missing), missing[0]))

    # anything starting with a digit but not a pure digit is not a story page
    extras = [images[s] for s in images
              if not DIGITS_RE.match(s) and not _looks_like_cover(s)]

    if cover:
        if cover not in images.values() and cover not in images:
            sys.exit("--cover %s not found in %s" % (cover, src))
        cover_img = images.get(cover, cover)
    else:
        candidates = [images[s] for s in sorted(images)
                      if not DIGITS_RE.match(s)]
        scored = [(f, _cover_score(os.path.splitext(f)[0], audios))
                  for f in candidates]
        scored = [c for c in scored if c[1] > -1]
        if not scored:
            sys.exit("Could not identify a cover image. Give one whose name "
                     "does not start with a digit (cover.png / 封面.png), or "
                     "pass --cover.")
        scored.sort(key=lambda c: (-c[1], c[0]))
        cover_img = scored[0][0]

    cover_stem = os.path.splitext(cover_img)[0]
    if cover_audio:
        # Same rule as --cover, and for the same reason: a plain name from the
        # folder, or a path that really exists. Copying a stale file from
        # somewhere else is how a book ends up narrating something nobody put in
        # the folder - and the packaged copy is always renamed to
        # audio/cover.mp3, so the provenance becomes invisible afterwards.
        if cover_audio in audios:
            cover_aud = audios[cover_audio]
        elif cover_audio in audios.values():
            cover_aud = cover_audio
        elif (os.path.isfile(cover_audio)
              or os.path.isfile(os.path.join(src, cover_audio))):
            cover_aud = cover_audio
        else:
            sys.exit("--cover-audio %s not found in %s (nor as a file path)"
                     % (cover_audio, src))
    else:
        cover_aud = _pick_cover_audio(cover_stem, audios)

    return cover_img, cover_aud, pages, extras


def _looks_like_cover(stem):
    return not DIGITS_RE.match(stem) and not stem[:1].isdigit()


def _cover_score(stem, audios):
    """Higher is a better cover candidate; -1 means unusable."""
    if stem[:1].isdigit():
        return -1
    if _pick_cover_audio(stem, audios):
        return 2
    return 1


def _pick_cover_audio(cover_stem, audios):
    if not audios:
        return None
    if cover_stem in audios:
        return audios[cover_stem]
    # cover.png + cover-narration.mp3 -> the audio stem contains the image stem
    for stem, f in sorted(audios.items()):
        if cover_stem and (cover_stem in stem):
            return f
    for stem, f in sorted(audios.items()):
        if stem and stem in cover_stem:
            return f
    if len(audios) == 1:
        return list(audios.values())[0]
    return None


# ---------------------------------------------------------------------------
# images
# ---------------------------------------------------------------------------
def probe_size(path):
    with Image.open(path) as im:
        return im.size


def pick_canvas(sizes, forced=None):
    """One canvas for the whole book.

    Apple Books scales a fixed-layout book by the first page's proportions and
    blows up any page whose size differs, so every page must match exactly.
    """
    if forced:
        return forced
    counter = Counter(sizes)
    (w, h), _n = counter.most_common(1)[0]
    return (w, h)


def render_image(src, dst, canvas, quality=92):
    with Image.open(src) as im:
        if im.mode in ("RGBA", "LA", "P"):
            im = im.convert("RGBA")
            flat = Image.new("RGB", im.size, (255, 255, 255))
            flat.paste(im, mask=im.split()[-1])
            im = flat
        else:
            im = im.convert("RGB")

        cw, ch = canvas
        scale = min(cw / float(im.width), ch / float(im.height))
        nw = max(1, int(round(im.width * scale)))
        nh = max(1, int(round(im.height * scale)))
        if (nw, nh) != im.size:
            im = im.resize((nw, nh), Image.LANCZOS)

        out = Image.new("RGB", canvas, BG_RGB)
        out.paste(im, ((cw - nw) // 2, (ch - nh) // 2))
        out.save(dst, "JPEG", quality=quality, optimize=True,
                 progressive=True, subsampling=0)
    return canvas


# ---------------------------------------------------------------------------
# build
# ---------------------------------------------------------------------------
def build(args):
    src = os.path.abspath(args.src)
    if not os.path.isdir(src):
        sys.exit("Source folder not found: %s" % src)

    cover_img, cover_aud, pages, extras = discover(
        src, args.cover, args.cover_audio, args.allow_silent)

    if extras:
        print("Not treated as story pages (name starts with a digit but is not "
              "a pure number): %s" % ", ".join(extras))

    # Say where the cover came from. The packaged files are always named
    # images/cover.jpg and audio/cover.mp3, so without this line a cover that is
    # really page 01 looks like something the builder invented.
    reused = []
    if cover_img in set(p["img"] for p in pages):
        reused.append("image (%s)" % cover_img)
    if cover_aud and cover_aud in set(p["aud"] for p in pages if p["aud"]):
        reused.append("narration (%s)" % cover_aud)
    print("Cover: image \"%s\", narration %s"
          % (cover_img, '"%s"' % cover_aud if cover_aud else "(none)"))
    if reused:
        print("Note: the cover reuses a story page's %s, so that asset is packaged "
              "twice - as images/cover.jpg / audio/cover.mp3 AND as that page's "
              "own file. Add a cover.png + cover.mp3 to the folder (or pass "
              "--cover / --cover-audio) if the cover should stand alone; leaving "
              "it as is means the cover looks and sounds exactly like that page."
              % " and ".join(reused))

    title = args.title or os.path.basename(src) or "Picture Book"
    lang = args.language
    now = datetime.now(timezone.utc)
    date = args.date or now.strftime("%Y-%m-%d")
    # dcterms:modified must be the packaging moment, in UTC
    modified = now.strftime("%Y-%m-%dT%H:%M:%SZ")

    cover_w, cover_h = probe_size(os.path.join(src, cover_img))
    sizes = [probe_size(os.path.join(src, p["img"])) for p in pages]
    canvas = pick_canvas(sizes + [(cover_w, cover_h)], args.canvas)
    odd = [p["img"] for p, s in zip(pages, sizes) if s != canvas]
    if odd:
        print("Pages resized onto the shared canvas %dx%d: %s"
              % (canvas[0], canvas[1], ", ".join(odd)))
    print("Canvas: %dx%d   pages: %d (+ cover)" % (canvas[0], canvas[1],
                                                   len(pages)))

    # The staging tree and the finished book both live under <workdir>/build/,
    # so a build never scatters files around the working folder.
    build_dir = os.path.join(os.getcwd(), "build")
    os.makedirs(build_dir, exist_ok=True)
    tmp = tempfile.mkdtemp(prefix="epubbuild", dir=build_dir)
    try:
        oebps = os.path.join(tmp, "OEBPS")
        for sub in ("images", "audio", "css", "js", "xhtml"):
            os.makedirs(os.path.join(oebps, sub))
        os.makedirs(os.path.join(tmp, "META-INF"))

        render_image(os.path.join(src, cover_img),
                     os.path.join(oebps, "images", "cover.jpg"), canvas,
                     args.quality)
        for p in pages:
            render_image(os.path.join(src, p["img"]),
                         os.path.join(oebps, "images", "p%s.jpg" % p["num"]),
                         canvas, args.quality)

        if cover_aud:
            shutil.copyfile(os.path.join(src, cover_aud),
                            os.path.join(oebps, "audio", "cover.mp3"))
        for p in pages:
            if p["aud"]:
                shutil.copyfile(os.path.join(src, p["aud"]),
                                os.path.join(oebps, "audio",
                                             "p%s.mp3" % p["num"]))

        write(oebps, "css/style.css", CSS.replace("__BG__", BG_HEX))
        write(oebps, "js/reader.js", JS)

        # ---- page documents -------------------------------------------------
        rows = [{"key": "cover", "file": "cover.xhtml",
                 "doc_id": "cover-page",
                 "label": args.cover_label, "img": "images/cover.jpg",
                 "aud": "audio/cover.mp3" if cover_aud else None}]
        for i, p in enumerate(pages, start=1):
            rows.append({"key": "p%s" % p["num"],
                         "file": "p%s.xhtml" % p["num"],
                         "doc_id": "pg%s" % p["num"],
                         "label": "%s %d" % (args.page_label, i),
                         "img": "images/p%s.jpg" % p["num"],
                         "aud": "audio/p%s.mp3" % p["num"] if p["aud"] else None})

        hud = HUD_HTML if args.debug_hud else ""
        for row in rows:
            html = (PAGE_TPL
                    .replace("__HUD__", hud)
                    .replace("__SILENT__",
                             (" " + hard_rules.SILENT_ATTR)
                             if row["aud"] is None else "")
                    .replace("__KEY__", row["key"])
                    .replace("__LANG__", lang)
                    .replace("__LABEL__", xml_escape(row["label"]))
                    .replace("__ALT__", xml_escape(row["label"]))
                    .replace("__W__", str(canvas[0]))
                    .replace("__H__", str(canvas[1]))
                    .replace("__IMG__", row["img"])
                    .replace("__AUDIO__", row["aud"] or row["img"]))
            if row["aud"] is None:
                # silent page: drop the audio element entirely and declare the
                # page silent, so the checker accepts a deliberate silence but
                # still flags a page that lost its audio by accident
                html = re.sub(r'\s*<audio id="pgAudio-[^"]*".*?</audio>', "", html,
                              flags=re.S)
                html = re.sub(r'\s*<button type="button" id="pgBtn-[^"]*".*?</button>',
                              "", html, flags=re.S)
            # Self-check with the checker's own predicates: what this script
            # just rendered either passes them or the build stops here.
            violations = hard_rules.check_page(
                hard_rules.page_facts(row["file"], html), has_controller=True)
            fatal = [msg for sev, msg in violations if sev == "error"]
            if fatal:
                sys.exit("Build self-check failed for %s:\n  %s"
                         % (row["file"], "\n  ".join(fatal)))
            for sev, msg in violations:
                print("[warning] %s" % msg)
            write(oebps, "xhtml/" + row["file"], html)

        # ---- navigation -----------------------------------------------------
        nav_items = "\n".join(
            '      <li><a href="xhtml/%s">%s</a></li>'
            % (r["file"], xml_escape(r["label"])) for r in rows)
        write(oebps, "nav.xhtml",
              NAV_TPL.replace("__LANG__", lang)
                     .replace("__NAVTITLE__", xml_escape(args.nav_label))
                     .replace("__COVERLABEL__", xml_escape(args.cover_label))
                     .replace("__BODYLABEL__", xml_escape(args.page_label + " 1"))
                     .replace("__W__", str(canvas[0]))
                     .replace("__H__", str(canvas[1]))
                     .replace("__BG__", BG_HEX)
                     .replace("__ITEMS__", nav_items))

        uid = "urn:uuid:" + str(uuid.uuid4())
        ncx_items = "\n".join(
            '    <navPoint id="%s" playOrder="%d">\n'
            '      <navLabel><text>%s</text></navLabel>\n'
            '      <content src="xhtml/%s"/>\n'
            '    </navPoint>'
            % (r["key"], i + 1, xml_escape(r["label"]), r["file"])
            for i, r in enumerate(rows))
        write(oebps, "toc.ncx",
              NCX_TPL.replace("__LANG__", lang).replace("__UID__", uid)
                     .replace("__TITLE__", xml_escape(title))
                     .replace("__ITEMS__", ncx_items))

        # ---- package --------------------------------------------------------
        manifest = [
            '    <item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>',
            '    <item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>',
            '    <item id="css" href="css/style.css" media-type="text/css"/>',
            # properties="scripted" marks a CONTENT DOCUMENT holding script.
            # Putting it on the .js resource itself is epubcheck OPF-012.
            '    <item id="js" href="js/reader.js" media-type="text/javascript"/>',
            '    <item id="cover-image" href="images/cover.jpg" media-type="image/jpeg" properties="cover-image"/>',
        ]
        if cover_aud:
            manifest.append('    <item id="cover-audio" href="audio/cover.mp3" '
                            'media-type="audio/mpeg"/>')
        for p in pages:
            n = p["num"]
            manifest.append('    <item id="img%s" href="images/p%s.jpg" '
                            'media-type="image/jpeg"/>' % (n, n))
            if p["aud"]:
                manifest.append('    <item id="aud%s" href="audio/p%s.mp3" '
                                'media-type="audio/mpeg"/>' % (n, n))
        for row in rows:
            manifest.append('    <item id="%s" href="xhtml/%s" '
                            'media-type="application/xhtml+xml" '
                            'properties="scripted"/>'
                            % (row["doc_id"], row["file"]))

        # The navigation document is deliberately NOT in the spine.
        spine = ['    <itemref idref="cover-page" linear="yes"/>']
        for p in pages:
            spine.append('    <itemref idref="pg%s" linear="yes"/>' % p["num"])

        creator = ("    <dc:creator>%s</dc:creator>\n" % xml_escape(args.author)
                   if args.author else "")
        opf = (OPF_TPL
               .replace("__LANG__", lang)
               .replace("__UID__", uid)
               .replace("__TITLE__", xml_escape(title))
               .replace("__CREATOR__", creator)
               .replace("__DATE__", date)
               .replace("__MODIFIED__", modified)
               .replace("__COVERLABEL__", xml_escape(args.cover_label))
               .replace("__MANIFEST__", "\n".join(manifest))
               .replace("__SPINE__", "\n".join(spine)))
        write(oebps, "content.opf", opf)

        write(tmp, "META-INF/container.xml", CONTAINER)
        write(tmp, "META-INF/com.apple.ibooks.display-options.xml", APPLE_OPTS)

        out = os.path.abspath(
            args.out or os.path.join(build_dir, title + ".epub"))
        os.makedirs(os.path.dirname(out), exist_ok=True)
        if os.path.exists(out):
            os.remove(out)
        with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
            info = zipfile.ZipInfo("mimetype", (1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_STORED
            z.writestr(info, hard_rules.MIMETYPE_BYTES)
            for root, _dirs, files in os.walk(tmp):
                for name in sorted(files):
                    full = os.path.join(root, name)
                    rel = os.path.relpath(full, tmp).replace("\\", "/")
                    if rel == "mimetype":
                        continue
                    z.write(full, rel)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print("Wrote %s  (%.2f MB, %d pages + cover)"
          % (out, os.path.getsize(out) / 1048576.0, len(pages)))
    print("Validate it with:  python check_epub.py \"%s\"" % out)
    return out


def write(root, rel, text):
    path = os.path.join(root, rel)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)


def parse_canvas(value):
    if not value:
        return None
    m = re.match(r"^\s*(\d+)\s*[xX*]\s*(\d+)\s*$", value)
    if not m:
        sys.exit("--canvas must look like 940x1672")
    return (int(m.group(1)), int(m.group(2)))


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--src", default=".", help="folder with the images/audio")
    ap.add_argument("--out",
                    help="output .epub path (default: build/<title>.epub)")
    ap.add_argument("--title", help="book title (default: folder name)")
    ap.add_argument("--author", default="", help="dc:creator")
    ap.add_argument("--language", default="zh-CN")
    ap.add_argument("--date", default=None,
                    help="dc:date (default: today, UTC)")
    ap.add_argument("--canvas", default=None,
                    help="force canvas, e.g. 940x1672 (default: most common "
                         "page image size)")
    ap.add_argument("--quality", type=int, default=92, help="JPEG quality")
    ap.add_argument("--cover", help="cover image filename")
    ap.add_argument("--cover-audio", dest="cover_audio",
                    help="cover narration filename")
    ap.add_argument("--allow-silent", action="store_true",
                    help="build pages that have no matching audio")
    ap.add_argument("--debug-hud", dest="debug_hud", action="store_true",
                    help="stamp a diagnostic readout onto every page showing "
                         "what the playback controller did (for debugging a "
                         "reader on a real device; do not ship it)")
    ap.add_argument("--cover-label", default="Cover")
    ap.add_argument("--page-label", default="Page")
    ap.add_argument("--nav-label", default="Contents")
    args = ap.parse_args(argv)
    args.canvas = parse_canvas(args.canvas)
    build(args)


if __name__ == "__main__":
    main()
