---
name: cross-platform-shell
description: Write commands and scripts that actually run on Windows. Use when working on a Windows machine, when a command fails with "is not recognized as the name of a cmdlet", when a bash one-liner needs a PowerShell equivalent, when paths break on backslashes or spaces, or when making a repo's scripts work on Windows, macOS, and Linux at once.
license: MIT
compatibility: Targets Windows 10 and 11 with Windows PowerShell 5.1 or PowerShell 7+. Notes where Git Bash and WSL behave differently. The POSIX halves of each comparison assume bash and coreutils.
metadata:
  author: software-factory
  version: "1.0"
allowed-tools: Bash(*)
---

# Cross-platform shell

Most agent skills are written by people on macOS and assume bash, coreutils,
and forward slashes. On Windows those assumptions fail quietly or loudly, and
the loud ones are easier. This covers the quiet ones.

## Pick the shell on purpose

Three shells are usually available and they are not interchangeable.

| Shell | Use it for | Avoid it for |
|---|---|---|
| PowerShell | Windows services, registry, `.ps1`, anything Windows-native | POSIX one-liners copied from docs |
| Git Bash | `.sh` scripts, POSIX pipelines, git hooks | Windows-native paths, long-running servers |
| WSL | Full Linux toolchain, Docker, make | Touching Windows files across `/mnt/c` in hot loops |

Decide before writing the command, and say which one you assumed. A script that
works in Git Bash and fails in PowerShell is not broken, it is undocumented.

## PowerShell 5.1 traps

Windows 10 and 11 ship 5.1 by default. These are the ones that bite.

- `&&` and `||` do not exist. They are a parser error, not a runtime failure.
  Use `A; if ($?) { B }`.
- No ternary `?:`, no `??`, no `?.`. Those are PowerShell 7+.
- `2>&1` on a native `.exe` wraps each stderr line in an ErrorRecord and sets
  `$?` to false even on exit code 0. Do not redirect stderr from native tools.
- `Set-Content` and `Add-Content` default to the system ANSI codepage. Pass
  `-Encoding utf8` whenever another tool will read the file.
- `ConvertFrom-Json` returns a PSCustomObject. `-AsHashtable` is 7+ only.
- `curl` and `wget` are aliases for `Invoke-WebRequest`, which takes completely
  different flags. Call `curl.exe` explicitly if you mean real curl.

## Command translations

| POSIX | PowerShell |
|---|---|
| `head -n 20 f` | `Get-Content f -TotalCount 20` |
| `tail -n 20 f` | `Get-Content f -Tail 20` |
| `which x` | `(Get-Command x).Source` |
| `wc -l f` | `(Get-Content f \| Measure-Object -Line).Lines` |
| `mkdir -p d` | `New-Item -ItemType Directory -Force d` |
| `rm -rf d` | `Remove-Item -Recurse -Force d` |
| `touch f` | `if (-not (Test-Path f)) { New-Item -ItemType File f }` |
| `ln -s t l` | `New-Item -ItemType SymbolicLink -Path l -Target t` |
| `export V=x` | `$env:V = 'x'` |
| `V=x cmd` | `$env:V = 'x'; cmd` |
| `2>/dev/null` | `2>$null` |
| `lsof -i :3000` | `netstat -ano \| findstr :3000` |
| `kill -9 PID` | `Stop-Process -Id PID -Force` |
| `grep -r p .` | `Select-String -Pattern p -Path . -Recurse` |

`touch` has a trap: `New-Item -Force` on an existing file truncates it. Guard
with `Test-Path` as shown, or you will silently empty a file you meant to
timestamp.

## Paths

- Backslashes are escape characters in bash. In Git Bash, use `/c/Users/...`,
  not `C:\Users\...`.
- Quote every path. Windows user directories contain spaces far more often than
  Unix ones, and `C:\Program Files` breaks unquoted arguments.
- Prefer forward slashes in scripts. Windows APIs accept them almost
  everywhere, including in PowerShell.
- `MAX_PATH` is 260 characters unless long paths are enabled. Deep
  `node_modules` trees plus a nested worktree still hit it. Keep repos near the
  drive root when a toolchain starts failing with truncated-looking paths.

## Line endings

`core.autocrlf=true` rewrites shell scripts to CRLF on checkout, and then Git
Bash fails with `bad interpreter: /bin/sh^M`. Commit a `.gitattributes`:

```
* text=auto eol=lf
*.sh text eol=lf
*.ps1 text eol=crlf
*.bat text eol=crlf
```

This is the single highest-value file for cross-platform repos. Add it before
the first bug report, not after.

## Scripts and execution policy

`.ps1` files are blocked by default. For a one-off, bypass for that process
only rather than weakening the machine:

```powershell
powershell -ExecutionPolicy Bypass -File .\script.ps1
```

Never tell a user to run `Set-ExecutionPolicy Unrestricted` machine-wide to
make one script work.

## Symlinks

Creating a symlink on Windows needs Developer Mode enabled or an elevated
shell. This matters for agent skills specifically: `npx skills add` symlinks by
default. When it fails on Windows, `--copy` is the fix.

```bash
npx skills add <owner>/<repo> --copy
```

## Writing scripts that work everywhere

- Ship `.sh` and `.ps1` side by side, or ship one Node or Python script. Node
  and Python are the only interpreters reliably present on all three.
- In Node, use `path.join` and `os.tmpdir()`, never string-concatenated paths
  or a hardcoded `/tmp`.
- npm scripts run through `cmd.exe` on Windows. `FOO=bar npm run x` fails
  there. Use `cross-env`, or read config from a file instead.
- Test the Windows path in CI. `runs-on: windows-latest` costs one matrix entry
  and catches every item on this page.
