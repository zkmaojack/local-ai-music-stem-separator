param(
    [string]$Python = (Join-Path $PSScriptRoot '..\.venv\Scripts\python.exe'),
    [string]$UvPackage = (Join-Path $PSScriptRoot '..\.runtime\uv')
)

$ErrorActionPreference = 'Stop'
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$Python = (Resolve-Path -LiteralPath $Python).Path
$UvPackage = (Resolve-Path -LiteralPath $UvPackage).Path
$versionLine = Select-String -LiteralPath (Join-Path $repoRoot 'pyproject.toml') -Pattern '^version = "(\d+\.\d+\.\d+)"$'
if ($versionLine.Matches.Count -ne 1) { throw 'Expected a semantic project version in pyproject.toml' }
$version = $versionLine.Matches[0].Groups[1].Value
$buildRoot = Join-Path $repoRoot 'build\release'
$stage = Join-Path $buildRoot ([guid]::NewGuid().ToString('N'))
$payload = Join-Path $stage 'payload'
New-Item -ItemType Directory -Path $payload -Force | Out-Null
Push-Location $repoRoot
try {
    & (Join-Path $UvPackage 'bin/uv.exe') build --wheel --python $Python --out-dir $payload
    if ($LASTEXITCODE) { throw 'Wheel build failed' }
    Copy-Item -LiteralPath (Join-Path $UvPackage 'bin/uv.exe') -Destination $payload
    $uvInfo = Get-ChildItem -LiteralPath $UvPackage -Filter 'uv-*.dist-info' -Directory
    if (@($uvInfo).Count -ne 1) { throw 'Expected one installed uv distribution with its license files' }
    Copy-Item -LiteralPath (Join-Path $uvInfo.FullName 'licenses') -Destination (Join-Path $payload 'uv-licenses') -Recurse
    $archive = Join-Path $stage 'payload.zip'
    Compress-Archive -Path (Join-Path $payload '*') -DestinationPath $archive -CompressionLevel Optimal
    $releaseInfo = Join-Path $stage 'ReleaseInfo.cs'
    Set-Content -LiteralPath $releaseInfo -Encoding ascii -Value @"
internal static class ReleaseInfo {
    public const string Version = "$version";
    public const string AssemblyVersion = "$version.0";
}
"@
    $framework = Join-Path $env:WINDIR 'Microsoft.NET/Framework64/v4.0.30319'
    $references = @('System.Xaml.dll', 'System.Drawing.dll', 'System.Windows.Forms.dll', 'System.Management.dll',
        'System.IO.Compression.dll', 'System.IO.Compression.FileSystem.dll', 'Microsoft.CSharp.dll',
        'WPF/WindowsBase.dll', 'WPF/PresentationFramework.dll', 'WPF/PresentationCore.dll')
    $setup = Join-Path $buildRoot "StemSeparator-$version-windows-x64-setup.exe"
    $source = Join-Path $PSScriptRoot 'Setup.cs'
    $xaml = Join-Path $PSScriptRoot 'Setup.xaml'
    $compilerArgs = @('/nologo', '/target:winexe', '/platform:x64', '/optimize+', '/codepage:65001',
        "/out:$setup", "/resource:$xaml,Setup.xaml", "/resource:$archive,payload.zip")
    $compilerArgs += @($references | ForEach-Object { '/reference:' + (Join-Path $framework $_) })
    & (Join-Path $framework 'csc.exe') @compilerArgs $source $releaseInfo
    if ($LASTEXITCODE) { throw 'Installer compilation failed' }
    $hash = (Get-FileHash -Algorithm SHA256 -LiteralPath $setup).Hash.ToLowerInvariant()
    "$hash  $([IO.Path]::GetFileName($setup))" | Set-Content -LiteralPath (Join-Path $buildRoot 'SHA256SUMS.txt') -Encoding ascii
    Get-Item -LiteralPath $setup | Select-Object FullName,Length
    Write-Output "SHA256 $hash"
}
finally {
    Pop-Location
    $resolvedStage = (Resolve-Path -LiteralPath $stage).Path
    $stageItem = Get-Item -LiteralPath $resolvedStage
    if ($resolvedStage.StartsWith($buildRoot + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase) -and
        ($stageItem.Attributes -band [IO.FileAttributes]::ReparsePoint) -eq 0) {
        Remove-Item -LiteralPath $resolvedStage -Recurse -Force
    }
}
