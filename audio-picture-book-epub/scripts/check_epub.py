#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Validate a per-page narrated fixed-layout EPUB 3 picture book.

Checks the structure a reading system relies on, plus explicit regression guards
for the defects that are easy to reintroduce and invisible in a desktop browser:

  1. every page image the same pixel size, and every page the same viewport
     (Apple Books scales a book by its first page and enlarges the odd ones)
  2. exactly one <audio> per page, the audio URL referenced exactly once, and
     no autoplay attribute anywhere (pre-render contexts run no scripts and
     start autoplay themselves - Apple Books once swept the whole book cover
     to back page that way); a <noscript> fallback plays the same file twice,
     ~0.5 s apart
  2b. every page's element ids are page-scoped and the controller names its own
     page (data-page), so it can never reach a neighbour's <audio> (a reader
     that keeps several spine documents in one DOM otherwise plays the cover)
  3. no navigation document and no linear="no" item inside the spine
     (Apple Books renders those as extra pages at the end of the book)

A book that packages audio but no js/reader.js is an error: the controller is
the only start path. Plus the epubcheck OPF-012 rule.

Usage:
    python check_epub.py book.epub
"""

from __future__ import annotations

import hashlib
import os
import re
import struct
import sys
import zipfile
import xml.etree.ElementTree as ET

import hard_rules

NS = {
    "opf": "http://www.idpf.org/2007/opf",
    "dc": "http://purl.org/dc/elements/1.1/",
    "x": "http://www.w3.org/1999/xhtml",
    "c": "urn:oasis:names:tc:opendocument:xmlns:container",
    "ncx": "http://www.daisy.org/z3986/2005/ncx/",
}


def jpeg_size(data):
    i = 2
    while i < len(data) - 9:
        if data[i] != 0xFF:
            i += 1
            continue
        marker = data[i + 1]
        if marker in (0xC0, 0xC1, 0xC2, 0xC3):
            h, w = struct.unpack(">HH", data[i + 5:i + 9])
            return (w, h)
        if marker in (0xD8, 0xD9) or 0xD0 <= marker <= 0xD7:
            i += 2
            continue
        seg = struct.unpack(">H", data[i + 2:i + 4])[0]
        i += 2 + seg
    return None


def validate(path):
    errors, warnings, notes = [], [], []

    def err(msg):
        errors.append(msg)

    def warn(msg):
        warnings.append(msg)

    def rep(sev, msg):
        (err if sev == "error" else warn)(msg)

    if not os.path.exists(path):
        sys.exit("No such file: %s" % path)

    z = zipfile.ZipFile(path)
    infos = z.infolist()
    names = z.namelist()
    name_set = set(names)

    # ---- 1. mimetype -----------------------------------------------------
    if not infos:
        err("archive is empty")
        return errors, warnings, notes
    for sev, msg in hard_rules.check_mimetype(
            infos[0].filename,
            infos[0].compress_type == zipfile.ZIP_STORED,
            z.read("mimetype") if infos[0].filename == "mimetype" else None):
        rep(sev, msg)

    # ---- 2. XML well-formedness -----------------------------------------
    parsed = {}
    for n in names:
        if n.endswith((".opf", ".ncx", ".xhtml", ".xml")):
            try:
                parsed[n] = ET.fromstring(z.read(n))
            except Exception as exc:
                err("XML parse failed: %s: %s" % (n, exc))

    # ---- 3. container.xml ------------------------------------------------
    container = parsed.get("META-INF/container.xml")
    opf_path = "OEBPS/content.opf"
    if container is None:
        err("missing META-INF/container.xml")
    else:
        rf = container.find(".//c:rootfile", NS)
        if rf is None:
            err("container.xml has no <rootfile>")
        else:
            opf_path = rf.get("full-path")

    pkg = parsed.get(opf_path)
    if pkg is None:
        err("missing or unparsable package document: %s" % opf_path)
        return errors, warnings, notes
    pkg_dir = os.path.dirname(opf_path)

    # ---- 4. metadata -----------------------------------------------------
    for tag in ("title", "language", "identifier"):
        if pkg.find(".//dc:" + tag, NS) is None:
            err("package metadata is missing dc:%s" % tag)
    if pkg.find(".//opf:meta[@property='dcterms:modified']", NS) is None:
        err("package metadata is missing dcterms:modified")

    # ---- 5. manifest / spine --------------------------------------------
    manifest = {}
    for it in pkg.findall(".//opf:manifest/opf:item", NS):
        manifest[it.get("id")] = {
            "href": it.get("href"),
            "type": it.get("media-type"),
            "props": (it.get("properties") or "").split(),
        }

    spine_ids, linear = [], {}
    for ref in pkg.findall(".//opf:spine/opf:itemref", NS):
        spine_ids.append(ref.get("idref"))
        linear[ref.get("idref")] = ref.get("linear", "yes")

    for sid in spine_ids:
        if sid not in manifest:
            err("spine references unknown manifest id: %s" % sid)

    def manifest_path(href):
        return os.path.normpath(os.path.join(pkg_dir, href)).replace("\\", "/")

    used_paths = set()
    for mid, item in manifest.items():
        p = manifest_path(item["href"])
        used_paths.add(p)
        if p not in name_set:
            err("manifest item '%s' points at a missing file: %s" % (mid, item["href"]))

    for n in names:
        if n.endswith((".jpg", ".jpeg", ".png", ".mp3", ".m4a", ".css", ".js")):
            if n not in used_paths:
                warn("packaged but not in the manifest: %s" % n)

    navs = [i for i, v in manifest.items() if "nav" in v["props"]]
    if not navs:
        err("no manifest item carries properties=\"nav\" "
            "(EPUB 3 requires a navigation document)")
    covers = [i for i, v in manifest.items() if "cover-image" in v["props"]]
    if len(covers) != 1:
        warn("expected exactly one properties=\"cover-image\" item, found %d"
             % len(covers))

    # ---- 6. GUARD: navigation document must not be in the spine ----------
    for sev, msg in hard_rules.check_spine(manifest, spine_ids, linear):
        rep(sev, msg)

    reading_order = [sid for sid in spine_ids if linear.get(sid) != "no"]
    notes.append("reading order: %d documents" % len(reading_order))
    if not reading_order:
        err("spine is empty")
        return errors, warnings, notes

    first = manifest.get(reading_order[0], {})
    if "cover" not in (first.get("href") or ""):
        warn("first spine item is %s; expected the cover page first"
             % first.get("href"))

    # ---- 7. per-page document checks ------------------------------------
    controller = False
    js_path = manifest_path("js/reader.js") if "js" in manifest else None
    if js_path and js_path in name_set:
        controller = True
    elif any((it.get("type") or "").startswith("audio/")
             for it in manifest.values()):
        # The controller is the ONLY start path (autoplay is banned): without
        # it every page of this book stays silent in every reader.
        err("no js/reader.js found, but the book packages audio - the "
            "controller is the only start path, so every page would stay "
            "silent")
    else:
        warn("no js/reader.js found (the book packages no audio either)")

    viewports, img_sizes = {}, {}
    page_facts_list = []
    for sid in reading_order:
        href = manifest.get(sid, {}).get("href")
        if not href:
            continue
        doc = manifest_path(href)
        if doc not in name_set:
            continue
        raw = z.read(doc).decode("utf-8")
        # GUARD 2 + 2b + GUARD 1 (per page): facts are extracted here, the
        # predicates run below - hard_rules holds the single encoding, and
        # build_epub.py runs the very same predicates on its own output.
        f = hard_rules.page_facts(os.path.basename(doc), raw)
        page_facts_list.append(f)
        if f["viewport"] is not None:
            viewports[doc] = f["viewport"]

    for f in page_facts_list:
        for sev, msg in hard_rules.check_page(f, controller):
            rep(sev, msg)

    if viewports:
        violations = hard_rules.check_viewports(viewports)
        if violations:
            for sev, msg in violations:
                rep(sev, msg)
        else:
            notes.append("shared viewport: %s" % list(set(viewports.values()))[0])

    # GUARD 1b: one shared image size
    for n in names:
        if n.startswith(pkg_dir) and n.lower().endswith(".jpg"):
            sz = jpeg_size(z.read(n))
            if sz:
                img_sizes[n] = sz
    if img_sizes:
        violations = hard_rules.check_image_sizes(img_sizes)
        if violations:
            for sev, msg in violations:
                rep(sev, msg)
        else:
            uniq = set(img_sizes.values())
            notes.append("shared image size: %dx%d across %d images"
                         % (list(uniq)[0][0], list(uniq)[0][1], len(img_sizes)))

    # ---- 8. properties="scripted" only on content documents (epubcheck OPF-012) --
    for sev, msg in hard_rules.check_scripted_props(manifest):
        rep(sev, msg)

    # ---- 9. navigation targets resolve -----------------------------------
    for nav_id in navs:
        doc = manifest_path(manifest[nav_id]["href"])
        if doc not in name_set:
            continue
        root = parsed.get(doc)
        if root is None:
            continue
        links = [a.get("href") for a in root.findall(".//x:a", NS)]
        links = [h for h in links if h and not h.startswith(("http", "mailto", "#"))]
        for h in links:
            target = os.path.normpath(
                os.path.join(os.path.dirname(doc), h)).replace("\\", "/")
            if target not in name_set:
                err("navigation link points at a missing file: %s" % h)
        notes.append("navigation document lists %d links" % len(links))

    for n in names:
        if n.endswith(".ncx"):
            pts = parsed.get(n)
            if pts is not None:
                notes.append("NCX entries: %d"
                             % len(pts.findall(".//ncx:navPoint", NS)))

    # ---- 10. audio really present in the archive -------------------------
    audio_files = [n for n in names if n.lower().endswith(hard_rules.AUDIO_EXT)]
    if not audio_files:
        warn("no audio files are packaged")
    else:
        notes.append("packaged audio files: %d" % len(audio_files))

    # ---- 11. a cover that duplicates a story page ------------------------
    # Not a spec violation, but almost always an accident: a folder with no cover
    # asset makes the builder reuse a page (or `--cover` asks it to), so the same
    # recording ships twice - and on a device you can no longer tell which one you
    # heard, which is exactly what makes a playback bug hard to diagnose.
    dupes = {}
    for n in names:
        base = os.path.basename(n)
        # NB: the extension pattern must allow digits - `.mp3` has one, and an
        # `[a-z]+$` here silently skipped every audio file, which is exactly the
        # guard's whole point. Found by feeding it a deliberately duplicated book.
        if not (base.startswith("cover.") or re.match(r"^p\d+\.[a-z0-9]+$", base)):
            continue
        try:
            dupes.setdefault(hashlib.md5(z.read(n)).digest(), []).append(base)
        except Exception:
            continue
    for group in dupes.values():
        if len(group) > 1:
            warn("cover and page assets are byte-identical (%s) - the same "
                 "recording/image ships twice. Give the folder its own "
                 "cover.png + cover.mp3, or expect the cover to look and sound "
                 "exactly like that page." % ", ".join(sorted(group)))

    return errors, warnings, notes


def main(argv):
    if len(argv) != 2:
        sys.exit(__doc__.strip().splitlines()[-1].strip())
    path = argv[1]
    errors, warnings, notes = validate(path)
    print("Validating %s" % path)
    print("  size: %.2f MB" % (os.path.getsize(path) / 1048576.0))
    for n in notes:
        print("  - %s" % n)
    print()
    for w in warnings:
        print("[warning] %s" % w)
    for e in errors:
        print("[ERROR] %s" % e)
    if errors:
        print("\nFAILED: %d error(s), %d warning(s)" % (len(errors), len(warnings)))
        return 1
    print("PASSED: 0 errors, %d warning(s)" % len(warnings))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
