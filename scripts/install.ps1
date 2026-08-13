$ErrorActionPreference = "Stop"

$repository = if ($env:DRIFT_REPOSITORY) { $env:DRIFT_REPOSITORY } else { "c-schembri/drift" }
$version = if ($env:DRIFT_VERSION) { $env:DRIFT_VERSION } else { "latest" }
if ($version -notmatch '^[A-Za-z0-9._-]+$') { throw "drift: invalid version" }
$architecture = switch ([System.Runtime.InteropServices.RuntimeInformation]::OSArchitecture) {
    "X64" { "x86_64" }
    "Arm64" { "arm64" }
    default { throw "drift: unsupported architecture: $_" }
}
$asset = "drift-windows-$architecture.tar.gz"
$release = "https://github.com/$repository/releases"
$base = if ($version -eq "latest") { "$release/latest/download" } else { "$release/download/v$version" }
$home = if ($env:DRIFT_HOME) { $env:DRIFT_HOME } else { Join-Path $env:LOCALAPPDATA "drift" }
$temporary = Join-Path ([System.IO.Path]::GetTempPath()) ("drift-install-" + [guid]::NewGuid())

try {
    New-Item -ItemType Directory -Path $temporary | Out-Null
    $archive = Join-Path $temporary $asset
    $checksums = Join-Path $temporary "SHA256SUMS"
    Invoke-WebRequest "$base/$asset" -OutFile $archive
    Invoke-WebRequest "$base/SHA256SUMS" -OutFile $checksums
    $line = Get-Content $checksums | Where-Object { $_ -match "^[0-9a-f]{64}  $([regex]::Escape($asset))$" }
    if (-not $line) { throw "drift: release checksum does not contain $asset" }
    $expected = ($line -split "  ")[0]
    $actual = (Get-FileHash $archive -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($actual -ne $expected) { throw "drift: release checksum mismatch" }

    $destination = Join-Path $home "versions\$version-$($actual.Substring(0, 12))"
    if (-not (Test-Path $destination)) {
        New-Item -ItemType Directory -Path $destination | Out-Null
        tar -xzf $archive -C $destination
    }
    $bin = Join-Path $home "bin"
    New-Item -ItemType Directory -Force -Path $bin | Out-Null
    $shim = Join-Path $bin "drift.cmd"
    Set-Content -Path $shim -Encoding ASCII -Value "@`"$destination\drift.exe`" %*"
    Write-Host "Installed Drift at $destination"
    Write-Host "Add $bin to PATH."
} finally {
    Remove-Item $temporary -Recurse -Force -ErrorAction SilentlyContinue
}
