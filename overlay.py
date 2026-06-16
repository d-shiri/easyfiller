"""A modern loading overlay injected into the editor's webview.

We render our own CSS spinner inside the editor (instead of Anki's stock Qt
progress dialog) so the indicator looks current and can stay up for the whole
generate -> pronounce job. Appended to <body> as a sibling of the fields, so
Anki's field re-render (set_note) doesn't remove it.

Under the spinner we show a checklist of steps, each in one of these states:
pending (dim hollow dot) -> active (spinning ring) -> done (green check) /
error (red). Callers build the list with start() and advance it with set_step().
"""

import json
import random

from aqt import mw

from . import loaders

# Overlay chrome only; the spinner itself (.ga-spinner) comes from loaders.py.
# A fixed-size .ga-stage reserves space and centers each loader, so animations
# whose drawing spills past their own box (or that vary in size) stay fully
# visible inside the card instead of being clipped.
_BASE_CSS = """
#ga-overlay{position:fixed;inset:0;z-index:2147483647;display:flex;
align-items:center;justify-content:center;background:rgba(18,18,22,.45);
-webkit-backdrop-filter:blur(3px);backdrop-filter:blur(3px);
animation:ga-fade .15s ease both;}
@keyframes ga-fade{from{opacity:0}to{opacity:1}}
.ga-card{display:flex;flex-direction:column;align-items:center;gap:18px;
padding:24px 28px;border-radius:16px;background:rgba(255,255,255,.92);
box-shadow:0 10px 34px rgba(0,0,0,.28);
font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;}
.ga-stage{flex:0 0 auto;width:96px;height:96px;display:grid;
place-items:center;overflow:visible;}
.ga-spinner{flex:0 0 auto;}
.ga-steps{display:flex;flex-direction:column;gap:9px;align-self:stretch;
min-width:215px;}
.ga-step{display:flex;align-items:center;gap:10px;font-size:13px;font-weight:500;
letter-spacing:.2px;color:#1d1d1f;
font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;}
.ga-step .ga-ic{width:16px;height:16px;flex:0 0 auto;box-sizing:border-box;
border-radius:50%;position:relative;}
.ga-pending{opacity:.45;}
.ga-pending .ga-ic{border:2px solid currentColor;}
.ga-active{font-weight:700;}
.ga-active .ga-ic{border:2px solid rgba(38,154,242,.25);border-top-color:#269af2;
animation:ga-spin .7s linear infinite;}
@keyframes ga-spin{to{transform:rotate(360deg)}}
.ga-done .ga-ic{background:#34c759;}
.ga-done .ga-ic::after{content:"";position:absolute;left:5px;top:2px;width:4px;
height:8px;border:solid #fff;border-width:0 2px 2px 0;transform:rotate(45deg);}
.ga-error .ga-ic{background:#ff3b30;}
.ga-error .ga-ic::after{content:"!";position:absolute;inset:0;color:#fff;
font-size:12px;font-weight:800;line-height:16px;text-align:center;}
@media (prefers-color-scheme:dark){
.ga-card{background:rgba(44,44,48,.94);}
.ga-step{color:#f5f5f7;}
}
"""

# Build the overlay shell once (style + spinner + empty steps container).
_SHOW_JS = """
(function(){
  if(document.getElementById('ga-overlay')) return;
  var s = document.createElement('style');
  s.id = 'ga-overlay-style';
  s.textContent = %s;
  document.head.appendChild(s);
  var o = document.createElement('div');
  o.id = 'ga-overlay';
  o.innerHTML = '<div class="ga-card"><div class="ga-stage">'
    + '<div class="ga-spinner"></div></div>'
    + '<div class="ga-steps"></div></div>';
  document.body.appendChild(o);
})();
"""

