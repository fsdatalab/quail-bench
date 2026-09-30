"""The exact prompt text a predicate asks, for filters, joins, and labels.

The reference labels answer this text. An engine that runs QUAIL-B
sends the same text, so the text is defined here and not borrowed
from any engine. Every document prefix starts with `SHARED_PRE`; the
question comes after the document so the document's prefix does not
depend on the query.
"""

from __future__ import annotations

import re

PROMPT_FORMAT = "raw-v1"
SHARED_PRE = "DOCUMENT:\n"

# Fixed strings for join prompt layout.
JOIN_DOC_LABEL = "\n\nDOCUMENT {}:\n"      # each partner block
JOIN_ANCHOR_NOTE = "\n\n(The document above is DOCUMENT {}.)"
JOIN_QUESTION_SEP = "\n\n"                 # anchor note -> question
TASK_INSTRUCTION = "Evaluate TRUE or FALSE for the following question: "
ANSWER_CUE = "\nANSWER:"


def _marker(placeholder: int) -> str:
    return "{%d}" % placeholder


def _check_placeholders(template: str, n_args: int) -> None:
    slots = [int(m) for m in re.findall(r"\{(\d+)\}", template)]
    if sorted(set(slots)) != list(range(n_args)):
        raise ValueError(
            f"template placeholders {sorted(set(slots))} do not match "
            f"{n_args} document(s): expected {{0}}..{{{n_args - 1}}} "
            "each used at least once")


def split_template(template: str) -> tuple[str, str]:
    """Split into (pre-placeholder text, placeholder-onward text)."""
    i = template.find("{")
    if i < 0:
        return template, ""
    return template[:i], template[i:]


def split_frame(template: str) -> tuple[str, str]:
    """Split into (frame, canonical_template).

    Relocates text before the first placeholder to after it, so the
    document comes first and its prefix stays query-independent.
    """
    user_pre, tail = split_template(template)
    if not tail:
        return "", template
    m = re.match(r"\{\d+\}", tail)
    if m is None:
        raise ValueError(
            "template text before the first placeholder must not contain "
            f"a brace that is not a placeholder: {tail[:40]!r}")
    frame = user_pre.strip()
    rest = tail[m.end():]
    return frame, (SHARED_PRE + m.group(0)
                   + (f"\n\n{frame}" if frame else "") + rest)


def render_filter_question(tail: str) -> str:
    """Wrap a filter question with the task instruction and answer cue."""
    content = tail.lstrip("\n")
    sep = tail[:len(tail) - len(content)]
    if not sep:
        sep = "\n\n"
    return sep + TASK_INSTRUCTION + content + ANSWER_CUE


def render_filter_prompt(template: str, document: str) -> str:
    """Return the complete text of one filter question over one document."""
    _check_placeholders(template, 1)
    _frame, canonical = split_frame(template)
    preamble, tail = split_template(canonical)
    m = re.match(r"(\{\d+\})(.*)", tail, re.DOTALL)
    if m is None or not tail.startswith("{0}"):
        raise ValueError(f"unexpected filter template layout: {template!r}")
    return preamble + document + render_filter_question(m.group(2))


CLASSIFY_INSTRUCTION = ("Answer with exactly one of the categories below "
                        "for the following question: ")
CATEGORIES_HEADER = "\n\nCategories:"
# Labels follow the answer cue after one space, as a word would.
LABEL_PREFIX = " "


def render_categories(labels, descriptions=None) -> str:
    """Return the category list, one `- label` or `- label: description` line each."""
    descriptions = descriptions or ("",) * len(labels)
    if len(descriptions) != len(labels):
        raise ValueError("each label needs one description, empty for none")
    return CATEGORIES_HEADER + "".join(
        f"\n- {label}" + (f": {description}" if description else "")
        for label, description in zip(labels, descriptions))


def render_classify_prompt(template: str, document: str, labels,
                           descriptions=None, partner: str | None = None) -> str:
    """Return the text a classification scores its labels after.

    The document comes first, then the instruction and question, the
    category list, and the answer cue. A label's text is
    `LABEL_PREFIX + label`, appended after this text.

    With a partner, the classification labels a joined row and the
    prompt has the join's layout: the anchor document with its anchor
    note, then the labeled partner document, then the instruction,
    the question with `{0}` and `{1}` kept as written, the category
    list, and the answer cue.

    Args:
        template: The question with `{0}`, or `{0}` and `{1}` for a
            joined row.
        document: The text of document `{0}`, the anchor of a joined row.
        labels: The categories, in the order that breaks ties.
        descriptions: One description per label, or None for none.
        partner: The text of document `{1}` for a joined row, else None.
    """
    if partner is not None:
        _check_placeholders(template, 2)
        return (SHARED_PRE + document + JOIN_ANCHOR_NOTE.format(_marker(0))
                + join_label(1) + partner + "\n\n" + CLASSIFY_INSTRUCTION
                + template + render_categories(labels, descriptions)
                + ANSWER_CUE)
    _check_placeholders(template, 1)
    _frame, canonical = split_frame(template)
    preamble, tail = split_template(canonical)
    m = re.match(r"(\{\d+\})(.*)", tail, re.DOTALL)
    if m is None or not tail.startswith("{0}"):
        raise ValueError(f"unexpected classify template layout: {template!r}")
    content = m.group(2).lstrip("\n")
    sep = m.group(2)[:len(m.group(2)) - len(content)] or "\n\n"
    return (preamble + document + sep + CLASSIFY_INSTRUCTION + content
            + render_categories(labels, descriptions) + ANSWER_CUE)


def join_label(placeholder: int) -> str:
    return JOIN_DOC_LABEL.format(_marker(placeholder))


def render_join_frame(template: str, placeholder: int) -> str:
    """The anchor note and question written once after the anchor document."""
    return (JOIN_ANCHOR_NOTE.format(_marker(placeholder))
            + JOIN_QUESTION_SEP + TASK_INSTRUCTION + template)


def render_join_prompt(template: str, documents, anchor: int = 0) -> str:
    """Return the complete text of one join question over one tuple.

    Args:
        template: Join template with one placeholder per document.
        documents: The document texts in placeholder order.
        anchor: The placeholder whose document comes first.
    """
    documents = tuple(documents)
    _check_placeholders(template, len(documents))
    if len(documents) < 2:
        raise ValueError("a join prompt needs at least two documents")
    if anchor < 0 or anchor >= len(documents):
        raise ValueError(f"join anchor placeholder {anchor} is out of range")
    out = SHARED_PRE + documents[anchor] + render_join_frame(template, anchor)
    for i, document in enumerate(documents):
        if i != anchor:
            out += join_label(i) + document
    return out + ANSWER_CUE


def true_false_ids(tok):
    """The token ids that mean TRUE and FALSE.

    Args:
        tok: A HuggingFace tokenizer; called with add_special_tokens=False.
    """
    true, false = set(), set()
    for w in ("TRUE", " TRUE", "True", " True"):
        ids = tok(w, add_special_tokens=False)["input_ids"]
        if ids:
            true.add(ids[0])
    for w in ("FALSE", " FALSE", "False", " False"):
        ids = tok(w, add_special_tokens=False)["input_ids"]
        if ids:
            false.add(ids[0])
    return true, false
