"""Assemble the two builds from head.html + body.html.

  flowboard.html   the desk build  -- fetches /api/flow from the local server
  flow-board.html  the artifact    -- one session's board embedded, no server

One source, two consumers, so a UI fix cannot land in only one of them.
"""
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))


def read(name):
    with open(os.path.join(HERE, name), "r", encoding="utf-8") as fh:
        return fh.read()


EMBED_HEAD = ('<script id="boarddata" type="application/json">__DATA__</script>\n'
              '<script>\n(function(){\n"use strict";\n'
              "var DATA = JSON.parse(document.getElementById('boarddata').textContent);\n"
              "var META = DATA.meta, CARDS = DATA.cards;")

DESK_HEAD = '''<script>
(function(){
"use strict";
/* Desk build: the board comes from the local server, which scans on its own
   thread every 5 minutes whether or not a tab is open. flowboard.html is read
   from disk per request, so a UI change needs a reload and nothing more -- but
   a STALE TAB looks identical to a failed deploy, which is why the server
   stamps the served file's mtime into the footer. */
var DATA = {meta:{counts:{board:0,spreads:0,near:0,out:0},session:'',captured_et:'',
  alerts:0,contracts:0,tickers:0,observed_max:0,warnings:[],status:''},cards:[]};
var META = DATA.meta, CARDS = DATA.cards;'''

EMBED_BOOT = '''function boot(){
  renderStats(); renderLanes(); renderGrid(); bindOpts();
}
if(document.fonts && document.fonts.ready){ document.fonts.ready.then(boot); } else { boot(); }'''

DESK_BOOT = '''function boot(){
  renderStats(); renderLanes(); renderGrid(); bindOpts();
}
function applyBoard(d){
  DATA=d; META=d.meta||META; CARDS=d.cards||[];
  if(!META.captured_et && META.scanned_at) META.captured_et=META.scanned_at.slice(11,16)+'Z';
  LOADED=true;
  renderStats(); renderLanes(); renderGrid();
  var warn=document.getElementById('warnbar');
  var ws=(META.warnings||[]);
  if(ws.length){
    /* Two different problems used to share one sentence, and the setup case --
       the one a new install actually hits -- came out as
       "Endpoint shape problem: scan returned no API token found".
       A message a first-time reader cannot act on is worse than none. */
    warn.style.display='block';
    warn.innerHTML = ws.map(function(w){
      return w.endpoint==='scan'
        ? '<b>The desk cannot scan yet.</b> '+esc(w.shape)
        : '<b>Unexpected response from '+esc(w.endpoint)+'.</b> Got '+esc(w.shape)+
          ' \\u2014 the board may be incomplete.';
    }).join('<br>');
  } else { warn.style.display='none'; }
}
function poll(){
  fetch('/api/flow',{cache:'no-store'}).then(function(r){return r.json();})
    .then(function(d){
      applyBoard(d);
      document.getElementById('staleness').textContent='';
    })
    .catch(function(){
      /* Never fail silently and never blank a good board: keep the last one
         and say it is stale. GEX ES Desk showed an empty chart with no error
         and cost two rounds of guessing. */
      document.getElementById('staleness').textContent=
        'Lost contact with the desk \\u2014 showing the last board it sent. '+
        'Is the START_HERE window still open?';
    });
}
boot();
poll();
setInterval(poll,60000);'''

BARS = ('<p id="warnbar" class="why" style="display:none;color:var(--bear);'
        'border-left-color:var(--bear);margin:12px 0"></p>\n'
        '<p id="staleness" class="lane-note" style="color:var(--pend)"></p>\n'
        '<nav id="lanes" aria-label="Lanes"></nav>')


# The dashboard's theme engine (desk build only; the artifact build has no dashboard to ask).
THEME_HOOK = '<script src="http://127.0.0.1:8700/theme.js?desk=flow"></script>\n'


def build():
    head, body = read("head.html"), read("body.html")

    # --- desk build ------------------------------------------------------
    desk = body.replace(EMBED_HEAD, DESK_HEAD).replace(EMBED_BOOT, DESK_BOOT)
    assert DESK_HEAD in desk and DESK_BOOT in desk, "desk build markers missing"
    desk = desk.replace('<nav id="lanes" aria-label="Lanes"></nav>', BARS)
    page = ('<!doctype html>\n<html lang="en">\n<head>\n<meta charset="utf-8">\n'
            '<meta name="viewport" content="width=device-width,initial-scale=1">\n'
            + head + THEME_HOOK + '</head>\n<body>\n' + desk + '\n</body>\n</html>\n')
    with open(os.path.join(HERE, "flowboard.html"), "w", encoding="utf-8") as fh:
        fh.write(page)

    # --- artifact build --------------------------------------------------
    data_path = os.path.join(HERE, "board.min.json")
    if os.path.exists(data_path):
        data = read("board.min.json")
        art = body.replace("__DATA__", data.replace("</", "<\\/"))   # data can't close the <script> tag
        with open(os.path.join(HERE, "flow-board.html"), "w", encoding="utf-8") as fh:
            fh.write(head + art)
    return len(page)


if __name__ == "__main__":
    print("flowboard.html %d bytes" % build())
