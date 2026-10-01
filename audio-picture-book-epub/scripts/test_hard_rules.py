#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Direct tests for the predicates in hard_rules.py.

The interface is the test surface: these feed synthetic page HTML and synthetic
facts to the same functions build_epub.py and check_epub.py call, so a rule
change is exercised in milliseconds instead of by assembling a whole book.

Run:  python test_hard_rules.py
"""

from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from hard_rules import (  # noqa: E402
    SILENT_ATTR,
    check_image_sizes,
    check_mimetype,
    check_page,
    check_scripted_props,
    check_spine,
    check_viewports,
    page_facts,
)


def page_html(viewport=True, audio=True, marker=False, key="p01"):
    """A synthetic page shaped like PAGE_TPL renders it."""
    head = ('<head><meta charset="utf-8"/>'
            + ('<meta name="viewport" content="width=940, height=1672"/>'
               if viewport else '')
            + '</head>')
    body_attr = ' ' + SILENT_ATTR if marker else ''
    audio_tag = (
        '<audio id="pgAudio-%s" class="pg-audio" src="../audio/%s.mp3" '
        'preload="auto" controls="controls"></audio>'
        % (key, key)
    ) if audio else ''
    btn = ('<button type="button" id="pgBtn-%s" class="pg-btn"></button>'
           % key) if audio else ''
    return (
        '<?xml version="1.0" encoding="utf-8"?>'
        '<html xmlns="http://www.w3.org/1999/xhtml">%s<body%s>'
        '<div class="stage" id="stage-%s">'
        '<img class="pg-img" src="../images/%s.jpg" alt="page"/>'
        '%s%s</div>'
        '<script type="text/javascript" src="../js/reader.js" data-page="%s">'
        '</script></body></html>' % (head, body_attr, key, key, audio_tag, btn, key)
    )


def errors(violations):
    return [m for sev, m in violations if sev == "error"]


def warnings(violations):
    return [m for sev, m in violations if sev == "warning"]


class PageFacts(unittest.TestCase):
    def test_counts_and_extraction(self):
        f = page_facts("p01.xhtml", page_html())
        self.assertEqual(f["audio_count"], 1)
        self.assertEqual(f["autoplay_count"], 0)
        self.assertEqual(f["audio_src"], "../audio/p01.mp3")
        self.assertEqual(f["src_ref_count"], 1)
        self.assertEqual(f["data_page"], "p01")
        self.assertEqual(f["viewport"], "width=940, height=1672")
        self.assertIn("pgAudio-p01", f["ids"])
        self.assertIn("pgBtn-p01", f["ids"])
        self.assertFalse(f["silent_marker"])
        self.assertFalse(f["script_before_body"])

    def test_comments_are_inert(self):
        raw = page_html().replace("<body", "<!-- <audio></audio> --><body")
        f = page_facts("p01.xhtml", raw)
        self.assertEqual(f["audio_count"], 1)  # the real one, not the comment


class CheckPage(unittest.TestCase):
    def test_good_page_passes(self):
        f = page_facts("p01.xhtml", page_html())
        self.assertEqual(check_page(f, has_controller=True), [])

    def test_two_audio_elements(self):
        raw = page_html().replace("</div>", "</div><audio src=\"../audio/x.mp3\"></audio>", 1)
        v = check_page(page_facts("p01.xhtml", raw), True)
        self.assertTrue(any("2 <audio> elements" in m for m in errors(v)))

    def test_zero_audio_without_marker(self):
        v = check_page(page_facts("p01.xhtml", page_html(audio=False)), True)
        self.assertTrue(any("0 <audio> elements" in m for m in errors(v)))
        self.assertTrue(any(SILENT_ATTR in m for m in errors(v)))  # tells you the fix

    def test_silent_page_with_marker_passes(self):
        f = page_facts("p01.xhtml", page_html(audio=False, marker=True))
        self.assertEqual(check_page(f, has_controller=True), [])

    def test_marker_with_audio_is_a_violation(self):
        raw = page_html(marker=True)  # marker AND audio
        v = check_page(page_facts("p01.xhtml", raw), True)
        self.assertTrue(any(SILENT_ATTR in m for m in errors(v)))

    def test_stray_autoplay_is_a_violation(self):
        # a quoted autoplay attribute anywhere - even on a silent page
        raw = page_html(audio=False, marker=True).replace(
            "</div>", '</div><video autoplay="autoplay"></video>', 1)
        v = check_page(page_facts("p01.xhtml", raw), True)
        self.assertTrue(any("autoplay attribute(s); none are allowed" in m
                            for m in errors(v)))

    def test_label_text_cannot_inflate_autoplay_count(self):
        # the label is echoed into <title>/alt=; bare `autoplay=2` text there
        # must not count as an attribute
        raw = page_html().replace('alt="page"', 'alt="autoplay=2 page"')
        f = page_facts("p01.xhtml", raw)
        self.assertEqual(f["autoplay_count"], 0)
        self.assertEqual(check_page(f, has_controller=True), [])

    def test_noscript(self):
        raw = page_html().replace("</div>", "</div><noscript><audio></audio></noscript>", 1)
        v = check_page(page_facts("p01.xhtml", raw), True)
        self.assertTrue(any("<noscript>" in m for m in errors(v)))

    def test_autoplay_attribute_is_a_violation(self):
        raw = page_html().replace('preload="auto"',
                                  'preload="auto" autoplay="autoplay"', 1)
        v = check_page(page_facts("p01.xhtml", raw), True)
        self.assertTrue(any("autoplay attribute(s); none are allowed" in m
                            for m in errors(v)))

    def test_boolean_autoplay_is_a_violation(self):
        raw = page_html().replace('preload="auto"',
                                  'preload="auto" autoplay', 1)
        v = check_page(page_facts("p01.xhtml", raw), True)
        self.assertTrue(any("autoplay attribute(s); none are allowed" in m
                            for m in errors(v)))

    def test_single_quoted_autoplay_is_a_violation(self):
        raw = page_html().replace('preload="auto"',
                                  "preload=\"auto\" autoplay='autoplay'", 1)
        v = check_page(page_facts("p01.xhtml", raw), True)
        self.assertTrue(any("autoplay attribute(s); none are allowed" in m
                            for m in errors(v)))

    def test_same_url_referenced_twice(self):
        raw = page_html().replace("</div>", '<a href="../audio/p01.mp3">again</a>', 1)
        v = check_page(page_facts("p01.xhtml", raw), True)
        self.assertTrue(any("references ../audio/p01.mp3 2 times" in m
                            for m in errors(v)))

    def test_audio_src_outside_audio_dir(self):
        raw = page_html().replace("../audio/p01.mp3", "../images/p01.jpg")
        v = check_page(page_facts("p01.xhtml", raw), True)
        self.assertTrue(any("does not point into audio/" in m for m in errors(v)))

    def test_script_in_head(self):
        raw = page_html().replace("<head>", "<head><script src=\"../js/reader.js\"></script>", 1)
        v = check_page(page_facts("p01.xhtml", raw), True)
        self.assertTrue(any("loads its script in <head>" in m for m in errors(v)))

    def test_bare_id(self):
        raw = page_html().replace('id="stage-p01"', 'id="stage"')
        v = check_page(page_facts("p01.xhtml", raw), True)
        self.assertTrue(any("unscoped id=\"stage\"" in m for m in errors(v)))

    def test_missing_data_page(self):
        raw = page_html().replace(' data-page="p01"', "")
        v = check_page(page_facts("p01.xhtml", raw), True)
        self.assertTrue(any("carries no data-page" in m for m in errors(v)))

    def test_data_page_mismatch(self):
        # script names p02 but the ids on the page are p01's
        raw = page_html().replace('data-page="p01"', 'data-page="p02"')
        v = check_page(page_facts("p01.xhtml", raw), True)
        self.assertTrue(any('missing id="pgAudio-p02"' in m for m in errors(v)))
        self.assertTrue(any('missing id="pgBtn-p02"' in m for m in errors(v)))

    def test_no_data_page_check_without_controller(self):
        f = page_facts("p01.xhtml", page_html().replace(' data-page="p01"', ""))
        self.assertEqual(errors(check_page(f, has_controller=False)), [])

    def test_missing_viewport_is_a_warning(self):
        v = check_page(page_facts("p01.xhtml", page_html(viewport=False)), True)
        self.assertEqual(errors(v), [])
        self.assertTrue(any("no <meta name=\"viewport\">" in m for m in warnings(v)))


class Aggregates(unittest.TestCase):
    def test_empty_input_is_not_a_violation(self):
        self.assertEqual(check_viewports({}), [])
        self.assertEqual(check_image_sizes({}), [])

    def test_one_viewport(self):
        self.assertEqual(check_viewports({"a.xhtml": "w=1", "b.xhtml": "w=1"}), [])

    def test_distinct_viewports(self):
        v = check_viewports({"a.xhtml": "w=1", "b.xhtml": "w=2"})
        self.assertTrue(any("do not share one viewport (2 distinct" in m
                            for m in errors(v)))
        self.assertEqual(len(errors(v)), 3)  # header + one line per document

    def test_one_image_size(self):
        self.assertEqual(check_image_sizes({"a.jpg": (1, 2), "b.jpg": (1, 2)}), [])

    def test_distinct_image_sizes(self):
        v = check_image_sizes({"a.jpg": (1, 2), "b.jpg": (3, 4)})
        self.assertTrue(any("do not all share one pixel size" in m
                            for m in errors(v)))
        self.assertTrue(any("a.jpg -> 1x2" in m for m in errors(v)))


class SpineAndProps(unittest.TestCase):
    MANIFEST = {
        "nav": {"href": "nav.xhtml", "props": ["nav"], "type": "application/xhtml+xml"},
        "pg01": {"href": "xhtml/p01.xhtml", "props": [], "type": "application/xhtml+xml"},
    }

    def test_clean_spine(self):
        self.assertEqual(
            check_spine(self.MANIFEST, ["pg01"], {"pg01": "yes"}), [])

    def test_linear_no_in_spine(self):
        v = check_spine(self.MANIFEST, ["pg01"], {"pg01": "no"})
        self.assertTrue(any('linear="no"' in m for m in errors(v)))

    def test_unknown_spine_id_falls_back_to_question_mark(self):
        v = check_spine(self.MANIFEST, ["ghost"], {"ghost": "no"})
        self.assertTrue(any("'ghost' (?)" in m for m in errors(v)))

    def test_nav_in_spine(self):
        v = check_spine(self.MANIFEST, ["nav"], {"nav": "yes"})
        self.assertTrue(any("navigation document 'nav' is inside the spine" in m
                            for m in errors(v)))

    def test_scripted_on_xhtml_ok(self):
        m = {"pg01": {"href": "xhtml/p01.xhtml", "props": ["scripted"],
                      "type": "application/xhtml+xml"}}
        self.assertEqual(check_scripted_props(m), [])

    def test_scripted_on_js_is_opf012(self):
        m = {"js": {"href": "js/reader.js", "props": ["scripted"],
                    "type": "text/javascript"}}
        v = check_scripted_props(m)
        self.assertTrue(any("OPF-012" in msg for msg in errors(v)))


class Mimetype(unittest.TestCase):
    OK = b"application/epub+zip"

    def test_ok(self):
        self.assertEqual(check_mimetype("mimetype", True, self.OK), [])

    def test_empty_archive(self):
        v = check_mimetype(None, True, None)
        self.assertTrue(any("archive is empty" in m for m in errors(v)))

    def test_wrong_first_entry(self):
        v = check_mimetype("OEBPS/", True, self.OK)
        self.assertTrue(any("must be 'mimetype'" in m for m in errors(v)))

    def test_compressed(self):
        v = check_mimetype("mimetype", False, self.OK)
        self.assertTrue(any("must be STORED" in m for m in errors(v)))

    def test_wrong_content(self):
        v = check_mimetype("mimetype", True, b"text/plain")
        self.assertTrue(any("'mimetype' content is" in m for m in errors(v)))


if __name__ == "__main__":
    unittest.main(verbosity=2)
