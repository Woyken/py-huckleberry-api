$ErrorActionPreference = 'Stop'
if ([version](node -p 'process.versions.node') -lt [version]'22.12.0') {
    throw 'Node 22.12+ is required. Use native Windows Node, not WSL.'
}
$root = Split-Path $PSScriptRoot -Parent
$runtime = Join-Path $env:LOCALAPPDATA 'huckleberry-agent/runtime'
$marker = Join-Path $runtime '.controlling-huckleberry-runtime'
if ((Test-Path $runtime) -and -not (Test-Path $marker)) {
    throw "Refusing to overwrite an unmarked runtime directory: $runtime"
}
New-Item -ItemType Directory -Path $runtime -Force | Out-Null
if (-not (Test-Path $marker)) { Set-Content -LiteralPath $marker -Value 'Owned dependency cache for controlling-huckleberry; contains no account credentials.' }
Copy-Item -LiteralPath (Join-Path $root 'runtime-package.json') -Destination (Join-Path $runtime 'package.json')
Copy-Item -LiteralPath (Join-Path $root 'runtime-package-lock.json') -Destination (Join-Path $runtime 'package-lock.json')
# Dependencies are deliberately outside every skill discovery directory:
# playwright-core bundles its own SKILL.md files, which must not become global skills.
& npm --prefix $runtime ci --ignore-scripts --no-audit --no-fund
if ($LASTEXITCODE -ne 0) { throw 'Pinned dependency installation failed.' }
Write-Output "Runtime ready: $runtime (no browser download)"
