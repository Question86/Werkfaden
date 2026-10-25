from __future__ import annotations


def mask_markdown_code(text: str) -> str:
    """Mask fenced and inline code while preserving byte/character offsets and newlines.

    KAIROS documentation legitimately contains literal examples of its own reference syntax and
    anchored headings. Those examples are documentation, not live references or sections. Structural
    scanners therefore operate on this mask, while indexed section bodies retain the original text.
    """
    chars = list(text)
    length = len(chars)
    i = 0
    fenced = False
    fence_char = ""
    fence_len = 0

    while i < length:
        if not fenced and chars[i] in {"`", "~"}:
            ch = chars[i]
            j = i
            while j < length and chars[j] == ch:
                j += 1
            run = j - i
            if run >= 3 and (i == 0 or chars[i - 1] == "\n"):
                fenced = True
                fence_char = ch
                fence_len = run
                for k in range(i, j):
                    chars[k] = " "
                i = j
                continue
            if ch == "`":
                # Inline code: mask from opening run through the next identical run on the same line.
                line_end = text.find("\n", j)
                if line_end < 0:
                    line_end = length
                closing = text.find(ch * run, j, line_end)
                if closing >= 0:
                    for k in range(i, closing + run):
                        if chars[k] != "\n":
                            chars[k] = " "
                    i = closing + run
                    continue
        elif fenced and chars[i] == fence_char and (i == 0 or chars[i - 1] == "\n"):
            j = i
            while j < length and chars[j] == fence_char:
                j += 1
            if j - i >= fence_len:
                for k in range(i, j):
                    chars[k] = " "
                fenced = False
                fence_char = ""
                fence_len = 0
                i = j
                continue

        if fenced and chars[i] != "\n":
            chars[i] = " "
        i += 1

    return "".join(chars)