# Re-render the step list from a [{label,state}, ...] array (labels via
# textContent so note/field text can never inject markup).
_RENDER_JS = """
(function(){
  var steps = %s;
  var o = document.getElementById('ga-overlay');
  if(!o) return;
  var box = o.querySelector('.ga-steps');
  if(!box) return;
  var html = '';
  for(var i=0;i<steps.length;i++){
    html += '<div class="ga-step ga-' + steps[i].state + '">'
      + '<span class="ga-ic"></span><span class="ga-lbl"></span></div>';
  }
  box.innerHTML = html;
  var els = box.querySelectorAll('.ga-step .ga-lbl');
  for(var j=0;j<steps.length;j++){ els[j].textContent = steps[j].label; }
})();
"""

_HIDE_JS = """
(function(){
  var o = document.getElementById('ga-overlay');
  if(o){
    o.style.animation = 'ga-fade .15s ease reverse both';
    setTimeout(function(){
      o.remove();
      var s = document.getElementById('ga-overlay-style');
      if(s){ s.remove(); }
    }, 140);
  }
})();
"""

# Current step list: [[key, label, state], ...]. One operation runs at a time.
_steps = []
_shown = False


def _web(editor):
    return getattr(editor, "web", None) if editor else None


# --------------------------------------------------------------------------- #
# Suppress Anki's own "Processing…" dialog while our overlay is up.            #
#                                                                              #
# Triggering an action saves the editor's note (a "blur" bridge command ->     #
# _save_current_note -> a CollectionOp), whose progress window pops up after a #
# ~600ms QTimer -- on top of our overlay. Our overlay is the only progress UI  #
# we want, so for as long as it is visible we (1) close any save dialog already #
# armed and (2) block new ones from appearing. Restored on hide().            #
# --------------------------------------------------------------------------- #
_orig_progress_start = None


def _suppress_anki_progress():
    global _orig_progress_start
    if _orig_progress_start is not None:
        return  # already suppressed (set_step() may be called repeatedly)
    pm = mw.progress
    _orig_progress_start = pm.start
    pm.start = lambda *a, **k: None
    # If the note-save's progress timer is already armed/shown, cancel it.
    timer = getattr(pm, "_show_timer", None)
    if getattr(pm, "_win", None) or (timer is not None and timer.isActive()):
        try:
            pm.finish()
        except Exception:
            pass


def _restore_anki_progress():
    global _orig_progress_start
    if _orig_progress_start is None:
        return
    mw.progress.start = _orig_progress_start
    _orig_progress_start = None


def _ensure(editor):
    """Inject the overlay shell once (picking a random loader) and suppress
    Anki's own progress dialog for as long as we're shown."""
    global _shown
    web = _web(editor)
    if not web:
        return None
    _suppress_anki_progress()
    if not _shown:
        web.eval(_SHOW_JS % json.dumps(_BASE_CSS + random.choice(loaders.LOADERS)))
        _shown = True
    return web


def _render(editor):
    web = _web(editor)
    if web:
        payload = [{"label": s[1], "state": s[2]} for s in _steps]
        web.eval(_RENDER_JS % json.dumps(payload))


def is_shown():
    return _shown


def start(editor, steps):
    """Show the overlay with an initial checklist.

    `steps` is a list of (key, label) or (key, label, state); state defaults to
    "pending". `key` identifies the step for later set_step() updates.
    """
    global _steps
    _steps = [
        [s[0], s[1], s[2] if len(s) > 2 else "pending"] for s in steps
    ]
    if _ensure(editor):
        _render(editor)


def set_step(editor, key, label=None, state=None):
    """Update an existing step's label and/or state, creating it if missing.

    Creating on demand lets a standalone phase (e.g. pronounce on its own) open
    the overlay with just its step, while the combined flow updates a step that
    start() already placed.
    """
    found = next((s for s in _steps if s[0] == key), None)
    if found is None:
        found = [key, label or key, state or "active"]
        _steps.append(found)
    else:
        if label is not None:
            found[1] = label
        if state is not None:
            found[2] = state
    if _ensure(editor):
        _render(editor)


def hide(editor):
    global _steps, _shown
    _restore_anki_progress()
    _steps = []
    _shown = False
    web = _web(editor)
    if web:
        web.eval(_HIDE_JS)
