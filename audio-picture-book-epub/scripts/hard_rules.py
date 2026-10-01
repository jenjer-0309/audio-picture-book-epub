#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""The hard rules, encoded once, for both the builder and the checker.

Every rule in this file used to live twice: build_epub.py emitted it through
templates and regex surgery, check_epub.py verified it through string counting
and XML parsing. The two copies drifted (see the repo history), so now they are
one:

  * fact extractors turn raw artifacts (a page's HTML, the package
    structures) into plain dicts/lists;
  * predicates turn facts into violations, each ``(severity, message)`` with
    severity ``"error"`` or ``"warning"``.

check_epub.py extracts facts from a finished ZIP and runs the predicates;
build_epub.py runs the page predicates on its own just-rendered pages before
packaging, so an emit-side regression fails the build instead of shipping.

Only the standard library is used here, and only pure functions - no zipfile,
no Pillow, no I/O.
"""

from __future__ import annotations

import os
import re

# ---------------------------------------------------------------------------
# Shared constants - the single source both sides read.
# ---------------------------------------------------------------------------

IMAGE_EXT = (".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".tif", ".tiff")
AUDIO_EXT = (".mp3", ".m4a", ".aac", ".ogg", ".oga", ".wav")

MIMETYPE_BYTES = b"application/epub+zip"

# Unscoped element ids. A reading system may keep several spine documents in
# one DOM (observed on a real device), where a bare id="pgAudio" resolves to
# whichever page came first - the reader taps play on page 9 and hears the
# cover.
BARE_IDS = ('id="stage"', 'id="pgAudio"', 'id="pgBtn"')

# The explicit marker a page carries when it was built without narration
# (--allow-silent). The builder writes it, the checker accepts a page with zero
# <audio> elements only when it sees this attribute. It must be an attribute,
# not a comment: the checker strips HTML comments before counting anything.
SILENT_ATTR = 'data-audio="none"'


# ---------------------------------------------------------------------------
# Naming conventions. The builder emits these ids, the checker demands them.
# ---------------------------------------------------------------------------

def audio_id(key):
    return "pgAudio-" + key


def btn_id(key):
    return "pgBtn-" + key


# ---------------------------------------------------------------------------
# Fact extraction. Pure functions over strings/structures.
# ---------------------------------------------------------------------------

def page_facts(doc_name, raw):
    """Facts about one page document, extracted from its raw XHTML.

    Markup comments are inert, so they are stripped first - a page is allowed
    to explain the rules it follows in a comment without tripping a guard.
    """
    raw = re.sub(r"<!--.*?-->", "", raw, flags=re.S)
    m = re.search(r'src="([^"]*audio/[^"]+)"', raw)
    src = m.group(1) if m else None
    pm = re.search(r'<script[^>]*\bdata-page="([^"]+)"', raw)
    vm = re.search(r'name="viewport"\s+content="([^"]+)"', raw)
    # Counted in any of its forms: autoplay="...", autoplay='...', and the
    # bare boolean autoplay inside an <audio>/<video> tag. The page label is
    # echoed into <title> and alt=, where literal text like `autoplay=2` must
    # not inflate the count - the build self-check would abort on its own
    # output.
    quoted = re.findall(r'\bautoplay\s*=\s*["\']', raw)
    boolean = [t for t in re.findall(r'<(?:audio|video)\b[^>]*>', raw)
               if re.search(r'(?<=\s)autoplay(?=[\s/>])', t)]
    return {
        "doc": doc_name,
        "audio_count": raw.count("<audio"),
        "autoplay_count": len(quoted) + len(boolean),
        "noscript": "<noscript" in raw,
        "silent_marker": SILENT_ATTR in raw,
        "audio_src": src,
        "src_ref_count": raw.count(src) if src else 0,
        "script_before_body": ("<script" in raw and "<body" in raw
                               and raw.find("<script") < raw.find("<body")),
        "bare_ids": [b for b in BARE_IDS if b in raw],
        "data_page": pm.group(1) if pm else None,
        "ids": set(re.findall(r'id="([^"]+)"', raw)),
        "viewport": vm.group(1) if vm else None,
    }


# ---------------------------------------------------------------------------
# Predicates. Each returns a list of (severity, message) violations.
# ---------------------------------------------------------------------------

def check_mimetype(first_filename, first_stored, data):
    """The packaging rule that silently breaks everything when violated."""
    if first_filename is None:
        return [("error", "archive is empty")]
    if first_filename != "mimetype":
        return [("error", "first zip entry is %r, must be 'mimetype'"
                 % first_filename)]
    if not first_stored:
        return [("error", "'mimetype' is compressed; it must be STORED")]
    if data != MIMETYPE_BYTES:
        return [("error", "'mimetype' content is %r" % data)]
    return []


def check_page(facts, has_controller):
    """GUARD 2 + 2b: one audio element, no autoplay, page-scoped ids, script
    placement.

    A page is either a narrated page - exactly one <audio>, no autoplay, no
    silent marker - or a declared-silent page: zero <audio> and the SILENT_ATTR
    marker. Any other combination is a violation.
    """
    v = []
    doc = facts["doc"]
    n = facts["audio_count"]
    autoplay = facts["autoplay_count"]
    marker = facts["silent_marker"]

    if marker:
        if n:
            v.append(("error",
                      "%s carries %s but still has %d <audio> element(s); a "
                      "declared-silent page must contain none."
                      % (doc, SILENT_ATTR, n)))
    elif n != 1:
        msg = ("%s contains %d <audio> elements; exactly 1 is required "
               "(a second one plays the same file twice)" % (doc, n))
        if n == 0:
            msg += (" If this page is intentionally silent, mark its "
                    "<body> with %s." % SILENT_ATTR)
        v.append(("error", msg))

    if facts["noscript"]:
        v.append(("error",
                  "%s contains <noscript>. Pre-render contexts (Apple Books "
                  "parsing the book and its thumbnails) do not run scripts, "
                  "so the fallback branch is live - a second element decoding "
                  "the same file, which once started plays the recording "
                  "again about 0.5 s later." % doc))

    if autoplay:
        v.append(("error",
                  "%s carries %d autoplay attribute(s); none are allowed. "
                  "Pre-render / preload contexts run no scripts and start "
                  "autoplay on their own - Apple Books swept the whole book "
                  "cover to back page that way. Playback must come from the "
                  "controller only." % (doc, autoplay)))

    if not marker and n == 1:
        # one audio element, one URL reference
        if facts["audio_src"] is None:
            v.append(("error",
                      "%s has an <audio> element whose src does not point into "
                      "audio/; nothing would play." % doc))
        elif facts["src_ref_count"] != 1:
            v.append(("error", "%s references %s %d times; exactly 1 is required"
                      % (doc, facts["audio_src"], facts["src_ref_count"])))

    if facts["script_before_body"]:
        v.append(("error",
                  "%s loads its script in <head>; getElementById would return "
                  "null. Move it to the end of <body>." % doc))

    for bare in facts["bare_ids"]:
        v.append(("error",
                  "%s uses the unscoped %s. Each page must carry its own ids "
                  "(stage-<key>, pgAudio-<key>, pgBtn-<key>); a shared id "
                  "resolves to another page's element and plays the wrong "
                  "recording." % (doc, bare)))

    # The controller resolves its own elements from the script's data-page,
    # so both page-scoped ids must agree with it.
    if not marker and n == 1 and has_controller:
        dp = facts["data_page"]
        if not dp:
            v.append(("error",
                      "%s: the controller script carries no data-page, so the "
                      "page cannot identify its own audio element" % doc))
        else:
            for need in (audio_id(dp), btn_id(dp)):
                if need not in facts["ids"]:
                    v.append(("error",
                              "%s is missing id=\"%s\" for its own "
                              "data-page=\"%s\"" % (doc, need, dp)))

    if facts["viewport"] is None:
        v.append(("warning", "%s has no <meta name=\"viewport\">; fixed layout "
                  "needs one" % doc))
    return v


def check_viewports(viewports):
    """GUARD 1 (aggregate): every page shares one viewport."""
    uniq = set(viewports.values())
    if not uniq or len(uniq) == 1:
        return []
    v = [("error",
          "pages do not share one viewport (%d distinct values). Apple Books "
          "scales a fixed-layout book by the first page and blows up any page "
          "that differs:" % len(uniq))]
    for doc in sorted(viewports):
        v.append(("error", "    %s -> %s" % (os.path.basename(doc),
                                             viewports[doc])))
    return v


def check_image_sizes(sizes):
    """GUARD 1b (aggregate): every page image shares one pixel size."""
    uniq = set(sizes.values())
    if not uniq or len(uniq) == 1:
        return []
    v = [("error",
          "page images do not all share one pixel size (%d distinct sizes). "
          "Apple Books will enlarge the odd ones:" % len(uniq))]
    for n in sorted(sizes):
        v.append(("error", "    %s -> %dx%d" % (os.path.basename(n),
                                                sizes[n][0], sizes[n][1])))
    return v


def check_spine(manifest, spine_ids, linear):
    """GUARD 3: no navigation document, no linear="no" item, inside the spine.

    Apple Books ignores linear="no" and still renders the document as a
    reading page, so a reflowable link list becomes extra pages at the end.
    """
    v = []
    for sid in spine_ids:
        if linear.get(sid) == "no":
            v.append(("error",
                      "spine contains linear=\"no\" item '%s' (%s). Apple "
                      "Books ignores linear=\"no\" and still renders it as a "
                      "reading page."
                      % (sid, manifest.get(sid, {}).get("href", "?"))))
    for sid in spine_ids:
        if "nav" in manifest.get(sid, {}).get("props", []):
            v.append(("error",
                      "navigation document '%s' is inside the spine; it will "
                      "show up as extra pages. Declare it in the manifest "
                      "only." % sid))
    return v


def check_scripted_props(manifest):
    """epubcheck OPF-012: properties="scripted" marks a content document."""
    v = []
    for mid, item in sorted(manifest.items()):
        if "scripted" in item["props"] and item["type"] != "application/xhtml+xml":
            v.append(("error",
                      "manifest item '%s' (%s) carries "
                      "properties=\"scripted\" but is not a content document; "
                      "epubcheck reports this as OPF-012. That property marks "
                      "an XHTML document that contains script."
                      % (mid, item["type"])))
    return v
