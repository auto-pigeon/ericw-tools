---
title: Color output is off when stdout is not a terminal
author: Andrea-dintino
date: 2026-10-30
standing: true
id: l-12514f84
scope: repo
status: active
src: init-review
touches:
  - common/log.cc
---

## Decided
- Colors showed up as raw escape codes in TrenchBroom and in piped output. They are now printed only when stdout is a TTY, so no -nocolor switch is needed.
- In the code: See is_terminal() in common/log.cc: `_isatty(_fileno(stdout))` on _WIN32, `isatty(STDOUT_FILENO)` otherwise. Not _WIN32 is assumed to be POSIX, as in cmdlib.cc. Keep #ifdefs un-indented per clang-format.

_From PR #478 here, Jan 10._
