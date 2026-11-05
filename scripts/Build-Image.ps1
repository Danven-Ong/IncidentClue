# Build only allowlisted inputs, avoiding unrelated Windows test-directory ACLs.
[CmdletBinding()]
param(
    [string]$Tag = 'incidentclue:local'
)
$ErrorActionPreference = 'Stop'
$repository = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$temporaryRoot = [System.IO.Path]::GetFullPath([System.IO.Path]::GetTempPath())
$contextName = 'incidentclue-container-' + [guid]::NewGuid().ToString('N')
$context = Join-Path $temporaryRoot $contextName
New-Item -ItemType Directory -Path $context | Out-Null
try {
    $inputs = @(
        '.dockerignore', 'backend/Dockerfile', 'backend/pyproject.toml',
        'backend/README.md', 'backend/MANIFEST.in',
        'backend/requirements-dev.lock.txt', 'backend/requirements-runtime.lock.txt',
        'backend/requirements-rag.lock.txt'
    )
    foreach ($relative in $inputs) {
        $destination = Join-Path $context $relative
        New-Item -ItemType Directory -Path (Split-Path $destination -Parent) -Force | Out-Null
        Copy-Item -LiteralPath (Join-Path $repository $relative) -Destination $destination
    }
    New-Item -ItemType Directory -Path (Join-Path $context 'backend/src') -Force | Out-Null
    Copy-Item -LiteralPath (Join-Path $repository 'backend/src/incidentclue') `
        -Destination (Join-Path $context 'backend/src/incidentclue') -Recurse
    $corpus = Join-Path $context 'data/runbooks'
    New-Item -ItemType Directory -Path $corpus -Force | Out-Null
    Copy-Item -LiteralPath (Join-Path $repository 'data/runbooks/manifest.json') -Destination $corpus
    Get-ChildItem -LiteralPath (Join-Path $repository 'data/runbooks') -File -Filter '*.md' |
        Copy-Item -Destination $corpus
    & docker build --file (Join-Path $context 'backend/Dockerfile') --tag $Tag $context
    if ($LASTEXITCODE -ne 0) { throw "Container build failed with exit code $LASTEXITCODE" }
}
finally {
    # Verify the absolute target before recursively removing our own temp context.
    $resolvedContext = [System.IO.Path]::GetFullPath($context)
    $resolvedParent = [System.IO.Path]::GetFullPath((Split-Path $resolvedContext -Parent))
    if ($resolvedParent.TrimEnd('\', '/') -ne $temporaryRoot.TrimEnd('\', '/') -or
        (Split-Path $resolvedContext -Leaf) -ne $contextName -or
        $contextName -notmatch '^incidentclue-container-[a-f0-9]{32}$') {
        throw 'Refusing to remove an unexpected container build context'
    }
    Remove-Item -LiteralPath $resolvedContext -Recurse -Force
}
