"""A small checker for the PowerShell scripts under packaging/ (used by tests/test_packaging.py).

PowerShell cannot be run where GridSim is developed (Linux); the scripts only ever run on GitHub's Windows machines,
where one syntax error stops a whole script before its first line. This reads a script the way PowerShell's parser
does for the things that go wrong most easily, and returns a list of "line N: what is wrong":

  * strings that are never closed, and brackets ( ) { } [ ] that do not pair up (also inside "$( ... )" in strings);
  * "$name:" in a double-quoted string or in code, where PowerShell takes "name:" for a scope or drive and then
    stops with "variable reference is not valid" - write "${name}:" instead;
  * characters outside plain ASCII (Windows PowerShell 5.1, which runs stop-gridsim.ps1, reads a file without a
    byte-order mark as ANSI);
  * here-strings, strings that run over a line end and backtick line continuations (not used in these scripts on
    purpose: they break silently on a stray space);
  * PowerShell's own read-only variables ($PID, $Host, $Error, ...) used as a parameter, a loop variable or the
    target of an assignment, which fails at run time;
  * the short aliases (%, ?, select, where, sort, echo, ls, cat, rm, cp, mv, ...), which are not used in these
    scripts so every command reads the same everywhere;
  * the exit code of a process read on the very line that says the process is still running
    ("-not $p.HasExited ... $p.ExitCode"): PowerShell works out every argument before the call, and .ExitCode of
    a running process is an error, so a check written like that fails exactly when everything is fine.

It is not a PowerShell parser: a script that passes can still be wrong. It catches the mistakes that would cost a
whole round trip through the build machine.
"""
from __future__ import annotations

import re

SCOPES = {"env", "script", "global", "local", "private", "using", "function", "variable", "alias"}
READ_ONLY = ("pid", "host", "error", "home", "pshome", "true", "false", "args", "input", "psscriptroot",
             "pscommandpath", "myinvocation", "executioncontext", "psversiontable", "shellid", "stacktrace", "psitem")
PAIRS = {")": "(", "}": "{", "]": "["}
VAR_START = re.compile(r"[A-Za-z_]")
VAR_BODY = re.compile(r"[A-Za-z0-9_]*")
_RO = "|".join(READ_ONLY)
ASSIGNED = re.compile(r"\s*(=|\+=|-=)(?!=)")
DECLARED = re.compile(r"(?:\[[\w.\[\]]+\]\s*|foreach\s*\(\s*)\$(" + _RO + r")\b(?![.:\w])", re.I)
ALIAS = re.compile(r"(?:^|[|;{(]|\s)\s*(%|\?|select|where|sort|echo|ls|cat|rm|cp|mv|gci|gc|iex|iwr|curl|wget|kill|"
                   r"sleep|del|dir|copy|move|ren|measure|group|tee|write|ft|fl)\s+[{\-$\w(]", re.I)
ALIAS_AFTER_PIPE = re.compile(r"\|\s*(foreach|select|where|sort|group|measure|tee|ft|fl)\b(?!-)", re.I)
EXIT_CODE_WHILE_RUNNING = re.compile(r"-not\s+\$(\w+)\.HasExited\b[^\n{]*\$\1\.ExitCode\b", re.I)


class _Lexer:
    def __init__(self, text: str):
        self.t = text
        self.n = len(text)
        self.problems: list[str] = []
        self.code_chars: list[str] = []          # the script with strings and comments blanked (alias check)

    def line(self, i: int) -> int:
        return self.t.count("\n", 0, i) + 1

    def bad(self, i: int, what: str) -> None:
        self.problems.append(f"line {self.line(i)}: {what}")

    def variable(self, i: int) -> int:
        """t[i] is '$'. Checks "$name:" and returns the index after the variable's name (members are not read)."""
        j = i + 1
        if j < self.n and self.t[j] == "{":                       # ${any name, with (brackets) or spaces}
            k = self.t.find("}", j)
            if k < 0 or "\n" in self.t[j:k]:
                self.bad(i, "'${' without a closing '}' on the same line")
                return j + 1
            return k + 1
        if j < self.n and VAR_START.match(self.t[j]):
            k = VAR_BODY.match(self.t, j).end()
            name = self.t[j:k]
            if k < self.n and self.t[k] == ":" and self.t[k + 1:k + 2] != ":":
                after = self.t[k + 1:k + 2]
                if name.lower() not in SCOPES:
                    self.bad(i, f"'${name}:' - PowerShell reads '{name}:' as a scope or drive; write '${{{name}}}:'")
                elif VAR_START.match(after):
                    k = VAR_BODY.match(self.t, k + 1).end()
                elif after != "{":
                    self.bad(i, f"'${name}:' is not followed by a variable name")
            return k
        return j

    def double_quoted(self, i: int) -> int:
        """t[i] is the opening '"'. Returns the index after the closing one."""
        start = i
        i += 1
        while i < self.n:
            c = self.t[i]
            if c == "`":
                i += 2
            elif c == '"':
                if self.t[i + 1:i + 2] == '"':
                    i += 2
                else:
                    return i + 1
            elif c == "$" and self.t[i + 1:i + 2] == "(":
                i = self.code(i + 2, closer=")", opened_at=i)
            elif c == "$":
                i = self.variable(i)
            else:
                if c == "\n":
                    self.bad(start, "a double-quoted string runs over the end of the line (not closed?)")
                i += 1
        self.bad(start, "a double-quoted string is never closed")
        return self.n

    def single_quoted(self, i: int) -> int:
        start = i
        i += 1
        while i < self.n:
            c = self.t[i]
            if c == "'":
                if self.t[i + 1:i + 2] == "'":
                    i += 2
                    continue
                return i + 1
            if c == "\n":
                self.bad(start, "a single-quoted string runs over the end of the line (not closed?)")
            i += 1
        self.bad(start, "a single-quoted string is never closed")
        return self.n

    def code(self, i: int, closer: str | None = None, opened_at: int = 0) -> int:
        """Reads code from i. With closer=')': until the ')' that ends a "$( ... )" inside a string."""
        stack: list[tuple[str, int]] = []
        top = closer is None
        while i < self.n:
            c = self.t[i]
            prev = self.t[i - 1] if i > 0 else "\n"
            if self.t.startswith("<#", i):
                k = self.t.find("#>", i + 2)
                if k < 0:
                    self.bad(i, "a block comment '<#' is never closed")
                    return self.n
                i = k + 2
            elif c == "#" and (prev.isspace() or prev in "({[;,|"):
                k = self.t.find("\n", i)
                i = self.n if k < 0 else k
            elif c == "@" and self.t[i + 1:i + 2] in ("'", '"'):
                self.bad(i, "a here-string (@' or @\") - not used in these scripts")
                i += 1
            elif c == '"':
                i = self.double_quoted(i)
                if top:
                    self.code_chars.append(" S ")
            elif c == "'":
                i = self.single_quoted(i)
                if top:
                    self.code_chars.append(" S ")
            elif c == "`":
                if self.t[i + 1:].split("\n", 1)[0].strip() == "":
                    self.bad(i, "a backtick line continuation - not used in these scripts")
                i += 2
            elif c == "$":
                k = self.variable(i)
                name = self.t[i + 1:k]
                if name.lower() in READ_ONLY and ASSIGNED.match(self.t, k):
                    self.bad(i, f"assignment to PowerShell's own variable ${name}")
                if name.lower() in SCOPES:
                    self.bad(i, f"a variable named ${name} (that word is a scope name: pick another)")
                if top:
                    self.code_chars.append(self.t[i:k])
                i = k
            else:
                if c in "({[":
                    stack.append((c, i))
                elif c in ")}]":
                    if not stack:
                        if closer is not None and c == closer:
                            return i + 1
                        self.bad(i, f"'{c}' without an opening bracket")
                    else:
                        if stack[-1][0] != PAIRS[c]:
                            self.bad(i, f"'{c}' closes '{stack[-1][0]}' opened on line {self.line(stack[-1][1])}")
                        stack.pop()
                if top:
                    self.code_chars.append(c)
                i += 1
        for c, at in stack:
            self.bad(at, f"'{c}' is never closed")
        if closer is not None:
            self.bad(opened_at, "'$(' in a string is never closed")
        return self.n


def problems(text: str) -> list[str]:
    """Every problem found in one PowerShell script, as "line N: ..." (an empty list when none)."""
    lx = _Lexer(text)
    for m in re.finditer(r"[^\x09\x0a\x0d\x20-\x7e]", text):
        lx.bad(m.start(), f"a character outside plain ASCII ({text[m.start()]!r})")
    lx.code(0)
    for n, line in enumerate(text.split("\n"), 1):
        m = EXIT_CODE_WHILE_RUNNING.search(line)
        if m:
            lx.problems.append(f"line {n}: ${m.group(1)}.ExitCode is read where ${m.group(1)} is expected to be still "
                               "running (an error for a running process)")
    for n, line in enumerate("".join(lx.code_chars).split("\n"), 1):
        m = ALIAS.search(line) or ALIAS_AFTER_PIPE.search(line)
        if m:
            lx.problems.append(f"line {n}: the alias '{m.group(1)}' (write the full command name)")
        m = DECLARED.search(line)
        if m:
            lx.problems.append(f"line {n}: PowerShell's own variable ${m.group(1)} used as a parameter or loop variable")
    return lx.problems
